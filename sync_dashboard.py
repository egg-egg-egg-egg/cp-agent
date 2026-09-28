#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键让「信奥题库看板」数据库跟上仓库产物（幂等）。

做的事（按序）：
  0. 静态门禁：对**已下载、库里还没有**的题跑质检，脏产物一律拦下不进库
  1. 新增同步：把通过门禁的新题 batch_add 进数据表
  2. PID 回填：把 uploaded_pids.txt 里有值、库里还是「无PID」的题 batch_update
  3. 删除幽灵记录：目录与上传记录都已消失、库里却还记着 PID 的题 batch_delete

任何一步「无事可做」都会自动跳过，所以随时可以重复运行。
下载脚本跑完、以及上传 OJ 跑完后都会自动调它；也可手动运行。

用法:
  python sync_dashboard.py                 # 门禁 + 同步 + PID 回填 + 删幽灵
  python sync_dashboard.py --dry-run       # 只报告，不写库
  python sync_dashboard.py --strict        # 门禁告警也当失败（更严）
  python sync_dashboard.py --no-gate       # 跳过门禁（慎用）
  python sync_dashboard.py --no-prune      # 不删幽灵记录（只同步）
  python sync_dashboard.py --lib <dir>     # 指定 library 脚本目录
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(HERE, ".workbuddy", "skills", "problem-dashboard", "scripts")
BUILD_RECORDS = os.path.join(SCRIPT_DIR, "build_records.py")
DATABASE_ID = "TKB9rZWsiuJUJJHDjkL55J"

LIB_CANDIDATES = [
    os.environ.get("WORKBUDDY_LIBRARY_DIR", ""),
    r"C:\Users\Coder\AppData\Local\Programs\WorkBuddy\resources\app.asar.unpacked"
    r"\resources\plugins\workbuddy-builtin\skills\library",
]


def find_root():
    d = HERE
    for _ in range(8):
        if os.path.exists(os.path.join(d, "tasks.yaml")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return HERE


def find_lib(explicit=""):
    for c in ([explicit] if explicit else []) + LIB_CANDIDATES:
        if c and os.path.exists(os.path.join(c, "database", "query_database_record.py")):
            return c
    return ""


def run(cmd, stdin_data=None, check=True):
    r = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError("命令失败(%d): %s\n%s\n%s"
                           % (r.returncode, " ".join(cmd), r.stdout[-800:], r.stderr[-800:]))
    return r


def query_db(lib):
    body = json.dumps({"database_id": DATABASE_ID, "page_size": 200})
    fd, path = tempfile.mkstemp(suffix=".json", prefix="dash_q_")
    os.close(fd)
    r = run([sys.executable, os.path.join(lib, "database", "query_database_record.py"), "--stdin"],
            stdin_data=body)
    open(path, "w", encoding="utf-8").write(r.stdout)
    return path


def main():
    ap = argparse.ArgumentParser(description="看板数据库同步（门禁 + 新增 + PID 回填）")
    ap.add_argument("--root", default="", help="项目根（缺省自动查找 tasks.yaml）")
    ap.add_argument("--lib", default="", help="library 脚本目录")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    ap.add_argument("--no-gate", dest="no_gate", action="store_true", help="跳过门禁")
    ap.add_argument("--no-prune", dest="no_prune", action="store_true", help="不删除幽灵记录")
    ap.add_argument("--strict", action="store_true", help="门禁告警也当失败")
    a = ap.parse_args()

    root = a.root or find_root()
    lib = find_lib(a.lib)
    if not lib:
        print("[!] 找不到 library 脚本目录，用 --lib 指定", file=sys.stderr)
        return 2
    if not os.path.exists(BUILD_RECORDS):
        print("[!] 找不到 %s" % BUILD_RECORDS, file=sys.stderr)
        return 2

    tmp = []
    try:
        # ── 1) 拉库里现有编号
        q1 = query_db(lib)
        tmp.append(q1)

        # ── 2) 算增量（build_records 内部会跑门禁）
        br_args = [sys.executable, BUILD_RECORDS, "--root", root,
                   "--sync", "--have", q1]
        if a.no_gate:
            br_args.append("--no-gate")
        if a.strict:
            br_args.append("--strict")
        r = run(br_args, check=False)
        sys.stderr.write(r.stderr)
        payload = json.loads(r.stdout or "{}")
        new_records = payload.get("records") or []

        added = 0
        if new_records:
            if a.dry_run:
                print("[dry-run] 将新增 %d 条" % len(new_records))
            else:
                run([sys.executable, os.path.join(lib, "database", "batch_add_database_records.py"),
                     "--stdin"], stdin_data=json.dumps(payload, ensure_ascii=False))
                added = len(new_records)
        else:
            print("新增：无（库里已是最新，或被门禁全部拦下）")

        # ── 3) PID 回填
        q2 = query_db(lib)
        tmp.append(q2)
        r2 = run([sys.executable, BUILD_RECORDS, "--root", root, "--pid-diff", "--have", q2],
                 check=False)
        sys.stderr.write(r2.stderr)
        upd = json.loads(r2.stdout or "{}")
        upd_records = upd.get("records") or []

        updated = 0
        if upd_records:
            if a.dry_run:
                print("[dry-run] 将回填 PID %d 条" % len(upd_records))
            else:
                run([sys.executable, os.path.join(lib, "database", "batch_update_database_records.py"),
                     "--stdin"], stdin_data=json.dumps(upd, ensure_ascii=False))
                updated = len(upd_records)
        else:
            print("PID 回填：无（已上传的题 PID 都已在库中）")

        # ── 4) 删除幽灵记录（目录与上传记录都消失、库里却还记着 PID）
        removed = 0
        if not a.no_prune:
            q3 = query_db(lib)
            tmp.append(q3)
            r3 = run([sys.executable, BUILD_RECORDS, "--root", root, "--prune-diff", "--have", q3],
                     check=False)
            sys.stderr.write(r3.stderr)
            victims = (json.loads(r3.stdout or "{}")).get("record_ids") or []
            if victims:
                if a.dry_run:
                    print("[dry-run] 将删除 %d 条幽灵记录" % len(victims))
                else:
                    run([sys.executable, os.path.join(lib, "database", "batch_delete_database_records.py"),
                         "--stdin"], stdin_data=r3.stdout)
                    removed = len(victims)
            else:
                print("删除：无（没有目录已消失却仍留在库里的题）")

        print("\n完成：新增 %d 条，回填 PID %d 条，删除幽灵 %d 条%s"
              % (added, updated, removed, "（dry-run，未写库）" if a.dry_run else ""))
        return 0
    except RuntimeError as e:
        print("[!] %s" % e, file=sys.stderr)
        return 1
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
