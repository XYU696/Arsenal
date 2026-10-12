"""
Phase 1 运行时状态定义。

本文件只做两件事：
1. 定义 SessionState 的核心数据结构
2. 提供基于 Redis 的轻量状态存取能力

设计原则：
- 状态字段尽量直白，便于调试和人工阅读
- 不引入复杂 ORM 或状态框架，先保证运行时链路清晰可控
"""
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import redis


def _utc_now() -> str:
    """统一使用 UTC 字符串，便于日志与数据库字段对齐。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TaskType(Enum):
    """粗粒度任务类型枚举。

    定义了客服系统中的主要任务分类，用于路由到不同的处理流程和工具集。

    Phase 1 先保留粗粒度分类，目的是：
    1. 初始化任务流
    2. 决定允许调用哪些工具
    3. 减少过早引入细粒度标签带来的复杂度
    """

    FAQ = "faq"  # 常见问题咨询（包括产品介绍、使用指南、物流查询等）
    REFUND = "refund"  # 退款相关请求（退款咨询、退款申请、退款进度查询等）
    ACCOUNT = "account_issue"  # 账户问题（登录问题、密码重置、账户设置等）
    TECHNICAL = "technical_issue"  # 技术问题（系统故障、功能异常、报错等）
    BILLING = "billing_issue"  # 账单问题（订单查询、支付问题、发票开具等）
    ESCALATION = "escalation"  # 升级请求（用户明确要求转人工或问题超出自动化处理范围）
    UNKNOWN = "unknown"  # 未知类型（无法明确分类的请求，需要进一步澄清或兜底处理）


class TaskState(Enum):
    """任务推进状态枚举。

    描述了一个任务在处理过程中的不同阶段，用于控制 Agent 的决策流程。
    """

    NEW_REQUEST = "new_request"  # 新请求（刚接收到用户消息，尚未开始处理）
    NEED_CLARIFICATION = "need_clarification"  # 需要澄清（信息不足，需要向用户追问）
    READY_FOR_TOOL = "ready_for_tool"  # 准备调用工具（已确定需要调用哪个工具及参数）
    WAITING_TOOL_RESULT = "waiting_tool_result"  # 等待工具结果（工具调用中，等待返回）
    READY_TO_ANSWER = "ready_to_answer"  # 准备回答（已获取所需信息，可以生成最终回复）
    NEEDS_HANDOFF = "needs_handoff"  # 需要转人工（问题超出能力范围或用户明确要求）
    COMPLETED = "completed"  # 已完成（本轮任务处理完毕，可以退出循环）


class PendingAction(Enum):
    """当前轮建议动作枚举。

    定义了 Agent 在每一轮对话中可执行的建议动作，
    由 Agent 决策产生，用于控制 LangGraph 流程下一步的分支走向。
    """

    ASK_FOLLOWUP = "ask_followup"  # 追问（信息不足，需要向用户补充询问）
    CALL_TOOL = "call_tool"  # 调用工具（需要调用工具核实事实或查询数据）
    RESPOND = "respond"  # 直接回应（无需工具，直接给用户答复）
    HANDOFF = "handoff"  # 转人工（问题无法自动处理，升级到人工客服）
    FINISH = "finish"  # 结束（本轮任务处理完毕，结束对话循环）


@dataclass
class SessionState:
    """
    单个会话的运行时状态。

    封装了 Agent 会话的完整状态信息，用于跨轮次持久化和状态恢复。
    这是 Phase 1 中最核心的对象：
    它记录当前任务属于什么类型、进行到哪一步、已收集到哪些槽位、调用过哪些工具。
    """

    session_id: str  # 会话状态的唯一标识符（通常与 conv_id 相同或关联）
    user_id: str  # 用户唯一标识
    conv_id: str  # 会话唯一标识
    task_type: str = TaskType.UNKNOWN.value  # 任务类型（faq、refund、account_issue 等）
    task_state: str = TaskState.NEW_REQUEST.value  # 任务推进状态（new_request、ready_for_tool、completed 等）
    current_agent: str = "support"  # 当前处理的 Agent 类型（support、support_graph 等）
    step_count: int = 0  # 本会话已执行的轮次计数（用于追踪对话进度）
    collected_slots: Dict[str, Any] = field(default_factory=dict)  # 已收集的槽位信息（如订单号、退款原因等结构化数据）
    pending_action: str = PendingAction.ASK_FOLLOWUP.value  # 待执行的动作（ask_followup、call_tool、end_turn 等）
    tool_history: List[Dict[str, Any]] = field(default_factory=list)  # 工具调用历史记录（包含工具名、参数、结果）
    last_tool_result: Optional[Dict[str, Any]] = None  # 最后一次工具调用的结果（便于 Agent 引用）
    requires_handoff: bool = False  # 是否需要转人工标志
    handoff_reason: Optional[str] = None  # 转人工原因（如"超出能力范围"、"用户明确要求"等）
    final_outcome: Optional[str] = None  # 最终结果（resolved、handed_off、ticket_created 等）
    last_user_message: str = ""  # 用户最后一条消息（用于上下文引用）
    last_assistant_message: str = ""  # Assistant 最后一条回复（用于连贯性检查）
    created_at: str = field(default_factory=_utc_now)  # 会话创建时间（UTC 格式）
    updated_at: str = field(default_factory=_utc_now)  # 会话最后更新时间（UTC 格式）

    @classmethod
    def new(cls, user_id: str, conv_id: str) -> "SessionState":
        """
        创建新会话状态。

        生成新的会话 ID，并记录用户 ID 和会话 ID，返回初始化的 SessionState 实例。

        参数：
            user_id: 用户唯一标识
            conv_id: 会话唯一标识

        返回：
            新建的 SessionState 实例（其余字段使用默认值）
        """
        # 构造 SessionState 实例并返回
        return cls(
            session_id=str(uuid.uuid4()),  # 生成全局唯一的会话状态 ID
            user_id=user_id,  # 传入用户 ID
            conv_id=conv_id,  # 传入会话 ID
        )

    def touch(self) -> None:
        """
        每次状态更新后刷新更新时间。

        调用后将 updated_at 字段更新为当前 UTC 时间，
        用于记录状态最后修改的时间点。
        """
        # 更新最后修改时间戳为当前 UTC 时间
        self.updated_at = _utc_now()

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为普通字典，方便写入 Redis。

        使用 dataclasses.asdict 将 SessionState 的字段转为普通字典，
        便于 JSON 序列化后存储到 Redis。

        返回：
            包含所有字段的普通字典
        """
        # 将 dataclass 转为普通字典
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        """
        从 Redis 中恢复 SessionState。

        将读取到的字典按字段名展开为关键字参数，构造 SessionState 实例。

        参数：
            data: 存储的字段字典（从 Redis JSON 解析得到）

        返回：
            恢复的 SessionState 实例
        """
        # 用字典展开为关键字参数构造实例
        return cls(**data)


class SessionStateStore:
    """
    基于 Redis 的状态存储。

    负责会话状态的持久化：将 SessionState 序列化后写入 Redis，
    以及从 Redis 中读取恢复。不额外引入复杂的 Repository 层，
    直接提供最基本的 get/save/delete 接口。

    Redis 存储格式：
        Key:   runtime:session:{conv_id}
        Value: SessionState 序列化后的 JSON 字符串（带 TTL 过期时间）
    """

    # Redis key 前缀，用于区分会话状态与其他数据
    KEY_PREFIX = "runtime:session"

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl_s: int = 86400):
        """
        初始化状态存储。

        参数：
            redis_url: Redis 连接地址（默认本地 6379）
            ttl_s: 会话状态的过期时间，单位秒（默认 24 小时）
        """
        # 创建 Redis 连接，启用 decode_responses 让返回值直接是字符串
        self._redis = redis.from_url(redis_url, decode_responses=True)
        # 保存 TTL 过期时间
        self._ttl_s = ttl_s

    def load(self, conv_id: str) -> Optional[SessionState]:
        """
        读取现有状态；不存在时返回 None。

        参数：
            conv_id: 会话唯一标识

        返回：
            恢复的 SessionState，不存在时返回 None
        """
        # 从 Redis 中按 key 读取原始 JSON 字符串
        raw = self._redis.get(self._key(conv_id))
        # key 不存在或值为空时返回 None
        if not raw:
            return None
        # 将 JSON 字符串解析为字典
        data = json.loads(raw)
        # 从字典恢复 SessionState 实例
        return SessionState.from_dict(data)

    def save(self, state: SessionState) -> None:
        """
        写入状态，并刷新 TTL。

        参数：
            state: 要保存的 SessionState 实例
        """
        # 刷新 updated_at 时间戳
        state.touch()
        # 写入 Redis（setex 自动设置过期时间）
        self._redis.setex(
            self._key(state.conv_id),  # Redis key：runtime:session:{conv_id}
            self._ttl_s,  # TTL 过期时间（秒）
            json.dumps(state.to_dict(), ensure_ascii=False),  # 序列化为 JSON 字符串
        )

    def delete(self, conv_id: str) -> None:
        """
        删除状态，通常用于任务完成后的清理。

        参数：
            conv_id: 会话唯一标识
        """
        # 按 key 从 Redis 删除对应会话状态
        self._redis.delete(self._key(conv_id))

    def load_or_create(self, user_id: str, conv_id: str) -> SessionState:
        """
        读取状态；若不存在则新建。

        优先从 Redis 读取已有会话状态；若不存在则创建新会话并立即保存。

        参数：
            user_id: 用户唯一标识
            conv_id: 会话唯一标识

        返回：
            已有或新建的 SessionState
        """
        # 先尝试读取已有状态
        state = self.load(conv_id)
        # 状态存在时直接返回
        if state is not None:
            return state  # 返回已有状态
        # 状态不存在时创建新会话
        state = SessionState.new(user_id=user_id, conv_id=conv_id)
        # 立即保存新会话到 Redis
        self.save(state)
        # 返回新建的状态
        return state

    @classmethod
    def _key(cls, conv_id: str) -> str:
        """
        根据会话 ID 生成 Redis key。

        参数：
            conv_id: 会话唯一标识

        返回：
            拼接后的 Redis key（格式：{前缀}:{conv_id}）
        """
        # 拼接前缀和会话 ID 生成 key
        return f"{cls.KEY_PREFIX}:{conv_id}"
