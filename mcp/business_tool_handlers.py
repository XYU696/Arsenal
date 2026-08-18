"""
基于 SQLite 业务库的工具 handler。

设计目标：
1. 尽量保持简单，直接把数据库查询包装成 MCP 可调用工具。
2. 每个 handler 只做一件事：取参数、查库、整理结果。
3. 返回结果统一为可序列化的 dict / list，方便后续 Agent Runtime 直接消费。
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from integrations.business_db import BusinessDB


class BusinessToolHandlers:
    """把业务数据库查询能力封装成 MCP 工具 handler。"""

    def __init__(self, db: Optional[BusinessDB] = None):
        self._db = db or BusinessDB()

    async def account_status_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询账号状态、角色和权限。

        支持两种输入：
        1. account_id：最精确的查找方式
        2. email：当用户只提供邮箱时，用邮箱查第一个匹配账号
        """
        account_id = params.get("account_id")
        email = params.get("email")
        result = self._db.get_account_status(account_id=account_id, email=email)
        if not result:
            return {
                "found": False,
                "message": "未查询到对应账号，请核对 account_id 或 email。",
                "query": {"account_id": account_id, "email": email},
            }

        return {
            "found": True,
            "account_id": result.get("account_id"),
            "customer_code": result.get("customer_code"),
            "customer_name": result.get("name"),
            "email": result.get("email"),
            "tier": result.get("tier"),
            "customer_status": result.get("customer_status"),
            "account_status": result.get("account_status"),
            "role": result.get("role"),
            "permissions": result.get("permissions", []),
            "last_login_at": result.get("last_login_at"),
            "locked_reason": result.get("locked_reason"),
        }

    async def order_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单信息。

        支持两种模式：
        1. order_no：精确查询某一笔订单
        2. customer_code：查询某个客户最近的订单列表
        """
        order_no = params.get("order_no")
        customer_code = params.get("customer_code")

        if order_no:
            order = self._db.get_order(order_no)
            if not order:
                return {
                    "found": False,
                    "message": "未找到对应订单号。",
                    "query": {"order_no": order_no},
                }
            return {
                "found": True,
                "mode": "single_order",
                "order": {
                    "order_no": order.get("order_no"),
                    "customer_code": order.get("customer_code"),
                    "customer_name": order.get("name"),
                    "product_name": order.get("product_name"),
                    "plan_type": order.get("plan_type"),
                    "amount": order.get("amount"),
                    "currency": order.get("currency"),
                    "payment_status": order.get("payment_status"),
                    "order_status": order.get("order_status"),
                    "created_at": order.get("created_at"),
                    "paid_at": order.get("paid_at"),
                },
            }

        if customer_code:
            orders = self._db.list_orders_for_customer(customer_code)
            return {
                "found": bool(orders),
                "mode": "customer_orders",
                "customer_code": customer_code,
                "order_count": len(orders),
                "orders": [
                    {
                        "order_no": item.get("order_no"),
                        "product_name": item.get("product_name"),
                        "plan_type": item.get("plan_type"),
                        "amount": item.get("amount"),
                        "currency": item.get("currency"),
                        "payment_status": item.get("payment_status"),
                        "order_status": item.get("order_status"),
                        "created_at": item.get("created_at"),
                    }
                    for item in orders
                ],
            }

        return {
            "found": False,
            "message": "缺少查询参数，请提供 order_no 或 customer_code。",
        }

    async def order_items_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单商品项。

        这个工具用于把“订单头”拆成“订单内的具体商品事实”，
        方便后续退货、换货、部分退款等动作型工具使用。
        """
        order_no = params.get("order_no")
        if not order_no:
            return {
                "found": False,
                "message": "缺少 order_no，无法查询订单商品项。",
            }

        items = self._db.get_order_items(order_no)
        if not items:
            return {
                "found": False,
                "message": "未查询到订单商品项，请核对 order_no。",
                "query": {"order_no": order_no},
            }

        return {
            "found": True,
            "order_no": order_no,
            "item_count": len(items),
            "items": [
                {
                    "order_item_id": item.get("id"),
                    "sku_id": item.get("sku_id"),
                    "sku_name": item.get("sku_name"),
                    "category": item.get("category"),
                    "quantity": item.get("quantity"),
                    "unit_price": item.get("unit_price"),
                    "discount_amount": item.get("discount_amount"),
                    "final_amount": item.get("final_amount"),
                    "item_status": item.get("item_status"),
                    "fulfillment_status": item.get("fulfillment_status"),
                    "returnable": bool(item.get("returnable")),
                    "exchangeable": bool(item.get("exchangeable")),
                }
                for item in items
            ],
        }

    async def shipment_tracking_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单物流状态。

        支持按订单号或物流单号查询，优先回答“包裹现在到哪里了、是否存在异常”。
        """
        order_no = params.get("order_no")
        tracking_no = params.get("tracking_no")
        shipment = self._db.get_shipment(order_no=order_no, tracking_no=tracking_no)
        if not shipment:
            return {
                "found": False,
                "message": "未查询到对应物流信息，请核对 order_no 或 tracking_no。",
                "query": {"order_no": order_no, "tracking_no": tracking_no},
            }

        return {
            "found": True,
            "order_no": shipment.get("order_no"),
            "customer_code": shipment.get("customer_code"),
            "customer_name": shipment.get("customer_name"),
            "product_name": shipment.get("product_name"),
            "delivery_type": shipment.get("delivery_type"),
            "order_status": shipment.get("order_status"),
            "shipment_no": shipment.get("shipment_no"),
            "tracking_no": shipment.get("tracking_no"),
            "carrier": shipment.get("carrier"),
            "shipment_status": shipment.get("shipment_status"),
            "last_checkpoint": shipment.get("last_checkpoint"),
            "last_checkpoint_at": shipment.get("last_checkpoint_at"),
            "signed_at": shipment.get("signed_at"),
            "receiver_name": shipment.get("receiver_name"),
            "exception_type": shipment.get("exception_type"),
            "exception_summary": shipment.get("exception_summary"),
        }

    async def payment_attempt_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单支付尝试记录。

        用于重复扣费、支付失败、支付重试等售后场景。
        """
        order_no = params.get("order_no")
        if not order_no:
            return {
                "found": False,
                "message": "缺少 order_no，无法查询支付尝试。",
            }

        attempts = self._db.get_payment_attempts(order_no)
        if not attempts:
            return {
                "found": False,
                "message": "未查询到该订单的支付尝试记录。",
                "query": {"order_no": order_no},
            }

        duplicate_risk = any(bool(item.get("is_duplicate_risk")) for item in attempts)
        return {
            "found": True,
            "order_no": order_no,
            "payment_status": attempts[0].get("payment_status"),
            "order_status": attempts[0].get("order_status"),
            "attempt_count": len(attempts),
            "duplicate_risk": duplicate_risk,
            "attempts": [
                {
                    "attempt_no": item.get("attempt_no"),
                    "payment_channel": item.get("payment_channel"),
                    "attempt_status": item.get("attempt_status"),
                    "amount": item.get("amount"),
                    "currency": item.get("currency"),
                    "transaction_id": item.get("transaction_id"),
                    "failure_reason": item.get("failure_reason"),
                    "is_duplicate_risk": bool(item.get("is_duplicate_risk")),
                    "created_at": item.get("created_at"),
                }
                for item in attempts
            ],
        }

    async def refund_policy_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询退款政策。

        这个工具只负责“查政策”，不直接给出某一笔具体订单是否具备退款资格的结论。
        具体订单级资格判断由 `refund_eligibility_check` 负责。
        """
        product_name = params.get("product_name")
        product_type = params.get("product_type")
        plan_type = params.get("plan_type")
        purchase_time = params.get("purchase_time")

        policy = self._db.match_refund_policy(product_name, plan_type, purchase_time)
        if not policy:
            return {
                "matched": False,
                "message": "未找到匹配的退款规则，建议转人工或检查产品信息。",
                "query": {
                    "product_name": product_name,
                    "product_type": product_type,
                    "plan_type": plan_type,
                    "purchase_time": purchase_time,
                },
            }

        return {
            "matched": True,
            "policy_code": policy.get("policy_code"),
            "product_name": policy.get("product_name"),
            "product_type": policy.get("product_type"),
            "plan_type": policy.get("plan_type"),
            "refund_window_hours": policy.get("refund_window_hours"),
            "usage_limit_percent": policy.get("usage_limit_percent"),
            "allow_refund": bool(policy.get("allow_refund")),
            "manual_review_required": bool(policy.get("manual_review_required")),
            "review_reason_template": policy.get("review_reason_template"),
            "refund_method": policy.get("refund_method"),
            "policy_category": policy.get("policy_category"),
            "policy_text": policy.get("policy_text"),
            "effective_from": policy.get("effective_from"),
            "effective_to": policy.get("effective_to"),
        }

    async def refund_request_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询退款申请状态。

        既支持按 refund_no 精确查，也支持按 order_no 查最近一条退款申请。
        """
        refund_no = params.get("refund_no")
        order_no = params.get("order_no")
        refund_request = self._db.get_refund_request(refund_no=refund_no, order_no=order_no)
        if not refund_request:
            return {
                "found": False,
                "message": "未查询到对应退款申请。",
                "query": {"refund_no": refund_no, "order_no": order_no},
            }

        return {
            "found": True,
            "refund_no": refund_request.get("refund_no"),
            "order_no": refund_request.get("order_no"),
            "product_name": refund_request.get("product_name"),
            "customer_code": refund_request.get("customer_code"),
            "customer_name": refund_request.get("customer_name"),
            "refund_reason": refund_request.get("refund_reason"),
            "requested_amount": refund_request.get("requested_amount"),
            "approved_amount": refund_request.get("approved_amount"),
            "refund_status": refund_request.get("refund_status"),
            "review_required": bool(refund_request.get("review_required")),
            "review_status": refund_request.get("review_status"),
            "review_reason": refund_request.get("review_reason"),
            "refund_channel": refund_request.get("refund_channel"),
            "requested_at": refund_request.get("requested_at"),
            "reviewed_at": refund_request.get("reviewed_at"),
            "completed_at": refund_request.get("completed_at"),
        }

    async def refund_eligibility_check(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        判断一笔具体订单是否具备退款资格。

        这里故意把“规则判断”和“订单事实判断”合并到一个工具里，
        目的是避免 Agent 只查规则却把结果说成已经核实过订单。
        """
        order_no = params.get("order_no")
        if not order_no:
            return {
                "eligible": False,
                "order_found": False,
                "message": "缺少 order_no，无法判断具体订单的退款资格。",
            }

        order = self._db.get_order(order_no)
        if not order:
            return {
                "eligible": False,
                "order_found": False,
                "message": "未找到对应订单，请核对 order_no。",
                "query": {"order_no": order_no},
            }

        existing_request = self._db.get_refund_request(order_no=order_no)
        payment_attempts = self._db.get_payment_attempts(order_no)
        policy = self._db.match_refund_policy(
            product_name=order.get("product_name"),
            plan_type=order.get("plan_type"),
            at_time=order.get("paid_at") or order.get("created_at"),
        )
        if not policy:
            return {
                "eligible": False,
                "order_found": True,
                "message": "未找到匹配的退款规则，建议转人工进一步核实。",
                "order_no": order_no,
            }

        duplicate_risk = any(bool(item.get("is_duplicate_risk")) for item in payment_attempts)
        allow_refund = bool(policy.get("allow_refund"))
        manual_review_required = bool(policy.get("manual_review_required"))
        already_requested = existing_request is not None

        reason_codes: List[str] = []
        if not allow_refund:
            reason_codes.append("policy_not_allow")
        if manual_review_required:
            reason_codes.append("manual_review_required")
        if duplicate_risk:
            reason_codes.append("duplicate_payment_risk")
        if already_requested:
            reason_codes.append("refund_request_exists")

        # 第一版先用订单状态和支付事实做保守资格判断，不在此处引入复杂使用量计算。
        eligible = allow_refund and order.get("payment_status") == "paid" and not already_requested
        if order.get("payment_status") != "paid":
            eligible = False
            reason_codes.append("payment_not_paid")
        if order.get("order_status") in {"refunded", "cancelled"}:
            eligible = False
            reason_codes.append("order_status_not_supported")

        summary_parts = [
            f"订单 {order_no} 当前支付状态为 {order.get('payment_status')}。",
            f"匹配到退款政策 {policy.get('policy_code')}。",
        ]
        if manual_review_required:
            summary_parts.append("该订单退款需要人工审核。")
        if duplicate_risk:
            summary_parts.append("支付记录中存在重复扣费风险。")
        if already_requested:
            summary_parts.append("该订单已经存在退款申请记录。")

        return {
            "eligible": eligible,
            "order_found": True,
            "order_no": order_no,
            "product_name": order.get("product_name"),
            "plan_type": order.get("plan_type"),
            "payment_status": order.get("payment_status"),
            "order_status": order.get("order_status"),
            "policy_code": policy.get("policy_code"),
            "allow_refund": allow_refund,
            "manual_review_required": manual_review_required,
            "already_requested": already_requested,
            "existing_refund_no": existing_request.get("refund_no") if existing_request else None,
            "duplicate_risk": duplicate_risk,
            "reason_codes": reason_codes,
            "summary": "".join(summary_parts),
        }

    async def create_refund_request(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建退款申请。

        这一步是真正的业务动作，不再只是解释规则。
        """
        order_no = params.get("order_no")
        refund_reason = params.get("refund_reason")
        requested_amount = params.get("requested_amount")

        if not order_no or not refund_reason:
            return {
                "created": False,
                "message": "缺少必要参数，请至少提供 order_no 和 refund_reason。",
                "query": {
                    "order_no": order_no,
                    "refund_reason": refund_reason,
                },
            }

        order = self._db.get_order(order_no)
        if not order:
            return {
                "created": False,
                "message": "未找到对应订单，无法创建退款申请。",
                "query": {"order_no": order_no},
            }

        if requested_amount is None:
            requested_amount = float(order.get("amount") or 0.0)
        else:
            requested_amount = float(requested_amount)

        policy = self._db.match_refund_policy(
            product_name=order.get("product_name"),
            plan_type=order.get("plan_type"),
            at_time=order.get("paid_at") or order.get("created_at"),
        )
        review_required = bool(policy.get("manual_review_required")) if policy else True
        review_status = "pending" if review_required else "none"
        review_reason = policy.get("review_reason_template") if policy else "未匹配到自动退款规则，需人工审核。"

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        refund_no = f"RFD-REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        refund_request = self._db.create_refund_request(
            order_no=order_no,
            refund_no=refund_no,
            refund_reason=refund_reason,
            requested_amount=requested_amount,
            review_required=review_required,
            review_status=review_status,
            review_reason=review_reason,
            refund_channel=(policy or {}).get("refund_method", "manual"),
            requested_at=now,
        )

        return {
            "created": True,
            "refund_no": refund_request.get("refund_no"),
            "order_no": refund_request.get("order_no"),
            "requested_amount": refund_request.get("requested_amount"),
            "refund_status": refund_request.get("refund_status"),
            "review_required": bool(refund_request.get("review_required")),
            "review_status": refund_request.get("review_status"),
            "review_reason": refund_request.get("review_reason"),
            "requested_at": refund_request.get("requested_at"),
        }

    async def incident_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询已知故障。

        支持：
        1. category：按故障类别精确筛选
        2. keyword：按标题或摘要模糊匹配
        """
        category = params.get("category")
        keyword = params.get("keyword")
        incidents = self._db.search_incidents(category=category, keyword=keyword)

        return {
            "matched": bool(incidents),
            "count": len(incidents),
            "incidents": [
                {
                    "incident_code": item.get("incident_code"),
                    "title": item.get("title"),
                    "category": item.get("category"),
                    "severity": item.get("severity"),
                    "status": item.get("status"),
                    "affected_scope": item.get("affected_scope"),
                    "summary": item.get("summary"),
                    "started_at": item.get("started_at"),
                    "resolved_at": item.get("resolved_at"),
                    "updated_at": item.get("updated_at"),
                }
                for item in incidents
            ],
        }

    async def create_handoff_ticket(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建转人工工单。

        这是单 Agent 场景里的“安全退出路径”：
        当系统无法继续自动处理时，至少要留下一个可追踪的工单记录。
        """
        customer_code = params.get("customer_code")
        customer_id = None
        if customer_code:
            customer = self._db.get_customer_by_code(customer_code)
            customer_id = customer.get("id") if customer else None

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ticket_no = f"TKT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        ticket = self._db.create_handoff_ticket(
            customer_id=customer_id,
            conv_id=params["conv_id"],
            source_intent=params["source_intent"],
            priority=params.get("priority", "medium"),
            reason=params["reason"],
            summary=params["summary"],
            assigned_queue=params.get("assigned_queue", "general_support"),
            ticket_no=ticket_no,
            created_at=now,
        )
        return {
            "created": True,
            "ticket_no": ticket.get("ticket_no"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "assigned_queue": ticket.get("assigned_queue"),
            "created_at": ticket.get("created_at"),
        }
