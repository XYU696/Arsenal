"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import hashlib
import logging
from datetime import date
from typing import Any, Dict, List, Optional

import chromadb

from mcp.retrieval_hybrid import BM25Index, match_metadata, reciprocal_rank_fusion

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "knowledge_base"       # 旧版通用文档 collection（保留，便于回滚）
    COLLECTION_NAME_V2 = "knowledge_base_v2" # 电商售后知识库 collection（当前使用）

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
        collection_name: Optional[str] = None,
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 默认使用电商售后 v2 collection；旧 collection 保留不动
        self._collection_name = collection_name or self.COLLECTION_NAME_V2
        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"description": "EchoMind 电商售后 RAG 知识库" if self._collection_name == self.COLLECTION_NAME_V2 else "EchoMind RAG 知识库"},
        )

        # 仅旧版 collection 在为空时自动导入默认文档；
        # v2 collection 由 integrations/knowledge_base/init_knowledge.py 显式导入
        if self._collection_name == self.COLLECTION_NAME and self._collection.count() == 0:
            self._load_default_docs()

        # 混合检索的 BM25 缓存（文档增删后失效重建）
        self._bm25: Optional[BM25Index] = None
        self._bm25_snapshot: Optional[Dict[str, List[Any]]] = None

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "...", "category": "...", ...}, ...]
        除 title/content 外，其余字段会作为 metadata 存储，支持检索过滤。
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            chunks  = self._chunk_text(content, chunk_size=500)
            base_meta = self._extract_metadata(doc)

            for i, chunk in enumerate(chunks):
                # 优先使用文档自带 doc_id，分块时追加序号；否则沿用旧版 md5 生成
                doc_id = doc.get("doc_id") or hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                if doc.get("doc_id"):
                    doc_id = f"{doc_id}-{i}"
                ids.append(doc_id)
                docs.append(chunk)
                metas.append({**base_meta, "chunk_index": i, "total_chunks": len(chunks)})

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            self._invalidate_bm25()
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    def search(self, query: str, top_k: int = 5, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        where: 可选 metadata 过滤条件（ChromaDB where 语法），如 {"category": "refund_policy"}。
        """
        kwargs: Dict[str, Any] = {"query_texts": [query], "n_results": top_k}
        if where:
            kwargs["where"] = where
        results = self._collection.query(**kwargs)

        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "doc_id":   meta.get("doc_id", ""),
                    "title":    meta.get("title", ""),
                    "category": meta.get("category", ""),
                    "product":  meta.get("product", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),
                    "source":   meta.get("source", ""),
                })

        return items

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
        per_system_top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Phase B 混合检索：向量 + BM25，再用 RRF 融合排序。

        召回阶段：
          - 向量检索（ChromaDB 语义相似度），取 top per_system_top_k；
          - BM25 关键词检索（本地索引，中文 unigram/bigram 分词），同样取 top N；
        融合阶段：
          - RRF 把两路排序融合，兼顾语义相关与关键词命中。
        where 过滤同时作用于两路召回，与向量检索行为一致。
        """
        snap = self._get_bm25_snapshot()
        ids = snap["ids"]
        if not ids or self._bm25 is None:
            return []

        per = per_system_top_k or max(top_k * 4, 10)
        per = max(per, top_k)
        id_to_idx = {doc_id: i for i, doc_id in enumerate(ids)}

        # 1) 向量召回（与 search() 同一套 ChromaDB query，支持 where）
        kwargs: Dict[str, Any] = {
            "query_texts": [query],
            "n_results": min(per, len(ids)),
        }
        if where:
            kwargs["where"] = where
        vec = self._collection.query(**kwargs)
        vector_ranks: List[int] = []
        if vec.get("ids") and vec["ids"][0]:
            for doc_id in vec["ids"][0]:
                idx = id_to_idx.get(doc_id)
                if idx is not None and idx not in vector_ranks:
                    vector_ranks.append(idx)

        # 2) BM25 召回：全集打分后按 metadata 过滤，再取 top N
        bm25_ranks: List[int] = []
        for idx, _score in self._bm25.search(query):
            if where and not match_metadata(snap["metadatas"][idx], where):
                continue
            bm25_ranks.append(idx)
            if len(bm25_ranks) >= per:
                break

        # 3) RRF 融合排序
        fused = reciprocal_rank_fusion([vector_ranks, bm25_ranks], k=60)

        items = []
        for idx, rrf_score in fused[:top_k]:
            meta = snap["metadatas"][idx]
            items.append({
                "doc_id":   meta.get("doc_id", ids[idx]),
                "title":    meta.get("title", ""),
                "category": meta.get("category", ""),
                "product":  meta.get("product", ""),
                "content":  snap["documents"][idx],
                "score":    round(rrf_score, 4),
                "chunk":    meta.get("chunk_index", 0),
                "source":   meta.get("source", ""),
                "method":   "hybrid",
            })
        return items

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    def clear(self) -> None:
        """清空并重建当前 collection，用于知识库整体重建（配合初始化脚本 --reset）。"""
        self._client.delete_collection(self._collection_name)
        self._invalidate_bm25()
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"description": "EchoMind 电商售后 RAG 知识库" if self._collection_name == self.COLLECTION_NAME_V2 else "EchoMind RAG 知识库"},
        )
        logger.info(f"知识库 collection 已清空重建: {self._collection_name}")

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        where = self._build_where(params)
        return self.search_hybrid(query, top_k=top_k, where=where)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _invalidate_bm25(self) -> None:
        """文档变更后失效 BM25 缓存，下次混合检索时重建。"""
        self._bm25 = None
        self._bm25_snapshot = None

    def _get_bm25_snapshot(self) -> Dict[str, List[Any]]:
        """
        拉取 collection 全量文档/元数据并构建 BM25 索引（结果缓存）。

        语料小（当前 37 篇）时全量打分足够快；语料变大后可改为分片/增量索引。
        """
        if self._bm25 is None:
            snap = self._collection.get(include=["documents", "metadatas"])
            ids = snap.get("ids") or []
            docs = snap.get("documents") or []
            metas = snap.get("metadatas") or []
            metas = [m or {} for m in metas]
            self._bm25 = BM25Index(docs)
            self._bm25_snapshot = {"ids": ids, "documents": docs, "metadatas": metas}
        return self._bm25_snapshot  # type: ignore[return-value]

    @staticmethod
    def _to_yyyymmdd(value: Any) -> int:
        """
        把 ISO 日期字符串转成 YYYYMMDD 整数。

        ChromaDB 的 where 只支持 $lte/$gte 数值比较，日期必须用整数存储，
        否则检索时抛 "Expected operand value to be an int or a float"。
        """
        text = str(value or "")[:10].replace("-", "").replace("/", "")
        if text.isdigit() and len(text) == 8:
            return int(text)
        # 兜底：空值按"长期有效"（9999-12-31）处理
        return 99991231

    def _extract_metadata(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        从文档中提取 ChromaDB metadata。

        ChromaDB metadata 只支持 str/int/float/bool 标量：
        - 空值统一用默认值（如 effective_to 空表示长期有效，存 9999-12-31）
        - 标签 tags 存为逗号分隔字符串
        """
        meta = {
            "doc_id": doc.get("doc_id", ""),
            "title": doc.get("title", ""),
            "category": doc.get("category", "general"),
            "product": doc.get("product", "通用"),
            "plan_type": doc.get("plan_type", "通用"),
            "effective_from": self._to_yyyymmdd(doc.get("effective_from") or "1970-01-01"),
            "effective_to": self._to_yyyymmdd(doc.get("effective_to") or "9999-12-31"),
            "source": doc.get("source", "manual"),
            "version": int(doc.get("version", 1)),
            "owner": doc.get("owner", ""),
            "tags": doc.get("tags", ""),
            "db_table": doc.get("db_table", "-"),
        }
        # 一致性校验字段（仅部分文档携带，用于结构化比对与精确过滤）
        for key in ("policy_code", "incident_code", "incident_status"):
            if doc.get(key):
                meta[key] = str(doc[key])
        for key in ("refund_window_hours", "usage_limit_percent"):
            if doc.get(key) is not None:
                meta[key] = int(doc[key])
        for key in ("allow_refund", "manual_review_required"):
            if doc.get(key) is not None:
                meta[key] = bool(doc[key])
        return meta

    def _build_where(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        根据工具参数构建 ChromaDB where 过滤条件。

        支持：category（字符串或列表）、product、source、db_table、plan_type、
        policy_code、incident_code，并自动限定当前日期在 effective_from/to 之内，
        避免已过期文档（如已恢复的故障公告）进入结果。
        """
        conditions: List[Dict[str, Any]] = []
        today = int(date.today().strftime("%Y%m%d"))

        category = params.get("category")
        if isinstance(category, str) and category:
            conditions.append({"category": {"$eq": category}})
        elif isinstance(category, list) and category:
            conditions.append({"category": {"$in": category}})

        for key in ("product", "source", "db_table", "plan_type", "policy_code", "incident_code"):
            value = params.get(key)
            if value:
                conditions.append({key: {"$eq": str(value)}})

        # 有效期过滤：effective_from <= 今天 <= effective_to
        conditions.append({"effective_from": {"$lte": today}})
        conditions.append({"effective_to": {"$gte": today}})

        if not conditions:
            return None
        return {"$and": conditions} if len(conditions) > 1 else conditions[0]

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        default_docs = [
            {
                "title": "退款政策",
                "content": (
                    "退款政策说明。"
                    "用户在购买后 7 天内可以申请无理由退款。"
                    "退款申请提交后，系统会在 1-3 个工作日内审核。"
                    "审核通过后，款项将在 5-7 个工作日内退回原支付账户。"
                    "如果商品已发货，需要先完成退货流程才能退款。"
                    "退货运费由用户承担，除非是商品质量问题。"
                    "超过 7 天但未超过 30 天的订单，需要提供商品质量问题的证据才能退款。"
                ),
            },
            {
                "title": "订单查询",
                "content": (
                    "订单查询指南。"
                    "用户可以通过订单号查询订单状态。"
                    "订单状态包括：待支付、已支付、已发货、运输中、已签收、已完成。"
                    "如果订单显示已发货但超过 7 天未收到，可以联系客服申请查件。"
                    "物流信息通常在发货后 24 小时内更新。"
                    "如果订单显示异常，请提供订单号联系客服处理。"
                ),
            },
            {
                "title": "账户安全",
                "content": (
                    "账户安全说明。"
                    "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。"
                    "如果忘记密码，可以通过绑定的手机号或邮箱重置。"
                    "发现账户异常登录时，系统会自动锁定账户并发送通知。"
                    "用户可以在安全设置中开启两步验证，提高账户安全性。"
                    "不要将密码分享给他人，客服人员不会索要用户密码。"
                ),
            },
            {
                "title": "技术故障排查",
                "content": (
                    "常见技术问题排查。"
                    "应用崩溃：请尝试清除缓存后重启应用，如果问题持续请更新到最新版本。"
                    "登录失败 401 错误：表示认证失败，请检查用户名密码是否正确，或尝试重置密码。"
                    "页面加载慢：检查网络连接，尝试切换 WiFi 或移动数据。"
                    "支付失败：确认银行卡余额充足，检查是否开启了网上支付功能。"
                    "500 服务器错误：这是服务端问题，请稍后重试，如果持续出现请联系技术支持。"
                ),
            },
            {
                "title": "会员与积分",
                "content": (
                    "会员积分规则。"
                    "每消费 1 元累积 1 积分。"
                    "积分可以在下次购物时抵扣，100 积分 = 1 元。"
                    "会员等级分为：普通会员、银卡会员（累计消费 1000 元）、金卡会员（累计消费 5000 元）。"
                    "银卡会员享受 95 折优惠，金卡会员享受 9 折优惠。"
                    "积分有效期为 1 年，过期自动清零。"
                    "生日当月消费可获得双倍积分。"
                ),
            },
            {
                "title": "配送说明",
                "content": (
                    "配送服务说明。"
                    "标准配送：3-5 个工作日送达，免运费（订单满 99 元）。"
                    "加急配送：1-2 个工作日送达，运费 15 元。"
                    "同城配送：当日达或次日达，运费 10 元。"
                    "偏远地区可能需要额外 2-3 天。"
                    "配送时间为每天 9:00-18:00，节假日可能延迟。"
                    "如果需要修改收货地址，请在发货前联系客服。"
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(f"已导入默认知识库: {len(default_docs)} 篇文档")
