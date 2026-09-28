#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题目产物「同步前」静态质检门禁。

为什么是「静态」：CI artifact 里的 bin/ 是 Linux ELF，在本机跑不起来；而且同步只是
把题目登记进看板，不需要重编译。质量信号全部来自 CI 自己写下的两个文件：
  - result.json          出题 agent 的产出总结（success / artifacts.complete / counts_match）
  - problem.yaml         的 validation 块（validator / 样例 / 数据强度 / 边界覆盖）

硬失败(fail) 会**阻断入库**；软告警(warn) 只提示、不阻断。

解析只有一条路：**PyYAML**（`pyyaml` 已在 pyproject 的正式 dependencies 里）。
曾经有一条「无 pyyaml 时用正则回退」的路径，2026-09-28 已删除 ——
PyYAML 对**空列表写 flow**(`key: []`)、**非空列表写 block**(`key:` 换行 `- x`)，
而那条正则只认 flow，于是 `bounds_unhit` 这种**硬失败**被静默读成空 ⇒ 脏产物穿过门禁。
「两条路都跑得通」正是 bug 最好的藏身处，所以现在只留一条路；跑错环境就显式报错，
而不是静默给出错误答案。

用法:
  python problem_gate.py <题目目录> [<题目目录> ...]
  → 打印 JSON: {"results":[{dir,ok,fail:[],warn:[],info:{}}], "passed":n, "failed":n}
  退出码: 0 = 无硬失败；1 = 有硬失败
"""
import json
import os
import sys

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - 缺依赖时给明确指引，而不是静默降级
    raise SystemExit(
        "problem_gate 需要 pyyaml（它已在 pyproject 的 dependencies 里）。\n"
        "本项目只保留 yaml 一条解析路径：正则回退已于 2026-09-28 删除，\n"
        "因为它会把「非空列表」误读成空，静默放过未触达边界。\n"
        "请用项目 venv 运行：.venv/Scripts/python.exe problem_gate.py ..."
    )

MAX_TESTCASE_BYTES = int(1.2 * 1024 * 1024)   # 与 pipeline.py 保持一致
REQUIRED_FILES = ["problem.md", "problem.yaml", "solution.cpp",
                  "generator.cpp", "validator.cpp", "naive.cpp"]
CJK_MIN, CJK_RATIO_MIN = 80, 0.35              # 与 agent._validate_problem_md_chinese 一致


# ─────────────────────────── problem.yaml 读取 ───────────────────────────

def _meta(dirpath):
    """读 problem.yaml → (扁平字段 dict, 错误信息)。

    文件不存在 → (None, None)；解析失败 → (None, "原因")（由 check() 转成硬失败）。
    """
    p = os.path.join(dirpath, "problem.yaml")
    if not os.path.exists(p):
        return None, None
    try:
        y = _yaml.safe_load(open(p, encoding="utf-8-sig").read()) or {}
    except Exception as e:
        return None, "problem.yaml 解析失败：%s" % e
    val = y.get("validation") or {}
    ds = (val.get("data_strength") or {})
    sm = (val.get("samples") or {})
    return {
        "title": str(y.get("title") or ""),
        "slug": str(y.get("slug") or ""),
        "difficulty": int(y.get("difficulty") or 0),
        "checker_type": str((y.get("checker") or {}).get("type") or ""),
        "cases_count": len(y.get("cases") or []),
        "validator_passed": bool(val.get("validator_passed")),
        "bounds_unhit": list(val.get("bounds_unhit") or []),
        "waived_bounds": list(val.get("waived_bounds") or []),
        "samples_count": int(sm.get("count") or 0),
        "samples_passed": int(sm.get("passed") or 0),
        "ds_passed": bool(ds.get("passed")),
        "naive_tle_on": list(ds.get("naive_tle_on") or []),
    }, None


def _cjk_ratio(problem_md):
    """题面（去掉代码块）的 中文字数 与 中文占比。"""
    text, in_code = [], False
    for line in problem_md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            text.append(line)
    s = "\n".join(text)
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in s if "a" <= ch.lower() <= "z")
    total = cjk + latin
    return cjk, (cjk / total if total else 0.0)


# ─────────────────────────── 主检查 ───────────────────────────

def check(problem_dir, expect_name=None):
    """对一个题目目录做静态质检。返回 report dict。"""
    d = os.path.abspath(problem_dir)
    name = os.path.basename(d)
    fail, warn, info = [], [], {}

    if not os.path.isdir(d):
        return {"dir": name, "ok": False, "fail": ["目录不存在：%s" % d], "warn": [], "info": {}}

    # F2 必需文件
    meta, meta_err = _meta(d)
    is_spj = bool(meta) and meta.get("checker_type") == "testlib"
    need = REQUIRED_FILES + (["checker.cpp"] if is_spj else [])
    missing = [f for f in need if not os.path.exists(os.path.join(d, f))]
    if missing:
        fail.append("缺必需文件：%s" % ", ".join(missing))
    if meta is None:
        fail.append(meta_err or "problem.yaml 缺失或无法解析")

    # F3 result.json（CI 产出总结）
    rj_path = os.path.join(d, "result.json")
    if not os.path.exists(rj_path):
        warn.append("缺 result.json（非 CI 产物？）—— 质量判据降级为只看 problem.yaml")
        rj = None
    else:
        try:
            rj = json.load(open(rj_path, encoding="utf-8"))
        except Exception as e:
            rj = None
            fail.append("result.json 解析失败：%s" % e)
        if rj is not None:
            if rj.get("success") is not True:
                fail.append("result.json 标记出题失败：success=%r, reason=%r"
                            % (rj.get("success"), rj.get("failure_reason")))
            elif rj.get("failure_reason"):
                fail.append("result.json 有 failure_reason：%r" % (rj.get("failure_reason"),))
            art = rj.get("artifacts") or {}
            if art.get("complete") is False:
                fail.append("result.json: artifacts.complete=false")
            if art.get("counts_match") is False:
                fail.append("result.json: artifacts.counts_match=false")
            info["result_success"] = rj.get("success")
            info["iterations"] = rj.get("iterations")

    # F4 problem.yaml 基本字段
    if meta is not None:
        info.update({"title": meta["title"], "difficulty": meta["difficulty"],
                     "cases": meta["cases_count"], "slug": meta["slug"]})
        if not meta["title"]:
            fail.append("problem.yaml 缺 title")
        if meta["difficulty"] <= 0:
            fail.append("problem.yaml difficulty 非法：%r" % meta["difficulty"])
        if meta["cases_count"] <= 0:
            fail.append("problem.yaml cases 为空")
        if meta["checker_type"] not in ("builtin", "testlib"):
            fail.append("checker.type 非法：%r" % meta["checker_type"])

        # F5~F8 validation 质量块
        if not meta["validator_passed"]:
            fail.append("validation.validator_passed 不为 true（输入未过校验器）")
        if meta["samples_count"] <= 0:
            fail.append("题面样例数为 0（problem.md 样例格式或未解析）")
        elif meta["samples_passed"] != meta["samples_count"]:
            fail.append("样例未全过：%d/%d" % (meta["samples_passed"], meta["samples_count"]))
        if not meta["ds_passed"]:
            fail.append("validation.data_strength.passed 不为 true（数据强度不足）")
        unwaived = [b for b in meta["bounds_unhit"] if b not in meta["waived_bounds"]]
        if unwaived:
            fail.append("有未触达且未豁免的约束边界：%s" % ", ".join(map(str, unwaived)))

        # W1 数据强度薄弱信号
        if meta["ds_passed"] and not meta["naive_tle_on"]:
            warn.append("data_strength 通过但 naive_tle_on 为空 —— 暴力未被卡住，数据可能偏弱")

    # F9 inputs/outputs 配对与数量
    ins = sorted(f for f in os.listdir(os.path.join(d, "inputs"))) if os.path.isdir(os.path.join(d, "inputs")) else []
    outs = sorted(f for f in os.listdir(os.path.join(d, "outputs"))) if os.path.isdir(os.path.join(d, "outputs")) else []
    ins = [f for f in ins if f.endswith(".in")]
    outs = [f for f in outs if f.endswith(".out")]
    if not ins:
        fail.append("inputs/ 为空")
    if {f[:-3] for f in ins} != {f[:-4] for f in outs}:
        fail.append("inputs 与 outputs 不配对（%d in / %d out）" % (len(ins), len(outs)))
    if meta is not None and meta["cases_count"] and len(ins) != meta["cases_count"]:
        fail.append("cases(%d) 与 inputs(%d) 数量不一致" % (meta["cases_count"], len(ins)))
    info["inputs"] = len(ins)

    # F10 单点大小
    biggest = 0
    for f in ins + outs:
        sub = "inputs" if f.endswith(".in") else "outputs"
        sz = os.path.getsize(os.path.join(d, sub, f))
        biggest = max(biggest, sz)
        if sz > MAX_TESTCASE_BYTES:
            fail.append("测试点 %s/%s = %.2fMB 超过 %.1fMB 上限"
                        % (sub, f, sz / 1024 / 1024, MAX_TESTCASE_BYTES / 1024 / 1024))
    info["max_testcase_mb"] = round(biggest / 1024 / 1024, 2)

    # F11 题面中文
    md_path = os.path.join(d, "problem.md")
    if os.path.exists(md_path):
        cjk, ratio = _cjk_ratio(open(md_path, encoding="utf-8", errors="replace").read())
        info["cjk"] = cjk
        info["cjk_ratio"] = round(ratio, 4)
        if cjk < CJK_MIN or ratio < CJK_RATIO_MIN:
            fail.append("题面非中文：中文字符 %d（需≥%d），中文比例 %.2f%%（需≥%.0f%%）"
                        % (cjk, CJK_MIN, ratio * 100, CJK_RATIO_MIN * 100))

    # F12 名实相符
    if meta is not None and meta["slug"] and meta["slug"] != name:
        fail.append("problem.yaml slug(%s) 与目录名(%s) 不一致" % (meta["slug"], name))
    if expect_name and name != expect_name:
        fail.append("目录名(%s) 与期望名(%s) 不一致" % (name, expect_name))

    return {"dir": name, "ok": not fail, "fail": fail, "warn": warn, "info": info}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.stderr.write(__doc__)
        return 2
    results = [check(a) for a in args]
    passed = sum(1 for r in results if r["ok"])
    print(json.dumps({"results": results, "passed": passed,
                      "failed": len(results) - passed}, ensure_ascii=False, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
