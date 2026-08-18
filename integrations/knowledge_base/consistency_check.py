"""
知识库一致性校验脚本。

校验知识库文档中携带的结构化字段与业务库是否一致：
- refund_policy 文档（policy_code）↔ refund_policies 表：窗口/使用率/是否可退/是否人工审核
- incident 文档（incident_code）↔ incidents 表：故障状态

用法：
    python3 -m integrations.knowledge_base.consistency_check
    python3 -m integrations.knowledge_base.consistency_check --docs <path> --db-path <path>
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCS_PATH = ROOT / "data" / "knowledge" / "after_sales_v2.json"
DEFAULT_DB_PATH = ROOT / "data" / "business" / "echomind_ecommerce_v2.db"


def _check_refund_policy(doc: Dict[str, Any], row: Optional[Dict[str, Any]]) -> List[str]:
    """比对退款政策文档与表记录。"""
    errors: List[str] = []
    if row is None:
        return [f"{doc['doc_id']}: 表里不存在政策编码 {doc.get('policy_code')}"]
    for key in ("refund_window_hours", "usage_limit_percent", "allow_refund", "manual_review_required"):
        expected = doc.get(key)
        actual = row.get(key)
        if expected is None or actual is None:
            continue
        # 统一数值类型后比较（int vs float、bool vs 0/1）
        if isinstance(actual, bool) or isinstance(expected, bool):
            match = bool(expected) == bool(actual)
        else:
            match = float(expected) == float(actual)
        if not match:
            errors.append(f"{doc['doc_id']}: 字段 {key} 不一致（文档={expected}，表={actual}）")
    return errors


def _check_incident(doc: Dict[str, Any], row: Optional[Dict[str, Any]]) -> List[str]:
    """比对故障公告文档与 incidents 表。"""
    if row is None:
        return [f"{doc['doc_id']}: 表里不存在故障编码 {doc.get('incident_code')}"]
    errors: List[str] = []
    if doc.get("incident_status") and doc["incident_status"] != row.get("status"):
        errors.append(f"{doc['doc_id']}: 故障状态不一致（文档={doc['incident_status']}，表={row.get('status')}）")
    return errors


def run_consistency_check(docs: List[Dict[str, Any]], db: Any = None) -> bool:
    """
    执行一致性校验，返回是否全部通过。
    支持传入外部 db（便于测试），默认创建 BusinessDB 实例。
    """
    if db is None:
        from integrations.business_db import BusinessDB
        db = BusinessDB(str(DEFAULT_DB_PATH))

    errors: List[str] = []
    checked_policy = 0
    checked_incident = 0

    for doc in docs:
        # 只要文档携带政策编码/故障编码就参与校验，不限于特定 category
        if doc.get("policy_code"):
            row = db.get_refund_policy_by_code(doc["policy_code"])
            errors.extend(_check_refund_policy(doc, row))
            checked_policy += 1
        if doc.get("incident_code"):
            row = db.get_incident_by_code(doc["incident_code"])
            errors.extend(_check_incident(doc, row))
            checked_incident += 1

    print(f"一致性校验：退款政策 {checked_policy} 篇，故障公告 {checked_incident} 篇")
    if errors:
        for err in errors:
            print(f"  [不一致] {err}")
        print(f"校验未通过：{len(errors)} 处不一致")
        return False
    print("校验通过：知识库结构化字段与业务库一致")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Check knowledge base consistency against business DB.")
    parser.add_argument("--docs", default=str(DEFAULT_DOCS_PATH))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))
    from integrations.business_db import BusinessDB
    db = BusinessDB(args.db_path)
    return 0 if run_consistency_check(docs, db=db) else 1


if __name__ == "__main__":
    raise SystemExit(main())
