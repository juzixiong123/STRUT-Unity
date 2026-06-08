#!/usr/bin/env python3
"""Batch run STRUT-Unity on the _dataset/data_structures corpus."""

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
DATASET = ROOT / "_dataset" / "data_structures"
RESULTS = BUILD / "dataset_results"

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


def discover_targets(dataset_dir: Path, include_main: bool = False) -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []
    for source in discover_files(dataset_dir):
        try:
            functions = list_function_definitions(source, include_main=include_main)
        except Exception as exc:
            print(f"Could not inspect {source.relative_to(dataset_dir)}: {exc}")
            continue
        for function in functions:
            targets.append((source, function.name))
    return targets


def move_build_artifacts(subdir: Path) -> None:
    PROTECTED_NAMES = {"dataset_results", "dataset_run.log", "dataset_run.pid"}
    if not BUILD.exists():
        return
    for item in BUILD.iterdir():
        if item.name in PROTECTED_NAMES:
            continue
        dest = subdir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
            shutil.rmtree(item)
        else:
            shutil.move(str(item), str(dest))


def run_single(
    source: Path,
    function: str,
    case_source: str = "hybrid",
    llm_model: str = "qwen3.5:latest",
    timeout: int = 300,
    no_optimize: bool = False,
) -> dict:
    """Run STRUT-Unity on one source file and move artefacts into a subdir."""
    rel = source.relative_to(DATASET)
    subdir = RESULTS / rel.parent / source.stem / function
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
        "source": str(source),
        "relative": str(rel),
        "function": function,
        "command": cmd,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "timestamp": datetime.now().isoformat(),
    }

    # Move build artefacts into subdir so the next run starts clean
    move_build_artifacts(subdir)

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
    parser.add_argument("--case-source", default="hybrid", choices=["rules", "llm", "hybrid"])
    parser.add_argument("--llm-model", default="qwen3.5:latest")
    parser.add_argument("--limit", type=int, default=None, help="Only run first N function targets (for testing).")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for each function target.")
    parser.add_argument("--include-main", action="store_true", help="Also test main functions. Defaults to skipping main.")
    parser.add_argument("--no-optimize", action="store_true", help="Skip LLM optimization pass after initial run.")
    args = parser.parse_args()

    files = discover_files(DATASET)
    print(f"Discovered {len(files)} source files in {DATASET}")
    for f in files:
        print(f"  - {f.relative_to(DATASET)}")

    targets = discover_targets(DATASET, include_main=args.include_main)
    print(f"\nDiscovered {len(targets)} business function targets")
    for source, function in targets:
        print(f"  - {source.relative_to(DATASET)}::{function}")

    if args.limit:
        targets = targets[:args.limit]
        print(f"\nLimited to first {len(targets)} function targets.")

    overall: list[dict] = []
    for source, function in targets:
        try:
            entry = run_single(source, function, args.case_source, args.llm_model, args.timeout, args.no_optimize)
            overall.append(entry)
        except Exception as exc:
            print(f"EXCEPTION on {source}::{function}: {exc}")
            overall.append({
                "source": str(source),
                "relative": str(source.relative_to(DATASET)),
                "function": function,
                "returncode": -1,
                "exception": str(exc),
                "timestamp": datetime.now().isoformat(),
            })

    # Write master summary
    master = RESULTS / "_master_summary.json"
    master.write_text(json.dumps(overall, indent=2), encoding="utf-8")

    ok = sum(1 for e in overall if e.get("returncode") == 0)
    fail = len(overall) - ok
    print(f"\n{'='*60}")
    print(f"DONE — {ok} passed, {fail} failed out of {len(overall)}")
    print(f"Master summary: {master}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
