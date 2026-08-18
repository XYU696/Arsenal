import json

from .db import BusinessDB


def main() -> int:
    db = BusinessDB()
    samples = {
        "account_status": db.get_account_status(account_id="ACC-ZM-01"),
        "order_lookup": db.get_order("ORD-20260728-1001"),
        "refund_policy": db.match_refund_policy("EchoMind Pro", "yearly", "2026-07-29T00:00:00Z"),
        "incidents": db.search_incidents(category="login"),
    }
    print(json.dumps(samples, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
