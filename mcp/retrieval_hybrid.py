"""
混合检索基础组件（纯 Python，零第三方依赖）。

Phase B 检索质量改进的底层实现：
  - tokenizer：对中文做 unigram + bigram，对拉丁词按字母数字切分，兼顾中英混排；
  - BM25：Okapi BM25 关键词打分（与向量检索互补，解决"向量召回不全"）；
  - RRF：倒数排名融合，把向量排序和 BM25 排序融合成最终排序；
  - match_metadata：ChromaDB where 条件的最小实现，供 BM25 候选做 metadata 过滤。

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
    """
    text = (text or "").lower()
    tokens = _WORD_RE.findall(text)
    for cjk in _CJK_RE.findall(text):
        tokens.extend(cjk)
        tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


class BM25Index:
    """
    Okapi BM25 索引，文档量小（几百篇以内）时直接全量打分即可，无需倒排表。
    """

    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.documents = list(documents)
        self.tokenized = [tokenize(doc) for doc in self.documents]
        self.n = len(self.documents)
        self.k1 = k1
        self.b = b
        self.avgdl = (
            sum(len(toks) for toks in self.tokenized) / self.n if self.n else 0.0
        )
        self.df: Dict[str, int] = {}
        for toks in self.tokenized:
            for tok in set(toks):
                self.df[tok] = self.df.get(tok, 0) + 1

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        # 加 1 避免 log 出负数 idf，与常见实现（如 rank_bm25）一致
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def _score(self, query_terms: List[str], doc_idx: int) -> float:
        toks = self.tokenized[doc_idx]
        dl = len(toks)
        if dl == 0:
            return 0.0
        tf_map: Dict[str, int] = {}
        for tok in toks:
            tf_map[tok] = tf_map.get(tok, 0) + 1

        score = 0.0
        for term in set(query_terms):
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            denominator = (
                tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                if self.avgdl
                else tf
            )
            score += self._idf(term) * (tf * (self.k1 + 1)) / denominator
        return score

    def search(self, query: str, top_k: Optional[int] = None) -> List[Tuple[int, float]]:
        """返回 [(doc_index, bm25_score), ...]，按分数降序。"""
        q = tokenize(query)
        if not q or self.n == 0:
            return []
        scored = [(i, self._score(q, i)) for i in range(self.n)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k] if top_k is not None else scored


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[int]], k: int = 60
) -> List[Tuple[int, float]]:
    """
    倒数排名融合（RRF）：
      score(idx) = Σ 1 / (k + rank_i(idx))
    多个检索系统各自给出排序，融合后按总分降序返回 [(idx, score)]。
    """
    fused: Dict[int, float] = {}
    for ranked in rankings:
        for rank, idx in enumerate(ranked, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


# ── ChromaDB where 最小实现 ──────────────────────────────────────────────────


def _coerce(value: Any) -> Any:
    """宽松类型转换：数字字符串转数值，便于与 metadata 中的 int/float 比较。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
    return value


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    ca, cb = _coerce(a), _coerce(b)
    if isinstance(ca, (int, float)) and isinstance(cb, (int, float)):
        return float(ca) == float(cb)
    return str(a) == str(b)


def _op_match(meta_value: Any, op: str, target: Any) -> bool:
    if op == "$eq":
        return _eq(meta_value, target)
    if op == "$ne":
        return not _eq(meta_value, target)
    if op == "$in":
        return any(_eq(meta_value, t) for t in target)
    if op == "$nin":
        return not any(_eq(meta_value, t) for t in target)
    if op in ("$gt", "$gte", "$lt", "$lte"):
        a, b = _coerce(meta_value), _coerce(target)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        if op == "$gt":
            return a > b
        if op == "$gte":
            return a >= b
        if op == "$lt":
            return a < b
        return a <= b
    return False


def match_metadata(metadata: Dict[str, Any], where: Dict[str, Any]) -> bool:
    """
    判断 metadata 是否满足 ChromaDB where 条件。

    支持：$and / $or、字段级 $eq / $ne / $in / $nin / $gt / $gte / $lt / $lte，
    以及 {field: value} 简写（等价 $eq）。
    """
    for field, cond in where.items():
        if field == "$and":
            if not all(match_metadata(metadata, c) for c in cond):
                return False
        elif field == "$or":
            if not any(match_metadata(metadata, c) for c in cond):
                return False
        elif isinstance(cond, dict) and all(
            isinstance(k, str) and k.startswith("$") for k in cond
        ):
            for op, target in cond.items():
                if not _op_match(metadata.get(field), op, target):
                    return False
        else:
            if not _eq(metadata.get(field), cond):
                return False
    return True
