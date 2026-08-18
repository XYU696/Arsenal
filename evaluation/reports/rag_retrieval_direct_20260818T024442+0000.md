# RAG 检索层评测报告（direct）

- 数据集: rag_retrieval_eval_v1.json
- 模式: direct | top_k=5 | 过滤: False
- 说明: baseline-direct-v2kb
- 时间: 2026-08-18T02:44:42+00:00

## 汇总指标

| 指标 | 值 |
|---|---|
| Recall@5 | 0.3143 |
| Precision@5 | 0.0743 |
| MRR | 0.2881 |
| NDCG@5 | 0.2706 |
| 过滤合规率 | 0.1314 |
| 空结果率 | 0.0 |

## 逐用例

| ID | 查询 | 期望 | 召回 | Recall | MRR |
|---|---|---|---|---|---|
| RAG-001 | EchoMind Pro 年费会员刚买一天，想退 | KB-RP-002,KB-RP-001 | KB-RP-002,KB-RP-007,KB-RP-003,KB-RP-006,KB-RP-004 | 0.5 | 1.0 |
| RAG-002 | Pro 月付会员退款时效是多久 | KB-RP-003 | KB-EX-003,KB-PI-003,KB-RP-006,KB-CP-003,KB-CP-001 | 0.0 | 0.0 |
| RAG-003 | Team 季度套餐退款需要人工审核吗 | KB-RP-004 | KB-RP-004,KB-CP-002,KB-AT-001,KB-RP-005,KB-RP-006 | 1.0 | 1.0 |
| RAG-004 | 智能录音笔 A1 不想要了能退吗 | KB-RP-005,KB-RT-001 | KB-RP-005,KB-RP-006,KB-AT-001,KB-CP-002,KB-LG-006 | 0.5 | 1.0 |
| RAG-005 | 会议耳机 H1 退款要什么条件 | KB-RP-006 | KB-RP-006,KB-LG-004,KB-LG-006,KB-CP-002,KB-RP-005 | 1.0 | 1.0 |
| RAG-006 | 企业服务包能不能自动退款 | KB-RP-007 | KB-PI-002,KB-RT-004,KB-CC-001,KB-RP-009,KB-RP-006 | 0.0 | 0.0 |
| RAG-007 | 退款审核要多久，钱什么时候到账 | KB-RP-008,KB-RP-001 | KB-EX-003,KB-RP-006,KB-RP-005,KB-PI-003,KB-CP-003 | 0.0 | 0.0 |
| RAG-008 | 我提交了退款申请还能撤销吗 | KB-RP-009 | KB-PI-003,KB-EX-003,KB-RT-004,KB-CP-003,KB-RT-003 | 0.0 | 0.0 |
| RAG-009 | 怎么申请退货 | KB-RT-001 | KB-EX-003,KB-CP-003,KB-RT-004,KB-RP-006,KB-LG-004 | 0.0 | 0.0 |
| RAG-010 | 退货上门取件要等多久 | KB-RT-002 | KB-EX-003,KB-RP-006,KB-RP-005,KB-CC-001,KB-CC-002 | 0.0 | 0.0 |
| RAG-011 | 退货寄回去之后什么时候退款 | KB-RT-003,KB-RP-008 | KB-CC-001,KB-RP-006,KB-PI-002,KB-PI-003,KB-RP-009 | 0.0 | 0.0 |
| RAG-012 | 退货运费由谁承担 | KB-RT-004 | KB-EX-003,KB-RT-004,KB-CP-003,KB-RP-006,KB-LG-004 | 1.0 | 0.5 |
| RAG-013 | 耳机有问题想换个新的可以吗 | KB-EX-001,KB-EX-002 | KB-RP-006,KB-LG-004,KB-RT-001,KB-RP-005,KB-CC-002 | 0.0 | 0.0 |
| RAG-014 | 换货多久能收到新货 | KB-EX-002 | KB-RP-009,KB-PI-003,KB-RP-006,KB-EX-003,KB-RP-005 | 0.0 | 0.0 |
| RAG-015 | 换货的商品缺货了怎么办 | KB-EX-003 | KB-CP-003,KB-RT-004,KB-AT-001,KB-PI-003,KB-RP-006 | 0.0 | 0.0 |
| RAG-016 | 我的包裹显示运输中是什么意思 | KB-LG-001 | KB-AT-003,KB-RP-008,KB-RT-001,KB-CC-002,KB-LG-006 | 0.0 | 0.0 |
| RAG-017 | 包裹疑似丢了怎么办 | KB-LG-002 | KB-EX-003,KB-RT-004,KB-RP-006,KB-CP-003,KB-LG-004 | 0.0 | 0.0 |
| RAG-018 | 仓库爆仓了发货会延迟多久 | KB-LG-003 | KB-EX-003,KB-RP-006,KB-CP-001,KB-PI-003,KB-RP-005 | 0.0 | 0.0 |
| RAG-019 | 收到的耳机配件不对怎么办 | KB-LG-004 | KB-RP-006,KB-LG-004,KB-RT-004,KB-PI-002,KB-CP-003 | 1.0 | 0.5 |
| RAG-020 | 订单为什么被拦截不发货了 | KB-LG-005,KB-PI-001 | KB-PI-002,KB-RT-004,KB-CC-001,KB-RP-009,KB-RP-006 | 0.0 | 0.0 |
| RAG-021 | 上海中转的包裹延误了多久 | KB-LG-006 | KB-LG-006,KB-CC-002,KB-PI-003,KB-CP-003,KB-RP-009 | 1.0 | 1.0 |
| RAG-022 | 因为发货延迟能给我补偿券吗 | KB-CC-001,KB-CC-002 | KB-EX-003,KB-PI-003,KB-RT-004,KB-CP-003,KB-CP-001 | 0.0 | 0.0 |
| RAG-023 | 补偿优惠券有效期多久 | KB-CC-002 | KB-RT-004,KB-CC-002,KB-CP-003,KB-EX-003,KB-PI-003 | 1.0 | 0.5 |
| RAG-024 | 我被重复扣费了怎么处理 | KB-PI-001,KB-INC-002 | KB-PI-003,KB-EX-003,KB-RT-004,KB-CP-003,KB-CP-001 | 0.0 | 0.0 |
| RAG-025 | 支付失败订单会扣款吗 | KB-PI-002 | KB-RP-006,KB-CP-001,KB-LG-005,KB-RT-003,KB-EX-003 | 0.0 | 0.0 |
| RAG-026 | 怎么开发票，多久能开好 | KB-PI-003 | KB-EX-003,KB-CP-003,KB-RP-006,KB-PI-003,KB-RP-005 | 1.0 | 0.25 |
| RAG-027 | 什么问题会被转人工处理 | KB-AT-001,KB-AT-003 | KB-CP-001,KB-RP-006,KB-AT-001,KB-CP-002,KB-RP-005 | 0.5 | 0.3333 |
| RAG-028 | 售后工单多久有人处理 | KB-AT-002 | KB-AT-001,KB-PI-003,KB-RP-006,KB-RP-005,KB-CP-003 | 0.0 | 0.0 |
| RAG-029 | 工单显示 in_progress 是什么意思 | KB-AT-003 | KB-AT-003,KB-RP-008,KB-RT-001,KB-LG-001,KB-INC-004 | 1.0 | 1.0 |
| RAG-030 | 账号被临时锁定了是怎么回事 | KB-INC-001 | KB-CP-003,KB-CP-001,KB-RP-006,KB-RT-001,KB-AT-003 | 0.0 | 0.0 |
| RAG-031 | 最近有重复扣费的故障吗 | KB-INC-002 | KB-RT-004,KB-CP-003,KB-AT-001,KB-PI-003,KB-RP-006 | 0.0 | 0.0 |
| RAG-032 | 售后申请页面一直报 500 错误 | KB-INC-003,KB-INC-004 | KB-INC-004,KB-CC-002,KB-CC-001,KB-RT-004,KB-RP-009 | 0.5 | 1.0 |
| RAG-033 | 登录一直提示 401 怎么办 | KB-INC-004 | KB-INC-004,KB-CC-002,KB-AT-003,KB-RP-008,KB-RT-001 | 1.0 | 1.0 |
| RAG-034 | 客服能不能承诺退款到账时间 | KB-CP-001 | KB-PI-002,KB-RT-004,KB-CC-001,KB-RP-006,KB-RP-009 | 0.0 | 0.0 |
| RAG-035 | 客服会向我要密码或验证码吗 | KB-CP-002 | KB-EX-003,KB-RT-003,KB-CP-001,KB-LG-005,KB-PI-003 | 0.0 | 0.0 |