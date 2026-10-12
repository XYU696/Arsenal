# 执行计划：Tool 自适应治理 & 分层检索增强意图路由

> 目标：补齐简历第 2 条（Tool 自适应治理）与第 3 条（分层检索增强意图路由）的工程实现，
> 并让简历上的每个数字都有可复现的评测/模拟报告支撑。
>
> 原则：
> 1. 所有新行为用配置开关控制，默认关闭，灰度打开（不影响现有 /chat 链路）；
> 2. 数字以实际跑出的结果为准，达不到目标就调参，或同步修改简历口径——**绝不编造报告**；
> 3. 模拟与评测固定随机种子，产物落 `evaluation/reports/`，面试时能现场复现。

---

## 0. 现状盘点

### 0.1 Tool 自适应治理

| 能力 | 现状 | 位置 |
|---|---|---|
| 滑动窗口 Z-score 异常检测 | ✅ 已有（window=60，阈值 2.5 / 高严重度 3.75） | `monitor/performance_monitor.py` `AnomalyDetector` |
| 绝对 SLO 阈值告警（成功率/延迟） | ✅ 已有 `THRESHOLDS` 表 + `_check_threshold` | 同上 |
| 按工具聚合成功率、平均延迟 | ✅ 已有（`_collect` 中 tool_success_rate / tool_avg_ms） | 同上 |
| ToolManager 接监控 | ❌ **零引用**，监控和工具层目前是断开的 | `mcp/tool_manager.py` |
| 四级严重度 → 治理动作 | ❌ 没有，检测结果只打日志/告警 | — |
| 动态缓存 TTL | ❌ TTL 注册时写死 | `Tool.cache_ttl` |
| 并发限流 | ❌ 无 semaphore / limiter | — |
| 过期缓存兜底（stale cache） | ❌ 过期即删（`_get_cache`） | `tool_manager.py` 缓存段 |
| 强制降级 | ⚠️ 只有"连续失败 5 次后被动熔断"，无主动降级 | `CircuitBreaker` |
| 故障注入 / 抖动模拟 harness | ❌ 没有 | — |

结论：**检测侧约 70%，执行侧 0%**。新增代码集中在 tool_manager + 一个新治理控制器 + 一个模拟脚本。

### 0.2 分层检索增强意图路由

| 能力 | 现状 | 位置 |
|---|---|---|
| 意图枚举（10 类）+ 紧急度 | ✅ | `core/intent_recognizer.py` `IntentCategory / UrgencyLevel` |
| 三路投票（LLM / embedding 余弦 / 关键词） | ✅ `_vote` | 同上 |
| confidence + 阈值降级 OTHER | ✅ 0.5 | 同上 |
| 实体抽取 | ✅ `_extract_entities` | 同上 |
| LRU 结果缓存 | ✅ 1000 条 | 同上 |
| Domain → Intent 两级路由 | ❌ 只有单层扁平分类 | — |
| BM25 + Dense 检索意图定义/历史样例库 | ❌（组件在 `mcp/retrieval_hybrid.py` 可直接复用） | — |
| LLM 候选重排 | ❌（`tool_manager._rerank` 可参考） | — |
| 融合 final_confidence（检索分/LLM/实体/会话一致性/间隔） | ❌ | — |
| 按置信度触发 执行/追问/Fallback/转人工 | ❌ runtime 目前不消费 confidence | `agents/runtime.py` `_classify_task_type` |
| 意图评测集 | ❌ | — |

结论：**骨架在，缺语料、两级检索、融合公式、动作策略和评测**。

---

## 1. 模块 A：Tool 自适应治理（Day 1–3）

### A1. 设计总览

```
ToolManager.call()
   │ 每次调用结束（成功/超时/异常）
   ▼
GovernanceMetricsSink.record(tool, latency_ms, success/error_type)
   │ 滑动窗口聚合（错误率、延迟均值/P95、z-score）
   ▼
GovernanceController.classify(tool) → Severity (NORMAL/WARN/ERROR/CRITICAL)
   │ 滞回 + 冷却，避免等级抖动
   ▼
GovernancePolicy（按等级的动作参数）
   ├─ 动态 cache_ttl 倍率
   ├─ 动态熔断阈值 / 主动 force_open
   ├─ 每工具并发信号量（Semaphore）
   ├─ stale cache 是否允许兜底
   └─ 是否强制走 fallback
```

### A2. 新增文件

- `mcp/governance.py`
  - `Severity`：`NORMAL / WARN / ERROR / CRITICAL` 四级枚举
  - `ToolWindowStats`：每工具滑动窗口（deque maxlen=60），产出错误率、平均延迟、P95、连续失败数
  - `GovernanceController`：
    - 输入窗口统计 + 复用 `AnomalyDetector` 的 Z-score 思路（或直接组合它）
    - `classify() -> Severity`，带**滞回**（升级立即生效，降级需连续 N 个窗口正常）和**冷却时间**（如 30s 内等级不再上调）
  - `GovernancePolicy`：等级 → 参数映射
  - `GovernanceMetricsSink`：抽象 record 接口，生产用内存实现，后续可换 Prometheus/Redis 实现
- `evaluation/simulate_backend_jitter.py`：3000 请求故障注入模拟（详见 A6）
- `tests/test_governance.py`：单测

### A3. 四级策略表（初值，模拟后标定）

| 等级 | 触发条件（示例初值） | 缓存 TTL | 熔断阈值 | 并发上限 | stale 兜底 | 强制降级 |
|---|---|---|---|---|---|---|
| NORMAL | 错误率<5%，z<2.5 | 1.0× | 5 | 不限 | 保留但不使用 | 否 |
| WARN | 错误率≥10% 或 z≥2.5 | 2.0× | 5 | 8 | 允许读 stale | 否 |
| ERROR | 错误率≥25% 或 z≥3.75 | 4.0× | 3 | 4 | 优先 stale | 否 |
| CRITICAL | 错误率≥50% 或连续失败≥8 | 8.0×（封顶 10×） | force_open | 1 | **stale 直接返回** | 是（无 stale 时直接 fallback） |

恢复方向：每恢复一级，TTL 倍率按 2 倍衰减回基线，解除限流，CR解除 force_open。

### A4. tool_manager.py 改动点（保持向后兼容）

1. `Tool` dataclass 新增字段（都有默认值）：
   - `base_cache_ttl`、`ttl_multiplier=1.0`
   - `semaphore: Optional[asyncio.Semaphore]`（懒初始化，避免注册时无 event loop）
   - `governance_enabled`（由全局 config 控制）
2. `CircuitBreaker` 增强：
   - `threshold` 改为可运行时设置（`update_threshold(n)`）
   - 新增 `force_open()` / `clear_force_open()`
3. 缓存结构改造（stale）：
   - 现在：`_cache[key] = (data, expire_at)`，过期立即删
   - 改为：`(data, expire_at, stale_expire_at)`，过期后移入 `_stale_cache[key]`，stale 保留到 `stale_expire_at = expire_at + ttl`（即双倍 TTL）
   - CRITICAL 且有 stale：返回 `ToolResult(success=True, cached=True, stale=True, data=stale_data)`，**不调用下游**
4. `call()` 流程改造（在现有 缓存→熔断→校验→执行 链路上插入）：
   - 入口：取 controller 当前等级 → 查并发信号量（拿不到令牌时：有 stale 返回 stale，否则走 fallback，error 记 `"并发限流"`）
   - CRITICAL：直接 stale/fallback，不进入执行
   - 执行结束：向 sink 上报；controller 重算等级 → 应用 TTL 倍率 / 熔断阈值
5. 配置开关：`config` 增加 `governance.enabled`，`MCPToolManager.__init__` 注入可选 controller；不传时行为与现在完全一致

### A5. 关键实现注意

- 信号量在 asyncio 下用 `asyncio.Semaphore`，**懒创建**（dataclass 实例化时可能还没有运行中的 loop）
- 所有治理参数变化写日志（等级迁移、限流、stale 命中），模拟报告需要统计这些事件
- ToolResult 增加 `stale: bool = False`、`governed: bool = False` 字段，方便监控统计，不破坏现有调用方
- 统计窗口与监控模块复用概念但不直接 import PerformanceMonitor（后者自带 asyncio 循环和 Prometheus），保持 tool 层可独立测试

### A6. 故障注入模拟（数字的来源，最高优先级）

`evaluation/simulate_backend_jitter.py`：

1. **假后端**：可控的 fake handler
   - 参数化：正常延迟分布（如均值 80ms）、故障窗口（3 段，每段持续 N 秒）内延迟升到 800ms+、错误率升到 60%
   - 固定随机种子（`random.seed / numpy.seed`），两组对照跑同一套请求序列
2. **流量模型**：3000 个请求，泊松到达；混合 read/write 工具（write 不缓存，检验治理不伤害写路径）
3. **两个对照组**：
   - baseline：现状（固定 TTL、阈值 5 的纯熔断、无限流、无 stale）
   - treatment：四级自适应治理
4. **统计指标**（对齐简历）：
   - 无效后端请求数（故障窗口内仍打到下游的请求）→ 目标 **−47%**
   - 用户可用成功率（含缓存/stale/fallback 视为可用）→ 目标 **82% → 94%**
   - P95 端到端延迟 → 目标 **−31%**
5. 输出：`evaluation/reports/governance_simulation_<ts>.json` + Markdown（含故障时间线、两组指标、等级迁移日志摘要）
6. **若达不到目标**：优先调策略表初值（等级阈值、TTL 倍率、并发上限），不要改流量模型凑数字；最终简历数字以报告为准更新

### A7. 验收标准

- [ ] 关闭开关时，现有工具调用行为零变化
- [ ] 单测覆盖：四级迁移、滞回不抖动、限流令牌、stale 返回与过期、force_open
- [ ] 模拟脚本一键可复现（README 写明命令），报告落盘
- [ ] /chat 真实链路开关打开后冒烟通过（退款问答 + 知识检索各 1 轮）

---

## 2. 模块 B：分层检索增强意图路由（Day 4–8）

### B1. 设计总览

```
用户消息 (+ SessionState)
   ▼
Stage 1: Domain 路由
   BM25 + Dense(Chroma) over domain 定义 → RRF → Top-3 域
   ▼
Stage 2: Intent 路由（域内）
   BM25 + Dense over 意图定义 + 历史样例库 → RRF → Top-K=5 候选意图
   ▼
LLM 候选重排/判定（返回 intent + judgment 0~1 + reasoning）
   ▼
final_confidence 融合
   0.35·检索分 + 0.30·LLM判定 + 0.15·实体匹配
   + 0.10·会话一致性 + 0.10·候选间隔(margin)
   ▼
阈值策略
   ≥0.70        → execute（正常执行，intent → task_type → Tool Policy）
   0.45 ~ 0.70  → ask_followup（带候选意图的消歧追问）
   0.25 ~ 0.45  → fallback（降级为现有 IntentRecognizer/UNKNOWN 处理）
   <0.25        → OOD 拒识 → 标记 + 转人工候选
```

### B2. 语料准备（Day 4，工作量最大的一块）

新建 `data/intents/`：

- `domains.jsonl`：10 个域，每行 `{domain_id, name, definition, keywords[]}`
  （售后退款、账单支付、物流、账户、技术故障、订单、产品咨询、投诉升级 …）
- `intents.jsonl`：域下意图，每行
  `{intent_id, domain_id, definition, keywords[], task_type, examples[]}`
  - 每个意图 8–15 条历史样例（口语化、长尾表达优先，从评测集 RAG-018/030/031 那类
    "爆仓""锁定"式真实表达取材）
- `ood.jsonl`：域外表达 30–50 条（闲聊、与售后无关请求），用于标定最低阈值
- 种子来源：现有 IntentRecognizer 的 `INTENT_EXAMPLES`/关键词表、RAG 评测集 query、
  business_db 能触发的场景；不足部分手写补齐
- 注意：简历写"10 类意图、4 档紧急度"，语料规模要与这个口径一致

### B3. 新增文件与复用

- 新建 Chroma collection：`intent_examples`（documents = 定义+样例，metadata = domain_id/intent_id）
  导入脚本 `integrations/knowledge_base/init_intents.py`（仿照现有 init_knowledge.py）
- 新建 `core/intent_router.py`：
  - `IntentCandidate`：intent_id, domain_id, retrieval_score, llm_score, entity_score,
    session_score, margin, final_confidence
  - `IntentRouter`：
    - `_domain_recall(query)`：复用 `retrieval_hybrid.BM25Index` + Chroma query + `reciprocal_rank_fusion`
    - `_intent_recall(query, domains)`：域内两级检索
    - `_llm_judge(query, candidates)`：候选送 LLM，返回每候选判定分（temperature=0，
      参考 `tool_manager._rerank` 的容错解析）
    - `_entity_score`：复用 recognizer 的 `_extract_entities`，候选意图需要的槽位是否在
      消息/session.collected_slots 中出现
    - `_session_score`：候选与 session.task_type / 上轮意图一致则加分
    - `_margin`：top1 与 top2 融合检索分之差（间隔越小越可疑）
    - `route() -> IntentRouteResult(intent, confidence, action_hint, candidates, reasoning)`
- 权重与阈值写在 `config`（初值见 B1 图），**评测后标定**，不在代码里写死

### B4. 接入 runtime（Day 7）

- `agents/runtime.py` `_classify_task_type`：
  - 开关 `intent_routing.enabled=true` 时走 IntentRouter，否则走现有 recognizer
  - 消费 `action_hint`：
    - `ask_followup` → 直接构造 `ASK_FOLLOWUP` 决策（消歧文案带 2–3 个候选）
    - `fallback` → 走现有 recognizer 结果 / UNKNOWN
    - `ood_handoff` → task_type=ESCALATION 或在 session 标记低置信，交 agent 决策
  - intent → task_type 映射复用现有函数；task_type → 工具白名单（TOOL_WHITELIST）
    天然就是简历所说的"Intent 映射任务级 Tool Policy"，不需要新建策略系统
- SessionState 增加 `last_intent`/`last_domain` 字段（给会话一致性打分用，
  按 to_dict/from_dict 兼容方式加默认值）
- 识别结果继续走现有 LRU 缓存（key 用 query 归一化），LLM judge 单独缓存

### B5. 评测（Day 8，与实现同等重要）

新建 `evaluation/run_intent_eval.py` + `evaluation/datasets/intent_eval_v1.json`：

- 标注集 200–300 条：每条 `{query, expected_domain, expected_intent, is_ood}`
  （从语料库留出，不能与 examples 同源泄漏；最好再人工新写 50 条）
- 指标：
  - Domain/Intent 两级 Accuracy、Macro-F1、混淆矩阵
  - OOD 拒识率、误拒率（正常 query 被误判 OOD 的比例）
  - 阈值消融：固定其他权重，扫 final_confidence 阈值，画 execute 准确率-覆盖率曲线
  - 与现有 IntentRecognizer 的**对比基线**（同一标注集，同报告输出）
  - 权重消融：去掉实体分/会话一致性分/margin 各自的影响（证明融合公式每个因子有用）
- 报告落 `evaluation/reports/intent_eval_<ts>.json|md`

### B6. 验收标准

- [ ] 新路由在标注集上的 Intent Accuracy 显著高于现有 recognizer（目标 +10pt 以上，
  实际数字以报告为准）
- [ ] OOD 样本拒识率 ≥90%，正常样本误拒率 ≤5%
- [ ] 每个融合权重因子有消融数据；阈值选择有曲线支撑，不是拍脑袋
- [ ] 开关关闭时 runtime 走旧路径，行为不变
- [ ] 单测：两级检索、融合分计算边界（单候选/平分）、LLM judge 容错、阈值四分支

---

## 3. 排期

| 天 | 内容 | 产出 |
|---|---|---|
| Day 1 | 模块 A：governance.py（窗口统计 + 四级控制器 + 滞回） | 单测通过 |
| Day 2 | 模块 A：tool_manager 改造（限流/stale/动态 TTL/force_open）+ 开关 | 单测 + 冒烟 |
| Day 3 | 模块 A：3000 请求抖动模拟、标定策略表、出报告 | **可复现的三项指标** |
| Day 4 | 模块 B：意图语料整理（domains/intents/ood）+ 导入脚本 | 语料库落盘 |
| Day 5 | 模块 B：intent_router.py 两级检索 + RRF | 检索可手动调试 |
| Day 6 | 模块 B：LLM judge + final_confidence 融合 | 路由可跑通 |
| Day 7 | 模块 B：接入 runtime（action_hint 四分支 + session 一致性） | /chat 端到端 |
| Day 8 | 模块 B：标注评测、阈值/权重标定、消融报告 | 意图评测报告 |
| Buffer | 数字不达标时的调参/补语料时间；更新简历口径 | — |

建议**先做 A 再做 B**：A 不依赖语料质量，2–3 天就能产出简历上最硬的三个量化数字；
B 的成败一半在语料和调参，放在 A 的报告到手之后做风险更小。

## 4. 风险与注意事项

1. **数字诚信**：A 的 47%/94%/31% 和 B 的任何提升都必须能一条命令复现；
   报告与简历口径不一致时，改简历而不是改报告
2. **性能**：意图链路新增多次检索 + LLM judge，延迟可能增加 300–800ms；
   靠 LRU + judge 缓存 + 域内检索控制；评测报告里附平均/P95 延迟
3. **LLM 波动**：judge 用 temperature=0；关键结论跑 3 次取均值（吸取 RAG 评测中
   rewrite 单次 0.27 的教训）
4. **不要过度工程**：Tool Policy 直接复用 TOOL_WHITELIST；治理控制器不引入
   Redis/外部依赖，进程内实现即可（多实例部署的局限作为面试中的未来工作讲）
5. 简历 RAG 条目写"9 个知识域"、此前定稿口径是 10 个域/41 篇文档——动手前统一口径，
   以 `integrations/knowledge_base` 实际导入数据为准
