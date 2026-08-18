# EchoMind 电商售后二期 Phase 2 售后工具体系升级文档

## 1. 文档目标

本文档用于拆解 EchoMind 电商售后二期的 `Phase 2`：

> 售后工具体系升级

在当前项目中，Agent 已经具备：

- 基于 `LangGraph` 的受控单 Agent loop
- 基础数据库工具调用
- 知识库工具调用
- 人工工单写库能力

但如果要把项目升级成更真实的电商售后 Agent 平台，下一步的关键不是继续调 prompt，而是：

> 让 Agent 拥有更完整、更真实的售后工具能力。

本文档重点回答以下问题：

1. 当前系统已经有哪些工具
2. 当前工具体系的边界是什么
3. 电商售后场景下还需要新增哪些工具
4. 每个工具会读取或写入数据库中的哪些表和字段
5. 每个工具的输入输出应该如何设计
6. 工具开发顺序应该如何安排

---

## 2. 当前工具体系现状

### 2.1 当前已接入工具

目前 EchoMind 已接入的核心工具分为两类：

#### 知识工具

1. `knowledge_search`

作用：

- 检索知识库
- 用于 FAQ、规则解释、产品说明、操作文档回答

当前来源：

- `ChromaDB`
- 注册位置：`api/main.py`

#### 业务数据库工具

1. `account_status_lookup`
2. `order_lookup`
3. `refund_policy_check`
4. `incident_lookup`
5. `create_handoff_ticket`

这些工具的第一版 handler 位于：

- `mcp/business_tool_handlers.py`

注册入口位于：

- `api/main.py`

### 2.2 当前已接入工具与数据库映射

#### `account_status_lookup`

作用：

- 查询账号状态
- 查询账号角色与权限
- 查询锁定 / 受限原因

当前使用表：

- `accounts`
- `customers`

当前主要读取字段：

- `accounts.account_id`
- `accounts.role`
- `accounts.account_status`
- `accounts.permissions_json`
- `accounts.last_login_at`
- `accounts.locked_reason`
- `customers.customer_code`
- `customers.name`
- `customers.email`
- `customers.tier`
- `customers.status`

#### `order_lookup`

作用：

- 查询单笔订单
- 或按客户编号查询最近订单列表

当前使用表：

- `orders`
- `customers`

当前主要读取字段：

- `orders.order_no`
- `orders.product_name`
- `orders.plan_type`
- `orders.amount`
- `orders.currency`
- `orders.payment_status`
- `orders.order_status`
- `orders.created_at`
- `orders.paid_at`
- `customers.customer_code`
- `customers.name`

#### `refund_policy_check`

作用：

- 查询退款规则
- 基于规则给出基础资格判断

当前使用表：

- `refund_policies`

当前主要读取字段：

- `refund_policies.policy_code`
- `refund_policies.product_name`
- `refund_policies.plan_type`
- `refund_policies.refund_window_hours`
- `refund_policies.usage_limit_percent`
- `refund_policies.allow_refund`
- `refund_policies.manual_review_required`
- `refund_policies.policy_text`
- `refund_policies.effective_from`
- `refund_policies.effective_to`

#### `incident_lookup`

作用：

- 查询已知故障或服务异常

当前使用表：

- `incidents`

当前主要读取字段：

- `incidents.incident_code`
- `incidents.title`
- `incidents.category`
- `incidents.severity`
- `incidents.status`
- `incidents.affected_scope`
- `incidents.summary`
- `incidents.started_at`
- `incidents.resolved_at`
- `incidents.updated_at`

#### `create_handoff_ticket`

作用：

- 转人工
- 创建客服工单

当前使用表：

- `handoff_tickets`
- `customers`

当前主要写入字段：

- `handoff_tickets.ticket_no`
- `handoff_tickets.customer_id`
- `handoff_tickets.conv_id`
- `handoff_tickets.source_intent`
- `handoff_tickets.priority`
- `handoff_tickets.status`
- `handoff_tickets.reason`
- `handoff_tickets.summary`
- `handoff_tickets.assigned_queue`
- `handoff_tickets.created_at`
- `handoff_tickets.updated_at`

### 2.3 当前工具体系的边界

现有工具已经能支撑一个最小客服 Agent，但还存在明显边界：

1. 订单信息粒度不够
- 只能查订单头，不能查订单项
- 无法知道用户具体买了什么商品

2. 没有物流能力
- 无法回答包裹在哪里
- 无法处理超时、丢件、签收异常

3. 没有售后申请能力
- 只能查规则，不能提交退款申请
- 不能提交退货 / 换货申请

4. 没有补偿能力
- 不能发券
- 不能记录安抚赔付

5. 规则和事实仍然容易混淆
- 只有 `refund_policy_check` 时，Agent 很容易把规则判断说成订单事实判断

所以二期 Phase 2 的核心目标是：

> 从“解释型客服工具”升级到“查询 + 动作执行型售后工具体系”。

---

## 3. Phase 2 总体目标

### 3.1 业务目标

让 Agent 支持以下电商售后关键能力：

- 查询订单详情
- 查询订单商品项
- 查询物流状态
- 查询支付尝试与重复扣费风险
- 查询退款申请进度
- 提交退款申请
- 提交退货申请
- 提交换货申请
- 发放补偿券
- 升级人工处理

### 3.2 技术目标

- 让每个售后工具都有清晰的输入输出 schema
- 所有动作型工具都落库
- 工具输出能被 runtime 稳定消费
- 工具返回结果能用于前端展示和评测

### 3.3 设计原则

1. 一个工具只做一类明确动作
2. 查询工具优先返回结构化事实
3. 动作工具必须返回编号，如：
- `refund_no`
- `return_no`
- `exchange_no`
- `ticket_no`
- `compensation_no`

4. 工具结果必须适合被 Agent 直接引用
5. 高风险动作要为后续人工审核预留字段

---

## 4. Phase 2 工具分层设计

建议把二期工具分为 3 层：

### 4.1 基础事实查询工具

作用：

- 查询结构化业务事实

包含：

- `account_status_lookup`
- `order_lookup`
- `order_items_lookup`
- `shipment_tracking_lookup`
- `payment_attempt_lookup`
- `refund_request_lookup`
- `return_request_lookup`
- `exchange_request_lookup`
- `incident_lookup`

### 4.2 规则判断工具

作用：

- 基于规则和业务事实做资格判断

包含：

- `refund_policy_check`
- `refund_eligibility_check`

### 4.3 动作执行工具

作用：

- 执行售后动作并落库

包含：

- `create_refund_request`
- `create_return_request`
- `create_exchange_request`
- `issue_coupon_compensation`
- `create_handoff_ticket`

---

## 5. 当前已有工具清单与升级建议

### 5.1 `account_status_lookup`

#### 当前状态

已存在，可继续复用。

#### 建议升级点

结合 `schema_v2.sql`，建议增加读取字段：

- `accounts.security_review_status`
- `accounts.failed_login_count`
- `accounts.unlock_available_at`
- `customers.risk_level`
- `customers.preferred_channel`

#### 升级后的价值

可以支持更丰富的账号场景：

- 账号临时锁定
- 安全审查中
- 风控等级判断
- 自动解锁时间说明

---

### 5.2 `order_lookup`

#### 当前状态

已存在，但还只是订单头查询。

#### 建议升级点

基于 `orders` 表新增读取字段：

- `orders.product_type`
- `orders.delivery_type`
- `orders.channel`
- `orders.consignee_name`
- `orders.consignee_phone`
- `orders.province`
- `orders.city`
- `orders.district`
- `orders.detail_address`
- `orders.order_remark`
- `orders.updated_at`

#### 升级后的价值

可以支持：

- 数字商品与实物商品区分
- 收货信息核对
- 渠道问题定位
- 物流售后前置信息准备

---

### 5.3 `refund_policy_check`

#### 当前状态

已存在，用来解释退款规则。

#### 当前局限

它只能回答：

- “这个产品规则上支不支持退款”

但它不能回答：

- “这笔具体订单当前是否可申请退款”

#### 建议升级点

增加读取字段：

- `refund_policies.product_type`
- `refund_policies.review_reason_template`
- `refund_policies.refund_method`
- `refund_policies.policy_category`

#### 保留定位

这个工具继续保留为“规则判断工具”，不要让它承担真实订单资格核验的全部职责。

---

### 5.4 `incident_lookup`

#### 当前状态

已存在。

#### 建议升级点

除了当前 `incidents`，后续可以增加对：

- `warehouse_incidents`

的联合查询能力，或者单独新增一个仓储物流异常工具。

---

### 5.5 `create_handoff_ticket`

#### 当前状态

已存在。

#### 建议升级点

结合 `handoff_tickets` 新字段写入：

- `source_ref_type`
- `source_ref_no`
- `sla_level`
- `owner_agent`

#### 升级后的价值

工单不再只是“一个泛化升级记录”，而是能明确知道：

- 是哪类售后问题
- 来自哪一笔订单或申请
- 应该进入哪个人工队列

---

## 6. Phase 2 需要新增的核心工具

下面是二期建议新增的第一批售后工具。

---

## 7. `order_items_lookup`

### 7.1 工具作用

查询订单中的商品项。

### 7.2 为什么需要

没有这类工具，Agent 只能知道“这是一笔订单”，却不知道：

- 具体商品是什么
- 哪个商品可退
- 哪个商品可换
- 某个商品是否已经进入售后流程

### 7.3 读取表

- `orders`
- `order_items`

### 7.4 主要读取字段

从 `orders` 读取：

- `orders.order_no`
- `orders.customer_id`

从 `order_items` 读取：

- `order_items.sku_id`
- `order_items.sku_name`
- `order_items.category`
- `order_items.quantity`
- `order_items.unit_price`
- `order_items.discount_amount`
- `order_items.final_amount`
- `order_items.item_status`
- `order_items.fulfillment_status`
- `order_items.returnable`
- `order_items.exchangeable`

### 7.5 输入建议

- `order_no`

### 7.6 输出建议

- `found`
- `order_no`
- `item_count`
- `items[]`

---

## 8. `shipment_tracking_lookup`

### 8.1 工具作用

查询订单物流与履约状态。

### 8.2 为什么需要

这是电商售后最重要的工具之一，用于回答：

- 包裹发货了吗
- 现在到哪里了
- 是否签收
- 是否有延迟、丢件、拒收等异常

### 8.3 读取表

- `orders`
- `shipments`
- 可选补充：`warehouse_incidents`

### 8.4 主要读取字段

从 `orders` 读取：

- `orders.order_no`
- `orders.delivery_type`
- `orders.order_status`

从 `shipments` 读取：

- `shipments.shipment_no`
- `shipments.tracking_no`
- `shipments.carrier`
- `shipments.shipment_status`
- `shipments.last_checkpoint`
- `shipments.last_checkpoint_at`
- `shipments.signed_at`
- `shipments.receiver_name`
- `shipments.exception_type`
- `shipments.exception_summary`

从 `warehouse_incidents` 可选读取：

- `warehouse_incidents.incident_no`
- `warehouse_incidents.incident_type`
- `warehouse_incidents.status`
- `warehouse_incidents.summary`

### 8.5 输入建议

- `order_no`
- 或 `tracking_no`

### 8.6 输出建议

- `found`
- `order_no`
- `shipment_status`
- `carrier`
- `tracking_no`
- `last_checkpoint`
- `exception_type`
- `exception_summary`

---

## 9. `payment_attempt_lookup`

### 9.1 工具作用

查询某订单的支付尝试记录，用于解释：

- 重复扣费
- 支付失败
- 重试扣款

### 9.2 读取表

- `orders`
- `payment_attempts`

### 9.3 主要读取字段

- `payment_attempts.attempt_no`
- `payment_attempts.payment_channel`
- `payment_attempts.attempt_status`
- `payment_attempts.amount`
- `payment_attempts.currency`
- `payment_attempts.transaction_id`
- `payment_attempts.failure_reason`
- `payment_attempts.is_duplicate_risk`
- `payment_attempts.created_at`

### 9.4 输入建议

- `order_no`

### 9.5 输出建议

- `found`
- `order_no`
- `attempt_count`
- `duplicate_risk`
- `attempts[]`

---

## 10. `refund_request_lookup`

### 10.1 工具作用

查询退款申请状态。

### 10.2 为什么需要

用户常问的不只是“能不能退款”，而是：

- 我提交的退款审核到哪里了
- 是否已通过
- 什么时候到账

### 10.3 读取表

- `refund_requests`
- `orders`
- `customers`

### 10.4 主要读取字段

- `refund_requests.refund_no`
- `refund_requests.refund_reason`
- `refund_requests.requested_amount`
- `refund_requests.approved_amount`
- `refund_requests.refund_status`
- `refund_requests.review_required`
- `refund_requests.review_status`
- `refund_requests.review_reason`
- `refund_requests.refund_channel`
- `refund_requests.requested_at`
- `refund_requests.reviewed_at`
- `refund_requests.completed_at`
- `orders.order_no`
- `customers.customer_code`

### 10.5 输入建议

- `refund_no`
- 或 `order_no`

### 10.6 输出建议

- `found`
- `refund_no`
- `order_no`
- `refund_status`
- `review_status`
- `requested_amount`
- `approved_amount`

---

## 11. `return_request_lookup`

### 11.1 工具作用

查询退货申请进度。

### 11.2 读取表

- `return_requests`
- `order_items`
- `orders`

### 11.3 主要读取字段

- `return_requests.return_no`
- `return_requests.return_reason`
- `return_requests.return_status`
- `return_requests.pickup_required`
- `return_requests.warehouse_received_at`
- `return_requests.refund_linked`
- `order_items.sku_name`
- `orders.order_no`

### 11.4 输入建议

- `return_no`
- 或 `order_no`

---

## 12. `exchange_request_lookup`

### 12.1 工具作用

查询换货申请进度。

### 12.2 读取表

- `exchange_requests`
- `order_items`
- `orders`

### 12.3 主要读取字段

- `exchange_requests.exchange_no`
- `exchange_requests.exchange_reason`
- `exchange_requests.exchange_status`
- `exchange_requests.new_shipment_no`
- `order_items.sku_name`
- `orders.order_no`

### 12.4 输入建议

- `exchange_no`
- 或 `order_no`

---

## 13. `refund_eligibility_check`

### 13.1 工具作用

基于“订单事实 + 退款规则”判断一笔具体订单是否具备退款资格。

### 13.2 为什么必须新增

这是二期非常关键的工具，因为它能解决当前系统的一个核心问题：

> 不能再只用 `refund_policy_check` 解释规则，却把规则判断说成订单事实判断。

### 13.3 读取表

- `orders`
- `order_items`
- `refund_policies`
- `payment_attempts`
- `refund_requests`

### 13.4 主要读取字段

从 `orders` 读取：

- `orders.order_no`
- `orders.product_name`
- `orders.product_type`
- `orders.plan_type`
- `orders.payment_status`
- `orders.order_status`
- `orders.created_at`
- `orders.paid_at`

从 `order_items` 读取：

- `order_items.item_status`
- `order_items.fulfillment_status`

从 `refund_policies` 读取：

- `refund_policies.refund_window_hours`
- `refund_policies.usage_limit_percent`
- `refund_policies.allow_refund`
- `refund_policies.manual_review_required`
- `refund_policies.review_reason_template`

从 `payment_attempts` 读取：

- `payment_attempts.is_duplicate_risk`
- `payment_attempts.attempt_status`

从 `refund_requests` 读取：

- 是否已有退款申请

### 13.5 输入建议

- `order_no`
- 可选：`usage_percent`

### 13.6 输出建议

- `eligible`
- `order_found`
- `policy_code`
- `allow_refund`
- `manual_review_required`
- `already_requested`
- `duplicate_risk`
- `reason_codes[]`
- `summary`

---

## 14. `create_refund_request`

### 14.1 工具作用

提交退款申请单。

### 14.2 为什么需要

只有这一步做出来，Agent 才真正具备“动作执行能力”，而不只是解释规则。

### 14.3 写入表

- `refund_requests`
- 可选补充：`after_sales_tickets`
- 可选补充：`agent_audit_logs`

### 14.4 主要写入字段

写入 `refund_requests`：

- `refund_requests.refund_no`
- `refund_requests.order_id`
- `refund_requests.customer_id`
- `refund_requests.refund_reason`
- `refund_requests.requested_amount`
- `refund_requests.refund_status`
- `refund_requests.review_required`
- `refund_requests.review_status`
- `refund_requests.review_reason`
- `refund_requests.refund_channel`
- `refund_requests.requested_at`
- `refund_requests.created_at`
- `refund_requests.updated_at`

### 14.5 输入建议

- `order_no`
- `refund_reason`
- `requested_amount`

### 14.6 输出建议

- `created`
- `refund_no`
- `refund_status`
- `review_required`
- `review_status`

---

## 15. `create_return_request`

### 15.1 工具作用

提交退货申请单。

### 15.2 写入表

- `return_requests`
- 可选补充：`agent_audit_logs`

### 15.3 主要写入字段

- `return_requests.return_no`
- `return_requests.order_item_id`
- `return_requests.customer_id`
- `return_requests.return_reason`
- `return_requests.return_status`
- `return_requests.pickup_required`
- `return_requests.refund_linked`
- `return_requests.created_at`
- `return_requests.updated_at`

### 15.4 输入建议

- `order_no`
- `sku_id` 或 `order_item_id`
- `return_reason`

---

## 16. `create_exchange_request`

### 16.1 工具作用

提交换货申请单。

### 16.2 写入表

- `exchange_requests`
- 可选补充：`agent_audit_logs`

### 16.3 主要写入字段

- `exchange_requests.exchange_no`
- `exchange_requests.order_item_id`
- `exchange_requests.customer_id`
- `exchange_requests.exchange_reason`
- `exchange_requests.exchange_status`
- `exchange_requests.created_at`
- `exchange_requests.updated_at`

### 16.4 输入建议

- `order_no`
- `sku_id` 或 `order_item_id`
- `exchange_reason`

---

## 17. `issue_coupon_compensation`

### 17.1 工具作用

给用户发放补偿券或记录赔付。

### 17.2 为什么需要

真实售后里很多情况不会直接退款，而是：

- 给优惠券补偿
- 给物流安抚券
- 给 VIP 客户发 goodwill coupon

### 17.3 写入表

- `coupon_compensations`
- 可选补充：`agent_audit_logs`

### 17.4 主要写入字段

- `coupon_compensations.customer_id`
- `coupon_compensations.order_id`
- `coupon_compensations.compensation_no`
- `coupon_compensations.coupon_code`
- `coupon_compensations.compensation_type`
- `coupon_compensations.coupon_value`
- `coupon_compensations.issue_reason`
- `coupon_compensations.issued_by`
- `coupon_compensations.status`
- `coupon_compensations.expire_at`
- `coupon_compensations.created_at`

### 17.5 输入建议

- `customer_code`
- `order_no`
- `issue_reason`
- `coupon_value`

### 17.6 输出建议

- `issued`
- `compensation_no`
- `coupon_code`
- `coupon_value`
- `expire_at`

---

## 18. 工具优先级与开发顺序建议

不建议一次性把全部工具都写完。

建议分三批推进。

### 第一批：先补最关键的查询与退款闭环

优先实现：

1. `shipment_tracking_lookup`
2. `payment_attempt_lookup`
3. `refund_request_lookup`
4. `refund_eligibility_check`
5. `create_refund_request`

这一批完成后，就能支撑：

- 订单 / 物流查询
- 重复扣费分析
- 退款资格核验
- 真正提交退款申请

### 第二批：补退货换货闭环

优先实现：

1. `order_items_lookup`
2. `return_request_lookup`
3. `exchange_request_lookup`
4. `create_return_request`
5. `create_exchange_request`

### 第三批：补补偿与精细化工单

优先实现：

1. `issue_coupon_compensation`
2. `create_handoff_ticket` 升级版
3. 可选：`create_after_sales_ticket`

---

## 19. 建议落地文件

本阶段建议主要改这些文件：

- `mcp/business_tool_handlers.py`
- 可选：拆分为 `mcp/ecommerce_tool_handlers.py`
- `integrations/business_db/db.py`
- `api/main.py`
- `agents/runtime.py`
- `docs/echomind-ecommerce-tools.md`

如果后续工具越来越多，建议将工具 handler 从当前文件中拆分：

- `mcp/account_tool_handlers.py`
- `mcp/order_tool_handlers.py`
- `mcp/after_sales_tool_handlers.py`

但在当前阶段，为了保持简洁，也可以先继续集中在一个文件里实现。

---

## 20. Phase 2 验收标准

本阶段完成后，至少应满足以下标准：

### 20.1 查询能力

- 能查询订单详情
- 能查询订单商品项
- 能查询物流状态
- 能查询退款申请状态
- 能查询支付尝试与重复扣费风险

### 20.2 动作能力

- 能创建退款申请
- 能创建退货申请
- 能创建换货申请
- 能发放补偿券
- 能创建人工工单

### 20.3 一致性要求

- 没有真实工具结果时，Agent 不能宣称动作已经完成
- 规则判断与订单事实判断要区分清楚
- 工具输出应能支撑前端展示和后续评测

---

## 21. 最终阶段目标

Phase 2 最终要达到的不是“工具数量更多了”，而是：

> EchoMind 已经具备了电商售后平台最核心的工具底座，Agent 可以真实查询事实、执行动作，并形成售后闭环。

只有完成这一步，后面的：

- Runtime 业务增强
- Human-in-the-loop
- 售后专项评测
- 平台化展示

才真正有足够的业务承载力。

---

## 22. 推荐下一步

建议紧接着进入第一批工具实现，按以下顺序推进：

1. `shipment_tracking_lookup`
2. `payment_attempt_lookup`
3. `refund_request_lookup`
4. `refund_eligibility_check`
5. `create_refund_request`

这 5 个工具做完后，EchoMind 就能形成二期第一个非常有代表性的最小售后闭环：

> 查询订单物流 + 分析支付异常 + 判断退款资格 + 提交退款申请 + 必要时升级人工

这会比当前系统更像真实企业售后 Agent，也更适合后续继续写进简历与项目介绍。
