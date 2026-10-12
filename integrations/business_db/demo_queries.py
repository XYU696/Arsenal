import json  # 打印结果的 JSON 序列化

from .db import BusinessDB  # 业务库访问类


def main() -> int:
    """
    演示脚本：跑几类典型查询并打印结果，验证业务库可用。

    参数：
        无

    返回：
        进程退出码（0 成功）
    """
    # 用默认路径实例化数据库访问对象
    db = BusinessDB()
    # 组装各类查询的示例结果
    samples = {
        "account_status": db.get_account_status(account_id="ACC-ZM-01"),  # 账户状态查询
        "order_lookup": db.get_order("ORD-20260728-1001"),  # 订单查询
        "refund_policy": db.match_refund_policy("EchoMind Pro", "yearly", "2026-07-29T00:00:00Z"),  # 退款政策匹配
        "incidents": db.search_incidents(category="login"),  # 故障公告搜索
    }
    # 打印结果（中文不转义，缩进 2）
    print(json.dumps(samples, ensure_ascii=False, indent=2))
    # 成功退出
    return 0


if __name__ == "__main__":
    # 直接运行脚本时以返回码退出
    raise SystemExit(main())