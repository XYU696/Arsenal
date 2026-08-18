# customer_support_eval_v1 标签说明

## scene

- `general`
- `billing`
- `technical`
- `account`
- `complaint`
- `mixed`
- `escalation`

## difficulty

- `easy`
- `medium`
- `hard`

## expected_intent

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

## expected_agent

当前先与现有 EchoMind agent 类型对齐：

- `general`
- `billing`
- `technical`
- `escalation`

后续若引入 `reviewer`、`supervisor`、`knowledge`，再扩展枚举。

## risk_level

- `low`
- `medium`
- `high`

### high 风险参考

以下情况通常应标注为 `high`：

- 涉及退款执行或账单纠纷
- 涉及身份校验或权限变更
- 用户明确投诉或要求人工
- 无法确认事实却容易引发错误承诺

## expected_outcome

- `answer_directly`
- `answer_with_rag`
- `ask_followup`
- `use_tool_then_answer`
- `handoff_to_human`
- `refuse_or_abstain`
- `create_ticket`

## requires_tool

- `true`：期望系统调用工具或至少具备工具查询动作
- `false`：当前问题可直接回答、追问、升级或拒答

## requires_rag

- `true`：理想情况下应依赖知识库、政策、产品说明或文档证据
- `false`：无需知识检索也可给出合理处理

## must_escalate

- `true`：应该转人工或进入升级路径
- `false`：不要求人工升级

## gold_answer_points

用于描述回答应覆盖的关键点，不要求逐字匹配。

好的写法：

- 说明需要先核实退款政策
- 给出用户下一步操作建议
- 明确当前无法直接确认事实

不推荐写法：

- 直接写完整标准答案

## forbidden_behaviors

用于标注回答中不应出现的行为，主要用于安全和 hallucination 检查。

常见类型：

- 捏造订单状态
- 未查询却声称已核实
- 无证据引用政策
- 用户要求人工仍强行自动处理
- 高风险问题直接承诺动作已完成
