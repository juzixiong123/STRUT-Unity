from __future__ import annotations

from pathlib import Path

from .analyzer import FunctionContext
from .cases import (
    OutputValue,
    TestCase,
    case_declarations,
    case_outputs,
    case_return_output,
    convert_inputs_with_default_ptr,
    default_ptr_entries,
    is_return_output,
)
from .stubs import stub_definitions, stub_function_names, stub_prelude


def write_unity_test(context: FunctionContext, cases: list[TestCase], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '#include "unity.h"',
        "#include <stddef.h>",
        "#include <stdbool.h>",
        *stub_prelude(context, cases),
        f'#include "{context.source}"',
        "",
        *stub_definitions(context, cases),
        "void setUp(void) {}",
        "void tearDown(void) {}",
        "",
    ]

    for index, case in enumerate(cases, start=1):
        args = ", ".join(case.args)
        lines.extend(
            [
                f"static void test_{context.name}_{index}(void)",
                "{",
            ]
        )
        for declaration in case_declarations(case):
            lines.append(f"    {declaration}")
        if stub_function_names(context, [case]):
            lines.append(f"    __strut_stub_case_index = {index};")
        actual = "__strut_actual"
        if _normalize_type(context.return_type) == "void":
            lines.append(f"    {context.name}({args});")
        else:
            lines.append(f"    {context.return_type} {actual} = {context.name}({args});")
        return_output = case_return_output(context, case)
        for assertion in _assertions(context, return_output.value if return_output else None, actual):
            lines.append(f"    {assertion}")
        for case_output in case_outputs(context, case):
            if is_return_output(context, case_output.expr):
                continue
            for assertion in _output_assertions(_resolve_output_expr(context, case_output)):
                lines.append(f"    {assertion}")
        lines.extend(["}", ""])

    lines.extend(
        [
            "int main(void)",
            "{",
            "    UNITY_BEGIN();",
        ]
    )
    for index in range(1, len(cases) + 1):
        lines.append(f"    RUN_TEST(test_{context.name}_{index});")
    lines.extend(
        [
            "    return UNITY_END();",
            "}",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def _assertions(context: FunctionContext, expected, actual: str) -> list[str]:
    if expected is None:
        return []
    if context.return_type_kind == "pointer" and isinstance(expected, dict):
        return _pointer_assertions(context, expected, actual)
    if context.return_type_kind == "composite" and isinstance(expected, dict):
        return _struct_assertions(context, expected, actual)
    return [_assertion(context.return_type, expected, actual) + ";"]


def _pointer_assertions(context: FunctionContext, expected: dict, actual: str) -> list[str]:
    lines = []
    if expected.get("is_null"):
        lines.append(f"TEST_ASSERT_NULL({actual});")
        return lines

    lines.append(f"TEST_ASSERT_NOT_NULL({actual});")
    if "value" in expected:
        pointee_type = context.return_pointee_type or _strip_pointer(context.return_type)
        lines.append(_assertion(pointee_type, expected["value"], f"*{actual}") + ";")
    fields = expected.get("fields")
    if isinstance(fields, dict):
        lines.extend(_field_assertions(context.return_fields, fields, actual, pointer=True))
    return lines


def _struct_assertions(context: FunctionContext, expected: dict, actual: str) -> list[str]:
    lines = []
    fields = expected.get("fields")
    if isinstance(fields, dict):
        lines.extend(_field_assertions(context.return_fields, fields, actual, pointer=False))
    return lines


def _output_assertions(output: OutputValue) -> list[str]:
    return _generic_output_assertions(output.expr, output.c_type, output.value)


def _resolve_output_expr(context: FunctionContext, output: OutputValue) -> OutputValue:
    converted = convert_inputs_with_default_ptr(
        [{"expr": output.expr, "type": output.c_type, "value": output.value}],
        default_ptr_entries(context),
    )[0]["expr"]
    return OutputValue(expr=converted, c_type=output.c_type, value=output.value)


def _generic_output_assertions(expr: str, c_type: str, expected) -> list[str]:
    if isinstance(expected, dict):
        lines = []
        if "is_null" in expected:
            if expected.get("is_null"):
                lines.append(f"TEST_ASSERT_NULL({expr});")
                return lines
            lines.append(f"TEST_ASSERT_NOT_NULL({expr});")
        fields = expected.get("fields")
        if isinstance(fields, dict):
            pointer = "*" in c_type or c_type.strip().endswith("]")
            for name, value in fields.items():
                access = f"{expr}->{name}" if pointer else f"{expr}.{name}"
                lines.extend(_generic_output_assertions(access, "int", value))
        if "value" in expected:
            access = f"*{expr}" if "*" in c_type else expr
            lines.append(_assertion(_strip_pointer(c_type), expected["value"], access) + ";")
        return lines
    return [_assertion(c_type, expected, expr) + ";"]


def _field_assertions(fields, expected_fields: dict, base: str, pointer: bool) -> list[str]:
    lines: list[str] = []
    fields_by_name = {field.name: field for field in fields or []}
    for name, expected in expected_fields.items():
        field = fields_by_name.get(name)
        if field is None:
            continue
        access = f"{base}->{name}" if pointer else f"{base}.{name}"
        if field.type_kind == "pointer" and isinstance(expected, dict):
            if expected.get("is_null"):
                lines.append(f"TEST_ASSERT_NULL({access});")
                continue
            lines.append(f"TEST_ASSERT_NOT_NULL({access});")
            if "value" in expected:
                pointee_type = field.pointee_type or _strip_pointer(field.c_type)
                lines.append(_assertion(pointee_type, expected["value"], f"*{access}") + ";")
        elif field.type_kind == "array":
            lines.append(_assertion(field.element_type or field.c_type, expected, f"{access}[0]") + ";")
        elif field.type_kind == "composite" and isinstance(expected, dict):
            lines.extend(_field_assertions(field.fields or [], expected, access, pointer=False))
        else:
            lines.append(_assertion(field.c_type, expected, access) + ";")
    return lines


def _assertion(c_type: str, expected: str | int | float | None, actual: str) -> str:
    expected_value = _literal_for_assertion(c_type, expected)
    normalized = _normalize_type(c_type)
    if normalized in {"char *", "const char *"} or normalized.endswith("[]"):
        return f"TEST_ASSERT_EQUAL_STRING({expected_value}, {actual})"
    if "*" in normalized and expected_value == "NULL":
        return f"TEST_ASSERT_NULL({actual})"
    if normalized == "float":
        return f"TEST_ASSERT_FLOAT_WITHIN(0.0001f, {expected_value}f, {actual})"
    if normalized == "double":
        return f"TEST_ASSERT_DOUBLE_WITHIN(0.000001, {expected_value}, {actual})"
    return f"TEST_ASSERT_EQUAL_INT({expected_value}, {actual})"


def _literal_for_assertion(c_type: str, expected: str | int | float | None) -> str:
    if expected is None:
        return _zero_for_type(c_type)
    if isinstance(expected, bool):
        return "1" if expected else "0"
    if isinstance(expected, (int, float)):
        return str(expected)
    text = str(expected).strip()
    normalized = _normalize_type(c_type)
    if normalized in {"char *", "const char *"} or normalized.endswith("[]"):
        return text if text.startswith('"') else f'"{text}"'
    return text


def _zero_for_type(c_type: str) -> str:
    if _normalize_type(c_type) in {"float", "double"}:
        return "0.0"
    return "0"


def _normalize_type(c_type: str) -> str:
    return " ".join(c_type.replace("const ", "").split())


def _strip_pointer(c_type: str) -> str:
    return c_type.replace("*", "").strip()
