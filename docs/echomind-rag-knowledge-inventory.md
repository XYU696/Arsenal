# EchoMind RAG 知识库建设方案 V1

> 面向电商售后场景的企业级 Agent 平台 · 知识层（Phase A：文档清单 + Metadata 设计）
>
> 更新日期：2026-08-05

---

## 1. 目标与定位

RAG 在本项目中的定位是 Agent 的**知识层**，负责回答三类问题：

1. **规则/政策类**：退款政策、审核时效、到账时效
2. **流程/操作类**：退货怎么申请、换货多久、发票怎么开
3. **故障/公告类**：当前已知故障、技术排查步骤

边界原则：**DB 管"事实"（订单/账号/物流/支付状态），RAG 管"规则与流程"**。
知识库中的政策数值必须与业务库 `refund_policies` 等表保持一致，作为评测项之一。

---

## 2. 知识域与文档清单

共 **9 个知识域、37 篇文档**，全部与 v2 种子数据对齐。

### 2.1 退款政策（refund_policy）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-RP-001 | 退款政策总览与通用规则 | 通用 | 政策条款 | refund_policies | 申请入口、审核时效、到账时效、人工审核条件 |
| KB-RP-002 | EchoMind Pro 年费会员退款政策 | EchoMind Pro | 政策条款 | refund_policies | 72 小时窗口、使用率 ≤10%、需人工审核 |
| KB-RP-003 | EchoMind Pro 月度会员退款政策 | EchoMind Pro | 政策条款 | refund_policies | 24 小时窗口、使用率 ≤5%、无需人工审核 |
| KB-RP-004 | EchoMind Team 季度套餐退款政策 | EchoMind Team | 政策条款 | refund_policies | 48 小时窗口、使用率 ≤10%、需人工审核 |
| KB-RP-005 | EchoMind 智能录音笔 A1 退款政策 | 智能录音笔 A1 | 政策条款 | refund_policies | 168 小时窗口、硬件未拆封、无需人工审核 |
| KB-RP-006 | EchoMind 会议耳机 H1 退款政策 | 会议耳机 H1 | 政策条款 | refund_policies | 168 小时窗口、需人工审核 |
| KB-RP-007 | EchoMind Enterprise 服务包退款政策 | Enterprise | 合同条款 | refund_policies | 不支持自动退款、需人工审核 |
| KB-RP-008 | 退款申请审核与到账说明 | 通用 | SOP | refund_requests | pending_review/approved 状态含义、1-3 个工作日审核、原路退回 |
| KB-RP-009 | 退款申请撤销与修改 | 通用 | SOP | refund_requests | 审核前可撤销、重复申请处理 |

### 2.2 退货流程（return_process）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-RT-001 | 退货申请流程 | 硬件类 | SOP | return_requests | 申请入口、可退货条件（returnable） |
| KB-RT-002 | 上门取件与时效 | 硬件类 | SOP | return_requests | pickup_required 含义、取件时效 |
| KB-RT-003 | 退货质检与退款关联 | 硬件类 | SOP | return_requests / refund_requests | 质检通过后自动关联退款（refund_linked） |
| KB-RT-004 | 退货运费规则 | 硬件类 | SOP | return_requests | 质量问题免运费、非质量问题用户承担 |

### 2.3 换货流程（exchange_process）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-EX-001 | 换货申请条件与流程 | 硬件类 | SOP | exchange_requests | 申请条件、可换货判断（exchangeable） |
| KB-EX-002 | 换货处理时效与新运单 | 硬件类 | SOP | exchange_requests / shipments | 质检、新运单发放（new_shipment_no） |
| KB-EX-003 | 换货失败与降级处理 | 硬件类 | SOP | exchange_requests | 缺货转退款、补偿方案 |

### 2.4 物流异常（logistics_issue）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-LG-001 | 物流状态说明 | 硬件类 | FAQ | shipments | pending/in_transit/lost/signed 状态含义 |
| KB-LG-002 | 丢件处理流程 | 硬件类 | SOP | shipments / after_sales_tickets | 异常申报、承运商核查、VIP 补偿跟进 |
| KB-LG-003 | 发货延迟与仓库爆仓 | 硬件类 | 公告 | warehouse_incidents | capacity_overload 影响、预计恢复时间 |
| KB-LG-004 | 错发配件处理 | 硬件类 | 公告 | warehouse_incidents | mis_pick 复核流程、补发或补偿 |
| KB-LG-005 | 售后拦截订单说明 | 硬件类 | SOP | orders / shipments | after_sales 状态、暂停出库原因 |
| KB-LG-006 | 华东转运延迟公告 | 硬件类 | 公告 | incidents | INC-LOGISTICS-20260730、延误 12-24 小时 |

### 2.5 优惠券补偿（coupon_compensation）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-CC-001 | 优惠券补偿规则 | 通用 | SOP | coupon_compensations | 触发条件（仓库延迟/丢件）、发放主体 |
| KB-CC-002 | 补偿面额与有效期 | 通用 | SOP | coupon_compensations | 面额标准、有效期、使用限制 |

### 2.6 支付与发票（payment_invoice）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-PI-001 | 重复扣费处理流程 | 通用 | SOP | payment_attempts | duplicate_suspected 识别、财务核实、退款关联 |
| KB-PI-002 | 支付失败处理 | 通用 | SOP | payment_attempts | 余额不足等失败原因、重试建议 |
| KB-PI-003 | 发票开具流程 | 通用 | SOP | orders | 开票入口、时效、抬头修改 |

### 2.7 售后工单与 SLA（after_sales_ticket）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-AT-001 | 转人工/售后工单触发条件 | 通用 | SOP | handoff_tickets | 高风险退款、账号异常、VIP 场景 |
| KB-AT-002 | 工单队列与处理时效 | 通用 | SOP | after_sales_tickets | after_sales_ops/logistics_ops/billing_ops/vip_desk |
| KB-AT-003 | 工单状态说明 | 通用 | SOP | after_sales_tickets / handoff_tickets | open/in_progress/triaged 状态含义 |

### 2.8 故障与公告（incident）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-INC-001 | 登录锁定故障说明 | 通用 | 公告 | incidents | INC-LOGIN-20260730、解锁窗口、重置密码 |
| KB-INC-002 | 重复扣费排查公告 | 通用 | 公告 | incidents | INC-BILLING-20260730、财务核实中 |
| KB-INC-003 | 售后 API 500 故障说明 | 通用 | 公告 | incidents | INC-API-20260720、已恢复 |
| KB-INC-004 | 常见技术故障排查 | 通用 | FAQ | incidents | 401/500/登录失败/崩溃处理步骤 |

### 2.9 话术与合规（compliance）

| 文档 ID | 标题 | 适用产品 | 来源 | 对齐 DB 表 | 关键内容 |
|---|---|---|---|---|---|
| KB-CP-001 | 禁止承诺事项 | 通用 | 合规规范 | — | 不承诺到账时间、不承诺赔偿金额 |
| KB-CP-002 | 隐私与安全边界 | 通用 | 合规规范 | — | 不索要密码/验证码、信息核实方式 |
| KB-CP-003 | 客服回复规范 | 通用 | 培训文档 | — | 话术结构、升级话术、结束语 |

---

## 3. Metadata 设计

### 3.1 Schema

```json
{
  "doc_id": "KB-RP-005",
  "title": "EchoMind 智能录音笔 A1 退款政策",
  "category": "refund_policy",
  "product": "智能录音笔 A1",
  "plan_type": "one_time",
  "effective_from": "2026-01-01",
  "effective_to": null,
  "source": "policy",
  "version": 1,
  "owner": "售后运营",
  "tags": "退款,硬件,录音笔",
  "db_table": "refund_policies"
}
```

### 3.2 字段说明

| 字段 | 类型 | 必填 | 说明 | 可过滤 |
|---|---|---|---|---|
| doc_id | string | ✅ | 全局唯一文档 ID，前缀按知识域（KB-RP / KB-RT / KB-EX / KB-LG / KB-CC / KB-PI / KB-AT / KB-INC / KB-CP） | 否 |
| title | string | ✅ | 文档标题 | 否 |
| category | string | ✅ | 知识域枚举（见 3.3） | ✅ `$eq` / `$in` |
| product | string | ✅ | 适用产品；通用场景填 `通用` | ✅ `$eq` |
| plan_type | string | ✅ | 套餐/规格：yearly / monthly / quarterly / one_time / enterprise / 通用 | ✅ `$eq` |
| effective_from | int | ✅ | 生效日期，存 `YYYYMMDD` 整数（如 `20260101`） | ✅ `$gte` |
| effective_to | int | ❌ | 失效日期，存 `YYYYMMDD` 整数；长期有效存 `99991231` | ✅ `$lte` |
| source | string | ✅ | 文档来源：policy / sop / faq / incident / compliance | ✅ `$eq` |
| version | int | ✅ | 文档版本号，用于更新追溯 | 否 |
| owner | string | ✅ | 责任人（售后运营 / 法务 / 技术） | 否 |
| tags | string | ✅ | 逗号分隔的标签，如 "退款,硬件" | 否（查询侧做关键词匹配） |
| db_table | string | ✅ | 对齐的业务表名，无则 `-` | ✅ `$eq` |

### 3.3 category 枚举

```
refund_policy | return_process | exchange_process | logistics_issue
| coupon_compensation | payment_invoice | after_sales_ticket | incident | compliance
```

### 3.4 ChromaDB 存储约束与过滤规则

1. ChromaDB metadata 的值只支持 `str / int / float / bool` 标量，因此：
   - `tags` 存为逗号分隔字符串，不做 `where` 过滤，检索侧用于关键词路召回；
   - `effective_from / effective_to` 必须存 `YYYYMMDD` 整数，因为 ChromaDB 的 `$gte / $lte` 只支持数值比较（字符串会报错）。
2. 检索时的标准过滤组合：

```json
{
  "category": {"$in": ["refund_policy", "return_process"]},
  "product": "智能录音笔 A1",
  "effective_from": {"$lte": 20260805},
  "effective_to": {"$gte": 20260805}
}
```

3. 分块时每块继承同一份 metadata，并追加 `chunk_index` / `total_chunks`（现有逻辑保留）。

---

### 3.5 一致性校验字段（Phase A 新增）

部分文档额外携带结构化字段，用于与业务库自动比对，防止知识库与业务规则冲突：

| 字段 | 适用文档 | 对齐表 |
|---|---|---|
| policy_code / refund_window_hours / usage_limit_percent / allow_refund / manual_review_required | refund_policy 类（6 篇） | refund_policies |
| incident_code / incident_status | 故障/物流公告类（4 篇） | incidents |

校验命令：`python3 -m integrations.knowledge_base.consistency_check`

---

## 4. 一致性映射（知识域 ↔ DB 表 ↔ 工具）

| 知识域 | 对齐 DB 表 | 对应工具 | 一致性校验 |
|---|---|---|---|
| refund_policy | refund_policies / refund_requests | refund_policy_lookup / refund_eligibility_check | 校验 KB 政策数值 = 表字段 |
| return_process | return_requests / refund_requests | refund_request_lookup | 校验流程状态枚举一致 |
| exchange_process | exchange_requests / shipments | shipment_tracking_lookup | — |
| logistics_issue | shipments / warehouse_incidents / incidents | shipment_tracking_lookup / incident_lookup | — |
| coupon_compensation | coupon_compensations | —（待建工具） | — |
| payment_invoice | payment_attempts / orders | payment_attempt_lookup | — |
| after_sales_ticket | after_sales_tickets / handoff_tickets | create_handoff_ticket | — |
| incident | incidents | incident_lookup | 校验 KB 公告状态 = 表状态 |
| compliance | — | — | — |

---

## 5. 分块与存储设计

- 每篇文档按"条款 / SOP 步骤"语义分块，块大小 300-500 字（沿用现有按句切分，可加 50 字重叠）。
- 每块 metadata 完整继承文档 metadata + `chunk_index` / `total_chunks`。
- 存储：ChromaDB collection `knowledge_base_v2`（新 collection，避免与旧 6 篇通用文档混在一起；旧 collection 保留不动，便于回滚）。
- Embedding：先沿用 ChromaDB 内置模型，Phase B 再评估换中文友好的 BGE 系列。

---

## 6. 落地步骤

### Phase A（当前阶段）：内容入库

1. 生成知识库内容 JSON（`data/knowledge/after_sales_v2.json`，37 篇文档带 metadata）
2. 扩展 `mcp/knowledge_base.py`：`add_documents` 支持 metadata，`search` 支持 `where` 过滤
3. 新增知识库初始化脚本（导入 v2 文档到 `knowledge_base_v2` collection）
4. 新增一致性校验脚本（KB 政策数值 vs `refund_policies` 表）
5. `/knowledge` 接口支持 category/product 过滤查询

### Phase C-0（Phase B 之前）：检索层基线评测

先用当前"直接向量检索"建立可量化基线，Phase B 改进后用同一数据集对比：

- 评测集：`evaluation/datasets/rag_retrieval_eval_v1.json`（35 条用例，覆盖 9 个知识域，标注期望文档）
- 脚本：`evaluation/run_rag_retrieval_eval.py`
- 指标：Recall@k / Precision@k / MRR / NDCG@k / 过滤合规率 / 空结果率
- 基线命令：`python3 -m evaluation.run_rag_retrieval_eval --mode direct --note baseline-direct-v2kb`
- 对比命令：`python3 -m evaluation.run_rag_retrieval_eval --compare <baseline.json> --new <新报告.json>`

### Phase B：检索质量

- [x] Agent 工具路径接入 `search_with_rewrite` 完整链路（`knowledge_search` 工具 `use_rewrite=True`）
- [x] 混合检索（向量 + BM25）+ RRF 融合（`KnowledgeBase.search_hybrid`，纯 Python BM25，零新依赖）
- cross-encoder 重排 + 相关性阈值 + 引用溯源

Phase B 评测命令：

- `python3 -m evaluation.run_rag_retrieval_eval --mode hybrid --note after-hybrid`
- `python3 -m evaluation.run_rag_retrieval_eval --mode rewrite --note after-rewrite`（需 ANTHROPIC_API_KEY）
- 对比：`python3 -m evaluation.run_rag_retrieval_eval --compare evaluation/reports/rag_retrieval_direct_20260805T093607+0000.json --new evaluation/reports/rag_retrieval_hybrid_*.json`

### Phase C：评测

- 三层评测（检索层 / 生成层 / 端到端）；检索层基线已先行（Phase C-0）
- 生成层与端到端评测集补充，改前改后对比报告 + 回归门槛

### Phase D：工程化

- 文档版本管理、badcase 回流、监控告警、权限可见性

---

## 7. 验收标准（Phase A）

- [ ] 37 篇文档全部入库，`knowledge_base_v2` collection 可查
- [ ] 每篇文档 metadata 完整，`where` 按 category/product/有效期过滤生效
- [ ] 一致性校验通过：KB 退款政策数值与 `refund_policies` 表一致
- [ ] `/knowledge/search` 支持过滤参数，返回带 metadata 的结果
