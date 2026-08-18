DELETE FROM handoff_tickets;
DELETE FROM incidents;
DELETE FROM refund_policies;
DELETE FROM orders;
DELETE FROM accounts;
DELETE FROM customers;

INSERT INTO customers (id, customer_code, name, email, phone, region, tier, status, created_at, updated_at) VALUES
(1, 'CUST-1001', 'Li Wei', 'li.wei@example.com', '13800000001', 'CN', 'standard', 'active', '2026-06-10T09:00:00Z', '2026-07-28T10:30:00Z'),
(2, 'CUST-1002', 'Chen Yu', 'chen.yu@example.com', '13800000002', 'CN', 'pro', 'active', '2026-05-18T08:20:00Z', '2026-07-28T11:15:00Z'),
(3, 'CUST-1003', 'Wang Na', 'wang.na@example.com', '13800000003', 'CN', 'vip', 'restricted', '2026-03-08T12:00:00Z', '2026-07-27T16:40:00Z'),
(4, 'CUST-1004', 'Zhao Ming', 'zhao.ming@example.com', '13800000004', 'CN', 'enterprise', 'active', '2026-02-14T15:45:00Z', '2026-07-28T09:50:00Z'),
(5, 'CUST-1005', 'Sun Lin', 'sun.lin@example.com', '13800000005', 'CN', 'standard', 'suspended', '2026-01-22T10:10:00Z', '2026-07-26T13:05:00Z'),
(6, 'CUST-1006', 'Gao Qi', 'gao.qi@example.com', '13800000006', 'CN', 'pro', 'active', '2026-07-01T14:20:00Z', '2026-07-29T02:10:00Z');

INSERT INTO accounts (id, customer_id, account_id, role, account_status, permissions_json, last_login_at, locked_reason, updated_at) VALUES
(1, 1, 'ACC-LW-01', 'member', 'active', '["chat_basic","kb_view"]', '2026-07-28T08:00:00Z', NULL, '2026-07-28T08:00:00Z'),
(2, 2, 'ACC-CY-01', 'admin', 'active', '["chat_basic","kb_view","kb_manage","team_manage"]', '2026-07-28T10:55:00Z', NULL, '2026-07-28T10:55:00Z'),
(3, 3, 'ACC-WN-01', 'owner', 'restricted', '["chat_basic","billing_view"]', '2026-07-27T15:12:00Z', 'Risk review pending after repeated billing disputes', '2026-07-27T16:40:00Z'),
(4, 4, 'ACC-ZM-01', 'admin', 'active', '["chat_basic","kb_view","kb_manage","team_manage","billing_view"]', '2026-07-28T09:30:00Z', NULL, '2026-07-28T09:50:00Z'),
(5, 4, 'ACC-ZM-02', 'member', 'active', '["chat_basic","kb_view"]', '2026-07-28T07:20:00Z', NULL, '2026-07-28T07:20:00Z'),
(6, 5, 'ACC-SL-01', 'member', 'suspended', '["chat_basic"]', '2026-07-24T12:44:00Z', 'Account suspended after security review', '2026-07-26T13:05:00Z'),
(7, 6, 'ACC-GQ-01', 'finance_viewer', 'locked', '["billing_view"]', '2026-07-28T21:20:00Z', 'Multiple failed login attempts triggered temporary lock', '2026-07-29T02:10:00Z');

INSERT INTO orders (id, customer_id, order_no, product_name, plan_type, amount, currency, payment_status, order_status, created_at, paid_at) VALUES
(1, 1, 'ORD-20260728-1001', 'EchoMind Pro', 'yearly', 1999.00, 'CNY', 'paid', 'paid', '2026-07-28T09:12:00Z', '2026-07-28T09:12:30Z'),
(2, 1, 'ORD-20260728-1002', 'EchoMind Pro', 'yearly', 1999.00, 'CNY', 'paid', 'paid', '2026-07-28T09:13:10Z', '2026-07-28T09:13:42Z'),
(3, 2, 'ORD-20260715-2001', 'EchoMind Team', 'monthly', 299.00, 'CNY', 'paid', 'fulfilled', '2026-07-15T11:00:00Z', '2026-07-15T11:00:15Z'),
(4, 2, 'ORD-20260701-2002', 'EchoMind Team', 'monthly', 299.00, 'CNY', 'partial_refund', 'refunding', '2026-07-01T08:20:00Z', '2026-07-01T08:20:20Z'),
(5, 3, 'ORD-20260620-3001', 'EchoMind Pro', 'yearly', 1999.00, 'CNY', 'paid', 'paid', '2026-06-20T13:45:00Z', '2026-06-20T13:45:30Z'),
(6, 4, 'ORD-20260710-4001', 'EchoMind Enterprise', 'enterprise', 12888.00, 'CNY', 'paid', 'fulfilled', '2026-07-10T10:00:00Z', '2026-07-10T10:00:45Z'),
(7, 5, 'ORD-20260705-5001', 'EchoMind Pro', 'monthly', 199.00, 'CNY', 'failed', 'cancelled', '2026-07-05T07:30:00Z', NULL),
(8, 6, 'ORD-20260729-6001', 'EchoMind Team', 'quarterly', 799.00, 'CNY', 'paid', 'paid', '2026-07-29T01:20:00Z', '2026-07-29T01:20:30Z');

INSERT INTO refund_policies (id, policy_code, product_name, plan_type, refund_window_hours, usage_limit_percent, allow_refund, manual_review_required, policy_text, effective_from, effective_to) VALUES
(1, 'RFD-PRO-YEARLY-2026', 'EchoMind Pro', 'yearly', 72, 10.0, 1, 1, 'Yearly Pro plans can request refund within 72 hours if usage is below 10%.', '2026-01-01T00:00:00Z', NULL),
(2, 'RFD-PRO-MONTHLY-2026', 'EchoMind Pro', 'monthly', 24, 5.0, 1, 0, 'Monthly Pro plans can request refund within 24 hours if usage is below 5%.', '2026-01-01T00:00:00Z', NULL),
(3, 'RFD-TEAM-MONTHLY-2026', 'EchoMind Team', 'monthly', 48, 15.0, 1, 0, 'Monthly Team plans can request refund within 48 hours if usage is below 15%.', '2026-01-01T00:00:00Z', NULL),
(4, 'RFD-TEAM-QUARTERLY-2026', 'EchoMind Team', 'quarterly', 48, 10.0, 1, 1, 'Quarterly Team plans require manual review even within the standard refund window.', '2026-01-01T00:00:00Z', NULL),
(5, 'RFD-ENT-2026', 'EchoMind Enterprise', 'enterprise', 0, 0.0, 0, 1, 'Enterprise contracts do not support automatic refunds and always require account review.', '2026-01-01T00:00:00Z', NULL);

INSERT INTO incidents (id, incident_code, title, category, severity, status, affected_scope, summary, started_at, resolved_at, updated_at) VALUES
(1, 'INC-LOGIN-20260729', 'SMS verification delays for CN mobile carriers', 'login', 'high', 'identified', 'CN mobile users using SMS OTP', 'Some users may not receive SMS verification codes in time. Email login and retry after 5 minutes are recommended.', '2026-07-29T00:30:00Z', NULL, '2026-07-29T03:10:00Z'),
(2, 'INC-KB-20260727', 'Knowledge base upload processing backlog', 'knowledge_base', 'medium', 'monitoring', 'Newly uploaded PDF documents', 'Recent PDF uploads may take longer than usual to become searchable while the indexing queue drains.', '2026-07-27T09:00:00Z', NULL, '2026-07-28T18:20:00Z'),
(3, 'INC-API-20260720', 'API 500 errors on import endpoint', 'api', 'critical', 'resolved', 'Document import API only', 'A deployment issue caused intermittent 500 errors on the import endpoint. The incident has been resolved.', '2026-07-20T08:10:00Z', '2026-07-20T10:35:00Z', '2026-07-20T10:35:00Z'),
(4, 'INC-BILLING-20260718', 'Duplicate billing retries under investigation', 'billing', 'medium', 'investigating', 'A small subset of Pro yearly renewals', 'Some yearly renewals may show duplicate payment attempts. Billing ops is validating affected orders.', '2026-07-18T06:50:00Z', NULL, '2026-07-28T17:40:00Z');

INSERT INTO handoff_tickets (id, ticket_no, customer_id, conv_id, source_intent, priority, status, reason, summary, assigned_queue, created_at, updated_at) VALUES
(1, 'TKT-20260727-0001', 3, 'conv_seed_wn_01', 'billing', 'high', 'triaged', 'Repeated refund complaint from restricted account', 'VIP customer reported unresolved refund issue after previous contact and requests manual billing review.', 'billing_ops', '2026-07-27T16:45:00Z', '2026-07-27T17:05:00Z'),
(2, 'TKT-20260728-0002', 4, 'conv_seed_zm_02', 'technical', 'medium', 'in_progress', 'Knowledge base management page unavailable to team member', 'Enterprise admin reported that a member account cannot access knowledge base management despite expected permissions.', 'account_ops', '2026-07-28T09:55:00Z', '2026-07-28T10:15:00Z');
