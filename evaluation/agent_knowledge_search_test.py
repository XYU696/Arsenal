"""
Agent 知识库检索测试案例（AGENT-KB-001 / AGENT-KB-002）

目的：验证两件事
  1. Agent 遇到知识库类问题时，是否真的执行 knowledge_search 工具；
  2. knowledge_search 的检索效果：期望文档是否被召回、排在第几位、回答是否引用知识库内容。

预设用例来自 evaluation/datasets/rag_retrieval_eval_v1.json，
期望文档均确认存在于 knowledge_base_v2（37 篇电商售后文档）。
两个问题都会落入 FAQ 分类（白名单含 knowledge_search），且业务库无法回答，
只能依赖知识库，适合验证工具执行。

用法（容器内，依赖齐全）：

    # 只验证检索效果（不需要启动服务端；rewrite 链路需要 ANTHROPIC_API_KEY）
    docker compose run --rm --no-deps -v $PWD:/app echomind \
        python3 -m evaluation.agent_knowledge_search_test --mode retrieval --case AGENT-KB-001

    # 验证 Agent 是否执行工具（需要服务端已启动：docker compose up -d）
    docker compose exec echomind \
        python3 -m evaluation.agent_knowledge_search_test --mode agent --case AGENT-KB-001 --api http://echomind:8000

    # 从宿主机直接测 Agent（agent 模式只用标准库，无需项目依赖）
    python3 evaluation/agent_knowledge_search_test.py --mode agent --case AGENT-KB-001 --api http://localhost:8000
"""
import argparse  # 命令行参数解析
import json  # JSON 序列化 / 反序列化
import os  # 读取环境变量（Chroma 连接、API Key 等）
import sys  # 进程退出码
import urllib.request  # 发起 HTTP 请求（agent 模式调用 /chat）
import uuid  # 生成随机会话 ID
from pathlib import Path  # 路径对象
from typing import Any, Dict, List, Optional  # 类型注解

ROOT = Path(__file__).resolve().parents[1]  # 项目根目录（本文件所在目录的上一级）

# ── 预设测试用例 ──────────────────────────────────────────────────────────────
PRESET_CASES: Dict[str, Dict[str, Any]] = {
    "AGENT-KB-001": {  # 用例一：退货流程类问题
        "query": "退货上门取件要等多久",  # 查询文本
        "expected_docs": ["KB-RT-002"],  # 期望召回的知识库文档 ID
        "category": "return_process",  # 知识域过滤条件
        "note": "退货流程类问题：知识库独有内容，业务库无法回答，必须走 knowledge_search",  # 用例说明
    },
    "AGENT-KB-002": {  # 用例二：物流类问题
        "query": "上海中转的包裹延误了多久",  # 查询文本
        "expected_docs": ["KB-LG-006"],  # 期望召回的知识库文档 ID
        "category": "logistics_issue",  # 知识域过滤条件
        "note": "物流公告类问题：期望召回华东转运延迟公告（INC-LOGISTICS-20260730）",  # 用例说明
    },
}


def _pass(msg: str) -> None:
    """打印一条带 ✅ 的通过标记到控制台。"""
    # 输出测试通过信息（加缩进便于与用例标题区分）
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    """打印一条带 ❌ 的失败标记到控制台。"""
    # 输出测试失败信息（加缩进便于与用例标题区分）
    print(f"  ❌ {msg}")


def _kb_config() -> Dict[str, Any]:
    """
    组装 KnowledgeBase 的初始化参数。

    所有值都允许用环境变量覆盖，未设置时回落到默认值。

    参数：
        无

    返回：
        KnowledgeBase 构造参数字典（Chroma 连接信息 + 集合名）
    """
    # 返回配置字典
    return {
        "chroma_host": os.getenv("CHROMA_HOST", "localhost"),  # ChromaDB 主机名
        "chroma_port": int(os.getenv("CHROMA_PORT", "8000")),  # ChromaDB 端口号
        "chroma_path": os.getenv("CHROMA_PERSIST_DIRECTORY", str(ROOT / "data" / "chroma")),  # Chroma 持久化目录
        "collection_name": "knowledge_base_v2",  # 知识库集合名
    }


# ── 模式一：检索效果（hybrid + rewrite 完整链路）──────────────────────────────
def run_retrieval(query: str, expected_docs: List[str], category: Optional[str], top_k: int, run_rewrite: bool) -> int:
    """
    模式一：直接验证 knowledge_search 的检索效果（不经过 Agent）。

    依次做三件事：
      0) 校验期望文档真实存在；
      1) 混合检索（向量 + BM25）看期望文档是否被召回及位置；
      2) 完整 rewrite 链路（查询改写 → 混合召回 → 去重 → 重排）。

    参数：
        query: 查询文本
        expected_docs: 期望召回的文档 ID 列表
        category: 知识域过滤条件（None 表示不过滤）
        top_k: 检索返回条数
        run_rewrite: 是否执行完整 rewrite 链路（需要 ANTHROPIC_API_KEY）

    返回：
        0 表示主检查（混合检索）全部通过，1 表示有失败
    """
    # 延迟导入知识库类（retrieval 模式才需要项目依赖）
    from mcp.knowledge_base import KnowledgeBase

    ok = True  # 整体通过标记（主检查以混合检索结果为准）
    # 实例化知识库对象
    kb = KnowledgeBase(**_kb_config())
    # 打印当前集合名和片段数
    print(f"collection: {kb._collection_name}，现有 {kb.doc_count} 个片段")

    # 0) 期望文档必须真实存在于知识库
    # 取 BM25 快照（内含全部文档的元数据）
    snap = kb._get_bm25_snapshot()
    # 收集所有已知文档 ID（去重）
    known_doc_ids = {str(m.get("doc_id")) for m in snap["metadatas"]}
    # 找出不在知识库中的期望文档
    missing = [d for d in expected_docs if d not in known_doc_ids]
    # 期望文档缺失则直接判失败
    if missing:
        # 打印缺失的文档 ID
        _fail(f"期望文档不在知识库中: {missing}")
        # 返回失败码
        return 1
    # 期望文档全部存在则通过
    _pass(f"期望文档已入库: {expected_docs}")

    # 1) 混合检索（生产工具 handler 默认路径）
    # 有分类时构造 where 过滤条件，否则不过滤
    where = {"category": {"$eq": category}} if category else None
    # 执行混合检索
    results = kb.search_hybrid(query, top_k=top_k, where=where)
    # 打印检索标题行
    print(f"\n[混合检索 hybrid] 查询: {query!r}（top_k={top_k}）")
    # 暂存本次召回的所有文档 ID
    retrieved_ids: List[str] = []
    # 遍历检索结果（rank 从 1 开始）
    for rank, item in enumerate(results, start=1):
        # 收集文档 ID
        retrieved_ids.append(str(item.get("doc_id", "")))
        # 逐条打印排名、ID、分数、标题
        print(f"  #{rank} {item.get('doc_id', '-'):<12} score={item.get('score'):<8} {item.get('title', '')}")
    # 记录期望文档被召回的位置（{doc_id: 名次}）
    hit_positions = {d: retrieved_ids.index(d) + 1 for d in expected_docs if d in retrieved_ids}
    # 期望文档全部被召回时判通过
    if len(hit_positions) == len(expected_docs):
        # 打印召回位置
        _pass(f"期望文档全部被召回，位置: {hit_positions}")
    else:
        # 有期望文档未召回则置失败标记
        ok = False
        # 打印未全部召回的明细
        _fail(f"期望文档未全部召回，命中: {hit_positions}，期望: {expected_docs}")

    # 2) 完整链路（查询改写 → 并行混合召回 → 去重 → 重排），需要 LLM
    # 仅在启用 rewrite 且设置了 API Key 时执行
    if run_rewrite and os.getenv("ANTHROPIC_API_KEY"):
        # 延迟导入 asyncio（rewrite 链路需要异步执行）
        import asyncio
        # 延迟导入工具管理器相关类
        from mcp.tool_manager import MCPToolManager, Tool

        # 组装 LLM 客户端配置
        cfg = {
            "api_key": os.getenv("ANTHROPIC_API_KEY"),  # API Key
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),  # 改写用模型
        }
        # 读取可选的 base_url（空则忽略）
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        # 非空才写入配置
        if base_url:
            cfg["base_url"] = base_url  # 自定义 API 网关地址
        # 实例化工具管理器
        tm = MCPToolManager(**cfg)
        # 注册 knowledge_search 工具（复用知识库搜索处理器）
        tm.register(Tool(
            name="knowledge_search",  # 工具名
            description="搜索知识库（向量 + BM25 混合检索）",  # 工具描述
            handler=kb.search_handler,  # 工具执行函数
            schema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]},  # 参数 JSON Schema
            cache_ttl=300.0,  # 缓存 300 秒
            supports_rerank=True,  # 支持 LLM 重排
            use_rewrite=True,  # 启用查询改写
        ))
        # 有分类时把过滤条件作为额外参数传入
        extra = {"category": category} if category else None
        # 执行完整 rewrite 检索链路（同步包一层 asyncio.run）
        result = asyncio.run(tm.search_with_rewrite("knowledge_search", query, top_k=top_k, extra_params=extra))
        # 打印 rewrite 链路标题行
        print(f"\n[完整链路 rewrite] 查询: {query!r}（top_k={top_k}）")
        # 链路成功且数据是列表时解析召回结果
        if result.success and isinstance(result.data, list):
            # 暂存 rewrite 链路召回的所有文档 ID
            rw_ids: List[str] = []
            # 遍历重排后的结果
            for rank, item in enumerate(result.data, start=1):
                # 收集文档 ID
                rw_ids.append(str(item.get("doc_id", "")))
                # 逐条打印排名、ID、分数、标题
                print(f"  #{rank} {item.get('doc_id', '-'):<12} score={item.get('score'):<8} {item.get('title', '')}")
            # 记录期望文档在 rewrite 链路的召回位置
            rw_hit = {d: rw_ids.index(d) + 1 for d in expected_docs if d in rw_ids}
            # 期望文档全部被召回时判通过
            if len(rw_hit) == len(expected_docs):
                # 打印召回位置
                _pass(f"rewrite 链路召回期望文档，位置: {rw_hit}")
            else:
                # 未全部召回只警告（混合检索已达标，可作重排优化项）
                print(f"  ⚠️ rewrite 链路未全部召回，命中: {rw_hit}（混合检索已达标，可作为重排优化项）")
        else:
            # 链路失败时只警告，不影响主检查结论
            print(f"  ⚠️ rewrite 链路失败: {result.error}")
    else:
        # 未配置 API Key 时跳过 rewrite 链路
        print("\n[完整链路 rewrite] 跳过（未设置 ANTHROPIC_API_KEY）")

    # 主检查（混合检索）全部通过则返回 0
    return 0 if ok else 1


# ── 模式二：Agent 是否执行 knowledge_search（走 HTTP /chat）─────────────────
def run_agent(api: str, query: str, expected_docs: List[str]) -> int:
    """
    模式二：走 HTTP 调 /chat，验证 Agent 真实执行了 knowledge_search。

    三个断言：
      1) 响应中 knowledge_used=True；
      2) trace 中有 knowledge_search 工具执行节点；
      3) 工具返回结果包含期望文档（兼容 dict 或截断字符串）。

    参数：
        api: 服务端地址（如 http://localhost:8000）
        query: 发送给 Agent 的用户消息
        expected_docs: 期望被召回的知识库文档 ID 列表

    返回：
        0 表示三个断言全部通过，1 表示有失败
    """
    # 组装 /chat 请求体
    payload = {
        "message": query,  # 用户消息
        "user_id": "test-user",  # 测试用户 ID
        "conv_id": f"test-{uuid.uuid4().hex[:12]}",  # 随机会话 ID
    }
    # 构造 HTTP POST 请求
    req = urllib.request.Request(
        api.rstrip("/") + "/chat",  # 拼接 /chat 接口地址
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),  # 请求体（JSON → UTF-8 字节）
        headers={"Content-Type": "application/json"},  # 请求头
    )
    # 发起请求并解析响应
    try:
        # 发送并读取响应（180 秒超时）
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))  # 响应体解析为 JSON
    except Exception as ex:
        # 调用失败时打印错误
        _fail(f"调用 /chat 失败: {ex}")
        # 给出排查提示
        print("  提示：请确认服务端已启动（docker compose up -d），且 --api 指向正确地址。")
        # 返回失败码
        return 1

    ok = True  # 三个断言的总体通过标记
    # 打印当前查询
    print(f"查询: {query!r}")
    # 打印意图和回复摘要
    print(f"意图: {data.get('intent')} | 最终回复: {data.get('response', '')[:120]}...")

    # 断言 1：knowledge_used
    # 响应中标记使用了知识库内容
    if data.get("knowledge_used"):
        # 打印通过信息
        _pass("knowledge_used=True，本轮回复使用了知识库内容")
    else:
        # 未使用知识库则置失败标记
        ok = False
        # 打印失败信息
        _fail("knowledge_used=False，Agent 未使用知识库内容")

    # 断言 2：trace 中出现 knowledge_search 工具执行记录
    # 过滤出 knowledge_search 的工具执行节点
    tool_steps = [
        s for s in data.get("trace", [])  # 遍历 trace 节点
        if s.get("node") == "tool" and s.get("tool_name") == "knowledge_search"  # 命中工具节点
    ]
    # 存在工具执行记录时判通过
    if tool_steps:
        # 打印执行次数
        _pass(f"trace 中检测到 knowledge_search 执行（{len(tool_steps)} 次）")
    else:
        # 未执行工具则置失败标记
        ok = False
        # 打印失败信息
        _fail("trace 中未检测到 knowledge_search 执行")

    # 断言 3：检索结果里包含期望文档（tool_result 可能被截断成字符串，做子串匹配）
    # 初始化命中表（{doc_id: 是否命中}）
    hit = {d: False for d in expected_docs}
    # 遍历每个工具执行节点
    for step in tool_steps:
        # 取该节点的工具结果（缺失时按空字典处理）
        tr = step.get("tool_result") or {}
        # 结果为字典时钻取 data 字段
        if isinstance(tr, dict):
            # 取结果数据列表（缺失时按空列表处理）
            items = tr.get("data") or []
            # 遍历每条检索结果
            for item in items if isinstance(items, list) else []:
                # 结果是字典且 doc_id 命中期望文档时标记命中
                if isinstance(item, dict) and str(item.get("doc_id", "")) in hit:
                    hit[str(item.get("doc_id", ""))] = True  # 标记该文档已命中
        # 结果为截断字符串时做子串匹配
        elif isinstance(tr, str):
            # 遍历期望文档做子串判断
            for doc_id in expected_docs:
                # 文档 ID 出现在字符串中即命中
                if doc_id in tr:
                    hit[doc_id] = True  # 标记该文档已命中
    # 全部期望文档命中时判通过
    if all(hit.values()):
        # 打印通过信息
        _pass(f"检索结果包含期望文档: {expected_docs}")
    else:
        # 有未命中文档则置失败标记
        ok = False
        # 打印未命中的文档 ID（回答引用了内容仍算通过）
        _fail(f"检索结果未包含期望文档: {[d for d, h in hit.items() if not h]}")

    # 三个断言全部通过则返回 0
    return 0 if ok else 1


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    参数：
        无

    返回：
        解析后的参数对象（argparse.Namespace）
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="Agent knowledge_search 测试案例")
    # 模式：检索效果 or Agent 链路
    parser.add_argument("--mode", choices=["retrieval", "agent"], default="retrieval")
    # 预设用例 ID
    parser.add_argument("--case", choices=list(PRESET_CASES), default="AGENT-KB-001")
    # 覆盖预设查询文本
    parser.add_argument("--query", default="", help="覆盖预设查询")
    # 覆盖期望文档（逗号分隔）
    parser.add_argument("--expected", default="", help="覆盖期望文档（逗号分隔）")
    # 覆盖知识域过滤条件
    parser.add_argument("--category", default=None, help="覆盖知识域过滤")
    # 检索返回条数
    parser.add_argument("--top-k", type=int, default=5)
    # agent 模式的服务端地址
    parser.add_argument("--api", default="http://localhost:8000", help="agent 模式的服务端地址")
    # retrieval 模式跳过 rewrite 链路
    parser.add_argument("--no-rewrite", action="store_true", help="retrieval 模式跳过 rewrite 链路")
    # 仅列出预设用例
    parser.add_argument("--list-cases", action="store_true", help="列出预设用例")
    # 解析并返回命令行参数
    return parser.parse_args()


def main() -> int:
    """
    程序入口：分发到 retrieval 或 agent 两种测试模式。

    参数：
        无

    返回：
        进程退出码（0 成功，1 失败）
    """
    # 解析命令行参数
    args = parse_args()
    # 仅列出用例时打印所有预设用例
    if args.list_cases:
        # 遍历预设用例
        for cid, case in PRESET_CASES.items():
            # 打印用例摘要（ID、查询、期望文档、说明）
            print(f"{cid}: {case['query']} → {case['expected_docs']}（{case['note']}）")
        # 列表模式直接成功返回
        return 0

    # 取当前用例定义
    case = PRESET_CASES[args.case]
    # 查询文本（命令行覆盖优先，否则用预设值）
    query = args.query or case["query"]
    # 期望文档（命令行覆盖优先，否则用预设值）
    expected = [d.strip() for d in args.expected.split(",") if d.strip()] or case["expected_docs"]
    # 知识域过滤条件（None 表示用预设值中的 category）
    category = args.category if args.category is not None else case.get("category")

    # 打印本次测试的用例摘要
    print(f"用例 {args.case}: {query!r} → 期望 {expected}（{case['note']}）\n")
    # agent 模式走 HTTP 验证 Agent 行为
    if args.mode == "agent":
        return run_agent(args.api, query, expected)
    # retrieval 模式直接验证检索效果
    return run_retrieval(query, expected, category, args.top_k, run_rewrite=not args.no_rewrite)


if __name__ == "__main__":
    # 直接运行时以返回码退出进程
    sys.exit(main())