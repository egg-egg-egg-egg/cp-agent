# -*- coding: utf-8 -*-
"""problem_gate 的解析回归测试。

背景（2026-09-28 修掉的「静默假阴性」）
------------------------------------
PyYAML 序列化时：**空列表写 flow**（`key: []`）、**非空列表写 block**（`key:` 换行 `- x`）。
门禁曾有一条「没有 pyyaml 就用正则回退」的解析路径，而那条正则只认 flow 写法，
于是 `bounds_unhit`（第 7 项**硬失败**）会被读成空列表 ⇒ **脏产物直接穿过门禁**。
当时全量对拍「两解释器结果一致」根本抓不到它 —— 因为现存 100 份 problem.yaml
的三个列表字段**全是空**，两条路径都返回 `[]`，自然一致。它只在第一次出现非空值时爆，
而那正是门禁最该拦的时刻。

修法不是「让两条路一致」，而是**让它只有一条路**：正则回退已删除，解析只用 PyYAML。
本测试守住下面三件事：
  1. flow / block 两种写法都必须被正确解析（防止有人再引入字符串解析）
  2. `bounds_unhit` 去掉 `waived_bounds` 后的未豁免项，必须真的进 `fail`
  3. round-trip：PyYAML `dump` 出来的 problem.yaml 重新解析后字段必须一致

注意：pyyaml 是**硬依赖**（pyproject 的 dependencies 里）。若本模块 import 失败，
说明跑在了没装依赖的解释器上 —— 请用项目 venv。
"""
import json
import os

import yaml

import problem_gate

_TEMPLATE = """title: 测试题面
slug: {slug}
difficulty: 1200
checker:
  type: builtin
cases:
- input: 1.in
  output: 1.out
validation:
  validator_passed: true
  bounds_unhit: {bounds}
  waived_bounds: {waived}
  samples:
    count: 2
    passed: 2
  data_strength:
    passed: true
    naive_tle_on: {naive}
"""

_SOURCES = ("solution.cpp", "generator.cpp", "validator.cpp", "naive.cpp")

# block 写法（PyYAML 对非空列表就是这么输出的）。
# 缩进必须与字段所在层级一致：bounds_unhit / waived_bounds 在 validation 下（2 空格），
# naive_tle_on 在 validation.data_strength 下（4 空格）——缩进错了是 YAML 语法错，不是漏判。
_BLOCK2 = "\n  - n.max\n  - a[i].max"
_BLOCK1 = "\n  - n.max"
_NAIVE1 = "\n    - n.max"


def _text(slug="sim_0001", bounds="[]", waived="[]", naive="[]"):
    return _TEMPLATE.format(slug=slug, bounds=bounds, waived=waived, naive=naive)


def _write_yaml(root, text, name="sim_0001"):
    d = os.path.join(str(root), name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "problem.yaml"), "w", encoding="utf-8") as f:
        f.write(text)
    return d


def _full_problem(root, name="p1", bounds="[]", waived="[]"):
    """造一个「除边界以外全部合规」的题目目录，用来驱动 check()。"""
    d = os.path.join(str(root), name)
    os.makedirs(os.path.join(d, "inputs"), exist_ok=True)
    os.makedirs(os.path.join(d, "outputs"), exist_ok=True)
    with open(os.path.join(d, "problem.md"), "w", encoding="utf-8") as f:
        f.write("# 测试题\n\n" + "这是一道用于测试门禁的题目。" * 10)
    for src in _SOURCES:
        with open(os.path.join(d, src), "w", encoding="utf-8") as f:
            f.write("int main(){return 0;}")
    with open(os.path.join(d, "inputs", "01.in"), "w", encoding="utf-8") as f:
        f.write("1\n")
    with open(os.path.join(d, "outputs", "01.out"), "w", encoding="utf-8") as f:
        f.write("2\n")
    with open(os.path.join(d, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"success": True,
                   "artifacts": {"complete": True, "counts_match": True}}, f)
    with open(os.path.join(d, "problem.yaml"), "w", encoding="utf-8") as f:
        f.write(_text(slug=name, bounds=bounds, waived=waived))
    return d


# ─────────────────── _meta：解析层（bug 就在这里） ───────────────────

def test_flow_empty_lists(tmp_path):
    meta, err = problem_gate._meta(_write_yaml(tmp_path, _text()))
    assert err is None
    assert meta["bounds_unhit"] == []
    assert meta["waived_bounds"] == []
    assert meta["naive_tle_on"] == []


def test_block_style_lists_are_parsed(tmp_path):
    """★ 回归核心：非空列表是 block 写法，曾经被正则回退读成空。"""
    meta, err = problem_gate._meta(_write_yaml(tmp_path, _text(bounds=_BLOCK2)))
    assert err is None
    assert meta["bounds_unhit"] == ["n.max", "a[i].max"]


def test_block_style_single_item(tmp_path):
    meta, _ = problem_gate._meta(_write_yaml(tmp_path, _text(bounds=_BLOCK1)))
    assert meta["bounds_unhit"] == ["n.max"]


def test_block_style_waived(tmp_path):
    meta, _ = problem_gate._meta(_write_yaml(tmp_path, _text(bounds=_BLOCK2, waived=_BLOCK1)))
    assert meta["waived_bounds"] == ["n.max"]


def test_block_style_naive_tle_on(tmp_path):
    meta, _ = problem_gate._meta(_write_yaml(tmp_path, _text(naive=_NAIVE1)))
    assert meta["naive_tle_on"] == ["n.max"]


def test_roundtrip_dump_then_parse_is_stable(tmp_path):
    """PyYAML 自己 dump 出来的文本，重新解析必须与原文解析结果一致。"""
    text = _text(bounds=_BLOCK2, waived=_BLOCK1, naive=_NAIVE1)
    m1, _ = problem_gate._meta(_write_yaml(tmp_path, text, name="a"))

    dumped = yaml.safe_dump(yaml.safe_load(text), allow_unicode=True, sort_keys=False)
    m2, err = problem_gate._meta(_write_yaml(tmp_path, dumped, name="b"))
    assert err is None
    assert m1 == m2


def test_roundtrip_nonempty_lists_survive_dump(tmp_path):
    """dump 出来的非空列表是 block 写法 —— 重新解析不能丢项。"""
    data = {"validation": {
        "bounds_unhit": ["n.max", "a[i].max"],
        "waived_bounds": ["n.max"],
        "data_strength": {"naive_tle_on": ["n.max"]},
    }}
    dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    m, err = problem_gate._meta(_write_yaml(tmp_path, dumped, name="c"))
    assert err is None
    assert m["bounds_unhit"] == ["n.max", "a[i].max"]
    assert m["waived_bounds"] == ["n.max"]
    assert m["naive_tle_on"] == ["n.max"]


def test_broken_yaml_reports_reason(tmp_path):
    """解析失败必须给出原因，而不是静默当成空/合法。"""
    meta, err = problem_gate._meta(_write_yaml(tmp_path, "title: [unclosed\n"))
    assert meta is None
    assert err and "解析失败" in err


# ─────────────────── check()：未豁免边界必须被拦下 ───────────────────

def test_check_blocks_unwaived_bounds(tmp_path):
    """block 写法且未豁免 → 必须判失败（这正是曾经会被静默放行的场景）。"""
    d = _full_problem(tmp_path, name="p1", bounds=_BLOCK2)
    r = problem_gate.check(d)
    assert r["ok"] is False, r
    assert any("未触达" in f for f in r["fail"]), r["fail"]


def test_check_passes_when_all_bounds_waived(tmp_path):
    d = _full_problem(tmp_path, name="p2", bounds=_BLOCK1, waived=_BLOCK1)
    r = problem_gate.check(d)
    assert r["ok"] is True, r["fail"]
    assert not any("未触达" in f for f in r["fail"])


def test_check_flags_name_mismatch(tmp_path):
    d = _full_problem(tmp_path, name="p3")
    r = problem_gate.check(d, expect_name="expected_other_name")
    assert any("期望名" in f for f in r["fail"]), r["fail"]
