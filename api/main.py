"""
EchoMind 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

# 将项目根目录加入 sys.path，确保无论从哪里执行都能找到 agents/core/memory 等模块
# 这一行必须在所有项目内部 import 之前执行
_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   EchoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_runtime = None


def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg

#lifespan 管理 FastAPI 启动和关闭时的资源初始化和清理
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _runtime

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from agents.runtime import SingleAgentRuntime
    from agents.session_state import SessionStateStore
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from integrations.business_db import BusinessDB
    from mcp.knowledge_base import KnowledgeBase
    from mcp.business_tool_handlers import BusinessToolHandlers
    from mcp.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager

    #cfg: 配置 Anthropic API Key、模型和可选 base_url
    cfg = _anthropic_cfg()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("ECHOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("ECHOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    business_db = BusinessDB(os.getenv("ECHOMIND_BUSINESS_DB_PATH", str(pathlib.Path(_ROOT) / "data" / "business" / "echomind.db")))
    business_tools = BusinessToolHandlers(business_db)
    logger.info(f"知识库已加载: {kb.doc_count} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    #将检索知识库的工具注册到MCP工具管理器中，支持查询改写、并行召回、重排和降级处理
    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "category": {"type": "string", "description": "知识域过滤，如 refund_policy / return_process / logistics_issue"},
                "product": {"type": "string", "description": "产品过滤，如 智能录音笔 A1"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        use_rewrite=True,  # Phase B：Agent 工具路径走"查询改写→混合召回→重排"完整链路
        fallback=knowledge_fallback,
    ))

    def account_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "found": False,
            "message": "账号状态暂时无法查询，请稍后重试或转人工处理。",
            "fallback": True,
            "error": error,
        }

    def order_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "found": False,
            "message": "订单信息暂时无法查询，请稍后重试或由人工协助核实。",
            "fallback": True,
            "error": error,
        }

    def order_items_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "found": False,
            "message": "订单商品项暂时无法查询，请稍后重试。",
            "query": {"order_no": params.get("order_no")},
            "fallback": True,
            "error": error,
        }

    def shipment_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "found": False,
            "message": "物流信息暂时无法查询，请稍后重试或转人工确认。",
            "query": {
                "order_no": params.get("order_no"),
                "tracking_no": params.get("tracking_no"),
            },
            "fallback": True,
            "error": error,
        }

    def payment_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "found": False,
            "message": "支付尝试记录暂时无法查询，请稍后重试或转人工确认。",
            "query": {"order_no": params.get("order_no")},
            "fallback": True,
            "error": error,
        }

    def refund_policy_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "matched": False,
            "message": "退款政策暂时无法查询，请先告知用户需稍后核实或转人工确认。",
            "policy_lookup_failed": True,
            "query": {
                "product_name": params.get("product_name"),
                "product_type": params.get("product_type"),
                "plan_type": params.get("plan_type"),
                "purchase_time": params.get("purchase_time"),
            },
            "fallback": True,
            "error": error,
        }

    def refund_eligibility_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "eligible": False,
            "order_found": False,
            "message": "当前无法核实这笔订单的退款资格，请稍后重试或转人工确认。",
            "query": {
                "order_no": params.get("order_no"),
                "usage_percent": params.get("usage_percent"),
            },
            "fallback": True,
            "error": error,
        }

    def refund_request_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "created": False,
            "message": "退款申请暂时无法创建，请记录用户诉求后转人工处理。",
            "fallback": True,
            "error": error,
        }

    def incident_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "matched": False,
            "count": 0,
            "incidents": [],
            "message": "故障公告暂时无法查询，请按常规排障流程继续处理。",
            "fallback": True,
            "error": error,
        }

    def ticket_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return {
            "created": False,
            "message": "人工工单暂时无法创建，请记录用户诉求后由人工补录。",
            "fallback": True,
            "error": error,
        }

    # 业务数据库工具：这 5 个工具是后续 Agent Runtime 的核心事实来源。
    _tool_manager.register(Tool(
        name="account_status_lookup",
        description="查询账号状态、角色、权限和锁定原因",
        handler=business_tools.account_status_lookup,
        schema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "email": {"type": "string"},
            },
        },
        cache_ttl=120.0,
        fallback=account_fallback,
    ))
    _tool_manager.register(Tool(
        name="order_lookup",
        description="查询单笔订单信息，或按客户编号查看最近订单列表",
        handler=business_tools.order_lookup,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
                "customer_code": {"type": "string"},
            },
        },
        cache_ttl=120.0,
        fallback=order_fallback,
    ))
    _tool_manager.register(Tool(
        name="order_items_lookup",
        description="查询订单中的商品明细，包括 SKU、数量、履约状态和是否可退换",
        handler=business_tools.order_items_lookup,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
            },
            "required": ["order_no"],
        },
        cache_ttl=120.0,
        fallback=order_items_fallback,
    ))
    _tool_manager.register(Tool(
        name="shipment_tracking_lookup",
        description="查询订单物流状态、最新轨迹、签收情况和物流异常",
        handler=business_tools.shipment_tracking_lookup,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
                "tracking_no": {"type": "string"},
            },
        },
        cache_ttl=60.0,
        fallback=shipment_fallback,
    ))
    _tool_manager.register(Tool(
        name="payment_attempt_lookup",
        description="查询订单支付尝试记录，用于分析重复扣费、支付失败和重试",
        handler=business_tools.payment_attempt_lookup,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
            },
            "required": ["order_no"],
        },
        cache_ttl=60.0,
        fallback=payment_fallback,
    ))
    _tool_manager.register(Tool(
        name="refund_policy_lookup",
        description="根据产品名称、产品类型和套餐类型查询退款政策",
        handler=business_tools.refund_policy_lookup,
        schema={
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "product_type": {"type": "string"},
                "plan_type": {"type": "string"},
                "purchase_time": {"type": "string"},
            },
            "required": ["product_name", "plan_type"],
        },
        cache_ttl=300.0,
        fallback=refund_policy_fallback,
    ))
    _tool_manager.register(Tool(
        name="refund_eligibility_check",
        description="基于订单事实、支付记录、退款申请和退款政策，判断某笔具体订单是否具备退款资格",
        handler=business_tools.refund_eligibility_check,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
                "usage_percent": {"type": "number"},
            },
            "required": ["order_no"],
        },
        cache_ttl=60.0,
        fallback=refund_eligibility_fallback,
    ))
    _tool_manager.register(Tool(
        name="create_refund_request",
        description="创建退款申请单，返回退款申请号和审核状态",
        handler=business_tools.create_refund_request,
        schema={
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
                "refund_reason": {"type": "string"},
                "requested_amount": {"type": "number"},
            },
            "required": ["order_no", "refund_reason"],
        },
        cache_ttl=0.0,
        fallback=refund_request_fallback,
    ))
    _tool_manager.register(Tool(
        name="refund_request_lookup",
        description="查询退款申请状态，支持按退款申请号或订单号查询",
        handler=business_tools.refund_request_lookup,
        schema={
            "type": "object",
            "properties": {
                "refund_no": {"type": "string"},
                "order_no": {"type": "string"},
            },
        },
        cache_ttl=60.0,
        fallback=refund_request_fallback,
    ))
    _tool_manager.register(Tool(
        name="incident_lookup",
        description="查询已知故障、影响范围和当前处理状态",
        handler=business_tools.incident_lookup,
        schema={
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "keyword": {"type": "string"},
            },
        },
        cache_ttl=60.0,
        fallback=incident_fallback,
    ))
    _tool_manager.register(Tool(
        name="create_handoff_ticket",
        description="创建转人工工单，记录升级原因、优先级和摘要",
        handler=business_tools.create_handoff_ticket,
        schema={
            "type": "object",
            "properties": {
                "customer_code": {"type": "string"},
                "conv_id": {"type": "string"},
                "source_intent": {"type": "string"},
                "priority": {"type": "string"},
                "reason": {"type": "string"},
                "summary": {"type": "string"},
                "assigned_queue": {"type": "string"},
            },
            "required": ["conv_id", "source_intent", "reason", "summary"],
        },
        cache_ttl=0.0,
        fallback=ticket_fallback,
    ))

    # LangGraph Runtime：使用 Redis 保存 SessionState，通过 Graph API 执行单 Agent loop。
    # 现有 orchestrator 仍保留，但只作为异常兜底，不再是 /chat 的主执行链。
    state_store = SessionStateStore(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
    )
    _runtime = SingleAgentRuntime(
        recognizer=recognizer,
        tool_manager=_tool_manager,
        state_store=state_store,
        skill_manager=_skill_manager,
    )

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    logger.info("EchoMind 已就绪")
    yield

    await _monitor.stop()
    logger.info("EchoMind 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="EchoMind 智能客服",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None


class ChatResponse(BaseModel):
    conv_id:     str
    response:    str
    intent:      str
    agent_type:  str
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    ticket_no:   Optional[str] = None
    handoff_queue: Optional[str] = None
    # Trace V1：返回本轮 Agent 执行轨迹，供前端展示节点、决策与工具调用
    trace: List[Dict[str, Any]] = Field(default_factory=list)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok", "agents": _orchestrator.get_stats()}


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    主对话接口。

    当前执行策略：
      1. 优先走 LangGraph 版 SingleAgentRuntime
      2. Agent 在 graph 中自主决定是否追问、调工具、建单或结束
      3. 如果 runtime 异常，再回退到现有 orchestrator 保底
    """
    if _orchestrator is None or _memory is None or _runtime is None:
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    from agents.session_state import TaskType
    from memory.conversation_memory import MsgRole

    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建编排请求（含对话历史，用于意图识别上下文）
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    try:
        # 3. 优先交给 LangGraph runtime 执行完整 agent loop。
        runtime_state, runtime_result = await _runtime.step(
            user_id=req.user_id,
            conv_id=conv_id,
            message=req.message,
            context=mem_ctx.to_prompt_text(),
            history=history,
        )
        knowledge_used = any(item.get("tool_name") == "knowledge_search" for item in runtime_result.used_tools)
        response_text = runtime_result.response
        response_intent = runtime_result.task_type
        response_agent_type = "support"
        response_escalated = runtime_result.requires_handoff
        response_latency_ms = 0.0
        response_ticket_no = runtime_result.ticket_no
        response_handoff_queue = runtime_result.handoff_queue
        response_trace = runtime_result.steps
    except Exception as ex:
        # 如果 LangGraph runtime 出现异常，回退到现有 orchestrator，保证主链路可用。
        logger.exception("LangGraph runtime 执行失败，回退到 orchestrator: %s", ex)
        knowledge_text, knowledge_used = await _build_knowledge_context(req.message)
        context_parts = [mem_ctx.to_prompt_text()]
        if knowledge_text:
            context_parts.append(knowledge_text)
        full_context = "\n\n".join(part for part in context_parts if part)

        orch_req = OrcReq(
            message=req.message,
            user_id=req.user_id,
            conv_id=conv_id,
            context=full_context,
            history=history,
        )
        result = await _orchestrator.run(orch_req)
        response_text = result.response
        response_intent = result.intent.value if result.intent else "other"
        response_agent_type = result.agent_type.value
        response_escalated = result.escalated
        response_latency_ms = round(result.latency_ms, 1)
        response_ticket_no = None
        response_handoff_queue = None
        # 兜底链路不产生 LangGraph 轨迹，返回空列表
        response_trace = []

    # 5. 写入记忆
    await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
    await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, response_text)

    # 6. 异步更新用户画像（不阻塞响应）
    asyncio.create_task(_memory.update_profile(req.user_id, conv_id))

    return ChatResponse(
        conv_id=conv_id,#会话id
        response=response_text,#输出结果
        intent=response_intent,#意图识别结果
        agent_type=response_agent_type,#agent类型
        escalated=response_escalated,#是否升级到人工客服
        latency_ms=response_latency_ms,#响应延迟，单位ms
        knowledge_used=knowledge_used,#是否使用了知识库内容
        ticket_no=response_ticket_no,#如果已创建工单，则返回工单号
        handoff_queue=response_handoff_queue,#如果已转人工，则返回工单所属队列
        trace=response_trace,#本轮 Agent 执行轨迹（Trace V1）
    )


async def _build_knowledge_context(message: str, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message):
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        #如果检索失败、返回结果不是列表或列表结果为空，则不使用知识库内容
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        #构建知识库上下文文本parts，包含标题，相关度和内容
        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


def _should_use_knowledge(message: str) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}

    #如果message是打招呼的，则不适用知识库
    if msg in greetings:
        return False

    business_keywords = [
        "退款", "订单", "物流", "配送", "发票", "扣款", "支付", "账单", "订阅",
        "登录", "报错", "错误", "崩溃", "会员", "积分", "账户", "密码", "地址",
        "refund", "order", "invoice", "payment", "error", "login",
    ]
    
    #message长度大于等于4，或者包含业务关键词，则使用知识库
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """
    单篇文档输入。

    title/content 必填；其余为可选 metadata（Phase A：RAG 知识库元数据），
    会随文档一起存入 ChromaDB，支持检索过滤与一致性校验。
    """
    title: str
    content: str
    doc_id: Optional[str] = None
    category: Optional[str] = None
    product: Optional[str] = None
    plan_type: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    source: Optional[str] = None
    version: Optional[int] = None
    owner: Optional[str] = None
    tags: Optional[str] = None
    db_table: Optional[str] = None
    # 一致性校验字段（与业务表对齐）
    policy_code: Optional[str] = None
    incident_code: Optional[str] = None
    incident_status: Optional[str] = None
    refund_window_hours: Optional[int] = None
    usage_limit_percent: Optional[float] = None
    allow_refund: Optional[bool] = None
    manual_review_required: Optional[bool] = None


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class KnowledgeSearchInput(BaseModel):
    """
    知识库过滤检索请求。

    category 支持字符串或列表；product/source 等为精确过滤；
    有效期自动限定为当前日期，已过期的公告不会返回。
    """
    query: str
    top_k: int = 5
    category: Optional[Any] = None
    product: Optional[str] = None
    source: Optional[str] = None
    db_table: Optional[str] = None
    plan_type: Optional[str] = None
    policy_code: Optional[str] = None
    incident_code: Optional[str] = None


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = kb.add_documents([d.model_dump(exclude_none=True) for d in body.documents])
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": kb.doc_count}


@app.post("/knowledge/search", tags=["知识库"])
async def knowledge_search(body: KnowledgeSearchInput):
    """
    带 metadata 过滤的语义检索。

    示例：按知识域过滤 {"query": "退款政策", "category": "refund_policy", "product": "智能录音笔 A1"}
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    where = kb._build_where(body.model_dump(exclude_none=True))
    results = kb.search(body.query, top_k=body.top_k, where=where)
    return {
        "query": body.query,
        "top_k": body.top_k,
        "where": where,
        "results": results,
    }


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = kb.add_documents(docs)
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": kb.doc_count,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"collection": kb._collection_name, "total_chunks": kb.doc_count}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("EchoMind CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("ECHOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("ECHOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\nEchoMind [{result.agent_type.value}]: {result.response}\n")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
