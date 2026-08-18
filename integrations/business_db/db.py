import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "business" / "echomind.db"


class BusinessDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_customer_by_code(self, customer_code: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM customers WHERE customer_code = ?"
        return self._fetch_one(sql, [customer_code])

    def get_account_status(self, account_id: Optional[str] = None, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if account_id:
            sql = """
            SELECT a.*, c.customer_code, c.name, c.email, c.tier, c.status AS customer_status
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE a.account_id = ?
            """
            row = self._fetch_one(sql, [account_id])
        elif email:
            sql = """
            SELECT a.*, c.customer_code, c.name, c.email, c.tier, c.status AS customer_status
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE c.email = ?
            ORDER BY a.id ASC
            LIMIT 1
            """
            row = self._fetch_one(sql, [email])
        else:
            return None

        if row and isinstance(row.get("permissions_json"), str):
            row["permissions"] = json.loads(row["permissions_json"])
        return row

    def get_order(self, order_no: str) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT o.*, c.customer_code, c.name, c.email, c.tier
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE o.order_no = ?
        """
        return self._fetch_one(sql, [order_no])

    def get_order_items(self, order_no: str) -> List[Dict[str, Any]]:
        """
        根据订单号查询订单商品项。

        这里同时带出订单头信息，方便上层 handler 直接组织返回，
        避免后续再做一次额外查询。
        """
        sql = """
        SELECT
            oi.*,
            o.order_no,
            o.product_name,
            o.product_type,
            o.plan_type,
            o.order_status,
            o.payment_status
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.order_no = ?
        ORDER BY oi.id ASC
        """
        return self._fetch_all(sql, [order_no])

    def get_shipment(self, order_no: Optional[str] = None, tracking_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        按订单号或物流单号查询物流信息。

        两种查询方式都很常见：
        1. 用户提供 order_no
        2. 用户直接贴 tracking_no
        """
        if order_no:
            sql = """
            SELECT
                s.*,
                o.order_no,
                o.delivery_type,
                o.order_status,
                o.product_name,
                c.customer_code,
                c.name AS customer_name
            FROM shipments s
            JOIN orders o ON o.id = s.order_id
            JOIN customers c ON c.id = o.customer_id
            WHERE o.order_no = ?
            ORDER BY s.id DESC
            LIMIT 1
            """
            return self._fetch_one(sql, [order_no])

        if tracking_no:
            sql = """
            SELECT
                s.*,
                o.order_no,
                o.delivery_type,
                o.order_status,
                o.product_name,
                c.customer_code,
                c.name AS customer_name
            FROM shipments s
            JOIN orders o ON o.id = s.order_id
            JOIN customers c ON c.id = o.customer_id
            WHERE s.tracking_no = ?
            ORDER BY s.id DESC
            LIMIT 1
            """
            return self._fetch_one(sql, [tracking_no])
        return None

    def get_payment_attempts(self, order_no: str) -> List[Dict[str, Any]]:
        """
        查询某笔订单的所有支付尝试。

        用于重复扣费、支付失败、支付逆转等场景。
        """
        sql = """
        SELECT
            p.*,
            o.order_no,
            o.payment_status,
            o.order_status,
            o.amount AS order_amount
        FROM payment_attempts p
        JOIN orders o ON o.id = p.order_id
        WHERE o.order_no = ?
        ORDER BY p.created_at ASC
        """
        return self._fetch_all(sql, [order_no])

    def list_orders_for_customer(self, customer_code: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT o.*
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE c.customer_code = ?
        ORDER BY o.created_at DESC
        """
        return self._fetch_all(sql, [customer_code])

    def match_refund_policy(self, product_name: str, plan_type: str, at_time: Optional[str] = None) -> Optional[Dict[str, Any]]:
        effective_time = at_time or "9999-12-31T23:59:59Z"
        sql = """
        SELECT *
        FROM refund_policies
        WHERE product_name = ?
          AND plan_type = ?
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC
        LIMIT 1
        """
        return self._fetch_one(sql, [product_name, plan_type, effective_time, effective_time])

    def get_refund_policy_by_code(self, policy_code: str) -> Optional[Dict[str, Any]]:
        """按政策编码精确查询退款政策（用于知识库一致性校验）。"""
        sql = "SELECT * FROM refund_policies WHERE policy_code = ?"
        return self._fetch_one(sql, [policy_code])

    def get_incident_by_code(self, incident_code: str) -> Optional[Dict[str, Any]]:
        """按故障编码精确查询故障公告（用于知识库一致性校验）。"""
        sql = "SELECT * FROM incidents WHERE incident_code = ?"
        return self._fetch_one(sql, [incident_code])

    def get_refund_request(
        self,
        refund_no: Optional[str] = None,
        order_no: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        查询退款申请。

        支持两种方式：
        1. 直接按 refund_no 精确查
        2. 按 order_no 查最近一条退款申请
        """
        if refund_no:
            sql = """
            SELECT
                rr.*,
                o.order_no,
                o.product_name,
                c.customer_code,
                c.name AS customer_name
            FROM refund_requests rr
            JOIN orders o ON o.id = rr.order_id
            JOIN customers c ON c.id = rr.customer_id
            WHERE rr.refund_no = ?
            LIMIT 1
            """
            return self._fetch_one(sql, [refund_no])

        if order_no:
            sql = """
            SELECT
                rr.*,
                o.order_no,
                o.product_name,
                c.customer_code,
                c.name AS customer_name
            FROM refund_requests rr
            JOIN orders o ON o.id = rr.order_id
            JOIN customers c ON c.id = rr.customer_id
            WHERE o.order_no = ?
            ORDER BY rr.created_at DESC
            LIMIT 1
            """
            return self._fetch_one(sql, [order_no])
        return None

    def create_refund_request(
        self,
        order_no: str,
        refund_no: str,
        refund_reason: str,
        requested_amount: float,
        review_required: bool,
        review_status: str,
        review_reason: Optional[str],
        refund_channel: str,
        requested_at: str,
    ) -> Dict[str, Any]:
        """
        为指定订单创建退款申请。

        这里先保持实现简单：
        - 自动从订单找到 order_id / customer_id
        - 自动推导 refund_status
        - 创建后直接回查整条记录
        """
        order = self.get_order(order_no)
        if not order:
            raise ValueError(f"未找到订单: {order_no}")

        refund_status = "pending_review" if review_required else "created"
        sql = """
        INSERT INTO refund_requests (
            refund_no, order_id, customer_id, refund_reason, requested_amount,
            approved_amount, refund_status, review_required, review_status,
            review_reason, refund_channel, requested_at, reviewed_at,
            completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """
        params = [
            refund_no,
            order["id"],
            order["customer_id"],
            refund_reason,
            requested_amount,
            refund_status,
            1 if review_required else 0,
            review_status,
            review_reason,
            refund_channel,
            requested_at,
            requested_at,
            requested_at,
        ]
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()
        return self.get_refund_request(refund_no=refund_no) or {}

    def search_incidents(self, category: Optional[str] = None, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if keyword:
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            needle = f"%{keyword}%"
            params.extend([needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
        SELECT *
        FROM incidents
        {where}
        ORDER BY
          CASE status
            WHEN 'investigating' THEN 1
            WHEN 'identified' THEN 2
            WHEN 'monitoring' THEN 3
            WHEN 'resolved' THEN 4
            ELSE 5
          END,
          updated_at DESC
        """
        return self._fetch_all(sql, params)

    def create_handoff_ticket(
        self,
        customer_id: Optional[int],
        conv_id: str,
        source_intent: str,
        priority: str,
        reason: str,
        summary: str,
        assigned_queue: str,
        ticket_no: str,
        created_at: str,
    ) -> Dict[str, Any]:
        sql = """
        INSERT INTO handoff_tickets (
            ticket_no, customer_id, conv_id, source_intent, priority, status,
            reason, summary, assigned_queue, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
        """
        params = [
            ticket_no, customer_id, conv_id, source_intent, priority,
            reason, summary, assigned_queue, created_at, created_at,
        ]
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()
        return self.get_handoff_ticket(ticket_no) or {}

    def get_handoff_ticket(self, ticket_no: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM handoff_tickets WHERE ticket_no = ?"
        return self._fetch_one(sql, [ticket_no])

    def _fetch_one(self, sql: str, params: List[Any]) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
