# EchoMind Eval Report V1

## 目标

`report_schema_v1` 定义 `evaluation/run_eval.py` 产出的第一版评测结果格式，用于：

1. 固定 baseline 报告结构
2. 支持后续版本之间做机器对比
3. 为未来补充 Tool Calling、Runtime、RAG、Memory 指标预留字段

机器可读 schema 见：

- [report_schema_v1.json](/home/li/workspace/EchoMind/evaluation/report_schema_v1.json:1)

## 顶层字段

- `schema_version`
- `report_name`
- `generated_at`
- `dataset`
- `runtime`
- `metrics`
- `summary`
- `cases`

## dataset

记录这次评测使用的是哪一份数据集：

- `name`
- `path`
- `case_count`

## runtime

记录本次评测连接的运行环境：

- `base_url`
- `health_status`
- `judge_enabled`
- `judge_model`

## metrics

第一版重点支持以下指标：

- `intent_accuracy`
- `agent_routing_accuracy`
- `escalation_accuracy`
- `rag_usage_recall`
- `task_completion_rate`
- `avg_latency_ms`
- `p95_latency_ms`

以下指标在当前 EchoMind `/chat` 输出能力下先预留为 `null`：

- `tool_selection_accuracy`
- `grounded_response_rate`
- `avg_tokens_per_session`
- `tool_success_rate`

如果启用了 LLM judge，还会额外输出：

- `judge_point_coverage`
- `judge_outcome_match`
- `judge_safety`
- `judge_overall`

## summary

用于快速阅读本轮结果：

- `total_cases`
- `scenes`
- `unsupported_metrics`
- `failed_case_count`
- `failed_cases_preview`
- `metrics_ready_for_comparison`

## cases

每条样本的详细结果由 5 部分组成：

- `input`
- `expectation`
- `actual`
- `checks`
- `judge`

### input

回放给系统的输入数据：

- `user_query`
- `history`
- `user_profile`

### expectation

来自数据集标注的目标：

- `expected_intent`
- `expected_agent`
- `requires_tool`
- `expected_tools`
- `requires_rag`
- `must_escalate`
- `expected_outcome`
- `gold_answer_points`
- `forbidden_behaviors`

### actual

当前系统 `/chat` 的实际返回：

- `conv_id`
- `response`
- `intent`
- `agent_type`
- `escalated`
- `latency_ms`
- `knowledge_used`

### checks

脚本根据 expectation 和 actual 计算出的规则校验：

- `intent_match`
- `agent_match`
- `escalation_match`
- `rag_usage_match`
- `tool_selection_match`

其中 `tool_selection_match` 在第一版会是 `null`，等后续 `/chat` 或 runtime 暴露 tool traces 后再接上。

### judge

如果启用 LLM judge，会记录：

- `enabled`
- `scores`
- `error`

`scores` 中包括：

- `point_coverage`
- `outcome_match`
- `safety`
- `overall`
- `passed`
- `summary`

## 使用方式

示例命令：

```bash
python -m evaluation.run_eval \
  --dataset evaluation/datasets/customer_support_eval_v1.jsonl \
  --base-url http://127.0.0.1:8000 \
  --report-name baseline_v1
```

输出结果默认写入：

- `evaluation/reports/baseline_v1.json`
- `evaluation/reports/baseline_v1.md`

## 后续演进建议

当 EchoMind 完成 Runtime 和 Tool Calling 改造后，下一版 schema 建议新增：

- `tool_calls`
- `tool_results`
- `trace_id`
- `grounding_citations`
- `token_usage`
- `session_steps`
- `handoff_history`
