"""Chunk-level diagnostic with 4 dimensions:

  1. doc 召回:    target doc 是否在返回中 (target_doc_id_substr ⊆ chunk_id)
  2. chunk 召回:  含答案的关键 chunk 是否被命中 (expected_keywords ⊆ chunk text)
  3. tier 准确:   含答案的 chunk 是否被赋予 critical 或 important
  4. retrieve 延迟

不评 answer_agent (answer 只是基于 chunks 输出)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CACHE = Path(__file__).parent / "cache_feishu"

# 把 chunk 判定为"含答案"的阈值: 至少有这么多比例的 expected keywords 出现在 chunk 文本中
ANSWER_BEARING_KEYWORD_RATIO = 0.5    # 50% 以上 keywords 出现 → 算 answer-bearing
FULL_ANSWER_KEYWORD_RATIO = 1.0       # 100% 出现 → 算 full chunk hit


import re


def _tokenize(text: str) -> set:
    """简单中英混合分词:连续 ASCII / 单字中文 + 数字。返回小写 token 集合。"""
    if not text:
        return set()
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[一-鿿]|\d+", text)
    return {t.lower() for t in tokens if len(t) >= 1}


def _char_ngrams(text: str, n: int = 3) -> set:
    """提取字符 n-gram 集合,用于字符级重合度判断。

    去除噪声字符:
      - 空格/制表/换行 (`\\s`): 文档段落格式差异
      - 表格分隔符 `|`: sheet/bitable chunk 用 `|` 分行,会人为打断 3-gram 边界
    这样 `444,000` 之类的数字与表格行 `| 444,000 |` 能正确对齐。
    """
    if not text:
        return set()
    t = re.sub(r"[\s|]+", "", text).lower()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i+n] for i in range(len(t) - n + 1)}


def _coverage(expected: str, chunk_text: str) -> float:
    """token 覆盖率: chunk 中出现的 expected token 占比"""
    exp_tokens = _tokenize(expected)
    if not exp_tokens:
        return 0.0
    chunk_tokens = _tokenize(chunk_text)
    return len(exp_tokens & chunk_tokens) / len(exp_tokens)


def _char_overlap(expected: str, chunk_text: str) -> float:
    """字符 3-gram 覆盖率: 严格刻画字符串重合度(token 顺序、连续性敏感)"""
    exp_ng = _char_ngrams(expected, n=3)
    if not exp_ng:
        return 0.0
    chk_ng = _char_ngrams(chunk_text, n=3)
    return len(exp_ng & chk_ng) / len(exp_ng)


def _combined_coverage(expected: str, chunks: list) -> float:
    """所有 chunks 合并后,expected 内容的整体 token 覆盖率(token 并集)。"""
    exp_tokens = _tokenize(expected)
    if not exp_tokens:
        return 0.0
    combined_tokens = set()
    for c in chunks:
        combined_tokens |= _tokenize(c.get("text") or "")
    return len(exp_tokens & combined_tokens) / len(exp_tokens)


def _combined_char_overlap(expected: str, chunks: list) -> float:
    """所有 chunks 合并的字符 3-gram 覆盖率。"""
    exp_ng = _char_ngrams(expected, n=3)
    if not exp_ng:
        return 0.0
    combined_ng = set()
    for c in chunks:
        combined_ng |= _char_ngrams(c.get("text") or "", n=3)
    return len(exp_ng & combined_ng) / len(exp_ng)


def analyze_case(rec: dict) -> dict:
    expected = rec.get("expected", {})
    chunks = (rec.get("raw_payload") or {}).get("chunks") or []
    log = rec.get("log_parsed", {})

    target_substr = expected.get("target_doc_id_substr")
    must_reject = expected.get("must_reject", False)
    expected_chunk_text = expected.get("expected_chunk_text") or ""

    # 1. doc 召回
    target_doc_hit = False
    if target_substr:
        target_doc_hit = any(target_substr in (c.get("chunk_id") or "") for c in chunks)

    # 2. chunk 覆盖率(token + 字符 3-gram 两套)
    best_single_token_cov = 0.0
    best_single_char_cov = 0.0
    best_chunk_tier = None
    bearing_high_tier = 0
    bearing_total = 0
    bearing_critical = 0
    nonbearing_critical = 0
    nonbearing_total = 0

    BEARING_THRESHOLD = 0.30   # 单 chunk char-overlap ≥30% 视为 answer-bearing

    for c in chunks:
        tok_cov = _coverage(expected_chunk_text, c.get("text") or "")
        char_cov = _char_overlap(expected_chunk_text, c.get("text") or "")
        tier = c.get("confidence_tier") or "untiered"
        if char_cov > best_single_char_cov:
            best_single_char_cov = char_cov
            best_single_token_cov = tok_cov   # 记录同一 chunk 的 token cov
            best_chunk_tier = tier
        # 用字符级判断 answer-bearing(更严格)
        is_bearing = char_cov >= BEARING_THRESHOLD
        if is_bearing:
            bearing_total += 1
            if tier == "critical":
                bearing_critical += 1
                bearing_high_tier += 1
            elif tier == "important":
                bearing_high_tier += 1
        else:
            nonbearing_total += 1
            if tier == "critical":
                nonbearing_critical += 1

    combined_token_cov = _combined_coverage(expected_chunk_text, chunks)
    combined_char_cov = _combined_char_overlap(expected_chunk_text, chunks)

    return {
        "test_id": rec["test_id"],
        "must_reject": must_reject,
        "target_doc_hit": target_doc_hit if target_substr else None,
        "n_chunks": len(chunks),
        "best_single_token_cov": best_single_token_cov,
        "best_single_char_cov": best_single_char_cov,
        "best_chunk_tier": best_chunk_tier,
        "combined_token_cov": combined_token_cov,
        "combined_char_cov": combined_char_cov,
        "bearing_total": bearing_total,
        "bearing_high_tier": bearing_high_tier,
        "bearing_critical": bearing_critical,
        "nonbearing_total": nonbearing_total,
        "nonbearing_critical": nonbearing_critical,
        "retrieve_ms": (log.get("latency") or {}).get("total_ms") or 0,
    }


def main() -> None:
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=str(CACHE))
    ap.add_argument("--cases-file", default=str(Path(__file__).parent / "feishu_test_cases.yaml"))
    args = ap.parse_args()
    cache_dir = Path(args.cache_dir)
    print(f"\n[cache_dir: {cache_dir}]")
    files = sorted(cache_dir.glob("*__balanced.json"))
    if not files:
        print("No cached results.")
        return

    # 重新加载 yaml 的 expected (cache 可能是旧字段缺失版本)
    spec = yaml.safe_load(Path(args.cases_file).read_text())
    expected_by_tid = {c["test_id"]: c.get("expected", {}) for c in spec.get("test_cases", [])}

    rows = []
    for f in files:
        rec = json.loads(f.read_text())
        # 合并 cache 里的 expected 和 yaml 里最新的(yaml 优先)
        tid = rec.get("test_id", "")
        if tid in expected_by_tid:
            rec.setdefault("expected", {}).update(expected_by_tid[tid])
        rows.append(analyze_case(rec))

    # ── Per-case table (主指标: 所有 chunks 合并的覆盖率,辅助: 单 chunk 最佳) ─────
    print(f"\n{'─'*125}")
    print(f"{'test_id':12} {'doc':>4} | {'COMB_char':>10} {'COMB_tok':>9} | {'best_char':>10} {'best_tok':>9} {'best_tier':>11} | {'NB-cri(FP)':>11} | {'ms':>7}")
    print(f"{'─'*125}")
    for r in rows:
        if r["must_reject"]:
            print(f"{r['test_id']:12} (out_of_scope, skipped)")
            continue
        doc_s = "—" if r["target_doc_hit"] is None else ("✓" if r["target_doc_hit"] else "✗")
        best_char_s = f"{r['best_single_char_cov']*100:.0f}%"
        best_tok_s = f"{r['best_single_token_cov']*100:.0f}%"
        best_tier_s = (r['best_chunk_tier'] or "—")[:10]
        comb_char_s = f"{r['combined_char_cov']*100:.0f}%"
        comb_tok_s = f"{r['combined_token_cov']*100:.0f}%"
        print(
            f"{r['test_id']:12} "
            f"{doc_s:>4} | "
            f"{comb_char_s:>10} "
            f"{comb_tok_s:>9} | "
            f"{best_char_s:>10} "
            f"{best_tok_s:>9} "
            f"{best_tier_s:>11} | "
            f"{r['nonbearing_critical']:>5}/{r['nonbearing_total']:<5} | "
            f"{r['retrieve_ms']:>6}ms"
        )
    print(f"{'─'*125}")
    print("  COMB_*  : 主指标 - 所有 chunks 合并对原文片段的覆盖率(retrieve_agent 实际输出给下游用的整体)")
    print("  best_*  : 辅助 - 单个 chunk 中对原文片段覆盖最高的那个")
    print("  *_char  : 字符 3-gram 重合(严格,要求连续字符片段)")
    print("  *_tok   : token 重合(宽松,只需 token 出现即可)")
    print("  best_cov   : 单 chunk 对原文片段最高覆盖率")
    print("  best_tier  : 最高覆盖 chunk 被标的 tier(理想 critical)")
    print("  combined   : 所有 chunks 合并对原文片段的总覆盖率")
    print("  bear/B-hi  : 覆盖≥30%的 chunks 数 / 其中被标 critical/important 的数")
    print("  NB-cri(FP) : 覆盖<30%但被标 critical 的数 / non-bearing 总数")

    # ── Summary ─────────────────────────────────────────────
    answerable = [r for r in rows if not r["must_reject"]]
    n_ans = len(answerable)
    if n_ans == 0:
        print("\n(No answerable cases)")
        return

    doc_hits = sum(1 for r in answerable if r["target_doc_hit"])

    # 覆盖率分层统计(以更严的 char-overlap 为准)
    excellent_cov = sum(1 for r in answerable if r["combined_char_cov"] >= 0.8)
    good_cov = sum(1 for r in answerable if 0.5 <= r["combined_char_cov"] < 0.8)
    partial_cov = sum(1 for r in answerable if 0.3 <= r["combined_char_cov"] < 0.5)
    poor_cov = sum(1 for r in answerable if r["combined_char_cov"] < 0.3)
    avg_combined_char = sum(r["combined_char_cov"] for r in answerable) / n_ans
    avg_combined_token = sum(r["combined_token_cov"] for r in answerable) / n_ans
    avg_best_char = sum(r["best_single_char_cov"] for r in answerable) / n_ans
    avg_best_token = sum(r["best_single_token_cov"] for r in answerable) / n_ans

    # Tier 准确度
    total_bearing = sum(r["bearing_total"] for r in answerable)
    bearing_critical = sum(r["bearing_critical"] for r in answerable)
    bearing_high_tier = sum(r["bearing_high_tier"] for r in answerable)
    total_nonbearing = sum(r["nonbearing_total"] for r in answerable)
    nonbearing_critical = sum(r["nonbearing_critical"] for r in answerable)
    total_critical = bearing_critical + nonbearing_critical

    tier_recall = (bearing_high_tier / max(1, total_bearing)) * 100
    tier_precision_critical = (bearing_critical / max(1, total_critical)) * 100
    fp_rate = (nonbearing_critical / max(1, total_nonbearing)) * 100

    # Best-chunk tier match: 最高覆盖的 chunk 被标 critical 的比例
    best_critical = sum(1 for r in answerable if r["best_chunk_tier"] == "critical" and r["best_single_char_cov"] >= 0.3)
    best_important = sum(1 for r in answerable if r["best_chunk_tier"] == "important" and r["best_single_char_cov"] >= 0.3)

    avg_retrieve = sum(r["retrieve_ms"] for r in rows) / len(rows) / 1000

    print(f"\n{'='*60}")
    print(f"Summary  (answerable: {n_ans}, out_of_scope: {len(rows)-n_ans})")
    print(f"{'='*60}")
    print(f"\n[1] 文档召回")
    print(f"    target_doc 命中:                  {doc_hits}/{n_ans}  ({doc_hits/n_ans*100:.0f}%)")

    print(f"\n[2] 原文 chunk 覆盖率 (token & 字符 3-gram 两套)")
    print(f"    {'指标':18} {'char-overlap':>14} {'token-overlap':>14}")
    print(f"    {'单 chunk 最高 (avg)':18} {avg_best_char*100:>13.0f}% {avg_best_token*100:>13.0f}%")
    print(f"    {'合并所有 chunks (avg)':18} {avg_combined_char*100:>13.0f}% {avg_combined_token*100:>13.0f}%")
    print(f"    分层(以 char-overlap combined 为准):")
    print(f"      ≥80% (优秀):  {excellent_cov}/{n_ans}")
    print(f"      50-80%(良好): {good_cov}/{n_ans}")
    print(f"      30-50%(部分): {partial_cov}/{n_ans}")
    print(f"      <30% (差):    {poor_cov}/{n_ans}")

    print(f"\n[3] Tier 准确性")
    print(f"    answer-bearing(覆盖≥30%) chunks:  {total_bearing}")
    print(f"    └ 被标 critical/important:       {bearing_high_tier}/{total_bearing}  ({tier_recall:.0f}%)  (Tier Recall)")
    print(f"    最高覆盖 chunk 是 critical:       {best_critical}/{n_ans}")
    print(f"    最高覆盖 chunk 是 important:      {best_important}/{n_ans}")
    print(f"    critical 总数:                    {total_critical}")
    print(f"    └ 真 bearing 占比 (Precision):    {bearing_critical}/{total_critical}  ({tier_precision_critical:.0f}%)")
    print(f"    non-bearing 误标 critical (FP率): {nonbearing_critical}/{total_nonbearing}  ({fp_rate:.0f}%)")

    print(f"\n[4] 延迟")
    print(f"    Avg retrieve-only latency:        {avg_retrieve:.1f}s")


if __name__ == "__main__":
    main()
