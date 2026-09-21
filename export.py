"""
Export a generated problem directory to judge-specific packages.

Formats:
  hydrooj — export/hydrooj/<YYYYMMDD_HHMMSS>.zip: **平台上传主产物**，严格对齐
            static/Hydro格式示例.zip 的目录结构：
              <dir>/problem.json         结构化题面（samples/hint/dataRange/file_io）
              <dir>/problem.yaml         title / tag / difficulty
              <dir>/problem_zh.md        中文题面（平台排版约定）
              <dir>/testdata/config.yaml time / memory / [filename] / subtasks
              <dir>/testdata/input.name  file_io 模式下的输入文件名
              <dir>/testdata/output.name file_io 模式下的输出文件名
              <dir>/testdata/<fn><i>.in|.out
            `hydro` 是它的兼容别名。
  luogu   — export/luogu/: flat data.zip (NN.in/NN.out at zip root, matching
            洛谷题库上传格式), statement.md, checker.cpp (SPJ only), README.txt.
            HustOJ accepts the same flat in/out zip.
  xml     — export/xml/<slug>.xml: **HUSTOJ 原生 FPS XML**，题面/时限/内存/样例/
            测试点一个文件全带上，对应 admin/problem_import_xml.php。
            第三方 HUSTOJ 部署上比 hydro/qduoj 两个 zip 导入器可靠得多。
  polygon — export/polygon/<slug>/: upload-ready layout for Codeforces Polygon
            (statements/, files/, solutions/, tests/ with NN / NN.a naming)

Usage:
  python export.py problems/<name> --format hydrooj|xml|all [--out DIR]
                                   [--file-io|--no-file-io] [--filename NAME]
                                   [--hydro-dir NAME] [--include-editorial]
"""
import argparse
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

import yaml

import config

FORMATS = ("hydrooj", "qduoj", "xml", "luogu", "polygon")


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

def export_luogu(problem_dir: Path, meta: dict, out_root: Path,
                 opts: dict | None = None) -> Path:
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


# ─── HydroOJ（平台上传格式，对齐 static/Hydro格式示例.zip）────────────────────

# 样例块：`### 样例输入 #1` / `### 样例输入 1` / `## 样例输入` 后紧跟代码块
_SAMPLE_RE = re.compile(
    r"^(#{2,6})([^\n]*?(输入|输出)[^\n]*)\n+```[^\n]*\n(.*?)```",
    re.MULTILINE | re.DOTALL,
)
# 样例解释条目：`样例 1：…` / `- 样例 1: …` / `**样例 1**：…`
_EXPLAIN_RE = re.compile(r"^\s*[-*]?\s*\**(?:样例|示例)\s*#?(\d+)\**\s*[：:]\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _split_md_sections(md: str) -> list[tuple[int, str, str]]:
    """把 markdown 拆成 [(level, heading, body), ...]（body 为标题到下一标题之间）。"""
    sections: list[tuple[int, str, str]] = []
    level, heading, buf = 0, "", []
    for line in md.replace("\r\n", "\n").split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            if heading or any(s.strip() for s in buf):
                sections.append((level, heading, "\n".join(buf).strip()))
            level, heading, buf = len(m.group(1)), m.group(2), []
        else:
            buf.append(line)
    if heading or any(s.strip() for s in buf):
        sections.append((level, heading, "\n".join(buf).strip()))
    return sections


def _find_section(sections, *keywords: str, min_level: int = 2) -> str:
    """返回第一个标题命中任一关键词的段落正文（level >= min_level）。"""
    for level, heading, body in sections:
        if level < min_level:
            continue
        flat = heading.replace(" ", "")
        if any(k in flat for k in keywords):
            return body
    return ""


def _parse_statement(md: str) -> dict:
    """从 problem.md 抽取平台所需的题面结构（description/input/output/samples/...）。"""
    sections = _split_md_sections(md)
    title = next((h for lv, h, _ in sections if lv == 1), "")

    description = _find_section(sections, "题目描述")
    background = _find_section(sections, "题目背景")
    if background:
        description = f"{background}\n\n{description}".strip()
    if not description:  # 兜底：没有「题目描述」小标题时，用非样例/格式章节拼出正文
        others = [b for lv, h, b in sections
                  if lv >= 2 and b
                  and not re.search(r"样例|输入|输出|约束|数据范围|题解|提示", h)]
        description = "\n\n".join(others).strip()

    # 样例（按出现顺序配对 输入→输出）
    samples, pending = [], None
    for _, heading, _kind, body in _SAMPLE_RE.findall(md.replace("\r\n", "\n")):
        ns = re.search(r"(\d+)", heading)
        idx = int(ns.group(1)) if ns else len(samples) + 1
        if "输入" in heading:
            pending = (idx, body)
        elif "输出" in heading and pending is not None:
            samples.append({"index": pending[0], "input": pending[1], "output": body})
            pending = None

    # 样例解释：按 `样例 N：…` 映射到对应样例
    explains: dict[int, str] = {}
    cur = None
    for line in _find_section(sections, "样例解释", "样例说明", "提示与说明").split("\n"):
        m = _EXPLAIN_RE.match(line)
        if m:
            cur = int(m.group(1))
            explains[cur] = m.group(2).strip()
        elif line.strip() and cur is not None:
            explains[cur] = f"{explains[cur]} {line.strip()}".strip()
    if explains:
        if len(explains) == 1 and (only := next(iter(explains.values()))):
            for s in samples:  # 只有一段解释时挂到首个样例
                s["explain"] = only
                break
        else:
            for i, s in enumerate(samples, 1):
                txt = explains.get(s["index"]) or explains.get(i)
                if txt:
                    s["explain"] = txt

    return {
        "title": title,
        "description": description,
        "input": _find_section(sections, "输入格式", "输入描述"),
        "output": _find_section(sections, "输出格式", "输出描述"),
        "data_range": _find_section(sections, "数据范围", "约束", "输入限制"),
        "hint": _find_section(sections, "题解说明", "解题思路", "题解"),
        "samples": samples,
    }


def _ascii_name(raw: str) -> str:
    """把 slug 收敛成可安全用于文件名的 ASCII 短名（file_io 文件名 / 测试点前缀）。"""
    name = re.sub(r"[^0-9A-Za-z_]+", "_", raw or "").strip("_").lower()
    return name or "problem"


def _write_text(path: Path, text: str, crlf: bool = False) -> None:
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_text(text, encoding="utf-8", newline="")


def _yaml_str(obj: dict) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)


def export_hydrooj(problem_dir: Path, meta: dict, out_root: Path,
                   opts: dict | None = None) -> Path:
    """导出 HydroOJ 平台上传包（严格对齐 static/Hydro格式示例.zip）。

    `hydro` 格式名是它的兼容别名。file_io 开关优先级：
    opts['file_io'] > problem.yaml 的 file_io > config.yaml 的 hydro.file_io。

    返回生成的 zip 路径；同目录下同时保留解包后的目录结构，便于人工核对。
    """
    opts = dict(opts or {})
    pairs = _paired_cases(problem_dir)
    md = (problem_dir / "problem.md").read_text(encoding="utf-8")
    parsed = _parse_statement(md)

    slug = meta.get("slug") or problem_dir.name
    filename = str(opts.get("filename") or config.HYDRO_FILENAME or "")
    if not filename:
        filename = _ascii_name(slug)
        # slug 以数字结尾时（如 smoke_dp2）避免测试点变成 smoke_dp21.in 这类歧义命名
        if filename[-1].isdigit():
            filename += "_"
    file_io = opts.get("file_io")
    meta_fio = meta.get("file_io")
    if isinstance(meta_fio, str) and meta_fio.lower() in ("false", "none", "off", "stdin"):
        file_io = False
    elif isinstance(meta_fio, dict) and meta_fio.get("input"):
        file_io = True
        fname = str(meta_fio["input"])
        filename = fname[:-3] if fname.endswith(".in") else fname
    elif meta_fio is True:
        file_io = True
    if file_io is None:
        file_io = config.HYDRO_FILE_IO
    file_io = bool(file_io)

    dir_name = str(opts.get("dir_name") or time.strftime("%Y%m%d_%H%M%S"))
    stage = out_root / "hydrooj" / dir_name
    if stage.exists():
        shutil.rmtree(stage)
    testdata = stage / "testdata"
    testdata.mkdir(parents=True)

    title = meta.get("title") or parsed["title"] or slug
    time_ms = int(meta.get("time_limit_ms") or config.DEFAULT_TIME_LIMIT_MS)
    mem_mb = int(meta.get("memory_limit_mb") or config.DEFAULT_MEMORY_LIMIT_MB)
    fio_note = (f"本题的输入文件为 {filename}.in，输出文件为 {filename}.out。"
                if file_io else "")

    # ── problem.json（键序与示例一致）──
    obj: dict = {"title": title, "time": time_ms, "memory": mem_mb,
                 "description": parsed["description"]}
    in_body = parsed["input"] or ""
    obj["input"] = f"{fio_note}\n\n{in_body}".strip() if fio_note else in_body
    obj["output"] = parsed["output"]
    samples = []
    for i, s in enumerate(parsed["samples"], 1):
        item = {"input": s["input"], "output": s["output"], "title": f"样例 {i}"}
        if s.get("explain"):
            item["explain"] = s["explain"]
        samples.append(item)
    if samples:
        obj["samples"] = samples
    if parsed["hint"]:
        obj["hint"] = parsed["hint"]
    if parsed["data_range"]:
        obj["dataRange"] = parsed["data_range"]
    tags = meta.get("algorithm_tags") or []
    if tags:
        obj["tags"] = list(tags)
    if meta.get("difficulty"):
        obj["difficulty"] = meta["difficulty"]
    if file_io:
        obj["file_io"] = {"input": f"{filename}.in", "output": f"{filename}.out"}
    _write_text(stage / "problem.json",
                json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

    # ── problem.yaml ──
    hydro_meta = {"title": title, "tag": list(tags)}
    if meta.get("difficulty"):
        hydro_meta["difficulty"] = meta["difficulty"]
    _write_text(stage / "problem.yaml", _yaml_str(hydro_meta), crlf=True)

    # ── problem_zh.md（按平台排版重新生成）──
    parts = [f"# {title}"]
    if parsed["description"]:
        parts += ["## 题目描述", parsed["description"]]
    if in_body or fio_note:
        parts += ["## 输入格式", "\n\n".join(x for x in (fio_note, in_body) if x)]
    if parsed["output"]:
        parts += ["## 输出格式", parsed["output"]]
    if samples:
        block = ["## 样例"]
        for i, s in enumerate(samples, 1):
            block += [f"### 样例输入 {i}", f"```\n{s['input']}```",
                      f"### 样例输出 {i}", f"```\n{s['output']}```"]
        parts += block
    explains = [f"样例 {i}：{s['explain']}" for i, s in enumerate(samples, 1)
                if s.get("explain")]
    if explains:
        parts += ["## 样例解释", "\n\n".join(explains)]
    if parsed["data_range"]:
        parts += ["## 数据范围", parsed["data_range"]]
    if parsed["hint"]:
        parts += ["## 题解说明", parsed["hint"]]
    _write_text(stage / "problem_zh.md", "\n\n".join(parts).strip() + "\n", crlf=True)

    # ── testdata ──
    cases = []
    for i, (fin, fout) in enumerate(pairs, 1):
        in_name, out_name = f"{filename}{i}.in", f"{filename}{i}.out"
        shutil.copy(fin, testdata / in_name)
        shutil.copy(fout, testdata / out_name)
        cases.append({"input": in_name, "output": out_name})
    judge_cfg: dict = {"time": f"{time_ms}ms", "memory": f"{mem_mb}m"}
    if file_io:
        judge_cfg["filename"] = filename
    judge_cfg["subtasks"] = [{
        "score": 100,
        "type": config.HYDRO_SUBTASK_TYPE,
        "cases": cases,
    }]
    _write_text(testdata / "config.yaml", _yaml_str(judge_cfg), crlf=True)

    if file_io:
        _write_text(testdata / "input.name", f"{filename}.in\n")
        _write_text(testdata / "output.name", f"{filename}.out\n")

    # SPJ：平台以 checker 源码随包上传
    if _is_spj(meta) and (problem_dir / "checker.cpp").exists():
        shutil.copy(problem_dir / "checker.cpp", stage / "checker.cpp")

    zip_path = out_root / "hydrooj" / f"{dir_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, f"{dir_name}/{f.relative_to(stage).as_posix()}")
    mode = f"file_io({filename})" if file_io else "stdin/stdout"
    print(f"✓ HydroOJ 包: {zip_path}（{len(pairs)} 组，{mode}）")
    return zip_path


# 兼容别名：老脚本里的 --format hydro
export_hydro = export_hydrooj


# ─── QDUOJ（HUSTOJ 的 problem_import_qduoj.php 入口）─────────────────────────
#
# 该导入器分两趟处理 zip：
#   第 1 趟 把非 .json 条目解到 $tempdir；
#   第 2 趟 每遇到一个 .json 就 import_json() 建题，再执行
#           `mv $tempdir/$i/testcase/* $OJ_DATA/$pid/`（$i 是 json 的序号，从 1 起）。
# 因此：
#   1) json 必须位于 zip 根的 `1/`（多题就 1,2,3… 顺序编号，不能跳号）；
#   2) **整个包不能再套一层目录**，否则 `$tempdir/1/testcase` 不存在，数据搬不过去；
#   3) json 里的字段是硬编码读取的（见下），字段缺失会告警且该处为空。
# 读取的字段：title / time_limit(ms) / memory_limit(MB) /
#   description.value / input_description.value / output_description.value /
#   samples[0].input / samples[0].output / hint.value / problem.source

_QDUOJ_MD_OPEN = '<span class="md">'
_QDUOJ_MD_CLOSE = "</span>"


def _md_block(parsed: dict, *, samples_in_body: bool) -> str:
    """组装 QDUOJ description.value 用的 markdown（默认不重复样例块，样例单独给）。"""
    parts = []
    if parsed["description"]:
        parts += ["## 题目描述", parsed["description"]]
    if parsed["input"]:
        parts += ["## 输入格式", parsed["input"]]
    if parsed["output"]:
        parts += ["## 输出格式", parsed["output"]]
    if samples_in_body and parsed["samples"]:
        block = ["## 样例"]
        for i, s in enumerate(parsed["samples"], 1):
            block += [f"### 样例输入 {i}", f"```\n{s['input']}```",
                      f"### 样例输出 {i}", f"```\n{s['output']}```"]
        parts += block
    explains = [f"样例 {i}：{s['explain']}" for i, s in enumerate(parsed["samples"], 1)
                if s.get("explain")]
    if explains:
        parts += ["## 样例解释", "\n\n".join(explains)]
    if parsed["data_range"]:
        parts += ["## 数据范围", parsed["data_range"]]
    if parsed["hint"]:
        parts += ["## 题解说明", parsed["hint"]]
    return "\n\n".join(parts).strip()


def export_qduoj(problem_dir: Path, meta: dict, out_root: Path,
                 opts: dict | None = None) -> Path:
    """
    导出 QDUOJ 格式包（HUSTOJ `admin/problem_import_qduoj.php` 用）。

    结构（zip 根目录，**不套外层目录**）：
        1/problem.json       全量题面 + 时限内存 + 样例
        1/testcase/1.in      测试点（导入器直接 mv 进 OJ_DATA/<pid>/）
        1/testcase/1.out
    本题固定为 stdin/stdout（QDUOJ 格式不支持 NOIP 式 file_io）。
    """
    opts = dict(opts or {})
    pairs = _paired_cases(problem_dir)
    md = (problem_dir / "problem.md").read_text(encoding="utf-8")
    parsed = _parse_statement(md)

    slug = meta.get("slug") or problem_dir.name
    title = meta.get("title") or parsed["title"] or slug
    time_ms = int(meta.get("time_limit_ms") or config.DEFAULT_TIME_LIMIT_MS)
    mem_mb = int(meta.get("memory_limit_mb") or config.DEFAULT_MEMORY_LIMIT_MB)
    tags = list(meta.get("algorithm_tags") or [])

    def md_wrap(text: str) -> dict:
        return {"value": f"{_QDUOJ_MD_OPEN}\n{text}\n{_QDUOJ_MD_CLOSE}", "format": "markdown"}

    obj = {
        "title": title,
        "description": md_wrap(_md_block(parsed, samples_in_body=False)),
        "input_description": md_wrap(parsed["input"]),
        "output_description": md_wrap(parsed["output"]),
        "samples": [{"input": s["input"], "output": s["output"]}
                    for s in parsed["samples"]],
        "hint": md_wrap(parsed["hint"]),
        "problem": {"source": " ".join(tags)},
        "time_limit": time_ms,
        "memory_limit": mem_mb,
        "difficulty": meta.get("difficulty"),
        "tags": tags,
    }

    stage = out_root / "qduoj"
    stage.mkdir(parents=True, exist_ok=True)
    zip_path = stage / f"{_ascii_name(slug) or 'problem'}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("1/problem.json", json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        for i, (fin, fout) in enumerate(pairs, 1):
            zf.write(fin, f"1/testcase/{i}.in")     # 扁平数字命名，与 HUSTOJ 判题一致
            zf.write(fout, f"1/testcase/{i}.out")
    print(f"✓ QDUOJ 包: {zip_path}（{len(pairs)} 组，stdin/stdout）")
    return zip_path


# ─── Polygon ─────────────────────────────────────────────────────────────────

def export_polygon(problem_dir: Path, meta: dict, out_root: Path,
                   opts: dict | None = None) -> Path:
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


# ─── FPS XML（HUSTOJ 原生格式，admin/problem_import_xml.php）──────────────────
#
# 这是 HUSTOJ 的"母语"格式：题面、时限、内存、样例、测试点全在一个 XML 里，
# 一次导入全部落地。相比 hydro/qduoj 两个 zip 导入器，它在第三方 HUSTOJ 部署上
# 明显更可靠（实例见 README_wb_integration.md 的上传实测记录）。
#
# 已知边界（都由格式本身决定，不是实现偷懒）：
#   1) FPS 只有一对 sample_input/sample_output → 第 1 组样例走这两个字段
#      （对齐该格式在真实 OJ 上的导出自洽形态），第 2 组起的样例渲染进
#      description，既不丢样例也不重复展示；
#   2) 没有 file_io 概念（不会生成 input.name/output.name）→ 固定 stdin/stdout；
#   3) description/input/output 一律转成 HTML：HUSTOJ 前台按 HTML 渲染，而 HTML
#      在 markdown 渲染器下也能原样透传 —— 是两种前台的"安全交集"；
#      `$..$` / `$$..$$` 原样保留，交给前台的 KaTeX；
#   4) 题解说明默认**不写进包**（导入后选手可见）；需要时加 --include-editorial；
#   5) 测试数据是文本内联，导出时遇到非 UTF-8 或 XML 非法控制字符会直接报错，
#      而不是悄悄写坏数据。

_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FPS_DOCTYPE = (
    "<!DOCTYPE fps PUBLIC \n"
    '  "-//freeproblemset//An opensource XML standard for Algorithm Contest Problem Set//EN"\n'
    '  "http://hustoj.com/fps.current.dtd" >'
)


def _cdata(text: str) -> str:
    """包成 CDATA；内部出现 `]]>` 时按 XML 规范拆开。"""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _inline(text: str) -> str:
    """行内 markdown：**粗体** / `行内代码` → HTML；$..$ 一律不动（给 KaTeX）。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)


def _sec(md_text: str) -> str:
    """把一段 markdown 转成 HTML（标题/无序列表/代码块/段落/行内强调；不做表格）。"""
    out: list[str] = []
    in_code = in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in (md_text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            close_list()
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(raw)
            continue
        head = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if head:
            close_list()
            lvl = min(len(head.group(1)) + 1, 6)   # 题面里的 # 在页面里降一级
            out.append(f"<h{lvl}>{_inline(head.group(2).strip())}</h{lvl}>")
        elif item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(item.group(1).strip())}</li>")
        elif line.strip():
            close_list()
            out.append(f"<p>{_inline(line.strip())}</p>")
        else:
            close_list()

    close_list()
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


def _data_text(path: Path) -> str:
    """读测试数据为文本；非 UTF-8 或含 XML 非法控制字符时明确报错。"""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(
            f"{path.name} 不是 UTF-8 文本（{e}）—— FPS XML 只能内联文本数据，"
            "二进制数据请改用文件读写题或换 hydro 包。") from e
    bad = _XML_ILLEGAL.search(text)
    if bad:
        raise ValueError(f"{path.name} 含 XML 非法控制字符 0x{ord(bad.group()):02x}，"
                         "无法内联进 FPS XML。")
    return text


def _fmt_seconds(ms: int) -> str:
    return f"{ms / 1000:g}"


def export_xml(problem_dir: Path, meta: dict, out_root: Path,
               opts: dict | None = None) -> Path:
    """导出 HUSTOJ 原生 FPS XML（`admin/problem_import_xml.php` 一次导入）。

    opts：
      include_editorial (bool) — 是否把「题解说明」也写进 <hint>（默认否）
    """
    opts = dict(opts or {})
    pairs = _paired_cases(problem_dir)
    md = (problem_dir / "problem.md").read_text(encoding="utf-8")
    parsed = _parse_statement(md)

    slug = meta.get("slug") or problem_dir.name
    title = meta.get("title") or parsed["title"] or slug
    time_ms = int(meta.get("time_limit_ms") or config.DEFAULT_TIME_LIMIT_MS)
    mem_mb = int(meta.get("memory_limit_mb") or config.DEFAULT_MEMORY_LIMIT_MB)
    tags = list(meta.get("algorithm_tags") or [])

    # description：题目描述 + 样例解释 +（第 2 组起的）样例
    desc_parts = [parsed["description"]]
    explains = [f"样例 {i}：{s['explain']}" for i, s in enumerate(parsed["samples"], 1)
                if s.get("explain")]
    if explains:
        desc_parts += ["## 样例解释", "\n\n".join(explains)]
    for i, s in enumerate(parsed["samples"][1:], 2):
        desc_parts += [f"## 样例 #{i}",
                       f"### 样例输入 #{i}", f"```\n{s['input']}```",
                       f"### 样例输出 #{i}", f"```\n{s['output']}```"]
    description = _sec("\n\n".join(p for p in desc_parts if p))

    hint_parts = []
    if parsed["data_range"]:
        hint_parts += ["## 数据范围", parsed["data_range"]]
    if opts.get("include_editorial") and parsed["hint"]:
        hint_parts += ["## 题解说明", parsed["hint"]]
    hint = _sec("\n\n".join(hint_parts)) if hint_parts else ""

    spj = ""
    if _is_spj(meta) and (problem_dir / "checker.cpp").exists():
        spj = (problem_dir / "checker.cpp").read_text(encoding="utf-8")

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', _FPS_DOCTYPE, "",
             '<fps version="1.5" url="https://github.com/zhblue/freeproblemset/">',
             '  <generator name="cp-agent" url="https://github.com/tianqick/cp-agent" />',
             '  <item>',
             f'    <title>{_cdata(title)}</title>',
             f'    <time_limit unit="s">{_cdata(_fmt_seconds(time_ms))}</time_limit>',
             f'    <memory_limit unit="mb">{_cdata(str(mem_mb))}</memory_limit>',
             f'    <description>{_cdata(description)}</description>',
             f'    <input>{_cdata(_sec(parsed["input"]))}</input>',
             f'    <output>{_cdata(_sec(parsed["output"]))}</output>']
    if parsed["samples"]:
        first = parsed["samples"][0]
        lines += [f'    <sample_input>{_cdata(first["input"])}</sample_input>',
                  f'    <sample_output>{_cdata(first["output"])}</sample_output>']
    for i, (fin, fout) in enumerate(pairs, 1):
        lines += [f'    <test_input name="{i}">{_cdata(_data_text(fin))}</test_input>',
                  f'    <test_output name="{i}">{_cdata(_data_text(fout))}</test_output>']
    if hint:
        lines.append(f'    <hint>{_cdata(hint)}</hint>')
    if tags:
        lines.append(f'    <source>{_cdata(" ".join(tags))}</source>')
    if spj:
        lines.append(f'    <spj language="C++">{_cdata(spj)}</spj>')
    lines += ["  </item>", "</fps>", ""]

    dest = out_root / "xml"
    dest.mkdir(parents=True, exist_ok=True)
    xml_path = dest / f"{_ascii_name(slug)}.xml"
    xml_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    size_mb = xml_path.stat().st_size / 1024 / 1024
    note = "，含 SPJ（服务端导入后需自行 g++ 编译 spj.cc）" if spj else ""
    editorial = "，含题解说明" if opts.get("include_editorial") else ""
    print(f"✓ FPS XML: {xml_path}（{len(pairs)} 组，{time_ms / 1000:g}s/{mem_mb}MB，"
          f"stdin/stdout{note}{editorial}，{size_mb:.1f}MB）")
    if size_mb > 40:
        print("⚠ 已超过 40MB：部分 PHP 部署的 upload_max_filesize 接不住，"
              "失败时服务器会直接回报 'File size is too big'")
    return xml_path


# ─── entry ───────────────────────────────────────────────────────────────────

EXPORTERS = {
    "hydrooj": export_hydrooj,
    "hydro": export_hydrooj,      # 兼容别名 → HydroOJ 平台格式
    "qduoj": export_qduoj,
    "xml": export_xml,
    "luogu": export_luogu,
    "polygon": export_polygon,
}


def export_problem(problem_dir: Path, formats: list[str], out_root: Path | None = None,
                   opts: dict | None = None) -> dict:
    """
    Export a problem dir to the requested formats.

    opts 支持：
      file_io (bool)           — HydroOJ 专用：是否使用文件读写
      filename (str)           — HydroOJ 专用：file_io 与测试点名前缀
      dir_name (str)           — HydroOJ 专用：包内顶层目录名，默认 `YYYYMMDD_HHMMSS`
      include_editorial (bool) — XML 专用：是否把题解说明写进 <hint>（默认否）
    """
    problem_dir = Path(problem_dir)
    if not (problem_dir / "problem.md").exists():
        raise FileNotFoundError(f"{problem_dir} 不是题目目录（缺少 problem.md）")
    meta = load_metadata(problem_dir)
    out_root = Path(out_root) if out_root else problem_dir / "export"
    results = {}
    for fmt in formats:
        results[fmt] = str(EXPORTERS[fmt](problem_dir, meta, out_root, opts))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a problem to judge packages")
    parser.add_argument("problem_dir", type=Path)
    parser.add_argument("--format", "-f", default="all",
                        help="hydrooj,luogu,polygon or all (default: all)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output root (default: <problem_dir>/export)")
    fio = parser.add_mutually_exclusive_group()
    fio.add_argument("--file-io", dest="file_io", action="store_true", default=None,
                     help="HydroOJ: 使用文件读写（默认，见 config.yaml hydro.file_io）")
    fio.add_argument("--no-file-io", dest="file_io", action="store_false",
                     help="HydroOJ: 使用标准输入输出")
    parser.add_argument("--filename", default=None,
                        help="HydroOJ: file_io 与测试点名前缀（如 lantern）")
    parser.add_argument("--hydro-dir", dest="dir_name", default=None,
                        help="HydroOJ: 包内顶层目录名（默认时间戳）")
    parser.add_argument("--include-editorial", dest="include_editorial",
                        action="store_true", default=None,
                        help="XML: 把「题解说明」也写进 <hint>（默认不写，避免选手直接看到题解）")
    args = parser.parse_args(argv)

    formats = list(FORMATS) if args.format == "all" else [
        f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in EXPORTERS]
    if unknown:
        parser.error(f"未知格式: {', '.join(unknown)}（支持: {', '.join(FORMATS)}, hydro, all）")

    opts = {"file_io": args.file_io, "filename": args.filename,
            "dir_name": args.dir_name, "include_editorial": args.include_editorial}
    opts = {k: v for k, v in opts.items() if v is not None}

    try:
        export_problem(args.problem_dir, formats, args.out, opts)
    except (FileNotFoundError, config.ConfigError) as e:
        print(f"导出失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
