# RAG Pipeline Evaluation & Tuning Guide

## 1. 配置层次结构

```
ExperimentConfig
├── eval_config (EvalConfig)     # 控制 early-stop 和全局参数
│   ├── min_good_chunks          # 达到后可触发 early-stop
│   ├── hard_timeout_seconds     # 硬超时
│   ├── top_k_chunks             # 最终输出 chunk 数量
│   └── min_per_focus_select     # 每个 focus 最少选择数
│
├── retrieval_overrides          # 控制 source retriever 召回量
│   ├── bailian_rerank_top_n     # Bailian 每个 sub_query 返回数
│   ├── bailian_rerank_min_score # Bailian rerank 分数阈值
│   ├── feishu_top_n_docs        # Feishu 搜索文档数
│   ├── feishu_max_per_doc       # Feishu 每文档最大 chunk 数
│   └── feishu_max_output        # Feishu 总输出上限
│
└── reranker_overrides           # 控制 rerank 阶段
    ├── top_k_chunks             # 覆盖 eval_config.top_k_chunks
    ├── emb_weight               # 语义相似度权重
    └── kw_weight                # 关键词匹配权重
```

## 2. 预设配置说明

### baseline
- **用途**: 生产环境默认配置
- **特点**: 平衡速度和质量，early-stop 启用
- **典型指标**: ~8s, 10-12 chunks, sq_efficiency ~50%

### wide_recall
- **用途**: Phase 1 最大化召回，用于质量迭代基准
- **特点**: 禁用 early-stop (`min_good_chunks=999`)，放大所有瓶颈
- **关键参数**:
  ```python
  hard_timeout_seconds=60.0    # 等待所有任务完成
  bailian_rerank_top_n=15      # vs baseline 10
  feishu_top_n_docs=10         # vs baseline 5
  feishu_max_output=25         # vs baseline 12
  ```
- **典型指标**: ~32s, 24+ chunks, sq_efficiency ~83%

### speed_tuning
- **用途**: Phase 2 收紧参数，监控质量损失
- **特点**: 中等 timeout，适度放大召回
- **关键参数**:
  ```python
  hard_timeout_seconds=15.0
  min_good_chunks=5
  bailian_rerank_top_n=10
  feishu_top_n_docs=6
  ```

## 3. 关键指标解读

### sq_efficiency (Sub-Query Efficiency)
- **定义**: 有返回结果的 sub_query 比例中，chunk 质量达标的比例
- **计算**: `(有效 sub_queries / 非零 sub_queries) * 100%`
- **目标**: ≥ 70%

### sq_zero (Zero-Return Rate)
- **定义**: 零返回的 sub_query 比例
- **分析**: 
  - **高 bailian_zero**: 关键词/tags 策略问题
  - **高 feishu_zero**: SOURCE_GAP（飞书没有该类型内容）或搜索策略问题
- **目标**: ≤ 30%

### Coverage (Sufficiency)
- **定义**: 检索到的 chunks 能覆盖用户问题的程度 (1-5 分)
- **评分标准**:
  - 5: 完全覆盖所有信息需求
  - 3: 覆盖主要信息需求
  - 1: 几乎不能回答问题
- **目标**: ≥ 3/5 通过率 40%+

## 4. 细粒度时间戳分析

trace 文件中 `sq_results[].elapsed_ms` 记录每个 sub_query 的完成时间（相对于 retrieve 阶段开始）：

```python
# 分析 80% chunks 完成时间点
def analyze_80_pct_time(trace):
    sq_sorted = sorted(trace['retrieval']['sq_results'], 
                       key=lambda x: x['elapsed_ms'])
    total = sum(sr['chunk_count'] for sr in sq_sorted)
    target = total * 0.8
    cumsum = 0
    for sr in sq_sorted:
        cumsum += sr['chunk_count']
        if cumsum >= target:
            return sr['elapsed_ms']
```

**典型发现**:
- Bailian 通常 2-4s 完成，贡献稳定 15 chunks/sub_query
- Feishu 可能需要 40+ 秒，但贡献大量 chunks（20-50+）
- 如果 80% 时间点 < 10s，说明 early-stop 可以更激进

## 5. 调优流程

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: 用 wide_recall 建立质量基准                         │
│  · 目标: 确定理论最大 Coverage                               │
│  · 命令: eval run --config wide_recall                      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2: 分析细粒度时间戳                                    │
│  · 找到 80% chunks 时间点                                    │
│  · 识别慢任务是否贡献了关键 chunks                            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 用 speed_tuning 逐步收紧                           │
│  · 调整 hard_timeout → 80% 时间点 + buffer                  │
│  · 监控 Coverage 下降幅度                                    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 4: 优化 Reranker 参数                                 │
│  · 分析 raw_chunks 和 final_chunks 的质量差异                │
│  · 调整 emb_weight/kw_weight 比例                           │
└─────────────────────────────────────────────────────────────┘
```

## 6. Feishu 优化策略

### 问题: Feishu 零返回率高 (60-80%)

**原因分析**:
1. SOURCE_GAP: 飞书没有数据类内容（流量、转化率等）
2. 搜索关键词不匹配文档标题/内容

**优化策略: 关键词预检**

```python
# 在执行 feishu retrieval 前，先快速搜索验证
async def feishu_preflight_check(query: str, keywords: List[str]) -> bool:
    """快速检查飞书是否有相关内容"""
    search_results = await feishu_search(query, page_size=1)
    return len(search_results) > 0

# 如果预检失败，跳过该 feishu sub_query
if not await feishu_preflight_check(sq.query, sq.keywords):
    logger.info("Skipping feishu query (no content): %s", sq.query)
    continue
```

**预期收益**:
- 减少 40-50 秒的无效等待
- 保持 bailian 的快速响应
- 整体延迟从 32s 降至 10-15s

## 7. 常见问题排查

### Q: Coverage 低但 sq_efficiency 高
**A**: Reranker 过滤掉了好的 chunks
- 检查 `raw_chunks_all` vs `final_chunks` 差异
- 降低 `reranker_min_score` 或调整权重

### Q: sq_zero 高
**A**: 分析是哪个 source
- bailian_zero 高 → 检查 tags 策略
- feishu_zero 高 → 检查是否 SOURCE_GAP，考虑预检策略

### Q: 时间过长
**A**: 分析细粒度时间戳
- 找到最慢的 sub_query
- 考虑是否可以并行优化或跳过

## 8. 评估命令速查

```bash
# 运行实验（带进度打印）
python -m eval run --config wide_recall --ids 2,10,22 --output .eval_output/test

# 快速评估 sub_query 效率
python -m eval grade .eval_output/test --graders sq

# 完整评估（含 Coverage，较慢）
python -m eval grade .eval_output/test --graders coverage,sq,chunk

# 对比两次实验
python -m eval compare .eval_output/baseline .eval_output/wide_recall
```
