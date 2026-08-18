"""
电商售后知识库初始化脚本。

将 data/knowledge/after_sales_v2.json 中的 30 篇带 metadata 的文档
导入 ChromaDB 的 knowledge_base_v2 collection。

用法：
    python3 -m integrations.knowledge_base.init_knowledge           # 增量导入
    python3 -m integrations.knowledge_base.init_knowledge --reset   # 清空后重建
    python3 -m integrations.knowledge_base.init_knowledge --check   # 导入后跑一致性校验
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCS_PATH = ROOT / "data" / "knowledge" / "after_sales_v2.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize EchoMind after-sales RAG knowledge base.")
    parser.add_argument("--docs", default=str(DEFAULT_DOCS_PATH), help="知识库文档 JSON 路径")
    parser.add_argument("--collection", default="knowledge_base_v2", help="ChromaDB collection 名称")
    parser.add_argument("--host", default=os.getenv("CHROMA_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHROMA_PORT", "8000")))
    parser.add_argument("--path", default=os.getenv("CHROMA_PERSIST_DIRECTORY", str(ROOT / "data" / "chroma")))
    parser.add_argument("--reset", action="store_true", help="清空目标 collection 后重新导入")
    parser.add_argument("--check", action="store_true", help="导入完成后运行政策/故障一致性校验")
    return parser.parse_args()


def load_docs(path: Path) -> List[Dict[str, Any]]:
    """加载并校验知识库文档 JSON。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("文档文件应为 JSON 数组")

    required = {"doc_id", "title", "content", "category"}
    seen: set[str] = set()
    for doc in data:
        missing = required - set(doc)
        if missing:
            raise ValueError(f"文档缺少字段 {sorted(missing)}: {doc.get('doc_id', '?')}")
        if doc["doc_id"] in seen:
            raise ValueError(f"doc_id 重复: {doc['doc_id']}")
        seen.add(doc["doc_id"])
    return data


def main() -> int:
    args = parse_args()
    docs_path = Path(args.docs)
    if not docs_path.exists():
        print(f"文档文件不存在: {docs_path}", file=sys.stderr)
        return 1

    docs = load_docs(docs_path)
    print(f"加载文档: {len(docs)} 篇（{docs_path.name}）")

    # 延迟导入 KnowledgeBase，避免无 chromadb 环境时脚本不可用（--help 等仍可用）
    from mcp.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(
        chroma_host=args.host,
        chroma_port=args.port,
        chroma_path=args.path,
        collection_name=args.collection,
    )

    if args.reset:
        kb.clear()
        print(f"已清空 collection: {args.collection}")

    added = kb.add_documents(docs)
    print(f"导入完成: {added} 个文档片段，collection 现有 {kb.doc_count} 个片段")

    if args.check:
        from integrations.knowledge_base.consistency_check import run_consistency_check
        ok = run_consistency_check(docs)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
