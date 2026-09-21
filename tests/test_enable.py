"""题目启用状态链路测试：全部用假 session，不联网。

背景（踩过的坑）：
  - `problem_edit.php` 里**没有**启用字段，`is_md` 只是 Markdown/HTML 编辑器开关；
  - 真正入口是 `admin/problem_df_change.php?id=&getkey=&csrf=`，且是 **toggle** 语义；
  - 缺 `csrf` 时服务端只回一段 `$("#csrf").load(...)` + `history.go(-1)` 的 JS，
    状态纹丝不动 —— 所以必须"先读、再切、后复核"。
"""
import importlib.util
import re
from pathlib import Path

from integrations import hustoj


class FakeResp:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


def _row(pid: int, state: str, getkey: str | None) -> str:
    cell = (f"<td><a href=problem_df_change.php?id={pid}&getkey={getkey}>"
            + ("<span class=red title='click to be available'>未启用</span>"
               if state == "off" else
               "<span titlc='click to reserve it' class=green>启用</span>")
            + "</a><td>")
    return (f"<tr><td>{pid} <input type=checkbox name='pid[]' value='{pid}'></td>"
            f"<td><a href='../problem.php?id={pid}'>T{pid}</a></td><td>0</td>"
            f"<td>2026-09-21 13:14:31</td>{cell}</tr>")


class FakeOJ:
    """模拟 admin/problem_list.php + problem_df_change.php + csrf.php 三者联动。"""

    def __init__(self, states: dict[int, str], getkey: str | None = "AB12CD34EF",
                 csrf: str | None = "tokEN123"):
        self.states = dict(states)
        self.getkey = getkey
        self.csrf = csrf
        self.gets: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def _list_html(self) -> str:
        # 批量按钮故意带上 value='启用'/'未启用'，用来验证行解析不会被它们带偏
        head = ("<input type=submit name='enable' value='启用'>"
                "<input type=submit name='disable' value='未启用'>")
        return head + "<table>" + "".join(
            _row(p, s, self.getkey) for p, s in self.states.items()) + "</table>"

    def get(self, url, **kwargs):
        self.gets.append(url)
        self.calls.append((url, kwargs))
        params = kwargs.get("params") or {}
        if url.endswith("/csrf.php"):
            if self.csrf is None:
                return FakeResp("<html>no token</html>")
            return FakeResp(f'<input type="hidden" name="csrf" value="{self.csrf}">')
        if url.endswith("/admin/problem_list.php"):
            return FakeResp(self._list_html())
        if url.endswith("/admin/problem_df_change.php"):
            pid = int(params["id"])
            if params.get("csrf") != self.csrf:
                # 缺 csrf：服务端只回刷新脚本，什么都不改
                return FakeResp('$("#csrf").load("../csrf.php"); history.go(-1);')
            self.states[pid] = "on" if self.states[pid] == "off" else "off"
            return FakeResp("<script>history.go(-1)</script>")
        return FakeResp("")


URL = "https://oj.test"


# ─── 读状态 ──────────────────────────────────────────────────────────────────

def test_problem_states_parses_per_row():
    s = FakeOJ({19600: "on", 19601: "off", 19595: "off"})
    assert hustoj.problem_states(s, URL) == {19600: "on", 19601: "off", 19595: "off"}
    assert hustoj.problem_status(s, URL, 19601) == "off"
    assert hustoj.problem_status(s, URL, 99999) is None


def test_latest_problem_id_is_max_pid():
    s = FakeOJ({19595: "off", 19603: "off", 19601: "on"})
    assert hustoj.latest_problem_id(s, URL) == 19603


def test_fetch_getkey_and_missing():
    assert hustoj.fetch_getkey(FakeOJ({19601: "off"}), URL) == "AB12CD34EF"
    assert hustoj.fetch_getkey(FakeOJ({19601: "off"}, getkey=None), URL) is None


# ─── 切状态 ──────────────────────────────────────────────────────────────────

def test_set_enabled_is_noop_when_already_on():
    s = FakeOJ({19601: "on"})
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, True, delay=0)
    assert ok and "无需切换" in msg
    assert not any("problem_df_change" in u for u in s.gets)   # 不应发出切换请求


def test_set_enabled_toggles_off_to_on_and_verifies():
    s = FakeOJ({19601: "off"})
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, True, delay=0)
    assert ok and "已切换为「启用」" in msg
    assert s.states[19601] == "on"
    assert any("problem_df_change.php" in u for u in s.gets)


def test_set_enabled_sends_id_getkey_and_csrf():
    s = FakeOJ({19601: "off"})
    hustoj.set_problem_enabled(s, URL, 19601, True, delay=0)
    call = next(k for u, k in s.calls if u.endswith("problem_df_change.php"))
    assert call["params"] == {"id": 19601, "getkey": "AB12CD34EF", "csrf": "tokEN123"}
    assert s.states[19601] == "on"


def test_set_disabled_toggles_on_to_off():
    s = FakeOJ({19601: "on"})
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, False, delay=0)
    assert ok and "未启用" in msg
    assert s.states[19601] == "off"


def test_set_enabled_fails_without_csrf():
    """缺 csrf → 服务端不理，复核读回旧状态 → 必须报失败，不能谎报成功。"""
    s = FakeOJ({19601: "off"}, csrf=None)
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, True, retries=1, delay=0)
    assert ok is False and "csrf" in msg
    assert s.states[19601] == "off"


def test_set_enabled_fails_without_getkey():
    s = FakeOJ({19601: "off"}, getkey=None)
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, True, retries=1, delay=0)
    assert ok is False and "getkey" in msg


def test_set_enabled_unknown_pid():
    s = FakeOJ({19601: "off"})
    ok, msg = hustoj.set_problem_enabled(s, URL, 99999, True, delay=0)
    assert ok is False and "不在题目列表页" in msg


def test_set_enabled_verify_false_skips_polling():
    s = FakeOJ({19601: "off"})
    ok, msg = hustoj.set_problem_enabled(s, URL, 19601, True, verify=False)
    assert ok and "未复核" in msg
    assert s.states[19601] == "on"


# ─── upload.py 侧：从响应里抠 pid ────────────────────────────────────────────

def _import_upload():
    spec = importlib.util.spec_from_file_location(
        "upload_cli2", Path(__file__).resolve().parents[1] / "upload.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_added_pid_variants():
    mod = _import_upload()
    assert mod.parse_added_pid("20260921_131431/smoke_dp2.xml 长廊里的金币 - Problem ID 19601 added!") == 19601
    assert mod.parse_added_pid("<hr>skip 长廊里的金币 PID: problem.php?id=0") is None
    assert mod.parse_added_pid("post key fail...") is None


def test_parse_added_pid_ignores_zero():
    mod = _import_upload()
    assert mod.parse_added_pid("Problem ID 0 added") is None
