# EchoMind Agent 化升级路线图

## 1. 文档目标

本文档面向当前 EchoMind 项目，给出一份可执行的升级开发计划，把现有“工程化客服 LLM 原型”逐步升级为更符合 2026 年 AI 应用开发 / Agent 开发岗位预期的“生产级客服 Agent 系统”。

升级重点覆盖 8 个方向：

1. 真正拥有 Agent Runtime
2. Agent Tool Calling
3. Agentic RAG
4. Agent Memory
5. LLMOps
6. 企业客服能力
7. Multi-Agent
8. 可量化指标与评测集

本文档默认基于当前代码结构推进：

- `api/main.py`
- `agents/agent_orchestrator.py`
- `core/intent_recognizer.py`
- `mcp/tool_manager.py`
- `mcp/knowledge_base.py`
- `memory/conversation_memory.py`
- `monitor/performance_monitor.py`
- `evaluation/evaluator.py`

---

## 2. 当前项目现状与升级原则

### 2.1 当前现状

从现有实现看，EchoMind 已具备以下基础：

- `FastAPI` 主链路与前端控制台
- 意图识别 + 路由编排
- `ChromaDB` RAG 检索
- `Redis + ChromaDB` 三级记忆
- `Prometheus` 指标暴露
- 端到端评测入口
- Skills 热加载

但目前仍存在几个关键边界：

- 编排器仍以“一次性路由 + 单次回复”为主，不是真正的 Agent Runtime
- 工具层已存在，但 Agent 并未自主调用工具
- RAG 仍以 API 预检索拼接上下文为主，不是 Agentic RAG
- 记忆更偏 conversation memory，不是任务/状态/策略驱动的 agent memory
- 评测、trace、回归、A/B、成本治理还不够系统
- 多 Agent 更偏 prompt 分工，而非 handoff / supervisor / workflow 协作

### 2.2 升级原则

升级时遵循以下原则：

1. 不追求“多加几个 Agent”，而是优先补齐 runtime、tool、eval、memory、ops 闭环。
2. 尽量复用现有模块，在当前代码基础上演进，而不是推倒重写。
3. 每完成一个阶段都跑固定评测集，记录前后变化，沉淀成简历可写的量化指标。
4. 优先落地“企业客服真问题”：退款、工单、账号异常、技术排障、投诉升级、人机协同。

---

## 3. 2026 年值得对齐的 Agent 工程实践

这一节用于回答“最新项目在用什么技术，我在 EchoMind 里怎么运用”。

### 3.1 Agent Runtime

2025-2026 年更成熟的 Agent 系统不再停留在单轮 prompt routing，而是强调：

- stateful runtime
- handoff
- supervisor / specialist 模式
- tool execution loop
- traceable execution spans

可参考的实践：

- OpenAI Agents SDK：`handoffs`、`tracing`
- LangGraph：`StateGraph`、supervisor / graph workflow
- Anthropic 的 agent engineering：强调 workflow、tool use、明确状态边界

在 EchoMind 中的具体应用：

- 保留现有 `AgentOrchestrator`，但将其重构为 `runtime + state machine + execution loop`
- 把单次 `run()` 改造成可多步推进的 `run_session()` / `step()`
- 为每步写入 trace、tool call、state snapshot、outcome

### 3.2 Agent Tool Calling

最新客服 Agent 项目通常使用：

- JSON Schema tool definition
- structured output
- approval / confirmation for high-risk actions
- retry / timeout / idempotency
- MCP 作为统一工具接入协议

在 EchoMind 中的具体应用：

- 将 `mcp/tool_manager.py` 从“API 层工具底座”升级为“Agent Runtime 工具执行器”
- 为知识检索、订单查询、退款校验、工单创建、FAQ 查询等能力统一建模
- 对退款、账号变更等高风险动作加入审批确认 / 二次确认

### 3.3 Agentic RAG

RAG 的最新方向不只是“检索到内容”，而是：

- query understanding / decomposition
- hybrid retrieval
- citation grounding
- answerability / abstain
- retrieval trace
- retrieval evaluation

在 EchoMind 中的具体应用：

- 不再固定在 `/chat` 前预检索
- 改为 Agent 在需要时自行决定检索、改写、重排、再次检索
- 引入引用片段、拒答与转人工策略

### 3.4 Agent Memory

最新记忆实践更强调：

- working memory
- episodic memory
- semantic memory
- user state / profile
- task state memory
- write policy / retrieval policy

在 EchoMind 中的具体应用：

- 保留 Redis 短期记忆
- 将 Chroma 中长期记忆拆成“情景记忆、用户事实、任务状态、交接摘要”
- 只把高价值事实写入长期记忆，并记录时间戳、来源、置信度

### 3.5 LLMOps

2026 年生产级 Agent 项目通常会补齐：

- traces
- evals
- regression suite
- prompt / tool / KB versioning
- cost and latency budgets
- online feedback and replay

在 EchoMind 中的具体应用：

- 将 `evaluation/` 扩成固定数据集 + 自动回归 + 版本对比
- 将 `monitor/` 扩成 trace + metrics + alerts + badcase replay
- 接入 `OpenTelemetry` 作为统一 trace 标准，必要时接 LangSmith / Phoenix

### 3.6 企业客服能力

企业客服不是只回答问题，而是要处理：

- 风险识别
- 身份校验
- 敏感信息脱敏
- SLA 和升级流程
- 人工接管
- 审计日志

在 EchoMind 中的具体应用：

- 新增 `EscalationAgent` / `ComplianceGuard`
- 工具调用前后增加 policy check
- 为人工客服输出可读的会话摘要、用户状态和已执行动作

---

## 4. 总体阶段规划

建议分 6 个阶段推进。

### Phase 0：建立基线与评测集

目标：

- 在动大功能之前先固定评测口径
- 建立当前系统的性能、质量、成本基线

阶段产出：

- 评测集 `100~300` 条
- baseline 指标报表
- badcase 标签体系

### Phase 1：Runtime + Tool Calling

目标：

- 让 EchoMind 从“路由系统”升级成“能多步执行的 Agent Runtime”
- 让 Agent 真正用上工具，而不是 API 预处理工具

### Phase 2：Agentic RAG + Agent Memory

目标：

- 让检索与记忆成为 Agent 决策的一部分
- 提升复杂客服任务的连续性、可解释性和 groundedness

### Phase 3：企业客服闭环 + Multi-Agent

目标：

- 支持更真实的业务流程：退款、升级、人工接管、投诉、工单
- 支持 supervisor + specialist handoff

### Phase 4：LLMOps 与生产观测

目标：

- 建立可观测、可评测、可回放、可回归的工程闭环

### Phase 5：量化优化与简历包装

目标：

- 连续迭代 2~3 轮
- 沉淀最终简历数据、面试话术、项目亮点

---

## 5. Phase 0：建立基线与评测集

### 5.1 技术目标

建立一套固定评测体系。之后每次改动 runtime、tool、memory、RAG、prompt、skills 时，都跑同一批评测。

### 5.2 评测集设计

建议先做 `180` 条左右，后续扩到 `300` 条。

分类建议：

- 通用咨询：`40`
- 退款/账单：`35`
- 技术支持：`40`
- 账号/权限：`20`
- 投诉/升级：`15`
- 多意图混合：`15`
- 需拒答/需转人工：`15`

每条样本包含字段：

- `id`
- `user_query`
- `history`
- `expected_intent`
- `expected_agent`
- `requires_tool`
- `expected_tools`
- `requires_rag`
- `expected_outcome`
- `gold_answer_points`
- `risk_level`
- `must_escalate`

建议新增目录：

- `evaluation/datasets/customer_support_eval_v1.jsonl`
- `evaluation/datasets/customer_support_eval_v1_readme.md`

### 5.3 评测维度

必须评：

- Intent Accuracy
- Intent Macro-F1
- Tool Selection Accuracy
- Task Completion Rate
- Grounded Response Rate
- Escalation Precision / Recall
- Average Latency
- Average Token Usage
- Tool Success Rate

建议加：

- Hallucination Rate
- Repeated Question Rate
- Human Handoff Rate
- Cost per Session

### 5.4 开发任务

1. 重构 `evaluation/evaluator.py`，支持从数据集批量回放。
2. 为每条样本保存：
   - 最终回复
   - intent
   - agent
   - tool calls
   - knowledge citations
   - latency
   - token usage
   - escalation decision
3. 为结果增加 `json` 和 `markdown` 双格式报表。
4. 增加基线命令，例如：
   - `python -m evaluation.run_eval --dataset customer_support_eval_v1`
5. 在前端 `eval lab` 展示关键指标和版本对比。

### 5.5 验收标准

- 可以一键跑完整评测集
- 有一份 baseline 报告
- 每次迭代都能做前后版本对比

---

## 6. Phase 1：真正拥有 Agent Runtime

### 6.1 目标

将当前 `AgentOrchestrator.run(req)` 的“一次性决策”模式升级为“有状态、可多步推进、可观测”的 Agent Runtime。

### 6.2 推荐技术与做法

建议吸收以下实践，但在 EchoMind 内自行实现轻量 runtime：

- OpenAI Agents SDK 的 `handoff` 和 trace span 思路
- LangGraph 的 state graph 思路
- Anthropic 强调的 workflow first，而不是无限 loop

为什么不直接全面换框架：

- 当前项目已经有自己的 orchestrator / tool / memory / monitor
- 最优路线是“借鉴最新 runtime 思路”，而不是重写全部基础设施

### 6.3 具体设计

建议新增：

- `agents/runtime.py`
- `agents/session_state.py`
- `agents/policies.py`

核心状态对象建议包含：

- `session_id`
- `user_id`
- `current_agent`
- `task_type`
- `task_state`
- `collected_slots`
- `pending_action`
- `tool_history`
- `memory_context`
- `knowledge_context`
- `escalation_state`
- `final_outcome`

runtime 循环建议：

1. 读取 session state
2. 选择当前 agent
3. 判断是否需要工具
4. 执行工具 / handoff / ask follow-up
5. 更新状态
6. 满足完成条件则结束，否则继续下一步

### 6.4 开发任务

1. 抽离 `AgentOrchestrator` 中的路由逻辑，拆成：
   - `route_initial()`
   - `step()`
   - `complete()`
2. 新增 `SessionState` 数据模型，落盘到 Redis。
3. 新增任务状态机，先覆盖 3 类核心流程：
   - 退款申请
   - 账号异常
   - 技术排障
4. 实现最大步数限制，例如 `max_steps=6`，防止 agent loop 失控。
5. 为每一步写入 trace：
   - 当前状态
   - 输入消息
   - 选择的 agent
   - 触发的工具
   - 输出结果
6. 对 `conv_id` 绑定 session state，实现真正跨轮执行。

### 6.5 验收标准

- 一个退款/排障任务能跨 2~6 步推进
- 中途断开后可恢复执行
- 可以看到 step 级运行轨迹

### 6.6 重点指标

- Task Completion Rate
- Average Steps per Task
- Session Resume Success Rate
- Invalid Loop Rate
- Average Resolution Time

---

## 7. Phase 1：Agent Tool Calling

### 7.1 目标

将现有 `MCPToolManager` 从“工具平台”升级为“Agent Runtime 的执行中枢”。

### 7.2 推荐技术与做法

最新实践建议：

- 所有工具采用 JSON Schema
- 工具输出结构化
- 高风险工具增加 approval / confirmation
- 所有工具调用可 trace、可重试、可审计
- 统一兼容 MCP server 模式

### 7.3 EchoMind 中优先落地的工具

第一批工具建议：

- `knowledge_search`
- `faq_lookup`
- `order_lookup`
- `refund_policy_check`
- `refund_create_ticket`
- `account_status_lookup`
- `incident_lookup`
- `human_handoff_create`

第二批工具建议：

- `kb_article_feedback`
- `shipping_status_lookup`
- `user_profile_update`

### 7.4 具体改造点

1. 改造 `BaseAgent._call_llm()`，支持：
   - 模型返回 tool call
   - runtime 执行 tool
   - 将 tool result 继续喂回模型
2. 在 `mcp/tool_manager.py` 中补齐：
   - tool schema version
   - idempotency key
   - retry policy
   - approval_required
   - audit logging
3. 对退款、账号变更等动作型工具增加：
   - 前置参数校验
   - 二次确认
   - 风险标签
4. 新增工具调用结果标准格式：
   - `status`
   - `data`
   - `error`
   - `source`
   - `latency_ms`

### 7.5 开发任务

1. 给现有工具定义统一 `ToolExecutionResult`。
2. 增加 `tool_call_trace` 与 `tool_call_id`。
3. 在前端工作台展示本轮发生了哪些工具调用。
4. 对失败调用支持：
   - retry
   - fallback
   - escalate
5. 新增“工具白名单 + Agent 权限矩阵”。
   - `billing` 允许退款相关工具
   - `technical` 允许故障查询工具
   - `general` 默认只允许知识类工具

### 7.6 验收标准

- Agent 能在一次任务中调用 1~3 个工具
- 高风险工具必须确认后执行
- 工具调用轨迹可查看、可回放

### 7.7 重点指标

- Tool Selection Accuracy
- Tool Success Rate
- Retry Recovery Rate
- Approval Compliance Rate
- Tool-caused Failure Rate

---

## 8. Phase 2：Agentic RAG

### 8.1 目标

让 RAG 从“请求前置增强”升级成“Agent 按需决策的检索系统”。

### 8.2 推荐技术与做法

建议引入以下做法：

- query classification
- query rewrite
- multi-query retrieval
- hybrid retrieval
- metadata filter
- citation grounding
- abstain / escalate if not answerable

### 8.3 EchoMind 具体运用

保留当前 `ChromaDB` 语义检索能力，同时增加：

- BM25 倒排检索层
- hybrid merging
- 文档版本和生效时间过滤
- top-k 证据引用
- “无法根据当前知识确认”拒答分支

### 8.4 建议新增模块

- `rag/retrieval_router.py`
- `rag/hybrid_retriever.py`
- `rag/citation_builder.py`
- `rag/grounding_checker.py`

### 8.5 开发任务

1. 将 `_build_knowledge_context()` 逻辑从 API 层下沉到 runtime / tool 层。
2. 为客服问题做 query 分类：
   - FAQ
   - 政策规则
   - 产品说明
   - 技术文档
   - 工单/订单相关
3. 为知识文档补 metadata：
   - 产品线
   - 版本号
   - 地区
   - 生效时间
   - 文档可信度
4. 在最终回复中增加 citations。
5. 增加 groundedness evaluator：
   - 回答是否由检索证据支持
6. 对无命中或低可信命中问题：
   - 拒答
   - 追问澄清
   - 转人工

### 8.6 验收标准

- Agent 能自主决定是否检索
- 回复可展示证据来源
- 检索不足时不会硬答

### 8.7 重点指标

- Recall@5
- nDCG@10
- Grounded Response Rate
- Hallucination Rate
- Abstain Precision

---

## 9. Phase 2：Agent Memory

### 9.1 目标

将当前三级记忆升级为“支持任务连续性、用户事实管理和人工交接”的 agent memory。

### 9.2 推荐技术与做法

建议按 4 类记忆建模：

- Working Memory：当前会话窗口
- Episodic Memory：关键任务片段与事件
- Semantic Memory：用户稳定事实
- Task Memory：正在执行的任务状态

### 9.3 EchoMind 具体运用

基于现有 `Redis + ChromaDB`：

- Redis：working memory + task state cache
- ChromaDB：episodic / semantic memory
- 用户画像从“纯向量片段”升级为“结构化事实 + 向量辅助召回”

### 9.4 开发任务

1. 为长期记忆新增事实结构：
   - `fact_key`
   - `fact_value`
   - `confidence`
   - `source_turn`
   - `updated_at`
2. 设计 memory write policy：
   - 只有高价值事实写入长期记忆
   - 低价值闲聊不写
3. 设计 memory retrieval policy：
   - 根据任务类型动态组装上下文
4. 增加会话摘要器：
   - 长对话自动压缩
   - 输出 task summary / handoff summary
5. 增加事实冲突处理：
   - 新事实覆盖旧事实
   - 记录版本与时间戳
6. 让人工接管可以看到：
   - 用户画像
   - 当前任务状态
   - 已尝试动作
   - 未解决问题

### 9.5 验收标准

- 多轮任务中无需重复询问同一信息
- 人工接管可直接读摘要接手
- 长会话 token 不随轮次线性失控

### 9.6 重点指标

- Repeated Question Rate
- Context Compression Ratio
- Multi-turn Task Success Rate
- Memory Retrieval Hit Rate
- Handoff Summary Usefulness Score

---

## 10. Phase 3：企业客服能力

### 10.1 目标

补齐真实客服系统必须具备的业务能力，而不只是问答能力。

### 10.2 推荐优先做的业务能力

1. 人工升级与 SLA
2. 身份校验与权限控制
3. 敏感信息脱敏
4. 工单系统对接
5. 投诉与高风险场景流程
6. 审计日志

### 10.3 EchoMind 中的具体实现

建议新增：

- `agents/escalation_agent.py`
- `core/compliance_guard.py`
- `integrations/ticketing/`
- `integrations/customer_ops/`

### 10.4 开发任务

1. 实现升级规则：
   - 明确要求人工
   - 高情绪 / 投诉
   - 涉及退款失败 / 权限异常
   - 模型低置信 / 工具失败多次
2. 新增人工接管摘要生成器：
   - 问题概述
   - 用户诉求
   - 已做动作
   - 当前风险
   - 推荐下一步
3. 加入敏感信息识别：
   - 手机号
   - 邮箱
   - 身份证
   - 银行卡
4. 对敏感信息做 mask / redact。
5. 接入模拟工单工具，后续可替换真实工单系统。
6. 引入 SLA 字段：
   - `priority`
   - `deadline`
   - `queue`

### 10.5 验收标准

- 高风险问题不会被直接自动执行
- 转人工时能自动生成交接卡片
- 审计日志可追溯关键动作

### 10.6 重点指标

- Human Handoff Precision
- PII Redaction Recall
- Escalation Resolution Time
- Ticket Creation Success Rate
- Unsafe Automation Rate

---

## 11. Phase 3：Multi-Agent

### 11.1 目标

把当前“多角色”升级成“多 Agent 协作”。

### 11.2 推荐协作模式

优先使用以下两种，不建议一开始就做开放式 agent society：

1. Supervisor -> Specialist handoff
2. Planner / Executor / Reviewer 三段式

在客服场景里，第一种更实用。

### 11.3 EchoMind 建议的 Agent 拆分

建议逐步扩展到：

- `RouterSupervisorAgent`
- `GeneralSupportAgent`
- `TechnicalSupportAgent`
- `BillingSupportAgent`
- `EscalationAgent`
- `QualityReviewerAgent`

可选扩展：

- `KnowledgeAgent`
- `PolicyAgent`

### 11.4 开发任务

1. 将当前 `General/Technical/Billing` 改造成 specialist。
2. 新增 `SupervisorAgent`，负责：
   - 初始任务理解
   - handoff 决策
   - step budget 控制
   - 最终回答整合
3. 新增 `ReviewerAgent`，用于：
   - 高风险回答复核
   - 无证据回答拦截
   - 格式与合规检查
4. 支持多 Agent 协作轨迹展示：
   - 谁接手过
   - 为什么切换
   - 哪个 agent 用了什么工具
5. 对复杂混合问题支持并行子任务：
   - 例如“登录异常 + 发票开具”

### 11.5 验收标准

- 复杂混合问题可发生 handoff
- 高风险回答能被 reviewer 拦截
- 多 Agent 协作过程可解释

### 11.6 重点指标

- Handoff Success Rate
- Mixed-Intent Resolution Rate
- Reviewer Catch Rate
- Unnecessary Handoff Rate

---

## 12. Phase 4：LLMOps

### 12.1 目标

建立生产级 Agent 系统的观测、评测、回归、告警和优化闭环。

### 12.2 推荐技术

基础层：

- `OpenTelemetry`：统一 traces / metrics / logs
- `Prometheus`：指标

Agent / LLM 专项：

- LangSmith 或 Phoenix：用于 LLM trace、prompt、eval、debug
- 现有前端 monitor 页面继续保留，作为项目内控制台

### 12.3 开发任务

1. 为一次会话建立 trace tree：
   - request span
   - routing span
   - tool call span
   - retrieval span
   - memory span
   - handoff span
2. 每次发布保存版本元信息：
   - model
   - prompt version
   - skill version
   - kb version
   - tool schema version
3. 增加 A/B 对比能力：
   - 新旧 prompt
   - 新旧 reranker
   - 新旧 memory policy
4. 增加 badcase replay：
   - 把线上失败会话回灌到评测集
5. 增加成本治理面板：
   - token
   - latency
   - cache hit
   - rerank cost
6. 加入告警规则：
   - 工具失败率异常
   - 转人工率异常
   - groundedness 下降
   - 延迟升高

### 12.4 验收标准

- 一次会话的完整 trace 可查看
- 可以基于版本做前后对比
- 线上 badcase 能快速回放复现

### 12.5 重点指标

- P50 / P95 Latency
- Avg Tokens per Session
- Cost per Resolved Ticket
- Regression Failure Rate
- Alert MTTR

---

## 13. Phase 5：量化优化与简历指标沉淀

### 13.1 目标

在完成前几个阶段后，通过 2~3 轮定向优化，把结果沉淀成可写进简历的数字。

### 13.2 推荐优化顺序

第一轮：

- Runtime + Tool Calling

第二轮：

- Agentic RAG + Memory

第三轮：

- Multi-Agent + Reviewer + 企业客服能力

### 13.3 建议最终沉淀的简历指标

优先争取拿到这些数字：

- 客服任务完成率从 `A% -> B%`
- Intent Macro-F1 从 `A -> B`
- Grounded Response Rate 从 `A% -> B%`
- Tool Success Rate 达到 `A%`
- 转人工准确率提升 `A%`
- 平均 token 成本下降 `A%`
- 平均响应时延下降 `A%`
- P95 响应时延下降 `A%`
- 工具失败恢复率提升 `A%`
- 多轮任务重复追问率下降 `A%`

### 13.4 简历写法模板

可以按如下句式组织：

- 将基于意图路由的客服系统升级为具备状态机 runtime 的多 Agent 执行框架，支持退款、账号异常、排障等多步任务闭环，任务完成率提升 `X%`。
- 打通 Agent 自主工具调用链，支持知识检索、订单查询、退款校验与工单创建，工具调用成功率达到 `X%`，失败恢复率提升 `Y%`。
- 构建 Agentic RAG 与 groundedness 评测体系，结合 query rewrite、hybrid retrieval、citation 和拒答策略，将知识支撑回答率提升 `X%`，幻觉率下降 `Y%`。
- 设计覆盖意图、检索、工具、任务完成与回归检测的评测集和自动化回归流程，累计沉淀 `N` 条客服评测样本，迭代验证效率提升 `X%`。

---

## 14. 推荐实施顺序与周计划

如果按 `6~8` 周推进，建议这样拆：

### Week 1

- 建评测集 v1
- 跑 baseline
- 给 evaluator 补齐结构化输出

### Week 2

- 抽 `SessionState`
- 改 `AgentOrchestrator` 为 step-based runtime
- 先做退款流程状态机

### Week 3

- 打通 tool calling loop
- 接入 `order_lookup / refund_policy_check / ticket_create`
- 实现工具审批与审计

### Week 4

- 下沉 RAG 到 tool/runtime
- 做 citations / groundedness / abstain
- 建立 RAG 专项评测

### Week 5

- 升级 memory 模块
- 做 task summary / handoff summary
- 优化长会话上下文压缩

### Week 6

- 新增 supervisor / reviewer / escalation
- 打通混合问题 handoff
- 实现人机协同流程

### Week 7

- 补齐 trace / version / badcase replay / dashboard
- 做 1~2 轮定向优化

### Week 8

- 固化指标
- 更新简历描述
- 准备项目面试讲解材料

---

## 15. 第一优先级任务清单

如果时间有限，最值得优先做的是以下 12 项：

1. 建立 `180` 条客服评测集和 baseline。
2. 将 `AgentOrchestrator` 改造成 step-based runtime。
3. 新增 `SessionState` 并存储到 Redis。
4. 让 Agent 通过 `MCPToolManager` 真正发起工具调用。
5. 接入 `order_lookup`、`refund_policy_check`、`human_handoff_create` 三个关键工具。
6. 为高风险工具加入确认、审批和审计日志。
7. 将 RAG 检索从 API 预处理下沉到 Agent 工具层。
8. 为回答增加 citation 和 groundedness 检查。
9. 为长期记忆引入结构化事实与任务状态。
10. 增加人工接管摘要与转人工流程。
11. 引入 `OpenTelemetry` trace，并将 tool / memory / retrieval / handoff 纳入 span。
12. 对每次优化跑评测并保留版本对比报表。

---

## 16. 推荐在仓库中的新增目录结构

```text
agents/
  runtime.py
  session_state.py
  escalation_agent.py
  reviewer_agent.py

evaluation/
  datasets/
    customer_support_eval_v1.jsonl
  reports/
  run_eval.py

integrations/
  customer_ops/
  ticketing/

rag/
  retrieval_router.py
  hybrid_retriever.py
  citation_builder.py
  grounding_checker.py

observability/
  tracing.py
  versioning.py
```

---

## 17. 外部实践参考

以下资料可作为设计依据：

- OpenAI Agents SDK：handoffs、tracing  
  https://openai.github.io/openai-agents-python/
- OpenAI API：Graders / Batch / Tools / Remote MCP  
  https://platform.openai.com/docs/
- Anthropic：Building Effective Agents  
  https://www.anthropic.com/engineering/building-effective-agents
- Anthropic：Demystifying Evals for AI Agents  
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- MCP Specification（2025-06-18）  
  https://modelcontextprotocol.io/specification/2025-06-18
- LangChain Long-Term Memory  
  https://docs.langchain.com/oss/python/langchain/long-term-memory
- LangSmith Observability  
  https://docs.langchain.com/langsmith/observability-quickstart

---

## 18. 最终目标

完成以上升级后，EchoMind 的项目叙事将从：

> 一个带多角色路由、RAG、记忆、监控、评测的客服 LLM 原型

升级为：

> 一个面向企业客服场景的生产级 Agent 应用系统，具备状态机 runtime、自主工具调用、Agentic RAG、长期记忆、人机协同、可观测、可评测和多 Agent handoff 能力。
