#!/usr/bin/env python3
"""Batch run STRUT-Unity on a C dataset corpus."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from strut_unity.analyzer import list_function_definitions

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DEFAULT_DATASET = ROOT / "_dataset" / "data_structures"
DEFAULT_RESULTS = BUILD / "dataset_results"

SKIP_PATTERNS = {
    "_tests.c",
    "test_program.c",
    "main.c",
}


def discover_files(dataset_dir: Path) -> list[Path]:
    files = sorted(dataset_dir.rglob("*.c"))
    keep = []
    for f in files:
        if any(f.name.endswith(pat) or f.name == pat for pat in SKIP_PATTERNS):
            continue
        keep.append(f)
    return keep


def discover_targets(dataset_dir: Path, include_main: bool = False) -> tuple[list[tuple[Path, str]], list[dict]]:
    targets: list[tuple[Path, str]] = []
    failures: list[dict] = []
    for source in discover_files(dataset_dir):
        try:
            functions = list_function_definitions(source, include_main=include_main)
        except Exception as exc:
            print(f"Could not inspect {source.relative_to(dataset_dir)}: {exc}")
            failures.append({"source": str(source), "error": str(exc)})
            continue
        for function in functions:
            targets.append((source, function.name))
    return targets, failures


def move_build_artifacts(subdir: Path, results_dir: Path) -> None:
    protected_names = {"dataset_run.log", "dataset_run.pid"}
    if not BUILD.exists():
        return
    results_dir = results_dir.resolve()
    for item in BUILD.iterdir():
        if item.name in protected_names or item.name.startswith("dataset_"):
            continue
        if _contains_path(item.resolve(), results_dir):
            continue
        dest = subdir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
            shutil.rmtree(item)
        else:
            shutil.move(str(item), str(dest))


def _contains_path(parent: Path, child: Path) -> bool:
    return parent == child or child.is_relative_to(parent)


def parse_pipeline_stdout(stdout: str) -> dict | None:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass

    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(stdout[start : end + 1])
    except json.JSONDecodeError:
        return None


def resolve_cli_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def run_single(
    source: Path,
    function: str,
    dataset_dir: Path,
    results_dir: Path,
    case_source: str = "hybrid",
    llm_model: str = "qwen3.5:latest",
    timeout: int = 300,
    no_optimize: bool = False,
    index: int | None = None,
) -> dict:
    """Run STRUT-Unity on one source file and move artefacts into a subdir."""
    rel = source.relative_to(dataset_dir)
    subdir = results_dir / rel.parent / source.stem / function
    subdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "strut_unity",
        str(source),
        "--function", function,
        "--case-source", case_source,
        "--llm-model", llm_model,
    ]
    if no_optimize:
        cmd.append("--no-optimize")

    print(f"\n{'='*60}")
    print(f"[{datetime.now().isoformat()}] Running: {rel}::{function}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=timeout,
        )
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = -2
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nTimed out after {timeout} seconds."
        timed_out = True

    entry = {
        "index": index,
        "source": str(source),
        "relative": str(rel),
        "function": function,
        "command": cmd,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "parsed": parse_pipeline_stdout(stdout),
        "timed_out": timed_out,
        "timestamp": datetime.now().isoformat(),
    }

    # Move build artefacts into subdir so the next run starts clean
    move_build_artifacts(subdir, results_dir)

    # Also stash a summary json next to the artefacts
    summary_path = subdir / "_run_summary.json"
    summary_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")

    status = "✅ OK" if returncode == 0 else f"❌ FAILED (rc={returncode})"
    print(f"Result: {status}")
    if returncode != 0:
        print(f"stderr: {stderr[-500:]}")
    print(f"Artefacts: {subdir}")

    return entry


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Batch-run STRUT-Unity on data_structures dataset.")
    parser.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET),
        help="Dataset directory to scan. Defaults to _dataset/data_structures.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_RESULTS),
        help="Directory for batch results. Defaults to build/dataset_results.",
    )
    parser.add_argument("--case-source", default="hybrid", choices=["rules", "llm", "hybrid"])
    parser.add_argument("--llm-model", default="qwen3.5:latest")
    parser.add_argument("--limit", type=int, default=None, help="Only run first N function targets (for testing).")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for each function target.")
    parser.add_argument("--include-main", action="store_true", help="Also test main functions. Defaults to skipping main.")
    parser.add_argument("--no-optimize", action="store_true", help="Skip LLM optimization pass after initial run.")
    args = parser.parse_args()

    dataset_dir = resolve_cli_path(args.dataset_dir)
    results_dir = resolve_cli_path(args.output_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    files = discover_files(dataset_dir)
    print(f"Discovered {len(files)} source files in {dataset_dir}")
    for f in files:
        print(f"  - {f.relative_to(dataset_dir)}")

    targets, inspect_failures = discover_targets(dataset_dir, include_main=args.include_main)
    print(f"\nDiscovered {len(targets)} business function targets")
    for source, function in targets:
        print(f"  - {source.relative_to(dataset_dir)}::{function}")

    if args.limit is not None:
        targets = targets[:args.limit]
        print(f"\nLimited to first {len(targets)} function targets.")

    started_at = datetime.now().isoformat()
    meta_path = results_dir / "_meta.json"
    summary_path = results_dir / "_summary.json"
    master_summary_path = results_dir / "_master_summary.json"
    meta = {
        "started_at": started_at,
        "dataset": str(dataset_dir),
        "output_dir": str(results_dir),
        "files": len(files),
        "targets": len(targets),
        "inspect_failures": inspect_failures,
        "case_source": args.case_source,
        "llm_model": args.llm_model,
        "timeout_seconds": args.timeout,
        "completed": 0,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    overall: list[dict] = []
    for index, (source, function) in enumerate(targets, start=1):
        try:
            entry = run_single(
                source,
                function,
                dataset_dir,
                results_dir,
                args.case_source,
                args.llm_model,
                args.timeout,
                args.no_optimize,
                index,
            )
            overall.append(entry)
        except Exception as exc:
            print(f"EXCEPTION on {source}::{function}: {exc}")
            overall.append({
                "index": index,
                "source": str(source),
                "relative": str(source.relative_to(dataset_dir)),
                "function": function,
                "returncode": -1,
                "exception": str(exc),
                "timestamp": datetime.now().isoformat(),
            })
        summary_path.write_text(json.dumps(overall, indent=2), encoding="utf-8")
        master_summary_path.write_text(json.dumps(overall, indent=2), encoding="utf-8")
        meta["completed"] = len(overall)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    meta["finished_at"] = datetime.now().isoformat()
    meta["completed"] = len(overall)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    ok = sum(1 for e in overall if e.get("returncode") == 0)
    fail = len(overall) - ok
    print(f"\n{'='*60}")
    print(f"DONE — {ok} passed, {fail} failed out of {len(overall)}")
    print(f"Summary: {summary_path}")
    print(f"Metadata: {meta_path}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
