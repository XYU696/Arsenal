# EchoMind 项目架构总结

## 1. 项目定位

EchoMind 是一个面向企业客服场景的 LLM 后端原型系统。它将意图识别、路由编排、知识库检索、会话记忆、性能监控、自动评测和业务规则热加载整合到一个可运行的客服闭环中。

从代码实现看，这个项目的核心价值不在于“强自治智能体”，而在于把一个智能客服系统所需的工程模块拼装得较完整，具备一定的可观测、可评测、可扩展特性。

更准确地说，它是：

- 一个基于 `FastAPI` 的智能客服后端
- 一个基于意图路由的多角色客服编排系统
- 一个带 RAG 检索、用户记忆、监控和评测的工程化原型

而不是：

- 一个真正具备自主规划和工具调用能力的多 Agent 系统
- 一个具备 Agent 间通信协议和长期智能体记忆的自治智能体平台

---

## 2. 整体架构

一次 `/chat` 请求的核心链路如下：

1. API 接收用户消息
2. 从记忆模块读取最近会话、历史语义片段和用户画像
3. 根据消息内容决定是否先进行知识库检索
4. 将记忆上下文和知识库内容拼成完整上下文
5. 进入多 Agent 编排器进行意图识别和路由
6. 选中对应 Agent，通过 LLM 生成回复
7. 将用户消息和模型回复写回记忆系统
8. Monitor 周期性采集 Agent 和工具统计
9. Evaluator 可对系统进行离线/在线质量评测

对应入口文件：

- [api/main.py](/home/li/workspace/EchoMind/api/main.py:1)

系统中关键模块的职责如下：

- `api/`：HTTP 接口与主流程串联
- `agents/`：多 Agent 编排与路由
- `core/`：意图识别、Skill 注入、LLM 工具函数
- `mcp/`：知识库与工具调用框架
- `memory/`：会话记忆、长期记忆、用户画像
- `monitor/`：性能监控、异常检测、路由反馈
- `evaluation/`：意图评测、对话评测、回归检测
- `skills/`：业务规则热加载文件
- `frontend/`：独立 Next.js 前端控制台

---

## 3. 第一点：FastAPI 主入口与服务编排

主入口在 [api/main.py](/home/li/workspace/EchoMind/api/main.py:1)。

服务启动时通过 `lifespan` 初始化以下核心组件：

- `IntentRecognizer`
- `SkillManager`
- `AgentOrchestrator`
- `MemoryManager`
- `MCPToolManager`
- `KnowledgeBase`
- `PerformanceMonitor`
- `EndToEndEvaluator`

它提供的关键 API 包括：

- `POST /chat`：主对话接口
- `GET /health`：服务健康状态
- `GET /monitor`：监控摘要
- `POST /search`：知识库检索测试
- `GET /skills`、`POST /skills/reload`：Skill 查看与热加载
- `POST /knowledge/add`、`POST /knowledge/upload`：知识库导入
- `POST /eval/run`：评测执行
- `GET /metrics`：Prometheus 指标输出

这一层的作用是把本来分散的能力模块串成一条业务链路。

---

## 4. 第二点：多 Agent 编排器

核心代码在 [agents/agent_orchestrator.py](/home/li/workspace/EchoMind/agents/agent_orchestrator.py:1)。

### 4.1 实现方式

项目中定义了三类客服 Agent：

- `GeneralAgent`
- `TechnicalAgent`
- `BillingAgent`

它们的差异主要体现在各自的 `system_prompt`，实际执行时都继承自 `BaseAgent`，统一通过 `_call_llm()` 单次调用模型生成回复。

### 4.2 路由逻辑

编排器主要做三件事：

1. 意图识别
2. 按意图选择目标 Agent 类型
3. 在同类 Agent 中按路由评分选择实例

代码中的三层路由含义是：

- 意图路由：`intent -> agent type`
- 性能路由：同类实例中选 `routing_score` 最高者
- 降级路由：专属 Agent 失败时降级到 `GeneralAgent`

### 4.3 局限性

虽然项目名中强调多 Agent，但这里的“多 Agent”更接近：

- 多角色 Prompt 路由
- 多实例选择
- 简单并行拼接

它没有真正实现：

- Agent 自主 loop
- Agent 主动工具调用
- Agent 间消息通信
- Planner / Executor / Reviewer 协作链

因此更准确的说法是：这是一个基于意图驱动的多角色客服编排器，而不是自治式多智能体系统。

---

## 5. 第三点：三路融合意图识别

核心代码在 [core/intent_recognizer.py](/home/li/workspace/EchoMind/core/intent_recognizer.py:1)。

### 5.1 三条识别路径

项目的意图识别使用三路融合：

1. `LLM 语义理解`
2. `Embedding 相似度匹配`
3. `关键词模式匹配`

其中：

- LLM 是主力路径，负责理解复杂语义和上下文
- Embedding 用于模板相似度匹配
- Pattern 是零延迟兜底策略

### 5.2 加权投票

识别结果通过 `_vote()` 合并：

- 官方 Embedding 可用时：`LLM 0.7 + Embedding 0.2 + Pattern 0.1`
- 兼容接口不支持 Embedding 时：权重退化为 `LLM 0.85 + Pattern 0.15`

### 5.3 其他能力

该模块还提供：

- 实体提取
- 紧急度判断
- 缓存
- 在线学习样本追加

### 5.4 价值与边界

这一模块比“纯 prompt 分类”更工程化，但依然属于客服意图分类器，而不是复杂任务规划器。

---

## 6. 第四点：RAG 知识库

核心代码在 [mcp/knowledge_base.py](/home/li/workspace/EchoMind/mcp/knowledge_base.py:1)。

### 6.1 实现方式

知识库基于 ChromaDB，主要能力是：

- 文档导入
- 文本切片
- 语义检索
- 作为工具注册到 `MCPToolManager`

### 6.2 工作流程

文档导入时：

1. 按句子和长度切片
2. 存入 ChromaDB collection
3. 由 Chroma 自动生成向量

查询时：

1. 输入 query
2. 调 Chroma `query()`
3. 返回文档片段、标题和相似度得分

### 6.3 关键事实

知识库本身是独立的，但在当前项目中，Agent 并不会自己调用它。实际调用发生在 API 层的 `_build_knowledge_context()` 中，由主链路先检索再把结果拼接到上下文里。

因此这里实现的是：

- `API 先做 RAG，再交给 Agent`

而不是：

- `Agent 在推理过程中自主决定何时调用知识库`

---

## 7. 第五点：MCP 工具调用框架

核心代码在 [mcp/tool_manager.py](/home/li/workspace/EchoMind/mcp/tool_manager.py:1)。

### 7.1 已实现的能力

该模块已经实现了一套完整的工具调用基础设施：

- 工具注册
- 参数校验
- TTL 缓存
- 熔断器
- 超时控制
- fallback 降级
- 查询改写
- 结果重排
- 并行召回

### 7.2 检索优化链路

对于检索类工具，核心流程是：

1. 原始 query
2. LLM 改写成多个子查询
3. 并行检索
4. 结果去重
5. LLM 重排
6. 返回 top-k

这部分工程实现质量较高，已经具备比较典型的“工具层优化”思路。

### 7.3 项目中的真实地位

尽管叫 `MCPToolManager`，但当前项目中的 Agent 并不会自己调用它。  
工具层目前更像一个可扩展底座，而不是 Agent Runtime 的一部分。

因此对这个模块最准确的描述是：

> 项目实现了一个较完整的工具调用框架，但尚未实现 Agent 自主工具使用。

---

## 8. 第六点：三级记忆管理器

核心代码在 [memory/conversation_memory.py](/home/li/workspace/EchoMind/memory/conversation_memory.py:1)。

### 8.1 三层结构

所谓三级记忆是：

1. 工作记忆：Redis 中保存当前会话最近若干条消息
2. 情景记忆：ChromaDB 中保存跨会话历史片段
3. 用户画像：ChromaDB 中保存从历史对话提炼出的偏好与实体

### 8.2 工作方式

当请求进入系统时：

- `get_context()` 会同时读取最近对话、历史语义匹配结果和用户画像
- 再把这些内容格式化成 prompt 文本

当工作记忆过长时：

- `_compress()` 会用 LLM 生成摘要
- 把旧对话写入长期记忆
- Redis 中只保留最近若干条

### 8.3 应该如何理解

这套记忆系统服务的主要对象是：

- 用户会话
- 历史语义召回
- 用户偏好抽取

它不是严格意义上的 Agent Memory。  
更准确地说，这里实现的是：

- Conversation Memory
- Historical Retrieval Memory
- User Profile Memory

而不是：

- Agent 自身经验记忆
- 任务规划状态记忆
- 工具使用反思记忆

---

## 9. 第七点：性能监控器

核心代码在 [monitor/performance_monitor.py](/home/li/workspace/EchoMind/monitor/performance_monitor.py:1)。

### 9.1 工作机制

Monitor 在服务启动时创建后台任务，每隔固定时间执行一次 `_collect()`：

- 读取 `Orchestrator.get_stats()`
- 读取 `ToolManager.get_stats()`
- 做异常检测
- 做阈值告警
- 计算 penalty
- 回写 Orchestrator

### 9.2 检测能力

它实现了：

- 滑动窗口 Z-score 异常检测
- 成功率和耗时阈值告警
- 连续失败场景下的建议生成
- Webhook 通知
- Prometheus 指标输出

### 9.3 路由反馈闭环

这是 Monitor 的核心亮点。

Monitor 会根据 Agent 的：

- 成功率
- 平均耗时

计算 `routing_penalty`，再调用：

- `AgentOrchestrator.update_routing_penalties()`

把 penalty 写回每个 Agent 的 `stats.monitor_penalty`。

随后在路由选择时：

- `_best_agent()` 会根据 `routing_score()` 选实例
- `routing_score()` 会把 penalty 乘进去

因此形成了一个“监控反馈到调度”的闭环。

### 9.4 应如何评价

这一模块是项目中最像企业系统的一部分，因为它不只是展示指标，而是影响系统运行策略。

---

## 10. 第八点：端到端评测框架

核心代码在 [evaluation/evaluator.py](/home/li/workspace/EchoMind/evaluation/evaluator.py:1)。

### 10.1 评测维度

该模块实现了四类评估能力：

1. 意图识别评测
2. LLM-as-Judge 对话质量评分
3. 单轮 / 多轮端到端对话评测
4. 与历史基线的回归检测

### 10.2 意图评测

对于意图识别，它计算：

- Accuracy
- Macro-F1
- 每类 Precision / Recall / F1

### 10.3 对话质量评测

它会让系统真实生成回答，再让 LLM 充当 Judge，从以下四个维度打分：

- relevance
- accuracy
- completeness
- helpfulness

然后取平均值得到 `overall`。

### 10.4 回归检测

会把本次平均分与历史 baseline 比较：

- 相对退化超过 5% 的指标会被记录到 `regressions`

### 10.5 内置样例

项目已经内置：

- 8 个意图测试用例
- 5 个对话测试用例

因此评测模块开箱即用，不需要用户先自己造测试集。

### 10.6 局限

这套评测框架已经很完整，但仍然是：

- 轻量级回归评测系统

而不是：

- 大规模离线 benchmark 平台
- 人工标注校准闭环系统

---

## 11. 第九点：Skill 热加载引擎

核心代码在 [core/skill_loader.py](/home/li/workspace/EchoMind/core/skill_loader.py:1)，示例 Skill 在 `skills/` 目录中。

### 11.1 Skill 的本质

Skill 本质上不是代码插件，而是：

- 一段结构化业务规则文本
- 一种运行时 Prompt 注入机制

它用于维护：

- 客服话术
- 处理流程
- 合规边界
- 故障排查 SOP

### 11.2 支持的格式

支持：

- Markdown（含 Front Matter）
- JSON
- TXT

项目当前主要使用的是 `SKILL.md` 形式。

### 11.3 命中逻辑

每个 Skill 可以配置：

- `keywords`
- `agents`
- `enabled`

匹配时要求：

1. Skill 已启用
2. 当前 Agent 类型匹配（如果配置了 agents）
3. 用户消息命中关键词（如果配置了 keywords）

### 11.4 注入方式

命中后，`SkillManager.prompt_for()` 会把多个 Skill 格式化并拼接成统一文本块，再在 Agent 的 `_build_system_prompt()` 中附加到基础 `system_prompt` 后面。

最终输入给模型的是：

- 原始角色 Prompt
- 动态业务规则 Prompt

### 11.5 热加载

通过：

- `POST /skills/reload`

可以重新扫描整个 Skill 目录，无需重启服务。

### 11.6 长度控制

为了避免 Skill 文本过长挤占上下文，系统实现了两层限制：

- 单个 Skill 截断
- 整体 Prompt 截断

这使它更像一个轻量 Prompt 规则中间层，而不是完整插件平台。

---

## 12. 项目亮点总结

从整体上看，这个项目真正的亮点主要有以下几点：

- 用较清晰的模块边界，把客服系统常见能力做了完整拼装
- 意图识别不是单一路径，而是三路融合
- RAG 不只是向量检索，还加了查询改写和重排
- 记忆系统实现了会话、长期历史和用户画像分层
- Monitor 不只是展示数据，还能反馈到路由决策
- Evaluator 能做质量评估和回归检测
- Skill 系统把业务规则从代码里解耦出来
- 增加了独立前端，便于联调和演示

---

## 13. 项目局限与真实评价

### 13.1 局限

这个项目没有真正实现：

- 自主规划式 Agent Loop
- Agent 自主工具调用
- Agent 间通信协议
- 真正的 Agent 长期记忆
- 多 Agent 深度协作链

### 13.2 更准确的评价

因此，对这个项目最真实、最不夸张的评价应该是：

> EchoMind 是一个面向企业客服场景的工程化 LLM 原型系统。它实现了基于意图路由的多角色客服编排，并集成了 RAG 检索、用户记忆、监控反馈、自动评测和业务规则热加载能力。其“多 Agent”更偏向多角色编排而非自治式智能体协作。

---

## 14. 学习后的输出能力

如果已经理解这个项目，你应该能够独立回答以下问题：

1. `/chat` 请求在系统里是怎么流动的
2. 多 Agent 路由到底做了什么，没做什么
3. RAG 是在哪一层接入的，而不是谁主动调用的
4. 记忆系统服务的是用户上下文还是 Agent 本身
5. Monitor 如何把在线表现反馈到路由选择
6. Evaluator 如何计算准确率、质量分和回归退化
7. Skill 系统如何把业务规则从代码中解耦
8. 这个项目适合如何真实地写进简历，而不是夸大成自治 Agent 系统

---

## 15. 一句话总评

EchoMind 最值得学习的地方，不是“智能体有多强”，而是：

> 它把一个智能客服系统从单纯的 LLM 对话，扩展成了一个具备路由、检索、记忆、监控、评测和规则注入能力的工程化后端原型。
