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

## 快速开始

### 本地 Web 方式（推荐给 GitHub 下载用户）

```bat
REM Windows：首次运行会自动从 .env.example 复制 .env（不含任何真实密钥）
scripts\start_local.bat
```

启动后打开：`http://127.0.0.1:8000/`。

首次使用请点击页面右上角 **“模型/API 设置”**，填写你自己的 DeepSeek API Key。Key 只会保存在当前机器的本地文件 `.data_agent_provider_config.json`，该文件已被 `.gitignore` 排除，不会进入 Git 提交；后端接口和页面也不会回显明文 Key。

也可以手工配置：

```bash
# 1) 复制模板。模板只保留空槽位，不包含真实 API Key
cp .env.example .env

# 2) 在 .env 中填入自己的值，或保持 DEEPSEEK_API_KEY 为空并使用页面配置
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 3) 启动服务
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000
```

配置优先级：页面保存的本地 Provider 配置 > `.env` / 环境变量 > 缺省安全降级。生产部署不应直接使用本地 JSON 存储密钥，应替换为 KMS / Secret Manager 等托管方案。

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

## 配置

仓库只提交 `.env.example`，不提交 `.env` 或任何本地 API Key。`.env.example` 是自由配置模板，默认留空 DeepSeek Key：

```bash
# .env 文件，本地自行创建；不要提交到 Git
DATA_AGENT_HOST=127.0.0.1
DATA_AGENT_PORT=8000
# DATA_AGENT_AUTH=false
# DATA_AGENT_API_KEY=replace-with-your-own-local-api-key
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
