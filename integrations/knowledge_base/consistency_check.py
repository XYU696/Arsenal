"""
知识库一致性校验脚本。

校验知识库文档中携带的结构化字段与业务库是否一致：
- refund_policy 文档（policy_code）↔ refund_policies 表：窗口/使用率/是否可退/是否人工审核
- incident 文档（incident_code）↔ incidents 表：故障状态

用法：
    python3 -m integrations.knowledge_base.consistency_check
    python3 -m integrations.knowledge_base.consistency_check --docs <path> --db-path <path>
"""
import argparse  # 命令行参数解析
import json  # 读取文档 JSON
import sys  # 退出码
from pathlib import Path  # 路径对象
from typing import Any, Dict, List, Optional  # 类型注解

ROOT = Path(__file__).resolve().parents[2]  # 项目根目录
DEFAULT_DOCS_PATH = ROOT / "data" / "knowledge" / "after_sales_v2.json"  # 默认知识库文档路径
DEFAULT_DB_PATH = ROOT / "data" / "business" / "echomind_ecommerce_v2.db"  # 默认业务库路径


def _check_refund_policy(doc: Dict[str, Any], row: Optional[Dict[str, Any]]) -> List[str]:
    """
    比对退款政策文档与表记录。

    参数：
        doc: 知识库文档（携带 policy_code 及政策字段）
        row: 业务表 refund_policies 的记录（可能为 None）

    返回：
        不一致描述列表（全部一致时为空列表）
    """
    # 收集不一致项
    errors: List[str] = []
    # 表里没有该政策编码直接报缺失
    if row is None:
        return [f"{doc['doc_id']}: 表里不存在政策编码 {doc.get('policy_code')}"]
    # 逐个比对关键政策字段
    for key in ("refund_window_hours", "usage_limit_percent", "allow_refund", "manual_review_required"):
        # 文档中的期望值
        expected = doc.get(key)
        # 表中的实际值
        actual = row.get(key)
        # 任一侧缺字段就跳过该字段
        if expected is None or actual is None:
            continue
        # 统一数值类型后比较（int vs float、bool vs 0/1）
        if isinstance(actual, bool) or isinstance(expected, bool):
            # 布尔字段直接比布尔值
            match = bool(expected) == bool(actual)
        else:
            # 数值字段统一转浮点比较
            match = float(expected) == float(actual)
        # 不一致时记录差异
        if not match:
            errors.append(f"{doc['doc_id']}: 字段 {key} 不一致（文档={expected}，表={actual}）")
    # 返回全部不一致项
    return errors


def _check_incident(doc: Dict[str, Any], row: Optional[Dict[str, Any]]) -> List[str]:
    """
    比对故障公告文档与 incidents 表。

    参数：
        doc: 知识库文档（携带 incident_code 及状态）
        row: 业务表 incidents 的记录（可能为 None）

    返回：
        不一致描述列表
    """
    # 表里没有该故障编码直接报缺失
    if row is None:
        return [f"{doc['doc_id']}: 表里不存在故障编码 {doc.get('incident_code')}"]
    # 收集不一致项
    errors: List[str] = []
    # 文档携带状态且与表状态不同时记录差异
    if doc.get("incident_status") and doc["incident_status"] != row.get("status"):
        errors.append(f"{doc['doc_id']}: 故障状态不一致（文档={doc['incident_status']}，表={row.get('status')}）")
    # 返回全部不一致项
    return errors


def run_consistency_check(docs: List[Dict[str, Any]], db: Any = None) -> bool:
    """
    执行一致性校验，返回是否全部通过。
    支持传入外部 db（便于测试），默认创建 BusinessDB 实例。

    参数：
        docs: 知识库文档列表
        db: 外部业务库对象（None 时默认创建）

    返回：
        True 全部一致，False 存在不一致
    """
    # 未传入外部 db 时默认实例化业务库
    if db is None:
        from integrations.business_db import BusinessDB
        db = BusinessDB(str(DEFAULT_DB_PATH))

    # 收集全部不一致项
    errors: List[str] = []
    # 已校验的退款政策数
    checked_policy = 0
    # 已校验的故障公告数
    checked_incident = 0

    # 遍历文档逐个校验
    for doc in docs:
        # 只要文档携带政策编码/故障编码就参与校验，不限于特定 category
        if doc.get("policy_code"):
            # 按政策编码查业务表
            row = db.get_refund_policy_by_code(doc["policy_code"])
            # 比对并收集不一致项
            errors.extend(_check_refund_policy(doc, row))
            # 计数加一
            checked_policy += 1
        if doc.get("incident_code"):
            # 按故障编码查业务表
            row = db.get_incident_by_code(doc["incident_code"])
            # 比对并收集不一致项
            errors.extend(_check_incident(doc, row))
            # 计数加一
            checked_incident += 1

    # 打印校验覆盖范围
    print(f"一致性校验：退款政策 {checked_policy} 篇，故障公告 {checked_incident} 篇")
    # 存在不一致时输出明细
    if errors:
        # 逐条打印不一致项
        for err in errors:
            print(f"  [不一致] {err}")
        # 打印失败汇总
        print(f"校验未通过：{len(errors)} 处不一致")
        # 返回失败
        return False
    # 全部一致时打印通过
    print("校验通过：知识库结构化字段与业务库一致")
    # 返回成功
    return True


def main() -> int:
    """
    命令行入口：解析参数并执行一致性校验。

    参数：
        无

    返回：
        进程退出码（0 通过，1 不一致）
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="Check knowledge base consistency against business DB.")
    # 文档 JSON 路径
    parser.add_argument("--docs", default=str(DEFAULT_DOCS_PATH))
    # 业务库路径
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    # 解析参数
    args = parser.parse_args()

    # 读取并解析文档 JSON
    docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))
    # 延迟导入业务库访问类
    from integrations.business_db import BusinessDB
    # 实例化业务库
    db = BusinessDB(args.db_path)
    # 执行校验并按结果返回退出码
    return 0 if run_consistency_check(docs, db=db) else 1


if __name__ == "__main__":
    # 直接运行脚本时以返回码退出
    raise SystemExit(main())