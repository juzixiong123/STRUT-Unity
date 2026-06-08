from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from pathlib import Path

from .analyzer import FunctionContext
from .cases import TestCase


@dataclass(frozen=True)
class BranchOutcome:
    condition: str
    true_covered: bool
    false_covered: bool
    evaluable: bool


def estimate_branch_coverage(context: FunctionContext, cases: list[TestCase]) -> dict:
    outcomes = []
    for condition in context.branch_conditions:
        seen_true = False
        seen_false = False
        evaluable = True
        for case in cases:
            value = _evaluate_condition(condition, context, case)
            if value is None:
                evaluable = False
                continue
            seen_true = seen_true or value
            seen_false = seen_false or not value
        outcomes.append(BranchOutcome(condition, seen_true, seen_false, evaluable))

    total = 2 * len(outcomes)
    covered = sum(int(item.true_covered) + int(item.false_covered) for item in outcomes)
    uncovered = []
    for item in outcomes:
        if not item.true_covered:
            uncovered.append(f"if ({item.condition}): true condition uncovered")
        if not item.false_covered:
            uncovered.append(f"if ({item.condition}): false condition uncovered")
    return {
        "branch_outcomes": [item.__dict__ for item in outcomes],
        "covered_branch_outcomes": covered,
        "total_branch_outcomes": total,
        "estimated_branch_coverage_percent": round((covered / total) * 100, 2) if total else 100.0,
        "uncovered_conditions": uncovered,
    }


def collect_gcov_coverage(
    context: FunctionContext,
    source_path: Path,
    test_path: Path,
    unity_dir: Path,
    build_dir: Path,
    label: str,
) -> dict:
    if shutil.which("gcc") is None or shutil.which("gcov") is None:
        return {"available": False, "reason": "gcc or gcov is not available"}

    coverage_dir = build_dir / f"coverage_{context.name}_{label}"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    test_object = coverage_dir / "test.o"
    unity_object = coverage_dir / "unity.o"
    executable = coverage_dir / f"test_{context.name}"
    compile_steps = [
        [
            "gcc",
            "-std=c11",
            "-O0",
            "--coverage",
            "-I",
            str(unity_dir),
            "-I",
            str(source_path.parent),
            "-DUNITY_INCLUDE_DOUBLE",
            "-c",
            str(test_path),
            "-o",
            str(test_object),
        ],
        [
            "gcc",
            "-std=c11",
            "-O0",
            "--coverage",
            "-I",
            str(unity_dir),
            "-DUNITY_INCLUDE_DOUBLE",
            "-c",
            str(unity_dir / "unity.c"),
            "-o",
            str(unity_object),
        ],
        [
            "gcc",
            "--coverage",
            str(test_object),
            str(unity_object),
            "-o",
            str(executable),
        ],
    ]

    for command in compile_steps:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            return {
                "available": False,
                "reason": "coverage compile failed",
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    run_result = subprocess.run([str(executable)], cwd=coverage_dir, text=True, capture_output=True, check=False)
    if run_result.returncode != 0:
        return {
            "available": False,
            "reason": "coverage test run failed",
            "stdout": run_result.stdout,
            "stderr": run_result.stderr,
        }

    gcov_result = subprocess.run(
        ["gcov", "-b", "-c", "-o", str(test_object), str(source_path)],
        cwd=coverage_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    file_coverage = _parse_gcov_output(gcov_result.stdout, source_path)
    function_coverage = _parse_function_gcov_file(coverage_dir, source_path, context)
    return {
        "available": gcov_result.returncode == 0,
        "directory": str(coverage_dir),
        "stdout": gcov_result.stdout,
        "stderr": gcov_result.stderr,
        **_prefix_keys(file_coverage, "source_file_"),
        **function_coverage,
    }


def _evaluate_condition(condition: str, context: FunctionContext, case: TestCase) -> bool | None:
    expression = re.sub(r"\s*->\s*", "->", condition)
    expression = re.sub(r"(-?\d+(?:\.\d+)?)[fFuUlL]+\b", r"\1", expression)
    replacements = {}
    for value in case.input_values:
        if value.value == "NULL":
            continue
        replacements[value.expr] = value.value
        pointer_index = re.fullmatch(r"([A-Za-z_]\w*)\[0\]", value.expr)
        if pointer_index:
            replacements[f"*{pointer_index.group(1)}"] = value.value
        pointer_deref = re.fullmatch(r"\*([A-Za-z_]\w*)", value.expr)
        if pointer_deref:
            replacements[f"{pointer_deref.group(1)}[0]"] = value.value
    replacements.update(
        {
            binding.parameter: "0" if binding.argument == "NULL" else "1"
            for binding in case.bindings
            if "*" in binding.c_type or binding.argument == "NULL"
        }
    )
    for expr, value in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        expression = expression.replace(re.sub(r"\s*->\s*", "->", expr), value)
    expression = re.sub(r"\bNULL\b", "0", expression)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression)
    identifier_check = re.sub(r"\b(?:and|or|not)\b", "", expression)
    if re.search(r"[A-Za-z_]", identifier_check):
        return None
    try:
        return bool(eval(expression, {"__builtins__": {}}, {}))
    except Exception:
        return None


def _parse_gcov_output(output: str, source_path: Path) -> dict:
    output = _source_gcov_section(output, source_path)
    parsed: dict[str, float | int] = {}
    lines = re.search(r"Lines executed:([0-9.]+)% of ([0-9]+)", output)
    if lines:
        parsed["line_coverage_percent"] = float(lines.group(1))
        parsed["instrumented_lines"] = int(lines.group(2))

    branches = re.search(r"Branches executed:([0-9.]+)% of ([0-9]+)", output)
    if branches:
        parsed["branches_executed_percent"] = float(branches.group(1))
        parsed["instrumented_branches"] = int(branches.group(2))

    taken = re.search(r"Taken at least once:([0-9.]+)% of ([0-9]+)", output)
    if taken:
        parsed["branch_coverage_percent"] = float(taken.group(1))
        if "instrumented_branches" in parsed:
            parsed["covered_branches"] = int(
                round(parsed["instrumented_branches"] * parsed["branch_coverage_percent"] / 100)
            )
    return parsed


def _parse_function_gcov_file(coverage_dir: Path, source_path: Path, context: FunctionContext) -> dict:
    gcov_path = coverage_dir / f"{source_path.name}.gcov"
    if not gcov_path.exists():
        return {
            "coverage_scope": "function",
            "coverage_function": context.name,
            "function_start_line": context.start_line,
            "function_end_line": context.end_line,
            "line_coverage_percent": None,
            "branch_coverage_percent": None,
            "reason": f"gcov file not found: {gcov_path}",
        }

    instrumented_lines = 0
    covered_lines = 0
    instrumented_branches = 0
    covered_branches = 0
    current_source_line: int | None = None

    for raw_line in gcov_path.read_text(encoding="utf-8", errors="replace").splitlines():
        source_match = re.match(r"\s*([^:]+):\s*(\d+):(.*)$", raw_line)
        if source_match:
            count_text = source_match.group(1).strip()
            line_number = int(source_match.group(2))
            current_source_line = line_number
            if context.start_line <= line_number <= context.end_line and _is_instrumented_count(count_text):
                instrumented_lines += 1
                if _is_executed_count(count_text):
                    covered_lines += 1
            continue

        if current_source_line is None or not (context.start_line <= current_source_line <= context.end_line):
            continue
        branch_match = re.match(r"\s*branch\s+\d+\s+(.*)$", raw_line)
        if not branch_match:
            continue
        instrumented_branches += 1
        if _branch_was_taken(branch_match.group(1)):
            covered_branches += 1

    return {
        "coverage_scope": "function",
        "coverage_function": context.name,
        "function_start_line": context.start_line,
        "function_end_line": context.end_line,
        "covered_lines": covered_lines,
        "instrumented_lines": instrumented_lines,
        "line_coverage_percent": round((covered_lines / instrumented_lines) * 100, 2)
        if instrumented_lines
        else 100.0,
        "covered_branches": covered_branches,
        "instrumented_branches": instrumented_branches,
        "branch_coverage_percent": round((covered_branches / instrumented_branches) * 100, 2)
        if instrumented_branches
        else 100.0,
    }


def _is_instrumented_count(count_text: str) -> bool:
    return count_text not in {"-", ""}


def _is_executed_count(count_text: str) -> bool:
    if count_text.startswith("#") or count_text.startswith("="):
        return False
    normalized = count_text.rstrip("*")
    return normalized.isdigit() and int(normalized) > 0


def _branch_was_taken(branch_text: str) -> bool:
    if "never executed" in branch_text:
        return False
    taken = re.search(r"taken\s+(-?\d+)", branch_text)
    if taken:
        return int(taken.group(1)) > 0
    percent = re.search(r"taken\s+([0-9.]+)%", branch_text)
    if percent:
        return float(percent.group(1)) > 0
    return False


def _prefix_keys(data: dict, prefix: str) -> dict:
    return {f"{prefix}{key}": value for key, value in data.items()}


def _source_gcov_section(output: str, source_path: Path) -> str:
    marker = f"File '{source_path}'"
    start = output.find(marker)
    if start == -1:
        return output
    next_file = output.find("\nFile '", start + len(marker))
    if next_file == -1:
        return output[start:]
    return output[start:next_file]
