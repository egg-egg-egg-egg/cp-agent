"""
Export a generated problem directory to judge-specific packages.

Formats:
  luogu   — export/luogu/: flat data.zip (NN.in/NN.out at zip root, matching
            洛谷题库上传格式), statement.md, checker.cpp (SPJ only), README.txt.
            HustOJ accepts the same flat in/out zip.
  hydro   — export/hydro/<slug>.zip: Hydro import package
            (<slug>/problem.yaml + problem_zh.md + testdata/ with config.yaml)
  polygon — export/polygon/<slug>/: upload-ready layout for Codeforces Polygon
            (statements/, files/, solutions/, tests/ with NN / NN.a naming)

Usage:
  python export.py problems/<name> --format luogu,hydro,polygon|all [--out DIR]
"""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path

import yaml

import config

FORMATS = ("luogu", "hydro", "polygon")


def load_metadata(problem_dir: Path) -> dict:
    """Read problem.yaml; degrade to scanned defaults with a warning if absent."""
    path = problem_dir / "problem.yaml"
    if path.exists():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            print(f"⚠ problem.yaml 解析失败（{e}），使用推断元数据")
    else:
        print("⚠ 无 problem.yaml，使用推断元数据（建议先通过 write_metadata 生成）")
    return {
        "title": problem_dir.name,
        "slug": problem_dir.name,
        "algorithm_tags": [],
        "difficulty": None,
        "time_limit_ms": config.DEFAULT_TIME_LIMIT_MS,
        "memory_limit_mb": config.DEFAULT_MEMORY_LIMIT_MB,
        "checker": {"type": "testlib" if (problem_dir / "checker.cpp").exists() else "builtin"},
    }


def _paired_cases(problem_dir: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for f in sorted((problem_dir / "inputs").glob("*.in")):
        out = problem_dir / "outputs" / (f.stem + ".out")
        if out.exists():
            pairs.append((f, out))
        else:
            print(f"⚠ 跳过 {f.name}：缺少对应输出")
    return pairs


def _is_spj(meta: dict) -> bool:
    return (meta.get("checker") or {}).get("type") == "testlib"


# ─── 洛谷 ────────────────────────────────────────────────────────────────────

def export_luogu(problem_dir: Path, meta: dict, out_root: Path) -> Path:
    dest = out_root / "luogu"
    dest.mkdir(parents=True, exist_ok=True)
    pairs = _paired_cases(problem_dir)

    zip_path = dest / "data.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fin, fout in pairs:
            zf.write(fin, fin.name)      # flat: NN.in / NN.out at zip root
            zf.write(fout, fout.name)

    shutil.copy(problem_dir / "problem.md", dest / "statement.md")
    spj = _is_spj(meta)
    if spj and (problem_dir / "checker.cpp").exists():
        shutil.copy(problem_dir / "checker.cpp", dest / "checker.cpp")

    (dest / "README.txt").write_text(
        f"""洛谷题库上传说明 — {meta.get('title', '')}
1. data.zip 为扁平测试数据包（{len(pairs)} 组），在题目管理→测试数据处上传
2. 测试点配置：时间限制 {meta.get('time_limit_ms')}ms，内存限制 {meta.get('memory_limit_mb')}MB（需在洛谷测试点配置中手动设置）
3. 题面：statement.md（洛谷 Markdown/LaTeX 兼容，直接粘贴）
4. Special Judge: {'是 — 上传 checker.cpp（testlib，洛谷自带 testlib.h）' if spj else '否 — 使用默认逐行比较'}
5. HustOJ 可复用同一份扁平 in/out 数据
""", encoding="utf-8")
    print(f"✓ 洛谷包: {dest}（data.zip {len(pairs)} 组）")
    return dest


# ─── Hydro ───────────────────────────────────────────────────────────────────

def export_hydro(problem_dir: Path, meta: dict, out_root: Path) -> Path:
    slug = meta.get("slug") or problem_dir.name
    stage = out_root / "hydro" / slug
    if stage.exists():
        shutil.rmtree(stage)
    testdata = stage / "testdata"
    testdata.mkdir(parents=True)
    pairs = _paired_cases(problem_dir)

    # Hydro problem.yaml（与本项目 problem.yaml 是不同 schema，做字段映射）
    hydro_meta = {
        "title": meta.get("title", slug),
        "tag": meta.get("algorithm_tags") or [],
    }
    if meta.get("difficulty"):
        hydro_meta["difficulty"] = meta["difficulty"]
    (stage / "problem.yaml").write_text(
        yaml.safe_dump(hydro_meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copy(problem_dir / "problem.md", stage / "problem_zh.md")

    for fin, fout in pairs:
        shutil.copy(fin, testdata / fin.name)
        shutil.copy(fout, testdata / fout.name)

    spj = _is_spj(meta)
    judge_cfg = {
        "time": f"{meta.get('time_limit_ms', 1000)}ms",
        "memory": f"{meta.get('memory_limit_mb', 256)}m",
    }
    if spj and (problem_dir / "checker.cpp").exists():
        shutil.copy(problem_dir / "checker.cpp", testdata / "checker.cc")
        judge_cfg["checker_type"] = "testlib"
        judge_cfg["checker"] = "checker.cc"
    (testdata / "config.yaml").write_text(
        yaml.safe_dump(judge_cfg, sort_keys=False), encoding="utf-8")

    zip_path = out_root / "hydro" / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, f"{slug}/{f.relative_to(stage)}")
    print(f"✓ Hydro 包: {zip_path}（{len(pairs)} 组，可在 Hydro 题目导入处上传）")
    return zip_path


# ─── Polygon ─────────────────────────────────────────────────────────────────

def export_polygon(problem_dir: Path, meta: dict, out_root: Path) -> Path:
    slug = meta.get("slug") or problem_dir.name
    dest = out_root / "polygon" / slug
    if dest.exists():
        shutil.rmtree(dest)
    pairs = _paired_cases(problem_dir)

    (dest / "statements" / "chinese").mkdir(parents=True)
    shutil.copy(problem_dir / "problem.md", dest / "statements" / "chinese" / "problem.md")

    files_dir = dest / "files"
    files_dir.mkdir()
    for name in ("generator.cpp", "validator.cpp"):
        if (problem_dir / name).exists():
            shutil.copy(problem_dir / name, files_dir / name)
    if config.TESTLIB_PATH.exists():
        shutil.copy(config.TESTLIB_PATH, files_dir / "testlib.h")

    sols = dest / "solutions"
    sols.mkdir()
    shutil.copy(problem_dir / "solution.cpp", sols / "main.cpp")
    if (problem_dir / "naive.cpp").exists():
        shutil.copy(problem_dir / "naive.cpp", sols / "naive.cpp")

    spj = _is_spj(meta)
    if spj and (problem_dir / "checker.cpp").exists():
        shutil.copy(problem_dir / "checker.cpp", dest / "check.cpp")

    tests = dest / "tests"
    tests.mkdir()
    for idx, (fin, fout) in enumerate(pairs, 1):
        shutil.copy(fin, tests / f"{idx:02d}")        # Polygon: input has no extension
        shutil.copy(fout, tests / f"{idx:02d}.a")     # answer file: NN.a

    (dest / "README_polygon.md").write_text(
        f"""# Polygon 录入说明 — {meta.get('title', '')}

Polygon 不支持整包导入，请按以下映射在 web 界面/API 逐项录入：

- General: TL = {meta.get('time_limit_ms')}ms, ML = {meta.get('memory_limit_mb')}MB
- Tags: {', '.join(meta.get('algorithm_tags') or []) or '（无）'}; difficulty ≈ CF {meta.get('difficulty') or '?'}
- Statement: statements/chinese/problem.md
- Files: files/generator.cpp、files/validator.cpp（validator 已为 registerValidation/stdin 约定，与 Polygon 一致）
- Solutions: solutions/main.cpp（Main correct）、solutions/naive.cpp（标记为 TLE solution）
- Checker: {'check.cpp（testlib）' if spj else '选择内置 std::wcmp.cpp（本题非 SPJ）'}
- Tests: tests/NN（输入，无扩展名）+ tests/NN.a（答案），共 {len(pairs)} 组；
  建议在 Polygon 中改用 generator 脚本生成（files/generator.cpp 支持 argv 编号）
""", encoding="utf-8")
    print(f"✓ Polygon 目录: {dest}（{len(pairs)} 组 tests）")
    return dest


# ─── entry ───────────────────────────────────────────────────────────────────

EXPORTERS = {"luogu": export_luogu, "hydro": export_hydro, "polygon": export_polygon}


def export_problem(problem_dir: Path, formats: list[str], out_root: Path | None = None) -> dict:
    problem_dir = Path(problem_dir)
    if not (problem_dir / "problem.md").exists():
        raise FileNotFoundError(f"{problem_dir} 不是题目目录（缺少 problem.md）")
    meta = load_metadata(problem_dir)
    out_root = Path(out_root) if out_root else problem_dir / "export"
    results = {}
    for fmt in formats:
        results[fmt] = str(EXPORTERS[fmt](problem_dir, meta, out_root))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a problem to judge packages")
    parser.add_argument("problem_dir", type=Path)
    parser.add_argument("--format", "-f", default="all",
                        help="luogu,hydro,polygon or all (default: all)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output root (default: <problem_dir>/export)")
    args = parser.parse_args(argv)

    formats = list(FORMATS) if args.format == "all" else [
        f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in EXPORTERS]
    if unknown:
        parser.error(f"未知格式: {', '.join(unknown)}（支持: {', '.join(FORMATS)}, all）")

    try:
        export_problem(args.problem_dir, formats, args.out)
    except (FileNotFoundError, config.ConfigError) as e:
        print(f"导出失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
