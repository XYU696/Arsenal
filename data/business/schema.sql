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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (tier IN ('standard', 'pro', 'vip', 'enterprise')),
    CHECK (status IN ('active', 'restricted', 'suspended'))
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
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (role IN ('owner', 'admin', 'member', 'finance_viewer', 'support_viewer')),
    CHECK (account_status IN ('active', 'restricted', 'locked', 'suspended'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_no TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    payment_status TEXT NOT NULL,
    order_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    CHECK (plan_type IN ('monthly', 'quarterly', 'yearly', 'enterprise')),
    CHECK (payment_status IN ('paid', 'refunded', 'partial_refund', 'failed', 'pending')),
    CHECK (order_status IN ('created', 'paid', 'fulfilled', 'cancelled', 'refunding', 'refunded'))
);

CREATE TABLE IF NOT EXISTS refund_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    refund_window_hours INTEGER NOT NULL,
    usage_limit_percent REAL NOT NULL,
    allow_refund INTEGER NOT NULL DEFAULT 1,
    manual_review_required INTEGER NOT NULL DEFAULT 0,
    policy_text TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    CHECK (plan_type IN ('monthly', 'quarterly', 'yearly', 'enterprise')),
    CHECK (allow_refund IN (0, 1)),
    CHECK (manual_review_required IN (0, 1))
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
    CHECK (category IN ('login', 'billing', 'knowledge_base', 'api', 'frontend', 'general')),
    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CHECK (status IN ('investigating', 'identified', 'monitoring', 'resolved'))
);

CREATE TABLE IF NOT EXISTS handoff_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no TEXT NOT NULL UNIQUE,
    customer_id INTEGER,
    conv_id TEXT NOT NULL,
    source_intent TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    assigned_queue TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    CHECK (status IN ('open', 'triaged', 'in_progress', 'resolved', 'closed')),
    CHECK (assigned_queue IN ('general_support', 'billing_ops', 'technical_support', 'account_ops', 'vip_desk'))
);

CREATE INDEX IF NOT EXISTS idx_accounts_customer_id ON accounts(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_no ON orders(order_no);
CREATE INDEX IF NOT EXISTS idx_refund_policies_lookup ON refund_policies(product_name, plan_type, effective_from);
CREATE INDEX IF NOT EXISTS idx_incidents_category_status ON incidents(category, status);
CREATE INDEX IF NOT EXISTS idx_handoff_tickets_customer_id ON handoff_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_handoff_tickets_conv_id ON handoff_tickets(conv_id);
