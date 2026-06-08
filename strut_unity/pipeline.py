from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess

from tree_sitter import Language, Parser
import tree_sitter_c

from .analyzer import analyze_function, FunctionContext
from .cases import generate_seed_cases, TestCase, with_expected
from .coverage import collect_gcov_coverage, estimate_branch_coverage
from .llm_cases import generate_llm_cases, generate_optimized_llm_cases
from .llm_client import LLMConfig, OpenAICompatibleClient, write_llm_trace
from .prompts import cases_to_strut_json
from .stubs import stub_definitions, stub_function_names, stub_prelude
from .unity_writer import write_unity_test


ROOT = Path(__file__).resolve().parents[1]


def run_pipeline(
    source: str | Path,
    function: str | None = None,
    case_source: str = "hybrid",
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    optimize: bool = True,
) -> dict:
    source_path = Path(source).resolve()
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)

    context = analyze_function(source_path, function)
    test_source, callable_name = _prepare_test_source(source_path, build_dir, context.name)
    context = replace(context, source=str(test_source), name=callable_name)
    source_code = source_path.read_text(encoding="utf-8")
    context_path = build_dir / f"{context.name}_context.json"
    context_path.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")

    cases, generation_info = _generate_cases(
        context,
        source_code,
        build_dir,
        case_source,
        llm_base_url,
        llm_model,
        llm_api_key,
    )
    stubs = stub_function_names(context, cases)
    if stubs:
        test_source, callable_name = _prepare_test_source(source_path, build_dir, context.name, stubs)
        context = replace(context, source=str(test_source), name=callable_name)
        context_path.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")
    cases = _fill_expected_values(context, cases, source_path, build_dir)
    result = _write_compile_run_collect(context, cases, source_path, test_source, build_dir, context_path, "initial")

    result = {**result, **generation_info}
    uncovered = result.get("coverage", {}).get("estimated", {}).get("uncovered_conditions", [])
    if not (optimize and case_source in {"llm", "hybrid"} and uncovered and result.get("run_returncode") == 0):
        return result

    config = LLMConfig.from_values(base_url=llm_base_url, model=llm_model, api_key=llm_api_key)
    client = OpenAICompatibleClient(config)
    optimized_cases, prompt, response = generate_optimized_llm_cases(
        context,
        source_code,
        cases,
        uncovered,
        client,
    )
    extended_cases = _merge_cases(cases, optimized_cases)
    stubs = stub_function_names(context, extended_cases)
    if stubs:
        test_source, callable_name = _prepare_test_source(source_path, build_dir, context.name, stubs)
        context = replace(context, source=str(test_source), name=callable_name)
        context_path.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")
    extended_cases = _fill_expected_values(context, extended_cases, source_path, build_dir)
    optimized_result = _write_compile_run_collect(context, extended_cases, source_path, test_source, build_dir, context_path, "optimized")
    optimized_trace = write_llm_trace(build_dir, f"{context.name}_optimization", prompt, response)
    return {
        **optimized_result,
        **generation_info,
        "optimization": {
            "enabled": True,
            "uncovered_conditions": uncovered,
            "added_cases": len(extended_cases) - len(cases),
            **optimized_trace,
        },
        "initial_result": result,
    }


def _write_compile_run_collect(
    context: FunctionContext,
    cases: list[TestCase],
    source_path: Path,
    test_source: Path,
    build_dir: Path,
    context_path: Path,
    label: str,
) -> dict:
    case_path = build_dir / f"{context.name}_cases.json"
    test_path = build_dir / f"test_{context.name}.c"
    exe_path = build_dir / f"test_{context.name}"

    case_path.write_text(json.dumps(cases_to_strut_json(context, cases, backend=True), indent=2), encoding="utf-8")
    write_unity_test(context, cases, test_path)

    compile_cmd = [
        "clang",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-I",
        str(ROOT / "unity"),
        "-I",
        str(source_path.parent),
        "-I",
        str(test_source.parent),
        str(test_path),
        str(ROOT / "unity" / "unity.c"),
        "-DUNITY_INCLUDE_DOUBLE",
        "-o",
        str(exe_path),
    ]
    compile_result = subprocess.run(compile_cmd, text=True, capture_output=True, check=False)
    if compile_result.returncode != 0:
        return {
            "context": str(context_path),
            "cases": str(case_path),
            "test": str(test_path),
            "stage": label,
            "compile_cmd": compile_cmd,
            "compile_returncode": compile_result.returncode,
            "compile_stdout": compile_result.stdout,
            "compile_stderr": compile_result.stderr,
        }

    run_result = subprocess.run([str(exe_path)], text=True, capture_output=True, check=False)
    estimated_coverage = estimate_branch_coverage(context, cases)
    gcov_coverage = collect_gcov_coverage(context, test_source, test_path, ROOT / "unity", build_dir, label)
    return {
        "context": str(context_path),
        "cases": str(case_path),
        "test": str(test_path),
        "executable": str(exe_path),
        "stage": label,
        "compile_cmd": compile_cmd,
        "compile_returncode": compile_result.returncode,
        "compile_stdout": compile_result.stdout,
        "compile_stderr": compile_result.stderr,
        "run_returncode": run_result.returncode,
        "run_stdout": run_result.stdout,
        "run_stderr": run_result.stderr,
        "coverage": {
            "estimated": estimated_coverage,
            "gcov": gcov_coverage,
        },
    }


def _generate_cases(
    context: FunctionContext,
    source_code: str,
    build_dir: Path,
    case_source: str,
    llm_base_url: str | None,
    llm_model: str | None,
    llm_api_key: str | None,
) -> tuple[list[TestCase], dict]:
    rules_cases = generate_seed_cases(context)
    if case_source == "rules":
        return rules_cases, {"case_source": "rules"}

    config = LLMConfig.from_values(base_url=llm_base_url, model=llm_model, api_key=llm_api_key)

    client = OpenAICompatibleClient(config)
    llm_cases, prompt, response = generate_llm_cases(context, source_code, rules_cases, client)
    info = {
        "case_source": case_source,
        "llm_base_url": config.base_url,
        "llm_model": config.model,
        **write_llm_trace(build_dir, f"{context.name}_generation", prompt, response),
    }
    if case_source == "llm":
        return llm_cases, info
    if case_source == "hybrid":
        return _merge_cases(rules_cases, llm_cases), info
    raise ValueError(f"Unsupported case source: {case_source}")


def _merge_cases(*case_groups: list[TestCase]) -> list[TestCase]:
    merged: list[TestCase] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for cases in case_groups:
        for case in cases:
            identity = case.identity()
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(case)
    return merged


def _fill_expected_values(
    context: FunctionContext,
    cases: list[TestCase],
    source_path: Path,
    build_dir: Path,
) -> list[TestCase]:
    if _normalize_type(context.return_type) == "void":
        return cases
    if not _is_supported_return_context(context):
        raise NotImplementedError(
            "The connector currently supports scalar returns, scalar/struct pointer returns, and struct value returns."
        )

    oracle_c = build_dir / f"oracle_{context.name}.c"
    oracle_exe = build_dir / f"oracle_{context.name}"
    oracle_c.write_text(_oracle_source(context, cases), encoding="utf-8")
    cmd = ["clang", "-std=c11", "-I", str(source_path.parent), str(oracle_c), "-o", str(oracle_exe)]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Oracle compile failed:\n{result.stderr}")
    output = subprocess.run([str(oracle_exe)], text=True, capture_output=True, check=True).stdout
    expected_values = [_parse_expected_marker(line.split(":", 1)[1]) for line in output.splitlines() if line.startswith("__STRUT_EXPECTED__:")]
    if len(expected_values) != len(cases):
        raise RuntimeError(f"Oracle returned {len(expected_values)} expected values for {len(cases)} cases:\n{output}")
    return [with_expected(case, expected) for case, expected in zip(cases, expected_values)]


def _oracle_source(context: FunctionContext, cases: list[TestCase]) -> str:
    lines = [
        "#include <stdio.h>",
        "#include <stddef.h>",
        *stub_prelude(context, cases),
        f'#include "{context.source}"',
        "",
        *stub_definitions(context, cases),
        "int main(void)",
        "{",
    ]
    for index, case in enumerate(cases, start=1):
        lines.append(f"    /* case {index}: {case.desc} */")
        lines.append("    {")
        for declaration in _case_declarations(case):
            lines.append(f"        {declaration}")
        if case.stubins:
            lines.append(f"        __strut_stub_case_index = {index};")
        args = ", ".join(case.args)
        lines.append(_oracle_print(context, f"{context.name}({args})"))
        lines.append("    }")
    lines.extend(["    return 0;", "}", ""])
    return "\n".join(lines)


def _case_declarations(case: TestCase) -> list[str]:
    declarations: list[str] = []
    seen: set[str] = set()
    for binding in case.bindings:
        for declaration in binding.declarations:
            if declaration in seen:
                continue
            seen.add(declaration)
            declarations.append(declaration)
    return declarations


def _oracle_print(context: FunctionContext, actual: str) -> str:
    normalized = _normalize_type(context.return_type)
    if normalized in {"float", "double"}:
        return f'        printf("__STRUT_EXPECTED__:%.17g\\n", (double)({actual}));'
    if context.return_type_kind == "pointer":
        return _oracle_pointer_print(context, actual)
    if context.return_type_kind == "composite":
        return _oracle_struct_print(context, actual)
    return f'        printf("__STRUT_EXPECTED__:%lld\\n", (long long)({actual}));'


def _oracle_pointer_print(context: FunctionContext, actual: str) -> str:
    variable = "__strut_actual"
    lines = [f"        {context.return_type} {variable} = {actual};"]
    lines.append('        printf("__STRUT_EXPECTED__:{\\"kind\\":\\"pointer\\",\\"is_null\\":%d", ' f"{variable} == NULL);")
    lines.append(f"        if ({variable} != NULL)")
    lines.append("        {")
    pointee_type = context.return_pointee_type or _strip_pointer(context.return_type)
    if context.return_fields:
        lines.append('            printf(",\\"fields\\":{");')
        lines.extend(_oracle_field_prints(context.return_fields, variable, pointer=True, indent="            "))
        lines.append('            printf("}");')
    elif _is_supported_scalar_type(pointee_type):
        lines.append('            printf(",\\"value\\":");')
        lines.append(_oracle_value_printf(f"*{variable}", pointee_type, indent="            "))
    lines.append("        }")
    lines.append('        printf("}\\n");')
    return "\n".join(lines)


def _oracle_struct_print(context: FunctionContext, actual: str) -> str:
    variable = "__strut_actual"
    lines = [f"        {context.return_type} {variable} = {actual};"]
    lines.append('        printf("__STRUT_EXPECTED__:{\\"kind\\":\\"struct\\",\\"fields\\":{");')
    lines.extend(_oracle_field_prints(context.return_fields, variable, pointer=False, indent="        "))
    lines.append('        printf("}}\\n");')
    return "\n".join(lines)


def _oracle_field_prints(fields, variable: str, pointer: bool, indent: str) -> list[str]:
    lines: list[str] = []
    printable = [field for field in fields if _field_is_assertable(field)]
    for index, field in enumerate(printable):
        separator = "" if index == 0 else ","
        access = f"{variable}->{field.name}" if pointer else f"{variable}.{field.name}"
        lines.append(f'{indent}printf("{separator}\\"{field.name}\\":");')
        if field.type_kind == "pointer":
            lines.append(f'{indent}printf("{{\\"is_null\\":%d", {access} == NULL);')
            pointee_type = field.pointee_type or _strip_pointer(field.c_type)
            if _is_supported_scalar_type(pointee_type):
                lines.append(f"{indent}if ({access} != NULL)")
                lines.append(f"{indent}{{")
                lines.append(f'{indent}    printf(",\\"value\\":");')
                lines.append(_oracle_value_printf(f"*{access}", pointee_type, indent=f"{indent}    "))
                lines.append(f"{indent}}}")
            lines.append(f'{indent}printf("}}");')
        elif field.type_kind == "array":
            lines.append(_oracle_value_printf(f"{access}[0]", field.element_type or field.c_type, indent=indent))
        elif field.type_kind == "composite":
            lines.append('        printf("{");')
            lines.extend(_oracle_field_prints(field.fields or [], access, pointer=False, indent=indent))
            lines.append(f'{indent}printf("}}");')
        else:
            lines.append(_oracle_value_printf(access, field.c_type, indent=indent))
    return lines


def _oracle_value_printf(expr: str, c_type: str, indent: str) -> str:
    normalized = _normalize_type(c_type)
    if normalized in {"float", "double"}:
        return f'{indent}printf("%.17g", (double)({expr}));'
    return f'{indent}printf("%lld", (long long)({expr}));'


def _parse_expected_marker(value: str):
    stripped = value.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    return stripped


def _is_supported_return_context(context: FunctionContext) -> bool:
    if _is_supported_scalar_type(context.return_type):
        return True
    if context.return_type_kind == "pointer":
        pointee_type = context.return_pointee_type or _strip_pointer(context.return_type)
        return _is_supported_scalar_type(pointee_type) or _has_assertable_fields(context.return_fields)
    if context.return_type_kind == "composite":
        return _has_assertable_fields(context.return_fields)
    return False


def _is_supported_scalar_type(c_type: str) -> bool:
    return _normalize_type(c_type) in {
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
        "bool",
        "_Bool",
        "float",
        "double",
        "char",
        "signed char",
        "unsigned char",
    }


def _has_assertable_fields(fields) -> bool:
    return any(_field_is_assertable(field) for field in fields or [])


def _field_is_assertable(field) -> bool:
    if field.type_kind == "pointer":
        return True
    if field.type_kind == "array":
        return _is_supported_scalar_type(field.element_type or field.c_type)
    if field.type_kind == "composite":
        return _has_assertable_fields(field.fields)
    return _is_supported_scalar_type(field.c_type)


def _strip_pointer(c_type: str) -> str:
    return c_type.replace("*", "").strip()


def _prepare_test_source(
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


def _normalize_type(c_type: str) -> str:
    return " ".join(c_type.replace("const ", "").split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and run Unity tests from a C function.")
    parser.add_argument("source", help="C source file containing the function under test")
    parser.add_argument("--function", "-f", default=None, help="Function name. Defaults to first definition.")
    parser.add_argument(
        "--case-source",
        choices=["rules", "llm", "hybrid"],
        default="hybrid",
        help="Use deterministic rules, STRUT-style LLM cases, or both. Defaults to hybrid with local Ollama.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help="OpenAI-compatible API base URL. Defaults to STRUT_LLM_BASE_URL or local Ollama.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Model name. Defaults to STRUT_LLM_MODEL or local Ollama qwen3.6:27b.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="API key. Defaults to STRUT_LLM_API_KEY or OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Skip STRUT's LLM optimization pass after the first compile/test run.",
    )
    args = parser.parse_args()
    result = run_pipeline(
        args.source,
        args.function,
        case_source=args.case_source,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
        optimize=not args.no_optimize,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("compile_returncode") == 0 and result.get("run_returncode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
