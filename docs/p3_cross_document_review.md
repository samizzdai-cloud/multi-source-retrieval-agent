# P3 跨文档 Gold Evidence Chain 人工审核包

审核范围仅限跨文档 case；单文档、跨 chunk 和数据缺失控制由确定性规则验证。

审核人不需要评价最终答案文风。请只决定这条 case 是否可以成为
`retrieve_agent -> FeishuSourceAgent` 的检索金标。

## 统一审核标准

每个 case 必须同时满足以下条件才可 **批准**：

1. **Query 合法**：问题由允许的文档范围完整支持，不依赖 Answer Agent、外部 MCP 或未提供数据。
2. **实体一致**：两份文档都以 Hypershell 为主业务实体，而非仅偶然提及。
3. **时间关系有效**：同一经营周期，或能明确说明“策略定义/策略基线 -> 本期执行结果”的关系。
4. **角色不可替代**：每份文档在 chain 中承担独立角色；移除任一文档后，query 不能完整完成。
5. **最小性**：保留实现该关系所需的最少 chunk 与锚点；同一事实的重复锚点应删除。
6. **不越界**：不要把“可能原因、建议补数或业务推测”误标为已证实的事实；此类内容必须写作 `upstream_evidence_needed`。

批准时，请同时确认保留的 `resource_ref` / role。拒绝时，请选用以下原因：

- `not_cross_document`：一个文档已足够，另一个只是重复或装饰；
- `invalid_join`：实体、期间、口径或策略—执行关系不成立；
- `not_minimal`：链过长或存在重复锚点，但可修改后重审；
- `query_too_broad`：问题没有明确的可检索合同；
- `unsupported_inference`：链把推测当作已证实事实；
- `other`：请说明。

---

## Review A — pilot_02

**当前状态：待审核**

### Query

> 帮我看一下 Hypershell 最近策略和结果

### 允许范围与预期

- 类型：`cross_document`
- 预期：`sufficient_evidence`
- 文档 A：`OvjXdowbkoFv05xIbJycL13PnSc` — *Hypershell 新品 GTM 深度研究报告*
- 文档 B：`PbD8dNl0wooNiIxA9pRcszu6nHS` — *HYPERSHELL 业务总结报告 (2026-06-15 ~2026-06-21)*
- 连接关系：策略/SKU 角色与行动路线图 → 同期经营结果与行动建议

### 当前模型链

| Ref | 当前角色 | 用途 | 审核建议 |
|---|---|---|---|
| `Ovj...:C1` | SKU Role Diagnosis | 定义 Hero、Conversion、Clearance SKU 的策略角色 | **建议保留**：策略定义角色 |
| `Ovj...:C2` | 90-Day Action Plan | 定义后续素材、LP、预算、清库等行动 | **待决定**：可能与 C1 同属策略侧，未必是最小链必需项 |
| `PbD...:C17` | Weekly Results & Actions | 本周流量、营收、新品/清库问题与行动建议 | **建议保留**：实际经营结果角色 |

### 审核焦点

1. 这条宽泛 query 是否仍足够明确，能稳定要求“策略定义 + 本期结果”两类证据？
2. C1 + C17 是否已足够；若足够，请删除 C2，避免把同一策略侧信息重复记为 Gold。
3. GTM 报告与周报是否构成可接受的策略—执行关系，即使它们都引用同一经营周期？

### 建议结论

**建议：条件批准，链缩减为 `Ovj...:C1` + `PbD...:C17`。**

理由：两份文档角色互补；但 C2 对“最近策略和结果”不是显然不可替代。

### 审核记录（请填写）

- [ ] 批准，保留 C1 + C17
- [ ] 批准，保留 C1 + C2 + C17，并说明 C2 不可替代原因：
- [ ] 拒绝，原因代码：
- 审核人 / 日期：
- 备注：

---

## Review B — pilot_03

**当前状态：待审核**

### Query

> 结合《Hypershell 新品 GTM 深度研究报告》中的策略/产品角色定义与《HYPERSHELL 业务总结报告 (2026-06-15 ~2026-06-21)》中的本期经营表现，哪些策略假设已被执行数据支持，哪些仍需补充上游证据？

### 允许范围与预期

- 类型：`cross_document`
- 预期：`sufficient_evidence`，但推测性原因必须输出为 `upstream_evidence_needed`
- 策略文档：`Ovj...:C1`，含 Ultra S / Pro S / X Pro 的 SKU 角色与运营原则
- 执行文档：`PbD...:C18`，含 Ultra S 型号搜索、Pro S 异常下滑与库存排除；`PbD...:C17`，含 X Pro DOS、折扣与销量变化

### 当前证据配对

| 策略锚点 | 执行锚点 | 审核应确认的关系 |
|---|---|---|
| Ultra S = Hero SKU，承担型号教育 | Ultra S 社媒销量可观，但型号搜索展现 347，低于旧款 1158 | 支持“型号心智仍弱”；不能证明具体素材/LP 是唯一原因 |
| Pro S = Conversion SKU，先修复 LP/PDP、搜索承接与定价表达 | 第 5 周销量 -43%，DOS 67 天，基本排除断货 | 支持“需诊断承接/扶持问题”；预算、热力图和具体原因仍需上游证据 |
| X Pro = Clearance SKU，采用独立促销/清库策略 | DOS 124 天；优惠缩减 36.6%，净销量回落 25.7% | 支持“清库对促销敏感”；促销缩减的决策原因仍是上游证据缺口 |

### 审核焦点

1. 上述三对是否均是“策略定义 + 实际表现”的不可替代关系？
2. 同一 `resource_ref` 出现多次是为了表达不同 SKU 事实；最终 manifest 应保留多个锚点，但 runtime 只要求恢复相应的 3 个 distinct chunks。
3. 不允许把“预算被抽离”“LP 有问题”“为什么缩减促销”记为已证实结论；它们必须是 `upstream_evidence_needed`。
4. 如果认为三对事实过多、导致 query 不够最小，可批准其中两对并将 query 收窄；不要保留无法用最小链支持的泛化要求。

### 建议结论

**建议：批准。**

理由：三组策略假设和同期结果分别来自两份文档，角色清晰；链中的缺失原因已被显式界定为上游证据，而不是事实结论。

### 审核记录（请填写）

- [ ] 批准，保留三组 SKU 配对
- [ ] 批准但缩减为：
- [ ] 拒绝，原因代码：
- 审核人 / 日期：
- 备注：

---

## 审核后的处理规则

| 你的决定 | 后续动作 |
|---|---|
| 批准 | 将 `review_status` 设为 `approved`，写入 promoted benchmark；才允许三轮 retrieve-only。 |
| 条件批准 / 缩减 | 更新 Gold Chain，重跑本地 anchor/hash/minimality 校验，再写入 benchmark。 |
| 拒绝 | 从试冻结集中移除；若仍需保持 8 条门槛，回到 Resource Map 生成替代 query，不能降低跨文档标准。 |
