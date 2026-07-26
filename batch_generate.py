#!/usr/bin/env python3
"""
Batch problem generation script for CP-Agent.
Generates problems across all topic × difficulty combinations with checkpoint/resume support.

Strategy:
  - 20 topics × 23 difficulties × 4 problems each = 1840 problems
  - Each problem name: {topic}_d{difficulty}_{index}
  - Checkpoint saved after each problem to enable resume
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
CHECKPOINT_FILE = PROJECT_ROOT / "batch_checkpoint.json"
LOG_FILE = PROJECT_ROOT / "batch_generate.log"

# All topics from config.yaml
TOPICS = [
    "dp", "tree", "graph", "greedy", "binary_search",
    "data_structure", "math", "string", "geometry", "bit",
    "dsu", "segment_tree", "fenwick", "bfs_dfs", "shortest_path",
    "matching", "flow", "combinatorics", "number_theory", "constructive",
]

# All difficulty levels from config.yaml
DIFFICULTIES = [
    800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700,
    1800, 1900, 2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700,
    2800, 2900, 3000,
]

# Number of problems per (topic, difficulty) combination
REPLICAS_PER_COMBO = 4

TOTAL_PROBLEMS = len(TOPICS) * len(DIFFICULTIES) * REPLICAS_PER_COMBO  # 1840


def build_queue():
    """Build the full problem queue."""
    queue = []
    for topic in TOPICS:
        for diff in DIFFICULTIES:
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
        "total_target": TOTAL_PROBLEMS,
        "progress_pct": round(len(completed) / TOTAL_PROBLEMS * 100, 2),
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


def run_one_problem(topic, difficulty, name):
    """
    Run main.py to generate one problem.
    Returns (success, summary).
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

        success = result.returncode == 0 and "Problem package ready" in result.stdout

        # Extract summary
        summary = ""
        for line in result.stdout.splitlines():
            if "Problem package ready" in line or "Agent 未完成" in line:
                summary = line.strip()
                break

        if success:
            log(f"  ✅ {name} done in {elapsed:.0f}s: {summary}")
        else:
            log(f"  ❌ {name} failed (rc={result.returncode}) in {elapsed:.0f}s")
            # Log last 20 lines of output for debugging
            stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
            log(f"  stderr tail:\n{stderr_tail}")
            stdout_tail = "\n".join(result.stdout.splitlines()[-20:])
            log(f"  stdout tail:\n{stdout_tail}")

        return success, summary

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log(f"  ⏰ {name} timed out after {elapsed:.0f}s")
        return False, "Timeout"
    except Exception as e:
        elapsed = time.time() - start
        log(f"  💥 {name} exception: {e}")
        return False, str(e)


def main():
    queue = build_queue()
    completed, failed = load_checkpoint()

    log(f"{'='*60}")
    log("CP-Agent Batch Generation")
    log(f"  Topics: {len(TOPICS)}")
    log(f"  Difficulties: {len(DIFFICULTIES)}")
    log(f"  Replicas per combo: {REPLICAS_PER_COMBO}")
    log(f"  Total target: {TOTAL_PROBLEMS}")
    log(f"  Already completed: {len(completed)}")
    log(f"  Previously failed: {len(failed)}")
    log(f"{'='*60}")

    pending = [item for item in queue if item["name"] not in completed]

    if not pending:
        log("🎉 All problems already generated!")
        return

    log(f"  Remaining: {len(pending)}")

    stats = {"success": 0, "failed": 0, "total": len(pending)}
    batch_start = time.time()

    for i, item in enumerate(pending):
        elapsed_total = time.time() - batch_start
        eta = (elapsed_total / max(stats["success"] + stats["failed"], 1)) * (len(pending) - i - 1) if stats["success"] + stats["failed"] > 0 else 0

        log(f"\n[{i+1}/{len(pending)}] ({len(completed)}/{TOTAL_PROBLEMS} total) "
            f"ETA: {eta/3600:.1f}h | {item['name']} "
            f"(topic={item['topic']}, diff={item['difficulty']}, idx={item['index']})")

        success, summary = run_one_problem(
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
                "summary": summary,
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
    log(f"  Total completed: {len(completed)}/{TOTAL_PROBLEMS}")
    log(f"  Total time: {total_time/3600:.1f} hours")
    log(f"{'='*60}")

    if failed:
        log("Failed problems:")
        for name, info in failed.items():
            log(f"  - {name}: {info['summary']}")


if __name__ == "__main__":
    main()
