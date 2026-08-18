# EchoMind 基于 LangGraph 的单 Agent 设计文档

## 1. 文档目标

本文档用于说明如何将当前 EchoMind 的 `Phase 1` 单 Agent runtime，从“手写状态机 + 受控工具调用”的实现，演进为基于 `LangGraph` 的单 Agent loop。

文档重点回答以下问题：

1. 为什么 EchoMind 适合切换到 LangGraph
2. LangGraph 版和当前手写 runtime 版的本质区别是什么
3. 单 Agent Graph 应该如何设计
4. 已有模块如何映射到 LangGraph
5. 应该按照什么顺序迁移，避免推倒重来

本文档只做架构设计，不直接修改代码。

---

## 2. 为什么考虑 LangGraph

### 2.1 当前手写 runtime 的优点

当前 EchoMind 已经有了：

- `SessionState`
- `SingleAgentRuntime`
- 工具白名单
- 三条最小任务流
- 业务数据库工具

这说明项目已经从“单轮回复系统”迈向了“任务执行系统”。

当前方案的优点是：

- 简洁
- 易控
- 容易调试
- 适合快速验证退款 / 账号异常 / 技术排障闭环

### 2.2 当前手写 runtime 的边界

但如果目标是做一个更像“现代 Agent 系统”的项目，当前方案有几个明显边界：

- 任务流主要由代码预定义，不是 Agent 自主决定下一步动作
- 工具调用由状态机主导，而不是模型主导
- 缺少标准化的 graph runtime 结构
- 后续如果要接入多步循环、人工中断、持久化恢复、节点 trace，会越来越重

### 2.3 LangGraph 更适合的地方

LangGraph 适合 EchoMind 的原因主要有 4 个：

1. 它天然支持 `stateful agent`
2. 它天然支持 `tool call -> observation -> agent loop`
3. 它支持显式 graph，便于可视化、调试、监控
4. 它适合在客服场景中做“受控自主循环”，而不是完全自由的黑盒 Agent

所以 EchoMind 如果切换到 LangGraph，最值得获得的不是“更多功能”，而是：

> 一个更标准的 Agent Runtime 结构。

---

## 3. LangGraph 版和当前手写 runtime 版的区别

### 3.1 当前方案：状态机主导

当前方案更像：

- 程序决定任务步骤
- 程序决定何时调哪个工具
- 模型主要负责内容生成和粗粒度判断

可以概括为：

> 程序主导流程，模型参与执行。

### 3.2 LangGraph 方案：Agent loop 主导

LangGraph 方案更像：

- 程序定义状态、节点和边
- 模型决定下一步动作
- 工具调用结果作为 observation 回到状态
- Graph 控制循环边界和退出条件

可以概括为：

> Graph 主导运行边界，模型主导下一步动作。

### 3.3 两者的本质区别

它们最本质的区别不是“是不是用 LangGraph”，而是：

- 当前方案偏 `workflow engine`
- LangGraph 方案偏 `agent runtime`

所以如果你的目标是更贴近 Agent 开发岗位，LangGraph 方案会更有代表性。

---

## 4. EchoMind 在 LangGraph 下的目标形态

### 4.1 设计目标

我们希望得到的不是一个“开放式自由思考”的危险 Agent，而是一个：

- 单 Agent
- 有状态
- 会循环
- 会用工具
- 有边界
- 可恢复
- 可升级

也就是说，目标不是完全自由的自治 Agent，而是：

> 一个适合客服场景的受控 ReAct Agent。

### 4.2 不做什么

LangGraph 版第一阶段暂时不做：

- 多 Agent 协作
- planner + reviewer 双层结构
- 长链复杂规划
- 真正的任务审批流
- 完整前端可视化 trace

先把单 Agent loop 做稳，才是最优路线。

---

## 5. 单 Agent Graph 总体设计

### 5.1 推荐总图

建议单 Agent Graph 结构如下：

```text
START
  -> load_state
  -> classify_or_resume
  -> agent_node
       -> if tool_call  -> tool_node    -> agent_node
       -> if ask_followup -> save_state -> END
       -> if handoff      -> handoff_node -> save_state -> END
       -> if finish       -> save_state -> END
```

这是一个非常典型的“受控 ReAct loop”。

### 5.2 这个图的核心思想

它不是预定义所有任务细节，而是：

1. 保留状态
2. 让模型输出“下一步动作”
3. 工具执行后把 observation 写回状态
4. 再让模型决定下一步
5. 直到结束

这比手写状态机更接近真正的 Agent。

---

## 6. Graph State 设计

### 6.1 为什么还需要 State

即使切到 LangGraph，状态仍然是核心。

LangGraph 的运行本质也是：

- 状态
- 节点
- 边

所以你当前已经有的 `SessionState` 思路不是白做，而是可以升级成 Graph State。

### 6.2 建议的 Graph State 字段

建议 LangGraph 版状态至少包含以下字段：

- `session_id`
- `user_id`
- `conv_id`
- `messages`
- `task_type`
- `step_count`
- `collected_slots`
- `allowed_tools`
- `tool_history`
- `last_tool_result`
- `last_action`
- `requires_handoff`
- `handoff_reason`
- `final_response`
- `final_outcome`
- `is_completed`

### 6.3 字段说明

`messages`

用于保存当前 Agent loop 可见的对话与 observation 序列。

建议包含：

- 用户消息
- Agent 中间动作摘要
- 工具结果 observation

`task_type`

仍建议保留粗粒度任务类型，例如：

- `faq`
- `refund`
- `account_issue`
- `technical_issue`
- `billing_issue`
- `escalation`
- `unknown`

它在 LangGraph 版里主要作用是：

- 初始化上下文
- 限制工具白名单
- 帮助模型少走弯路

`allowed_tools`

这是 LangGraph 版里非常重要的 guardrail。

即使让 Agent 自主决定工具，也不应该让它在所有工具中无限自由选择。

### 6.4 是否复用现有 SessionState

建议复用现有 `SessionState` 思路，但做以下调整：

- 从“状态机字段”升级为“Graph State 字段”
- 去掉过于死板的 `task_state -> pending_action` 强耦合
- 增加 `messages`、`last_action`、`is_completed`

---

## 7. 节点设计

### 7.1 `load_state`

职责：

- 从 Redis 读取当前会话状态
- 如果不存在则创建新状态
- 初始化 `step_count`

输入：

- `user_id`
- `conv_id`
- `message`

输出：

- Graph State

### 7.2 `classify_or_resume`

职责：

- 如果是新任务，则做粗粒度任务分类
- 如果是已有任务，则恢复上下文
- 初始化工具白名单

为什么保留这个节点：

- 虽然 LangGraph 版更自主，但仍不建议一开始完全取消任务分类
- 客服场景里，粗粒度分类可以显著提升 Agent loop 的稳定性

### 7.3 `agent_node`

这是核心节点。

职责：

- 读取当前状态
- 基于用户消息、历史、工具 observation 生成下一步动作

这里不直接生成普通自然语言回复，而是生成结构化动作，例如：

```json
{
  "thought": "当前需要先核实退款规则",
  "action": "call_tool",
  "tool_name": "refund_policy_check",
  "tool_args": {
    "product_name": "EchoMind Pro",
    "plan_type": "yearly"
  }
}
```

推荐动作枚举：

- `ask_followup`
- `call_tool`
- `respond`
- `handoff`
- `finish`

### 7.4 `tool_node`

职责：

- 接收 `agent_node` 产生的工具调用动作
- 校验工具是否在 `allowed_tools` 中
- 调用 `MCPToolManager`
- 将结果写入 `last_tool_result`
- 记录 `tool_history`

输出给后续 `agent_node` 的内容，就是 observation。

### 7.5 `handoff_node`

职责：

- 调用 `create_handoff_ticket`
- 将工单信息写回状态
- 生成最终用户回复

这个节点的意义在于：

- 让“转人工”成为正式动作
- 而不是只说一句“建议转人工”

### 7.6 `save_state`

职责：

- 把当前状态写回 Redis
- 标记是否完成
- 更新 step 计数与时间戳

这一步对客服场景非常重要，因为多轮连续推进依赖它。

---

## 8. 边和条件判断

### 8.1 主要条件边

建议在 `agent_node` 后做条件分支：

- `call_tool` -> `tool_node`
- `ask_followup` -> `save_state` -> END
- `handoff` -> `handoff_node` -> `save_state` -> END
- `finish` -> `save_state` -> END

### 8.2 为什么 `tool_node` 后要回到 `agent_node`

因为这才是 Agent loop 的关键：

- Agent 先决定调工具
- 工具执行后返回 observation
- Agent 再决定下一步

这和纯 workflow 的区别就在这里。

### 8.3 步数上限

建议加全局 guardrail：

- `max_steps = 5` 或 `6`

如果超过：

- 自动进入 `handoff`

这样可以防止死循环。

---

## 9. 工具设计在 LangGraph 里的位置

### 9.1 当前工具可以直接复用

EchoMind 当前已经接好的工具可以直接沿用：

- `knowledge_search`
- `account_status_lookup`
- `order_lookup`
- `refund_policy_check`
- `incident_lookup`
- `create_handoff_ticket`

这意味着 LangGraph 迁移的重点不是重写工具，而是：

> 让 Agent loop 真正调这些工具。

### 9.2 为什么仍然要保留工具白名单

虽然 LangGraph 版更自主，但客服 Agent 仍然不能完全放开所有工具。

建议按粗粒度任务限制：

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

### 9.3 为什么不建议完全自由工具调用

因为客服场景里：

- 动作有风险
- 用户信息不完整很常见
- 工具结果和业务规则强绑定

所以 LangGraph 版虽然是 Agent loop，但仍应是“受控自主”。

---

## 10. 客服场景下的 ReAct 形态

### 10.1 不建议纯开放式 ReAct

在客服领域，不建议做：

- 无限思考
- 任意工具选择
- 任意行动

这会导致：

- 延迟失控
- 错误动作
- 审计困难

### 10.2 推荐“受控 ReAct”

建议形态是：

1. 有状态
2. 有工具白名单
3. 模型输出结构化动作
4. 有 step 限制
5. 有人工升级出口

这在客服场景里是最合理的 Agent loop 设计。

---

## 11. 意图识别在 LangGraph 版里还要不要保留

结论：

> 要保留，但角色变化了。

它不再是“路由到哪个 agent”的核心，而是：

- 初始化 `task_type`
- 限定工具范围
- 提供任务上下文

### 11.1 为什么先保留粗粒度意图

因为第一阶段切 LangGraph 时，系统最需要的是稳定性，而不是最细的任务分类。

粗粒度任务类型已经足够支持：

- 工具白名单
- 初始 prompt 提示
- 任务流方向感

### 11.2 后续如何增强

后续可以逐步扩展为：

- 粗粒度 `task_type`
- 细粒度 `subtask_type`

但第一阶段不建议强依赖细粒度 intent。

---

## 12. 与当前 EchoMind 模块的映射关系

### 12.1 `api/main.py`

仍作为主入口，但职责将变化为：

- 收到 `/chat`
- 读取记忆上下文
- 调用 LangGraph graph 执行一次 loop
- 写回记忆
- 返回最终结果

### 12.2 `agents/session_state.py`

可继续保留，但建议重构为更适合 Graph State 的结构。

### 12.3 `agents/runtime.py`

当前这个文件将不再作为主要运行时实现。

未来更推荐：

- 保留作为兼容层或旧实现
- 新增 LangGraph 版实现文件，例如：
  - `agents/langgraph_runtime.py`

### 12.4 `mcp/tool_manager.py`

完全保留。

LangGraph 版只是调用它，不需要替换。

### 12.5 `integrations/business_db/`

完全保留。

它继续作为工具事实来源。

---

## 13. 迁移策略

### 13.1 不建议直接删除现有 runtime

更推荐双轨迁移：

1. 保留当前手写 runtime
2. 新增 LangGraph 版 runtime
3. 用配置或接口切换

这样可以：

- 快速对比两种实现
- 避免一次性重构风险过大
- 继续复用现有 baseline

### 13.2 推荐迁移顺序

#### Step 1

新增 LangGraph 版 state 定义

#### Step 2

新增 LangGraph graph 结构

#### Step 3

先接退款任务，作为第一条完整 loop

#### Step 4

接账号异常和技术排障

#### Step 5

把 `/chat` 切换到 LangGraph 版

#### Step 6

对比 baseline 前后结果

---

## 14. 第一阶段实现范围建议

为了控制复杂度，LangGraph 第一版建议只做：

- 单 Agent
- 粗粒度任务类型
- 4 个动作
  - `ask_followup`
  - `call_tool`
  - `handoff`
  - `finish`
- 4 个核心工具
  - `refund_policy_check`
  - `account_status_lookup`
  - `incident_lookup`
  - `create_handoff_ticket`

暂时不做：

- `knowledge_search` 主驱动
- 细粒度子任务规划
- 多 Agent handoff
- reviewer 节点
- planner 节点

---

## 15. LangGraph 版成功标准

如果 LangGraph 单 Agent 版设计成功，应该至少满足：

1. 同一个 `conv_id` 下，Agent 可以多轮连续推进任务
2. Agent 可以自主决定是否追问或调工具
3. 工具结果会成为下一轮推理输入
4. Agent 可以在无法继续时转人工并建单
5. 系统具有明确的循环边界，不会无限运行

---

## 16. 一句话结论

EchoMind 切换到 LangGraph 的真正价值，不是“换个框架”，而是：

> 把当前偏 workflow 的手写状态机 runtime，升级成一个更标准的、状态持久化的、可工具循环的单 Agent Runtime，从而让项目更接近真正的 Agent 系统。
