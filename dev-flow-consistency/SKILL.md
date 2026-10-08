---
name: dev-flow-consistency
description: AI駆動開発フローの consistency ステージ（3/6: 整合性チェック）。plan_repair（implementation 内の計画修正）も mini モードとして担当します。トレーサビリティID整合性とドキュメント間の矛盾を検出し、カバレッジ行列（REQ × TC × API）を生成、タスクを Infra/App/Cross + DAG `depends_on` に分類して設計を凍結します。`mode=incremental` 時は Impact Analysis で baseline_commit 以降の差分のみを抽出します。ドキュメント生成承認後、または `--from=consistency` 起動時に使用します。
model: haiku
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---


# Stage 3/6 consistency: ドキュメント整合性チェックと設計凍結

## 実行モード

| モード | 用途 | state.json の next_stage | 起動エージェント |
|---|---|---|---|
| `full` | 通常（全 STEP を実行） | `"consistency"` | `stage-consistency-agent` |
| `incremental` | 要件追加時の差分チェック | `"consistency"` + `mode="incremental"` | `stage-consistency-agent` |
| `mini` | implementation 中の Plan Repair 時（差分修正のみ） | `"plan_repair"` | `stage-plan-repair-agent` |
| `lite` | `kind = "fix"` / `"refactor"`（ドキュメント整合性は変わらない前提で、実装タスクだけ切る） | `"consistency"` + `kind` が `fix` / `refactor` | `stage-consistency-agent` |

**`lite` モードの動作（kind = fix / refactor）:**
- STEP 0〜3（Impact Analysis・ID 整合性・整合性チェック・カバレッジ行列）はスキップ
- STEP 4 の checklist-writer に次を渡して **1 グループだけ**のチェックリストを生成させる：
  - `fix`: テスト定義書の `status: added` の TC（再現テストケース）と `task`。QA タスク = 再現 TC を実装する、Dev タスク = それを通す修正
  - `refactor`: `task` のみ。QA タスク = 既存テストが全通過することの確認（新規 TC なし）、Dev タスク = task に書かれた内部改善
  - グループ種別は変更対象ファイルから判定（IaC のみなら Infra、それ以外は App、両方なら Cross）
- spec-cache-writer は `fix` では実行（追加 TC を反映）、`refactor` ではスキップ
- `fix` では、`doc/process/coverage_matrix.md` の該当 REQ の行の「テストID」列に追加 TC（`status: added`）の ID を Edit で書き足す（行列全体は作り直さない。ファイルが無ければ飛ばす）
- STEP 5 の設計凍結コミットは実行する

**`mini` モードの動作:**
- STEP 1（ID整合性）・STEP 2（整合性チェック）・STEP 3（カバレッジ行列）はスキップ
- タスクチェックリストの**未着手グループのみ**を対象に checklist-writer を再実行（完了済みグループは保持）
- カバレッジ行列・スペックキャッシュは更新しない
- 完了後、state.json の `next_stage` を `"implementation"` に戻す

---

## 入力

`state.json` の `kind` と `task` を必ず読む（`lite` モードの判定に使う）。

状態ファイル `doc/process/state.json` から読み込み：
- requirements_paths
- test_spec_path
- api_spec_path
- infra_spec_path
- mock_path
- tech_stack
- is_gui
- is_api
- is_infra
- mode（`"full"` または `"incremental"`）
- baseline_commit（`incremental` 時のみ有効）

## STEP 0: Impact Analysis（incremental mode 時のみ）

`mode = "incremental"` の場合のみ、STEP 1 の前に Impact Analysis を実行します。

以下のエージェントを起動（同期実行、`run_in_background=false`, `model` は quality: `"opus"` / cost: `"sonnet"`。`dev-flow/reference/profiles.md`）：

```
あなたは Impact Analysis エージェントです。baseline_commit 以降のドキュメント変更を分析し、
影響を受ける実装箇所を ID ベースで特定してください。

baseline_commit: {BASELINE_COMMIT}

手順:
1. `git diff {BASELINE_COMMIT}..HEAD -- doc/` で変更されたドキュメントを確認
2. 変更された REQ-ID / TC-ID / API-ID を抽出
3. 各 ID に紐付く既存実装コードを特定（git log grep / find で探す）
4. 影響範囲をリスト化

出力フォーマット:
```
## Impact Analysis 結果

### 変更されたドキュメントノード
- REQ-002 (modified): "（変更内容の要約）"
- API-002 (modified): "（変更内容の要約）"
- TC-005 (added): "（追加内容の要約）"

### 影響を受ける実装
| 変更ID | 影響ファイル | 必要な変更 |
|---|---|---|
| REQ-002 | pkg/auth/validator.go | 最小文字数を 12 に変更 |
| API-002 | handlers/auth.go | リクエストスキーマ更新 |

### 影響を受けるタスク（チェックリスト用）
- [ ] task: （ファイル名）の（変更内容）（REQ-002）
```
```

Impact Analysis 完了後、その結果を STEP 4（checklist-writer）に渡して、影響範囲のタスクのみをチェックリスト化させます。

---

## STEP 1: トレーサビリティID整合性チェック

整合性チェック（STEP 2）の前に、以下のID参照チェックを実施してください：

**1. 要件ID一覧の抽出:**

要件定義書（全ファイル）の frontmatter から `requirements[].id`（REQ-NNN）を収集し、マスターリストを作成します。

**2. 参照先の存在チェック:**

| チェック対象 | 確認事項 |
|---|---|
| テスト定義書 `test_cases[].covers` | すべての REQ-NNN がマスターリストに存在するか |
| API仕様書 `endpoints[].covers` | すべての REQ-NNN がマスターリストに存在するか |
| テスト定義書ルートの `covers` | すべての REQ-NNN がマスターリストに存在するか |

**3. 未参照REQ-IDの検出:**

マスターリストのREQ-IDのうち、どのドキュメントの `covers` にも登場しないものを「未カバー要件」として記録します（STEP 3 でカバレッジ行列に反映）。

**4. エラー処理:**

- 存在しないIDへの参照 → AskUserQuestion で人間に修正を依頼（続行不可）
- frontmatter が存在しないドキュメント → 警告を記録して STEP 2 に進む（ブロックしない）

---

## STEP 2: ドキュメント整合性チェック

`mode` によって実行内容が異なります。

- **`mode = "full"`**: ドキュメント間の矛盾・考慮漏れを検出する（従来通り）
- **`mode = "incremental"`**: `baseline_commit` 以降に変更されたドキュメントと既存コードを比較し、「未実装の差分」を検出する（STEP 0 の Impact Analysis 結果を参考にする）

以下のエージェントを起動（同期実行、`run_in_background=false`, `model="opus"`）。

プロンプトは `prompts/consistency-check.md` を Read ツールで読み込み、プレースホルダー（`{MODE}`, `{BASELINE_COMMIT}`, `{REQUIREMENTS_PATHS}` 等）を実際の値に置換してから Agent に渡してください。

**重要:** `prompts/consistency-check.md` にmode別の詳細な手順が記載されています。このプロンプトファイルの指示に完全に従ってください。

---

## STEP 3: カバレッジ行列の生成

STEP 2（整合性チェック）完了後、以下の手順で `doc/process/coverage_matrix.md` を生成します：

**フォーマット:**

```markdown
# カバレッジ行列

| 要件ID | 要件タイトル | テストID | API/エンドポイント | 実装タスク |
|---|---|---|---|---|
| REQ-001 | ユーザー認証 | TC-001, TC-002 | API-001 (POST /auth/login) | （STEP 4 のチェックリスト生成後に補完） |
| REQ-002 | パスワードリセット | TC-003 | API-002 | （同上） |
| REQ-003 | ログアウト | ❌ 未カバー | ❌ | ❌ |
```

**生成手順:**

1. 要件定義書の frontmatter から全 REQ-ID と要件タイトルを収集
2. テスト定義書の frontmatter から `test_cases[].covers` を読み取り、REQ-IDごとにTC-IDをマッピング
3. API仕様書の frontmatter から `endpoints[].covers` を読み取り、REQ-IDごとにAPI-IDをマッピング
4. 未カバーの REQ-ID（TC または API が空）を `❌ 未カバー` でマーク

**未カバー検出時の処理:**

未カバー要件が1件以上ある場合、AskUserQuestion で人間に判断を仰ぎます：

| 選択肢 | 動作 |
|---|---|
| 「要件を削除する」 | 要件定義書から該当 REQ-ID を削除し、frontmatter を更新 |
| 「テスト/APIを追加する」 | spec に戻ってドキュメントを補完（state.json の `next_stage` を `"spec"` に戻して終了し、人間に `/dev-flow` の再実行を案内） |
| 「このまま進める（除外範囲として認識）」 | coverage_matrix.md に `除外` と記録して続行 |

---

## STEP 4: タスクチェックリストとスペックキャッシュの並列生成

Agent Teams（`TeamCreate` / `team_name`）は使用しません。以下の 2 エージェントを **同一ターンで同時に** 名前付きバックグラウンドサブエージェントとして起動し、それぞれの完了通知（最終回答）を本エージェントが受け取ります。中間オーケストレーター（旧 `consistency-orchestrator`）は置きません。

### タスクチェックリスト生成（`run_in_background=true`, `model` は quality: `"opus"` / cost: `"sonnet"`, `name="checklist-writer"`）

プロンプトは `prompts/checklist-writer.md` を Read ツールで読み込み、プレースホルダー（`{MODE}`, `{BASELINE_COMMIT}`, `{REQUIREMENTS_PATHS}` 等）を実際の値に置換してから Agent に渡してください。

### スペックキャッシュ生成（`run_in_background=true`, `model` は quality: `"opus"` / cost: `"sonnet"`, `name="spec-cache-writer"`）

プロンプトは `prompts/spec-cache-writer.md` を Read ツールで読み込み、プレースホルダー（`{MODE}`, `{BASELINE_COMMIT}`, `{REQUIREMENTS_PATHS}` 等）を実際の値に置換してから Agent に渡してください。

### 完了待ち

両方の完了通知が届くまで待ちます（`sleep` ポーリング禁止）。各 writer は最終回答で「生成完了: {パス}」と生成内容の要約を返します。

---

## STEP 5: 設計凍結コミット

`checklist-writer` と `spec-cache-writer` の両方の完了通知を受けたら、以下を実行。

通知が届かない場合（エージェントが途中でエラー終了した等）は、以下の手順でリカバリします：
1. `doc/process/task_checklist.md` と `doc/internal/spec_cache.md` の存在を Bash で確認する
2. 両ファイルが存在すれば内容を Read して品質を直接確認し、問題なければ設計凍結コミットへ進む
3. どちらかが存在しなければ、該当する writer を同じ `name` で Agent 再起動して生成し直す

```bash
git add doc/
git commit -m "docs: freeze specifications"
```

これにより設計が物理的に固定されます。

---

## 出力

設計凍結コミット後、以下を実行：

1. `doc/process/state.json` を更新（`next_stage` を `"implementation"` に）
2. 人間に「consistency 完了。次は `/dev-flow` を実行して implementation（並列実装）に進んでください」と通知
