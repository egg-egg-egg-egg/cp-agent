"""
Pipeline: sandboxed tool functions for the Agent.

All file operations are sandboxed to the problem directory.
Each tool returns a structured dict for LLM tool_result consumption.
"""
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import config

# ─── Sandbox helper ──────────────────────────────────────────────────────────

def _sandbox_resolve(problem_dir: Path, path: str) -> tuple[Path, str]:
    """
    Resolve a relative path inside the problem directory sandbox.
    Returns (resolved_path, error_string). error_string is "" on success.
    Rejects absolute paths, path traversal (..), and anything outside problem_dir
    (including escapes via symlinks).
    """
    # Reject absolute paths
    if os.path.isabs(path):
        return Path(""), f"拒绝：不允许绝对路径 '{path}'，必须使用相对路径"

    # Reject path traversal
    if ".." in path.split("/") or ".." in path.split("\\"):
        return Path(""), f"拒绝：不允许路径遍历 '..' in '{path}'"

    resolved = (problem_dir / path).resolve()
    try:
        resolved.relative_to(problem_dir.resolve())
    except ValueError:
        return Path(""), f"拒绝：路径 '{path}' 越界（沙盒限制在 {problem_dir} 内）"

    return resolved, ""


# ─── Low-level helpers ───────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], cwd: str = ".", timeout: int = 60,
             stdin_data: Optional[str] = None) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, input=stdin_data
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -2, "", f"Command not found: {cmd[0]}"


def _compile(src: Path, out: Path) -> tuple[bool, str]:
    """Compile a C++ file. Returns (success, message)."""
    cmd = [config.CXX, *config.CXX_FLAGS, f"-I{config.TESTLIB_PATH.resolve().parent}",
           str(src.resolve()), "-o", str(out.resolve())]
    code, stdout, stderr = _run_cmd(cmd, timeout=60)
    if code != 0:
        return False, stderr
    return True, f"Compiled {src.name} -> {out.name}"


def _list_dir(dir_path: Path) -> list[str]:
    """List files and dirs in a directory, non-recursive."""
    result = []
    if not dir_path.exists():
        return result
    for p in sorted(dir_path.iterdir()):
        name = p.name
        if p.is_dir():
            name += "/"
        result.append(name)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS — 每个 tool 返回 dict，适配 LLM tool_result
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 1. read_file ────────────────────────────────────────────────────────────

def tool_read_file(problem_dir: Path, path: str, max_lines: int = 500,
                   max_chars: int = 20000) -> dict:
    """读取文件内容。返回 {success, content, path}"""
    resolved, err = _sandbox_resolve(problem_dir, path)
    if err:
        return {"success": False, "message": err, "path": path}

    if not resolved.exists():
        return {"success": False, "message": f"文件不存在: {path}", "path": path}
    if not resolved.is_file():
        return {"success": False, "message": f"不是文件: {path}", "path": path}

    try:
        content = resolved.read_text(encoding="utf-8")
        lines = content.split("\n")
        original_chars = len(content)
        if len(lines) > max_lines:
            content = "\n".join(lines[:max_lines]) + f"\n... (截断，共 {len(lines)} 行)"
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... (截断，共 {original_chars} 字符)"
        return {"success": True, "content": content, "path": path, "lines": len(lines)}
    except Exception as e:
        return {"success": False, "message": f"读取失败: {e}", "path": path}


# ─── 2. write_file ───────────────────────────────────────────────────────────

def tool_write_file(problem_dir: Path, path: str, content: str) -> dict:
    """创建或覆写文件。返回 {success, message, path}"""
    resolved, err = _sandbox_resolve(problem_dir, path)
    if err:
        return {"success": False, "message": err, "path": path}

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return {"success": True, "message": f"已写入 {path} ({lines} 行, {len(content)} 字节)", "path": path}
    except Exception as e:
        return {"success": False, "message": f"写入失败: {e}", "path": path}


# ─── 3. edit_file ────────────────────────────────────────────────────────────

def tool_edit_file(problem_dir: Path, path: str,
                   old_text: str, new_text: str) -> dict:
    """搜索替换修改文件。返回 {success, message, path, replacements}"""
    resolved, err = _sandbox_resolve(problem_dir, path)
    if err:
        return {"success": False, "message": err, "path": path}

    if not resolved.exists():
        return {"success": False, "message": f"文件不存在: {path}", "path": path}

    try:
        content = resolved.read_text(encoding="utf-8")

        if old_text not in content:
            return {
                "success": False,
                "message": "未找到匹配文本。请检查 old_text 是否与文件内容完全一致（包括缩进和换行）",
                "path": path,
            }

        count = content.count(old_text)
        new_content = content.replace(old_text, new_text)
        resolved.write_text(new_content, encoding="utf-8")

        return {
            "success": True,
            "message": f"已修改 {path}，替换了 {count} 处",
            "path": path,
            "replacements": count,
        }
    except Exception as e:
        return {"success": False, "message": f"修改失败: {e}", "path": path}


# ─── 4. list_files ───────────────────────────────────────────────────────────

def tool_list_files(problem_dir: Path, dir: str = ".") -> dict:
    """列出目录内容。返回 {success, files, path}"""
    resolved, err = _sandbox_resolve(problem_dir, dir)
    if err:
        return {"success": False, "message": err, "path": dir}

    if not resolved.exists():
        return {"success": False, "message": f"目录不存在: {dir}", "path": dir}
    if not resolved.is_dir():
        return {"success": False, "message": f"不是目录: {dir}", "path": dir}

    files = _list_dir(resolved)
    return {"success": True, "files": files, "path": dir, "count": len(files)}


# ─── 5. compile_cpp ──────────────────────────────────────────────────────────

def tool_compile_cpp(problem_dir: Path, source: str, output: str) -> dict:
    """编译 C++ 文件。返回 {success, message, source, output}"""
    src_resolved, err = _sandbox_resolve(problem_dir, source)
    if err:
        return {"success": False, "message": err}

    out_resolved, err = _sandbox_resolve(problem_dir, output)
    if err:
        return {"success": False, "message": err}

    if not src_resolved.exists():
        return {"success": False, "message": f"源文件不存在: {source}", "source": source}

    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = _compile(src_resolved, out_resolved)

    return {
        "success": ok,
        "message": msg,
        "source": source,
        "output": output,
    }


# ─── 6. generate_test_data ───────────────────────────────────────────────────

def tool_generate_test_data(problem_dir: Path, count: int = 30) -> dict:
    """运行 generator 生成测试数据。返回 {success, message, files_created}"""
    gen_bin = problem_dir.resolve() / "bin" / "generator"
    if not gen_bin.exists():
        return {"success": False, "message": "generator 未编译，请先 compile_cpp generator.cpp -> bin/generator"}

    inputs_dir = problem_dir.resolve() / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧数据
    for f in inputs_dir.glob("*.in"):
        f.unlink()

    created = []
    errors = []
    for i in range(1, count + 1):
        cmd = [str(gen_bin), str(i), str(count)]
        code, stdout, stderr = _run_cmd(cmd, cwd=str(problem_dir.resolve()), timeout=30)
        if code != 0:
            errors.append(f"test {i}: {stderr[:200]}")
            if len(errors) >= 3:
                break
            continue
        fpath = inputs_dir / f"{i:02d}.in"
        fpath.write_text(stdout)
        created.append(f"{i:02d}.in")

    if errors:
        return {
            "success": False,
            "message": f"生成了 {len(created)} 个，失败 {len(errors)} 个",
            "files_created": created,
            "errors": errors,
        }

    return {
        "success": True,
        "message": f"已生成 {len(created)} 个测试数据",
        "files_created": created,
    }


# ─── 7. validate_inputs ─────────────────────────────────────────────────────

_BOUNDS_LINE_RE = None  # compiled lazily


def _parse_bounds_log(text: str) -> dict:
    """Parse testlib --testOverviewLogFileName bounds lines: '"n": min-value-hit max-value-hit'."""
    import re
    global _BOUNDS_LINE_RE
    if _BOUNDS_LINE_RE is None:
        _BOUNDS_LINE_RE = re.compile(r'^"(.*)":((?:\s+(?:min|max)-value-hit)*)\s*$')
    result = {}
    for line in text.splitlines():
        m = _BOUNDS_LINE_RE.match(line)
        if not m:
            continue
        result[m.group(1)] = {
            "min_hit": "min-value-hit" in m.group(2),
            "max_hit": "max-value-hit" in m.group(2),
        }
    return result


def tool_validate_inputs(problem_dir: Path) -> dict:
    """
    运行 validator 校验所有输入。兼容两种 validator 约定：
    - registerValidation（推荐）: stdin 传入，并用 --testOverviewLogFileName
      收集每个变量的边界触达情况，跨测试点合并输出 bounds_report/bounds_unhit
    - registerGen + inf.init（旧式）: argv[1] 传文件路径，无边界报告
    返回 {success, message, validated_count, errors, bounds_report, bounds_unhit}
    """
    base = problem_dir.resolve()
    val_bin = base / "bin" / "validator"
    if not val_bin.exists():
        return {"success": False, "message": "validator 未编译，请先 compile_cpp validator.cpp -> bin/validator"}

    inputs_dir = base / "inputs"
    if not inputs_dir.exists():
        return {"success": False, "message": "inputs/ 目录不存在，请先 generate_test_data"}

    inputs = sorted(inputs_dir.glob("*.in"))
    if not inputs:
        return {"success": False, "message": "inputs/ 目录为空，请先 generate_test_data"}

    val_src = base / "validator.cpp"
    new_style = val_src.exists() and "registerValidation" in val_src.read_text(
        encoding="utf-8", errors="replace")

    bounds: dict = {}
    errors = []
    count = 0
    for f in inputs:
        if new_style:
            log_file = base / ".judge" / "overview.log"
            log_file.parent.mkdir(exist_ok=True)
            code, stdout, stderr = _run_cmd(
                [str(val_bin), f"--testOverviewLogFileName={log_file}"],
                cwd=str(base), stdin_data=f.read_text(), timeout=10
            )
        else:
            code, stdout, stderr = _run_cmd([str(val_bin), str(f)], cwd=str(base), timeout=10)

        if code != 0:
            errors.append(f"{f.name}: {stderr[:300]}")
            if len(errors) >= 5:
                break
            continue

        count += 1
        if new_style and log_file.exists():
            for var, hits in _parse_bounds_log(log_file.read_text(encoding="utf-8", errors="replace")).items():
                agg = bounds.setdefault(var, {"min_hit": False, "max_hit": False})
                agg["min_hit"] = agg["min_hit"] or hits["min_hit"]
                agg["max_hit"] = agg["max_hit"] or hits["max_hit"]

    if errors:
        return {
            "success": False,
            "message": f"校验了 {count} 个，失败 {len(errors)} 个",
            "validated_count": count,
            "errors": errors,
        }

    bounds_unhit = sorted(
        f"{var}.{side}"
        for var, hits in bounds.items()
        for side, hit in (("min", hits["min_hit"]), ("max", hits["max_hit"]))
        if not hit
    )
    message = f"全部 {count} 个输入校验通过"
    if bounds_unhit:
        message += f"；警告：以下约束边界未被任何测试点触达: {', '.join(bounds_unhit)}"
    elif new_style:
        message += "；所有变量的 min/max 边界均被触达"

    return {
        "success": True,
        "message": message,
        "validated_count": count,
        "bounds_report": bounds if new_style else None,
        "bounds_unhit": bounds_unhit if new_style else None,
    }


# ─── 8. run_solution ─────────────────────────────────────────────────────────

def tool_run_solution(problem_dir: Path, timeout_sec: Optional[int] = None) -> dict:
    """运行 solution 为每个输入生成输出。返回 {success, message, files_created, timeout_files}"""
    timeout_sec = timeout_sec or config.DEFAULT_SOLUTION_TIMEOUT_SEC
    sol_bin = problem_dir.resolve() / "bin" / "solution"
    if not sol_bin.exists():
        return {"success": False, "message": "solution 未编译，请先 compile_cpp solution.cpp -> bin/solution"}

    inputs_dir = problem_dir.resolve() / "inputs"
    outputs_dir = problem_dir.resolve() / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    inputs = sorted(inputs_dir.glob("*.in"))
    if not inputs:
        return {"success": False, "message": "inputs/ 为空，请先 generate_test_data"}

    created = []
    errors = []
    timeout_files = []
    for f in inputs:
        inp = f.read_text()
        code, stdout, stderr = _run_cmd(
            [str(sol_bin)], cwd=str(problem_dir.resolve()),
            stdin_data=inp, timeout=timeout_sec
        )
        if code == -1:  # TIMEOUT
            timeout_files.append(f.name)
            errors.append(f"{f.name}: 超时（>{timeout_sec}s）— 标程复杂度可能有误")
            if len(timeout_files) >= 3:
                break
            continue
        if code != 0:
            errors.append(f"{f.name}: {stderr[:200]}")
            if len(errors) >= 3:
                break
            continue
        out_file = outputs_dir / f.with_suffix(".out").name
        out_file.write_text(stdout)
        created.append(out_file.name)

    if timeout_files:
        return {
            "success": False,
            "message": f"超时 {len(timeout_files)} 个（{', '.join(timeout_files[:5])}）— 标程复杂度过高，需要优化算法",
            "files_created": created,
            "errors": errors,
            "timeout": True,
            "timeout_files": timeout_files,
        }

    if errors:
        return {
            "success": False,
            "message": f"生成了 {len(created)} 个输出，失败 {len(errors)} 个",
            "files_created": created,
            "errors": errors,
        }

    return {
        "success": True,
        "message": f"已为 {len(created)} 个输入生成输出",
        "files_created": created,
    }


# ─── 9. stress_test ──────────────────────────────────────────────────────────

def _token_compare(a: str, b: str) -> bool:
    """Whitespace-insensitive token comparison (testlib wcmp semantics)."""
    return a.split() == b.split()


_CHECKER_VERDICTS = {0: "AC", 1: "WA", 2: "PE", 3: "FAIL"}


def _run_checker(problem_dir: Path, in_text: str, out_text: str,
                 ans_text: str) -> tuple[str, str]:
    """
    Run bin/checker (testlib convention: checker <input> <output> <answer>).
    Returns (verdict, message) where verdict ∈ AC/WA/PE/FAIL/ERROR.
    """
    base = problem_dir.resolve()
    checker_bin = base / "bin" / "checker"
    judge_dir = base / ".judge"
    judge_dir.mkdir(exist_ok=True)
    in_f, out_f, ans_f = judge_dir / "in.txt", judge_dir / "out.txt", judge_dir / "ans.txt"
    in_f.write_text(in_text)
    out_f.write_text(out_text)
    ans_f.write_text(ans_text)

    code, _, stderr = _run_cmd(
        [str(checker_bin), str(in_f), str(out_f), str(ans_f)],
        cwd=str(base), timeout=10
    )
    if code == -1:
        return "ERROR", "checker 超时"
    verdict = _CHECKER_VERDICTS.get(code, "ERROR")
    return verdict, stderr.strip()[:300]


def tool_stress_test(problem_dir: Path, count: int = 1000) -> dict:
    """对拍 solution vs naive。返回 {success, message, iterations, mismatches}"""
    import time as _time

    sol_bin = problem_dir.resolve() / "bin" / "solution"
    naive_bin = problem_dir.resolve() / "bin" / "naive"
    gen_bin = problem_dir.resolve() / "bin" / "generator"

    missing = []
    if not sol_bin.exists():
        missing.append("solution")
    if not naive_bin.exists():
        missing.append("naive")
    if not gen_bin.exists():
        missing.append("generator")
    if missing:
        return {"success": False, "message": f"缺少编译产物: {', '.join(missing)}，请先 compile_cpp"}

    sol_timeout = config.DEFAULT_STRESS_TIMEOUT_SEC
    naive_timeout = config.DEFAULT_STRESS_NAIVE_TIMEOUT_SEC
    naive_soft_limit = config.DEFAULT_STRESS_NAIVE_SOFT_LIMIT_SEC
    checker_bin = problem_dir.resolve() / "bin" / "checker"
    use_checker = checker_bin.exists()
    mismatches = []
    completed = 0
    for i in range(1, count + 1):
        # 生成随机输入（argv[3]="stress" 提示 generator 使用对拍模式的小规模数据）
        code, inp, _ = _run_cmd(
            [str(gen_bin), str(i), str(count), "stress"],
            cwd=str(problem_dir.resolve()), timeout=10
        )
        if code != 0:
            continue

        # 运行 solution（超时说明标程有误）
        sol_code, sol_out, sol_err = _run_cmd(
            [str(sol_bin)], cwd=str(problem_dir.resolve()),
            stdin_data=inp, timeout=sol_timeout
        )
        if sol_code == -1:  # TIMEOUT
            return {
                "success": False,
                "message": f"对拍失败：第 {i} 轮标程超时（>{sol_timeout}s）— 标程复杂度有误，需要优化算法",
                "iterations": i,
                "mismatches": [],
                "timeout": True,
            }
        if sol_code != 0:
            return {
                "success": False,
                "message": f"对拍失败：第 {i} 轮标程运行出错 (exit={sol_code}): {sol_err[:200]}",
                "iterations": i,
                "mismatches": [],
                "input": inp[:500],
            }

        # 运行 naive（计时）
        t_naive = _time.time()
        naive_code, naive_out, naive_err = _run_cmd(
            [str(naive_bin)], cwd=str(problem_dir.resolve()),
            stdin_data=inp, timeout=naive_timeout
        )
        naive_elapsed = _time.time() - t_naive

        if naive_code == -1 or naive_elapsed > naive_soft_limit:
            return {
                "success": False,
                "message": (
                    f"对拍未完成：第 {i} 轮 naive 耗时 {naive_elapsed:.1f}s 超过 {naive_soft_limit}s 上限，"
                    f"仅完成 {completed}/{count} 轮。对拍数据规模过大——请修改 generator.cpp，"
                    "在 argc > 3 && argv[3] == \"stress\" 时生成小规模数据（如 n ≤ 500），"
                    "然后重新编译 generator 并重跑 stress_test"
                ),
                "iterations": i,
                "mismatches": [],
                "naive_timeout": True,
            }
        if naive_code != 0:
            return {
                "success": False,
                "message": f"对拍失败：第 {i} 轮 naive 运行出错 (exit={naive_code}): {naive_err[:200]}",
                "iterations": i,
                "mismatches": [],
                "input": inp[:500],
            }

        completed = i
        if use_checker:
            # SPJ：naive 输出作为 jury answer，checker 依据 input 判 solution 输出合法性
            verdict, checker_msg = _run_checker(problem_dir, inp, sol_out, naive_out)
            if verdict in ("FAIL", "ERROR"):
                return {
                    "success": False,
                    "message": f"对拍失败：第 {i} 轮 checker 自身出错 ({verdict}): {checker_msg} — 请检查 checker.cpp",
                    "iterations": i,
                    "mismatches": [],
                    "checker_used": True,
                }
            matched = verdict == "AC"
        else:
            checker_msg = ""
            matched = _token_compare(sol_out, naive_out)

        if not matched:
            mismatches.append({
                "iteration": i,
                "input": inp[:500],
                "solution_output": sol_out.strip()[:200],
                "naive_output": naive_out.strip()[:200],
                **({"checker_verdict": verdict, "checker_message": checker_msg} if use_checker else {}),
            })
            if len(mismatches) >= 3:
                break

    if mismatches:
        return {
            "success": False,
            "message": f"对拍失败：{len(mismatches)} 个不匹配（跑了 {completed} 轮）",
            "iterations": completed,
            "mismatches": mismatches,
            "checker_used": use_checker,
        }

    return {
        "success": True,
        "message": f"对拍通过：{count} 轮全部一致" + ("（checker 判定）" if use_checker else ""),
        "iterations": count,
        "mismatches": [],
        "checker_used": use_checker,
    }


# ─── 10. write_metadata ──────────────────────────────────────────────────────

def tool_write_metadata(problem_dir: Path, title: str, algorithm_tags: list,
                        difficulty: Optional[int] = None,
                        time_limit_ms: Optional[int] = None,
                        memory_limit_mb: Optional[int] = None,
                        subtasks: Optional[list] = None,
                        provider: str = "") -> dict:
    """
    写入 problem.yaml：LLM 提供语义字段（标题/标签/难度/限制），
    机械字段（slug、checker 类型、cases 列表）由代码扫描目录生成。
    """
    from datetime import date

    import yaml

    base = problem_dir.resolve()
    if not title or not str(title).strip():
        return {"success": False, "message": "title 不能为空"}
    if not algorithm_tags or not isinstance(algorithm_tags, list):
        return {"success": False, "message": "algorithm_tags 必须是非空列表"}

    inputs = sorted((base / "inputs").glob("*.in")) if (base / "inputs").exists() else []
    if not inputs:
        return {"success": False, "message": "inputs/ 为空，请先 generate_test_data 并 run_solution"}
    cases = []
    missing = []
    for f in inputs:
        out = base / "outputs" / (f.stem + ".out")
        if not out.exists():
            missing.append(out.name)
            continue
        cases.append({"input": f"inputs/{f.name}", "output": f"outputs/{out.name}"})
    if missing:
        return {"success": False,
                "message": f"缺少输出文件: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}，请先 run_solution"}

    has_checker = (base / "checker.cpp").exists() or (base / "bin" / "checker").exists()
    checker_block = {"type": "testlib", "source": "checker.cpp"} if has_checker else {"type": "builtin"}

    # 保留已有 problem.yaml 中 final_check 回填的 validation 块
    existing_validation = None
    yaml_path = base / "problem.yaml"
    if yaml_path.exists():
        try:
            existing = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            existing_validation = existing.get("validation")
        except yaml.YAMLError:
            pass

    meta = {
        "version": 1,
        "title": str(title).strip(),
        "slug": base.name,
        "algorithm_tags": [str(t) for t in algorithm_tags],
        "difficulty": int(difficulty) if difficulty else None,
        "time_limit_ms": int(time_limit_ms or config.DEFAULT_TIME_LIMIT_MS),
        "memory_limit_mb": int(memory_limit_mb or config.DEFAULT_MEMORY_LIMIT_MB),
        "checker": checker_block,
        "cases": cases,
        "subtasks": subtasks or [],
        "generated": {"by": "cp-agent", "date": date.today().isoformat(),
                      **({"provider": provider} if provider else {})},
    }
    if existing_validation:
        meta["validation"] = existing_validation

    yaml_path.write_text(
        yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return {
        "success": True,
        "message": f"已写入 problem.yaml（{len(cases)} 个测试点，checker={checker_block['type']}）",
        "cases": len(cases),
        "checker_type": checker_block["type"],
    }


def _load_problem_yaml(problem_dir: Path) -> dict | None:
    import yaml
    path = problem_dir.resolve() / "problem.yaml"
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None


# ─── 11. check_data_strength ─────────────────────────────────────────────────

def tool_check_data_strength(problem_dir: Path, top_n: int = 3) -> dict:
    """
    数据强度检查：在体积最大的 top_n 个测试点上，solution 必须在时限内通过，
    而 naive 必须至少在一个测试点上超时（或耗时 ≥ 5× solution）。
    注意：与 stress_test 相反，这里 naive 超时是【好事】——说明数据能区分
    正解与暴力；全部大点 naive 轻松通过说明数据太弱。
    """
    import time as _time

    base = problem_dir.resolve()
    sol_bin = base / "bin" / "solution"
    naive_bin = base / "bin" / "naive"
    if not sol_bin.exists() or not naive_bin.exists():
        return {"success": False, "message": "需要先编译 bin/solution 和 bin/naive"}

    meta = _load_problem_yaml(problem_dir)
    tl_ms = (meta or {}).get("time_limit_ms") or config.DEFAULT_TIME_LIMIT_MS
    tl_sec = tl_ms / 1000.0

    inputs = sorted((base / "inputs").glob("*.in"),
                    key=lambda f: f.stat().st_size, reverse=True)[:top_n]
    if not inputs:
        return {"success": False, "message": "inputs/ 为空，请先 generate_test_data"}

    details = []
    naive_tle_on = []
    strong = False
    for f in inputs:
        inp = f.read_text()

        t0 = _time.time()
        sol_code, _, _ = _run_cmd([str(sol_bin)], cwd=str(base), stdin_data=inp,
                                  timeout=max(int(tl_sec * 2) + 1, 2))
        sol_elapsed = _time.time() - t0
        if sol_code == -1 or sol_elapsed > tl_sec:
            return {
                "success": False,
                "message": f"标程在最大数据 {f.name} 上耗时 {sol_elapsed:.2f}s，超过时限 {tl_ms}ms —— 标程复杂度或时限设置有误",
                "solution_timeout": True,
                "file": f.name,
            }

        t0 = _time.time()
        naive_code, _, _ = _run_cmd([str(naive_bin)], cwd=str(base), stdin_data=inp,
                                    timeout=max(int(tl_sec * 3) + 1, 3))
        naive_elapsed = _time.time() - t0
        naive_tle = naive_code == -1 or naive_elapsed > tl_sec

        details.append({
            "file": f.name,
            "size_bytes": f.stat().st_size,
            "solution_sec": round(sol_elapsed, 3),
            "naive_sec": round(naive_elapsed, 3),
            "naive_tle": naive_tle,
        })
        if naive_tle:
            naive_tle_on.append(f.name)
            strong = True
        elif sol_elapsed > 0 and naive_elapsed >= 5 * sol_elapsed:
            strong = True

    if not strong:
        return {
            "success": False,
            "message": (
                f"数据太弱：最大的 {len(inputs)} 个测试点上 naive 全部在时限（{tl_ms}ms）内通过"
                "且不明显慢于标程。请增大 generator 的最大规模测试点（非对拍模式），使暴力解无法通过，"
                "然后重新 generate_test_data、validate_inputs、run_solution 并重跑本检查"
            ),
            "details": details,
        }

    return {
        "success": True,
        "message": f"数据强度检查通过：naive 在 {', '.join(naive_tle_on) or '大数据上明显慢于标程'} 上超时/显著慢于标程",
        "naive_tle_on": naive_tle_on,
        "details": details,
    }


# ─── 12. final_check ─────────────────────────────────────────────────────────

def _extract_samples(problem_md: str) -> list[tuple[str, str]]:
    """
    Extract (sample_input, sample_output) pairs from problem.md.
    Matches headings containing 样例/示例 + 输入|输出 followed by a code fence.
    """
    import re
    blocks = re.findall(
        r"^#{2,4}[^\n]*?(输入|输出)[^\n]*\n+```[^\n]*\n(.*?)```",
        problem_md, re.MULTILINE | re.DOTALL,
    )
    samples = []
    pending_input = None
    for kind, body in blocks:
        if kind == "输入":
            pending_input = body
        elif kind == "输出" and pending_input is not None:
            samples.append((pending_input, body))
            pending_input = None
    return samples


def tool_final_check(problem_dir: Path, waive_bounds: Optional[list] = None) -> dict:
    """
    出题完成前的最终确定性检查：
    1. 必需文件齐全（problem.md/solution.cpp/generator.cpp/validator.cpp/naive.cpp/problem.yaml；SPJ 时含 checker）
    2. inputs 与 outputs 一一配对，且与 problem.yaml cases 一致
    3. 题面样例：样例输入必须通过 validator 且 solution 的输出与样例输出一致（SPJ 用 checker 判）
    4. 边界覆盖：validator 报告的未触达边界必须为空（可用 waive_bounds 显式豁免并说明理由）
    5. 数据强度：check_data_strength 必须通过
    全部通过后把 validation 结果回填 problem.yaml。
    """
    import yaml

    base = problem_dir.resolve()
    problems = []

    # 1. required files
    required = ["problem.md", "solution.cpp", "generator.cpp", "validator.cpp", "naive.cpp", "problem.yaml"]
    meta = _load_problem_yaml(problem_dir)
    if meta is None:
        problems.append("problem.yaml 缺失或不合法，请先调用 write_metadata")
        meta = {}
    is_spj = (meta.get("checker") or {}).get("type") == "testlib"
    if is_spj:
        required.append("checker.cpp")
    for name in required:
        if not (base / name).exists():
            problems.append(f"缺少文件 {name}")
    if is_spj and not (base / "bin" / "checker").exists():
        problems.append("checker.cpp 未编译为 bin/checker")

    # 2. inputs/outputs pairing vs problem.yaml cases
    input_stems = {f.stem for f in (base / "inputs").glob("*.in")} if (base / "inputs").exists() else set()
    output_stems = {f.stem for f in (base / "outputs").glob("*.out")} if (base / "outputs").exists() else set()
    if not input_stems:
        problems.append("inputs/ 为空")
    elif input_stems != output_stems:
        problems.append(f"inputs 与 outputs 不配对（{len(input_stems)} in / {len(output_stems)} out）")
    cases = meta.get("cases") or []
    if cases and len(cases) != len(input_stems):
        problems.append(f"problem.yaml cases 数量（{len(cases)}）与 inputs（{len(input_stems)}）不一致，请重新 write_metadata")

    # 3. samples consistency
    sample_result = {"count": 0, "passed": 0}
    md_path = base / "problem.md"
    sol_bin = base / "bin" / "solution"
    val_bin = base / "bin" / "validator"
    if md_path.exists() and sol_bin.exists():
        samples = _extract_samples(md_path.read_text(encoding="utf-8", errors="replace"))
        sample_result["count"] = len(samples)
        if not samples:
            problems.append("problem.md 中未找到可解析的样例（需要 '## 样例输入/样例输出' + 代码块格式）")
        for idx, (sin, sout) in enumerate(samples, 1):
            if not sin.endswith("\n"):
                sin += "\n"
            # validator on sample
            val_src = base / "validator.cpp"
            new_style = val_src.exists() and "registerValidation" in val_src.read_text(
                encoding="utf-8", errors="replace")
            if val_bin.exists():
                if new_style:
                    vcode, _, vstderr = _run_cmd([str(val_bin)], cwd=str(base), stdin_data=sin, timeout=10)
                else:
                    tmp = base / ".judge" / f"sample{idx}.in"
                    tmp.parent.mkdir(exist_ok=True)
                    tmp.write_text(sin)
                    vcode, _, vstderr = _run_cmd([str(val_bin), str(tmp)], cwd=str(base), timeout=10)
                if vcode != 0:
                    problems.append(f"样例 {idx} 未通过 validator: {vstderr[:150]}")
                    continue
            scode, s_out, s_err = _run_cmd([str(sol_bin)], cwd=str(base), stdin_data=sin, timeout=10)
            if scode != 0:
                problems.append(f"样例 {idx}: solution 运行失败 (exit={scode}): {s_err[:150]}")
                continue
            if (base / "bin" / "checker").exists():
                verdict, cmsg = _run_checker(problem_dir, sin, s_out, sout)
                ok = verdict == "AC"
                if not ok:
                    problems.append(f"样例 {idx}: checker 判 {verdict}: {cmsg[:150]}")
            else:
                ok = _token_compare(s_out, sout)
                if not ok:
                    problems.append(
                        f"样例 {idx}: solution 输出与题面样例输出不一致 "
                        f"(solution: {s_out.strip()[:80]!r} vs 题面: {sout.strip()[:80]!r})"
                    )
            if ok:
                sample_result["passed"] += 1

    # 4. bounds coverage
    waived = set(waive_bounds or [])
    validate_result = tool_validate_inputs(problem_dir)
    bounds_unhit = []
    if not validate_result.get("success"):
        problems.append(f"validate_inputs 未通过: {validate_result.get('message')}")
    else:
        bounds_unhit = [b for b in (validate_result.get("bounds_unhit") or []) if b not in waived]
        if bounds_unhit:
            problems.append(
                f"约束边界未触达: {', '.join(bounds_unhit)} —— 请在 generator 中加入命中边界的测试点，"
                "或用 waive_bounds 参数显式豁免（需说明理由）"
            )

    # 5. data strength
    strength = tool_check_data_strength(problem_dir)
    if not strength.get("success"):
        problems.append(f"数据强度检查未通过: {strength.get('message')}")

    if problems:
        return {
            "success": False,
            "message": f"final_check 未通过（{len(problems)} 项）",
            "problems": problems,
            "samples": sample_result,
        }

    # backfill validation block into problem.yaml
    if meta:
        meta["validation"] = {
            "validator_passed": True,
            "bounds_unhit": [],
            "waived_bounds": sorted(waived),
            "samples": sample_result,
            "data_strength": {"passed": True, "naive_tle_on": strength.get("naive_tle_on", [])},
        }
        (base / "problem.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")

    return {
        "success": True,
        "message": f"final_check 全部通过（样例 {sample_result['passed']}/{sample_result['count']}，"
                   f"测试点 {len(input_stems)} 组，数据强度 OK）",
        "samples": sample_result,
    }


# ─── 13. web_search (stub — 由 agent.py 实现) ───────────────────────────────
# web_search 不在此文件实现，因为它不需要沙盒，由 agent.py 直接处理。

# ═══════════════════════════════════════════════════════════════════════════════
# Tool dispatcher — 根据 tool name 调用对应函数
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_DISPATCHER = {
    "read_file":           lambda pd, args: tool_read_file(pd, args["path"]),
    "write_file":          lambda pd, args: tool_write_file(pd, args["path"], args["content"]),
    "edit_file":           lambda pd, args: tool_edit_file(pd, args["path"], args["old_text"], args["new_text"]),
    "list_files":          lambda pd, args: tool_list_files(pd, args.get("dir", ".")),
    "compile_cpp":         lambda pd, args: tool_compile_cpp(pd, args["source"], args["output"]),
    "generate_test_data":  lambda pd, args: tool_generate_test_data(pd, args.get("count", 30)),
    "validate_inputs":     lambda pd, args: tool_validate_inputs(pd),
    "run_solution":        lambda pd, args: tool_run_solution(pd, timeout_sec=args.get("timeout_sec")),
    "stress_test":         lambda pd, args: tool_stress_test(pd, args.get("count", 1000)),
    "write_metadata":      lambda pd, args: tool_write_metadata(
                               pd, args.get("title", ""), args.get("algorithm_tags", []),
                               difficulty=args.get("difficulty"),
                               time_limit_ms=args.get("time_limit_ms"),
                               memory_limit_mb=args.get("memory_limit_mb"),
                               subtasks=args.get("subtasks"),
                               provider=args.get("provider", "")),
    "check_data_strength": lambda pd, args: tool_check_data_strength(pd),
    "final_check":         lambda pd, args: tool_final_check(pd, waive_bounds=args.get("waive_bounds")),
}


def execute_tool(problem_dir: Path, tool_name: str, args: dict) -> dict:
    """Execute a tool by name. Returns structured dict for LLM tool_result."""
    if tool_name not in TOOL_DISPATCHER:
        return {"success": False, "message": f"未知工具: {tool_name}"}
    try:
        return TOOL_DISPATCHER[tool_name](problem_dir, args)
    except Exception as e:
        return {"success": False, "message": f"工具执行异常: {e}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline class — 向后兼容，内部调用 tool 函数
# ═══════════════════════════════════════════════════════════════════════════════

class Pipeline:
    """Legacy pipeline for --pipeline mode. Wraps tool functions."""

    def __init__(self, problem_dir: Path):
        self.d = problem_dir.resolve()
        self.errors: list[str] = []

    def run_full(self, test_count: int = 20,
                 stress_iterations: Optional[int] = None,
                 skip_stress: bool = False) -> dict:
        if stress_iterations is None:
            stress_iterations = config.DEFAULT_STRESS_ITERATIONS
        self.errors = []
        start = time.time()
        results = {}

        print(f"\n{'='*60}")
        print(f"Pipeline: {self.d.name}")
        print(f"{'='*60}")

        steps = [
            ("compile", lambda: self._step_compile(naive_required=not skip_stress)),
            ("generate", lambda: self._step_generate(test_count)),
            ("validate", self._step_validate),
            ("solve", self._step_solve),
        ]
        if not skip_stress:
            steps.append(("stress_test", lambda: self._step_stress(stress_iterations)))
        # 质量检查步骤：需要 problem.yaml（write_metadata 产物），存量题目缺失时跳过并警告
        if (self.d / "problem.yaml").exists():
            steps.append(("data_strength", self._step_data_strength))
            steps.append(("final_check", self._step_final_check))
        else:
            print("  ⚠ 无 problem.yaml，跳过 data_strength/final_check（可先用 write_metadata 生成）")

        for idx, (name, fn) in enumerate(steps, 1):
            print(f"\n[{idx}/{len(steps)}] {name}...")
            ok = fn()
            results[name] = ok
            if not ok:
                break

        elapsed = time.time() - start
        summary = {
            "problem": self.d.name,
            "results": results,
            "errors": self.errors,
            "elapsed_sec": round(elapsed, 2),
            "all_passed": all(results.values()),
        }
        status = "✅ ALL PASSED" if summary["all_passed"] else "❌ FAILED"
        print(f"\n{'='*60}")
        print(f"Pipeline finished: {status} ({elapsed:.1f}s)")
        if self.errors:
            print(f"Errors ({len(self.errors)}):")
            for e in self.errors[:10]:
                print(f"  - {e}")
        print(f"{'='*60}\n")
        return summary

    def _step_compile(self, naive_required: bool = True) -> bool:
        ok = True
        for src_name, out_name in [
            ("generator.cpp", "bin/generator"),
            ("validator.cpp", "bin/validator"),
            ("solution.cpp", "bin/solution"),
            ("naive.cpp", "bin/naive"),
        ]:
            src = self.d / src_name
            if not src.exists():
                if src_name == "naive.cpp" and not naive_required:
                    continue
                self.errors.append(f"[COMPILE] {src_name}: 源文件不存在")
                ok = False
                continue
            r = tool_compile_cpp(self.d, src_name, out_name)
            if not r["success"]:
                self.errors.append(f"[COMPILE] {src_name}: {r['message']}")
                ok = False
            else:
                print(f"  ✓ {r['message']}")
        return ok

    def _step_generate(self, count: int) -> bool:
        r = tool_generate_test_data(self.d, count)
        if not r["success"]:
            self.errors.append(f"[GENERATE] {r['message']}")
            return False
        print(f"  ✓ {r['message']}")
        return True

    def _step_validate(self) -> bool:
        r = tool_validate_inputs(self.d)
        if not r["success"]:
            self.errors.append(f"[VALIDATE] {r['message']}")
            if r.get("errors"):
                self.errors.extend(r["errors"][:3])
            return False
        print(f"  ✓ {r['message']}")
        return True

    def _step_solve(self) -> bool:
        r = tool_run_solution(self.d)
        if not r["success"]:
            self.errors.append(f"[SOLVE] {r['message']}")
            return False
        print(f"  ✓ {r['message']}")
        return True

    def _step_stress(self, iterations: int) -> bool:
        r = tool_stress_test(self.d, iterations)
        if not r["success"]:
            self.errors.append(f"[STRESS] {r['message']}")
            if r.get("mismatches"):
                for m in r["mismatches"][:3]:
                    self.errors.append(f"  mismatch at iter {m['iteration']}")
            return False
        print(f"  ✓ {r['message']}")
        return True

    def _step_data_strength(self) -> bool:
        r = tool_check_data_strength(self.d)
        if not r["success"]:
            self.errors.append(f"[DATA_STRENGTH] {r['message']}")
            return False
        print(f"  ✓ {r['message']}")
        return True

    def _step_final_check(self) -> bool:
        r = tool_final_check(self.d)
        if not r["success"]:
            self.errors.append(f"[FINAL_CHECK] {r['message']}")
            for p in r.get("problems", [])[:5]:
                self.errors.append(f"  - {p}")
            return False
        print(f"  ✓ {r['message']}")
        return True
