"""
电商售后知识库初始化脚本。

将 data/knowledge/after_sales_v2.json 中的 30 篇带 metadata 的文档
导入 ChromaDB 的 knowledge_base_v2 collection。

用法：
    python3 -m integrations.knowledge_base.init_knowledge           # 增量导入
    python3 -m integrations.knowledge_base.init_knowledge --reset   # 清空后重建
    python3 -m integrations.knowledge_base.init_knowledge --check   # 导入后跑一致性校验
"""
import argparse  # 命令行参数解析
import json  # 读取文档 JSON
import os  # 读取环境变量
import sys  # 退出码
from pathlib import Path  # 路径对象
from typing import Any, Dict, List  # 类型注解

ROOT = Path(__file__).resolve().parents[2]  # 项目根目录
DEFAULT_DOCS_PATH = ROOT / "data" / "knowledge" / "after_sales_v2.json"  # 默认文档路径


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    参数：
        无

    返回：
        解析后的参数对象（argparse.Namespace）
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="Initialize EchoMind after-sales RAG knowledge base.")
    # 文档 JSON 路径
    parser.add_argument("--docs", default=str(DEFAULT_DOCS_PATH), help="知识库文档 JSON 路径")
    # ChromaDB 集合名
    parser.add_argument("--collection", default="knowledge_base_v2", help="ChromaDB collection 名称")
    # ChromaDB 主机（环境变量可覆盖）
    parser.add_argument("--host", default=os.getenv("CHROMA_HOST", "localhost"))
    # ChromaDB 端口（环境变量可覆盖）
    parser.add_argument("--port", type=int, default=int(os.getenv("CHROMA_PORT", "8000")))
    # Chroma 持久化目录（环境变量可覆盖）
    parser.add_argument("--path", default=os.getenv("CHROMA_PERSIST_DIRECTORY", str(ROOT / "data" / "chroma")))
    # 清空后重建标志
    parser.add_argument("--reset", action="store_true", help="清空目标 collection 后重新导入")
    # 导入后一致性校验标志
    parser.add_argument("--check", action="store_true", help="导入完成后运行政策/故障一致性校验")
    # 解析并返回参数
    return parser.parse_args()


def load_docs(path: Path) -> List[Dict[str, Any]]:
    """
    加载并校验知识库文档 JSON。

    参数：
        path: 文档 JSON 文件路径

    返回：
        校验通过的文档列表

    异常：
        ValueError: 文件非数组 / 缺必需字段 / doc_id 重复
    """
    # 读取并解析 JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    # 顶层必须是数组
    if not isinstance(data, list):
        raise ValueError("文档文件应为 JSON 数组")

    # 每篇文档必需的字段
    required = {"doc_id", "title", "content", "category"}
    # 已见过的 doc_id（查重）
    seen: set[str] = set()
    # 遍历每篇文档
    for doc in data:
        # 计算缺失的必需字段
        missing = required - set(doc)
        # 缺字段时抛错
        if missing:
            raise ValueError(f"文档缺少字段 {sorted(missing)}: {doc.get('doc_id', '?')}")
        # doc_id 重复时抛错
        if doc["doc_id"] in seen:
            raise ValueError(f"doc_id 重复: {doc['doc_id']}")
        # 记录已见 doc_id
        seen.add(doc["doc_id"])
    # 返回校验通过的文档
    return data


def main() -> int:
    """
    程序入口：加载文档并导入 ChromaDB。

    参数：
        无

    返回：
        进程退出码（0 成功，非 0 失败）
    """
    # 解析命令行参数
    args = parse_args()
    # 构造文档路径对象
    docs_path = Path(args.docs)
    # 文档文件不存在时打印错误并退出
    if not docs_path.exists():
        print(f"文档文件不存在: {docs_path}", file=sys.stderr)
        return 1

    # 加载并校验文档
    docs = load_docs(docs_path)
    # 打印加载数量
    print(f"加载文档: {len(docs)} 篇（{docs_path.name}）")

    # 延迟导入 KnowledgeBase，避免无 chromadb 环境时脚本不可用（--help 等仍可用）
    from mcp.knowledge_base import KnowledgeBase

    # 实例化知识库对象
    kb = KnowledgeBase(
        chroma_host=args.host,  # ChromaDB 主机
        chroma_port=args.port,  # ChromaDB 端口
        chroma_path=args.path,  # 持久化目录
        collection_name=args.collection,  # 集合名
    )

    # 指定了 reset 时先清空集合
    if args.reset:
        # 清空目标集合
        kb.clear()
        # 打印清空结果
        print(f"已清空 collection: {args.collection}")

    # 增量导入文档并返回新增片段数
    added = kb.add_documents(docs)
    # 打印导入结果
    print(f"导入完成: {added} 个文档片段，collection 现有 {kb.doc_count} 个片段")

    # 指定了 check 时跑一致性校验
    if args.check:
        # 导入一致性校验函数
        from integrations.knowledge_base.consistency_check import run_consistency_check
        # 执行一致性校验
        ok = run_consistency_check(docs)
        # 按校验结果返回退出码
        return 0 if ok else 1
    # 成功退出
    return 0


if __name__ == "__main__":
    # 直接运行脚本时以返回码退出
    raise SystemExit(main())