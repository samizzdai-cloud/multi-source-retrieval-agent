"""The Qwen client must stay SDK-free; source I/O stays independent."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("agents", "tools", "google_workspace_tool", "feishu_read_tool", "eval", "retrieve_agent.py")


class RerankIsolationTests(unittest.TestCase):
    def test_runtime_does_not_reintroduce_dashscope_sdk(self):
        offenders = []
        for scope in SCOPES:
            path = ROOT / scope
            paths = path.rglob("*.py") if path.is_dir() else (path,)
            for candidate in paths:
                if "cache_" in candidate.as_posix() or "artifacts" in candidate.as_posix():
                    continue
                text = candidate.read_text(encoding="utf-8")
                lowered = text.casefold()
                if "import dashscope" in lowered or "from dashscope" in lowered:
                    offenders.append(f"{candidate.relative_to(ROOT)}:dashscope-sdk")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
