"""批量提交标程（solution.cpp）到 OJ。

过滤规则：单个测试点（.in）> 1MB 的题跳过（避免判题机卡顿）。
用法：python batch_submit.py [--dry-run]
"""
import sys
import time
import re
import zipfile
import pathlib
import winreg
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integrations import hustoj  # noqa: E402

MAX_CASE_BYTES = 300 * 1024  # 300KB（黄sir 定的提交阈值，更大的暂不提交）


def get_oj_creds():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
        vals = {}
        i = 0
        while True:
            try:
                n, v, _ = winreg.EnumValue(k, i)
                vals[n] = v
                i += 1
            except OSError:
                break
        return vals.get('OJ_USER'), vals.get('OJ_PASSWORD')
    except Exception:
        return None, None


def load_pids():
    up = {}
    for ln in pathlib.Path('uploaded_pids.txt').read_text(encoding='utf-8').splitlines():
        if '=' in ln:
            n, pid = ln.split('=', 1)
            up[n.strip()] = pid.strip()
    return up


def fio_base(problem_dir):
    """file_io 文件名（不含扩展名）。优先读 hydro 包 input.name，回退按 export 规则生成。"""
    d = problem_dir / 'export' / 'hydrooj'
    zips = sorted(d.glob('*.zip')) if d.exists() else []
    for z in reversed(zips):
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    if n.endswith('input.name'):
                        raw = zf.read(n).decode('utf-8', errors='replace').strip()
                        if raw.endswith('.in'):
                            return raw[:-3]
        except Exception:
            continue
    # 回退：按 export._ascii_name + 末尾数字加下划线 的规则
    nm = re.sub(r'[^0-9A-Za-z_]+', '_', problem_dir.name).strip('_').lower() or 'problem'
    if nm[-1].isdigit():
        nm += '_'
    return nm


def main():
    dry = '--dry-run' in sys.argv
    user, pwd = get_oj_creds()
    host = 'https://oj.ipachong.com'

    up = load_pids()
    print('共 %d 题，逐题检查测试点大小并提交标程%s' % (len(up), '（dry-run）' if dry else ''),
          flush=True)

    session = requests.Session()
    if not dry:
        if not hustoj.login(session, user, pwd, host, verify=True):
            print('✗ 登录失败，中止', flush=True)
            return 1
        print('登录成功', flush=True)

    ok = skip_big = skip_nosol = fail = 0
    big_list = []
    for name, pid in sorted(up.items()):
        d = pathlib.Path('problems') / name
        if not d.exists():
            continue
        ins = list((d / 'inputs').glob('*.in'))
        if ins:
            maxin = max(f.stat().st_size for f in ins)
        else:
            maxin = 0
        if maxin > MAX_CASE_BYTES:
            skip_big += 1
            big_list.append((name, pid, maxin / 1024 / 1024))
            continue
        sol = d / 'solution.cpp'
        if not sol.exists():
            skip_nosol += 1
            print('  [跳过] %-28s 无 solution.cpp' % name, flush=True)
            continue
        if dry:
            ok += 1
            continue
        source = sol.read_text(encoding='utf-8')
        # 题目是 file_io 模式：标程需 freopen 读 <fio>.in / 写 <fio>.out
        # （fio 文件名从 hydro 包 input.name 读，因「末尾数字才加下划线」导致不统一）
        base = fio_base(d)
        fopen = 'freopen("%s.in", "r", stdin);\n    freopen("%s.out", "w", stdout);' % (base, base)
        source = source.replace('int main() {', 'int main() {\n    ' + fopen, 1)
        token = hustoj.init_csrf_token(session, host)
        if not token:
            fail += 1
            print('  [失败] %-28s csrf 获取失败' % name, flush=True)
            continue
        try:
            resp = session.post(host + '/submit.php',
                                data={'id': pid, 'language': '1', 'source': source,
                                      'input_text': '', 'csrf': token},
                                headers=hustoj.HEADERS, timeout=60)
            ok_flag = resp.status_code == 200 and 'Please Login' not in resp.text
            if ok_flag:
                ok += 1
                print('  [%3d] ✓ %-26s pid=%s' % (ok, name, pid), flush=True)
            else:
                fail += 1
                print('  [失败] %-28s status=%s' % (name, resp.status_code), flush=True)
        except Exception as e:
            fail += 1
            print('  [异常] %-28s %s' % (name, repr(e)[:80]), flush=True)
        time.sleep(4)  # 放慢提交节奏，避免判题队列堆积卡 OJ

    print('\n=== 汇总 ===', flush=True)
    print('提交成功 %d / 跳过(超1MB) %d / 跳过(无标程) %d / 失败 %d' % (
        ok, skip_big, skip_nosol, fail), flush=True)
    print('\n超 1MB 未提交的题（%d）:' % len(big_list), flush=True)
    for name, pid, sz in sorted(big_list, key=lambda x: -x[2]):
        print('  %-28s pid=%s  %.2fMB' % (name, pid, sz), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
