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
