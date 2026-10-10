import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

from claude_usage import local_usage


class ClaudeUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 9, 20, tzinfo=timezone(timedelta(hours=8)))

    def record(self, identifier, at="2026-10-09T10:00:00Z", incoming=100, outgoing=20):
        return {"type": "assistant", "timestamp": at, "message": {
            "id": identifier, "model": "example", "usage": {"input_tokens": incoming,
                "output_tokens": outgoing, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 30}}}

    def write(self, name, items):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(item) for item in items) + "\n")
        return path

    def test_streaming_and_fork_copies_count_once_with_final_usage(self):
        self.write("project/main.jsonl", [self.record("one", outgoing=2), self.record("one"), self.record("two")])
        self.write("project/subagents/copy.jsonl", [self.record("one")])
        result, _ = local_usage(self.root, now=self.now)
        self.assertEqual(result["today"]["total"], 320)
        self.assertEqual(result["today"]["requests"], 2)
        self.assertEqual(result["today"]["cache"], 80)

    def test_calendar_days_use_local_timezone_and_ignore_future_records(self):
        self.write("a.jsonl", [self.record("today", at="2026-10-08T17:00:00Z"),
            self.record("first-day", at="2026-10-02T17:00:00Z"),
            self.record("old", at="2026-10-02T15:00:00Z"), self.record("future", at="2026-10-10T10:00:00Z")])
        result, _ = local_usage(self.root, now=self.now)
        self.assertEqual(result["today"]["requests"], 1)
        self.assertEqual(result["week"]["requests"], 2)
        self.assertEqual(result["days"][0]["date"], "2026-10-03")

    def test_incremental_cache_rereads_changed_files_and_drops_deleted_files(self):
        path = self.write("a.jsonl", [self.record("one")])
        _, cache = local_usage(self.root, now=self.now)
        with mock.patch("claude_usage.read_records", side_effect=AssertionError("Unchanged file should use cache")):
            result, _ = local_usage(self.root, cache, self.now)
        self.assertEqual(result["today"]["total"], 160)
        self.write("a.jsonl", [self.record("one"), self.record("two")])
        result, cache = local_usage(self.root, cache, self.now)
        self.assertEqual(result["today"]["total"], 320)
        path.unlink()
        result, _ = local_usage(self.root, cache, self.now)
        self.assertEqual(result["today"]["total"], 0)

    def test_partial_records_and_synthetic_errors_are_not_usage(self):
        error = self.record("error")
        error["isApiErrorMessage"] = True
        synthetic = self.record("synthetic")
        synthetic["message"]["model"] = "<synthetic>"
        path = self.write("a.jsonl", [self.record("one"), error, synthetic])
        with path.open("a") as stream:
            stream.write('{"type": "assistant",')
        result, _ = local_usage(self.root, now=self.now)
        self.assertEqual(result["today"]["requests"], 1)


if __name__ == "__main__":
    unittest.main()
