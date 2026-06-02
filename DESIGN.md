# Claude Code × Codex オーケストレーション 設計仕様（最終形）

作成日: 2026-06-02 / 最終更新: 2026-06-02（実装・検証完了）
対象環境: Codex CLI 0.134.0 / Claude Code 2.1.146 / macOS

## 1. 目的とねらい

Claude Code（Pro プラン＝トークン枠が希少）と Codex CLI（サブスクプラン＝5時間・週間枠が圧倒的に大きい）を、
**1 つの開発ワークフロー**として統合する。本質は「**トークン経済の最適化**」と「**品質ゲートの確保**」の両立。

- Claude Code = 親（司令塔）: 設計・仕様策定・最終レビュー
- Codex = 実働部隊: TDD 実装・重い分析・1次コードレビュー
- 両者は必要に応じて独立に往復対話してよい

## 2. 採用方式（確定）

**Codex を MCP サーバ（`codex mcp-server`）として Claude Code に登録する。**

```
[あなた] ──指示──▶ [Claude Code (親)]
                      │  設計・仕様・最終レビュー
                      ▼ MCP ツール呼び出し（要承認）
                   [codex (子) = mcp__codex__codex / codex-reply]  ← ChatGPT 認証＝サブスク枠
                      │  TDD 実装・重い分析・1次レビュー
                      ▼
                   同一リポジトリのファイル / git 差分
```

### 2.1 なぜ第三者ツールを使わないか

- 定番の多モデル MCP（Zen MCP / PAL MCP / multi_mcp 等）は **API キー方式 = 標準 API 課金**で、
  Codex の**サブスク枠を使わない**。本目的（枠の活用）に対して本末転倒。
- 手元の Codex は ChatGPT 認証でログイン済み（`stored auth mode: chatgpt` / `stored API key: false`）。
  ネイティブの `codex mcp-server` はこの枠をそのまま使う。
- よって第三者ゼロ・追加課金ゼロで最短なのはネイティブ `codex mcp-server` の登録。

## 3. 確定した設計判断

| 論点 | 決定 |
|---|---|
| 自動化レベル | **半自動（承認ゲート型）**。各受け渡しで人が承認 |
| 文脈共有 | **会話文脈も渡す**。CC が意図・判断を要約し、`codex-reply` で往復維持 |
| アウトプット | **汎用の再利用セットアップ**（全プロジェクトで使える） |
| 接続方式 | **MCP 化**（`codex mcp-server` を `claude mcp add`） |
| Codex の権限 | **タスクごとに CC が選ぶ**（実装→`workspace-write` / レビュー→`read-only`） |
| 再利用の器 | **user スコープのプレーンスキル**（プラグイン化しない＝方式B） |
| 配置 | **別ディレクトリで git 管理し、`~/.claude/skills` から symlink** |

## 4. `codex` MCP インターフェース（実機確認済み）

公開ツールは 2 つ。

- **`codex`**: 新規セッション開始。主なパラメータ `prompt`（必須） / `sandbox`（`read-only`|`workspace-write`|`danger-full-access`） / `approval-policy`（`untrusted`|`on-failure`|`on-request`|`never`） / `cwd` / `model` / `config`。**戻り値に `threadId` を含む。**
- **`codex-reply`**: 既存セッションの継続。`threadId` ＋ `prompt`（`conversationId` は deprecated）。**往復対話**に使う。

→ サンドボックスや cwd を**呼び出しごとに**指定でき、設計要件をすべて満たす。

## 5. 成果物（最終形）

marp-repo には一切触れない。実体は marp-repo の外、設定は user スコープ。

### 5.1 実体リポジトリ（git 管理）

```
~/Documents/codex-orchestration/      ← git 管理する実体
├── SKILL.md                          ← 委譲プロトコル本体（プレーンスキル）
└── README.md                         ← セットアップ／持ち運び／アンインストール手順
```

### 5.2 symlink で CC に認識させる

```bash
ln -s ~/Documents/codex-orchestration  ~/.claude/skills/codex-orchestration
```

プレーンスキルとして `/codex-orchestration` で発動。編集は実体側で行い、SKILL.md テキストはライブ反映。

### 5.3 MCP 登録（一度きり）

```bash
claude mcp add -s user codex -- codex mcp-server
```

`-s user` で全プロジェクトに有効。

### 5.4 なぜプレーンスキル（方式B）にしたか

- 対案（方式A）はプラグイン化し `.mcp.json` を同梱する案。利点は「symlink/install 一発で MCP 登録＋スキルが両方入る」「MCP 設定も git に乗り再現性が高い」。
- ただし構造が一段深く、スキル名が namespaced になる。MCP 登録はコマンド1回・1度きりで済むため、**シンプルさを優先**して方式B（プレーンスキル＋別途 `claude mcp add`）を採用。

## 6. SKILL プロトコル要旨（詳細は `SKILL.md`）

- **役割分担**: CC＝設計/仕様/最終レビュー、Codex＝実装/重い分析/1次レビュー。
- **委譲トリガー**: トークン重作業（実装一式・広域調査・大きい差分レビュー）は Codex へ。
- **委譲ブリーフ**（`codex` の `prompt` に同梱）: ゴール / 受け入れ条件 / 対象ファイル / 設計判断と理由 / 規律（Codex は superpowers を持たないので TDD 等を明示）/ 返し方（詳細はファイル、戻りは要約のみ）。
- **サンドボックス**: 実装系→`workspace-write`（`on-failure`）/ レビュー・分析系→`read-only`（`never`）。
- **承認ゲート（半自動）**: ① 委譲前に人へ確認 ② サンドボックス内実行 ③ CC が `git diff` を再レビューしてから完了。
- **往復**: 手直し・続きは `codex-reply`（`threadId`）で Codex 側文脈を維持。
- **トークン規律**: Codex の戻りは要約のみ。詳細はファイル出力。CC は必要時のみ読む。
- **並列・同時作業の衝突回避**: 複数の `workspace-write` を同じ作業ツリーで走らせると衝突するため、**セッションごとに git worktree を切り、各 `cwd` に渡す**。これは「複数エージェントのオーケストレーション」ではなく、**並行して書き込む主体（並列 Codex／複数 CC セッション）を隔離する**ための仕組み。`read-only` 並列には不要。

## 7. superpowers との共存・委譲ポイント差し込み

- 名前空間が別（superpowers はプラグイン名前空間 `superpowers:*`、本スキルは個人スキル `/codex-orchestration`）なので**衝突しない**。常時コストも description 1 行ぶんのみ。
- superpowers は「**どう進めるか**」（設計→TDD→計画→レビュー）、本スキルは「**重い実行を誰がやるか**」（Codex 枠へ逃がす）。レイヤーが別で補完関係。
- **差し込みポイント**: `executing-plans` / `subagent-driven-development` / `dispatching-parallel-agents` は既定で Claude サブエージェント（＝同じ Pro 枠を消費）に投げる。トークンの重い実行はワーカーを **Codex** に向け、終わったら superpowers フローに復帰する。

## 8. 典型フロー（例: 新機能の実装）

1. CC があなたと設計・仕様を固める（CC の頭脳、低トークン）。
2. CC が委譲ブリーフを作り、人の承認後 `codex`（`sandbox=workspace-write`）で TDD 実装を委譲。
3. Codex が実装し、詳細は `.md`/コードに、要約のみ返す。
4. CC が `git diff` を再レビュー。修正が要れば `codex-reply`（`threadId`）で往復。
5. （任意）1次コードレビューを `codex`（`sandbox=read-only`）に委譲し、CC が最終判断。

## 9. スコープ外（YAGNI）

- 第三者フレームワークによる **3 体以上のエージェント**オーケストレーション（CAO / swarms 等。将来拡張可）。
  ※ CC↔Codex 1リンク内の worktree 並列（＝同時書き込みの衝突回避）は §6 のとおりスコープ内。
- Gemini など他モデルの追加。
- 自動マージや CI 連携。

## 10. 実装結果・検証（完了）

- ✅ `claude mcp add -s user codex -- codex mcp-server` → `codex` MCP 接続、`mcp__codex__codex` / `codex-reply` ツールが出現。
- ✅ symlink（`~/.claude/skills/codex-orchestration` → 実体）→ **再起動後にスキルが発見された**（symlink 経由の発見を実機で確認）。
- ✅ 煙テスト（`codex`, `read-only`, `cwd=実体リポ`）→ Codex（GPT-5）が応答し、`README.md` / `SKILL.md` を正しく列挙。`threadId` を取得。
- ✅ 往復テスト（`codex-reply` + 同一 `threadId`）→ 再スキャンせず前回内容（2 件: README.md, SKILL.md）を記憶して応答。**会話文脈の往復を確認**。
- ✅ ChatGPT 認証＝**サブスク枠**で稼働（API 課金なし）。

## 11. 受け入れ条件（達成状況）

- [x] `claude mcp add` 後、CC のセッションで `codex` / `codex-reply` ツールが見える。
- [x] Skill が一覧に出て、`/codex-orchestration` で発動できる。
- [x] 委譲が Codex のサブスク枠で実行され、API 課金が発生しない。
- [x] `codex-reply`（`threadId`）で往復し、Codex 側文脈が維持される。
