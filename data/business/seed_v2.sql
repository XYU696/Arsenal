DELETE FROM agent_audit_logs;
DELETE FROM after_sales_tickets;
DELETE FROM handoff_tickets;
DELETE FROM warehouse_incidents;
DELETE FROM incidents;
DELETE FROM coupon_compensations;
DELETE FROM exchange_requests;
DELETE FROM return_requests;
DELETE FROM refund_requests;
DELETE FROM refund_policies;
DELETE FROM payment_attempts;
DELETE FROM shipments;
DELETE FROM order_items;
DELETE FROM orders;
DELETE FROM accounts;
DELETE FROM customers;

INSERT INTO customers (id, customer_code, name, email, phone, region, tier, status, preferred_channel, risk_level, vip_expire_at, created_at, updated_at) VALUES
(1, 'CUST-2001', 'Li Wei', 'li.wei@example.com', '13800010001', 'CN', 'standard', 'active', 'app', 'low', NULL, '2026-03-15T09:00:00Z', '2026-07-30T10:20:00Z'),
(2, 'CUST-2002', 'Chen Yu', 'chen.yu@example.com', '13800010002', 'CN', 'pro', 'active', 'wechat', 'low', NULL, '2026-04-08T08:30:00Z', '2026-07-29T14:10:00Z'),
(3, 'CUST-2003', 'Wang Na', 'wang.na@example.com', '13800010003', 'CN', 'vip', 'restricted', 'app', 'high', '2026-12-31T23:59:59Z', '2026-01-18T10:00:00Z', '2026-07-28T17:40:00Z'),
(4, 'CUST-2004', 'Zhao Ming', 'zhao.ming@example.com', '13800010004', 'CN', 'enterprise', 'active', 'email', 'medium', NULL, '2025-12-20T15:30:00Z', '2026-07-30T08:45:00Z'),
(5, 'CUST-2005', 'Sun Lin', 'sun.lin@example.com', '13800010005', 'CN', 'standard', 'suspended', 'app', 'medium', NULL, '2026-02-12T11:15:00Z', '2026-07-26T13:05:00Z'),
(6, 'CUST-2006', 'Gao Qi', 'gao.qi@example.com', '13800010006', 'CN', 'pro', 'active', 'app', 'low', NULL, '2026-07-01T14:20:00Z', '2026-07-30T09:30:00Z'),
(7, 'CUST-2007', 'Liu Fang', 'liu.fang@example.com', '13800010007', 'CN', 'vip', 'active', 'wechat', 'low', '2027-03-31T23:59:59Z', '2026-05-06T12:40:00Z', '2026-07-30T16:20:00Z'),
(8, 'CUST-2008', 'He Tao', 'he.tao@example.com', '13800010008', 'CN', 'standard', 'active', 'sms', 'medium', NULL, '2026-06-28T13:10:00Z', '2026-07-30T18:10:00Z');

INSERT INTO accounts (id, customer_id, account_id, role, account_status, permissions_json, last_login_at, locked_reason, security_review_status, failed_login_count, unlock_available_at, updated_at) VALUES
(1, 1, 'ACC-LW-11', 'member', 'active', '["chat_basic","order_view"]', '2026-07-30T08:15:00Z', NULL, 'none', 0, NULL, '2026-07-30T08:15:00Z'),
(2, 2, 'ACC-CY-21', 'admin', 'active', '["chat_basic","order_view","after_sales_manage"]', '2026-07-29T21:10:00Z', NULL, 'none', 0, NULL, '2026-07-29T21:10:00Z'),
(3, 3, 'ACC-WN-31', 'owner', 'restricted', '["chat_basic","billing_view"]', '2026-07-28T17:00:00Z', 'Risk review pending after repeated refund disputes', 'pending', 1, NULL, '2026-07-28T17:40:00Z'),
(4, 4, 'ACC-ZM-41', 'admin', 'active', '["chat_basic","order_view","logistics_view","team_manage"]', '2026-07-30T06:55:00Z', NULL, 'none', 0, NULL, '2026-07-30T06:55:00Z'),
(5, 5, 'ACC-SL-51', 'member', 'suspended', '["chat_basic"]', '2026-07-24T12:44:00Z', 'Account suspended after security review', 'rejected', 2, NULL, '2026-07-26T13:05:00Z'),
(6, 6, 'ACC-GQ-61', 'finance_viewer', 'locked', '["billing_view","order_view"]', '2026-07-30T21:20:00Z', 'Multiple failed login attempts triggered temporary lock', 'none', 5, '2026-07-31T03:00:00Z', '2026-07-30T21:20:00Z'),
(7, 7, 'ACC-LF-71', 'member', 'active', '["chat_basic","vip_service"]', '2026-07-30T16:00:00Z', NULL, 'none', 0, NULL, '2026-07-30T16:00:00Z'),
(8, 8, 'ACC-HT-81', 'member', 'active', '["chat_basic","order_view"]', '2026-07-30T18:00:00Z', NULL, 'none', 1, NULL, '2026-07-30T18:00:00Z');

INSERT INTO orders (id, customer_id, order_no, product_name, product_type, plan_type, delivery_type, channel, amount, currency, payment_status, order_status, consignee_name, consignee_phone, province, city, district, detail_address, order_remark, created_at, paid_at, updated_at) VALUES
(1, 1, 'ORD-20260730-1001', 'EchoMind 智能录音笔 A1', 'hardware', 'one_time', 'express', 'app', 899.00, 'CNY', 'paid', 'shipped', 'Li Wei', '13800010001', '上海', '上海', '浦东新区', '张江路 88 号', '尽快发货', '2026-07-30T09:12:00Z', '2026-07-30T09:12:20Z', '2026-07-30T11:00:00Z'),
(2, 1, 'ORD-20260730-1002', 'EchoMind 智能录音笔 A1', 'hardware', 'one_time', 'express', 'app', 899.00, 'CNY', 'paid', 'after_sales', 'Li Wei', '13800010001', '上海', '上海', '浦东新区', '张江路 88 号', '疑似重复扣费', '2026-07-30T09:13:00Z', '2026-07-30T09:13:22Z', '2026-07-30T15:20:00Z'),
(3, 2, 'ORD-20260725-2001', 'EchoMind Pro 年费会员', 'subscription', 'yearly', 'digital', 'wechat', 1999.00, 'CNY', 'paid', 'completed', NULL, NULL, NULL, NULL, NULL, NULL, '企业试用升级', '2026-07-25T10:05:00Z', '2026-07-25T10:05:16Z', '2026-07-25T10:05:16Z'),
(4, 3, 'ORD-20260718-3001', 'EchoMind Team 季度套餐', 'subscription', 'quarterly', 'digital', 'app', 799.00, 'CNY', 'partial_refund', 'refund_pending', NULL, NULL, NULL, NULL, NULL, NULL, '历史退款争议订单', '2026-07-18T13:10:00Z', '2026-07-18T13:10:20Z', '2026-07-28T16:00:00Z'),
(5, 4, 'ORD-20260727-4001', 'EchoMind 智能工牌 B1', 'hardware', 'one_time', 'express', 'web', 699.00, 'CNY', 'paid', 'processing', 'Zhao Ming', '13800010004', '北京', '北京', '朝阳区', '望京 SOHO T2', '大客户加急', '2026-07-27T08:40:00Z', '2026-07-27T08:40:18Z', '2026-07-30T09:45:00Z'),
(6, 4, 'ORD-20260710-4002', 'EchoMind Enterprise 服务包', 'service', 'enterprise', 'digital', 'offline', 12888.00, 'CNY', 'paid', 'completed', NULL, NULL, NULL, NULL, NULL, NULL, '企业合同交付', '2026-07-10T10:00:00Z', '2026-07-10T10:00:45Z', '2026-07-10T10:00:45Z'),
(7, 5, 'ORD-20260705-5001', 'EchoMind Pro 月度会员', 'subscription', 'monthly', 'digital', 'app', 199.00, 'CNY', 'failed', 'cancelled', NULL, NULL, NULL, NULL, NULL, NULL, '支付失败订单', '2026-07-05T07:30:00Z', NULL, '2026-07-05T07:31:30Z'),
(8, 6, 'ORD-20260730-6001', 'EchoMind Team 季度套餐', 'subscription', 'quarterly', 'digital', 'app', 799.00, 'CNY', 'paid', 'completed', NULL, NULL, NULL, NULL, NULL, NULL, '登录异常后咨询退款', '2026-07-30T01:20:00Z', '2026-07-30T01:20:30Z', '2026-07-30T01:20:30Z'),
(9, 7, 'ORD-20260726-7001', 'EchoMind 会议耳机 H1', 'hardware', 'one_time', 'express', 'wechat', 459.00, 'CNY', 'paid', 'after_sales', 'Liu Fang', '13800010007', '广州', '广州', '天河区', '珠江新城 18 号', 'VIP 用户催件', '2026-07-26T12:15:00Z', '2026-07-26T12:15:25Z', '2026-07-30T16:30:00Z'),
(10, 8, 'ORD-20260729-8001', 'EchoMind 智能客服主机 M1', 'hardware', 'one_time', 'express', 'web', 2499.00, 'CNY', 'paid', 'delivered', 'He Tao', '13800010008', '杭州', '杭州', '西湖区', '文三路 66 号', '签收后想换货', '2026-07-29T17:40:00Z', '2026-07-29T17:40:18Z', '2026-07-30T18:15:00Z');

INSERT INTO order_items (id, order_id, sku_id, sku_name, category, quantity, unit_price, discount_amount, final_amount, item_status, fulfillment_status, returnable, exchangeable, created_at, updated_at) VALUES
(1, 1, 'SKU-A1-BLACK', 'EchoMind 智能录音笔 A1 黑色', 'device', 1, 899.00, 0.00, 899.00, 'normal', 'shipped', 1, 1, '2026-07-30T09:12:00Z', '2026-07-30T11:00:00Z'),
(2, 2, 'SKU-A1-BLACK', 'EchoMind 智能录音笔 A1 黑色', 'device', 1, 899.00, 0.00, 899.00, 'refund_pending', 'pending', 1, 1, '2026-07-30T09:13:00Z', '2026-07-30T15:20:00Z'),
(3, 3, 'SKU-PRO-YEAR', 'EchoMind Pro 年费会员', 'subscription', 1, 1999.00, 0.00, 1999.00, 'normal', 'delivered', 1, 0, '2026-07-25T10:05:00Z', '2026-07-25T10:05:16Z'),
(4, 4, 'SKU-TEAM-QTR', 'EchoMind Team 季度套餐', 'subscription', 1, 799.00, 0.00, 799.00, 'refund_pending', 'delivered', 1, 0, '2026-07-18T13:10:00Z', '2026-07-28T16:00:00Z'),
(5, 5, 'SKU-B1-STD', 'EchoMind 智能工牌 B1 标准版', 'device', 2, 349.50, 0.00, 699.00, 'normal', 'packed', 1, 1, '2026-07-27T08:40:00Z', '2026-07-30T09:45:00Z'),
(6, 6, 'SKU-ENT-SVC', 'EchoMind Enterprise 服务包', 'service', 1, 12888.00, 0.00, 12888.00, 'normal', 'delivered', 0, 0, '2026-07-10T10:00:00Z', '2026-07-10T10:00:45Z'),
(7, 7, 'SKU-PRO-MONTH', 'EchoMind Pro 月度会员', 'subscription', 1, 199.00, 0.00, 199.00, 'normal', 'pending', 1, 0, '2026-07-05T07:30:00Z', '2026-07-05T07:31:30Z'),
(8, 8, 'SKU-TEAM-QTR', 'EchoMind Team 季度套餐', 'subscription', 1, 799.00, 0.00, 799.00, 'normal', 'delivered', 1, 0, '2026-07-30T01:20:00Z', '2026-07-30T01:20:30Z'),
(9, 9, 'SKU-H1-SILVER', 'EchoMind 会议耳机 H1 银色', 'device', 1, 459.00, 0.00, 459.00, 'return_pending', 'shipped', 1, 1, '2026-07-26T12:15:00Z', '2026-07-30T16:30:00Z'),
(10, 10, 'SKU-M1-STD', 'EchoMind 智能客服主机 M1 标准版', 'device', 1, 2499.00, 200.00, 2299.00, 'exchange_pending', 'delivered', 1, 1, '2026-07-29T17:40:00Z', '2026-07-30T18:15:00Z');

INSERT INTO shipments (id, order_id, shipment_no, tracking_no, carrier, shipment_status, last_checkpoint, last_checkpoint_at, signed_at, receiver_name, exception_type, exception_summary, created_at, updated_at) VALUES
(1, 1, 'SHP-20260730-1001', 'YT202607300001', 'YTO', 'in_transit', '上海转运中心发出', '2026-07-30T18:20:00Z', NULL, NULL, 'none', NULL, '2026-07-30T10:00:00Z', '2026-07-30T18:20:00Z'),
(2, 5, 'SHP-20260727-4001', 'SF202607270041', 'SF', 'pending', '仓库已打包，等待揽收', '2026-07-30T09:45:00Z', NULL, NULL, 'delay', '仓库爆仓导致揽收延迟', '2026-07-27T12:00:00Z', '2026-07-30T09:45:00Z'),
(3, 9, 'SHP-20260726-7001', 'JD202607260071', 'JD', 'lost', '末端站点反馈包裹异常丢失', '2026-07-30T11:10:00Z', NULL, NULL, 'lost', 'VIP 订单疑似丢件，待物流核查', '2026-07-26T18:00:00Z', '2026-07-30T11:10:00Z'),
(4, 10, 'SHP-20260729-8001', 'ZTO202607290081', 'ZTO', 'signed', '已由前台代收', '2026-07-30T09:20:00Z', '2026-07-30T09:20:00Z', '前台', 'none', NULL, '2026-07-29T21:00:00Z', '2026-07-30T09:20:00Z'),
(5, 2, 'SHP-20260730-1002', 'YT202607300002', 'YTO', 'pending', '订单进入售后拦截，未出库', '2026-07-30T15:20:00Z', NULL, NULL, 'none', '疑似重复扣费订单，已暂停出库', '2026-07-30T10:10:00Z', '2026-07-30T15:20:00Z');

INSERT INTO payment_attempts (id, order_id, attempt_no, payment_channel, attempt_status, amount, currency, transaction_id, failure_reason, is_duplicate_risk, created_at) VALUES
(1, 1, 'PAY-20260730-1001-A', 'wechat_pay', 'success', 899.00, 'CNY', 'WX1001A', NULL, 0, '2026-07-30T09:12:18Z'),
(2, 2, 'PAY-20260730-1002-A', 'wechat_pay', 'success', 899.00, 'CNY', 'WX1002A', NULL, 1, '2026-07-30T09:13:20Z'),
(3, 2, 'PAY-20260730-1002-B', 'wechat_pay', 'duplicate_suspected', 899.00, 'CNY', 'WX1002B', 'Duplicate billing retry detected', 1, '2026-07-30T09:13:29Z'),
(4, 3, 'PAY-20260725-2001-A', 'alipay', 'success', 1999.00, 'CNY', 'ALI2001A', NULL, 0, '2026-07-25T10:05:14Z'),
(5, 4, 'PAY-20260718-3001-A', 'wechat_pay', 'success', 799.00, 'CNY', 'WX3001A', NULL, 0, '2026-07-18T13:10:18Z'),
(6, 5, 'PAY-20260727-4001-A', 'bank_card', 'success', 699.00, 'CNY', 'CARD4001A', NULL, 0, '2026-07-27T08:40:16Z'),
(7, 6, 'PAY-20260710-4002-A', 'bank_card', 'success', 12888.00, 'CNY', 'CARD4002A', NULL, 0, '2026-07-10T10:00:43Z'),
(8, 7, 'PAY-20260705-5001-A', 'wechat_pay', 'failed', 199.00, 'CNY', NULL, 'Insufficient balance', 0, '2026-07-05T07:30:18Z'),
(9, 8, 'PAY-20260730-6001-A', 'wechat_pay', 'success', 799.00, 'CNY', 'WX6001A', NULL, 0, '2026-07-30T01:20:28Z'),
(10, 9, 'PAY-20260726-7001-A', 'wechat_pay', 'success', 459.00, 'CNY', 'WX7001A', NULL, 0, '2026-07-26T12:15:23Z'),
(11, 10, 'PAY-20260729-8001-A', 'alipay', 'success', 2299.00, 'CNY', 'ALI8001A', NULL, 0, '2026-07-29T17:40:16Z');

INSERT INTO refund_policies (id, policy_code, product_name, product_type, plan_type, refund_window_hours, usage_limit_percent, allow_refund, manual_review_required, review_reason_template, refund_method, policy_category, policy_text, effective_from, effective_to) VALUES
(1, 'RFD-PRO-YEARLY-2026', 'EchoMind Pro 年费会员', 'subscription', 'yearly', 72, 10.0, 1, 1, '年费会员退款需人工审核订阅使用情况。', 'original_route', 'subscription', 'Yearly Pro subscription can request refund within 72 hours if usage is below 10%, but manual review is required.', '2026-01-01T00:00:00Z', NULL),
(2, 'RFD-PRO-MONTHLY-2026', 'EchoMind Pro 月度会员', 'subscription', 'monthly', 24, 5.0, 1, 0, '月度会员在窗口内可自动退款。', 'original_route', 'subscription', 'Monthly Pro subscription can request refund within 24 hours if usage is below 5%.', '2026-01-01T00:00:00Z', NULL),
(3, 'RFD-TEAM-QUARTERLY-2026', 'EchoMind Team 季度套餐', 'subscription', 'quarterly', 48, 10.0, 1, 1, '季度套餐退款需人工审核。', 'original_route', 'subscription', 'Quarterly Team plans support refund within 48 hours if usage is below 10%, but manual review is always required.', '2026-01-01T00:00:00Z', NULL),
(4, 'RFD-HW-A1-2026', 'EchoMind 智能录音笔 A1', 'hardware', 'one_time', 168, 0.0, 1, 0, '未拆封硬件 7 天内可退。', 'original_route', 'physical_goods', 'Hardware device can request refund within 7 days if unopened and not activated.', '2026-01-01T00:00:00Z', NULL),
(5, 'RFD-HW-H1-2026', 'EchoMind 会议耳机 H1', 'hardware', 'one_time', 168, 0.0, 1, 1, '耳机类商品涉及签收异常时需人工审核。', 'manual', 'physical_goods', 'Headset orders can request refund within 7 days, but lost-package and signed-dispute cases require manual review.', '2026-01-01T00:00:00Z', NULL),
(6, 'RFD-ENT-2026', 'EchoMind Enterprise 服务包', 'service', 'enterprise', 0, 0.0, 0, 1, '企业服务包不支持自动退款，需合同审核。', 'manual', 'enterprise_contract', 'Enterprise service contracts do not support automatic refunds and always require account review.', '2026-01-01T00:00:00Z', NULL);

INSERT INTO refund_requests (id, refund_no, order_id, customer_id, refund_reason, requested_amount, approved_amount, refund_status, review_required, review_status, review_reason, refund_channel, requested_at, reviewed_at, completed_at, created_at, updated_at) VALUES
(1, 'RFD-REQ-20260728-3001', 4, 3, 'Repeated billing dispute and service dissatisfaction', 799.00, NULL, 'pending_review', 1, 'pending', 'Risk account requires manual billing review', 'manual', '2026-07-28T16:00:00Z', NULL, NULL, '2026-07-28T16:00:00Z', '2026-07-28T16:00:00Z'),
(2, 'RFD-REQ-20260730-1002', 2, 1, 'Suspected duplicate charge before shipment', 899.00, NULL, 'pending_review', 1, 'pending', 'Duplicate payment needs finance confirmation', 'original_route', '2026-07-30T15:20:00Z', NULL, NULL, '2026-07-30T15:20:00Z', '2026-07-30T15:20:00Z'),
(3, 'RFD-REQ-20260729-7001', 9, 7, 'Package reported lost in transit', 459.00, 459.00, 'approved', 1, 'approved', 'VIP lost package approved for manual refund', 'manual', '2026-07-30T12:00:00Z', '2026-07-30T14:00:00Z', NULL, '2026-07-30T12:00:00Z', '2026-07-30T14:00:00Z');

INSERT INTO return_requests (id, return_no, order_item_id, customer_id, return_reason, return_status, pickup_required, warehouse_received_at, refund_linked, created_at, updated_at) VALUES
(1, 'RET-20260730-7001', 9, 7, 'Package likely lost, request return flow entry for after-sales tracking', 'pending_review', 0, NULL, 1, '2026-07-30T12:05:00Z', '2026-07-30T12:05:00Z'),
(2, 'RET-20260731-1001', 1, 1, 'Unopened device wants return after shipment delay', 'created', 1, NULL, 0, '2026-07-31T01:10:00Z', '2026-07-31T01:10:00Z');

INSERT INTO exchange_requests (id, exchange_no, order_item_id, customer_id, exchange_reason, exchange_status, new_shipment_no, created_at, updated_at) VALUES
(1, 'EXG-20260730-8001', 10, 8, 'Device fan noise too loud after first use', 'approved', 'SHP-EXG-20260731-8001', '2026-07-30T18:20:00Z', '2026-07-30T19:10:00Z');

INSERT INTO coupon_compensations (id, customer_id, order_id, compensation_no, coupon_code, compensation_type, coupon_value, issue_reason, issued_by, status, expire_at, created_at) VALUES
(1, 4, 5, 'CMP-20260730-4001', 'CPN-DELAY-80', 'coupon', 80.00, 'Warehouse delay compensation for enterprise urgent order', 'system_after_sales', 'issued', '2026-08-31T23:59:59Z', '2026-07-30T10:10:00Z'),
(2, 7, 9, 'CMP-20260730-7001', 'CPN-VIP-120', 'coupon', 120.00, 'VIP lost package goodwill compensation', 'vip_desk_agent', 'issued', '2026-09-15T23:59:59Z', '2026-07-30T14:20:00Z');

INSERT INTO incidents (id, incident_code, title, category, severity, status, affected_scope, summary, started_at, resolved_at, updated_at) VALUES
(1, 'INC-LOGIN-20260730', 'Temporary login lock spike after security rule rollout', 'login', 'high', 'identified', 'Users with repeated failed logins', 'A stricter security rule caused more temporary locks than expected. Users can retry after the unlock window or reset password.', '2026-07-30T20:30:00Z', NULL, '2026-07-31T01:20:00Z'),
(2, 'INC-BILLING-20260730', 'Duplicate payment retries under investigation', 'billing', 'medium', 'investigating', 'A small subset of hardware express orders', 'Some orders may show duplicate payment attempts. Finance is verifying payment reversals.', '2026-07-30T09:00:00Z', NULL, '2026-07-30T18:00:00Z'),
(3, 'INC-LOGISTICS-20260730', 'East China carrier transfer delay', 'logistics', 'medium', 'monitoring', 'Express orders routed through Shanghai hub', 'Several parcels routed through Shanghai are delayed by 12-24 hours because of sorting backlog.', '2026-07-30T13:10:00Z', NULL, '2026-07-30T21:00:00Z'),
(4, 'INC-API-20260720', 'After-sales apply API intermittent 500 errors', 'api', 'critical', 'resolved', 'Refund apply API only', 'A deployment regression caused intermittent 500 responses on after-sales apply API. The incident has been resolved.', '2026-07-20T08:10:00Z', '2026-07-20T10:35:00Z', '2026-07-20T10:35:00Z');

INSERT INTO warehouse_incidents (id, incident_no, incident_type, severity, status, affected_scope, summary, started_at, resolved_at, updated_at) VALUES
(1, 'WH-20260730-001', 'capacity_overload', 'high', 'identified', '华东一仓 B2 区域', '大促回流订单叠加补货，导致部分订单揽收延迟。', '2026-07-30T07:30:00Z', NULL, '2026-07-30T17:30:00Z'),
(2, 'WH-20260729-002', 'mis_pick', 'medium', 'monitoring', '耳机配件 SKU', '个别耳机订单出现错发配件，仓库正在复核波次任务。', '2026-07-29T11:00:00Z', NULL, '2026-07-30T12:20:00Z'),
(3, 'WH-20260728-003', 'inventory_mismatch', 'medium', 'resolved', '智能工牌 B1 标准版', '库存同步延迟导致短时可售库存异常，现已恢复。', '2026-07-28T09:00:00Z', '2026-07-28T16:10:00Z', '2026-07-28T16:10:00Z');

INSERT INTO handoff_tickets (id, ticket_no, customer_id, conv_id, source_intent, source_ref_type, source_ref_no, priority, status, reason, summary, assigned_queue, sla_level, owner_agent, created_at, updated_at) VALUES
(1, 'TKT-20260730-3001', 3, 'conv_ecom_wn_01', 'refund', 'refund_request', 'RFD-REQ-20260728-3001', 'high', 'triaged', 'Restricted account refund requires manual billing review', 'VIP restricted customer requested quarterly plan refund after multiple disputes.', 'billing_ops', 'vip', 'agent_lee', '2026-07-30T16:05:00Z', '2026-07-30T16:20:00Z'),
(2, 'TKT-20260730-7001', 7, 'conv_ecom_lf_01', 'logistics', 'order', 'ORD-20260726-7001', 'urgent', 'in_progress', 'VIP shipment lost and compensation needed', 'VIP user reported lost package and requested both refund and replacement options.', 'vip_desk', 'vip', 'agent_tan', '2026-07-30T12:10:00Z', '2026-07-30T15:30:00Z');

INSERT INTO after_sales_tickets (id, ticket_no, ticket_type, order_id, customer_id, source_channel, status, priority, assigned_queue, summary, created_at, updated_at) VALUES
(1, 'AST-20260730-1002', 'refund', 2, 1, 'app', 'open', 'high', 'after_sales_ops', 'Duplicate charge order waiting for finance confirmation before refund execution.', '2026-07-30T15:25:00Z', '2026-07-30T15:25:00Z'),
(2, 'AST-20260730-7001', 'logistics', 9, 7, 'wechat', 'in_progress', 'urgent', 'logistics_ops', 'VIP lost-package complaint requires courier investigation and compensation follow-up.', '2026-07-30T12:12:00Z', '2026-07-30T15:40:00Z');

INSERT INTO agent_audit_logs (id, conv_id, session_id, agent_name, task_type, action_type, target_type, target_no, action_payload_json, result_status, error_message, created_at) VALUES
(1, 'conv_ecom_wn_01', 'sess_seed_001', 'support_graph', 'refund', 'create_handoff_ticket', 'ticket', 'TKT-20260730-3001', '{"reason":"refund_manual_review","priority":"high"}', 'success', NULL, '2026-07-30T16:05:10Z'),
(2, 'conv_ecom_lf_01', 'sess_seed_002', 'support_graph', 'logistics', 'issue_coupon_compensation', 'compensation', 'CMP-20260730-7001', '{"coupon_value":120,"queue":"vip_desk"}', 'success', NULL, '2026-07-30T14:20:10Z'),
(3, 'conv_ecom_lw_01', 'sess_seed_003', 'support_graph', 'refund', 'create_refund_request', 'refund_request', 'RFD-REQ-20260730-1002', '{"order_no":"ORD-20260730-1002"}', 'success', NULL, '2026-07-30T15:20:10Z');
