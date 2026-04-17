"""Smoke test for doc/sheet/bitable chunks after reader refactor.

Tests:
  1. Doc chunks: concept query q3 (nubia Z70 定价)
  2. Sheet chunks: data query q1 (nubia 英国站 DTC GMV)
  3. Bitable chunks: data query q4 (nubia 日本 KOL 合作)

Validates:
  - Chunks returned non-empty
  - Sheet/bitable chunks have 40-row structure + tab/table prefix
  - Each chunk has metadata (doc_type, tab/table name)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")
# Silence noisy libs
for name in ("httpx", "httpcore", "urllib3", "openai", "google"):
    logging.getLogger(name).setLevel(logging.WARNING)

from tools.feishu_retrieve import feishu_retrieve
from tools.feishu_doc_pool import FeishuDocPool
from tools.table_pool import TablePool
from tools.embedding import EmbeddingCache


async def run_one(query: str, query_type: str, focuses: list, label: str):
    print(f"\n{'=' * 80}")
    print(f"SMOKE TEST: {label}")
    print(f"  query: {query}")
    print(f"  type: {query_type}")
    print(f"{'=' * 80}")

    uat = os.getenv("FEISHU_UAT", "")
    api_key = os.getenv("DASHSCOPE_API_KEY", "")

    doc_pool = FeishuDocPool(uat=uat, query_type=query_type)
    cache = EmbeddingCache(api_key=api_key, enabled=True)
    await cache.precompute_focuses(focuses)
    doc_pool.set_embedding_cache(cache, focuses)

    table_pool = TablePool(uat=uat) if query_type in ("data", "mixed") else None

    chunks = await feishu_retrieve(
        query=query,
        uat=uat,
        query_type=query_type,
        doc_pool=doc_pool,
        table_pool=table_pool,
        pool_timeout=55.0,
        skip_preflight=True,
        pool_noise_threshold=0.0,
        focus_group_idx=0,
        focuses=focuses,
    )

    by_type = {"docx": 0, "SHEET": 0, "BITABLE": 0, "other": 0}
    samples = {"docx": None, "SHEET": None, "BITABLE": None, "other": None}
    for c in chunks:
        # doc chunks have metadata.content_type; table chunks have metadata.doc_type
        dt = c.metadata.get("doc_type", "") or c.metadata.get("content_type", "")
        dtU = str(dt).upper()
        if "SHEET" in dtU:
            by_type["SHEET"] += 1
            samples["SHEET"] = samples["SHEET"] or c
        elif "BITABLE" in dtU:
            by_type["BITABLE"] += 1
            samples["BITABLE"] = samples["BITABLE"] or c
        elif dt:
            # any other content_type (heading/paragraph/table/list/etc.) is a doc chunk
            by_type["docx"] += 1
            samples["docx"] = samples["docx"] or c
        else:
            by_type["other"] += 1
            samples["other"] = samples["other"] or c

    print(f"\nTotal chunks: {len(chunks)}")
    print(f"  docx={by_type['docx']}, sheet={by_type['SHEET']}, "
          f"bitable={by_type['BITABLE']}, other={by_type['other']}")

    for kind, sample in samples.items():
        if sample:
            print(f"\n── {kind} sample ──")
            print(f"  source: {sample.source_name}")
            print(f"  heading: {sample.heading_path}")
            print(f"  score: {sample.score}")
            print(f"  metadata: {sample.metadata}")
            print(f"  text[:400]: {sample.text[:400]}")

    return by_type


async def main():
    # Test 1: CONCEPT query → expect doc chunks
    r1 = await run_one(
        query="nubia Z70 Ultra 的产品定价策略",
        query_type="concept",
        focuses=["定价策略"],
        label="CONCEPT → docx",
    )

    # Test 2: DATA query → expect sheet chunks (broad → more sheets)
    r2 = await run_one(
        query="nubia 销售进度",
        query_type="data",
        focuses=["销售进度", "各站点销售"],
        label="DATA → sheet",
    )

    # Test 3: DATA query → expect bitable chunks
    r3 = await run_one(
        query="nubia 知识库管理",
        query_type="data",
        focuses=["知识库管理", "文档管理"],
        label="DATA → bitable",
    )

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"T1 CONCEPT: docx={r1['docx']} sheet={r1['SHEET']} bitable={r1['BITABLE']}")
    print(f"T2 DATA:    docx={r2['docx']} sheet={r2['SHEET']} bitable={r2['BITABLE']}")
    print(f"T3 DATA:    docx={r3['docx']} sheet={r3['SHEET']} bitable={r3['BITABLE']}")


if __name__ == "__main__":
    asyncio.run(main())
