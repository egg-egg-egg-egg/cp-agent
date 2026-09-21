#!/usr/bin/env python3
"""
验收工具：只读核对 OJ 上题目是否真的"导入成功"。

为什么需要它：HUSTOJ 的各导入器（hydro / qduoj / xml）遇到包结构问题时会
**静默半成功**——题目记录建出来了，但题面为空、时限内存停在默认值、
测试点没进去，甚至状态是"未启用"。只看导入接口返回的 HTML 会被骗，
必须回头核对题库的真实状态。

用法
----
python oj_check.py 19600                # 查单个
python oj_check.py 19596 19597 19600    # 查多个
python oj_check.py --recent 5           # 查最近 5 个 pid
python oj_check.py 19600 --no-data      # 跳过测试点核对（大数据题更快）

核对项
------
题面字符数 / 时限 / 内存 / 测试点数（读 FPS XML 导出反算）/ 是否"未启用"
凭据解析与 upload.py 一致：命令行 > upload_config.toml > 环境变量。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from upload import DEFAULT_CONFIG, load_upload_config, resolve_credentials  # noqa: E402

TIMEOUT = 60
TAG = re.compile(r"<[^>]+>")


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", html)).strip()


def probe(session, root: str, pid: int, with_data: bool) -> dict:
    """只看状态用 with_data=False，避免拉几十 MB 的 XML。"""
    from integrations import hustoj

    info: dict = {"pid": pid, "exists": False}
    try:
        r = session.get(f"{root}/problem.php?id={pid}",
                        headers=hustoj.HEADERS, timeout=TIMEOUT)
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return info
    if r.status_code != 200:
        info["error"] = f"HTTP {r.status_code}"
        return info
    html = r.text
    m = re.search(r"<title>(.*?)</title>", html)
    title = m.group(1).strip() if m else "?"
    if "错误信息" in title:
        return info
    info.update(exists=True, title=title, disabled="未启用" in html)
    body = plain(html)
    lim = re.search(r"([0-9.]+)\s*(?:S|s)\b", body)
    mem = re.search(r"([0-9]+)\s*(?:MB|M)\b", body)
    info["time"] = lim.group(1) if lim else "?"
    info["memory"] = mem.group(1) if mem else "?"
    d = re.search(r'font-content">(.*?)</div>', html, re.S)
    info["desc"] = len(plain(d.group(1))) if d else 0

    if with_data:
        try:
            r = session.post(f"{root}/admin/problem_export_xml.php",
                             data={"in": str(pid), "do": "do", "postkey": ""},
                             headers=hustoj.HEADERS, timeout=TIMEOUT * 5)
            info["cases"] = r.text.count("<test_input")
            info["bytes"] = len(r.text)
        except Exception as e:
            info["cases"] = f"ERR {type(e).__name__}"
    return info


def _latest_pid(session, root: str, limit: int = 1 << 20) -> int:
    """倍增找上界 + 二分收敛，定位当前最大有效 pid。"""
    hi = 1
    while hi < limit and probe(session, root, hi, False)["exists"]:
        hi *= 2
    lo = hi // 2
    hi = min(hi, limit)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if probe(session, root, mid, False)["exists"]:
            lo = mid
        else:
            hi = mid
    return lo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="只读验收 OJ 上题目的真实状态")
    ap.add_argument("pids", nargs="*", type=int, help="题目 ID")
    ap.add_argument("--recent", type=int, default=0, help="改查最近 N 个 pid")
    ap.add_argument("--no-data", action="store_true", help="不读 FPS XML，跳过测试点核对")
    ap.add_argument("--enable", action="store_true",
                    help="把检出「未启用」的题目切回启用（默认只读，不改任何东西）")
    ap.add_argument("--host", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args(argv)
    if not args.pids and not args.recent:
        ap.error("请给 pid（如 oj_check.py 19600），或用 --recent N")

    creds = resolve_credentials(args, load_upload_config(args.config))
    missing = [k for k in ("host", "user", "password") if not creds[k]]
    if missing:
        print(f"✗ 缺少凭据 {', '.join(missing)}（参考 upload_config.toml.example）")
        return 2

    from integrations import hustoj

    session = hustoj.make_session(creds["user"], creds["password"], creds["host"])
    if session is None:
        print("✗ 登录失败")
        return 1
    root = hustoj.base_url(creds["host"])

    pids = list(args.pids)
    if args.recent:
        # 优先读管理端题目列表（按 pid 倒序、只含真实存在的题）；pid 空间有空洞，
        # 用 range 或二分探测都会把已删题的低 pid 带进来，误报成"可疑题目"。
        states = hustoj.problem_states(session, creds["host"])
        if states:
            pids = sorted(states)[-args.recent:]
            print(f"（题目列表页最新 pid = {pids[-1]}）")
        else:
            top = _latest_pid(session, root)
            pids = list(range(max(1, top - args.recent + 1), top + 1))
            print(f"（最新 pid = {top}，探测法）")

    head = f"{'PID':<8}{'题面':>6}{'用例':>6}{'时限':>8}{'内存':>8}  状态      标题"
    print(head)
    print("-" * len(head) * 2)
    bad = []
    off = []
    for pid in pids:
        i = probe(session, root, pid, not args.no_data)
        if not i["exists"]:
            print(f"{pid:<8}{'—':>6}{'—':>6}{'—':>8}{'—':>8}  {'不存在':<8}{i.get('error', '')}")
            continue
        cases = i.get("cases", "—")
        state = "未启用" if i["disabled"] else "正常"
        print(f"{pid:<8}{i['desc']:>6}{str(cases):>6}{i['time'] + 's':>8}"
              f"{i['memory'] + 'MB':>8}  {state:<8}{i['title']}")
        if i["desc"] < 20 or i["disabled"] or (isinstance(cases, int) and cases == 0):
            bad.append(pid)
        if i["disabled"]:
            off.append(pid)

    print()
    if bad:
        print(f"⚠ 可疑题目（题面过短 / 未启用 / 无用例）：{bad}")
        print("  → 多半是导入器'半成功'，题面与测试点需另想办法补齐。")
    else:
        print("✓ 以上题目：题面、用例、启用状态均正常。")

    if args.enable and off:
        print("\n切为「启用」（problem_df_change.php）：")
        for pid in off:
            ok, msg = hustoj.set_problem_enabled(session, creds["host"], pid, True)
            print(("  ✓ " if ok else "  ⚠ ") + msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
