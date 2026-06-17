from __future__ import annotations

import json
import re

from .analyzer import FunctionContext
from .cases import OutputValue, StubIn, TestCase, case_from_args, case_from_structured_inputs
from .llm_client import OpenAICompatibleClient
from .prompts import build_case_generation_messages, build_optimization_messages


def generate_llm_cases(
    context: FunctionContext,
    source_code: str,
    seed_cases: list[TestCase] | None = None,
    client: OpenAICompatibleClient | None = None,
) -> tuple[list[TestCase], list[dict], str]:
    messages = build_case_generation_messages(context, source_code, seed_cases or [])
    llm = client or OpenAICompatibleClient()
    response = llm.chat_completion(messages)
    return parse_llm_cases(response, context), messages, response


def generate_optimized_llm_cases(
    context: FunctionContext,
    source_code: str,
    current_cases: list[TestCase],
    uncovered_conditions: list[str],
    client: OpenAICompatibleClient | None = None,
) -> tuple[list[TestCase], list[dict], str]:
    messages = build_optimization_messages(context, source_code, current_cases, uncovered_conditions)
    llm = client or OpenAICompatibleClient()
    response = llm.chat_completion(messages)
    return parse_llm_cases(response, context), messages, response


def parse_llm_cases(response: str, context: FunctionContext) -> list[TestCase]:
    payload = json.loads(_extract_json(response))
    raw_cases = payload["cases"]
    cases: list[TestCase] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for index, item in enumerate(raw_cases, start=1):
        desc = str(item.get("desc") or f"llm case {index}")
        case = _case_from_item(item, context, desc)
        identity = case.identity()
        if identity in seen:
            continue
        seen.add(identity)
        cases.append(case)
    if not cases:
        raise ValueError("LLM returned no usable cases")
    return cases


def _case_from_item(item: dict, context: FunctionContext, desc: str) -> TestCase:
    stubins = _parse_stubins(item)
    if "args" in item:
        return case_from_args(context, desc, list(item["args"]), stubins=stubins)

    inputs = item.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("LLM case must contain either args or inputs")

    values_by_expr = {str(entry.get("expr")): entry.get("value") for entry in inputs if isinstance(entry, dict)}
    return case_from_structured_inputs(context, desc, values_by_expr, stubins=stubins)


def _parse_stubins(item: dict) -> tuple[StubIn, ...]:
    raw_stubs = item.get("stubins", item.get("stubs", []))
    if not isinstance(raw_stubs, list):
        return ()
    stubins = []
    for raw in raw_stubs:
        if not isinstance(raw, dict):
            continue
        called = raw.get("called function") or raw.get("called_function") or raw.get("function") or ""
        changed = raw.get("changed variable", raw.get("changed_variable", raw.get("outputs", [])))
        if not isinstance(changed, list):
            changed = []
        values = tuple(_output_value(entry) for entry in changed if isinstance(entry, dict))
        stubins.append(StubIn(called_function=str(called), changed_variables=values))
    return tuple(stubins)


def _output_value(entry: dict) -> OutputValue:
    return OutputValue(
        expr=str(entry.get("expr", "")),
        c_type=str(entry.get("type", entry.get("c_type", "int"))),
        value=entry.get("value"),
    )


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    raise ValueError("Could not find a JSON object in LLM response")
