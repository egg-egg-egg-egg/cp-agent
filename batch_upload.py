"""批量上传成品题到 HUSTOJ（支持断点续传）。

用法：
  python batch_upload.py --dry-run    # 只导出，不联网
  python batch_upload.py              # 真传（默认不启用）
  python batch_upload.py --enable     # 真传并启用

断点续传：uploaded_pids.txt 记录 name=pid，已传的会自动跳过。
凭据：OJ_USER / OJ_PASSWORD 从注册表 HKCU\\Environment 读取；host/kind 走 upload_config.toml。
"""
import os
import re
import subprocess
import sys
import pathlib
import winreg

EXCLUDE = {'ci_v7_smoke', 'example_sum', 'failed', 'smoke_wb', 'cspj_mock1'}
LOG = pathlib.Path('uploaded_pids.txt')


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


def load_done():
    d = {}
    if LOG.exists():
        for ln in LOG.read_text(encoding='utf-8').splitlines():
            if '=' in ln:
                name, pid = ln.split('=', 1)
                d[name.strip()] = pid.strip()
    return d


def save_done(done):
    with open(LOG, 'w', encoding='utf-8') as f:
        for name, pid in sorted(done.items()):
            f.write('%s=%s\n' % (name, pid))


def list_problems():
    result = []
    for d in sorted(pathlib.Path('problems').iterdir()):
        if not d.is_dir() or d.name in EXCLUDE:
            continue
        if not (d / 'problem.md').exists() or not (d / 'problem.yaml').exists():
            continue
        result.append(d)
    return result


def extract_pid(out):
    m = re.search(r'新题 pid\s*=\s*(\d+)', out)
    if m:
        return m.group(1)
    m = re.search(r'Problem ID (\d+) added', out)
    return m.group(1) if m else None


def main():
    argv = sys.argv[1:]
    dry = '--dry-run' in argv
    enable = '--enable' in argv

    user, pwd = get_oj_creds()
    env = dict(os.environ)
    if user:
        env['OJ_USER'] = user
    if pwd:
        env['OJ_PASSWORD'] = pwd

    done = load_done()
    problems = [d for d in list_problems() if d.name not in done]
    mode = 'dry-run' if dry else ('真传+启用' if enable else '真传')
    print('已传 %d 题，本次待%s %d 题' % (len(done), mode, len(problems)), flush=True)

    ok = fail = 0
    for i, d in enumerate(problems, 1):
        cmd = [sys.executable, 'upload.py', str(d)]
        if dry:
            cmd.append('--dry-run')
        elif enable:
            cmd.append('--enable')
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        pid = extract_pid(r.stdout) if not dry else None
        if r.returncode == 0:
            ok += 1
            if not dry:
                done[d.name] = pid or '?'
                save_done(done)
            print('  [%3d/%d] ✓ %-30s pid=%s' % (i, len(problems), d.name, pid or '-'),
                  flush=True)
        else:
            fail += 1
            print('  [%3d/%d] ✗ %-30s %s' % (i, len(problems), d.name,
                                             (r.stdout + r.stderr)[-120:]),
                  flush=True)

    print('\n结果：成功 %d / 失败 %d / 累计已传 %d' % (ok, fail, len(done)), flush=True)


if __name__ == '__main__':
    main()
