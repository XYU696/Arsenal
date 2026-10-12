"""
Phase 1 单 Agent Runtime（LangGraph Graph API 版本）。

设计目标：
1. 用 LangGraph 替代手写状态机，让 Agent 形成真正的“思考 -> 动作 -> 观察 -> 再思考”循环。
2. 保留当前业务数据库工具、Redis 状态存储和 Skills 机制，避免推倒重来。
3. 保持实现简单：先做单 Agent、受控 ReAct、有限动作、有限工具。
"""
import json  # JSON 序列化 / 反序列化
import logging  # 日志记录
import re  # 正则表达式（槽位提取、决策解析）
import time  # 耗时统计
from dataclasses import dataclass, field  # 数据类
from typing import Any, Dict, List, Optional, Tuple, TypedDict  # 类型注解

from core.intent_recognizer import IntentCategory, IntentRecognizer  # 意图识别器与意图分类
from mcp.tool_manager import MCPToolManager, ToolResult  # 工具管理器与工具结果类型

from .session_state import PendingAction, SessionState, SessionStateStore, TaskState, TaskType  # 会话状态相关类型

try:
    # LangGraph 是当前版本的核心依赖；如果环境未安装，启动时给出清晰报错。
    from langgraph.graph import END, START, StateGraph
except ImportError as ex:  # pragma: no cover - 运行时依赖检查
    # 未安装 LangGraph：将类引用置空，运行时给出明确报错
    END = START = StateGraph = None  # type: ignore
    # 暂存导入异常，供 __init__ 抛原始错误信息
    _LANGGRAPH_IMPORT_ERROR = ex
else:
    # 导入成功：异常标记置空
    _LANGGRAPH_IMPORT_ERROR = None


logger = logging.getLogger(__name__)  # 模块级日志记录器


@dataclass
class RuntimeStepResult:
    '''
    Runtime 单轮执行结果。

    封装了 LangGraph Agent 执行一次完整对话轮次后的所有输出信息，
    包括响应文本、任务状态、工具使用情况、转人工信息和执行轨迹等。

    这里的”一次 step”是指：从接收用户本轮消息开始，到 Agent 完成本轮循环退出为止。
    '''

    response: str  # Agent 返回给用户的响应文本
    task_type: str  # 任务类型（咨询、投诉、建单等）
    task_state: str  # 任务状态（进行中、已完成、等待确认等）
    pending_action: str  # 待执行的动作（如等待用户确认、等待补充信息等）
    used_tools: List[Dict[str, Any]] = field(default_factory=list)  # 本轮调用的工具列表（包含工具名、参数、结果）
    requires_handoff: bool = False  # 是否需要转人工
    final_outcome: Optional[str] = None  # 最终结果（已解决、已转人工、已创建工单等）
    ticket_no: Optional[str] = None  # 工单号（如果已创建工单）
    handoff_queue: Optional[str] = None  # 转人工队列名称（如果已转人工）
    # Trace V1：本轮 Agent 执行轨迹（节点 -> 决策 -> 工具调用 -> 结果）
    steps: List[Dict[str, Any]] = field(default_factory=list)  # 执行轨迹列表，记录每个节点的输入输出


class LangGraphState(TypedDict, total=False):
    """
    LangGraph 内部状态定义。

    本类定义了 LangGraph Agent 执行过程中的所有状态字段，用于在各个节点之间传递和更新信息。

    注意：
    - `session` 是持久化到 Redis 的核心状态
    - 其他字段主要服务于当前一次 graph 执行，不一定长期持久化
    """

    user_id: str  # 用户唯一标识
    conv_id: str  # 会话唯一标识
    message: str  # 用户当前输入的消息
    context: str  # 记忆上下文（用户画像、历史片段等）
    history: List[Dict[str, str]]  # 最近几轮对话历史
    session: Dict[str, Any]  # 持久化的会话状态（SessionState 序列化后的字典）
    task_type: str  # 任务类型（咨询、投诉、建单等）
    allowed_tools: List[str]  # 当前任务类型允许使用的工具列表
    task_flow_name: str  # 当前执行的任务流名称
    loop_count: int  # 当前 graph 循环次数（防止死循环）
    last_action: str  # 上一个节点执行的动作
    response: str  # Agent 生成的响应文本
    tool_name: str  # 当前调用的工具名称
    tool_args: Dict[str, Any]  # 当前工具调用的参数
    tool_result: Dict[str, Any]  # 当前工具调用的返回结果
    current_turn_tools: List[Dict[str, Any]]  # 本轮对话中已调用的所有工具记录
    final_response: str  # 最终返回给用户的响应文本
    final_outcome: str  # 最终结果（已解决、已转人工、已创建工单等）
    requires_handoff: bool  # 是否需要转人工
    ticket_no: str  # 工单号（如果已创建工单）
    handoff_queue: str  # 转人工队列名称（如果已转人工）
    trace: List[Dict[str, Any]]  # 执行轨迹（记录每个节点的执行过程）


class SingleAgentRuntime:
    """
    单 Agent、受控 ReAct、基于 LangGraph 的 runtime。

    与上一版状态机实现相比，这一版的核心变化是：
    - 不再由程序完全写死每一步
    - 让 Agent 自主决定下一步动作
    - 通过 Graph 明确循环边界和退出路径
    """

    # LangGraph 最大循环次数限制，防止死循环
    MAX_GRAPH_LOOPS = 4

    # 粗粒度任务允许使用的工具白名单。
    # 键：任务类型；值：该任务类型下允许调用的工具名称列表
    TOOL_WHITELIST: Dict[str, List[str]] = {
        # FAQ 增加物流查询：纯物流咨询（无退款关键词）会被分类到这里
        TaskType.FAQ.value: [ #常用工具
            "knowledge_search",  # 知识库检索工具
            "shipment_tracking_lookup"  # 物流追踪查询工具
        ],
        TaskType.REFUND.value: [
            "order_lookup",  # 订单查询工具
            "order_items_lookup",  # 订单明细查询工具
            "payment_attempt_lookup",  # 支付记录查询工具
            "shipment_tracking_lookup",  # 物流追踪查询工具
            "refund_policy_lookup",  # 退款政策查询工具
            "refund_eligibility_check",  # 退款资格检查工具
            "refund_request_lookup",  # 退款请求查询工具
            "create_refund_request",  # 创建退款请求工具
            "create_handoff_ticket",  # 创建转人工工单工具
        ],
        TaskType.ACCOUNT.value: [
            "account_status_lookup",  # 账户状态查询工具
            "create_handoff_ticket"  # 创建转人工工单工具
        ],
        TaskType.TECHNICAL.value: [
            "incident_lookup",  # 故障事件查询工具
            "knowledge_search",  # 知识库检索工具
            "create_handoff_ticket"  # 创建转人工工单工具
        ],
        TaskType.BILLING.value: [
            "order_lookup",  # 订单查询工具
            "order_items_lookup",  # 订单明细查询工具
            "payment_attempt_lookup",  # 支付记录查询工具
            "shipment_tracking_lookup",  # 物流追踪查询工具
            "refund_policy_lookup",  # 退款政策查询工具
            "refund_eligibility_check",  # 退款资格检查工具
            "refund_request_lookup",  # 退款请求查询工具
            "create_refund_request",  # 创建退款请求工具
            "create_handoff_ticket",  # 创建转人工工单工具
        ],
        TaskType.ESCALATION.value: [
            "create_handoff_ticket"  # 创建转人工工单工具（升级类任务直接转人工）
        ],
        TaskType.UNKNOWN.value: [
            "knowledge_search",  # 知识库检索工具（尝试从知识库找答案）
            "create_handoff_ticket"  # 创建转人工工单工具（无法识别时可转人工）
        ],
    }

    # 每个粗粒度任务对应的说明性流名称，便于调试与监控。
    # 键：任务类型；值：该任务类型对应的 LangGraph 流程名称
    TASK_FLOW_NAMES: Dict[str, str] = {
        TaskType.FAQ.value: "faq_graph_flow",  # FAQ 咨询流程
        TaskType.REFUND.value: "refund_graph_flow",  # 退款处理流程
        TaskType.ACCOUNT.value: "account_issue_graph_flow",  # 账户问题处理流程
        TaskType.TECHNICAL.value: "technical_issue_graph_flow",  # 技术问题处理流程
        TaskType.BILLING.value: "billing_issue_graph_flow",  # 账单问题处理流程
        TaskType.ESCALATION.value: "escalation_graph_flow",  # 升级转人工流程
        TaskType.UNKNOWN.value: "fallback_graph_flow",  # 未知类型兜底流程
    }

    def __init__(
        self,
        recognizer: IntentRecognizer,
        tool_manager: MCPToolManager,
        state_store: SessionStateStore,
        skill_manager: Optional[Any] = None,
    ):
        # 未安装 langgraph 时拒绝启动，给出清晰指引
        if StateGraph is None:
            raise RuntimeError(  # 抛出带指引的运行时错误
                "未安装 langgraph，无法使用 LangGraph 版 runtime。"  # 错误主因
                "请先安装 requirements.txt 中的 langgraph 依赖。"  # 解决方法
            ) from _LANGGRAPH_IMPORT_ERROR  # 关联原始导入异常

        self._recognizer = recognizer  # 保存意图识别器
        self._tool_manager = tool_manager  # 保存工具管理器
        self._state_store = state_store  # 保存会话状态存储（Redis）
        self._skill_manager = skill_manager  # 保存技能管理器（可为 None）
        self._client = recognizer.client  # 复用识别器内部的 LLM 客户端
        self._model = recognizer.model  # 复用识别器使用的模型名
        self._graph = self._build_graph()  # 预编译 LangGraph，避免每次执行重复构建

    async def step(
        self,
        user_id: str,  # 用户唯一标识
        conv_id: str,  # 会话唯一标识
        message: str,  # 用户当前输入的消息
        context: str = "",  # 记忆上下文（用户画像、历史片段等）
        history: Optional[List[Dict[str, str]]] = None,  # 最近几轮对话历史
    ) -> Tuple[SessionState, RuntimeStepResult]:
        """
        执行一次完整的 LangGraph Agent loop。

        注意：
        这里不是只执行 graph 的一个节点，而是让 graph 按条件边循环，
        直到当前轮应该退出为止。
        """
        # 构建 LangGraph 的初始状态字典
        initial_state: LangGraphState = {
            "user_id": user_id,  # 传入用户 ID
            "conv_id": conv_id,  # 传入会话 ID
            "message": message,  # 传入用户消息
            "context": context, # 记忆上下文
            "history": history or [], # 最近几次对话
            "current_turn_tools": [],  # 初始化本轮工具调用记录为空列表
        }
        # 异步调用 LangGraph 执行完整流程，返回最终状态
        output = await self._graph.ainvoke(initial_state)
        # 从输出中提取 session 字段并反序列化为 SessionState 对象
        session = SessionState.from_dict(output["session"])
        # 构建 RuntimeStepResult 结果对象
        result = RuntimeStepResult(
            response=output.get("final_response") or output.get("response") or session.last_assistant_message,  # 优先使用 final_response，其次 response，最后用 session 中的最后一条助手消息
            task_type=session.task_type,  # 任务类型（咨询、投诉、建单等）
            task_state=session.task_state,  # 任务状态（进行中、已完成等）
            pending_action=session.pending_action,  # 待执行的动作（如等待用户确认）
            used_tools=output.get("current_turn_tools", []),  # 本轮调用的工具列表
            requires_handoff=session.requires_handoff,  # 是否需要转人工
            final_outcome=session.final_outcome,  # 最终结果（已解决、已转人工等）
            ticket_no=output.get("ticket_no"),  # 工单号（如果已创建工单）
            handoff_queue=output.get("handoff_queue"),  # 转人工队列（如果已转人工）
            steps=output.get("trace", []),  # 执行轨迹（每个节点的执行记录）
        )
        # 返回会话状态和步骤结果
        return session, result

    def allowed_tools(self, task_type: str) -> List[str]:
        """返回某类任务允许使用的工具。"""
        # 命中白名单返回对应工具，未知类型用 UNKNOWN 的兜底工具
        return self.TOOL_WHITELIST.get(task_type, self.TOOL_WHITELIST[TaskType.UNKNOWN.value])

    def task_flow_name(self, task_type: str) -> str:
        """返回某类任务对应的 flow 名称。"""
        # 命中映射返回对应流程名，未知类型用 fallback 流程名兜底
        return self.TASK_FLOW_NAMES.get(task_type, self.TASK_FLOW_NAMES[TaskType.UNKNOWN.value])

    def _build_graph(self):
        """构建 LangGraph 状态图。

        创建并配置 Agent 的执行流程图，定义各个节点及其之间的转换关系。
        执行流程：加载状态 -> 分类/恢复 -> Agent 决策 -> (工具调用/转人工) -> 保存状态
        """
        # 创建状态图实例，使用 LangGraphState 作为状态类型
        graph = StateGraph(LangGraphState)

        # 添加各个处理节点
        graph.add_node("load_state", self._load_state_node)  # 加载持久化状态节点（从 Redis 读取会话状态）
        graph.add_node("classify_or_resume", self._classify_or_resume_node)  # 任务分类或恢复节点（识别任务类型或恢复上一轮状态）
        graph.add_node("agent_node", self._agent_node)  # Agent 决策节点（调用 LLM 进行推理和决策）
        graph.add_node("tool_node", self._tool_node)  # 工具调用节点（执行具体的工具调用）
        graph.add_node("handoff_node", self._handoff_node)  # 转人工节点（创建转人工工单）
        graph.add_node("save_state", self._save_state_node)  # 保存状态节点（将会话状态持久化到 Redis）

        # 添加固定边：定义节点之间的直接转换关系
        graph.add_edge(START, "load_state")  # 从起始点进入加载状态节点
        graph.add_edge("load_state", "classify_or_resume")  # 加载完状态后进入分类/恢复节点
        graph.add_edge("classify_or_resume", "agent_node")  # 分类/恢复后进入 Agent 决策节点

        # 添加条件边：根据 Agent 决策结果动态选择下一个节点
        graph.add_conditional_edges(
            "agent_node",  # 从 agent_node 节点出发
            self._route_after_agent,  # 使用此函数决定下一步路由
            {
                "tool": "tool_node",  # 如果需要调用工具，进入工具节点
                "handoff": "handoff_node",  # 如果需要转人工，进入转人工节点
                "save": "save_state",  # 如果任务完成，进入保存状态节点
            },
        )

        # 工具调用后返回 Agent 节点继续决策（形成循环）
        graph.add_edge("tool_node", "agent_node")
        # 转人工后进入保存状态节点
        graph.add_edge("handoff_node", "save_state")
        # 保存状态后结束流程
        graph.add_edge("save_state", END)

        # 编译并返回可执行的图
        return graph.compile()

    def _append_trace(
        self,
        state: LangGraphState,  # 当前 LangGraph 状态
        *,
        node: str,  # 当前执行的节点名称（如 "agent_node"、"tool_node" 等）
        decision: str,  # 节点的决策结果（如 "call_tool"、"handoff"、"completed" 等）
        start_ts: float,  # 节点开始执行的时间戳（用于计算延迟）
        reasoning: str = "",  # Agent 的推理过程（LLM 返回的思考内容）
        tool_name: str = "",  # 调用的工具名称（如果本步涉及工具调用）
        tool_args: Optional[Dict[str, Any]] = None,  # 工具调用的参数（如果本步涉及工具调用）
        tool_result: Optional[Dict[str, Any]] = None,  # 工具调用的返回结果（如果本步涉及工具调用）
        detail: str = "",  # 额外的详细信息（如 "step_count=3"）
        loop_count: Optional[int] = None,  # 当前循环次数（如果未提供则从 state 中获取）
    ) -> List[Dict[str, Any]]:
        """
        向执行轨迹追加一步并返回新列表。

        每次节点执行都会调用一次，用于记录 Agent 的执行过程，便于调试和前端展示。
        生成的轨迹格式与前端 TraceStep 字段对齐，latency_ms 由节点起始时间自动计算。

        参数：
            state: 当前 LangGraph 状态
            node: 节点名称
            decision: 决策结果
            start_ts: 节点开始时间戳
            reasoning: 推理过程（可选）
            tool_name: 工具名称（可选）
            tool_args: 工具参数（可选）
            tool_result: 工具结果（可选）
            detail: 额外详情（可选）
            loop_count: 循环次数（可选）

        返回：
            更新后的轨迹列表
        """
        # 从当前状态中获取已有的轨迹列表，复制一份避免直接修改
        steps = list(state.get("trace", []))

        # 追加新的轨迹步骤
        steps.append(
            {
                "node": node,  # 节点名称
                "decision": decision,  # 决策结果
                "reasoning": reasoning,  # 推理过程
                "tool_name": tool_name,  # 工具名称
                "tool_args": tool_args or {},  # 工具参数（空时使用空字典）
                "tool_result": tool_result or {},  # 工具结果（空时使用空字典）
                "detail": detail,  # 详细信息
                "latency_ms": round((time.perf_counter() - start_ts) * 1000, 1),  # 计算本步骤耗时（毫秒，保留1位小数）
                "loop_count": loop_count if loop_count is not None else int(state.get("loop_count", 0)),  # 循环次数
            }
        )

        # 返回更新后的轨迹列表
        return steps

    @staticmethod
    def _truncate_trace_value(value: Any, limit: int = 500) -> Any:
        """工具参数/结果过大时截断，避免 trace 响应体过大。"""
        # 先把任意值序列化为 JSON 字符串（不可序列化的对象用 str 兜底）
        text = json.dumps(value, ensure_ascii=False, default=str)
        # 未超过长度上限时原样返回
        if len(text) <= limit:
            return value
        # 超限时截断到 limit，并标注被省略的字符数
        return text[:limit] + f"...(截断 {len(text) - limit} 字符)"

    async def _load_state_node(self, state: LangGraphState) -> LangGraphState:
        """
        加载状态节点：读取或创建会话状态。

        从 Redis 中加载已有的会话状态，如果不存在则创建新的会话。
        这一层仍然复用 Redis，LangGraph 负责循环逻辑，Redis 负责跨轮持久化。

        参数：
            state: 当前 LangGraph 状态（包含 user_id、conv_id、message 等）

        返回：
            更新后的 LangGraphState（包含加载的 session、初始化的循环计数等）
        """
        # 记录节点开始时间戳，用于性能追踪
        start_ts = time.perf_counter()

        # 从 Redis 加载已有会话状态，或创建新会话
        session = self._state_store.load_or_create(
            user_id=state["user_id"],  # 用户 ID
            conv_id=state["conv_id"],  # 会话 ID
        )

        # 增加步骤计数（记录本会话已执行的轮次）
        session.step_count += 1
        # 更新用户最后一条消息
        session.last_user_message = state["message"]
        # 设置当前 Agent 类型为 support_graph
        session.current_agent = "support_graph"

        # 追加执行轨迹记录
        trace = self._append_trace(
            state,  # 当前状态
            node="load_state",  # 节点名称
            decision="loaded_session",  # 决策结果
            start_ts=start_ts,  # 开始时间戳
            detail=f"step_count={session.step_count}",  # 详细信息（当前步骤数）
        )

        # 返回更新后的状态
        return {
            "session": session.to_dict(),  # 将 session 对象序列化为字典
            "loop_count": 0,  # 初始化循环计数为 0
            "current_turn_tools": [],  # 初始化本轮工具调用列表为空
            "requires_handoff": False,  # 初始化转人工标志为 False
            "trace": trace,  # 追加的执行轨迹
        }

    async def _classify_or_resume_node(self, state: LangGraphState) -> LangGraphState:
        """
        分类或恢复节点：对新任务做粗粒度分类；对旧任务则恢复上下文。

        判断当前请求是新任务还是多轮对话的延续，新任务需要重新分类任务类型，
        旧任务则恢复之前的上下文继续处理。

        这里保留粗粒度分类，不是为了“再做一个路由器”，
        而是为了给 LangGraph Agent 一个更稳定的任务上下文和工具白名单。

        参数：
            state: 当前 LangGraph 状态

        返回：
            更新后的 LangGraphState（包含任务类型、允许的工具列表等）
        """
        start_ts = time.perf_counter() # 记录节点开始时间戳，用于性能追踪
        session = SessionState.from_dict(state["session"])  # 从状态字典中反序列化 session 对象

        # 新任务、未知任务或上一任务已完成时，重新分类并重置关键字段。
        #判断是否需要重新分类
        if session.task_type == TaskType.UNKNOWN.value or session.task_state == TaskState.COMPLETED.value:
            session.task_type = await self._classify_task_type(  # 调用 意图识别器 对用户消息进行任务类型分类   
                state["message"],# 用户当前消息
                history=state.get("history"),# 对话历史（用于上下文理解）
            )
            session.task_state = TaskState.NEW_REQUEST.value  # 重置任务状态为新请求
            session.pending_action = PendingAction.ASK_FOLLOWUP.value  # 重置待执行动作为追问
            session.requires_handoff = False  # 重置转人工标志
            session.handoff_reason = None    # 清空转人工原因
            session.final_outcome = None  # 清空最终结果
            session.last_tool_result = None   # 清空上次工具调用结果
            decision = "classify"  # 标记决策为分类（新任务）
        else: 
            decision = "resume"

        # 每轮都尝试做一次轻量槽位提取，减少不必要的工具空调用。
        self._extract_slots(session, state["message"])  # 从用户消息中提取结构化信息（如订单号、退款原因等）
        allowed_tools = self.allowed_tools(session.task_type)  # 根据任务类型获取允许使用的工具列表
        trace = self._append_trace( # 追加执行轨迹记录
            state, # 当前状态
            node="classify_or_resume",  # 节点名称
            decision=decision, # 决策结果（classify 或 resume）
            start_ts=start_ts, # 开始时间戳
            detail=(
                f"task_type={session.task_type} "# 任务类型
                f"flow={self.task_flow_name(session.task_type)} "# 任务流名称
                f"tools={len(allowed_tools)}"# 可用工具数量
            ),
        )
        return {
            "session": session.to_dict(), # 序列化 session 对象
            "task_type": session.task_type, # 任务类型
            "allowed_tools": allowed_tools, # 允许使用的工具列表
            "task_flow_name": self.task_flow_name(session.task_type), # 任务流名称
            "trace": trace, # 执行轨迹
        }

    async def _agent_node(self, state: LangGraphState) -> LangGraphState:
        '''
        Agent 核心决策节点。

        这是 LangGraph 流程的核心，由 LLM 根据上下文决定下一步动作：
        - ask_followup: 追问用户以获取更多信息
        - call_tool: 调用工具获取数据
        - handoff: 转人工处理
        - finish: 任务完成，结束对话

        如果 LLM 决策失败，会回退到代码内置的保守策略。
        同时包含业务规则约束，确保决策的一致性（如声称建单则必须执行）。

        参数：
            state: 当前 LangGraph 状态（包含 session、task_type、history 等）

        返回：
            更新后的 LangGraphState（包含决策结果、响应文本、下一步动作等）
        '''
        # 从状态中反序列化 session 对象
        session = SessionState.from_dict(state["session"])
        # 增加循环计数（从 state 中获取并加 1）
        loop_count = int(state.get('loop_count', 0)) + 1
        # 记录节点开始时间戳，用于性能追踪
        start_ts = time.perf_counter()

        # 防止单轮 graph 内无限循环。
        # 如果循环次数超过最大限制，强制转人工
        if loop_count > self.MAX_GRAPH_LOOPS:
            # 设置任务状态为需要转人工
            session.task_state = TaskState.NEEDS_HANDOFF.value
            # 设置待执行动作为转人工
            session.pending_action = PendingAction.HANDOFF.value
            # 标记需要转人工
            session.requires_handoff = True
            # 记录转人工原因为循环超限
            session.handoff_reason = "graph_loop_limit_exceeded"
            # 追加执行轨迹
            trace = self._append_trace(
                state,  # 当前状态
                node="agent",  # 节点名称
                decision=PendingAction.HANDOFF.value,  # 决策为转人工
                start_ts=start_ts,  # 开始时间戳
                loop_count=loop_count,  # 循环次数
                detail="循环超限，强制转人工",  # 详细说明
            )
            # 返回转人工的状态
            return {
                "session": session.to_dict(),  # 序列化 session
                "loop_count": loop_count,  # 循环次数
                "last_action": PendingAction.HANDOFF.value,  # 最后动作为转人工
                "response": "为了避免重复尝试，我先为您转人工继续处理。",  # 用户可见的响应
                "requires_handoff": True,  # 标记需要转人工
                "trace": trace,  # 执行轨迹
            }

        # decision_source 标记本次决策来源，便于 trace 排查：llm / fallback / rule_override
        # 初始化决策来源为 LLM
        decision_source = "llm"
        try:
            # 调用 LLM 进行决策（根据上下文判断下一步动作）
            decision = await self._decide_action_with_llm(state, session)
        except Exception as ex:
            # LLM 决策失败时，使用更稳妥的保守回退逻辑，避免主链路直接崩。
            # 记录异常日志
            logger.exception(
                "Agent LLM 决策失败，进入 fallback。conv_id=%s task_type=%s error=%s",
                session.conv_id,  # 会话 ID
                session.task_type,  # 任务类型
                ex,  # 异常信息
            )
            # 调用兜底决策逻辑
            decision = self._fallback_decision(state, session)
            # 更新决策来源为 fallback
            decision_source = "fallback"

        # 从决策结果中提取动作类型（默认为 finish）
        action = decision.get("action", PendingAction.FINISH.value)
        # 从决策结果中提取响应文本并去除首尾空格
        response = str(decision.get("response", "") or "").strip()
        # 从决策结果中提取工具名称并去除首尾空格
        tool_name = str(decision.get("tool_name", "") or "").strip()
        # 从决策结果中提取工具参数（默认为空字典）
        tool_args = decision.get("tool_args") or {}
        # 从决策结果中提取推理过程
        reasoning = str(decision.get("reasoning", "") or "")

        # 对 LLM 决策做最后一步业务一致性约束：
        # 1. 如果回复里声称”已经转人工/已经建单”，就必须真的执行 handoff
        # 2. 如果退款规则明确要求人工审核，也不能直接 finish，必须进入 handoff
        
        # 检查是否需要强制转人工（决策为 finish 但业务规则要求转人工）
        if action == PendingAction.FINISH.value and self._should_force_handoff(session, response):
            # 记录决策被改写的日志
            logger.info(
                "Agent 决策被一致性规则改写为 handoff。conv_id=%s task_type=%s",
                session.conv_id,  # 会话 ID
                session.task_type,  # 任务类型
            )
            # 强制改写动作为转人工
            action = PendingAction.HANDOFF.value
            # 更新决策来源为规则覆盖
            decision_source = "rule_override"
            # 如果没有响应文本，设置默认响应
            if not response:
                response = "我先为您转人工继续处理。"

        # 处理追问动作（需要用户补充更多信息）
        if action == PendingAction.ASK_FOLLOWUP.value:
            # 设置任务状态为需要澄清
            session.task_state = TaskState.NEED_CLARIFICATION.value
            # 设置待执行动作为追问
            session.pending_action = PendingAction.ASK_FOLLOWUP.value
            # 记录助手最后一条消息
            session.last_assistant_message = response
            # 追加执行轨迹
            trace = self._append_trace(
                state,  # 当前状态
                node="agent",  # 节点名称
                decision=action,  # 决策为追问
                start_ts=start_ts,  # 开始时间戳
                loop_count=loop_count,  # 循环次数
                reasoning=reasoning,  # 推理过程
                detail=decision_source,  # 决策来源
            )
            # 返回追问的状态
            return {
                "session": session.to_dict(),  # 序列化 session
                "loop_count": loop_count,  # 循环次数
                "last_action": action,  # 最后动作为追问
                "response": response,  # 追问的内容
                "final_response": response,  # 最终返回给用户的响应
                "trace": trace,  # 执行轨迹
            }

        # 处理调用工具动作
        if action == PendingAction.CALL_TOOL.value:
            # 标准化工具参数（补充缺失的参数、校验格式等）
            normalized_args = self._normalize_tool_args(tool_name, tool_args, session, state["message"])
            # 设置任务状态为等待工具结果
            session.task_state = TaskState.WAITING_TOOL_RESULT.value
            # 设置待执行动作为调用工具
            session.pending_action = PendingAction.CALL_TOOL.value
            # 追加执行轨迹
            trace = self._append_trace(
                state,  # 当前状态
                node="agent",  # 节点名称
                decision=action,  # 决策为调用工具
                start_ts=start_ts,  # 开始时间戳
                loop_count=loop_count,  # 循环次数
                reasoning=reasoning,  # 推理过程
                tool_name=tool_name,  # 工具名称
                tool_args=normalized_args,  # 标准化后的工具参数
                detail=decision_source,  # 决策来源
            )
            # 返回调用工具的状态
            return {
                "session": session.to_dict(),  # 序列化 session
                "loop_count": loop_count,  # 循环次数
                "last_action": action,  # 最后动作为调用工具
                "tool_name": tool_name,  # 工具名称
                "tool_args": normalized_args,  # 工具参数
                "trace": trace,  # 执行轨迹
            }

        # 处理转人工动作
        if action == PendingAction.HANDOFF.value:
            # 设置任务状态为需要转人工
            session.task_state = TaskState.NEEDS_HANDOFF.value
            # 设置待执行动作为转人工
            session.pending_action = PendingAction.HANDOFF.value
            # 标记需要转人工
            session.requires_handoff = True
            # 记录转人工原因（从决策中提取，默认为 agent 请求转人工）
            session.handoff_reason = str(decision.get("reasoning", "") or "agent_requested_handoff")
            # 记录助手最后一条消息
            session.last_assistant_message = response
            # 追加执行轨迹
            trace = self._append_trace(
                state,  # 当前状态
                node="agent",  # 节点名称
                decision=action,  # 决策为转人工
                start_ts=start_ts,  # 开始时间戳
                loop_count=loop_count,  # 循环次数
                reasoning=reasoning,  # 推理过程
                detail=decision_source,  # 决策来源
            )
            # 返回转人工的状态
            return {
                "session": session.to_dict(),  # 序列化 session
                "loop_count": loop_count,  # 循环次数
                "last_action": action,  # 最后动作为转人工
                "response": response,  # 转人工时的响应文本
                "requires_handoff": True,  # 标记需要转人工
                "trace": trace,  # 执行轨迹
            }

        # 默认把剩余情况都视作 finish。
        # 设置任务状态为已完成
        session.task_state = TaskState.COMPLETED.value
        # 设置待执行动作为结束
        session.pending_action = PendingAction.FINISH.value
        # 设置最终结果为 agent 完成
        session.final_outcome = "agent_finished"
        # 记录助手最后一条消息
        session.last_assistant_message = response
        # 追加执行轨迹
        trace = self._append_trace(
            state,  # 当前状态
            node="agent",  # 节点名称
            decision=PendingAction.FINISH.value,  # 决策为结束
            start_ts=start_ts,  # 开始时间戳
            loop_count=loop_count,  # 循环次数
            reasoning=reasoning,  # 推理过程
            detail=decision_source,  # 决策来源
        )
        # 返回任务完成的状态
        return {
            "session": session.to_dict(),  # 序列化 session
            "loop_count": loop_count,  # 循环次数
            "last_action": PendingAction.FINISH.value,  # 最后动作为结束
            "response": response,  # 最终响应文本
            "final_response": response,  # 最终返回给用户的响应
            "final_outcome": session.final_outcome,  # 最终结果
            "trace": trace,  # 执行轨迹
        }

    async def _tool_node(self, state: LangGraphState) -> LangGraphState:
        """
        工具执行节点。

        负责执行 Agent 决策中指定的工具调用，并将执行结果写回状态。
        它只负责：
        1. 校验工具白名单
        2. 调用 MCPToolManager
        3. 把 observation 写回状态

        参数：
            state: 当前 LangGraph 状态（包含 tool_name、tool_args、allowed_tools 等）

        返回：
            更新后的 LangGraphState（包含工具执行结果、更新后的 session 等）
        """
        # 记录节点开始时间戳，用于性能追踪
        start_ts = time.perf_counter()
        # 从状态中反序列化 session 对象
        session = SessionState.from_dict(state["session"])
        # 从状态中获取要调用的工具名称
        tool_name = state.get("tool_name", "")
        # 从状态中获取工具调用参数
        tool_args = state.get("tool_args", {})

        # 调用工具（内部会校验白名单），返回执行结果
        result = await self._call_allowed_tool(
            session=session,  # 当前会话状态
            allowed_tools=state.get("allowed_tools", []),  # 允许使用的工具列表
            tool_name=tool_name,  # 要调用的工具名称
            params=tool_args,  # 工具调用参数
            conv_id=state["conv_id"],  # 会话 ID
        )
        # 构建 observation 字典（工具执行结果的统一格式）
        observation = {
            "success": result.success,  # 工具是否执行成功
            "tool_name": result.tool_name,  # 实际执行的工具名称
            "data": result.data,  # 工具返回的数据
            "error": result.error,  # 错误信息（如果失败）
            "cached": result.cached,  # 结果是否来自缓存
            "latency_ms": result.latency_ms,  # 工具执行耗时（毫秒）
        }
        # 将本次工具执行结果保存到 session 中
        session.last_tool_result = observation
        # 更新任务状态为准备好回答（等待 Agent 基于工具结果生成回复）
        session.task_state = TaskState.READY_TO_ANSWER.value
        # 将本次工具调用记录追加到本轮工具调用列表
        state["current_turn_tools"].append(
            {
                "tool_name": result.tool_name,  # 工具名称
                "success": result.success,  # 是否成功
                "error": result.error,  # 错误信息
                "latency_ms": result.latency_ms,  # 耗时（毫秒）
            }
        )
        # 追加执行轨迹
        trace = self._append_trace(
            state,  # 当前状态
            node="tool",  # 节点名称
            decision="executed_tool",  # 决策为执行工具
            start_ts=start_ts,  # 开始时间戳
            tool_name=result.tool_name,  # 工具名称
            tool_args=tool_args,  # 工具参数
            tool_result=self._truncate_trace_value(observation),  # 截断后的工具结果（避免 trace 过大）
            detail="success" if result.success else f"error: {result.error}",  # 成功或错误详情
        )
        # 返回更新后的状态
        return {
            "session": session.to_dict(),  # 序列化 session
            "tool_result": observation,  # 工具执行结果
            "current_turn_tools": state["current_turn_tools"],  # 本轮工具调用记录
            "trace": trace,  # 执行轨迹
        }

    async def _handoff_node(self, state: LangGraphState) -> LangGraphState:
        """
        人工升级节点。

        负责将对话升级到人工客服：尝试创建转人工工单，并生成用户可见的升级提示。
        这里会尝试创建工单；即使创建失败，也保证用户能拿到明确的升级提示。

        参数：
            state: 当前 LangGraph 状态（包含 session、response 等）

        返回：
            更新后的 LangGraphState（包含工单号、转人工队列、最终响应等）
        """
        # 记录节点开始时间戳，用于性能追踪
        start_ts = time.perf_counter()
        # 从状态中反序列化 session 对象
        session = SessionState.from_dict(state["session"])
        # 构建创建工单所需的参数（去除值为 None 的字段）
        ticket_params = self._compact_dict({
            "customer_code": session.collected_slots.get("customer_code"),  # 客户编号（从已收集槽位中获取）
            "conv_id": session.conv_id,  # 会话 ID
            "source_intent": session.task_type,  # 来源意图（任务类型）
            "priority": "high" if session.task_type in {TaskType.REFUND.value, TaskType.ACCOUNT.value} else "medium",  # 优先级（退款/账户问题为高，其余为中）
            "reason": session.handoff_reason or "agent_requested_handoff",  # 转人工原因（无原因时用默认值）
            "summary": session.last_user_message or "LangGraph runtime requested handoff",  # 问题摘要（用户最后一条消息）
            "assigned_queue": self._queue_for_task_type(session.task_type),  # 根据任务类型分配人工队列
        })
        # 调用创建转人工工单工具，返回执行结果
        ticket_result = await self._call_allowed_tool(
            session=session,  # 当前会话状态
            allowed_tools=self.allowed_tools(session.task_type),  # 允许使用的工具列表
            tool_name="create_handoff_ticket",  # 要调用的工具名称
            params=ticket_params,  # 工单参数
            conv_id=state["conv_id"],  # 会话 ID
        )

        # 将工单创建工具的调用记录追加到本轮工具调用列表
        state["current_turn_tools"].append(
            {
                "tool_name": ticket_result.tool_name,  # 工具名称
                "success": ticket_result.success,  # 是否成功
                "error": ticket_result.error,  # 错误信息
                "latency_ms": ticket_result.latency_ms,  # 耗时（毫秒）
            }
        )

        # 获取基础响应文本（Agent 之前生成的转人工提示，无则用默认文案）
        base_response = state.get("response") or "我先为您转人工继续处理。"
        # 判断工单是否创建成功
        if ticket_result.success and isinstance(ticket_result.data, dict):
            # 从结果中提取工单号
            ticket_no = ticket_result.data.get("ticket_no", "未知工单号")
            # 从结果中提取分配的人工队列
            handoff_queue = ticket_result.data.get("assigned_queue")
            # 拼接包含工单号的最终响应
            final_response = f"{base_response} 已为您创建工单 {ticket_no}，人工客服会继续跟进。"
            # 记录最终结果为工单已创建
            session.final_outcome = "handoff_ticket_created"
        else:
            # 工单创建失败，工单号置空
            ticket_no = None
            # 工单创建失败，队列置空
            handoff_queue = None
            # 最终响应只保留基础提示
            final_response = base_response
            # 记录最终结果为已请求转人工但未创建工单
            session.final_outcome = "handoff_requested_without_ticket"

        # 设置任务状态为已完成
        session.task_state = TaskState.COMPLETED.value
        # 设置待执行动作为结束
        session.pending_action = PendingAction.FINISH.value
        # 标记需要转人工
        session.requires_handoff = True
        # 记录助手最后一条消息
        session.last_assistant_message = final_response
        # 记录最后一次工具调用结果
        session.last_tool_result = {
            "tool_name": ticket_result.tool_name,  # 工具名称
            "success": ticket_result.success,  # 是否成功
            "data": ticket_result.data,  # 工具返回的数据
            "error": ticket_result.error,  # 错误信息
        }
        # 追加执行轨迹
        trace = self._append_trace(
            state,  # 当前状态
            node="handoff",  # 节点名称
            decision="ticket_created" if ticket_no else "ticket_failed",  # 决策结果（工单创建成功/失败）
            start_ts=start_ts,  # 开始时间戳
            tool_name="create_handoff_ticket",  # 工具名称
            tool_args=ticket_params,  # 工单参数
            tool_result=self._truncate_trace_value(ticket_result.data),  # 截断后的工具结果
            detail=(
                f"ticket_no={ticket_no} queue={handoff_queue}"  # 工单号和队列信息
                if ticket_no
                else f"error: {ticket_result.error}"  # 失败时记录错误
            ),
        )
        # 返回更新后的状态
        return {
            "session": session.to_dict(),  # 序列化 session
            "current_turn_tools": state["current_turn_tools"],  # 本轮工具调用记录
            "final_response": final_response,  # 最终返回给用户的响应
            "final_outcome": session.final_outcome,  # 最终结果
            "requires_handoff": True,  # 标记需要转人工
            "ticket_no": ticket_no,  # 工单号
            "handoff_queue": handoff_queue,  # 转人工队列
            "trace": trace,  # 执行轨迹
        }

    async def _save_state_node(self, state: LangGraphState) -> LangGraphState:
        """
        保存状态节点：将当前会话状态持久化到 Redis。

        这是 LangGraph 流程的最后一个节点，负责把本轮所有对 session 的修改
        通过 SessionStateStore 写入 Redis，实现跨轮次的状态持久化。

        参数：
            state: 当前 LangGraph 状态

        返回：
            更新后的 LangGraphState（包含序列化后的 session 和执行轨迹）
        """
        # 记录节点开始时间戳，用于性能追踪
        start_ts = time.perf_counter()
        # 从状态中反序列化 session 对象
        session = SessionState.from_dict(state["session"])
        # 将 session 保存到 Redis（含 TTL 刷新）
        self._state_store.save(session)
        # 追加执行轨迹
        trace = self._append_trace(
            state,  # 当前状态
            node="save_state",  # 节点名称
            decision="state_saved",  # 决策为状态已保存
            start_ts=start_ts,  # 开始时间戳
            detail=f"task_state={session.task_state}",  # 保存时的任务状态
        )
        # 返回更新后的状态
        return {"session": session.to_dict(), "trace": trace}

    def _route_after_agent(self, state: LangGraphState) -> str:
        """
        路由函数：根据 Agent 决定的动作选择下一条边。

        作为 LangGraph 条件边的路由函数，读取 state 中的 last_action 字段，
        决定 agent_node 之后流向哪个节点：
        - call_tool -> tool_node（形成循环，工具执行后回到 agent_node）
        - handoff -> handoff_node
        - 其余（ask_followup / finish）-> save_state

        参数：
            state: 当前 LangGraph 状态

        返回：
            下一个节点的路由标识（"tool" / "handoff" / "save"）
        """
        # 从状态中获取 Agent 最后决定的动作
        action = state.get("last_action")
        # 如果动作是调用工具，路由到工具节点
        if action == PendingAction.CALL_TOOL.value:
            return "tool"
        # 如果动作是转人工，路由到转人工节点
        if action == PendingAction.HANDOFF.value:
            return "handoff"
        # 其余情况（追问/结束）路由到保存状态节点
        return "save"

    async def _classify_task_type(
        self,
        message: str,  # 用户当前输入的消息
        history: Optional[List[Dict[str, str]]] = None,  # 对话历史（用于意图识别上下文）
    ) -> str:
        """
        将现有粗粒度意图映射到 runtime 的 task_type。

        调用 IntentRecognizer 识别用户意图，然后将意图分类映射为 runtime 内部
        使用的任务类型。其中退款类通过关键词进一步细分，从 BILLING/FAQ 中拆出
        单独的 REFUND 类型。

        参数：
            message: 用户消息
            history: 对话历史（可选）

        返回：
            映射后的任务类型字符串（如 "refund"、"technical_issue" 等）
        """
        # 调用意图识别器识别用户意图
        result = await self._recognizer.recognize(message, history=history)
        # 提取识别出的意图分类
        intent = result.intent
        # 将消息转为小写，用于关键词匹配
        lowered = message.lower()

        # 技术类意图 -> technical_issue
        if intent == IntentCategory.TECHNICAL:
            return TaskType.TECHNICAL.value
        # 账户类意图 -> account_issue
        if intent == IntentCategory.ACCOUNT:
            return TaskType.ACCOUNT.value
        # 账单类意图：先判断是否含退款关键词，含则归为退款
        if intent == IntentCategory.BILLING:
            # 检查消息中是否包含退款相关关键词
            if any(kw in lowered for kw in ["退款", "退费", "refund"]):
                # 含退款关键词 -> 退款任务
                return TaskType.REFUND.value
            # 不含退款关键词 -> 账单任务
            return TaskType.BILLING.value
        # 升级类意图 -> escalation
        if intent == IntentCategory.ESCALATION:
            return TaskType.ESCALATION.value
        # 查询/请求类意图：同样先判断退款关键词
        if intent in (IntentCategory.QUERY, IntentCategory.REQUEST):
            # 含退款关键词 -> 退款任务
            if any(kw in lowered for kw in ["退款", "退费", "refund"]):
                return TaskType.REFUND.value
            # 不含退款关键词 -> FAQ 咨询
            return TaskType.FAQ.value
        # 其余未识别意图 -> unknown
        return TaskType.UNKNOWN.value

    def _extract_slots(self, session: SessionState, message: str) -> None:
        """
        用最轻量的规则抽取基础槽位。

        基于正则表达式和关键词规则，从用户消息中提取结构化的业务信息
        （订单号、物流单号、账户 ID、邮箱、产品名、套餐类型等），
        写入 session.collected_slots，供后续工具调用直接使用。
        这里仍然保留规则抽取，是因为它对客服场景很实用，而且能降低模型空调用工具的概率。

        参数：
            session: 当前会话状态（槽位直接写入 session.collected_slots）
            message: 用户消息
        """
        # 用正则匹配订单号（格式：ORD-xxxxxxxx-xxxx）
        order_match = re.search(r"(ORD-\d{8}-\d{4})", message)
        # 匹配到订单号则写入槽位
        if order_match:
            session.collected_slots["order_no"] = order_match.group(1)  # 保存匹配到的订单号

        # 用正则匹配物流单号（2-5 位大写字母 + 8-20 位数字）
        tracking_match = re.search(r"([A-Z]{2,5}\d{8,20})", message)
        # 匹配到物流单号且不是订单号前缀，则写入槽位（setdefault 避免覆盖已有值）
        if tracking_match and not tracking_match.group(1).startswith("ORD-"):
            session.collected_slots.setdefault("tracking_no", tracking_match.group(1))  # 保存匹配到的物流单号

        # 用正则匹配账户 ID（格式：ACC-XX-xx）
        account_match = re.search(r"(ACC-[A-Z]{2}-\d{2})", message)
        # 匹配到账户 ID 则写入槽位
        if account_match:
            session.collected_slots["account_id"] = account_match.group(1)  # 保存匹配到的账户 ID

        # 用正则匹配客户编号（格式：CUST-xxxx）
        customer_match = re.search(r"(CUST-\d{4})", message)
        # 匹配到客户编号则写入槽位
        if customer_match:
            session.collected_slots["customer_code"] = customer_match.group(1)  # 保存匹配到的客户编号

        # 用正则匹配邮箱地址
        email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", message)
        # 匹配到邮箱则写入槽位
        if email_match:
            session.collected_slots["email"] = email_match.group(1)  # 保存匹配到的邮箱地址

        # 退款场景里常见的购买时间，优先提取 YYYY-MM-DD 或 YYYY-MM-DD HH:MM。
        # 用正则匹配日期时间（日期必选，时间可选）
        purchase_time_match = re.search(r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)", message)
        # 匹配到购买时间则写入槽位
        if purchase_time_match:
            session.collected_slots["purchase_time"] = purchase_time_match.group(1)  # 保存匹配到的购买时间

        # 提取使用比例，例如"0%""目前使用了 35%"。
        # 用正则匹配百分比数字
        usage_match = re.search(r"(\d+(?:\.\d+)?)\s*%", message)
        # 匹配到百分比则转为浮点数写入槽位
        if usage_match:
            session.collected_slots["usage_percent"] = float(usage_match.group(1))  # 保存使用比例（浮点数）

        # 将消息转为小写，用于产品名关键词匹配
        lowered = message.lower()
        # 判断是否提到 Pro 版本
        if "pro" in lowered:
            # 产品名写入 Pro（setdefault 避免覆盖已有值）
            session.collected_slots.setdefault("product_name", "EchoMind Pro")
            # 产品类型写入订阅制
            session.collected_slots.setdefault("product_type", "subscription")
        # 判断是否提到 Team 版本
        elif "team" in lowered:
            # 产品名写入 Team
            session.collected_slots.setdefault("product_name", "EchoMind Team")
            # 产品类型写入订阅制
            session.collected_slots.setdefault("product_type", "subscription")
        # 判断是否提到 Enterprise 版本
        elif "enterprise" in lowered:
            # 产品名写入 Enterprise
            session.collected_slots.setdefault("product_name", "EchoMind Enterprise")
            # 产品类型写入服务制
            session.collected_slots.setdefault("product_type", "service")

        # 判断是否提到硬件产品关键词
        if any(keyword in message for keyword in ["耳机", "录音笔", "工牌", "主机"]):
            # 产品类型覆盖为硬件
            session.collected_slots.setdefault("product_type", "hardware")

        # 判断订阅周期：年
        if "年" in message or "year" in lowered:
            session.collected_slots.setdefault("plan_type", "yearly")  # 套餐类型写入年付
        # 判断订阅周期：月
        elif "月" in message or "monthly" in lowered:
            session.collected_slots.setdefault("plan_type", "monthly")  # 套餐类型写入月付
        # 判断订阅周期：季
        elif "季" in message or "quarterly" in lowered:
            session.collected_slots.setdefault("plan_type", "quarterly")  # 套餐类型写入季付

        # 判断故障类别：登录问题
        if "验证码" in message or "登录" in message:
            session.collected_slots.setdefault("incident_category", "login")  # 故障类别写入登录类
        # 判断故障类别：知识库问题
        elif "导入" in message or "知识库" in message:
            session.collected_slots.setdefault("incident_category", "knowledge_base")  # 故障类别写入知识库类
        # 判断故障类别：接口问题
        elif "500" in message or "接口" in message or "api" in lowered:
            session.collected_slots.setdefault("incident_category", "api")  # 故障类别写入 API 类

    async def _decide_action_with_llm(self, state: LangGraphState, session: SessionState) -> Dict[str, Any]:
        """
        用模型决定下一步动作。

        构建包含任务类型、工具说明、会话状态、槽位、历史等信息的完整 prompt，
        调用 LLM 让模型自主选择 ask_followup / call_tool / handoff / finish 四种动作。
        返回固定 JSON，避免自由文本导致 graph 无法稳定分支。

        参数：
            state: 当前 LangGraph 状态（包含 allowed_tools、history、message 等）
            session: 当前会话状态（包含 collected_slots、last_tool_result 等）

        返回：
            模型决策字典（action、response、tool_name、tool_args、reasoning）
        """
        # 初始化技能提示词为空字符串
        skill_prompt = ""
        # 如果配置了技能管理器，则为 support 角色生成匹配当前消息的技能提示词
        if self._skill_manager is not None:
            skill_prompt = self._skill_manager.prompt_for(state["message"], "support")

        # 根据允许的工具列表生成工具说明文本块
        tool_block = self._tool_prompt_block(state.get("allowed_tools", []))
        # 将上一次工具结果序列化为 JSON 字符串（无结果则为空对象）
        last_tool = json.dumps(session.last_tool_result or {}, ensure_ascii=False)
        # 将已收集的槽位序列化为 JSON 字符串
        slot_text = json.dumps(session.collected_slots, ensure_ascii=False)
        # 将最近 4 条对话历史序列化为 JSON 字符串
        history_text = json.dumps(state.get("history", [])[-4:], ensure_ascii=False)
        # 截取辅助上下文前 1500 个字符（防止 prompt 过长）
        context_text = (state.get("context", "") or "")[:1500]

        # 组装给模型的完整 prompt：任务说明、四种可选动作、当前状态和输出格式要求
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

        # 调用 LLM 接口，让模型根据 prompt 决定下一步动作
        resp = await self._client.messages.create(
            model=self._model,  # 使用的模型名称
            max_tokens=400,  # 最大输出 token 数（决策 JSON 很短，限制即可）
            temperature=0.1,  # 低温度保证决策稳定
            # 显式关闭 thinking，确保这里稳定返回普通 text block，
            # 避免 DeepSeek Anthropic 兼容层只返回 thinking 而不给最终 JSON。
            thinking={"type": "disabled"},  # 禁用 thinking 模式
            messages=[{"role": "user", "content": prompt}],  # 用户消息（完整 prompt）
        )
        # 从模型响应中提取纯文本内容
        raw = self._extract_agent_text_response(resp)
        # 记录模型原始输出日志，便于排查决策问题
        logger.info(
            "Agent 原始输出 conv_id=%s task_type=%s raw=%s",
            session.conv_id,  # 会话 ID
            session.task_type,  # 任务类型
            raw,  # 模型原始输出文本
        )
        # 解析模型输出为决策字典
        decision = self._parse_agent_decision(raw)
        # 记录解析后的决策结果日志
        logger.info(
            "Agent 解析结果 conv_id=%s task_type=%s decision=%s",
            session.conv_id,  # 会话 ID
            session.task_type,  # 任务类型
            json.dumps(decision, ensure_ascii=False),  # 序列化后的决策字典
        )
        # 对决策做最终校验和清洗（过滤不在白名单的工具等），返回安全决策
        return self._sanitize_decision(decision, state.get("allowed_tools", []))

    def _fallback_decision(self, state: LangGraphState, session: SessionState) -> Dict[str, Any]:
        """
        当模型决策失败时使用的保守回退策略。

        按任务类型走规则分支，逐级判断：升级类直接转人工，退款/账户/技术类按
        槽位和工具结果决定追问、调工具或结束，其余通用咨询走知识库检索。
        这里不是主逻辑，只是保证 graph 不会因为一次 LLM 输出异常而完全不可用。

        参数：
            state: 当前 LangGraph 状态（包含 message、allowed_tools 等）
            session: 当前会话状态（包含 task_type、collected_slots、last_tool_result 等）

        返回：
            保守决策字典（与 LLM 决策格式一致，action/response/tool_name/tool_args/reasoning）
        """
        # 升级类任务：直接转人工
        if session.task_type == TaskType.ESCALATION.value:
            return {
                "action": PendingAction.HANDOFF.value,  # 动作为转人工
                "response": "好的，我先为您转人工继续处理。",  # 转人工提示文案
                "reasoning": "用户明确要求人工",  # 原因
            }

        # 退款类任务分支
        if session.task_type == TaskType.REFUND.value:
            # 还没有工具结果时，先收集信息或查询规则
            if not session.last_tool_result:
                # 已有订单号槽位：优先核实该订单的退款资格
                if "order_no" in session.collected_slots:
                    return {
                        "action": PendingAction.CALL_TOOL.value,  # 动作为调用工具
                        "tool_name": "refund_eligibility_check",  # 退款资格检查工具
                        "tool_args": {
                            "order_no": session.collected_slots.get("order_no"),  # 订单号
                            "usage_percent": session.collected_slots.get("usage_percent"),  # 使用比例
                        },
                        "response": "",  # 调工具时响应为空
                        "reasoning": "有订单号时优先核实具体订单的退款资格",  # 原因
                    }

                # 收集缺失槽位清单
                missing_slots: List[str] = []
                # 缺少产品名则加入追问清单
                if "product_name" not in session.collected_slots:
                    missing_slots.append("产品名称")
                # 缺少套餐类型则加入追问清单
                if "plan_type" not in session.collected_slots:
                    missing_slots.append("套餐类型")
                # 有缺失槽位：追问用户补齐
                if missing_slots:
                    return {
                        "action": PendingAction.ASK_FOLLOWUP.value,  # 动作为追问
                        "response": f"为了帮您查询退款政策，我还需要确认：{'、'.join(missing_slots)}。",  # 追问文案
                        "reasoning": "退款政策查询所需槽位不足",  # 原因
                    }
                # 槽位齐全：查询退款政策
                return {
                    "action": PendingAction.CALL_TOOL.value,  # 动作为调用工具
                    "tool_name": "refund_policy_lookup",  # 退款政策查询工具
                    "tool_args": {
                        "product_name": session.collected_slots.get("product_name"),  # 产品名
                        "product_type": session.collected_slots.get("product_type"),  # 产品类型
                        "plan_type": session.collected_slots.get("plan_type"),  # 套餐类型
                        "purchase_time": session.collected_slots.get("purchase_time"),  # 购买时间
                    },
                    "response": "",  # 调工具时响应为空
                    "reasoning": "没有订单号时先查询退款政策",  # 原因
                }
            # 已有工具结果：基于结果生成退款回复
            refund_reply = self._build_refund_reply_from_tool(session.last_tool_result)
            # 能生成有效回复时进一步判断
            if refund_reply:
                # 从工具结果中提取 data 字段
                data = session.last_tool_result.get("data") or {}
                # 退款规则要求人工审核：转人工
                if data.get("manual_review_required"):
                    return {
                        "action": PendingAction.HANDOFF.value,  # 动作为转人工
                        "response": refund_reply,  # 带规则结论的回复
                        "reasoning": "退款规则要求人工审核",  # 原因
                    }
                # 无需人工审核：直接结束本轮
                return {
                    "action": PendingAction.FINISH.value,  # 动作为结束
                    "response": refund_reply,  # 退款结论回复
                    "reasoning": "已基于退款工具结果生成明确答复",  # 原因
                }
            # 无法生成有效回复：保守结束并建议转人工
            return {
                "action": PendingAction.FINISH.value,  # 动作为结束
                "response": "我已经完成退款规则核实，但当前结果不完整，建议转人工进一步确认。",  # 兜底文案
                "reasoning": "已有工具结果，可先结束本轮",  # 原因
            }

        # 账户类任务分支
        if session.task_type == TaskType.ACCOUNT.value:
            # 缺少账户 ID 和邮箱：追问用户提供识别信息
            if "account_id" not in session.collected_slots and "email" not in session.collected_slots:
                return {
                    "action": PendingAction.ASK_FOLLOWUP.value,  # 动作为追问
                    "response": "为了帮您核实账号状态，请提供 account_id 或注册邮箱。",  # 追问文案
                    "reasoning": "账号问题缺少识别信息",  # 原因
                }
            # 还没有工具结果：查询账号状态
            if not session.last_tool_result:
                return {
                    "action": PendingAction.CALL_TOOL.value,  # 动作为调用工具
                    "tool_name": "account_status_lookup",  # 账号状态查询工具
                    "tool_args": {
                        "account_id": session.collected_slots.get("account_id"),  # 账户 ID
                        "email": session.collected_slots.get("email"),  # 邮箱
                    },
                    "response": "",  # 调工具时响应为空
                    "reasoning": "先查账号状态",  # 原因
                }
            # 已有账号查询结果：结束本轮
            return {
                "action": PendingAction.FINISH.value,  # 动作为结束
                "response": "我已经完成账号状态核实，接下来可以根据核实结果继续处理。",  # 结束文案
                "reasoning": "已有账号查询结果",  # 原因
            }

        # 技术类任务分支
        if session.task_type == TaskType.TECHNICAL.value:
            # 缺少故障类别：追问用户补充
            if "incident_category" not in session.collected_slots:
                return {
                    "action": PendingAction.ASK_FOLLOWUP.value,  # 动作为追问
                    "response": "为了更快帮您定位问题，请补充一下是登录、验证码、导入还是接口报错。",  # 追问文案
                    "reasoning": "技术问题缺少类别信息",  # 原因
                }
            # 还没有工具结果：查询已知故障
            if not session.last_tool_result:
                return {
                    "action": PendingAction.CALL_TOOL.value,  # 动作为调用工具
                    "tool_name": "incident_lookup",  # 故障查询工具
                    "tool_args": {
                        "category": session.collected_slots.get("incident_category"),  # 故障类别
                    },
                    "response": "",  # 调工具时响应为空
                    "reasoning": "先查已知故障",  # 原因
                }
            # 已有故障查询结果：结束本轮
            return {
                "action": PendingAction.FINISH.value,  # 动作为结束
                "response": "我已经检查了当前故障信息，建议按结果继续排查或转人工。",  # 结束文案
                "reasoning": "已有故障查询结果",  # 原因
            }

        # 通用情况：没有工具结果且白名单包含知识库检索时，先查知识库
        if not session.last_tool_result and "knowledge_search" in self.allowed_tools(session.task_type):
            return {
                "action": PendingAction.CALL_TOOL.value,  # 动作为调用工具
                "tool_name": "knowledge_search",  # 知识库检索工具
                "tool_args": {"query": state["message"], "top_k": 3},  # 查询参数（用户消息、返回条数）
                "response": "",  # 调工具时响应为空
                "reasoning": "通用咨询优先查知识库",  # 原因
            }

        # 最终兜底：保守结束本轮
        return {
            "action": PendingAction.FINISH.value,  # 动作为结束
            "response": "我已经记录您的问题，如果需要更深入处理，我也可以继续帮您转人工。",  # 兜底文案
            "reasoning": "保守结束本轮",  # 原因
        }

    async def _call_allowed_tool(
        self,
        session: SessionState,  # 当前会话状态（用于记录工具调用历史）
        allowed_tools: List[str],  # 当前任务类型允许使用的工具白名单
        tool_name: str,  # 要调用的工具名称
        params: Dict[str, Any],  # 工具调用参数
        conv_id: str,  # 会话 ID（传递给工具作为上下文）
    ) -> ToolResult:
        """
        在白名单内执行工具，并把调用记录写入 session。

        校验工具名是否在白名单内，通过则调用 ToolManager 执行工具，
        并将本次调用记录（工具名、参数、成功状态、错误、缓存、耗时）
        追加到 session.tool_history。

        参数：
            session: 当前会话状态
            allowed_tools: 允许的工具列表
            tool_name: 工具名称
            params: 工具参数
            conv_id: 会话 ID

        返回：
            ToolResult（包含 success、data、error、cached、latency_ms 等）
        """
        # 检查工具名是否在白名单内
        if tool_name not in allowed_tools:
            # 工具不在白名单内，返回失败结果
            return ToolResult(
                success=False,  # 标记为失败
                data=None,  # 数据为空
                tool_name=tool_name,  # 记录尝试调用的工具名
                error=f"工具 {tool_name} 不在当前任务允许范围内",  # 错误信息
            )

        # 调用 ToolManager 执行工具
        result = await self._tool_manager.call(tool_name, params, context={"conv_id": conv_id})
        # 将本次工具调用记录追加到 session 的工具历史
        session.tool_history.append(
            {
                "tool_name": tool_name,  # 工具名称
                "params": params,  # 工具参数
                "success": result.success,  # 是否成功
                "error": result.error,  # 错误信息
                "cached": result.cached,  # 是否来自缓存
                "latency_ms": result.latency_ms,  # 耗时（毫秒）
            }
        )
        # 返回工具执行结果
        return result

    def _normalize_tool_args(
        self,
        tool_name: str,  # 要调用的工具名称
        tool_args: Dict[str, Any],  # LLM 决策返回的原始工具参数
        session: SessionState,  # 当前会话状态（从中提取已收集槽位补参数）
        message: str,  # 用户当前消息（用于某些工具的默认 query）
    ) -> Dict[str, Any]:
        """
        规范化工具参数。

        为 LLM 可能遗漏的关键字段从 session.collected_slots 或 message 中补默认值，
        防止参数结构过于发散导致工具调用失败。最后过滤掉值为 None、空字符串、空列表的字段。

        作用：
        - 为模型遗漏的关键字段补默认值
        - 防止参数结构过于发散，影响工具调用成功率

        参数：
            tool_name: 工具名称
            tool_args: 原始工具参数
            session: 会话状态
            message: 用户消息

        返回：
            规范化后的工具参数字典（过滤了空值）
        """
        # 复制原始参数字典（避免修改传入的参数）
        args = dict(tool_args or {})

        # 知识库检索工具：补 query 和 top_k
        if tool_name == "knowledge_search":
            args.setdefault("query", message)  # 默认用用户消息作为查询词
            args.setdefault("top_k", 3)  # 默认返回 3 条结果

        # 退款政策查询工具：从槽位补产品信息
        elif tool_name == "refund_policy_lookup":
            args.setdefault("product_name", session.collected_slots.get("product_name"))  # 产品名
            args.setdefault("product_type", session.collected_slots.get("product_type"))  # 产品类型
            args.setdefault("plan_type", session.collected_slots.get("plan_type"))  # 套餐类型
            args.setdefault("purchase_time", session.collected_slots.get("purchase_time"))  # 购买时间

        # 退款资格检查工具：从槽位补订单号和使用比例
        elif tool_name == "refund_eligibility_check":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号
            # 如果槽位中有使用比例，转为浮点数补参数
            if "usage_percent" in session.collected_slots:
                args.setdefault("usage_percent", float(session.collected_slots.get("usage_percent", 0.0)))  # 使用比例

        # 创建退款请求工具：从槽位补订单号，用消息作为退款原因
        elif tool_name == "create_refund_request":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号
            args.setdefault("refund_reason", message)  # 退款原因（用户消息）

        # 订单明细查询工具：从槽位补订单号
        elif tool_name == "order_items_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号

        # 物流追踪查询工具：从槽位补订单号和物流单号
        elif tool_name == "shipment_tracking_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号
            args.setdefault("tracking_no", session.collected_slots.get("tracking_no"))  # 物流单号

        # 支付记录查询工具：从槽位补订单号
        elif tool_name == "payment_attempt_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号

        # 退款请求查询工具：从槽位补订单号
        elif tool_name == "refund_request_lookup":
            args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号

        # 订单查询工具：从槽位补订单号或客户编号（有则补，无则跳过）
        elif tool_name == "order_lookup":
            # 槽位中有订单号则补订单号
            if "order_no" in session.collected_slots:
                args.setdefault("order_no", session.collected_slots.get("order_no"))  # 订单号
            # 槽位中有客户编号则补客户编号
            if "customer_code" in session.collected_slots:
                args.setdefault("customer_code", session.collected_slots.get("customer_code"))  # 客户编号

        # 账户状态查询工具：从槽位补账户 ID 和邮箱
        elif tool_name == "account_status_lookup":
            args.setdefault("account_id", session.collected_slots.get("account_id"))  # 账户 ID
            args.setdefault("email", session.collected_slots.get("email"))  # 邮箱

        # 故障查询工具：从槽位补故障类别
        elif tool_name == "incident_lookup":
            args.setdefault("category", session.collected_slots.get("incident_category"))  # 故障类别

        # 过滤掉值为 None、空字符串、空列表的字段，返回清理后的参数字典
        return {k: v for k, v in args.items() if v not in (None, "", [])}

    def _tool_prompt_block(self, allowed_tools: List[str]) -> str:
        """
        把当前允许工具及其 schema 格式化给模型。

        遍历允许的工具列表，从 ToolManager 中获取每个工具的描述和 schema，
        格式化为文本块供 LLM 参考（让模型知道有哪些工具可用、参数格式是什么）。

        参数：
            allowed_tools: 允许使用的工具名称列表

        返回：
            格式化后的工具说明文本（无可用工具时返回 "无可用工具"）
        """
        # 初始化行列表
        lines: List[str] = []
        # 遍历每个允许的工具名
        for name in allowed_tools:
            # 从 ToolManager 中获取工具定义（这里直接复用已注册工具定义，避免重复维护 schema）
            tool = self._tool_manager._tools.get(name)
            # 工具未找到则跳过
            if tool is None:
                continue
            # 格式化为 "- 工具名: 描述\n  schema=..." 的形式
            lines.append(
                f"- {name}: {tool.description}\n"  # 工具名和描述
                f"  schema={json.dumps(tool.schema, ensure_ascii=False)}"  # 工具参数 schema（JSON 格式）
            )
        # 拼接所有行，无可用工具时返回提示文本
        return "\n".join(lines) if lines else "无可用工具"

    def _parse_agent_decision(self, raw: str) -> Dict[str, Any]:
        """
        解析模型输出的 JSON。

        尝试从模型的原始输出中提取 JSON 决策对象，兼容多种格式：
        1. 外层包裹 ```json 代码块
        2. 前后带少量说明文字
        3. 存在多余换行

        解析失败则抛出 ValueError。

        这里做得比简单 find("{") 更稳一些，尽量兼容：
        1. 外层包裹 ```json 代码块
        2. 前后带少量说明文字
        3. 存在多余换行

        参数：
            raw: 模型原始输出文本

        返回：
            解析后的决策字典

        异常：
            ValueError: 无法从输出中提取有效 JSON
        """
        # 去除首尾空白
        cleaned = raw.strip()
        # 尝试匹配代码块格式（```json ... ```）
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        # 如果匹配到代码块，提取其中的 JSON 内容
        if code_block_match:
            cleaned = code_block_match.group(1).strip()  # 提取代码块内的 JSON

        # 如果内容以 { 开头、} 结尾，直接解析
        if cleaned.startswith("{") and cleaned.endswith("}"):
            return json.loads(cleaned)  # 解析为字典并返回

        # 尝试从文本中提取 JSON 对象（忽略前后的说明文字）
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        # 如果找到 JSON，解析并返回
        if json_match:
            return json.loads(json_match.group(1))  # 提取并解析 JSON

        # 以上都失败，抛出异常
        raise ValueError(f"Agent decision returned non-JSON content: {raw}")

    @staticmethod
    def _response_claims_handoff_completed(response: str) -> bool:
        '''
        判断回复是否已经在”宣称某个人工动作完成”。

        检查回复文本中是否包含”已为您创建工单”、”已为您转人工”等表述。
        这些表述一旦出现，就必须确保 runtime 真的去执行 handoff 工具，
        否则前端和用户看到的内容会与真实系统状态不一致。

        参数：
            response: Agent 返回给用户的响应文本

        返回：
            True 表示回复中声称已完成转人工，False 表示未声称
        '''
        # 提取响应文本并去除首尾空白
        text = (response or "").strip()
        # 空文本直接返回 False
        if not text:
            return False

        # 定义”已完成转人工”的关键词列表
        completed_keywords = [
            "已为您创建工单",  # 工单已创建
            "已经为您创建工单",  # 工单已创建（变体）
            "我已为您创建工单",  # 工单已创建（变体）
            "已帮您创建工单",  # 工单已创建（变体）
            "已为您创建转人工工单",  # 转人工工单已创建
            "已经为您创建转人工工单",  # 转人工工单已创建（变体）
            "我已为您创建转人工工单",  # 转人工工单已创建（变体）
            "已为您转人工",  # 已转人工
            "已经为您转人工",  # 已转人工（变体）
            "我已为您转人工",  # 已转人工（变体）
            "马上为您转接人工",  # 即将转人工
            "客服将尽快处理",  # 人工将处理
        ]
        # 检查文本中是否包含任意一个关键词
        return any(keyword in text for keyword in completed_keywords)

    def _should_force_handoff(self, session: SessionState, response: str) -> bool:
        """
        判断当前这一轮是否必须改写成 handoff。
        
        检查两个维度：回复文案是否声称已转人工、工具结果是否要求人工审核。
        这里不只看回复文案，还看已经拿到的工具事实，避免模型“逻辑上应该转人工，
        但动作却给了 finish”。

         参数：
            session: 当前会话状态（包含 task_type、last_tool_result 等）
            response: Agent 返回的响应文本

        返回：
            True 表示必须强制改写为 handoff，False 表示无需强制
        """
        # 检查回复文案是否声称已完成转人工
        if self._response_claims_handoff_completed(response):
            return True # 回复中声称已转人工，必须真正执行


        last_tool_result = session.last_tool_result or {} # 从 session 中提取最后一次工具结果
        data = last_tool_result.get("data") or {} # 提取工具返回的 data 字段
        tool_name = last_tool_result.get("tool_name")# 提取工具名称

        # 退款规则要求人工审核时，必须真正走 handoff，不能只在 finish 里口头说明。
        if (
            session.task_type == TaskType.REFUND.value # 任务类型为退款
            and tool_name in {"refund_policy_lookup", "refund_eligibility_check"} # 工具为退款相关查询
            and isinstance(data, dict) # data 是字典类型
            and data.get("manual_review_required")# 工具结果标记需要人工审核
        ):
            return True# 强制转人工

        return False# 其余情况不强制

    def _extract_agent_text_response(self, response: Any) -> str:
        """
        只从真正的 text block 里取模型回复。

        遍历模型响应的 content blocks，只提取 type="text" 的块内容，
        忽略 thinking block 等其他类型。
        这样可以避免 DeepSeek Anthropic 兼容层在包含 thinking block 时，
        把某个内部 block id 误识别成最终文本输出。

        参数：
            response: 模型响应对象（包含 content 字段）

        返回：
            提取的纯文本内容（多个 text block 用换行拼接）

        异常：
            ValueError: 响应中未找到有效的 text block
        """
        # 初始化文本片段列表
        parts: List[str] = []
        # 初始化 block 类型摘要列表（用于日志和错误提示）
        block_summaries: List[str] = []
        # 从响应对象中获取 content 字段
        content = getattr(response, "content", None)

        # 如果 content 本身就是字符串（简单格式），直接返回
        if isinstance(content, str) and content.strip():
            return content.strip()  # 去除首尾空白并返回

        # 遍历响应中的所有 content blocks
        for block in self._iter_response_blocks(content):
            # 判断 block 是字典还是对象，分别提取 type 和 text
            if isinstance(block, dict):
                # block 是字典：直接通过 get 提取字段
                block_type = str(block.get("type", "") or "")  # 提取 block 类型
                text = block.get("text")  # 提取 text 字段
            else:
                # block 是对象：通过 getattr 提取属性
                block_type = str(getattr(block, "type", "") or "")  # 提取 block 类型
                text = getattr(block, "text", None)  # 提取 text 属性

            # 记录 block 类型到摘要列表（用于日志）
            block_summaries.append(block_type or type(block).__name__)
            # 只收集 type="text" 且内容非空的 block
            if block_type == "text" and isinstance(text, str) and text.strip():
                parts.append(text.strip())  # 去除首尾空白后追加到片段列表

        # 记录所有 block 类型的日志
        logger.info("Agent content blocks=%s", block_summaries)

        # 如果收集到了文本片段，拼接并返回
        if parts:
            return "\n".join(parts).strip()  # 用换行符拼接所有片段

        # 未找到有效 text block，抛出异常
        raise ValueError(f"Agent response 未包含可解析的 text block，content_types={block_summaries}")

    def _sanitize_decision(self, decision: Dict[str, Any], allowed_tools: List[str]) -> Dict[str, Any]:
        """
        对模型返回结果做最后一道规范化，减少脏输出直接击穿主链路。

        校验 action 是否合法、tool_name 是否在白名单内、字段类型是否正确，
        并清理非 call_tool 动作的 tool_name 和 tool_args 字段。

        参数：
            decision: 解析后的模型决策字典
            allowed_tools: 允许使用的工具白名单

        返回：
            规范化后的决策字典

        异常：
            ValueError: action 非法、tool_name 不在白名单、tool_args 类型错误
        """
        # 复制决策字典（避免修改原始输入）
        normalized = dict(decision or {})
        # 提取 action 字段并转为小写（统一格式）
        action = str(normalized.get("action", "") or "").strip().lower()
        # 定义合法的动作集合
        valid_actions = {
            PendingAction.ASK_FOLLOWUP.value,  # 追问
            PendingAction.CALL_TOOL.value,  # 调用工具
            PendingAction.HANDOFF.value,  # 转人工
            PendingAction.FINISH.value,  # 结束
        }
        # 校验 action 是否合法
        if action not in valid_actions:
            raise ValueError(f"Agent action 非法: {action}")  # 动作不合法则抛出异常

        # 写回规范化后的 action
        normalized["action"] = action
        # 规范化 response 字段为字符串并去除首尾空白
        normalized["response"] = str(normalized.get("response", "") or "").strip()
        # 规范化 reasoning 字段为字符串并去除首尾空白
        normalized["reasoning"] = str(normalized.get("reasoning", "") or "").strip()
        # 规范化 tool_name 字段为字符串并去除首尾空白
        normalized["tool_name"] = str(normalized.get("tool_name", "") or "").strip()
        # 规范化 tool_args 字段为字典（空时用空字典）
        normalized["tool_args"] = normalized.get("tool_args") or {}

        # 如果动作是调用工具，需要进一步校验工具名和参数
        if action == PendingAction.CALL_TOOL.value:
            # 校验 tool_name 是否在白名单内
            if normalized["tool_name"] not in allowed_tools:
                raise ValueError(f"Agent tool_name 不在白名单内: {normalized['tool_name']}")  # 工具不在白名单则抛出异常
            # 校验 tool_args 是否为字典类型
            if not isinstance(normalized["tool_args"], dict):
                raise ValueError("Agent tool_args 不是对象")  # 参数类型错误则抛出异常
        else:
            # 非调用工具动作：清空 tool_name 和 tool_args
            normalized["tool_name"] = ""
            normalized["tool_args"] = {}

        # 返回规范化后的决策字典
        return normalized

    def _build_refund_reply_from_tool(self, last_tool_result: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        根据退款工具真实返回值构造用户回复。

        从 refund_policy_lookup 或 refund_eligibility_check 的工具结果中提取关键信息，
        组装成结构化的用户回复文本，避免 fallback 只说空泛结论。

        目标：
        1. 避免 fallback 只说空泛结论
        2. 让用户能明确看到"系统核实了什么事实"

        参数：
            last_tool_result: 最后一次工具调用结果（包含 tool_name、data 等）

        返回：
            组装好的回复文本，无法生成回复时返回 None
        """
        # 校验工具结果是否为有效字典
        if not last_tool_result or not isinstance(last_tool_result, dict):
            return None  # 工具结果为空或格式错误，返回 None

        # 提取工具返回的 data 字段
        data = last_tool_result.get("data")
        # 校验 data 是否为字典类型
        if not isinstance(data, dict):
            return None  # data 不是字典，返回 None

        # 提取工具名称
        tool_name = last_tool_result.get("tool_name")

        # 处理退款政策查询工具的结果
        if tool_name == "refund_policy_lookup":
            # 未匹配到退款政策时的处理
            if not data.get("matched"):
                # 提取错误消息或使用默认文案
                message = data.get("message") or "暂未匹配到退款政策，建议转人工进一步核实。"
                return f"我已经查询了退款政策，但{message}"  # 返回查询失败提示

            # 提取是否支持退款
            allow_refund = "支持退款" if data.get("allow_refund") else "不支持退款"
            # 提取退款时效窗口（小时数）
            refund_window = data.get("refund_window_hours")
            # 提取使用比例上限
            usage_limit = data.get("usage_limit_percent")
            # 提取是否需要人工审核
            manual_review_required = data.get("manual_review_required")

            # 构建回复行列表
            lines = [f"我已经帮您查询退款政策：当前规则{allow_refund}。"]
            # 如果有退款时效窗口，追加说明
            if refund_window is not None:
                lines.append(f"退款时效窗口为购买后 {refund_window} 小时内。")
            # 如果有使用比例限制，追加说明
            if usage_limit is not None:
                lines.append(f"规则允许的最高使用比例为 {usage_limit}%。")
            # 如果需要人工审核，追加说明
            if manual_review_required:
                lines.append("该类订单退款需要人工审核。")
            # 拼接所有行并返回
            return "".join(lines)

        # 处理退款资格检查工具的结果
        if tool_name == "refund_eligibility_check":
            # 未找到订单时的处理
            if not data.get("order_found"):
                # 返回工具给出的消息或默认提示
                return data.get("message") or "未找到对应订单，暂时无法核实退款资格。"

            # 构建回复行列表，首行为摘要或默认文案
            lines = [data.get("summary") or f"我已经帮您核实订单 {data.get('order_no')} 的退款资格。"]
            # 判断订单是否符合退款条件
            if data.get("eligible"):
                lines.append("当前这笔订单具备发起退款申请的基础条件。")  # 符合条件
            else:
                lines.append("当前这笔订单暂不满足直接退款条件。")  # 不符合条件
            # 如果需要人工审核，追加说明
            if data.get("manual_review_required"):
                lines.append("该订单退款需要人工审核。")
            # 如果已经存在退款申请，追加说明
            if data.get("already_requested") and data.get("existing_refund_no"):
                lines.append(f"系统中已经存在退款申请 {data.get('existing_refund_no')}。")
            # 拼接所有行并返回
            return "".join(lines)

        # 其他工具不处理，返回 None
        return None

    @staticmethod
    def _iter_response_blocks(content: Any) -> List[Any]:
        """
        把响应 content 安全转成列表，便于逐个 block 检查。

        处理 content 可能为 None、列表或可迭代对象的多种情况，
        统一返回值类型为列表，方便调用方遍历。

        参数：
            content: 模型响应的 content 字段（可能是 None、列表或迭代器）

        返回：
            block 列表
        """
        # content 为空时返回空列表
        if content is None:
            return []
        # content 已经是列表时直接返回
        if isinstance(content, list):
            return content  # 直接返回原列表
        # 尝试将其他可迭代对象转为列表
        try:
            return list(content)  # 转为列表
        except TypeError:
            # 不可迭代时包装为单元素列表
            return [content]

    @staticmethod
    def _compact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        删除值为 None 的字段，避免可选参数在 schema 校验时出现类型错误。

        参数：
            data: 原始参数字典

        返回：
            过滤掉 None 值的新字典
        """
        # 过滤掉值为 None 的字段，返回新字典
        return {key: value for key, value in data.items() if value is not None}

    @staticmethod
    def _queue_for_task_type(task_type: str) -> str:
        """
        根据粗粒度任务类型选择人工队列。

        不同任务类型对应不同的人工客服队列，退款/账单走 billing_ops、
        账户走 account_ops、技术走 technical_support、其余走 general_support。

        参数：
            task_type: 任务类型字符串

        返回：
            对应的人工队列名称
        """
        # 退款和账单类任务分配给账单运营队列
        if task_type in {TaskType.REFUND.value, TaskType.BILLING.value}:
            return "billing_ops"  # 账单运营队列
        # 账户类任务分配给账户运营队列
        if task_type == TaskType.ACCOUNT.value:
            return "account_ops"  # 账户运营队列
        # 技术类任务分配给技术支持队列
        if task_type == TaskType.TECHNICAL.value:
            return "technical_support"  # 技术支持队列
        # 其余情况分配给通用客服队列
        return "general_support"  # 通用客服队列
