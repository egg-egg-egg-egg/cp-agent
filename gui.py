"""
CP-Agent 桌面客户端（PySide6）。

三个页面：
- 生成：配置参数一键出题，QProcess 流式显示 agent 日志，可中断
- 题库：浏览 problems/（含 failed/ 归档），查看题面/元数据/生成结果，一键导出与重跑
- 查重：手动搜索本地 24k 题库（纯检索，不走 LLM 裁判）

启动：python gui.py   （依赖：pip install -e ".[gui]"）
"""
import json
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QProcess, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).parent
PROBLEMS_DIR = PROJECT_ROOT / "problems"

VIEWABLE_FILES = [
    "problem.md", "problem.yaml", "result.json",
    "solution.cpp", "generator.cpp", "validator.cpp", "naive.cpp", "checker.cpp",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 数据扫描（纯函数，便于测试）
# ═══════════════════════════════════════════════════════════════════════════════

def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def scan_one_problem(d: Path, archived: bool = False) -> dict:
    """Collect display info for one problem directory."""
    result = _read_json(d / "result.json")
    meta = _read_yaml(d / "problem.yaml")
    inputs = len(list((d / "inputs").glob("*.in"))) if (d / "inputs").exists() else 0
    outputs = len(list((d / "outputs").glob("*.out"))) if (d / "outputs").exists() else 0

    if archived or (result is not None and not result.get("success")):
        status = "failed"
    elif result is not None and result.get("success"):
        status = "ok"
    else:
        status = "unknown"

    return {
        "name": d.name,
        "path": d,
        "archived": archived,
        "status": status,
        "title": (meta or {}).get("title", ""),
        "difficulty": (meta or {}).get("difficulty"),
        "tags": (meta or {}).get("algorithm_tags") or [],
        "inputs": inputs,
        "outputs": outputs,
        "has_meta": meta is not None,
        "meta": meta,
        "result": result,
    }


def scan_problems(problems_dir: Path = PROBLEMS_DIR) -> list[dict]:
    """Scan problems/ (and problems/failed/) into display entries."""
    entries = []
    if problems_dir.exists():
        for d in sorted(problems_dir.iterdir()):
            if d.is_dir() and d.name != "failed":
                entries.append(scan_one_problem(d))
        failed_root = problems_dir / "failed"
        if failed_root.exists():
            for d in sorted(failed_root.iterdir()):
                if d.is_dir():
                    entries.append(scan_one_problem(d, archived=True))
    return entries


def problem_summary_html(info: dict) -> str:
    """Render the details-panel summary for a problem entry."""
    meta, result = info["meta"], info["result"]
    rows = []

    def row(k, v):
        rows.append(f"<tr><td style='padding-right:12px;color:#888'>{k}</td><td>{v}</td></tr>")

    row("目录", str(info["path"]))
    if meta:
        row("标题", meta.get("title", ""))
        row("标签", "、".join(meta.get("algorithm_tags") or []) or "—")
        row("难度", meta.get("difficulty") or "—")
        row("限制", f"{meta.get('time_limit_ms', '?')} ms / {meta.get('memory_limit_mb', '?')} MB")
        row("Checker", (meta.get("checker") or {}).get("type", "builtin"))
        validation = meta.get("validation")
        if validation:
            samples = validation.get("samples") or {}
            row("校验", f"final_check 通过（样例 {samples.get('passed', '?')}/{samples.get('count', '?')}）")
    else:
        row("元数据", "无 problem.yaml（未跑 write_metadata）")
    row("测试点", f"{info['inputs']} in / {info['outputs']} out")
    if result:
        icon = "✅ 成功" if result.get("success") else "❌ 失败"
        row("生成结果", f"{icon}（{result.get('iterations', '?')} 轮迭代，"
                        f"{result.get('elapsed_sec', '?')}s，provider {result.get('provider', '?')}）")
        tokens = result.get("tokens") or {}
        if tokens:
            row("Token", f"{tokens.get('input', 0)} in / {tokens.get('output', 0)} out")
        if result.get("failure_reason"):
            row("失败原因", str(result["failure_reason"])[:300])
    else:
        row("生成结果", "无 result.json")
    return f"<table>{''.join(rows)}</table>"


# ═══════════════════════════════════════════════════════════════════════════════
# 后台线程：查重搜索（首次会加载 embedding 模型）
# ═══════════════════════════════════════════════════════════════════════════════

class SearchWorker(QThread):
    ok = Signal(list)
    failed = Signal(str)

    def __init__(self, query: str, top_k: int, parent=None):
        super().__init__(parent)
        self.query, self.top_k = query, top_k

    def run(self):
        try:
            from dedup import search_problem_db
            r = search_problem_db(self.query, self.top_k)
            if r.get("success"):
                self.ok.emit(r["results"])
            else:
                self.failed.emit(r.get("message", "搜索失败"))
        except Exception as e:  # noqa: BLE001 — 后台线程需兜底上报
            self.failed.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 生成页
# ═══════════════════════════════════════════════════════════════════════════════

class GeneratePage(QWidget):
    generation_finished = Signal()

    def __init__(self, cfg_data: dict, parent=None):
        super().__init__(parent)
        self.process: QProcess | None = None

        form = QFormLayout()
        self.topic = QComboBox()
        for key, desc in cfg_data.get("topics", {}).items():
            self.topic.addItem(f"{key} — {desc}", key)
        self.difficulty = QComboBox()
        for score, desc in cfg_data.get("difficulty", {}).items():
            self.difficulty.addItem(f"{score} — {desc}", score)
        self.difficulty.setCurrentIndex(
            max(0, self.difficulty.findData(1500)))
        self.provider = QComboBox()
        self.provider.addItem(f"默认（{cfg_data.get('now_model') or 'nowModel'}）", "")
        for name in cfg_data.get("providers", []):
            self.provider.addItem(name, name)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空则用时间戳")
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("如：答案不唯一，输出任意一组合法方案")
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText(
            "题意完善模式（可选）：在这里粘贴大致题意或题面草稿，"
            "系统会在不改变题目模型的前提下补全成完整题目；留空则按考点自由构思")
        self.idea.setFixedHeight(88)
        self.allow_dup = QCheckBox("撞题仍继续（--allow-dup，默认撞题即中止）")
        self.test_count = QSpinBox()
        self.test_count.setRange(5, 200)
        self.test_count.setValue(30)
        self.stress = QSpinBox()
        self.stress.setRange(50, 100000)
        self.stress.setValue(10000)
        self.max_iter = QSpinBox()
        self.max_iter.setRange(5, 200)
        self.max_iter.setValue(30)
        self.export_after = QComboBox()
        for label, val in [("不导出", ""), ("洛谷", "luogu"), ("Hydro", "hydro"),
                           ("Polygon", "polygon"), ("全部", "all")]:
            self.export_after.addItem(label, val)

        form.addRow("算法考点", self.topic)
        form.addRow("难度 (CF)", self.difficulty)
        form.addRow("Provider", self.provider)
        form.addRow("题目名称", self.name_edit)
        form.addRow("额外要求", self.extra)
        form.addRow("题意描述", self.idea)
        form.addRow("", self.allow_dup)
        form.addRow("测试点数量", self.test_count)
        form.addRow("对拍轮数", self.stress)
        form.addRow("最大迭代", self.max_iter)
        form.addRow("完成后导出", self.export_after)

        self.start_btn = QPushButton("▶ 开始生成")
        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setEnabled(False)
        self.status = QLabel("就绪")
        btns = QHBoxLayout()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.status, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Menlo", 11))
        self.log.setMaximumBlockCount(20000)

        box = QGroupBox("生成参数")
        box.setLayout(form)
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addLayout(btns)
        layout.addWidget(self.log, 1)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)

    def start(self):
        if self.process is not None:
            return
        args = [str(PROJECT_ROOT / "main.py"),
                "--topic", self.topic.currentData(),
                "--difficulty", str(self.difficulty.currentData()),
                "--test-count", str(self.test_count.value()),
                "--stress", str(self.stress.value()),
                "--max-iterations", str(self.max_iter.value())]
        if self.provider.currentData():
            args += ["--provider", self.provider.currentData()]
        if self.name_edit.text().strip():
            args += ["--name", self.name_edit.text().strip()]
        if self.extra.text().strip():
            args += ["--extra", self.extra.text().strip()]
        if self.idea.toPlainText().strip():
            args += ["--idea", self.idea.toPlainText().strip()]
            if self.allow_dup.isChecked():
                args += ["--allow-dup"]
        if self.export_after.currentData():
            args += ["--export-after", self.export_after.currentData()]

        self.log.clear()
        self.log.appendPlainText(f"$ {sys.executable} {' '.join(args)}\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_finished)
        self.process.start(sys.executable, args)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText("生成中…")

    def stop(self):
        if self.process is not None:
            self.process.kill()

    def _on_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(data)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)

    def _on_finished(self, code, _status):
        self.status.setText("✅ 完成" if code == 0 else f"❌ 退出码 {code}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self.generation_finished.emit()


# ═══════════════════════════════════════════════════════════════════════════════
# 题库页
# ═══════════════════════════════════════════════════════════════════════════════

class ProblemsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process: QProcess | None = None

        # 左侧列表
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_select)

        # 右侧详情
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignTop)
        self.file_combo = QComboBox()
        self.file_combo.currentTextChanged.connect(self._show_file)
        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)

        right = QVBoxLayout()
        right.addWidget(self.summary)
        right.addWidget(self.file_combo)
        right.addWidget(self.viewer, 1)
        right_w = QWidget()
        right_w.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(right_w)
        splitter.setSizes([280, 720])

        # 工具条
        self.refresh_btn = QPushButton("⟳ 刷新")
        self.open_btn = QPushButton("📂 打开目录")
        self.export_fmt = QComboBox()
        for label, val in [("洛谷", "luogu"), ("Hydro", "hydro"),
                           ("Polygon", "polygon"), ("全部", "all")]:
            self.export_fmt.addItem(label, val)
        self.export_btn = QPushButton("📦 导出")
        self.pipeline_btn = QPushButton("🔁 重跑 Pipeline")
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.open_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.export_fmt)
        toolbar.addWidget(self.export_btn)
        toolbar.addWidget(self.pipeline_btn)

        self.op_log = QPlainTextEdit()
        self.op_log.setReadOnly(True)
        self.op_log.setFont(QFont("Menlo", 10))
        self.op_log.setFixedHeight(120)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.op_log)

        self.refresh_btn.clicked.connect(self.refresh)
        self.open_btn.clicked.connect(self._open_folder)
        self.export_btn.clicked.connect(self._export)
        self.pipeline_btn.clicked.connect(self._rerun_pipeline)
        self.refresh()

    # ── 列表 ──
    def refresh(self):
        current = self._selected_path()
        self.tree.clear()
        entries = scan_problems()
        ok_root = QTreeWidgetItem(["📚 problems/"])
        failed_root = QTreeWidgetItem(["🗄 failed/（失败归档）"])
        icons = {"ok": "✅", "failed": "❌", "unknown": "❔"}
        for e in entries:
            label = f"{icons[e['status']]} {e['name']}"
            if e["title"]:
                label += f"　{e['title']}"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.UserRole, e)
            (failed_root if e["archived"] else ok_root).addChild(item)
        for root in (ok_root, failed_root):
            if root.childCount():
                self.tree.addTopLevelItem(root)
                root.setExpanded(True)
        # 尝试恢复选择
        if current is not None:
            for i in range(self.tree.topLevelItemCount()):
                root = self.tree.topLevelItem(i)
                for j in range(root.childCount()):
                    child = root.child(j)
                    if child.data(0, Qt.UserRole)["path"] == current:
                        self.tree.setCurrentItem(child)
                        return

    def _selected_info(self) -> dict | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def _selected_path(self) -> Path | None:
        info = self._selected_info()
        return info["path"] if info else None

    def _on_select(self, item, _prev):
        info = item.data(0, Qt.UserRole) if item else None
        if not info:
            self.summary.clear()
            self.file_combo.clear()
            self.viewer.clear()
            return
        self.summary.setText(problem_summary_html(info))
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for name in VIEWABLE_FILES:
            if (info["path"] / name).exists():
                self.file_combo.addItem(name)
        self.file_combo.blockSignals(False)
        self._show_file(self.file_combo.currentText())

    def _show_file(self, name: str):
        info = self._selected_info()
        if not info or not name:
            self.viewer.clear()
            return
        path = info["path"] / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            text = f"读取失败: {e}"
        if name.endswith(".md"):
            self.viewer.setMarkdown(text)
        else:
            self.viewer.setFont(QFont("Menlo", 11))
            self.viewer.setPlainText(text)

    # ── 操作 ──
    def _open_folder(self):
        path = self._selected_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _run_op(self, args: list[str]):
        if self.process is not None:
            QMessageBox.information(self, "CP-Agent", "已有操作在运行，请等它结束。")
            return
        self.op_log.appendPlainText(f"$ {sys.executable} {' '.join(args)}")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(
            lambda: self.op_log.appendPlainText(
                bytes(self.process.readAllStandardOutput())
                .decode("utf-8", errors="replace").rstrip()))
        self.process.finished.connect(self._op_finished)
        self.process.start(sys.executable, args)

    def _op_finished(self, code, _status):
        self.op_log.appendPlainText("✅ 完成" if code == 0 else f"❌ 退出码 {code}")
        self.process = None
        self.refresh()

    def _export(self):
        path = self._selected_path()
        if path is None:
            return
        self._run_op([str(PROJECT_ROOT / "export.py"), str(path),
                      "--format", self.export_fmt.currentData()])

    def _rerun_pipeline(self):
        path = self._selected_path()
        if path is None:
            return
        self._run_op([str(PROJECT_ROOT / "main.py"), "--pipeline", str(path)])


# ═══════════════════════════════════════════════════════════════════════════════
# 查重页
# ═══════════════════════════════════════════════════════════════════════════════

class SearchPage(QWidget):
    COLS = ["向量相似度", "排序分", "来源", "题号", "标题", "标签"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: SearchWorker | None = None
        self.results: list[dict] = []

        self.query = QLineEdit()
        self.query.setPlaceholderText("如：动态插入 最长上升子序列 平衡树")
        self.top_k = QSpinBox()
        self.top_k.setRange(1, 20)
        self.top_k.setValue(8)
        self.search_btn = QPushButton("🔍 搜索")
        self.status = QLabel("首次搜索需加载 embedding 模型（数秒）")
        bar = QHBoxLayout()
        bar.addWidget(self.query, 1)
        bar.addWidget(QLabel("top_k"))
        bar.addWidget(self.top_k)
        bar.addWidget(self.search_btn)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.currentCellChanged.connect(self._show_snippet)

        self.snippet = QTextBrowser()
        self.snippet.setFixedHeight(140)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.snippet)

        self.search_btn.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)

    def search(self):
        q = self.query.text().strip()
        if not q or self.worker is not None:
            return
        self.status.setText("搜索中…")
        self.search_btn.setEnabled(False)
        self.worker = SearchWorker(q, self.top_k.value(), self)
        self.worker.ok.connect(self._on_results)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _on_done(self):
        self.worker = None
        self.search_btn.setEnabled(True)

    def _on_failed(self, msg: str):
        self.status.setText(f"❌ {msg}")

    def _on_results(self, results: list):
        self.results = results
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            cells = [f"{r['vector_score']:.3f}", f"{r['final_score']:.3f}",
                     r["source"], str(r["source_id"]), r["title"], str(r["tags"])[:60]]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.status.setText(f"返回 {len(results)} 条（向量相似度是余弦分，排序分是 RRF 融合分仅供排序）")

    def _show_snippet(self, row, *_args):
        if 0 <= row < len(self.results):
            r = self.results[row]
            url = f"<a href='{r['url']}'>{r['url']}</a><br>" if r.get("url") else ""
            self.snippet.setHtml(f"{url}{r.get('snippet', '')}")


# ═══════════════════════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════════════════════

def load_cfg_data() -> dict:
    """Pull the lists the GUI needs from config; degrade gracefully if broken."""
    try:
        import config
        return {
            "topics": config.ALGO_TOPICS,
            "difficulty": config.DIFFICULTY_PRESETS,
            "providers": config.list_enabled_provider_choices(),
            "now_model": config.get_now_model(),
            "error": None,
        }
    except Exception as e:  # ConfigError 或缺依赖
        return {"topics": {}, "difficulty": {}, "providers": [],
                "now_model": "", "error": str(e)}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CP-Agent 控制台")
        self.resize(1100, 760)

        cfg_data = load_cfg_data()
        tabs = QTabWidget()
        self.generate_page = GeneratePage(cfg_data)
        self.problems_page = ProblemsPage()
        self.search_page = SearchPage()
        tabs.addTab(self.generate_page, "⚡ 生成")
        tabs.addTab(self.problems_page, "📚 题库")
        tabs.addTab(self.search_page, "🔍 查重")
        self.setCentralWidget(tabs)

        self.generate_page.generation_finished.connect(self.problems_page.refresh)

        if cfg_data["error"]:
            self.statusBar().showMessage(f"⚠ 配置加载失败：{cfg_data['error']}")
        else:
            self.statusBar().showMessage(
                f"nowModel: {cfg_data['now_model'] or '未设置'} · problems: {PROBLEMS_DIR}")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
