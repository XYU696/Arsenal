# customer_support_eval_v1

## 目标

`customer_support_eval_v1` 是 EchoMind 的第一版客服评测集，服务于以下目标：

1. 建立项目当前能力的 baseline
2. 为后续 Runtime、Tool Calling、Agentic RAG、Memory、Multi-Agent 优化提供统一对照集
3. 沉淀可重复、可回归、可版本化的客服评测流程

## 文件

- `customer_support_eval_v1.jsonl`
- `customer_support_eval_v1_labels.md`

## 样本格式

数据集采用 `JSONL`，每行一条记录。

核心字段：

- `id`
- `scene`
- `difficulty`
- `user_query`
- `history`
- `user_profile`
- `expected_intent`
- `expected_agent`
- `requires_tool`
- `expected_tools`
- `requires_rag`
- `must_escalate`
- `risk_level`
- `expected_outcome`
- `gold_answer_points`
- `forbidden_behaviors`
- `metadata`

## 第一版范围

当前文件先提供一批种子样本，覆盖：

- 通用咨询
- 退款/账单
- 技术支持
- 账号/权限
- 投诉/升级
- 多意图混合
- 拒答/转人工

后续目标是扩展到约 `180` 条样本。

## 使用建议

第一阶段可先把该数据集用于：

1. 意图识别准确率评测
2. 路由准确率评测
3. 工具选择评测
4. 升级策略评测
5. badcase 分类统计

后续引入 Agent Runtime 和 Tool Calling 后，再补充：

1. 任务完成率
2. groundedness
3. tool success rate
4. multi-turn success rate
5. token 与 latency 指标

## 命名规范

推荐 `id` 格式：

`{scene}_{subtype}_{index}`

例如：

- `billing_refund_001`
- `technical_login_002`
- `complaint_escalation_001`

## 扩展规则

新增样本时建议遵循：

1. 每条样本至少包含 2 条 `gold_answer_points`
2. 每条样本最好包含 1~3 条 `forbidden_behaviors`
3. 高风险样本必须明确 `must_escalate` 与 `risk_level`
4. 混合意图样本优先覆盖“技术 + 账单”“账号 + 账单”组合
