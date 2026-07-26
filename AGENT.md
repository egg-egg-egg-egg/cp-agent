# CP-Agent 项目文档

## 项目概述
全自动算法竞赛出题 AI Agent 框架，从题目概念到完整数据包一键生成。

## 架构
```
cp-agent/
├── main.py              # CLI 入口（agent / pipeline / export 三种模式）
├── agent.py             # Agent loop（LLM function calling + 质量门禁 + 重试退避）
├── pipeline.py          # 12 个沙盒 tool 函数 + Pipeline 类
├── export.py            # 洛谷 / Hydro / Polygon 题目包导出
├── report.py            # result.json 结构化结果 + 产物完整性检查
├── logutil.py           # 文件日志（cp_agent.log，DEBUG 级）
├── batch_generate.py    # 批量生成驱动（断点续跑，读 result.json 判成败）
├── config.py            # YAML 配置加载器（延迟加载 + 校验 + ConfigError）
├── config.yaml          # 所有配置（供应商、难度、算法主题）
├── tests/               # pytest 单元测试（不调 LLM）
├── .github/workflows/   # CI（ruff + pytest，py3.10/3.12）
├── testlib.h            # Codeforces 官方测试库
├── problem_db/          # 原题查重系统
│   ├── __init__.py      # CLI：crawl / enrich / import / build / search
│   ├── __main__.py      # python -m problem_db 入口
│   ├── crawler.py       # CF API 爬虫 + 限速
│   ├── cf_enrich.py     # CF 题面补爬（多线程，5 workers）
│   ├── import_deepmind.py  # 从 DeepMind code_contests 导入 CF 题面
│   ├── import_luogu.py  # 从 GitHub 导入洛谷题目
│   ├── luogu_enrich.py  # 洛谷题面爬虫（多线程）
│   ├── luogu_enrich_fast.py  # 洛谷题面快速爬虫（单线程）
│   ├── embedder.py      # 本地 embedding 模型
│   └── index.py         # FAISS 向量索引 + SQLite 元数据
├── problem_data/        # 数据文件
│   ├── problems.db      # SQLite 题库（24071 题）
│   ├── problems.faiss   # FAISS 索引（384 维）
│   └── problems.map.json
├── templates/           # C++ 模板文件
├── problems/            # 生成的题目目录
└── README.md
```

## 核心工作流（Agent 模式）
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
| `stress_test` | `count` | ✅ | 对拍（generator 收到 argv[3]="stress"；有 bin/checker 则 SPJ 判定；naive 超软上限=失败并要求缩小对拍数据） |
| `write_metadata` | `title`, `algorithm_tags`, … | ✅ | 写 problem.yaml（cases/checker 类型自动扫描） |
| `check_data_strength` | 无 | ✅ | 最大数据上 naive 必须 TLE（或 ≥5× std），否则数据太弱 |
| `final_check` | `waive_bounds?` | ✅ | 文件齐全/样例一致/边界覆盖/数据强度，通过后回填 validation 块 |
| `search_problem_db` | `query`, `top_k` | — | 本地 hybrid 题库查重，返回 dup_verdict |
| `web_search` | `query` | — | 搜索网页 |

## LLM 供应商（config.yaml）

| 供应商 | 协议 | 默认模型 | env_key |
|--------|------|---------|---------|
| anthropic | anthropic | claude-sonnet-4-20250514 | ANTHROPIC_API_KEY |
| openai | openai | gpt-4o | OPENAI_API_KEY |
| deepseek | openai | deepseek-chat | DEEPSEEK_API_KEY |
| ollama | openai | qwen2.5-coder:14b | 无需 |
| mimo | openai | mimo-v2.5-pro | 直接写在 env_key 里 |

API key 优先级：CLI --api-key > config yaml api_key > env_key（先查环境变量，再当 key 用）

## 难度系统
Codeforces 分数制：800-3000，步长 100，共 23 档。
- 分数只表示难度，不绑定算法或数据规模
- 算法由 `--topic` 指定，N/时间/内存由 LLM 自行决定

## 使用方式
```bash
conda activate cp-agent

# Agent 模式（默认）
python main.py --topic dp --difficulty 1800 --provider mimo --max-iterations 40

# Pipeline 模式（无 LLM，对已有题目跑流水线）
python main.py --pipeline problems/my_problem/

# 原题查重系统
python -m problem_db crawl codeforces          # 爬 CF 元数据
python -m problem_db import codeforces          # 导入 DeepMind CF 题面
python -m problem_db import luogu               # 导入洛谷题目
python -m problem_db enrich codeforces 0 5      # 补爬 CF 题面（5线程）
python -m problem_db build                      # 建 FAISS 索引
python -m problem_db search "线段树 区间GCD"     # 搜索相似题
```

## 沙盒规则
- 所有 path 必须是相对路径
- 拒绝 `..`、绝对路径
- resolve 后必须仍在 problem_dir 内

## 对拍超时策略
- `run_solution`：单个 .in 超时 → 返回 `timeout: true`（标程有误）
- `stress_test`：std 超时 5s → 失败；naive 超时 10s → 通过并提前结束

## Agent Loop
- 双协议支持：Anthropic（content blocks）和 OpenAI（tool_calls）
- 消息格式自动适配
- 最大迭代次数可配（默认 30）
- 每轮 LLM 返回 tool_use → 执行 → 返回 tool_result → 循环
- 返回 text（无 tool）→ 结束

## validator 正确写法
```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    inf.init(argv[1], _input);
    int n = inf.readInt(1, 100000, "n");
    inf.readEoln();
    // ... validate fields ...
    inf.readEof();
    return 0;
}
```
不要用 registerValidation()，必须用 registerGen + inf.init。

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
python -m problem_db crawl codeforces

# 导入 DeepMind CF 题面（需代理访问 HuggingFace）
export HUGGING_FACE_HUB_TOKEN="your_token"
python -m problem_db import codeforces

# 补爬 CF 题面（多线程）
python -m problem_db enrich codeforces 0 5

# 导入洛谷（从 GitHub）
python -m problem_db import luogu

# 补全洛谷题面（直接爬取洛谷网页）
python -m problem_db enrich_luogu 0 3 2.0    # 全量，3 workers，2s 延迟
python -m problem_db enrich_luogu 100 3 2.0  # 仅前 100 题

# 重建 FAISS 索引
python -m problem_db build

# 搜索相似题
python -m problem_db search "动态规划 背包"
```

## 查重流程（检索召回 + LLM 裁判判定）

Agent 模式下，`search_problem_db` 的完整流程（`agent._dedup_check`）：
1. **召回**：hybrid 检索本地索引（FAISS 向量 + FTS/LIKE 关键词 + 术语 rerank）
2. **触发**：`vector_score`（余弦相似度）≥ `dedup_judge_trigger`（默认 0.5）的候选，
   取前 `dedup_judge_max_candidates`（默认 5）个
3. **裁判**：把新题 problem.md + 候选题面摘要交给独立 LLM 裁判，逐候选判断是否
   【同一题目模型】（抽象掉故事背景后输入结构/约束/目标/解法基本一致）
4. **门禁**：任一候选判 same_model → `dup_verdict = must_change`，agent_loop 拦截
   generate_test_data/stress_test/write_metadata/final_check，且完成前不允许结束；
   裁判调用失败时退回向量相似度阈值兜底（≥0.85 换题 / 0.7-0.85 人工判断）

**为什么不用分数阈值判定**：`final_score` 是 RRF 排名融合分（量级 ~0.1，仅用于排序）；
`vector_score` 度量的是叙事相似度而非题目模型等价性。实测：与 P4309 完全同模型的题
向量相似度仅 0.68，而裸 LIS 与"动态插入 LIS"（不同模型）也能到 0.70——任何单一阈值
都同时存在漏杀和误杀，因此分数只作召回触发器，判定交给 LLM 裁判。

实测示例（2026-07-26，DeepSeek 裁判）：
- 复刻 P4309 动态插入 LIS → 裁判判 same_model=true，理由精确到输入输出格式 → must_change ✅
- 裸 LIS vs 动态 LIS/树上 LIS/LIS 期望 → 裁判逐个说明模型差异后放行 ✅

**已知盲区**：题库只含洛谷 P/SP 题（无 B 题库入门题）与 CF，裸经典题（如 B3637 裸 LIS）
可能不在召回结果里；经典套路仍建议 Agent 主动规避。

## 已知问题
1. testlib.h 在 problem_dir 里找不到（在项目根目录），Agent 不知道
2. web_search 用 DuckDuckGo HTML 解析，质量不稳定
3. macOS 没有 bits/stdc++.h，需用标准头文件
4. ~~洛谷 P9000+ 题目无题面~~ ✅ 已通过 `luogu_enrich_fast.py` 补全（100% 覆盖）
5. AtCoder 爬虫暂未实现（API 需要认证）
6. HuggingFace 下载需要代理：`export https_proxy=http://127.0.0.1:7897`
7. CF Gym 无法爬取 — 页面有 Cloudflare 防护，API 只返回元数据不返回题面
8. 洛谷 SPOJ 系列（SP 开头）约 1024 题无题面 — 洛谷上本身就没有完整页面
