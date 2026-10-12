"""
亮点：端到端 Agent 评测框架

核心问题：如何评测端到端 Agent？

评测维度：
  1. 意图识别准确率 —— 预测意图 vs 标注意图，计算 Accuracy / F1
  2. 响应质量评分 —— 用 LLM 作为评判者（LLM-as-Judge），
     从相关性、准确性、完整性、有用性四个维度打分
  3. 端到端对话评测 —— 模拟完整多轮对话，评估整体体验
  4. 回归测试 —— 与历史基线对比，防止性能退化

LLM-as-Judge 是评测 Agent 质量的关键技术：
  人工标注成本高、主观性强；用 LLM 评判可以规模化、可重复。
"""
import asyncio  # 异步执行（调用 LLM / 编排器）
import json  # 版本序列化 / 反序列化
import logging  # 日志记录
import pathlib  # 基线文件路径
import statistics  # 均值等统计计算
import time  # 时间戳
from dataclasses import asdict, dataclass, field  # 数据类及序列化
from datetime import datetime  # 时间戳生成
from typing import Any, Dict, List, Optional  # 类型注解

from anthropic import AsyncAnthropic  # Anthropic 异步客户端（LLM-as-Judge）

from core.intent_recognizer import IntentCategory, IntentRecognizer  # 意图识别器及其分类
from core.llm_utils import extract_text  # 从 LLM 响应提取文本

logger = logging.getLogger(__name__)  # 模块级日志记录器


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class IntentTestCase:
    """单条意图识别测试用例。"""
    message:          str  # 待识别的用户消息
    expected_intent:  str  # 期望的意图标签
    context:          Optional[Dict[str, Any]] = None  # 可选上下文（会话信息）


@dataclass
class QualityScores:
    """LLM-as-Judge 评分结果。"""
    relevance:    float   # 相关性：回答是否针对问题
    accuracy:     float   # 准确性：信息是否正确
    completeness: float   # 完整性：是否完整解决问题
    helpfulness:  float   # 有用性：用户是否能据此行动
    judge_failed: bool = False  # 评判过程是否失败
    error: Optional[str] = None  # 评判失败时的错误信息

    @property
    def overall(self) -> float:
        """四个维度的平均分（综合评分）。"""
        # 计算四维度均值作为综合评分
        return statistics.mean([self.relevance, self.accuracy, self.completeness, self.helpfulness])


@dataclass
class EvalResult:
    """单条评测结果。"""
    test_id:    str  # 测试用例 ID
    passed:     bool  # 是否通过
    scores:     Dict[str, float]  # 各维度评分
    detail:     str = ""  # 结果描述
    metadata:   Dict[str, Any] = field(default_factory=dict)  # 附加元数据（对话内容、意图等）


@dataclass
class EvalReport:
    """评测报告。"""
    timestamp:        str  # 报告生成时间戳
    total:            int  # 用例总数
    passed:           int  # 通过用例数
    pass_rate:        float  # 通过率
    avg_scores:       Dict[str, float]  # 各指标平均分
    regressions:      List[str]          # 相比基线退化的指标
    recommendations:  List[str]  # 优化建议列表
    results:          List[EvalResult]  # 全部单条结果


# ── LLM-as-Judge ─────────────────────────────────────────────────────────────

class LLMJudge:
    """
    用 LLM 评判 Agent 响应质量。

    为什么用 LLM 而不是人工？
    - 可规模化：数千条测试用例自动评测
    - 可重复：相同输入得到稳定评分
    - 多维度：同时评估相关性、准确性等多个维度

    注意：LLM Judge 本身也有偏差，建议定期用人工标注校准。
    """

    # 评判提示词模板（{question}/{response}/{context_section} 由 format 填充）
    JUDGE_PROMPT = """你是一个客服质量评估专家。请对以下客服响应进行评分。

用户问题: {question}
Agent 响应: {response}
{context_section}

请从以下四个维度评分（0.0-1.0），返回 JSON：
- relevance: 响应是否直接针对用户问题（0=完全无关，1=完全相关）
- accuracy: 信息是否准确无误（0=明显错误，1=完全正确）
- completeness: 是否完整解决了用户需求（0=完全没解决，1=完全解决）
- helpfulness: 用户能否据此采取行动（0=毫无帮助，1=非常有帮助）

只返回 JSON，例如: {{"relevance": 0.9, "accuracy": 0.8, "completeness": 0.7, "helpfulness": 0.85}}"""

    def __init__(self, client: AsyncAnthropic, model: str):
        """
        初始化 LLM 评判器。

        参数：
            client: Anthropic 异步客户端
            model: 用于评判的模型名
        """
        self._client = client  # 保存 Anthropic 客户端
        self._model  = model  # 保存评判模型名

    async def judge(
        self,
        question: str,  # 用户问题
        response: str,  # Agent 响应文本
        context: Optional[str] = None,  # 可选背景信息
    ) -> QualityScores:
        """
        对单条 Agent 响应做四维度评分。

        参数：
            question: 用户问题
            response: Agent 的响应文本
            context: 可选的背景信息（补充给评判模型）

        返回：
            QualityScores（评判失败时返回全 0.5 且 judge_failed=True）
        """
        # 有上下文时构造背景信息段落，否则留空
        ctx_section = f"背景信息: {context}" if context else ""
        # 用模板填充评判提示词
        prompt = self.JUDGE_PROMPT.format(
            question=question,  # 用户问题
            response=response,  # Agent 响应
            context_section=ctx_section,  # 背景段落
        )
        # 清洗文本（移除无法编码的代理字符）
        prompt = self._clean_text(prompt)
        # 调用 LLM 并解析结果
        try:
            # 向模型发起评分请求（低温保证稳定输出）
            resp = await self._client.messages.create(
                model=self._model, max_tokens=256, temperature=0.0,  # 模型 / 输出上限 / 温度
                messages=[{"role": "user", "content": prompt}],  # 用户消息内容
            )
            # 从响应中提取纯文本
            raw = extract_text(resp)
            # 取最外层花括号作为 JSON 区间
            s, e = raw.find("{"), raw.rfind("}") + 1
            # 解析 JSON 评分
            data = json.loads(raw[s:e])
            # 组装四维度评分（缺失字段回落 0.5）
            return QualityScores(
                relevance=float(data.get("relevance", 0.5)),  # 相关性分
                accuracy=float(data.get("accuracy", 0.5)),  # 准确性分
                completeness=float(data.get("completeness", 0.5)),  # 完整性分
                helpfulness=float(data.get("helpfulness", 0.5)),  # 有用性分
            )
        except Exception as ex:
            # 评判失败时记警告日志
            logger.warning(f"LLM Judge 失败: {ex}")
            # 返回中性分并标记失败
            return QualityScores(
                0.5, 0.5, 0.5, 0.5,  # 四维度均取中性分
                judge_failed=True,  # 标记评判失败
                error=str(ex),  # 记录错误信息
            )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        # 空值直接返回空字符串
        if value is None:
            return ""
        # 非字符串先转成字符串
        if not isinstance(value, str):
            value = str(value)
        # 按 UTF-8 忽略错误重新编码解码，丢弃非法代理字符
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ── 意图识别评测 ──────────────────────────────────────────────────────────────

class IntentEvaluator:
    """评测意图识别的准确率和 F1。"""

    def __init__(self, recognizer: IntentRecognizer):
        """
        初始化意图评测器。

        参数：
            recognizer: 意图识别器实例
        """
        self._recognizer = recognizer  # 保存意图识别器

    async def evaluate(self, cases: List[IntentTestCase]) -> Dict[str, Any]:
        """
        批量评测意图识别，返回准确率、Macro-F1 和逐条详情。

        参数：
            cases: 意图测试用例列表

        返回：
            评测指标字典（accuracy / macro_f1 / per_class / total / correct / cases）
        """
        # 收集预测意图和真实意图
        predictions, ground_truth = [], []
        # 逐条用例详情
        case_details: List[Dict[str, Any]] = []

        # 遍历所有测试用例
        for case in cases:
            # 调用识别器（异步）
            result = await self._recognizer.recognize(case.message)
            # 取识别出的意图标签
            predicted = result.intent.value
            # 记录预测意图
            predictions.append(predicted)
            # 记录真实意图
            ground_truth.append(case.expected_intent)
            # 记录本条用例详情
            case_details.append({
                "message": case.message,  # 用户消息
                "expected": case.expected_intent,  # 期望意图
                "predicted": predicted,  # 预测意图
                "confidence": result.confidence,  # 识别置信度
                "reasoning": result.reasoning,  # 识别推理过程
            })

        # 纯 Python 计算指标
        # 统计预测与真实一致的条数
        correct = sum(p == g for p, g in zip(predictions, ground_truth))
        # 计算整体准确率（无样本时为 0）
        accuracy = correct / len(predictions) if predictions else 0.0

        # 每类 F1
        # 取预测与真实的所有标签（去重排序）
        labels = sorted(set(ground_truth + predictions))
        # 每类的精确率 / 召回率 / F1
        per_class: Dict[str, Dict[str, float]] = {}
        # 遍历每个标签
        for label in labels:
            # 真正例：预测该标签且真实是该标签
            tp = sum(p == label and g == label for p, g in zip(predictions, ground_truth))
            # 假正例：预测该标签但真实不是
            fp = sum(p == label and g != label for p, g in zip(predictions, ground_truth))
            # 假负例：真实是该标签但预测不是
            fn = sum(p != label and g == label for p, g in zip(predictions, ground_truth))
            # 精确率（分母为 0 时取 0）
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            # 召回率（分母为 0 时取 0）
            rec  = tp / (tp + fn) if (tp + fn) else 0.0
            # F1（精确率与召回率都为 0 时取 0）
            f1   = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            # 保存该类的三项指标
            per_class[label] = {"precision": prec, "recall": rec, "f1": f1}

        # 全部类别的 F1 均值（Macro-F1）
        macro_f1 = statistics.mean(v["f1"] for v in per_class.values()) if per_class else 0.0

        # 返回汇总指标
        return {
            "accuracy":   round(accuracy, 4),  # 整体准确率
            "macro_f1":   round(macro_f1, 4),  # Macro-F1
            "per_class":  per_class,  # 每类指标
            "total":      len(cases),  # 用例总数
            "correct":    correct,  # 正确数
            "cases":      case_details,  # 逐条详情
        }


# ── 端到端评测器 ──────────────────────────────────────────────────────────────

class EndToEndEvaluator:
    """
    端到端 Agent 评测。

    评测流程：
      1. 运行意图识别评测（准确率/F1）
      2. 运行对话质量评测（LLM-as-Judge）
      3. 与历史基线对比（回归检测）
      4. 生成可操作的优化建议
    """

    # 质量及格线
    PASS_THRESHOLD = 0.75

    def __init__(
        self,
        orchestrator,  # Agent 编排器（产出回复）
        recognizer: IntentRecognizer,  # 意图识别器
        api_key:  str,  # Anthropic API Key（LLM-as-Judge 用）
        base_url: Optional[str] = None,  # 可选 API 网关地址
        model:    str = "claude-3-5-sonnet-20241022",  # 评判模型名
        baseline_path: Optional[str] = None,  # 基线文件路径
    ):
        # 先组装客户端参数
        kwargs: Dict[str, Any] = {"api_key": api_key}
        # 有 base_url 时追加到客户端参数
        if base_url:
            kwargs["base_url"] = base_url
        # 创建 Anthropic 异步客户端
        client = AsyncAnthropic(**kwargs)

        self._orchestrator     = orchestrator  # 保存编排器
        self._judge            = LLMJudge(client, model)  # 创建 LLM 评判器
        self._intent_evaluator = IntentEvaluator(recognizer)  # 创建意图评测器
        self._history:         List[EvalReport] = []  # 本次运行的评测历史
        self._baseline_path = pathlib.Path(baseline_path) if baseline_path else None  # 基线文件路径
        self._baseline: Optional[EvalReport] = self._load_baseline()  # 加载历史基线

    async def run(
        self,
        intent_cases:    Optional[List[IntentTestCase]] = None,  # 意图识别测试用例
        dialog_cases:    Optional[List[Dict[str, Any]]] = None,  # 对话评测用例
    ) -> EvalReport:
        """
        运行完整评测。

        intent_cases: 意图识别测试用例
        dialog_cases:
          - 单轮: [{"question": "..."}]
          - 多轮: [{"turns": ["第一轮", "第二轮", ...]}]
        """
        # 收集全部单条结果
        results: List[EvalResult] = []
        # 按维度收集评分（用于计算均值）
        all_scores: Dict[str, List[float]] = {
            "relevance": [], "accuracy": [], "completeness": [], "helpfulness": []
        }

        # 1. 意图识别评测
        # 意图识别的汇总指标
        intent_metrics: Dict[str, Any] = {}
        # 只提供了意图用例才评测
        if intent_cases:
            # 运行意图评测
            intent_metrics = await self._intent_evaluator.evaluate(intent_cases)
            # 准确率达到及格线才算通过
            passed = intent_metrics["accuracy"] >= self.PASS_THRESHOLD
            # 追加意图识别结果
            results.append(EvalResult(
                test_id="intent_recognition",  # 测试 ID
                passed=passed,  # 是否通过
                scores={"accuracy": intent_metrics["accuracy"], "macro_f1": intent_metrics["macro_f1"]},  # 核心指标
                detail=f"准确率 {intent_metrics['accuracy']:.1%}，Macro-F1 {intent_metrics['macro_f1']:.3f}",  # 描述
                metadata={  # 附加元数据
                    "total": intent_metrics.get("total", 0),  # 用例总数
                    "correct": intent_metrics.get("correct", 0),  # 正确数
                    "cases": intent_metrics.get("cases", []),  # 逐条详情
                },
            ))

        # 2. 对话质量评测（调用 orchestrator 产出回复，再用 LLM Judge 评分）
        # 只提供了对话用例才评测
        if dialog_cases:
            # 遍历每个对话用例
            for i, case in enumerate(dialog_cases):
                # 评测单个对话用例（可能多轮）
                case_results = await self._evaluate_dialog_case(case, i)
                # 扩展结果列表
                results.extend(case_results)
                # 把每轮评分按维度归集
                for r in case_results:
                    # 遍历四个维度
                    for k in all_scores:
                        # 该维度有分才算入
                        if k in r.scores:
                            # 归集该维度分数
                            all_scores[k].append(r.scores[k])

        # 3. 汇总
        # 各维度平均分（空维度不出现在结果中）
        avg_scores = {
            k: round(statistics.mean(v), 4) for k, v in all_scores.items() if v
        }
        # 有意图评测时把意图准确率并入平均分
        if intent_metrics:
            avg_scores["intent_accuracy"] = intent_metrics["accuracy"]

        # 统计通过用例数
        passed_count = sum(1 for r in results if r.passed)
        # 计算通过率（无结果为 0）
        pass_rate    = passed_count / len(results) if results else 0.0

        # 4. 回归检测
        # 对比基线找出退化指标
        regressions = self._detect_regressions(avg_scores)

        # 5. 优化建议
        # 基于评分生成优化建议
        recommendations = self._recommendations(avg_scores, intent_metrics)

        # 组装评测报告
        report = EvalReport(
            timestamp=datetime.now().isoformat(),  # 报告时间戳
            total=len(results),  # 用例总数
            passed=passed_count,  # 通过数
            pass_rate=round(pass_rate, 4),  # 通过率
            avg_scores=avg_scores,  # 平均分
            regressions=regressions,  # 退化指标
            recommendations=recommendations,  # 优化建议
            results=results,  # 全部单条结果
        )
        # 记入本次运行历史
        self._history.append(report)
        # 更新本地基线文件
        self._save_baseline(report)
        # 返回评测报告
        return report

    async def _evaluate_dialog_case(self, case: Dict[str, Any], case_idx: int) -> List[EvalResult]:
        """
        评测单轮或多轮对话用例。

        参数：
            case: 对话用例（{"question": ...} 或 {"turns": [...]}）
            case_idx: 用例序号（用于生成测试 ID）

        返回：
            该用例的 EvalResult 列表（每轮一条）
        """
        # 延迟导入编排器请求体（避免文件顶部循环依赖）
        from agents.agent_orchestrator import Request as OrcReq

        # 解析对话轮次（多轮返回 turns，单轮返回 question）
        questions = self._dialog_turns(case)
        # 用例为空直接返回空列表
        if not questions:
            return []

        # 组装会话 ID
        conv_id = str(case.get("conv_id") or f"eval_{case_idx}")
        # 组装用户 ID
        user_id = str(case.get("user_id") or "eval_user")
        # 维护对话历史（供上下文使用）
        history: List[Dict[str, str]] = []
        # 本用例的结果列表
        results: List[EvalResult] = []

        # 逐轮评测
        for turn_idx, question in enumerate(questions):
            # 构造上下文摘要（取最近 8 轮）
            context = self._history_context(history)
            # 组装编排器请求
            orch_req = OrcReq(
                message=question,  # 当前轮用户消息
                user_id=user_id,  # 用户 ID
                conv_id=conv_id,  # 会话 ID
                context=context,  # 上下文摘要
                history=history[-6:] if history else None,  # 最近 6 轮对话历史
            )
            # 调用编排器产出回复
            orch_result = await self._orchestrator.run(orch_req)
            # 取 Agent 的实际回复
            actual_answer = orch_result.response

            # 用 LLM Judge 评分
            scores = await self._judge.judge(question, actual_answer, context=context or None)
            # 综合分达到及格线才算通过
            passed = scores.overall >= self.PASS_THRESHOLD

            # 追加用户轮到历史
            history.append({"role": "user", "content": question})
            # 追加助手轮到历史
            history.append({"role": "assistant", "content": actual_answer})

            # 单轮用例用 dialog_<idx>，多轮用例追加轮次号
            test_id = f"dialog_{case_idx}" if len(questions) == 1 else f"dialog_{case_idx}_turn_{turn_idx}"
            # 记录本轮评测结果
            results.append(EvalResult(
                test_id=test_id,  # 测试 ID
                passed=passed,  # 是否通过
                scores={  # 四维度 + 综合分
                    "relevance": scores.relevance,  # 相关性
                    "accuracy": scores.accuracy,  # 准确性
                    "completeness": scores.completeness,  # 完整性
                    "helpfulness": scores.helpfulness,  # 有用性
                    "overall": scores.overall,  # 综合分
                },
                detail=f"Q: {question[:30]}... → 综合评分 {scores.overall:.3f}",  # 结果描述
                metadata={  # 附加元数据
                    "question": question,  # 用户问题
                    "response": actual_answer,  # Agent 回复
                    "agent_type": orch_result.agent_type.value,  # 实际路由的 Agent 类型
                    "intent": orch_result.intent.value if orch_result.intent else None,  # 识别出的意图
                    "turn": turn_idx,  # 轮次
                    "conv_id": conv_id,  # 会话 ID
                    "judge_failed": scores.judge_failed,  # 评判是否失败
                    "judge_error": scores.error,  # 评判错误信息
                },
            ))

        # 返回该用例的全部结果
        return results

    @staticmethod
    def _dialog_turns(case: Dict[str, Any]) -> List[str]:
        """
        从用例中解析对话轮次。

        参数：
            case: 对话用例字典

        返回：
            轮次文本列表（多轮取 turns，单轮取 question，均空返回空列表）
        """
        # 取 turns 字段
        turns = case.get("turns")
        # turns 是列表时逐条过滤空白
        if isinstance(turns, list):
            return [str(t) for t in turns if str(t).strip()]
        # 否则取单轮 question 字段
        question = case.get("question")
        # 有 question 返回单元素列表，否则返回空
        return [str(question)] if question else []

    @staticmethod
    def _history_context(history: List[Dict[str, str]]) -> str:
        """
        把最近的对话历史拼成给编排器的上下文摘要。

        参数：
            history: 对话历史列表（{"role": ..., "content": ...}）

        返回：
            拼接后的上下文文本（无历史返回空串）
        """
        # 没有历史时返回空上下文
        if not history:
            return ""
        # 拼接最近 8 轮为 "角色: 内容" 行
        lines = [f"{m['role']}: {m['content']}" for m in history[-8:]]
        # 加上标题后返回
        return "[评测多轮历史]\n" + "\n".join(lines)

    def _detect_regressions(self, current: Dict[str, float]) -> List[str]:
        """
        与上一次评测对比，找出退化超过 5% 的指标。

        参数：
            current: 本次评测的指标值

        返回：
            退化指标描述列表
        """
        # 取上一次报告（本次历史或磁盘基线）
        prev_report = self._history[-1] if self._history else self._baseline
        # 没有参照物时不做回归检测
        if prev_report is None:
            return []
        # 取上一次的指标值
        prev = prev_report.avg_scores
        # 退化指标列表
        regressions = []
        # 遍历本次各指标
        for metric, value in current.items():
            # 仅对比上期存在且为正的指标
            if metric in prev and prev[metric] > 0:
                # 计算相对变化率
                delta = (value - prev[metric]) / prev[metric]
                # 跌幅超过 5% 记为退化
                if delta < -0.05:
                    # 记录退化指标及变化幅度
                    regressions.append(
                        f"{metric}: {prev[metric]:.3f} → {value:.3f} (退化 {abs(delta):.1%})"
                    )
        # 返回退化指标列表
        return regressions

    def _recommendations(
        self,
        scores: Dict[str, float],  # 本次平均分
        intent_metrics: Dict[str, Any],  # 意图识别指标
    ) -> List[str]:
        """
        依据评分生成可操作的优化建议。

        参数：
            scores: 各指标平均分
            intent_metrics: 意图识别指标

        返回：
            优化建议文本列表
        """
        # 建议列表
        recs = []
        # 意图准确率低于 90% 时给建议
        if scores.get("intent_accuracy", 1.0) < 0.90:
            recs.append("意图识别准确率 < 90%：增加 Few-shot 示例，或对低 F1 的意图类别补充训练数据")
        # 相关性低于 0.75 时给建议
        if scores.get("relevance", 1.0) < 0.75:
            recs.append("相关性偏低：检查 Agent system_prompt，确保 Agent 聚焦于用户问题")
        # 完整性低于 0.75 时给建议
        if scores.get("completeness", 1.0) < 0.75:
            recs.append("完整性偏低：Agent 可能过早结束回答，考虑在 prompt 中要求提供完整解决方案")
        # 有用性低于 0.75 时给建议
        if scores.get("helpfulness", 1.0) < 0.75:
            recs.append("有用性偏低：回答可能过于抽象，考虑要求 Agent 提供具体操作步骤")
        # 全部达标时给肯定性建议
        if not recs:
            recs.append("所有指标均达标，继续保持")
        # 返回建议列表
        return recs

    @property
    def history(self) -> List[EvalReport]:
        """本次运行产生的评测报告列表（只读属性）。"""
        return self._history

    def _load_baseline(self) -> Optional[EvalReport]:
        """
        从磁盘加载历史评测基线。

        参数：
            无

        返回：
            EvalReport 或 None（未配置路径 / 文件不存在 / 解析失败）
        """
        # 未配置路径或文件不存在时返回空
        if not self._baseline_path or not self._baseline_path.exists():
            return None
        # 加载并解析基线文件
        try:
            # 读取 JSON 文件
            data = json.loads(self._baseline_path.read_text(encoding="utf-8"))
            # 反序列化为 EvalReport
            return self._report_from_dict(data)
        except Exception as ex:
            # 解析失败仅记警告日志
            logger.warning(f"读取评测基线失败: {ex}")
            # 返回空基线
            return None

    def _save_baseline(self, report: EvalReport) -> None:
        """
        把最新评测报告写入基线文件。

        参数：
            report: 本次评测报告
        """
        # 未配置路径时直接跳过
        if not self._baseline_path:
            return
        # 写入基线文件
        try:
            # 创建父目录（已存在则跳过）
            self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
            # 序列化为带缩进的 JSON 并写盘
            self._baseline_path.write_text(
                json.dumps(asdict(report), ensure_ascii=False, indent=2),  # 中文不转义，缩进 2
                encoding="utf-8",
            )
            # 更新内存中的基线
            self._baseline = report
        except Exception as ex:
            # 写入失败仅记警告日志
            logger.warning(f"保存评测基线失败: {ex}")

    @staticmethod
    def _report_from_dict(data: Dict[str, Any]) -> EvalReport:
        """
        把 JSON 字典反序列化为 EvalReport（容错：缺失字段用默认值）。

        参数：
            data: 从基线文件读出的字典

        返回：
            反序列化后的 EvalReport
        """
        # 组装评测报告
        return EvalReport(
            timestamp=data.get("timestamp", ""),  # 报告时间戳
            total=int(data.get("total", 0)),  # 用例总数
            passed=int(data.get("passed", 0)),  # 通过数
            pass_rate=float(data.get("pass_rate", 0.0)),  # 通过率
            avg_scores=dict(data.get("avg_scores", {})),  # 平均分
            regressions=list(data.get("regressions", [])),  # 退化指标
            recommendations=list(data.get("recommendations", [])),  # 优化建议
            results=[  # 逐条反序列化结果
                EvalResult(
                    test_id=r.get("test_id", ""),  # 测试 ID
                    passed=bool(r.get("passed", False)),  # 是否通过
                    scores=dict(r.get("scores", {})),  # 各维度评分
                    detail=r.get("detail", ""),  # 结果描述
                    metadata=dict(r.get("metadata", {})),  # 附加元数据
                )
                for r in data.get("results", [])  # 遍历原始结果列表
            ],
        )


# ── 内置测试用例（开箱即用）──────────────────────────────────────────────────

DEFAULT_INTENT_CASES: List[IntentTestCase] = [
    IntentTestCase("我的订单什么时候到？",       "query"),  # 订单查询
    IntentTestCase("帮我取消订单",               "request"),  # 服务请求
    IntentTestCase("你们服务太差了！",            "complaint"),  # 投诉
    IntentTestCase("应用一直报500错误",           "technical"),  # 技术问题
    IntentTestCase("为什么扣了两次款？",          "billing"),  # 账单问题
    IntentTestCase("我要投诉，转人工！",          "escalation"),  # 升级人工
    IntentTestCase("你好",                        "greeting"),  # 问候
    IntentTestCase("修改我的邮箱地址",            "account"),  # 账户操作
]

DEFAULT_DIALOG_CASES: List[Dict[str, Any]] = [
    {"question": "我的订单 #12345 还没到，已经超时了"},  # 单轮：订单时效
    {"question": "应用登录一直报错 401"},  # 单轮：登录报错
    {"question": "为什么这个月多扣了 50 块钱？"},  # 单轮：扣费疑问
    {"question": "帮我把收货地址改成北京市朝阳区"},  # 单轮：地址修改
    {"turns": ["你好，我想退款", "订单号是 #12345", "退款多久能到账？"]},  # 多轮：退款流程
]