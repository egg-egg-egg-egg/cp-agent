"""上传链路测试：全部用假 session，不联网。"""
import glob
import zipfile
from pathlib import Path

import pytest

from integrations import hustoj

CSRF_PAGE = '<input type="hidden" name="csrf" value="tokEN123" class="1">'
POSTKEY_PAGE = ('<form action="problem_import_hydro.php" method=post>'
                '<input type=hidden name="postkey" value="AB12CD34EF">'
                '<input type=file name=fps required></form>')
POSTKEY_PAGE_ALT = ('<input value="ZZ99" name=\'postkey\'>')


class FakeResp:
    def __init__(self, text="", status_code=200, url="https://oj.test/login.php"):
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """只记录请求，不做网络 IO。

    `admin_html` 用于模拟「受限后台页」：登录成功后能否打开它，是 login() 的判据。
    """

    def __init__(self, get_html="", post_text='<b>导入完成</b>', post_url="https://oj.test/",
                 admin_html=None):
        self.get_html = get_html
        self.admin_html = get_html if admin_html is None else admin_html
        self.post_text = post_text
        self.post_url = post_url
        self.gets = []
        self.posts = []          # [(url, kwargs), ...]

    def get(self, url, **kwargs):
        self.gets.append(url)
        if "admin/problem_import" in url:
            return FakeResp(self.admin_html)
        return FakeResp(self.get_html)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResp(self.post_text, url=self.post_url)


@pytest.fixture
def sample_zip(tmp_path):
    p = tmp_path / "pkg.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("d/problem.yaml", "title: t\n")
        zf.writestr("d/testdata/config.yaml", "time: 1s\n")
    return p


def test_init_csrf_token_parses_both_attribute_orders():
    s = FakeSession(get_html=CSRF_PAGE)
    assert hustoj.init_csrf_token(s, "https://oj.test") == "tokEN123"
    s2 = FakeSession(get_html='<input value="REV99" name="csrf">')
    assert hustoj.init_csrf_token(s2, "https://oj.test") == "REV99"
    assert s.gets[0].endswith("/csrf.php")


def test_fetch_postkey_and_missing():
    assert hustoj.fetch_postkey(FakeSession(get_html=POSTKEY_PAGE), "https://oj.test") == "AB12CD34EF"
    assert hustoj.fetch_postkey(FakeSession(get_html=POSTKEY_PAGE_ALT), "https://oj.test") == "ZZ99"
    # 老版本没有 postkey → None（仍可上传）
    assert hustoj.fetch_postkey(FakeSession(get_html="<form></form>"), "https://oj.test") is None


def test_upload_problem_zip_posts_fps_and_postkey(sample_zip):
    s = FakeSession(get_html=POSTKEY_PAGE)
    text = hustoj.upload_problem_zip(s, str(sample_zip), kind="hydro", url="https://oj.test")
    url, kwargs = s.posts[0]
    assert url == "https://oj.test/admin/problem_import_hydro.php"
    assert kwargs["files"]["fps"][0] == "pkg.zip"
    assert kwargs["data"] == {"postkey": "AB12CD34EF"}
    assert text == "<b>导入完成</b>"


def test_upload_problem_zip_without_postkey_sends_no_data(sample_zip):
    s = FakeSession(get_html="<form></form>")
    hustoj.upload_problem_zip(s, str(sample_zip), kind="hydro", url="https://oj.test")
    assert s.posts[0][1]["data"] is None


def test_upload_reports_postkey_rejection(sample_zip):
    s = FakeSession(get_html=POSTKEY_PAGE, post_text="post key fail...")
    out = hustoj.upload_problem_zip(s, str(sample_zip), kind="hydro", url="https://oj.test")
    assert out.startswith("上传失败") and "post key" in out


def test_upload_unknown_kind_and_missing_file(tmp_path, sample_zip):
    assert hustoj.upload_problem_zip(FakeSession(), str(sample_zip), kind="nope").startswith("上传失败")
    assert hustoj.upload_problem_zip(FakeSession(get_html=POSTKEY_PAGE),
                                     str(tmp_path / "nope.zip")).startswith("上传失败")


def test_qduoj_alias_hits_qduoj_endpoint(sample_zip):
    s = FakeSession(get_html=POSTKEY_PAGE)
    hustoj.upload_QDUOJ_zip(s, str(sample_zip), "https://oj.test")
    assert s.posts[0][0].endswith("/admin/problem_import_qduoj.php")


def test_login_detects_success_by_admin_page():
    """HUSTOJ 登录成功时 login.php 只回吐 history.go(-1) 的 JS，不跳转；
    判据必须是「能否打开受限后台页」，而不是 resp.url 是否还停在 login.php。"""
    good = FakeSession(get_html=CSRF_PAGE,
                       post_text="<script>setTimeout('history.go(-1)',500);</script>",
                       post_url="https://oj.test/login.php",
                       admin_html=POSTKEY_PAGE)          # 能打开导入页 → 视为成功
    assert hustoj.login(good, "u", "p", "https://oj.test") is True
    assert good.posts[0][1]["data"]["csrf"] == "tokEN123"


def test_login_rejects_wrong_password():
    bad = FakeSession(get_html=CSRF_PAGE,
                      post_text="<script>alert('UserName or Password Wrong!');</script>",
                      post_url="https://oj.test/login.php",
                      admin_html=POSTKEY_PAGE)
    assert hustoj.login(bad, "u", "p", "https://oj.test") is False


def test_login_without_import_rights():
    """账号密码对，但打不开导入页 → 登录判定为失败（并提示缺权限）。"""
    limited = FakeSession(get_html=CSRF_PAGE, post_text="ok", admin_html="<html>no rights</html>")
    assert hustoj.login(limited, "u", "p", "https://oj.test") is False


def test_login_verify_false_skips_admin_probe():
    s = FakeSession(get_html=CSRF_PAGE, post_text="ok", admin_html="<html>no rights</html>")
    assert hustoj.login(s, "u", "p", "https://oj.test", verify=False) is True
    assert not any("admin/problem_import" in u for u in s.gets)


# ─── upload.py CLI 侧 ────────────────────────────────────────────────────────

def _import_upload():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "upload_cli", Path(__file__).resolve().parents[1] / "upload.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_upload_cli_sanity_check(tmp_path):
    mod = _import_upload()
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("d/problem.yaml", "title: t\n")
        zf.writestr("d/testdata/1.in", "1\n")
    assert mod.sanity_check_zip(good, "hydro") == []

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("d/testdata/1.in", "1\n")
        zf.writestr("d/problem.yaml", "title: t\n")   # 数据在 yaml 之前 → HUSTOJ 会漏数据
    warns = mod.sanity_check_zip(bad, "hydro")
    assert any("problem.yaml 位于 testdata 之后" in w for w in warns)


def test_upload_cli_dry_run_needs_no_credentials(tmp_path, capsys):
    mod = _import_upload()
    problem = tmp_path / "p"
    problem.mkdir()
    (problem / "problem.md").write_text("# t\n\n## 题目描述\n\nx\n", encoding="utf-8")
    (problem / "inputs").mkdir()
    (problem / "outputs").mkdir()
    (problem / "inputs" / "01.in").write_text("1\n")
    (problem / "outputs" / "01.out").write_text("1\n")
    rc = mod.main([str(problem), "--dry-run", "--host", "https://oj.test",
                   "--user", "u", "--password", "p", "--dir-name", "d",
                   "--config", str(tmp_path / "none.toml")])
    out = capsys.readouterr().out
    assert rc == 0 and "dry-run" in out
    assert glob.glob(str(tmp_path / "p" / "export" / "hydrooj" / "d.zip"))


# ─── 新一代导入入口（admin/problem_import2.php）──────────────────────────────

def test_new_generation_endpoints_and_pages():
    """这台 OJ 同时装了两代导入器。新一代入口挂在 problem_import2.php 下，
    而 postkey 是**逐页面**写进 session 的 —— 用哪个入口就得去对应页面取。"""
    assert hustoj.IMPORT_ENDPOINTS["hydro2"] == "problem_import_hydro2.php"
    assert hustoj.IMPORT_ENDPOINTS["xml2"] == "problem_import_xml2.php"
    assert hustoj.import_page("hydro2") == "admin/problem_import2.php"
    assert hustoj.import_page("xml2") == "admin/problem_import2.php"
    assert hustoj.import_page("hydro") == "admin/problem_import.php"


def test_upload_to_new_generation_fetches_postkey_from_page2(sample_zip):
    s = FakeSession(get_html=POSTKEY_PAGE)
    hustoj.upload_problem_zip(s, str(sample_zip), kind="hydro2", url="https://oj.test")
    assert s.posts[0][0] == "https://oj.test/admin/problem_import_hydro2.php"
    assert any(u.endswith("/admin/problem_import2.php") for u in s.gets)
    assert not any(u.endswith("/admin/problem_import.php") for u in s.gets)
    assert s.posts[0][1]["data"] == {"postkey": "AB12CD34EF"}


IMPORT_LIST_PAGE = (
    "<form class='form-inline aj-up' action='problem_import_xml2.php' method=post>"
    "<input type=hidden name='postkey' value='AA11BB22CC'></form>"
    "<form class='form-inline aj-up' action='problem_import_hydro2.php' method=post></form>"
    "<form class='form-inline' action='problem_import_xml.php' method=post></form>"
)


def test_import_kinds_merges_both_generations():
    """两代页面各探一次，合并成 {kind: 脚本名}；用来把 404 变成清楚提示。"""
    s = FakeSession(get_html=IMPORT_LIST_PAGE)
    kinds = hustoj.import_kinds(s, "https://oj.test")
    assert kinds["hydro2"] == "problem_import_hydro2.php"
    assert kinds["xml2"] == "problem_import_xml2.php"
    assert kinds["xml"] == "problem_import_xml.php"
    assert len(s.gets) == 2
