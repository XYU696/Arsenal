"""
EchoMind 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio  # 异步（后台更新画像、CLI入口）
import logging  # 日志
import os  # 环境变量
import pathlib  # 路径（项目根、知识库目录）
import sys  # sys.path 注入
import uuid  # 会话 ID 生成
from contextlib import asynccontextmanager  # lifespan 异步上下文管理器
from typing import Any, Dict, List, Optional  # 类型注解

# 将项目根目录加入 sys.path，确保无论从哪里执行都能找到 agents/core/memory 等模块
# 这一行必须在所有项目内部 import 之前执行
_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())  # 项目根目录绝对路径
if _ROOT not in sys.path:  # 未注入过才注入
    sys.path.insert(0, _ROOT)  # 将根目录加入模块搜索路径

import uvicorn
from dotenv import load_dotenv  # 加载 .env 环境变量
from fastapi import FastAPI, HTTPException, Response, UploadFile, File  # FastAPI 核心与文件上传
from fastapi.middleware.cors import CORSMiddleware  # 跨域中间件
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # Prometheus 指标导出
from pydantic import BaseModel, Field  # 请求/响应模型

load_dotenv()  # 读取 .env（须在所有环境变量读取之前）

# 配置全局日志（LEVEL/格式由环境变量控制）
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),  # 日志级别（默认 INFO）
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",  # 日志格式
)
logger = logging.getLogger(__name__)  # 模块级日志记录器

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   EchoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None  # Agent 编排器（兜底路由）
_memory       = None  # 记忆管理器
_tool_manager = None  # MCP 工具管理器
_monitor      = None  # 性能监控器
_evaluator    = None  # 端到端评测器
_skill_manager = None  # 技能管理器
_runtime = None  # LangGraph 单 Agent 运行时（/chat 主执行链）


def _anthropic_cfg() -> Dict[str, Any]:
    """
    从环境变量组装 Anthropic 客户端配置。

    参数：
        无

    返回：
        客户端配置字典（api_key / model / 可选 base_url）
    """
    # 读取 API Key（必填）
    key = os.getenv("ANTHROPIC_API_KEY", "")
    # 未配置则抛错阻止启动
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    # 组装基础配置
    cfg: Dict[str, Any] = {
        "api_key":  key,  # API Key
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),  # 模型名（可覆盖）
    }
    # 读取可选的 base_url（去首尾空白）
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    # 非空才写入配置
    if base_url:
        cfg["base_url"] = base_url
    # 返回配置
    return cfg

#lifespan 管理 FastAPI 启动和关闭时的资源初始化和清理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 声明 lifespan 要赋值的全局组件（模块级占位，供路由读取）
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _runtime

    # 打印启动横幅
    print(BANNER, flush=True)

    # 延迟导入核心组件（缩短启动加载时间、避免重依赖强制导入）
    from agents.agent_orchestrator import AgentOrchestrator, Request  # Agent 编排器及请求类型（兜底路由）
    from agents.runtime import SingleAgentRuntime  # LangGraph 单 Agent 运行时（/chat 主执行链）
    from agents.session_state import SessionStateStore  # 会话状态存储（Redis）
    from core.intent_recognizer import IntentRecognizer  # 意图识别器
    from evaluation.evaluator import EndToEndEvaluator  # 端到端评测器
    from integrations.business_db import BusinessDB  # 业务数据库（SQLite）
    from mcp.knowledge_base import KnowledgeBase  # ChromaDB 知识库
    from mcp.business_tool_handlers import BusinessToolHandlers  # 业务数据工具处理器
    from mcp.tool_manager import MCPToolManager, Tool  # 工具管理器与工具定义
    from memory.conversation_memory import MemoryManager  # 记忆管理器（Redis + ChromaDB）
    from monitor.performance_monitor import PerformanceMonitor  # 性能监控器
    from core.skill_loader import SkillManager  # 技能加载器

    #cfg: 配置 Anthropic API Key、模型和可选 base_url
    cfg = _anthropic_cfg()
    # 打印当前模型与 base_url 配置摘要
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    # 技能目录（可用环境变量覆盖，默认项目内 skills/）
    skills_dir = os.getenv("ECHOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    # 创建技能管理器
    _skill_manager = SkillManager(
        root_dir=skills_dir,  # 技能根目录
        max_prompt_chars=int(os.getenv("ECHOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),  # 注入到 prompt 的最大字符数
    )
    # 加载全部技能
    _skill_manager.load()

    # Agent 编排器
    # 创建 Agent 编排器（LangGraph 异常时兜底）
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
        skill_manager=_skill_manager,  # 技能管理器
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    # 创建记忆管理器
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),  # 会话工作记忆 Redis 地址
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),  # ChromaDB 主机
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),  # ChromaDB 端口
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),  # ChromaDB 持久化目录
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名（用于画像生成）
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    # 创建 MCP 工具管理器
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
    )
    # 知识库（ChromaDB 向量检索底座）
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),  # ChromaDB 主机
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),  # ChromaDB 端口
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),  # ChromaDB 持久化目录
    )
    # 业务数据库（SQLite 订单/退款/物流等事实源）
    business_db = BusinessDB(os.getenv("ECHOMIND_BUSINESS_DB_PATH", str(pathlib.Path(_ROOT) / "data" / "business" / "echomind.db")))
    # 业务数据工具处理器（把数据库查询包装成 Agent 可调用工具）
    business_tools = BusinessToolHandlers(business_db)
    # 打印知识库片段总数
    logger.info(f"知识库已加载: {kb.doc_count} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """知识库检索失败时的降级结果，保证 Agent 拿到可读的提示而非裸异常。"""
        query = params.get("query", "")  # 取出用户的检索查询
        return [{  # 返回单条降级伪结果
            "title": "知识库降级结果",  # 标题标记
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",  # 面向用户的降级文案
            "score": 0.0,  # 无实际相关度
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }]

    # 将检索知识库的工具注册到MCP工具管理器中，支持查询改写、并行召回、重排和降级处理
    _tool_manager.register(Tool(
        name="knowledge_search",  # 工具名
        description="搜索知识库（基于 ChromaDB 向量检索）",  # 工具描述
        handler=kb.search_handler,  # 检索处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "query": {"type": "string"},  # 查询文本（必填）
                "top_k": {"type": "integer"},  # 返回条数
                "category": {"type": "string", "description": "知识域过滤，如 refund_policy / return_process / logistics_issue"},  # 知识域过滤
                "product": {"type": "string", "description": "产品过滤，如 智能录音笔 A1"},  # 产品过滤
            },
            "required": ["query"],  # query 必填
        },
        cache_ttl=300.0,  # 结果缓存 300 秒
        supports_rerank=True,  # 支持 LLM 重排
        use_rewrite=True,  # Phase B：Agent 工具路径走"查询改写→混合召回→重排"完整链路
        fallback=knowledge_fallback,  # 失败降级函数
    ))

    def account_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """账号查询失败的降级结果。"""
        return {  # 降级响应
            "found": False,  # 未查到
            "message": "账号状态暂时无法查询，请稍后重试或转人工处理。",  # 面向用户的降级文案
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def order_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """订单查询失败的降级结果。"""
        return {  # 降级响应
            "found": False,  # 未查到
            "message": "订单信息暂时无法查询，请稍后重试或由人工协助核实。",  # 面向用户的降级文案
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def order_items_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """订单商品项查询失败的降级结果。"""
        return {  # 降级响应
            "found": False,  # 未查到
            "message": "订单商品项暂时无法查询，请稍后重试。",  # 面向用户的降级文案
            "query": {"order_no": params.get("order_no")},  # 回显原查询参数
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def shipment_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """物流查询失败的降级结果。"""
        return {  # 降级响应
            "found": False,  # 未查到
            "message": "物流信息暂时无法查询，请稍后重试或转人工确认。",  # 面向用户的降级文案
            "query": {  # 回显原查询参数
                "order_no": params.get("order_no"),  # 订单号
                "tracking_no": params.get("tracking_no"),  # 物流单号
            },
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def payment_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """支付记录查询失败的降级结果。"""
        return {  # 降级响应
            "found": False,  # 未查到
            "message": "支付尝试记录暂时无法查询，请稍后重试或转人工确认。",  # 面向用户的降级文案
            "query": {"order_no": params.get("order_no")},  # 回显原查询参数
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def refund_policy_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """退款政策查询失败的降级结果。"""
        return {  # 降级响应
            "matched": False,  # 未匹配到政策
            "message": "退款政策暂时无法查询，请先告知用户需稍后核实或转人工确认。",  # 面向用户的降级文案
            "policy_lookup_failed": True,  # 标记查询失败
            "query": {  # 回显原查询参数
                "product_name": params.get("product_name"),  # 产品名
                "product_type": params.get("product_type"),  # 产品类型
                "plan_type": params.get("plan_type"),  # 套餐类型
                "purchase_time": params.get("purchase_time"),  # 购买时间
            },
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def refund_eligibility_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """退款资格判断在数据库不可用时的保守降级（默认不可退款）。"""
        return {  # 降级响应
            "eligible": False,  # 退保守默认：不判定为可退
            "order_found": False,  # 未找到订单
            "message": "当前无法核实这笔订单的退款资格，请稍后重试或转人工确认。",  # 面向用户的降级文案
            "query": {  # 回显原查询参数
                "order_no": params.get("order_no"),  # 订单号
                "usage_percent": params.get("usage_percent"),  # 使用进度
            },
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def refund_request_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """创建退款申请的降级结果。"""
        return {  # 降级响应
            "created": False,  # 未创建成功
            "message": "退款申请暂时无法创建，请记录用户诉求后转人工处理。",  # 面向用户的降级文案
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def incident_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """故障公告查询失败的降级结果。"""
        return {  # 降级响应
            "matched": False,  # 未匹配到故障
            "count": 0,  # 故障数 0
            "incidents": [],  # 空列表
            "message": "故障公告暂时无法查询，请按常规排障流程继续处理。",  # 面向用户的降级文案
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    def ticket_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """创建转人工工单的降级结果。"""
        return {  # 降级响应
            "created": False,  # 未创建成功
            "message": "人工工单暂时无法创建，请记录用户诉求后由人工补录。",  # 面向用户的降级文案
            "fallback": True,  # 标记为降级结果
            "error": error,  # 原始错误信息
        }

    # 业务数据库工具：这 5 个工具是后续 Agent Runtime 的核心事实来源。
    # 注册账号状态查询工具
    _tool_manager.register(Tool(
        name="account_status_lookup",  # 工具名
        description="查询账号状态、角色、权限和锁定原因",  # 工具描述
        handler=business_tools.account_status_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "account_id": {"type": "string"},  # 账号 ID（二选一）
                "email": {"type": "string"},  # 邮箱（二选一）
            },
        },
        cache_ttl=120.0,  # 结果缓存 120 秒
        fallback=account_fallback,  # 失败降级函数
    ))
    # 注册订单查询工具
    _tool_manager.register(Tool(
        name="order_lookup",  # 工具名
        description="查询单笔订单信息，或按客户编号查看最近订单列表",  # 工具描述
        handler=business_tools.order_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号（二选一）
                "customer_code": {"type": "string"},  # 客户编号（二选一）
            },
        },
        cache_ttl=120.0,  # 结果缓存 120 秒
        fallback=order_fallback,  # 失败降级函数
    ))
    # 注册订单商品项查询工具
    _tool_manager.register(Tool(
        name="order_items_lookup",  # 工具名
        description="查询订单中的商品明细，包括 SKU、数量、履约状态和是否可退换",  # 工具描述
        handler=business_tools.order_items_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号
            },
            "required": ["order_no"],  # order_no 必填
        },
        cache_ttl=120.0,  # 结果缓存 120 秒
        fallback=order_items_fallback,  # 失败降级函数
    ))
    # 注册物流跟踪工具
    _tool_manager.register(Tool(
        name="shipment_tracking_lookup",  # 工具名
        description="查询订单物流状态、最新轨迹、签收情况和物流异常",  # 工具描述
        handler=business_tools.shipment_tracking_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号（建议提供）
                "tracking_no": {"type": "string"},  # 物流单号
            },
        },
        cache_ttl=60.0,  # 结果缓存 60 秒（物流是高频变化数据）
        fallback=shipment_fallback,  # 失败降级函数
    ))
    # 注册支付记录查询工具
    _tool_manager.register(Tool(
        name="payment_attempt_lookup",  # 工具名
        description="查询订单支付尝试记录，用于分析重复扣费、支付失败和重试",  # 工具描述
        handler=business_tools.payment_attempt_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号
            },
            "required": ["order_no"],  # order_no 必填
        },
        cache_ttl=60.0,  # 结果缓存 60 秒
        fallback=payment_fallback,  # 失败降级函数
    ))
    # 注册退款政策查询工具
    _tool_manager.register(Tool(
        name="refund_policy_lookup",  # 工具名
        description="根据产品名称、产品类型和套餐类型查询退款政策",  # 工具描述
        handler=business_tools.refund_policy_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "product_name": {"type": "string"},  # 产品名称
                "product_type": {"type": "string"},  # 产品类型
                "plan_type": {"type": "string"},  # 套餐类型
                "purchase_time": {"type": "string"},  # 购买时间
            },
            "required": ["product_name", "plan_type"],  # 产品名与套餐必填
        },
        cache_ttl=300.0,  # 结果缓存 300 秒（政策相对静态）
        fallback=refund_policy_fallback,  # 失败降级函数
    ))
    # 注册退款资格判断工具
    _tool_manager.register(Tool(
        name="refund_eligibility_check",  # 工具名
        description="基于订单事实、支付记录、退款申请和退款政策，判断某笔具体订单是否具备退款资格",  # 工具描述
        handler=business_tools.refund_eligibility_check,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号
                "usage_percent": {"type": "number"},  # 已用进度占比
            },
            "required": ["order_no"],  # order_no 必填
        },
        cache_ttl=60.0,  # 结果缓存 60 秒
        fallback=refund_eligibility_fallback,  # 失败降级函数
    ))
    # 注册创建退款申请工具
    _tool_manager.register(Tool(
        name="create_refund_request",  # 工具名
        description="创建退款申请单，返回退款申请号和审核状态",  # 工具描述
        handler=business_tools.create_refund_request,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "order_no": {"type": "string"},  # 订单号
                "refund_reason": {"type": "string"},  # 退款原因
                "requested_amount": {"type": "number"},  # 申请金额
            },
            "required": ["order_no", "refund_reason"],  # 订单号与原因必填
        },
        cache_ttl=0.0,  # 写操作不缓存
        fallback=refund_request_fallback,  # 失败降级函数
    ))
    # 注册退款申请查询工具
    _tool_manager.register(Tool(
        name="refund_request_lookup",  # 工具名
        description="查询退款申请状态，支持按退款申请号或订单号查询",  # 工具描述
        handler=business_tools.refund_request_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "refund_no": {"type": "string"},  # 退款申请号（二选一）
                "order_no": {"type": "string"},  # 订单号（二选一）
            },
        },
        cache_ttl=60.0,  # 结果缓存 60 秒
        fallback=refund_request_fallback,  # 失败降级函数
    ))
    # 注册故障公告查询工具
    _tool_manager.register(Tool(
        name="incident_lookup",  # 工具名
        description="查询已知故障、影响范围和当前处理状态",  # 工具描述
        handler=business_tools.incident_lookup,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "category": {"type": "string"},  # 故障类别
                "keyword": {"type": "string"},  # 关键词
            },
        },
        cache_ttl=60.0,  # 结果缓存 60 秒
        fallback=incident_fallback,  # 失败降级函数
    ))
    # 注册转人工工单创建工具
    _tool_manager.register(Tool(
        name="create_handoff_ticket",  # 工具名
        description="创建转人工工单，记录升级原因、优先级和摘要",  # 工具描述
        handler=business_tools.create_handoff_ticket,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "customer_code": {"type": "string"},  # 客户编号
                "conv_id": {"type": "string"},  # 会话 ID
                "source_intent": {"type": "string"},  # 来源意图
                "priority": {"type": "string"},  # 优先级
                "reason": {"type": "string"},  # 转人工原因
                "summary": {"type": "string"},  # 问题摘要
                "assigned_queue": {"type": "string"},  # 分配队列
            },
            "required": ["conv_id", "source_intent", "reason", "summary"],  # 会话、意图、原因、摘要必填
        },
        cache_ttl=0.0,  # 写操作不缓存
        fallback=ticket_fallback,  # 失败降级函数
    ))

    # LangGraph Runtime：使用 Redis 保存 SessionState，通过 Graph API 执行单 Agent loop。
    # 现有 orchestrator 仍保留，但只作为异常兜底，不再是 /chat 的主执行链。
    # 创建会话状态存储
    state_store = SessionStateStore(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),  # Redis 连接地址
    )
    # 创建 LangGraph 单 Agent 运行时
    _runtime = SingleAgentRuntime(
        recognizer=recognizer,  # 意图识别器
        tool_manager=_tool_manager,  # 工具管理器（含已注册的全部工具）
        state_store=state_store,  # 会话状态存储
        skill_manager=_skill_manager,  # 技能管理器
    )

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None  # Prometheus 端口（0/空则不开）
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,  # 编排器（监控其统计）
        tool_manager=_tool_manager,  # 工具管理器（监控工具调用）
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),  # 采样间隔（秒）
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,  # 告警 Webhook
        prometheus_port=prom_port,  # Prometheus 暴露端口
    )
    # 启动监控采样循环
    await _monitor.start()

    # 评测器（供 /eval/run 与回归检测）
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,  # 编排器
        recognizer=recognizer,  # 意图识别器
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),  # 评测基线路径
    )

    # 就绪提示
    logger.info("EchoMind 已就绪")
    yield  # 交还控制给 FastAPI，进入服务运行状态

    # 关闭阶段：先停监控再退出
    await _monitor.stop()  # 停止监控采样
    logger.info("EchoMind 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
# 创建 FastAPI 应用（生命周期由 lifespan 托管资源初始化）
app = FastAPI(
    title="EchoMind 智能客服",  # 文档标题
    version="2.0.0",  # 版本号
    lifespan=lifespan,  # 启动/关闭钩子
    docs_url="/docs",  # Swagger 文档地址
)

# 注册跨域中间件（开发期全放开，生产应按需收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许全部来源
    allow_methods=["*"],  # 允许全部方法
    allow_headers=["*"],  # 允许全部请求头
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    """聊天请求体。"""
    message:     str  # 用户消息
    user_id:     str = "anonymous"  # 用户 ID（默认匿名）
    conv_id:     Optional[str] = None  # 会话 ID（不传则新建）


class ChatResponse(BaseModel):
    """聊天响应体。"""
    conv_id:     str  # 会话 ID
    response:    str  # 回复文本
    intent:      str  # 识别出的意图
    agent_type:  str  # 处理的 Agent 类型
    escalated:   bool  # 是否转人工
    latency_ms:  float  # 响应延迟（毫秒）
    knowledge_used: bool = False  # 是否使用了知识库
    ticket_no:   Optional[str] = None  # 转人工工单号
    handoff_queue: Optional[str] = None  # 转人工队列
    # Trace V1：返回本轮 Agent 执行轨迹，供前端展示节点、决策与工具调用
    trace: List[Dict[str, Any]] = Field(default_factory=list)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """健康检查接口，返回编排器统计作为探活内容。"""
    if _orchestrator is None:  # 核心组件未初始化
        raise HTTPException(503, "服务未就绪")  # 返回 503
    return {"status": "ok", "agents": _orchestrator.get_stats()}  # 返回健康状态与 Agent 统计


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:  # 未初始化
        raise HTTPException(503, "Skills 未初始化")  # 返回 503
    return _skill_manager.summary()  # 返回技能加载摘要


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:  # 未初始化
        raise HTTPException(503, "Skills 未初始化")  # 返回 503
    _skill_manager.reload()  # 重新扫描技能目录
    if _orchestrator is not None:  # 编排器已存在时
        _orchestrator.set_skill_manager(_skill_manager)  # 同步编排器技能管理器
    return _skill_manager.summary()  # 返回更新后的摘要


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    主对话接口。

    当前执行策略：
      1. 优先走 LangGraph 版 SingleAgentRuntime
      2. Agent 在 graph 中自主决定是否追问、调工具、建单或结束
      3. 如果 runtime 异常，再回退到现有 orchestrator 保底
    """
    # 检查核心组件是否已初始化，未就绪则返回 503
    if _orchestrator is None or _memory is None or _runtime is None:
        raise HTTPException(503, "服务未就绪")

    # 导入 orchestrator、状态管理和记忆模块的相关类型
    from agents.agent_orchestrator import Request as OrcReq
    from agents.session_state import TaskType
    from memory.conversation_memory import MsgRole

    # 生成或复用会话 ID
    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    # 从 Redis 读取工作记忆（最近消息）   从 ChromaDB 读取情景记忆（历史语义片段）和用户画像   拼接成 context 字符串传给 Runtime
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建编排请求（含对话历史，用于意图识别上下文）
    # 提取最近 5 条消息作为对话历史
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    try:
        # 3. 优先交给 LangGraph runtime 执行完整 agent loop。
        # 调用 runtime.step 执行单轮对话，返回状态和结果
        runtime_state, runtime_result = await _runtime.step(
            user_id=req.user_id,
            conv_id=conv_id,
            message=req.message,
            context=mem_ctx.to_prompt_text(),
            history=history,
        )
        # 检查是否使用了知识库检索工具
        knowledge_used = any(item.get("tool_name") == "knowledge_search" for item in runtime_result.used_tools) #由 Agent 在需要时主动调用 knowledge_search 工具
        # 提取 runtime 返回的响应文本
        response_text = runtime_result.response
        # 提取任务类型作为意图
        response_intent = runtime_result.task_type
        # 默认 agent 类型为 support
        response_agent_type = "support"
        # 提取是否需要转人工
        response_escalated = runtime_result.requires_handoff
        # LangGraph 路径暂不统计延迟
        response_latency_ms = 0.0
        # 提取工单号（如果已创建）
        response_ticket_no = runtime_result.ticket_no
        # 提取转人工队列（如果已转人工）
        response_handoff_queue = runtime_result.handoff_queue
        # 提取执行轨迹（LangGraph steps）
        response_trace = runtime_result.steps
    except Exception as ex:
        # 如果 LangGraph runtime 出现异常，回退到现有 orchestrator，保证主链路可用。
        # 记录异常日志
        logger.exception("LangGraph runtime 执行失败，回退到 orchestrator: %s", ex)
        # 调用知识库检索构建上下文
        knowledge_text, knowledge_used = await _build_knowledge_context(req.message)
        # 拼接记忆上下文和知识库上下文
        context_parts = [mem_ctx.to_prompt_text()]
        if knowledge_text:
            context_parts.append(knowledge_text)
        # 用双换行符连接所有上下文部分
        full_context = "\n\n".join(part for part in context_parts if part)

        # 构建 orchestrator 请求对象
        orch_req = OrcReq(
            message=req.message,
            user_id=req.user_id,
            conv_id=conv_id,
            context=full_context,
            history=history,
        )
        # 调用 orchestrator 执行兜底逻辑
        result = await _orchestrator.run(orch_req)
        # 提取 orchestrator 返回的响应文本
        response_text = result.response
        # 提取意图类型，无意图时默认为 "other"
        response_intent = result.intent.value if result.intent else "other"
        # 提取 agent 类型
        response_agent_type = result.agent_type.value
        # 提取是否升级到人工
        response_escalated = result.escalated
        # 提取并四舍五入延迟时间
        response_latency_ms = round(result.latency_ms, 1)
        # 兜底路径不支持工单创建
        response_ticket_no = None
        # 兜底路径不支持转人工队列
        response_handoff_queue = None
        # 兜底链路不产生 LangGraph 轨迹，返回空列表
        response_trace = []

    # 5. 写入记忆
    # 存储用户消息到记忆系统
    await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
    # 存储助手回复到记忆系统
    await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, response_text)

    # 6. 异步更新用户画像（不阻塞响应）
    # 启动后台任务更新用户画像
    asyncio.create_task(_memory.update_profile(req.user_id, conv_id))

    # 构造并返回响应对象
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
    if _tool_manager is None:  # 工具管理器未初始化
        return "", False  # 空上下文、未使用知识库
    if not _should_use_knowledge(message):  # 非业务类消息
        return "", False  # 空上下文、未使用知识库
    try:
        # 执行"查询改写 → 混合召回 → 重排"完整检索链路
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
            if not content:  # 内容为空则跳过该条
                continue
            used = True  # 至少收录了一条有效内容
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")  # 拼接条目（内容截断 600 字）

        if not used:  # 没有任何有效内容
            return "", False  # 空上下文、未使用知识库
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")  # 追加引用指引
        return "\n".join(parts), True  # 返回拼接上下文与"已使用"标记
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")  # 记录构建失败告警
        return "", False


def _should_use_knowledge(message: str) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()  # 归一化消息文本
    if not msg:  # 空消息
        return False  # 不用知识库
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}  # 寒暄词集合

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
    if _monitor is None:  # 未初始化
        raise HTTPException(503, "服务未就绪")  # 返回 503
    return _monitor.summary()  # 返回监控摘要


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)  # 以 Prometheus 文本格式返回


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:  # 未初始化
        raise HTTPException(503, "服务未就绪")  # 返回 503
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)  # 执行完整重排检索
    return {"query": query, "results": result.data, "reranked": result.reranked}  # 返回查询、结果与重排标记


class DocInput(BaseModel):
    """
    单篇文档输入。

    title/content 必填；其余为可选 metadata（Phase A：RAG 知识库元数据），
    会随文档一起存入 ChromaDB，支持检索过滤与一致性校验。
    """
    title: str  # 文档标题（必填）
    content: str  # 文档内容（必填）
    doc_id: Optional[str] = None  # 文档 ID（自动切片时用于合并）
    category: Optional[str] = None  # 知识域分类
    product: Optional[str] = None  # 关联产品
    plan_type: Optional[str] = None  # 套餐类型
    effective_from: Optional[str] = None  # 生效开始时间
    effective_to: Optional[str] = None  # 生效结束时间
    source: Optional[str] = None  # 来源
    version: Optional[int] = None  # 版本号
    owner: Optional[str] = None  # 负责人
    tags: Optional[str] = None  # 标签
    db_table: Optional[str] = None  # 关联业务表
    # 一致性校验字段（与业务表对齐）
    policy_code: Optional[str] = None  # 退款政策编码
    incident_code: Optional[str] = None  # 故障编码
    incident_status: Optional[str] = None  # 故障状态
    refund_window_hours: Optional[int] = None  # 退款窗口（小时）
    usage_limit_percent: Optional[float] = None  # 使用量上限
    allow_refund: Optional[bool] = None  # 是否允许退款
    manual_review_required: Optional[bool] = None  # 是否需人工复核


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]  # 文档列表


class KnowledgeSearchInput(BaseModel):
    """
    知识库过滤检索请求。

    category 支持字符串或列表；product/source 等为精确过滤；
    有效期自动限定为当前日期，已过期的公告不会返回。
    """
    query: str  # 查询文本
    top_k: int = 5  # 返回条数（默认 5）
    category: Optional[Any] = None  # 知识域过滤（支持单个或列表）
    product: Optional[str] = None  # 产品过滤
    source: Optional[str] = None  # 来源过滤
    db_table: Optional[str] = None  # 业务表过滤
    plan_type: Optional[str] = None  # 套餐类型过滤
    policy_code: Optional[str] = None  # 退款政策编码过滤
    incident_code: Optional[str] = None  # 故障编码过滤


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str  # 待识别消息
    expected_intent: str  # 期望意图
    context: Optional[Dict[str, Any]] = None  # 附加上下文


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None  # 单轮问题
    turns: Optional[List[str]] = None  # 多轮对话序列
    user_id: Optional[str] = None  # 用户 ID
    conv_id: Optional[str] = None  # 会话 ID


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None  # 意图评测用例（可空）
    dialog_cases: Optional[List[EvalDialogInput]] = None  # 对话评测用例（可空）


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
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None  # 取知识检索工具
    if tool is None:  # 工具未注册
        raise HTTPException(503, "知识库未初始化")  # 返回 503
    kb = tool.handler.__self__  # 从处理器反取知识库实例
    count = kb.add_documents([d.model_dump(exclude_none=True) for d in body.documents])  # 批量导入（剔除 None 字段）
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": kb.doc_count}  # 返回导入数与总数


@app.post("/knowledge/search", tags=["知识库"])
async def knowledge_search(body: KnowledgeSearchInput):
    """
    带 metadata 过滤的语义检索。

    示例：按知识域过滤 {"query": "退款政策", "category": "refund_policy", "product": "智能录音笔 A1"}
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None  # 取知识检索工具
    if tool is None:  # 工具未注册
        raise HTTPException(503, "知识库未初始化")  # 返回 503
    kb = tool.handler.__self__  # 从处理器反取知识库实例
    where = kb._build_where(body.model_dump(exclude_none=True))  # 由过滤字段构建 Chroma where 条件
    results = kb.search(body.query, top_k=body.top_k, where=where)  # 带过滤向量检索
    return {  # 返回检索详情
        "query": body.query,  # 查询文本
        "top_k": body.top_k,  # 返回条数
        "where": where,  # 实际过滤条件
        "results": results,  # 检索结果
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
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None  # 取知识检索工具
    if tool is None:  # 工具未注册
        raise HTTPException(503, "知识库未初始化")  # 返回 503
    kb = tool.handler.__self__  # 从处理器反取知识库实例

    content = await file.read()  # 读取上传文件内容
    if len(content) > 10 * 1024 * 1024:  # 超过 10MB
        raise HTTPException(413, "文件大小超过 10MB 限制")  # 拒绝上传
    text = content.decode("utf-8", errors="ignore")  # 解码为文本（忽略非法字节）
    filename = file.filename or "unknown"  # 文件名（缺省兜底）

    if filename.endswith(".json"):  # JSON 格式
        import json as _json  # 延迟导入 JSON 模块
        try:
            docs = _json.loads(text)  # 解析 JSON
            if not isinstance(docs, list):  # 不是数组
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")  # 返回 400
        except _json.JSONDecodeError as e:  # 解析失败
            raise HTTPException(400, f"JSON 解析失败: {e}")  # 返回解析错误
    else:  # 其它后缀按纯文本文档处理
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename  # 去掉扩展名作为标题
        docs = [{"title": title, "content": text}]  # 单篇文档

    count = kb.add_documents(docs)  # 导入知识库
    return {  # 返回导入结果
        "message": f"文件 {filename} 导入成功",  # 提示消息
        "added_chunks": count,  # 新增片段数
        "total_chunks": kb.doc_count,  # 当前总片段数
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None  # 取知识检索工具
    if tool is None:  # 工具未注册
        raise HTTPException(503, "知识库未初始化")  # 返回 503
    kb = tool.handler.__self__  # 从处理器反取知识库实例
    return {"collection": kb._collection_name, "total_chunks": kb.doc_count}  # 返回集合名与片段总数


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:  # 评测器未初始化
        raise HTTPException(503, "服务未就绪")  # 返回 503
    # 延迟导入评测器默认用例与用例类型
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:  # 请求携带意图用例
        intent_cases = [  # 转换为评测器用例对象
            IntentTestCase(  # 构造意图用例
                message=c.message,  # 待识别消息
                expected_intent=c.expected_intent,  # 期望意图
                context=c.context,  # 附加上下文
            )
            for c in body.intent_cases  # 遍历请求用例
        ]
    else:  # 未提供时
        intent_cases = DEFAULT_INTENT_CASES  # 使用内置默认意图用例

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(  # 运行端到端评测
        intent_cases=intent_cases,  # 意图评测用例
        dialog_cases=dialog_cases,  # 对话评测用例
    )
    return {  # 组装评测报告
        "pass_rate":       report.pass_rate,  # 通过率
        "total":           report.total,  # 用例总数
        "passed":          report.passed,  # 通过数
        "avg_scores":      report.avg_scores,  # 平均评分
        "regressions":     report.regressions,  # 相比基线的回退项
        "recommendations": report.recommendations,  # 改进建议
        "results": [  # 逐条结果
            {
                "test_id": r.test_id,  # 用例 ID
                "passed": r.passed,  # 是否通过
                "scores": r.scores,  # 各维度评分
                "detail": r.detail,  # 详情
                "metadata": r.metadata,  # 元数据
            }
            for r in report.results  # 遍历结果
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    """交互式命令行入口（python -m api.main --cli），逐轮对话调用编排器。"""
    print(BANNER)  # 打印横幅
    print("EchoMind CLI — 输入 quit 退出\n")  # 打印使用提示

    # 延迟导入运行所需的三个组件
    from agents.agent_orchestrator import AgentOrchestrator, Request  # 编排器与请求类型
    from memory.conversation_memory import MemoryManager, MsgRole  # 记忆管理器与角色枚举
    from core.skill_loader import SkillManager  # 技能管理器

    cfg = _anthropic_cfg()  # 读取 Anthropic 配置
    skill_manager = SkillManager(  # 创建技能管理器（CLI 本地实例）
        root_dir=os.getenv("ECHOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),  # 技能根目录
        max_prompt_chars=int(os.getenv("ECHOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),  # 注入 prompt 上限
    )
    skill_manager.load()  # 加载技能
    orch = AgentOrchestrator(  # 创建编排器（CLI 走 orchestrator 链路）
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
        skill_manager=skill_manager,  # 技能管理器
    )
    mem  = MemoryManager(  # 创建记忆管理器（CLI 连本机 Redis / Chroma）
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),  # Redis 地址
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),  # ChromaDB 主机
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),  # ChromaDB 端口
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),  # ChromaDB 持久化目录
        api_key=cfg["api_key"],  # API Key
        base_url=cfg.get("base_url"),  # 可选转发地址
        model=cfg["model"],  # 模型名
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())  # 固定用户 + 新生成的会话 ID

    while True:  # 对话主循环
        try:
            msg = input("你: ").strip()  # 读取用户输入并去空白
        except (EOFError, KeyboardInterrupt):  # 用户按 Ctrl+D / Ctrl+C
            print("\n再见 ʕ•ᴥ•ʔ")  # 告别
            break  # 退出循环
        if not msg or msg.lower() in ("quit", "exit", "退出"):  # 空输入或退出指令
            print("再见 ʕ•ᴥ•ʔ")  # 告别
            break  # 退出循环

        ctx = await mem.get_context(user_id, conv_id, query=msg)  # 读取记忆上下文
        history = [  # 提取最近 5 条作为对话历史
            {"role": m.role.value, "content": m.content}  # 角色与内容
            for m in ctx.recent_messages[-5:]  # 取末尾 5 条
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)  # 构造请求
        result = await orch.run(req)  # 执行编排器

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)  # 记录用户消息
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)  # 记录助手回复

        print(f"\nEchoMind [{result.agent_type.value}]: {result.response}\n")  # 打印带 Agent 类型的回复


if __name__ == "__main__":
    # CLI 模式：python -m api.main --cli
    if "--cli" in sys.argv:
        asyncio.run(_cli())  # 进入交互式对话
    else:
        # 服务模式：启动 uvicorn
        uvicorn.run(  # 启动 Web 服务
            "api.main:app",  # 应用对象
            host=os.getenv("API_HOST", "0.0.0.0"),  # 监听地址（默认全网卡）
            port=int(os.getenv("API_PORT", "8000")),  # 端口（默认 8000）
            reload=os.getenv("APP_ENV") == "development",  # 开发环境开启热重载
        )
