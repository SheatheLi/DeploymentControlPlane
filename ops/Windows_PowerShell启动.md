# Windows PowerShell｜第十二轮运行指南

前提：安装并运行 Docker Desktop（WSL2 后端）；本机已经有 Python 3.11+，且 `python -m pip` 可用。下面命令从本压缩包解压后的项目根目录执行。**使用的是独立新数据库，绝不可误连正式业务库。**

```powershell
$env:DCP_DB_PASSWORD = '请换成长随机口令'
docker compose -f ops/compose.yml up -d --wait
python -m pip install -r requirements-postgres.txt
$env:DATABASE_URL = "postgresql://dcp:$($env:DCP_DB_PASSWORD)@127.0.0.1:54327/dcp"
python -m src.pg_worker init-db
$env:DCP_ADMIN_TOKEN = '请换成不同的管理令牌'
$env:DCP_EFFECT_TOKEN = '请换成不同的副作用接收令牌'
$env:DCP_EFFECT_URL = 'http://127.0.0.1:8765'
```

然后打开三个 PowerShell 窗口，分别设置同样的环境变量（它们不会自动跨终端共享），执行：

```powershell
python -m src.webhook_sink --port 8765 --db .\receiver.db
```

```powershell
python -m src.pg_worker run
```

```powershell
python -m uvicorn src.pg_api:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。管理令牌在页面内输入；若有 Windows Defender 防火墙询问，示例只允许本机访问，不要开放公网。

**测试数据库**：

```powershell
docker compose -f ops/compose.yml exec -T postgres createdb -U dcp dcp_test
$env:DCP_TEST_PG_URL = "postgresql://dcp:$($env:DCP_DB_PASSWORD)@127.0.0.1:54327/dcp_test"
python ops/verify_postgres.py
```

`createdb` 若提示已存在，应新建一个独立空库用于测试。默认项目中的 `src.api` 是保留的历史 SQLite 技术切片；本轮新产品入口是 `src.pg_api`，切勿混淆。

**验收防误报：** 必须执行 `python ops/verify_postgres.py`；该命令将先检验 psycopg3 与真实 PostgreSQL 连接，随后运行集成测试并分析 JUnit。任何跳过、失败、断连均返回非零状态，不会宣称 PASS。不要只运行 `pytest` 后把 `2 skipped` 当成功。
