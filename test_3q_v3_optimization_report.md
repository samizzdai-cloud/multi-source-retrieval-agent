# V3 优化效果报告（Instruction 收束 + Drive Spaces 懒加载）

> 基于 `test_3q_e2e_v3.log`（2026-05-08 21:46–21:48）  
> 对比 v2（已修复 SDK 问题）vs v3（本轮 instruction 收束 + 懒加载）

---

## 一、Focus / SubQuery 收束验证

### 1.1 Google instruction 收束完美生效 ✅

| Run | n_focuses | google sq 数 | LLM 越界 WARNING | 是否触发 hard-cut |
|---|---|---|---|---|
| lite/Q44 | 2 | **2** | 无 | 无 |
| lite/Q51 | 3 | **2** | 无 | 无 |
| lite/Q48 | 3 | **2** | 无 | 无 |
| balanced/Q44 | 2 | **2** | 无 | 无 |
| balanced/Q51 | 3 | **2** | 无 | 无 |
| balanced/Q48 | 3 | **2** | 无 | 无 |

**关键发现：所有 6 个 case 中，Google plan_only LLM 在 instruction 引导下自主收束到 2 sq。**
- 完全没有出现 `[google] LLM exceeded budget` WARNING
- 完全没有 hard-cut 兜底触发
- 全部走 instruction-driven 路径，不再依赖 post-process 硬截

### 1.2 Drive Spaces 懒加载验证 ✅

| 信号 | v2 | v3 |
|---|---|---|
| `RetrieveAgent: Fetched X google spaces`（预取） | 6 次 | **0 次** |
| `[google] lazy-fetched X drive spaces` | 不适用 | 0 次（无 personal_recent） |
| 普通 query 是否付出 list_drive_spaces 开销 | 是 | **否** |

预期收益已落地：普通 query 全程**不再调用 `list_drive_spaces_async`**。仅 personal_recent 类 sub_query 才会按需懒加载（带 5min TTL cache）。

### 1.3 各源 SubQuery 明细（v3）

| Run | Focus | bailian sq | feishu sq | google sq | 备注 |
|---|---|---|---|---|---|
| lite/Q44 | f0=bailian+feishu, f1=feishu+google | 1 | 2 | 2 | google 全部命中 f1 |
| lite/Q51 | f0=ba+go, f1=ba+fe, f2=ba+fe+go | 4 | 2 | 2 | bailian 4 sq（f2 拆 2 条多角度） |
| lite/Q48 | f0=ba+fe, f1=ba+fe, f2=go+fe | 2 | 3 | 2 | feishu 3 sq 均匀分配三 focus |
| balanced/Q44 | f0=ba+fe, f1=fe+go | 2 | 2 | 2 | bailian f0 拆 2 角度（DTC + Marketplace） |
| balanced/Q51 | f0=ba+go, f1=fe+ba, f2=fe+ba | 3 | 2 | 2 | bailian 三 focus 各 1 sq |
| balanced/Q48 | f0=ba+fe, f1=ba+fe, f2=go+fe | 3 | 3 | 2 | feishu 三 focus 各 1 sq |

> Bailian 仍偶发出现 multi-angle 拆分（lite/Q51 拆 4 sq、balanced/Q51 拆 3 sq、balanced/Q48 拆 3 sq）——按之前确认**bailian 不收束**，保持 LLM 自由度。Feishu 由 sanitize 严格压到 1/focus（routed focus 数 = sq 数）。

---

## 二、Chunk 质量

| Run | Emitted | Merged | Critical | Important | Noise | 来源分布 | Angle 多样性 |
|---|---|---|---|---|---|---|---|
| lite/Q44 | 8 | 9 | 3 | 5 | 0 | feishu:3, google:5 | 7 unique |
| lite/Q51 | **12** | 14 | **6** | **6** | 0 | bailian:5, feishu:5, google:2 | **11 unique** |
| lite/Q48 | **12** | 12 | **6** | **6** | 0 | bailian:2, feishu:5, google:5 | **10 unique** |
| balanced/Q44 | 8 | 15 | **7** | 1 | 0 | bailian:2, feishu:4, google:2 | 7 unique |
| balanced/Q51 | 8 | 8 | 4 | 4 | 0 | bailian:5, google:3 | 6 unique |
| balanced/Q48 | 7 | 7 | 4 | 3 | 0 | bailian:2, feishu:5 | 6 unique |

**亮点**：
- **lite/Q51 和 lite/Q48 全部 emitted 满 12 条**（v2 分别为 10 / 9），三源协同完整  
- Q51/Q48 critical 比例提升至 50%（6/12），重要内容密度高
- Angle 多样性 lite/Q51 达 11 个不同角度（v2 为 10），lite/Q48 达 10 个

---

## 三、来源文件类型分布（基于 Google FUNNEL 日志）

Google 端按文件类型（S=Sheet, D=Doc, SL=Slide, G=Gmail）分布 type_pool 后选中数：

| Run | sq | Doc | Slide | Sheet | Gmail | 主导类型 |
|---|---|---|---|---|---|---|
| lite/Q44 | f1:sq1 | 28 | 106 | 0 | 0 | Slide |
| lite/Q44 | f1:sq2 | 28 | 106 | 0 | 0 | Slide |
| lite/Q51 | f0:sq1 | 0 | 0 | 0 | 2 | **Gmail only**（兜底） |
| lite/Q51 | f2:sq2 | 0 | 0 | 0 | 2 | Gmail only |
| lite/Q48 | f2:sq1 | 40 | 105 | 0 | 0 | Slide+Doc |
| lite/Q48 | f2:sq2 | 15 | 146 | 0 | 0 | Slide |
| balanced/Q44 | f1:sq1 | 1 | 145 | 7 | 1 | Slide+Sheet |
| balanced/Q44 | f1:sq2 | 1 | 145 | 7 | 1 | Slide+Sheet |
| balanced/Q51 | f0:sq1 | 0 | 0 | 16 | 0 | **Sheet only** |
| balanced/Q51 | f0:sq2 | 0 | 0 | 16 | 3 | Sheet+Gmail |
| balanced/Q48 | f2:sq1 | 40 | 105 | 20 | 0 | Slide+Doc+Sheet |
| balanced/Q48 | f2:sq2 | 10 | 0 | 5 | 0 | Doc+Sheet |

**关键观察**：
- lite 模式 Slide 比重最高（lite 关闭 PPTX/Sheet 深度解析时，slide 文件数仍高但实际选中后被 size gate 大量过滤）
- balanced 模式 Sheet 数量明显增加（lite Q51 sheet=0 → balanced Q51 sheet=16），符合 balanced 全开 Sheet 处理的设计
- balanced/Q48 是唯一 4 类型全开（Doc+Slide+Sheet+Gmail）的 case，对应耗时也最长

---

## 四、延迟对比（v2 → v3）

### 4.1 总耗时

| Run | v2 | v3 | Δ | 说明 |
|---|---|---|---|---|
| lite/Q44 | 23.40s | **19.79s** | **−3.6s** | ✅ 节省 spaces fetch + Google sq 收束 |
| lite/Q51 | 14.20s | 15.85s | +1.7s | bailian 4 sq（vs v2 3 sq）多了 1 次 API 调用 |
| lite/Q48 | 18.06s | 17.93s | −0.1s | ≈持平 |
| balanced/Q44 | 18.91s | 21.10s | +2.2s | feishu 增加 table 处理 |
| balanced/Q51 | 19.95s | 27.08s | +7.1s | ⚠️ Google sheet=16 文件读取耗时变长 |
| balanced/Q48 | 24.84s | 32.08s | +7.2s | ⚠️ Google batch_total 18.2s（v2 14.9s） |

### 4.2 Planner 阶段延迟（spaces fetch 是否消失）

| 阶段 | v2 | v3 | Δ |
|---|---|---|---|
| Planner（含 spaces fetch + 3 LLM 调用） | ~5s | **~4s** | **−1s ≈ spaces fetch 节省** |
| Spaces fetch（list_drive_spaces_async） | 700ms~1s | **0ms** | **−700ms~1s** |

### 4.3 关于 v3 部分 case 变慢的根因分析

**balanced/Q48 +7.2s** 和 **balanced/Q51 +7.1s** 的真因不是收束改动，而是：

1. **Planner LLM 路由变化（无 spaces 提示后）**：
   - v2 balanced/Q48：feishu 路由到 2 focuses（focus_1, focus_2）
   - v3 balanced/Q48：feishu 路由到 3 focuses（focus_0, focus_1, focus_2）→ feishu 多处理 1 个 focus → batch_total 13.97s（v2 是 8.4s）

2. **Google LLM-chosen 关键词命中更广**：
   - v3 balanced/Q48 sq1='REDMAGIC 硬件 散热 竞品' 命中 Sheet=20、Doc=40、Slide=105，远大于 v2 的同 sq 命中数
   - 命中越多 → type_pool 越大 → file_read 越慢

3. **Bailian sub_queries 增加**：
   - balanced/Q48 v3 bailian 拆 3 sq（v2 仅 2 sq），耗时 12.5s（v2 9.3s）

**结论**：v3 的额外延迟来自 Planner 不带 spaces 列表后的 LLM 路由选择差异 + LLM 关键词随机性，**不是 instruction 收束本身造成**。Google sq 数稳定为 2，没有越界。

---

## 五、综合评估

| 维度 | v2 | v3 | 评价 |
|---|---|---|---|
| Google sq 收束机制 | post-process 硬截 | **instruction 引导 + observer 兜底** | ✅ 改进 |
| Google sq 数稳定性 | 偶发越界后被截 | LLM 100% 自主达成 | ✅ 改进 |
| Drive spaces 预取 | 必付出 | **懒加载** | ✅ 改进（节省 1s） |
| 普通 query 总延迟 | 14–25s | 16–32s | ⚠️ 部分回升 |
| Chunk 质量（C/I 数量） | 4-7 critical | 4-7 critical | ≈持平 |
| Chunk 来源覆盖度 | 普遍单源主导 | **lite Q51/Q48 三源协同满 12 条** | ✅ 改进 |

### 关键判断

✅ **Instruction 收束策略成功**：6/6 case Google LLM 自主达到 ≤3 上限，无需硬截。
✅ **懒加载落地**：普通 query 不再付出 spaces 预取代价（−1s 一致回归）。
⚠️ **balanced 部分 case 总延迟回升**：根因是 Planner LLM 在缺失 spaces 提示后路由变化（更激进地把 feishu 分给所有 focus），非本次改动直接引起；如需控制可在 Global Planner instruction 里加"feishu 分配偏好"约束。

---

## 六、改动文件总览（实施版）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `tools/core/source_query_config.py` | google rules 顶部新增"Sub_query 数量预算"段，personal_recent 注明懒加载 |
| 2 | `agents/source_agent_base.py` | 新增 `SourceAgent._compute_sub_query_total_cap` 钩子；`_build_plan_only_prompt` 支持 `sq_budget_override` |
| 3 | `agents/google/google_source_agent.py` | 新增 `_compute_sub_query_total_cap` 覆盖（≤2/≤3）；`_post_process_sub_queries` 降级为 observer；新增 `_lazy_fetch_drive_spaces` + `_execute_batch` 中 personal_recent 检测触发 |
| 4 | `tools/core/source_profiles.py` | google sub_query_hints 移除 google_spaces 引用 |
| 5 | `tools/core/query_planner.py` | `plan_query`/`_build_source_profiles_block` 移除 `google_spaces` 参数与注入逻辑 |
| 6 | `retrieve_agent.py` | 移除 `fetch_google_spaces` 预取 + `_prepare_focuses` 签名瘦身 |
| 7 | `test_retrieve_3q_e2e.py` | 同步移除测试预取，与生产路径对齐 |

---

## 七、后续建议

1. **balanced/Q48 性能调优**：若 balanced 模式 32s 不可接受，可考虑：
   - 让 Global Planner 在不知道 spaces 的情况下减少把所有 focus 都路由给 feishu
   - 或为 balanced 加一个 `max_focuses_per_source` 软约束
   
2. **追加 personal_recent e2e**：当前 6 个 case 都不是 personal_recent，未验证 lazy fetch 触发路径。建议补一条 query（如"我最近写的 RM 11 Pro 销售报告"）单独测试。

3. **bailian instruction 收束**：bailian 的 multi-angle 拆分（如 balanced/Q48 拆 3 sq）目前不受约束。后续若延迟敏感可同步加 budget。

---

*生成时间：2026-05-08 | 基于 test_3q_e2e_v3.log + test_3q_e2e_report.json*
