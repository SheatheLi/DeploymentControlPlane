# 验证路线｜发布状态、Outbox与故障窗口

此页把本地产品原型中可检验的协议分别关联到源码、测试和数据；不代表Kubernetes生产部署已完成。

## 三种不能互相冒充的状态

- **Desired**：用户/服务期望的版本，不等于已经执行。
- **Active**：被控制面选定或记录的版本，不等于外部服务已经健康。
- **Observed**：从外部观察到的事实，不应被单纯的API成功响应替代。

## 故障与复核入口

| 必须回答的问题 | 代码／测试 | 已有数据 |
|---|---|---|
| 事务提交后外部副作用是否可能丢失 | `src/effect_outbox.py`、`tests/test_effect_outbox.py` | `evidence/workflow_outbox_crash_window.json` |
| 过期Worker能否覆盖新代际 | `src/lease_claim.py`、`tests/test_lease_claim.py` | `evidence/fencing_race_20261014.json` |
| 重试是否可能重复产生实际效果 | `src/recovery.py`、`src/control.py` | `evidence/workflow_outbox_crash_window.json` |
| SQLite与PostgreSQL的证据边界何在 | `ops/run-product.md`、`ops/verify_postgres.py` | `evidence/环境与运行命令.json` |

## 最短复核

```bash
python -m pip install -r requirements.txt
python -m pytest -q tests/test_control.py tests/test_effect_outbox.py tests/test_lease_claim.py
```

阅读[并发代际回退修复与线性化](并发代际回退修复与线性化.md)可了解一个受控竞争样本如何暴露旧代际覆盖风险。注意SQLite状态竞争结果不是数据库隔离级别的跨引擎证明；更不能把成功完成Smoke测试等同于公网可用性或集群级SLO。
