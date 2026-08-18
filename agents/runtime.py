"""
Phase 1 单 Agent Runtime（LangGraph Graph API 版本）。

设计目标：
1. 用 LangGraph 替代手写状态机，让 Agent 形成真正的“思考 -> 动作 -> 观察 -> 再思考”循环。
2. 保留当前业务数据库工具、Redis 状态存储和 Skills 机制，避免推倒重来。
3. 保持实现简单：先做单 Agent、受控 ReAct、有限动作、有限工具。
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from core.intent_recognizer import IntentCategory, IntentRecognizer
from mcp.tool_manager import MCPToolManager, ToolResult

from .session_state import PendingAction, SessionState, SessionStateStore, TaskState, TaskType

try:
    # LangGraph 是当前版本的核心依赖；如果环境未安装，启动时给出清晰报错。
    from langgraph.graph import END, START, StateGraph
except ImportError as ex:  # pragma: no cover - 运行时依赖检查
    END = START = StateGraph = None  # type: ignore
    _LANGGRAPH_IMPORT_ERROR = ex
else:
    _LANGGRAPH_IMPORT_ERROR = None


logger = logging.getLogger(__name__)


@dataclass
class RuntimeStepResult:
    """
    runtime 每执行一次 /chat 请求后的输出。

    这里的“一次 step”是指：从接收用户本轮消息开始，到 Agent 完成本轮循环退出为止。
    """

    response: str
    task_type: str
    task_state: str
    pending_action: str
    used_tools: List[Dict[str, Any]] = field(default_factory=list)
    requires_handoff: bool = False
    final_outcome: Optional[str] = None
    ticket_no: Optional[str] = None
    handoff_queue: Optional[str] = None
    # Trace V1：本轮 Agent 执行轨迹（节点 -> 决策 -> 工具调用 -> 结果）
    steps: List[Dict[str, Any]] = field(default_factory=list)


class LangGraphState(TypedDict, total=False):
    """
    LangGraph 内部状态。

    注意：
    - `session` 是持久化到 Redis 的核心状态
    - 其他字段主要服务于当前一次 graph 执行，不一定长期持久化
    """

    user_id: str
    conv_id: str
    message: str
    context: str
    history: List[Dict[str, str]]
    session: Dict[str, Any]
    task_type: str
    allowed_tools: List[str]
    task_flow_name: str
    loop_count: int
    last_action: str
    response: str
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Dict[str, Any]
    current_turn_tools: List[Dict[str, Any]]
    final_response: str
    final_outcome: str
    requires_handoff: bool
    ticket_no: str
    handoff_queue: str
    trace: List[Dict[str, Any]]


class SingleAgentRuntime:
    """
    单 Agent、受控 ReAct、基于 LangGraph 的 runtime。

    与上一版状态机实现相比，这一版的核心变化是：
    - 不再由程序完全写死每一步
    - 让 Agent 自主决定下一步动作
    - 通过 Graph 明确循环边界和退出路径
    """

    MAX_GRAPH_LOOPS = 4

    # 粗粒度任务允许使用的工具白名单。
    TOOL_WHITELIST: Dict[str, List[str]] = {
        # FAQ 增加物流查询：纯物流咨询（无退款关键词）会被分类到这里
        TaskType.FAQ.value: ["knowledge_search", "shipment_tracking_lookup"],
        TaskType.REFUND.value: [
            "order_lookup",
            "order_items_lookup",
            "payment_attempt_lookup",
            "shipment_tracking_lookup",
            "refund_policy_lookup",
            "refund_eligibility_check",
            "refund_request_lookup",
            "create_refund_request",
            "create_handoff_ticket",
        ],
        TaskType.ACCOUNT.value: ["account_status_lookup", "create_handoff_ticket"],
        TaskType.TECHNICAL.value: ["incident_lookup", "knowledge_search", "create_handoff_ticket"],
        TaskType.BILLING.value: [
            "order_lookup",
            "order_items_lookup",
            "payment_attempt_lookup",
            "shipment_tracking_lookup",
            "refund_policy_lookup",
            "refund_eligibility_check",
            "refund_request_lookup",
            "create_refund_request",
            "create_handoff_ticket",
        ],
        TaskType.ESCALATION.value: ["create_handoff_ticket"],
        TaskType.UNKNOWN.value: ["knowledge_search", "create_handoff_ticket"],
    }

    # 每个粗粒度任务对应的说明性流名称，便于调试与监控。
    TASK_FLOW_NAMES: Dict[str, str] = {
        TaskType.FAQ.value: "faq_graph_flow",
        TaskType.REFUND.value: "refund_graph_flow",
        TaskType.ACCOUNT.value: "account_issue_graph_flow",
        TaskType.TECHNICAL.value: "technical_issue_graph_flow",
        TaskType.BILLING.value: "billing_issue_graph_flow",
        TaskType.ESCALATION.value: "escalation_graph_flow",
        TaskType.UNKNOWN.value: "fallback_graph_flow",
    }

    def __init__(
        self,
        recognizer: IntentRecognizer,
        tool_manager: MCPToolManager,
        state_store: SessionStateStore,
        skill_manager: Optional[Any] = None,
    ):
        if StateGraph is None:
            raise RuntimeError(
                "未安装 langgraph，无法使用 LangGraph 版 runtime。"
                "请先安装 requirements.txt 中的 langgraph 依赖。"
            ) from _LANGGRAPH_IMPORT_ERROR

        self._recognizer = recognizer
        self._tool_manager = tool_manager
        self._state_store = state_store
        self._skill_manager = skill_manager
        self._client = recognizer.client
        self._model = recognizer.model
        self._graph = self._build_graph()

    async def step(
        self,
        user_id: str,
        conv_id: str,
        message: str,
        context: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[SessionState, RuntimeStepResult]:
        """
        执行一次完整的 LangGraph Agent loop。

        注意：
        这里不是只执行 graph 的一个节点，而是让 graph 按条件边循环，
        直到当前轮应该退出为止。
        """
        initial_state: LangGraphState = {
            "user_id": user_id,
            "conv_id": conv_id,
            "message": message,
            "context": context, # 记忆上下文
            "history": history or [], # 最近几次对话
            "current_turn_tools": [],
        }
        output = await self._graph.ainvoke(initial_state)
        session = SessionState.from_dict(output["session"])
        result = RuntimeStepResult(
            response=output.get("final_response") or output.get("response") or session.last_assistant_message,
            task_type=session.task_type,
            task_state=session.task_state,
            pending_action=session.pending_action,
            used_tools=output.get("current_turn_tools", []),
            requires_handoff=session.requires_handoff,
            final_outcome=session.final_outcome,
            ticket_no=output.get("ticket_no"),
            handoff_queue=output.get("handoff_queue"),
            steps=output.get("trace", []),
        )
        return session, result

    def allowed_tools(self, task_type: str) -> List[str]:
        """返回某类任务允许使用的工具。"""
        return self.TOOL_WHITELIST.get(task_type, self.TOOL_WHITELIST[TaskType.UNKNOWN.value])

    def task_flow_name(self, task_type: str) -> str:
        """返回某类任务对应的 flow 名称。"""
        return self.TASK_FLOW_NAMES.get(task_type, self.TASK_FLOW_NAMES[TaskType.UNKNOWN.value])

    def _build_graph(self):
        """构建 LangGraph Graph API。"""
        graph = StateGraph(LangGraphState)
        graph.add_node("load_state", self._load_state_node)
        graph.add_node("classify_or_resume", self._classify_or_resume_node)
        graph.add_node("agent_node", self._agent_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("handoff_node", self._handoff_node)
        graph.add_node("save_state", self._save_state_node)

        graph.add_edge(START, "load_state")
        graph.add_edge("load_state", "classify_or_resume")
        graph.add_edge("classify_or_resume", "agent_node")
        graph.add_conditional_edges(
            "agent_node",
            self._route_after_agent,
            {
                "tool": "tool_node",
                "handoff": "handoff_node",
                "save": "save_state",
            },
        )
        graph.add_edge("tool_node", "agent_node")
        graph.add_edge("handoff_node", "save_state")
        graph.add_edge("save_state", END)
        return graph.compile()

    def _append_trace(
        self,
        state: LangGraphState,
        *,
        node: str,
        decision: str,
        start_ts: float,
        reasoning: str = "",
        tool_name: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        detail: str = "",
        loop_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        向执行轨迹追加一步并返回新列表（与前端 TraceStep 字段对齐）。

        每次节点执行都会调用一次，latency_ms 由节点起始时间自动计算。
        """
        steps = list(state.get("trace", []))
        steps.append(
            {
                "node": node,
                "decision": decision,
                "reasoning": reasoning,
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "tool_result": tool_result or {},
                "detail": detail,
                "latency_ms": round((time.perf_counter() - start_ts) * 1000, 1),
                "loop_count": loop_count if loop_count is not None else int(state.get("loop_count", 0)),
            }
        )
        return steps

    @staticmethod
    def _truncate_trace_value(value: Any, limit: int = 500) -> Any:
        """工具参数/结果过大时截断，避免 trace 响应体过大。"""
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return value
        return text[:limit] + f"...(截断 {len(text) - limit} 字符)"

    async def _load_state_node(self, state: LangGraphState) -> LangGraphState:
        """
        读取或创建 SessionState。

        这一层仍然复用 Redis，LangGraph 负责循环逻辑，Redis 负责跨轮持久化。
        """
        start_ts = time.perf_counter()
        session = self._state_store.load_or_create(
            user_id=state["user_id"],
            conv_id=state["conv_id"],
        )
        session.step_count += 1
        session.last_user_message = state["message"]
        session.current_agent = "support_graph"
        trace = self._append_trace(
            state,
            node="load_state",
            decision="loaded_session",
            start_ts=start_ts,
            detail=f"step_count={session.step_count}",
        )
        return {
            "session": session.to_dict(),
            "loop_count": 0,
            "current_turn_tools": [],
            "requires_handoff": False,
            "trace": trace,
        }

    async def _classify_or_resume_node(self, state: LangGraphState) -> LangGraphState:
        """
        对新任务做粗粒度分类；对旧任务则恢复上下文。

        这里保留粗粒度分类，不是为了“再做一个路由器”，
        而是为了给 LangGraph Agent 一个更稳定的任务上下文和工具白名单。
        """
        start_ts = time.perf_counter()
        session = SessionState.from_dict(state["session"])

        # 新任务、未知任务或上一任务已完成时，重新分类并重置关键字段。
        if session.task_type == TaskType.UNKNOWN.value or session.task_state == TaskState.COMPLETED.value:
            session.task_type = await self._classify_task_type(
                state["message"],
                history=state.get("history"),
            )
            session.task_state = TaskState.NEW_REQUEST.value
            session.pending_action = PendingAction.ASK_FOLLOWUP.value
            session.requires_handoff = False
            session.handoff_reason = None
            session.final_outcome = None
            session.last_tool_result = None
            decision = "classify"
        else:
            decision = "resume"

        # 每轮都尝试做一次轻量槽位提取，减少不必要的工具空调用。
        self._extract_slots(session, state["message"])
        allowed_tools = self.allowed_tools(session.task_type)
        trace = self._append_trace(
            state,
            node="classify_or_resume",
            decision=decision,
            start_ts=start_ts,
            detail=(
                f"task_type={session.task_type} "
                f"flow={self.task_flow_name(session.task_type)} "
                f"tools={len(allowed_tools)}"
            ),
        )
        return {
            "session": session.to_dict(),
            "task_type": session.task_type,
            "allowed_tools": allowed_tools,
            "task_flow_name": self.task_flow_name(session.task_type),
            "trace": trace,
        }

    async def _agent_node(self, state: LangGraphState) -> LangGraphState:
        """
        Agent 核心节点。

        这里由模型决定下一步动作：
        - ask_followup
        - call_tool
        - handoff
        - finish

        如果模型失败，再回退到代码内置的保守策略。
        """
        session = SessionState.from_dict(state["session"])
        loop_count = int(state.get("loop_count", 0)) + 1
        start_ts = time.perf_counter()

        # 防止单轮 graph 内无限循环。
        if loop_count > self.MAX_GRAPH_LOOPS:
            session.task_state = TaskState.NEEDS_HANDOFF.value
            session.pending_action = PendingAction.HANDOFF.value
            session.requires_handoff = True
            session.handoff_reason = "graph_loop_limit_exceeded"
            trace = self._append_trace(
                state,
                node="agent",
                decision=PendingAction.HANDOFF.value,
                start_ts=start_ts,
                loop_count=loop_count,
                detail="循环超限，强制转人工",
            )
            return {
                "session": session.to_dict(),
                "loop_count": loop_count,
                "last_action": PendingAction.HANDOFF.value,
                "response": "为了避免重复尝试，我先为您转人工继续处理。",
                "requires_handoff": True,
                "trace": trace,
            }

        # decision_source 标记本次决策来源，便于 trace 排查：llm / fallback / rule_override
        decision_source = "llm"
        try:
            decision = await self._decide_action_with_llm(state, session)
        except Exception as ex:
            # LLM 决策失败时，使用更稳妥的保守回退逻辑，避免主链路直接崩。
            logger.exception(
                "Agent LLM 决策失败，进入 fallback。conv_id=%s task_type=%s error=%s",
                session.conv_id,
                session.task_type,
                ex,
            )
            decision = self._fallback_decision(state, session)
            decision_source = "fallback"

        action = decision.get("action", PendingAction.FINISH.value)
        response = str(decision.get("response", "") or "").strip()
        tool_name = str(decision.get("tool_name", "") or "").strip()
        tool_args = decision.get("tool_args") or {}
        reasoning = str(decision.get("reasoning", "") or "")

        # 对 LLM 决策做最后一步业务一致性约束：
        # 1. 如果回复里声称“已经转人工/已经建单”，就必须真的执行 handoff
        # 2. 如果退款规则明确要求人工审核，也不能直接 finish，必须进入 handoff
        if action == PendingAction.FINISH.value and self._should_force_handoff(session, response):
            logger.info(
                "Agent 决策被一致性规则改写为 handoff。conv_id=%s task_type=%s",
                session.conv_id,
                session.task_type,
            )
            action = PendingAction.HANDOFF.value
            decision_source = "rule_override"
            if not response:
                response = "我先为您转人工继续处理。"

        if action == PendingAction.ASK_FOLLOWUP.value:
            session.task_state = TaskState.NEED_CLARIFICATION.value
            session.pending_action = PendingAction.ASK_FOLLOWUP.value
            session.last_assistant_message = response
            trace = self._append_trace(
                state,
                node="agent",
                decision=action,
                start_ts=start_ts,
                loop_count=loop_count,
                reasoning=reasoning,
                detail=decision_source,
            )
            return {
                "session": session.to_dict(),
                "loop_count": loop_count,
                "last_action": action,
                "response": response,
                "final_response": response,
                "trace": trace,
            }

        if action == PendingAction.CALL_TOOL.value:
            normalized_args = self._normalize_tool_args(tool_name, tool_args, session, state["message"])
            session.task_state = TaskState.WAITING_TOOL_RESULT.value
            session.pending_action = PendingAction.CALL_TOOL.value
            trace = self._append_trace(
                state,
                node="agent",
                decision=action,
                start_ts=start_ts,
                loop_count=loop_count,
                reasoning=reasoning,
                tool_name=tool_name,
                tool_args=normalized_args,
                detail=decision_source,
            )
            return {
                "session": session.to_dict(),
                "loop_count": loop_count,
                "last_action": action,
                "tool_name": tool_name,
                "tool_args": normalized_args,
                "trace": trace,
            }

        if action == PendingAction.HANDOFF.value:
            session.task_state = TaskState.NEEDS_HANDOFF.value
            session.pending_action = PendingAction.HANDOFF.value
            session.requires_handoff = True
            session.handoff_reason = str(decision.get("reasoning", "") or "agent_requested_handoff")
            session.last_assistant_message = response
            trace = self._append_trace(
                state,
                node="agent",
                decision=action,
                start_ts=start_ts,
                loop_count=loop_count,
                reasoning=reasoning,
                detail=decision_source,
            )
            return {
                "session": session.to_dict(),
                "loop_count": loop_count,
                "last_action": action,
                "response": response,
                "requires_handoff": True,
                "trace": trace,
            }

        # 默认把剩余情况都视作 finish。
        session.task_state = TaskState.COMPLETED.value
        session.pending_action = PendingAction.FINISH.value
        session.final_outcome = "agent_finished"
        session.last_assistant_message = response
        trace = self._append_trace(
            state,
            node="agent",
            decision=PendingAction.FINISH.value,
            start_ts=start_ts,
            loop_count=loop_count,
            reasoning=reasoning,
            detail=decision_source,
        )
        return {
            "session": session.to_dict(),
            "loop_count": loop_count,
            "last_action": PendingAction.FINISH.value,
            "response": response,
            "final_response": response,
            "final_outcome": session.final_outcome,
            "trace": trace,
        }

    async def _tool_node(self, state: LangGraphState) -> LangGraphState:
        """
        工具执行节点。

        它只负责：
        1. 校验工具白名单
        2. 调用 MCPToolManager
        3. 把 observation 写回状态
        """
        start_ts = time.perf_counter()
        session = SessionState.from_dict(state["session"])
        tool_name = state.get("tool_name", "")
        tool_args = state.get("tool_args", {})

        result = await self._call_allowed_tool(
            session=session,
            allowed_tools=state.get("allowed_tools", []),
            tool_name=tool_name,
            params=tool_args,
            conv_id=state["conv_id"],
        )
        observation = {
            "success": result.success,
            "tool_name": result.tool_name,
            "data": result.data,
            "error": result.error,
            "cached": result.cached,
            "latency_ms": result.latency_ms,
        }
        session.last_tool_result = observation
        session.task_state = TaskState.READY_TO_ANSWER.value
        state["current_turn_tools"].append(
            {
                "tool_name": result.tool_name,
                "success": result.success,
                "error": result.error,
                "latency_ms": result.latency_ms,
            }
        )
        trace = self._append_trace(
            state,
            node="tool",
            decision="executed_tool",
            start_ts=start_ts,
            tool_name=result.tool_name,
            tool_args=tool_args,
            tool_result=self._truncate_trace_value(observation),
            detail="success" if result.success else f"error: {result.error}",
        )
        return {
            "session": session.to_dict(),
            "tool_result": observation,
            "current_turn_tools": state["current_turn_tools"],
            "trace": trace,
        }

    async def _handoff_node(self, state: LangGraphState) -> LangGraphState:
        """
        人工升级节点。

        这里会尝试创建工单；即使创建失败，也保证用户能拿到明确的升级提示。
        """
        start_ts = time.perf_counter()
        session = SessionState.from_dict(state["session"])
        ticket_params = self._compact_dict({
            "customer_code": session.collected_slots.get("customer_code"),
            "conv_id": session.conv_id,
            "source_intent": session.task_type,
            "priority": "high" if session.task_type in {TaskType.REFUND.value, TaskType.ACCOUNT.value} else "medium",
            "reason": session.handoff_reason or "agent_requested_handoff",
            "summary": session.last_user_message or "LangGraph runtime requested handoff",
            "assigned_queue": self._queue_for_task_type(session.task_type),
        })
        ticket_result = await self._call_allowed_tool(
            session=session,
            allowed_tools=self.allowed_tools(session.task_type),
            tool_name="create_handoff_ticket",
            params=ticket_params,
            conv_id=state["conv_id"],
        )

        state["current_turn_tools"].append(
            {
                "tool_name": ticket_result.tool_name,
                "success": ticket_result.success,
                "error": ticket_result.error,
                "latency_ms": ticket_result.latency_ms,
            }
        )

        base_response = state.get("response") or "我先为您转人工继续处理。"
        if ticket_result.success and isinstance(ticket_result.data, dict):
            ticket_no = ticket_result.data.get("ticket_no", "未知工单号")
            handoff_queue = ticket_result.data.get("assigned_queue")
            final_response = f"{base_response} 已为您创建工单 {ticket_no}，人工客服会继续跟进。"
            session.final_outcome = "handoff_ticket_created"
        else:
            ticket_no = None
            handoff_queue = None
            final_response = base_response
            session.final_outcome = "handoff_requested_without_ticket"

        session.task_state = TaskState.COMPLETED.value
        session.pending_action = PendingAction.FINISH.value
        session.requires_handoff = True
        session.last_assistant_message = final_response
        session.last_tool_result = {
            "tool_name": ticket_result.tool_name,
            "success": ticket_result.success,
            "data": ticket_result.data,
            "error": ticket_result.error,
        }
        trace = self._append_trace(
            state,
            node="handoff",
            decision="ticket_created" if ticket_no else "ticket_failed",
            start_ts=start_ts,
            tool_name="create_handoff_ticket",
            tool_args=ticket_params,
            tool_result=self._truncate_trace_value(ticket_result.data),
            detail=(
                f"ticket_no={ticket_no} queue={handoff_queue}"
                if ticket_no
                else f"error: {ticket_result.error}"
            ),
        )
        return {
            "session": session.to_dict(),
            "current_turn_tools": state["current_turn_tools"],
            "final_response": final_response,
            "final_outcome": session.final_outcome,
            "requires_handoff": True,
            "ticket_no": ticket_no,
            "handoff_queue": handoff_queue,
            "trace": trace,
        }

    async def _save_state_node(self, state: LangGraphState) -> LangGraphState:
        """保存当前会话状态。"""
        start_ts = time.perf_counter()
        session = SessionState.from_dict(state["session"])
        self._state_store.save(session)
        trace = self._append_trace(
            state,
            node="save_state",
            decision="state_saved",
            start_ts=start_ts,
            detail=f"task_state={session.task_state}",
        )
        return {"session": session.to_dict(), "trace": trace}

    def _route_after_agent(self, state: LangGraphState) -> str:
        """根据 Agent 决定的动作选择下一条边。"""
        action = state.get("last_action")
        if action == PendingAction.CALL_TOOL.value:
            return "tool"
        if action == PendingAction.HANDOFF.value:
            return "handoff"
        return "save"

    async def _classify_task_type(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """将现有粗粒度意图映射到 runtime 的 task_type。"""
        result = await self._recognizer.recognize(message, history=history)
        intent = result.intent
        lowered = message.lower()

        if intent == IntentCategory.TECHNICAL:
            return TaskType.TECHNICAL.value
        if intent == IntentCategory.ACCOUNT:
            return TaskType.ACCOUNT.value
        if intent == IntentCategory.BILLING:
            if any(kw in lowered for kw in ["退款", "退费", "refund"]):
                return TaskType.REFUND.value
            return TaskType.BILLING.value
        if intent == IntentCategory.ESCALATION:
            return TaskType.ESCALATION.value
        if intent in (IntentCategory.QUERY, IntentCategory.REQUEST):
            if any(kw in lowered for kw in ["退款", "退费", "refund"]):
                return TaskType.REFUND.value
            return TaskType.FAQ.value
        return TaskType.UNKNOWN.value

    def _extract_slots(self, session: SessionState, message: str) -> None:
        """
        用最轻量的规则抽取基础槽位。

        这里仍然保留规则抽取，是因为它对客服场景很实用，而且能降低模型空调用工具的概率。
        """
        order_match = re.search(r"(ORD-\d{8}-\d{4})", message)
        if order_match:
            session.collected_slots["order_no"] = order_match.group(1)

        tracking_match = re.search(r"([A-Z]{2,5}\d{8,20})", message)
        if tracking_match and not tracking_match.group(1).startswith("ORD-"):
            session.collected_slots.setdefault("tracking_no", tracking_match.group(1))

        account_match = re.search(r"(ACC-[A-Z]{2}-\d{2})", message)
        if account_match:
            session.collected_slots["account_id"] = account_match.group(1)

        customer_match = re.search(r"(CUST-\d{4})", message)
        if customer_match:
            session.collected_slots["customer_code"] = customer_match.group(1)

        email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", message)
        if email_match:
            session.collected_slots["email"] = email_match.group(1)

        # 退款场景里常见的购买时间，优先提取 YYYY-MM-DD 或 YYYY-MM-DD HH:MM。
        purchase_time_match = re.search(r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)", message)
        if purchase_time_match:
            session.collected_slots["purchase_time"] = purchase_time_match.group(1)

        # 提取使用比例，例如“0%”“目前使用了 35%”。
        usage_match = re.search(r"(\d+(?:\.\d+)?)\s*%", message)
        if usage_match:
            session.collected_slots["usage_percent"] = float(usage_match.group(1))

        lowered = message.lower()
        if "pro" in lowered:
            session.collected_slots.setdefault("product_name", "EchoMind Pro")
            session.collected_slots.setdefault("product_type", "subscription")
        elif "team" in lowered:
            session.collected_slots.setdefault("product_name", "EchoMind Team")
            session.collected_slots.setdefault("product_type", "subscription")
        elif "enterprise" in lowered:
            session.collected_slots.setdefault("product_name", "EchoMind Enterprise")
            session.collected_slots.setdefault("product_type", "service")

        if any(keyword in message for keyword in ["耳机", "录音笔", "工牌", "主机"]):
            session.collected_slots.setdefault("product_type", "hardware")

        if "年" in message or "year" in lowered:
            session.collected_slots.setdefault("plan_type", "yearly")
        elif "月" in message or "monthly" in lowered:
            session.collected_slots.setdefault("plan_type", "monthly")
        elif "季" in message or "quarterly" in lowered:
            session.collected_slots.setdefault("plan_type", "quarterly")

        if "验证码" in message or "登录" in message:
            session.collected_slots.setdefault("incident_category", "login")
        elif "导入" in message or "知识库" in message:
            session.collected_slots.setdefault("incident_category", "knowledge_base")
        elif "500" in message or "接口" in message or "api" in lowered:
            session.collected_slots.setdefault("incident_category", "api")

    async def _decide_action_with_llm(self, state: LangGraphState, session: SessionState) -> Dict[str, Any]:
        """
        用模型决定下一步动作。

        返回固定 JSON，避免自由文本导致 graph 无法稳定分支。
        """
        skill_prompt = ""
        if self._skill_manager is not None:
            skill_prompt = self._skill_manager.prompt_for(state["message"], "support")

        tool_block = self._tool_prompt_block(state.get("allowed_tools", []))
        last_tool = json.dumps(session.last_tool_result or {}, ensure_ascii=False)
        slot_text = json.dumps(session.collected_slots, ensure_ascii=False)
        history_text = json.dumps(state.get("history", [])[-4:], ensure_ascii=False)
        context_text = (state.get("context", "") or "")[:1500]

        prompt = f"""你是 EchoMind 的单 Agent 智能客服执行器。

你的任务不是随意聊天，而是为当前用户请求决定“下一步动作”。

可选动作只有四种：
1. ask_followup：信息不足，需要继续追问
2. call_tool：需要调用工具核实事实
3. handoff：无法安全继续，转人工
4. finish：当前轮可以直接给出最终答复

当前任务类型: {session.task_type}
允许使用的工具: {state.get("allowed_tools", [])}

工具说明:
{tool_block}

当前会话状态:
- 已收集槽位: {slot_text}
- 上一次工具结果: {last_tool}
- 当前用户消息: {state["message"]}
- 最近历史: {history_text}
- 辅助上下文: {context_text}

{skill_prompt}

请严格返回 JSON，不要返回其他文字。格式如下：
{{
  "action": "ask_followup | call_tool | handoff | finish",
  "response": "给用户的中文回复；如果 action=call_tool 这里可为空字符串",
  "tool_name": "如果 action=call_tool，则填写工具名，否则为空",
  "tool_args": {{}},
  "reasoning": "一句话说明为什么这样做"
}}

要求：
1. 只能从允许工具中选择 tool_name
2. 如果缺少关键信息，优先 ask_followup
3. 如果需要核实事实或规则，优先 call_tool
4. 如果问题高风险或当前无法安全继续，选择 handoff
5. 如果已经有足够信息，选择 finish 并给出简洁清晰的中文回复
6. 严禁输出 markdown、解释文字、代码块标记，只能输出一个 JSON 对象
7. 如果刚拿到工具结果，优先基于工具结果直接回答，不要忽略工具字段
8. 对退款场景：没有订单号时只能查询退款政策；有订单号时优先调用 refund_eligibility_check，避免把政策结论误说成订单事实
"""

        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=400,
            temperature=0.1,
            # 显式关闭 thinking，确保这里稳定返回普通 text block，
            # 避免 DeepSeek Anthropic 兼容层只返回 thinking 而不给最终 JSON。
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        raw = self._extract_agent_text_response(resp)
        logger.info(
            "Agent 原始输出 conv_id=%s task_type=%s raw=%s",
            session.conv_id,
            session.task_type,
            raw,
        )
        decision = self._parse_agent_decision(raw)
        logger.info(
            "Agent 解析结果 conv_id=%s task_type=%s decision=%s",
            session.conv_id,
            session.task_type,
            json.dumps(decision, ensure_ascii=False),
        )
        return self._sanitize_decision(decision, state.get("allowed_tools", []))

    def _fallback_decision(self, state: LangGraphState, session: SessionState) -> Dict[str, Any]:
        """
        当模型决策失败时使用的保守回退策略。

        这里不是主逻辑，只是保证 graph 不会因为一次 LLM 输出异常而完全不可用。
        """
        if session.task_type == TaskType.ESCALATION.value:
            return {
                "action": PendingAction.HANDOFF.value,
                "response": "好的，我先为您转人工继续处理。",
                "reasoning": "用户明确要求人工",
            }

        if session.task_type == TaskType.REFUND.value:
            if not session.last_tool_result:
                if "order_no" in session.collected_slots:
                    return {
                        "action": PendingAction.CALL_TOOL.value,
                        "tool_name": "refund_eligibility_check",
                        "tool_args": {
                            "order_no": session.collected_slots.get("order_no"),
                            "usage_percent": session.collected_slots.get("usage_percent"),
                        },
                        "response": "",
                        "reasoning": "有订单号时优先核实具体订单的退款资格",
                    }

                missing_slots: List[str] = []
                if "product_name" not in session.collected_slots:
                    missing_slots.append("产品名称")
                if "plan_type" not in session.collected_slots:
                    missing_slots.append("套餐类型")
                if missing_slots:
                    return {
                        "action": PendingAction.ASK_FOLLOWUP.value,
                        "response": f"为了帮您查询退款政策，我还需要确认：{'、'.join(missing_slots)}。",
                        "reasoning": "退款政策查询所需槽位不足",
                    }
                return {
                    "action": PendingAction.CALL_TOOL.value,
                    "tool_name": "refund_policy_lookup",
                    "tool_args": {
                        "product_name": session.collected_slots.get("product_name"),
                        "product_type": session.collected_slots.get("product_type"),
                        "plan_type": session.collected_slots.get("plan_type"),
                        "purchase_time": session.collected_slots.get("purchase_time"),
                    },
                    "response": "",
                    "reasoning": "没有订单号时先查询退款政策",
                }
            refund_reply = self._build_refund_reply_from_tool(session.last_tool_result)
            if refund_reply:
                data = session.last_tool_result.get("data") or {}
                if data.get("manual_review_required"):
                    return {
                        "action": PendingAction.HANDOFF.value,
                        "response": refund_reply,
                        "reasoning": "退款规则要求人工审核",
                    }
                return {
                    "action": PendingAction.FINISH.value,
                    "response": refund_reply,
                    "reasoning": "已基于退款工具结果生成明确答复",
                }
            return {
                "action": PendingAction.FINISH.value,
                "response": "我已经完成退款规则核实，但当前结果不完整，建议转人工进一步确认。",
                "reasoning": "已有工具结果，可先结束本轮",
            }

        if session.task_type == TaskType.ACCOUNT.value:
            if "account_id" not in session.collected_slots and "email" not in session.collected_slots:
                return {
                    "action": PendingAction.ASK_FOLLOWUP.value,
                    "response": "为了帮您核实账号状态，请提供 account_id 或注册邮箱。",
                    "reasoning": "账号问题缺少识别信息",
                }
            if not session.last_tool_result:
                return {
                    "action": PendingAction.CALL_TOOL.value,
                    "tool_name": "account_status_lookup",
                    "tool_args": {
                        "account_id": session.collected_slots.get("account_id"),
                        "email": session.collected_slots.get("email"),
                    },
                    "response": "",
                    "reasoning": "先查账号状态",
                }
            return {
                "action": PendingAction.FINISH.value,
                "response": "我已经完成账号状态核实，接下来可以根据核实结果继续处理。",
                "reasoning": "已有账号查询结果",
            }

        if session.task_type == TaskType.TECHNICAL.value:
            if "incident_category" not in session.collected_slots:
                return {
                    "action": PendingAction.ASK_FOLLOWUP.value,
                    "response": "为了更快帮您定位问题，请补充一下是登录、验证码、导入还是接口报错。",
                    "reasoning": "技术问题缺少类别信息",
                }
            if not session.last_tool_result:
                return {
                    "action": PendingAction.CALL_TOOL.value,
                    "tool_name": "incident_lookup",
                    "tool_args": {
                        "category": session.collected_slots.get("incident_category"),
                    },
                    "response": "",
                    "reasoning": "先查已知故障",
                }
            return {
                "action": PendingAction.FINISH.value,
                "response": "我已经检查了当前故障信息，建议按结果继续排查或转人工。",
                "reasoning": "已有故障查询结果",
            }

        if not session.last_tool_result and "knowledge_search" in self.allowed_tools(session.task_type):
            return {
                "action": PendingAction.CALL_TOOL.value,
                "tool_name": "knowledge_search",
                "tool_args": {"query": state["message"], "top_k": 3},
                "response": "",
                "reasoning": "通用咨询优先查知识库",
            }

        return {
            "action": PendingAction.FINISH.value,
            "response": "我已经记录您的问题，如果需要更深入处理，我也可以继续帮您转人工。",
            "reasoning": "保守结束本轮",
        }

    async def _call_allowed_tool(
        self,
        session: SessionState,
        allowed_tools: List[str],
        tool_name: str,
        params: Dict[str, Any],
        conv_id: str,
    ) -> ToolResult:
        """在白名单内执行工具，并把调用记录写入 session。"""
        if tool_name not in allowed_tools:
            return ToolResult(
                success=False,
                data=None,
                tool_name=tool_name,
                error=f"工具 {tool_name} 不在当前任务允许范围内",
            )

        result = await self._tool_manager.call(tool_name, params, context={"conv_id": conv_id})
        session.tool_history.append(
            {
                "tool_name": tool_name,
                "params": params,
                "success": result.success,
                "error": result.error,
                "cached": result.cached,
                "latency_ms": result.latency_ms,
            }
        )
        return result

    def _normalize_tool_args(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        session: SessionState,
        message: str,
    ) -> Dict[str, Any]:
        """
        规范化工具参数。

        作用：
        - 为模型遗漏的关键字段补默认值
        - 防止参数结构过于发散，影响工具调用成功率
        """
        args = dict(tool_args or {})

        if tool_name == "knowledge_search":
            args.setdefault("query", message)
            args.setdefault("top_k", 3)

        elif tool_name == "refund_policy_lookup":
            args.setdefault("product_name", session.collected_slots.get("product_name"))
            args.setdefault("product_type", session.collected_slots.get("product_type"))
            args.setdefault("plan_type", session.collected_slots.get("plan_type"))
            args.setdefault("purchase_time", session.collected_slots.get("purchase_time"))

        elif tool_name == "refund_eligibility_check":
            args.setdefault("order_no", session.collected_slots.get("order_no"))
            if "usage_percent" in session.collected_slots:
                args.setdefault("usage_percent", float(session.collected_slots.get("usage_percent", 0.0)))

        elif tool_name == "create_refund_request":
            args.setdefault("order_no", session.collected_slots.get("order_no"))
            args.setdefault("refund_reason", message)

        elif tool_name == "order_items_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))

        elif tool_name == "shipment_tracking_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))
            args.setdefault("tracking_no", session.collected_slots.get("tracking_no"))

        elif tool_name == "payment_attempt_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))

        elif tool_name == "refund_request_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))

        elif tool_name == "order_lookup":
            if "order_no" in session.collected_slots:
                args.setdefault("order_no", session.collected_slots.get("order_no"))
            if "customer_code" in session.collected_slots:
                args.setdefault("customer_code", session.collected_slots.get("customer_code"))

        elif tool_name == "account_status_lookup":
            args.setdefault("account_id", session.collected_slots.get("account_id"))
            args.setdefault("email", session.collected_slots.get("email"))

        elif tool_name == "incident_lookup":
            args.setdefault("category", session.collected_slots.get("incident_category"))

        return {k: v for k, v in args.items() if v not in (None, "", [])}

    def _tool_prompt_block(self, allowed_tools: List[str]) -> str:
        """把当前允许工具及其 schema 格式化给模型。"""
        lines: List[str] = []
        for name in allowed_tools:
            tool = self._tool_manager._tools.get(name)  # 这里直接复用已注册工具定义，避免重复维护 schema
            if tool is None:
                continue
            lines.append(
                f"- {name}: {tool.description}\n"
                f"  schema={json.dumps(tool.schema, ensure_ascii=False)}"
            )
        return "\n".join(lines) if lines else "无可用工具"

    def _parse_agent_decision(self, raw: str) -> Dict[str, Any]:
        """
        解析模型输出的 JSON。

        这里做得比简单 find("{") 更稳一些，尽量兼容：
        1. 外层包裹 ```json 代码块
        2. 前后带少量说明文字
        3. 存在多余换行
        """
        cleaned = raw.strip()
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if code_block_match:
            cleaned = code_block_match.group(1).strip()

        if cleaned.startswith("{") and cleaned.endswith("}"):
            return json.loads(cleaned)

        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

        raise ValueError(f"Agent decision returned non-JSON content: {raw}")

    @staticmethod
    def _response_claims_handoff_completed(response: str) -> bool:
        """
        判断回复是否已经在“宣称某个人工动作完成”。

        这些表述一旦出现，就必须确保 runtime 真的去执行 handoff 工具，
        否则前端和用户看到的内容会与真实系统状态不一致。
        """
        text = (response or "").strip()
        if not text:
            return False

        completed_keywords = [
            "已为您创建工单",
            "已经为您创建工单",
            "我已为您创建工单",
            "已帮您创建工单",
            "已为您创建转人工工单",
            "已经为您创建转人工工单",
            "我已为您创建转人工工单",
            "已为您转人工",
            "已经为您转人工",
            "我已为您转人工",
            "马上为您转接人工",
            "客服将尽快处理",
        ]
        return any(keyword in text for keyword in completed_keywords)

    def _should_force_handoff(self, session: SessionState, response: str) -> bool:
        """
        判断当前这一轮是否必须改写成 handoff。

        这里不只看回复文案，还看已经拿到的工具事实，避免模型“逻辑上应该转人工，
        但动作却给了 finish”。
        """
        if self._response_claims_handoff_completed(response):
            return True

        last_tool_result = session.last_tool_result or {}
        data = last_tool_result.get("data") or {}
        tool_name = last_tool_result.get("tool_name")

        # 退款规则要求人工审核时，必须真正走 handoff，不能只在 finish 里口头说明。
        if (
            session.task_type == TaskType.REFUND.value
            and tool_name in {"refund_policy_lookup", "refund_eligibility_check"}
            and isinstance(data, dict)
            and data.get("manual_review_required")
        ):
            return True

        return False

    def _extract_agent_text_response(self, response: Any) -> str:
        """
        只从真正的 text block 里取模型回复。

        这样可以避免 DeepSeek Anthropic 兼容层在包含 thinking block 时，
        把某个内部 block id 误识别成最终文本输出。
        """
        parts: List[str] = []
        block_summaries: List[str] = []
        content = getattr(response, "content", None)

        if isinstance(content, str) and content.strip():
            return content.strip()

        for block in self._iter_response_blocks(content):
            if isinstance(block, dict):
                block_type = str(block.get("type", "") or "")
                text = block.get("text")
            else:
                block_type = str(getattr(block, "type", "") or "")
                text = getattr(block, "text", None)

            block_summaries.append(block_type or type(block).__name__)
            if block_type == "text" and isinstance(text, str) and text.strip():
                parts.append(text.strip())

        logger.info("Agent content blocks=%s", block_summaries)

        if parts:
            return "\n".join(parts).strip()

        raise ValueError(f"Agent response 未包含可解析的 text block，content_types={block_summaries}")

    def _sanitize_decision(self, decision: Dict[str, Any], allowed_tools: List[str]) -> Dict[str, Any]:
        """
        对模型返回结果做最后一道规范化，减少脏输出直接击穿主链路。
        """
        normalized = dict(decision or {})
        action = str(normalized.get("action", "") or "").strip().lower()
        valid_actions = {
            PendingAction.ASK_FOLLOWUP.value,
            PendingAction.CALL_TOOL.value,
            PendingAction.HANDOFF.value,
            PendingAction.FINISH.value,
        }
        if action not in valid_actions:
            raise ValueError(f"Agent action 非法: {action}")

        normalized["action"] = action
        normalized["response"] = str(normalized.get("response", "") or "").strip()
        normalized["reasoning"] = str(normalized.get("reasoning", "") or "").strip()
        normalized["tool_name"] = str(normalized.get("tool_name", "") or "").strip()
        normalized["tool_args"] = normalized.get("tool_args") or {}

        if action == PendingAction.CALL_TOOL.value:
            if normalized["tool_name"] not in allowed_tools:
                raise ValueError(f"Agent tool_name 不在白名单内: {normalized['tool_name']}")
            if not isinstance(normalized["tool_args"], dict):
                raise ValueError("Agent tool_args 不是对象")
        else:
            normalized["tool_name"] = ""
            normalized["tool_args"] = {}

        return normalized

    def _build_refund_reply_from_tool(self, last_tool_result: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        根据退款工具真实返回值构造用户回复。

        目标：
        1. 避免 fallback 只说空泛结论
        2. 让用户能明确看到“系统核实了什么事实”
        """
        if not last_tool_result or not isinstance(last_tool_result, dict):
            return None

        data = last_tool_result.get("data")
        if not isinstance(data, dict):
            return None

        tool_name = last_tool_result.get("tool_name")

        if tool_name == "refund_policy_lookup":
            if not data.get("matched"):
                message = data.get("message") or "暂未匹配到退款政策，建议转人工进一步核实。"
                return f"我已经查询了退款政策，但{message}"

            allow_refund = "支持退款" if data.get("allow_refund") else "不支持退款"
            refund_window = data.get("refund_window_hours")
            usage_limit = data.get("usage_limit_percent")
            manual_review_required = data.get("manual_review_required")

            lines = [f"我已经帮您查询退款政策：当前规则{allow_refund}。"]
            if refund_window is not None:
                lines.append(f"退款时效窗口为购买后 {refund_window} 小时内。")
            if usage_limit is not None:
                lines.append(f"规则允许的最高使用比例为 {usage_limit}%。")
            if manual_review_required:
                lines.append("该类订单退款需要人工审核。")
            return "".join(lines)

        if tool_name == "refund_eligibility_check":
            if not data.get("order_found"):
                return data.get("message") or "未找到对应订单，暂时无法核实退款资格。"

            lines = [data.get("summary") or f"我已经帮您核实订单 {data.get('order_no')} 的退款资格。"]
            if data.get("eligible"):
                lines.append("当前这笔订单具备发起退款申请的基础条件。")
            else:
                lines.append("当前这笔订单暂不满足直接退款条件。")
            if data.get("manual_review_required"):
                lines.append("该订单退款需要人工审核。")
            if data.get("already_requested") and data.get("existing_refund_no"):
                lines.append(f"系统中已经存在退款申请 {data.get('existing_refund_no')}。")
            return "".join(lines)

        return None

    @staticmethod
    def _iter_response_blocks(content: Any) -> List[Any]:
        """把响应 content 安全转成列表，便于逐个 block 检查。"""
        if content is None:
            return []
        if isinstance(content, list):
            return content
        try:
            return list(content)
        except TypeError:
            return [content]

    @staticmethod
    def _compact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
        """删除值为 None 的字段，避免可选参数在 schema 校验时出现类型错误。"""
        return {key: value for key, value in data.items() if value is not None}

    @staticmethod
    def _queue_for_task_type(task_type: str) -> str:
        """根据粗粒度任务类型选择人工队列。"""
        if task_type in {TaskType.REFUND.value, TaskType.BILLING.value}:
            return "billing_ops"
        if task_type == TaskType.ACCOUNT.value:
            return "account_ops"
        if task_type == TaskType.TECHNICAL.value:
            return "technical_support"
        return "general_support"
