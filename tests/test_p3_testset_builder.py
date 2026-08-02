import json
import tempfile
import unittest
from pathlib import Path

from tests.p3_testset_builder import _metadata_title, load_resources, propose_queries, validate_case


class P3TestsetBuilderTest(unittest.TestCase):
    def setUp(self):
        self.resources = {
            "a:C1": {"resource_ref": "a:C1", "document_id": "a", "content_sha256": "x", "content": "GL 五月销量 10"},
            "b:C2": {"resource_ref": "b:C2", "document_id": "b", "content_sha256": "y", "content": "GL 五月预算 20"},
        }

    def test_validates_cross_document_evidence(self):
        case = validate_case({"id": "ok", "expected_outcome": "sufficient_evidence", "expected_chain": [
            {"resource_ref": "a:C1", "role": "sales", "anchor": "五月销量 10"},
            {"resource_ref": "b:C2", "role": "budget", "anchor": "五月预算 20"}],
            "evidence_shape": {"cross_chunk": True, "cross_document": True, "business_entity": "GL", "period": "五月"}}, self.resources)
        self.assertEqual(2, len(case["expected_chain"]))

    def test_rejects_unverified_sufficient_evidence(self):
        with self.assertRaises(ValueError):
            validate_case({"id": "bad", "expected_outcome": "sufficient_evidence", "expected_chain": [
                {"resource_ref": "a:C1", "role": "sales", "anchor": "不存在"}]}, self.resources)

    def test_prefers_drive_metadata_title(self):
        self.assertEqual("真实标题", _metadata_title({"name": "真实标题", "title": "旧标题"}))

    def test_load_resources_parses_markdown_chunk_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            resource_dir = Path(tmp)
            (resource_dir / "a.md").write_text("# A\n\n## [a:C1] h\n\n正文", encoding="utf-8")
            (resource_dir / "resource_manifest.json").write_text(json.dumps({"chunks": [{
                "resource_ref": "a:C1", "document_id": "a", "content_sha256": "x"}]}), encoding="utf-8")
            self.assertEqual("正文", load_resources(resource_dir)[0]["content"])

    def test_proposal_rejects_out_of_scope_reference(self):
        resources = [{"resource_ref": "a:C1", "document_id": "a", "document_title": "A", "heading_path": "h",
                      "content_type": "text", "content_sha256": "x", "anchor": "正文"}]
        response = '{"candidates":[{"id":"ok","query":"问题","expected_outcome":"sufficient_evidence","required_resource_refs":["a:C1"]},{"id":"bad","query":"越界","expected_outcome":"sufficient_evidence","required_resource_refs":["b:C1"]}]}'
        rows = propose_queries(resources, [], lambda _: response)
        self.assertEqual(["ok"], [item["id"] for item in rows])


if __name__ == "__main__":
    unittest.main()
