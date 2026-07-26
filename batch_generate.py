#!/usr/bin/env python3
"""
Batch problem generation script for CP-Agent.
Generates problems across all topic × difficulty combinations with checkpoint/resume support.

Strategy:
  - 20 topics × 23 difficulties × 4 problems each = 1840 problems
  - Each problem name: {topic}_d{difficulty}_{index}
  - Checkpoint saved after each problem to enable resume
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
PROBLEMS_DIR = PROJECT_ROOT / "problems"
CHECKPOINT_FILE = PROJECT_ROOT / "batch_checkpoint.json"
LOG_FILE = PROJECT_ROOT / "batch_generate.log"

# Number of problems per (topic, difficulty) combination
REPLICAS_PER_COMBO = 4


# 考点与难度直接来自 config.yaml（延迟读取，避免与配置漂移）
def get_topics() -> list[str]:
    import config
    return list(config.ALGO_TOPICS.keys())


def get_difficulties() -> list[int]:
    import config
    return sorted(config.DIFFICULTY_PRESETS.keys())


def total_problems() -> int:
    return len(get_topics()) * len(get_difficulties()) * REPLICAS_PER_COMBO


def build_queue():
    """Build the full problem queue."""
    queue = []
    for topic in get_topics():
        for diff in get_difficulties():
            for idx in range(1, REPLICAS_PER_COMBO + 1):
                name = f"{topic}_d{diff}_{idx}"
                queue.append({
                    "name": name,
                    "topic": topic,
                    "difficulty": diff,
                    "index": idx,
                })
    return queue


def load_checkpoint():
    """Load checkpoint file. Returns (completed_set, queue)."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
        completed = set(data.get("completed", []))
        failed = data.get("failed", {})
        return completed, failed
    return set(), {}


def save_checkpoint(completed, failed):
    """Save checkpoint to file."""
    data = {
        "completed": sorted(completed),
        "failed": failed,
        "last_updated": datetime.now().isoformat(),
        "total_completed": len(completed),
        "total_target": total_problems(),
        "progress_pct": round(len(completed) / total_problems() * 100, 2),
    }
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def log(msg):
    """Write message to both stdout and log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _load_result_json(name):
    """
    Find and read the result.json for a run: either in the problem dir
    (success) or in the newest problems/failed/{name}_* archive (failure).
    Returns (result_dict_or_None, result_path_or_None).
    """
    direct = PROBLEMS_DIR / name / "result.json"
    if direct.exists():
        try:
            return json.loads(direct.read_text(encoding="utf-8")), direct
        except (json.JSONDecodeError, OSError):
            return None, direct
    archived = sorted((PROBLEMS_DIR / "failed").glob(f"{name}_*/result.json"))
    if archived:
        path = archived[-1]
        try:
            return json.loads(path.read_text(encoding="utf-8")), path
        except (json.JSONDecodeError, OSError):
            return None, path
    return None, None


def run_one_problem(topic, difficulty, name):
    """
    Run main.py to generate one problem. Success is judged from the
    result.json the run writes, not from scraping stdout.
    Returns (success, info_dict).
    """
    cmd = [
        sys.executable, str(PROJECT_ROOT / "main.py"),
        "--topic", topic,
        "--difficulty", str(difficulty),
        "--name", name,
        "--test-count", "30",
        "--stress", "10000",
    ]

    log(f"  Running: {' '.join(cmd)}")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout per problem
        )
        elapsed = time.time() - start

        rj, rj_path = _load_result_json(name)
        success = result.returncode == 0 and rj is not None and rj.get("success") is True

        info = {
            "failure_reason": (rj or {}).get("failure_reason"),
            "iterations": (rj or {}).get("iterations"),
            "tokens": (rj or {}).get("tokens"),
            "result_path": str(rj_path) if rj_path else None,
        }

        if success:
            log(f"  ✅ {name} done in {elapsed:.0f}s "
                f"({info['iterations']} iterations, tokens={info['tokens']})")
        else:
            reason = info["failure_reason"] or f"rc={result.returncode}, result.json={'缺失' if rj is None else rj}"
            log(f"  ❌ {name} failed in {elapsed:.0f}s: {reason}")
            stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
            if stderr_tail.strip():
                log(f"  stderr tail:\n{stderr_tail}")
            stdout_tail = "\n".join(result.stdout.splitlines()[-20:])
            log(f"  stdout tail:\n{stdout_tail}")
            info["failure_reason"] = reason

        return success, info

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log(f"  ⏰ {name} timed out after {elapsed:.0f}s")
        return False, {"failure_reason": "Timeout", "result_path": None}
    except Exception as e:
        elapsed = time.time() - start
        log(f"  💥 {name} exception: {e}")
        import logging
        logging.getLogger("cp_agent.batch").exception("problem %s raised", name)
        return False, {"failure_reason": str(e), "result_path": None}


def main():
    import logutil
    logutil.setup()
    from config import ConfigError
    try:
        _main()
    except ConfigError as e:
        print(f"配置错误: {e}")
        sys.exit(2)


def _main():
    parser = argparse.ArgumentParser(description="CP-Agent batch generation")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N pending problems (smoke test)")
    parser.add_argument("--only", type=str, default=None,
                        help="Only run the problem with this exact name")
    args = parser.parse_args()

    queue = build_queue()
    completed, failed = load_checkpoint()

    log(f"{'='*60}")
    log("CP-Agent Batch Generation")
    log(f"  Topics: {len(get_topics())}")
    log(f"  Difficulties: {len(get_difficulties())}")
    log(f"  Replicas per combo: {REPLICAS_PER_COMBO}")
    log(f"  Total target: {total_problems()}")
    log(f"  Already completed: {len(completed)}")
    log(f"  Previously failed: {len(failed)}")
    log(f"{'='*60}")

    pending = [item for item in queue if item["name"] not in completed]
    if args.only:
        pending = [item for item in pending if item["name"] == args.only]
        if not pending:
            log(f"--only {args.only}: 不在待生成队列中（可能已完成或名字不存在）")
            return
    if args.limit is not None:
        pending = pending[:args.limit]

    if not pending:
        log("🎉 All problems already generated!")
        return

    log(f"  Remaining: {len(pending)}")

    stats = {"success": 0, "failed": 0, "total": len(pending)}
    batch_start = time.time()

    for i, item in enumerate(pending):
        elapsed_total = time.time() - batch_start
        eta = (elapsed_total / max(stats["success"] + stats["failed"], 1)) * (len(pending) - i - 1) if stats["success"] + stats["failed"] > 0 else 0

        log(f"\n[{i+1}/{len(pending)}] ({len(completed)}/{total_problems()} total) "
            f"ETA: {eta/3600:.1f}h | {item['name']} "
            f"(topic={item['topic']}, diff={item['difficulty']}, idx={item['index']})")

        success, info = run_one_problem(
            item["topic"], item["difficulty"], item["name"]
        )

        if success:
            completed.add(item["name"])
            stats["success"] += 1
            # Remove from failed if it was previously failed
            failed.pop(item["name"], None)
        else:
            stats["failed"] += 1
            failed[item["name"]] = {
                "topic": item["topic"],
                "difficulty": item["difficulty"],
                "failure_reason": info.get("failure_reason"),
                "result_path": info.get("result_path"),
                "timestamp": datetime.now().isoformat(),
            }

        # Save checkpoint after each problem
        save_checkpoint(completed, failed)

        # Brief pause between problems to avoid rate limiting
        time.sleep(2)

    # Final summary
    total_time = time.time() - batch_start
    log(f"\n{'='*60}")
    log("BATCH COMPLETE")
    log(f"  Success: {stats['success']}")
    log(f"  Failed: {stats['failed']}")
    log(f"  Total completed: {len(completed)}/{total_problems()}")
    log(f"  Total time: {total_time/3600:.1f} hours")
    log(f"{'='*60}")

    if failed:
        log("Failed problems:")
        for name, info in failed.items():
            log(f"  - {name}: {info.get('failure_reason') or info.get('summary', '')}")


if __name__ == "__main__":
    main()
