# Token Savings Calculator — 設計書

- 日付: 2026-06-03
- 対象スキル: `codex-orchestration`
- 目的: このスキルによる委譲で「Claude（Pro 枠）のトークン消費がどれだけ抑えられたか」を計測する

## 1. 背景とゴール

`codex-orchestration` は、トークンの重い実行を Claude（Pro 枠＝希少）から Codex（サブスク枠＝大）へ委譲するスキル。
「どれだけ Pro 枠を節約できたか」を数字で出したい。

**測りたい指標（合意済み）**: **反実仮想の推定（Claude 換算）**
= 「もし Claude が同じ作業をインラインでやっていたら使ったはずのトークン」を推定し、実際に払った分を差し引いた純節約。

**対象期間（合意済み）**: 過去の一括集計 ＋ 今後の継続計測の両方。
ログが数か月分残るため、**同じスクリプトを再実行**すれば継続計測になる（専用の台帳は持たない）。

## 2. スコープ / 非スコープ

**スコープ**
- 既存ログ（Codex セッション ＋ Claude transcript）からの集計。
- 反実仮想推定（換算係数 `k` 付き）と純節約の算出。
- 概算 USD の併記。トークン表示は維持し、USD は補助値として扱う。
- 過去一括 ＋ 再実行による継続計測。

**非スコープ（YAGNI）**
- スキルのプロトコル改変（委譲時の台帳ロギング等）は行わない。`source:"mcp"` で attribution が解けるため不要。
- ダッシュボード UI / 常駐デーモン。
- 厳密な請求額の再現。overhead は実 transcript の model 別 token から実コスト寄りに概算するが、
  回避分は反実仮想なので仮定値として表示する。

## 3. データソース（実在を確認済み）

### 3.1 Codex 側 — 委譲ぶんの「仕事量」
- 場所: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（1 セッション = 1 ファイル、JSONL）。
- 先頭行 `type:"session_meta"` の `payload` に:
  - `source`: `"mcp"`（= `codex` MCP ツール経由 = **このスキルの委譲**）/ `"exec"` / `"cli"` 等。
  - `cwd`: 作業ディレクトリ（プロジェクト絞り込み用）。
  - `id`: セッション ID（`codex-reply` 継続の名寄せに使用）。
  - `timestamp`: 開始時刻。
- token 情報は `type:"event_msg"` 行の **`payload.info`** の下にある（`payload` 直下ではない点に注意）:
  - `payload.info.total_token_usage.total_tokens` … **累積値**（ターン進行で増える）。
  - `payload.info.last_token_usage.total_tokens` … **そのターン単体**。
  - 実装は `payload.info` を優先解決し、無い場合のみ `payload` 直下に fallback する（後方互換）。

### 3.2 Claude 側 — 委譲の「オーバーヘッド」
- 場所: `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`（JSONL）。
- 各 assistant メッセージに `usage`:
  `{input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, ...}`。
- 委譲に関与したターン = `codex` / `codex-reply` の `tool_use` を含むメッセージとその結果処理ターン。

## 4. アプローチ

採用: **ログマイニング分析スクリプト 1 本**（標準ライブラリのみ、依存なし）。
プロトクル無改変・台帳なし。過去集計も、再実行で継続計測も同じスクリプトで賄う。

却下した代替:
- **前向き台帳**（委譲時にロギング）: `source:"mcp"` で attribution が既に解けるため主目的が冗長。未来分しか貯まらず、スキルが自分のターンのトークンを正確に自己計測するのも難しい。
- **ハイブリッド**: 部品が増えるだけで①以上の価値が薄い。

## 5. オーバーヘッドの定義

委譲そのもののために Claude（Pro 枠）が消費するトークン:
1. 委譲ブリーフの作成（出力）
2. `codex` ツール呼び出し
3. Codex の戻り要約の読み取り（入力）
4. `git diff` 再レビュー（入力＋出力, 承認ゲート②）
5. 上記が乗った会話コンテキストの累積

境界が曖昧なため **2 値で扱う**（詳細は 6.2）:
- **狭義 direct overhead**: 上記 1〜4 に直接対応するメッセージのみ。純節約の計算に使う。
- **全処理トークン（参考）**: 5 の文脈累積を含む上限的指標。

```
純節約 = 推定 Claude 回避量 − Claude オーバーヘッド（狭義 direct）
```

## 6. 計測ロジック

### 6.1 Codex 消費トークン（セッション単位）
- セッションログには 2 種の token 情報があることを確認済み:
  - `total_token_usage`: **累積**（ターン進行で増える）。
  - `last_token_usage`: **そのターン単体**の消費。
- **既定 = `last_token_usage.total_tokens` の総和**（増分和）。compaction やモデル/コンテキスト reset で
  累積値が非単調になっても歪まないため。
- `last_token_usage` が全ターンに無いセッションは、**最終ターンの累積 `total_token_usage.total_tokens` に fallback**。
- どちらも欠落するセッションは集計対象外（後述エッジケース）。

### 6.2 Claude オーバーヘッド
- `~/.claude/projects/**/*.jsonl` を走査。
- **dedupe（重要）**: 同一 assistant メッセージが複数 JSONL 行に分割される（ストリーミング等）。
  行単位で `usage` を合算すると二重計上になるため、**`message.id`（無ければ `requestId`）でグルーピングし、
  メッセージ単位で 1 回だけ**カウントする。
- **2 ビューで表示**（境界の曖昧さを正直に出す）:
  - **狭義 direct overhead**: `mcp__codex__codex` / `codex-reply` の `tool_use` を含むメッセージ＋その tool_result を
    処理する直後のメッセージのみ。委譲の「手間賃」に近い。
  - **全処理トークン**: 委譲が関与したセッション全体で処理した token（巨大な会話コンテキスト累積を含む上限的指標）。
  純節約の既定計算には **狭義 direct overhead** を用い、全処理トークンは参考表示。
- トークン数え方 = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens`。
  （`--no-cache` 指定時は `input_tokens + output_tokens` のみ＝キャッシュ分を除外。）
- USD 計算では、上記 4 種別を合算前に保持し、`message.model` を価格表キーに解決して
  `input×input_rate + output×output_rate + cache_creation×cache_write_rate + cache_read×cache_read_rate`
  を per MTok レートで課金する。`--no-cache` 指定時は token と USD の両方から cache creation/read を除外する。

### 6.3 attribution（突き合わせ）
**broad 一本**（`source == "mcp"` を委譲とみなす総和集計）。

- **実測の裏づけ**: この環境の全セッションを調べたところ、`source:"mcp"` は 9 件すべてが
  Claude Code のプロジェクト配下（daily-news worktree / codex-orchestration / marp-repo）からの委譲で、
  別 MCP クライアントの混入はゼロだった（他は `vscode`/`exec`/`cli`）。よって `source:"mcp"` ≒ 本スキルの委譲。
- このため threadId 照合による厳密化（`--strict`）は **YAGNI として持たない**。
  将来、別ツールから codex MCP を叩き始めて過大カウントが問題になった場合にのみ再検討する。
- 期間フィルタ: `--since`。**Codex の timestamp は UTC** なので、引数も **UTC 基準**で解釈する旨を明記
  （JST の 06-03 早朝が UTC では 06-02 に見えるズレに注意）。既定は無指定＝全期間。
- プロジェクトフィルタ: `--cwd <substr>`（部分一致）。過剰一致を避けたい時は `--cwd-exact`（正規化 path 完全一致）。
- 同一 Codex `id`（`codex-reply` の継続）は 1 セッションに名寄せして二重計上しない。

### 6.4 反実仮想（Claude 換算）
```
推定 Claude 回避量 = k × (Codex 委譲ぶんの総消費トークン)
純節約            = 推定 Claude 回避量 − Claude オーバーヘッド総和（狭義 direct）
```
- `k`: 「同じ仕事を Claude がやったら Codex の何倍トークンを使うか」の換算係数。
- **`k=1.0` は「下限」ではなく「未校正の中立 baseline」**。`k` は単なるトークナイザ差ではなく、
  モデルの問題解決効率差・Codex 側の tool/read/write 試行回数・委譲ブリーフ・base instructions・
  cache 条件・「要約だけ返す」運用による Claude 入力削減などを**すべて吸収する未校正係数**。
  Claude が Codex より少ないトークンで済む場合もあり得るため、下限保証はない。
- レポートは単一値でなく **感度表を既定表示**: `k = 0.5 / 1.0 / 1.5 / 2.0`。注記で「仮定 k に基づく反実仮想」と明記。
- `--k` で基準値を上書き可能。
- 校正方法（任意・将来）: 代表タスクを「Claude インライン」と「Codex 委譲」両方で走らせ、
  `r = Claudeトークン / Codexトークン` を実測して `k=r` に差し替える。

### 6.5 USD レイヤー

USD は token 指標の補助値として併記する。トークン表示は既存のまま維持する。

**overhead USD（実コスト寄り）**
- Claude transcript の direct overhead assistant メッセージごとに `message.model` と
  `message.usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` を読む。
- モデル別・4 種別別の per MTok レートで課金し、direct overhead 合算を `overhead_usd` として表示する。
- 参考値の「Claude 全処理トークン」も同じ方法で `total_usd` を併記する。

**回避分 USD（反実仮想）**
- Codex 側には Claude の model や入出力内訳が無いため、`avoided_usd(k) = codex_tokens × k × counterfactual_input_rate / 1e6`
  とする。
- 既定の反実仮想モデルは `claude-opus-4-5`（input $5/MTok）。入力レートのみで保守的に概算し、
  出力があれば上振れすることをレポートに注記する。
- `net_savings_usd(k) = avoided_usd(k) − overhead_usd` を k 感度行ごとに表示する。

**価格表**
- 出典: `https://platform.claude.com/docs/about-claude/pricing`
- 取得日: 2026-06-03
- per MTok USD、5 分 cache write、cache read = hit。

| 価格表キー | input | cache_write | cache_read | output |
|---|---:|---:|---:|---:|
| `claude-opus-4-8` / `claude-opus-4-7` / `claude-opus-4-6` / `claude-opus-4-5` | 5.00 | 6.25 | 0.50 | 25.00 |
| `claude-opus-4-1` / `claude-opus-4` | 15.00 | 18.75 | 1.50 | 75.00 |
| `claude-sonnet-4-6` / `claude-sonnet-4-5` / `claude-sonnet-4` | 3.00 | 3.75 | 0.30 | 15.00 |
| `claude-haiku-4-5` | 1.00 | 1.25 | 0.10 | 5.00 |
| `claude-3-5-haiku` | 0.80 | 1.00 | 0.08 | 4.00 |

**CLI**
- `--pricing PATH`: JSON で内蔵価格表を上書きマージする。
- `--counterfactual-model NAME`: 回避分 USD に使う反実仮想モデルを切り替える。既定は `claude-opus-4-5`。

**モデル解決**
- `message.model` は最も具体的な価格表キーを優先して部分一致する。
  優先順は opus 4.8 → 4.7 → 4.6 → 4.5 → 4.1 → 4、sonnet 4.6 → 4.5 → 4、haiku 4.5、haiku 3.5。
- 解決できないモデルは `claude-sonnet-4-5` で fallback 課金し、fallback 対象 token 数と model 名をレポートに注記する。

## 7. コンポーネント分割

`scripts/savings.py`（CLI、標準ライブラリのみ）。純粋関数で分割し単体テスト可能にする。

| 関数 | 役割 | 入力 | 出力 |
|---|---|---|---|
| `iter_codex_sessions(root)` | セッションファイル列挙 | ルートパス | パスのイテレータ |
| `parse_codex_session(path)` | メタ＋トークン抽出（増分和／fallback） | パス | `{id, source, cwd, ts_utc, codex_tokens}` |
| `collect_codex(root, since_utc, cwd_filter, cwd_exact)` | mcp 委譲のみ集約（id 名寄せ） | フィルタ | セッション dict の list |
| `parse_claude_transcript(path)` | `message.id` dedupe→委譲ターンの usage 抽出（direct/全処理 の2値） | パス | overhead レコード list |
| `collect_claude(root, since_utc)` | 全 transcript 集約 | フィルタ | overhead レコード list |
| `compute(codex, claude, ks)` | 反実仮想・純節約を複数 k で算出（token＋USD） | 集計＋k 群 | レポート用 dict |
| `render(report)` | 感度表＋Codex セッション一覧の整形（token＋USD） | dict | str |
| `main(argv)` | CLI 引数処理 | argv | 終了コード |

CLI 例:
```
python3 scripts/savings.py --since 2026-06-01 --cwd daily-news        # broad, UTC基準, 感度表
python3 scripts/savings.py --cwd-exact /Users/yumaohno/daily-news
python3 scripts/savings.py --counterfactual-model claude-sonnet-4-5
python3 scripts/savings.py --pricing ./pricing-overrides.json
python3 scripts/savings.py            # 全期間・全プロジェクト・broad・k 既定群(0.5/1/1.5/2)
```

## 8. 出力イメージ

```
codex-orchestration 節約レポート（UTC 2026-06-02〜06-03, attribution=broad[source:mcp]）
委譲セッション数: 7   対象プロジェクト: daily-news, ...
─────────────────────────────────────────────
Codex がやった仕事            : 1,240,000 tok   (≈ $6.20 反実仮想 k=1.0)
Claude overhead (狭義 direct) :    38,000 tok   (≈ $0.42 実コスト概算)
Claude 全処理トークン(参考)   :   210,000 tok   (≈ $0.85 実コスト概算)
─────────────────────────────────────────────
純節約 sensitivity（仮定 k に基づく反実仮想）:
  k=0.5  ->   582,000 tok   (≈ $2.68)
  k=1.0  -> 1,202,000 tok   (≈ $5.78)
  k=1.5  -> 1,822,000 tok   (≈ $8.88)
  k=2.0  -> 2,442,000 tok   (≈ $11.98)
注: k は Claude/Codex 間の tokenizer・モデル挙動・cache 条件・委譲運用差を含む未校正係数。下限保証ではない。
注: 回避分USDは反実仮想 claude-opus-4-5 入力レートのみの概算（出力があれば上振れ）。overhead USDは実transcriptのmodel別4種別課金。

Codex 委譲セッション一覧（日付UTC / cwd / Codex トークン）:
  2026-06-03 07:29Z  daily-news/.claude/worktrees/...  Codex 61,285
  ...
```

## 9. エッジケース / 注意

- **トークン currency の差**: Claude と Codex はトークナイザ/モデルが異なる。合算は近似であり、`k` で吸収する旨をレポートに明記。
- **USD の性質差**: overhead USD は実 transcript の model 別 token からの実コスト寄り概算。回避分 USD は
  「もし Claude でやっていたら」の反実仮想であり、既定では Opus 4.5 の input レートだけを使う。
- **未価格モデル**: `<synthetic>` 等、価格表に解決できない `message.model` は `claude-sonnet-4-5` で fallback 課金し、
  fallback 対象 token 数と model 名を注記する。
- **累積の非単調**: compaction/コンテキスト reset で `total_token_usage` が減ることがある。6.1 の増分和（`last_token_usage`）を既定にして回避。
- **token 情報欠落セッション**: `last_token_usage` も `total_token_usage` も無いものは集計対象外。
- **`source` キー欠落の古いセッション**: `source` 不明は集計対象外（mcp と確証できないため）。
- **同一 Claude メッセージ内の複数 `codex` 呼び出し**: メッセージ単位 dedupe 後、tool_use 数ぶんの委譲として扱う（overhead は二重計上しない）。
- **`codex-reply` の継続**: 同一 Codex `id` の継続は 1 セッションとして集計し、別セッションに重複カウントしない。
- **sidechain / subagent transcript**: 既定では除外（委譲の overhead は親セッションに現れるため）。`--include-sidechains` で任意包含。
- **malformed / 途中切れ JSONL**: 行単位 try/except でスキップし、壊れた 1 行で全体を落とさない。
- **タイムゾーン**: Codex timestamp は UTC。`--since` も UTC 解釈、表示も UTC 明示（`Z` 付き）。
- **複数プロジェクト混在**: 既定は全プロジェクト合算、`--cwd`(部分一致)/`--cwd-exact`(完全一致) で絞り込み。
- **ログのローテーション/削除**: 残存ログのみが対象（明記）。

## 10. テスト方針（TDD）

- `parse_codex_session`: `last_token_usage` 有り fixture → 増分和 / 無し fixture → 最終累積へ fallback / 両欠落 → 除外。
- `parse_codex_session`: 累積が非単調（compaction）な fixture でも増分和が正しい。
- `collect_codex`: `source`（mcp/exec/cli/欠落）混在・期間(UTC)・cwd(部分/完全)フィルタの分岐。
- `parse_claude_transcript`: 同一 `message.id` が複数行に分割された fixture → 1 回だけ計上（dedupe）。
- `parse_claude_transcript`: `codex` tool_use を含むメッセージだけ direct overhead に拾う / 含まないものは無視。
- `parse_claude_transcript`: 1 メッセージ内に複数 `codex` tool_use があっても overhead を二重計上しない。
- `collect_codex`: 同一 `id`（`codex-reply` 継続）が複数ファイルに跨っても 1 セッションに名寄せ。
- `compute`: k=0.5/1.0/1.5/2.0 の感度値、純節約＝推定回避量−狭義 direct overhead が定義通り。
- `parse_claude_transcript`: 実構造 fixture（`message.model` と `message.usage` 4 キー）から model 別 4 種別 USD が定義通り。
- `parse_claude_transcript`: `--no-cache` 相当では cache creation/read を token と USD の両方から除外する。
- `parse_claude_transcript` / `compute` / `render`: 未価格モデル fallback の token/model 注記。
- `compute`: `--counterfactual-model` 相当で回避分 USD の input レートが切り替わる。
- malformed JSONL 行を含む fixture → スキップして他行は集計継続。
- すべて小さな JSONL fixture（実ログの一部を模した最小データ）で検証。実ホームのログには依存しない。

## 11. 配置 / 成果物

- `scripts/savings.py` — 本体。
- `tests/test_savings.py` — 単体テスト（fixture 同梱）。
- `README.md` / `SKILL.md` に使い方を 1〜2 行追記（クイックリファレンス）。
