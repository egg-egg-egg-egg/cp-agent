#!/usr/bin/env python3
"""
CP-Agent CLI: Automated Competitive Programming Problem Generator

Usage:
    # Agent 模式（默认）— LLM 自主调用 tool 完成全流程
    python main.py --topic dp --difficulty medium
    python main.py --topic graph --difficulty hard --provider deepseek
    python main.py --topic tree --difficulty easy --provider mimo --max-iterations 50

    # Pipeline 模式 — 直接运行已有题目的流水线（无 LLM）
    python main.py --pipeline problems/my_problem/

    # 信息查询
    python main.py --list-topics
    python main.py --list-providers
"""
import argparse
import sys
from pathlib import Path

import config
from config import ConfigError


def list_topics():
    print("\nAvailable algorithm topics:")
    print("-" * 50)
    for key, desc in config.ALGO_TOPICS.items():
        print(f"  {key:<20} {desc}")
    print()


def list_difficulties():
    print("\nAvailable difficulty levels (Codeforces rating):")
    print("-" * 60)
    for score, desc in config.DIFFICULTY_PRESETS.items():
        print(f"  {score:<6} {desc}")
    print()


def list_providers():
    print("\nAvailable LLM providers:")
    print("-" * 80)
    print(f"  Current nowModel: {config.get_now_model() or 'N/A'}")
    print()
    print(f"  {'Protocol':<12} {'Provider':<14} {'Default Model':<30} {'Env Var':<22} {'Enabled'}")
    print(f"  {'-'*10:<12} {'-'*12:<14} {'-'*28:<30} {'-'*20:<22} {'-'*7}")
    for protocol, providers in config.LLM_PROVIDERS.items():
        for name, cfg in providers.items():
            enabled = "✓" if cfg.get("enabled", True) else "✗"
            env = cfg.get("env_key", "") or "N/A"
            print(f"  {protocol:<12} {name:<14} {cfg['default_model']:<30} {env:<22} {enabled}")
    print()
    print("  Tips:")
    print("  - Set enabled: false in config.yaml to disable a provider")
    print("  - For custom endpoints: --provider openai --base-url <url> --api-key <key>")
    print()


def main():
    import logutil
    logutil.setup()
    try:
        _main()
    except ConfigError as e:
        print(f"配置错误: {e}")
        sys.exit(2)


def _main():
    parser = argparse.ArgumentParser(
        description="CP-Agent: Automated CP Problem Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Problem options ──
    parser.add_argument("--topic", "-t", type=str,
                        help="Algorithm topic (e.g., dp, graph, tree, greedy)")
    parser.add_argument("--difficulty", "-d", type=int, default=1500,
                        help="Codeforces rating 800-3000 (default: 1500)")
    parser.add_argument("--name", "-n", type=str, default=None,
                        help="Problem name (directory name)")
    parser.add_argument("--extra", "-e", type=str, default="",
                        help="Extra requirements for problem generation")
    parser.add_argument("--idea", type=str, default="",
                        help="题意完善模式：给定大致题意，系统补全为完整题目（--topic 变为可选）")
    parser.add_argument("--idea-file", type=str, default=None,
                        help="从文件读取题意/题面草稿（与 --idea 二选一）")
    parser.add_argument("--allow-dup", action="store_true",
                        help="完善模式下题意与题库撞题时继续生成（默认中止）")

    # ── LLM provider options ──
    enabled_names = config.list_enabled_provider_choices()
    parser.add_argument("--provider", "-P", type=str, default=None,
                        choices=enabled_names,
                        help="LLM provider override (default: config.yaml nowModel)")
    parser.add_argument("--model", "-m", type=str, default=None,
                        help="Model name (default: provider's default)")
    parser.add_argument("--base-url", type=str, default=None,
                        help="Custom API base URL (overrides provider default)")
    parser.add_argument("--api-key", type=str, default=None,
                        help="API key (overrides env var)")
    parser.add_argument("--max-iterations", type=int, default=30,
                        help="Max agent loop iterations (default: 30)")
    parser.add_argument("--max-tokens", type=int, default=16000,
                        help="Max tokens per LLM call (default: 16000)")

    # ── Pipeline options ──
    parser.add_argument("--test-count", type=int, default=30,
                        help="Number of test cases to generate (default: 30)")
    parser.add_argument("--stress", "-s", type=int, default=10000,
                        help="Stress test iterations (default: 10000)")
    parser.add_argument("--pipeline", "-p", type=str, default=None,
                        help="Run pipeline only on existing problem directory (no LLM)")

    # ── Export options ──
    parser.add_argument("--export", type=str, default=None, metavar="FORMATS",
                        help="Export a problem dir to judge packages: luogu,hydro,polygon or all "
                             "(use with --problem-dir; no LLM)")
    parser.add_argument("--problem-dir", type=str, default=None,
                        help="Problem directory for --export")
    parser.add_argument("--export-after", type=str, default=None, metavar="FORMATS",
                        help="After successful generation, auto-export to these formats (or 'all')")

    # ── Info commands ──
    parser.add_argument("--list-topics", action="store_true",
                        help="List available algorithm topics")
    parser.add_argument("--list-difficulties", action="store_true",
                        help="List available difficulty levels")
    parser.add_argument("--list-providers", action="store_true",
                        help="List available LLM providers")

    args = parser.parse_args()

    if args.list_topics:
        list_topics()
        return

    if args.list_difficulties:
        list_difficulties()
        return

    if args.list_providers:
        list_providers()
        return

    # ── Export mode (no LLM) ──
    if args.export:
        if not args.problem_dir:
            parser.error("--export requires --problem-dir")
        from export import FORMATS, export_problem
        formats = list(FORMATS) if args.export == "all" else [
            f.strip() for f in args.export.split(",") if f.strip()]
        export_problem(Path(args.problem_dir), formats)
        return

    # ── Pipeline mode (no LLM) ──
    if args.pipeline:
        from agent import run_pipeline_only
        problem_dir = Path(args.pipeline)
        if not problem_dir.exists():
            print(f"Error: {problem_dir} does not exist")
            sys.exit(1)
        run_pipeline_only(problem_dir, args.test_count, args.stress)
        return

    # ── Agent mode (default) ──
    if args.idea and args.idea_file:
        parser.error("--idea 与 --idea-file 只能二选一")
    idea = args.idea
    if args.idea_file:
        idea_path = Path(args.idea_file)
        if not idea_path.exists():
            parser.error(f"--idea-file 不存在: {idea_path}")
        idea = idea_path.read_text(encoding="utf-8")
    if not args.topic and not idea:
        parser.error("--topic is required (or use --idea/--idea-file, --pipeline, --list-topics)")

    from agent import generate_problem
    result = generate_problem(
        topic=args.topic or "",
        difficulty=args.difficulty,
        extra=args.extra,
        idea=idea,
        allow_dup=args.allow_dup,
        problem_name=args.name,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        max_tokens=args.max_tokens,
        max_iterations=args.max_iterations,
        test_count=args.test_count,
        stress_iterations=args.stress,
    )

    if result.get("success"):
        if args.export_after and result.get("problem_dir"):
            from export import FORMATS, export_problem
            formats = list(FORMATS) if args.export_after == "all" else [
                f.strip() for f in args.export_after.split(",") if f.strip()]
            export_problem(Path(result["problem_dir"]), formats)
    else:
        print(f"\n⚠️  Agent 未完成: {result.get('summary', 'unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
