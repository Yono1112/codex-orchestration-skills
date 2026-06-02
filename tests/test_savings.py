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


class CollectCodexTests(SavingsTestCase):
    def make_session(self, root, rel_path, session_id, source, cwd, timestamp, tokens=10):
        path = Path(root) / rel_path
        payload = {"id": session_id, "cwd": cwd, "timestamp": timestamp}
        if source is not None:
            payload["source"] = source
        self.write_jsonl(path, [
            {"type": "session_meta", "payload": payload},
            {"type": "turn", "payload": {"last_token_usage": {"total_tokens": tokens}}},
        ])
        return path

    def test_iter_codex_sessions_finds_rollout_jsonl_files_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = self.make_session(
                root,
                "sessions/2026/06/03/rollout-a.jsonl",
                "codex-a",
                "mcp",
                "/repo/project",
                "2026-06-03T00:00:00Z",
            )
            self.write_jsonl(root / "sessions/2026/06/03/not-rollout.jsonl", [])

            paths = list(savings.iter_codex_sessions(root))

            self.assertEqual(paths, [expected])

    def test_collect_codex_filters_source_since_and_cwd_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_session(root, "sessions/2026/06/01/rollout-old.jsonl", "old", "mcp", "/repo/project", "2026-06-01T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-keep.jsonl", "keep", "mcp", "/repo/project", "2026-06-03T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-exec.jsonl", "exec", "exec", "/repo/project", "2026-06-03T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-cli.jsonl", "cli", "cli", "/repo/project", "2026-06-03T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-nosource.jsonl", "nosource", None, "/repo/project", "2026-06-03T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-othercwd.jsonl", "othercwd", "mcp", "/repo/other", "2026-06-03T00:00:00Z")

            sessions = savings.collect_codex(
                root,
                since_utc=savings._parse_utc("2026-06-02T00:00:00Z"),
                cwd_filter="project",
                cwd_exact=False,
            )

            self.assertEqual([session["id"] for session in sessions], ["keep"])

    def test_collect_codex_supports_exact_cwd_filter_with_normalized_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_session(root, "sessions/2026/06/03/rollout-keep.jsonl", "keep", "mcp", "/repo/project", "2026-06-03T00:00:00Z")
            self.make_session(root, "sessions/2026/06/03/rollout-near.jsonl", "near", "mcp", "/repo/project-extra", "2026-06-03T00:00:00Z")

            sessions = savings.collect_codex(
                root,
                since_utc=None,
                cwd_filter="/repo/project",
                cwd_exact=True,
            )

            self.assertEqual([session["id"] for session in sessions], ["keep"])


class ParseClaudeTranscriptTests(SavingsTestCase):
    def usage(self, input_tokens, cache_create, cache_read, output_tokens):
        return {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_create,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output_tokens,
        }

    def test_dedupes_split_message_and_counts_codex_tool_use_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(10, 2, 3, 5),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(10, 2, 3, 5),
                    "content": [{"type": "text", "text": "stream continuation"}],
                }},
                {"type": "assistant", "message": {
                    "id": "msg-2",
                    "usage": self.usage(20, 0, 0, 4),
                    "content": [{"type": "text", "text": "ordinary assistant text"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["direct_tokens"], 20)
            self.assertEqual(records[0]["total_tokens"], 44)
            self.assertEqual(records[0]["tool_use_count"], 1)

    def test_counts_multiple_codex_tool_uses_without_double_counting_overhead(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(100, 0, 0, 20),
                    "content": [
                        {"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "one"}},
                        {"type": "tool_use", "name": "mcp__codex__codex-reply", "input": {"prompt": "two"}},
                    ],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 120)
            self.assertEqual(records[0]["tool_use_count"], 2)

    def test_counts_next_assistant_after_codex_tool_result_as_direct_overhead(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "user", "message": {"content": [{
                    "type": "tool_result",
                    "content": "codex completed",
                }]}},
                {"type": "assistant", "requestId": "request-1", "message": {
                    "usage": self.usage(30, 1, 1, 8),
                    "content": [{"type": "text", "text": "review result"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 40)
            self.assertEqual(records[0]["total_tokens"], 40)

    def test_ignores_non_codex_messages_for_direct_overhead_but_keeps_session_total_when_delegation_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(5, 0, 0, 5),
                    "content": [{"type": "text", "text": "ordinary"}],
                }},
                {"type": "assistant", "message": {
                    "id": "msg-2",
                    "usage": self.usage(10, 0, 0, 1),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 11)
            self.assertEqual(records[0]["total_tokens"], 21)

    def test_no_cache_excludes_cache_creation_and_cache_read_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(10, 50, 60, 5),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
            ])

            records = savings.parse_claude_transcript(path, include_cache=False)

            self.assertEqual(records[0]["direct_tokens"], 15)
            self.assertEqual(records[0]["total_tokens"], 15)

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                "{not valid json",
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "usage": self.usage(1, 1, 1, 1),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 4)
