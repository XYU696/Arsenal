import argparse
import asyncio
import json
import os
import pathlib
import statistics
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic


ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "customer_support_eval_v1.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "evaluation" / "reports"
DEFAULT_SCHEMA_VERSION = "echomind.eval_report.v1"


@dataclass
class DatasetCase:
    id: str
    scene: str
    difficulty: str
    user_query: str
    history: List[Dict[str, str]]
    user_profile: Dict[str, Any]
    expected_intent: str
    expected_agent: str
    requires_tool: bool
    expected_tools: List[str]
    requires_rag: bool
    must_escalate: bool
    risk_level: str
    expected_outcome: str
    gold_answer_points: List[str]
    forbidden_behaviors: List[str]
    metadata: Dict[str, Any]


@dataclass
class JudgeScores:
    point_coverage: float
    outcome_match: float
    safety: float
    overall: float
    passed: bool
    summary: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EchoMind customer support eval dataset.")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="Path to a JSONL dataset file.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="EchoMind API base URL.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated reports.",
    )
    parser.add_argument(
        "--report-name",
        default="baseline_v1",
        help="Report file prefix, without extension.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Evaluate only the first N cases. 0 means all cases.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Disable LLM-as-judge even if API credentials are available.",
    )
    return parser.parse_args()


def load_dataset(path: pathlib.Path, limit: int = 0) -> List[DatasetCase]:
    cases: List[DatasetCase] = []
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            if not line.strip():
                continue
            data = json.loads(line)
            cases.append(DatasetCase(**data))
            if limit and len(cases) >= limit:
                break
    if not cases:
        raise ValueError(f"Dataset is empty: {path}")
    return cases


def http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


async def async_http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    return await asyncio.to_thread(http_json, method, url, payload, timeout)


def build_judge() -> Optional[AsyncAnthropic]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    kwargs: Dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncAnthropic(**kwargs)


async def judge_case(client: AsyncAnthropic, model: str, case: DatasetCase, response_text: str) -> JudgeScores:
    prompt = f"""你是企业客服系统的评测器。请根据以下标准评估回答质量，并只返回 JSON。

用户问题:
{case.user_query}

期望处理结果:
{case.expected_outcome}

必须覆盖的要点:
{json.dumps(case.gold_answer_points, ensure_ascii=False)}

禁止行为:
{json.dumps(case.forbidden_behaviors, ensure_ascii=False)}

是否必须转人工:
{json.dumps(case.must_escalate, ensure_ascii=False)}

系统回答:
{response_text}

请返回以下 JSON:
{{
  "point_coverage": 0.0,
  "outcome_match": 0.0,
  "safety": 0.0,
  "overall": 0.0,
  "passed": true,
  "summary": "简短说明"
}}

评分标准:
- point_coverage: 是否覆盖关键要点
- outcome_match: 是否达成预期处理方式
- safety: 是否避免禁止行为和不当承诺
- overall: 综合分数
"""
    resp = await client.messages.create(
        model=model,
        max_tokens=300,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = ""
    for block in getattr(resp, "content", []):
        text = getattr(block, "text", "")
        if text:
            raw += text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"Judge returned non-JSON content: {raw}")
    data = json.loads(raw[start:end])
    return JudgeScores(
        point_coverage=float(data.get("point_coverage", 0.0)),
        outcome_match=float(data.get("outcome_match", 0.0)),
        safety=float(data.get("safety", 0.0)),
        overall=float(data.get("overall", 0.0)),
        passed=bool(data.get("passed", False)),
        summary=str(data.get("summary", "")),
    )


async def evaluate_case(
    base_url: str,
    timeout: float,
    case: DatasetCase,
    judge_client: Optional[AsyncAnthropic],
    judge_model: str,
) -> Dict[str, Any]:
    conv_id = f"eval_{case.id}_{uuid.uuid4().hex[:8]}"
    payload = {
        "message": case.user_query,
        "user_id": f"eval_user_{case.scene}",
        "conv_id": conv_id,
    }
    response = await async_http_json("POST", f"{base_url.rstrip('/')}/chat", payload, timeout)

    actual_intent = str(response.get("intent", ""))
    actual_agent = str(response.get("agent_type", ""))
    actual_escalated = bool(response.get("escalated", False))
    latency_ms = float(response.get("latency_ms", 0.0))
    knowledge_used = bool(response.get("knowledge_used", False))
    answer = str(response.get("response", ""))

    intent_match = actual_intent == case.expected_intent
    agent_match = actual_agent == case.expected_agent
    escalation_match = actual_escalated == case.must_escalate
    rag_usage_match = knowledge_used if case.requires_rag else True

    judge_scores = None
    judge_error = None
    if judge_client is not None:
        try:
            judge_scores = await judge_case(judge_client, judge_model, case, answer)
        except Exception as ex:
            judge_error = str(ex)

    return {
        "id": case.id,
        "scene": case.scene,
        "difficulty": case.difficulty,
        "risk_level": case.risk_level,
        "input": {
            "user_query": case.user_query,
            "history": case.history,
            "user_profile": case.user_profile,
        },
        "expectation": {
            "expected_intent": case.expected_intent,
            "expected_agent": case.expected_agent,
            "requires_tool": case.requires_tool,
            "expected_tools": case.expected_tools,
            "requires_rag": case.requires_rag,
            "must_escalate": case.must_escalate,
            "expected_outcome": case.expected_outcome,
            "gold_answer_points": case.gold_answer_points,
            "forbidden_behaviors": case.forbidden_behaviors,
        },
        "actual": {
            "conv_id": response.get("conv_id"),
            "response": answer,
            "intent": actual_intent,
            "agent_type": actual_agent,
            "escalated": actual_escalated,
            "latency_ms": latency_ms,
            "knowledge_used": knowledge_used,
        },
        "checks": {
            "intent_match": intent_match,
            "agent_match": agent_match,
            "escalation_match": escalation_match,
            "rag_usage_match": rag_usage_match,
            "tool_selection_match": None,
        },
        "judge": {
            "enabled": judge_client is not None,
            "scores": asdict(judge_scores) if judge_scores else None,
            "error": judge_error,
        },
    }


def compute_metrics(case_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(case_results)
    intent_matches = [r["checks"]["intent_match"] for r in case_results]
    agent_matches = [r["checks"]["agent_match"] for r in case_results]
    escalation_matches = [r["checks"]["escalation_match"] for r in case_results]
    latency_values = [float(r["actual"]["latency_ms"]) for r in case_results]

    rag_required = [r for r in case_results if r["expectation"]["requires_rag"]]
    rag_usage_recall = (
        sum(1 for r in rag_required if r["actual"]["knowledge_used"]) / len(rag_required)
        if rag_required else None
    )

    judge_results = [r["judge"]["scores"] for r in case_results if r["judge"]["scores"]]
    judge_pass_rate = (
        sum(1 for j in judge_results if j["passed"]) / len(judge_results)
        if judge_results else None
    )

    metrics = {
        "intent_accuracy": safe_ratio(sum(intent_matches), total),
        "agent_routing_accuracy": safe_ratio(sum(agent_matches), total),
        "escalation_accuracy": safe_ratio(sum(escalation_matches), total),
        "rag_usage_recall": round(rag_usage_recall, 4) if rag_usage_recall is not None else None,
        "tool_selection_accuracy": None,
        "task_completion_rate": round(judge_pass_rate, 4) if judge_pass_rate is not None else None,
        "grounded_response_rate": None,
        "avg_latency_ms": round(statistics.mean(latency_values), 2) if latency_values else 0.0,
        "p95_latency_ms": round(percentile(latency_values, 95), 2) if latency_values else 0.0,
        "avg_tokens_per_session": None,
        "tool_success_rate": None,
    }

    if judge_results:
        metrics["judge_point_coverage"] = round(statistics.mean(j["point_coverage"] for j in judge_results), 4)
        metrics["judge_outcome_match"] = round(statistics.mean(j["outcome_match"] for j in judge_results), 4)
        metrics["judge_safety"] = round(statistics.mean(j["safety"] for j in judge_results), 4)
        metrics["judge_overall"] = round(statistics.mean(j["overall"] for j in judge_results), 4)

    return metrics


def build_summary(case_results: List[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    unsupported = [
        "tool_selection_accuracy",
        "grounded_response_rate",
        "avg_tokens_per_session",
        "tool_success_rate",
    ]
    failed_cases = []
    for result in case_results:
        checks = result["checks"]
        judge = result["judge"]["scores"]
        if not checks["intent_match"] or not checks["agent_match"] or not checks["escalation_match"] or (judge and not judge["passed"]):
            failed_cases.append({
                "id": result["id"],
                "scene": result["scene"],
                "actual_intent": result["actual"]["intent"],
                "actual_agent": result["actual"]["agent_type"],
                "actual_escalated": result["actual"]["escalated"],
            })

    by_scene: Dict[str, int] = {}
    for result in case_results:
        by_scene[result["scene"]] = by_scene.get(result["scene"], 0) + 1

    return {
        "total_cases": len(case_results),
        "scenes": by_scene,
        "unsupported_metrics": unsupported,
        "failed_case_count": len(failed_cases),
        "failed_cases_preview": failed_cases[:20],
        "metrics_ready_for_comparison": [k for k, v in metrics.items() if v is not None],
    }


def safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def percentile(values: List[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def write_json_report(path: pathlib.Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown_report(path: pathlib.Path, report: Dict[str, Any]) -> None:
    lines = [
        f"# {report['report_name']}",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- dataset: `{report['dataset']['name']}`",
        f"- total_cases: `{report['summary']['total_cases']}`",
        f"- base_url: `{report['runtime']['base_url']}`",
        f"- judge_enabled: `{report['runtime']['judge_enabled']}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend([
        "",
        "## Unsupported Metrics",
        "",
    ])
    for name in report["summary"]["unsupported_metrics"]:
        lines.append(f"- {name}")
    lines.extend([
        "",
        "## Failed Cases Preview",
        "",
    ])
    if not report["summary"]["failed_cases_preview"]:
        lines.append("- none")
    else:
        for case in report["summary"]["failed_cases_preview"]:
            lines.append(
                f"- {case['id']}: intent=`{case['actual_intent']}`, agent=`{case['actual_agent']}`, escalated=`{case['actual_escalated']}`"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    args = parse_args()
    dataset_path = pathlib.Path(args.dataset).resolve()
    output_dir = pathlib.Path(args.output_dir).resolve()

    try:
        health = await async_http_json("GET", f"{args.base_url.rstrip('/')}/health", timeout=args.timeout)
    except urllib.error.URLError as ex:
        print(f"Health check failed: {ex}", file=sys.stderr)
        return 1

    cases = load_dataset(dataset_path, limit=args.max_cases)
    judge_client = None if args.skip_judge else build_judge()
    judge_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    case_results: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] evaluating {case.id}")
        case_result = await evaluate_case(
            base_url=args.base_url,
            timeout=args.timeout,
            case=case,
            judge_client=judge_client,
            judge_model=judge_model,
        )
        case_results.append(case_result)

    metrics = compute_metrics(case_results)
    report = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "report_name": args.report_name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dataset": {
            "name": dataset_path.stem,
            "path": str(dataset_path),
            "case_count": len(cases),
        },
        "runtime": {
            "base_url": args.base_url,
            "health_status": health.get("status"),
            "judge_enabled": judge_client is not None,
            "judge_model": judge_model if judge_client is not None else None,
        },
        "metrics": metrics,
        "summary": build_summary(case_results, metrics),
        "cases": case_results,
    }

    json_path = output_dir / f"{args.report_name}.json"
    md_path = output_dir / f"{args.report_name}.md"
    write_json_report(json_path, report)
    write_markdown_report(md_path, report)

    print(f"Saved JSON report to {json_path}")
    print(f"Saved Markdown report to {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
