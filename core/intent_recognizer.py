"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    """意图分类枚举。

    定义了客服场景下用户消息的所有意图类别，
    用于三路识别（LLM/Embedding/Pattern）的最终输出和结果合并。
    """

    QUERY      = "query"       # 查询信息（如订单状态、如何使用等）
    COMPLAINT  = "complaint"   # 投诉不满（表达负面情绪）
    REQUEST    = "request"     # 请求操作（取消订单、改地址等）
    GREETING   = "greeting"    # 问候（打招呼、开场白）
    ESCALATION = "escalation"  # 要求升级/转人工
    TECHNICAL  = "technical"   # 技术问题（崩溃、报错、无法登录等）
    BILLING    = "billing"     # 账单/退款（扣款、退款、发票等）
    ACCOUNT    = "account"     # 账户管理（改邮箱、注销、改资料等）
    FEEDBACK   = "feedback"    # 正面反馈（好评、满意等）
    OTHER      = "other"       # 其他（无法归类的意图）

class UrgencyLevel(Enum):
    """紧急程度枚举。

    表示用户消息的紧急程度分级，用于后续客服处理优先级的判断。
    """

    LOW      = 1  # 低紧急（普通咨询）
    MEDIUM   = 2  # 中紧急（需要尽快处理）
    HIGH     = 3  # 高紧急（升级投诉等）
    CRITICAL = 4  # 紧急（立即需要处理）

@dataclass
class IntentResult:
    """意图识别结果数据类。

    封装一次意图识别的完整输出，包括识别出的意图、置信度、
    紧急程度、提取的实体、推理说明和耗时。
    """

    intent:     IntentCategory  # 识别出的意图类别
    confidence: float  # 置信度（0-1，越小越不确信）
    urgency:    UrgencyLevel  # 紧急程度（LOW/MEDIUM/HIGH/CRITICAL）
    entities:   Dict[str, List[str]]   # 从消息中提取的实体（订单号、产品等）
    reasoning:  str  # 识别依据说明（一句话）
    latency_ms: float  # 识别耗时（毫秒）


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
# 每个意图类别对应的示例消息列表，用于 LLM 的 few-shot 示例和 Embedding 向量匹配
_TEMPLATES: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["我的订单状态是什么？", "如何重置密码？", "快递什么时候到？"],  # 查询类示例
    IntentCategory.COMPLAINT:  ["等了好几个小时！", "服务太差了！", "一直没人处理！"],  # 投诉类示例
    IntentCategory.REQUEST:    ["帮我取消订单", "我需要修改地址", "请协助退款"],  # 请求操作类示例
    IntentCategory.GREETING:   ["你好", "嗨，有人吗", "早上好"],  # 问候类示例
    IntentCategory.ESCALATION: ["我要投诉！", "转人工客服", "找你们经理"],  # 升级/转人工类示例
    IntentCategory.TECHNICAL:  ["应用一直崩溃", "无法登录", "出现500错误"],  # 技术问题类示例
    IntentCategory.BILLING:    ["为什么扣了两次款？", "申请退款", "发票问题"],  # 账单/退款类示例
    IntentCategory.ACCOUNT:    ["修改邮箱", "注销账户", "更新个人信息"],  # 账户管理类示例
    IntentCategory.FEEDBACK:   ["服务很棒！", "非常满意", "给个好评"],  # 正面反馈类示例
}

# 紧急关键词：根据消息中包含的关键词判断紧急程度分级
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],  # 紧急级关键词
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],  # 高紧急关键词
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],  # 中紧急关键词
}


def _cosine(a: List[float], b: List[float]) -> float:
    """
    计算两个向量的余弦相似度。

    纯 Python 实现，不依赖 numpy。余弦相似度的取值范围为 -1 到 1，
    值越高表示两个向量方向越接近。

    参数：
        a: 第一个向量
        b: 第二个向量

    返回：
        余弦相似度（任一向量模长为 0 时返回 0.0）
    """
    # 计算向量点积（对应元素乘积之和）
    dot = sum(x * y for x, y in zip(a, b))
    # 计算向量 a 的模长
    na  = sum(x * x for x in a) ** 0.5
    # 计算向量 b 的模长
    nb  = sum(x * x for x in b) ** 0.5
    # 模长都非零时返回余弦相似度，否则返回 0        点积除以模长乘积
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    初始化时不加载任何本地模型，所有 AI 能力通过 Anthropic API 调用。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,  # Anthropic API 密钥
        base_url: Optional[str] = None,  # 可选的 API 基础地址（用于第三方兼容层，如 DeepSeek）
        model: str = "claude-3-5-sonnet-20241022",  # 使用的模型名称
        confidence_threshold: float = 0.5,  # 置信度阈值（低于该值的意图降级为 OTHER）
    ):
        """
        初始化意图识别器。

        参数：
            api_key: API 密钥
            base_url: 可选的基础地址（非 None 时视为第三方兼容层，禁用 Embedding）
            model: 模型名称
            confidence_threshold: 置信度阈值
        """
        # 构建客户端初始化参数
        kwargs: Dict[str, Any] = {"api_key": api_key}
        # 如果提供了 base_url 则加入客户端参数
        if base_url:
            kwargs["base_url"] = base_url
        # 创建 Anthropic 异步客户端
        self.client    = AsyncAnthropic(**kwargs)
        # 保存模型名称
        self.model     = model
        # 保存置信度阈值
        self.threshold = confidence_threshold
        # 第三方兼容 API（如 DeepSeek）通常不支持 Embedding，禁用该策略。
        # 官方 Anthropic SDK 当前没有 embeddings 资源，因此下面会使用稳定的
        # 本地字符 n-gram 向量作为轻量兜底，保证三路融合链路真实可跑。
        # 是否启用 Embedding 策略（base_url 非空视为第三方 API，禁用远端 Embedding）
        self._embedding_enabled = not bool(base_url)

        # 模板 Embedding 缓存（懒加载，键为意图类别，值为该类别所有模板的向量列表）
        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        # LRU 识别结果缓存（键为消息哈希键，值为识别结果）
        self._cache: Dict[str, IntentResult] = {}
        # 缓存命中计数
        self.cache_hits   = 0
        # 缓存未命中计数
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,  # 用户输入的消息
        history: Optional[List[Dict[str, str]]] = None,  # 对话历史（用于上下文理解）
    ) -> IntentResult:
        """
        识别用户意图。

        执行三路识别（LLM 语义理解、Embedding 相似度、关键词模式）并加权合并，
        同时提取实体、判断紧急程度。结果带 LRU 缓存。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]

        参数：
            message: 用户消息
            history: 对话历史（可选）

        返回：
            意图识别结果（IntentResult）
        """
        # 计算缓存键（清洗后的消息截取前 200 字符）
        key = self._cache_key(message)
        # 命中缓存则直接返回
        if key in self._cache:
            self.cache_hits += 1  # 命中计数 +1
            return self._cache[key]  # 返回缓存的识别结果
        self.cache_misses += 1  # 未命中计数 +1

        # 记录识别开始时间（用于计算耗时）
        t0 = time.monotonic()

        # LLM 和 Embedding 并行（Embedding 不可用时跳过）
        # 创建 LLM 识别异步任务
        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        # 创建 Embedding 识别异步任务（不可用时为 None）
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        # 同步执行关键词模式识别
        pat      = self._pattern_recognize(message)

        # 并行等待 LLM 和 Embedding 的结果
        if emb_task:
            # 同时等待两个异步任务完成
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            # Embedding 不可用时只等 LLM 结果
            llm = await llm_task
            # Embedding 结果置为 OTHER 且置信度为 0
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        # 三路加权投票合并最终意图
        intent = self._vote(llm, emb, pat)
        # 用 LLM 从消息中提取实体
        entities = await self._extract_entities(message)
        # 判断消息紧急程度
        urgency  = self._urgency(message, intent)

        # 组装识别结果
        result = IntentResult(
            intent=intent,  # 最终意图
            confidence=llm["confidence"],  # 置信度（以 LLM 为准）
            urgency=urgency,  # 紧急程度
            entities=entities,  # 提取的实体
            reasoning=llm.get("reasoning", ""),  # 识别依据说明
            latency_ms=(time.monotonic() - t0) * 1000,  # 耗时（毫秒）
        )

        # LRU 缓存：缓存超过 1000 条时清除最早的 500 条
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]  # 删除最早的缓存项
        # 写入本次结果到缓存
        self._cache[key] = result
        # 返回识别结果
        return result

    def learn(self, message: str, correct: IntentCategory) -> None:
        """
        在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。

        当用户指出识别错误时，将正确的样本加入模板列表，
        并清除该类别的 Embedding 缓存以便下次重新计算。

        参数：
            message: 需要学习的消息样本
            correct: 正确的意图类别
        """
        # 获取或创建该意图的模板列表
        tpls = _TEMPLATES.setdefault(correct, [])
        # 该样本不在模板中时才学习
        if message not in tpls:
            tpls.append(message)  # 加入模板列表
            self._tpl_embeddings.pop(correct, None)  # 清除该类的 Embedding 缓存（下次重新计算）
            logger.info(f"学习新样本 → {correct.value}: {message[:40]}")  # 记录学习日志

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,  # 用户输入的消息
        history: Optional[List[Dict[str, str]]],  # 对话历史（用于上下文理解）
    ) -> Dict[str, Any]:
        """
        策略 1：LLM 语义理解（Few-shot + 上下文）。

        构建包含 Few-shot 示例和最近对话上下文的 prompt，调用 LLM
        返回识别的意图类别、置信度和推理说明。

        参数：
            message: 用户消息
            history: 对话历史

        返回：
            识别结果字典（含 intent、confidence、reasoning；失败时含 failed 标记）
        """
        # 清洗消息文本（移除 Unicode 代理字符）
        message = self._clean_text(message)
        # 构建 Few-shot 示例
        # 遍历所有意图类别的模板，每类取 1 条示例
        examples = "\n".join(
            f'  消息: "{t}" → 意图: {cat.value}'  # 示例格式：消息 -> 意图
            for cat, tpls in _TEMPLATES.items()
            for t in tpls[:1]  # 每类取 1 条，控制 prompt 长度
        )
        # 最近 3 轮对话上下文
        ctx = ""
        # 有历史时构建上下文文本
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"  # 角色和内容
                for m in history[-3:]  # 只取最近 3 轮
            )

        # 组装发送给 LLM 的完整 prompt
        prompt = f"""你是客服意图分析专家。根据示例判断用户意图，返回 JSON。

示例:
{examples}

{ctx}
用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}"""
        # 清洗整个 prompt（防止非 ASCII 字符导致编码问题）
        prompt = self._clean_text(prompt)

        # 调用 LLM 并处理可能出现的异常
        try:
            # 调用 Anthropic 消息接口
            resp = await self.client.messages.create(
                model=self.model,  # 模型名称
                max_tokens=256,  # 最大输出 token 数
                temperature=0.1,  # 低温度保证稳定
                messages=[{"role": "user", "content": prompt}],  # 用户消息
            )
            # 提取模型返回的文本
            raw = extract_text(resp)
            # 提取 JSON 内容（从第一个 { 到最后一个 }）
            s, e = raw.find("{"), raw.rfind("}") + 1
            # 解析 JSON 为字典
            data = json.loads(raw[s:e])
            # 尝试将意图字符串转换为枚举值
            try:
                data["intent"] = IntentCategory(data["intent"])  # 转枚举
            except ValueError:
                data["intent"] = IntentCategory.OTHER  # 值非法时降级为 OTHER
            # 返回解析结果
            return data
        except Exception as ex:
            # 记录识别失败的警告日志
            logger.warning(f"LLM 识别失败: {ex}")
            # 返回失败占位结果（带 failed 标记）
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "reasoning": "LLM 失败", "failed": True}

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """
        策略 2：Embedding 向量相似度匹配。

        将消息向量化后与所有模板向量计算余弦相似度，
        取相似度最高的意图类别作为结果。

        参数：
            message: 用户输入的消息

        返回：
            识别结果字典（含 intent、confidence）
        """
        # 处理异常，保证 Embedding 不可用时不影响主链路
        try:
            # 懒加载模板 Embedding 向量
            await self._load_template_embeddings()
            # 将消息转为向量
            msg_vec = await self._embed_text(message)

            # 记录当前最佳类别和分数
            best_cat, best_score = IntentCategory.OTHER, 0.0
            # 遍历所有意图类别的模板向量
            for cat, vecs in self._tpl_embeddings.items():
                # 将消息向量与该类别所有模板向量计算余弦相似度，取最大值
                score = max(_cosine(msg_vec, v) for v in vecs)
                # 分数超过当前最佳则更新
                if score > best_score:
                    best_score, best_cat = score, cat  # 更新最佳类别和分数

            # 返回最佳匹配结果
            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            # 记录 Embedding 识别失败日志
            logger.warning(f"Embedding 识别失败: {ex}")
            # 返回 OTHER 占位结果
            return {"intent": IntentCategory.OTHER, "confidence": 0.0}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """
        策略 3：关键词模式匹配（同步，零延迟兜底）。

        用预定义的关键词列表对消息做匹配，命中比例作为置信度，
        取命中比例最高的类别作为结果。同步执行，零延迟。

        参数：
            message: 用户输入的消息

        返回：
            识别结果字典（含 intent、confidence）
        """
        # 将消息转为小写便于关键词匹配
        msg = message.lower()
        # 定义各意图类别的关键词列表
        patterns = {
            IntentCategory.ESCALATION: ["投诉", "经理", "转人工", "supervisor"],  # 升级类关键词
            IntentCategory.COMPLAINT:  ["太差", "糟糕", "horrible", "等了很久"],  # 投诉类关键词
            IntentCategory.QUERY:      ["?", "？", "怎么", "什么", "status"],  # 查询类关键词
            IntentCategory.REQUEST:    ["帮我", "需要", "please", "help"],  # 请求操作类关键词
            IntentCategory.GREETING:   ["你好", "嗨", "hello", "hi"],  # 问候类关键词
            IntentCategory.BILLING:    ["退款", "扣款", "发票", "refund"],  # 账单类关键词
            IntentCategory.TECHNICAL:  ["崩溃", "报错", "error", "crash"],  # 技术类关键词
            IntentCategory.ACCOUNT:    ["密码", "邮箱", "账户", "password"],  # 账户类关键词
        }
        # 记录当前最佳类别和分数
        best_cat, best_score = IntentCategory.OTHER, 0.0
        # 遍历所有意图的关键词列表
        for cat, kws in patterns.items():
            # 统计命中的关键词数量
            hits = sum(1 for kw in kws if kw in msg)
            # 有命中时计算命中比例作为置信度
            if hits:
                score = hits / len(kws)  # 命中数 / 关键词总数
                # 分数超过当前最佳则更新
                if score > best_score:
                    best_score, best_cat = score, cat  # 更新最佳类别和分数
        # 返回匹配结果
        return {"intent": best_cat, "confidence": best_score}

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> IntentCategory:
        """
        加权投票，合并三路识别结果得到最终意图。

        将 LLM、Embedding、Pattern 三路结果按权重加权求和，
        得分最高的类别作为最终意图；embedding 不可用时权重自动
        转移到 LLM 和 Pattern。最终得分低于阈值则返回 OTHER。

        参数：
            llm: LLM 策略的识别结果字典（含 intent、confidence、failed）
            emb: Embedding 策略的识别结果字典
            pat: Pattern 策略的识别结果字典

        返回：
            最终确定的意图类别
        """
        # LLM 失败时启用降级逻辑（weight 自动转移）
        if llm.get("failed"):
            # Embedding 有有效结果且置信度大于 0 时直接采用
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"]  # 返回 Embedding 的意图
            # 否则 Pattern 有有效结果时采用 Pattern
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"]  # 返回 Pattern 的意图
            # 都不可用时兜底为 OTHER（意图不明）
            return IntentCategory.OTHER

        # Embedding 可用时分配三路权重
        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]  # LLM 70% / Embedding 20% / Pattern 10%
        else:
            # Embedding 不可用时权重转移到 LLM 和 Pattern
            weights = [(llm, 0.85), (pat, 0.15)]
        # 记录每个类别的加权得分
        scores: Dict[IntentCategory, float] = {}
        # 遍历三路结果按权重累加得分
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)  # 取该路判定的意图
            conf = result.get("confidence", 0.0)  # 取该路的置信度
            # 按权重累加到该类别的总分
            scores[cat] = scores.get(cat, 0.0) + w * conf

        # 取得分最高的类别作为候选意图
        best = max(scores, key=scores.get)  # type: ignore

        # 如果三路判断加权得到的最佳 intent 的分数没有高于阈值，则说明意图判断不清晰，返回 other
        return best if scores[best] >= self.threshold else IntentCategory.OTHER

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    async def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """
        用 LLM 从消息中提取结构化实体。

        将客服消息中的订单号、产品、日期、金额、错误码等实体
        抽取为 JSON 字典返回，供槽位填充和后续流程使用。

        参数：
            message: 用户输入的消息

        返回：
            实体字典（值为列表；提取失败时返回全空列表）
        """
        # 清洗消息文本
        message = self._clean_text(message)
        # 构造实体提取 prompt（要求返回固定格式 JSON）
        prompt = f"""从客服消息中提取实体，返回 JSON（字段值为列表，没有则为空列表）:
消息: "{message}"
格式: {{"order_id":[],"product":[],"date":[],"amount":[],"error_code":[]}}"""
        # 清洗整个 prompt
        prompt = self._clean_text(prompt)
        # 调用 LLM 并捕获异常
        try:
            # 调用 Anthropic 消息接口
            resp = await self.client.messages.create(
                model=self.model,  # 模型名称
                max_tokens=256,  # 最大输出 token 数
                temperature=0.0,  # 温度 0 保证稳定抽取
                messages=[{"role": "user", "content": prompt}],  # 用户消息
            )
            # 提取模型返回文本
            raw = extract_text(resp)
            # 定位 JSON 起止位置（第一个 { 到最后一个 }）
            s, e = raw.find("{"), raw.rfind("}") + 1
            # 解析 JSON 为实体字典
            return json.loads(raw[s:e])
        except Exception:
            # 提取失败时返回全空实体列表
            return {"order_id": [], "product": [], "date": [], "amount": [], "error_code": []}

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """
        懒加载所有模板的 Embedding（只在首次调用时执行）。

        为缺少向量的模板类别逐一生成 Embedding 并缓存到
        _tpl_embeddings 字典，后续识别直接复用。
        """
        # 找出尚未生成向量的模板类别（懒加载判断）
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        # 全部已生成时直接返回
        if not missing:
            return

        # 收集所有缺失类别下的模板文本
        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        # 为每个模板文本生成向量
        vecs = [await self._embed_text(text) for text in all_texts]
        # 记录切片起始下标
        idx = 0
        # 按类别切分向量存入缓存
        for cat in missing:
            n = len(_TEMPLATES[cat])  # 该类别的模板数
            self._tpl_embeddings[cat] = vecs[idx: idx + n]  # 切出该类别的向量段
            idx += n  # 移动起始下标

    async def _embed_text(self, text: str) -> List[float]:
        """
        生成文本向量。

        如果未来接入的官方/兼容客户端提供 embeddings.create，会优先使用远端向量；
        当前 Anthropic SDK 没有该资源时，退化为本地字符 n-gram 哈希向量。这样不会因为
        Embedding 服务缺失导致三路融合中断。

        参数：
            text: 待向量化的文本

        返回：
            文本对应的向量（List[float]）
        """
        # 尝试获取客户端的 embeddings 资源（可能不存在）
        embeddings = getattr(self.client, "embeddings", None)
        # 客户端具备 embeddings.create 时优先使用远端向量
        if embeddings is not None:
            try:
                # 调用远端 Embedding 接口
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                # 返回首个文本的 embedding 向量
                return list(resp.data[0].embedding)
            except Exception as ex:
                # 远端失败时记录警告，继续走本地兜底
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        # 本地哈希向量兜底
        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """
        稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。

        对文本做 1-3 字符 n-gram 切分，再用 MD5 哈希映射到固定维度向量，
        保证相同文本得到相同向量、语义相近文本向量也相近，且零外部依赖。

        参数：
            text: 待向量化的文本
            dims: 向量维度（默认 256）

        返回：
            本地生成的哈希向量
        """
        # 统一小写并去除首尾空白
        normalized = text.lower().strip()
        # 初始化全零向量
        vec = [0.0] * dims
        # 用集合收集 n-gram 去重
        tokens = set()
        # 遍历 1-3 字符的 n-gram
        for n in (1, 2, 3):
            # 文本长度足够时才切分
            if len(normalized) >= n:
                # 收集所有该长度的连续子串
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        # 空文本时兜底加入整个文本
        if not tokens:
            tokens.add(normalized)

        # 遍历所有 n-gram token 哈希映射到向量
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()  # 计算 MD5 摘要
            idx = int.from_bytes(digest[:4], "big") % dims  # 前 4 字节确定维度下标
            sign = 1.0 if digest[4] % 2 == 0 else -1.0  # 第 5 字节决定正负号
            vec[idx] += sign  # 累加到对应维度
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        """
        判断消息的紧急程度。

        优先依据紧急关键词匹配，其次依据意图类别推断，
        返回 LOW / MEDIUM / HIGH 三级。

        参数：
            message: 用户输入的消息
            intent: 识别出的意图类别

        返回：
            紧急程度等级
        """
        # 转为小写便于关键词匹配
        msg = message.lower()
        # 遍历紧急关键词表
        for level, kws in _URGENCY_KEYWORDS.items():
            # 命中任一关键词即返回对应等级
            if any(kw in msg for kw in kws):
                return level  # 返回关键词对应的紧急等级
        # 无关键词命中时按意图推断
        if intent == IntentCategory.ESCALATION:
            # 升级请求视为高优先级
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            # 投诉视为中优先级
            return UrgencyLevel.MEDIUM
        # 其余情况为低优先级
        return UrgencyLevel.LOW

    def _cache_key(self, message: str) -> str:
        """
        生成识别结果缓存的 key。

        清洗消息后截取前 200 字符，保证 key 稳定且长度可控。

        参数：
            message: 用户输入的消息

        返回：
            缓存 key 字符串
        """
        # 清洗消息并截取前 200 字符作为 key
        return self._clean_text(message)[:200]

    @staticmethod
    def _clean_text(value: Any) -> str:
        """
        移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。

        先转成字符串，再用 utf-8 + errors="ignore" 剔除无法编码的
        代理字符（如 emoji 中成对代理）。

        参数：
            value: 任意值（可为 None）

        返回：
            清洗后的安全字符串
        """
        # None 值返回空字符串
        if value is None:
            return ""
        # 非字符串先转成字符串
        if not isinstance(value, str):
            value = str(value)
        # 用忽略错误的编码解码剔除代理字符
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        """
        返回识别缓存的统计信息。

        用于监控识别缓存命中情况，指导是否需要调整缓存策略。

        返回：
            缓存大小、命中数、未命中数和命中率
        """
        # 计算总请求次数
        total = self.cache_hits + self.cache_misses
        # 返回缓存统计字段
        return {
            "size": len(self._cache),  # 缓存条目数
            "hits": self.cache_hits,  # 命中次数
            "misses": self.cache_misses,  # 未命中次数
            "hit_rate": self.cache_hits / total if total else 0.0,  # 命中率（无请求时为 0）
        }
