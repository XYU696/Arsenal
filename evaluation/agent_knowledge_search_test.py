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
import argparse
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

# ── 预设测试用例 ──────────────────────────────────────────────────────────────
PRESET_CASES: Dict[str, Dict[str, Any]] = {
    "AGENT-KB-001": {
        "query": "退货上门取件要等多久",
        "expected_docs": ["KB-RT-002"],
        "category": "return_process",
        "note": "退货流程类问题：知识库独有内容，业务库无法回答，必须走 knowledge_search",
    },
    "AGENT-KB-002": {
        "query": "上海中转的包裹延误了多久",
        "expected_docs": ["KB-LG-006"],
        "category": "logistics_issue",
        "note": "物流公告类问题：期望召回华东转运延迟公告（INC-LOGISTICS-20260730）",
    },
}


def _pass(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _kb_config() -> Dict[str, Any]:
    return {
        "chroma_host": os.getenv("CHROMA_HOST", "localhost"),
        "chroma_port": int(os.getenv("CHROMA_PORT", "8000")),
        "chroma_path": os.getenv("CHROMA_PERSIST_DIRECTORY", str(ROOT / "data" / "chroma")),
        "collection_name": "knowledge_base_v2",
    }


# ── 模式一：检索效果（hybrid + rewrite 完整链路）──────────────────────────────
def run_retrieval(query: str, expected_docs: List[str], category: Optional[str], top_k: int, run_rewrite: bool) -> int:
    from mcp.knowledge_base import KnowledgeBase

    ok = True
    kb = KnowledgeBase(**_kb_config())
    print(f"collection: {kb._collection_name}，现有 {kb.doc_count} 个片段")

    # 0) 期望文档必须真实存在于知识库
    snap = kb._get_bm25_snapshot()
    known_doc_ids = {str(m.get("doc_id")) for m in snap["metadatas"]}
    missing = [d for d in expected_docs if d not in known_doc_ids]
    if missing:
        _fail(f"期望文档不在知识库中: {missing}")
        return 1
    _pass(f"期望文档已入库: {expected_docs}")

    # 1) 混合检索（生产工具 handler 默认路径）
    where = {"category": {"$eq": category}} if category else None
    results = kb.search_hybrid(query, top_k=top_k, where=where)
    print(f"\n[混合检索 hybrid] 查询: {query!r}（top_k={top_k}）")
    retrieved_ids: List[str] = []
    for rank, item in enumerate(results, start=1):
        retrieved_ids.append(str(item.get("doc_id", "")))
        print(f"  #{rank} {item.get('doc_id', '-'):<12} score={item.get('score'):<8} {item.get('title', '')}")
    hit_positions = {d: retrieved_ids.index(d) + 1 for d in expected_docs if d in retrieved_ids}
    if len(hit_positions) == len(expected_docs):
        _pass(f"期望文档全部被召回，位置: {hit_positions}")
    else:
        ok = False
        _fail(f"期望文档未全部召回，命中: {hit_positions}，期望: {expected_docs}")

    # 2) 完整链路（查询改写 → 并行混合召回 → 去重 → 重排），需要 LLM
    if run_rewrite and os.getenv("ANTHROPIC_API_KEY"):
        import asyncio
        from mcp.tool_manager import MCPToolManager, Tool

        cfg = {
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        }
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        if base_url:
            cfg["base_url"] = base_url
        tm = MCPToolManager(**cfg)
        tm.register(Tool(
            name="knowledge_search",
            description="搜索知识库（向量 + BM25 混合检索）",
            handler=kb.search_handler,
            schema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]},
            cache_ttl=300.0,
            supports_rerank=True,
            use_rewrite=True,
        ))
        extra = {"category": category} if category else None
        result = asyncio.run(tm.search_with_rewrite("knowledge_search", query, top_k=top_k, extra_params=extra))
        print(f"\n[完整链路 rewrite] 查询: {query!r}（top_k={top_k}）")
        if result.success and isinstance(result.data, list):
            rw_ids: List[str] = []
            for rank, item in enumerate(result.data, start=1):
                rw_ids.append(str(item.get("doc_id", "")))
                print(f"  #{rank} {item.get('doc_id', '-'):<12} score={item.get('score'):<8} {item.get('title', '')}")
            rw_hit = {d: rw_ids.index(d) + 1 for d in expected_docs if d in rw_ids}
            if len(rw_hit) == len(expected_docs):
                _pass(f"rewrite 链路召回期望文档，位置: {rw_hit}")
            else:
                print(f"  ⚠️ rewrite 链路未全部召回，命中: {rw_hit}（混合检索已达标，可作为重排优化项）")
        else:
            print(f"  ⚠️ rewrite 链路失败: {result.error}")
    else:
        print("\n[完整链路 rewrite] 跳过（未设置 ANTHROPIC_API_KEY）")

    return 0 if ok else 1


# ── 模式二：Agent 是否执行 knowledge_search（走 HTTP /chat）─────────────────
def run_agent(api: str, query: str, expected_docs: List[str]) -> int:
    payload = {
        "message": query,
        "user_id": "test-user",
        "conv_id": f"test-{uuid.uuid4().hex[:12]}",
    }
    req = urllib.request.Request(
        api.rstrip("/") + "/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as ex:
        _fail(f"调用 /chat 失败: {ex}")
        print("  提示：请确认服务端已启动（docker compose up -d），且 --api 指向正确地址。")
        return 1

    ok = True
    print(f"查询: {query!r}")
    print(f"意图: {data.get('intent')} | 最终回复: {data.get('response', '')[:120]}...")

    # 断言 1：knowledge_used
    if data.get("knowledge_used"):
        _pass("knowledge_used=True，本轮回复使用了知识库内容")
    else:
        ok = False
        _fail("knowledge_used=False，Agent 未使用知识库内容")

    # 断言 2：trace 中出现 knowledge_search 工具执行记录
    tool_steps = [
        s for s in data.get("trace", [])
        if s.get("node") == "tool" and s.get("tool_name") == "knowledge_search"
    ]
    if tool_steps:
        _pass(f"trace 中检测到 knowledge_search 执行（{len(tool_steps)} 次）")
    else:
        ok = False
        _fail("trace 中未检测到 knowledge_search 执行")

    # 断言 3：检索结果里包含期望文档（tool_result 可能被截断成字符串，做子串匹配）
    hit = {d: False for d in expected_docs}
    for step in tool_steps:
        tr = step.get("tool_result") or {}
        if isinstance(tr, dict):
            items = tr.get("data") or []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and str(item.get("doc_id", "")) in hit:
                    hit[str(item.get("doc_id", ""))] = True
        elif isinstance(tr, str):
            for doc_id in expected_docs:
                if doc_id in tr:
                    hit[doc_id] = True
    if all(hit.values()):
        _pass(f"检索结果包含期望文档: {expected_docs}")
    else:
        ok = False
        _fail(f"检索结果未包含期望文档: {[d for d, h in hit.items() if not h]}")

    return 0 if ok else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent knowledge_search 测试案例")
    parser.add_argument("--mode", choices=["retrieval", "agent"], default="retrieval")
    parser.add_argument("--case", choices=list(PRESET_CASES), default="AGENT-KB-001")
    parser.add_argument("--query", default="", help="覆盖预设查询")
    parser.add_argument("--expected", default="", help="覆盖期望文档（逗号分隔）")
    parser.add_argument("--category", default=None, help="覆盖知识域过滤")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--api", default="http://localhost:8000", help="agent 模式的服务端地址")
    parser.add_argument("--no-rewrite", action="store_true", help="retrieval 模式跳过 rewrite 链路")
    parser.add_argument("--list-cases", action="store_true", help="列出预设用例")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_cases:
        for cid, case in PRESET_CASES.items():
            print(f"{cid}: {case['query']} → {case['expected_docs']}（{case['note']}）")
        return 0

    case = PRESET_CASES[args.case]
    query = args.query or case["query"]
    expected = [d.strip() for d in args.expected.split(",") if d.strip()] or case["expected_docs"]
    category = args.category if args.category is not None else case.get("category")

    print(f"用例 {args.case}: {query!r} → 期望 {expected}（{case['note']}）\n")
    if args.mode == "agent":
        return run_agent(args.api, query, expected)
    return run_retrieval(query, expected, category, args.top_k, run_rewrite=not args.no_rewrite)


if __name__ == "__main__":
    sys.exit(main())
