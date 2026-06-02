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
3. **sandbox をタスクで選ぶ**（下表）。`cwd` は通常**省略でよい**（CC を起動したディレクトリに解決される）。別リポを触る時・並列で走らせる時だけ明示する（下記「作業ディレクトリ」「並列ファンアウト」を参照）。
4. Codex には「**詳細はファイルに書き、戻りは要約のみ**」を守らせる（トークン規律）。
5. 続き・手直しは `codex-reply`（`threadId` ＝ `codex` の戻り値）で往復する。Codex 側の文脈が維持される。
6. **承認ゲート②**: Codex が返したら、Claude が `git diff` と要約を**再レビュー**してから完了とする。

## 委譲ブリーフ テンプレ（`codex` ツールの `prompt`）

```
## ゴール
## 受け入れ条件
## 対象ファイル / パス
## 設計判断と理由（このセッションの文脈）
## 規律（重要: Codex 側に superpowers が入っていても MCP 経由起動では発火保証なし。重要な規律は明示）
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

## 作業ディレクトリ（cwd）

`cwd` 省略時は `codex mcp-server` プロセスの cwd ＝ **CC を起動したディレクトリ**に解決される（常駐プロセスなので起動時に固定）。

- 通常（1リポ・逐次委譲）は**省略でよい**。
- **別リポを触る／並列で複数セッションを走らせる**時だけ `cwd` を明示する。

## 並列ファンアウト（worktree で衝突を防ぐ）

重い実装を複数同時に分散する時は、**セッションごとに git worktree を切り、それぞれを `cwd` に渡す**。
同じ作業ツリーを複数の `workspace-write` セッションで共有すると、ファイルと git index が衝突する。worktree なら作業ファイル・index・HEAD が独立する。

1. CC がタスクごとに worktree を作る:
   ```
   git worktree add ../wt-taskA -b codex/taskA
   git worktree add ../wt-taskB -b codex/taskB
   ```
2. 各 Codex 呼び出しに別 worktree を `cwd` で渡し、1ターンで並行発行（`workspace-write`）。
3. CC が各ブランチを `git diff` でレビュー → 採用ぶんをマージ。
4. `git worktree remove ../wt-taskA` で後片付け。

`read-only` の並列（複数箇所の調査・レビュー）は worktree 不要。サブスク枠の消費とレート制限に注意し、本数は絞る。

## よくある失敗

- Codex の出力全文を Claude に貼り戻す → 節約が台無し。ファイル出力＋要約に徹する。
- ブリーフに規律（TDD 等）を書き忘れる → Codex に superpowers が入っていても、MCP 経由（`codex mcp-server`）のセッションで session_start フックが確実に発火するとは限らない。重要な規律はブリーフにも明示して二重化する。
- レビュー作業に `workspace-write` を使う → `read-only` にする。
- 承認ゲート①／②を飛ばす → 半自動が崩れる。委譲前の確認と差分の再レビューは必須。
- 手直しで新規 `codex` セッションを開く → 文脈が切れる。`codex-reply` ＋ `threadId` で継続する。
- 並列 `workspace-write` を**同じ作業ツリー**で走らせる → ファイル/git が衝突する。worktree を切って別 `cwd` に分ける。

## クイックリファレンス（MCP ツール）

- `codex(prompt, sandbox, approval-policy, cwd, model, config)` … 新規セッション開始。`threadId` を返す。
- `codex-reply(threadId, prompt)` … 既存セッションの継続（`conversationId` は deprecated、`threadId` を使う）。
