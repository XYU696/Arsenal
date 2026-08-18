PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    region TEXT NOT NULL DEFAULT 'CN',
    tier TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL DEFAULT 'active',
    preferred_channel TEXT NOT NULL DEFAULT 'app',
    risk_level TEXT NOT NULL DEFAULT 'low',
    vip_expire_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (tier IN ('standard', 'pro', 'vip', 'enterprise')),
    CHECK (status IN ('active', 'restricted', 'suspended')),
    CHECK (preferred_channel IN ('app', 'sms', 'email', 'wechat')),
    CHECK (risk_level IN ('low', 'medium', 'high'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    account_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    account_status TEXT NOT NULL DEFAULT 'active',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    last_login_at TEXT,
    locked_reason TEXT,
    security_review_status TEXT NOT NULL DEFAULT 'none',
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    unlock_available_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (role IN ('owner', 'admin', 'member', 'finance_viewer', 'support_viewer')),
    CHECK (account_status IN ('active', 'restricted', 'locked', 'suspended')),
    CHECK (security_review_status IN ('none', 'pending', 'approved', 'rejected'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_no TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    product_type TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    delivery_type TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'app',
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    payment_status TEXT NOT NULL,
    order_status TEXT NOT NULL,
    consignee_name TEXT,
    consignee_phone TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    detail_address TEXT,
    order_remark TEXT,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (product_type IN ('subscription', 'hardware', 'service')),
    CHECK (plan_type IN ('one_time', 'monthly', 'quarterly', 'yearly', 'enterprise')),
    CHECK (delivery_type IN ('digital', 'express', 'same_day', 'pickup')),
    CHECK (channel IN ('app', 'web', 'wechat', 'offline')),
    CHECK (payment_status IN ('paid', 'refunded', 'partial_refund', 'failed', 'pending')),
    CHECK (order_status IN ('created', 'paid', 'processing', 'shipped', 'delivered', 'completed', 'cancelled', 'refund_pending', 'refunded', 'after_sales'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    sku_id TEXT NOT NULL,
    sku_name TEXT NOT NULL,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    discount_amount REAL NOT NULL DEFAULT 0,
    final_amount REAL NOT NULL,
    item_status TEXT NOT NULL DEFAULT 'normal',
    fulfillment_status TEXT NOT NULL DEFAULT 'pending',
    returnable INTEGER NOT NULL DEFAULT 1,
    exchangeable INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CHECK (quantity > 0),
    CHECK (item_status IN ('normal', 'refund_pending', 'refunded', 'return_pending', 'returned', 'exchange_pending', 'exchanged')),
    CHECK (fulfillment_status IN ('pending', 'packed', 'shipped', 'delivered')),
    CHECK (returnable IN (0, 1)),
    CHECK (exchangeable IN (0, 1))
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    shipment_no TEXT NOT NULL UNIQUE,
    tracking_no TEXT NOT NULL UNIQUE,
    carrier TEXT NOT NULL,
    shipment_status TEXT NOT NULL,
    last_checkpoint TEXT NOT NULL,
    last_checkpoint_at TEXT NOT NULL,
    signed_at TEXT,
    receiver_name TEXT,
    exception_type TEXT NOT NULL DEFAULT 'none',
    exception_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CHECK (shipment_status IN ('pending', 'picked_up', 'in_transit', 'out_for_delivery', 'signed', 'failed_delivery', 'returned', 'lost')),
    CHECK (exception_type IN ('none', 'delay', 'lost', 'damaged', 'refused', 'address_issue'))
);

CREATE TABLE IF NOT EXISTS payment_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    attempt_no TEXT NOT NULL UNIQUE,
    payment_channel TEXT NOT NULL,
    attempt_status TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    transaction_id TEXT,
    failure_reason TEXT,
    is_duplicate_risk INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CHECK (payment_channel IN ('alipay', 'wechat_pay', 'bank_card', 'apple_pay')),
    CHECK (attempt_status IN ('initiated', 'success', 'failed', 'reversed', 'duplicate_suspected')),
    CHECK (is_duplicate_risk IN (0, 1))
);

CREATE TABLE IF NOT EXISTS refund_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    product_type TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    refund_window_hours INTEGER NOT NULL,
    usage_limit_percent REAL NOT NULL,
    allow_refund INTEGER NOT NULL DEFAULT 1,
    manual_review_required INTEGER NOT NULL DEFAULT 0,
    review_reason_template TEXT,
    refund_method TEXT NOT NULL DEFAULT 'original_route',
    policy_category TEXT NOT NULL,
    policy_text TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    CHECK (product_type IN ('subscription', 'hardware', 'service')),
    CHECK (plan_type IN ('one_time', 'monthly', 'quarterly', 'yearly', 'enterprise')),
    CHECK (allow_refund IN (0, 1)),
    CHECK (manual_review_required IN (0, 1)),
    CHECK (refund_method IN ('original_route', 'wallet', 'manual')),
    CHECK (policy_category IN ('subscription', 'physical_goods', 'enterprise_contract'))
);

CREATE TABLE IF NOT EXISTS refund_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_no TEXT NOT NULL UNIQUE,
    order_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    refund_reason TEXT NOT NULL,
    requested_amount REAL NOT NULL,
    approved_amount REAL,
    refund_status TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'none',
    review_reason TEXT,
    refund_channel TEXT NOT NULL DEFAULT 'original_route',
    requested_at TEXT NOT NULL,
    reviewed_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (refund_status IN ('created', 'pending_review', 'approved', 'rejected', 'processing', 'completed', 'cancelled')),
    CHECK (review_required IN (0, 1)),
    CHECK (review_status IN ('none', 'pending', 'approved', 'rejected')),
    CHECK (refund_channel IN ('original_route', 'wallet', 'manual'))
);

CREATE TABLE IF NOT EXISTS return_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_no TEXT NOT NULL UNIQUE,
    order_item_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    return_reason TEXT NOT NULL,
    return_status TEXT NOT NULL,
    pickup_required INTEGER NOT NULL DEFAULT 0,
    warehouse_received_at TEXT,
    refund_linked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_item_id) REFERENCES order_items(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (return_status IN ('created', 'pending_review', 'approved', 'rejected', 'in_transit', 'warehouse_received', 'completed', 'cancelled')),
    CHECK (pickup_required IN (0, 1)),
    CHECK (refund_linked IN (0, 1))
);

CREATE TABLE IF NOT EXISTS exchange_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_no TEXT NOT NULL UNIQUE,
    order_item_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    exchange_reason TEXT NOT NULL,
    exchange_status TEXT NOT NULL,
    new_shipment_no TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_item_id) REFERENCES order_items(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (exchange_status IN ('created', 'pending_review', 'approved', 'rejected', 'preparing', 'shipped', 'completed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS coupon_compensations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_id INTEGER,
    compensation_no TEXT NOT NULL UNIQUE,
    coupon_code TEXT NOT NULL UNIQUE,
    compensation_type TEXT NOT NULL,
    coupon_value REAL NOT NULL,
    issue_reason TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    status TEXT NOT NULL,
    expire_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    CHECK (compensation_type IN ('coupon', 'cashback', 'shipping_credit')),
    CHECK (status IN ('issued', 'used', 'expired', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    affected_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    started_at TEXT NOT NULL,
    resolved_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK (category IN ('login', 'billing', 'knowledge_base', 'api', 'frontend', 'general', 'logistics')),
    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CHECK (status IN ('investigating', 'identified', 'monitoring', 'resolved'))
);

CREATE TABLE IF NOT EXISTS warehouse_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_no TEXT NOT NULL UNIQUE,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    affected_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    started_at TEXT NOT NULL,
    resolved_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK (incident_type IN ('sorting_delay', 'mis_pick', 'capacity_overload', 'inventory_mismatch', 'damaged_package')),
    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CHECK (status IN ('investigating', 'identified', 'monitoring', 'resolved'))
);

CREATE TABLE IF NOT EXISTS handoff_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no TEXT NOT NULL UNIQUE,
    customer_id INTEGER,
    conv_id TEXT NOT NULL,
    source_intent TEXT NOT NULL,
    source_ref_type TEXT,
    source_ref_no TEXT,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    assigned_queue TEXT NOT NULL,
    sla_level TEXT NOT NULL DEFAULT 'standard',
    owner_agent TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    CHECK (status IN ('open', 'triaged', 'in_progress', 'resolved', 'closed')),
    CHECK (assigned_queue IN ('general_support', 'billing_ops', 'technical_support', 'account_ops', 'vip_desk', 'logistics_ops', 'after_sales_ops')),
    CHECK (sla_level IN ('standard', 'urgent', 'vip'))
);

CREATE TABLE IF NOT EXISTS after_sales_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no TEXT NOT NULL UNIQUE,
    ticket_type TEXT NOT NULL,
    order_id INTEGER,
    customer_id INTEGER NOT NULL,
    source_channel TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    assigned_queue TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (ticket_type IN ('refund', 'return', 'exchange', 'logistics', 'compensation', 'account')),
    CHECK (source_channel IN ('app', 'web', 'wechat', 'agent')),
    CHECK (status IN ('open', 'triaged', 'in_progress', 'resolved', 'closed')),
    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    CHECK (assigned_queue IN ('general_support', 'billing_ops', 'technical_support', 'account_ops', 'vip_desk', 'logistics_ops', 'after_sales_ops'))
);

CREATE TABLE IF NOT EXISTS agent_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    session_id TEXT,
    agent_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT,
    target_no TEXT,
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    result_status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    CHECK (result_status IN ('success', 'failed', 'blocked'))
);

CREATE INDEX IF NOT EXISTS idx_accounts_customer_id_v2 ON accounts(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id_v2 ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_no_v2 ON orders(order_no);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id_v2 ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_shipments_order_id_v2 ON shipments(order_id);
CREATE INDEX IF NOT EXISTS idx_refund_requests_order_id_v2 ON refund_requests(order_id);
CREATE INDEX IF NOT EXISTS idx_return_requests_item_id_v2 ON return_requests(order_item_id);
CREATE INDEX IF NOT EXISTS idx_exchange_requests_item_id_v2 ON exchange_requests(order_item_id);
CREATE INDEX IF NOT EXISTS idx_coupon_comp_customer_id_v2 ON coupon_compensations(customer_id);
CREATE INDEX IF NOT EXISTS idx_refund_policies_lookup_v2 ON refund_policies(product_name, plan_type, effective_from);
CREATE INDEX IF NOT EXISTS idx_incidents_category_status_v2 ON incidents(category, status);
CREATE INDEX IF NOT EXISTS idx_warehouse_incident_type_status_v2 ON warehouse_incidents(incident_type, status);
CREATE INDEX IF NOT EXISTS idx_handoff_tickets_customer_id_v2 ON handoff_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_handoff_tickets_conv_id_v2 ON handoff_tickets(conv_id);
CREATE INDEX IF NOT EXISTS idx_after_sales_tickets_customer_id_v2 ON after_sales_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_conv_id_v2 ON agent_audit_logs(conv_id);
