"""通用 Mock 教学演示的测试；所有数据库均位于自动清理的临时目录。"""
import tempfile
import unittest
from pathlib import Path

from workflow_demo import MockProvider, Workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="portfolio-test-")
        self.path = Path(self.folder.name) / "test.sqlite3"
        self.provider = MockProvider()
        self.flow = Workflow(self.path, self.provider)
        self.flow.request("item")

    def tearDown(self):
        self.flow.close()
        self.folder.cleanup()

    def generate(self):
        self.flow.advance("item")
        return self.flow.advance("item")

    def reopen(self):
        self.flow.close()
        self.flow = Workflow(self.path, self.provider)

    def test_same_business_key_creates_one_task(self):
        original = self.flow.get("item")
        self.assertEqual(original["id"], self.flow.request("item")["id"])
        self.generate()
        self.flow.request("item")
        self.flow.advance("item")
        count = self.flow.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.provider.calls["submit"], 1)

    def test_generated_result_survives_restart_without_provider_call(self):
        expected = self.generate()
        calls = self.provider.calls.copy()
        self.reopen()
        self.assertEqual(self.flow.advance("item"), expected)
        self.assertEqual(self.provider.calls, calls)

    def test_review_requires_matching_material_version(self):
        self.generate()
        with self.assertRaises(ValueError):
            self.flow.approve("item", 2)
        self.assertEqual(self.flow.get("item")["state"], "generated")
        approved = self.flow.approve("item", 1)
        self.assertEqual(approved["reviewed_version"], 1)

    def test_redo_invalidates_old_review(self):
        self.generate()
        self.flow.approve("item", 1)
        redone = self.flow.redo("item")
        self.assertEqual(redone["version"], 2)
        self.assertIsNone(redone["reviewed_version"])
        self.assertIsNone(redone["result"])
        self.generate()
        with self.assertRaises(ValueError):
            self.flow.approve("item", 1)
        self.assertEqual(self.flow.approve("item", 2)["state"], "approved")

    def test_remote_id_is_queried_after_restart_without_resubmission(self):
        submitted = self.flow.advance("item")
        self.reopen()
        restored = self.flow.advance("item")
        self.assertEqual(restored["remote_id"], submitted["remote_id"])
        self.assertEqual(self.provider.calls, {"submit": 1, "query": 1})

    def test_unknown_submission_does_not_blindly_retry(self):
        self.provider.unknown_next = True
        self.flow.advance("item")
        self.reopen()
        self.flow.request("item")
        self.assertEqual(self.flow.advance("item")["state"], "needs_attention")
        with self.assertRaises(ValueError):
            self.flow.redo("item")
        self.assertEqual(self.provider.calls, {"submit": 1, "query": 0})


if __name__ == "__main__":
    unittest.main()
