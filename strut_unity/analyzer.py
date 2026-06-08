from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Iterable

import clang.cindex
from clang.cindex import CursorKind, TypeKind
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
    )
    function = _find_function(tu.cursor, source, function_name)
    if function is None:
        name = function_name or "<first function>"
        raise RuntimeError(f"Could not find function definition {name!r} in {source}")

    dependencies: set[str] = set()
    global_refs: set[str] = set()
    branch_conditions: list[str] = []

    for cursor in function.walk_preorder():
        if cursor.kind == CursorKind.CALL_EXPR and cursor.spelling:
            if cursor.spelling != function.spelling:
                dependencies.add(cursor.spelling)
        elif cursor.kind == CursorKind.DECL_REF_EXPR and cursor.referenced is not None:
            ref = cursor.referenced
            if ref.kind == CursorKind.VAR_DECL and ref.semantic_parent.kind == CursorKind.TRANSLATION_UNIT:
                global_refs.add(ref.spelling)
        elif cursor.kind == CursorKind.IF_STMT:
            condition = _condition_from_if(cursor)
            if condition:
                branch_conditions.append(condition)

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
        global_refs=sorted(global_refs),
        branch_conditions=branch_conditions,
        tree_sitter_has_error=tree_has_error,
        tree_sitter_function_count=tree_function_count,
    )


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
