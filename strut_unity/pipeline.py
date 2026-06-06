from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from dataclasses import replace

from .analyzer import analyze_function, FunctionContext
from .cases import generate_seed_cases, TestCase
from .unity_writer import write_unity_test


ROOT = Path(__file__).resolve().parents[1]


def run_pipeline(source: str | Path, function: str | None = None) -> dict:
    source_path = Path(source).resolve()
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)

    context = analyze_function(source_path, function)
    cases = generate_seed_cases(context)
    cases = _fill_expected_values(context, cases, source_path, build_dir)

    context_path = build_dir / f"{context.name}_context.json"
    case_path = build_dir / f"{context.name}_cases.json"
    test_path = build_dir / f"test_{context.name}.c"
    exe_path = build_dir / f"test_{context.name}"

    context_path.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")
    case_path.write_text(json.dumps([case.__dict__ for case in cases], indent=2), encoding="utf-8")
    write_unity_test(context, cases, test_path)

    compile_cmd = [
        "clang",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-I",
        str(ROOT / "unity"),
        str(test_path),
        str(source_path),
        str(ROOT / "unity" / "unity.c"),
        "-o",
        str(exe_path),
    ]
    compile_result = subprocess.run(compile_cmd, text=True, capture_output=True, check=False)
    if compile_result.returncode != 0:
        return {
            "context": str(context_path),
            "cases": str(case_path),
            "test": str(test_path),
            "compile_cmd": compile_cmd,
            "compile_returncode": compile_result.returncode,
            "compile_stdout": compile_result.stdout,
            "compile_stderr": compile_result.stderr,
        }

    run_result = subprocess.run([str(exe_path)], text=True, capture_output=True, check=False)
    return {
        "context": str(context_path),
        "cases": str(case_path),
        "test": str(test_path),
        "executable": str(exe_path),
        "compile_cmd": compile_cmd,
        "compile_returncode": compile_result.returncode,
        "compile_stdout": compile_result.stdout,
        "compile_stderr": compile_result.stderr,
        "run_returncode": run_result.returncode,
        "run_stdout": run_result.stdout,
        "run_stderr": run_result.stderr,
    }


def _fill_expected_values(
    context: FunctionContext,
    cases: list[TestCase],
    source_path: Path,
    build_dir: Path,
) -> list[TestCase]:
    if context.return_type != "int":
        raise NotImplementedError("The demo connector currently supports int return values.")

    oracle_c = build_dir / f"oracle_{context.name}.c"
    oracle_exe = build_dir / f"oracle_{context.name}"
    oracle_c.write_text(_oracle_source(context, cases), encoding="utf-8")
    cmd = ["clang", "-std=c11", str(oracle_c), str(source_path), "-o", str(oracle_exe)]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Oracle compile failed:\n{result.stderr}")
    output = subprocess.run([str(oracle_exe)], text=True, capture_output=True, check=True).stdout
    expected_values = [int(line) for line in output.splitlines() if line.strip()]
    return [replace(case, expected=expected) for case, expected in zip(cases, expected_values)]


def _oracle_source(context: FunctionContext, cases: list[TestCase]) -> str:
    params = ", ".join(f"{p.c_type} {p.name}" for p in context.parameters)
    lines = [
        "#include <stdio.h>",
        f"{context.return_type} {context.name}({params});",
        "int main(void)",
        "{",
    ]
    for case in cases:
        args = ", ".join(str(arg) for arg in case.args)
        lines.append(f'    printf("%d\\n", {context.name}({args}));')
    lines.extend(["    return 0;", "}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and run Unity tests from a C function.")
    parser.add_argument("source", help="C source file containing the function under test")
    parser.add_argument("--function", "-f", default=None, help="Function name. Defaults to first definition.")
    args = parser.parse_args()
    result = run_pipeline(args.source, args.function)
    print(json.dumps(result, indent=2))
    return 0 if result.get("compile_returncode") == 0 and result.get("run_returncode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

