from __future__ import annotations

from pathlib import Path

from .analyzer import FunctionContext
from .cases import TestCase


def write_unity_test(context: FunctionContext, cases: list[TestCase], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '#include "unity.h"',
        "#include <stddef.h>",
        f'#include "{context.source}"',
        "",
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
        for declaration in _case_declarations(case):
            lines.append(f"    {declaration}")
        for assertion in _assertions(context, case.expected, f"{context.name}({args})"):
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


def _assertions(context: FunctionContext, expected, actual: str) -> list[str]:
    if context.return_type_kind == "pointer" and isinstance(expected, dict):
        return _pointer_assertions(context, expected, actual)
    if context.return_type_kind == "composite" and isinstance(expected, dict):
        return _struct_assertions(context, expected, actual)
    return [_assertion(context.return_type, expected, actual) + ";"]


def _pointer_assertions(context: FunctionContext, expected: dict, actual: str) -> list[str]:
    lines = [f"{context.return_type} __strut_actual = {actual};"]
    if expected.get("is_null"):
        lines.append("TEST_ASSERT_NULL(__strut_actual);")
        return lines

    lines.append("TEST_ASSERT_NOT_NULL(__strut_actual);")
    if "value" in expected:
        pointee_type = context.return_pointee_type or _strip_pointer(context.return_type)
        lines.append(_assertion(pointee_type, expected["value"], "*__strut_actual") + ";")
    fields = expected.get("fields")
    if isinstance(fields, dict):
        lines.extend(_field_assertions(context.return_fields, fields, "__strut_actual", pointer=True))
    return lines


def _struct_assertions(context: FunctionContext, expected: dict, actual: str) -> list[str]:
    lines = [f"{context.return_type} __strut_actual = {actual};"]
    fields = expected.get("fields")
    if isinstance(fields, dict):
        lines.extend(_field_assertions(context.return_fields, fields, "__strut_actual", pointer=False))
    return lines


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
    expected_value = expected if expected is not None else _zero_for_type(c_type)
    normalized = _normalize_type(c_type)
    if normalized == "float":
        return f"TEST_ASSERT_FLOAT_WITHIN(0.0001f, {expected_value}f, {actual})"
    if normalized == "double":
        return f"TEST_ASSERT_DOUBLE_WITHIN(0.000001, {expected_value}, {actual})"
    return f"TEST_ASSERT_EQUAL_INT({expected_value}, {actual})"


def _zero_for_type(c_type: str) -> str:
    if _normalize_type(c_type) in {"float", "double"}:
        return "0.0"
    return "0"


def _normalize_type(c_type: str) -> str:
    return " ".join(c_type.replace("const ", "").split())


def _strip_pointer(c_type: str) -> str:
    return c_type.replace("*", "").strip()
