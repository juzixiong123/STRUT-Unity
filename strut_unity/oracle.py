from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .analyzer import FunctionContext
from .cases import TestCase, case_declarations, case_return_output, with_expected
from .stubs import stub_definitions, stub_prelude


def fill_expected_values(
    context: FunctionContext,
    cases: list[TestCase],
    source_path: Path,
    build_dir: Path,
) -> list[TestCase]:
    if _normalize_type(context.return_type) == "void":
        return cases
    missing_indexes = [
        index
        for index, case in enumerate(cases)
        if (return_output := case_return_output(context, case)) is None or return_output.value is None
    ]
    if not missing_indexes:
        return cases
    if not _is_supported_return_context(context):
        raise NotImplementedError(
            "The connector currently supports scalar returns, scalar/struct pointer returns, and struct value returns."
        )

    oracle_cases = [cases[index] for index in missing_indexes]
    oracle_c = build_dir / f"oracle_{context.name}.c"
    oracle_exe = build_dir / f"oracle_{context.name}"
    oracle_c.write_text(_oracle_source(context, oracle_cases), encoding="utf-8")
    cmd = ["clang", "-std=c11", "-I", str(source_path.parent), str(oracle_c), "-o", str(oracle_exe)]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Oracle compile failed:\n{result.stderr}")

    output = subprocess.run([str(oracle_exe)], text=True, capture_output=True, check=True).stdout
    expected_values = [
        _parse_expected_marker(line.split(":", 1)[1])
        for line in output.splitlines()
        if line.startswith("__STRUT_EXPECTED__:")
    ]
    if len(expected_values) != len(oracle_cases):
        raise RuntimeError(f"Oracle returned {len(expected_values)} expected values for {len(oracle_cases)} cases:\n{output}")

    filled = list(cases)
    for index, expected in zip(missing_indexes, expected_values):
        filled[index] = with_expected(filled[index], expected, context)
    return filled


def _oracle_source(context: FunctionContext, cases: list[TestCase]) -> str:
    lines = [
        "#include <stdio.h>",
        "#include <stddef.h>",
        "#include <stdbool.h>",
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
        for declaration in case_declarations(case):
            lines.append(f"        {declaration}")
        if case.stubins:
            lines.append(f"        __strut_stub_case_index = {index};")
        args = ", ".join(case.args)
        lines.append(_oracle_print(context, f"{context.name}({args})"))
        lines.append("    }")
    lines.extend(["    return 0;", "}", ""])
    return "\n".join(lines)


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


def _normalize_type(c_type: str) -> str:
    return " ".join(c_type.replace("const ", "").split())
