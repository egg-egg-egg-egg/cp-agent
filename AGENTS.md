# CP-Agent 项目指引

> WorkBuddy 在**每轮会话首轮**自动注入本文件，硬上限 **8000 字符**，超出部分**从尾部静默截断**
> （没有任何提示）。⇒ 这里只放「每次都必须遵守」的规则；长篇参考资料在 `docs/reference.md`，
> 需要时按需 Read。
>
> ⚠️ 注入是**首命中即胜**，优先级 `CODEBUDDY.md` > `.codebuddy/CODEBUDDY.md` > `AGENTS.md`。
> **不要再往仓库里放前两个文件名**，否则本文件会静默失效。

## 0. 统一策略：状态文件归谁写（必读）

**一份状态只允许有一个权威来源。** 本项目分三层，**永远从上游重算下游，禁止反向手改**：

```
线上资料库看板          ← 唯一对外真源（老师/学生看到的那份）
   ↑ sync_dashboard.py 全量重算
A 类  仓库内机器产物（进 git）：tasks.yaml、uploaded_pids.txt
   ↑ 流水线 / 上传脚本写
B 类  本地运行状态（gitignore）：problems/、config.yaml、.workbuddy/、.dashboard/
   ↑ 流水线写
```

四条规则：

1. **A 类 = 机器写，但必须人工 commit 才算数。** 跑完流水线/上传后 `git status` 里出现 A 类改动
   是**预期行为**，不是脏工作区；但必须 review 后提交——**CI 也在写 `tasks.yaml`**，不提交就会分叉。
2. **B 类 = 机器写，不进 git，不保证跨机器一致。** 凡是「需要别人或别的机器看到」的状态，
   **一律不准放进 B 类**（放进去了就等于没写）。
3. **派生产物不是输入源。** `题目平台ID映射.csv`（`--export-csv` 快照）、手维护的 md / CSV
   一律视为只读；要改先改机器产物，再重跑同步。手改快照 = 制造「我以为它自动更新了，其实没有」。
4. **给谁读决定了放哪里**：给 agent 读的 → 本文件（≤8000 字，只放必读）；给人读的 → `docs/`；
   给机器读的 → 结构化文件（yaml/json/csv），别塞进 md。

> 反面教材就在眼前：本文件原名 `AGENT.md`，不在框架的注入名单里 ⇒ 它的「agent 必读」从未生效过，
> 于是成了全仓库唯一一份**既没人校对也没人使用**的说明，里面 4 处内容早已过期（如 validator 约定
> 写反、naive 超时语义写反）。**没被读取的文档不会报错，只会静静地错下去。**

## 1. 开工前先确认分支

- **唯一主分支 = `main`**：开发、出题、上传全在这。开工第一件事 `git checkout main`。
- WorkBuddy 新 session 有时会自动落在 `feature/*` 旧分支，会读到旧文档、踩旧坑。
- 仓库已 *Leave fork network* 独立：`origin = egg-egg-egg-egg/cp-agent`，**无 upstream**，
  勿再 `git fetch myfork`。旧 main 备份在 `backup-main-before-merge`。
- **被 gitignore 的 `config.yaml` 不随分支切换**：换分支后确认它与该分支的 `config.py` 匹配
  （曾因残留 workbuddy provider 块而报 `缺少 base_url`）。

## 2. 出题 SOP

一句话：用户需求 → 解析 topic/difficulty/name → 跑出题 → 校验 → **停一步等确认** → 上传 OJ。

```bash
.venv/Scripts/python.exe skills/cp-agent-chuti/cpgen.py --topic dp --difficulty 1500 --name my_problem
# 或直接走 main.py
.venv/Scripts/python.exe main.py --topic dp --difficulty 1500 --name my_problem --export-after hydrooj
```

- Python **一律** 用 `.venv\Scripts\python.exe`（托管 python 零依赖，import 直接 `ModuleNotFoundError`）。
- 凭证走环境变量，**不要硬编码进任何文件**。

## 3. 硬约束（不可违反）

1. **出完必须停一步等用户确认**，不得自动上传。
2. 上传用 `upload.py --kind hydro2`（题面按 Markdown 渲染）；批量用 `python batch_upload.py`
   （断点续传，写 `uploaded_pids.txt`，尾部自动同步看板）。
3. **validator 必须用 `registerValidation(argc, argv)`**，从 stdin 读，且 `readInt`/`readLong`
   必须带变量名（第三个参数）。**不要**用 `registerGen + inf.init` —— 那样不产边界报告，
   门禁的「边界未触达」检查会失效。权威源是 `prompts.py`，别凭记忆写。
4. `solution.cpp` / `naive.cpp` 用标准 stdin/stdout，**不要 freopen**（否则流水线跑不动）。
5. 沙盒规则：path 必须相对、拒绝 `..`、resolve 后必须仍在 problem 目录内。

## 4. 关键坑（每条都踩过，都浪费时间）

1. **改过 generator/validator/solution/naive 源码后，必须先删 `problems/<题>/bin` 再跑流水线。**
   Windows 下 `_compile()` 只在 `bin/<name>`（无扩展名）**不存在**时才从 `.exe` 拷贝 ⇒ 不删就
   永远跑旧二进制：数据毫无变化、`data_strength` 一直报「数据太弱」，极难定位。
2. **对拍里 naive 超时 = 失败，不是通过。** `stress_test` 中 naive 超过软上限（默认 10s）或硬超时
   （15s）⇒ 返回 `success:false` + `naive_timeout:true`，要求改 `generator.cpp` 在
   `argv[3]=="stress"` 时生成小规模数据。**别把「naive 跑得慢」误读成「数据够强」。**
3. **查重是静默降级**：`problem_data/` 题库未下载时 `search_problem_db` 不报错但也**不防撞题**。
   对外发布的题先补装 db 依赖 + 下载题库。
4. **撞 `--max-iterations`（默认 30）先放宽到 45 重试**：多为 LLM 陷入重试循环，
   不是题做不出来。
5. **写自动化脚本别用 `subprocess.PIPE` 又不读**：子进程写满管道缓冲会阻塞（实测卡 39 分钟，
   看起来像驱动挂死）。重定向到文件即可。

## 5. 题库看板与门禁

- **线上数据库是看板唯一真源**：db `TKB9rZWsiuJUJJHDjkL55J`，页面 `a7WZZj0rgSUcH0SgPo2LOp`
  （公开 https://workbuddy.link/p/a7WZZj0rgSUcH0SgPo2LOp）。**数据库驱动** ⇒ 改库即时生效；
  只有页面 UI 变更才需重导 + 重新发布。
- **同步前必过门禁**（看板要给老师筛选、再给学生，脏数据不能进）：`problem_gate.py` 住在
  **仓库根**（不是 skill 目录——`.workbuddy/` 被 gitignore，CI 看不见）。11 项硬失败含
  缺文件 / validator 未过 / 样例未全过 / data_strength 未过 / **边界未触达未豁免** /
  in-out 不配对 / **单测试点 >1.2MB** / 题面非中文 / slug 名实不符。`--no-gate` 强推、
  `--strict` 把告警也当失败。**pyyaml 是硬依赖**（解析只有一条路）⇒ 必须用项目 venv 跑。
- `tasks.yaml` 的 `status`：**只有 `done` 参与出题与看板同步**；`archived` = 已归档、永久出局
  （可保留 `name` 供追溯）。⚠️ `archived` **不影响库中已有记录**（prune 仍要求 `平台PID≠无PID`）。
- 全自动链路：`download_problems.py`（下载 CI 产物 → 改名 → 门禁 → 落盘 → 同步）、
  `sync_dashboard.py`（幂等编排，含 `--prune-diff` 删能力）、`batch_upload.py`（上传后自动同步）。
- **artifact 名 ≠ 题目名**：真名取 `result.json.problem_name`（回退 `problem.yaml.slug`），
  且要**先把目录改成真名再跑门禁**，否则名实相符检查必误报。
- `平台PID` 占位符是 **`无PID`**（不预设原因：可能从未上传，也可能上传后被删）。页面判「可否点击」
  用 `/^[0-9]+$/` 全数字校验 ⇒ 以后改文案不用动 JS。
- `problems/` 下的 `example_sum` / `failed` / `ci_v7_smoke` / `smoke_wb` **不是题目**，不进库。

## 6. CI 出题（GitHub Actions）

`.github/workflows/generate.yml`，`workflow_dispatch` 手动触发。要点：

- workflow 文件必须在**默认分支 main** 上，Actions 页才会出现 "Run workflow" 按钮。
- Secrets 放 `DEEPSEEK_API_KEY`（或 OPENAI / ANTHROPIC / MIMO 之一）；CI 里执行
  `cp config.yaml.example config.yaml`，provider 的 `env_key` 直接读环境变量。
- **workbuddy provider 不能上 CI**：本地文件队列阻塞等 `.resp`，runner 上没人应答会卡到超时。
- **CI 上查重等于失效**：`problem_data/` 未入库（`problems.db` 185.8MB 超 GitHub 100MB 硬限），
  题库为空，批量出题会自撞。
- **本地与 CI 都会写 `tasks.yaml`** ⇒ 每次跑完 CI 执行
  `git fetch origin main && git merge --ff-only origin/main` 同步状态。
- **不自动上传 OJ**（见硬约束 1），上传由人本地执行。

## 7. 深入参考

架构树、14 个 Tool 全表、查重系统（多路召回 + LLM 裁判）、可选质量增强（verify.py）、
数据来源与爬取、已知问题 → 全部在 **`docs/reference.md`**。
