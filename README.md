# DeploymentControlPlane

[**在线交互展示**](https://sheatheli.github.io/DeploymentControlPlane/) · [**工程证据与验证索引**](docs/verification-map.md) · [![Smoke CI](https://github.com/SheatheLi/DeploymentControlPlane/actions/workflows/smoke.yml/badge.svg)](https://github.com/SheatheLi/DeploymentControlPlane/actions/workflows/smoke.yml)

> 一个具有API、Worker、持久化状态与Web界面的发布控制面研究平台。重点不是“HTTP请求返回成功”，而是**发布意图、外部副作用和实际健康状态如何在重试、宕机与并发竞争下保持一致**。

**工程状态：** 可运行的本地系统原型，含SQLite WAL、HTTP子进程对照，以及需要额外环境的PostgreSQL/Docker验证路径；不等于已部署的生产Kubernetes发布平台。

## 系统行为与边界

```text
用户 / Web UI
      │ 请求发布
      ▼
API & 持久化事务 ──> Desired / Active / Observed
      │
      └── Outbox ──> Worker / Lease / Fencing
                             │
                             ▼
                       外部发布效果
                             │
                             ▼
                    健康回读与状态核验
```

- **状态语义：** Desired记录意图，Active记录选定版本，Observed依据外部观测更新；三者不可互相冒充。
- **副作用可靠性：** Outbox承载提交与执行之间的异步边界，处理重试和重复投递。
- **并发安全：** Lease和Fencing约束执行权，旧Worker不能用迟到结果覆盖新代际。
- **故障恢复：** 检查恢复后是否仍能保持单调状态与效果幂等。

## 本地快速路径

```bash
python -m pip install -r requirements.txt
python -m pytest -q tests/test_control.py tests/test_effect_outbox.py tests/test_lease_claim.py
python benchmarks/compare.py
```

需要启动完整产品或接入外部数据库时，请严格执行[`ops/run-product.md`](ops/run-product.md)、[`ops/compose.yml`](ops/compose.yml)与[`ops/verify_postgres.py`](ops/verify_postgres.py)的说明。SQLite本地验证**不能**替代PostgreSQL、Docker或真实远端发布环境的验证。

## 源码导览

| 文件 | 责任 |
|---|---|
| [`src/api.py`](src/api.py) · [`src/control.py`](src/control.py) | HTTP接口、发布状态与版本管理 |
| [`src/effect_outbox.py`](src/effect_outbox.py) · [`src/recovery.py`](src/recovery.py) | 异步效果、重试与崩溃恢复 |
| [`src/lease_claim.py`](src/lease_claim.py) · [`src/pg_control.py`](src/pg_control.py) | 认领权、代际围栏与PostgreSQL逻辑 |
| [`web/index.html`](web/index.html) | 本地UI |
| [`tests/`](tests/) · [`evidence/`](evidence/) | 协议测试与数据收据 |

## 已记录的关键实验

受控SQLite竞争中，旧更新路径可能把执行代际从**3错误回退为2**；原子条件更新拒绝旧执行者，保持代际3。另一个故障窗口实验中，重复效果尝试最终保留**1项唯一外部效果**。这些是特定协议合同和复现工况的证据，**不是线上集群SLO**。

结果应连同[`evidence/`](evidence/)中的原始输入、运行条件与分母阅读。

## 可视化入口

[交互式发布过程与状态竞争展示说明](docs/README.md) · [机制证据图（SVG）](docs/assets/figures/DeploymentControlPlane.svg)

**授权状态：** 仓库根目录暂无LICENSE；公开前应决定代码授权方式。
