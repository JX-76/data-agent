# 企业数据源接入（受控最小实现）

## 已实现范围

本实现为本地/自托管 Data Agent 增加了**受控企业数据源配置、只读连通性验证和允许表 Schema 探查**：

- UI：右上角 **企业数据源**；
- API：`/api/data-sources/{capabilities,config,test,schema-probe,disconnect}`；
- 已支持连接器：PostgreSQL（`psycopg2`）、MySQL/MariaDB（`mysql.connector`）；
- SQL Server、Oracle、Custom 仅可保存声明性配置，调用测试会明确返回 `connector_not_implemented`；
- 连接测试只运行 `SELECT 1`；Schema 探查只读取 `information_schema.columns`，只针对 allowlist 表/视图，绝不返回业务行数据；
- 真实连接默认 fail-closed，只有部署人员显式设置审批环境变量后才会尝试连接。

这不是把企业库自动接入分析执行链。当前 release/query 的既有数据源与证据控制面没有被替换；将该数据源纳入受 Evidence/permission/SQL-preflight 治理的查询执行，是后续独立 Phase。

## 凭据与配置边界

配置文件只保存非敏感连接元数据（默认位于项目根目录 `.data_agent_enterprise_data_source.json`，可通过 `DATA_AGENT_ENTERPRISE_DATA_SOURCE_CONFIG` 指定路径）。不得把它提交到 Git。

密码不通过 UI、HTTP body、日志或返回值传递。仅允许凭据引用：

```text
env:DATA_AGENT_ENTERPRISE_DB_PASSWORD
```

部署时设置：

```powershell
$env:DATA_AGENT_ENTERPRISE_DB_PASSWORD = '仅在部署环境配置的密码'
$env:DATA_AGENT_APPROVE_REAL_CONNECTION = 'true'
```

`DATA_AGENT_APPROVE_REAL_CONNECTION=true` 是单独的部署审批开关；未设置时，`/test` 和 `/schema-probe` 返回 `real_connection_not_approved`，不会加载驱动或发起网络连接。

## 最小配置示例

```json
{
  "db_type": "postgresql",
  "host": "readonly-db.company.internal",
  "port": 5432,
  "database": "analytics",
  "schema": "public",
  "username": "data_agent_ro",
  "credential_reference": "env:DATA_AGENT_ENTERPRISE_DB_PASSWORD",
  "ssl_mode": "require",
  "allowed_tables": ["orders_view", "stores_dim"]
}
```

要求：

1. 使用专用只读数据库账号，并在数据库侧授予最小权限；
2. 保持 TLS（禁止 `ssl_mode=disable`）；生产环境建议 `verify-ca` 或 `verify-full`，并由驱动/运行环境提供 CA 配置；
3. 在数据库侧实施 tenant RLS/视图隔离；当前 `tenant_scope_mode=external_rls_required` 只是声明和审计字段，不能替代数据库权限；
4. `allowed_tables` 必须为允许访问的表或视图白名单；
5. 连接成功不代表数据查询能力已经开放，也不代表数据已通过业务证据校验。

## API 合约

- `GET /api/data-sources/capabilities`：声明可用和已安装的连接器；
- `GET /api/data-sources/config`：脱敏配置，不含 credential reference；
- `PUT /api/data-sources/config`：保存非敏感配置，输入敏感字段直接拒绝；
- `POST /api/data-sources/test`：审批后执行 `SELECT 1`；
- `POST /api/data-sources/schema-probe`：审批后仅探查 allowlist Schema；
- `POST /api/data-sources/disconnect`：清除进程内 active 标记。

所有响应包含 contract、状态和 audit event 名称。连接异常经过脱敏；不会返回 DSN、密码、原始驱动异常或网络拓扑详情。

## 安装与运行

安装目标连接器（按实际数据库二选一）：

```powershell
python -m pip install psycopg2-binary
# 或
python -m pip install mysql-connector-python
```

启动 API 后访问：

```powershell
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000
```

然后使用 `http://127.0.0.1:8000/` 的“企业数据源”面板保存配置、执行受审批的连接测试与 Schema 探查。

## 质量门禁

```powershell
python scripts/run_enterprise_data_source_gate.py
```

测试覆盖：无明文密码输入、TLS fail-closed、无审批不连库、`SELECT 1` 探活、允许表 Schema 限制、未实现 adapter 不发起连接。

## 未覆盖项与生产前依赖

- 没有云 Secret Manager/Vault/KMS adapter；当前只提供环境变量引用；
- 没有连接池、证书轮换、网络策略编排或真实数据库健康探针；
- 没有 SQL Server/Oracle 驱动 adapter；
- 没有将该配置直接接入 Agent 查询执行，避免绕过现有 `permission_policy`、SQL preflight、EvidenceBus 与 final-output gate；
- 上线前需要平台团队提供 Secret 注入、TLS CA、私网/VPN/防火墙、只读用户、数据库 RLS、审计汇聚和变更审批。
