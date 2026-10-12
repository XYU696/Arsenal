import argparse  # 命令行参数解析
import asyncio  # 异步（async_http_json、main 入口）
import json  # JSON 序列化 / 反序列化
import os  # 环境变量
import pathlib  # 路径（数据集、报告目录）
import statistics  # 均值统计
import sys  # 退出码 / stderr
import urllib.error  # 健康检查异常捕获
import urllib.request  # HTTP 请求
import uuid  # 会话 ID 生成
from dataclasses import asdict, dataclass  # 数据类
from datetime import datetime  # 报告时间戳
from typing import Any, Dict, List, Optional  # 类型注解

from anthropic import AsyncAnthropic  # LLM-as-Judge 客户端


ROOT = pathlib.Path(__file__).resolve().parent.parent  # 项目根目录
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "customer_support_eval_v1.jsonl"  # 默认评测数据集路径
DEFAULT_OUTPUT_DIR = ROOT / "evaluation" / "reports"  # 默认报告输出目录
DEFAULT_SCHEMA_VERSION = "echomind.eval_report.v1"  # 报告 schema 版本


@dataclass
class DatasetCase:
    """单条评测用例（与 JSONL 每行字段一一对应）。"""
    id: str  # 用例 ID
    scene: str  # 业务场景
    difficulty: str  # 难度等级
    user_query: str  # 用户提问
    history: List[Dict[str, str]]  # 对话历史
    user_profile: Dict[str, Any]  # 用户画像
    expected_intent: str  # 期望意图
    expected_agent: str  # 期望路由的 Agent
    requires_tool: bool  # 是否需要调工具
    expected_tools: List[str]  # 期望使用的工具列表
    requires_rag: bool  # 是否依赖知识库
    must_escalate: bool  # 是否必须转人工
    risk_level: str  # 风险等级
    expected_outcome: str  # 期望处理结果
    gold_answer_points: List[str]  # 必须覆盖的要点
    forbidden_behaviors: List[str]  # 禁止行为
    metadata: Dict[str, Any]  # 附加元数据


@dataclass
class JudgeScores:
    """LLM 评判四维分数。"""
    point_coverage: float  # 要点覆盖分
    outcome_match: float  # 处理方式匹配分
    safety: float  # 安全性（禁止行为）
    overall: float  # 综合分
    passed: bool  # 是否通过
    summary: str  # 简短说明


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    参数：
        无

    返回：
        解析后的参数对象
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="Run EchoMind customer support eval dataset.")
    # 数据集 JSONL 路径
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="Path to a JSONL dataset file.",
    )
    # EchoMind 服务地址
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="EchoMind API base URL.",
    )
    # 报告输出目录
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated reports.",
    )
    # 报告文件名前缀（不带扩展名）
    parser.add_argument(
        "--report-name",
        default="baseline_v1",
        help="Report file prefix, without extension.",
    )
    # 只评测前 N 条用例（0 表示全部）
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Evaluate only the first N cases. 0 means all cases.",
    )
    # HTTP 请求超时（秒）
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="HTTP request timeout in seconds.",
    )
    # 即使有 API Key 也关闭 LLM 评判
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Disable LLM-as-judge even if API credentials are available.",
    )
    # 解析并返回参数
    return parser.parse_args()


def load_dataset(path: pathlib.Path, limit: int = 0) -> List[DatasetCase]:
    """
    从 JSONL 文件逐行加载评测用例。

    参数：
        path: 数据集文件路径
        limit: 只读取前 N 条（0 表示全部）

    返回：
        DatasetCase 列表

    异常：
        ValueError: 数据集为空
    """
    # 存放加载出的用例
    cases: List[DatasetCase] = []
    # 逐行读取文件
    with path.open("r", encoding="utf-8") as f:
        # 遍历每一行
        for idx, line in enumerate(f, start=1):
            # 空行跳过
            if not line.strip():
                continue
            # 解析该行 JSON
            data = json.loads(line)
            # 用字典展开构造用例对象
            cases.append(DatasetCase(**data))
            # 达到限制条数时提前结束
            if limit and len(cases) >= limit:
                break
    # 数据集为空时抛错
    if not cases:
        raise ValueError(f"Dataset is empty: {path}")
    # 返回用例列表
    return cases


def http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """
    同步发送 HTTP 请求并解析 JSON 响应。

    参数：
        method: HTTP 方法（GET/POST）
        url: 请求地址
        payload: 请求体字典（GET 时通常为 None）
        timeout: 超时秒数

    返回：
        解析后的响应字典
    """
    # 请求体字节（初始为空）
    body = None
    # 请求头（默认接受 JSON）
    headers = {"Accept": "application/json"}
    # 有请求体时序列化并设置 Content-Type
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")  # JSON → UTF-8 字节
        headers["Content-Type"] = "application/json"  # 声明请求体类型
    # 构造 HTTP 请求
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    # 发送请求并读取响应
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")  # 读取并解码响应体
    # 解析响应 JSON
    return json.loads(raw)


async def async_http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """
    异步发送 HTTP 请求（把同步版丢到线程池执行）。

    参数：
        method: HTTP 方法
        url: 请求地址
        payload: 请求体字典
        timeout: 超时秒数

    返回：
        解析后的响应字典
    """
    # 在线程池中执行同步请求，避免阻塞事件循环
    return await asyncio.to_thread(http_json, method, url, payload, timeout)


def build_judge() -> Optional[AsyncAnthropic]:
    """
    从环境变量构建 LLM 评判客户端（无 API Key 返回 None）。

    参数：
        无

    返回：
        AsyncAnthropic 客户端或 None
    """
    # 读取 API Key
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    # 无 Key 则不做评判
    if not api_key:
        return None
    # 组装客户端配置
    kwargs: Dict[str, Any] = {"api_key": api_key}
    # 读取可选 base_url
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    # 非空才写入
    if base_url:
        kwargs["base_url"] = base_url
    # 创建客户端
    return AsyncAnthropic(**kwargs)


async def judge_case(client: AsyncAnthropic, model: str, case: DatasetCase, response_text: str) -> JudgeScores:
    """
    用 LLM 评判单条回答并解析评分。

    参数：
        client: Anthropic 异步客户端
        model: 评判模型名
        case: 评测用例（提供期望与禁止内容）
        response_text: 系统实际回答

    返回：
        JudgeScores（四维分 + 通过标记 + 说明）
    """
    # 组装评判提示词（含期望要点、禁止行为、实际回答）
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
    # 调用 LLM 生成评分
    resp = await client.messages.create(
        model=model,  # 评判模型
        max_tokens=300,  # 输出上限
        temperature=0.0,  # 低温保证稳定
        messages=[{"role": "user", "content": prompt}],  # 用户消息
    )
    # 手工拼接文本块（兼容多 block 响应）
    raw = ""
    # 遍历所有内容块
    for block in getattr(resp, "content", []):
        # 取块内文本
        text = getattr(block, "text", "")
        # 非空则追加
        if text:
            raw += text
    # 定位最外层 JSON 花括号
    start = raw.find("{")
    end = raw.rfind("}") + 1
    # JSON 区间不合法时抛错
    if start < 0 or end <= start:
        raise ValueError(f"Judge returned non-JSON content: {raw}")
    # 解析评分 JSON
    data = json.loads(raw[start:end])
    # 组装评分对象（缺字段回落默认值）
    return JudgeScores(
        point_coverage=float(data.get("point_coverage", 0.0)),  # 要点覆盖分
        outcome_match=float(data.get("outcome_match", 0.0)),  # 方式匹配分
        safety=float(data.get("safety", 0.0)),  # 安全分
        overall=float(data.get("overall", 0.0)),  # 综合分
        passed=bool(data.get("passed", False)),  # 是否通过
        summary=str(data.get("summary", "")),  # 简短说明
    )


async def evaluate_case(
    base_url: str,  # 服务地址
    timeout: float,  # 请求超时
    case: DatasetCase,  # 待评测用例
    judge_client: Optional[AsyncAnthropic],  # LLM 评判客户端（可为 None）
    judge_model: str,  # 评判模型名
) -> Dict[str, Any]:
    """
    评测单条用例：调 /chat、对期望做校验、可选 LLM 评判。

    参数：
        base_url: 服务地址
        timeout: 请求超时
        case: 待评测用例
        judge_client: LLM 评判客户端（可为 None）
        judge_model: 评判模型名

    返回：
        该用例的完整评测结果字典
    """
    # 生成本次调用专用的会话 ID
    conv_id = f"eval_{case.id}_{uuid.uuid4().hex[:8]}"
    # 组装 /chat 请求体
    payload = {
        "message": case.user_query,  # 用户提问
        "user_id": f"eval_user_{case.scene}",  # 评测专用用户 ID
        "conv_id": conv_id,  # 会话 ID
    }
    # 调用 /chat 接口
    response = await async_http_json("POST", f"{base_url.rstrip('/')}/chat", payload, timeout)

    # 提取实际响应字段
    actual_intent = str(response.get("intent", ""))  # 实际意图
    actual_agent = str(response.get("agent_type", ""))  # 实际 Agent
    actual_escalated = bool(response.get("escalated", False))  # 是否转人工
    latency_ms = float(response.get("latency_ms", 0.0))  # 延迟（毫秒）
    knowledge_used = bool(response.get("knowledge_used", False))  # 是否用知识库
    answer = str(response.get("response", ""))  # 回答文本

    # 逐项校验
    intent_match = actual_intent == case.expected_intent  # 意图匹配
    agent_match = actual_agent == case.expected_agent  # Agent 匹配
    escalation_match = actual_escalated == case.must_escalate  # 转人工匹配
    rag_usage_match = knowledge_used if case.requires_rag else True  # RAG 使用匹配（不要求 RAG 时恒真）

    # LLM 评判结果（可为 None）
    judge_scores = None
    # 评判错误信息
    judge_error = None
    # 启用评判时执行
    if judge_client is not None:
        try:
            # 调用 LLM 评判
            judge_scores = await judge_case(judge_client, judge_model, case, answer)
        except Exception as ex:
            # 评判失败仅记录错误
            judge_error = str(ex)

    # 组装完整结果
    return {
        "id": case.id,  # 用例 ID
        "scene": case.scene,  # 场景
        "difficulty": case.difficulty,  # 难度
        "risk_level": case.risk_level,  # 风险等级
        "input": {  # 输入信息
            "user_query": case.user_query,  # 用户提问
            "history": case.history,  # 历史
            "user_profile": case.user_profile,  # 画像
        },
        "expectation": {  # 期望信息
            "expected_intent": case.expected_intent,  # 期望意图
            "expected_agent": case.expected_agent,  # 期望 Agent
            "requires_tool": case.requires_tool,  # 是否需要工具
            "expected_tools": case.expected_tools,  # 期望工具
            "requires_rag": case.requires_rag,  # 是否依赖 RAG
            "must_escalate": case.must_escalate,  # 是否必须转人工
            "expected_outcome": case.expected_outcome,  # 期望结果
            "gold_answer_points": case.gold_answer_points,  # 必须要点
            "forbidden_behaviors": case.forbidden_behaviors,  # 禁止行为
        },
        "actual": {  # 实际信息
            "conv_id": response.get("conv_id"),  # 会话 ID
            "response": answer,  # 回答
            "intent": actual_intent,  # 实际意图
            "agent_type": actual_agent,  # 实际 Agent
            "escalated": actual_escalated,  # 实际转人工
            "latency_ms": latency_ms,  # 延迟
            "knowledge_used": knowledge_used,  # 是否用知识库
        },
        "checks": {  # 校验结果
            "intent_match": intent_match,  # 意图匹配
            "agent_match": agent_match,  # Agent 匹配
            "escalation_match": escalation_match,  # 转人工匹配
            "rag_usage_match": rag_usage_match,  # RAG 使用匹配
            "tool_selection_match": None,  # 工具选择匹配（暂不支持）
        },
        "judge": {  # LLM 评判
            "enabled": judge_client is not None,  # 是否启用
            "scores": asdict(judge_scores) if judge_scores else None,  # 评分
            "error": judge_error,  # 错误
        },
    }


def compute_metrics(case_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    从全部用例结果聚合整体指标。

    参数：
        case_results: 各用例的评测结果列表

    返回：
        指标字典（含各准确率、延迟分位、LLM 评判均值）
    """
    # 用例总数
    total = len(case_results)
    # 每个用例的意图匹配布尔值
    intent_matches = [r["checks"]["intent_match"] for r in case_results]
    # 每个用例的 Agent 匹配布尔值
    agent_matches = [r["checks"]["agent_match"] for r in case_results]
    # 每个用例的转人工匹配布尔值
    escalation_matches = [r["checks"]["escalation_match"] for r in case_results]
    # 每个用例的延迟值
    latency_values = [float(r["actual"]["latency_ms"]) for r in case_results]

    # 需要 RAG 的用例（用于计算召回率）
    rag_required = [r for r in case_results if r["expectation"]["requires_rag"]]
    # RAG 使用召回率（无样本时 None）
    rag_usage_recall = (
        sum(1 for r in rag_required if r["actual"]["knowledge_used"]) / len(rag_required)
        if rag_required else None
    )

    # 有 LLM 评分的结果
    judge_results = [r["judge"]["scores"] for r in case_results if r["judge"]["scores"]]
    # 评判通过率（无样本时 None）
    judge_pass_rate = (
        sum(1 for j in judge_results if j["passed"]) / len(judge_results)
        if judge_results else None
    )

    # 组装聚合指标
    metrics = {
        "intent_accuracy": safe_ratio(sum(intent_matches), total),  # 意图准确率
        "agent_routing_accuracy": safe_ratio(sum(agent_matches), total),  # Agent 路由准确率
        "escalation_accuracy": safe_ratio(sum(escalation_matches), total),  # 转人工准确率
        "rag_usage_recall": round(rag_usage_recall, 4) if rag_usage_recall is not None else None,  # RAG 召回率
        "tool_selection_accuracy": None,  # 工具选择准确率（暂不支持）
        "task_completion_rate": round(judge_pass_rate, 4) if judge_pass_rate is not None else None,  # 任务完成率
        "grounded_response_rate": None,  # 基于事实的回复率（暂不支持）
        "avg_latency_ms": round(statistics.mean(latency_values), 2) if latency_values else 0.0,  # 平均延迟
        "p95_latency_ms": round(percentile(latency_values, 95), 2) if latency_values else 0.0,  # P95 延迟
        "avg_tokens_per_session": None,  # 会话平均 token（暂不支持）
        "tool_success_rate": None,  # 工具成功率（暂不支持）
    }

    # 有 LLM 评分时补充评判维度均值
    if judge_results:
        metrics["judge_point_coverage"] = round(statistics.mean(j["point_coverage"] for j in judge_results), 4)  # 要点覆盖均值
        metrics["judge_outcome_match"] = round(statistics.mean(j["outcome_match"] for j in judge_results), 4)  # 方式匹配均值
        metrics["judge_safety"] = round(statistics.mean(j["safety"] for j in judge_results), 4)  # 安全均值
        metrics["judge_overall"] = round(statistics.mean(j["overall"] for j in judge_results), 4)  # 综合均值

    # 返回指标
    return metrics


def build_summary(case_results: List[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    生成报告摘要（不支持指标、失败用例预览、场景分布）。

    参数：
        case_results: 各用例的评测结果列表
        metrics: 聚合指标字典

    返回：
        报告摘要字典
    """
    # 暂不支持的指标清单
    unsupported = [
        "tool_selection_accuracy",  # 工具选择准确率
        "grounded_response_rate",  # 基于事实的回复率
        "avg_tokens_per_session",  # 会话平均 token
        "tool_success_rate",  # 工具成功率
    ]
    # 失败用例列表
    failed_cases = []
    # 遍历每份结果找出失败用例
    for result in case_results:
        # 取校验结果
        checks = result["checks"]
        # 取 LLM 评分
        judge = result["judge"]["scores"]
        # 任一必检项失败或评判未通过即记为失败
        if not checks["intent_match"] or not checks["agent_match"] or not checks["escalation_match"] or (judge and not judge["passed"]):
            # 记录失败用例摘要
            failed_cases.append({
                "id": result["id"],  # 用例 ID
                "scene": result["scene"],  # 场景
                "actual_intent": result["actual"]["intent"],  # 实际意图
                "actual_agent": result["actual"]["agent_type"],  # 实际 Agent
                "actual_escalated": result["actual"]["escalated"],  # 实际转人工
            })

    # 按场景统计用例数
    by_scene: Dict[str, int] = {}
    # 遍历每份结果
    for result in case_results:
        by_scene[result["scene"]] = by_scene.get(result["scene"], 0) + 1  # 场景计数累加

    # 返回摘要
    return {
        "total_cases": len(case_results),  # 用例总数
        "scenes": by_scene,  # 场景分布
        "unsupported_metrics": unsupported,  # 暂不支持指标
        "failed_case_count": len(failed_cases),  # 失败数
        "failed_cases_preview": failed_cases[:20],  # 失败预览（最多 20 条）
        "metrics_ready_for_comparison": [k for k, v in metrics.items() if v is not None],  # 可用于对比的指标
    }


def safe_ratio(numerator: int, denominator: int) -> float:
    """
    计算比例（分母为 0 时返回 0）。

    参数：
        numerator: 分子
        denominator: 分母

    返回：
        保留 4 位小数的比例
    """
    # 分母非零时计算占比，否则返回 0
    return round(numerator / denominator, 4) if denominator else 0.0


def percentile(values: List[float], p: int) -> float:
    """
    线性插值计算百分位数。

    参数：
        values: 数值列表
        p: 百分位（如 95）

    返回：
        对应百分位的数值
    """
    # 空列表返回 0
    if not values:
        return 0.0
    # 排序副本
    ordered = sorted(values)
    # 单元素直接返回
    if len(ordered) == 1:
        return ordered[0]
    # 计算目标排名的浮点位置
    rank = (len(ordered) - 1) * (p / 100)
    # 取整数下限
    lower = int(rank)
    # 整数上限（不越界）
    upper = min(lower + 1, len(ordered) - 1)
    # 插值权重
    weight = rank - lower
    # 线性插值返回
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def write_json_report(path: pathlib.Path, report: Dict[str, Any]) -> None:
    """
    写 JSON 报告文件。

    参数：
        path: 输出路径
        report: 报告字典
    """
    # 确保父目录存在
    path.parent.mkdir(parents=True, exist_ok=True)
    # 序列化写入（中文不转义、缩进 2）
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown_report(path: pathlib.Path, report: Dict[str, Any]) -> None:
    """
    写 Markdown 报告文件（摘要 + 指标 + 失败用例预览）。

    参数：
        path: 输出路径
        report: 报告字典
    """
    # 报告头部行
    lines = [
        f"# {report['report_name']}",  # 报告标题
        "",  # 空行
        f"- generated_at: `{report['generated_at']}`",  # 生成时间
        f"- dataset: `{report['dataset']['name']}`",  # 数据集名
        f"- total_cases: `{report['summary']['total_cases']}`",  # 用例总数
        f"- base_url: `{report['runtime']['base_url']}`",  # 服务地址
        f"- judge_enabled: `{report['runtime']['judge_enabled']}`",  # 是否启用评判
        "",  # 空行
        "## Metrics",  # 指标小节
        "",  # 空行
    ]
    # 逐项输出指标
    for key, value in report["metrics"].items():
        lines.append(f"- {key}: `{value}`")  # 输出单条指标
    # 追加不支持指标小节
    lines.extend([
        "",  # 空行
        "## Unsupported Metrics",  # 小节标题
        "",  # 空行
    ])
    # 逐项输出不支持指标
    for name in report["summary"]["unsupported_metrics"]:
        lines.append(f"- {name}")  # 输出单条
    # 追加失败用例预览小节
    lines.extend([
        "",  # 空行
        "## Failed Cases Preview",  # 小节标题
        "",  # 空行
    ])
    # 无失败用例时输出 none
    if not report["summary"]["failed_cases_preview"]:
        lines.append("- none")  # 无失败
    else:
        # 逐条输出失败用例
        for case in report["summary"]["failed_cases_preview"]:
            lines.append(
                f"- {case['id']}: intent=`{case['actual_intent']}`, agent=`{case['actual_agent']}`, escalated=`{case['actual_escalated']}`"  # 失败详情
            )
    # 确保父目录存在
    path.parent.mkdir(parents=True, exist_ok=True)
    # 写入 Markdown 文本
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    """
    程序入口：加载数据集 → 逐条评测 → 聚合指标 → 输出报告。

    参数：
        无

    返回：
        进程退出码（0 成功，1 健康检查失败）
    """
    # 解析命令行参数
    args = parse_args()
    # 数据集绝对路径
    dataset_path = pathlib.Path(args.dataset).resolve()
    # 报告输出目录绝对路径
    output_dir = pathlib.Path(args.output_dir).resolve()

    # 先做健康检查
    try:
        health = await async_http_json("GET", f"{args.base_url.rstrip('/')}/health", timeout=args.timeout)  # 调 /health
    except urllib.error.URLError as ex:
        # 服务不可达时打印错误并退出
        print(f"Health check failed: {ex}", file=sys.stderr)
        return 1

    # 加载评测用例
    cases = load_dataset(dataset_path, limit=args.max_cases)
    # 构建评判客户端（skip-judge 或未配 Key 时为 None）
    judge_client = None if args.skip_judge else build_judge()
    # 评判模型名
    judge_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # 存放逐条评测结果
    case_results: List[Dict[str, Any]] = []
    # 遍历每个用例
    for index, case in enumerate(cases, start=1):
        # 打印当前进度
        print(f"[{index}/{len(cases)}] evaluating {case.id}")
        # 评测单条用例
        case_result = await evaluate_case(
            base_url=args.base_url,  # 服务地址
            timeout=args.timeout,  # 超时
            case=case,  # 用例
            judge_client=judge_client,  # 评判客户端
            judge_model=judge_model,  # 评判模型
        )
        # 收集结果
        case_results.append(case_result)

    # 聚合整体指标
    metrics = compute_metrics(case_results)
    # 组装完整报告
    report = {
        "schema_version": DEFAULT_SCHEMA_VERSION,  # schema 版本
        "report_name": args.report_name,  # 报告名
        "generated_at": datetime.utcnow().isoformat() + "Z",  # 生成时间（UTC）
        "dataset": {  # 数据集信息
            "name": dataset_path.stem,  # 文件名
            "path": str(dataset_path),  # 完整路径
            "case_count": len(cases),  # 用例数
        },
        "runtime": {  # 运行环境信息
            "base_url": args.base_url,  # 服务地址
            "health_status": health.get("status"),  # 健康状态
            "judge_enabled": judge_client is not None,  # 是否启用评判
            "judge_model": judge_model if judge_client is not None else None,  # 评判模型
        },
        "metrics": metrics,  # 聚合指标
        "summary": build_summary(case_results, metrics),  # 报告摘要
        "cases": case_results,  # 逐条结果
    }

    # JSON 报告路径
    json_path = output_dir / f"{args.report_name}.json"
    # Markdown 报告路径
    md_path = output_dir / f"{args.report_name}.md"
    # 写 JSON 报告
    write_json_report(json_path, report)
    # 写 Markdown 报告
    write_markdown_report(md_path, report)

    # 打印报告路径
    print(f"Saved JSON report to {json_path}")
    print(f"Saved Markdown report to {md_path}")
    # 成功退出
    return 0


if __name__ == "__main__":
    # 直接运行时以返回码退出
    raise SystemExit(asyncio.run(main()))