from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Iterable

import clang.cindex
from clang.cindex import CursorKind
from tree_sitter import Language, Parser
import tree_sitter_c


@dataclass(frozen=True)
class Parameter:
    name: str
    c_type: str


@dataclass
class FunctionContext:
    source: str
    name: str
    return_type: str
    parameters: list[Parameter]
    dependencies: list[str]
    global_refs: list[str]
    branch_conditions: list[str]
    tree_sitter_has_error: bool
    tree_sitter_function_count: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["parameters"] = [asdict(p) for p in self.parameters]
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

    return FunctionContext(
        source=str(source),
        name=function.spelling,
        return_type=function.result_type.spelling,
        parameters=[Parameter(p.spelling, p.type.spelling) for p in function.get_arguments()],
        dependencies=sorted(dependencies),
        global_refs=sorted(global_refs),
        branch_conditions=branch_conditions,
        tree_sitter_has_error=tree_has_error,
        tree_sitter_function_count=tree_function_count,
    )


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

