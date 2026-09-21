"""
HUSTOJ / 校内 OJ 对接（移植自 `pcoj/oj.py`，统一收进本项目以降低维护复杂度）。

能力：
  - init_csrf_token / login                登录（csrf 令牌 + 账号密码）
  - upload_problem_zip / upload_QDUOJ_zip  上传题目包（xml / hydro / qduoj … 各导入器）
  - problem_status / set_problem_enabled   读、切题目的「启用 | 未启用」状态
  - get_code / get_reinfo                  取提交代码 / 取错误信息
  - history_userSubmit / get_submit_records / history_contestSubmit
                                           拉取提交记录（需要 beautifulsoup4）

依赖：requests（必需）；beautifulsoup4（仅提交记录解析需要，缺省时给出提示）。
CSRF 抽取用正则实现，因此**上传功能不依赖 bs4**。

base_url 可传参覆盖，默认取环境变量 OJ_BASE_URL，最后回落到校内 OJ 地址。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests import Session

logger = logging.getLogger("cp-agent.hustoj")

DEFAULT_BASE_URL = "https://oj.ipachong.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
}


def base_url(url: str | None = None) -> str:
    return (url or os.environ.get("OJ_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _require_bs4():
    try:
        import bs4  # noqa: F401
    except ImportError as e:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "该功能需要 beautifulsoup4：pip install beautifulsoup4"
        ) from e


def _soup(html: str):
    _require_bs4()
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


# ─── 登录 ────────────────────────────────────────────────────────────────────

# 登录失败时 HUSTOJ 回吐的提示（成功时只回吐一段 history.go(-1) 的 JS，不跳转，
# 因此**不能**用 resp.url 是否还停在 login.php 来判断成败）
LOGIN_FAIL_MARKERS = ("UserName or Password Wrong", "Password Wrong", "登录失败")

# 判据页：只有具备管理员/题目导入权限的会话才能渲染出导入表单
IMPORT_PAGE = "admin/problem_import.php"
IMPORT_PAGE_MARKER = "problem_import_"


def init_csrf_token(session: Session, url: str | None = None) -> str | None:
    """取登录页的 csrf 令牌（正则实现，避免依赖 bs4）。"""
    resp = session.get(f"{base_url(url)}/csrf.php", headers=HEADERS)
    if resp.status_code != 200:
        logger.error("获取 CSRF 令牌失败，状态码：%s", resp.status_code)
        return None
    m = re.search(r'name=["\']csrf["\'][^>]*value=["\']([^"\']+)["\']', resp.text) or \
        re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf["\']', resp.text)
    return m.group(1) if m else None


def is_logged_in(session: Session, url: str | None = None,
                 page: str = IMPORT_PAGE, marker: str = IMPORT_PAGE_MARKER) -> bool:
    """靠「能否打开受限后台页」判断登录态与导入权限。"""
    root = base_url(url)
    try:
        resp = session.get(f"{root}/{page}", headers=HEADERS)
    except Exception:  # noqa: BLE001
        logger.exception("权限探测请求失败")
        return False
    return resp.status_code == 200 and marker in resp.text


def login(session: Session, user_id: str, password: str,
          url: str | None = None, submit: str = "", verify: bool = True) -> bool:
    """
    登录 OJ。返回是否成功（成功 = 账号密码通过，且 verify 时能打开导入页）。

    verify=False 只做账号密码校验，不发权限探测请求。
    """
    root = base_url(url)
    token = init_csrf_token(session, url)
    payload = {"user_id": user_id, "password": password, "submit": submit, "csrf": token or ""}
    resp = session.post(f"{root}/login.php", data=payload, headers=HEADERS)
    if resp.status_code != 200:
        logger.error("登录请求失败，状态码：%s", resp.status_code)
        return False
    if any(m in resp.text for m in LOGIN_FAIL_MARKERS):
        logger.error("登录失败：账号或密码错误")
        return False
    if not verify:
        logger.info("登录请求已通过：%s @ %s（未校验权限）", user_id, root)
        return True
    if is_logged_in(session, url):
        logger.info("登录成功：%s @ %s", user_id, root)
        return True
    logger.error("账号密码通过，但打不开 %s/%s —— 该账号可能没有管理员/题目导入权限",
                 root, IMPORT_PAGE)
    return False


# ─── 提交信息 ────────────────────────────────────────────────────────────────

def get_code(session: Session, url: str) -> str | None:
    """取某次提交的源代码。"""
    resp = session.get(url, headers=HEADERS)
    if resp.status_code != 200:
        logger.error("请求失败，状态码：%s", resp.status_code)
        return None
    node = _soup(resp.text).find("pre", id="source")
    if node:
        return node.get_text()
    logger.warning("未找到 <pre id='source'> 标签")
    return None


def get_reinfo(session: Session, sid: str, url: str | None = None) -> str | None:
    """取某次提交的判题错误信息（reinfo.php?sid=）。"""
    resp = session.get(f"{base_url(url)}/reinfo.php?sid={sid}", headers=HEADERS)
    if resp.status_code != 200:
        logger.error("请求失败，状态码：%s", resp.status_code)
        return None
    node = _soup(resp.text).find("code")
    if node:
        return node.get_text()
    logger.warning("未找到 <code> 标签")
    return None


def _parse_table(soup) -> list[dict]:
    """解析用户提交记录页面的表格信息。"""
    table = soup.find("table")
    if table is None:
        return []
    thead = table.find("thead")
    if thead is None:
        return []
    th_contents = [th.get_text(strip=True) for th in thead.find_all("th")]
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []
    return [
        dict(zip(th_contents, [td.get_text(strip=True) for td in tr.find_all("td")]))
        for tr in rows
    ]


def history_userSubmit(session: Session, user_id: str = "", pages: int = 5,
                       url: str | None = None, **kwargs):
    """
    迭代用户提交记录（每页一个 list[dict]）。

    kwargs 可选：problem_id / language / jresult / showsim
    """
    _require_bs4()
    params = {"problem_id": "", "user_id": user_id, "language": -1, "jresult": -1, "showsim": 0}
    for key in list(kwargs):
        if key in params:
            params[key] = kwargs[key]

    target = f"{base_url(url)}/status.php"
    for _ in range(pages):
        resp = session.get(target, headers=HEADERS, params=params)
        if resp.status_code != 200:
            logger.error("用户提交记录请求失败，状态码：%s", resp.status_code)
            return
        soup = _soup(resp.text)
        yield _parse_table(soup)
        nxt = soup.find("a", class_="icon item", id="page_next")
        if not (nxt and nxt.has_attr("href")):
            break
        target = urljoin(target, nxt["href"])


def get_submit_records(session: Session, cid: str = "", problem_id: str = "",
                       user_id: str = "", pages: int = 5, url: str | None = None, **kwargs):
    """迭代题目/题单提交记录（cid 为空则取全平台）。"""
    _require_bs4()
    params = {
        "problem_id": problem_id,
        "user_id": user_id,
        "language": kwargs.get("language", -1),
        "jresult": kwargs.get("jresult", -1),
        "showsim": kwargs.get("showsim", 0),
    }
    target = f"{base_url(url)}/status.php" + (f"?cid={cid}" if cid else "")
    for _ in range(pages):
        resp = session.get(target, headers=HEADERS, params=params)
        if resp.status_code != 200:
            logger.error("提交记录请求失败，状态码：%s", resp.status_code)
            yield None
            continue
        soup = _soup(resp.text)
        yield _parse_table(soup)
        nxt = soup.find("a", class_="icon item", id="page_next")
        if not (nxt and nxt.has_attr("href")):
            break
        target = urljoin(target, nxt["href"])


def history_contestSubmit(session: Session, cid: str, pages: int = 5,
                          url: str | None = None, **kwargs):
    """兼容旧接口：题单提交记录。"""
    return get_submit_records(session, cid=cid, pages=pages, url=url, **kwargs)


# ─── 题目上传 ────────────────────────────────────────────────────────────────
#
# HUSTOJ 后台各导入入口（admin/problem_import*.php）都收同一个文件字段 `fps`，
# 差别只在 action：
#   xml    → problem_import_xml.php      FPS XML / 内含 XML 的 zip
#   qduoj  → problem_import_qduoj.php    QDUOJ 的 json+testcase zip
#   syzoj  → problem_import_syzoj.php    SYZOJ zip
#   hydro  → problem_import_hydro.php    HydroOJ zip（= 本项目 export.py hydrooj 产物）
#   hoj    → problem_import_hoj.php      HOJ zip
#   tyvj   → problem_import_tyvj.php     TYVJ zip
#   md     → problem_import_md.php       markdown zip
#
# ⚠ 新版 HUSTOJ 在这些脚本开头 require 了 include/check_post_key.php：
#   它要求 POST 里带上与 session 一致的一次性 `postkey`（10 位大写 HEX），
#   该值由 include/set_post_key.php 在**渲染导入页面时**写进 session 并输出为
#   隐藏 input。因此脚本化上传必须：先 GET /admin/problem_import.php 取 postkey，
#   再带着它 POST 文件，否则服务端直接输出 `post key fail...` 并 exit(1)。
#   （原 pcoj/oj.py 的 upload_QDUOJ_zip 没有这一步，在较新版本上会失败。）

IMPORT_ENDPOINTS = {
    "hydro": "problem_import_hydro.php",
    "qduoj": "problem_import_qduoj.php",
    "syzoj": "problem_import_syzoj.php",
    "hoj": "problem_import_hoj.php",
    "tyvj": "problem_import_tyvj.php",
    "md": "problem_import_md.php",
    "xml": "problem_import_xml.php",
}

_POSTKEY_RE = re.compile(r'name=["\']postkey["\'][^>]*value=["\']([^"\']+)["\']')
_POSTKEY_RE_ALT = re.compile(r'value=["\']([^"\']+)["\'][^>]*name=["\']postkey["\']')


def fetch_postkey(session: Session, url: str | None = None,
                  page: str = IMPORT_PAGE) -> str | None:
    """GET 导入页面并抓取一次性 postkey；老版本 HUSTOJ 没有该字段时返回 None。"""
    root = base_url(url)
    resp = session.get(f"{root}/{page}", headers=HEADERS)
    if resp.status_code != 200:
        logger.warning("获取导入页失败（%s），尝试不带 postkey 上传", resp.status_code)
        return None
    m = _POSTKEY_RE.search(resp.text) or _POSTKEY_RE_ALT.search(resp.text)
    return m.group(1) if m else None


def upload_problem_zip(session: Session, file_path: str, kind: str = "hydro",
                       url: str | None = None) -> str:
    """
    上传题目包到 HUSTOJ 后台导入接口。

    :param session: 已登录（且具备管理员/题目导入权限）的 requests.Session
    :param file_path: 本地包路径（.zip；kind="xml" 时也可以是 .xml）
    :param kind: hydro / qduoj / syzoj / hoj / tyvj / md / xml
    :param url: OJ 根地址，默认 DEFAULT_BASE_URL / 环境变量 OJ_BASE_URL
    :return: 服务器返回文本（失败信息也在文本里，便于人工判断）
    """
    if kind not in IMPORT_ENDPOINTS:
        return f"上传失败: 未知导入类型 {kind}（可选 {', '.join(IMPORT_ENDPOINTS)}）"
    root = base_url(url)
    target = f"{root}/admin/{IMPORT_ENDPOINTS[kind]}"
    ext = Path(file_path).suffix.lower()
    mime = {"zip": "application/zip", "xml": "text/xml"}.get(
        ext, "application/octet-stream")
    try:
        with open(file_path, "rb") as f:
            files = {"fps": (os.path.basename(file_path), f, mime)}
            data = {}
            postkey = fetch_postkey(session, url)
            if postkey:
                data["postkey"] = postkey
            resp = session.post(target, files=files, data=data or None, headers=HEADERS)
            resp.raise_for_status()
            text = resp.text
            if "post key fail" in text:
                return ("上传失败: post key 校验未通过。"
                        "请确认已登录管理员账号，且上传前能正常打开 "
                        f"{root}/admin/problem_import.php")
            logger.info("上传完成：%s → %s（%d 字节响应）", os.path.basename(file_path),
                        IMPORT_ENDPOINTS[kind], len(text))
            return text
    except Exception as e:  # noqa: BLE001 - 与参考实现保持一致：吞异常返回文本
        logger.exception("上传失败")
        return f"上传失败: {e}"


def upload_QDUOJ_zip(session: Session, file_path: str, url: str | None = None) -> str:
    """兼容原 pcoj/oj.py 的接口名：以 QDUOJ 格式 zip 上传。"""
    return upload_problem_zip(session, file_path, kind="qduoj", url=url)


# ─── 题目启用状态 ────────────────────────────────────────────────────────────
#
# 靠导入接口建出来的题目，在这台 OJ 上**默认是「未启用」**（admin/problem_list.php
# 里的红字），学生端看不到。切换入口不在 problem_edit.php —— 那个表单里根本没有
# 启用字段（`is_md` 只是 Markdown/HTML 编辑器开关，改它不影响状态），而是：
#
#     GET admin/problem_df_change.php?id=<pid>&getkey=<KEY>&csrf=<TOKEN>
#
#   - getkey：会话级令牌，从 admin/problem_list.php 任一行的切换链接里抓；
#   - csrf  ：来自 /csrf.php。**缺了它服务端只回一段 `$("#csrf").load(...)`
#             的 JS 再 history.go(-1)，什么都不会发生** —— 这就是"点了没反应"、
#             也是脚本改状态"看起来成功但状态没变"的真因。
#
# ⚠ 该接口是**切换（toggle）语义**，不是幂等设置：已启用时再调一次会变回未启用。
#   所以 set_problem_enabled 一律先读状态，只在需要时切换。

PROBLEM_LIST_PAGE = "admin/problem_list.php"
_GETKEY_RE = re.compile(r"getkey=([0-9A-Fa-f]{6,})")
_ROW_PID_RE = re.compile(r"name='pid\[\]'\s+value='(\d+)'")

STATUS_ON = "on"
STATUS_OFF = "off"


def fetch_getkey(session: Session, url: str | None = None,
                 page: str = PROBLEM_LIST_PAGE) -> str | None:
    """抓题目列表页里的会话级 getkey（切换启用状态要用）。老版本没有则返回 None。"""
    resp = session.get(f"{base_url(url)}/{page}", headers=HEADERS)
    if resp.status_code != 200:
        logger.warning("获取题目列表页失败（%s），无法切换启用状态", resp.status_code)
        return None
    m = _GETKEY_RE.search(resp.text)
    return m.group(1) if m else None


def problem_states(session: Session, url: str | None = None,
                   page: str = PROBLEM_LIST_PAGE) -> dict[int, str]:
    """
    读 admin/problem_list.php，返回 {pid: 'on' | 'off'}。

    HUSTOJ 用同一段模板渲染状态单元格：未启用 = `<span class=red>未启用</span>`，
    启用 = `<span class=green>启用</span>`，故按行文本判定即可。
    """
    resp = session.get(f"{base_url(url)}/{page}", headers=HEADERS)
    if resp.status_code != 200:
        logger.warning("获取题目列表页失败：HTTP %s", resp.status_code)
        return {}
    states: dict[int, str] = {}
    for row in resp.text.split("<tr>"):
        m = _ROW_PID_RE.search(row)
        if not m:
            continue
        if "未启用" in row:
            states[int(m.group(1))] = STATUS_OFF
        elif ">启用<" in row:
            states[int(m.group(1))] = STATUS_ON
    return states


def problem_status(session: Session, url: str | None, pid: int) -> str | None:
    """单题状态：'on' / 'off' / None（列表页里找不到，例如已被删除）。"""
    return problem_states(session, url).get(int(pid))


def latest_problem_id(session: Session, url: str | None = None) -> int | None:
    """
    最新题目 pid。

    题目列表页按 pid 倒序，取最大 pid 即可。比"倍增+二分探测"可靠：HUSTOJ 的
    pid 空间有空洞（删题留洞），二分会被空洞带偏到很低的 pid 上。
    """
    states = problem_states(session, url)
    return max(states) if states else None


def set_problem_enabled(session: Session, url: str | None, pid: int, enabled: bool = True,
                        verify: bool = True, retries: int = 5,
                        delay: float = 2.5) -> tuple[bool, str]:
    """
    把某题切到「启用 / 未启用」。返回 (是否达成目标状态, 说明文本)。

    先读状态再决定是否切换（接口是 toggle 语义），切换后轮询复核 —— HUSTOJ 页面
    有 10 秒缓存（`$cache_time = 10`），切换完立刻读会读到旧状态。
    """
    target = STATUS_ON if enabled else STATUS_OFF
    label = "启用" if enabled else "未启用"
    current = problem_status(session, url, pid)
    if current is None:
        return False, f"{pid} 不在题目列表页里（可能已被删除，或该账号看不到）"
    if current == target:
        return True, f"{pid} 已是「{label}」，无需切换"

    key = fetch_getkey(session, url)
    token = init_csrf_token(session, url)
    if not key:
        return False, f"抓不到 getkey，无法切换 {pid} 的启用状态"
    if not token:
        return False, f"抓不到 csrf 令牌，无法切换 {pid} 的启用状态"

    root = base_url(url)
    resp = session.get(f"{root}/admin/problem_df_change.php",
                       params={"id": pid, "getkey": key, "csrf": token}, headers=HEADERS)
    if resp.status_code != 200:
        return False, f"切换请求失败：HTTP {resp.status_code}"
    if not verify:
        return True, f"已提交「{label}」请求（未复核）"

    import time as _time

    for _ in range(max(1, retries)):
        _time.sleep(delay)
        if problem_status(session, url, pid) == target:
            return True, f"{pid} 已切换为「{label}」"
    return False, (f"已提交「{label}」请求，但 {retries} 次复核仍读回旧状态；"
                   f"可稍后跑 `python oj_check.py {pid}` 确认")


def make_session(user_id: str, password: str, url: str | None = None,
                 submit: str = "") -> Session | None:
    """便捷入口：新建 session 并登录，失败返回 None。"""
    session = requests.Session()
    return session if login(session, user_id, password, url, submit) else None


if __name__ == "__main__":  # 手工自检：python -m integrations.hustoj
    logging.basicConfig(level=logging.INFO)
    print("base_url =", base_url())
