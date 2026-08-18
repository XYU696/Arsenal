# EchoMind 电商售后二期 Phase 1 数据库设计任务文档

## 1. 文档目标

本文档用于拆解 EchoMind 电商售后二期的 `Phase 1`：

> 电商售后业务域建模

这一阶段的重点不是先改 Agent，而是先把 EchoMind 背后的业务数据结构从“通用客服示例库”升级为“更贴近真实电商售后平台的结构化业务库”。

本文档重点说明：

1. 为什么要先做数据库设计
2. 当前数据库有哪些边界
3. Phase 1 需要新增和调整哪些表
4. 每张表服务什么业务场景
5. 每张表建议有哪些关键字段
6. 种子数据应该覆盖哪些样本
7. 开发顺序、文件落点和验收标准是什么

---

## 2. 为什么 Phase 1 要先做数据库设计

当前 EchoMind 已经具备：

- 单 Agent LangGraph runtime
- 工具调用
- 业务数据库
- 知识库
- 工单闭环

但如果想把它升级成“电商售后 Agent 平台”，Agent 是否足够聪明并不是第一优先级。

更关键的是：

> Agent 背后是否有一个足够真实、足够完整的业务世界可供查询和执行。

如果数据库设计不够贴近真实售后系统，会导致后续问题：

- Agent 只能查订单，无法查订单项和物流链路
- Agent 只能解释退款政策，无法提交退款申请
- Agent 无法区分“退款审核中”和“退款已完成”
- Agent 无法处理赔付和补偿场景
- Agent 无法形成真正的售后闭环

因此，二期第一个实际开发重点应该是：

> 先建立售后业务数据库，再让 Agent 围绕这些结构化事实工作。

---

## 3. 当前数据库现状与边界

### 3.1 当前已有表

当前 `data/business/schema.sql` 中已有 6 张表：

1. `customers`
2. `accounts`
3. `orders`
4. `refund_policies`
5. `incidents`
6. `handoff_tickets`

这些表已经能支撑第一阶段最小功能：

- 查账号状态
- 查订单
- 查退款规则
- 查故障
- 创建人工工单

### 3.2 当前边界

但对于电商售后场景，这套数据结构还不够表达真实业务：

- `orders` 只有订单头，没有订单商品明细
- 没有物流表，无法表达发货、签收、丢件、拒收、揽收超时
- 没有退款申请表，无法表达“用户已提交退款”
- 没有退货 / 换货申请表
- 没有补偿记录表，无法表达赔付或发券
- 没有支付尝试表，无法表达重复扣费、支付失败、重试
- 工单表还偏“客服升级”，没有完整售后任务语义

所以二期 Phase 1 要解决的问题不是“加几张表”，而是：

> 让数据库真正能承载电商售后业务链路。

---

## 4. Phase 1 总体目标

### 4.1 业务目标

让数据库能够表达以下售后高频问题：

- 用户买了什么
- 订单是否已付款、已发货、已签收
- 包裹是否异常
- 是否可以退款
- 是否已申请退款
- 是否已提交退货 / 换货
- 是否已发放补偿
- 是否存在重复扣费
- 是否已升级人工

### 4.2 技术目标

- 扩展当前 `SQLite` 业务库结构
- 保持与现有工具层兼容，便于平滑演进
- 为下一阶段工具开发提供可靠的数据基础
- 保证 schema 简洁、可读、易 seed、易调试

### 4.3 建模原则

Phase 1 建模时建议遵循以下原则：

1. 先做“够真实、够用”，不要一开始追求超复杂 ERP 模型
2. 优先围绕客服售后任务设计，而不是围绕后台运营全量系统设计
3. 一张表只表达一类核心事实
4. 所有动作表都要有状态字段
5. 状态字段尽量用枚举值，方便 Agent 判断
6. 所有关键售后对象都要有编号，方便前端展示与日志追踪

---

## 5. Phase 1 业务范围

本阶段建议覆盖以下业务范围：

### 5.1 订单域

- 订单头
- 订单商品项
- 支付尝试记录

### 5.2 履约与物流域

- 发货记录
- 物流轨迹摘要
- 物流异常

### 5.3 售后申请域

- 退款申请
- 退货申请
- 换货申请

### 5.4 补偿域

- 优惠券补偿
- 赔付记录

### 5.5 客服升级与审计域

- 人工工单
- Agent 审计日志

---

## 6. Phase 1 表设计任务拆解

本阶段建议分为两类：

- 保留并升级的旧表
- 新增的售后业务表

---

## 7. 保留并升级的旧表

### 7.1 `customers`

#### 作用

客户主数据表，用于记录用户基础身份信息。

#### 当前保留原因

- 账号、订单、补偿、工单都需要关联客户
- 后续 VIP、风控、企业客户策略都依赖客户层级

#### 建议补充字段

- `preferred_channel`
  - 用户偏好联系渠道，如 `app` / `sms` / `email`
- `risk_level`
  - 客户风控等级，如 `low` / `medium` / `high`
- `vip_expire_at`
  - VIP 权益截止时间

#### 主要服务场景

- 判断客户层级
- 判断是否可走 VIP 特殊补偿策略
- 售后工单绑定客户

---

### 7.2 `accounts`

#### 作用

记录登录账号、角色、权限、账号状态与锁定原因。

#### 建议补充字段

- `security_review_status`
  - `none` / `pending` / `approved` / `rejected`
- `failed_login_count`
  - 最近周期内失败登录次数
- `unlock_available_at`
  - 临时锁定自动解除时间

#### 主要服务场景

- 登录异常
- 风控限制
- 账号锁定解释
- 高风险售后行为升级判断

---

### 7.3 `orders`

#### 作用

订单主表，记录订单级核心信息。

#### 当前问题

当前订单表还偏“订阅型 SaaS 订单”，对电商售后来说字段不够。

#### 建议补充字段

- `channel`
  - 订单来源渠道，如 `app` / `web` / `wechat`
- `delivery_type`
  - `digital` / `express` / `same_day`
- `consignee_name`
  - 收货人姓名
- `consignee_phone`
  - 收货人电话
- `province`
- `city`
- `district`
- `detail_address`
- `order_remark`

#### 主要服务场景

- 查询订单状态
- 查询收货信息
- 判断是否适用物流售后
- 为退款/退货/换货建立上游订单关联

---

### 7.4 `refund_policies`

#### 作用

记录退款政策，用于规则核验。

#### 建议补充字段

- `review_reason_template`
  - 需人工审核时的标准原因模板
- `refund_method`
  - `original_route` / `wallet` / `manual`
- `policy_category`
  - `subscription` / `physical_goods` / `enterprise_contract`

#### 主要服务场景

- 退款资格判断
- 生成规则说明
- 判断是否必须人工审核

---

### 7.5 `handoff_tickets`

#### 作用

记录人工升级工单。

#### 当前问题

当前工单表能用，但还偏通用。

#### 建议补充字段

- `source_ref_type`
  - 来源对象类型，如 `order` / `refund_request` / `account`
- `source_ref_no`
  - 来源对象编号
- `sla_level`
  - `standard` / `urgent` / `vip`
- `owner_agent`
  - 当前跟进人工坐席或队列负责人

#### 主要服务场景

- 转人工
- 售后争议升级
- 人工审核接管

---

## 8. 新增售后业务表

### 8.1 `order_items`

#### 作用

记录订单内的商品明细。

#### 为什么必须有

真实电商售后不是只看订单号，而是要看：

- 买了什么商品
- 每个商品是否发货
- 某个商品是否可退
- 某个商品是否换货

#### 建议字段

- `id`
- `order_id`
- `sku_id`
- `sku_name`
- `category`
- `quantity`
- `unit_price`
- `discount_amount`
- `final_amount`
- `item_status`
- `fulfillment_status`
- `returnable`
- `exchangeable`
- `created_at`
- `updated_at`

#### 建议状态枚举

- `item_status`
  - `normal`
  - `refund_pending`
  - `refunded`
  - `return_pending`
  - `returned`
  - `exchange_pending`
  - `exchanged`

- `fulfillment_status`
  - `pending`
  - `packed`
  - `shipped`
  - `delivered`

#### 服务工具

- `order_items_lookup`
- `refund_eligibility_check`
- `create_return_request`
- `create_exchange_request`

---

### 8.2 `shipments`

#### 作用

记录订单物流与履约信息。

#### 为什么必须有

物流异常是售后最常见场景之一，数据库必须能表达：

- 是否已发货
- 是否已揽收
- 是否在运输中
- 是否已签收
- 是否存在丢件、延迟、拒收等异常

#### 建议字段

- `id`
- `order_id`
- `shipment_no`
- `tracking_no`
- `carrier`
- `shipment_status`
- `last_checkpoint`
- `last_checkpoint_at`
- `signed_at`
- `receiver_name`
- `exception_type`
- `exception_summary`
- `created_at`
- `updated_at`

#### 建议状态枚举

- `shipment_status`
  - `pending`
  - `picked_up`
  - `in_transit`
  - `out_for_delivery`
  - `signed`
  - `failed_delivery`
  - `returned`
  - `lost`

- `exception_type`
  - `none`
  - `delay`
  - `lost`
  - `damaged`
  - `refused`
  - `address_issue`

#### 服务工具

- `shipment_tracking_lookup`
- `incident_lookup`
- `issue_coupon_compensation`

---

### 8.3 `payment_attempts`

#### 作用

记录订单支付尝试，用于表达支付失败、重试、重复扣费等问题。

#### 为什么必须有

客服常见问题包括：

- 为什么我被扣了两次钱
- 为什么显示支付成功但订单没生效
- 为什么扣费失败

#### 建议字段

- `id`
- `order_id`
- `attempt_no`
- `payment_channel`
- `attempt_status`
- `amount`
- `currency`
- `transaction_id`
- `failure_reason`
- `is_duplicate_risk`
- `created_at`

#### 建议状态枚举

- `attempt_status`
  - `initiated`
  - `success`
  - `failed`
  - `reversed`
  - `duplicate_suspected`

#### 服务工具

- `payment_attempt_lookup`
- `refund_eligibility_check`
- `create_handoff_ticket`

---

### 8.4 `refund_requests`

#### 作用

记录退款申请单。

#### 为什么必须有

没有这张表，系统只能说“可以退款”，但无法表达：

- 是否已经提交退款
- 当前退款审核到哪一步
- 审核是否通过
- 退款金额是多少

#### 建议字段

- `id`
- `refund_no`
- `order_id`
- `customer_id`
- `refund_reason`
- `requested_amount`
- `approved_amount`
- `refund_status`
- `review_required`
- `review_status`
- `review_reason`
- `refund_channel`
- `requested_at`
- `reviewed_at`
- `completed_at`
- `created_at`
- `updated_at`

#### 建议状态枚举

- `refund_status`
  - `created`
  - `pending_review`
  - `approved`
  - `rejected`
  - `processing`
  - `completed`
  - `cancelled`

- `review_status`
  - `none`
  - `pending`
  - `approved`
  - `rejected`

#### 服务工具

- `refund_request_lookup`
- `create_refund_request`
- `refund_eligibility_check`

---

### 8.5 `return_requests`

#### 作用

记录退货申请单。

#### 建议字段

- `id`
- `return_no`
- `order_item_id`
- `customer_id`
- `return_reason`
- `return_status`
- `pickup_required`
- `warehouse_received_at`
- `refund_linked`
- `created_at`
- `updated_at`

#### 服务工具

- `return_request_lookup`
- `create_return_request`

---

### 8.6 `exchange_requests`

#### 作用

记录换货申请单。

#### 建议字段

- `id`
- `exchange_no`
- `order_item_id`
- `customer_id`
- `exchange_reason`
- `exchange_status`
- `new_shipment_no`
- `created_at`
- `updated_at`

#### 服务工具

- `exchange_request_lookup`
- `create_exchange_request`

---

### 8.7 `coupon_compensations`

#### 作用

记录补偿券发放和赔付行为。

#### 为什么必须有

真实售后经常不是直接退款，而是：

- 发优惠券安抚
- 给运费补偿
- 给延迟补偿

#### 建议字段

- `id`
- `customer_id`
- `order_id`
- `compensation_no`
- `coupon_code`
- `compensation_type`
- `coupon_value`
- `issue_reason`
- `issued_by`
- `status`
- `expire_at`
- `created_at`

#### 建议状态枚举

- `compensation_type`
  - `coupon`
  - `cashback`
  - `shipping_credit`

- `status`
  - `issued`
  - `used`
  - `expired`
  - `cancelled`

#### 服务工具

- `issue_coupon_compensation`
- `compensation_lookup`

---

### 8.8 `warehouse_incidents`

#### 作用

记录履约和仓储相关异常。

#### 为什么必须有

很多售后问题不是单个订单问题，而是仓配侧异常，例如：

- 爆仓延迟
- 仓库拣货错误
- 大促发货积压

#### 建议字段

- `id`
- `incident_no`
- `incident_type`
- `severity`
- `status`
- `affected_scope`
- `summary`
- `started_at`
- `resolved_at`
- `updated_at`

#### 服务工具

- `incident_lookup`
- `shipment_tracking_lookup`

---

### 8.9 `after_sales_tickets`

#### 作用

记录正式售后任务单，与纯人工升级工单区分开。

#### 为什么建议单独建表

`handoff_tickets` 更偏客服升级动作；
`after_sales_tickets` 更偏业务售后流程对象。

例如：

- 售后退款处理单
- 退货审核单
- 丢件赔付单

#### 建议字段

- `id`
- `ticket_no`
- `ticket_type`
- `order_id`
- `customer_id`
- `source_channel`
- `status`
- `priority`
- `assigned_queue`
- `summary`
- `created_at`
- `updated_at`

#### 服务工具

- `create_after_sales_ticket`
- `after_sales_ticket_lookup`

---

### 8.10 `agent_audit_logs`

#### 作用

记录 Agent 执行动作的审计日志。

#### 为什么建议在 Phase 1 就设计出来

后续会涉及：

- Agent 提交退款申请
- Agent 发放补偿券
- Agent 建工单

这些动作如果没有审计日志，后续排查问题很麻烦。

#### 建议字段

- `id`
- `conv_id`
- `session_id`
- `agent_name`
- `task_type`
- `action_type`
- `target_type`
- `target_no`
- `action_payload_json`
- `result_status`
- `error_message`
- `created_at`

#### 服务工具

- 暂不直接对外暴露
- 主要用于内部审计和评测分析

---

## 9. Phase 1 seed 数据设计任务

数据库设计不只是建表，还必须设计一套有代表性的样例数据。

### 9.1 种子数据目标

种子数据必须支撑：

- 联调
- 测试
- Demo 演示
- 评测集构造

### 9.2 每类场景建议覆盖

#### 订单场景

- 正常已支付订单
- 已支付未发货订单
- 已发货运输中订单
- 已签收订单
- 已取消订单

#### 物流场景

- 运输正常
- 揽收延迟
- 签收异常
- 丢件
- 拒收退回

#### 退款场景

- 可自动退款
- 需人工审核退款
- 已提交退款申请
- 退款被拒绝
- 已完成退款

#### 退货换货场景

- 已申请退货待审核
- 已寄回待仓收
- 已完成换货

#### 补偿场景

- 延迟发货发券
- 丢件补偿券
- VIP 用户额外补偿

#### 账号风控场景

- 临时锁定
- 安全审查中
- 风控限制导致人工审核升级

### 9.3 推荐的客户样本类型

建议至少准备：

- 普通用户
- 高频退款用户
- VIP 用户
- 企业客户
- 风控用户
- 物流争议用户

### 9.4 数据设计要求

- 所有关键对象都要有可读编号
- 时间字段要保持逻辑一致
- 订单、物流、退款、工单之间要能串联
- 保证一条用户链路能完整走通

---

## 10. Phase 1 具体开发任务清单

建议按以下顺序推进。

### 任务 A：输出 schema_v2 设计草案

目标：

- 明确新增表
- 明确字段
- 明确枚举值
- 明确外键关系

建议产出：

- `data/business/schema_v2.sql`

### 任务 B：设计 seed_v2 业务样本

目标：

- 让每张表都有真实样本
- 让关键售后链路能联通

建议产出：

- `data/business/seed_v2.sql`

### 任务 C：补 BusinessDB 查询接口

目标：

- 为后续工具开发准备最小数据库访问层

建议先补：

- `get_order_items(order_no)`
- `get_shipment(order_no)`
- `get_payment_attempts(order_no)`
- `get_refund_request(refund_no / order_no)`
- `list_refund_requests_for_order(order_no)`
- `get_return_request(return_no)`
- `get_exchange_request(exchange_no)`

### 任务 D：补数据库说明文档

目标：

- 让后续工具和 runtime 开发能快速对齐数据模型

建议产出：

- `docs/echomind-ecommerce-domain.md`

### 任务 E：补 demo 查询脚本

目标：

- 方便快速验证 schema 和 seed 是否合理

建议产出：

- `integrations/business_db/demo_queries_v2.py`

---

## 11. 文件落点建议

本阶段建议直接落到以下文件：

- `data/business/schema_v2.sql`
- `data/business/seed_v2.sql`
- `integrations/business_db/db.py`
- `integrations/business_db/init_db.py`
- `integrations/business_db/demo_queries.py`
- `docs/echomind-ecommerce-domain.md`

如果希望保留当前第一版业务库不动，建议：

- 新 schema / seed 使用 `v2` 命名
- 初始化脚本增加切换参数

---

## 12. Phase 1 验收标准

这一阶段完成后，至少要满足以下验收标准：

### 12.1 数据结构验收

- 能成功建表
- 外键关系正确
- 枚举值设计清晰
- 售后主链路需要的数据都能表示

### 12.2 样例数据验收

- 种子数据能支撑退款、物流、退货、补偿、账号异常等场景
- 至少能演示 5 条完整售后链路

### 12.3 开发可用性验收

- BusinessDB 能跑基础查询
- demo 查询能直观看到订单、物流、退款、补偿等数据
- 后续工具层开发不需要再反复返工 schema

---

## 13. 最终阶段目标

Phase 1 最终要达成的不是“表更多了”，而是：

> EchoMind 的数据底座已经从“客服原型库”升级成了“电商售后业务库”。

只有做到这一点，后续：

- 动作型工具
- Agentic RAG
- Human-in-the-loop
- 售后评测
- 前端平台化

才真正有意义。

---

## 14. 推荐下一步

如果要继续推进，建议紧接着做下面两件事：

1. 先根据本文档产出 `schema_v2.sql`
2. 再根据 `schema_v2.sql` 产出 `seed_v2.sql`

这两步完成后，就可以立即进入：

> `Phase 2` 售后工具体系升级

建议第一个最小闭环优先做：

- 查询订单
- 查询物流
- 判断退款资格
- 创建退款申请

这是最适合作为电商售后二期第一批演示能力的切入点。
