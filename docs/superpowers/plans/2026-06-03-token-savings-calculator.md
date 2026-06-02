# Token Savings Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standard-library Python CLI that mines Codex and Claude logs to estimate Claude Pro token savings from Codex delegation.

**Architecture:** `scripts/savings.py` is a single CLI module split into pure functions for parsing, collection, computation, rendering, and argument handling. `tests/test_savings.py` uses small in-test JSONL fixtures and temporary directories so the suite never depends on `~/.codex` or `~/.claude`.

**Tech Stack:** Python 3 標準ライブラリのみ（依存なし）。テストは `unittest`。

---

## File Structure

- Create: `scripts/savings.py`
  - Responsibility: Parse Codex session JSONL and Claude transcript JSONL, filter sessions, compute broad-attribution counterfactual savings for multiple `k` values, render a text report, and expose a CLI.
- Create: `tests/test_savings.py`
  - Responsibility: Unit-test each pure component with small JSONL fixtures created inside `tempfile.TemporaryDirectory`; no tests read real home-directory logs.
- Modify: `README.md`
  - Responsibility: Add a short user-facing quick reference for running the savings calculator and interpreting the sensitivity report.
- Modify: `SKILL.md`
  - Responsibility: Add a one-line pointer from the skill quick reference to the savings calculator.

## Task 1: `parse_codex_session`

**Files:**
- Create: `scripts/savings.py`
- Create: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.ParseCodexSessionTests -v`

Expected output:

```text
ImportError: cannot import name 'savings' from 'scripts'
FAILED (errors=1)
```

- [ ] **Step 3: Write minimal implementation**

Create `scripts/savings.py` with:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path):
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nested_total_tokens(payload: dict[str, Any], key: str) -> int | None:
    usage = payload.get(key)
    if not isinstance(usage, dict):
        return None
    value = usage.get("total_tokens")
    return value if isinstance(value, int) else None


def parse_codex_session(path: str | Path) -> dict[str, Any] | None:
    meta: dict[str, Any] | None = None
    last_token_sum = 0
    saw_last_token_usage = False
    final_cumulative_total: int | None = None

    for row in _read_jsonl(Path(path)):
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if row.get("type") == "session_meta":
            meta = payload

        last_total = _nested_total_tokens(payload, "last_token_usage")
        if last_total is not None:
            saw_last_token_usage = True
            last_token_sum += last_total

        cumulative_total = _nested_total_tokens(payload, "total_token_usage")
        if cumulative_total is not None:
            final_cumulative_total = cumulative_total

    if meta is None:
        return None

    if saw_last_token_usage:
        codex_tokens = last_token_sum
    elif final_cumulative_total is not None:
        codex_tokens = final_cumulative_total
    else:
        return None

    return {
        "id": meta.get("id"),
        "source": meta.get("source"),
        "cwd": meta.get("cwd"),
        "ts_utc": _parse_utc(meta.get("timestamp")),
        "codex_tokens": codex_tokens,
        "path": str(Path(path)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.ParseCodexSessionTests -v`

Expected output:

```text
test_falls_back_to_final_total_token_usage_when_last_usage_missing ... ok
test_returns_none_when_token_usage_is_absent ... ok
test_skips_malformed_jsonl_lines_and_keeps_valid_rows ... ok
test_sums_last_token_usage_even_when_cumulative_is_non_monotonic ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: parse codex session token usage"
```

## Task 2: `iter_codex_sessions` and `collect_codex`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.CollectCodexTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'iter_codex_sessions'
FAILED (errors=3)
```

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/savings.py`:

```python
def iter_codex_sessions(root: str | Path):
    root_path = Path(root)
    if not root_path.exists():
        return
    yield from sorted(root_path.rglob("rollout-*.jsonl"))


def _normalized_path_text(value: str | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser())


def collect_codex(
    root: str | Path,
    since_utc: datetime | None = None,
    cwd_filter: str | None = None,
    cwd_exact: bool = False,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    normalized_filter = _normalized_path_text(cwd_filter) if cwd_filter and cwd_exact else cwd_filter

    for path in iter_codex_sessions(root):
        session = parse_codex_session(path)
        if session is None:
            continue
        if session.get("source") != "mcp":
            continue
        ts_utc = session.get("ts_utc")
        if since_utc is not None and (ts_utc is None or ts_utc < since_utc):
            continue
        cwd = session.get("cwd") or ""
        if normalized_filter:
            if cwd_exact:
                if _normalized_path_text(cwd) != normalized_filter:
                    continue
            elif normalized_filter not in cwd:
                continue
        sessions.append(session)

    return sessions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.CollectCodexTests -v`

Expected output:

```text
test_collect_codex_filters_source_since_and_cwd_substring ... ok
test_collect_codex_supports_exact_cwd_filter_with_normalized_paths ... ok
test_iter_codex_sessions_finds_rollout_jsonl_files_recursively ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: collect delegated codex sessions"
```

## Task 3: `parse_claude_transcript`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.ParseClaudeTranscriptTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'parse_claude_transcript'
FAILED (errors=6)
```

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/savings.py`:

```python
CODEX_TOOL_NAMES = {
    "mcp__codex__codex",
    "mcp__codex__codex-reply",
    "codex",
    "codex-reply",
}


def _message_from_row(row: dict[str, Any]) -> dict[str, Any]:
    message = row.get("message")
    return message if isinstance(message, dict) else {}


def _message_key(row: dict[str, Any], message: dict[str, Any], index: int) -> str:
    message_id = message.get("id")
    if isinstance(message_id, str):
        return message_id
    request_id = row.get("requestId") or message.get("requestId")
    if isinstance(request_id, str):
        return request_id
    return f"row-{index}"


def _usage_tokens(usage: dict[str, Any] | None, include_cache: bool) -> int:
    if not isinstance(usage, dict):
        return 0
    keys = ["input_tokens", "output_tokens"]
    if include_cache:
        keys.extend(["cache_creation_input_tokens", "cache_read_input_tokens"])
    return sum(value for key in keys if isinstance((value := usage.get(key)), int))


def _content_items(message: dict[str, Any]) -> list[Any]:
    content = message.get("content", [])
    if isinstance(content, list):
        return content
    return [content]


def _is_codex_tool_use(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in CODEX_TOOL_NAMES


def _contains_codex_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_codex_marker(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_codex_marker(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return "codex" in lowered or "threadid" in lowered
    return False


def _row_has_codex_tool_result(row: dict[str, Any]) -> bool:
    message = _message_from_row(row)
    for item in _content_items(message):
        if isinstance(item, dict) and item.get("type") == "tool_result":
            return _contains_codex_marker(item)
    return False


def parse_claude_transcript(
    path: str | Path,
    include_sidechains: bool = False,
    include_cache: bool = True,
) -> list[dict[str, Any]]:
    unique_messages: list[dict[str, Any]] = []
    seen_message_keys: set[str] = set()
    next_assistant_is_direct = False

    for index, row in enumerate(_read_jsonl(Path(path))):
        if not isinstance(row, dict):
            continue
        if row.get("isSidechain") and not include_sidechains:
            continue

        if _row_has_codex_tool_result(row):
            next_assistant_is_direct = True
            continue

        if row.get("type") != "assistant":
            continue

        message = _message_from_row(row)
        key = _message_key(row, message, index)
        if key in seen_message_keys:
            continue
        seen_message_keys.add(key)

        content = _content_items(message)
        codex_tool_items = [item for item in content if _is_codex_tool_use(item)]

        is_direct = bool(codex_tool_items) or next_assistant_is_direct
        next_assistant_is_direct = False

        unique_messages.append({
            "tokens": _usage_tokens(message.get("usage"), include_cache),
            "is_direct": is_direct,
            "tool_use_count": len(codex_tool_items),
        })

    direct_tokens = sum(message["tokens"] for message in unique_messages if message["is_direct"])
    tool_use_count = sum(message["tool_use_count"] for message in unique_messages)
    if direct_tokens == 0 and tool_use_count == 0:
        return []

    return [{
        "path": str(Path(path)),
        "direct_tokens": direct_tokens,
        "total_tokens": sum(message["tokens"] for message in unique_messages),
        "tool_use_count": tool_use_count,
    }]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.ParseClaudeTranscriptTests -v`

Expected output:

```text
test_counts_multiple_codex_tool_uses_without_double_counting_overhead ... ok
test_counts_next_assistant_after_codex_tool_result_as_direct_overhead ... ok
test_dedupes_split_message_and_counts_codex_tool_use_once ... ok
test_ignores_non_codex_messages_for_direct_overhead_but_keeps_session_total_when_delegation_exists ... ok
test_malformed_lines_are_skipped ... ok
test_no_cache_excludes_cache_creation_and_cache_read_tokens ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: parse claude delegation overhead"
```

## Task 4: `collect_claude`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.CollectClaudeTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'collect_claude'
FAILED (errors=2)
```

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/savings.py`:

```python
def _first_timestamp(path: Path) -> datetime | None:
    for row in _read_jsonl(path):
        if isinstance(row, dict):
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, str):
                timestamp = _message_from_row(row).get("timestamp")
            if isinstance(timestamp, str):
                return _parse_utc(timestamp)
    return None


def collect_claude(
    root: str | Path,
    since_utc: datetime | None = None,
    include_sidechains: bool = False,
    include_cache: bool = True,
) -> list[dict[str, Any]]:
    root_path = Path(root)
    if not root_path.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(root_path.rglob("*.jsonl")):
        ts_utc = _first_timestamp(path)
        if since_utc is not None and (ts_utc is None or ts_utc < since_utc):
            continue
        records.extend(parse_claude_transcript(path, include_sidechains=include_sidechains, include_cache=include_cache))

    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.CollectClaudeTests -v`

Expected output:

```text
test_collect_claude_excludes_sidechains_by_default_and_includes_when_requested ... ok
test_collect_claude_reads_project_transcripts_and_filters_since ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: collect claude overhead records"
```

## Task 5: `compute`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
class ComputeTests(SavingsTestCase):
    def test_compute_broad_uses_all_codex_sessions_and_direct_overhead(self):
        codex = [
            {"id": "codex-a", "cwd": "/repo/a", "codex_tokens": 100, "ts_utc": savings._parse_utc("2026-06-03T00:00:00Z")},
            {"id": "codex-b", "cwd": "/repo/b", "codex_tokens": 300, "ts_utc": savings._parse_utc("2026-06-03T01:00:00Z")},
        ]
        claude = [
            {"path": "one.jsonl", "direct_tokens": 50, "total_tokens": 200},
            {"path": "two.jsonl", "direct_tokens": 10, "total_tokens": 30},
        ]

        report = savings.compute(codex, claude, ks=[0.5, 1.0, 2.0])

        self.assertEqual(report["attribution"], "broad[source:mcp]")
        self.assertEqual(report["codex_session_count"], 2)
        self.assertEqual(report["codex_tokens"], 400)
        self.assertEqual(report["claude_direct_tokens"], 60)
        self.assertEqual(report["claude_total_tokens"], 230)
        self.assertEqual(report["sensitivity"], [
            {"k": 0.5, "avoided_tokens": 200, "net_savings_tokens": 140},
            {"k": 1.0, "avoided_tokens": 400, "net_savings_tokens": 340},
            {"k": 2.0, "avoided_tokens": 800, "net_savings_tokens": 740},
        ])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.ComputeTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'compute'
FAILED (errors=1)
```

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/savings.py`:

```python
DEFAULT_KS = [0.5, 1.0, 1.5, 2.0]


def _unique_by_id(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session.get("id")
        if not isinstance(session_id, str):
            continue
        if session_id in seen:
            continue
        seen.add(session_id)
        unique.append(session)
    return unique


def compute(
    codex_sessions: list[dict[str, Any]],
    claude_overhead: list[dict[str, Any]],
    ks: list[float] | tuple[float, ...] = DEFAULT_KS,
) -> dict[str, Any]:
    unique_codex = _unique_by_id(codex_sessions)
    codex_tokens = sum(session.get("codex_tokens", 0) for session in unique_codex)
    claude_direct_tokens = sum(overhead.get("direct_tokens", 0) for overhead in claude_overhead)
    claude_total_tokens = sum(overhead.get("total_tokens", 0) for overhead in claude_overhead)

    sensitivity = []
    for k_value in ks:
        avoided_tokens = int(round(codex_tokens * k_value))
        sensitivity.append({
            "k": float(k_value),
            "avoided_tokens": avoided_tokens,
            "net_savings_tokens": avoided_tokens - claude_direct_tokens,
        })

    return {
        "attribution": "broad[source:mcp]",
        "codex_session_count": len(unique_codex),
        "codex_tokens": codex_tokens,
        "claude_direct_tokens": claude_direct_tokens,
        "claude_total_tokens": claude_total_tokens,
        "sensitivity": sensitivity,
        "codex_sessions": unique_codex,
        "projects": sorted({session.get("cwd", "") for session in unique_codex if session.get("cwd")}),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.ComputeTests -v`

Expected output:

```text
test_compute_broad_uses_all_codex_sessions_and_direct_overhead ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: compute counterfactual savings"
```

## Task 6: `render`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
class RenderTests(SavingsTestCase):
    def test_render_outputs_headline_sensitivity_and_codex_session_breakdown(self):
        report = {
            "attribution": "broad[source:mcp]",
            "codex_session_count": 1,
            "codex_tokens": 1240000,
            "claude_direct_tokens": 38000,
            "claude_total_tokens": 210000,
            "projects": ["/repo/daily-news"],
            "sensitivity": [
                {"k": 0.5, "avoided_tokens": 620000, "net_savings_tokens": 582000},
                {"k": 1.0, "avoided_tokens": 1240000, "net_savings_tokens": 1202000},
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
        self.assertIn("Codex がやった仕事            : 1,240,000 tok", text)
        self.assertIn("Claude overhead (狭義 direct) : 38,000 tok", text)
        self.assertIn("Claude 全処理トークン(参考)   : 210,000 tok", text)
        self.assertIn("k=0.5  -> 582,000 tok", text)
        self.assertIn("k=1.0  -> 1,202,000 tok", text)
        self.assertIn("下限保証ではない", text)
        self.assertIn("残存ログのみが対象", text)
        self.assertIn("Codex セッション一覧（日付UTC / cwd / Codex トークン）:", text)
        self.assertIn("2026-06-03T07:29:00Z  /repo/daily-news  Codex 61,285", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.RenderTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'render'
FAILED (errors=1)
```

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/savings.py`:

```python
def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_utc(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def render(report: dict[str, Any], since_utc: datetime | None = None) -> str:
    since_text = f"since {_format_utc(since_utc)}" if since_utc else "all time"
    projects = report.get("projects", [])
    project_text = ", ".join(projects) if projects else "all projects"

    lines = [
        f"codex-orchestration 節約レポート（UTC {since_text}, attribution={report['attribution']}）",
        f"委譲セッション数: {report['codex_session_count']}   対象プロジェクト: {project_text}",
        "─────────────────────────────────────────────",
        f"Codex がやった仕事            : {_format_int(report['codex_tokens'])} tok",
        f"Claude overhead (狭義 direct) : {_format_int(report['claude_direct_tokens'])} tok",
        f"Claude 全処理トークン(参考)   : {_format_int(report['claude_total_tokens'])} tok",
        "─────────────────────────────────────────────",
        "純節約 sensitivity（仮定 k に基づく反実仮想）:",
    ]

    for row in report.get("sensitivity", []):
        lines.append(f"  k={row['k']:.1f}  -> {_format_int(row['net_savings_tokens'])} tok")

    lines.extend([
        "注: k は Claude/Codex 間の tokenizer・モデル挙動・cache 条件・委譲運用差を含む未校正係数。下限保証ではない。",
        "注: ログのローテーションや削除がある場合、残存ログのみが対象。",
        "",
        "Codex セッション一覧（日付UTC / cwd / Codex トークン）:",
    ])

    codex_sessions = report.get("codex_sessions", [])
    if codex_sessions:
        for codex in codex_sessions:
            lines.append(
                f"  {_format_utc(codex.get('ts_utc'))}  {codex.get('cwd', '')}  "
                f"Codex {_format_int(codex.get('codex_tokens', 0))}"
            )
    else:
        lines.append("  Codex sessions: 0")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.RenderTests -v`

Expected output:

```text
test_render_outputs_headline_sensitivity_and_codex_session_breakdown ... ok

OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: render savings report"
```

## Task 7: `main`

**Files:**
- Modify: `scripts/savings.py`
- Modify: `tests/test_savings.py`
- Test: `tests/test_savings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_savings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_savings.MainTests -v`

Expected output:

```text
AttributeError: module 'scripts.savings' has no attribute 'main'
FAILED (errors=2)
```

- [ ] **Step 3: Write minimal implementation**

Add to the imports in `scripts/savings.py`:

```python
import argparse
import sys
```

Add to `scripts/savings.py`:

```python
def _parse_since_arg(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        if len(value) == 10:
            return _parse_utc(value + "T00:00:00Z")
        return _parse_utc(value)
    except ValueError:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Estimate Claude token savings from Codex delegation logs.")
    parser.add_argument("--codex-root", default=str(Path.home() / ".codex"), help="Codex log root; defaults to ~/.codex")
    parser.add_argument("--claude-root", default=str(Path.home() / ".claude"), help="Claude log root; defaults to ~/.claude")
    parser.add_argument("--since", help="UTC date or datetime, for example 2026-06-01 or 2026-06-01T00:00:00Z")
    parser.add_argument("--cwd", dest="cwd_filter", help="Project cwd substring filter")
    parser.add_argument("--cwd-exact", action="store_true", help="Treat --cwd as a normalized exact path")
    parser.add_argument("--k", type=float, action="append", dest="ks", help="Counterfactual multiplier; may be repeated")
    parser.add_argument("--no-cache", action="store_true", help="Exclude Claude cache creation/read tokens from overhead")
    parser.add_argument("--include-sidechains", action="store_true", help="Include Claude sidechain transcripts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    since_utc = _parse_since_arg(args.since)
    if args.since and since_utc is None:
        parser.print_usage(sys.stderr)
        sys.stderr.write("savings.py: error: --since must be a UTC date or ISO datetime\n")
        return 2

    codex_sessions = collect_codex(
        args.codex_root,
        since_utc=since_utc,
        cwd_filter=args.cwd_filter,
        cwd_exact=args.cwd_exact,
    )
    claude_overhead = collect_claude(
        args.claude_root,
        since_utc=since_utc,
        include_sidechains=args.include_sidechains,
        include_cache=not args.no_cache,
    )
    report = compute(
        codex_sessions,
        claude_overhead,
        ks=args.ks if args.ks else DEFAULT_KS,
    )
    sys.stdout.write(render(report, since_utc=since_utc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_savings.MainTests -v`

Expected output:

```text
test_main_rejects_invalid_since_date ... ok
test_main_runs_broad_report_with_custom_roots ... ok

OK
```

Then run the full suite:

Run: `python3 -m unittest tests.test_savings -v`

Expected output:

```text
OK
```

- [ ] **Step 5: Commit**

```bash
git add scripts/savings.py tests/test_savings.py
git commit -m "feat: add savings report cli"
```

## Task 8: README / SKILL Quick Reference

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Test: Manual documentation consistency check

- [ ] **Step 1: Add README quick reference**

Insert the following section in `README.md` after the existing `## 使い方` section and before `## 編集・更新`:

```markdown
## 節約量の計測

`python3 scripts/savings.py` で残存ログ全期間を broad attribution（`source:"mcp"`）で集計する。この環境では MCP-Codex セッションが本スキルの委譲のみであることを実測確認済みで、`--since 2026-06-01` は UTC 基準の期間フィルタとして扱う。
プロジェクトは `--cwd daily-news`（部分一致）または `--cwd-exact /path/to/project`（正規化パス完全一致）で絞り込む。
出力は `k=0.5/1.0/1.5/2.0` の感度表を含み、純節約は「推定 Claude 回避量 − Claude overhead（狭義 direct）」として表示する。
`k` は tokenizer・モデル挙動・cache 条件・委譲運用差を含む未校正係数で、下限保証ではない。
```

- [ ] **Step 2: Add SKILL quick-reference pointer**

Insert the following paragraph in `SKILL.md` immediately before `## クイックリファレンス（MCP ツール）`:

```markdown
## 節約量の計測

委譲による Claude Pro 枠の推定節約量は `python3 scripts/savings.py` で計測する。`--since` は UTC 基準で、attribution は実測確認済みの broad（`source:"mcp"`）一本にする。
```

- [ ] **Step 3: Verify documentation consistency**

Check the updated text against these items:

```text
- README.md states that net savings is estimated avoided Claude tokens minus narrow direct Claude overhead.
- README.md states that the report shows k sensitivity values.
- README.md states that k is an uncalibrated coefficient and not a lower-bound guarantee.
- README.md and SKILL.md state that --since is interpreted as UTC.
- README.md and SKILL.md state that attribution is broad source:mcp because MCP-Codex sessions are confirmed to be this skill's delegations in this environment.
```

- [ ] **Step 4: Commit**

```bash
git add README.md SKILL.md
git commit -m "docs: add savings calculator quick reference"
```

## Self-Review

- Spec coverage: §6.1 Codex token logic is assigned to Task 1; §6.2 Claude overhead and cache behavior are assigned to Task 3; §6.3 attribution is broad (`source:"mcp"`) because this environment has verified that MCP-Codex sessions are only this skill's delegations, so strict matching is unnecessary; UTC `--since`, `--cwd`, and `--cwd-exact` are assigned to Tasks 2 and 7; §6.4 counterfactual sensitivity is assigned to Task 5 and rendered in Task 6; §11 README/SKILL quick-reference documentation is assigned to Task 8.
- Component coverage: §7 functions are covered by task components: `parse_codex_session` in Task 1, `iter_codex_sessions` and `collect_codex` in Task 2, `parse_claude_transcript` in Task 3, `collect_claude` in Task 4, `compute` in Task 5, `render` in Task 6, and `main` in Task 7. The original `link` component is intentionally omitted as YAGNI for this verified environment.
- Edge-case coverage: §9 cases are covered by tests for non-monotonic cumulative usage, absent token data, absent or non-`mcp` source, split Claude messages, multiple Codex tool uses in one Claude message, `codex-reply` overhead counting, sidechain exclusion and optional inclusion, malformed JSONL skipping, UTC date filtering, and project filtering.
- Test coverage: §10 items are allocated across Tasks 1 through 5, with CLI, rendering, and documentation behavior covered in Tasks 6 through 8. Fixtures use temporary directories and inline JSONL rows, never real `~/.codex` or `~/.claude`.
- Placeholder scan: The plan contains concrete paths, commands, expected outputs, and full code blocks for each test and implementation step. It does not rely on unspecified future work.
- Type consistency: Later tasks use the same dict keys introduced earlier: `id`, `source`, `cwd`, `ts_utc`, `codex_tokens`, `direct_tokens`, `total_tokens`, `tool_use_count`, `codex_sessions`, and `sensitivity`.
- Scope decision: Task 8 covers design spec §11 by adding README/SKILL quick-reference documentation without expanding the implementation beyond `scripts/savings.py` and `tests/test_savings.py`.

## Execution Handoff

This plan contains 8 consecutive tasks. Two execution options:

1. **Subagent-Driven (recommended)** - Use `superpowers:subagent-driven-development` and dispatch one fresh worker per task, reviewing each task before continuing.
2. **Inline Execution** - Use `superpowers:executing-plans` and execute the tasks in this session with checkpoints between task batches.
