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
import argparse  # 命令行参数解析
import asyncio  # 异步执行 rewrite 检索
import json  # JSON 序列化 / 反序列化
import math  # 对数计算（NDCG）
import os  # 环境变量
import sys  # 对比报告输出到 stderr
from datetime import datetime, timezone  # 报告时间戳（UTC）
from pathlib import Path  # 路径操作
from typing import Any, Dict, List, Optional  # 类型注解

ROOT = Path(__file__).resolve().parents[1]  # 项目根目录（evaluation 的上一级）
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "rag_retrieval_eval_v1.json"  # 默认评测数据集
DEFAULT_KB_DOCS = ROOT / "data" / "knowledge" / "after_sales_v2.json"  # 默认知识库文档（校验期望文档 ID）
REPORT_DIR = ROOT / "evaluation" / "reports"  # 报告输出目录

# 用例中可作为检索过滤条件的字段
FILTER_KEYS = ("category", "product", "source", "db_table", "plan_type", "policy_code", "incident_code")


# ── 指标计算（纯函数，可独立单测）────────────────────────────────────────────

def recall_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    """计算出前 k 条里命中期望文档的比例。"""
    hit = sum(1 for d in retrieved[:k] if d in expected)  # 前 k 条中命中数
    return hit / len(expected) if expected else 0.0  # 命中数 / 期望数（期望为空时返回 0）


def precision_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    """计算前 k 条召回结果中相关文档的占比。"""
    if not retrieved:  # 无召回结果
        return 0.0  # 精度视为 0
    hit = sum(1 for d in retrieved[:k] if d in expected)  # 前 k 条中命中数
    return hit / min(k, len(retrieved))  # 命中数 / 实际返回条数的上限


def mrr(retrieved: List[str], expected: List[str], k: int) -> float:
    """计算第一个相关文档排名的倒数（无相关时返回 0）。"""
    for rank, doc in enumerate(retrieved[:k], start=1):  # 遍历前 k 条并记录名次
        if doc in expected:  # 命中期望文档
            return 1.0 / rank  # 返回排名倒数
    return 0.0  # 前 k 条均未命中


def ndcg_at_k(retrieved: List[str], expected: List[str], k: int) -> float:
    """计算排序质量 NDCG@k（相关文档越靠前得分越高）。"""
    rel = [1.0 if doc in expected else 0.0 for doc in retrieved[:k]]  # 每条结果相关性标记
    if not any(rel):  # 没有任何相关结果
        return 0.0  # NDCG 为 0
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))  # 按位置折扣的真实累计增益
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(expected), k)))  # 理想排序下的最大累计增益
    return dcg / idcg if idcg else 0.0  # DCG / IDCG 归一化


def filter_compliance(retrieved: List[Dict[str, Any]], case: Dict[str, Any]) -> Optional[float]:
    """
    返回结果中满足用例过滤条件的比例；用例未带过滤字段或结果为空时返回 None。
    """
    checks = [key for key in FILTER_KEYS if case.get(key)]  # 用例实际携带的过滤字段
    if not checks or not retrieved:  # 无过滤条件或结果为空
        return None  # 返回 None 表示不适用
    ok = sum(  # 满足全部过滤条件的文档数
        1 for item in retrieved
        if all(str(item.get(key, "")) == str(case[key]) for key in checks)  # 每个过滤字段都匹配
    )
    return ok / len(retrieved)  # 合规文档占比


def dedupe_by_doc_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一文档的多块（chunk）按 doc_id 去重，保留第一块。"""
    seen: set[str] = set()  # 已见过的 doc_id 集合
    unique: List[Dict[str, Any]] = []  # 去重后的结果列表
    for item in items:  # 遍历每条检索结果
        doc_id = str(item.get("doc_id", ""))  # 取该条的文档 ID
        if doc_id and doc_id not in seen:  # ID 有效且首次出现
            seen.add(doc_id)  # 记录该 ID
            unique.append(item)  # 保留该条
    return unique  # 返回去重结果


def case_metrics(retrieved: List[Dict[str, Any]], case: Dict[str, Any], k: int) -> Dict[str, Any]:
    """汇总单条用例的检索质量指标。"""
    unique = dedupe_by_doc_id(retrieved)  # 先按文档去重
    doc_ids = [item["doc_id"] for item in unique]  # 提取文档 ID 列表
    expected = case.get("expected_doc_ids", [])  # 用例期望文档 ID 列表
    return {  # 组装单条指标
        "expected": expected,  # 期望文档
        "retrieved": doc_ids,  # 实际召回文档
        "recall@k": round(recall_at_k(doc_ids, expected, k), 4),  # 召回率
        "precision@k": round(precision_at_k(doc_ids, expected, k), 4),  # 精确率
        "mrr": round(mrr(doc_ids, expected, k), 4),  # 倒数排名均值
        "ndcg@k": round(ndcg_at_k(doc_ids, expected, k), 4),  # 排序质量
        "filter_compliance": filter_compliance(unique, case),  # 过滤合规率
    }


def aggregate(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对全部用例求指标均值，过滤合规率仅统计适用用例。"""
    n = len(cases)  # 用例总数
    if n == 0:  # 无用例
        return {}  # 返回空指标
    keys = ("recall@k", "precision@k", "mrr", "ndcg@k")  # 需聚合的指标键
    summary = {key: round(sum(c[key] for c in cases) / n, 4) for key in keys}  # 各指标算术均值
    compliance = [c["filter_compliance"] for c in cases if c["filter_compliance"] is not None]  # 适用用例的合规率
    summary["filter_compliance"] = round(sum(compliance) / len(compliance), 4) if compliance else None  # 合规率均值（无适用用例为 None）
    summary["empty_result_rate"] = round(  # 空结果率
        sum(1 for c in cases if not c["retrieved"]) / n, 4  # 空结果用例数 / 总数
    )
    summary["total_cases"] = n  # 用例总数
    return summary  # 返回聚合指标


# ── 检索器（模式可扩展）───────────────────────────────────────────────────────

def _case_where_params(case: Dict[str, Any]) -> Dict[str, Any]:
    """从用例中抽取可用作检索过滤条件的字段。"""
    return {key: case[key] for key in FILTER_KEYS if case.get(key)}  # 只取用例携带的过滤字段


def retrieve_direct(kb: Any, case: Dict[str, Any], top_k: int, use_filters: bool) -> List[Dict[str, Any]]:
    """直接向量检索（当前 Agent 工具路径），可选 metadata 过滤。"""
    where = kb._build_where(_case_where_params(case)) if use_filters else None  # 构建过滤条件（不开过滤则为 None）
    return kb.search(case["query"], top_k=top_k, where=where)  # 向量检索


def retrieve_hybrid(kb: Any, case: Dict[str, Any], top_k: int, use_filters: bool) -> List[Dict[str, Any]]:
    """Phase B 混合检索：向量 + BM25 + RRF 融合，可选 metadata 过滤。"""
    where = kb._build_where(_case_where_params(case)) if use_filters else None  # 构建过滤条件（不开过滤则为 None）
    return kb.search_hybrid(case["query"], top_k=top_k, where=where)  # 混合检索


def _anthropic_cfg() -> Dict[str, Any]:
    """从环境变量读取 Anthropic 兼容配置（与 api/main.py 保持一致）。"""
    key = os.getenv("ANTHROPIC_API_KEY", "")  # 读取 API Key
    if not key:  # 未配置 Key
        raise RuntimeError("未设置 ANTHROPIC_API_KEY；rewrite 模式需要 LLM 查询改写与重排")  # 抛错提示
    cfg: Dict[str, Any] = {  # 基础配置
        "api_key": key,  # API Key
        "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),  # 默认模型
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()  # 读取可选 base_url
    if base_url:  # 非空才写入
        cfg["base_url"] = base_url  # 写入转发地址
    return cfg  # 返回配置


async def _retrieve_rewrite_cases(
    kb: Any,  # 知识库实例
    cases: List[Dict[str, Any]],  # 评测用例列表
    top_k: int,  # 返回条数
    use_filters: bool,  # 是否带 metadata 过滤
) -> List[List[Dict[str, Any]]]:
    """rewrite 模式：查询改写 → 并行混合召回 → LLM 重排（与 Agent 工具路径一致）。"""
    from mcp.tool_manager import MCPToolManager, Tool  # 延迟导入工具管理器（避免无依赖环境报错）

    cfg = _anthropic_cfg()  # 读取 Anthropic 配置
    tm = MCPToolManager(api_key=cfg["api_key"], base_url=cfg.get("base_url"), model=cfg["model"])  # 构造工具管理器
    tm.register(Tool(  # 注册知识检索工具
        name="knowledge_search",  # 工具名
        description="搜索知识库（向量 + BM25 混合检索）",  # 工具描述
        handler=kb.search_handler,  # 处理函数
        schema={  # 参数 schema
            "type": "object",  # 参数为对象
            "properties": {  # 属性定义
                "query": {"type": "string"},  # 查询文本
                "top_k": {"type": "integer"},  # 返回条数
                "category": {"type": "string"},  # 类目过滤
                "product": {"type": "string"},  # 商品过滤
            },
            "required": ["query"],  # query 必填
        },
        cache_ttl=300.0,  # 缓存 300 秒
        supports_rerank=True,  # 支持 LLM 重排
        use_rewrite=True,  # 启用查询改写
    ))

    results: List[List[Dict[str, Any]]] = []  # 各用例检索结果
    for case in cases:  # 遍历每条用例
        params = _case_where_params(case) if use_filters else {}  # 抽取过滤参数（不开过滤则为空）
        result = await tm.search_with_rewrite(  # 改写 + 召回 + 重排
            "knowledge_search",  # 工具名
            case["query"],  # 原始查询
            top_k=top_k,  # 返回条数
            extra_params=params,  # 附加过滤参数
        )
        if result.success and isinstance(result.data, list):  # 工具调用成功且返回列表
            results.append(result.data)  # 收集结果
        else:  # 调用失败
            results.append([])  # 记为空结果
    return results  # 返回全部检索结果


# ── 报告输出 ──────────────────────────────────────────────────────────────────

def write_reports(report: Dict[str, Any]) -> Path:
    """把评测报告同时写入 JSON 与 Markdown 文件，返回 JSON 路径。"""
    ts = report["created_at"].replace(":", "").replace("-", "")  # 时间戳压缩成文件名安全格式
    name = f"rag_retrieval_{report['mode']}_{ts}"  # 文件名（含模式与时间）
    json_path = REPORT_DIR / f"{name}.json"  # JSON 报告路径
    md_path = REPORT_DIR / f"{name}.md"  # Markdown 报告路径
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")  # 写入 JSON（中文不转义）
    md_path.write_text(render_markdown(report), encoding="utf-8")  # 写入 Markdown
    return json_path  # 返回 JSON 路径


def render_markdown(report: Dict[str, Any]) -> str:
    """把报告字典渲染为 Markdown 表格文本。"""
    m = report["metrics"]  # 取汇总指标
    lines = [  # 报告文本行（标题 + 元信息 + 汇总表头）
        f"# RAG 检索层评测报告（{report['mode']}）",  # 标题
        "",  # 空行
        f"- 数据集: {report['dataset']}",  # 数据集
        f"- 模式: {report['mode']} | top_k={report['top_k']} | 过滤: {report['filters_applied']}",  # 模式与参数
        f"- 说明: {report['note']}",  # 备注
        f"- 时间: {report['created_at']}",  # 生成时间
        "",  # 空行
        "## 汇总指标",  # 小节标题
        "",  # 空行
        "| 指标 | 值 |",  # 表头行
        "|---|---|",  # 分隔行
        f"| Recall@{report['top_k']} | {m['recall@k']} |",  # 召回率
        f"| Precision@{report['top_k']} | {m['precision@k']} |",  # 精确率
        "| MRR | " + str(m["mrr"]) + " |",  # MRR
        f"| NDCG@{report['top_k']} | {m['ndcg@k']} |",  # NDCG
        f"| 过滤合规率 | {m.get('filter_compliance', '-')} |",  # 过滤合规率（缺失则 -）
        f"| 空结果率 | {m['empty_result_rate']} |",  # 空结果率
        "",  # 空行
        "## 逐用例",  # 小节标题
        "",  # 空行
        "| ID | 查询 | 期望 | 召回 | Recall | MRR |",  # 表头行
        "|---|---|---|---|---|---|",  # 分隔行
    ]
    for c in report["per_case"]:  # 遍历每条用例
        lines.append(  # 输出用例行
            f"| {c['id']} | {c['query'][:24]} | {','.join(c['expected'])} "  # ID、查询（截断 24 字）、期望文档
            f"| {','.join(c['retrieved'])} | {c['recall@k']} | {c['mrr']} |"  # 召回文档、召回率、MRR
        )
    return "\n".join(lines)  # 拼接成全文


# ── 主流程 ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="EchoMind RAG retrieval-layer evaluation.")  # 创建解析器
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))  # 数据集路径
    parser.add_argument("--kb-docs", default=str(DEFAULT_KB_DOCS), help="知识库文档 JSON（用于校验期望文档 ID）")  # 知识库文档
    parser.add_argument("--top-k", type=int, default=5)  # 返回条数
    parser.add_argument("--mode", choices=["direct", "rewrite", "hybrid"], default="direct")  # 检索模式
    parser.add_argument("--no-filters", action="store_true", help="检索时不带 metadata 过滤（纯向量基线）")  # 关闭过滤
    parser.add_argument("--collection", default="knowledge_base_v2")  # Chroma 集合名
    parser.add_argument("--host", default="localhost")  # Chroma 主机
    parser.add_argument("--port", type=int, default=8000)  # Chroma 端口
    parser.add_argument("--path", default=str(ROOT / "data" / "chroma"))  # Chroma 数据目录
    parser.add_argument("--note", default="")  # 本次说明
    parser.add_argument("--compare", default="", help="与历史报告 JSON 对比（输出指标变化）")  # 对比基线
    parser.add_argument("--new", default="", help="对比的新报告 JSON 路径（默认取 reports 目录最新一份）")  # 对比新报告
    return parser.parse_args()  # 解析返回


def load_cases(path: Path, known_doc_ids: set[str]) -> List[Dict[str, Any]]:
    """加载评测用例并校验字段完整性、ID 唯一性、期望文档存在性。"""
    data = json.loads(path.read_text(encoding="utf-8"))  # 读取数据集
    seen: set[str] = set()  # 已出现的用例 ID
    for case in data:  # 遍历每条用例
        assert case.get("id") and case.get("query") and case.get("expected_doc_ids"), f"用例字段缺失: {case}"  # 三个必填字段
        assert case["id"] not in seen, f"用例 ID 重复: {case['id']}"  # ID 唯一
        seen.add(case["id"])  # 记录 ID
        unknown = set(case["expected_doc_ids"]) - known_doc_ids  # 期望文档中知识库不存在的部分
        assert not unknown, f"{case['id']} 期望文档不存在于知识库: {unknown}"  # 期望文档必须都在库中
    return data  # 返回用例列表


def main() -> int:
    """程序入口：加载用例 → 按模式检索 → 计算指标 → 写报告。"""
    args = parse_args()  # 解析命令行参数
    known_ids = {  # 知识库全部文档 ID
        str(doc.get("doc_id"))  # 文档 ID
        for doc in json.loads(Path(args.kb_docs).read_text(encoding="utf-8"))  # 遍历知识库文档
    }
    cases = load_cases(Path(args.dataset), known_ids)  # 加载并校验用例
    print(f"评测集: {len(cases)} 条用例（{Path(args.dataset).name}）")  # 打印用例数

    # 对比模式：不再跑检索，直接对比两份报告
    if args.compare:  # 指定了对比基线
        return compare_reports(args.compare, args.note, args.top_k, args.new)  # 直接对比并退出

    # 延迟导入 KnowledgeBase，避免无 chromadb 环境时指标单测不可用
    from mcp.knowledge_base import KnowledgeBase  # 导入知识库类
    kb = KnowledgeBase(  # 构造知识库实例
        chroma_host=args.host,  # Chroma 主机
        chroma_port=args.port,  # Chroma 端口
        chroma_path=args.path,  # Chroma 数据目录
        collection_name=args.collection,  # 集合名
    )
    print(f"collection: {args.collection}，现有 {kb.doc_count} 个片段")  # 打印集合片段数

    use_filters = not args.no_filters  # 是否带过滤
    if args.mode == "rewrite":  # 改写模式
        retrieved_list = asyncio.run(  # 异步执行
            _retrieve_rewrite_cases(kb, cases, args.top_k, use_filters)  # 改写 + 混合召回 + 重排
        )
    elif args.mode == "hybrid":  # 混合模式
        retrieved_list = [  # 逐条混合检索
            retrieve_hybrid(kb, case, args.top_k, use_filters)  # 向量 + BM25 融合
            for case in cases  # 遍历用例
        ]
    else:  # 默认 direct 模式
        retrieved_list = [  # 逐条直接检索
            retrieve_direct(kb, case, args.top_k, use_filters)  # 纯向量检索
            for case in cases  # 遍历用例
        ]

    per_case: List[Dict[str, Any]] = []  # 逐用例指标
    for case, retrieved in zip(cases, retrieved_list):  # 用例与检索结果一一对应
        metrics = case_metrics(retrieved, case, args.top_k)  # 计算该用例指标
        metrics["id"] = case["id"]  # 记录用例 ID
        metrics["query"] = case["query"]  # 记录查询文本
        per_case.append(metrics)  # 收集指标

    report = {  # 组装报告
        "dataset": str(Path(args.dataset).name),  # 数据集名
        "mode": args.mode,  # 检索模式
        "top_k": args.top_k,  # 返回条数
        "filters_applied": use_filters,  # 是否带过滤
        "note": args.note,  # 备注
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # 生成时间（UTC，精确到秒）
        "metrics": aggregate(per_case),  # 聚合指标
        "per_case": per_case,  # 逐用例指标
    }
    json_path = write_reports(report)  # 写报告
    print("\n汇总指标:")  # 打印指标标题
    for key, value in report["metrics"].items():  # 遍历聚合指标
        print(f"  {key}: {value}")  # 逐项打印
    print(f"\n报告已写入: {json_path}")  # 打印报告路径
    return 0  # 成功退出


def compare_reports(baseline_path: str, note: str, top_k: int, new_path: str = "") -> int:
    """对比 baseline 与新的评测报告（默认取 reports 目录最新一份，可用 --new 指定）。"""
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))  # 读取基线报告
    if new_path:  # 显式指定新报告
        new_file = Path(new_path)  # 使用指定路径
    else:  # 默认取最新
        candidates = [  # 收集非基线的全部历史报告
            p for p in sorted(REPORT_DIR.glob("rag_retrieval_*.json"))  # 按文件名排序
            if str(p) != str(Path(baseline_path).resolve())  # 排除基线自身
        ]
        if not candidates:  # 没有可对比报告
            print("未找到可对比的新报告，请先运行一次评测生成新报告", file=sys.stderr)  # 提示先运行评测
            return 2  # 失败退出码
        new_file = candidates[-1]  # 取最新一份
    new = json.loads(new_file.read_text(encoding="utf-8"))  # 读取新报告
    print(f"对比: {Path(baseline_path).name} vs {new_file.name}")  # 打印对比双方
    print("| 指标 | baseline | new | 变化 |")  # 表头行
    print("|---|---|---|---|")  # 分隔行
    for key in ("recall@k", "precision@k", "mrr", "ndcg@k", "filter_compliance"):  # 遍历对比指标
        b = baseline["metrics"].get(key)  # 基线值
        n = new["metrics"].get(key)  # 新值
        if b is None or n is None:  # 任一侧缺失则跳过
            continue  # 不对比该指标
        delta = round(n - b, 4)  # 变化量
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")  # 变化方向箭头
        print(f"| {key} | {b} | {n} | {delta} {arrow} |")  # 输出对比行
    return 0  # 成功退出


if __name__ == "__main__":
    raise SystemExit(main())  # 以返回码退出