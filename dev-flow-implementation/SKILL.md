---
name: dev-flow-implementation
description: AI駆動開発フローの implementation ステージ（4/6: 並列実装）。タスクチェックリストの DAG `depends_on` を解決しながらグループを並列実行し、各グループ内で Dev/QA を独立 worktree で並列実装します。Infra/App/Cross の 3 種類のチーム構成に対応し、設計判断の記録・Plan Repair・独立レビュアー・Implements/Tests コミットフッターを伴います。整合性チェック完了後、または `--from=implementation` 起動時に使用します。
model: haiku
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---


# Stage 4/6 implementation: 並列実装（git worktree ワークフロー）

## 入力

`doc/process/state.json` から: `requirements_paths` / `test_spec_path` / `api_spec_path`（IS_API）/ `infra_spec_path`（IS_INFRA）/ `mock_path`（IS_GUI）/ `tech_stack` / `is_gui` / `is_api` / `is_infra` / `mode`（`full` / `incremental`）/ `baseline_commit`（incremental のみ）。

| 項目 | full | incremental |
|---|---|---|
| 実装範囲 | チェックリストの全タスク | チェックリストのタスク（consistency で差分に絞り込み済み） |
| 既存コードの扱い | 参照のみ（スタイル・規約を合わせる） | 必ず確認し、既存実装がある箇所はスキップ |

モデル・レビューの並列化・待ち時間は `state.json.profile`（無ければ `quality`）で決まる。詳細は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/profiles.md`。このスキルの `model="…"` は、断りが無ければ cost の値として読む。

## チーム種別と「層」

グループ見出しの `(Infra)` / `(App)` / `(Cross)` で、そのグループが持つ**層**（`{team}` = `infra` / `app`）が決まる。以下の STEP は層ごとに同じ手順を行う。

| チーム種別 | 層 | 層の進め方 |
|---|---|---|
| `Infra` | infra | — |
| `App` | app | — |
| `Cross` | infra → app | infra 層の実装・レビューが終わってから app 層を始める（Infra の修正が App に響くため） |

1 つの層で作るもの（`N` はグループ番号）:

| | Dev | QA |
|---|---|---|
| worktree | `{MAIN_DIR}/../worktree-dev-{team}-group-N` | `{MAIN_DIR}/../worktree-qa-{team}-group-N` |
| ブランチ | `dev/{team}-group-N` | `qa/{team}-group-N` |
| エージェント | `dev-implementer-{team}-group-N` | `qa-implementer-{team}-group-N` |
| プロンプト | `prompts/dev.md` | `prompts/qa.md` |
| レビュー | `prompts/reviewer-dev.md` | `prompts/reviewer-qa.md` |

- **QA の part**: チェックリストに `#### QA タスク (…) — part K` が 2 つ以上あれば、part ごとに QA の worktree・ブランチ・implementer・レビュー・PR を 1 組ずつ作る。part 1 は上の名前のまま、part 2 以降は `N` を `N-pK` にする（`ensure-worktree.sh qa app N-p2` → `worktree-qa-app-group-N-p2` / `qa/app-group-N-p2` / `qa-implementer-app-group-N-p2`）。以下の「QA」は part があれば part ごとに読む
- **QA タスクが無い層**（「対応する TC なし」の注記だけ。基盤構築グループに多い）は、QA の worktree・ブランチ・エージェント・PR を作らない

## 事前準備

### STEP 0-a: チェックリストの読み込みと再開判定

`doc/process/task_checklist.md` の「並列実行グループ」から、グループごとの Dev / QA タスク・チーム種別・`depends_on` を読み取る（例: `### グループ 1 (Infra) — depends_on: []` → `group_types["group-1"] = "Infra"`, `depends_on["group-1"] = []`）。

`state.json` に `implementation_progress` があれば前回の中断から再開する：

0. **マージ待ち PR の取り込み**: `pr_numbers` にあり `completed_groups` に無いグループの PR を `gh pr view <N> --json state,url` で確認する
   - 全 PR が `MERGED` → そのグループの STEP H を実行
   - `OPEN` がある → STEP G の自動マージを再試行。それでも deny ならそのグループは「人間マージ待ち」
   - `CLOSED`（マージされずに閉じられた）→ AskUserQuestion（再作成 / グループをやり直す）
1. `completed_groups` のグループはスキップ対象にする
2. `active_worktrees` に残っているブランチの worktree を `git worktree remove {MAIN_DIR}/../worktree-{ブランチ名の / を - に} --force 2>/dev/null || true` で消す
3. AskUserQuestion:「グループ X から再開します。よろしいですか？」→ 再開する / 最初からやり直す（`implementation_progress` を初期化）

無ければ STEP 0-b でベースブランチを決めてから `implementation_progress` を初期化する。

### STEP 0-b: ベースブランチの確認

`git branch --show-current` を BASE_BRANCH とする。

- `main` / `master` / `develop` / `release/*` / `hotfix/*` の上にいたら、そこへの PR は自動マージが常に拒否されるので、`git switch -c feature/{task を表す英小文字とハイフンの短い名前}` で作業用ブランチを作って BASE_BRANCH にする（元のブランチは触らない）
- worktree を作る前に `git push -u origin {BASE_BRANCH}` する。push していないと、Dev / QA の PR の差分に requirements〜consistency のドキュメントのコミットまで入る

```json
{
  "implementation_progress": {
    "total_groups": 3,
    "completed_groups": [],
    "active_worktrees": [],
    "base_branch": "feature/xxx",
    "group_types": {"group-1": "Infra", "group-2": "App", "group-3": "Cross"},
    "depends_on": {"group-1": [], "group-2": [], "group-3": ["group-1", "group-2"]},
    "pr_numbers": {}
  }
}
```

`pr_numbers` は STEP E で PR を作るたびに `"group-N": [番号, ...]` を追記する。

---

## グループ実行ループ（DAG）

```
while 未完了グループが存在する:
  実行可能グループ = depends_on が全て completed_groups に含まれるグループ
  実行可能グループを並列に STEP A〜H まで起動（run_in_background で同時進行）
  いずれかのグループ完了 → completed_groups に追加 → 次の実行可能グループを評価
```

**同時に動かすのは最大 4 グループ、implementer は最大 8 本**（QA の part の数だけ増える）。implementer は tmux のペインを 1 つずつ使い、上限に達するとレビュアーや修正の implementer を起動できなくなる。実行可能なグループが 5 つ以上あれば、`depends_on` で後続に待たれている数が多いグループから起動する。STEP H で implementer とレビュアーを閉じたら次のグループを起動する。

---

### STEP A: worktree の作成

メインの作業ディレクトリ（`MAIN_DIR=$(pwd)`）で、層ごとに `${CLAUDE_SKILL_DIR}/scripts/ensure-worktree.sh <dev|qa> <infra|app> <N>` を実行する（QA の part 2 以降は `<N>` に `N-pK`）。既存なら再利用し、メイン側の `vendor` / `node_modules` / `.venv` のコピーと `.env.example` からの `.env` 作成まで行う（メインの `.env` は秘密を含みうるのでコピーしない）。最後の行に worktree の絶対パスを出す。Laravel の `APP_KEY` など `.env` 作成後の初期化は implementer が行う（`doc/process/environment.md` に書いておく）。

作ったブランチ名を `implementation_progress.active_worktrees` に追加する。

---

### STEP B: implementer の起動

Agent Teams（`TeamCreate` / `team_name`）は使わない。implementer は**名前付きサブエージェント**として起動し、結果は最終回答（JSON）で受け取る。

- 層の中では **Dev と QA（すべての part）を同じターンで `run_in_background=true` で起動する**。Cross は infra 層が STEP D まで終わってから app 層を起動する
- プロンプトは `prompts/dev.md` / `prompts/qa.md` を Read し、プレースホルダーを置換して渡す: `{TEAM}`（`app` / `infra`）・`{TEAM_LABEL}`（`App` / `Infra`）・`{TEAM_DOCS}`（ファイル冒頭の説明どおり）・`{GROUP_N}`（QA の part 2 以降は `N-pK`）・`{TASKS}`（その層の Dev タスク / その part の QA タスクと担当ファイル）・`{MAIN_DIR}`・`{MODE}`・`{BASELINE_COMMIT}` ほか
- **QA に Dev を待たせない**: 「Dev の実装ができたら」「Dev ブランチを取り込んでから」のような指示を足さない。QA は仕様書だけで書き終えて完了を返し、Dev との突き合わせは STEP C.5 で行う（Dev を sleep / until で待つコマンドは hook `dev-wait-guard.sh` が拒否する）
- 長時間動くので、起動後にツールを実行しないままハングすることがある。疑わしければ `${CLAUDE_SKILL_DIR}/../dev-flow/reference/agent-hang-recovery.md` に従う

**プロンプトへの注入**（詳細は [reference/agent-prompt-injection.md](reference/agent-prompt-injection.md)）:

1. **言語・フレームワーク規約**: 同ファイルの表の順に Read し、「書き方」を `{CONVENTIONS}`（implementer）、「レビューチェックリスト」を `{REVIEW_CHECKLIST}`（reviewer）、「標準コマンド」を `{STANDARD_COMMANDS}`（両方）に入れる。規約のバージョン照合（`conventions_verified.md`）が無ければ先に作る
2. **実行環境ノート**: `doc/process/environment.md` があれば全文をプロンプト冒頭に入れる。オーケストレーターが環境差異に気付いた時点で作り、以後の全エージェントに配る
3. **memory フィードバック**: `~/.claude/projects/$(pwd | sed 's|/|-|g')/memory/` の `feedback_review_*.md` / `feedback_test_failures.md` を冒頭に入れる
4. **ファイルスコープ**: 担当 worktree 配下の作業許可パターンと禁止パターンを明示する
5. **Opus 昇格時（cost のみ）**: Sonnet の試行履歴と未解決指摘を冒頭に入れる

**implementer のモデル**

- quality: 初回から `opus`。昇格ラダーは使わない。レビュー指摘の修正は同じ implementer に `SendMessage` で渡す（STEP D の最大 3 回が上限）
- cost: `sonnet` で始め（`state.json.task_complexity` が新規アーキテクチャ・横断的な変更なら `opus`）、次の昇格ラダーに従う

  | 段階 | モデル | 試行 | 次へ進む条件 |
  |---|---|---|---|
  | 初回実装 | `sonnet` | 1 回 | 完了 → レビューへ |
  | 修正（Sonnet） | `sonnet` | 最大 2 回 | 指摘が設計レベル → Opus に昇格 |
  | 修正（Opus） | `opus` | 最大 3 回 | 上限 → 人間にエスカレーション |

3 回以上繰り返された指摘や、人間によるマージ後の修正は、agent-prompt-injection.md のフォーマットで memory に保存し、次回のフローで注入する。

---

### STEP C: 完了の受け取り

完了通知（最終回答の JSON）を待つ。`sleep` でポーリングしない。タイムアウトの目安（cost: haiku 5 分 / sonnet 15 分 / opus 30 分。quality: 起動 5 分後に一次確認、生存確認に 3 分応答が無ければハング）を超えたら agent-hang-recovery.md で切り分ける。

| status | blocker_type | 対応 |
|---|---|---|
| `"completed"` | — | 下の「完了 JSON の確認」 |
| `"blocked"` | `"plan_repair_needed"` | Plan Repair（下記） |
| `"blocked"` | その他 | AskUserQuestion で人間に判断を仰ぐ |
| `"failed"` | — | AskUserQuestion で人間に報告し指示を仰ぐ |

**完了 JSON の確認**（満たさなければ同じ implementer を `SendMessage` で再開して直させる。各項目最大 2 回、それでも駄目なら `failed` 扱い）:

- 共通: `result.lint.exit_code` が 0（欠損も不可）
- Dev: `result.unit_tests.failed` が 0、`result.coverage.changed_functions_below_threshold` が空（テストを減らす方向の修正は却下）、`result.mutation` があり `killed: false` が無い（無ければ「重要な分岐を最大 5 件選んでミューテーション確認をし、生き残った変異はテストを強化する」よう依頼）
- QA: `result.tests.failed` は Dev 実装が無いので非 0 が正常。`note` に「Dev 実装待ち」以外の原因（構文エラー・セットアップ不備）があるときだけ再開させる

通ったら `result.commits` を記録して次へ進む。`needs_human_review` や `uncertainty_points` があってもここでは人間に聞かない（STEP D でレビュアーが判定する）。最終回答に JSON が無ければ、`git log` のコミットと STEP C.5 の結果で代わりに確かめる。

**Plan Repair**（詳細は [reference/plan-repair.md](reference/plan-repair.md)）: 発動は最大 3 回（超えたら `requirement_ambiguity` でエスカレーション）。AskUserQuestion で「承認 / 却下 / 全体再生成」を出し、承認なら `state.json.next_stage` を `"plan_repair"` にして終える（オーケストレーターが consistency を mini モードで実行し、未着手グループから再開する）。履歴は `doc/process/plan_repair_log.md`。

---

### STEP C.5: Dev + QA 統合検証（レビュー前に必ず実施）

QA は Dev の実装を見ずにインターフェースを推測してテストを書いているので、合わせて初めて分かる不一致（属性名・文言の違い、テスト間の状態の残留、同名ファイルのコンフリクト）が高い確率で出る。レビュアーに渡す前に、オーケストレーターが機械的に統合して実テストを回す。

手順（QA worktree に Dev ブランチを検証用マージ → Dev ユニット + QA 仕様テスト・lint・型検査 → 不備は該当 implementer に差し戻し → QA のミューテーション確認を依頼 → 検証マージを `reset --hard` で取り消し → 統合結果を PR 説明に書く）は [reference/integration-check.md](reference/integration-check.md) に従う。QA の part ごとに、それぞれの QA worktree で行う（順番は問わない）。

---

### STEP D: レビュー

レビュアーは `model="opus"` で、昇格ラダーは無い（設計判断・セキュリティ判断の質を最重視する）。層ごとに Dev レビューと QA レビュー（part ごと）を行う。

| | 実行順 |
|---|---|
| cost | 層ごとに Dev レビュー → QA レビュー（同期実行、`run_in_background=false`） |
| quality | 層ごとに Dev レビュー ∥ QA レビューを同じターンで `run_in_background=true` で起動し、両方の完了を待つ。修正ループも並行してよい |

Cross は infra 層 → app 層の順を保つ。修正でインターフェース・エラーメッセージ・ID が変わり Dev と QA が食い違いうるときは、両方の承認後に STEP C.5 をもう一度回してから STEP E へ進む。

**エンジン（codex 優先）:** ステージ最初のレビューの前に `state.json.reviewer_engine`（無ければ `auto`）を見る。`auto` なら `${CLAUDE_SKILL_DIR}/../dev-flow/codex/review.sh available` を 1 回実行し、0 ならすべてのレビューを Codex CLI で動かす（`claude`、または `available` が 3 なら Claude のサブエージェント）。起動・待ち方・失敗時の切り替えは `${CLAUDE_SKILL_DIR}/../dev-flow/reference/codex-review.md`（種別 `impl`、cwd はレビュー対象の worktree）。codex なら Dev と QA は cost でも並べて起動してよい。それ以外（プロンプト・修正ループ・指摘の渡し方）は Claude と同じ。

**プロンプト:** `prompts/reviewer-dev.md` / `prompts/reviewer-qa.md` を Read し、プレースホルダー（`{TEAM}` `{TEAM_LABEL}` `{GROUP_N}` `{BASE_BRANCH}`（`implementation_progress.base_branch`）・仕様書パス・`{tech_stack}` `{REVIEW_CHECKLIST}`、QA は `{QA_MUTATION}`）を置換する。Agent ツールにはツール制限が無いので、Claude のレビュアーにはプロンプト冒頭に「**ファイルの編集・作成は禁止。Read / Grep / Bash（読み取り系）のみで確認し、指摘は最終回答で返す（ミューテーションの再現で一時的に壊したファイルは直後に `git checkout --` で戻す）**」を付ける。対応する implementer の `uncertainty_points` を JSON のまま末尾に付ける。

**レビュー結果の保存:** 最終回答 JSON を受け取ったらすぐ、メインの作業ディレクトリの `doc/process/reviews/group-{N}-{dev|qa}-{team}-r{回数}.json` に Write する（codex は `review.sh wait` が書くので不要）。STEP H の集約はこのファイルから行う。

**修正ループ（最大 3 回）:** `changes_requested` なら、`findings` を**要約・取捨選択せず JSON のまま**、その implementer に `SendMessage` で渡す（一部だけ渡すと残りが直らないまま再レビューに回り、往復が増える）:

```
以下はレビュアーの指摘（JSON そのまま）です。severity が blocker / major のものはすべて直してください。minor は参考です（直さなくてよい）。
直したら、指摘ごとに「どう直したか / 直さなかった理由」を完了 JSON の result.review_responses に書いてください。
<findings の JSON 全文>
```

- minor は修正ループに回さない（記録だけ）。同じ `rule` が 3 回以上出たら memory に保存する
- **再レビュー**: レビュー起動時に対象 worktree の `git rev-parse HEAD` を控え、再レビューではそれを `{PREV_REVIEWED_COMMIT}` として、前回の `findings` と `review_responses` と一緒に渡す。レビュアーが見るのは「前回の blocker / major が解消したか」と「`git diff {PREV_REVIEWED_COMMIT}..HEAD` に新しい問題が無いか」だけ（初回に全部挙げ、2 回目以降は差分だけを見れば 1 往復で片付く）
- **3 回目の修正後も blocker / major が残ったら**、残りの `findings` を JSON のまま AskUserQuestion で出す（この指摘を残したまま PR にする / もう 1 回直させる / 自分で直す）。上限まで回るのはレビュアーと implementer の判断が食い違っているときで、続けても収まらない

**uncertainty の判定:** レビュアーは `uncertainty_verdicts` で 1 件ずつ `resolved` / `needs_human` を返す。`needs_human` が 1 件でもあるときだけ AskUserQuestion で人間に確かめる。

**グループの範囲で直せない指摘（人間に聞かない）:**

| 指摘の種類 | 処理 |
|---|---|
| 差分に無い既存コードの問題（`existing/` 付きの rule。同じ書き方を新しいコードが真似ているだけのものも含む） | 直さない。`review-findings-backlog.md` に「既存コード」と書いて記録し、承認扱いにする。新しいコードだけ規約に合わせられるなら、それは直す |
| 規約そのものを変える話 | 直さない。backlog に記録し、compliance の報告で推奨とあわせて出す |
| 仕様（テスト定義書）に無い振る舞いのテストが欲しい | minor なら backlog に記録して進む。major なら Plan Repair に回す |
| `spec_cache.md` などの内部資料が古い | その場で直す |

---

### STEP E: PR 作成

レビュー承認後、層ごとに Dev / QA（part 2 以降も 1 本ずつ）のブランチを push して PR を作る。

- ラベル: Infra → `infra`、App → `app`、Cross → infra 層に `infra,cross`、app 層に `app,cross`
- タイトル例: `feat(infra): グループ N Infra Dev タスク実装` / `test(app): グループ N App QA タスク実装`（part は `part K` を入れる）
- **Draft にしない**（`--draft` を付けない）。レビューはエージェントが済ませている。プロジェクトの CLAUDE.md に「PR は Draft」とあっても、スキル経由の PR を例外とする記述があればそちらに従う（無ければ人間に確認する）
- `--base "$BASE_BRANCH"` を明示し、PR 番号を `implementation_progress.pr_numbers["group-N"]` に**配列**で追記する

---

### STEP F: worktree の削除

PR 作成後、このグループの worktree をすべて（QA の part も）`git worktree remove {MAIN_DIR}/../worktree-{dev|qa}-{team}-group-{N} --force` で消す。ブランチは STEP H まで残す。

---

### STEP G: ドキュメント誤りの記録とマージ

**doc_issues:** 完了 JSON に `doc_issues` があれば `doc/process/doc_issues.md` に記録する。**グループの完了ごとには人間に聞かず**、全グループ完了後にまとめて聞く（例外と判断の 3 択・doc-fix フローは [reference/doc-issues.md](reference/doc-issues.md)）。

**自動マージ（非ブロッキング）:** **人間による**マージは待たない。まず hook の有無を確かめる：

```bash
jq -e '[.. | strings | select(test("pr-merge-guard"))] | length > 0' ~/.claude/settings.json >/dev/null 2>&1 && echo enabled || echo disabled
```

- `disabled` → `gh pr merge` を発行せず、PR URL を人間に示して終える（マージ後に `/dev-flow` で再入）
- `enabled` → 各 PR に 1 コマンドずつ `gh pr merge <N> --merge --delete-branch` を実行する。hook `pr-merge-guard.sh` が条件（ベースが `feature/*` かつ `base_branch` と一致・CI 全通過・コンフリクトなし・DB 破壊的変更なし）を満たさなければ deny する。CI 未完了で deny されたら `timeout 900 gh pr checks <N> --watch --fail-fast` で完了を待って 1 回だけ再試行する（`sleep` のポーリングではない）

Dev → QA（各 part）の順序と `update-branch`、`mergeable=UNKNOWN` の待ち方、deny 理由の分類と PR コメントは [reference/merge-ops.md](reference/merge-ops.md) に従う。

---

### STEP H: マージ後の後片付け

グループの全 PR が `MERGED` であることを `gh pr view <N> --json state` で確かめてから：

1. このグループのローカル・リモートのブランチ（QA の part も）を消す
2. **実バージョンの確認（基盤グループのみ）**: 「実バージョンの書き戻し」タスクを含むグループなら、`state.json.tech_stack.language_version` / `framework_version` が lock ファイルと一致しているか `jq` で確かめる。書き戻されていなければ lock から読んで `state.json` だけ更新する（要件定義書は compliance の乖離として残す）
3. **レビュー findings の集約**: `doc/process/reviews/group-{N}-*.json` の `findings` のうち、`rule` が `review/*`（規約に無かった指摘）でプロジェクト固有でない汎用的なものを `doc/process/review-findings-backlog.md` に追記する（`| グループ | rule | severity | 内容 | 該当ファイル | 昇格先候補 |`。同じ内容があれば「回数」列を増やす）。compliance が「規約ファイルへの昇格候補」として人間に出す
4. `${CLAUDE_SKILL_DIR}/../dev-flow/hooks/mark-group-done.sh N <PR番号...>` を 1 回の Bash で実行する（チェックリストの `[x]` 化・`completed_groups` / `active_worktrees` / `pr_numbers` の更新・1 コミット。冪等）。スクリプトが無い環境では同じことを手で行う
5. このグループの Dev / QA implementer と Claude のレビュアーを `TaskStop` で閉じ、待っているグループを起動する

---

## 全グループ完了後

1. **たまった doc_issues をまとめて聞く**（[reference/doc-issues.md](reference/doc-issues.md)）。「実装側で対応する」が 1 件でもあれば Plan Repair に回し、追加グループが終わってから次へ進む
2. **リモートの実際の状態を確かめる**（state.json を更新する前に。しないと hook `state-sync.sh` が test への移行を拒否する）：
   ```bash
   git switch {base_branch} && git pull --ff-only
   ${CLAUDE_SKILL_DIR}/../dev-flow/hooks/verify-remote-state.sh --expect-merged   # PR 番号は state.json の pr_numbers から取る
   ```
   `summary: NG 0` 以外なら test に進まず、NG の行を解消してからもう一度実行する
3. `doc/process/state.json` を更新する：
   - `next_stage` を `"test"` に
   - `base_branch` に `implementation_progress.base_branch` を写す（test が origin と同期するのに使う）
   - `implementation_progress` を削除
   - **`mode == "incremental"` のときだけ**: 上書きする前の `baseline_commit` を `diff_base_commit` に写し（compliance が今回の差分を取るのに使う）、`baseline_commit` を `git rev-parse HEAD` で上書きする（詳細は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/state-schema.md`「baseline_commit のライフサイクル」）
4. 人間に「implementation 完了。次は `/dev-flow` を実行して test（テスト実行）に進んでください」と伝える（2 の出力を添える）

---

## エラーハンドリング

| 状況 | 対応 |
|---|---|
| worktree 作成失敗 | 既存 worktree を消してから再試行 |
| push 失敗 | 人間に報告して解消後に再 push |
| lint エラーが解消できない | 人間に報告 |
| ブロッカー発生 | エージェントを止めて人間に判断を仰ぐ |
| state.json とリモートの食い違い（worktree 作成失敗・ブランチ衝突・state.json 破損） | GitHub 側のマージ状態を正として復旧する。[reference/recovery.md](reference/recovery.md) |
