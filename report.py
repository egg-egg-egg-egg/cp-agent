"""
Structured generation results: result.json written into each problem directory.

batch_generate.py and other callers read result.json to judge success instead
of scraping stdout.
"""
import json
import os
from pathlib import Path

RESULT_VERSION = 1
RESULT_FILENAME = "result.json"


def check_artifacts(problem_dir: Path) -> dict:
    """Inspect a problem directory for the required generation artifacts."""
    problem_dir = Path(problem_dir)
    problem_md = problem_dir / "problem.md"
    problem_md_ok = problem_md.exists() and problem_md.stat().st_size > 0
    solution_ok = (problem_dir / "solution.cpp").exists()

    inputs_dir = problem_dir / "inputs"
    outputs_dir = problem_dir / "outputs"
    input_stems = {f.stem for f in inputs_dir.glob("*.in")} if inputs_dir.exists() else set()
    output_stems = {f.stem for f in outputs_dir.glob("*.out")} if outputs_dir.exists() else set()
    counts_match = bool(input_stems) and input_stems == output_stems

    return {
        "problem_md": problem_md_ok,
        "solution_cpp": solution_ok,
        "inputs": len(input_stems),
        "outputs": len(output_stems),
        "counts_match": counts_match,
        "complete": problem_md_ok and solution_ok and counts_match,
    }


def write_result_json(problem_dir: Path, payload: dict) -> Path:
    """Atomically write result.json into the problem directory."""
    problem_dir = Path(problem_dir)
    payload = {"version": RESULT_VERSION, **payload}
    target = problem_dir / RESULT_FILENAME
    tmp = problem_dir / (RESULT_FILENAME + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_result_json(problem_dir: Path) -> dict | None:
    """Read result.json if present and valid, else None."""
    target = Path(problem_dir) / RESULT_FILENAME
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
