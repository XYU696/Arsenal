# EchoMind Phase 1 设计文档

## 1. 文档目标

本文档用于细化 EchoMind 的 `Phase 1：Runtime + Tool Calling`，重点回答以下问题：

1. 如何把当前“单轮路由 + 单次 LLM 回复”升级为 `SessionState + step-based runtime`
2. 单 Agent 场景下，runtime 应如何设计
3. 已有业务数据库工具如何接入 runtime
4. 应先实现哪些最小任务流，保证系统可跑、可测、可迭代

本文档只做设计说明，不直接修改代码。

---

## 2. 当前系统现状

当前 EchoMind 已具备以下能力：

- `api/main.py` 提供 `/chat` 主链路
- `agents/agent_orchestrator.py` 负责意图识别 + agent 路由 + 单次回复
- `mcp/tool_manager.py` 提供通用工具调用框架
- `mcp/business_tool_handlers.py` 已接入 5 个业务数据库工具
- `Redis + ChromaDB` 提供工作记忆与长期语义记忆
- `evaluation/` 已能跑 baseline

但当前仍然存在一个关键边界：

> 系统仍然是“收到消息 -> 判断意图 -> 选 Agent -> 单次回答”的一次性执行模式。

这意味着它还不具备真正的任务推进能力，例如：

- 先追问缺失信息
- 再查询业务事实
- 根据工具结果决定下一步动作
- 多轮维持任务状态
- 失败时升级或建单

因此，Phase 1 的目标不是“再加一个工具”，而是给现有系统补一个真正的 `runtime`。

---

## 3. Phase 1 的整体目标

Phase 1 只聚焦两件事：

1. 让系统拥有可持续推进任务的 `Agent Runtime`
2. 让 Agent 真正通过工具来查询事实、判断规则、创建升级工单

本阶段暂不追求：

- 多 Agent handoff
- 自由式开放规划
- 复杂长链反思
- 完整企业审批流
- Tool trace 的完整可视化前端

本阶段更适合的定位是：

> 做一个“单 Agent、受控循环、状态驱动、可调工具”的客服任务执行引擎。

---

## 4. 为什么要从单次调用升级为 step-based runtime

### 4.1 单次调用的边界

当前单次调用模型适合：

- FAQ
- 简单政策解释
- 通用咨询
- 单轮排障建议

但它不擅长：

- 信息不完整时主动追问
- 多轮任务中记住当前进行到哪一步
- 调用工具后根据结果决定下一步
- 对退款、账号异常、已知故障这种任务进行闭环处理

### 4.2 step-based runtime 的价值

引入 step-based runtime 后，系统将获得以下能力：

- 任务分步推进
- 缺信息时先补槽位
- 通过工具核实事实
- 按规则决定继续、升级还是结束
- 让会话变成“处理任务”而不是“只回答问题”

在客服场景下，这种改造的本质不是“让 Agent 想得更多”，而是：

> 让系统从回答器升级成任务处理器。

---

## 5. Phase 1 的设计原则

### 5.1 单 Agent 优先

本阶段不做多 Agent 协作，先只保留一个统一的客服执行 Agent。

原因：

- 先把任务执行闭环跑通比增加 Agent 数量更重要
- 单 Agent 更容易调试
- 有利于先验证 `runtime + tools + state` 这条主链路

### 5.2 受控循环，不做开放式自由 Agent

本阶段不采用完全自由的开放式 agent loop。

更推荐：

- 状态机驱动
- 限制最大步数
- 模型输出结构化“下一步动作”
- 工具白名单
- 高风险任务有明确退出路径

也就是说，本阶段更接近：

> 受控 ReAct + 状态机 + 结构化工具调用

### 5.3 复杂问题优先做最小闭环

本阶段先只覆盖 3 类最具代表性的任务：

1. 退款申请
2. 账号异常
3. 技术排障

这样可以在最小改动下验证 runtime 的价值。

---

## 6. 目标架构

Phase 1 完成后，建议的执行结构如下：

```text
/chat
  -> 读取 MemoryContext
  -> 读取 / 创建 SessionState
  -> Runtime.step(...)
       -> 判断任务类型
       -> 决定下一步动作
       -> 可选调用工具
       -> 更新 SessionState
       -> 生成当前轮回复
  -> 写回 SessionState
  -> 写回会话记忆
  -> 返回当前 step 结果
```

这里的关键变化是：

- `api/main.py` 不再直接依赖单次 `orchestrator.run()`
- `SessionState` 成为跨轮任务推进的核心
- 工具调用成为 runtime 的一部分，而不是 API 预处理的旁路逻辑

---

## 7. SessionState 设计

### 7.1 SessionState 的职责

`SessionState` 是 Agent Runtime 的核心对象，用来记录：

- 当前任务属于哪一类
- 当前进行到哪一步
- 已经收集到哪些信息
- 已调用过哪些工具
- 当前是否应升级
- 任务是否已结束

### 7.2 建议的数据结构

建议至少包含以下字段：

- `session_id`
- `user_id`
- `conv_id`
- `task_type`
- `task_state`
- `current_agent`
- `step_count`
- `collected_slots`
- `pending_action`
- `tool_history`
- `last_tool_result`
- `requires_handoff`
- `final_outcome`
- `created_at`
- `updated_at`

### 7.3 字段说明

`task_type`

用于标识当前任务类型，建议值：

- `faq`
- `refund`
- `account_issue`
- `technical_issue`
- `billing_issue`
- `unknown`

`task_state`

用于描述当前任务阶段，建议值：

- `new_request`
- `need_clarification`
- `ready_for_tool`
- `waiting_tool_result`
- `ready_to_answer`
- `needs_handoff`
- `completed`

`collected_slots`

用于保存任务推进中收集到的结构化信息，例如：

```json
{
  "order_no": "ORD-20260728-1001",
  "account_id": "ACC-ZM-01",
  "product_name": "EchoMind Pro",
  "plan_type": "yearly",
  "issue_category": "login"
}
```

`pending_action`

表示下一步预期要做什么，建议值：

- `ask_followup`
- `call_tool`
- `respond`
- `handoff`
- `finish`

`tool_history`

记录本次任务已经调用过哪些工具，用于：

- 避免重复调用
- 后续评测和调试
- 人工接管摘要

### 7.4 存储位置

本阶段建议先把 `SessionState` 存到 Redis。

原因：

- 你当前已经有 Redis
- state 生命周期天然适合短期缓存
- 简单直接，便于快速接入

建议 key 设计：

- `runtime:session:{conv_id}`

---

## 8. Runtime 的最小执行模型

### 8.1 Runtime 的职责

建议新增一个独立 runtime 模块，负责：

1. 读取 state
2. 分析用户输入
3. 判断当前任务类型与任务状态
4. 选择下一步动作
5. 如有需要则调用工具
6. 更新 state
7. 返回本轮结果

### 8.2 Runtime 不负责什么

runtime 本身不负责：

- 长期记忆检索细节
- 工具底层实现
- 复杂前端展示
- 多 Agent handoff 策略

这些能力后续可以逐步增强，但 Phase 1 先聚焦主链路。

### 8.3 建议的核心接口

建议后续代码结构里提供类似接口：

- `load_or_create_state(conv_id, user_id)`
- `classify_task(message, state)`
- `decide_next_action(message, state)`
- `execute_tool_if_needed(state)`
- `build_response(state, tool_result)`
- `save_state(state)`

最终 `/chat` 只需要调用一个入口，例如：

- `runtime.step(user_input, conv_id, user_id, context, history)`

---

## 9. 单 Agent 的动作设计

本阶段建议让模型输出“结构化下一步动作”，而不是直接自由回答。

### 9.1 建议的动作枚举

- `ask_followup`
- `call_tool`
- `respond`
- `handoff`
- `finish`

### 9.2 每个动作的含义

`ask_followup`

- 说明当前信息不足
- 本轮目标是继续追问

`call_tool`

- 说明已具备足够条件，可以查询业务事实或规则

`respond`

- 已有足够信息，可以给用户一个阶段性答复

`handoff`

- 需要转人工或创建工单

`finish`

- 当前任务已达成最终结果，本轮后可以关闭

### 9.3 为什么要做结构化动作

这样做有 4 个好处：

1. 减少自由式推理失控
2. 更容易加工具权限控制
3. 更容易把 runtime 调试清楚
4. 更容易做任务完成率评测

---

## 10. 最小任务流设计

本阶段建议先落地 3 条任务流。

### 10.1 退款任务流

目标：

- 收集退款所需基础信息
- 调退款规则工具
- 决定直接说明、继续追问或升级

最小状态流：

1. `new_request`
2. 判断是否缺订单 / 产品 / 套餐信息
3. 缺信息则 `ask_followup`
4. 信息够则 `call_tool(refund_policy_check)`
5. 根据规则结果：
   - 可继续说明 -> `respond`
   - 需人工审核 -> `handoff`
   - 不支持 -> `respond`
6. `finish`

依赖工具：

- `refund_policy_check`
- 可选 `order_lookup`
- 可选 `create_handoff_ticket`

### 10.2 账号异常任务流

目标：

- 判断账号是否锁定、受限、权限异常
- 给出可执行解释或升级路径

最小状态流：

1. `new_request`
2. 判断是否有 `account_id` 或 `email`
3. 缺信息则 `ask_followup`
4. 信息够则 `call_tool(account_status_lookup)`
5. 根据结果：
   - 正常但权限不符 -> `respond`
   - 锁定 / 受限 -> `handoff` 或 `respond + handoff`
6. `finish`

依赖工具：

- `account_status_lookup`
- 可选 `create_handoff_ticket`

### 10.3 技术排障任务流

目标：

- 判断是否有已知故障
- 如果是平台事故，给出准确信息
- 如果不是，给出基础排障步骤

最小状态流：

1. `new_request`
2. 判断问题类别是否明确
3. 不明确则 `ask_followup`
4. 明确则 `call_tool(incident_lookup)`
5. 根据结果：
   - 有已知故障 -> `respond`
   - 无已知故障 -> `respond`，给基础排障建议
   - 高风险 / 连续失败 -> `handoff`
6. `finish`

依赖工具：

- `incident_lookup`
- 可选 `create_handoff_ticket`

---

## 11. Tool Calling 在 Runtime 中的接入方式

本阶段不建议让 Agent 完全自由地产生任意工具调用。

建议做法：

- runtime 根据 `task_type + task_state` 决定“允许调用哪些工具”
- 模型只能在允许范围内选择动作
- tool 参数由 runtime 从 `collected_slots` 中拼装

### 11.1 工具白名单建议

`refund`

- `refund_policy_check`
- `order_lookup`
- `create_handoff_ticket`

`account_issue`

- `account_status_lookup`
- `create_handoff_ticket`

`technical_issue`

- `incident_lookup`
- `knowledge_search`
- `create_handoff_ticket`

### 11.2 为什么不直接开放全部工具

因为客服场景里：

- 工具本身有风险
- 工具参数往往依赖业务字段
- 完全自由的工具调用容易造成错误动作

所以本阶段更适合做“半开放式工具调用”。

---

## 12. 与现有模块的关系

### 12.1 `agent_orchestrator.py`

本阶段不建议直接删除现有编排器。

更合理的做法是：

- 保留现有 orchestrator 中的意图识别与基础 agent 能力
- 将单次 `run()` 拆出可复用的部分
- 新 runtime 先在外层包住 orchestrator，而不是完全推倒重写

也就是说，Phase 1 更适合“增量演进”，不是彻底重构。

### 12.2 `api/main.py`

未来 `/chat` 的主流程应从：

- 构建 `OrcReq`
- 直接 `orchestrator.run()`

改成：

- 读取 MemoryContext
- 读取或创建 SessionState
- 调 runtime 执行一步
- 保存 SessionState
- 保存记忆

### 12.3 业务数据库工具

已接入的 5 个数据库工具将作为 runtime 的事实来源：

- `account_status_lookup`
- `order_lookup`
- `refund_policy_check`
- `incident_lookup`
- `create_handoff_ticket`

本阶段 runtime 要做的不是重新实现这些工具，而是：

> 让任务流程在合适的时机调用它们。

---

## 13. 风险控制

### 13.1 步数限制

建议每个任务设置最大步数，例如：

- `max_steps = 5`

超过后自动进入：

- `needs_handoff`

### 13.2 高风险任务升级条件

建议以下情况自动升级：

- 退款规则要求人工审核
- 账号处于 `restricted / locked / suspended`
- 工具连续失败
- 用户明确要求人工
- 模型多次无法给出明确下一步

### 13.3 避免死循环

建议加入以下保护：

- 同一个 state 下连续两次相同 `pending_action` 时触发 fallback
- 同一个工具连续重复调用次数受限
- 若 `collected_slots` 长时间无新增则停止追问并升级

---

## 14. 实施顺序建议

建议按下面顺序推进：

### Step 1

新增 `SessionState` 数据结构，并先接 Redis 持久化

### Step 2

新增 `runtime.py`，实现最小 `step()` 主入口

### Step 3

先做退款任务流，作为第一条完整闭环

### Step 4

接 `refund_policy_check` 与 `create_handoff_ticket`

### Step 5

补账号异常任务流和 `account_status_lookup`

### Step 6

补技术排障任务流和 `incident_lookup`

### Step 7

最后再把现有 `/chat` 接到 runtime 主链路

---

## 15. Phase 1 验收标准

本阶段完成后，系统应至少满足：

1. 同一个 `conv_id` 可以跨多轮推进任务
2. 系统能够根据缺失信息主动追问
3. 系统能够在合适时机调用数据库工具
4. 工具结果会影响下一步动作
5. 退款、账号异常、技术排障三类任务可以形成最小闭环
6. 高风险任务可以触发升级或建单

---

## 16. 建议新增文件

后续实现阶段建议新增：

- `agents/session_state.py`
- `agents/runtime.py`
- `agents/runtime_policies.py`

可选新增：

- `agents/runtime_types.py`

如果你希望结构更简单，也可以只先新增 2 个文件：

- `agents/session_state.py`
- `agents/runtime.py`

把 policy 先放在 `runtime.py` 中，避免过早拆分。

---

## 17. 一句话结论

Phase 1 的核心不是“让模型多思考几轮”，而是：

> 给 EchoMind 增加一个受控的、状态驱动的、可调用工具的单 Agent 执行 runtime，让它能真正完成客服任务，而不是只生成一段答案。
