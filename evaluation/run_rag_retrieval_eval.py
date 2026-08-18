"""
RAG 检索层评测脚本（Phase C-0 基线 / Phase B 对比）。

目的：用同一数据集对比三种检索模式，量化 Phase B 检索质量改进：
  - direct  ：直接向量检索（基线）
  - hybrid  ：向量 + BM25 + RRF 融合（Phase B 新增）
  - rewrite ：查询改写 → 混合召回 → LLM 重排（Phase B 新增，与 Agent 工具路径一致）

指标：
  - Recall@k      ：期望文档被召回的比例
  - Precision@k   ：召回结果中相关文档占比
  - MRR           ：第一个相关文档排名的倒数均值
  - NDCG@k        ：排序质量（相关文档越靠前越高）
  - 过滤合规率     ：返回结果的 metadata 是否满足用例的 category/product 过滤

用法：
    python3 -m evaluation.run_rag_retrieval_eval --mode direct --note baseline-direct-v2kb
    python3 -m evaluation.run_rag_retrieval_eval --mode direct --no-filters   # 纯向量、不带过滤
    python3 -m evaluation.run_rag_retrieval_eval --mode hybrid                # Phase B 混合检索
    python3 -m evaluation.run_rag_retrieval_eval --mode rewrite               # Phase B 完整链路（需 ANTHROPIC_API_KEY）
    python3 -m evaluation.run_rag_retrieval_eval --compare <baseline.json> --note after-hybrid
"""
import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "rag_retrieval_eval_v1.json"
DEFAULT_KB_DOCS = ROOT / "data" / "knowledge" / "after_sales_v2.json"
REPORT_DIR = ROOT / "evaluation" / "reports"

# 用例中可作为检索过滤条件的字段
FILTER_KEYS = ("category", "product", "source", "db_table", "plan_type", "policy_code", "incident_code")


# ── 指标计算（纯函数，可独立单测）────────────────────────────────────────────

def recall_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    hit = sum(1 for d in retrieved[:k] if d in expected)
    return hit / len(expected) if expected else 0.0


def precision_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    if not retrieved:
        return 0.0
    hit = sum(1 for d in retrieved[:k] if d in expected)
    return hit / min(k, len(retrieved))


def mrr(retrieved: List[str], expected: List[str], k: int) -> float:
    for rank, doc in enumerate(retrieved[:k], start=1):
        if doc in expected:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    rel = [1.0 if doc in expected else 0.0 for doc in retrieved[:k]]
    if not any(rel):
        return 0.0
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(expected), k)))
    return dcg / idcg if idcg else 0.0


def filter_compliance(retrieved: List[Dict[str, Any]], case: Dict[str, Any]) -> Optional[float]:
    """
    返回结果中满足用例过滤条件的比例；用例未带过滤字段或结果为空时返回 None。
    """
    checks = [key for key in FILTER_KEYS if case.get(key)]
    if not checks or not retrieved:
        return None
    ok = sum(
        1 for item in retrieved
        if all(str(item.get(key, "")) == str(case[key]) for key in checks)
    )
    return ok / len(retrieved)


def dedupe_by_doc_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一文档的多块（chunk）按 doc_id 去重，保留第一块。"""
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for item in items:
        doc_id = str(item.get("doc_id", ""))
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            unique.append(item)
    return unique


def case_metrics(retrieved: List[Dict[str, Any]], case: Dict[str, Any], k: int) -> Dict[str, Any]:
    unique = dedupe_by_doc_id(retrieved)
    doc_ids = [item["doc_id"] for item in unique]
    expected = case.get("expected_doc_ids", [])
    return {
        "expected": expected,
        "retrieved": doc_ids,
        "recall@k": round(recall_at_k(doc_ids, expected, k), 4),
        "precision@k": round(precision_at_k(doc_ids, expected, k), 4),
        "mrr": round(mrr(doc_ids, expected, k), 4),
        "ndcg@k": round(ndcg_at_k(doc_ids, expected, k), 4),
        "filter_compliance": filter_compliance(unique, case),
    }


def aggregate(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对全部用例求指标均值，过滤合规率仅统计适用用例。"""
    n = len(cases)
    if n == 0:
        return {}
    keys = ("recall@k", "precision@k", "mrr", "ndcg@k")
    summary = {key: round(sum(c[key] for c in cases) / n, 4) for key in keys}
    compliance = [c["filter_compliance"] for c in cases if c["filter_compliance"] is not None]
    summary["filter_compliance"] = round(sum(compliance) / len(compliance), 4) if compliance else None
    summary["empty_result_rate"] = round(
        sum(1 for c in cases if not c["retrieved"]) / n, 4
    )
    summary["total_cases"] = n
    return summary


# ── 检索器（模式可扩展）───────────────────────────────────────────────────────

def _case_where_params(case: Dict[str, Any]) -> Dict[str, Any]:
    return {key: case[key] for key in FILTER_KEYS if case.get(key)}


def retrieve_direct(kb: Any, case: Dict[str, Any], top_k: int, use_filters: bool) -> List[Dict[str, Any]]:
    """直接向量检索（当前 Agent 工具路径），可选 metadata 过滤。"""
    where = kb._build_where(_case_where_params(case)) if use_filters else None
    return kb.search(case["query"], top_k=top_k, where=where)


def retrieve_hybrid(kb: Any, case: Dict[str, Any], top_k: int, use_filters: bool) -> List[Dict[str, Any]]:
    """Phase B 混合检索：向量 + BM25 + RRF 融合，可选 metadata 过滤。"""
    where = kb._build_where(_case_where_params(case)) if use_filters else None
    return kb.search_hybrid(case["query"], top_k=top_k, where=where)


def _anthropic_cfg() -> Dict[str, Any]:
    """从环境变量读取 Anthropic 兼容配置（与 api/main.py 保持一致）。"""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY；rewrite 模式需要 LLM 查询改写与重排")
    cfg: Dict[str, Any] = {
        "api_key": key,
        "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


async def _retrieve_rewrite_cases(
    kb: Any,
    cases: List[Dict[str, Any]],
    top_k: int,
    use_filters: bool,
) -> List[List[Dict[str, Any]]]:
    """rewrite 模式：查询改写 → 并行混合召回 → LLM 重排（与 Agent 工具路径一致）。"""
    from mcp.tool_manager import MCPToolManager, Tool

    cfg = _anthropic_cfg()
    tm = MCPToolManager(api_key=cfg["api_key"], base_url=cfg.get("base_url"), model=cfg["model"])
    tm.register(Tool(
        name="knowledge_search",
        description="搜索知识库（向量 + BM25 混合检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "category": {"type": "string"},
                "product": {"type": "string"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        use_rewrite=True,
    ))

    results: List[List[Dict[str, Any]]] = []
    for case in cases:
        params = _case_where_params(case) if use_filters else {}
        result = await tm.search_with_rewrite(
            "knowledge_search",
            case["query"],
            top_k=top_k,
            extra_params=params,
        )
        if result.success and isinstance(result.data, list):
            results.append(result.data)
        else:
            results.append([])
    return results


# ── 报告输出 ──────────────────────────────────────────────────────────────────

def write_reports(report: Dict[str, Any]) -> Path:
    ts = report["created_at"].replace(":", "").replace("-", "")
    name = f"rag_retrieval_{report['mode']}_{ts}"
    json_path = REPORT_DIR / f"{name}.json"
    md_path = REPORT_DIR / f"{name}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path


def render_markdown(report: Dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        f"# RAG 检索层评测报告（{report['mode']}）",
        "",
        f"- 数据集: {report['dataset']}",
        f"- 模式: {report['mode']} | top_k={report['top_k']} | 过滤: {report['filters_applied']}",
        f"- 说明: {report['note']}",
        f"- 时间: {report['created_at']}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| Recall@{report['top_k']} | {m['recall@k']} |",
        f"| Precision@{report['top_k']} | {m['precision@k']} |",
        "| MRR | " + str(m["mrr"]) + " |",
        f"| NDCG@{report['top_k']} | {m['ndcg@k']} |",
        f"| 过滤合规率 | {m.get('filter_compliance', '-')} |",
        f"| 空结果率 | {m['empty_result_rate']} |",
        "",
        "## 逐用例",
        "",
        "| ID | 查询 | 期望 | 召回 | Recall | MRR |",
        "|---|---|---|---|---|---|",
    ]
    for c in report["per_case"]:
        lines.append(
            f"| {c['id']} | {c['query'][:24]} | {','.join(c['expected'])} "
            f"| {','.join(c['retrieved'])} | {c['recall@k']} | {c['mrr']} |"
        )
    return "\n".join(lines)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EchoMind RAG retrieval-layer evaluation.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--kb-docs", default=str(DEFAULT_KB_DOCS), help="知识库文档 JSON（用于校验期望文档 ID）")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--mode", choices=["direct", "rewrite", "hybrid"], default="direct")
    parser.add_argument("--no-filters", action="store_true", help="检索时不带 metadata 过滤（纯向量基线）")
    parser.add_argument("--collection", default="knowledge_base_v2")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--path", default=str(ROOT / "data" / "chroma"))
    parser.add_argument("--note", default="")
    parser.add_argument("--compare", default="", help="与历史报告 JSON 对比（输出指标变化）")
    parser.add_argument("--new", default="", help="对比的新报告 JSON 路径（默认取 reports 目录最新一份）")
    return parser.parse_args()


def load_cases(path: Path, known_doc_ids: set[str]) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for case in data:
        assert case.get("id") and case.get("query") and case.get("expected_doc_ids"), f"用例字段缺失: {case}"
        assert case["id"] not in seen, f"用例 ID 重复: {case['id']}"
        seen.add(case["id"])
        unknown = set(case["expected_doc_ids"]) - known_doc_ids
        assert not unknown, f"{case['id']} 期望文档不存在于知识库: {unknown}"
    return data


def main() -> int:
    args = parse_args()
    known_ids = {
        str(doc.get("doc_id"))
        for doc in json.loads(Path(args.kb_docs).read_text(encoding="utf-8"))
    }
    cases = load_cases(Path(args.dataset), known_ids)
    print(f"评测集: {len(cases)} 条用例（{Path(args.dataset).name}）")

    # 对比模式：不再跑检索，直接对比两份报告
    if args.compare:
        return compare_reports(args.compare, args.note, args.top_k, args.new)

    # 延迟导入 KnowledgeBase，避免无 chromadb 环境时指标单测不可用
    from mcp.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(
        chroma_host=args.host,
        chroma_port=args.port,
        chroma_path=args.path,
        collection_name=args.collection,
    )
    print(f"collection: {args.collection}，现有 {kb.doc_count} 个片段")

    use_filters = not args.no_filters
    if args.mode == "rewrite":
        retrieved_list = asyncio.run(
            _retrieve_rewrite_cases(kb, cases, args.top_k, use_filters)
        )
    elif args.mode == "hybrid":
        retrieved_list = [
            retrieve_hybrid(kb, case, args.top_k, use_filters)
            for case in cases
        ]
    else:
        retrieved_list = [
            retrieve_direct(kb, case, args.top_k, use_filters)
            for case in cases
        ]

    per_case: List[Dict[str, Any]] = []
    for case, retrieved in zip(cases, retrieved_list):
        metrics = case_metrics(retrieved, case, args.top_k)
        metrics["id"] = case["id"]
        metrics["query"] = case["query"]
        per_case.append(metrics)

    report = {
        "dataset": str(Path(args.dataset).name),
        "mode": args.mode,
        "top_k": args.top_k,
        "filters_applied": use_filters,
        "note": args.note,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": aggregate(per_case),
        "per_case": per_case,
    }
    json_path = write_reports(report)
    print("\n汇总指标:")
    for key, value in report["metrics"].items():
        print(f"  {key}: {value}")
    print(f"\n报告已写入: {json_path}")
    return 0


def compare_reports(baseline_path: str, note: str, top_k: int, new_path: str = "") -> int:
    """对比 baseline 与新的评测报告（默认取 reports 目录最新一份，可用 --new 指定）。"""
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    if new_path:
        new_file = Path(new_path)
    else:
        candidates = [
            p for p in sorted(REPORT_DIR.glob("rag_retrieval_*.json"))
            if str(p) != str(Path(baseline_path).resolve())
        ]
        if not candidates:
            print("未找到可对比的新报告，请先运行一次评测生成新报告", file=sys.stderr)
            return 2
        new_file = candidates[-1]
    new = json.loads(new_file.read_text(encoding="utf-8"))
    print(f"对比: {Path(baseline_path).name} vs {new_file.name}")
    print("| 指标 | baseline | new | 变化 |")
    print("|---|---|---|---|")
    for key in ("recall@k", "precision@k", "mrr", "ndcg@k", "filter_compliance"):
        b = baseline["metrics"].get(key)
        n = new["metrics"].get(key)
        if b is None or n is None:
            continue
        delta = round(n - b, 4)
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
        print(f"| {key} | {b} | {n} | {delta} {arrow} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
