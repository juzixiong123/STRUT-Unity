from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .analyzer import FunctionContext


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
            "block_coverage_percent": None,
            "reason": f"gcov file not found: {gcov_path}",
        }

    instrumented_lines = 0
    covered_lines = 0
    instrumented_branches = 0
    covered_branches = 0
    block_coverage_percent: float | None = None
    uncovered_branches: list[dict] = []
    current_source_line: int | None = None
    current_source_text = ""
    source_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()

    for raw_line in gcov_path.read_text(encoding="utf-8", errors="replace").splitlines():
        function_match = re.match(r"\s*function\s+(\S+)\s+called\b.*\bblocks executed\s+([0-9.]+)%", raw_line)
        if function_match and function_match.group(1) == context.name:
            block_coverage_percent = float(function_match.group(2))
            continue

        source_match = re.match(r"\s*([^:]+):\s*(\d+):(.*)$", raw_line)
        if source_match:
            count_text = source_match.group(1).strip()
            line_number = int(source_match.group(2))
            current_source_line = line_number
            current_source_text = source_match.group(3)
            if context.start_line <= line_number <= context.end_line and _is_instrumented_count(count_text):
                instrumented_lines += 1
                if _is_executed_count(count_text):
                    covered_lines += 1
            continue

        if current_source_line is None or not (context.start_line <= current_source_line <= context.end_line):
            continue
        branch_match = re.match(r"\s*branch\s+(\d+)\s+(.*)$", raw_line)
        if not branch_match:
            continue
        branch_index = int(branch_match.group(1))
        branch_text = branch_match.group(2)
        instrumented_branches += 1
        if _branch_was_taken(branch_text):
            covered_branches += 1
        else:
            uncovered_branches.append(
                _uncovered_branch(
                    source_lines,
                    current_source_line,
                    current_source_text,
                    branch_index,
                    branch_text,
                )
            )

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
        "block_coverage_percent": block_coverage_percent,
        "uncovered_branches": uncovered_branches,
        "uncovered_conditions": [item["description"] for item in uncovered_branches],
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


def _uncovered_branch(
    source_lines: list[str],
    line_number: int,
    source_text: str,
    branch_index: int,
    branch_text: str,
) -> dict:
    control = _control_for_line(source_lines, line_number)
    keyword = control[0] if control else "condition"
    condition = control[1] if control else " ".join(source_text.split())
    direction = _branch_direction(branch_index)
    description = (
        f"line {line_number}: {keyword} ({condition}): {direction} branch uncovered "
        f"(gcov branch {branch_index}, {branch_text})"
    )
    return {
        "line": line_number,
        "branch_index": branch_index,
        "branch_text": branch_text,
        "control": keyword,
        "condition": condition,
        "direction": direction,
        "description": description,
    }


def _branch_direction(branch_index: int) -> str:
    return "true/fallthrough" if branch_index % 2 == 0 else "false/non-fallthrough"


def _control_for_line(source_lines: list[str], line_number: int) -> tuple[str, str] | None:
    if line_number < 1 or line_number > len(source_lines):
        return None
    statement = _control_statement_from_line(source_lines, line_number)
    return _extract_control_condition(statement)


def _control_statement_from_line(source_lines: list[str], line_number: int) -> str:
    pieces = []
    depth = 0
    saw_open = False
    for line in source_lines[line_number - 1 :]:
        pieces.append(line.strip())
        for char in line:
            if char == "(":
                depth += 1
                saw_open = True
            elif char == ")" and saw_open:
                depth -= 1
                if depth <= 0:
                    return " ".join(pieces)
        if saw_open and depth <= 0:
            break
    return " ".join(pieces)


def _extract_control_condition(statement: str) -> tuple[str, str] | None:
    match = re.search(r"\b(if|while|for|switch)\s*\(", statement)
    if not match:
        return None
    keyword = match.group(1)
    start = statement.find("(", match.end() - 1)
    if start == -1:
        return None
    depth = 0
    pieces = []
    for char in statement[start:]:
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                return keyword, " ".join("".join(pieces).split())
        if depth >= 1:
            pieces.append(char)
    return None


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
