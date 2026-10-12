"""
亮点：MCP 工具调用框架

核心问题：工具调用出错（检索不全、召回不好）怎么优化？

本模块的答案：
  1. 查询改写（Query Rewriting）—— 用 LLM 把用户原始问题扩写成多个角度的子查询，
     再合并去重，解决"召回不全"问题。
  2. 结果重排（Reranking）—— 对召回结果用 LLM 打分，按相关性重新排序，
     解决"召回不好/排序差"问题。
  3. 熔断器（Circuit Breaker）—— 连续失败超阈值时自动断开，防止雪崩。
  4. 结果缓存（TTL Cache）—— 相同参数直接返回缓存，减少重复调用。
  5. 降级策略（Fallback）—— 工具不可用时返回有意义的降级结果。

工具调用流程:
1. 参数校验 (JSON Schema)
2. 缓存检查 (Redis, TTL 60-300s)
3. 熔断检测 (连续失败5次→开路)
4. 超时控制 (默认10s)
5. 执行工具
6. Fallback降级 (失败时返回兜底结果)
7. 记录统计 (成功率/延迟)
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text

logger = logging.getLogger(__name__)


def _extract_json_array(text: str) -> Optional[List[Any]]:
    """
    从 LLM 文本响应里稳健地提取 JSON 数组。

    兼容：
      - 纯 JSON 数组
      - Markdown 代码块包裹（```json [...] ```）
      - 前后有解释文字、或 JSON 对象包裹（如 {"queries": [...]}）
    提取失败返回 None，由调用方走文本兜底解析。

    参数：
        text: LLM 返回的原始文本

    返回：
        提取到的 JSON 数组；提取失败时返回 None
    """
    # 空文本直接返回 None
    if not text:
        return None
    # 去掉 Markdown 代码块标记
    text = re.sub(r"```(?:json)?\s*(.*?)```", r"\1", text, flags=re.S)
    # 去掉首尾空白
    stripped = text.strip()
    # 1) 整体就是 JSON 数组
    try:
        # 尝试整体解析为 JSON
        data = json.loads(stripped)
        # 是列表则直接返回
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        # 整体解析失败时继续尝试方案 2
        pass
    # 2) 取第一对 [ ]（跨行），兼容"文本 + JSON 数组"或 JSON 对象包裹
    s, e = text.find("["), text.rfind("]")  # 定位第一对方括号
    if s != -1 and e > s:
        # 尝试解析方括号内的部分
        try:
            data = json.loads(text[s : e + 1])
            # 是列表则返回
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            # 解析失败时跳过
            pass
    # 全部方案失败，返回 None
    return None


def _parse_text_list(text: str) -> List[str]:
    """
    把非 JSON 的文本列表解析成字符串列表（编号/项目符号/逗号分隔）。

    兼容格式：
      - "1. 子查询A\n2. 子查询B"
      - "- 子查询A\n- 子查询B"
      - "子查询A, 子查询B, 子查询C"
    无法识别为列表项时返回空列表。

    参数：
        text: 待解析的原始文本

    返回：
        解析出的字符串列表；没有可识别列表项时返回空列表
    """
    # 初始化列表项容器
    items: List[str] = []
    # 逐行解析文本
    for line in (text or "").splitlines():
        line = line.strip()  # 去除首尾空白
        # 空行跳过
        if not line:
            continue
        # 匹配编号列表项（如 "1."、"1、"、"1)"）
        m = re.match(r"^\d+[.、)）]\s*(.+)$", line)
        # 是编号项时去掉编号前缀
        if m:
            line = m.group(1).strip()
        else:
            # 匹配项目符号前缀（-、*、•）
            bullet = line.lstrip("-*•").strip()
            # 没有项目符号的普通行不作为列表项
            if bullet == line:
                # 无编号/项目符号的普通行不作为列表项，交给分隔符兜底
                continue
            # 去掉项目符号后的剩余内容
            line = bullet
        # 去掉两端引号
        line = line.strip().strip('"\'"')
        # 非空且未重复时才加入列表
        if line and line not in items:
            items.append(line)
    # 编号/项目符号解析无结果时，尝试逗号分隔符拆分
    if not items and re.search(r"[,，、;；]", text or ""):
        # 按中英文逗号、顿号、分号切分
        for part in re.split(r"[,，、;；]", text or ""):
            # 去掉首尾空白和引号
            part = part.strip().strip('"\'"')
            # 非空且未重复时才加入列表
            if part and part not in items:
                items.append(part)
    # 返回解析结果
    return items


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    """熔断器三态枚举：描述熔断器当前处于的打开/关闭/探测状态。"""
    CLOSED    = "closed"     # 正常：允许请求通过
    OPEN      = "open"       # 熔断：拒绝请求
    HALF_OPEN = "half_open"  # 探测：放行少量请求测试恢复


@dataclass
class ToolResult:
    """
    工具调用结果的统一封装。

    无论工具成功与否，都以 ToolResult 返回给调用方，
    携带成功标记、数据、错误信息、缓存/重排标识和耗时。
    """
    success:        bool  # 是否调用成功
    data:           Any  # 返回的数据（失败时为 None）
    tool_name:      str  # 工具名称
    error:          Optional[str] = None  # 错误信息（成功时为 None）
    cached:         bool = False  # 是否命中缓存
    latency_ms:     float = 0.0  # 调用耗时（毫秒）
    reranked:       bool = False   # 是否经过重排


@dataclass
class ToolStats:
    """工具运行时统计，供 Monitor 读取。"""
    total:              int = 0  # 总调用次数
    success:            int = 0  # 成功次数
    failed:             int = 0  # 失败次数
    total_latency_ms:   float = 0.0  # 累计耗时（毫秒）
    consecutive_fails:  int = 0  # 连续失败次数（熔断器用）

    @property
    def success_rate(self) -> float:
        """计算成功率（无调用时按 1.0 计）。"""
        # 总调用为 0 时返回 1.0（视为健康）
        return self.success / self.total if self.total else 1.0

    @property
    def avg_latency_ms(self) -> float:
        """计算平均耗时（毫秒，无调用时为 0）。"""
        # 总调用为 0 时返回 0
        return self.total_latency_ms / self.total if self.total else 0.0


# ── 熔断器 ────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    三态熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED

    连续失败 failure_threshold 次后打开（OPEN）；
    打开 recovery_s 秒后进入 HALF_OPEN 探测；
    探测请求成功则关闭（CLOSED），失败则重新打开（OPEN）。

    作用：在下游工具持续故障时主动断开调用，防止请求雪崩拖垮服务。
    """

    def __init__(self, failure_threshold: int = 5, recovery_s: float = 60.0):
        # 保存连续失败阈值
        self.threshold   = failure_threshold
        # 保存熔断恢复时间（秒）
        self.recovery_s  = recovery_s
        # 初始为关闭状态
        self.state       = CircuitState.CLOSED
        # 累计连续失败次数
        self.fail_count  = 0
        # 熔断打开的时间点（供恢复判断使用）
        self.opened_at:  Optional[float] = None

    def allow(self) -> bool:
        """
        判断当前是否允许放行请求。

        返回：
            True 允许调用；False 拒绝调用（熔断中未到恢复期）
        """
        # 关闭状态直接放行
        if self.state == CircuitState.CLOSED:
            return True
        # 打开状态需要判断恢复时间
        if self.state == CircuitState.OPEN:
            # 已超过恢复期则转入半开探测并放行
            if time.monotonic() - self.opened_at >= self.recovery_s:  # type: ignore
                self.state = CircuitState.HALF_OPEN  # 转入半开状态
                return True  # 放行一次探测请求
            # 未到恢复期则拒绝
            return False
        # HALF_OPEN：放行一次探测
        return True

    def record_success(self) -> None:
        """记录一次成功：清零失败计数并恢复关闭状态。"""
        self.fail_count = 0  # 清零连续失败计数
        self.state = CircuitState.CLOSED  # 恢复为关闭状态

    def record_failure(self) -> None:
        """记录一次失败：累计失败计数，达到阈值则打开熔断器。"""
        self.fail_count += 1  # 连续失败计数加一
        # 达到阈值时打开熔断器
        if self.fail_count >= self.threshold:
            self.state     = CircuitState.OPEN  # 切换为打开状态
            self.opened_at = time.monotonic()  # 记录打开时间点
            # 记录熔断器打开的警告日志
            logger.warning(f"熔断器打开（连续失败 {self.fail_count} 次）")


# ── 工具定义 ──────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    """
    单个工具的定义，描述工具的名称、处理函数、参数 schema 及运行时属性。

    通过 register 注册到 MCPToolManager 后即可被调用。
    运行时统计（stats）和熔断器（breaker）在构造时自动创建，不参与构造参数。
    """
    name:        str  # 工具唯一名称
    description: str  # 工具功能描述（供 Agent 选择使用）
    handler:     Callable                    # async (params, context) -> Any：工具执行函数
    schema:      Dict[str, Any]              # JSON Schema：参数结构定义（用于校验）
    cache_ttl:   float = 0.0                 # 缓存生命周期（秒），0 = 不缓存
    timeout_s:   float = 30.0  # 执行超时时间（秒）
    supports_rerank: bool = False            # 是否支持结果重排
    use_rewrite: bool = False                # 是否走"查询改写→并行召回→重排"完整链路
    fallback:    Optional[Callable] = None    # sync/async (params, context, error) -> Any：降级处理函数

    # 运行时状态（不参与构造）
    stats:   ToolStats    = field(default_factory=ToolStats, init=False)  # 运行时统计指标
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker, init=False)  # 独立熔断器


# ── MCP 工具管理器 ────────────────────────────────────────────────────────────

class MCPToolManager:
    """
    MCP 工具调用框架。

    统一管理工具的注册、调用、缓存、熔断、参数校验和统计。
    核心优化链路（针对检索类工具）：
      用户查询 → 查询改写（多角度子查询）→ 并行召回 → 结果重排 → 返回 Top-K
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "claude-3-5-sonnet-20241022"):
        """
        初始化工具管理器。

        参数：
            api_key: Anthropic API 密钥
            base_url: 自定义 API 地址（可选，默认官方地址）
            model: 使用的模型名称（默认 claude-3-5-sonnet-20241022）
        """
        # 构造 Anthropic 客户端参数
        kwargs: Dict[str, Any] = {"api_key": api_key}
        # 提供了 base_url 时加入连接参数
        if base_url:
            kwargs["base_url"] = base_url
        # 创建异步 Anthropic 客户端
        self._client = AsyncAnthropic(**kwargs)
        # 保存模型名称
        self._model  = model
        # 工具注册表：name → Tool
        self._tools: Dict[str, Tool] = {}
        # 结果缓存：key → (result, expire_at)
        self._cache: Dict[str, tuple] = {}   # key → (result, expire_at)

    # ── 注册 / 注销 ───────────────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        """
        注册工具到管理器。

        参数：
            tool: 要注册的 Tool 实例
        """
        # 将工具按名称存入注册表
        self._tools[tool.name] = tool
        # 记录注册日志
        logger.info(f"注册工具: {tool.name}")

    def unregister(self, name: str) -> None:
        """
        注销工具（不存在时静默忽略）。

        参数：
            name: 要注销的工具名称
        """
        # 从注册表移除指定工具；不存在时 pop 返回默认值
        self._tools.pop(name, None)

    # ── 核心调用 ──────────────────────────────────────────────────────────────

    async def call(
        self,
        name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        *,
        use_cache: bool = True,
        rerank_top_k: int = 0,          # >0 时对结果重排，取 Top-K
        _skip_rewrite: bool = False,     # 内部标记：避免 rewrite 链路递归调用
    ) -> ToolResult:
        """
        调用工具，完整执行链：
          缓存检查 → 熔断检查 → 参数校验 → 执行（含超时）→ 缓存写入 → 可选重排

        检索类工具（use_rewrite=True）会先路由到 search_with_rewrite：
          查询改写 → 并行召回 → 去重 → LLM 重排 → Top-K

        参数：
            name: 工具名称
            params: 传给工具的参数
            context: 调用上下文（可选，透传给 handler）
            use_cache: 是否启用缓存（默认开启）
            rerank_top_k: >0 时对列表结果重排取 Top-K（默认 0 不重排）
            _skip_rewrite: 内部标记，避免重写链路中的子调用再次触发重写

        返回：
            统一封装的 ToolResult（成功或失败均返回）
        """
        # 从注册表中取工具
        tool = self._tools.get(name)
        # 工具不存在时直接返回失败结果
        if not tool:
            return ToolResult(success=False, data=None, tool_name=name, error=f"工具不存在: {name}")

        # 检索类工具走完整优化链路（Agent 工具路径同样生效）
        if tool.use_rewrite and not _skip_rewrite and params.get("query"):
            # 路由到查询改写优化链路
            return await self.search_with_rewrite(
                name,  # 工具名称
                params.get("query"),  # 用户查询
                top_k=params.get("top_k", 5),  # 返回条数（默认 5）
                context=context,  # 调用上下文
                extra_params=params,  # 附带其它参数透传
            )

        # 缓存命中
        # 启用缓存且工具设置了 TTL 时才查询缓存
        if use_cache and tool.cache_ttl > 0:
            # 按工具和参数生成缓存 key 查找
            cached = self._get_cache(name, params)
            # 缓存命中时直接返回
            if cached is not None:
                tool.stats.total += 1  # 总调用次数加一
                tool.stats.success += 1  # 成功次数加一
                # 返回缓存命中结果
                return ToolResult(success=True, data=cached, tool_name=name, cached=True)

        # 熔断检查
        # 熔断器不允许放行时
        if not tool.breaker.allow():
            error = f"工具熔断中: {name}，请稍后重试"  # 构造熔断提示
            # 返回降级结果
            return await self._fallback_result(tool, params, context, error)

        # 记录执行起始时间
        t0 = time.monotonic()
        # 总调用次数加一
        tool.stats.total += 1
        try:
            # 参数校验（根据 JSON Schema 的 required 和 properties.type）
            self._validate_params(tool, params)

            # 执行工具（带超时控制）
            data = await asyncio.wait_for(tool.handler(params, context), timeout=tool.timeout_s)
            # 计算执行耗时（毫秒）
            latency = (time.monotonic() - t0) * 1000

            # 记录成功统计
            tool.stats.success += 1  # 成功次数加一
            tool.stats.consecutive_fails = 0  # 清零连续失败
            tool.stats.total_latency_ms += latency  # 累计耗时
            # 记录熔断器成功
            tool.breaker.record_success()

            # 写缓存
            # 工具设置了缓存 TTL 时写入缓存
            if tool.cache_ttl > 0:
                self._set_cache(name, params, data, tool.cache_ttl)  # 写入缓存

            # 重排（针对返回列表的检索工具）
            reranked = False  # 重排标记
            # 需要重排、工具支持重排且结果是列表时执行
            if rerank_top_k > 0 and tool.supports_rerank and isinstance(data, list):
                query = params.get("query", "")  # 取用户查询用于打分
                data, reranked = await self._rerank(query, data, rerank_top_k), True  # 重排并标记

            # 返回成功结果（带耗时和重排标记）
            return ToolResult(success=True, data=data, tool_name=name,
                              latency_ms=latency, reranked=reranked)

        except asyncio.TimeoutError:
            # 超时：记录失败统计
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            # 记录熔断器失败
            tool.breaker.record_failure()
            # 记录超时错误日志
            logger.error(f"工具超时: {name} ({tool.timeout_s}s)")
            # 返回降级结果
            return await self._fallback_result(tool, params, context, "执行超时")

        except Exception as ex:
            # 异常：记录失败统计
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            # 记录熔断器失败
            tool.breaker.record_failure()
            # 记录异常日志
            logger.error(f"工具异常: {name} — {ex}")
            # 返回降级结果
            return await self._fallback_result(tool, params, context, str(ex))

    async def _fallback_result(
        self,
        tool: Tool,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
        error: str,
    ) -> ToolResult:
        """
        工具不可用时返回降级结果，而不是把空错误直接暴露给调用方。

        参数：
            tool: 失败的工具
            params: 原始调用参数
            context: 调用上下文
            error: 失败原因

        返回：
            降级后的 ToolResult
        """
        # 没有配置 fallback 时直接返回失败结果
        if tool.fallback is None:
            return ToolResult(success=False, data=None, tool_name=tool.name, error=error)
        try:
            # 调用降级函数
            data = tool.fallback(params, context, error)
            # 降级函数是协程时等待其完成
            if asyncio.iscoroutine(data):
                data = await data
            # 返回降级成功结果（保留原始错误信息）
            return ToolResult(
                success=True,  # 降级视为成功
                data=data,  # 降级数据
                tool_name=tool.name,  # 工具名称
                error=error,  # 保留原始错误
            )
        except Exception as ex:
            # 降级本身失败时记录日志
            logger.error(f"工具降级失败: {tool.name} — {ex}")
            # 返回复合失败信息
            return ToolResult(success=False, data=None, tool_name=tool.name, error=f"{error}; fallback失败: {ex}")

    # ── 查询改写（解决召回不全）────────────────────────────────────────────────

    async def rewrite_query(self, query: str, n: int = 3) -> List[str]:
        """
        用 LLM 将原始查询改写为 n 个不同角度的子查询。

        目的：单一查询往往只能召回某一角度的文档，
        多角度子查询并行检索后合并，显著提升召回率。

        示例：
          原始: "退款流程"
          改写: ["如何申请退款", "退款需要多少天", "退款政策是什么"]

        参数：
            query: 用户原始查询
            n: 生成的子查询数量（默认 3）

        返回：
            子查询列表（原始查询保留并去重；失败时返回仅含原始查询的列表）
        """
        # 构造查询改写 prompt
        prompt = f"""将以下用户查询改写为 {n} 个不同角度的搜索子查询，用于检索知识库。
要求：每个子查询角度不同，覆盖原始问题的不同方面。
原始查询: "{query}"
返回 JSON 数组，例如: ["子查询1", "子查询2", "子查询3"]
不要输出 Markdown 代码块，不要任何解释，只输出 JSON 数组。"""
        # 清洗整个 prompt
        prompt = self._clean_text(prompt)
        try:
            # 调用 LLM 生成子查询
            resp = await self._client.messages.create(
                model=self._model, max_tokens=256, temperature=0.3,  # 模型与参数
                messages=[{"role": "user", "content": prompt}],  # 用户消息
            )
            # 提取返回文本
            raw = extract_text(resp)
            # 尝试按 JSON 数组解析
            queries = _extract_json_array(raw)
            # JSON 解析失败
            if queries is None:
                # 模型没按 JSON 返回时，用文本兜底解析（编号/项目符号/逗号分隔）
                queries = _parse_text_list(raw)
            # 过滤空字符串并转成纯字符串列表
            queries = [str(q).strip() for q in queries if str(q).strip()]
            # 无有效子查询时降级为原始查询
            if not queries:
                # 记录警告日志
                logger.warning(f"查询改写响应无有效内容: {raw[:200]!r}，使用原始查询")
                return [query]
            # 原始查询也保留，去重（dict.fromkeys 保序去重）
            return list(dict.fromkeys([query] + queries))
        except Exception as ex:
            # 改写失败时返回原始查询兜底
            logger.warning(f"查询改写失败，使用原始查询: {ex}")
            return [query]

    async def search_with_rewrite(
        self,
        tool_name: str,
        query: str,
        top_k: int = 5,
        context: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """
        完整的检索优化链路：查询改写 → 并行召回 → 去重 → 重排 → Top-K

        这是解决"检索不全、召回不好"的完整方案。
        extra_params 中的其它参数（如 category/product 过滤）会透传给每个子查询。

        参数：
            tool_name: 检索工具名称
            query: 用户查询
            top_k: 最终返回条数（默认 5）
            context: 调用上下文
            extra_params: 其它要透传给每个子查询的参数

        返回：
            重排后的检索结果 ToolResult
        """
        # 1. 查询改写：生成多角度子查询
        sub_queries = await self.rewrite_query(query, n=3)
        # 记录改写结果日志
        logger.info(f"查询改写: {query!r} → {sub_queries}")

        # 2. 并行召回：所有子查询同时检索
        recall_k = max(top_k, 5)  # 召回条数不小于 5（多召回供重排挑选）
        # 复制基础参数（去掉 query 和 top_k 由各子查询覆盖）
        base_params = dict(extra_params or {})
        base_params.pop("query", None)  # 移除 query（逐个子查询覆盖）
        base_params.pop("top_k", None)  # 移除 top_k（调用时统一指定）
        # 为每个子查询构造并行调用任务
        tasks = [
            self.call(
                tool_name,  # 工具名称
                {**base_params, "query": q, "top_k": recall_k},  # 合并参数后调用
                context,  # 调用上下文
                use_cache=True,  # 子查询复用缓存
                _skip_rewrite=True,  # 禁止再次触发重写（避免递归）
            )
            for q in sub_queries  # 遍历所有子查询
        ]
        # 并行执行所有子查询召回
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 合并去重（按内容哈希去重）
        seen, merged = set(), []  # 哈希去重集合和合并结果列表
        for r in results:
            # 判断子查询是否成功
            if isinstance(r, ToolResult) and r.success and isinstance(r.data, list):
                for item in r.data:
                    # 生成md5唯一标识，若未出现则放入seen和merged，若出现了则不加入
                    key = hashlib.md5(str(item).encode()).hexdigest()  # 内容哈希
                    if key not in seen:  # 未去重过才加入
                        seen.add(key)  # 记录到已见集合
                        merged.append(item)  # 加入合并结果

        # 所有子查询均无结果时返回失败
        if not merged:
            return ToolResult(success=False, data=[], tool_name=tool_name, error="所有子查询均无结果")

        # 4. 重排：用 LLM 对合并结果按相关性打分，取 Top-K
        reranked = await self._rerank(query, merged, top_k)
        # 返回重排后的成功结果
        return ToolResult(success=True, data=reranked, tool_name=tool_name, reranked=True)

    # ── 结果重排（解决召回不好）──────────────────────────────────────────────

    async def _rerank(self, query: str, items: List[Any], top_k: int) -> List[Any]:
        """
        用 LLM 对召回结果重新打分排序。

        解决问题：向量检索的相似度分数不等于"对用户有用"，
        LLM 能理解语义相关性，重排后 Top-K 质量显著提升。

        参数：
            query: 用户查询（打分依据）
            items: 待重排的召回结果列表
            top_k: 要返回的结果条数

        返回：
            重排后的 Top-K 结果
        """
        # 结果不足 top_k 时无需重排
        if len(items) <= top_k:
            return items

        # 将结果序列化为文本供 LLM 评分
        items_text = "\n".join(f"{i}. {json.dumps(item, ensure_ascii=False)[:200]}"
                               for i, item in enumerate(items))
        # 构造重排打分 prompt
        prompt = f"""根据用户查询，对以下检索结果按相关性打分（0-10），返回 JSON 数组。
用户查询: "{query}"
检索结果:
{items_text}

返回格式（按相关性降序排列的索引列表）: [最相关的索引, ..., 最不相关的索引]
必须包含全部 {len(items)} 个索引（0 到 {len(items)-1}），不要遗漏；
只返回 JSON 数组，不要其他文字；不要输出 Markdown 代码块，不要解释。"""
        # 清洗整个 prompt
        prompt = self._clean_text(prompt)

        try:
            # 调用 LLM 打分
            resp = await self._client.messages.create(
                model=self._model, max_tokens=256, temperature=0.0,  # 模型参数（0 温度保证稳定）
                messages=[{"role": "user", "content": prompt}],  # 用户消息
            )
            # 提取返回文本
            raw = extract_text(resp)
            # 尝试解析索引数组
            order = _extract_json_array(raw)
            # 解析失败时文本兜底
            if order is None:
                # 文本兜底：把回复里出现的数字当作索引（如 "排序结果：2, 0, 1"）
                order = [int(x) for x in re.findall(r"\d+", raw or "")]
            # 依据索引顺序重组结果
            ranked: List[Any] = []
            # 记录已使用的索引（去重）
            seen_idx: set[int] = set()
            # 遍历模型给出的索引顺序
            for i in order:
                try:
                    idx = int(i)  # 转成整数索引
                except (TypeError, ValueError):
                    continue  # 无法转换时跳过
                # 索引合法且未使用过才加入
                if 0 <= idx < len(items) and idx not in seen_idx:
                    seen_idx.add(idx)  # 标记已使用
                    ranked.append(items[idx])  # 按序加入
            # 模型没排到的候选按原顺序补在末尾，保证结果数不少于 min(top_k, len(items))
            # 避免重排成功反而把召回结果截短、丢掉本已召回的文档
            for idx, item in enumerate(items):
                # 未出现在排序里的候选补到末尾
                if idx not in seen_idx:
                    ranked.append(item)
            # 完全无法解析时回退原始顺序
            if not ranked:
                # 记录警告日志
                logger.warning(f"重排响应无法解析: {raw[:200]!r}，返回原始顺序")
                return items[:top_k]
            # 返回 Top-K 结果
            return ranked[:top_k]
        except Exception as ex:
            # 重排失败时回退原始顺序
            logger.warning(f"重排失败，返回原始顺序: {ex}")
            return items[:top_k]

    # ── 缓存 ──────────────────────────────────────────────────────────────────

    def _cache_key(self, name: str, params: Dict) -> str:
        """
        生成缓存 key：工具名 + 排序参数 JSON 的 MD5。

        参数排序（sort_keys）保证相同语义的参数生成相同 key。

        参数：
            name: 工具名称
            params: 调用参数

        返回：
            缓存 key 字符串
        """
        # 工具名 + 参数哈希拼成 key
        return f"{name}:{hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()}"

    def _get_cache(self, name: str, params: Dict) -> Optional[Any]:
        """
        读取缓存；未命中或已过期返回 None。

        参数：
            name: 工具名称
            params: 调用参数

        返回：
            缓存的调用结果；未命中返回 None
        """
        # 生成缓存 key
        key = self._cache_key(name, params)
        # key 命中时
        if key in self._cache:
            data, expire_at = self._cache[key]  # 取出数据和过期时间
            # 未过期则直接返回
            if time.monotonic() < expire_at:
                return data
            # 已过期则删除条目
            del self._cache[key]
        # 未命中返回 None
        return None

    def _set_cache(self, name: str, params: Dict, data: Any, ttl: float) -> None:
        """
        写入缓存；缓存超过 5000 条时清掉最旧的 1/4。

        参数：
            name: 工具名称
            params: 调用参数
            data: 要缓存的结果数据
            ttl: 缓存生命周期（秒）
        """
        # 缓存规模超限时清理
        if len(self._cache) >= 5000:
            # 清掉最旧的 1/4
            for k in list(self._cache)[:1250]:
                del self._cache[k]
        # 写入缓存（值 = 数据 + 当前时间 + TTL）
        self._cache[self._cache_key(name, params)] = (data, time.monotonic() + ttl)

    # ── 参数校验 ──────────────────────────────────────────────────────────────

    _TYPE_MAP = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}

    def _validate_params(self, tool: Tool, params: Dict[str, Any]) -> None:
        """
        根据工具的 JSON Schema 校验参数，不合法时抛出 ValueError。

        检查必填项是否齐全，以及已提供参数的类型是否符合 schema 声明。

        参数：
            tool: 工具实例（内含 schema）
            params: 调用参数

        异常：
            ValueError: 缺少必填参数或参数类型不符时抛出
        """
        # 取出参数 schema
        schema = tool.schema
        required = schema.get("required", [])  # 必填参数列表
        properties = schema.get("properties", {})  # 参数属性定义

        # 检查必填参数是否齐全
        for field in required:
            # 必填参数缺失时抛错
            if field not in params:
                raise ValueError(f"工具 {tool.name} 缺少必需参数: {field}")

        # 逐个校验已提供参数的类型
        for key, value in params.items():
            # 仅校验 schema 中声明过的参数
            if key in properties:
                expected_type = properties[key].get("type")  # 期望类型名
                # 类型名在映射表中才校验
                if expected_type and expected_type in self._TYPE_MAP:
                    # 实际类型不匹配时抛错
                    if not isinstance(value, self._TYPE_MAP[expected_type]):
                        raise ValueError(
                            f"工具 {tool.name} 参数 {key} 类型错误: 期望 {expected_type}，实际 {type(value).__name__}"
                        )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """
        移除 Unicode 代理字符，避免 LLM 请求编码失败。

        先把任意值转成字符串，再用 utf-8 + errors="ignore" 剔除
        无法编码的代理字符。

        参数：
            value: 任意值（可为 None）

        返回：
            清洗后的安全字符串
        """
        # None 返回空字符串
        if value is None:
            return ""
        # 非字符串先转字符串
        if not isinstance(value, str):
            value = str(value)  # 转为字符串
        # 忽略代理字符重新编码解码
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """
        返回全部工具的运行统计，供监控面板读取。

        返回：
            {工具名: {总调用、成功率、平均耗时、连续失败次数、熔断状态}}
        """
        # 遍历工具注册表生成统计字典
        return {
            name: {
                "total": t.stats.total,  # 总调用次数
                "success_rate": round(t.stats.success_rate, 3),  # 成功率（3 位小数）
                "avg_latency_ms": round(t.stats.avg_latency_ms, 1),  # 平均耗时（毫秒）
                "consecutive_fails": t.stats.consecutive_fails,  # 连续失败次数
                "circuit_state": t.breaker.state.value,  # 熔断器当前状态
            }
            for name, t in self._tools.items()
        }
