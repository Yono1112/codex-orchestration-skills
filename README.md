# codex-orchestration

Claude Code（親・オーケストレーター）から Codex CLI（子・実働）へ、トークンの重い作業を委譲するための個人スキル。Claude の Pro 枠を温存し、Codex のサブスク枠（5時間／週間枠が大きい）に重作業を逃がす。

- 方式: Codex を MCP サーバ（`codex mcp-server`）として Claude Code に登録し、`codex` / `codex-reply` ツールで委譲
- スキル本体: [`SKILL.md`](./SKILL.md)（委譲プロトコル）
- 配置: このリポジトリを git 管理の実体とし、`~/.claude/skills/codex-orchestration` から **symlink** で参照する

## セットアップ

```bash
# 1) Codex を MCP サーバとして user スコープ登録（全プロジェクトで有効・一度きり）
claude mcp add -s user codex -- codex mcp-server

# 2) このリポジトリを ~/.claude/skills に symlink（プレーンスキルとして読み込ませる）
ln -s "$(pwd)" ~/.claude/skills/codex-orchestration

# 3) Claude Code を再起動して読み込みを確認（/plugin や実セッションで /codex-orchestration が見えるか）
```

前提:

- Codex CLI が ChatGPT 認証でログイン済みであること（`codex doctor` で `stored auth mode: chatgpt` を確認）。これによりサブスク枠で動き、API 課金が発生しない。
- Claude Code v2.1 以降、Codex CLI 0.x 以降。

## 使い方

普段どおり Claude Code を使う。トークンの重い実行（機能実装・TDD・広域調査・大きい差分のレビュー）に差し掛かると、このスキルに従って Claude が「Codex に委譲していいか」を確認し、承認後に Codex へ委譲、戻ってきた差分を Claude が再レビューする。superpowers の委譲ポイント（`executing-plans` など）の最中にも差し込める。

## 編集・更新

- `SKILL.md` を編集すれば（symlink 経由でも）テキストはライブ反映される。
- このリポジトリで git 管理する。別端末へは clone し、上記セットアップの 1) と 2) を実行すれば同じ状態を再現できる。

## アンインストール

```bash
rm ~/.claude/skills/codex-orchestration      # symlink を外す（実体は消えない）
claude mcp remove -s user codex              # MCP 登録を外す
```
