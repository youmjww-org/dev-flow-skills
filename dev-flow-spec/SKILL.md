---
name: dev-flow-spec
description: AI駆動開発フローの spec ステージ（2/6: 仕様書生成）。テスト定義書（Gherkin）・API仕様書（OpenAPI 3.1.0）・インフラ仕様書・UIモックを名前付きサブエージェントで並列生成し、frontmatter に `covers: [REQ-NNN]` を付与してエージェントレビューと人間レビューを得ます。要件定義承認後の `/dev-flow` 継続時、または `--from=spec` で起動時に使用します。
model: haiku
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---


# Stage 2/6 spec: 仕様書生成とレビュー

## 入力

状態ファイル `doc/process/state.json` から読み込み：
- requirements_paths
- test_spec_path（未指定の場合は自動決定）
- api_spec_path（未指定の場合は自動決定）
- infra_spec_path（未指定の場合は自動決定）
- mock_path（未指定の場合は自動決定）
- tech_stack
- is_gui
- is_api
- is_infra
- is_e2e
- kind（`feature` / `change` / `fix`。`refactor` はこのステージを通らない。欠損時は `feature`）
- baseline_commit（`change` / `fix` のときの差分基点）
- task（`fix` のときの不具合説明。オーケストレーターの引数 TASK）

### 0. 変更対象 REQ の抽出（kind が `change` / `fix` のとき）

writer に渡す `{CHANGED_REQ_IDS}` を作る：

```bash
# 差分のある要件定義書と、追加・変更・削除された REQ-ID
git diff {baseline_commit}..HEAD -- doc/requirements/ | grep -E '^[+-].*REQ-[0-9]+' | sort -u
```

- `+` 行にだけ現れる ID → `added`、`-` 行にだけ現れる ID → `removed`、両方に現れる ID → `modified`
- 判定に迷う ID は要件定義書の該当箇所を Read して確認する
- 結果を `REQ-002 (modified), REQ-007 (added)` の形式で `{CHANGED_REQ_IDS}` に入れる。`fix` で差分が無い場合は `（要件変更なし）` とする

**小さい change の判定（`kind = "change"` のとき）:** `{CHANGED_REQ_IDS}` が 3 件以下なら `state.json.change_scale` に `"small"`、4 件以上なら `"normal"` を書く。`small` は consistency・compliance の軽量ルートに使う（2026-10 の sandbox-api では、REQ 2〜3 件の change でも spec 9 分・consistency 7 分・compliance 6 分×2 と、実装以外に 30 分前後かかっていた）。`feature` / `fix` / `refactor` では書かない（あれば消す）

`kind = "fix"` のときは `{FIX_DESCRIPTION}` に task をそのまま渡す。`kind = "fix"` では **test-spec-writer のみ**起動し、API 仕様書・インフラ仕様書・モックは触らない（不具合修正で API が変わるなら `change` として扱う）。

## STEP 1: ドキュメント生成

### 1-0. 実行モデル（チーム機能は使わない）

Agent Teams（`TeamCreate` / `team_name`）は使用しません。writer・reviewer はすべて **名前付きサブエージェント**として `Agent(name=..., run_in_background=true)` で起動し、完了通知（最終回答）を本エージェントが受け取って次の処理を決めます。

- writer / reviewer は SendMessage を送らず、**最終回答で結果を返す**（各 prompts/*.md に記載済み）
- 修正依頼は `SendMessage(to: "{writer name}", message: ...)` で **同じ名前の writer を再開**する（コンテキストを保ったまま続きから修正できる）
- 再開できない場合（エージェントが破棄されている等）は同じ `name` で `Agent` を新規起動し、修正依頼をプロンプトに含める
- 完了待ちは通知が届くまで待つ。`sleep` によるポーリングはしない

### 1a. writer の並列起動

以下のうち起動条件を満たすものを **同一ターンで同時に**起動します（`run_in_background=true`, `model` は quality: `"opus"` / cost: `"sonnet"`。`dev-flow/reference/profiles.md`）。プロンプトは各ファイルを Read し、プレースホルダーを実際の値に置換してから Agent に渡してください。

| name | プロンプトファイル | プレースホルダー | 起動条件 |
|---|---|---|---|
| `test-spec-writer` | `prompts/test-spec-writer.md` | `{REQUIREMENTS_PATHS}`, `{TEST_SPEC_PATH}`, `{KIND}`, `{CHANGED_REQ_IDS}`, `{FIX_DESCRIPTION}` | 常に |
| `api-spec-writer` | `prompts/api-spec-writer.md` | `{REQUIREMENTS_PATHS}`, `{API_SPEC_PATH}`, `{tech_stack}`, `{KIND}`, `{CHANGED_REQ_IDS}` | IS_API=true かつ kind ≠ fix |
| `infra-spec-writer` | `prompts/infra-spec-writer.md` | `{REQUIREMENTS_PATHS}`, `{INFRA_SPEC_PATH}`, `{tech_stack}`, `{KIND}`, `{CHANGED_REQ_IDS}` | IS_INFRA=true かつ kind ≠ fix |
| `mock-writer` | `prompts/mock-writer.md` | `{REQUIREMENTS_PATHS}`, `{MOCK_PATH}`, `{tech_stack}`, `{KIND}`, `{CHANGED_REQ_IDS}` | IS_GUI=true かつ kind ≠ fix |

reviewer にも `{KIND}` を渡す（差分更新モードでは既存 ID の保持を検証する）。

**`kind = "change"` で起動しない writer:** `api-spec-writer` / `infra-spec-writer` / `mock-writer` は、次の両方に当てはまるとき起動しない（その文書は変わらないので、reviewer も起動しない）。迷ったら起動する。
- 既存の文書に、変更 REQ を `covers` している項目（エンドポイント・リソース・画面）が無い
- 要件定義書の差分（`git diff {baseline_commit}..HEAD -- doc/requirements/`）が、その文書の範囲（API のリクエスト・レスポンス / インフラ構成 / 画面の表示・操作）に触れていない

**差分更新モードの reviewer の範囲:** reviewer は `status: added|modified` の項目と、それらと ID・`covers` でつながる項目だけを見る（各 reviewer プロンプトの「差分更新モードの追加チェック」）。変わっていない既存項目の書き方では差し戻さない。

**差分更新モード（kind = `change` / `fix`）の要点**: writer は既存ファイルを読み、既存 ID を振り直さず、変更のあった REQ に紐づく項目だけ追加・修正して `status: added|modified` を付ける。詳細は各 writer プロンプトに記載。

`kind = feature` でも、テスト定義書・API 仕様書がすでにある（bootstrap の as-is ドキュメントや前回の run の成果がある）ときは、全文生成ではなく差分更新モードで書く（`{KIND}` には `change` を渡し、`{CHANGED_REQ_IDS}` に今回追加・変更した REQ を入れる）。全文を書き直すと TC / API の ID が振り直され、既存テストや `implemented_by` との対応が切れる（2026-10 の API sandbox の検証で、bootstrap 後の feature が as-is のテスト定義書を全文書き直し、既存の振る舞い 29 件のテストとの対応が切れた）。人間に「全文書き直してよいか」とは聞かない

**テスト定義書の frontmatter テンプレート（test-spec-writer に指示すること）:**

```markdown
---
doc_type: test-spec
covers:
  - REQ-001
  - REQ-002
test_cases:
  - id: TC-001
    title: （テストケースタイトル）
    covers: [REQ-001]
  - id: TC-002
    title: （テストケースタイトル）
    covers: [REQ-001, REQ-002]
---
```

**API仕様書の frontmatter テンプレート（api-spec-writer に指示すること）:**

```markdown
---
doc_type: api-spec
endpoints:
  - id: API-001
    method: POST
    path: /example
    covers: [REQ-001]
  - id: API-002
    method: GET
    path: /example/{id}
    covers: [REQ-002]
---
```

いずれも要件定義書の `requirements[].id`（REQ-NNN）を参照して `covers` フィールドを埋めること。

### 1b. reviewer の起動（writer 完了ごと）

writer の完了通知を受け取るたびに、対応する reviewer を起動します（`run_in_background=true`, `model` は quality: `"opus"` / cost: `"sonnet"`）。他の writer の完了は待ちません。

| writer | reviewer name | プロンプトファイル | プレースホルダー |
|---|---|---|---|
| `test-spec-writer` | `test-spec-reviewer` | `prompts/test-spec-reviewer.md` | `{TEST_SPEC_PATH}` |
| `api-spec-writer` | `api-spec-reviewer` | `prompts/api-spec-reviewer.md` | `{API_SPEC_PATH}` |
| `infra-spec-writer` | `infra-spec-reviewer` | `prompts/infra-spec-reviewer.md` | `{INFRA_SPEC_PATH}` |
| `mock-writer` | `mock-reviewer` | `prompts/mock-reviewer.md` | `{MOCK_PATH}` |

reviewer は最終回答として `{"status":"approved"|"changes_requested","issues":[...]}` の JSON を返します。

**reviewer のエンジン（codex 優先）:** 最初の reviewer を起動する前に `state.json.reviewer_engine`（無ければ `auto`）を見る。`auto` なら `${CLAUDE_SKILL_DIR}/../dev-flow/codex/review.sh available` を 1 回実行し、0 なら reviewer を **Codex CLI で動かす**（`claude`、または `available` が 3 なら上の表のとおり Claude のサブエージェント）。起動・待ち方・失敗時に Claude へ切り替える手順は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/codex-review.md` を Read して従う（種別は `spec`、cwd はメインの作業ディレクトリ、出力は `doc/process/reviews/spec-{reviewer name}-r{回数}.json`）。プロンプトは上の表のファイルを置換したもの、受け取った JSON の扱い（1c の修正ループ）は Claude のときと同じ。

### 1c. 修正ループ

| reviewer の結果 | 動作 |
|---|---|
| `approved` | そのドキュメントは完了 |
| `changes_requested` | `issues[]` を `SendMessage(to: "{writer name}")` で writer に渡して修正させ、完了後に同じ reviewer を再起動して再レビュー |
| JSON がパースできない | 回答本文を人間が読める形で保持し、明確な指摘があれば `changes_requested` として扱う |

1 ドキュメントあたりの修正ループは **最大 3 回**。超過したら残りの指摘を STEP 2 の人間レビューに持ち越します。

### 1d. 完了判定とリカバリ

起動したすべての reviewer が `approved`（または上限到達）になったら STEP 2 へ進みます。

通知が届かない場合（エージェントが途中でエラー終了した等）は、以下の手順でリカバリします：
1. 各ドキュメントファイル（TEST_SPEC_PATH / API_SPEC_PATH / INFRA_SPEC_PATH / MOCK_PATH）の存在を Bash で確認する
2. ファイルが存在すれば内容を Read して品質を直接確認し、問題なければ STEP 2 の人間レビューへ進む
3. ファイルが存在しなければ、該当する writer を同じ `name` で再起動して生成し直す

---

## STEP 2: 人間レビュー

`kind = "fix"` では人間レビューを行わない。すべての reviewer が `approved` なら、そのまま「出力」へ進む（修正ループの上限に達して指摘が残ったときだけ、残りの指摘を AskUserQuestion で人間に出す）。`feature` / `change` では以下を行う。

AskUserQuestion ツールで以下を同時に提示してレビューを依頼（`change` / `fix` では `status: added|modified` の項目と `git diff` の要約を先に示し、変更箇所に絞ってレビューしてもらう）：

- テスト定義書（TEST_SPEC_PATH）
- API仕様書（API_SPEC_PATH）（IS_API=true の場合）
- インフラ仕様書（INFRA_SPEC_PATH）（IS_INFRA=true の場合）
- モック HTML（MOCK_PATH）（IS_GUI=true の場合）— ブラウザで開いて確認するよう案内する

| 対象 | 結果 | 動作 |
|---|---|---|
| テスト定義書 | 修正が必要 | 指摘内容を `test-spec-writer` に SendMessage して再生成、完了後 `test-spec-reviewer` を再起動して再レビュー |
| API仕様書 | 修正が必要 | 指摘内容を `api-spec-writer` に SendMessage して再生成、完了後 `api-spec-reviewer` を再起動して再レビュー |
| モック | 修正が必要 | 指摘内容を `mock-writer` に SendMessage して再生成、完了後 `mock-reviewer` を再起動して再レビュー |
| インフラ仕様書 | 修正が必要 | 指摘内容を `infra-spec-writer` に SendMessage して再生成、完了後 `infra-spec-reviewer` を再起動して再レビュー |
| すべて承認 | — | 出力処理へ進む |

**SendMessage で再開できない場合（writer が破棄済み等）:**
Agent ツールで同じ `name` を使って新規起動し、修正依頼プロンプトを直接渡してください。
例（cost の場合。quality では `model="opus"`）: `Agent(name="test-spec-writer", run_in_background=true, model="sonnet", prompt="以下の指摘を反映して {TEST_SPEC_PATH} を修正してください: {指摘内容}。完了したら修正内容の要約を最終回答で返してください。")`

---

## 出力

すべて承認されたら、以下を実行：

1. `doc/process/state.json` を更新：
   ```json
   {
     "next_stage": "consistency",
     "test_spec_path": "確定したパス",
     "api_spec_path": "確定したパス",
     "mock_path": "確定したパス",
     ...
   }
   ```
2. 人間に「spec 完了。次は `/dev-flow` を実行して consistency（整合性チェック）に進んでください」と通知
