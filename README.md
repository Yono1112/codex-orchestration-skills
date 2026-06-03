# codex-orchestration

Claude Code（親・オーケストレーター）から Codex CLI（子・実働）へ、トークンの重い作業を委譲するための個人スキル。Claude の Pro 枠を温存し、Codex のサブスク枠（5時間／週間枠が大きい）に重作業を逃がす。

- 方式: Codex を MCP サーバ（`codex mcp-server`）として Claude Code に登録し、`codex` / `codex-reply` ツールで委譲
- スキル本体: [`SKILL.md`](./SKILL.md)（委譲プロトコル）
- 設計・経緯: [`DESIGN.md`](./DESIGN.md)（採用方式の判断と検証結果）
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

## 節約量の計測

`python3 scripts/savings.py` で残存ログ全期間を broad attribution（`source:"mcp"`）で集計する。この環境では MCP-Codex セッションが本スキルの委譲のみであることを実測確認済みで、`--since 2026-06-01` は UTC 基準の期間フィルタとして扱う。
プロジェクトは `--cwd daily-news`（部分一致）または `--cwd-exact /path/to/project`（正規化パス完全一致）で絞り込む。
出力は `k=0.5/1.0/1.5/2.0` の感度表を含み、純節約は「推定 Claude 回避量 − Claude overhead（狭義 direct）」として表示する。
token 表示に加えて概算 USD も併記する。overhead は実 transcript の model 別 4 種別課金、回避分は `--counterfactual-model`（既定 `claude-opus-4-5`）の input レートによる反実仮想。
`k` は tokenizer・モデル挙動・cache 条件・委譲運用差を含む未校正係数で、下限保証ではない。

## 編集・更新

- `SKILL.md` を編集すれば（symlink 経由でも）テキストはライブ反映される。
- このリポジトリで git 管理する。別端末へは clone し、上記セットアップの 1) と 2) を実行すれば同じ状態を再現できる。

## アンインストール

```bash
rm ~/.claude/skills/codex-orchestration      # symlink を外す（実体は消えない）
claude mcp remove -s user codex              # MCP 登録を外す
```
