# baseline_v1

- generated_at: `2026-07-29T06:39:36.698163Z`
- dataset: `customer_support_eval_v1`
- total_cases: `21`
- base_url: `http://127.0.0.1:8000`
- judge_enabled: `False`

## Metrics

- intent_accuracy: `0.0`
- agent_routing_accuracy: `0.5238`
- escalation_accuracy: `0.8571`
- rag_usage_recall: `1.0`
- tool_selection_accuracy: `None`
- task_completion_rate: `None`
- grounded_response_rate: `None`
- avg_latency_ms: `10740.28`
- p95_latency_ms: `17544.3`
- avg_tokens_per_session: `None`
- tool_success_rate: `None`

## Unsupported Metrics

- tool_selection_accuracy
- grounded_response_rate
- avg_tokens_per_session
- tool_success_rate

## Failed Cases Preview

- general_product_001: intent=`query`, agent=`general`, escalated=`False`
- general_usage_001: intent=`query`, agent=`general`, escalated=`True`
- general_usage_002: intent=`query`, agent=`general`, escalated=`False`
- billing_refund_001: intent=`request`, agent=`general`, escalated=`False`
- billing_refund_002: intent=`request`, agent=`general`, escalated=`False`
- billing_charge_001: intent=`billing`, agent=`billing`, escalated=`False`
- billing_invoice_001: intent=`query`, agent=`general`, escalated=`False`
- billing_invoice_002: intent=`query`, agent=`general`, escalated=`False`
- technical_login_001: intent=`technical`, agent=`technical`, escalated=`False`
- technical_login_002: intent=`technical`, agent=`technical`, escalated=`False`
- technical_connection_001: intent=`technical`, agent=`technical`, escalated=`False`
- technical_bug_001: intent=`technical`, agent=`technical`, escalated=`False`
- account_password_001: intent=`account`, agent=`billing`, escalated=`False`
- account_permission_001: intent=`technical`, agent=`technical`, escalated=`False`
- account_permission_002: intent=`account`, agent=`billing`, escalated=`False`
- complaint_service_001: intent=`complaint`, agent=`general`, escalated=`False`
- complaint_escalation_001: intent=`escalation`, agent=`general`, escalated=`True`
- escalation_request_001: intent=`escalation`, agent=`general`, escalated=`True`
- mixed_tech_billing_001: intent=`other`, agent=`general`, escalated=`True`
- mixed_account_billing_001: intent=`billing`, agent=`billing`, escalated=`False`
