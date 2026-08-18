# EchoMind 业务数据库说明

## 目标

为 Phase 1 的单 Agent runtime 和 tool calling 提供一个最小可用的结构化业务数据层。

这层数据库不替代现有的 `Redis` 和 `ChromaDB`：

- `Redis`：短期会话状态与运行时状态
- `ChromaDB`：知识检索与长期语义记忆
- `SQLite`：订单、账号、退款规则、故障、工单等结构化业务事实

## 当前目录结构

```text
data/business/
  schema.sql
  seed.sql
  echomind.db

integrations/business_db/
  __init__.py
  db.py
  init_db.py
  demo_queries.py
```

## 表设计

当前包含 6 张表：

1. `customers`
2. `accounts`
3. `orders`
4. `refund_policies`
5. `incidents`
6. `handoff_tickets`

这些表分别支撑：

- `account_status_lookup`
- `order_lookup`
- `refund_policy_check`
- `incident_lookup`
- `create_handoff_ticket`

## 初始化数据库

由于当前环境不依赖 `sqlite3` 命令行，直接使用 Python 内置 `sqlite3` 模块初始化：

```bash
python3 -m integrations.business_db.init_db
```

如果只想建表、不导入 seed 数据：

```bash
python3 -m integrations.business_db.init_db --skip-seed
```

默认数据库文件会生成到：

- `data/business/echomind.db`

## 快速验证

初始化后可以运行：

```bash
python3 -m integrations.business_db.demo_queries
```

它会打印几类典型查询结果：

- 账号状态
- 订单信息
- 退款策略
- 故障公告

## 代码中如何使用

```python
from integrations.business_db import BusinessDB

db = BusinessDB()
account = db.get_account_status(account_id="ACC-ZM-01")
order = db.get_order("ORD-20260728-1001")
policy = db.match_refund_policy("EchoMind Pro", "yearly", "2026-07-29T00:00:00Z")
incidents = db.search_incidents(category="login")
```

## 当前 seed 数据覆盖的场景

当前种子数据覆盖：

- 正常账号
- 权限异常账号
- 受限 / 锁定 / 暂停账号
- 年付 / 月付 / 季付订单
- 可退款 / 需人工审核 / 不支持自动退款的策略
- 登录故障 / 知识库导入延迟 / API 故障 / 账单异常
- 已存在的人工工单

## Phase 1 中如何使用这层数据库

建议后续工具层这样接：

- `account_status_lookup`
  调 `BusinessDB.get_account_status()`
- `order_lookup`
  调 `BusinessDB.get_order()` 或 `list_orders_for_customer()`
- `refund_policy_check`
  调 `BusinessDB.match_refund_policy()`
- `incident_lookup`
  调 `BusinessDB.search_incidents()`
- `create_handoff_ticket`
  调 `BusinessDB.create_handoff_ticket()`

## 当前阶段边界

这层数据库目前是最小业务后端，目标是支撑 Agent 运行和评测，不是完整业务系统。

因此当前不处理：

- 支付流水明细
- 真正的退款执行
- 审批流
- 多组织租户隔离
- 工单分派自动化

这些能力可以在后续阶段再扩展。
