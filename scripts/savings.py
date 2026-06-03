from __future__ import annotations

import argparse
import json
import sys
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


def _usage_container(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info")
    return info if isinstance(info, dict) else payload


def _nested_total_tokens(payload: dict[str, Any], key: str) -> int | None:
    usage = _usage_container(payload).get(key)
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


def _codex_tool_use_id(item: Any) -> str | None:
    if not _is_codex_tool_use(item):
        return None
    tool_use_id = item.get("id")
    return tool_use_id if isinstance(tool_use_id, str) else None


def _row_has_codex_tool_result(row: dict[str, Any], codex_tool_use_ids: set[str]) -> bool:
    message = _message_from_row(row)
    for item in _content_items(message):
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_result"
            and item.get("tool_use_id") in codex_tool_use_ids
        ):
            return True
    return False


def parse_claude_transcript(
    path: str | Path,
    include_sidechains: bool = False,
    include_cache: bool = True,
) -> list[dict[str, Any]]:
    unique_messages: list[dict[str, Any]] = []
    seen_message_keys: set[str] = set()
    codex_tool_use_ids: set[str] = set()
    next_assistant_is_direct = False

    for index, row in enumerate(_read_jsonl(Path(path))):
        if not isinstance(row, dict):
            continue
        if row.get("isSidechain") and not include_sidechains:
            continue

        if _row_has_codex_tool_result(row, codex_tool_use_ids):
            next_assistant_is_direct = True
            continue

        if row.get("type") != "assistant":
            continue

        message = _message_from_row(row)
        content = _content_items(message)
        codex_tool_items = [item for item in content if _is_codex_tool_use(item)]
        for tool_use_id in (_codex_tool_use_id(item) for item in codex_tool_items):
            if tool_use_id is not None:
                codex_tool_use_ids.add(tool_use_id)

        key = _message_key(row, message, index)
        if key in seen_message_keys:
            continue
        seen_message_keys.add(key)

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
