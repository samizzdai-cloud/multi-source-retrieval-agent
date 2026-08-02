import unittest

from feishu_read_tool.chunker import merge_adjacent_chunks
from feishu_read_tool.pipeline_models import Chunk, ChunkContentType


def _chunk(chunk_id: str, heading: str, content: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, doc_id="doc", doc_title="doc", doc_url="", heading_path=heading,
        content_type=ChunkContentType.TABLE_MD, content=content, block_ids=[chunk_id],
        token_count=10, has_table=True,
    )


class ChunkHeadingMergeTests(unittest.TestCase):
    def test_top_level_sections_do_not_merge_and_lose_action_heading(self):
        chunks = merge_adjacent_chunks([
            _chunk("c1", "新品 GTM 策略框架", "## 新品 GTM 策略框架\n\nsummary"),
            _chunk("c2", "90 天行动路线图", "## 90 天行动路线图\n\nAffiliate/SEM governance"),
        ])
        self.assertEqual(["新品 GTM 策略框架", "90 天行动路线图"], [chunk.heading_path for chunk in chunks])
        self.assertIn("Affiliate/SEM governance", chunks[1].content)

    def test_same_heading_chunks_can_still_merge(self):
        chunks = merge_adjacent_chunks([
            _chunk("c1", "三、分析 > 搜索", "## 三、分析 > 搜索\n\nfirst"),
            _chunk("c2", "三、分析 > 搜索", "## 三、分析 > 搜索\n\nsecond"),
        ])
        self.assertEqual(1, len(chunks))
        self.assertEqual(["c1", "c2"], chunks[0].block_ids)

    def test_unheaded_chunks_never_merge_by_empty_path(self):
        chunks = merge_adjacent_chunks([
            _chunk("c1", "", "first"),
            _chunk("c2", "", "second"),
        ])
        self.assertEqual(["c1", "c2"], [chunk.chunk_id for chunk in chunks])
