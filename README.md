# CP-Agent: 算法竞赛全自动出题框架

全自动化的算法竞赛（Competitive Programming）题目生成 Agent，从题目概念到完整数据包一键生成。

## 功能特性

- 🤖 **AI 出题**：基于 LLM 自动生成题面、标程、生成器、校验器、暴力解（支持 DeepSeek、Claude、OpenAI 等多供应商）
- 🔧 **自动化流水线**：编译 → 生成数据 → 校验 → 求解 → 对拍 → 数据强度检查 → 最终检查，全自动完成
- ⚖️ **Special Judge**：多解题自动生成 testlib checker，对拍与样例校验走 checker 判定
- 🚧 **质量门槛**：查重不通过强制换题、约束边界覆盖检查、暴力解必须被大数据卡掉、题面样例与标程一致性校验
- 📦 **一键导出**：洛谷（扁平 data.zip）、Hydro（导入包）、Codeforces Polygon（上传就绪目录）；HustOJ 可复用洛谷数据包
- 🎯 **多难度支持**：Codeforces 分数制 800-3000，共 23 档难度
- 🌐 **丰富算法考点**：DP、图论、树、贪心、数据结构、数学等 20+ 算法方向
- 🔍 **原题查重**：内置 24000+ 题库（Codeforces + 洛谷），hybrid 检索召回 + 独立 LLM 裁判判定"是否同一题目模型"，判撞题即强制换题
- 🖥️ **桌面客户端**：PySide6 原生 GUI（`python gui.py`）——生成面板（实时日志/可中断）、题库浏览（题面渲染/元数据/失败归档）、一键导出与重跑、查重搜索

## 环境配置

```bash
# 创建 conda 环境
conda create -n cp-agent python=3.11 -y
conda activate cp-agent

# 安装依赖（db extra 为查重系统，dev extra 为测试/lint）
pip install -e ".[db,dev]"

# 下载题库数据（用于原题查重，可选）
# 百度网盘: https://pan.baidu.com/s/1NhIJ05LKN9lA8-qUiRwaOQ?pwd=ghx2 提取码: ghx2
# 下载后解压到项目根目录，得到 problem_data/ 文件夹

# 配置 API Key
cp config.yaml.example config.yaml
# 编辑 config.yaml：api_key 字段填 key 本身，或 env_key 字段填环境变量名（如 DEEPSEEK_API_KEY）
# 注意：env_key 只接受环境变量名，填明文 key 会报错
```

## 快速开始

```bash
# 生成一道动态规划题（CF 1800 分）
python main.py --topic dp --difficulty 1800

# 生成一道图论困难题，自定义名称
python main.py --topic graph --difficulty 2200 --name "shortest_path_hard"

# 使用 DeepSeek 生成题目
python main.py --topic tree --difficulty 1500 --provider deepseek

# 题意完善模式：给大致题意，系统补全成完整题目（--topic 可省略）
python main.py --idea "给一棵树，每次删一条边问连通块内第k大，考主席树+启发式合并" --difficulty 2100
python main.py --idea-file draft.md --difficulty 1800     # 长题意/草稿从文件读
# 题意与题库撞题时默认中止并给出裁判理由；确认无妨后加 --allow-dup 继续
# 若 --name 指向已有半成品目录（含 problem.md 等），会在已有文件基础上增量补全

# 仅运行流水线（对已有题目目录）
python main.py --pipeline problems/my_problem/

# 导出题目包（洛谷 / Hydro / Polygon）
python main.py --export all --problem-dir problems/my_problem/
python export.py problems/my_problem --format luogu,hydro   # 等价的独立入口
python main.py --topic dp --difficulty 1800 --export-after all   # 生成成功后自动导出

# 批量生成（断点续跑，结果以每题 result.json 为准）
python batch_generate.py --limit 2      # 冒烟测试：只跑 2 题
python batch_generate.py                # 全量

# 桌面客户端（需先 pip install -e ".[gui]"）
python gui.py

# 查看可用算法主题
python main.py --list-topics

# 查看难度预设
python main.py --list-difficulties

# 查看可用 LLM 供应商
python main.py --list-providers
```

## 输出目录结构

```
problems/<problem_name>/
├── problem.md          # 题面（Markdown + LaTeX）
├── problem.yaml        # 机器可读元数据（标题/标签/难度/时限/测试点/校验结果）
├── result.json         # 本次生成的结构化结果（成功与否、耗时、token、各步骤状态）
├── solution.cpp        # 标程（高效 C++ 解法）
├── generator.cpp       # 数据生成器（testlib.h；argv[3]=="stress" 时生成对拍小数据）
├── validator.cpp       # 输入校验器（testlib registerValidation，支持边界覆盖统计）
├── naive.cpp           # 暴力解（用于对拍和数据强度检查）
├── checker.cpp         # Special Judge（仅多解题）
├── bin/                # 编译产物
├── inputs/             # 自动生成的 .in 文件
├── outputs/            # 标程运行生成的 .out 文件
└── export/             # --export 生成的洛谷/Hydro/Polygon 包
```

失败的生成不会留在 `problems/` 下：空目录直接删除，有产物的归档到 `problems/failed/<name>_<时间戳>/` 供排查。

## 流水线步骤

1. **编译** — 编译 generator、validator、solution、naive（多解题另有 checker）
2. **生成数据** — 运行 generator 生成随机 + 边界测试数据
3. **校验输入** — 运行 validator 检查所有输入是否合法，并统计每个约束的 min/max 边界是否被触达
4. **生成输出** — 运行 solution 为每个输入生成输出
5. **对拍验证** — naive 与 solution 对比数千组数据（SPJ 题用 checker 判定）
6. **数据强度检查** — solution 必须在时限内通过最大数据，naive 必须被卡掉
7. **最终检查（final_check）** — 文件齐全、样例与标程一致、边界覆盖、数据强度，全部通过才算完成

Agent 模式还有代码级质量门禁：查重相似度 ≥0.95 时强制换题（拦截造数据/对拍等工具），完成前服务器端自动跑 final_check，不通过不允许结束。

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | CLI 入口，参数解析 |
| `agent.py` | 核心 Agent，调用 LLM 生成所有文件（含查重/完成门禁、重试退避） |
| `pipeline.py` | 沙箱工具集：编译、生成、校验、对拍、元数据、数据强度、final_check |
| `export.py` | 洛谷 / Hydro / Polygon 题目包导出 |
| `report.py` | result.json 结构化结果与产物完整性检查 |
| `batch_generate.py` | 批量生成驱动（断点续跑、--limit/--only） |
| `config.py` | 配置加载与校验（延迟加载，缺失/非法给出友好报错） |
| `config.yaml` | LLM 供应商、难度、算法主题配置（需从 `config.yaml.example` 复制） |
| `testlib.h` | Codeforces 官方测试库（用于 generator/validator/checker） |
| `problem_db/` | 原题查重系统（爬虫 + 向量索引） |
| `tests/` | pytest 单元测试（不调 LLM），CI 自动运行 |

调试日志写入 `cp_agent.log`（未截断的工具结果、LLM token 统计、完整堆栈）。

## 原题查重系统

内置题库包含 **24000+ 道题目**，支持向量相似度检索：

| 来源 | 题数 | 有实质内容 | 覆盖率 |
|------|------|-----------|--------|
| Codeforces | 12,228 | 11,985 | 98% |
| 洛谷 | 11,843 | 10,789 | 91% |
| **总计** | **24,071** | **22,774** | **95%** |

### 使用方法

```bash
# 搜索相似题（查重）
python -m problem_db search "动态规划 背包"
python -m problem_db search "线段树 区间GCD"

# 重建索引（添加新题目后）
python -m problem_db build

# 导入洛谷题目
python -m problem_db import luogu

# 补全洛谷题面
python -m problem_db enrich_luogu 0 3 2.0
```

### 技术栈

- **Embedding**：`paraphrase-multilingual-MiniLM-L12-v2`（384 维，本地推理）
- **索引**：FAISS IndexFlatIP（余弦相似度）
- **存储**：SQLite（题库）+ FAISS 文件 + JSON 映射
