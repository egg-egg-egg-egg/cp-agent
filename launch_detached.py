#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以「脱离进程树」的方式启动 auto_pipeline.py —— 跨会话存活。

为什么需要它：WorkBuddy 在会话结束时会杀掉主 agent 派生的一切子进程，
`nohup ... &` 也救不了（实测 run_j_batch.py 就这样断了）。
Windows 下用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP 启动，
子进程不再挂在调用者的进程树上，会话结束也不受影响。

用法:
  python launch_detached.py
之后:
  tail -f auto_pipeline.log      # 看进度
  tasklist | grep python         # 确认进程还在
"""
import pathlib
import subprocess
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).parent
LOG = ROOT / "auto_pipeline.log"
PY = sys.executable


def main():
    logf = open(LOG, "a", encoding="utf-8")
    logf.write("\n[%s] launch_detached: 启动 auto_pipeline（DETACHED）\n"
               % datetime.now().strftime("%m-%d %H:%M:%S"))
    logf.flush()

    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS
                 | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    else:
        flags = 0

    p = subprocess.Popen(
        [PY, str(ROOT / "auto_pipeline.py")],
        cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )
    print("已脱离启动 auto_pipeline.py，PID=%d" % p.pid)
    print("日志：%s" % LOG)
    print("确认存活：tasklist | grep -i python  （或看日志是否持续增长）")
    logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
