from __future__ import annotations

from dataclasses import dataclass
import re

from .analyzer import FunctionContext


@dataclass(frozen=True)
class TestCase:
    desc: str
    args: tuple[int, ...]
    expected: int | None = None


def generate_seed_cases(context: FunctionContext) -> list[TestCase]:
    int_params = [p for p in context.parameters if _is_int_like(p.c_type)]
    if len(int_params) != len(context.parameters):
        raise NotImplementedError("The demo connector currently supports integer scalar parameters.")

    values_by_name: dict[str, set[int]] = {p.name: {0, 1, -1} for p in int_params}
    for condition in context.branch_conditions:
        for name, op, raw_value in re.findall(r"\b([A-Za-z_]\w*)\b\s*(<=|>=|==|!=|<|>)\s*(-?\d+)", condition):
            if name in values_by_name:
                values_by_name[name].update(_boundary_values(op, int(raw_value)))
        for raw_value, op, name in re.findall(r"(-?\d+)\s*(<=|>=|==|!=|<|>)\s*\b([A-Za-z_]\w*)\b", condition):
            if name in values_by_name:
                values_by_name[name].update(_boundary_values(_flip(op), int(raw_value)))

    cases: list[TestCase] = []
    seen: set[tuple[int, ...]] = set()
    defaults = [0 for _ in int_params]
    for param_index, param in enumerate(int_params):
        for value in sorted(values_by_name[param.name]):
            args = defaults.copy()
            args[param_index] = value
            arg_tuple = tuple(args)
            if arg_tuple not in seen:
                seen.add(arg_tuple)
                cases.append(TestCase(desc=f"{param.name}={value}", args=arg_tuple))
    return cases


def _is_int_like(c_type: str) -> bool:
    normalized = " ".join(c_type.split())
    return normalized in {
        "int",
        "signed int",
        "short",
        "short int",
        "long",
        "long int",
        "long long",
        "long long int",
    }


def _boundary_values(op: str, value: int) -> set[int]:
    if op in {"<", ">=", "!="}:
        return {value - 1, value}
    if op in {">", "<="}:
        return {value, value + 1}
    return {value, value - 1, value + 1}


def _flip(op: str) -> str:
    return {
        "<": ">",
        ">": "<",
        "<=": ">=",
        ">=": "<=",
        "==": "==",
        "!=": "!=",
    }[op]

