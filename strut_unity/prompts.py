from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re

from .analyzer import FunctionContext
from .cases import TestCase, default_ptr_entries, to_original_seed_case


ROOT = Path(__file__).resolve().parents[1]

TEST_CASE_GENERATION_PROMPT = ROOT / "Test Cases Generation Prompts.md"
TEST_SUITE_OPTIMIZATION_PROMPT = ROOT / "Test Suite Optimization Prompts Used By STRUT.md"
JSON_STRUCTURE_PROMPT = ROOT / "json structure.md"


def build_case_generation_messages(
    context: FunctionContext,
    source_code: str,
    seed_cases: list[TestCase],
) -> list[dict]:
    return [
        {"role": "system", "content": _structured_system_prompt()},
        {
            "role": "user",
            "content": _render_template(
                _read_prompt(TEST_CASE_GENERATION_PROMPT),
                context=context,
                source_code=source_code,
                seed_cases=seed_cases,
            ),
        },
    ]


def build_optimization_messages(
    context: FunctionContext,
    source_code: str,
    current_cases: list[TestCase],
    uncovered_conditions: list[str],
) -> list[dict]:
    template = _read_prompt(TEST_SUITE_OPTIMIZATION_PROMPT)
    template = re.sub(r"\n1\..*", "", template, flags=re.DOTALL).rstrip()
    uncovered = "\n".join(f"{index}. {condition}" for index, condition in enumerate(uncovered_conditions, start=1))
    content = "\n\n".join(
        [
            template,
            uncovered or "All extracted branch outcomes are covered.",
            _render_template(
                "Current structured test suite:\n{{ seed case }}\n\n{{ context }}\n\n{{ focal method }}",
                context=context,
                source_code=source_code,
                seed_cases=current_cases,
            ),
        ]
    )
    return [
        {"role": "system", "content": _structured_system_prompt()},
        {"role": "user", "content": content},
    ]


def cases_to_structured_json(context: FunctionContext, cases: list[TestCase]) -> dict:
    return cases_to_strut_json(context, cases, backend=False)


def cases_to_strut_json(context: FunctionContext, cases: list[TestCase], backend: bool = False) -> dict:
    return {
        "func": context.name,
        "file": context.source,
        "cases": [to_original_seed_case(context, case, backend=backend) for case in cases],
        "userVar": [],
        "defaultPTR": default_ptr_entries(context),
        "ios": [],
    }


def _render_template(
    template: str,
    context: FunctionContext,
    source_code: str,
    seed_cases: list[TestCase],
) -> str:
    context_payload = {
        "function": context.name,
        "dependencies": context.dependencies,
        "global_refs": context.global_refs,
        "interface_data": {
            "return_type": context.return_type,
            "parameters": [asdict(parameter) for parameter in context.parameters],
        },
        "branch_conditions": context.branch_conditions,
        "syntax": {
            "tree_sitter_has_error": context.tree_sitter_has_error,
            "tree_sitter_function_count": context.tree_sitter_function_count,
        },
    }
    replacements = {
        "{{ context }}": "Function Context Database:\n" + json.dumps(context_payload, indent=2),
        "{{ focal method }}": "Function Source Code:\n```c\n" + source_code + "\n```",
        "{{ seed case }}": (
            "Required JSON structure:\n"
            + _read_prompt(JSON_STRUCTURE_PROMPT).strip().replace('"stubs"', '"stubins"')
            + "\n\nStructured Seed Cases:\n"
            + json.dumps(cases_to_structured_json(context, seed_cases), indent=2)
        ),
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def _structured_system_prompt() -> str:
    return (
        "You are STRUT's structured test-suite generator for C unit testing. "
        "Return only strict JSON matching the provided JSON structure. "
        "Generate compact branch-covering cases. Use parameter names exactly as shown. "
        "Do not include markdown, comments, prose, or complete test code. "
        "Expected outputs may be omitted or provisional because the runner computes them with an oracle."
    )


def _read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")
