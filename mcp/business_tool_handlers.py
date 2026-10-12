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
        # 保存业务数据库访问对象；未传入时新建
        self._db = db or BusinessDB()

    async def account_status_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询账号状态、角色和权限。

        支持两种输入：
        1. account_id：最精确的查找方式
        2. email：当用户只提供邮箱时，用邮箱查第一个匹配账号

        参数：
            params: 工具调用参数（含 account_id 或 email）
            context: 调用上下文

        返回：
            账号状态查询结果字典
        """
        # 取参数中的账号 ID
        account_id = params.get("account_id")
        # 取参数中的邮箱
        email = params.get("email")
        # 调用数据库查询账号状态
        result = self._db.get_account_status(account_id=account_id, email=email)
        # 查询无结果时返回未找到提示
        if not result:
            return {
                "found": False,  # 未找到标记
                "message": "未查询到对应账号，请核对 account_id 或 email。",  # 提示信息
                "query": {"account_id": account_id, "email": email},  # 回显查询条件
            }

        # 返回完整的账号状态信息
        return {
            "found": True,  # 找到标记
            "account_id": result.get("account_id"),  # 账号 ID
            "customer_code": result.get("customer_code"),  # 客户编码
            "customer_name": result.get("name"),  # 客户名称
            "email": result.get("email"),  # 邮箱
            "tier": result.get("tier"),  # 客户等级
            "customer_status": result.get("customer_status"),  # 客户状态
            "account_status": result.get("account_status"),  # 账号状态
            "role": result.get("role"),  # 角色
            "permissions": result.get("permissions", []),  # 权限列表
            "last_login_at": result.get("last_login_at"),  # 最近登录时间
            "locked_reason": result.get("locked_reason"),  # 锁定原因
        }

    async def order_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单信息。

        支持两种模式：
        1. order_no：精确查询某一笔订单
        2. customer_code：查询某个客户最近的订单列表

        参数：
            params: 工具调用参数（含 order_no 或 customer_code）
            context: 调用上下文

        返回：
            订单查询结果字典
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 取参数中的客户编码
        customer_code = params.get("customer_code")

        # 模式一：按订单号精确查询
        if order_no:
            # 查询单笔订单
            order = self._db.get_order(order_no)
            # 订单不存在时返回未找到提示
            if not order:
                return {
                    "found": False,  # 未找到标记
                    "message": "未找到对应订单号。",  # 提示信息
                    "query": {"order_no": order_no},  # 回显查询条件
                }
            # 返回单笔订单详情
            return {
                "found": True,  # 找到标记
                "mode": "single_order",  # 查询模式：单笔订单
                "order": {
                    "order_no": order.get("order_no"),  # 订单号
                    "customer_code": order.get("customer_code"),  # 客户编码
                    "customer_name": order.get("name"),  # 客户名称
                    "product_name": order.get("product_name"),  # 产品名称
                    "plan_type": order.get("plan_type"),  # 套餐类型
                    "amount": order.get("amount"),  # 金额
                    "currency": order.get("currency"),  # 币种
                    "payment_status": order.get("payment_status"),  # 支付状态
                    "order_status": order.get("order_status"),  # 订单状态
                    "created_at": order.get("created_at"),  # 创建时间
                    "paid_at": order.get("paid_at"),  # 支付时间
                },
            }

        # 模式二：按客户编码查询订单列表
        if customer_code:
            # 查询客户的订单列表
            orders = self._db.list_orders_for_customer(customer_code)
            # 返回客户订单列表
            return {
                "found": bool(orders),  # 是否有订单
                "mode": "customer_orders",  # 查询模式：客户订单列表
                "customer_code": customer_code,  # 客户编码
                "order_count": len(orders),  # 订单数量
                "orders": [
                    {
                        "order_no": item.get("order_no"),  # 订单号
                        "product_name": item.get("product_name"),  # 产品名称
                        "plan_type": item.get("plan_type"),  # 套餐类型
                        "amount": item.get("amount"),  # 金额
                        "currency": item.get("currency"),  # 币种
                        "payment_status": item.get("payment_status"),  # 支付状态
                        "order_status": item.get("order_status"),  # 订单状态
                        "created_at": item.get("created_at"),  # 创建时间
                    }
                    for item in orders
                ],
            }

        # 两个参数都没提供时返回提示
        return {
            "found": False,  # 未找到标记
            "message": "缺少查询参数，请提供 order_no 或 customer_code。",  # 提示信息
        }

    async def order_items_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单商品项。

        这个工具用于把"订单头"拆成"订单内的具体商品事实"，
        方便后续退货、换货、部分退款等动作型工具使用。

        参数：
            params: 工具调用参数（含 order_no）
            context: 调用上下文

        返回：
            订单商品项列表结果字典
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 缺少订单号时返回提示
        if not order_no:
            return {
                "found": False,  # 未找到标记
                "message": "缺少 order_no，无法查询订单商品项。",  # 提示信息
            }

        # 查询订单商品项
        items = self._db.get_order_items(order_no)
        # 无商品项时返回未找到提示
        if not items:
            return {
                "found": False,  # 未找到标记
                "message": "未查询到订单商品项，请核对 order_no。",  # 提示信息
                "query": {"order_no": order_no},  # 回显查询条件
            }

        # 返回商品项明细列表
        return {
            "found": True,  # 找到标记
            "order_no": order_no,  # 订单号
            "item_count": len(items),  # 商品项数量
            "items": [
                {
                    "order_item_id": item.get("id"),  # 订单商品项 ID
                    "sku_id": item.get("sku_id"),  # SKU 编码
                    "sku_name": item.get("sku_name"),  # SKU 名称
                    "category": item.get("category"),  # 商品类别
                    "quantity": item.get("quantity"),  # 数量
                    "unit_price": item.get("unit_price"),  # 单价
                    "discount_amount": item.get("discount_amount"),  # 优惠金额
                    "final_amount": item.get("final_amount"),  # 实际金额
                    "item_status": item.get("item_status"),  # 商品项状态
                    "fulfillment_status": item.get("fulfillment_status"),  # 履约状态
                    "returnable": bool(item.get("returnable")),  # 是否可退
                    "exchangeable": bool(item.get("exchangeable")),  # 是否可换
                }
                for item in items
            ],
        }

    async def shipment_tracking_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单物流状态。

        支持按订单号或物流单号查询，优先回答"包裹现在到哪里了、是否存在异常"。

        参数：
            params: 工具调用参数（含 order_no 或 tracking_no）
            context: 调用上下文

        返回：
            物流跟踪信息结果字典
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 取参数中的物流单号
        tracking_no = params.get("tracking_no")
        # 查询物流信息
        shipment = self._db.get_shipment(order_no=order_no, tracking_no=tracking_no)
        # 无物流信息时返回未找到提示
        if not shipment:
            return {
                "found": False,  # 未找到标记
                "message": "未查询到对应物流信息，请核对 order_no 或 tracking_no。",  # 提示信息
                "query": {"order_no": order_no, "tracking_no": tracking_no},  # 回显查询条件
            }

        # 返回完整物流信息
        return {
            "found": True,  # 找到标记
            "order_no": shipment.get("order_no"),  # 订单号
            "customer_code": shipment.get("customer_code"),  # 客户编码
            "customer_name": shipment.get("customer_name"),  # 客户名称
            "product_name": shipment.get("product_name"),  # 产品名称
            "delivery_type": shipment.get("delivery_type"),  # 配送方式
            "order_status": shipment.get("order_status"),  # 订单状态
            "shipment_no": shipment.get("shipment_no"),  # 发货单号
            "tracking_no": shipment.get("tracking_no"),  # 物流单号
            "carrier": shipment.get("carrier"),  # 承运商
            "shipment_status": shipment.get("shipment_status"),  # 物流状态
            "last_checkpoint": shipment.get("last_checkpoint"),  # 最近节点
            "last_checkpoint_at": shipment.get("last_checkpoint_at"),  # 最近节点时间
            "signed_at": shipment.get("signed_at"),  # 签收时间
            "receiver_name": shipment.get("receiver_name"),  # 收件人
            "exception_type": shipment.get("exception_type"),  # 异常类型
            "exception_summary": shipment.get("exception_summary"),  # 异常摘要
        }

    async def payment_attempt_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询订单支付尝试记录。

        用于重复扣费、支付失败、支付重试等售后场景。

        参数：
            params: 工具调用参数（含 order_no）
            context: 调用上下文

        返回：
            支付尝试记录结果字典
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 缺少订单号时返回提示
        if not order_no:
            return {
                "found": False,  # 未找到标记
                "message": "缺少 order_no，无法查询支付尝试。",  # 提示信息
            }

        # 查询支付尝试记录
        attempts = self._db.get_payment_attempts(order_no)
        # 无记录时返回未找到提示
        if not attempts:
            return {
                "found": False,  # 未找到标记
                "message": "未查询到该订单的支付尝试记录。",  # 提示信息
                "query": {"order_no": order_no},  # 回显查询条件
            }

        # 判断是否存在重复扣费风险（任一记录标记为有风险即为真）
        duplicate_risk = any(bool(item.get("is_duplicate_risk")) for item in attempts)
        # 返回支付尝试汇总与明细
        return {
            "found": True,  # 找到标记
            "order_no": order_no,  # 订单号
            "payment_status": attempts[0].get("payment_status"),  # 当前支付状态
            "order_status": attempts[0].get("order_status"),  # 当前订单状态
            "attempt_count": len(attempts),  # 尝试次数
            "duplicate_risk": duplicate_risk,  # 重复扣费风险标记
            "attempts": [
                {
                    "attempt_no": item.get("attempt_no"),  # 尝试序号
                    "payment_channel": item.get("payment_channel"),  # 支付渠道
                    "attempt_status": item.get("attempt_status"),  # 尝试状态
                    "amount": item.get("amount"),  # 金额
                    "currency": item.get("currency"),  # 币种
                    "transaction_id": item.get("transaction_id"),  # 交易 ID
                    "failure_reason": item.get("failure_reason"),  # 失败原因
                    "is_duplicate_risk": bool(item.get("is_duplicate_risk")),  # 是否重复扣费风险
                    "created_at": item.get("created_at"),  # 创建时间
                }
                for item in attempts
            ],
        }

    async def refund_policy_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询退款政策。

        这个工具只负责"查政策"，不直接给出某一笔具体订单是否具备退款资格的结论。
        具体订单级资格判断由 `refund_eligibility_check` 负责。

        参数：
            params: 工具调用参数（含产品、套餐、购买时间等）
            context: 调用上下文

        返回：
            匹配到的退款政策字典
        """
        # 取参数中的产品名称
        product_name = params.get("product_name")
        # 取参数中的产品类型
        product_type = params.get("product_type")
        # 取参数中的套餐类型
        plan_type = params.get("plan_type")
        # 取参数中的购买时间
        purchase_time = params.get("purchase_time")

        # 在退款规则表中匹配政策
        policy = self._db.match_refund_policy(product_name, plan_type, purchase_time)
        # 未匹配到政策时返回提示
        if not policy:
            return {
                "matched": False,  # 未匹配标记
                "message": "未找到匹配的退款规则，建议转人工或检查产品信息。",  # 提示信息
                "query": {  # 回显查询条件
                    "product_name": product_name,  # 产品名称
                    "product_type": product_type,  # 产品类型
                    "plan_type": plan_type,  # 套餐类型
                    "purchase_time": purchase_time,  # 购买时间
                },
            }

        # 返回匹配到的退款政策详情
        return {
            "matched": True,  # 匹配成功标记
            "policy_code": policy.get("policy_code"),  # 政策编码
            "product_name": policy.get("product_name"),  # 适用产品
            "product_type": policy.get("product_type"),  # 适用产品类型
            "plan_type": policy.get("plan_type"),  # 适用套餐
            "refund_window_hours": policy.get("refund_window_hours"),  # 退款窗口（小时）
            "usage_limit_percent": policy.get("usage_limit_percent"),  # 使用量上限（%）
            "allow_refund": bool(policy.get("allow_refund")),  # 是否允许退款
            "manual_review_required": bool(policy.get("manual_review_required")),  # 是否需人工审核
            "review_reason_template": policy.get("review_reason_template"),  # 审核原因模板
            "refund_method": policy.get("refund_method"),  # 退款方式
            "policy_category": policy.get("policy_category"),  # 政策类别
            "policy_text": policy.get("policy_text"),  # 政策原文
            "effective_from": policy.get("effective_from"),  # 生效开始时间
            "effective_to": policy.get("effective_to"),  # 生效结束时间
        }

    async def refund_request_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询退款申请状态。

        既支持按 refund_no 精确查，也支持按 order_no 查最近一条退款申请。

        参数：
            params: 工具调用参数（含 refund_no 或 order_no）
            context: 调用上下文

        返回：
            退款申请状态结果字典
        """
        # 取参数中的退款单号
        refund_no = params.get("refund_no")
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 查询退款申请记录
        refund_request = self._db.get_refund_request(refund_no=refund_no, order_no=order_no)
        # 无申请记录时返回未找到提示
        if not refund_request:
            return {
                "found": False,  # 未找到标记
                "message": "未查询到对应退款申请。",  # 提示信息
                "query": {"refund_no": refund_no, "order_no": order_no},  # 回显查询条件
            }

        # 返回退款申请完整信息
        return {
            "found": True,  # 找到标记
            "refund_no": refund_request.get("refund_no"),  # 退款单号
            "order_no": refund_request.get("order_no"),  # 订单号
            "product_name": refund_request.get("product_name"),  # 产品名称
            "customer_code": refund_request.get("customer_code"),  # 客户编码
            "customer_name": refund_request.get("customer_name"),  # 客户名称
            "refund_reason": refund_request.get("refund_reason"),  # 退款原因
            "requested_amount": refund_request.get("requested_amount"),  # 申请金额
            "approved_amount": refund_request.get("approved_amount"),  # 审核通过金额
            "refund_status": refund_request.get("refund_status"),  # 退款状态
            "review_required": bool(refund_request.get("review_required")),  # 是否需人工审核
            "review_status": refund_request.get("review_status"),  # 审核状态
            "review_reason": refund_request.get("review_reason"),  # 审核原因
            "refund_channel": refund_request.get("refund_channel"),  # 退款渠道
            "requested_at": refund_request.get("requested_at"),  # 申请时间
            "reviewed_at": refund_request.get("reviewed_at"),  # 审核时间
            "completed_at": refund_request.get("completed_at"),  # 完成时间
        }

    async def refund_eligibility_check(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        判断一笔具体订单是否具备退款资格。

        这里故意把"规则判断"和"订单事实判断"合并到一个工具里，
        目的是避免 Agent 只查规则却把结果说成已经核实过订单。

        参数：
            params: 工具调用参数（含 order_no）
            context: 调用上下文

        返回：
            退款资格判断结果字典（含资格标记、原因代码与摘要）
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 缺少订单号时返回提示
        if not order_no:
            return {
                "eligible": False,  # 不具备退款资格
                "order_found": False,  # 订单未查到
                "message": "缺少 order_no，无法判断具体订单的退款资格。",  # 提示信息
            }

        # 查询订单信息
        order = self._db.get_order(order_no)
        # 订单不存在时返回提示
        if not order:
            return {
                "eligible": False,  # 不具备退款资格
                "order_found": False,  # 订单未查到
                "message": "未找到对应订单，请核对 order_no。",  # 提示信息
                "query": {"order_no": order_no},  # 回显查询条件
            }

        # 查询该订单已有的退款申请
        existing_request = self._db.get_refund_request(order_no=order_no)
        # 查询该订单的支付尝试记录
        payment_attempts = self._db.get_payment_attempts(order_no)
        # 按订单事实匹配退款政策
        policy = self._db.match_refund_policy(
            product_name=order.get("product_name"),  # 产品名称
            plan_type=order.get("plan_type"),  # 套餐类型
            at_time=order.get("paid_at") or order.get("created_at"),  # 以支付时间（无则创建时间）作为规则判定时点
        )
        # 未匹配到政策时建议转人工
        if not policy:
            return {
                "eligible": False,  # 不具备退款资格
                "order_found": True,  # 订单已查到
                "message": "未找到匹配的退款规则，建议转人工进一步核实。",  # 提示信息
                "order_no": order_no,  # 订单号
            }

        # 是否存在重复扣费风险
        duplicate_risk = any(bool(item.get("is_duplicate_risk")) for item in payment_attempts)
        # 政策是否允许退款
        allow_refund = bool(policy.get("allow_refund"))
        # 政策是否要求人工审核
        manual_review_required = bool(policy.get("manual_review_required"))
        # 是否已有退款申请
        already_requested = existing_request is not None

        # 初始化不满足条件的原因代码列表
        reason_codes: List[str] = []
        # 政策不允许退款时记录原因
        if not allow_refund:
            reason_codes.append("policy_not_allow")  # 政策不允许
        # 需人工审核时记录原因
        if manual_review_required:
            reason_codes.append("manual_review_required")  # 需人工审核
        # 存在重复扣费风险时记录原因
        if duplicate_risk:
            reason_codes.append("duplicate_payment_risk")  # 重复支付风险
        # 已有退款申请时记录原因
        if already_requested:
            reason_codes.append("refund_request_exists")  # 已有退款申请

        # 第一版先用订单状态和支付事实做保守资格判断，不在此处引入复杂使用量计算。
        eligible = allow_refund and order.get("payment_status") == "paid" and not already_requested  # 基础资格：政策允许 + 已支付 + 无重复申请
        # 支付状态不是已支付时排除
        if order.get("payment_status") != "paid":
            eligible = False  # 判定为不具备资格
            reason_codes.append("payment_not_paid")  # 未支付原因
        # 订单已退款或已取消时排除
        if order.get("order_status") in {"refunded", "cancelled"}:
            eligible = False  # 判定为不具备资格
            reason_codes.append("order_status_not_supported")  # 订单状态不支持

        # 组装判断摘要
        summary_parts = [
            f"订单 {order_no} 当前支付状态为 {order.get('payment_status')}。",  # 支付状态摘要
            f"匹配到退款政策 {policy.get('policy_code')}。",  # 匹配政策摘要
        ]
        # 需人工审核时补充说明
        if manual_review_required:
            summary_parts.append("该订单退款需要人工审核。")
        # 重复扣费风险时补充说明
        if duplicate_risk:
            summary_parts.append("支付记录中存在重复扣费风险。")
        # 已有退款申请时补充说明
        if already_requested:
            summary_parts.append("该订单已经存在退款申请记录。")

        # 返回资格判断完整结果
        return {
            "eligible": eligible,  # 是否具备退款资格
            "order_found": True,  # 订单已查到
            "order_no": order_no,  # 订单号
            "product_name": order.get("product_name"),  # 产品名称
            "plan_type": order.get("plan_type"),  # 套餐类型
            "payment_status": order.get("payment_status"),  # 支付状态
            "order_status": order.get("order_status"),  # 订单状态
            "policy_code": policy.get("policy_code"),  # 匹配的政策编码
            "allow_refund": allow_refund,  # 政策是否允许退款
            "manual_review_required": manual_review_required,  # 是否需人工审核
            "already_requested": already_requested,  # 是否已有退款申请
            "existing_refund_no": existing_request.get("refund_no") if existing_request else None,  # 已有申请的退款单号
            "duplicate_risk": duplicate_risk,  # 重复扣费风险
            "reason_codes": reason_codes,  # 不满足条件的原因代码列表
            "summary": "".join(summary_parts),  # 合并后的判断摘要
        }

    async def create_refund_request(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建退款申请。

        这一步是真正的业务动作，不再只是解释规则。
        需提供订单号和退款原因；申请金额缺省取订单金额。

        参数：
            params: 工具调用参数（含 order_no、refund_reason、requested_amount）
            context: 调用上下文

        返回：
            创建结果字典（含退款单号与审核信息）
        """
        # 取参数中的订单号
        order_no = params.get("order_no")
        # 取参数中的退款原因
        refund_reason = params.get("refund_reason")
        # 取参数中的申请金额
        requested_amount = params.get("requested_amount")

        # 必填参数缺失时返回提示
        if not order_no or not refund_reason:
            return {
                "created": False,  # 未创建标记
                "message": "缺少必要参数，请至少提供 order_no 和 refund_reason。",  # 提示信息
                "query": {  # 回显查询条件
                    "order_no": order_no,  # 订单号
                    "refund_reason": refund_reason,  # 退款原因
                },
            }

        # 查询订单信息
        order = self._db.get_order(order_no)
        # 订单不存在时返回提示
        if not order:
            return {
                "created": False,  # 未创建标记
                "message": "未找到对应订单，无法创建退款申请。",  # 提示信息
                "query": {"order_no": order_no},  # 回显查询条件
            }

        # 申请金额缺省时取订单金额
        if requested_amount is None:
            requested_amount = float(order.get("amount") or 0.0)  # 订单金额转浮点
        else:
            requested_amount = float(requested_amount)  # 转成浮点

        # 按订单事实匹配退款政策
        policy = self._db.match_refund_policy(
            product_name=order.get("product_name"),  # 产品名称
            plan_type=order.get("plan_type"),  # 套餐类型
            at_time=order.get("paid_at") or order.get("created_at"),  # 规则判定时点
        )
        # 政策要求人工审核则审核标记为真（无政策时保守起见也需人工审核）
        review_required = bool(policy.get("manual_review_required")) if policy else True
        # 审核状态：需要审核为 pending，否则 none
        review_status = "pending" if review_required else "none"
        # 审核原因：有政策用模板，无政策给默认说明
        review_reason = policy.get("review_reason_template") if policy else "未匹配到自动退款规则，需人工审核。"

        # 生成当前 UTC 时间戳
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        # 生成退款单号（RFD-REQ-日期-随机后缀）
        refund_no = f"RFD-REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        # 写入退款申请记录
        refund_request = self._db.create_refund_request(
            order_no=order_no,  # 订单号
            refund_no=refund_no,  # 退款单号
            refund_reason=refund_reason,  # 退款原因
            requested_amount=requested_amount,  # 申请金额
            review_required=review_required,  # 是否需人工审核
            review_status=review_status,  # 审核状态
            review_reason=review_reason,  # 审核原因
            refund_channel=(policy or {}).get("refund_method", "manual"),  # 退款渠道（默认 manual）
            requested_at=now,  # 申请时间
        )

        # 返回创建结果
        return {
            "created": True,  # 创建成功标记
            "refund_no": refund_request.get("refund_no"),  # 退款单号
            "order_no": refund_request.get("order_no"),  # 订单号
            "requested_amount": refund_request.get("requested_amount"),  # 申请金额
            "refund_status": refund_request.get("refund_status"),  # 退款状态
            "review_required": bool(refund_request.get("review_required")),  # 是否需人工审核
            "review_status": refund_request.get("review_status"),  # 审核状态
            "review_reason": refund_request.get("review_reason"),  # 审核原因
            "requested_at": refund_request.get("requested_at"),  # 申请时间
        }

    async def incident_lookup(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查询已知故障。

        支持：
        1. category：按故障类别精确筛选
        2. keyword：按标题或摘要模糊匹配

        参数：
            params: 工具调用参数（含 category 或 keyword）
            context: 调用上下文

        返回：
            故障列表结果字典
        """
        # 取参数中的故障类别
        category = params.get("category")
        # 取参数中的搜索关键词
        keyword = params.get("keyword")
        # 按类别和关键词查询故障列表
        incidents = self._db.search_incidents(category=category, keyword=keyword)

        # 返回故障查询结果
        return {
            "matched": bool(incidents),  # 是否匹配到故障
            "count": len(incidents),  # 故障数量
            "incidents": [
                {
                    "incident_code": item.get("incident_code"),  # 故障编码
                    "title": item.get("title"),  # 故障标题
                    "category": item.get("category"),  # 故障类别
                    "severity": item.get("severity"),  # 严重程度
                    "status": item.get("status"),  # 状态
                    "affected_scope": item.get("affected_scope"),  # 影响范围
                    "summary": item.get("summary"),  # 故障摘要
                    "started_at": item.get("started_at"),  # 开始时间
                    "resolved_at": item.get("resolved_at"),  # 解决时间
                    "updated_at": item.get("updated_at"),  # 更新时间
                }
                for item in incidents
            ],
        }

    async def create_handoff_ticket(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建转人工工单。

        这是单 Agent 场景里的"安全退出路径"：
        当系统无法继续自动处理时，至少要留下一个可追踪的工单记录。

        参数：
            params: 工具调用参数（含 customer_code、conv_id、source_intent、reason、summary 等）
            context: 调用上下文

        返回：
            工单创建结果字典
        """
        # 取参数中的客户编码
        customer_code = params.get("customer_code")
        # 初始化客户 ID
        customer_id = None
        # 提供了客户编码时尝试解析客户 ID
        if customer_code:
            # 按编码查客户
            customer = self._db.get_customer_by_code(customer_code)
            # 客户存在时取客户 ID
            customer_id = customer.get("id") if customer else None

        # 生成当前 UTC 时间戳
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        # 生成工单号（TKT-日期-随机后缀）
        ticket_no = f"TKT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        # 写入转人工工单记录
        ticket = self._db.create_handoff_ticket(
            customer_id=customer_id,  # 客户 ID
            conv_id=params["conv_id"],  # 会话 ID
            source_intent=params["source_intent"],  # 来源意图
            priority=params.get("priority", "medium"),  # 优先级（默认 medium）
            reason=params["reason"],  # 转人工原因
            summary=params["summary"],  # 会话摘要
            assigned_queue=params.get("assigned_queue", "general_support"),  # 分配队列（默认 general_support）
            ticket_no=ticket_no,  # 工单号
            created_at=now,  # 创建时间
        )
        # 返回工单创建结果
        return {
            "created": True,  # 创建成功标记
            "ticket_no": ticket.get("ticket_no"),  # 工单号
            "status": ticket.get("status"),  # 工单状态
            "priority": ticket.get("priority"),  # 优先级
            "assigned_queue": ticket.get("assigned_queue"),  # 分配队列
            "created_at": ticket.get("created_at"),  # 创建时间
        }
