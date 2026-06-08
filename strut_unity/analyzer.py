from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Iterable

import clang.cindex
from clang.cindex import CursorKind, TranslationUnit, TypeKind
from tree_sitter import Language, Parser
import tree_sitter_c


@dataclass(frozen=True)
class TypeField:
    name: str
    c_type: str
    type_kind: str
    pointee_type: str | None = None
    element_type: str | None = None
    fields: list["TypeField"] | None = None


@dataclass(frozen=True)
class Parameter:
    name: str
    c_type: str
    type_kind: str = "basic"
    pointee_type: str | None = None
    element_type: str | None = None
    fields: list[TypeField] | None = None


@dataclass(frozen=True)
class DependencyItem:
    name: str
    kind: str
    c_type: str | None = None
    signature: str | None = None
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    source: str | None = None


@dataclass(frozen=True)
class DependencyDetails:
    macros: list[DependencyItem]
    typedefs: list[DependencyItem]
    structs: list[DependencyItem]
    global_variables: list[DependencyItem]
    callee_declarations: list[DependencyItem]


@dataclass(frozen=True)
class FunctionDefinition:
    name: str
    return_type: str
    start_line: int
    end_line: int
    parameter_count: int


@dataclass
class FunctionContext:
    source: str
    name: str
    return_type: str
    return_type_kind: str
    return_pointee_type: str | None
    return_element_type: str | None
    return_fields: list[TypeField]
    parameters: list[Parameter]
    start_line: int
    end_line: int
    dependencies: list[str]
    dependency_details: DependencyDetails
    global_refs: list[str]
    branch_conditions: list[str]
    tree_sitter_has_error: bool
    tree_sitter_function_count: int

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


def analyze_function(source_path: str | Path, function_name: str | None = None) -> FunctionContext:
    source = Path(source_path).resolve()
    code = source.read_bytes()
    tree_has_error, tree_function_count = _tree_sitter_summary(code)

    index = clang.cindex.Index.create()
    tu = index.parse(
        str(source),
        args=["-std=c11", f"-I{source.parent}", "-Wno-everything"],
        options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )
    function = _find_function(tu.cursor, source, function_name)
    if function is None:
        name = function_name or "<first function>"
        raise RuntimeError(f"Could not find function definition {name!r} in {source}")

    dependencies: set[str] = set()
    global_refs: set[str] = set()
    callee_cursors = []
    global_cursors = []
    branch_conditions: list[str] = []

    for cursor in function.walk_preorder():
        if cursor.kind == CursorKind.CALL_EXPR and cursor.spelling:
            if cursor.spelling != function.spelling:
                dependencies.add(cursor.spelling)
                if cursor.referenced is not None:
                    callee_cursors.append(cursor.referenced)
        elif cursor.kind == CursorKind.DECL_REF_EXPR and cursor.referenced is not None:
            ref = cursor.referenced
            if ref.kind == CursorKind.VAR_DECL and ref.semantic_parent.kind == CursorKind.TRANSLATION_UNIT:
                global_refs.add(ref.spelling)
                global_cursors.append(ref)
        elif cursor.kind == CursorKind.IF_STMT:
            condition = _condition_from_if(cursor)
            if condition:
                branch_conditions.append(condition)

    dependency_details = _dependency_details(tu.cursor, function, source, callee_cursors, global_cursors)

    return_info = _type_info(function.result_type)
    return FunctionContext(
        source=str(source),
        name=function.spelling,
        return_type=function.result_type.spelling,
        return_type_kind=return_info["type_kind"],
        return_pointee_type=return_info["pointee_type"],
        return_element_type=return_info["element_type"],
        return_fields=return_info["fields"],
        parameters=[_parameter_from_cursor(p) for p in function.get_arguments()],
        start_line=function.extent.start.line,
        end_line=function.extent.end.line,
        dependencies=sorted(dependencies),
        dependency_details=dependency_details,
        global_refs=sorted(global_refs),
        branch_conditions=branch_conditions,
        tree_sitter_has_error=tree_has_error,
        tree_sitter_function_count=tree_function_count,
    )


def list_function_definitions(source_path: str | Path, include_main: bool = False) -> list[FunctionDefinition]:
    source = Path(source_path).resolve()
    index = clang.cindex.Index.create()
    tu = index.parse(
        str(source),
        args=["-std=c11", f"-I{source.parent}", "-Wno-everything"],
        options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )
    definitions = []
    for cursor in tu.cursor.walk_preorder():
        if cursor.kind != CursorKind.FUNCTION_DECL or not cursor.is_definition():
            continue
        if not cursor.location.file or Path(cursor.location.file.name).resolve() != source:
            continue
        if not include_main and cursor.spelling == "main":
            continue
        definitions.append(
            FunctionDefinition(
                name=cursor.spelling,
                return_type=cursor.result_type.spelling,
                start_line=cursor.extent.start.line,
                end_line=cursor.extent.end.line,
                parameter_count=sum(1 for _ in cursor.get_arguments()),
            )
        )
    return sorted(definitions, key=lambda item: (item.start_line, item.name))


def _parameter_from_cursor(cursor) -> Parameter:
    type_info = _type_info(cursor.type, _looks_like_array_parameter(cursor))
    return Parameter(name=cursor.spelling, c_type=cursor.type.spelling, **type_info)


def _looks_like_array_parameter(cursor) -> bool:
    tokens = [token.spelling for token in cursor.get_tokens()]
    return "[" in tokens and "]" in tokens


def _type_info(c_type, force_array: bool = False, depth: int = 2) -> dict:
    canonical = c_type.get_canonical()
    kind = c_type.kind
    canonical_kind = canonical.kind

    if force_array or kind in {TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY}:
        element = c_type.get_array_element_type()
        if not element.spelling and canonical_kind == TypeKind.POINTER:
            element = canonical.get_pointee()
        return {
            "type_kind": "array",
            "element_type": element.spelling or _strip_pointer(c_type.spelling),
            "pointee_type": None,
            "fields": [],
        }

    if kind == TypeKind.POINTER or canonical_kind == TypeKind.POINTER:
        pointee = c_type.get_pointee() if kind == TypeKind.POINTER else canonical.get_pointee()
        return {
            "type_kind": "pointer",
            "pointee_type": pointee.spelling,
            "element_type": None,
            "fields": _fields_for_type(pointee, depth - 1),
        }

    if _is_record_type(c_type):
        return {
            "type_kind": "composite",
            "pointee_type": None,
            "element_type": None,
            "fields": _fields_for_type(c_type, depth - 1),
        }

    if _is_basic_type(c_type.spelling):
        return {"type_kind": "basic", "pointee_type": None, "element_type": None, "fields": []}

    return {"type_kind": "other", "pointee_type": None, "element_type": None, "fields": []}


def _fields_for_type(c_type, depth: int) -> list[TypeField]:
    if depth < 0:
        return []
    record = _record_type(c_type)
    if record is None:
        return []
    fields = []
    for child in record.get_declaration().get_children():
        if child.kind != CursorKind.FIELD_DECL:
            continue
        info = _type_info(child.type, False, depth)
        fields.append(
            TypeField(
                name=child.spelling,
                c_type=child.type.spelling,
                type_kind=info["type_kind"],
                pointee_type=info["pointee_type"],
                element_type=info["element_type"],
                fields=info["fields"],
            )
        )
    return fields


def _dependency_details(
    root,
    function,
    source: Path,
    callee_cursors: list,
    global_cursors: list,
) -> DependencyDetails:
    typedefs: list[DependencyItem] = []
    structs: list[DependencyItem] = []
    local_macro_definitions: dict[str, DependencyItem] = {}

    for cursor in root.walk_preorder():
        if cursor.kind == CursorKind.MACRO_DEFINITION and _cursor_is_local(cursor, source):
            item = _dependency_item_from_cursor(cursor, "macro", source_override=_macro_source(cursor))
            if item is not None:
                local_macro_definitions[item.name] = item

    for cursor in root.get_children():
        if not _cursor_is_local(cursor, source):
            continue

        if cursor.kind == CursorKind.TYPEDEF_DECL:
            item = _dependency_item_from_cursor(cursor, "typedef")
            if item is not None:
                typedefs.append(item)
        elif cursor.kind in {CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.ENUM_DECL}:
            if not cursor.is_definition():
                continue
            item = _dependency_item_from_cursor(cursor, _record_kind_name(cursor))
            if item is not None:
                structs.append(item)

    global_variables = [
        item
        for item in (_dependency_item_from_cursor(cursor, "global_variable") for cursor in global_cursors)
        if item is not None
    ]
    callee_declarations = [
        item
        for item in (_callee_declaration_item(cursor) for cursor in callee_cursors)
        if item is not None
    ]

    macro_names = _referenced_macro_names(root, function, source)
    dependency_sources = "\n".join(
        item.source or ""
        for item in [*typedefs, *structs, *global_variables, *callee_declarations]
        if item.source
    )
    for name in local_macro_definitions:
        if re.search(rf"\b{re.escape(name)}\b", dependency_sources):
            macro_names.add(name)

    macros = [local_macro_definitions[name] for name in sorted(macro_names) if name in local_macro_definitions]
    return DependencyDetails(
        macros=_dedupe_items(macros),
        typedefs=_dedupe_items(typedefs),
        structs=_dedupe_items(structs),
        global_variables=_dedupe_items(global_variables),
        callee_declarations=_dedupe_items(callee_declarations),
    )


def _dependency_item_from_cursor(cursor, kind: str, source_override: str | None = None) -> DependencyItem | None:
    location = _cursor_location(cursor)
    if location is None:
        return None
    file_path, start_line, end_line = location
    source = source_override if source_override is not None else _source_excerpt(file_path, start_line, end_line)
    return DependencyItem(
        name=cursor.spelling or cursor.displayname,
        kind=kind,
        c_type=_cursor_type_spelling(cursor),
        signature=cursor.type.spelling if hasattr(cursor, "type") and cursor.type.spelling else None,
        file=str(file_path),
        start_line=start_line,
        end_line=end_line,
        source=source,
    )


def _callee_declaration_item(cursor) -> DependencyItem | None:
    location = _cursor_location(cursor)
    file_path = start_line = end_line = source = None
    if location is not None:
        file_path, start_line, end_line = location
        source = _source_excerpt(file_path, start_line, end_line)
        if cursor.is_definition():
            source = _function_prototype(cursor)

    prototype = _function_prototype(cursor)
    return DependencyItem(
        name=cursor.spelling or cursor.displayname,
        kind="callee_declaration",
        c_type=_cursor_type_spelling(cursor),
        signature=cursor.type.spelling if hasattr(cursor, "type") and cursor.type.spelling else None,
        file=str(file_path) if file_path is not None else None,
        start_line=start_line,
        end_line=end_line,
        source=source or prototype,
    )


def _function_prototype(cursor) -> str | None:
    if not hasattr(cursor, "result_type") or not cursor.spelling:
        return None
    result = cursor.result_type.spelling
    args = []
    for index, argument in enumerate(cursor.get_arguments()):
        name = argument.spelling or f"arg{index + 1}"
        args.append(f"{argument.type.spelling} {name}".strip())
    if not args and cursor.type.spelling.endswith("(void)"):
        args_text = "void"
    else:
        args_text = ", ".join(args)
    separator = "" if result.rstrip().endswith("*") else " "
    return f"{result}{separator}{cursor.spelling}({args_text});"


def _referenced_macro_names(root, function, source: Path) -> set[str]:
    names: set[str] = set()
    function_file = Path(function.location.file.name).resolve() if function.location.file else source
    for cursor in root.walk_preorder():
        if cursor.kind != CursorKind.MACRO_INSTANTIATION or not cursor.spelling:
            continue
        if not cursor.location.file:
            continue
        macro_file = Path(cursor.location.file.name).resolve()
        if macro_file != function_file:
            continue
        if function.extent.start.line <= cursor.location.line <= function.extent.end.line:
            names.add(cursor.spelling)
    return names


def _record_kind_name(cursor) -> str:
    return {
        CursorKind.STRUCT_DECL: "struct",
        CursorKind.UNION_DECL: "union",
        CursorKind.ENUM_DECL: "enum",
    }.get(cursor.kind, "record")


def _cursor_type_spelling(cursor) -> str | None:
    return cursor.type.spelling if hasattr(cursor, "type") and cursor.type.spelling else None


def _cursor_location(cursor) -> tuple[Path, int, int] | None:
    if not cursor.location.file:
        return None
    start = cursor.extent.start
    end = cursor.extent.end
    if not start.file:
        return None
    return Path(start.file.name).resolve(), start.line, max(start.line, end.line)


def _cursor_is_local(cursor, source: Path) -> bool:
    if not cursor.location.file:
        return False
    file_path = Path(cursor.location.file.name).resolve()
    return file_path == source or source.parent == file_path.parent or source.parent in file_path.parents


def _source_excerpt(file_path: Path, start_line: int, end_line: int) -> str | None:
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start = max(start_line - 1, 0)
    end = min(end_line, len(lines))
    return "\n".join(lines[start:end])


def _macro_source(cursor) -> str | None:
    location = _cursor_location(cursor)
    if location is None:
        return None
    file_path, start_line, _ = location
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    index = start_line - 1
    collected = []
    while 0 <= index < len(lines):
        collected.append(lines[index])
        if not lines[index].rstrip().endswith("\\"):
            break
        index += 1
    return "\n".join(collected)


def _dedupe_items(items: list[DependencyItem]) -> list[DependencyItem]:
    seen: set[tuple[str, str, str | None, int | None, int | None, str | None]] = set()
    unique: list[DependencyItem] = []
    for item in items:
        key = (item.kind, item.name, item.file, item.start_line, item.end_line, item.source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _record_type(c_type):
    candidate = c_type
    if candidate.kind == TypeKind.ELABORATED:
        candidate = candidate.get_named_type()
    canonical = candidate.get_canonical()
    if canonical.kind == TypeKind.RECORD:
        return canonical
    if candidate.kind == TypeKind.RECORD:
        return candidate
    return None


def _is_record_type(c_type) -> bool:
    return _record_type(c_type) is not None


def _is_basic_type(c_type: str) -> bool:
    return " ".join(c_type.split()) in {
        "int",
        "signed int",
        "short",
        "short int",
        "long",
        "long int",
        "long long",
        "long long int",
        "unsigned",
        "unsigned int",
        "unsigned short",
        "unsigned short int",
        "unsigned long",
        "unsigned long int",
        "unsigned long long",
        "unsigned long long int",
        "size_t",
        "float",
        "double",
        "char",
        "signed char",
        "unsigned char",
        "bool",
        "_Bool",
    }


def _strip_pointer(c_type: str) -> str:
    return c_type.replace("*", "").strip()


def _tree_sitter_summary(code: bytes) -> tuple[bool, int]:
    parser = Parser(Language(tree_sitter_c.language()))
    tree = parser.parse(code)
    count = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            count += 1
        stack.extend(node.children)
    return tree.root_node.has_error, count


def _find_function(root, source: Path, function_name: str | None):
    for cursor in root.walk_preorder():
        if cursor.kind != CursorKind.FUNCTION_DECL or not cursor.is_definition():
            continue
        if not cursor.location.file or Path(cursor.location.file.name).resolve() != source:
            continue
        if function_name is None or cursor.spelling == function_name:
            return cursor
    return None


def _condition_from_if(cursor) -> str:
    tokens = [token.spelling for token in cursor.get_tokens()]
    try:
        start = tokens.index("(")
    except ValueError:
        return ""

    depth = 0
    pieces: list[str] = []
    for token in tokens[start:]:
        if token == "(":
            depth += 1
            if depth == 1:
                continue
        elif token == ")":
            depth -= 1
            if depth == 0:
                break
        if depth >= 1:
            pieces.append(token)
    return _compact_condition(pieces)


def _compact_condition(tokens: Iterable[str]) -> str:
    text = " ".join(tokens)
    text = re.sub(r"\s+([)\],;])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"\s+([<>!=]=?)\s+", r" \1 ", text)
    return text.strip()
