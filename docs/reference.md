# CP-Agent 参考资料

> 本文件**不被自动注入**，是 `AGENTS.md` 的延伸；只在需要时按需 Read。
> 最后校对：2026-09-29，逐条对照 `pipeline.py` / `config.py` / `prompts.py` / `agent.py` 核实，
> 已修掉 4 处过期内容（见每节末尾的「📌 已订正」）。

---

## 项目概述

全自动算法竞赛出题 AI Agent 框架，从题目概念到完整数据包一键生成。

## 架构

```
cp-agent/
├── main.py              # CLI 入口（agent / pipeline / export 三种模式）
├── agent.py             # Agent 循环 + 质量门禁 + generate_problem 入口（含 search_problem_db / web_search 两个工具的 schema 与分发）
├── llm_client.py        # LLM 协议适配（Anthropic/OpenAI）、重试退避、消息组装适配器
├── dedup.py             # 原题查重：多路召回 + LLM 裁判
├── prompts.py           # SYSTEM_PROMPT / build_user_prompt（纯 prompt 资产，**约定类问题的权威源**）
├── pipeline.py          # 工具注册表（@tool 装饰器）+ 12 个沙盒工具 + Pipeline 类
├── export.py            # 题目包导出：hydrooj（默认）/ xml（HUSTOJ FPS，实测最可靠）/ luogu / polygon
├── upload.py            # 上传到 OJ（导出 → 登录 → 探测入口 → postkey → 导入；--enable 才改状态）
├── oj_check.py          # 只读验收：核对题面/用例/时限/启用状态是否真的落地
├── verify.py            # 可选质量增强：独立验题 + 难度校准（默认关闭）
├── integrations/        # 平台对接（hustoj.py：登录 / 提交记录 / 上传 / 启用状态）
├── report.py            # result.json 结构化结果 + 产物完整性检查
├── logutil.py           # 文件日志（cp_agent.log，DEBUG 级）
├── gui.py               # PySide6 桌面客户端
├── batch_generate.py    # 批量生成驱动（断点续跑，考点/难度读自 config）
├── batch_upload.py      # 批量上传（断点续传，写 uploaded_pids.txt，尾部自动同步看板）
├── config.py            # YAML 配置加载器（延迟加载 + 校验 + ConfigError；默认值的权威源）
├── config.yaml          # 所有配置（供应商、难度、算法主题）—— 被 gitignore
├── problem_gate.py      # 看板门禁（**仓库根**，CI 可见）
├── download_problems.py # 下载 CI 产物 → 门禁 → 落盘 → 同步看板
├── sync_dashboard.py    # 编排「门禁 + 新增 + PID 回填」，幂等，含 --prune-diff
├── tests/               # pytest 单元测试（不调 LLM）
├── docs/                # 本项目文档（reference.md 等）
├── .github/workflows/   # CI（ruff + pytest + gate job，py3.10/3.12）
├── testlib.h            # Codeforces 官方测试库
├── problem_db/          # 原题查重系统
│   ├── __init__.py      # CLI：crawl / enrich / import / build / search
│   ├── __main__.py      # python -m problem_db 入口
│   ├── crawler.py       # CF API 爬虫 + 限速
│   ├── cf_enrich.py     # CF 题面补爬（多线程，5 workers）
│   ├── import_deepmind.py  # 从 DeepMind code_contests 导入 CF 题面
│   ├── import_luogu.py  # 从 GitHub 导入洛谷题目
│   ├── luogu_enrich.py      # 洛谷题面爬虫（多线程）
│   ├── luogu_enrich_fast.py # 洛谷题面快速爬虫（单线程）
│   ├── embedder.py      # 本地 embedding 模型
│   └── index.py         # FAISS 向量索引 + SQLite 元数据
├── problem_data/        # 数据文件（problems.db / problems.faiss / problems.map.json）
├── templates/           # C++ 模板文件
├── problems/            # 生成的题目目录 —— 被 gitignore
└── README.md
```

## 核心工作流（Agent 模式）

两种入口：**自由构思**（`--topic`）与**题意完善**（`--idea` / `--idea-file`，`--topic` 变可选）。
完善模式下 prompt 要求"不得改变核心题目模型"，只允许补数据范围/时限/样例/规范表述；
`--name` 指向含已有文件的目录时按"增量补全"处理，失败也不归档用户草稿目录。

查重策略随模式分化（`agent_loop(dedup_policy=…)`）：

- 自由构思 `rewrite`：撞题 → 拦截产出工具，逼 LLM 换题重查
- 完善模式 `abort`（默认）：题意是用户给的，撞题 → 立即终止，`failure_reason` 带原题与裁判理由
- 完善模式 + `--allow-dup` → `warn`：降级为警告继续

LLM 通过 function calling 自主驱动：

```
1. 构思题目 → 生成 problem.md
2. search_problem_db 本地题库查重（≥0.95 强制换题，代码级拦截产出类工具）
3. 生成 solution / generator / validator / naive（多解题另加 checker.cpp）
4. 编译 → 生成数据 → 校验（含边界覆盖统计）→ 求解 → 对拍
5. write_metadata 写 problem.yaml → check_data_strength → final_check
6. 出错则检查修复、重试；完成前服务器端自动跑 final_check，不通过不允许结束
```

## 14 个 Tool

其中 12 个在 `pipeline.py`（`@tool` 装饰，沙盒内），`search_problem_db` / `web_search` 两个在
`agent.py`（不进沙盒，直接发网络请求）。

| Tool | 参数 | 沙盒 | 说明 |
|------|------|------|------|
| `read_file` | `path` | ✅ | 读文件 |
| `write_file` | `path`, `content` | ✅ | 写文件 |
| `edit_file` | `path`, `old_text`, `new_text` | ✅ | 搜索替换 |
| `list_files` | `dir` | ✅ | 列目录 |
| `compile_cpp` | `source`, `output` | ✅ | 编译 C++ |
| `generate_test_data` | `count` | ✅ | 生成 .in（CLI --test-count 经 tool_defaults 兜底） |
| `validate_inputs` | 无 | ✅ | 校验输入；registerValidation 时统计边界触达（bounds_unhit） |
| `run_solution` | `timeout_sec` | ✅ | 运行标程（超时=标程有误；默认时限来自 config） |
| `stress_test` | `count` | ✅ | 对拍（generator 收 argv[3]="stress"；有 bin/checker 则 SPJ 判定；naive 超软上限=**失败并要求缩小对拍数据**） |
| `write_metadata` | `title`, `algorithm_tags`, … | ✅ | 写 problem.yaml（cases/checker 类型自动扫描） |
| `check_data_strength` | 无 | ✅ | 最大数据上 naive 必须 TLE（或 ≥5× std），否则数据太弱 |
| `final_check` | `waive_bounds?` | ✅ | 文件齐全/样例一致/边界覆盖/数据强度，通过后回填 validation 块 |
| `search_problem_db` | `query`, `top_k` | — | 本地 hybrid 题库查重，返回 dup_verdict |
| `web_search` | `query` | — | 搜索网页 |

> 📌 已订正：「14 个」是正确的（12 + 2）。此前一度以为文档写多了，是因为只数了 `pipeline.py`。

## LLM 供应商（config.yaml）

| 供应商 | 协议 | 默认模型 | env_key |
|--------|------|---------|---------|
| anthropic | anthropic | claude-sonnet-4-20250514 | ANTHROPIC_API_KEY |
| openai | openai | gpt-4o | OPENAI_API_KEY |
| deepseek | openai | deepseek-chat | DEEPSEEK_API_KEY |
| ollama | openai | qwen2.5-coder:14b | 无需 |
| mimo | openai | mimo-v2.5-pro | 直接写在 env_key 里 |

API key 优先级：CLI `--api-key` > config yaml `api_key` > `env_key`（先查环境变量，再当 key 用）。

## 难度系统

Codeforces 分数制：800-3000，步长 100，共 23 档。

- 分数只表示难度，不绑定算法或数据规模
- 算法由 `--topic` 指定，N/时间/内存由 LLM 自行决定

## 使用方式

```bash
# 主开发环境是项目 venv（不要用 conda）
.venv/Scripts/python.exe main.py --topic dp --difficulty 1800 --provider deepseek --max-iterations 40

# Pipeline 模式（无 LLM，对已有题目跑流水线）
.venv/Scripts/python.exe main.py --pipeline problems/my_problem/

# 原题查重系统
.venv/Scripts/python.exe -m problem_db crawl codeforces   # 爬 CF 元数据
.venv/Scripts/python.exe -m problem_db import codeforces  # 导入 DeepMind CF 题面
.venv/Scripts/python.exe -m problem_db import luogu       # 导入洛谷题目
.venv/Scripts/python.exe -m problem_db enrich codeforces 0 5  # 补爬 CF 题面（5 线程）
.venv/Scripts/python.exe -m problem_db build              # 建 FAISS 索引
.venv/Scripts/python.exe -m problem_db search "线段树 区间GCD"  # 搜索相似题
```

> 📌 已订正：原文写 `conda activate cp-agent`，实际项目用 `.venv\Scripts\python.exe`。

## 沙盒规则

- 所有 path 必须是相对路径
- 拒绝 `..`、绝对路径
- resolve 后必须仍在 `problem_dir` 内

## 超时策略

默认值在 `config.py`，均可在 `config.yaml` 覆盖：

| 场景 | 配置键（默认） | 行为 |
|------|--------------|------|
| `run_solution` 标程 | `solution_timeout_sec`（5s） | 单个 .in 超时 → 返回 `timeout: true`（标程有误） |
| `stress_test` 标程 | `stress_timeout_sec`（5s） | 超时 → 失败，提示标程复杂度有误 |
| `stress_test` naive 硬超时 | `stress_naive_timeout_sec`（15s） | 触发软上限判定 |
| `stress_test` naive 软上限 | `stress_naive_soft_limit_sec`（10s） | 超时 → **`success: false` + `naive_timeout: true`**，要求修改 generator 在 `argv[3]=="stress"` 时生成小数据后重跑 |

> 📌 已订正：原文写「naive 超时 10s → 通过并提前结束」，与 `pipeline.py:708` 实际行为相反
> （返回失败并要求缩小对拍数据）。同一份文档前面第 155 行反而写对了，属自相矛盾。

## Agent Loop

- 双协议支持：Anthropic（content blocks）和 OpenAI（tool_calls）
- 消息格式自动适配
- 最大迭代次数可配（默认 30；撞上限先放宽到 45 重试）
- 每轮 LLM 返回 tool_use → 执行 → 返回 tool_result → 循环
- 返回 text（无 tool）→ 结束

## validator 正确写法

```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);        // ← 从 stdin 读
    int n = inf.readInt(1, 100000, "n");   // ← 第三个参数是变量名，边界报告靠它
    inf.readEoln();
    // ... validate fields ...
    inf.readEof();
    return 0;
}
```

**必须用 `registerValidation(argc, argv)`**，不要用 `registerGen + inf.init` / `registerTestlibCmd`。
`pipeline.py:435` 就是拿 `"registerValidation" in val_src` 当「新式（有边界报告）」的判据：
用旧式写，`--testOverviewLogFileName` 不产出，**边界覆盖检查直接失效**，门禁那条硬失败会挂。

> 📌 已订正：`AGENT.md` 原文写的是「不要用 `registerValidation()`，必须用 `registerGen + inf.init`」——
> 完全写反了，与权威源 `prompts.py:132` 正好相反。这条若被注入会直接污染出题。

## 原题查重系统

### 数据来源

**Codeforces（12228 题）**

1. `crawler.py` — CF API `problemset.problems` 获取全部题目元数据（标题、标签、分数）
2. `import_deepmind.py` — 从 HuggingFace `deepmind/code_contests` 下载 parquet，提取 CF 题面（4118 题）
3. `cf_enrich.py` — 多线程补爬剩余题面（5 workers，3-8s 延迟，~50 题/分钟）
   - URL: `https://codeforces.com/contest/{cid}/problem/{index}`
   - 解析 `<div class="problem-statement">` 提取 HTML 题面
   - 结果：11985/12228（98.0%）有完整题面

**洛谷（11843 题）**

1. `import_luogu.py` — 从 GitHub `Molmin/luoguProblems-datas` 下载元数据
   - `https://raw.githubusercontent.com/Molmin/luoguProblems-datas/main/data/P.json`
   - `https://raw.githubusercontent.com/Molmin/luoguProblems-datas/main/data/SP.json`
   - 包含：难度、标签、统计（通过率、提交数）
2. 同一脚本从 `OldAntique110/Luogu-Problems` 下载题面
   - `https://raw.githubusercontent.com/OldAntique110/Luogu-Problems/main/P{pid}.md`
   - 结果：7717/11843（65.2%）有完整题面（P9000+ 未覆盖）
3. `luogu_enrich_fast.py` — 直接爬取洛谷网页补全题面
   - 从 `<script id="lentille-context">` 提取内嵌 JSON 数据
   - 包含：题目描述、输入格式、输出格式、提示
   - 结果：10789/11843（91%）有实质内容，P9000+ 100% 覆盖
   - 速率：~115 题/分钟，总耗时 ~33 分钟
   - 剩余 1024 题（8%）为 SPOJ 系列，在洛谷上无完整题面

### 技术栈

- Embedding：`paraphrase-multilingual-MiniLM-L12-v2`（384 维，本地推理）
- 索引：FAISS IndexFlatIP（余弦相似度）
- 存储：SQLite（题库）+ FAISS 文件 + JSON 映射

### 使用方法

```bash
# 爬取 CF 元数据
.venv/Scripts/python.exe -m problem_db crawl codeforces

# 导入 DeepMind CF 题面（需代理访问 HuggingFace）
export HUGGING_FACE_HUB_TOKEN="your_token"
.venv/Scripts/python.exe -m problem_db import codeforces

# 补爬 CF 题面（多线程）
.venv/Scripts/python.exe -m problem_db enrich codeforces 0 5

# 导入洛谷（从 GitHub）
.venv/Scripts/python.exe -m problem_db import luogu

# 补全洛谷题面（直接爬取洛谷网页）
.venv/Scripts/python.exe -m problem_db enrich_luogu 0 3 2.0    # 全量，3 workers，2s 延迟
.venv/Scripts/python.exe -m problem_db enrich_luogu 100 3 2.0  # 仅前 100 题

# 重建 FAISS 索引
.venv/Scripts/python.exe -m problem_db build

# 搜索相似题
.venv/Scripts/python.exe -m problem_db search "动态规划 背包"
```

## 查重流程（检索召回 + LLM 裁判判定）

Agent 模式下，`search_problem_db` 的完整流程（`agent._dedup_check`）：

1. **多路召回**：hybrid 检索（FAISS 向量 + FTS/LIKE 关键词 + 术语 rerank）跑三路 query——
   LLM 关键词、题面标题、题面描述首段——合并去重取高分。原因：LLM 的 query 措辞不稳定，
   整段题面 embedding 会稀释语义（实测同模型原题标题路召回 0.79，整段题面路召不回）
2. **触发**：`vector_score`（余弦相似度）≥ `dedup_judge_trigger`（默认 0.5）的候选，
   取前 `dedup_judge_max_candidates`（默认 **50**）个
3. **裁判**：把新题 problem.md + 候选题面摘要交给独立 LLM 裁判，逐候选判断是否
   【同一题目模型】（抽象掉故事背景后输入结构/约束/目标/解法基本一致）
4. **门禁**：任一候选判 same_model → `dup_verdict = must_change`，agent_loop 拦截
   generate_test_data/stress_test/write_metadata/final_check，且完成前不允许结束；
   裁判调用失败时退回向量相似度阈值兜底（≥0.85 换题 / 0.7-0.85 人工判断）
5. **自建题入库**（`index_generated`，默认开）：生成成功的题目 upsert 进 SQLite/FTS
   并追加 FAISS 向量（`problem_db/ingest.py`），后续生成的查重能召回它——批量出题防自撞

> 📌 已订正：原文写 `dedup_judge_max_candidates` 默认 5，实际 `config.py:101` 默认 **50**。

### 为什么不用分数阈值判定

`final_score` 是 RRF 排名融合分（量级 ~0.1，仅用于排序）；`vector_score` 度量的是叙事相似度
而非题目模型等价性。实测：与 P4309 完全同模型的题向量相似度仅 0.68，而裸 LIS 与"动态插入 LIS"
（不同模型）也能到 0.70——任何单一阈值都同时存在漏杀和误杀，因此分数只作召回触发器，
判定交给 LLM 裁判。

实测示例（2026-07-26，DeepSeek 裁判）：

- 复刻 P4309 动态插入 LIS → 裁判判 `same_model=true`，理由精确到输入输出格式 → must_change ✅
- 裸 LIS vs 动态 LIS/树上 LIS/LIS 期望 → 裁判逐个说明模型差异后放行 ✅

**已知盲区**：题库只含洛谷 P/SP 题（无 B 题库入门题）与 CF，裸经典题（如 B3637 裸 LIS）
可能不在召回结果里；经典套路仍建议 Agent 主动规避。

## 可选质量增强（verify.py，默认关闭）

- **独立验题** `--cross-check` / config `cross_check`：另一次隔离 LLM 调用只看题面
  （剥离题解）盲解，编译（错误反馈重试 1 次）后在全部测试点与标程比对（SPJ 走 checker）。
  仅 WA 判 failed（生成判失败）；验题人程序 TLE/RE 记 skipped；全部无法运行记 inconclusive。
  动机：std 和 naive 同源，错得一致时对拍测不出来。`cross_check_model` 可指定验题 provider（需强模型）
- **难度校准** `--difficulty-review` / config `difficulty_review`：独立评审读题面+题解估
  CF rating，与标称偏差 >300 警告（写入 result.json，不判失败）。`difficulty_review_model`
  可独立配置，缺省优先复用 `dedup_judge_model`
- 两者的 token 用量都并入 result.json 的 tokens 总账

## 已知问题

1. ~~testlib.h 在 problem_dir 里找不到（在项目根目录），Agent 不知道~~
   ✅ **已解决**：`pipeline.py:89` 编译时统一追加 `-I{testlib 所在目录}`，任意目录都能
   `#include "testlib.h"`；`export.py:485` 还会把它拷进导出包。
2. `web_search` 用 DuckDuckGo HTML 解析，质量不稳定
3. macOS 没有 `bits/stdc++.h`，需用标准头文件
4. ~~洛谷 P9000+ 题目无题面~~ ✅ 已通过 `luogu_enrich_fast.py` 补全（100% 覆盖）
5. AtCoder 爬虫暂未实现（API 需要认证）
6. HuggingFace 下载需要代理：`export https_proxy=http://127.0.0.1:7890`
7. CF Gym 无法爬取 — 页面有 Cloudflare 防护，API 只返回元数据不返回题面
8. 洛谷 SPOJ 系列（SP 开头）约 1024 题无题面 — 洛谷上本身就没有完整页面
9. **看板门禁的 1.2MB 单测试点上限**：2026-09-28 才定，存量有 54 个超标题目（暂不处理）
