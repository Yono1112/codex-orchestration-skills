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
- 過去一括 ＋ 再実行による継続計測。

**非スコープ（YAGNI）**
- スキルのプロトコル改変（委譲時の台帳ロギング等）は行わない。`source:"mcp"` で attribution が解けるため不要。
- ダッシュボード UI / 常駐デーモン。
- 厳密な課金額（USD）換算。トークン数のみ扱う。

## 3. データソース（実在を確認済み）

### 3.1 Codex 側 — 委譲ぶんの「仕事量」
- 場所: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（1 セッション = 1 ファイル、JSONL）。
- 先頭行 `type:"session_meta"` の `payload` に:
  - `source`: `"mcp"`（= `codex` MCP ツール経由 = **このスキルの委譲**）/ `"exec"` / `"cli"` 等。
  - `cwd`: 作業ディレクトリ（プロジェクト絞り込み用）。
  - `id`: セッション ID（Claude 側 `threadId` との突き合わせ候補）。
  - `timestamp`: 開始時刻。
- 各ターンに token 情報（`input_tokens` / `output_tokens` / `total_tokens`）。
  - **重要**: これらは**累積値**（ターンが進むと `input_tokens` が膨らむ）。単純合算は二重計上になる。

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

```
純節約 = 推定 Claude 回避量 − Claude オーバーヘッド
```

## 6. 計測ロジック

### 6.1 Codex 消費トークン（セッション単位）
- 二重計上を避けるため、各セッションについて **最終ターンの累積 `total_tokens`** を採用する
  （= そのセッションが消費したトークンの近似）。
- 累積でなくターンごとに独立に記録される版に備え、実装時に「累積か独立か」を最初の数ターンで判定し、
  - 累積なら最終値、
  - 独立なら総和、
  を取るフォールバックを持たせる。

### 6.2 Claude オーバーヘッド
- `~/.claude/projects/**/*.jsonl` を走査。
- `codex` / `codex-reply` の `tool_use` を含む assistant ターン、およびその直後の tool_result を処理するターンの
  `usage` を「委譲オーバーヘッド」として合算する。
- 既定のトークン数え方 = **全処理トークン**:
  `input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens`。
  （`--no-cache` 指定時は `input_tokens + output_tokens` のみ＝キャッシュ分を除外。）

### 6.3 attribution（突き合わせ）
- 一次フィルタ: Codex セッションの `source == "mcp"`。
- 期間フィルタ: `--since`（既定: スキル初コミット日 2026-06-03、または無指定で全期間）。
- プロジェクトフィルタ: `--cwd <substr>`（任意, `cwd` 部分一致）。
- セッション×Claude ターンの 1:1 紐付けは **best-effort**:
  - Codex `id` ↔ Claude tool_result 内の `threadId` が一致すれば厳密リンク。
  - 取れない場合は時刻近接でゆるく対応付け、ヘッドライン集計（総和）は紐付け不要で算出。

### 6.4 反実仮想（Claude 換算）
```
推定 Claude 回避量 = k × (Codex 委譲ぶんの総消費トークン)
純節約            = 推定 Claude 回避量 − Claude オーバーヘッド総和
```
- `k`: 「同じ仕事を Claude がやったら Codex の何倍トークンを使うか」の換算係数。
- 既定 `k = 1.0`（保守的: Codex の仕事量をそのまま Claude 換算の下限とみなす）。
- `--k` で上書き可能。レポートには複数 `k`（例 1.0 / 1.5）の感度も併記。
- 校正方法（任意・将来）: 代表タスクを「Claude インライン」と「Codex 委譲」両方で走らせ、
  `r = Claudeトークン / Codexトークン` を実測して `k=r` に差し替える。

## 7. コンポーネント分割

`scripts/savings.py`（CLI、標準ライブラリのみ）。純粋関数で分割し単体テスト可能にする。

| 関数 | 役割 | 入力 | 出力 |
|---|---|---|---|
| `iter_codex_sessions(root)` | セッションファイル列挙 | ルートパス | パスのイテレータ |
| `parse_codex_session(path)` | メタ＋トークン抽出 | パス | `{id, source, cwd, ts, codex_tokens}` |
| `collect_codex(root, since, cwd_filter)` | mcp 委譲のみ集約 | フィルタ | セッション dict の list |
| `parse_claude_transcript(path)` | 委譲ターンの usage 抽出 | パス | overhead レコード list |
| `collect_claude(root, since)` | 全 transcript 集約 | フィルタ | overhead レコード list |
| `compute(codex, claude, k)` | 反実仮想・純節約算出 | 集計＋k | レポート用 dict |
| `render(report, ks)` | テキスト整形 | dict | str |
| `main(argv)` | CLI 引数処理 | argv | 終了コード |

CLI 例:
```
python3 scripts/savings.py --since 2026-06-01 --cwd daily-news --k 1.0
python3 scripts/savings.py            # 全期間・全プロジェクト・k=1.0
```

## 8. 出力イメージ

```
codex-orchestration 節約レポート（2026-06-02〜06-03, source=mcp）
委譲セッション数: 7   対象プロジェクト: daily-news, ...
─────────────────────────────────────────────
Codex がやった仕事         : 1,240,000 tok   ← Pro 枠から外した分
Claude オーケストレーション :    38,000 tok   ← 実際に払った overhead
─────────────────────────────────────────────
推定 Claude 回避量 (k=1.0) : 1,240,000 tok
純節約 (k=1.0)             : 1,202,000 tok   (約 32倍のレバレッジ)
感度: k=1.5 → 純節約 1,822,000 tok

セッション別内訳:
  2026-06-03 07:29  daily-news/.claude/worktrees/...   Codex  61,285  Claude  5,400
  ...
```

## 9. エッジケース / 注意

- **トークン currency の差**: Claude と Codex はトークナイザ/モデルが異なる。合算は近似であり、`k` で吸収する旨をレポートに明記。
- **累積 vs 独立トークン**: 6.1 のフォールバックで対応。
- **`source` キー欠落の古いセッション**: `source` 不明は集計対象外（mcp と確証できないため）。
- **Claude 側で threadId 取得不可**: ヘッドライン総和は紐付け不要で算出、内訳のリンクのみ best-effort。
- **複数プロジェクト混在**: 既定は全プロジェクト合算、`--cwd` で絞り込み。
- **ログのローテーション/削除**: 残存ログのみが対象（明記）。

## 10. テスト方針（TDD）

- `parse_codex_session`: 累積トークンの fixture → 最終値を返す / 独立トークン fixture → 総和を返す。
- `collect_codex`: `source` 混在・期間・cwd フィルタの分岐。
- `parse_claude_transcript`: `codex` tool_use を含むターンだけ拾う / 含まないターンは無視。
- `compute`: k=1.0 / k=1.5 で推定回避量・純節約が定義通り。
- すべて小さな JSONL fixture（実ログの一部を模した最小データ）で検証。実ホームのログには依存しない。

## 11. 配置 / 成果物

- `scripts/savings.py` — 本体。
- `tests/test_savings.py` — 単体テスト（fixture 同梱）。
- `README.md` / `SKILL.md` に使い方を 1〜2 行追記（クイックリファレンス）。
