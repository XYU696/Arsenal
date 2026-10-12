import json  # 解析 permissions_json 字段
import sqlite3  # SQLite 数据库连接
from pathlib import Path  # 路径对象
from typing import Any, Dict, List, Optional  # 类型注解


# 业务数据库默认路径（项目根 /data/business/echomind.db）
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "business" / "echomind.db"


class BusinessDB:
    """电商售后业务数据库访问层（封装全部工具 handler 需要的 SQL 查询）。"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据库访问对象。

        参数：
            db_path: 数据库文件路径（None 时用默认路径）
        """
        # 有显式路径用显式路径，否则用模块默认路径
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    def connect(self) -> sqlite3.Connection:
        """
        建立一条新的数据库连接（Row 工厂，可直接按列名取字段）。

        返回：
            已配置行工厂的 sqlite3.Connection
        """
        # 建立连接
        conn = sqlite3.connect(self.db_path)
        # 启用按列名访问（conn["customer_code"] 形式）
        conn.row_factory = sqlite3.Row
        # 返回连接
        return conn

    def get_customer_by_code(self, customer_code: str) -> Optional[Dict[str, Any]]:
        """
        按客户编码查询客户。

        参数：
            customer_code: 客户编码

        返回：
            客户记录字典或 None（不存在时）
        """
        # 按客户编码精确查询
        sql = "SELECT * FROM customers WHERE customer_code = ?"
        # 执行并返回单行
        return self._fetch_one(sql, [customer_code])

    def get_account_status(self, account_id: Optional[str] = None, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        按账户 ID 或邮箱查询账户状态（含权限 JSON 解析）。

        参数：
            account_id: 账户 ID（二选一）
            email: 客户邮箱（二选一）

        返回：
            账户记录字典（附带解析后的 permissions 列表）或 None
        """
        # 优先按账户 ID 查询
        if account_id:
            # 联表带出客户信息
            sql = """
            SELECT a.*, c.customer_code, c.name, c.email, c.tier, c.status AS customer_status
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE a.account_id = ?
            """
            # 执行查询
            row = self._fetch_one(sql, [account_id])
        # 其次按邮箱查询
        elif email:
            # 联表带出客户信息（只取最早一条）
            sql = """
            SELECT a.*, c.customer_code, c.name, c.email, c.tier, c.status AS customer_status
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE c.email = ?
            ORDER BY a.id ASC
            LIMIT 1
            """
            # 执行查询
            row = self._fetch_one(sql, [email])
        # 两个参数都没有时直接返回空
        else:
            return None

        # permissions_json 是字符串时解析成列表
        if row and isinstance(row.get("permissions_json"), str):
            # 反序列化权限 JSON
            row["permissions"] = json.loads(row["permissions_json"])
        # 返回账户记录（含解析后的权限）
        return row

    def get_order(self, order_no: str) -> Optional[Dict[str, Any]]:
        """
        按订单号查询订单（带客户信息）。

        参数：
            order_no: 订单号

        返回：
            订单记录字典或 None
        """
        # 联表带出客户等级等信息
        sql = """
        SELECT o.*, c.customer_code, c.name, c.email, c.tier
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE o.order_no = ?
        """
        # 执行并返回单行
        return self._fetch_one(sql, [order_no])

    def get_order_items(self, order_no: str) -> List[Dict[str, Any]]:
        """
        根据订单号查询订单商品项。

        这里同时带出订单头信息，方便上层 handler 直接组织返回，
        避免后续再做一次额外查询。

        参数：
            order_no: 订单号

        返回：
            商品项记录列表
        """
        # 联表带出订单头字段（支付状态、套餐等）
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
        # 执行并返回全部行
        return self._fetch_all(sql, [order_no])

    def get_shipment(self, order_no: Optional[str] = None, tracking_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        按订单号或物流单号查询物流信息。

        两种查询方式都很常见：
        1. 用户提供 order_no
        2. 用户直接贴 tracking_no

        参数：
            order_no: 订单号（二选一）
            tracking_no: 物流单号（二选一）

        返回：
            最新一条物流记录字典或 None
        """
        # 优先按订单号查
        if order_no:
            # 联表带出订单与客户信息
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
            # 取最早一条记录
            return self._fetch_one(sql, [order_no])

        # 其次按物流单号查
        if tracking_no:
            # 联表带出订单与客户信息
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
            # 取最早一条记录
            return self._fetch_one(sql, [tracking_no])
        # 两个参数都没有时返回空
        return None

    def get_payment_attempts(self, order_no: str) -> List[Dict[str, Any]]:
        """
        查询某笔订单的所有支付尝试。

        用于重复扣费、支付失败、支付逆转等场景。

        参数：
            order_no: 订单号

        返回：
            支付尝试记录列表（按创建时间正序）
        """
        # 联表带出订单支付状态
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
        # 执行并返回全部行
        return self._fetch_all(sql, [order_no])

    def list_orders_for_customer(self, customer_code: str) -> List[Dict[str, Any]]:
        """
        列出某客户的全部订单（最新在前）。

        参数：
            customer_code: 客户编码

        返回：
            订单记录列表
        """
        # 按客户编码联表查询并按创建时间倒序
        sql = """
        SELECT o.*
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE c.customer_code = ?
        ORDER BY o.created_at DESC
        """
        # 执行并返回全部行
        return self._fetch_all(sql, [customer_code])

    def match_refund_policy(self, product_name: str, plan_type: str, at_time: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        按商品 + 套餐 + 时间点匹配生效中的退款政策。

        参数：
            product_name: 商品名
            plan_type: 套餐类型
            at_time: 生效判定时间点（None 表示"当前"即远期）

        返回：
            命中的政策记录字典或 None
        """
        # 未指定时间时用远期时间（9999 年）让所有政策都生效
        effective_time = at_time or "9999-12-31T23:59:59Z"
        # 精确匹配商品/套餐，并校验生效时间区间（取最近生效的一条）
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
        # 执行并返回命中的政策
        return self._fetch_one(sql, [product_name, plan_type, effective_time, effective_time])

    def get_refund_policy_by_code(self, policy_code: str) -> Optional[Dict[str, Any]]:
        """按政策编码精确查询退款政策（用于知识库一致性校验）。"""
        # 按政策编码精确查询
        sql = "SELECT * FROM refund_policies WHERE policy_code = ?"
        # 执行并返回单行
        return self._fetch_one(sql, [policy_code])

    def get_incident_by_code(self, incident_code: str) -> Optional[Dict[str, Any]]:
        """按故障编码精确查询故障公告（用于知识库一致性校验）。"""
        # 按故障编码精确查询
        sql = "SELECT * FROM incidents WHERE incident_code = ?"
        # 执行并返回单行
        return self._fetch_one(sql, [incident_code])

    def get_refund_request(
        self,
        refund_no: Optional[str] = None,  # 退款单号（二选一）
        order_no: Optional[str] = None,  # 订单号（二选一）
    ) -> Optional[Dict[str, Any]]:
        """
        查询退款申请。

        支持两种方式：
        1. 直接按 refund_no 精确查
        2. 按 order_no 查最近一条退款申请

        参数：
            refund_no: 退款单号（二选一）
            order_no: 订单号（二选一）

        返回：
            退款申请记录字典或 None
        """
        # 优先按退款单号精确查询
        if refund_no:
            # 联表带出订单与客户信息
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
            # 执行查询
            return self._fetch_one(sql, [refund_no])

        # 其次按订单号取最近一条
        if order_no:
            # 联表带出订单与客户信息
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
            # 执行查询
            return self._fetch_one(sql, [order_no])
        # 两个参数都没有时返回空
        return None

    def create_refund_request(
        self,
        order_no: str,  # 关联订单号
        refund_no: str,  # 退款单号
        refund_reason: str,  # 退款原因
        requested_amount: float,  # 申请退款金额
        review_required: bool,  # 是否需要人工审核
        review_status: str,  # 审核状态
        review_reason: Optional[str],  # 审核拒绝原因（可能为 None）
        refund_channel: str,  # 退款渠道
        requested_at: str,  # 申请时间
    ) -> Dict[str, Any]:
        """
        为指定订单创建退款申请。

        这里先保持实现简单：
        - 自动从订单找到 order_id / customer_id
        - 自动推导 refund_status
        - 创建后直接回查整条记录

        参数：
            order_no: 关联订单号
            refund_no: 退款单号
            refund_reason: 退款原因
            requested_amount: 申请退款金额
            review_required: 是否需要人工审核
            review_status: 审核状态
            review_reason: 审核拒绝原因（可能为 None）
            refund_channel: 退款渠道
            requested_at: 申请时间

        返回：
            创建后的退款申请记录字典（回查）
        """
        # 先取关联订单（验证存在并取外键）
        order = self.get_order(order_no)
        # 订单不存在则抛错
        if not order:
            raise ValueError(f"未找到订单: {order_no}")

        # 需要人工审核时状态置 pending_review，否则直接 created
        refund_status = "pending_review" if review_required else "created"
        # 插入退款申请（审核/完成时间为空）
        sql = """
        INSERT INTO refund_requests (
            refund_no, order_id, customer_id, refund_reason, requested_amount,
            approved_amount, refund_status, review_required, review_status,
            review_reason, refund_channel, requested_at, reviewed_at,
            completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """
        # 组装绑定参数（order_id/customer_id 取自订单外键）
        params = [
            refund_no,  # 退款单号
            order["id"],  # 订单主键
            order["customer_id"],  # 客户主键
            refund_reason,  # 退款原因
            requested_amount,  # 申请金额
            refund_status,  # 推导出的退款状态
            1 if review_required else 0,  # 布尔转 0/1
            review_status,  # 审核状态
            review_reason,  # 审核原因
            refund_channel,  # 退款渠道
            requested_at,  # 申请时间（reviewed_at 之前列）
            requested_at,  # 创建时间
            requested_at,  # 更新时间
        ]
        # 事务内执行插入并提交
        with self.connect() as conn:
            # 执行插入语句
            conn.execute(sql, params)
            # 提交事务
            conn.commit()
        # 回查并返回整条记录
        return self.get_refund_request(refund_no=refund_no) or {}

    def search_incidents(self, category: Optional[str] = None, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        按分类 / 关键词搜索故障公告，结果按状态优先级和更新时间排序。

        参数：
            category: 故障分类（可选）
            keyword: 标题/摘要关键词（可选）

        返回：
            故障公告记录列表
        """
        # 动态 WHERE 子句片段
        clauses = []
        # 绑定参数列表
        params: List[Any] = []
        # 有分类时加分类条件
        if category:
            # 追加分类条件段
            clauses.append("category = ?")
            # 追加对应参数
            params.append(category)
        # 有关键词时加标题/摘要模糊匹配
        if keyword:
            # 追加模糊匹配条件段（标题或摘要命中）
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            # 构造差值通配
            needle = f"%{keyword}%"
            # 追加标题和摘要两个参数
            params.extend([needle, needle])
        # 有条件时拼 WHERE（无则空串）
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # 按状态排序（investigating 最优先）+ 更新时间倒序
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
        # 执行并返回全部行
        return self._fetch_all(sql, params)

    def create_handoff_ticket(
        self,
        customer_id: Optional[int],  # 客户主键（可能为 None）
        conv_id: str,  # 会话 ID
        source_intent: str,  # 触发转人工的意图
        priority: str,  # 工单优先级
        reason: str,  # 转人工原因
        summary: str,  # 对话摘要
        assigned_queue: str,  # 分配队列
        ticket_no: str,  # 工单号
        created_at: str,  # 创建时间
    ) -> Dict[str, Any]:
        """
        创建人工服务工单（转人工落地）。

        参数：
            customer_id: 客户主键（可能为 None）
            conv_id: 会话 ID
            source_intent: 触发转人工的意图
            priority: 工单优先级
            reason: 转人工原因
            summary: 对话摘要
            assigned_queue: 分配队列
            ticket_no: 工单号
            created_at: 创建时间

        返回：
            创建后的工单记录字典（回查）
        """
        # 插入工单（状态固定 open，更新时间为创建时间）
        sql = """
        INSERT INTO handoff_tickets (
            ticket_no, customer_id, conv_id, source_intent, priority, status,
            reason, summary, assigned_queue, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
        """
        # 组装绑定参数
        params = [
            ticket_no, customer_id, conv_id, source_intent, priority,  # 工单标识与会话信息
            reason, summary, assigned_queue, created_at, created_at,  # 内容与时间戳
        ]
        # 事务内执行插入并提交
        with self.connect() as conn:
            # 执行插入语句
            conn.execute(sql, params)
            # 提交事务
            conn.commit()
        # 回查并返回整条记录
        return self.get_handoff_ticket(ticket_no) or {}

    def get_handoff_ticket(self, ticket_no: str) -> Optional[Dict[str, Any]]:
        """
        按工单号查询工单。

        参数：
            ticket_no: 工单号

        返回：
            工单记录字典或 None
        """
        # 按工单号精确查询
        sql = "SELECT * FROM handoff_tickets WHERE ticket_no = ?"
        # 执行并返回单行
        return self._fetch_one(sql, [ticket_no])

    def _fetch_one(self, sql: str, params: List[Any]) -> Optional[Dict[str, Any]]:
        """
        执行查询并返回单行字典（无结果返回 None）。

        参数：
            sql: 查询语句
            params: 绑定参数列表

        返回：
            记录字典或 None
        """
        # 连接内执行查询
        with self.connect() as conn:
            # 绑定参数并取第一行
            row = conn.execute(sql, params).fetchone()
        # 有结果时转成字典返回
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        """
        执行查询并返回全部行（字典列表）。

        参数：
            sql: 查询语句
            params: 绑定参数列表

        返回：
            记录字典列表
        """
        # 连接内执行查询
        with self.connect() as conn:
            # 绑定参数并取全部行
            rows = conn.execute(sql, params).fetchall()
        # 把每一行转成字典后返回
        return [dict(row) for row in rows]