"""
混合检索基础组件（纯 Python，零第三方依赖）。

Phase B 检索质量改进的底层实现：
  - tokenizer：对中文做 unigram + bigram，对拉丁词按字母数字切分，兼顾中英混排；
  - BM25：Okapi BM25 关键词打分（与向量检索互补，解决"向量召回不全"）；
  - RRF：倒数排名融合，把向量排序和 BM25 排序融合成最终排序；
  - match_metadata：ChromaDB where 条件的最小实现，供 BM25 候选做 metadata 过滤。

        原始query: "7天无理由退款"
            ↓
        查询改写 (LLM)
            ↓
        并行召回:
        ├─ 向量检索 (ChromaDB) → 5条
        └─ BM25关键词检索 → 5条
            ↓
        RRF融合去重 → 8条
            ↓
        LLM重排 → Top-5
            ↓
        返回: [
        {title: "退款政策", score: 0.92},
        {title: "退货流程", score: 0.85},
        ...
        ]


该模块不依赖 chromadb，可独立单测。
"""
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    """
    轻量中文分词：
      - 拉丁/数字词按连续字母数字切分（如 "A1" -> ["a1"]）；
      - 中文按单字 + 相邻双字（bigram）切分（如 "退款" -> ["退","款","退款"]）。
    单字词频高、idf 低，真正起区分作用的是 bigram，与 BM25 的 idf 加权配合良好。

    参数：
        text: 待分词的原始文本

    返回：
        切分后的 token 列表
    """
    # 统一小写并处理空值
    text = (text or "").lower()
    # 先按连续字母数字切分出拉丁/数字词
    tokens = _WORD_RE.findall(text)
    # 再遍历连续中文字段
    for cjk in _CJK_RE.findall(text):
        tokens.extend(cjk)  # 加入单字
        # 加入相邻双字 bigram
        tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    # 返回全部 token
    return tokens


class BM25Index:
    """
    Okapi BM25 索引，文档量小（几百篇以内）时直接全量打分即可，无需倒排表。

    构建时对全部文档做分词并统计词频（df），检索时对查询词逐文档打分排序。
    """

    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        """
        构建 BM25 索引。

        参数：
            documents: 文档原文列表
            k1: 词频饱和参数（默认 1.5，控制词频对分数的增长强度）
            b: 文档长度归一化参数（默认 0.75，控制长度对分数的惩罚强度）
        """
        # 保存原始文档列表
        self.documents = list(documents)
        # 预切分所有文档
        self.tokenized = [tokenize(doc) for doc in self.documents]
        # 文档总数
        self.n = len(self.documents)
        # 保存 k1 参数
        self.k1 = k1
        # 保存 b 参数
        self.b = b
        # 计算平均文档长度（无文档时为 0）
        self.avgdl = (
            sum(len(toks) for toks in self.tokenized) / self.n if self.n else 0.0
        )
        # 词项文档频率表：term -> 出现该词的文档数
        self.df: Dict[str, int] = {}
        # 遍历每个文档统计 df
        for toks in self.tokenized:
            # 用 set 去重保证一个文档只计一次
            for tok in set(toks):
                self.df[tok] = self.df.get(tok, 0) + 1  # df 计数加一

    def _idf(self, term: str) -> float:
        """
        计算词项的反文档频率（IDF）。

        参数：
            term: 查询词项

        返回：
            IDF 分值（出现越少分值越高）
        """
        # 取该词的文档频率（未出现为 0）
        df = self.df.get(term, 0)
        # 加 1 避免 log 出负数 idf，与常见实现（如 rank_bm25）一致
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def _score(self, query_terms: List[str], doc_idx: int) -> float:
        """
        计算单个查询对单个文档的 BM25 分数。

        参数：
            query_terms: 查询分词列表
            doc_idx: 文档在索引中的下标

        返回：
            BM25 分数
        """
        # 取文档的分词结果
        toks = self.tokenized[doc_idx]
        # 文档长度
        dl = len(toks)
        # 空文档直接给 0 分
        if dl == 0:
            return 0.0
        # 词频表：term -> 词频
        tf_map: Dict[str, int] = {}
        # 统计文档内各词词频
        for tok in toks:
            tf_map[tok] = tf_map.get(tok, 0) + 1

        # 累计分数
        score = 0.0
        # 遍历查询词（set 去重）
        for term in set(query_terms):
            tf = tf_map.get(term, 0)  # 该词在文档中的词频
            # 词频为 0 跳过
            if tf == 0:
                continue
            # 计算长度归一化分母
            denominator = (
                tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                if self.avgdl
                else tf
            )
            # 累加 idf * 词频饱和项
            score += self._idf(term) * (tf * (self.k1 + 1)) / denominator
        # 返回总分数
        return score

    def search(self, query: str, top_k: Optional[int] = None) -> List[Tuple[int, float]]:
        """
        检索文档，按 BM25 分数降序返回。

        参数：
            query: 查询文本
            top_k: 返回条数（None 时返回全部）

        返回：
            [(doc_index, bm25_score), ...]，按分数降序
        """
        # 查询分词
        q = tokenize(query)
        # 查询为空或没有文档时返回空
        if not q or self.n == 0:
            return []
        # 对全部文档打分
        scored = [(i, self._score(q, i)) for i in range(self.n)]
        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)
        # 按 top_k 截断返回
        return scored[:top_k] if top_k is not None else scored


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[int]], k: int = 60
) -> List[Tuple[int, float]]:
    """
    倒数排名融合（RRF）：
      score(idx) = Σ 1 / (k + rank_i(idx))

    多个检索系统各自给出排序，融合后按总分降序返回 [(idx, score)]。
    其核心思想：同一文档在多个系统里排名越靠前，融合后的分数越高。

    参数：
        rankings: 多个检索系统各自返回的文档索引排序序列
        k: RRF 平滑常数（默认 60）

    返回：
        [(doc索引, 融合分数), ...]，按分数降序
    """
    # 累计各文档的融合分数
    fused: Dict[int, float] = {}
    # 遍历所有检索系统的排序
    for ranked in rankings:
        # 遍历该系统内的每个文档（rank 从 1 开始）
        for rank, idx in enumerate(ranked, start=1):
            # 按 RRF 公式累加分数
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    # 按分数降序返回
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


# ── ChromaDB where 最小实现 ──────────────────────────────────────────────────


def _coerce(value: Any) -> Any:
    """
    宽松类型转换：数字字符串转数值，便于与 metadata 中的 int/float 比较。

    参数：
        value: 任意值

    返回：
        转换后的值（字符串数字转 int/float，其余原样返回）
    """
    # bool 是 int 子类，需先处理避免误转
    if isinstance(value, bool):
        return value
    # 数值类型原样返回
    if isinstance(value, (int, float)):
        return value
    # 字符串尝试转数值
    if isinstance(value, str):
        try:
            return int(value)  # 优先转整数
        except (TypeError, ValueError):
            try:
                return float(value)  # 再转浮点
            except (TypeError, ValueError):
                return value  # 都失败原样返回
    # 其它类型原样返回
    return value


def _eq(a: Any, b: Any) -> bool:
    """
    宽松相等比较：优先数值比较，其次字符串比较。

    参数：
        a: 左侧值
        b: 右侧值

    返回：
        是否相等
    """
    # bool 特殊处理（避免被 _coerce 转成数值后误判）
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    # 两边都做宽松类型转换
    ca, cb = _coerce(a), _coerce(b)
    # 两边都是数值时按数值比较
    if isinstance(ca, (int, float)) and isinstance(cb, (int, float)):
        return float(ca) == float(cb)
    # 否则按字符串比较
    return str(a) == str(b)


def _op_match(meta_value: Any, op: str, target: Any) -> bool:
    """
    执行单个字段级运算符匹配。

    参数：
        meta_value: metadata 中该字段的值
        op: 运算符（$eq / $ne / $in / $nin / $gt / $gte / $lt / $lte）
        target: 运算符的目标值

    返回：
        是否匹配
    """
    # 等于
    if op == "$eq":
        return _eq(meta_value, target)
    # 不等于
    if op == "$ne":
        return not _eq(meta_value, target)
    # 属于集合
    if op == "$in":
        return any(_eq(meta_value, t) for t in target)
    # 不属于集合
    if op == "$nin":
        return not any(_eq(meta_value, t) for t in target)
    # 大小比较运算
    if op in ("$gt", "$gte", "$lt", "$lte"):
        # 两边转数值比较
        a, b = _coerce(meta_value), _coerce(target)
        # 任一侧不是数值则视为不匹配
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        # 大于
        if op == "$gt":
            return a > b
        # 大于等于
        if op == "$gte":
            return a >= b
        # 小于
        if op == "$lt":
            return a < b
        # 小于等于
        return a <= b
    # 未识别的运算符视为不匹配
    return False


def match_metadata(metadata: Dict[str, Any], where: Dict[str, Any]) -> bool:
    """
    判断 metadata 是否满足 ChromaDB where 条件。

    支持：$and / $or、字段级 $eq / $ne / $in / $nin / $gt / $gte / $lt / $lte，
    以及 {field: value} 简写（等价 $eq）。

    参数：
        metadata: 文档的元数据字典
        where: ChromaDB where 过滤条件

    返回：
        是否满足全部条件
    """
    # 遍历 where 的每个条件字段
    for field, cond in where.items():
        # $and 逻辑且：全部子条件满足才通过
        if field == "$and":
            if not all(match_metadata(metadata, c) for c in cond):
                return False
        # $or 逻辑或：任一子条件满足即可
        elif field == "$or":
            if not any(match_metadata(metadata, c) for c in cond):
                return False
        # 字段级运算符条件（如 {"price": {"$gt": 100}}）
        elif isinstance(cond, dict) and all(
            isinstance(k, str) and k.startswith("$") for k in cond
        ):
            # 逐个运算符校验，全部通过才行
            for op, target in cond.items():
                if not _op_match(metadata.get(field), op, target):
                    return False
        # 简写形式 {field: value}（等价 $eq）
        else:
            if not _eq(metadata.get(field), cond):
                return False
    # 所有条件都满足
    return True
