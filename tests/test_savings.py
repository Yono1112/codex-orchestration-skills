import json
import tempfile
import unittest
from pathlib import Path

from scripts import savings


class SavingsTestCase(unittest.TestCase):
    def write_jsonl(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                if isinstance(row, str):
                    handle.write(row + "\n")
                else:
                    handle.write(json.dumps(row) + "\n")


class ParseCodexSessionTests(SavingsTestCase):
    def test_sums_last_token_usage_even_when_cumulative_is_non_monotonic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-2026-06-03T000000Z.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-a",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T00:00:00Z",
                }},
                {"type": "turn", "payload": {
                    "last_token_usage": {"total_tokens": 100},
                    "total_token_usage": {"total_tokens": 1000},
                }},
                {"type": "turn", "payload": {
                    "last_token_usage": {"total_tokens": 150},
                    "total_token_usage": {"total_tokens": 900},
                }},
                {"type": "turn", "payload": {
                    "last_token_usage": {"total_tokens": 25},
                    "total_token_usage": {"total_tokens": 1200},
                }},
            ])

            session = savings.parse_codex_session(path)

            self.assertEqual(session["id"], "codex-a")
            self.assertEqual(session["source"], "mcp")
            self.assertEqual(session["cwd"], "/repo/project")
            self.assertEqual(session["ts_utc"].isoformat(), "2026-06-03T00:00:00+00:00")
            self.assertEqual(session["codex_tokens"], 275)

    def test_falls_back_to_final_total_token_usage_when_last_usage_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-fallback.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-b",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T01:00:00Z",
                }},
                {"type": "turn", "payload": {"total_token_usage": {"total_tokens": 300}}},
                {"type": "turn", "payload": {"total_token_usage": {"total_tokens": 450}}},
            ])

            session = savings.parse_codex_session(path)

            self.assertEqual(session["codex_tokens"], 450)

    def test_returns_none_when_token_usage_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-missing.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-c",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T02:00:00Z",
                }},
                {"type": "turn", "payload": {"message": "no token data"}},
            ])

            self.assertIsNone(savings.parse_codex_session(path))

    def test_skips_malformed_jsonl_lines_and_keeps_valid_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-malformed.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-d",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T03:00:00Z",
                }},
                "{not valid json",
                {"type": "turn", "payload": {"last_token_usage": {"total_tokens": 44}}},
            ])

            session = savings.parse_codex_session(path)

            self.assertEqual(session["codex_tokens"], 44)
