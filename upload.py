#!/usr/bin/env python3
"""
上传题目到 OJ（HUSTOJ 后台导入接口）。

典型用法
--------
# 1) 先看要发生什么（不联网）
python upload.py problems/smoke_dp2 --dry-run

# 2) ⭐ 出题目录 → 导出 FPS XML → 登录 → 上传（多数 HUSTOJ 部署最可靠的一条）
python upload.py problems/smoke_dp2 --kind xml

# 3) 出题目录 → 自动导出 HydroOJ 包 → 登录 → 上传
python upload.py problems/smoke_dp2

# 4) 直接推一个已有文件（平台导出的包 / 自己生成的 xml）
python upload.py export/hydrooj/20260920_195543.zip --kind hydro
python upload.py export/xml/smoke_dp2.xml --kind xml

上传后本脚本会自动把新题从「未启用」切为「启用」（HUSTOJ 各导入器建出来的题目
默认都是未启用，学生端看不到该题）；不想自动切就加 --no-enable。之后请跑
`python oj_check.py <pid>` 验收：HUSTOJ 的各导入器遇到包结构问题时会**静默半成功**
（题目建出来了但题面为空、测试点没进去），只看接口返回的 HTML 会被骗。

凭据解析优先级：命令行参数 > upload_config.toml > 环境变量
（OJ_BASE_URL / OJ_USER / OJ_PASSWORD / OJ_KIND）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_CONFIG = PROJECT_ROOT / "upload_config.toml"
# 各 kind 的包内"标志文件"，用于粗查包结构与 kind 是否匹配
_UPLOAD_ZIP_KEYS = {
    "hydro": "problem.yaml",
    "qduoj": "1/problem.json",
}
# 题目目录能直接打包成哪些 --kind
_DIR_EXPORT_FORMAT = {
    "hydro": "hydrooj",
    "qduoj": "qduoj",
    "xml": "xml",
}
# 每种 kind 允许直接上传的文件后缀
_PKG_SUFFIX = {
    "xml": (".xml", ".zip"),   # HUSTOJ 也接受"zip 内含 xml"的形式
}
_DEFAULT_SUFFIX = (".zip",)


def load_upload_config(path: Path) -> dict:
    """读 upload_config.toml；文件不存在返回空 dict。"""
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # py3.10
        try:
            import toml as tomllib  # type: ignore
            with open(path, "rb") as f:
                return tomllib.load(f)  # type: ignore
        except ModuleNotFoundError:
            print(f"⚠ 无法解析 {path}：Python <3.11 且未安装 toml 包，改用环境变量/命令行参数")
            return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_credentials(cli: argparse.Namespace, cfg: dict) -> dict:
    """CLI > 配置文件 > 环境变量（cli 允许缺字段，便于被别的脚本复用）。"""
    section = cfg.get("oj") or {}
    return {
        "host": getattr(cli, "host", None) or section.get("host") or os.environ.get("OJ_BASE_URL", ""),
        "user": getattr(cli, "user", None) or section.get("user") or os.environ.get("OJ_USER", ""),
        "password": getattr(cli, "password", None) or section.get("password") or os.environ.get("OJ_PASSWORD", ""),
        "kind": getattr(cli, "kind", None) or section.get("kind") or os.environ.get("OJ_KIND", "hydro"),
    }


def resolve_zip(target: Path, args: argparse.Namespace, kind: str) -> Path:
    """目标为题目目录时按 --kind 打包；目标为已打好的包（zip/xml）时原样返回。"""
    if target.is_dir():
        if not (target / "problem.md").exists():
            raise SystemExit(f"✗ {target} 不是题目目录（缺少 problem.md）")
        fmt = _DIR_EXPORT_FORMAT.get(kind)
        if not fmt:
            raise SystemExit(f"✗ --kind {kind} 不能由题目目录直接打包，请自己提供对应格式的包。"
                             f"（目录可直接打包为：{', '.join(sorted(set(_DIR_EXPORT_FORMAT))) }）")
        sys.path.insert(0, str(PROJECT_ROOT))
        import export

        opts = {"dir_name": args.dir_name, "filename": args.filename_opt}
        if args.file_io is not None:
            opts["file_io"] = args.file_io
        if getattr(args, "include_editorial", None) is not None:
            opts["include_editorial"] = args.include_editorial
        opts = {k: v for k, v in opts.items() if v is not None}
        out = export.export_problem(target, [fmt], opts=opts)
        return Path(out[fmt])
    allowed = _PKG_SUFFIX.get(kind, _DEFAULT_SUFFIX)
    if target.suffix.lower() not in allowed:
        raise SystemExit(f"✗ {target} 既不是题目目录，也不是 --kind {kind} 认的包"
                         f"（{' / '.join(allowed)}）")
    return target


def _sanity_check_xml(package: Path) -> list[str]:
    """FPS XML 粗查。导入器对这些字段是"读到就用、读不到就静默留空"，必须提前拦。"""
    if package.suffix.lower() == ".zip":          # HUSTOJ 也吃"zip 内含 xml"
        try:
            with zipfile.ZipFile(package) as zf:
                parts = [zf.read(n).decode("utf-8", "replace")
                         for n in zf.namelist() if n.lower().endswith(".xml")]
        except zipfile.BadZipFile:
            return [f"{package.name} 不是合法 zip"]
        if not parts:
            return [f"{package.name} 里没有 .xml"]
        text = "\n".join(parts)
    else:
        try:
            text = package.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return [f"{package.name} 读取失败: {e}"]

    warn = []
    if "<fps" not in text:
        warn.append("未见 <fps> 根节点，可能不是 FPS XML")
    if "<item>" not in text:
        warn.append("未见 <item>，导入后不会产生任何题目")
    for tag, note in (("description", "题面会是空的"),
                      ("time_limit", "时限会落到 OJ 默认值"),
                      ("memory_limit", "内存会落到 OJ 默认值")):
        # 用前缀匹配：这些标签可能带属性，如 <time_limit unit="s">
        if f"<{tag}" not in text:
            warn.append(f"未见 <{tag}>，{note}")
    n_in = text.count("<test_input")
    n_out = text.count("<test_output")
    if n_in == 0:
        warn.append("未见 <test_input>，导入后没有任何测试点")
    elif n_in != n_out:
        warn.append(f"test_input({n_in}) 与 test_output({n_out}) 数量不一致，部分测试点会缺答案")
    return warn


def sanity_check_zip(zip_path: Path, kind: str) -> list[str]:
    """粗查包结构是否像对应的导入格式，返回可疑点列表（不阻断）。"""
    if kind == "xml":
        return _sanity_check_xml(zip_path)
    warn = []
    key = _UPLOAD_ZIP_KEYS.get(kind)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return [f"{zip_path.name} 不是合法 zip"]
    if key and not any(n == key or n.endswith("/" + key) for n in names):
        warn.append(f"包内未见 {key}，可能不匹配 --kind {kind}")
    if kind == "hydro":
        if not any("/testdata/" in n for n in names):
            warn.append("包内未见 testdata/ 目录")
        # problem.yaml 必须早于 testdata，否则 HUSTOJ 导入时数据会落到空的 pid
        idx_yaml = next((i for i, n in enumerate(names) if n.endswith("problem.yaml")), None)
        idx_data = next((i for i, n in enumerate(names) if "/testdata/" in n), None)
        if idx_yaml is not None and idx_data is not None and idx_yaml > idx_data:
            warn.append("problem.yaml 位于 testdata 之后，HUSTOJ 可能导入不到数据")
    if kind == "qduoj":
        if not any(n.startswith("1/") for n in names):
            warn.append("json 不在 zip 根的 1/ 目录下，HUSTOJ 的 mv 会失败")
        if any(n.count("/") > 2 for n in names):
            warn.append("包内存在三层以上路径，可能整体多套了一层目录")
    return warn


_PID_ADDED_RE = re.compile(r"Problem ID\s+(\d+)\s+added", re.I)
_PID_ADDED_LOOSE_RE = re.compile(r"(\d+)\s+added", re.I)


def parse_added_pid(text: str) -> int | None:
    """
    从导入接口响应里抠出新题 pid。

    HUSTOJ 各导入器成功时都会打 `... - Problem ID <pid> added!`；只建了题但没拿到
    pid 的情况会打 `PID: problem.php?id=0`，此时返回 None（0 不算）。
    """
    for rx in (_PID_ADDED_RE, _PID_ADDED_LOOSE_RE):
        m = rx.search(text)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="上传题目到 OJ（HUSTOJ 导入接口）")
    ap.add_argument("target", type=Path, help="题目目录 或 已打包的 zip")
    ap.add_argument("--kind", "-k", default=None,
                    choices=["hydro", "qduoj", "syzoj", "hoj", "tyvj", "md", "xml"],
                    help="导入接口类型（默认 hydro）")
    ap.add_argument("--host", default=None, help="OJ 根地址，如 https://oj.example.com")
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="凭据 TOML 路径")
    ap.add_argument("--dry-run", action="store_true", help="只解析与校验，不登录不上传")
    ap.add_argument("--no-enable", dest="no_enable", action="store_true",
                    help="导入成功后不要自动把新题切为「启用」（默认会自动切）")
    ap.add_argument("--filename", dest="filename_opt", default=None,
                    help="导出 HydroOJ 包时的 file_io / 测试点前缀")
    ap.add_argument("--dir-name", default=None, help="导出 HydroOJ 包时的顶层目录名")
    ap.add_argument("--include-editorial", dest="include_editorial",
                    action="store_true", default=None,
                    help="XML: 把「题解说明」也写进包（默认不写，避免选手直接看到题解）")
    fio = ap.add_mutually_exclusive_group()
    fio.add_argument("--file-io", dest="file_io", action="store_true", default=None)
    fio.add_argument("--no-file-io", dest="file_io", action="store_false")
    args = ap.parse_args(argv)

    target = args.target if args.target.is_absolute() else (Path.cwd() / args.target)
    if not target.exists():
        print(f"✗ 路径不存在: {target}")
        return 2

    creds = resolve_credentials(args, load_upload_config(args.config))
    kind = creds["kind"]
    zip_path = resolve_zip(target, args, kind)

    print(f"包文件   : {zip_path}")
    print(f"导入接口 : {kind}")
    print(f"OJ 地址  : {creds['host'] or '（未配置）'}")
    for w in sanity_check_zip(zip_path, kind):
        print(f"⚠ {w}")

    missing = [k for k in ("host", "user", "password") if not creds[k]]
    if args.dry_run:
        note = f"（缺凭据 {', '.join(missing)}，实际执行前需补齐）" if missing else ""
        print(f"✓ dry-run：以上为将要执行的导入，未联网。{note}")
        return 0
    if missing:
        print(f"✗ 缺少凭据 {', '.join(missing)}；"
              f"请填 {args.config}（参考 upload_config.toml.example）或用命令行/环境变量提供")
        return 2

    sys.path.insert(0, str(PROJECT_ROOT))
    from integrations import hustoj

    session = hustoj.make_session(creds["user"], creds["password"], creds["host"])
    if session is None:
        print("✗ 登录失败：请核对账号密码，以及该账号是否有题目导入权限")
        return 1

    text = hustoj.upload_problem_zip(session, str(zip_path), kind=kind, url=creds["host"])
    if text.startswith("上传失败"):
        print(f"✗ {text}")
        return 1
    print("✓ 服务器返回：")
    # ⚠ 不要截断：导入器的失败信号（警告行、'skiped'、'Problem ID ... added'）
    #   常常出现在响应后半段，截断会直接把排查线索丢掉。
    limit = 20000
    print(text if len(text) <= limit else text[:limit] + f"\n…（响应共 {len(text)} 字符）")

    pid = parse_added_pid(text)
    if pid is None:
        print("\n⚠ 响应里没有 'Problem ID <pid> added'，无法自动启用。"
              "请到题目列表页手动把该题切为「启用」，或跑 oj_check.py --recent 3 定位")
    elif args.no_enable:
        print(f"\n！按 --no-enable 跳过自动启用：新题 pid = {pid}"
              f"（当前是「未启用」，学生端看不到）")
    else:
        print(f"\n新题 pid = {pid}，正在切为「启用」…")
        ok, msg = hustoj.set_problem_enabled(session, creds["host"], pid, True)
        print(("✓ " if ok else "⚠ ") + msg)

    print(f"\n下一步：python oj_check.py {pid if pid else '--recent 3'}"
          f"   # 核对题面/用例/时限/启用状态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
