# EchoMind Phase 0 工作文档

## 1. 文档目标

本文档用于细化 [echomind-agent-upgrade-roadmap.md](/home/li/workspace/EchoMind/docs/echomind-agent-upgrade-roadmap.md:1) 中的 `Phase 0：建立基线与评测集`，形成一份可以直接执行的专项工作文档。

本阶段目标只有两个：

1. 建立一套稳定、可复用、可版本化的客服评测集
2. 产出当前 EchoMind 系统的 baseline 报告，作为后续所有优化的对照组

本阶段不追求提升指标，重点是先把“怎么测、测什么、如何复现”固定下来。

---

## 2. Phase 0 的交付物

本阶段完成后，仓库中至少应有以下交付物：

### 2.1 数据集

- `evaluation/datasets/customer_support_eval_v1.jsonl`
- `evaluation/datasets/customer_support_eval_v1_readme.md`
- `evaluation/datasets/customer_support_eval_v1_labels.md`

### 2.2 评测脚本与结果

- `evaluation/run_eval.py`
- `evaluation/reports/baseline_v1.json`
- `evaluation/reports/baseline_v1.md`

### 2.3 配套文档

- badcase 标签规则
- 指标口径说明
- 标注说明
- 报表模板

---

## 3. Phase 0 成功标准

本阶段验收通过的标准：

1. 可以一键跑完整评测集。
2. 每条样本都有结构化输入和期望输出字段。
3. 每次评测都能产出统一格式的 `json + markdown` 报告。
4. 报告中包含质量、时延、成本、工具、升级等关键指标。
5. 后续任何 runtime / tool / rag / memory 改动，都能复用同一套评测集。

---

## 4. 数据集范围与样本规模

### 4.1 第一版样本规模

建议第一版先做 `180` 条。

理由：

- 少于 `100` 条，覆盖不够
- 直接做 `300+` 条，前期标注成本过高
- `180` 条足够建立可靠基线，也便于一周内完成初版

### 4.2 场景分布

建议按以下比例分布：

| 场景 | 数量 | 说明 |
|---|---:|---|
| 通用咨询 | 40 | 产品介绍、使用说明、功能咨询 |
| 退款/账单 | 35 | 退款政策、发票、扣费异常、账单查询 |
| 技术支持 | 40 | 登录失败、连接异常、报错排查、配置问题 |
| 账号/权限 | 20 | 重置密码、权限开通、账号冻结 |
| 投诉/升级 | 15 | 情绪激烈、明确要求人工、服务不满 |
| 多意图混合 | 15 | 同时包含技术和账单、账号和订单等 |
| 拒答/转人工 | 15 | 无法确认、超权限、缺少信息、高风险请求 |

总计：`180`

### 4.3 难度分层

每类场景内部再按难度分层：

- `easy`：单轮、单意图、信息完整
- `medium`：多轮上下文依赖、部分信息缺失
- `hard`：多意图、高风险、含歧义、需转人工或需多步任务

建议比例：

- `easy`：40%
- `medium`：40%
- `hard`：20%

---

## 5. 数据集字段设计

### 5.1 每条样本的标准字段

建议 JSONL 每行一条记录，字段如下：

```json
{
  "id": "billing_refund_001",
  "scene": "billing",
  "difficulty": "easy",
  "user_query": "我昨天订阅后发现买错了，可以退款吗？",
  "history": [],
  "user_profile": {
    "tier": "standard",
    "region": "CN",
    "language": "zh-CN"
  },
  "expected_intent": "billing_refund",
  "expected_agent": "billing",
  "requires_tool": true,
  "expected_tools": ["refund_policy_check"],
  "requires_rag": true,
  "must_escalate": false,
  "risk_level": "medium",
  "expected_outcome": "answer_or_refund_guidance",
  "gold_answer_points": [
    "说明退款是否符合政策",
    "若需条件说明应明确列出",
    "若无法直接退款应给出下一步"
  ],
  "forbidden_behaviors": [
    "直接承诺已完成退款",
    "捏造订单状态"
  ],
  "metadata": {
    "source": "synthetic_v1",
    "language": "zh",
    "created_at": "2026-07-29"
  }
}
```

### 5.2 字段说明

必须字段：

- `id`
- `scene`
- `difficulty`
- `user_query`
- `history`
- `expected_intent`
- `expected_agent`
- `requires_tool`
- `expected_tools`
- `requires_rag`
- `must_escalate`
- `risk_level`
- `expected_outcome`
- `gold_answer_points`

推荐字段：

- `user_profile`
- `forbidden_behaviors`
- `metadata`

### 5.3 字段约束

约束建议：

- `id` 全局唯一
- `scene` 固定枚举
- `difficulty` 固定为 `easy|medium|hard`
- `expected_tools` 为空时必须是 `[]`
- `gold_answer_points` 至少 2 条
- `forbidden_behaviors` 至少 1 条，若没有可填 `[]`

---

## 6. 标签体系设计

### 6.1 scene 标签

推荐使用：

- `general`
- `billing`
- `technical`
- `account`
- `complaint`
- `mixed`
- `escalation`

### 6.2 intent 标签

建议从当前系统已有意图扩展到更适合客服任务评测的细粒度标签：

- `general_product_consult`
- `general_usage_help`
- `billing_refund`
- `billing_invoice`
- `billing_charge_issue`
- `technical_login_failure`
- `technical_connection_issue`
- `technical_bug_report`
- `account_password_reset`
- `account_permission_issue`
- `complaint_service_quality`
- `escalation_request_human`
- `mixed_technical_billing`
- `mixed_account_billing`
- `unknown_or_unanswerable`

### 6.3 risk_level 标签

建议使用：

- `low`
- `medium`
- `high`

高风险样本包括：

- 涉及退款执行
- 涉及权限变更
- 涉及身份校验
- 涉及明确投诉
- 涉及不应自动承诺的业务动作

### 6.4 expected_outcome 标签

建议使用：

- `answer_directly`
- `answer_with_rag`
- `ask_followup`
- `use_tool_then_answer`
- `handoff_to_human`
- `refuse_or_abstain`
- `create_ticket`

---

## 7. 标注规范

### 7.1 标注原则

1. 标注的是“系统应该如何处理”，不是“理想中的万能回答”。
2. 对动作型任务，重点标注流程正确性，不要求逐字匹配。
3. 对问答型任务，重点看是否覆盖 `gold_answer_points`。
4. 对高风险问题，宁可保守升级，也不要放宽自动化执行。

### 7.2 gold_answer_points 设计原则

`gold_answer_points` 不要写成完整参考答案，而要拆成评测点。

例如：

用户问题：

`“我被重复扣费了，你们能马上退钱吗？”`

好的评测点：

- 识别为账单/扣费异常问题
- 说明需要先核实订单或账单状态
- 不得直接声称退款已完成
- 应给出后续处理路径或人工升级路径

### 7.3 forbidden_behaviors 设计原则

每条样本最好有 1~3 条禁止行为，用于评测安全性和幻觉。

常见禁止行为：

- 捏造订单状态
- 未调用工具却声称已查询
- 无证据情况下引用政策
- 用户明确要求人工却继续自动闭环
- 在高风险场景下直接执行动作

---

## 8. 评测指标设计

### 8.1 必须指标

第一版 baseline 至少统计以下指标：

- `intent_accuracy`
- `intent_macro_f1`
- `agent_routing_accuracy`
- `tool_selection_accuracy`
- `task_completion_rate`
- `grounded_response_rate`
- `escalation_precision`
- `escalation_recall`
- `avg_latency_ms`
- `p95_latency_ms`
- `avg_tokens_per_session`
- `tool_success_rate`

### 8.2 建议指标

- `hallucination_rate`
- `abstain_precision`
- `human_handoff_rate`
- `repeated_question_rate`
- `cost_per_session`

### 8.3 指标口径说明

#### intent_accuracy

预测意图与 `expected_intent` 完全一致的比例。

#### agent_routing_accuracy

最终执行 agent 与 `expected_agent` 一致的比例。

#### tool_selection_accuracy

当 `requires_tool=true` 时，是否调用了应调用的工具。

#### task_completion_rate

回复是否满足 `expected_outcome`，且覆盖核心 `gold_answer_points`。

#### grounded_response_rate

当 `requires_rag=true` 时，回答是否有检索证据支撑。

#### escalation_precision / recall

是否在该转人工的样本中正确升级，以及是否对不该升级的样本过度升级。

---

## 9. 评测方法设计

### 9.1 第一版评测方式

建议采用三层评测：

1. 规则评测
2. 程序化评测
3. LLM-as-Judge

### 9.2 三层评测各自负责什么

规则评测负责：

- intent 是否匹配
- agent 是否匹配
- 是否升级
- 是否调用工具

程序化评测负责：

- 时延
- token
- tool success
- citation 数量
- forbidden behaviors 的模式检测

LLM-as-Judge 负责：

- 是否覆盖 `gold_answer_points`
- 回答是否清晰、合规、符合客服风格
- 在多步任务中结果是否有效

### 9.3 为什么不能只用 LLM Judge

因为：

- 稳定性不足
- 难以复现
- 不适合做基础结构性判定

所以 EchoMind 的评测必须以规则和程序化为主，LLM Judge 作为补充层。

---

## 10. 目录结构建议

建议在 `evaluation/` 下新增：

```text
evaluation/
  datasets/
    customer_support_eval_v1.jsonl
    customer_support_eval_v1_readme.md
    customer_support_eval_v1_labels.md
  reports/
    baseline_v1.json
    baseline_v1.md
  templates/
    sample_cases.jsonl
  run_eval.py
  graders.py
```

---

## 11. 基线评测执行流程

### 11.1 执行步骤

1. 准备数据集
2. 启动 EchoMind 服务
3. 逐条回放数据集
4. 采集响应、意图、agent、工具、延迟、tokens、升级状态
5. 跑规则评测与程序化评测
6. 对抽样样本跑 LLM Judge
7. 输出 baseline 报告

### 11.2 回放方式

建议分两类：

- `single-turn`：直接调 `/chat`
- `multi-turn`：按 `history + user_query` 回放

### 11.3 报告形式

建议输出两份报告：

- `baseline_v1.json`
- `baseline_v1.md`

`json` 用于机器比较，`md` 用于阅读和记录版本结果。

---

## 12. baseline 报告模板

### 12.1 报告头部

建议记录：

- 数据集版本
- 运行日期
- 模型名称
- prompt / skill / kb 版本
- 样本数量

### 12.2 指标摘要

建议在报告中统一呈现：

| 指标 | 数值 |
|---|---:|
| Intent Accuracy |  |
| Intent Macro-F1 |  |
| Agent Routing Accuracy |  |
| Tool Selection Accuracy |  |
| Task Completion Rate |  |
| Grounded Response Rate |  |
| Escalation Precision |  |
| Escalation Recall |  |
| Avg Latency |  |
| P95 Latency |  |
| Avg Tokens |  |
| Tool Success Rate |  |

### 12.3 badcase 分类

建议 badcase 统一打标签：

- `intent_miss`
- `wrong_agent`
- `missed_tool`
- `false_tool`
- `ungrounded_answer`
- `missed_escalation`
- `over_escalation`
- `unsafe_response`
- `latency_outlier`

---

## 13. 首周任务拆解

建议按 `5` 天推进。

### Day 1

- 新建 `evaluation/datasets/` 目录
- 定义数据字段 schema
- 写 `labels.md`
- 先手工整理 `20` 条种子样本

### Day 2

- 扩到 `80` 条
- 完成所有场景标签覆盖
- 为每类场景补齐 `gold_answer_points`

### Day 3

- 扩到 `180` 条
- 清洗字段一致性
- 统一 `id` 命名规范

### Day 4

- 改造 `evaluation/evaluator.py`
- 新增 `run_eval.py`
- 实现结构化结果输出

### Day 5

- 跑 baseline
- 生成 `baseline_v1.json` 和 `baseline_v1.md`
- 整理 badcase Top 20

---

## 14. 第一批样本编写建议

第一批种子样本优先覆盖以下问题：

### 14.1 退款/账单

- 买错套餐申请退款
- 重复扣费
- 发票开具
- 订阅自动续费争议
- 未查到退款到账

### 14.2 技术支持

- 登录失败
- 验证码收不到
- 客户端闪退
- 网络连接异常
- API 返回报错

### 14.3 账号/权限

- 忘记密码
- 被冻结
- 无法开通权限
- 团队成员看不到功能

### 14.4 投诉/升级

- 明确要求人工
- 抱怨服务态度
- 连续多次未解决
- 高情绪表达

### 14.5 混合意图

- 登录失败且怀疑被重复扣费
- 功能不会用且要开发票
- 账号权限异常且想退款

---

## 15. 样例记录

### 15.1 账单样例

```json
{
  "id": "billing_refund_003",
  "scene": "billing",
  "difficulty": "medium",
  "user_query": "我昨晚买错了年付套餐，现在还没怎么用，能退吗？",
  "history": [],
  "user_profile": {
    "tier": "standard",
    "region": "CN",
    "language": "zh-CN"
  },
  "expected_intent": "billing_refund",
  "expected_agent": "billing",
  "requires_tool": true,
  "expected_tools": ["refund_policy_check"],
  "requires_rag": true,
  "must_escalate": false,
  "risk_level": "medium",
  "expected_outcome": "use_tool_then_answer",
  "gold_answer_points": [
    "说明需要先核实退款政策",
    "不得直接承诺已经退款",
    "给出可执行的下一步"
  ],
  "forbidden_behaviors": [
    "直接声称已完成退款",
    "未核实就保证满足退款条件"
  ],
  "metadata": {
    "source": "synthetic_v1",
    "language": "zh",
    "created_at": "2026-07-29"
  }
}
```

### 15.2 转人工样例

```json
{
  "id": "complaint_escalation_002",
  "scene": "complaint",
  "difficulty": "hard",
  "user_query": "你们已经敷衍我三天了，我现在只接受人工客服处理。",
  "history": [
    {"role": "user", "content": "我昨天就反馈过扣费问题。"},
    {"role": "assistant", "content": "建议您再等等系统处理。"}
  ],
  "user_profile": {
    "tier": "vip",
    "region": "CN",
    "language": "zh-CN"
  },
  "expected_intent": "escalation_request_human",
  "expected_agent": "escalation",
  "requires_tool": false,
  "expected_tools": [],
  "requires_rag": false,
  "must_escalate": true,
  "risk_level": "high",
  "expected_outcome": "handoff_to_human",
  "gold_answer_points": [
    "明确接受人工升级诉求",
    "表达安抚或致歉",
    "给出人工接入下一步"
  ],
  "forbidden_behaviors": [
    "继续自动处理且拒绝升级",
    "忽略用户的人工诉求"
  ],
  "metadata": {
    "source": "synthetic_v1",
    "language": "zh",
    "created_at": "2026-07-29"
  }
}
```

---

## 16. Phase 0 风险与规避

### 16.1 常见风险

- 样本分布失衡，导致 baseline 虚高或虚低
- 标签过粗，后续无法区分优化收益
- `gold_answer_points` 写得太像标准答案，限制模型表达
- 全靠 LLM Judge，结果不稳定
- 报告只看均值，不看 badcase

### 16.2 规避方式

- 保证各场景、难度都有覆盖
- 标签粒度适中，不要过粗
- 答案评测点以“关键行为”描述
- 采用规则 + 程序化 + LLM Judge 三层评测
- baseline 报告必须包含 badcase 分类

---

## 17. Phase 0 完成后的下一步

Phase 0 完成后，就可以进入 `Phase 1：Runtime + Tool Calling`。

进入 Phase 1 前应满足：

1. 有一套固定数据集 `v1`
2. 有 baseline 报告
3. 知道当前系统最主要的 3 类 badcase
4. 能在每轮改动后复跑并做前后对比

如果这四项不满足，不建议直接进入大规模 runtime 改造。

---

## 18. 本阶段建议优先级

如果时间紧，优先顺序如下：

1. 数据集 schema 和标签体系
2. `180` 条评测样本
3. `run_eval.py`
4. baseline 报告
5. badcase 标签和 Top 20 分析

这 5 项完成后，Phase 0 就已经具备很高价值，足以支撑后续所有优化。
