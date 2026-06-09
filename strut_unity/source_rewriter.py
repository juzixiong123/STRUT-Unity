from __future__ import annotations

from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c


def prepare_test_source(
    source_path: Path,
    build_dir: Path,
    function_name: str,
    stubbed_functions: set[str] | None = None,
) -> tuple[Path, str]:
    source = source_path.read_bytes()
    replacements = _identifier_replacements(source, function_name, stubbed_functions or set())
    if not replacements:
        return source_path, function_name

    rewritten = bytearray(source)
    for start, end, name in sorted(replacements, reverse=True):
        rewritten[start:end] = name.encode("utf-8")

    test_source = build_dir / f"strut_source_{source_path.stem}.c"
    test_source.write_bytes(bytes(rewritten))
    callable_name = "__strut_unity_target_main" if function_name == "main" else function_name
    return test_source, callable_name


def _identifier_replacements(
    source: bytes,
    function_name: str,
    stubbed_functions: set[str],
) -> list[tuple[int, int, str]]:
    parser = Parser(Language(tree_sitter_c.language()))
    tree = parser.parse(source)
    replacements: list[tuple[int, int, str]] = []
    for node in _walk_tree(tree.root_node):
        if node.type != "function_definition":
            continue
        identifier = _function_identifier(node)
        if identifier is None:
            continue
        name = source[identifier.start_byte : identifier.end_byte].decode("utf-8", errors="replace")
        if name == "main":
            replacement = "__strut_unity_target_main" if function_name == "main" else "__strut_unity_disabled_main"
            replacements.append((identifier.start_byte, identifier.end_byte, replacement))
        elif name in stubbed_functions and name != function_name:
            replacements.append((identifier.start_byte, identifier.end_byte, f"__strut_unity_original_{name}"))
    return replacements


def _function_identifier(node):
    declarators = [child for child in _walk_tree(node) if child.type == "function_declarator"]
    if not declarators:
        return None
    for child in declarators[0].children:
        if child.type == "identifier":
            return child
        if child.type.endswith("declarator"):
            nested = _first_identifier(child)
            if nested is not None:
                return nested
    return None


def _first_identifier(node):
    if node.type == "identifier":
        return node
    for child in node.children:
        found = _first_identifier(child)
        if found is not None:
            return found
    return None


def _walk_tree(node):
    yield node
    for child in node.children:
        yield from _walk_tree(child)
