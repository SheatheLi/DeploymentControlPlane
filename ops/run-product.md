# Deployment Control Plane｜真实 PostgreSQL 模式运行

> **需要的环境**：Docker Compose（或独立 PostgreSQL 14+）、Python 3.11+、psycopg3、Node/tsc（只在改动前端后重新编译）。本次生成环境没有 PostgreSQL Server、Docker 或 psycopg，下面的原生数据库验收尚未执行。

## 1. 准备专用数据库

在实际具有 Docker Compose 的机器上运行：

```bash
export DCP_DB_PASSWORD='change_this_to_a_long_random_password'
docker compose -f ops/compose.yml up -d --wait
python -m pip install -r requirements-postgres.txt
export DATABASE_URL="postgresql://dcp:${DCP_DB_PASSWORD}@127.0.0.1:54327/dcp"
python -m src.pg_worker init-db
```

不能使用现有旧项目的 `CONTROL_DB` 变量替代 PostgreSQL DSN；不能把 SQLite 测试绿灯认作 PostgreSQL 验收通过。

## 2. 运行用户端、后台 Worker 和外部接收端（三个不同进程）

```bash
export DCP_ADMIN_TOKEN='set_a_long_random_local_admin_token'
export DCP_EFFECT_TOKEN='set_an_independent_random_effect_token'
export DCP_EFFECT_URL='http://127.0.0.1:8765'

# 终端 A：外部接收端（独立的持久化去重系统）
python -m src.webhook_sink --port 8765 --db ./receiver.db

# 终端 B：独立后台 Worker（从真实 PostgreSQL claim/health/promote/dispatch）
python -m src.pg_worker run

# 终端 C：React/TS 控制台 + PG API
uvicorn src.pg_api:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`，输入管理令牌，提交 `service=demo` 和 `version=v1`。提交时生成持久化的 `REQUESTED` 记录；Worker 认领后启动固定本地服务，读取 `/health` 校验版本和随机 nonce，通过带租约 Fence 的事务提交 `active` 与 Outbox；独立外部接收进程对稳定的 `effect_id` 去重，随后 Worker ACK。刷新页面可以看到状态与尝试次数。

**不支持用户上传代码、Docker 镜像或任意 Shell 命令作为待执行发布物。** 固定本地 HTTP 子进程仅用于证明真实进程和健康门控，不是 Kubernetes 容器发布。

## 3. 真实数据库独立验证

使用单独的、可丢弃的测试数据库：

```bash
export DCP_TEST_PG_URL='postgresql://dcp:...@127.0.0.1:54327/dcp_test'
python ops/verify_postgres.py  # Windows PowerShell / Linux 均可；缺驱动/服务或出现 SKIP 会失败
# Linux 也可使用 ./ops/verify-postgres.sh
```

该脚本要求真 PostgreSQL，缺失时不会使用其他数据库代替。测试包含请求幂等、真实健康门控、Promote/Outbox 同事务、Worker 崩溃窗口的 ACK 缺失、超时重领、接收端去重和陈旧 Lease 拒绝。

## 已知运行和安全边界

- 此产品只应绑定 `127.0.0.1`；管理 API 使用独立 token，当前无 TLS/OIDC/RBAC/生产机密管理。
- 发布的子进程独立存在；示例没有安全的容器式生命周期清理、回滚与外部流量切换，**不能公开部署或用于生产发布**。
- 独立接收端用 SQLite 存储自己的幂等结果，它不是控制数据库的替代品。跨系统 Exactly-once 不成立；ACK 前崩溃只能通过下游幂等实现最终效果去重。
- React 16.0 为当前离线环境已有的旧版可执行运行时（已附 NOTICE）；仅用于脱网验证。对外发布前必须升级到仍受维护的 React 并执行生产安全审计。
- 若 Worker 在创建本地发布子进程后、数据库 Promote 前异常退出，可能留下孤儿进程；当前没有完善的安全接管/回收协议，不能宣称已解决。
- 本轮源码包含 PostgreSQL 模式，但生成环境无法运行真 PostgreSQL，故端到端数据库验收为 **NOT RUN**。
