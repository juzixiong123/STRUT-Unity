from __future__ import annotations

from pathlib import Path

from .analyzer import FunctionContext
from .cases import TestCase


def write_unity_test(context: FunctionContext, cases: list[TestCase], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '#include "unity.h"',
        "",
        _prototype(context) + ";",
        "",
        "void setUp(void) {}",
        "void tearDown(void) {}",
        "",
    ]

    for index, case in enumerate(cases, start=1):
        args = ", ".join(str(arg) for arg in case.args)
        lines.extend(
            [
                f"static void test_{context.name}_{index}(void)",
                "{",
                f"    TEST_ASSERT_EQUAL_INT({case.expected}, {context.name}({args}));",
                "}",
                "",
            ]
        )

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


def _prototype(context: FunctionContext) -> str:
    params = ", ".join(f"{p.c_type} {p.name}" for p in context.parameters)
    return f"{context.return_type} {context.name}({params})"

