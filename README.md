# Data Agent — 自然语言数据分析 Agent

基于自研 **Nucleus DAG** 框架的语义层数据分析 Agent。支持 Pipeline 和 ReAct 双模式，Langfuse 全链路可观测，14 标签自动诊断。

**7,237 行代码 · 31 源文件 · 144 tests · 0 failures**

## 架构

```
用户查询 → Route → Switch → Preview → Filter
                │                         │
                ├─ blocked ──→ Output     ├─ Aggregate → FilterValue → Sort
                │                         ├─ MergeDual → Sort
                └─ clarify (Interrupt)    └─ Compare → Sort
                     │                              │
                  [用户选择]                     Analyze → Output
                                                  │
                                          ╔═══════╧═══════╗
                                          ║  可观测层      ║
                                          ║ Langfuse Trace ║
                                          ║ deepeval Eval  ║
                                          ║ Diagnosis (14) ║
                                          ║ Score Bridge   ║
                                          ║ Data Flywheel  ║
                                          ╚═══════════════╝
```

## 安装与首次使用

下面的步骤适用于从 GitHub 首次下载项目的用户。推荐使用 **Python 3.10 或更高版本**、Git，以及独立虚拟环境；请勿在项目文件、终端历史或 Git 提交中写入真实 API Key。

### 1. 下载项目并创建虚拟环境

```bash
# 克隆项目并进入目录
git clone https://github.com/JX-76/data-agent.git
cd data-agent

# Windows PowerShell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux（二选一；不要与上面的 Windows 命令同时执行）
# python3 -m venv .venv
# source .venv/bin/activate
```

若 Windows PowerShell 因执行策略阻止激活，可改用 CMD：

```bat
.venv\Scripts\activate.bat
```

### 2. 安装依赖

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. 创建本地配置（不填入仓库）

复制模板后，选择以下任一种方式配置你的 DeepSeek API Key：

```bash
# macOS / Linux
cp .env.example .env

# Windows CMD
# copy .env.example .env

# Windows PowerShell
# Copy-Item .env.example .env
```

- **推荐：在网页中填写。** 先按下一步启动，再点击右上角 **“模型/API 设置”**，填写自己的 Key。
- **或写入本地 `.env`。** 打开 `.env` 后设置 `DEEPSEEK_API_KEY=你的Key`；可保留默认 `DEEPSEEK_BASE_URL=https://api.deepseek.com` 与 `DEEPSEEK_MODEL=deepseek-chat`。

`.env` 和网页保存的 `.data_agent_provider_config.json` 均被 `.gitignore` 排除；页面和接口只展示脱敏 Key。配置优先级为：**网页本地配置 > `.env` / 环境变量 > 缺省安全降级**。

### 4. 启动并打开网页

**Windows 一键启动（推荐）**：

```bat
scripts\start_local.bat
```

该脚本会在 `.env` 不存在时从 `.env.example` 创建模板，并在缺少运行依赖时安装 `requirements.txt`。启动成功后访问：<http://127.0.0.1:8000/>。

其他系统或手动启动方式：

```bash
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000
```

保持此终端运行；使用 `Ctrl+C` 停止服务。若 8000 端口已被占用，可改用其他端口，例如：

```bash
python -m uvicorn src.server:app --host 127.0.0.1 --port 8001
```

然后访问 <http://127.0.0.1:8001/>。

### 5. 完成首次查询

1. 打开网页并点击右上角 **“模型/API 设置”**；
2. 填入自己的 DeepSeek API Key，按“保存”后可使用“测试连接”确认配置；
3. 回到对话页面，输入例如：`昨天 GMV 是多少？` 或 `最近 7 天各渠道的订单量`；
4. 查看返回的分析结果；如提示缺少 Key，请检查网页设置或本地 `.env`，然后重新测试连接。

### 6. 受控项目工作区与执行流

右上角 **“工作区 / 执行流”** 提供一个受控的工程协作视图：

- 浏览、预览和下载当前项目根目录下的普通文本文件；`.env`、密钥、`.git`、虚拟环境、缓存及路径穿越请求默认拒绝。
- 查看本会话的模式切换和活动事件；模式是 `Plan`、`Act`、`Auto`、`Exit`。当前版本只记录模式与活动，**不会**因此授予模型任意 Shell、Git 或文件写入权限。
- 后端或经批准的工作流可创建“更新轮次”，把声明的输出复制为不可变版本，再通过界面下载；原文件后续改变不会影响已交付版本。

工作区根目录默认是项目目录；生产环境应显式设置 `DATA_AGENT_WORKSPACE_ROOT` 为隔离的、最小权限的工作目录。工作区活动与版本化输出当前是进程内控制面，服务重启后不会恢复；它不是生产级多租户审计存储，也未接入 GitHub/GitLab 或远程执行器。

> 本地启动默认只监听 `127.0.0.1`。生产部署不应使用本地 JSON 文件保存密钥，应接入 KMS、Secret Manager 等受管密钥服务。

## 其他使用方式

### CLI

```bash
# 健康检查
python3 src/cli.py check

# 执行查询
python3 src/cli.py query "昨天 GMV 是多少？"
python3 src/cli.py query "最近7天各渠道的订单量"

# JSON 输出
python3 src/cli.py query "昨天各品类 GMV" --format json

# 多轮会话
python3 src/cli.py session create
python3 src/cli.py session run SID "昨天 GMV 是多少？"
python3 src/cli.py session run SID "那各渠道的呢？"
python3 src/cli.py session history SID

# Mermaid 图
python3 src/cli.py mermaid -o dag.md
```

### Docker

```bash
# 启动
docker compose up -d

# 查询
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"query": "昨天 GMV 是多少？"}'

# 健康检查
curl http://localhost:8000/health

# API 文档
open http://localhost:8000/docs
```

### Python

```python
from graph_agent import run_graph

result = run_graph("昨天各渠道 GMV", use_db=True)
print(result["insight"]["insight"])  # NL 结论
print(result["diagnosis"]["overall_severity"])  # 诊断
```

## API 端点

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/health` | 健康检查 + 缓存统计 + 会话数 |
| `POST` | `/query` | 执行自然语言查询 |
| `POST` | `/query/stream` | SSE 流式查询（实时进度） |
| `POST` | `/sessions` | 创建多轮会话 |
| `GET` | `/sessions/{id}/history` | 会话历史 |
| `POST` | `/sessions/{id}/resume` | 恢复暂停的会话 |
| `DELETE` | `/sessions/{id}` | 删除会话 |
| `DELETE` | `/cache` | 清除查询缓存 |
| `GET` | `/api/workspace/tree` | 受控项目目录浏览 |
| `GET` | `/api/workspace/file` | 受控文本文件预览 |
| `GET` | `/api/workspace/download` | 受控原文件下载 |
| `GET/POST` | `/api/workspace/activity`、`/mode` | 会话活动流与模式切换 |
| `GET/POST` | `/api/workspace/rounds` | 更新轮次与版本化输出登记 |

## 配置

仓库只提交 `.env.example`，不提交 `.env` 或任何本地 API Key。`.env.example` 是自由配置模板，默认留空 DeepSeek Key：

```bash
# .env 文件，本地自行创建；不要提交到 Git
DATA_AGENT_HOST=127.0.0.1
DATA_AGENT_PORT=8000
# DATA_AGENT_AUTH=false
# DATA_AGENT_API_KEY=${DATA_AGENT_API_KEY}
DEEPSEEK_API_KEY=                    # 可留空，改用页面“模型/API 设置”填写
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
LANGFUSE_PUBLIC_KEY=                 # Langfuse（可选）
LANGFUSE_SECRET_KEY=
```

模型 API Key 的推荐配置方式：

1. GitHub 下载/克隆后复制 `.env.example` 为 `.env`；
2. 启动服务；
3. 在网页右上角 **“模型/API 设置”** 中填写自己的 Key；
4. Key 将保存在本机 `.data_agent_provider_config.json`，接口只返回脱敏值；
5. 删除本地配置后自动回退到 `.env` / 环境变量。

安全边界：`.env`、`.env.*`、`.data_agent_provider_config.json` 均被 `.gitignore` 排除。不要把自己的 API Key 写入 README、脚本、测试、Dockerfile 或任何会提交到 GitHub 的文件。

## 语义层

| 模型 | 指标 | 维度 |
|------|------|------|
| `order_detail` | gmv, order_count, aov, avg_price | date, channel, region, category |
| `user_summary` | gmv, order_count, aov | date, channel, region |
| `product_analysis` | gmv, order_count, avg_price | date, channel, category |

编辑 `semantic/*.yaml` 可扩展。

## 三套执行模式

| 模式 | 状态 | 入口 | 编排 | 适用 |
|------|------|------|------|------|
| **Pipeline (DAG)** | ✅ stable | `run_graph()` | Nucleus 12 节点 | 生产、确定性、可观测 |
| **ReAct Agent** | 🧪 experimental | `react_loop()` | LLM 自主 Tool-use | 探索性、灵活场景 |
| **多轮会话** | ✅ stable | `SessionManager` | 上下文继承 + follow-up 检测 | 对话式分析 |

> 推荐入口：生产优先使用 `run_graph()`；ReAct 主要用于实验、调试和对比评测。

## 可观测

- **Langfuse**: 全自动 trace（6 observer callbacks），无需改业务代码
- **deepeval**: 4 种自定义 Metric 门禁（Status/Routing/SQL/ToolChain）
- **诊断引擎**: 14 种标签（SQL_SYNTAX_ERROR / RESULT_EMPTY / BLOCKED_QUERY …），自动注入 output
- **评分桥**: deepeval evaluation → Langfuse score push
- **数据飞轮**: 运行记录 → 模式分析 → 趋势检测 → 策略推荐

## 项目结构

```
data-agent-mvp/
├── src/
│   ├── nucleus.py          # 自研 DAG 框架
│   ├── graph_agent.py      # DAG 12 节点编排
│   ├── dag_agent.py        # 工具集 + 路由引擎
│   ├── agent_loop.py       # ReAct Agent 循环
│   ├── analysis.py         # 统计分析层
│   ├── nlu.py              # NL 结论 + 图表推荐
│   ├── chart_renderer.py   # ECharts HTML
│   ├── diagnosis.py        # 14 标签诊断引擎
│   ├── session.py          # 多轮会话管理
│   ├── tracer.py           # Langfuse 集成
│   ├── server.py           # FastAPI 生产服务器
│   ├── cli.py              # 命令行工具
│   ├── db_executor.py      # SQLite 执行 + Mock 数据
│   ├── config.py           # API 配置
│   ├── llm_router.py       # LLM 路由
│   └── mvp_agent.py        # 向后兼容入口
├── harness/
│   ├── score_bridge.py     # deepeval → Langfuse 桥接
│   └── flywheel.py         # 数据飞轮
├── semantic/               # YAML 语义层定义
│   ├── tables.yaml
│   ├── models.yaml
│   ├── metrics.yaml
│   └── dimensions.yaml
├── tests/                  # 144 tests
├── evals/                  # Golden queries + LLM tests
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

## 测试

```bash
# 契约测试
pytest tests/test_contracts.py tests/test_context_compat.py -q

# 常规回归（不跑慢测）
pytest tests/ -q -m "not slow and not llm"

# 性能 / 集成 / 安全
pytest tests/ -q -m "perf or integration or security"

# 含 LLM ReAct 测试
pytest tests/ -q -m "llm"
```

更多测试分层说明见 `docs/TESTING.md`。

## 自研框架 vs LangGraph

| 特性 | LangGraph | Nucleus (自研) |
|------|-----------|---------------|
| 核心抽象 | StateGraph + Node + Edge | Graph + Node + Edge + Executor |
| 条件路由 | add_conditional_edges | conditional_edge(from, fn) |
| Human-in-the-loop | Interrupt | Interrupt + resume |
| 重试 | ⏳ | Node-level retry |
| 可观测性 | LangSmith | Langfuse + built-in trace + Mermaid |
| 语义层 | ❌ | ✅ YAML → CTE SQL |
| SQL 确定性 | ❌ LLM 自由生成 | ✅ 工具链 + CTE 编译 |
| 依赖 | langchain, pydantic | PyYAML only |
| 诊断 | ❌ | ✅ 14 标签 + 修复策略 |
