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
