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
    def test_sums_info_last_token_usage_from_event_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-info-last-usage.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-info-a",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T00:00:00Z",
                }},
                {"type": "event_msg", "payload": {"info": {
                    "last_token_usage": {"total_tokens": 18140},
                    "total_token_usage": {"total_tokens": 36193},
                    "model_context_window": 258400,
                }}},
                {"type": "event_msg", "payload": {"info": {
                    "last_token_usage": {"total_tokens": 2220},
                    "total_token_usage": {"total_tokens": 38413},
                    "model_context_window": 258400,
                }}},
            ])

            session = savings.parse_codex_session(path)

            self.assertEqual(session["codex_tokens"], 20360)

    def test_falls_back_to_info_total_token_usage_when_last_usage_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-info-fallback.jsonl"
            self.write_jsonl(path, [
                {"type": "session_meta", "payload": {
                    "id": "codex-info-b",
                    "source": "mcp",
                    "cwd": "/repo/project",
                    "timestamp": "2026-06-03T01:00:00Z",
                }},
                {"type": "event_msg", "payload": {"info": {
                    "total_token_usage": {"total_tokens": 300},
                    "model_context_window": 258400,
                }}},
                {"type": "event_msg", "payload": {"info": {
                    "total_token_usage": {"total_tokens": 450},
                    "model_context_window": 258400,
                }}},
            ])

            session = savings.parse_codex_session(path)

            self.assertEqual(session["codex_tokens"], 450)

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

    def test_counts_next_assistant_after_matching_codex_tool_result_as_direct_overhead(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-delegate",
                    "usage": self.usage(20, 0, 0, 5),
                    "content": [{
                        "type": "tool_use",
                        "name": "mcp__codex__codex",
                        "id": "toolu_A",
                        "input": {"prompt": "work"},
                    }],
                }},
                {"type": "user", "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_A",
                    "content": "completed",
                }]}},
                {"type": "assistant", "requestId": "request-1", "message": {
                    "usage": self.usage(30, 1, 1, 8),
                    "content": [{"type": "text", "text": "review result"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 65)
            self.assertEqual(records[0]["total_tokens"], 65)

    def test_tool_result_content_with_codex_text_does_not_mark_next_assistant_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-delegate",
                    "usage": self.usage(100, 0, 0, 20),
                    "content": [{
                        "type": "tool_use",
                        "name": "mcp__codex__codex",
                        "id": "toolu_A",
                        "input": {"prompt": "work"},
                    }],
                }},
                {"type": "user", "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_A",
                    "content": [{"type": "text", "text": "completed"}],
                }]}},
                {"type": "assistant", "message": {
                    "id": "msg-review",
                    "usage": self.usage(30, 1, 1, 8),
                    "content": [{"type": "text", "text": "review result"}],
                }},
                {"type": "user", "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_OTHER",
                    "content": [{"type": "text", "text": "README mentions codex-orchestration"}],
                }]}},
                {"type": "assistant", "message": {
                    "id": "msg-after-read",
                    "usage": self.usage(20, 0, 0, 5),
                    "content": [{"type": "text", "text": "ordinary follow-up"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 160)
            self.assertEqual(records[0]["total_tokens"], 185)
            self.assertEqual(records[0]["tool_use_count"], 1)

    def test_any_matching_tool_result_item_marks_next_assistant_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-delegate",
                    "usage": self.usage(10, 0, 0, 2),
                    "content": [{
                        "type": "tool_use",
                        "name": "mcp__codex__codex",
                        "id": "toolu_A",
                        "input": {"prompt": "work"},
                    }],
                }},
                {"type": "user", "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_OTHER",
                        "content": "unrelated",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_A",
                        "content": "completed",
                    },
                ]}},
                {"type": "assistant", "message": {
                    "id": "msg-review",
                    "usage": self.usage(20, 0, 0, 3),
                    "content": [{"type": "text", "text": "review result"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 35)

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

    def test_prices_direct_overhead_usd_from_real_message_usage_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-delegate",
                    "model": "claude-opus-4-5-20251101",
                    "usage": self.usage(1_000_000, 200_000, 3_000_000, 100_000),
                    "content": [{
                        "type": "tool_use",
                        "name": "mcp__codex__codex",
                        "id": "toolu_A",
                        "input": {"prompt": "work"},
                    }],
                }},
                {"type": "assistant", "message": {
                    "id": "msg-ordinary",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": self.usage(1_000_000, 0, 0, 100_000),
                    "content": [{"type": "text", "text": "ordinary text"}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertEqual(records[0]["direct_tokens"], 4_300_000)
            self.assertAlmostEqual(records[0]["direct_usd"], 10.25)
            self.assertEqual(records[0]["total_tokens"], 5_400_000)
            self.assertAlmostEqual(records[0]["total_usd"], 14.75)

    def test_no_cache_excludes_cache_tokens_from_usd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": self.usage(1_000_000, 2_000_000, 3_000_000, 100_000),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
            ])

            records = savings.parse_claude_transcript(path, include_cache=False)

            self.assertEqual(records[0]["direct_tokens"], 1_100_000)
            self.assertAlmostEqual(records[0]["direct_usd"], 4.5)

    def test_unpriced_models_use_fallback_and_report_tokens_and_model_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            self.write_jsonl(path, [
                {"type": "assistant", "message": {
                    "id": "msg-1",
                    "model": "<synthetic>",
                    "usage": self.usage(1_000_000, 0, 0, 100_000),
                    "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
                }},
            ])

            records = savings.parse_claude_transcript(path)

            self.assertAlmostEqual(records[0]["direct_usd"], 4.5)
            self.assertEqual(records[0]["unpriced_fallback_tokens"], 1_100_000)
            self.assertEqual(records[0]["unpriced_fallback_models"], ["<synthetic>"])

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


class CollectClaudeTests(SavingsTestCase):
    def write_transcript(self, path, timestamp, message_id, sidechain=False):
        self.write_jsonl(path, [
            {"type": "assistant", "timestamp": timestamp, "isSidechain": sidechain, "message": {
                "id": message_id,
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 5,
                },
                "content": [{"type": "tool_use", "name": "mcp__codex__codex", "input": {"prompt": "work"}}],
            }},
        ])

    def test_collect_claude_reads_project_transcripts_and_filters_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_transcript(root / "projects" / "repo" / "old.jsonl", "2026-06-01T00:00:00Z", "old")
            self.write_transcript(root / "projects" / "repo" / "new.jsonl", "2026-06-03T00:00:00Z", "new")

            records = savings.collect_claude(root, since_utc=savings._parse_utc("2026-06-02T00:00:00Z"))

            self.assertEqual([Path(record["path"]).name for record in records], ["new.jsonl"])
            self.assertEqual(records[0]["direct_tokens"], 15)

    def test_collect_claude_excludes_sidechains_by_default_and_includes_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_transcript(root / "projects" / "repo" / "main.jsonl", "2026-06-03T00:00:00Z", "main", sidechain=False)
            self.write_transcript(root / "projects" / "repo" / "side.jsonl", "2026-06-03T00:00:00Z", "side", sidechain=True)

            default_records = savings.collect_claude(root)
            included_records = savings.collect_claude(root, include_sidechains=True)

            self.assertEqual([Path(record["path"]).name for record in default_records], ["main.jsonl"])
            self.assertEqual([Path(record["path"]).name for record in included_records], ["main.jsonl", "side.jsonl"])


class ComputeTests(SavingsTestCase):
    def test_compute_broad_uses_all_codex_sessions_and_direct_overhead(self):
        codex = [
            {"id": "codex-a", "cwd": "/repo/a", "codex_tokens": 100, "ts_utc": savings._parse_utc("2026-06-03T00:00:00Z")},
            {"id": "codex-b", "cwd": "/repo/b", "codex_tokens": 300, "ts_utc": savings._parse_utc("2026-06-03T01:00:00Z")},
        ]
        claude = [
            {"path": "one.jsonl", "direct_tokens": 50, "total_tokens": 200, "direct_usd": 0.50, "total_usd": 2.00},
            {"path": "two.jsonl", "direct_tokens": 10, "total_tokens": 30, "direct_usd": 0.10, "total_usd": 0.30},
        ]

        report = savings.compute(codex, claude, ks=[0.5, 1.0, 2.0])

        self.assertEqual(report["attribution"], "broad[source:mcp]")
        self.assertEqual(report["codex_session_count"], 2)
        self.assertEqual(report["codex_tokens"], 400)
        self.assertEqual(report["claude_direct_tokens"], 60)
        self.assertEqual(report["claude_total_tokens"], 230)
        self.assertAlmostEqual(report["codex_counterfactual_usd"], 0.002)
        self.assertAlmostEqual(report["claude_direct_usd"], 0.60)
        self.assertAlmostEqual(report["claude_total_usd"], 2.30)
        self.assertEqual(report["sensitivity"], [
            {"k": 0.5, "avoided_tokens": 200, "net_savings_tokens": 140, "avoided_usd": 0.001, "net_savings_usd": -0.599},
            {"k": 1.0, "avoided_tokens": 400, "net_savings_tokens": 340, "avoided_usd": 0.002, "net_savings_usd": -0.598},
            {"k": 2.0, "avoided_tokens": 800, "net_savings_tokens": 740, "avoided_usd": 0.004, "net_savings_usd": -0.596},
        ])

    def test_compute_allows_counterfactual_model_switch_and_accumulates_fallback_notes(self):
        codex = [
            {"id": "codex-a", "cwd": "/repo/a", "codex_tokens": 1_000_000, "ts_utc": savings._parse_utc("2026-06-03T00:00:00Z")},
        ]
        claude = [
            {
                "path": "one.jsonl",
                "direct_tokens": 1_100_000,
                "total_tokens": 1_100_000,
                "direct_usd": 4.50,
                "total_usd": 4.50,
                "unpriced_fallback_tokens": 1_100_000,
                "unpriced_fallback_models": ["<synthetic>"],
            },
        ]

        report = savings.compute(codex, claude, ks=[1.0], counterfactual_model="claude-sonnet-4-5")

        self.assertEqual(report["counterfactual_model"], "claude-sonnet-4-5")
        self.assertAlmostEqual(report["codex_counterfactual_usd"], 3.0)
        self.assertEqual(report["unpriced_fallback_tokens"], 1_100_000)
        self.assertEqual(report["unpriced_fallback_models"], ["<synthetic>"])
        self.assertAlmostEqual(report["sensitivity"][0]["avoided_usd"], 3.0)
        self.assertAlmostEqual(report["sensitivity"][0]["net_savings_usd"], -1.5)


class RenderTests(SavingsTestCase):
    def test_render_outputs_headline_sensitivity_and_codex_session_breakdown(self):
        report = {
            "attribution": "broad[source:mcp]",
            "codex_session_count": 1,
            "codex_tokens": 1240000,
            "codex_counterfactual_usd": 6.20,
            "claude_direct_tokens": 38000,
            "claude_direct_usd": 0.42,
            "claude_total_tokens": 210000,
            "claude_total_usd": 0.85,
            "counterfactual_model": "claude-opus-4-5",
            "unpriced_fallback_tokens": 0,
            "unpriced_fallback_models": [],
            "projects": ["/repo/daily-news"],
            "sensitivity": [
                {"k": 0.5, "avoided_tokens": 620000, "net_savings_tokens": 582000, "avoided_usd": 3.10, "net_savings_usd": 2.68},
                {"k": 1.0, "avoided_tokens": 1240000, "net_savings_tokens": 1202000, "avoided_usd": 6.20, "net_savings_usd": 5.78},
            ],
            "codex_sessions": [
                {
                    "id": "codex-a",
                    "cwd": "/repo/daily-news",
                    "ts_utc": savings._parse_utc("2026-06-03T07:29:00Z"),
                    "codex_tokens": 61285,
                },
            ],
        }

        text = savings.render(report, since_utc=savings._parse_utc("2026-06-02T00:00:00Z"))

        self.assertIn("codex-orchestration 節約レポート（UTC since 2026-06-02T00:00:00Z, attribution=broad[source:mcp]）", text)
        self.assertIn("委譲セッション数: 1", text)
        self.assertIn("Codex がやった仕事            : 1,240,000 tok   (≈ $6.20 反実仮想 k=1.0)", text)
        self.assertIn("Claude overhead (狭義 direct) : 38,000 tok   (≈ $0.42 実コスト概算)", text)
        self.assertIn("Claude 全処理トークン(参考)   : 210,000 tok   (≈ $0.85 実コスト概算)", text)
        self.assertIn("k=0.5  -> 582,000 tok   (≈ $2.68)", text)
        self.assertIn("k=1.0  -> 1,202,000 tok   (≈ $5.78)", text)
        self.assertIn("下限保証ではない", text)
        self.assertIn("回避分USDは反実仮想 claude-opus-4-5 入力レートのみの概算", text)
        self.assertIn("overhead USDは実transcriptのmodel別4種別課金", text)
        self.assertIn("残存ログのみが対象", text)
        self.assertIn("Codex セッション一覧（日付UTC / cwd / Codex トークン）:", text)
        self.assertIn("2026-06-03T07:29:00Z  /repo/daily-news  Codex 61,285", text)

    def test_render_notes_unpriced_fallback_models(self):
        report = {
            "attribution": "broad[source:mcp]",
            "codex_session_count": 0,
            "codex_tokens": 0,
            "codex_counterfactual_usd": 0.0,
            "claude_direct_tokens": 1100000,
            "claude_direct_usd": 4.50,
            "claude_total_tokens": 1100000,
            "claude_total_usd": 4.50,
            "counterfactual_model": "claude-opus-4-5",
            "unpriced_fallback_tokens": 1100000,
            "unpriced_fallback_models": ["<synthetic>"],
            "projects": [],
            "sensitivity": [
                {"k": 1.0, "avoided_tokens": 0, "net_savings_tokens": -1100000, "avoided_usd": 0.0, "net_savings_usd": -4.5},
            ],
            "codex_sessions": [],
        }

        text = savings.render(report)

        self.assertIn("unpriced(fallback) tokens: 1,100,000, models: <synthetic>", text)


class MainTests(SavingsTestCase):
    def write_codex_session(self, root, session_id, source, cwd, timestamp, tokens):
        self.write_jsonl(Path(root) / "codex" / "sessions" / "2026" / "06" / "03" / f"rollout-{session_id}.jsonl", [
            {"type": "session_meta", "payload": {
                "id": session_id,
                "source": source,
                "cwd": cwd,
                "timestamp": timestamp,
            }},
            {"type": "turn", "payload": {"last_token_usage": {"total_tokens": tokens}}},
        ])

    def write_claude_transcript(self, root, direct_tokens):
        self.write_jsonl(Path(root) / "claude" / "projects" / "repo" / "session.jsonl", [
            {"type": "assistant", "timestamp": "2026-06-03T00:00:00Z", "message": {
                "id": "msg-1",
                "usage": {"input_tokens": direct_tokens, "output_tokens": 0},
                "content": [{"type": "tool_use", "name": "mcp__codex__codex-reply", "input": {"prompt": "continue"}}],
            }},
        ])

    def test_main_runs_broad_report_with_custom_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_codex_session(tmp, "codex-a", "mcp", "/repo/project", "2026-06-03T00:00:00Z", 100)
            self.write_claude_transcript(tmp, 25)

            exit_code = savings.main([
                "--codex-root", str(Path(tmp) / "codex"),
                "--claude-root", str(Path(tmp) / "claude"),
                "--since", "2026-06-02",
                "--cwd", "project",
                "--k", "1.0",
            ])

            self.assertEqual(exit_code, 0)

    def test_main_rejects_invalid_since_date(self):
        exit_code = savings.main(["--since", "not-a-date"])

        self.assertEqual(exit_code, 2)
