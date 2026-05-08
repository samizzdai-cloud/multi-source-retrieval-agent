# Focus / SubQuery 生成分析报告

> 基于 `test_3q_e2e_v2.log`（2026-05-08 21:11–21:13，SDK 修复后首次完整跑通）  
> 3 queries × lite + balanced × bailian + feishu + google

---

## 一、Global Planner Focus 分发

| Run | Focuses | focus_0 text | targets | focus_1 text | targets | focus_2 text | targets |
|---|---|---|---|---|---|---|---|
| lite/Q44 | 2 | REDMAGIC 11 Pro 各站点首发销量数据及市场表现 | bailian,feishu,google | REDMAGIC 11 Pro DTC 官网渠道销售转化、流量及运营数据 | feishu,google | — | — |
| balanced/Q44 | 2 | REDMAGIC 11 Pro 各站点首发阶段销量数据与市场表现 | feishu,google | REDMAGIC 11 Pro DTC 官网渠道销售转化与运营表现 | feishu,google | — | — |
| lite/Q51 | 3 | nubia 近6个月发布的核心硬件产品清单及技术规格 | bailian,google | nubia 近6个月销售渠道布局与业务运营模式 | bailian,feishu | nubia 近6个月销售额、GMV 及财务统计 | feishu,google |
| balanced/Q51 | 3 | nubia 近6个月核心硬件产品清单及功能卖点 | bailian,google | nubia 近6个月销售渠道布局与业务模式 | bailian,feishu | nubia 近6个月销售额、GMV 及财务表现 | feishu,google |
| lite/Q48 | 3 | REDMAGIC 游戏手机核心竞争对手及市场份额格局 | bailian,google | REDMAGIC 品牌用户知名度、美誉度及认知标签 | feishu,google | REDMAGIC 与黑鲨/ROG 产品性能与营销对比 | bailian,feishu |
| balanced/Q48 | 3 | REDMAGIC 游戏手机核心竞争对手及市场份额格局 | bailian,google | REDMAGIC 品牌用户知名度、美誉度及认知标签 | feishu,google | REDMAGIC 与黑鲨/ROG 产品性能与营销对比 | bailian,feishu |

### 规律 & 问题点

| 观察 | 结论 |
|---|---|
| Q48 lite/balanced 两档 planner 输出完全一致（focus 文本和 targets 相同） | **正常** — query 特征明确，planner 稳定 |
| Q51 lite/balanced focus 文本略有差异（balanced 更简练）但结构一致 | **正常** — LLM 随机性导致的小差异，语义等价 |
| Q44 lite：focus_0 targets=`[bailian,feishu,google]`（bailian 被包含） | **偏差** — balanced 下 planner 把 focus_0 targets 缩为 `[feishu,google]`，导致 bailian 被跳过（`no focus routed`）。两档行为不一致，lite 更合理 |
| Q44 bailian 始终 0 chunks（即使被路由） | **正常** — nubia 索引没有 REDMAGIC 销量/DTC 运营流水，属索引内容边界 |

---

## 二、各源 SubQuery 明细

### 2.1 Bailian

| Run | Focus | Tags | Index | SubQuery text | 结果 |
|---|---|---|---|---|---|
| lite/Q44 | focus_0 | Product_Launch_Management, 2026_Q2 | nubia | REDMAGIC 11 Pro 在各国家/地区站点的首发销量数据和市场表现情况 | **0 chunks**（索引无此类数据） |
| lite/Q51 | focus_0 | Product_Launch_Management, 2026_Q2 | nubia | nubia 在 2025年11月至 2026年5月期间发布的核心硬件产品有哪些及其关键技术规格？ | ✓ |
| lite/Q51 | focus_1 | Market_Intelligence, 2026_Q2 | nubia | nubia 最近半年在全球市场的销售渠道布局是怎样的？ | ✓ |
| lite/Q51 | focus_1 | Marketplace | nubia | nubia 目前在各个区域市场的核心业务运营模式如何开展？ | ✓ |
| lite/Q48 | focus_0 | Market_Intelligence, last_long | nubia | REDMAGIC 品牌在游戏手机市场的核心竞争对手有哪些，近期市场份额分布格局如何？ | **0 chunks** |
| lite/Q48 | focus_2 | Market_Intelligence | nubia | REDMAGIC 与主要竞品如黑鲨、ROG 在产品性能上的对比分析。 | ✓ (2 chunks) |
| lite/Q48 | focus_2 | Market_Intelligence | nubia | REDMAGIC 品牌在游戏手机领域的营销策略与黑鲨、ROG 等的对比分析。 | ✓ |
| balanced/Q44 | — | — | — | （no focus routed，planner 未分配 bailian） | skip |
| balanced/Q51 | focus_0 | Product_Launch_Management, 2026_Q2 | nubia | nubia品牌在2025年11月至2026年5月期间发布的核心硬件产品名称及功能卖点？ | ✓ |
| balanced/Q51 | focus_1 | DTC, 2026_Q2 | nubia | nubia品牌在2025年11月至2026年5月期间全球销售渠道布局和业务模式？ | ✓ |
| balanced/Q48 | focus_0 | Market_Intelligence, last_long | nubia | REDMAGIC 品牌在游戏手机市场的核心竞争对手有哪些，近期市场份额如何？ | **0 chunks** |
| balanced/Q48 | focus_2 | Market_Intelligence | nubia | REDMAGIC 与黑鲨/ROG 在游戏手机产品性能上的核心差异及优势？ | ✓ (3 chunks) |
| balanced/Q48 | focus_2 | PR, last_long | nubia | REDMAGIC 与黑鲨/ROG 品牌营销推广策略的主要区别？ | ✓ |

**Bailian sub_query 计数：**

| Run | Focuses 路由到 bailian | 实际 sub_queries | 期望（1 sq/focus） | 差异 |
|---|---|---|---|---|
| lite/Q44 | 1（focus_0） | 1 | 1 | ✅ |
| lite/Q51 | 2（focus_0, focus_1） | 3 | 2 | ⚠️ focus_1 生成了 2 条 |
| lite/Q48 | 2（focus_0, focus_2） | 3 | 2 | ⚠️ focus_2 生成了 2 条 |
| balanced/Q51 | 2（focus_0, focus_1） | 2 | 2 | ✅ |
| balanced/Q48 | 2（focus_0, focus_2） | 3 | 2 | ⚠️ focus_2 生成了 2 条（不同角度） |

> Bailian 没有类似 Google 的 `_post_process_sub_queries` cap。对于竞品/对比类 focus，LLM 主动拆出性能维度 + 营销维度两条 sq，属于**有意的多角度覆盖**，但会增加 API 调用数（从 1→2）。考虑是否需要对 bailian 也加 per-focus cap。

---

### 2.2 Feishu

所有运行中 feishu sanitize 始终将每个 focus 压缩到 1 条 sq（`before→after`），总量 = 被路由的 focus 数。

| Run | 路由 focuses | 产出 sq | sanitize 后 | 正常？ |
|---|---|---|---|---|
| lite/Q44 | focus_0, focus_1 | 3→2 | ✅ | ✅ |
| lite/Q51 | focus_1, focus_2 | 2→2 | — | ✅ |
| lite/Q48 | focus_1, focus_2 | 3→2 | ✅ | ✅ |
| balanced/Q44 | focus_0, focus_1 | 3→2 | ✅ | ✅ |
| balanced/Q51 | focus_1, focus_2 | 2→2 | — | ✅ |
| balanced/Q48 | focus_1, focus_2 | 4→2 | ✅ | ✅ |

**Feishu 各 sub_query 文本：**

| Run | sq | Focus | qtype | 检索词 |
|---|---|---|---|---|
| lite/Q44 | sq1 | focus_0 | structured | REDMAGIC 11 Pro 销量 表现 |
| lite/Q44 | sq2 | focus_1 | structured | REDMAGIC 11 Pro 销售 转化 |
| lite/Q51 | sq1 | focus_1 | concept | nubia 市场 策略 |
| lite/Q51 | sq2 | focus_2 | structured | nubia 销售 表现 |
| lite/Q48 | sq1 | focus_1 | concept | REDMAGIC 品牌 市场 |
| lite/Q48 | sq2 | focus_2 | concept | REDMAGIC 竞品 营销 |
| balanced/Q44 | sq1 | focus_0 | structured | REDMAGIC 11 Pro 销售 市场 |
| balanced/Q44 | sq2 | focus_1 | structured | REDMAGIC 11 Pro 销售 转化 |
| balanced/Q51 | sq1 | focus_1 | concept | nubia 市场 策略 |
| balanced/Q51 | sq2 | focus_2 | structured | nubia 销售 表现 |
| balanced/Q48 | sq1 | focus_1 | concept | REDMAGIC 市场 表现 |
| balanced/Q48 | sq2 | focus_2 | concept | REDMAGIC 产品 营销 |

> Feishu 检索词偏短（3 token），属结构化倒排检索优化，合理。Q51/Q48 的 feishu focus_0（nubia 产品规格 / REDMAGIC 竞品格局）因 targets 里不含 feishu，故 feishu 未处理，正常。

---

### 2.3 Google

`_post_process_sub_queries`（Codex 修复）在所有多 focus 运行中均生效：`per_focus_cap=1` 压到每 focus 最多 1 条。

| Run | Focuses 路由到 google | LLM 产出 | sanitize 后 | 正常？ |
|---|---|---|---|---|
| lite/Q44 | focus_0, focus_1 | 2 | — (无需裁剪) | ✅ |
| lite/Q51 | focus_0, focus_2 | 2 | — | ✅ |
| lite/Q48 | focus_0, focus_1 | 3→2 | ✅ before={f0:1,f1:2}→after={f0:1,f1:1} | ✅ |
| balanced/Q44 | focus_0, focus_1 | 4→2 | ✅ before={f0:2,f1:2}→after={f0:1,f1:1} | ✅ |
| balanced/Q51 | focus_0, focus_2 | 4→2 | ✅ before={f0:2,f2:2}→after={f0:1,f2:1} | ✅ |
| balanced/Q48 | focus_0, focus_1 | 4→2 | ✅ before={f0:2,f1:2}→after={f0:1,f1:1} | ✅ |

**Google 各 sub_query 文本 + Drive 命中：**

| Run | sq_id | 检索词 | doc | slide | sheet |
|---|---|---|---|---|---|
| lite/Q44 | focus_0:sq1 | REDMAGIC 11 Pro 销量 市场表现 | 0 | 6 | 0 |
| lite/Q44 | focus_1:sq2 | REDMAGIC 11 Pro DTC 转化 流量 | 0 | 6 | 0 |
| lite/Q51 | focus_0:sq1 | nubia 硬件 产品 规格 | 1 | 1 | 0 |
| lite/Q51 | focus_2:sq2 | nubia 销售额 GMV 报表 | 1 | 1 | 0 |
| lite/Q48 | focus_0:sq1 | REDMAGIC 游戏手机 竞争对手 市场份额 | 8 | 1 | 0 |
| lite/Q48 | focus_1:sq2 | REDMAGIC 用户画像 品牌知名度 认知标签 | 0 | 3 | 0 |
| balanced/Q44 | focus_0:sq1 | REDMAGIC 11 Pro 销量 市场表现 | 1 | 6 | 1 |
| balanced/Q44 | focus_1:sq3 | REDMAGIC 11 Pro DTC 转化率 | 1 | 6 | 3 |
| balanced/Q51 | focus_0:sq1 | nubia 硬件 产品卖点 | 0 | 0 | 3 |
| balanced/Q51 | focus_2:sq3 | nubia 销售额 GMV 财务 | 1 | 0 | 5 |
| balanced/Q48 | focus_0:sq1 | REDMAGIC 竞品 市场份额 | 5 | 6 | 5 |
| balanced/Q48 | focus_1:sq3 | REDMAGIC 品牌认知 标签 | 1 | 5 | 0 |

> lite/Q44 Google 两条 sq 命中均为 0 doc + 0 sheet，只有 slide，经 size gate 过滤后最终 0 chunks，消耗 11s 空跑。这是 Google Drive 里 REDMAGIC 11 Pro 首发销量类数据以 Sheet 为主（但在 lite 模式 sq 搜索词偏向文档语义），改善方向：为该类 query 增加时间过滤或提升 sheet 搜索权重。

---

## 三、生成合理性综合判断

| 组件 | 机制 | 多 focus 下 sq 数 | 是否符合预期 | 备注 |
|---|---|---|---|---|
| Global Planner | Gemini Flash LLM | 2–3 focuses | ✅ 结构稳定 | Q44 bailian 路由在 lite/balanced 间偶发不一致 |
| Feishu plan_only | LLM + sanitize cap | 始终 = n_focuses_routed | ✅ | sanitize 稳定收敛 |
| Google plan_only | LLM + `_post_process_sub_queries` cap | 始终 = n_focuses_routed（cap=1/focus） | ✅ | Codex 修复生效，balanced 从 4 sq 压到 2 sq |
| Bailian plan_only | LLM，无 cap | 有时 = n_focuses，有时 n_focuses+1 | ⚠️ 偶发超出 | focus_2 竞品对比类拆出 2 sq（性能+营销），属多角度设计；可选加 cap |

---

## 四、残留问题

1. **Q44 bailian 路由不稳定**：lite 路由到 focus_0（bailian+feishu+google），balanced 不路由（feishu+google）。planner 对"首发销量"是否属于 bailian 知识范围的判断在两档之间存在差异。建议在 query_planner prompt 里明确 bailian 的索引内容描述。

2. **lite/Q44 Google 空跑 11s**：2 sq 均只命中 slide，size gate 后全部丢弃。可考虑对 lite 模式增加"无 sheet 命中时提前退出 slide 处理"的短路逻辑。

3. **balanced/Q48 Google 14.9s**：2 sq × (5 docs + 11 slides + 5 sheets)，其中 sheet 处理 phase_b 最慢（metadata_prefetch 1.7s + 读取）。为正常工作量，但已触及 lite/balanced 约 15s 的 acceptable 上限。

---

*生成时间：2026-05-08 | 基于 test_3q_e2e_v2.log*
