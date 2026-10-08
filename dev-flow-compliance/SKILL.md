---
name: dev-flow-compliance
description: AI駆動開発フローの compliance ステージ（6/6: 準拠チェック・完了報告）。カバレッジ行列で TC-NNN・API-NNN の実装存在を機械的に検証し、実装がドキュメントに完全準拠しているか確認します。乖離は実装ミス/仕様変更に分類して対応し、完了レポートを生成し、`doc/process/state.json` を `completed` にして（削除せず）フローを終了します。test 通過後、または `--from=compliance` 起動時に使用します。
model: opus
allowed-tools: Read Write Edit Bash AskUserQuestion
disable-model-invocation: true
---


# Stage 6/6 compliance: ドキュメント準拠チェックと完了報告

## 入力

状態ファイル `doc/process/state.json` から読み込み：
- requirements_paths
- test_spec_path
- api_spec_path
- mock_path
- is_api
- is_gui
- tech_stack

## STEP 0: 検証範囲の決定（kind による）

`state.json.kind` で STEP 1〜2 の対象 ID を絞る：

| kind | 対象 |
|---|---|
| `feature` | 全 REQ / TC / API |
| `change` | テスト定義書・API 仕様書で `status: added` / `status: modified` の ID、およびそれらが `covers` する REQ |
| `fix` | `status: added` の TC（再現テストケース）のみ。加えて test ステージの全通過を確認する |
| `refactor` | 全 REQ / TC / API（挙動が変わっていないことの確認。ドキュメントに差分が無いことも `git diff {baseline_commit}..HEAD -- doc/` で確認し、差分があれば乖離として報告） |

## STEP 1: カバレッジ行列による機械的検証

ドキュメント準拠チェック（STEP 2）の前に、`doc/process/coverage_matrix.md` を使って以下の機械的検証を実行します。

**coverage_matrix.md が存在しない場合:** この STEP 1 をスキップして STEP 2 に進みます。

**検証手順:**

**1. TC-ID の実在確認:**

```bash
# テストファイルを列挙
find . -type f \( -name "*_test.*" -o -name "*.test.*" -o -name "*.spec.*" \) \
  ! -path "*/.git/*" ! -path "*/node_modules/*"
```

coverage_matrix.md に記載された各 TC-NNN が、実際のテストファイル内に存在するかを確認します。判定は次の優先順：

1. テスト定義書 frontmatter の `test_cases[].implemented_by`（`path::関数名`。bootstrap 由来の TC と、それに倣って実装された TC が持つ）→ そのファイルに関数名が存在するか Grep
2. テスト名または関数名に TC-NNN が含まれるか
3. REQ-NNN を covers している旨のコメントがあるか

**2. API-ID の実在確認:**

coverage_matrix.md に記載された各 API-NNN のエンドポイント（method + path）が、実装コードのルート定義に存在するかを確認します。

**3. 実装コードの REQ-ID 到達確認（任意）:**

可能であれば、実装コードのコメントまたはコミットメッセージに REQ-NNN が含まれるかを確認します：

```bash
git log --oneline --all | grep -E "REQ-[0-9]+" | head -20
```

**4. 検証レポートの生成:**

```markdown
## カバレッジ行列検証レポート

| REQ-ID | TC確認 | API確認 | 判定 |
|---|---|---|---|
| REQ-001 | ✅ TC-001, TC-002 実在 | ✅ POST /auth/login 実在 | OK |
| REQ-002 | ❌ TC-003 が見つからない | ✅ | 準拠違反 |
```

**準拠違反の場合:** STEP 2 の通常チェックと合わせて乖離として報告します。

---

## STEP 2: ドキュメント準拠チェック

テスト通過後、実装がドキュメントに完全に準拠しているかを確認します。

**原則: ドキュメントが正です。乖離が見つかった場合は実装を修正してください。**

以下のプロンプトで Agent を起動（同期実行、`run_in_background=false`, `model="opus"`）：

---
**ドキュメント準拠チェックプロンプト**

実装がドキュメントに完全に準拠しているかを確認してください。

**厳守事項: ドキュメントが正です。実装ファイルのみ修正し、ドキュメントは変更しないこと。乖離が発見された場合は「その場での即時調整」を行わず、必ず以下のカテゴリ分類と対応フローに従うこと。**

ドキュメント:
- 要件定義書（全ファイルを順に読み込んでください）: {REQUIREMENTS_PATHS}
- テスト定義書: `{TEST_SPEC_PATH}`
- API仕様書（IS_API=true の場合）: `{API_SPEC_PATH}`

技術スタック: `{tech_stack}`

### 手順

**1. 実装ファイルの列挙と読み込み**

まず Bash でプロジェクトの実装ファイルを列挙する（テストファイルを除く）：

```bash
# ソースファイルを列挙（テストファイル・設定ファイル・node_modules 等を除外）
find . -type f \
  \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.go" \) \
  ! -path "*/node_modules/*" \
  ! -path "*/.git/*" \
  ! -path "*/dist/*" \
  ! -path "*/__pycache__/*" \
  ! -name "*.test.*" \
  ! -name "*.spec.*" \
  ! -name "*_test.*" \
  | sort
```

列挙されたファイルを Read ツールで読み込む。ファイル数が多い場合は API 仕様書のエンドポイント一覧を基準に、関連するハンドラ・サービスファイルを優先して読み込む。

**2. 乖離の検出**

以下の観点でドキュメントと実装を比較する：
- 要件定義書の全機能要件が実装されているか
- API仕様書のエンドポイント・リクエスト/レスポンス定義と実装が一致しているか
- ドキュメントに定義されていない機能・エンドポイントが実装に混入していないか

**3. 乖離の分類**

各乖離を以下のカテゴリに分類する：

- **A: 実装ミス**（ドキュメントが正しく、実装がドキュメントに従っていない）
  - 例：未実装の機能、エンドポイントの仕様違い、ドキュメントにない独自実装の混入
- **B: ドキュメントの記述ミスの疑い**（実装側の判断が正しく、ドキュメントに誤りがある可能性がある）
  - 例：実装してみて初めて判明した仕様の矛盾・不整合

**4. A カテゴリの自動修正**
- 実装を Edit ツールで修正する
- 修正後、変更をコミットする：
  ```bash
  git add {修正したファイル}
  git commit -m "fix: ドキュメント準拠修正 - {修正内容の概要}"
  ```
- コミット後、完了報告に「test 再実行が必要」と記載する

**5. B カテゴリの報告**

以下のフォーマットで報告し、メインオーケストレーターが AskUserQuestion で人間に判断を仰ぐ：

```
## ドキュメント準拠チェック - 仕様変更の確認

### 確認が必要な乖離

#### 乖離 1
- **内容**: （具体的な乖離の説明）
- **実装の状態**: （現在の実装内容）
- **ドキュメントの記述**: （該当箇所）
- **推奨**: （実装を直すべきか、仕様変更として承認すべきか）

（乖離ごとに繰り返す）
```

人間の判断：
- 「実装を修正する」→ A カテゴリと同様に実装を修正し、test 再実行が必要と記載
- 「仕様変更として承認する」→ **仕様変更フロー**を実行する

**6. 乖離なしの場合**
- 完了を報告して STEP 3 へ進む

---

**仕様変更フロー（「仕様変更として承認する」が選択された場合）**

メインオーケストレーターが以下を順に実行する：
1. 承認された変更内容で該当ドキュメント（要件定義書・API仕様書）を Edit ツールで修正する
2. `doc/process/state.json` の `next_stage` を `"consistency"` に戻して保存する
3. `doc/process/task_checklist.md` の「ステージ進捗」を以下に戻す（hook 導入環境では state.json 書き込み時に自動同期されるので不要）：
   - `- [x] 3. consistency: ...` → `- [ ]`
   - `- [x] 4. implementation: ...` → `- [ ]`（完了済みだった場合）
   - `- [x] 5. test: ...` → `- [ ]`（完了済みだった場合）
4. 人間に「仕様変更を反映しました。`/dev-flow` を実行して consistency（整合性チェック）からやり直してください」と通知する

---

**test 再実行フロー（A カテゴリ修正後）**

1. `doc/process/state.json` の `next_stage` を `"test"` に戻して保存する
2. `doc/process/task_checklist.md` の「ステージ進捗」を以下に戻す（hook 導入環境では自動同期されるので不要）：
   - `- [x] 5. test: ...` → `- [ ]`
3. 人間に「実装を修正しました。`/dev-flow` を実行して test（テスト実行）からやり直してください」と通知する

---

## STEP 3: 人間への完了報告

準拠チェック完了（乖離なし）を確認したら、以下の形式で報告：

```markdown
## 開発フロー完了レポート

### 成果物
- 要件定義書: {REQUIREMENTS_PATHS}
- テスト定義書: {TEST_SPEC_PATH}
- API仕様書（API の場合）: {API_SPEC_PATH}
- モック HTML（GUI の場合）: {MOCK_PATH}
- 実装ファイル: （変更・作成したファイル一覧）
- テストファイル: （変更・作成したファイル一覧）

### テスト結果
- 総テスト数: X / 通過: X / 失敗: 0

### ドキュメント準拠チェック
- 実装修正: （A カテゴリで修正した内容の概要）
- 仕様変更承認: （B カテゴリで人間が承認した仕様変更の概要、なければ「なし」）

### レビュー概要
git log で implementation のコミット履歴を確認し、fix: / chore: プレフィックスのコミットから主な修正内容を要約する：
```bash
git log --oneline --grep="^fix\|^chore" -- .
```

### 規約ファイルへの昇格候補
`doc/process/review-findings-backlog.md` があれば、その表を「回数」の多い順に提示し、次を人間に問う（AskUserQuestion、複数選択）：
- 「{rule}: {内容}」を `dev-flow-implementation/reference/conventions/{昇格先}.md` のチェックリストに追加する（dev-flow-skills リポジトリへの PR が必要。ここでは候補の提示まで）
- このプロジェクト固有なので `doc/conventions.md` に追加する（その場で追記する）
- 見送る
無ければ「レビューで規約外の汎用指摘はありませんでした」と書く。
```

---

## 出力

完了レポートを送信したら、以下を実行：

1. テスト定義書・API 仕様書・インフラ仕様書の frontmatter に `status: added|modified` があれば、`kind` に関係なく取り除く（`feature` でも既存の文書に差分として足すと付く）（次の run が差分を正しく判定できるように）。要件定義書の `（廃止）` 項目はそのまま残す
2. `doc/process/state.json` を更新して保存（**削除しない**。`tech_stack` / 各パス / `is_*` / `baseline_commit` は次の run が使う）：
   - `next_stage` を `"completed"`
   - `baseline_commit` を `git rev-parse HEAD`（次の change / fix の差分基点）
   - `implementation_progress` を削除
   hooks が `task_checklist.md` のステージ進捗を全完了に同期し、`flow.log` に完了を記録する
3. `git add doc/ && git commit -m "docs: {kind} 完了（{task の要約}）"` で仕様書の status 除去と state.json を確定
4. 人間に「すべてのステージが完了しました。次の変更は `/dev-flow --kind=change|fix|refactor "内容"` で始められます」と通知
