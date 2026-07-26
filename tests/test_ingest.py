"""自建题目入库（真实 tmp SQLite + 真实小 FAISS 索引，embedder 打桩）。"""
import sqlite3

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from problem_db import embedder as embedder_mod  # noqa: E402
from problem_db.index import ProblemIndex  # noqa: E402
from problem_db.ingest import add_generated_problem  # noqa: E402

DIM = 8


class _StubEmbedder:
    model_name = "stub"

    def encode_single(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.random(DIM).astype(np.float32)
        return v / np.linalg.norm(v)


def _make_db(tmp_path):
    db_path = tmp_path / "problems.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE problems (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
        source_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
        difficulty TEXT, tags TEXT, url TEXT, UNIQUE(source, source_id))""")
    conn.execute("INSERT INTO problems (source, source_id, title, content) "
                 "VALUES ('luogu', 'P1', '旧题', '内容')")
    conn.commit()
    conn.close()

    index_path = tmp_path / "problems.faiss"
    idx = ProblemIndex(db_path, index_path)
    vec = _StubEmbedder().encode_single("旧题").reshape(1, -1)
    idx.build(vec, [1])
    idx.save({"model_key": "mini"})
    return db_path, index_path


def test_add_and_upsert(tmp_path, monkeypatch):
    monkeypatch.setattr(embedder_mod, "get_embedder", lambda key="mini": _StubEmbedder())
    db_path, index_path = _make_db(tmp_path)

    r = add_generated_problem(db_path, index_path, source_id="dp_1500_x",
                              title="新题", content="题面内容", difficulty="1500",
                              tags="动态规划")
    assert r["success"] and r["vector_added"]

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT title, tags FROM problems WHERE source='cp-agent' "
                       "AND source_id='dp_1500_x'").fetchone()
    assert row == ("新题", "动态规划")

    idx = ProblemIndex(db_path, index_path)
    idx.load()
    assert idx.index.ntotal == 2
    assert idx.id_map[-1] == r["db_id"]

    # upsert：同 source_id 再入库 → 更新行、不新增行、追加向量
    r2 = add_generated_problem(db_path, index_path, source_id="dp_1500_x",
                               title="新题v2", content="改过的题面", difficulty="1600")
    assert r2["db_id"] == r["db_id"]
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM problems WHERE source='cp-agent'").fetchone()[0] == 1
    assert conn.execute("SELECT title FROM problems WHERE id=?", (r["db_id"],)).fetchone()[0] == "新题v2"


def test_missing_db_skips(tmp_path):
    r = add_generated_problem(tmp_path / "nope.db", tmp_path / "nope.faiss",
                              source_id="x", title="t", content="c")
    assert r["success"] is False


def test_missing_index_still_inserts_row(tmp_path, monkeypatch):
    monkeypatch.setattr(embedder_mod, "get_embedder", lambda key="mini": _StubEmbedder())
    db_path, index_path = _make_db(tmp_path)
    index_path.unlink()
    r = add_generated_problem(db_path, index_path, source_id="y", title="t", content="c")
    assert r["success"] is True
    assert r["vector_added"] is False
