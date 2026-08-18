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
    """粗粒度任务类型。

    Phase 1 先保留粗粒度分类，目的是：
    1. 初始化任务流
    2. 决定允许调用哪些工具
    3. 减少过早引入细粒度标签带来的复杂度
    """

    FAQ = "faq"
    REFUND = "refund"
    ACCOUNT = "account_issue"
    TECHNICAL = "technical_issue"
    BILLING = "billing_issue"
    ESCALATION = "escalation"
    UNKNOWN = "unknown"


class TaskState(Enum):
    """任务推进状态。"""

    NEW_REQUEST = "new_request"
    NEED_CLARIFICATION = "need_clarification"
    READY_FOR_TOOL = "ready_for_tool"
    WAITING_TOOL_RESULT = "waiting_tool_result"
    READY_TO_ANSWER = "ready_to_answer"
    NEEDS_HANDOFF = "needs_handoff"
    COMPLETED = "completed"


class PendingAction(Enum):
    """当前轮建议动作。"""

    ASK_FOLLOWUP = "ask_followup"
    CALL_TOOL = "call_tool"
    RESPOND = "respond"
    HANDOFF = "handoff"
    FINISH = "finish"


@dataclass
class SessionState:
    """
    单个会话的运行时状态。

    这是 Phase 1 中最核心的对象：
    它记录当前任务属于什么类型、进行到哪一步、已收集到哪些槽位、调用过哪些工具。
    """

    session_id: str
    user_id: str
    conv_id: str
    task_type: str = TaskType.UNKNOWN.value
    task_state: str = TaskState.NEW_REQUEST.value
    current_agent: str = "support"
    step_count: int = 0
    collected_slots: Dict[str, Any] = field(default_factory=dict)
    pending_action: str = PendingAction.ASK_FOLLOWUP.value
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    last_tool_result: Optional[Dict[str, Any]] = None
    requires_handoff: bool = False
    handoff_reason: Optional[str] = None
    final_outcome: Optional[str] = None
    last_user_message: str = ""
    last_assistant_message: str = ""
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    @classmethod
    def new(cls, user_id: str, conv_id: str) -> "SessionState":
        """创建新会话状态。"""
        return cls(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            conv_id=conv_id,
        )

    def touch(self) -> None:
        """每次状态更新后刷新更新时间。"""
        self.updated_at = _utc_now()

    def to_dict(self) -> Dict[str, Any]:
        """序列化为普通字典，方便写入 Redis。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        """从 Redis 中恢复 SessionState。"""
        return cls(**data)


class SessionStateStore:
    """
    基于 Redis 的状态存储。

    不额外引入复杂的 Repository 层，直接提供最基本的 get/save/delete 接口。
    """

    KEY_PREFIX = "runtime:session"

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl_s: int = 86400):
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl_s = ttl_s

    def load(self, conv_id: str) -> Optional[SessionState]:
        """读取现有状态；不存在时返回 None。"""
        raw = self._redis.get(self._key(conv_id))
        if not raw:
            return None
        data = json.loads(raw)
        return SessionState.from_dict(data)

    def save(self, state: SessionState) -> None:
        """写入状态，并刷新 TTL。"""
        state.touch()
        self._redis.setex(
            self._key(state.conv_id),
            self._ttl_s,
            json.dumps(state.to_dict(), ensure_ascii=False),
        )

    def delete(self, conv_id: str) -> None:
        """删除状态，通常用于任务完成后的清理。"""
        self._redis.delete(self._key(conv_id))

    def load_or_create(self, user_id: str, conv_id: str) -> SessionState:
        """读取状态；若不存在则新建。"""
        state = self.load(conv_id)
        if state is not None:
            return state
        state = SessionState.new(user_id=user_id, conv_id=conv_id)
        self.save(state)
        return state

    @classmethod
    def _key(cls, conv_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{conv_id}"
