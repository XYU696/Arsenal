# EchoMind 电商售后版二期开发计划

## 1. 文档目标

本文档用于指导 EchoMind 二期开发，将当前已经完成的：

- 基于 `LangGraph` 的受控单 Agent runtime
- 工具调用与业务数据库接入
- RAG 检索与 Skills 热加载
- 人工工单写库与基础评测

进一步升级为一个更贴近真实企业业务、可持续演进、可评测、可上线的：

> 面向电商售后场景的企业级客服 Agent 平台。

本文档重点说明：

1. 二期的整体业务目标是什么
2. 每个阶段要解决什么问题
3. 每个阶段要做哪些具体开发任务
4. 每个阶段要落到哪些模块和文件
5. 每个阶段的验收标准是什么
6. 二期完成后的最终优化目标是什么

---

## 2. 当前项目基础与二期升级方向

### 2.1 当前基础能力

截至 2026 年 7 月 31 日，EchoMind 已经具备以下能力：

- `FastAPI + Next.js` 的前后端联调链路
- 基于 `LangGraph` 的受控单 Agent ReAct 循环
- 结构化业务工具调用
- `SQLite` 业务数据库
- `ChromaDB` 知识库检索
- `Redis` 会话状态保存
- `Skills` 热加载
- 工单创建与转人工闭环
- 评测数据集与 baseline 跑分能力

### 2.2 当前边界

虽然 Agent Runtime 已经初步成型，但当前系统仍然偏“通用客服 Agent 原型”，主要存在以下边界：

- 业务场景还不够聚焦，缺少明确行业落地面
- 数据库结构还不够像真实售后系统
- 工具以查询和简单工单为主，缺少真实售后动作
- 知识库内容仍偏通用，缺少售后规则中心
- Skill 规则还不够业务化、运营化
- 对复合售后问题的拆解能力不足
- 人工审核、补偿发放、退款申请等高风险动作缺少完整流程
- 前端还没有形成“售后平台控制台”的感觉

### 2.3 二期升级方向

二期建议将 EchoMind 收敛到一个具体业务方向：

> 电商售后客服

聚焦以下高频业务：

- 订单查询
- 物流异常
- 退款申请
- 退货 / 换货
- 赔付 / 优惠券补偿
- 账号异常
- 人工升级与工单流转

---

## 3. 二期总体目标

二期的目标不是简单“多加几个工具”，而是把 EchoMind 做成一个更像真实企业智能客服平台的系统。

### 3.1 业务目标

- 覆盖电商售后核心问题链路
- 支持“解释规则 + 核实事实 + 执行动作 + 人工升级”完整闭环
- 让 Agent 的判断基于真实业务事实，而不是只靠大模型生成

### 3.2 技术目标

- 完善结构化售后业务数据库
- 扩展动作型工具，支持真实售后申请
- 建立售后知识中心与 Agentic RAG
- 将规则抽离成可热更新的 Skills
- 在 LangGraph runtime 中引入更强的业务约束与人工审核能力
- 建立售后专项评测集，沉淀可量化指标

### 3.3 简历目标

二期完成后，这个项目应能够支撑类似下面的项目定位：

> 面向电商售后场景设计并实现企业级客服 Agent 平台，基于 LangGraph 构建受控单 Agent Runtime，支持订单查询、物流异常、退款退货、补偿发放与人工审核闭环；结合结构化业务数据库、Agentic RAG、Skill 热更新、工单系统与评测体系，实现可观测、可控、可上线的智能售后服务平台。

---

## 4. 二期阶段划分总览

二期建议拆成 8 个阶段：

1. `Phase 1`：电商售后业务域建模
2. `Phase 2`：售后工具体系升级
3. `Phase 3`：售后知识库与 Agentic RAG
4. `Phase 4`：Skill 体系业务化
5. `Phase 5`：LangGraph Runtime 业务增强
6. `Phase 6`：人工审核与高风险动作闭环
7. `Phase 7`：售后专项评测体系
8. `Phase 8`：前端平台化与运营视角完善

推荐执行顺序：

1. `Phase 1`
2. `Phase 2`
3. `Phase 5`
4. `Phase 3`
5. `Phase 4`
6. `Phase 7`
7. `Phase 6`
8. `Phase 8`

这样做的原因是：

- 先把真实业务数据与工具能力建好
- 再让 runtime 真正围绕这些业务能力演化
- 再补知识、规则、评测与人工审核
- 最后做平台化展示

---

## 5. Phase 1：电商售后业务域建模

### 5.1 阶段目标

让 EchoMind 的底层数据结构从“通用客服示例”升级为“真实电商售后业务库”。

这一阶段的核心不是 Agent，而是先把“Agent 将要服务的业务世界”建出来。

### 5.2 需求说明

当前业务库已有：

- `customers`
- `accounts`
- `orders`
- `refund_policies`
- `incidents`
- `handoff_tickets`

但对于电商售后场景仍然不够，需要让数据库能表达：

- 一个订单由哪些商品组成
- 是否已发货、签收、拒收、丢件
- 是否已提交退款申请
- 是否已提交退货 / 换货申请
- 是否已发放补偿券
- 是否存在重复扣费或支付异常
- 售后工单是否来自退款、物流还是补偿问题

### 5.3 具体开发任务

#### 任务 1：设计售后业务表

新增或升级以下表：

- `order_items`
- `shipments`
- `refund_requests`
- `return_requests`
- `exchange_requests`
- `coupon_compensations`
- `payment_attempts`
- `warehouse_incidents`
- `after_sales_tickets`
- `agent_audit_logs`

#### 任务 2：明确字段含义与业务流

重点字段建议包括：

- `order_items`
  - `order_id`
  - `sku_id`
  - `sku_name`
  - `quantity`
  - `unit_price`
  - `fulfillment_status`

- `shipments`
  - `order_id`
  - `tracking_no`
  - `carrier`
  - `shipment_status`
  - `last_checkpoint`
  - `signed_at`
  - `exception_type`

- `refund_requests`
  - `order_id`
  - `refund_no`
  - `refund_reason`
  - `refund_status`
  - `requested_amount`
  - `review_required`

- `coupon_compensations`
  - `customer_id`
  - `coupon_code`
  - `issue_reason`
  - `coupon_value`
  - `expire_at`

#### 任务 3：构造真实风格 seed 数据

每类表建议至少准备：

- 正常样本
- 异常样本
- 边界样本

例如：

- 正常签收订单
- 已付款未发货订单
- 超时未揽件订单
- 已签收但用户投诉未收到
- 重复扣费订单
- 可自动退款订单
- 需人工审核退款订单
- 不支持退款订单
- 已赔付优惠券记录

#### 任务 4：补充业务数据库说明文档

要求明确：

- 每张表服务哪个工具
- 每张表对应哪个售后场景
- 关键字段在 Agent 决策中的作用

### 5.4 建议落地文件

- `data/business/schema_v2.sql`
- `data/business/seed_v2.sql`
- `integrations/business_db/db.py`
- `docs/echomind-ecommerce-domain.md`

### 5.5 验收标准

- 新数据库可以通过初始化脚本成功建表
- 样例数据覆盖退款、物流、退货、补偿、支付异常等核心场景
- 每个售后工具都能找到对应的数据来源

---

## 6. Phase 2：售后工具体系升级

### 6.1 阶段目标

让 Agent 不只会“查询”，而且能够执行真实售后动作。

### 6.2 需求说明

当前工具能力主要是：

- 查账号
- 查订单
- 查退款规则
- 查故障
- 建工单

对于电商售后平台来说，还需要让 Agent 支持：

- 查物流
- 查退款申请状态
- 判断退款资格
- 提交退款申请
- 提交退货 / 换货申请
- 发放补偿券

### 6.3 具体开发任务

#### 任务 1：补充查询型工具

新增：

- `order_items_lookup`
- `shipment_tracking_lookup`
- `refund_request_lookup`
- `return_request_lookup`
- `exchange_request_lookup`
- `payment_attempt_lookup`

#### 任务 2：补充动作型工具

新增：

- `refund_eligibility_check`
- `create_refund_request`
- `create_return_request`
- `create_exchange_request`
- `issue_coupon_compensation`

#### 任务 3：统一工具输入输出 schema

每个工具都要明确：

- 输入参数
- 必填字段
- 输出结构
- 成功与失败标志
- fallback 返回格式

要求和现有 `MCPToolManager` 兼容。

#### 任务 4：工具风险分级

建议按风险分级：

- 低风险工具
  - 查订单
  - 查物流
  - 查知识

- 中风险工具
  - 提交退货申请
  - 提交换货申请
  - 创建人工工单

- 高风险工具
  - 提交退款申请
  - 发放优惠券补偿

后续 `Phase 6` 的人工审核就建立在这套分级基础上。

### 6.4 建议落地文件

- `mcp/ecommerce_tool_handlers.py`
- `integrations/business_db/db.py`
- `api/main.py`
- `docs/echomind-ecommerce-tools.md`

### 6.5 验收标准

- 查询型工具均可返回结构化售后事实
- 动作型工具可落库并返回结果编号
- 前端或日志中可看到工具执行结果

---

## 7. Phase 3：售后知识库与 Agentic RAG

### 7.1 阶段目标

把知识库升级为一个“售后规则中心”，让 Agent 能同时使用：

- 结构化业务事实
- 非结构化运营规则

### 7.2 需求说明

数据库适合回答“事实”：

- 某订单状态是什么
- 某账号是否受限
- 某退款申请是否存在

知识库适合回答“规则”：

- 什么情况下支持退款
- 已签收商品能否退货
- 大促期间赔付规则是什么
- 物流超时怎么安抚用户

### 7.3 具体开发任务

#### 任务 1：重建售后知识文档集

建议新增以下知识文档：

- `refund_policy.md`
- `return_exchange_policy.md`
- `shipping_exception_policy.md`
- `coupon_compensation_policy.md`
- `risk_review_policy.md`
- `peak_season_special_policy.md`
- `customer_service_sop.md`

#### 任务 2：为知识文档加分类标签

建议字段：

- `category`
- `policy_code`
- `channel`
- `priority`
- `effective_from`
- `effective_to`

#### 任务 3：优化 Agentic RAG 策略

保留并强化：

- 查询改写
- 多路召回
- rerank
- 低置信度拒答
- fallback

重点要求：

- 规则说明优先查知识库
- 事实说明优先查数据库
- 二者冲突时优先提示人工核实

#### 任务 4：增加检索可解释性

让回复中能体现：

- 使用了哪类规则
- 来自哪份政策文档
- 是否只是规则判断而非订单事实核验

### 7.4 建议落地文件

- `knowledge/ecommerce/`
- `mcp/knowledge_base.py`
- `api/main.py`
- `docs/echomind-ecommerce-rag.md`

### 7.5 验收标准

- 知识库可覆盖退款、退货、物流异常、补偿等关键政策
- Agent 能区分规则判断与事实核验
- 检索失败时有稳定 fallback

---

## 8. Phase 4：Skill 体系业务化

### 8.1 阶段目标

把一些高频售后规则从代码中进一步抽离，形成可热更新的业务 Skill 层。

### 8.2 需求说明

不是所有规则都应该写死在代码里。

例如这些内容很适合放进 Skills：

- 退款话术偏好
- 大促临时政策
- VIP 客户补偿上限
- 风控账号的升级策略
- 物流异常的安抚优先级

### 8.3 具体开发任务

#### 任务 1：新增售后 Skills

建议新增：

- `refund_policy.skill.md`
- `return_exchange.skill.md`
- `logistics_exception.skill.md`
- `vip_compensation.skill.md`
- `fraud_review.skill.md`
- `promotion_season.skill.md`

#### 任务 2：统一 Skill 结构

每份 Skill 应包含：

- 适用场景
- 规则摘要
- 推荐追问
- 禁止动作
- 转人工条件
- 示例话术

#### 任务 3：验证热更新效果

要求在不重启服务的前提下：

- 修改 Skill 文本
- reload 生效
- 对回答行为产生可观测影响

### 8.4 建议落地文件

- `skills/*.skill.md`
- `core/skill_loader.py`
- `frontend/components/skills-panel.tsx`
- `docs/echomind-ecommerce-skills.md`

### 8.5 验收标准

- 售后核心规则可以通过 Skill 表达
- 技术人员可通过配置快速调整策略
- Skill 修改前后可以通过评测观察差异

---

## 9. Phase 5：LangGraph Runtime 业务增强

### 9.1 阶段目标

让当前“能循环、能调工具”的 runtime，更贴近真实电商售后 Agent 的运行方式。

### 9.2 需求说明

当前 runtime 的核心问题包括：

- 一次只处理一个粗粒度任务
- 遇到复合问题容易只抓住一个子问题
- 缺少“规则判断”和“事实核验”的强约束
- 动作前置条件不够严格

### 9.3 具体开发任务

#### 任务 1：支持复合诉求拆分

例如用户一句话同时提出：

- 账号被限制
- 订单未收到
- 想退款

则 runtime 至少要支持：

- 识别主问题与子问题
- 逐个处理子任务
- 汇总当前轮结果

建议新增：

- `primary_task_type`
- `secondary_tasks`
- `resolved_subtasks`

#### 任务 2：补齐动作前置校验

要求：

- 没有 `order_no` 时，不能把订单事实说死
- 没有真实使用量时，不能默认说“使用量 0%”
- 没有真实建单时，不能说“已创建工单”
- 没有物流单号时，不能说“包裹已签收”

#### 任务 3：细化任务状态

建议增加：

- `awaiting_order_info`
- `awaiting_logistics_info`
- `awaiting_refund_confirmation`
- `awaiting_review`
- `refund_requested`
- `return_requested`
- `exchange_requested`

#### 任务 4：增强业务一致性约束

要求 runtime 在最终落地动作前做强校验：

- 模型话术与真实动作一致
- 工具结果与最终回复一致
- 高风险场景不能被 `finish` 直接绕过去

#### 任务 5：扩展 session 持久化信息

建议在 `SessionState` 中记录：

- 当前售后任务类型
- 已核实的订单事实
- 已核实的退款资格
- 待确认动作
- 已创建的工单 / 申请单编号

### 9.4 建议落地文件

- `agents/runtime.py`
- `agents/session_state.py`
- `docs/echomind-ecommerce-runtime.md`

### 9.5 验收标准

- 复合售后问题不再只处理单一子问题
- Agent 在缺失关键事实时优先追问而非臆断
- 高风险动作都有明确前置约束

---

## 10. Phase 6：人工审核与高风险动作闭环

### 10.1 阶段目标

让系统更接近企业上线版本，支持真正的 Human-in-the-loop。

### 10.2 需求说明

并不是所有售后动作都适合全自动执行。

以下动作应纳入人工审核：

- 年费退款
- 季度套餐退款
- 企业套餐退款
- 超标补偿
- 风控账号相关售后
- 高价值客户争议订单

### 10.3 具体开发任务

#### 任务 1：定义审核场景与规则

明确：

- 哪些工具必须审核
- 哪些条件下必须审核
- 审核前系统应该输出什么摘要

#### 任务 2：新增审核数据表

建议新增：

- `review_tasks`
- `review_decisions`

字段建议包括：

- `review_no`
- `source_action`
- `source_ref_id`
- `status`
- `decision`
- `reviewer`
- `review_reason`
- `created_at`

#### 任务 3：在 LangGraph 中加入审核中断点

流程建议：

1. Agent 先给出建议动作
2. 系统写入待审核记录
3. runtime 暂停在 `awaiting_review`
4. 审核通过后恢复执行
5. 审核驳回后向用户解释并结束

#### 任务 4：前端展示审核状态

让平台支持看到：

- 待审核
- 已通过
- 已驳回
- 已人工接管

### 10.4 建议落地文件

- `data/business/schema_v2.sql`
- `agents/runtime.py`
- `frontend/components/chat-studio.tsx`
- `docs/echomind-ecommerce-hitl.md`

### 10.5 验收标准

- 高风险动作不会直接无审核执行
- 审核状态可在系统中追踪
- 审核通过与驳回都能形成清晰闭环

---

## 11. Phase 7：售后专项评测体系

### 11.1 阶段目标

让每次优化都能量化比较，而不是只靠主观感觉判断“看起来变好了”。

### 11.2 需求说明

当前已有 baseline 评测，但还偏通用。

二期需要建立一套面向电商售后的专项评测集。

### 11.3 具体开发任务

#### 任务 1：构建电商售后评测集 v2

建议总量：

- `200~300` 条

覆盖：

- 订单查询
- 物流异常
- 退款资格判断
- 退款申请
- 退货 / 换货
- 补偿申请
- 账号异常
- 复合诉求
- 人工审核场景

#### 任务 2：设计核心指标

建议重点统计：

- 任务完成率
- 工具调用成功率
- 事实一致率
- 错误升级率
- 漏升级率
- 工单创建成功率
- 平均推理步数
- 平均响应时延
- 退款类事实性错误率

#### 任务 3：建立阶段性报告

每完成一大阶段，固定输出：

- `baseline_v2`
- `tools_v2`
- `runtime_v2`
- `rag_v2`
- `skills_v2`
- `hitl_v2`

#### 任务 4：沉淀 badcase 库

重点记录：

- 复合问题漏处理
- 未核实就说死订单事实
- 未核实就说使用量为 0
- 不该建单却建单
- 应该升级却未升级

### 11.4 建议落地文件

- `evaluation/datasets/ecommerce_after_sales_eval_v2.jsonl`
- `evaluation/report_schema_v2.json`
- `evaluation/reports/`
- `docs/echomind-ecommerce-eval.md`

### 11.5 验收标准

- 能稳定运行售后专项评测
- 每阶段优化都有前后对比结果
- 能提炼出可写入简历的量化指标

---

## 12. Phase 8：前端平台化与运营视角完善

### 12.1 阶段目标

把系统从“有个聊天页”升级成“企业级售后 Agent 平台控制台”。

### 12.2 需求说明

前端当前已经能展示：

- 回复内容
- 意图
- Agent 类型
- latency
- knowledge 使用情况
- escalated
- ticket_no

但对平台化展示来说还不够。

### 12.3 具体开发任务

#### 任务 1：对话工作台增强

新增展示：

- 当前任务状态
- 最近一次工具调用记录
- 当前售后申请编号
- 是否需要人工审核

#### 任务 2：工单面板

展示：

- 最近创建工单
- 工单状态
- 工单优先级
- 所属队列
- 工单来源任务

#### 任务 3：售后申请面板

展示：

- 退款申请状态
- 退货申请状态
- 换货申请状态
- 补偿发放记录

#### 任务 4：运营与评测面板

展示：

- 工具成功率
- 任务完成率
- 审核通过率
- 最近 badcase
- 最近回归问题

### 12.4 建议落地文件

- `frontend/components/chat-studio.tsx`
- `frontend/components/monitor-center.tsx`
- `frontend/components/*`
- `frontend/lib/types.ts`

### 12.5 验收标准

- 前端可以体现“平台感”
- 测试和演示时可以清楚看到工具、工单、审核和状态流
- 能支撑项目答辩与简历展示

---

## 13. 二期最终优化目标

### 13.1 业务落地目标

形成一个面向电商售后的企业级 Agent 平台，支持：

- 订单查询
- 物流异常处理
- 退款 / 退货 / 换货
- 优惠券补偿
- 账号异常
- 人工审核与工单流转

### 13.2 工程目标

- 数据库结构更贴近真实电商售后系统
- Agent 具备查询 + 动作执行双能力
- 高风险动作可被审核和追踪
- 技术规则与业务规则职责清晰
- 前后端、知识库、工具、评测形成闭环

### 13.3 量化目标

建议二期结束后尽量达到：

- 售后专项评测集任务完成率 `>= 75%`
- 工具调用成功率 `>= 95%`
- 工单创建成功率 `>= 98%`
- 错误升级率 `<= 10%`
- 漏升级率 `<= 5%`
- 退款类事实性错误率 `<= 5%`

### 13.4 简历输出目标

最终项目要能清楚体现以下关键词：

- `LangGraph`
- `Agent Runtime`
- `Tool Calling`
- `Agentic RAG`
- `Human-in-the-loop`
- `Business Workflow`
- `Evaluation`
- `Enterprise AI Application`

---

## 14. 推荐下一步行动

如果二期要真正开始实施，建议先立刻推进以下 3 个动作：

1. 先完成 `Phase 1` 的数据库扩展设计
2. 再完成 `Phase 2` 的动作型工具设计
3. 然后立即补 `Phase 7` 的售后专项评测集

原因是：

- 没有真实业务库，后面的 Agent 行为都容易飘
- 没有动作型工具，平台仍然只是“会解释规则”
- 没有专项评测，后面每次优化都无法量化证明价值

建议二期第一个实际落地里程碑可以定义为：

> 支持“退款资格判断 + 提交退款申请 + 创建人工工单 + 退款专项评测”的最小售后闭环。

这会是一个非常适合作为下一阶段开发和简历升级的切入点。
