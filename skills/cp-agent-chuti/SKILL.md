---
name: cp-agent-chuti
description: 用 cp-agent 项目全自动出一道完整算法题（题面/标程/数据生成器/校验器/暴力解 + 平台导出包）的 SOP。触发词：出题、生成题目、出一道X题、难度参考 CSP-J。agent 据此运行 main.py 出题 → 校验 → 等用户确认 → 上传 OJ。含环境、难度对标、Hydro 格式、失败处理、上传链路。
---

# cp-agent-chuti 出题 SOP

## 维护约定（改前必读）

本 skill 有**两份副本**，改动必须同步：

- 远程版 `skills/cp-agent-chuti/`：随 clone 分发，提交 git。
- 本机版 `.workbuddy/skills/cp-agent-chuti/`：WorkBuddy 自动发现，不入库。

- **cpgen.py 完全同源**：REPO 用向上探测定位，两份可整文件覆盖，无差异。
- **SKILL.md 唯一差异**：正文命令示例里 cpgen 的路径（各自指向自己那份）。
- 改 SKILL.md 正文时，除路径外其余内容必须同步到另一份，别漏。

本 skill 给 agent 用：**保证每次出题都照着流程产出完整、可上传的题目**。
全流程 = 用户提需求 → agent 跑本项目出题 → 用户确认 → 上传平台。

## 触发与输入解析

用户说「出题 / 生成题目 / 出一道 X 题」时启用。先问清三要素，缺了要补：

```
需求三要素
├── topic        算法主题（dp/tree/graph/greedy/string/math/dsu/... 共 20 个，见 config.yaml）
├── difficulty   CF 分数 800–3000，或按 CSP-J2 复赛题位映射（见下）
└── name         题目目录名（同时是 file_io 文件名前缀，用 ASCII 短名）
```

难度对标（参考 2025 CSP-J2 第二轮：拼数/座位/异或和/多边形，四题传统型各 1 秒）：

| 复赛题位 | CF rating |
|---|---|
| T1 入门 | 800–1000 |
| T2 | 1000–1300 |
| T3 | 1300–1600 |
| T4 | 1600–1900 |

## 环境（先确认，别踩）

```
python    D:\Workspace\workbuddy\cp-agent\.venv\Scripts\python.exe
          （托管 python 零依赖，直接跑 main.py 会 ModuleNotFoundError）
g++       已装（RedPanda MinGW64）
凭证      环境变量 DEEPSEEK_API_KEY / OJ_USER / OJ_PASSWORD
```

凭证不在当前进程环境里时，从注册表 `HKCU\Environment` 读取后注入子进程 env
（避免 Git Credential Manager 弹 GUI 卡死）。cpgen.py 已内置此逻辑。

## 出题（核心命令）

```bash
cd /d/Workspace/workbuddy/cp-agent

.venv/Scripts/python.exe main.py \
    --topic <topic> --difficulty <CF分> --name <name> \
    --test-count 30 --export-after hydrooj
```

也可直接用封装脚本（自动注入凭证 + 事后校验）：

```bash
.venv/Scripts/python.exe skills/cp-agent-chuti/cpgen.py \
    --topic dp --difficulty 1500 --name my_problem
```

单题耗时 **27–86 分钟**、**15–27 万 input token**，建议后台跑。

## 零凭证模式：用 WorkBuddy 代替 API（没有 key 时走这条）

`--provider workbuddy` 不出网、不读 key：每轮 LLM 调用落成
`.llm_queue/pending/<id>.json`，等你（WorkBuddy）写入 `<id>.resp` 后继续。

```bash
# 1) 后台起进程（会阻塞在队列上等你应答）
.venv/Scripts/python.exe main.py --topic dp --difficulty 1500 --name my_problem \
    --provider workbuddy --test-count 20

# 2) 看有什么待处理 / 读请求 / 写响应
.venv/Scripts/python.exe workbuddy_llm.py list
.venv/Scripts/python.exe workbuddy_llm.py show <id>
.venv/Scripts/python.exe workbuddy_llm.py answer <id> --file my_reply.md
```

cpgen.py 也支持透传：`cpgen.py --topic dp --difficulty 1500 --name x --provider workbuddy`

应答规则：读请求里的 `system` / `messages` / `tools`，然后写响应文件，三选一

```text
纯文本                                        → 本轮结束
{"tool":"write_file","input":{...}}           → 调一个工具
{"content":[{"type":"text",...},{"type":"tool_use",...}]}  → 多段文本 + 多工具
```

要点：
- `compile_cpp` 的 `output` 要写完整相对路径 `bin/generator`，不是 `generator`。
- 驱动是**阻塞**的，一次一条；没响应就等到 `timeout_sec`（默认 3600s）后报错。
- 队列目录 `.llm_queue/` 已 gitignore。
- 这条链路能替换**所有**原本要 key 的阶段（出题、查重裁判、独立验题、难度评审），
  因为它们共用同一个 `llm_client` 入口。

## 校验清单（出完后必查）

按顺序核 `problems/<name>/result.json`：

```
1. success == true
2. failure_reason == null
3. artifacts.complete == true，inputs == outputs == test-count
4. 终端输出含「final_check 全部通过」
5. 导出包存在：problems/<name>/export/hydrooj/<时间戳>.zip
```

Hydro 包结构与 `static/Hydro格式示例.zip` 对齐（缺一个字段都算失败）：

```
<YYYYMMDD_HHMMSS>/
├── problem.json       title/time/memory/description/input/output/
│                      samples[{input,output,title,explain}]/hint/
│                      dataRange/tags[]/difficulty/file_io{input,output}
├── problem.yaml       title / tag[] / difficulty
├── problem_zh.md      中文题面
└── testdata/
    ├── config.yaml    time/memory/filename/subtasks
    ├── input.name / output.name
    └── <name>1..30.in/.out
```

## 汇报与确认（必须停一步）

出完**不要自动上传**，先向用户汇报：
- 题目名 + 难度 + 一句话题意 + 用了多少迭代/耗时
- 导出包路径

等用户明确说「上传 / 上平台 / 确认」后再继续。

## 上传平台

```bash
# 先预演
.venv/Scripts/python.exe upload.py problems/<name> --kind hydro2 --dry-run

# 正式上传（默认「未启用」）
.venv/Scripts/python.exe upload.py problems/<name> --kind hydro2

# 要启用加 --enable；上传后只读验收
.venv/Scripts/python.exe upload.py problems/<name> --kind hydro2 --enable
```

要点：
- `--kind hydro2`（新一代入口，题面按 Markdown 渲染）；老一代 hydro/xml/qduoj 会把
  HTML 当 markdown、格式会散，别用。
- 凭证优先级：命令行 > upload_config.toml > 环境变量（OJ_BASE_URL/OJ_USER/OJ_PASSWORD/OJ_KIND）。
- 凭证已迁到环境变量，upload_config.toml 里不再有明文。

## 失败处理

- **撞 `--max-iterations`**（默认 30）：把上限放宽到 45 重试一次。
  实测 greedy 1200 首次 6 分钟撞满 30 次，放宽后 13 次迭代就过 —— 是 LLM 陷入
  重试循环，不是题做不出来。仍失败再换 `--topic`。
- 失败产物归档在 `problems/failed/<name>_<时间戳>/`（空目录会被删，无产物）。
- 日志 `cp_agent.log` 会被后续运行轮转覆盖，失败后要及时取证。

## 关键坑（按概率排序）

1. 用错 python → 必须 .venv\Scripts\python.exe
2. 改完环境变量没开新终端 → 注册表写入不传播到已运行进程
3. 查重在静默降级 → 题库 problem_data/ 未下载、db 依赖未装时，不报错但没防撞题，
   对外发布的题要先补装
4. 导出格式名用 hydrooj（hydro 是别名也可用，但 --export all 不含 hydro）
5. 上传前看一眼包体积（数据规模上限会让包膨胀到几十 MB）

## 一句话流程

用户需求 → 解析 topic/difficulty/name → cpgen.py 出题 → 校验 result.json + Hydro 包
→ 汇报等确认 → upload.py --kind hydro2 上传 → oj_check 验收。
