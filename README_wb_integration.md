# CP-Agent × WorkBuddy 接入指南

把 [cp-agent](https://github.com/tianqick/cp-agent)（算法竞赛全自动出题框架）接入 WorkBuddy 的出题流程。
**最终方案：Windows 本机 venv 部署 + WorkBuddy 技能（skill CLI 方式）。**

---

## 一、位置总览

```text
D:\Workspace\workbuddy\cp-agent\          # 项目根（本机 venv 部署）
├── .venv\Scripts\python.exe              # 专用 Python（依赖都在这里）
├── config.yaml                           # DeepSeek key 已写入 api_key（已被 gitignore）
├── main.py / agent.py / pipeline.py ...  # 官方框架
├── export.py                             # 导出：hydrooj（平台包，默认）/ luogu / polygon
├── upload.py                             # 上传到 OJ（登录 → postkey → 导入接口）
├── integrations\hustoj.py                # HUSTOJ 对接（移植自 pcoj/oj.py）
├── upload_config.toml                    # OJ 凭据（已被 gitignore；模板见 .example）
├── static\Hydro格式示例.zip              # 平台格式基准（导出严格对齐它）
├── problems\<name>\                      # 生成产物
│   └── export\hydrooj\<时间戳>.zip       # ← 最终交付物：可直接上传平台
└── README_wb_integration.md              # 本文档

C:\Users\Coder\.workbuddy\skills\cp-agent-chuti\   # WorkBuddy 技能（接入点）
├── SKILL.md                              # 出题流程指令
└── cpgen.py                              # 包装 agent.generate_problem，输出 CPSUMMARY json
```

---

## 二、依赖与前置（已就绪）

| 项 | 状态 |
|---|---|
| Python | 项目内 `.venv`（base 为 managed 3.13） |
| cp-agent 依赖 | anthropic / openai / pyyaml / requests（已装） |
| C++ 编译器 | `g++`（MinGW-w64，`D:\CodeApp\RedPanda-Cpp\MinGW64\bin`，已在 PATH） |
| LLM | DeepSeek，`deepseek-v4-pro`（key 已写入 `config.yaml`，实测连通） |
| 原题查重库 | **未启用**（需另下 ~GB 题库 + 建 FAISS 索引） |

---

## 三、用法

### 1) 直接用官方 CLI

```bat
D:\Workspace\workbuddy\cp-agent\.venv\Scripts\python.exe main.py --topic dp --difficulty 1800
D:\Workspace\workbuddy\cp-agent\.venv\Scripts\python.exe main.py --idea "给一棵树，每次删边问连通块第k大" --difficulty 2100
D:\Workspace\workbuddy\cp-agent\.venv\Scripts\python.exe main.py --topic segment_tree -d 1900 --export-after hydrooj
```

### 2) 用 WorkBuddy 技能（推荐）

在 WorkBuddy 对话里直接说自然语言即可，例如「出一道 1800 分的线段树题」。
技能会把请求翻译成：

```bat
D:\Workspace\workbuddy\cp-agent\.venv\Scripts\python.exe ^
  C:\Users\Coder\.workbuddy\skills\cp-agent-chuti\cpgen.py --topic segment_tree --difficulty 1900
```

`cpgen.py` 结束会打印 `CPSUMMARY {json}`，含 `success / problem_dir / elapsed_sec / tokens / export` 等。
默认 `--export hydrooj`（生成成功后立刻打出平台包）。

### 3) 单独导出 / 上传

```bat
rem 导出 HydroOJ 平台包（默认 file_io；--no-file-io 改标准输入输出）
.venv\Scripts\python.exe export.py problems\<name> --format hydrooj --filename gold_chain
.venv\Scripts\python.exe export.py problems\<name> --format luogu,polygon

rem 上传（先 --dry-run 看计划，不联网）
.venv\Scripts\python.exe upload.py problems\<name> --dry-run
.venv\Scripts\python.exe upload.py problems\<name> --kind hydro
```

---

## 四、接入方式说明（为什么是 skill CLI）

- **已采用：WorkBuddy 技能（skill CLI）**。技能目录 `~/.workbuddy/skills/cp-agent-chuti/` 已就位，
  WorkBuddy 会自动发现。这是最稳、无额外运行时依赖的方式。
- **未采用：MCP 服务**。项目内保留了 `mcp_server.py`（FastMCP 封装），但本机安装 `mcp` 依赖极慢、
  且默认拉到 `mcp 2.x`（`FastMCP` 已改名 `MCPServer`，API 不兼容）。如需启用：
  `.venv\Scripts\python.exe -m pip install "mcp<2"`，再 `python mcp_server.py --transport streamable-http`，
  并在 `~/.workbuddy/mcp.json` 加 `{"cp-agent-mcp":{"type":"http","url":"http://localhost:8000/mcp"}}`。
- **未采用：Docker**。Windows 上 Docker Desktop/WSL2 兼容坑较多，而本机 g++ 已可用，故直接本机部署。
  项目内保留了 `Dockerfile` / `docker-compose.yml`（已把 mcp 钉到 `mcp<2`）供需要时使用。

---

## 五、题目产物：HydroOJ 格式（最终交付物）

导出严格对齐平台示例 `static/Hydro格式示例.zip`：

```
<时间戳>/
├── problem.json          # title/time/memory/description/input/output
│                         # + samples[{input,output,title,explain}]/hint/dataRange
│                         # + tags/difficulty/file_io
├── problem.yaml          # title / tag / difficulty
├── problem_zh.md         # 中文题面（## 题目描述/输入格式/输出格式/样例/样例解释/数据范围/题解说明）
└── testdata/
    ├── config.yaml       # time: 1000ms / memory: 256m / filename: <fn> / subtasks[].cases[]
    ├── input.name        # <fn>.in（file_io 模式）
    ├── output.name       # <fn>.out（file_io 模式）
    └── <fn>1.in/.out ... # 测试点，命名 = <fn> + 序号（不补零，和示例一致）
```

生成约定与注意事项：

- `problem.md` 用 `# 标题` + `## 题目描述 / 输入格式 / 输出格式 / 样例 / 样例解释 / 数据范围(或约束) / 题解说明`
  小节组织；导出器按小节解析成 `problem.json` 字段，缺小节就不生成该字段。
- **file_io 默认开启**（示例里 `problem.json.file_io` + `input.name`/`output.name` + `config.yaml.filename`
  三者是一套）。若不需要，`--no-file-io`，或 `config.yaml` 里 `hydro.file_io: false`。
- `<fn>`（file_io 文件名/测试点前缀）优先取 `--filename`，其次 `config.yaml hydro.filename`，最后取题目 slug；
  slug 以数字结尾时自动补 `_`（`smoke_dp2` → `smoke_dp2_1.in`），显式 `--filename` 则原样使用。
- SPJ 题会把 `checker.cpp` 放进包根（HUSTOJ 侧按文件名识别，需自行确认平台是否吃）。
- 兼容别名：`--format hydro` 等价于 `hydrooj`（老脚本不用改）。

## 六、上传链路（HUSTOJ 后台导入）

`upload.py` 的流程：登录 → GET 导入页取一次性 `postkey` → multipart POST 文件 → 回显服务器响应。

| `--kind` | 接口 | 吃哪种包 |
|---|---|---|
| `hydro`（默认） | `admin/problem_import_hydro.php` | **本项目导出的 HydroOJ 包** |
| `qduoj` | `admin/problem_import_qduoj.php` | QDUOJ 导出的 json+testcase zip（本项目不产出） |
| `syzoj` / `hoj` / `tyvj` / `md` / `xml` | 对应 `problem_import_*.php` | 各自格式 |

从 HUSTOJ 源码（`trunk/web/admin/problem_import_hydro.php`）读出来的两个硬约束：

1. **必须带 `postkey`**。新版导入脚本开头 `require include/check_post_key.php`，它比对 `$_POST['postkey']`
   与 session 里的一次性令牌，不匹配直接 `post key fail...` 并 `exit(1)`；令牌由 `include/set_post_key.php`
   在渲染导入页时生成（10 位大写 HEX），且**用一次即失效**。
   原 `pcoj/oj.py` 的 `upload_QDUOJ_zip` 没有这一步，在较新版本上会失败——本项目已补上。
2. **`problem.yaml` 必须排在 zip 里 `testdata/` 之前**。导入器按 zip 条目顺序遍历：遇到 `problem.yaml`
   才 `addproblem()` 拿到 `$pid` 并建目录，之后的 `testdata/*` 才会写进 `$pid`；顺序反了数据会丢。
   `export.py` 用 `sorted(rglob)` 输出，天然满足（`upload.py --dry-run` 也会再校验一次）。

凭据解析优先级：命令行参数 > `upload_config.toml` > 环境变量（`OJ_BASE_URL/OJ_USER/OJ_PASSWORD/OJ_KIND`）。
`upload_config.toml` 已被 gitignore；模板见 `upload_config.toml.example`。

> ⚠️ 上传是对生产 OJ 的写操作。`upload.py` 默认只做 `--dry-run` 之外的显式调用；
> 在 WorkBuddy 里让技能代跑前请先确认。


## 七、注意事项

- **查重库未启用**：出题流程能跑完，但**不查重**。启用需：从百度网盘下载 `problem_data/` 放到项目根，
  再 `python -m problem_db build`（首次会拉 faiss / sentence-transformers 等重依赖）。
- **单次出题是长任务**：LLM 循环 + 编译 + 对拍，通常数分钟，建议后台运行。
- **失败产物**归档在 `problems/failed/<name>_<时间戳>/`，含 `result.json` 便于排查。
- **不要动 `config.yaml` 的 key**；该文件已被 gitignore。换 key 改 `api_key` 字段即可。
- 可换模型：`--model deepseek-flash`（更快更便宜）或 `deepseek-v4-pro`（默认，更强）。
- **跑测试**：`.venv` 里没装 pytest，用系统 Python 跑（需 pyyaml）：
  `python -m pytest -q`。当前基线 117 passed / 3 failed（3 个是既有 Windows 环境问题：
  2 个 symlink 权限、1 个路径分隔符断言，与功能无关）。

---

## 八、本机重建步骤 / 已知坑

在本机（或换机）重建：

```bat
git clone https://github.com/tianqick/cp-agent.git D:\Workspace\workbuddy\cp-agent
cd /d D:\Workspace\workbuddy\cp-agent
<managed-py> -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy config.yaml.example config.yaml   rem 然后在 deepseek.env_key 对应环境变量或 api_key 填 key
```

已在本机踩过并解决的坑（重建时注意）：

1. **pip 默认镜像坏**：本机 pip 默认源会把 `httpx` 换成非官方的 `httpx2`，且解析 `charset-normalizer` 失败。
   强制走官方源即可：`set PIP_INDEX_URL=https://pypi.org/simple` 并清空 `PIP_EXTRA_INDEX_URL`。
2. **不要装 mcp 2.x**：若日后启用 MCP，必须 `pip install "mcp<2"`（2.x 把 `FastMCP` 改名 `MCPServer`，API 不兼容）。
3. **Windows 可执行文件扩展名（已修）**：g++ 产出 `bin/xxx.exe`，而 pipeline 按 `bin/xxx` 查找。
   已在 `pipeline._compile` 里加了 Windows 分支：编译后额外复制一份无扩展名副本。
   ⚠️ 若重新 `git clone` 官方仓库，这个补丁会丢失，需要重新打上（或改用本目录）。
4. **查重库未启用**：缺少 numpy/faiss，`search_problem_db` 会干净降级（不影响出题）；启用见第五节。
5. **本机 bash 沙箱**（WorkBuddy 环境）：`dirname/tail/head/ls/rm` 等被 safe-bin 包装且损坏，
   脚本里尽量用 `python` 做文件操作、并全程用绝对路径（不要依赖 `cd`）。
6. **本地改动没有上游**：本项目是在 GitHub 原仓库基础上改的（Windows `.exe` 补丁、`export.py` 的
   hydrooj 导出器、`upload.py` + `integrations/`、`tests/test_export.py`、`tests/test_upload.py`）。
   直接重新 clone 官方仓库会全部丢失。建议 `git remote add` 一个自己的远端（或 fork）并提交，
   或至少把本目录整份备份。
7. **平台格式基准**：`static/Hydro格式示例.zip` 是导出格式的唯一事实来源。改导出器前先解压对比它；
   `tests/test_export.py` 已把该结构固化成断言，回归跑测试即可。
8. **上传接口的事实来源**：HUSTOJ 官方仓库
   `trunk/web/admin/problem_import_hydro.php`、`include/check_post_key.php`、`include/set_post_key.php`。
   平台若改版，先看这三个文件再动 `integrations/hustoj.py`。
