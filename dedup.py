"""
原题查重子系统：hybrid 多路召回 + 独立 LLM 裁判判定"是否同一题目模型"。
"""
import json
import logging
from pathlib import Path
from typing import Optional

import config
from llm_client import call_llm_text

_logger = logging.getLogger("cp_agent.dedup")


def search_problem_db(query: str, top_k: int = 8) -> dict:
    """Search local problem DB with hybrid retrieval for duplicate checking."""
    try:
        from problem_db import DB_PATH, INDEX_PATH
        from problem_db.hybrid_search import hybrid_search

        top_k = max(1, min(int(top_k or 8), 20))
        raw_results = hybrid_search(DB_PATH, INDEX_PATH, query, top_k=top_k)

        results = []
        for r in raw_results:
            content = (r.get("content") or r.get("llm_standardized") or "")
            content = " ".join(str(content).split())
            results.append({
                "rank": r.get("rank"),
                "final_score": round(float(r.get("final_score", 0.0)), 4),
                "vector_score": round(float(r.get("vector_score", 0.0)), 4),
                "source": r.get("source", ""),
                "source_id": r.get("source_id", ""),
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "tags": r.get("tags", ""),
                "difficulty": r.get("difficulty", ""),
                "matched_terms": r.get("matched_terms", []),
                "term_coverage": r.get("term_coverage", 0),
                "term_strength": r.get("term_strength", 0),
                "snippet": content[:350],
            })

        # final_score 是 RRF 排名融合分（仅用于排序，量级 ~0.1）；
        # vector_score 是余弦相似度（0-1），才是可判读的相似度信号
        top_vector = max((r["vector_score"] for r in results), default=0.0)
        return {
            "success": True,
            "message": f"本地题库检索完成：返回 {len(results)} 条，最高向量相似度 {top_vector:.2f}",
            "query": query,
            "top_vector_score": top_vector,
            "results": results,
        }
    except (ModuleNotFoundError, ImportError) as e:
        _logger.warning("search_problem_db 依赖缺失(%s)，查重未启用，已跳过", e)
        return {
            "success": False,
            "disabled": True,
            "message": (
                f"查重库未启用：缺少依赖 {getattr(e, 'name', '') or e}。"
                "需安装 db 依赖（numpy/faiss/sentence-transformers）并下载题库后再用；"
                "本次跳过查重，不影响出题。"
            ),
            "query": query,
        }
    except Exception as e:
        _logger.exception("search_problem_db failed for query=%r", query)
        return {"success": False, "message": f"本地题库搜索失败: {e}", "query": query}



# ═══════════════════════════════════════════════════════════════════════════════
# DEDUP JUDGE — LLM 裁判判定是否同一题目模型
# ═══════════════════════════════════════════════════════════════════════════════
# 通用 embedding 度量的是叙事相似度而非题目模型等价性（实测撞题案例 final_score
# 仅 0.67），因此分数只作召回触发器，判定交给独立的 LLM 裁判。

JUDGE_SYSTEM_PROMPT = """\
你是算法竞赛题目查重裁判。给你一道"新题"的题面和若干候选原题，逐个判断候选题与新题是否为【同一题目模型】。

同一题目模型的标准：抽象掉故事背景后，输入结构、核心约束、目标函数和预期解法基本一致——
即熟悉候选题的选手可以把做法和结论直接搬到新题上。
注意：仅算法/数据结构相同（都是 DP、都用线段树）但问题本身不同，【不算】同一题目模型。
候选题 snippet 可能不完整，请基于可见信息做最合理的判断；信息严重不足时倾向 same_model=false。

只输出一个 JSON 对象，不要输出任何其它文字，格式：
{"judgements": [{"index": 1, "same_model": true, "reason": "一句话理由"}, ...]}
judgements 必须覆盖每个候选题的 index。"""


def parse_judge_response(text: str) -> list[dict]:
    """Extract the judgements list from the judge LLM's output. Raises on failure."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"裁判输出中未找到 JSON: {text[:200]!r}")
    data = json.loads(text[start:end + 1])
    judgements = data.get("judgements")
    if not isinstance(judgements, list):
        raise ValueError("裁判 JSON 缺少 judgements 列表")
    return judgements


def threshold_fallback_verdict(top_vector_score: float) -> tuple[str, str]:
    """Score-threshold verdict on cosine similarity, used only when the LLM judge is unavailable."""
    if top_vector_score >= 0.85:
        return "must_change", "退回阈值判定：向量相似度 ≥ 0.85，视为撞题，必须换题并重新查重。"
    if top_vector_score >= 0.7:
        return "manual_review", (
            "退回阈值判定：向量相似度在 0.7-0.85 之间，请自行对比 snippet 判断是否同一题目模型；"
            "若几乎一样必须换题并重新查重。"
        )
    return "ok", "退回阈值判定：相似度较低，可以继续。"


def statement_recall_queries(statement: str) -> list[str]:
    """从题面提取短召回 query：标题 + 描述首段（短聚焦查询对向量库召回最有效）。"""
    lines = [line.strip() for line in statement.splitlines()]
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("#")), "")
    body_parts = []
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.startswith("#") or not line:
            continue
        body_parts.append(line)
        if sum(len(p) for p in body_parts) >= 200:
            break
    desc = " ".join(body_parts)[:200]
    return [q for q in (title, desc) if q]


def dedup_check(problem_dir: Optional[Path], query: str, top_k: int = 8,
                 llm_kwargs: Optional[dict] = None) -> dict:
    """
    完整查重：hybrid 检索召回 → 高分候选交给独立 LLM 裁判判定是否同一题目模型。
    返回检索结果 + dup_verdict（must_change / manual_review / ok）+ 裁判理由。
    """
    retrieval = search_problem_db(query, top_k)
    if not retrieval.get("success"):
        return retrieval

    statement = ""
    if problem_dir is not None:
        md = Path(problem_dir) / "problem.md"
        if md.exists():
            statement = md.read_text(encoding="utf-8", errors="replace")[:4000]

    # 多路召回：LLM 的关键词 query 措辞不稳定，整段题面 embedding 又会稀释语义
    # （实测同模型原题：标题 query 召回 0.79，整段题面 query 召不回）。
    # 因此追加"标题"和"描述首段"两路短查询，合并去重取高分。
    if statement:
        extra_hits = 0
        for q2 in statement_recall_queries(statement):
            stmt_retrieval = search_problem_db(q2, top_k)
            if not stmt_retrieval.get("success"):
                continue
            extra_hits += 1
            merged: dict = {}
            for r in retrieval["results"] + stmt_retrieval["results"]:
                key = (r["source"], r["source_id"])
                if key not in merged or r["vector_score"] > merged[key]["vector_score"]:
                    merged[key] = r
            retrieval["results"] = sorted(
                merged.values(), key=lambda r: r["vector_score"], reverse=True)
        if extra_hits:
            retrieval["top_vector_score"] = max(
                (r["vector_score"] for r in retrieval["results"]), default=0.0)
            retrieval["message"] = (
                f"本地题库检索完成（关键词 + 标题 + 题面描述多路召回）："
                f"合并 {len(retrieval['results'])} 条，"
                f"最高向量相似度 {retrieval['top_vector_score']:.2f}"
            )

    trigger = config.DEDUP_JUDGE_TRIGGER
    candidates = sorted(
        (r for r in retrieval["results"] if r["vector_score"] >= trigger),
        key=lambda r: r["vector_score"], reverse=True,
    )[:config.DEDUP_JUDGE_MAX_CANDIDATES]

    if not candidates:
        retrieval["dup_verdict"] = "ok"
        retrieval["message"] += f"。查重结论：无候选达到裁判触发线（向量相似度 ≥ {trigger}），可以继续。"
        return retrieval
    if not statement:
        retrieval["dup_verdict"] = "manual_review"
        retrieval["message"] += (
            f"。有 {len(candidates)} 个候选达到裁判触发线，但 problem.md 尚未写入，无法比对。"
            "请先写好 problem.md 再重新调用 search_problem_db 完成裁判查重。"
        )
        return retrieval

    cand_text = "\n\n".join(
        f"候选 {i}（向量相似度 {c['vector_score']:.2f}，来源 {c['source']} {c['source_id']}）\n"
        f"标题：{c['title']}\n题面摘要：{c['snippet']}"
        for i, c in enumerate(candidates, 1)
    )
    judge_user = f"【新题题面】\n{statement}\n\n【候选原题（共 {len(candidates)} 个）】\n{cand_text}"

    # 裁判可用独立（更便宜的）模型：config dedup_judge_model 优先于主 provider
    judge_kwargs = dict(llm_kwargs or {})
    if config.DEDUP_JUDGE_MODEL:
        judge_kwargs = {"provider": config.DEDUP_JUDGE_MODEL}
    judge_tokens: dict = {}

    try:
        raw = call_llm_text(JUDGE_SYSTEM_PROMPT, judge_user,
                            usage_sink=judge_tokens, **judge_kwargs)
        retrieval["judge_tokens"] = judge_tokens
        judgements = parse_judge_response(raw)
    except Exception as e:
        _logger.exception("dedup judge failed for query=%r", query)
        verdict, note = threshold_fallback_verdict(retrieval["top_vector_score"])
        retrieval["dup_verdict"] = verdict
        retrieval["judge_error"] = str(e)
        retrieval["message"] += f"。LLM 裁判调用失败（{e}），{note}"
        return retrieval

    duplicates = []
    for j in judgements:
        try:
            idx = int(j.get("index", 0))
        except (TypeError, ValueError):
            continue
        if j.get("same_model") and 1 <= idx <= len(candidates):
            c = candidates[idx - 1]
            duplicates.append({
                "title": c["title"], "source": c["source"], "source_id": c["source_id"],
                "url": c["url"], "vector_score": c["vector_score"],
                "reason": str(j.get("reason", ""))[:200],
            })

    retrieval["judgements"] = judgements
    if duplicates:
        dup_desc = "；".join(f"《{d['title']}》({d['source']} {d['source_id']}): {d['reason']}"
                             for d in duplicates)
        retrieval["dup_verdict"] = "must_change"
        retrieval["duplicates"] = duplicates
        retrieval["message"] += (
            f"。查重结论【强制】：LLM 裁判判定与 {len(duplicates)} 道原题为同一题目模型——{dup_desc}。"
            "必须重写 problem.md 换题（换问题模型，不是换故事皮），并重新调用 search_problem_db 复查；"
            "复查通过前 generate_test_data/stress_test/write_metadata/final_check 会被拒绝执行。"
        )
    else:
        retrieval["dup_verdict"] = "ok"
        retrieval["message"] += (
            f"。查重结论：LLM 裁判确认 {len(candidates)} 个高分候选均非同一题目模型，可以继续。"
        )
    return retrieval


def index_generated_problem(problem_dir) -> dict:
    """
    把生成成功的题目写入本地题库索引，供后续生成查重召回（防批量自撞）。
    题库数据不存在时静默跳过。
    """
    try:
        import yaml

        from problem_db import DB_PATH, INDEX_PATH
        from problem_db.ingest import add_generated_problem

        base = Path(problem_dir)
        md = base / "problem.md"
        if not md.exists():
            return {"success": False, "message": "problem.md 不存在"}
        content = md.read_text(encoding="utf-8", errors="replace")

        meta = {}
        meta_path = base / "problem.yaml"
        if meta_path.exists():
            try:
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                pass

        title = meta.get("title") or next(
            (line.lstrip("#").strip() for line in content.splitlines()
             if line.startswith("#")), base.name)

        result = add_generated_problem(
            DB_PATH, INDEX_PATH,
            source_id=base.name,
            title=title,
            content=content[:8000],
            difficulty=str(meta.get("difficulty") or ""),
            tags=", ".join(meta.get("algorithm_tags") or []),
        )
        if result.get("success"):
            print(f"  📥 {result['message']}")
        return result
    except Exception as e:
        _logger.exception("index_generated_problem failed for %s", problem_dir)
        return {"success": False, "message": f"入库失败: {e}"}
