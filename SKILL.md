---
name: codex-orchestration
description: Use when Claude is about to spend significant tokens on execution — implementing a full feature, running TDD cycles, broad codebase exploration/search, or reviewing a large diff — and you want to preserve Claude's limited Pro quota. Also use at superpowers delegation points (executing-plans, subagent-driven-development, dispatching-parallel-agents) when the worker could be Codex instead of a Claude subagent. Codex CLI runs on a separate, much larger subscription quota and is callable via its MCP tools (codex, codex-reply).
---

# Codex Orchestration

## 概要

Claude（Pro＝枠が希少）は**オーケストレーター**（設計・仕様・最終レビュー）に徹し、**トークンを大量に消費する実行**を Codex（サブスク枠＝大）に委譲する。Codex は MCP サーバ `codex` 経由で呼ぶ（ツール: `codex` / `codex-reply`）。ChatGPT 認証なら API 課金は発生せず、サブスク枠で動く。

**前提**: `codex` MCP サーバが登録済みであること（`claude mcp add -s user codex -- codex mcp-server`）。ツールが見えない時は登録を確認する。

## 使う / 使わない

**使う**
- 機能まるごとの実装、TDD サイクル、広域の探索・調査、大きい差分のレビュー
- superpowers が「サブエージェントに投げる」と判断する場面（下記）

**使わない**
- 設計・仕様策定・最終判断（Claude の頭脳の仕事）
- 数行の編集や軽い質問（委譲オーバーヘッドの方が高い）

## superpowers の委譲ポイントに差し込む（このスキルの中核）

`executing-plans` / `subagent-driven-development` / `dispatching-parallel-agents` は、既定では **Claude のサブエージェント**にワーカーを投げる＝**同じ Pro 枠を消費**する。トークンの重い実行は、ワーカーの行き先を **Codex** に向ける。Claude は司令塔／レビュアーのまま動かない。

Codex が返したら、中断していた superpowers のフローに**そのまま復帰**する（次の計画ステップへ、または spec 準拠レビュー → コード品質レビューへ）。委譲はフロー内の1ステップであって、フローの置き換えではない。

## 委譲プロトコル（半自動・承認ゲート型）

1. **承認ゲート①**: 委譲する前に人へ確認する（「これ Codex に投げていい?」）。
2. **委譲ブリーフ**を組んで `codex` ツールを呼ぶ（テンプレ下記）。会話文脈＝設計判断とその理由を要約して必ず渡す。
3. **sandbox をタスクで選ぶ**（下表）。`cwd` にリポジトリのルートを渡す。
4. Codex には「**詳細はファイルに書き、戻りは要約のみ**」を守らせる（トークン規律）。
5. 続き・手直しは `codex-reply`（`threadId` ＝ `codex` の戻り値）で往復する。Codex 側の文脈が維持される。
6. **承認ゲート②**: Codex が返したら、Claude が `git diff` と要約を**再レビュー**してから完了とする。

## 委譲ブリーフ テンプレ（`codex` ツールの `prompt`）

```
## ゴール
## 受け入れ条件
## 対象ファイル / パス
## 設計判断と理由（このセッションの文脈）
## 規律（重要: Codex は superpowers を持たない。毎回明示する）
- 例: TDD で進める。まず落ちるテストを書き、最小実装、リファクタ。
## 返し方
- 詳細は <file> に書く。返信は簡潔な要約のみ（変更点 / 場所 / 検証方法）。
- 大きなファイル本文を返信に貼らない。
```

## sandbox / approval（タスク別）

| タスク | sandbox | approval-policy |
|---|---|---|
| 実装 / TDD / 修正 | `workspace-write` | `on-failure` |
| レビュー / 分析 / 調査 | `read-only` | `never` |

`danger-full-access` は使わない。人間の承認は「委譲前の確認（ゲート①）」と「差分の再レビュー（ゲート②）」で担保するので、Codex 自体は非対話で走らせる。

## よくある失敗

- Codex の出力全文を Claude に貼り戻す → 節約が台無し。ファイル出力＋要約に徹する。
- ブリーフに規律（TDD 等）を書き忘れる → Codex は superpowers 流に従わない。必ず明示する。
- レビュー作業に `workspace-write` を使う → `read-only` にする。
- 承認ゲート①／②を飛ばす → 半自動が崩れる。委譲前の確認と差分の再レビューは必須。
- 手直しで新規 `codex` セッションを開く → 文脈が切れる。`codex-reply` ＋ `threadId` で継続する。

## クイックリファレンス（MCP ツール）

- `codex(prompt, sandbox, approval-policy, cwd, model, config)` … 新規セッション開始。`threadId` を返す。
- `codex-reply(threadId, prompt)` … 既存セッションの継続（`conversationId` は deprecated、`threadId` を使う）。
