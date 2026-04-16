"""Test bailian-only recall — diagnose why 0 chunks returned in benchmarks

Tests:
1. Direct API call (no reranker) to see raw results
2. With tags vs without tags
3. Full pipeline (plan_query → bailian_retrieve) with bailian-only sources
"""
import asyncio, os, sys
from dotenv import load_dotenv
load_dotenv()

WORKSPACE_ID = os.getenv("BAILIAN_WORKSPACE_ID", "")
AK = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
SK = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")


def test_direct_api():
    """Call bailian retrieve API directly, no reranker, no tag filter."""
    from tools.bailian_retrieve import _retrieve_single_index, get_configured_indexes
    indexes = get_configured_indexes()
    print(f"Configured indexes: {indexes}")

    queries = [
        "nubia UK 销售",
        "hypershell 竞品",
        "项目任务进展",
    ]

    for index_name, index_id in indexes.items():
        print(f"\n{'='*50}")
        print(f"  Index: {index_name} ({index_id})")
        print(f"{'='*50}")

        for q in queries:
            result = _retrieve_single_index(
                query=q,
                index_id=index_id,
                tags=None,        # no tag filter
                rerank_min_score=0.0,  # no score filter
                rerank_top_n=5,
            )
            status = result.get("status")
            chunks = result.get("chunks", [])
            error = result.get("message", "")
            print(f"  Q: {q!r:30s} → status={status} chunks={len(chunks)} {error or ''}")
            if chunks:
                for c in chunks[:2]:
                    print(f"    score={c.score:.3f} | {c.source_name[:40]} | {c.text[:80]}")


def test_with_various_thresholds():
    """Test with different rerank_min_score values to find where chunks are being filtered."""
    from tools.bailian_retrieve import _retrieve_single_index, get_configured_indexes
    indexes = get_configured_indexes()
    if not indexes:
        print("No indexes configured!")
        return

    index_name, index_id = next(iter(indexes.items()))
    query = "nubia 销售数据"
    print(f"\n{'='*50}")
    print(f"  Threshold test: '{query}' on {index_name}")
    print(f"{'='*50}")

    for threshold in [0.0, 0.3, 0.5, 0.7]:
        result = _retrieve_single_index(
            query=query,
            index_id=index_id,
            tags=None,
            rerank_min_score=threshold,
            rerank_top_n=10,
        )
        chunks = result.get("chunks", [])
        scores = [c.score for c in chunks]
        print(f"  threshold={threshold}: {len(chunks)} chunks | scores={[round(s,3) for s in scores[:5]]}")


async def test_full_pipeline():
    """Run plan_query → bailian_retrieve with bailian-only sources."""
    from tools.query_planner import plan_query
    from tools.bailian_retrieve import bailian_retrieve_unified

    queries = [
        ("nubia UK 3月销售额", ["bailian"]),
        ("hypershell DTC 竞品格局", ["bailian"]),
        ("nubia 产品定价策略", ["bailian"]),
    ]

    print(f"\n{'='*50}")
    print("  Full pipeline test (bailian-only)")
    print(f"{'='*50}")

    for q, sources in queries:
        plan = await asyncio.to_thread(plan_query, q, sources)
        print(f"\n  Q: {q}")
        print(f"    query_type={plan.query_type}, FGs={len(plan.focus_groups)}")
        for fg in plan.focus_groups:
            for sq in fg.sub_queries:
                if "bailian" in sq.targets:
                    chunks = await bailian_retrieve_unified(
                        sq.query, tags=sq.tags, indexes=sq.indexes,
                        query_type=plan.query_type,
                    )
                    print(f"    sub_q={sq.query!r:40s} tags={sq.tags} → {len(chunks)} chunks")
                    for c in chunks[:2]:
                        print(f"      score={c.score:.3f} idx={c.metadata.get('index_name','')} | {c.source_name[:40]}")


if __name__ == "__main__":
    if not WORKSPACE_ID or not AK or not SK:
        print("ERROR: Missing BAILIAN_WORKSPACE_ID / ALIBABA_CLOUD_ACCESS_KEY_ID / SECRET")
        sys.exit(1)

    print("TEST 1: Direct API (no reranker, no tags)")
    test_direct_api()

    print("\nTEST 2: Threshold scan")
    test_with_various_thresholds()

    print("\nTEST 3: Full pipeline (bailian-only)")
    asyncio.run(test_full_pipeline())

    print("\nDONE")
