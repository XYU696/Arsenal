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

    职责：
      - 文档导入（自动切片、生成 metadata）
      - 语义检索（search）与混合检索（search_hybrid，向量 + BM25 + RRF）
      - 作为 knowledge_search 工具的真实 handler
    """

    COLLECTION_NAME = "knowledge_base"       # 旧版通用文档 collection（保留，便于回滚）
    COLLECTION_NAME_V2 = "knowledge_base_v2" # 电商售后知识库 collection（当前使用）

    def __init__(
        self,
        chroma_host: str = "localhost",  # ChromaDB 服务主机（服务模式）
        chroma_port: int = 8000,  # ChromaDB 服务端口（服务模式）
        chroma_path: str = "./data/chroma",  # 本地持久化路径（本地模式）
        collection_name: Optional[str] = None,  # 使用的 collection 名（默认 v2 电商售后）
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False  # 服务模式标记（默认本地模式，连接成功置 True）
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,  # 服务地址
                port=chroma_port,  # 服务端口
                settings=chromadb.Settings(anonymized_telemetry=False),  # 关闭匿名遥测
            )
            self._client.heartbeat()  # 发送心跳验证服务可用
            self._use_server = True  # 连接成功，标记为服务模式
            # 记录连接成功日志
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            # 服务不可用时降级为本地持久化模式
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,  # 本地数据目录
                settings=chromadb.Settings(anonymized_telemetry=False),  # 关闭匿名遥测
            )

        # 默认使用电商售后 v2 collection；旧 collection 保留不动
        self._collection_name = collection_name or self.COLLECTION_NAME_V2
        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        # 获取或创建目标 collection
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,  # collection 名称
            metadata={"description": "EchoMind 电商售后 RAG 知识库" if self._collection_name == self.COLLECTION_NAME_V2 else "EchoMind RAG 知识库"},  # collection 描述
        )

        # 仅旧版 collection 在为空时自动导入默认文档；
        # v2 collection 由 integrations/knowledge_base/init_knowledge.py 显式导入
        if self._collection_name == self.COLLECTION_NAME and self._collection.count() == 0:
            # 旧 collection 为空时导入默认文档
            self._load_default_docs()

        # 混合检索的 BM25 缓存（文档增删后失效重建）
        self._bm25: Optional[BM25Index] = None  # BM25 检索索引缓存
        self._bm25_snapshot: Optional[Dict[str, List[Any]]] = None  # collection 全量快照缓存

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "...", "category": "...", ...}, ...]
        除 title/content 外，其余字段会作为 metadata 存储，支持检索过滤。
        长文档会自动切片（每片 500 字）。

        参数：
            documents: 待导入的文档字典列表

        返回：
            实际导入的文档片段数量
        """
        # 初始化 id、文档内容、metadata 三个列表
        ids, docs, metas = [], [], []

        # 遍历每篇文档
        for doc in documents:
            # 取文档标题
            title   = doc.get("title", "")
            # 取文档正文
            content = doc.get("content", "")
            # 长文档切片（每片 500 字）
            chunks  = self._chunk_text(content, chunk_size=500)
            # 提取基础 metadata
            base_meta = self._extract_metadata(doc)

            # 遍历每个切片
            for i, chunk in enumerate(chunks):
                # 优先使用文档自带 doc_id，分块时追加序号；否则沿用旧版 md5 生成
                doc_id = doc.get("doc_id") or hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                # 文档自带 doc_id 时追加分块序号区分
                if doc.get("doc_id"):
                    doc_id = f"{doc_id}-{i}"  # 拼接分块序号
                # 记录该切片的 id
                ids.append(doc_id)
                # 记录该切片内容
                docs.append(chunk)
                # 记录该切片 metadata（含分块序号信息）
                metas.append({**base_meta, "chunk_index": i, "total_chunks": len(chunks)})

        # 有切片时才写入 ChromaDB
        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            # 文档已变更，失效 BM25 缓存
            self._invalidate_bm25()
            # 记录导入结果日志
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    def search(self, query: str, top_k: int = 5, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        where: 可选 metadata 过滤条件（ChromaDB where 语法），如 {"category": "refund_policy"}。

        参数：
            query: 用户检索查询
            top_k: 返回的文档片段数量（默认 5）
            where: ChromaDB metadata 过滤条件（可选）

        返回：
            检索结果列表（含 doc_id、title、category、content、score 等）
        """
        # 构造 ChromaDB 查询参数
        kwargs: Dict[str, Any] = {"query_texts": [query], "n_results": top_k}
        # 提供过滤条件时追加 where
        if where:
            kwargs["where"] = where
        # 执行 ChromaDB 语义查询
        results = self._collection.query(**kwargs)

        # 初始化结果列表
        items = []
        # 有返回文档时逐条整理
        if results["documents"] and results["documents"][0]:
            # 并行遍历文档、metadata 和距离
            for doc, meta, dist in zip(
                results["documents"][0],  # 文档内容
                results["metadatas"][0],  # 对应 metadata
                results["distances"][0],  # 对应距离
            ):
                # 组装单条结果
                items.append({
                    "doc_id":   meta.get("doc_id", ""),  # 文档 ID
                    "title":    meta.get("title", ""),  # 标题
                    "category": meta.get("category", ""),  # 类别
                    "product":  meta.get("product", ""),  # 产品
                    "content":  doc,  # 文档内容
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),  # 分块序号
                    "source":   meta.get("source", ""),  # 来源
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

        参数：
            query: 用户检索查询
            top_k: 最终返回的文档片段数量（默认 5）
            where: ChromaDB metadata 过滤条件（可选）
            per_system_top_k: 单路召回条数（默认 max(top_k*4, 10)）

        返回：
            混合检索结果列表（含 method: "hybrid" 标记）
        """
        # 拉取 BM25 快照（含全量文档与 metadata）
        snap = self._get_bm25_snapshot()
        # 取全量文档 id 列表
        ids = snap["ids"]
        # 无文档或 BM25 未构建时返回空
        if not ids or self._bm25 is None:
            return []

        # 单路召回条数：默认 top_k 的 4 倍，且不小于 top_k
        per = per_system_top_k or max(top_k * 4, 10)
        per = max(per, top_k)  # 确保召回数不小于返回数
        # 构造 doc_id -> 快照下标 的映射，供向量召回转下标
        id_to_idx = {doc_id: i for i, doc_id in enumerate(ids)}

        # 1) 向量召回（与 search() 同一套 ChromaDB query，支持 where）
        kwargs: Dict[str, Any] = {
            "query_texts": [query],  # 查询文本
            "n_results": min(per, len(ids)),  # 返回条数（不超过总文档数）
        }
        # 提供过滤条件时追加 where
        if where:
            kwargs["where"] = where
        # 执行向量检索
        vec = self._collection.query(**kwargs)
        # 记录向量召回的下标列表
        vector_ranks: List[int] = []
        # 有返回 id 时整理去重
        if vec.get("ids") and vec["ids"][0]:
            for doc_id in vec["ids"][0]:
                idx = id_to_idx.get(doc_id)  # 映射到快照下标
                # 映射成功且未重复时记录
                if idx is not None and idx not in vector_ranks:
                    vector_ranks.append(idx)

        # 2) BM25 召回：全集打分后按 metadata 过滤，再取 top N
        bm25_ranks: List[int] = []
        # 遍历 BM25 打分结果（分数降序）
        for idx, _score in self._bm25.search(query):
            # 提供过滤条件时校验 metadata 是否匹配
            if where and not match_metadata(snap["metadatas"][idx], where):
                continue  # 不匹配则跳过
            bm25_ranks.append(idx)  # 记录命中文档下标
            # 达到单路召回上限即停止
            if len(bm25_ranks) >= per:
                break

        # 3) RRF 融合排序
        fused = reciprocal_rank_fusion([vector_ranks, bm25_ranks], k=60)

        # 组装融合后的结果
        items = []
        # 取前 top_k 个融合结果
        for idx, rrf_score in fused[:top_k]:
            meta = snap["metadatas"][idx]  # 取该文档的 metadata
            # 组装单条结果（标记 method: hybrid）
            items.append({
                "doc_id":   meta.get("doc_id", ids[idx]),  # 文档 ID
                "title":    meta.get("title", ""),  # 标题
                "category": meta.get("category", ""),  # 类别
                "product":  meta.get("product", ""),  # 产品
                "content":  snap["documents"][idx],  # 文档内容
                "score":    round(rrf_score, 4),  # RRF 融合分数
                "chunk":    meta.get("chunk_index", 0),  # 分块序号
                "source":   meta.get("source", ""),  # 来源
                "method":   "hybrid",  # 检索方式标记
            })
        return items

    @property
    def doc_count(self) -> int:
        """
        返回知识库当前文档片段总数。

        返回：
            当前 collection 中的文档片段数量
        """
        # 直接取 collection 的文档计数
        return self._collection.count()

    def clear(self) -> None:
        """清空并重建当前 collection，用于知识库整体重建（配合初始化脚本 --reset）。"""
        # 删除整个 collection
        self._client.delete_collection(self._collection_name)
        # 失效 BM25 缓存（文档已清空）
        self._invalidate_bm25()
        # 重新创建同名空 collection
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,  # collection 名称
            metadata={"description": "EchoMind 电商售后 RAG 知识库" if self._collection_name == self.COLLECTION_NAME_V2 else "EchoMind RAG 知识库"},  # collection 描述
        )
        # 记录清空重建日志
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

        参数：
            params: 工具调用参数（含 query、top_k、category 等过滤条件）
            context: 调用上下文

        返回：
            混合检索结果列表
        """
        # 取参数中的查询文本
        query = params.get("query", "")
        # 取参数中的返回条数（默认 5）
        top_k = params.get("top_k", 5)
        # 从参数构造 where 过滤条件
        where = self._build_where(params)
        # 执行混合检索并返回结果
        return self.search_hybrid(query, top_k=top_k, where=where)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _invalidate_bm25(self) -> None:
        """文档变更后失效 BM25 缓存，下次混合检索时重建。"""
        # 清空 BM25 索引缓存
        self._bm25 = None
        # 清空 collection 快照缓存
        self._bm25_snapshot = None

    def _get_bm25_snapshot(self) -> Dict[str, List[Any]]:
        """
        拉取 collection 全量文档/元数据并构建 BM25 索引（结果缓存）。

        语料小（当前 37 篇）时全量打分足够快；语料变大后可改为分片/增量索引。

        返回：
            全量快照字典（ids、documents、metadatas）
        """
        # 缓存为空时（懒加载）构建
        if self._bm25 is None:
            # 拉取 collection 全量文档和 metadata
            snap = self._collection.get(include=["documents", "metadatas"])
            ids = snap.get("ids") or []  # 文档 id 列表
            docs = snap.get("documents") or []  # 文档内容列表
            metas = snap.get("metadatas") or []  # metadata 列表
            # 空 metadata 统一补成空字典
            metas = [m or {} for m in metas]
            # 用全量文档构建 BM25 索引
            self._bm25 = BM25Index(docs)
            # 缓存全量快照
            self._bm25_snapshot = {"ids": ids, "documents": docs, "metadatas": metas}
        return self._bm25_snapshot  # type: ignore[return-value]

    @staticmethod
    def _to_yyyymmdd(value: Any) -> int:
        """
        把 ISO 日期字符串转成 YYYYMMDD 整数。

        ChromaDB 的 where 只支持 $lte/$gte 数值比较，日期必须用整数存储，
        否则检索时抛 "Expected operand value to be an int or a float"。

        参数：
            value: ISO 日期字符串（如 "2026-01-15"）或空值

        返回：
            YYYYMMDD 整数；无法解析时返回 99991231（长期有效）
        """
        # 取前 10 位日期并去掉分隔符
        text = str(value or "")[:10].replace("-", "").replace("/", "")
        # 是 8 位纯数字时转成整数
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

        参数：
            doc: 原始文档字典

        返回：
            整理后的 metadata 字典
        """
        # 组装基础 metadata 字段
        meta = {
            "doc_id": doc.get("doc_id", ""),  # 文档 ID
            "title": doc.get("title", ""),  # 标题
            "category": doc.get("category", "general"),  # 类别（默认 general）
            "product": doc.get("product", "通用"),  # 产品（默认通用）
            "plan_type": doc.get("plan_type", "通用"),  # 套餐（默认通用）
            "effective_from": self._to_yyyymmdd(doc.get("effective_from") or "1970-01-01"),  # 生效开始（默认 1970-01-01）
            "effective_to": self._to_yyyymmdd(doc.get("effective_to") or "9999-12-31"),  # 生效结束（默认长期有效）
            "source": doc.get("source", "manual"),  # 来源（默认 manual）
            "version": int(doc.get("version", 1)),  # 版本号
            "owner": doc.get("owner", ""),  # 负责人
            "tags": doc.get("tags", ""),  # 标签
            "db_table": doc.get("db_table", "-"),  # 关联数据表
        }
        # 一致性校验字段（仅部分文档携带，用于结构化比对与精确过滤）
        for key in ("policy_code", "incident_code", "incident_status"):
            # 提供的字符串类校验字段按字符串存储
            if doc.get(key):
                meta[key] = str(doc[key])
        for key in ("refund_window_hours", "usage_limit_percent"):
            # 数值类校验字段按整数存储
            if doc.get(key) is not None:
                meta[key] = int(doc[key])
        for key in ("allow_refund", "manual_review_required"):
            # 布尔类校验字段按布尔存储
            if doc.get(key) is not None:
                meta[key] = bool(doc[key])
        return meta

    def _build_where(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        根据工具参数构建 ChromaDB where 过滤条件。

        支持：category（字符串或列表）、product、source、db_table、plan_type、
        policy_code、incident_code，并自动限定当前日期在 effective_from/to 之内，
        避免已过期文档（如已恢复的故障公告）进入结果。

        参数：
            params: 工具调用参数字典

        返回：
            ChromaDB where 条件；无任何条件时返回 None
        """
        # 初始化条件列表
        conditions: List[Dict[str, Any]] = []
        # 今天日期转 YYYYMMDD 整数
        today = int(date.today().strftime("%Y%m%d"))

        # 取参数中的类别（支持字符串或列表）
        category = params.get("category")
        # 单类别用 $eq 精确匹配
        if isinstance(category, str) and category:
            conditions.append({"category": {"$eq": category}})
        # 多类别用 $in 包含匹配
        elif isinstance(category, list) and category:
            conditions.append({"category": {"$in": category}})

        # 遍历其它等值过滤字段
        for key in ("product", "source", "db_table", "plan_type", "policy_code", "incident_code"):
            value = params.get(key)
            # 提供了值则追加等值条件
            if value:
                conditions.append({key: {"$eq": str(value)}})

        # 有效期过滤：effective_from <= 今天 <= effective_to
        conditions.append({"effective_from": {"$lte": today}})  # 生效开始不晚于今天
        conditions.append({"effective_to": {"$gte": today}})  # 生效结束不早于今天

        # 无条件时返回 None
        if not conditions:
            return None
        # 多条件用 $and 合并，单条件直接返回
        return {"$and": conditions} if len(conditions) > 1 else conditions[0]

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """
        将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。

        参数：
            text: 待切分的文档文本
            chunk_size: 每个切片的字符数上限（默认 500）

        返回：
            切分后的文本列表
        """
        # 文本未超限时整体作为一片返回（去空白为空则返回空列表）
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        # 初始化切片列表
        chunks = []
        # 当前累积的切片文本
        current = ""
        # 按句子切分（换行符视为句号）
        sentences = text.replace("\n", "。").split("。")
        # 遍历每个句子
        for sent in sentences:
            sent = sent.strip()  # 去除首尾空白
            # 空句跳过
            if not sent:
                continue
            # 当前切片加上该句会超限时，先把当前切片收尾
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)  # 收尾当前切片
                current = sent  # 以该句开启新切片
            else:
                # 未超限则拼接到当前切片
                current = f"{current}。{sent}" if current else sent

        # 收尾最后累积的切片
        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """
        导入默认知识库文档（客服场景常见问题）。

        仅旧版通用 collection 为空时调用，用于开箱即用的最小知识库。
        """
        # 定义默认知识库文档列表（标题 + 正文）
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
        # 批量导入默认文档到知识库
        self.add_documents(default_docs)
        # 记录默认文档导入日志
        logger.info(f"已导入默认知识库: {len(default_docs)} 篇文档")
