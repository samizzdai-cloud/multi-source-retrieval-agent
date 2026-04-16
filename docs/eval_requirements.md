**评估与召回质量需求 / Evaluation & Retrieval Requirements**

概述（中文）：
- 目标：在不引入 LLM 推理环节的前提下，提高召回质量（Recall / Coverage / Sufficiency），同时维持或提升检索排序的有效性（nDCG@10）。
- 优先级：保证数据源稳定性与候选池完整性（高），再持续优化融合与重排序策略（中）。

Overview (English):
- Goal: Improve recall/coverage/sufficiency while preserving ranking quality (nDCG@10). Do not add LLM reranking steps for now.
- Priority: source reliability & candidate completeness first, then fusion/reranker tuning.

关键指标 / Key metrics (track per-run and per-query-type):
- mean_nDCG@10 — 目标保留或提升（基线 v1: ~0.862）
- candidate_recall@K — 候选池是否包含金标证据（K=100/500）
- coverage / sufficiency_ratio — trace 中 sufficiency 平均（基线 v1: ~0.33）
- funnel_rate = final_chunk_count / raw_chunk_count（越高越好代表召回->选取链路保留度高）
- per-source reliability: api_error_rate, missing_chunk_rate, avg_chunks_per_doc

接受准则（示例）：
- mean_nDCG@10 >= 0.86
- mean_sufficiency >= 0.30
- candidate_recall@100 >= baseline（由离线 gold set 计算）

工程与配置项（可试验参数）：
- `source_weight`, `emb_weight`, `kw_weight`（混合得分权重）
- `feishu_max_per_doc`, `feishu_table_budget`, `feishu_max_output`（来源/表格预算）
- normalization: per-source global vs per-group（影响分布与 diversity）
- per-doc cap（max chunks per document）和 per-source cap

运行与回归测试：
- 每次重要改动做三份评估：baseline vs candidate-change vs control（相同 query set）。
- 自动化 smoke tests：确保 Feishu readers 不抛 99991400/131005 类错误并保证最小候选数。
- 把所有 `.eval_output/<experiment>/` 留存为可比的运行快照（config.json, experiment_result.json, traces/）。

数据与标注要求：
- 创建并维护金标集合（覆盖表格查询、数值/时间、实体抽取等多种 query 类型）。
- 为每种 query 类型记录期望的最小证据数与示例（用于 sufficiency 指标）。

参考（Selected references for methods）：
- Dense retrieval (DPR): Karpukhin et al., 2020 — 用于双编码语义检索和扩展候选池（arXiv:2004.04906）。
- Late-interaction models (ColBERT): 高质量的 token-level late-interaction 检索，适合域内训练与高召回（ColBERT repo/docs）。
- Pseudo-Relevance Feedback (RM3): 经典的查询扩展方法，可用于扩展检索候选。参考 Pyserini/Anserini 实现文档。
- Table understanding (TaPas): 表格语义解析/编码，用于改善表格型候选的表达与检索（arXiv:2004.02349）。
- Calibration methods (Platt / Isotonic / Temperature): 用于把不同来源的 raw scores 校准为可比概率（see scikit-learn calibration docs）。

实践要点（短清单）：
1. 先稳健化来源：共享 RateLimiter、实例级 semaphore、退避重试、缓存与增量同步。
2. 扩大候选池同时保证多样性：BM25 ∪ table-specific ∪ embedding ANN；按 source/doc cap 做预算分配。
3. 保留并校准 `source_score` 元数据（用于后续学习或规则融合），用 isotonic/platt 对 source_score 做 per-source 校准。
4. 将表格处理升级为“表格优先”：保留表头、生成表格级与行级 chunk、给表格 chunk 打 `table_bonus` 特征。
5. 做小型有标签 reranker（可用 LR/GBDT），以合成特征（source_score, sim_emb, focus_overlap, table_bonus）学习排名与阈值。
6. 在评估中分层统计（table / numeric / temporal / entity / free-text），确保针对弱点有定向改进。

负责人与下一步：
- Retrieval: 实现 DPR/ColBERT PoC（小规模索引）并做候选召回对比（Owner: Retrieval）
- Readers: 完善 Feishu 退避/缓存与表格解析（Owner: Data Engineering）
- Eval: 扩展金标并把回归阈值加入 CI（Owner: QA / Evaluation）
