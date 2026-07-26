"""
将 cp-agent 生成的题目增量写入本地题库（SQLite + FTS + FAISS），
使后续生成的查重能召回它们——防止批量出题时自己撞自己。
"""
import sqlite3
from pathlib import Path


def add_generated_problem(db_path: Path, index_path: Path, *,
                          source_id: str, title: str, content: str,
                          difficulty: str = "", tags: str = "",
                          url: str = "") -> dict:
    """
    Upsert 一道 source='cp-agent' 的题目并把向量追加进 FAISS 索引。
    重复入库（同 source_id）会更新元数据并追加新向量（旧向量映射同一 db id，
    检索按 id 合并去重，无影响）。
    """
    db_path, index_path = Path(db_path), Path(index_path)
    if not db_path.exists():
        return {"success": False, "message": "problem_data 数据库不存在，跳过入库"}

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO problems (source, source_id, title, content, difficulty, tags, url)
               VALUES ('cp-agent', ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
                   title=excluded.title, content=excluded.content,
                   difficulty=excluded.difficulty, tags=excluded.tags, url=excluded.url""",
            (source_id, title, content, str(difficulty), tags, url),
        )
        row_id = conn.execute(
            "SELECT id FROM problems WHERE source='cp-agent' AND source_id=?",
            (source_id,),
        ).fetchone()[0]

        # 同步 FTS（保持行数一致，避免下次搜索触发全量重建）；失败则留给自动重建
        try:
            conn.execute("DELETE FROM problems_fts WHERE rowid=?", (row_id,))
            conn.execute(
                """INSERT INTO problems_fts(rowid, title, content, tags,
                       llm_algorithm, llm_data_structure, llm_core_operation, llm_standardized)
                   VALUES (?, ?, ?, ?, '', '', '', '')""",
                (row_id, title, content, tags),
            )
        except sqlite3.Error:
            pass
        conn.commit()
    finally:
        conn.close()

    # FAISS 向量：与建索引时相同的标准化文本格式
    vector_added = False
    if index_path.exists():
        from .embedder import get_embedder
        from .index import ProblemIndex
        from .llm_preprocessor import get_standardized_text

        text = get_standardized_text(
            (title, content, tags, str(difficulty), "", "", "", "", ""))
        vec = get_embedder("mini").encode_single(text)

        idx = ProblemIndex(db_path, index_path)
        idx.load()
        idx.index.add(vec.reshape(1, -1))
        idx.id_map.append(row_id)
        idx.dimension = idx.index.d
        idx.save(idx.metadata or {})
        vector_added = True

    return {"success": True, "db_id": row_id, "vector_added": vector_added,
            "message": f"已入库 cp-agent/{source_id} (db_id={row_id}, vector={'✓' if vector_added else '跳过'})"}
