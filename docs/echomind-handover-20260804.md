# EchoMind 项目开发交接文档

更新日期：2026-08-04

## 1. 项目当前定位

EchoMind 当前正在从“通用智能客服原型”升级为“面向电商售后场景的企业级客服 Agent 平台”。

项目当前的核心目标是：

- 基于 `LangGraph` 构建受控单 Agent runtime
- 支持工具调用，查询结构化业务事实
- 支持知识库检索与规则解释
- 支持人工升级与工单闭环
- 逐步收敛到电商售后场景
- 通过评测集和量化指标驱动优化

---

## 2. 项目从开始到现在的主要改动

### 2.1 初始阶段

项目最初是一个面向企业客服场景的工程化 LLM 原型，已具备：

- `FastAPI` 后端主链路
- `Next.js` 前端控制台
- 意图识别与多角色路由
- `ChromaDB` 知识库检索
- `Redis + ChromaDB` 记忆能力
- `Prometheus` 指标暴露
- 评测入口与基础监控

这一阶段更像“单轮回复式客服系统”，还不是真正的 Agent。

### 2.2 Phase 0：评测基线

已经补齐：

- `evaluation/datasets/customer_support_eval_v1.jsonl`
- 数据集说明与标签文档
- `evaluation/run_eval.py`
- 报告 schema

当前系统已经具备跑 baseline 的能力，可以用于后续对比优化前后效果。

### 2.3 Phase 1：Runtime Agent 化

已经完成：

- 引入 `SessionState`
- 从一次性 orchestrator 迁移到 `LangGraph` 版 `SingleAgentRuntime`
- `/chat` 主链路优先走 runtime
- orchestrator 只在 runtime 异常时兜底

当前 runtime 已具备：

- `load_state`
- `classify_or_resume`
- `agent_node`
- `tool_node`
- `handoff_node`
- `save_state`

这意味着系统已经从“单次调用大模型回复”升级为“受控单 Agent ReAct 循环”。

### 2.4 第一版业务数据库与工具

已经完成：

- `SQLite` 业务数据库第一版
- 账号、订单、退款规则、故障、工单等数据表
- 5 个初始业务工具：
  - `account_status_lookup`
  - `order_lookup`
  - `refund_policy_check`（后续已重构）
  - `incident_lookup`
  - `create_handoff_ticket`

### 2.5 退款链路重构

已经完成：

- 将 `refund_policy_check` 重命名为 `refund_policy_lookup`
- 明确它只负责“查退款政策”
- 新增 `refund_eligibility_check`
  - 用于针对具体订单判断退款资格
- 新增 `create_refund_request`
  - 用于真正创建退款申请

当前退款工具职责已经拆清：

- `refund_policy_lookup`：查政策
- `refund_eligibility_check`：查具体订单资格
- `create_refund_request`：执行退款申请动作

### 2.6 电商售后二期业务建模

已经新增电商售后版数据库设计与种子数据：

- `data/business/schema_v2.sql`
- `data/business/seed_v2.sql`

新增的数据域包括：

- `order_items`
- `shipments`
- `payment_attempts`
- `refund_requests`
- `return_requests`
- `exchange_requests`
- `coupon_compensations`
- `warehouse_incidents`
- `after_sales_tickets`
- `agent_audit_logs`

并且初始化脚本已支持：

- `python3 -m integrations.business_db.init_db --profile ecommerce_v2`

### 2.7 第一批电商售后工具

已完成底层 `BusinessDB` 方法与 handler：

- `order_items_lookup`
- `shipment_tracking_lookup`
- `payment_attempt_lookup`
- `refund_request_lookup`
- `refund_eligibility_check`
- `create_refund_request`

这些工具已接入：

- `mcp/business_tool_handlers.py`
- `api/main.py`
- `agents/runtime.py` 工具白名单和参数归一化

---

## 3. 当前项目的核心结构

### 3.1 主执行链路

当前 `/chat` 主链路是：

1. 前端发请求到 `/chat`
2. 后端读取记忆上下文
3. 进入 `LangGraph` runtime
4. runtime 根据任务类型和会话状态决定动作
5. 按需调用业务工具或知识库工具
6. 必要时创建人工工单
7. 返回回复结果与元信息给前端

### 3.2 当前主要模块

- `api/main.py`
  - 主入口，工具注册，`/chat` 接口

- `agents/runtime.py`
  - 当前最核心的 LangGraph 单 Agent runtime

- `agents/session_state.py`
  - 会话状态定义与 Redis 持久化

- `mcp/business_tool_handlers.py`
  - 业务工具 handler

- `integrations/business_db/db.py`
  - SQLite 业务库访问层

- `data/business/schema_v2.sql`
  - 电商售后二期数据库结构

- `data/business/seed_v2.sql`
  - 电商售后二期种子数据

- `evaluation/`
  - baseline 评测集和评测脚本

- `frontend/components/chat-studio.tsx`
  - 当前前端对话工作台

---

## 4. 当前已经达到的状态

当前项目已经实现：

- 从单次 LLM 回复迁移到 LangGraph 单 Agent loop
- Agent 可调用工具查询数据库
- Agent 可根据规则决定是否升级人工
- Agent 可写工单到数据库
- 已引入电商售后二期数据结构
- 已接入第一批售后工具

目前已经不再只是一个“客服问答 Demo”，而是开始具备：

- 结构化业务事实查询
- 动作型售后工具雏形
- 人工升级闭环
- 评测基线

---

## 5. 当前已知问题与边界

虽然项目已完成较大升级，但当前仍存在以下边界：

### 5.1 Runtime 可观测性不足

当前调试 runtime 仍然较依赖后端日志。

缺少：

- 前端执行轨迹可视化
- 每一轮 Agent 执行步骤的结构化 trace 展示

这会影响联调效率。

### 5.2 复合问题处理仍不够强

用户一句话同时提出：

- 账号问题
- 订单问题
- 退款问题

当前单 Agent 往往只会优先抓住其中一个主问题，不能稳定拆分多个子任务。

### 5.3 动作型工具虽已接入，但尚未完全形成闭环

例如：

- `create_refund_request` 已有 handler 和注册
- 但 runtime 还没有完整实现“用户确认 -> 自动创建退款申请”的主链路策略

### 5.4 前端还偏演示页

当前前端已经能展示：

- 回复
- intent
- agent
- latency
- knowledge
- escalated
- ticket_no

但还缺：

- 完整执行轨迹
- 工具调用过程
- 任务状态
- 审核状态
- 售后申请状态面板

### 5.5 二期知识库、Skill、HITL 还未进入完整开发

目前已完成的是：

- 二期数据库设计
- 二期工具设计与第一批工具接入

但还没有完整推进：

- 电商售后知识库重建
- Skill 业务化
- 人工审核闭环
- 售后专项评测集 v2

---

## 6. 当前建议使用方式

### 6.1 初始化数据库

基础版数据库：

```bash
python3 -m integrations.business_db.init_db
```

电商售后二期数据库：

```bash
python3 -m integrations.business_db.init_db --profile ecommerce_v2
```

### 6.2 启动项目

当前建议仍通过 Docker 启动：

```bash
docker compose build --no-cache echomind
docker compose up -d echomind
docker compose logs -f echomind
```

### 6.3 测试重点

建议优先测试：

- 账号查询
- 退款政策咨询
- 指定订单退款资格判断
- 重复扣费查询
- 工单创建闭环

---

## 7. 下一步任务建议

当前最建议优先推进的任务有 3 个。

### 7.1 第一优先级：做执行轨迹 Trace V1

目标：

- 不再只靠日志调试
- 在前端完整展示 Agent 每一步执行轨迹

建议内容：

- 后端在 runtime 中记录 trace
- `/chat` 返回 trace
- 前端展示：
  - 节点
  - decision
  - tool
  - tool args
  - tool result
  - handoff / ticket

这是当前最能提升开发效率的一步。

### 7.2 第二优先级：补退款动作闭环

目标：

- 当用户明确确认申请退款时
- runtime 自动调用 `create_refund_request`
- 返回 `refund_no`

这样退款场景就能形成真正的闭环：

- 查政策
- 查资格
- 提交退款申请
- 必要时转人工

### 7.3 第三优先级：继续扩充二期工具

下一批推荐实现：

- `create_return_request`
- `create_exchange_request`
- `issue_coupon_compensation`

这样项目会更完整地覆盖电商售后主链路。

---

## 8. 后续整体路线

当前项目后续建议继续按二期规划推进：

1. `Phase 2`
   - 售后工具体系继续补齐
2. `Phase 3`
   - 售后知识库与 Agentic RAG
3. `Phase 4`
   - Skill 体系业务化
4. `Phase 5`
   - Runtime 业务增强与复合问题处理
5. `Phase 6`
   - Human-in-the-loop 审核闭环
6. `Phase 7`
   - 售后专项评测集
7. `Phase 8`
   - 前端平台化

---

## 9. 一句话总结

当前 EchoMind 已经完成了从“单轮客服问答系统”到“基于 LangGraph 的受控单 Agent 售后客服系统”的第一轮升级，二期已经完成数据层和第一批售后工具的基础建设。

接下来最关键的工作是：

> 补齐执行轨迹可视化、退款动作闭环和更多售后动作工具，让项目真正变成一个可调试、可评测、可落地的电商售后 Agent 平台。
