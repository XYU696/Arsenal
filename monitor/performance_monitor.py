"""
亮点：利用 Monitor 监控 Agent 在线表现

核心问题：如何利用 Monitor 监控 Agent 的在线表现？

本模块的答案：
  1. 实时采集 —— 每隔 N 秒从 Orchestrator 和 ToolManager 拉取最新统计
  2. 异常检测 —— Z-score 统计方法，自动发现指标突变
  3. 路由反馈 —— 将 Agent 成功率/延迟写回 Orchestrator，
     Orchestrator 的 _best_agent() 会据此动态调整路由权重
  4. 优化建议 —— 基于规则生成可操作的优化建议（不是空话）
  5. 告警 —— 超阈值时打日志 + 可选 Webhook
"""
import asyncio
import logging
import statistics
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

import httpx
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class Severity(Enum):
    """告警严重程度枚举。"""
    INFO     = "info"  # 信息
    WARNING  = "warning"  # 警告
    ERROR    = "error"  # 错误
    CRITICAL = "critical"  # 严重


@dataclass
class Alert:
    """
    告警记录。

    当某个指标越过阈值时生成，供 summary() 对外暴露、
    日志输出和 Webhook 通知使用。
    """
    severity:    Severity  # 严重程度
    metric:      str  # 指标名称（含标签，如 agent_success_rate:support_agent）
    message:     str  # 人类可读的告警信息
    value:       float  # 触发告警时的指标值
    threshold:   float  # 触发阈值
    ts:          str = field(default_factory=lambda: datetime.now().isoformat())  # 告警时间戳
    resolved:    bool = False  # 是否已解决


@dataclass
class Suggestion:
    """可操作的优化建议。"""
    title:       str  # 建议标题
    detail:      str  # 依据数据的具体说明
    action:      str    # 具体操作步骤
    priority:    int    # 1-10：建议优先级


# ── 异常检测 ──────────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    基于滑动窗口 Z-score 的异常检测。

    Z-score = |当前值 - 均值| / 标准差
    超过 sensitivity 倍标准差则判定为异常。
    用滑动窗口（deque 定长）记忆历史，无需存全量数据。
    """

    def __init__(self, window: int = 60, sensitivity: float = 2.5):
        """
        初始化异常检测器。

        参数：
            window: 滑动窗口大小（保留最近多少个数据点）
            sensitivity: Z-score 灵敏度系数（超过其倍数即判异常）
        """
        # 保存窗口大小
        self._window      = window
        # 保存灵敏度系数
        self._sensitivity = sensitivity
        # 按指标名存储滑窗数据（deque 自动淘汰最旧数据）
        self._history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def record(self, metric: str, value: float) -> Optional[Dict[str, Any]]:
        """
        记录一个数据点，如果异常则返回异常信息，否则返回 None。

        参数：
            metric: 指标名称
            value: 当前数据点值

        返回：
            异常信息字典（含 z_score、severity）；无异常返回 None
        """
        # 取该指标的历史缓冲
        buf = self._history[metric]
        # 记录当前数据点
        buf.append(value)

        # 数据不足半个窗口时不检测
        if len(buf) < self._window // 2:
            return None  # 数据不足，不检测

        # 计算均值和标准差
        mean  = statistics.mean(buf)
        # 只有 1 个点时标准差为 0（样本方差需至少 2 点）
        stdev = statistics.stdev(buf) if len(buf) > 1 else 0.0
        # 标准差为 0 时无法判定异常
        if stdev == 0:
            return None

        # 计算 Z-score
        z = abs(value - mean) / stdev
        # 超过灵敏度阈值判为异常
        if z > self._sensitivity:
            # 返回异常详情（z 越大严重程度越高）
            return {
                "metric":   metric,  # 指标名
                "value":    value,  # 当前值
                "mean":     mean,  # 历史均值
                "z_score":  round(z, 2),  # Z-score
                "severity": "high" if z > self._sensitivity * 1.5 else "medium",  # 严重程度（超过 1.5 倍灵敏度为 high）
            }
        return None


# ── 性能监控器 ────────────────────────────────────────────────────────────────

class PerformanceMonitor:
    """
    Agent 在线表现监控。

    与 Orchestrator 的联动：
      Monitor 采集 → 发现某 Agent 成功率下降 →
      Orchestrator.get_stats() 中该 Agent 的 routing_score 自动降低 →
      _best_agent() 路由时自动绕开该 Agent

    这就是"利用 Monitor 监控在线表现"的闭环。
    """

    # 告警阈值配置：指标名 -> (阈值, 严重程度, 比较符)
    THRESHOLDS = {
        "agent_success_rate":  (0.90, Severity.ERROR,   "less_than"),  # Agent 成功率低于 90% 告错
        "tool_success_rate":   (0.95, Severity.WARNING,  "less_than"),  # 工具成功率低于 95% 告警
        "agent_avg_ms":        (3000, Severity.WARNING,  "greater_than"),  # Agent 平均耗时高于 3000ms 告警
        "tool_avg_ms":         (5000, Severity.ERROR,    "greater_than"),  # 工具平均耗时高于 5000ms 告错
    }

    def __init__(
        self,
        orchestrator,  # Agent 编排器（提供 get_stats / update_routing_penalties）
        tool_manager,  # 工具管理器（提供 get_stats）
        interval_s:       float = 10.0,  # 采集间隔（秒）
        webhook_url:      Optional[str] = None,  # 告警 Webhook 地址（可选）
        prometheus_port:  Optional[int] = None,   # Prometheus 暴露端口（None = 不启动）
    ):
        # 保存编排器引用（采集 Agent 统计 + 写回路由降权）
        self._orchestrator = orchestrator
        # 保存工具管理器引用（采集工具统计）
        self._tool_manager = tool_manager
        # 记录采集间隔（秒）
        self._interval     = interval_s
        # 记录告警 Webhook 地址
        self._webhook      = webhook_url
        # 初始化异常检测器
        self._detector     = AnomalyDetector()

        # 告警记录列表
        self._alerts:      List[Alert]      = []
        # 优化建议列表
        self._suggestions: List[Suggestion] = []
        # 运行中标记
        self._active       = False
        # 采集循环任务句柄
        self._task:        Optional[asyncio.Task] = None

        # Prometheus 指标（可选）
        self._prom: Dict[str, Any] = {}
        # 指定了端口才启动 Prometheus
        if prometheus_port:
            self._setup_prometheus(prometheus_port)

    def _setup_prometheus(self, port: int) -> None:
        """
        初始化并启动 Prometheus 指标暴露。

        参数：
            port: Prometheus HTTP 端口
        """
        # 定义需暴露的指标
        self._prom = {
            "agent_success_rate": Gauge("agent_success_rate", "Agent 成功率", ["agent"]),  # Agent 成功率（按 agent 分标签）
            "agent_latency_ms":   Histogram("agent_latency_ms", "Agent 延迟", ["agent"]),  # Agent 延迟（按 agent 分标签）
            "tool_success_rate":  Gauge("tool_success_rate", "工具成功率", ["tool"]),  # 工具成功率（按 tool 分标签）
            "requests_total":     Counter("requests_total", "总请求数"),  # 总请求数
        }
        # 启动 Prometheus HTTP 服务
        start_http_server(port)
        # 记录启动日志
        logger.info(f"Prometheus 已启动: :{port}")

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """启动采集循环（重复调用不重复启动）。"""
        # 已在运行时不重复启动
        if self._active:
            return
        # 标记为运行中
        self._active = True
        # 创建后台采集协程任务
        self._task   = asyncio.create_task(self._loop())
        # 记录启动日志
        logger.info(f"Monitor 已启动，采集间隔 {self._interval}s")

    async def stop(self) -> None:
        """停止采集循环并取消后台任务。"""
        # 清除运行标记（循环退出条件）
        self._active = False
        # 有任务时取消
        if self._task:
            self._task.cancel()
            try:
                # 等待任务结束
                await self._task
            except asyncio.CancelledError:
                pass  # 任务被取消属正常流程

    # ── 采集循环 ──────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """后台采集循环：周期执行一次 _collect，异常不中断循环。"""
        # 运行期间不断采集
        while self._active:
            try:
                # 执行一次采集
                await self._collect()
            except Exception as ex:
                # 单次采集异常不中断循环
                logger.error(f"Monitor 采集异常: {ex}")
            # 按间隔休眠
            await asyncio.sleep(self._interval)

    async def _collect(self) -> None:
        """
        采集 Agent 和工具的实时统计，检测异常，生成建议。

        关键：这里读取的 stats 就是 Orchestrator/ToolManager 在处理请求时
        实时更新的数据，Monitor 不需要额外埋点。
        """
        # 拉取所有 Agent 的实时统计
        agent_stats = self._orchestrator.get_stats()
        # 拉取所有工具的实时统计
        tool_stats  = self._tool_manager.get_stats()
        # 记录每个 Agent 的路由降权系数
        routing_penalties: Dict[str, float] = {}

        # ── Agent 指标 ────────────────────────────────────────────────────────
        # 遍历每个 Agent 的统计
        for agent_key, s in agent_stats.items():
            sr  = s["success_rate"]  # 成功率
            ms  = s["avg_ms"]  # 平均耗时（毫秒）

            # 异常检测
            # 对成功率和延迟分别做 Z-score 异常检测
            for metric, value in [("agent_success_rate", sr), ("agent_avg_ms", ms)]:
                # 记录数据点并检测
                anomaly = self._detector.record(f"{metric}:{agent_key}", value)
                # 命中异常时打日志
                if anomaly:
                    logger.warning(f"异常检测 [{agent_key}] {metric}={value:.3f} z={anomaly['z_score']}")

            # 阈值告警
            self._check_threshold("agent_success_rate", sr, agent_key)  # 成功率阈值告警
            self._check_threshold("agent_avg_ms", ms, agent_key)  # 延迟阈值告警

            # Prometheus
            # 启用 Prometheus 时更新指标
            if "agent_success_rate" in self._prom:
                self._prom["agent_success_rate"].labels(agent=agent_key).set(sr)  # 成功率（按 agent 分标签）
                self._prom["agent_latency_ms"].labels(agent=agent_key).observe(ms)  # 延迟直方图

            # 计算该 Agent 的路由降权系数
            routing_penalties[agent_key] = self._routing_penalty(sr, ms)

        # ── 工具指标 ──────────────────────────────────────────────────────────
        # 遍历每个工具的统计
        for tool_name, s in tool_stats.items():
            sr = s["success_rate"]  # 成功率
            ms = s["avg_latency_ms"]  # 平均耗时（毫秒）
            cf = s["consecutive_fails"]  # 连续失败次数

            # 阈值告警
            self._check_threshold("tool_success_rate", sr, tool_name)  # 成功率阈值告警
            self._check_threshold("tool_avg_ms", ms, tool_name)  # 延迟阈值告警

            # 启用 Prometheus 时更新工具成功率
            if "tool_success_rate" in self._prom:
                self._prom["tool_success_rate"].labels(tool=tool_name).set(sr)  # 按 tool 分标签

            # 连续失败 → 生成具体建议
            if cf >= 3:
                # 连续失败超过 3 次生成排查建议
                self._add_suggestion(Suggestion(
                    title=f"工具 {tool_name} 连续失败 {cf} 次",  # 建议标题
                    detail=f"成功率 {sr:.1%}，平均延迟 {ms:.0f}ms，熔断状态: {s['circuit_state']}",  # 依据数据
                    action="1. 检查工具依赖服务是否正常\n2. 查看错误日志\n3. 考虑增加超时时间或降级策略",  # 操作步骤
                    priority=9,  # 高优先级
                ))

        # ── 路由优化建议 ──────────────────────────────────────────────────────
        # 尝试获取编排器的路由降权写入接口（可能不存在）
        updater = getattr(self._orchestrator, "update_routing_penalties", None)
        # 接口存在时把降权系数写回编排器
        if updater:
            updater(routing_penalties)
        # 生成路由优化建议（反馈闭环体现）
        self._generate_routing_suggestions(agent_stats)

    @staticmethod
    def _routing_penalty(success_rate: float, avg_ms: float) -> float:
        """
        把在线表现转成 0-0.9 的路由降权系数。

        成功率越低、延迟越高，降权系数越大（但封顶 0.9），
        Orchestrator 在路由时会用它降低该 Agent 的选中概率。

        参数：
            success_rate: Agent 成功率
            avg_ms: Agent 平均耗时（毫秒）

        返回：
            0.0-0.9 的降权系数（0 表示不降权）
        """
        # 初始化降权系数
        penalty = 0.0
        # 成功率低于 90% 时按缺口加大降权（最多 0.5）
        if success_rate < 0.90:
            penalty += min(0.5, (0.90 - success_rate) * 2)
        # 平均耗时超过 3000ms 时按超出量加大降权（最多 0.4）
        if avg_ms > 3000:
            penalty += min(0.4, (avg_ms - 3000) / 10000)
        # 总降权封顶 0.9
        return min(penalty, 0.9)

    def _check_threshold(self, metric: str, value: float, label: str) -> None:
        """
        按阈值表检查单个指标，超限则生成告警。

        参数：
            metric: 指标名（须在 THRESHOLDS 中配置）
            value: 当前指标值
            label: 指标所属对象标签（Agent 名或工具名）
        """
        # 未配置该指标的阈值直接跳过
        if metric not in self.THRESHOLDS:
            return
        # 解包阈值配置
        threshold, severity, operator = self.THRESHOLDS[metric]
        # 按比较符判断是否触发
        triggered = (operator == "less_than" and value < threshold) or \
                    (operator == "greater_than" and value > threshold)
        # 触发时生成告警
        if triggered:
            # 构造告警记录
            alert = Alert(
                severity=severity,  # 严重程度
                metric=f"{metric}:{label}",  # 指标名 + 标签
                message=f"{label} 的 {metric} = {value:.3f}，阈值 {threshold}",  # 告警信息
                value=value,  # 当前值
                threshold=threshold,  # 阈值
            )
            # 记录告警
            self._alerts.append(alert)
            # 输出警告日志
            logger.warning(f"[{severity.value.upper()}] {alert.message}")
            # 异步发送 Webhook（不阻塞采集循环）
            if self._webhook:
                asyncio.create_task(self._send_webhook(alert))

    def _generate_routing_suggestions(self, agent_stats: Dict[str, Any]) -> None:
        """
        基于 Agent 在线表现生成路由优化建议。
        这是 Monitor → Orchestrator 反馈闭环的体现。

        参数：
            agent_stats: 各 Agent 的统计字典
        """
        # 遍历每个 Agent 的统计
        for agent_key, s in agent_stats.items():
            # 成功率低于 85% 且样本量足够时给建议
            if s["success_rate"] < 0.85 and s["total"] > 10:
                # 生成可操作的优化建议
                self._add_suggestion(Suggestion(
                    title=f"Agent {agent_key} 成功率偏低",  # 建议标题
                    detail=f"成功率 {s['success_rate']:.1%}，路由评分 {s['routing_score']:.3f}",  # 依据数据
                    action=(
                        "Orchestrator 的 _best_agent() 已自动降低该 Agent 的路由权重。\n"  # 已生效动作
                        "建议：1. 检查 system_prompt 是否需要优化\n"  # 建议 1
                        "      2. 检查该类型问题的复杂度是否超出 Agent 能力\n"  # 建议 2
                        "      3. 考虑增加同类型 Agent 实例"  # 建议 3
                    ),
                    priority=8,  # 高优先级
                ))

    def _add_suggestion(self, s: Suggestion) -> None:
        """
        添加一条优化建议（按标题去重）。

        参数：
            s: 待添加的建议
        """
        # 去重：相同 title 不重复添加
        if not any(x.title == s.title for x in self._suggestions):
            # 追加到建议列表
            self._suggestions.append(s)
            # 记录新建议日志
            logger.info(f"优化建议 [P{s.priority}]: {s.title}")

    async def _send_webhook(self, alert: Alert) -> None:
        """
        把告警以 JSON 形式发送到 Webhook（异步，不阻塞采集循环）。

        参数：
            alert: 待推送的告警记录
        """
        try:
            # 发送 HTTP POST（5 秒超时）
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(self._webhook, json=asdict(alert))  # type: ignore
        except Exception as ex:
            # 发送失败仅记日志
            logger.error(f"Webhook 发送失败: {ex}")

    # ── 查询接口 ──────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """返回当前监控摘要，供 API 层暴露。"""
        # 组装监控摘要（最新 10 条未解决告警 + 优先级前 5 建议）
        return {
            "agent_stats":   self._orchestrator.get_stats(),  # Agent 实时统计
            "tool_stats":    self._tool_manager.get_stats(),  # 工具实时统计
            "active_alerts": [asdict(a) for a in self._alerts if not a.resolved][-10:],  # 未解决告警最近 10 条
            "suggestions":   [  # 按优先级降序取前 5 条建议
                {"title": s.title, "action": s.action, "priority": s.priority}  # 建议摘要
                for s in sorted(self._suggestions, key=lambda x: -x.priority)[:5]
            ],
        }
