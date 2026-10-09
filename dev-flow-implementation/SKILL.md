---
name: dev-flow-implementation
description: AI駆動開発フローの implementation ステージ（4/6: 並列実装）。タスクチェックリストの DAG `depends_on` を解決しながらグループを並列実行し、各グループ内で Dev/QA を独立 worktree で並列実装します。Infra/App/Cross の 3 種類のチーム構成に対応し、設計判断の記録・Plan Repair・独立レビュアー・Implements/Tests コミットフッターを伴います。整合性チェック完了後、または `--from=implementation` 起動時に使用します。
model: haiku
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---


# Stage 4/6 implementation: 並列実装（git worktree ワークフロー）

## 入力

状態ファイル `doc/process/state.json` から読み込み：
- requirements_paths
- test_spec_path
- api_spec_path (IS_API=true の場合)
- infra_spec_path (IS_INFRA=true の場合)
- mock_path (IS_GUI=true の場合)
- tech_stack
- is_gui
- is_api
- is_infra
- mode（`"full"` または `"incremental"`）
- baseline_commit（`incremental` 時のみ有効）

**モードによる動作の違い：**

| 項目 | full | incremental |
|---|---|---|
| 実装範囲 | チェックリストの全タスク | チェックリストのタスク（差分のみ・既に consistency で絞り込み済み） |
| 既存コードの扱い | 参照のみ（スタイル・規約を合わせる） | 必ず確認し、既存実装がある箇所はスキップ |
| dev/qa implementer モデル | quality: `opus` / cost: `sonnet` | quality: `opus` / cost: `sonnet` |

モデル・レビューの並列化・待ち時間は `state.json.profile`（無ければ `quality`）で決まる。詳細は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/profiles.md`。このスキルの `model="…"` の記述は、断りが無ければ cost の値として読む。

## 事前準備

### STEP 0-a: チェックリストの読み込みと再開判定

`doc/process/task_checklist.md` を Read ツールで読み込み、「並列実行グループ」セクションを解析します。

- グループ数を確認する（グループ 1、グループ 2、...）
- 各グループの Dev タスク・QA タスクを一覧化する
- **各グループのチーム種別（Infra / App / Cross）を抽出する**（グループ見出しから `(Infra)`, `(App)`, `(Cross)` を読み取る）
- **各グループの `depends_on` を抽出する**（例: `depends_on: [group-1, group-2]`）

例: `### グループ 1 (Infra) — depends_on: []` → `group_types["group-1"] = "Infra"`, `depends_on["group-1"] = []`

**DAGベース並列実行の初期化:**

`depends_on` を解析して実行可能グループを特定します：

- `depends_on` が空のグループ → **即時実行可能**
- `depends_on` に完了済みグループがすべて含まれるグループ → **実行可能**
- 上記以外 → **待機中**

次に `doc/process/state.json` を Read ツールで読み込み、`implementation_progress` フィールドを確認します。

**`implementation_progress` が存在する場合（前回の中断あり）:**

0. **マージ待ち PR の取り込み**: `pr_numbers` に番号があり `completed_groups` に無いグループについて、各 PR を `gh pr view <N> --json state,url` で確認する
   - グループの全 PR が `MERGED` → そのグループの STEP H（クリーンアップ・`completed_groups` 追加）を実行
   - `OPEN` の PR がある → STEP G の自動マージ試行を再実行。それでも deny された場合はそのグループを「人間マージ待ち」として扱う
   - `CLOSED`（マージされずに閉じられた）→ AskUserQuestion で人間に確認（再作成 / グループをやり直す）
1. `completed_groups` を確認 → 完了済みグループはスキップ対象に記録
2. `active_worktrees` を確認 → 残存 worktree があれば以下でクリーンアップ：
   ```bash
   git worktree remove {MAIN_DIR}/../worktree-dev-group-N --force 2>/dev/null || true
   git worktree remove {MAIN_DIR}/../worktree-qa-group-N --force 2>/dev/null || true
   ```
3. AskUserQuestion で人間に確認：「グループ X から再開します。よろしいですか？」
   - 「再開する」→ 完了済みグループをスキップして処理継続
   - 「最初からやり直す」→ `implementation_progress` を初期化して全グループを再実行

**`implementation_progress` が存在しない場合（初回実行）:**

まず STEP 0-b でベースブランチを確認してから `implementation_progress` を初期化します（base_branch が確定してから書き込むため）。

### STEP 0-b: ベースブランチの確認

```bash
git branch --show-current
```

現在のブランチ名を BASE_BRANCH として記録します。

- `main` / `master` / `develop` / `release/*` / `hotfix/*` の上にいたら、そこへ PR を出すと自動マージが常に拒否されるので、`git switch -c feature/{task を表す英小文字とハイフンの短い名前}` で作業用ブランチを作り、それを BASE_BRANCH にする（それまでのドキュメントのコミットはそのまま引き継がれる。元のブランチは触らない）
- BASE_BRANCH を `git push -u origin {BASE_BRANCH}` で push してから worktree を作る。push していないと、PR の差分に requirements〜consistency のドキュメントのコミットまで入り、Dev / QA の PR の範囲がずれる（2026-10 の change の検証で起きた）

その後 state.json に `implementation_progress` を初期化して書き込みます：

```json
{
  "implementation_progress": {
    "total_groups": {グループ数},
    "completed_groups": [],
    "active_worktrees": [],
    "base_branch": "{git branch --show-current の結果}",
    "group_types": {
      "group-1": "Infra",
      "group-2": "App",
      "group-3": "Cross"
    },
    "pr_numbers": {}
  }
}
```

`group_types` は STEP 0-a で抽出したチーム種別をすべて記録します。`pr_numbers` は STEP E で PR を作成するたびに `"group-N": [番号, ...]` を追記します

---

## DAGベースのグループ実行ループ

DAGの依存関係に従って、実行可能なグループを並列に処理します。

**実行モデル:**
- **グループ間**: DAGベース並列（`depends_on` が解決済みのグループを同時に起動）
- **グループ内**: 並列（Dev と QA を同時に worktree で実行）

**実行アルゴリズム:**

```
while 未完了グループが存在する:
  実行可能グループ = depends_on が全て completed_groups に含まれるグループ
  実行可能グループ を並列に STEP A〜H まで起動（Background で複数グループ同時進行。同時に動かすのは最大 4 グループ）
  いずれかのグループ完了 → completed_groups に追加
  次の実行可能グループを評価して追加起動
```

**同時に動かすグループは最大 4 つ、implementer は最大 8 本**（QA を part に分けたグループは part の数だけ implementer が増える）。consistency はグループを小さく切る（Dev タスク 3 件・TC 8 件まで）のでグループ数が増えるが、implementer は tmux のペインを 1 つずつ使い、上限に達するとレビュアーや修正の implementer を起動できなくなる。実行可能なグループが 5 つ以上あるときは、`depends_on` で後続に待たれている数が多いグループから起動する。グループの PR がマージされて Dev / QA implementer とレビュアーを閉じたら（STEP H）、次のグループを起動する。

**state.json の `implementation_progress` に `depends_on` マップを追加:**

```json
{
  "implementation_progress": {
    "depends_on": {
      "group-1": [],
      "group-2": [],
      "group-3": ["group-1", "group-2"]
    }
  }
}
```

**worktree 構造:**
- Dev 用と QA 用で2本の worktree を作成（並列実行のため独立したブランチが必要）
- 例: Infra グループ → `dev/infra-group-N` と `qa/infra-group-N` の2ブランチ

---

### STEP A: チーム種別判定と worktree の作成（グループ開始時）

`completed_groups` に含まれるグループは **スキップ** して次のグループへ進みます。

**QA タスクが part に分かれたグループ:** チェックリストの `#### QA タスク (…) — part K` が 2 つ以上あるグループは、part ごとに QA の worktree・ブランチ・implementer・レビュー・PR を 1 組ずつ作る。part 1 は通常どおりの名前（`qa/app-group-N`）、part 2 以降は `N-pK` を付ける（`ensure-worktree.sh qa app N-p2` → worktree `worktree-qa-app-group-N-p2`、ブランチ `qa/app-group-N-p2`、エージェント名 `qa-implementer-app-group-N-p2`）。どの part も Dev と同時に起動する（QA は Dev の実装を待たない）。以下の STEP の「QA」は、part があれば part ごとに読む。

**QA タスクが無いグループの扱い:** タスクチェックリストの `#### QA タスク` に実タスクが無く「対応する TC なし」等の注記のみのグループ（基盤構築グループに多い）は、**QA 用の worktree・ブランチ・エージェントを作らない**。Dev 側のみで STEP A〜H を進め、PR も Dev の 1 本だけ作る。使わない QA ブランチを作ると後で削除の手間が増えるだけで意味が無い。

まず、`state.json` の `implementation_progress.group_types["group-N"]` からグループのチーム種別を取得し、実行するエージェントを決定します：

| チーム種別 | 実行するエージェント | 説明 |
|---|---|---|
| `Infra` | Dev (Infra) + QA (Infra) のみ | アプリチームは起動しない |
| `App` | Dev (App) + QA (App) のみ | インフラチームは起動しない |
| `Cross` | Dev (Infra) → QA (Infra) → Dev (App) → QA (App) | インフラ実装・テスト完了後にアプリ実装を開始（直列） |

メインの作業ディレクトリ（`MAIN_DIR=$(pwd)`）で、必要な worktree を `${CLAUDE_SKILL_DIR}/scripts/ensure-worktree.sh <dev|qa> <infra|app> <N>` で作る。既存なら再利用し、メイン側の `vendor` / `node_modules` / `.venv` のコピーと `.env.example` からの `.env` 作成までを行う（メインの `.env` は秘密を含みうるのでコピーしない）。最後の行に worktree の絶対パスを出す。Laravel の `APP_KEY` など `.env` 作成後の初期化は implementer が行う（`doc/process/environment.md` に書いておく）。

| チーム種別 | 作成する worktree |
|---|---|
| **Infra** | `ensure-worktree.sh dev infra N` + `ensure-worktree.sh qa infra N` |
| **App** | `ensure-worktree.sh dev app N` + `ensure-worktree.sh qa app N` |
| **Cross** | Infra と App の 4 つすべて |

worktree 作成後、state.json の `implementation_progress.active_worktrees` に作成したブランチ名を追加します：
- Infra: `["dev/infra-group-N", "qa/infra-group-N"]`
- App: `["dev/app-group-N", "qa/app-group-N"]`
- Cross: `["dev/infra-group-N", "qa/infra-group-N", "dev/app-group-N", "qa/app-group-N"]`

---

### STEP B: チーム種別に応じたエージェント起動

Agent Teams（`TeamCreate` / `team_name`）は使用しません。各 implementer は **名前付きサブエージェント**として起動し、結果は最終回答（JSON）で受け取ります。並列起動するものは `run_in_background=true` で同一ターンに起動し、順次起動するものは `run_in_background=false` で 1 つずつ起動します。

Dev/QA implementer は数十分単位で稼働するため、pane 型サブエージェントが起動後にツールを一切実行しないままハングする既知の問題の影響を受けやすい。STEP C の完了待機中にハングが疑われる場合は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/agent-hang-recovery.md` の検知手順・fork フォールバック手順に従う。

グループのチーム種別に応じて、以下のパターンでエージェントを起動します：

**Infra グループ：Dev (Infra) + QA (Infra) を並列起動**

**App グループ：Dev (App) + QA (App) を並列起動**

**Cross グループ：Dev (Infra) → QA (Infra) → Dev (App) → QA (App) を順次起動**（インフラ実装・テスト完了を待ってからアプリ実装を開始）

各エージェントのプロンプトは以下のファイルを Read ツールで読み込んで使用します：

| エージェント | プロンプトファイル | 起動条件 |
|---|---|---|
| dev-implementer-infra-group-N | `${CLAUDE_SKILL_DIR}/prompts/dev-infra.md` | Infra / Cross グループ |
| dev-implementer-app-group-N | `${CLAUDE_SKILL_DIR}/prompts/dev-app.md` | App / Cross グループ |
| qa-implementer-infra-group-N | `${CLAUDE_SKILL_DIR}/prompts/qa-infra.md` | Infra グループ |
| qa-implementer-app-group-N | `${CLAUDE_SKILL_DIR}/prompts/qa-app.md` | App / Cross グループ |

プロンプトファイル内のプレースホルダー（`{GROUP_N}`, `{MAIN_DIR}`, `{MODE}` 等）を実際の値に置換してからエージェントに渡すこと。QA の part 2 以降は `{GROUP_N}` を `N-pK` に置き換え（worktree のパスとエージェント名がそれで決まる）、`{QA_APP_TASKS}` / `{QA_INFRA_TASKS}` にはその part のタスクと担当ファイルだけを入れる。

**QA に Dev を待たせない:** QA implementer に「Dev の実装ができたら」「Dev ブランチを取り込んでから」のような指示を足さない。QA は仕様書だけでテストを書き終えて完了を返し、Dev の実装と合わせるのは STEP C.5（統合検証）でオーケストレーターが行う。2026-10 の notify-hub では、QA が Dev ブランチにファイルができるのを `until git cat-file -e ...; do sleep 15; done` で 10 分ずつ待ち、その間 QA が完了しないためにグループ全体が遅れた。

**エージェント起動前のプロンプト追加注入:**

各エージェント起動前に、以下を順にプロンプトへ注入する。詳細は [reference/agent-prompt-injection.md](reference/agent-prompt-injection.md) を参照。

0. **規約のバージョン照合**: `doc/process/conventions_verified.md` が無い、または `verified_for` のバージョンが `tech_stack.language_version` / `framework_version` と違う場合、[reference/conventions/version-check.md](reference/conventions/version-check.md) の手順で `conventions-verifier` エージェント（`model` は quality: `"opus"` / cost: `"sonnet"`、WebFetch 使用）を先に実行して生成する。バージョンが未検出ならマニフェストから検出して `tech_stack` に書き戻す。WebFetch が使えない環境では「未検証」と明記して先へ進む（止めない）
1. **言語・フレームワーク規約**: 下の表の順に `reference/conventions/` のファイルを**すべて** Read し、「書き方」セクションを implementer に、「レビューチェックリスト」を reviewer に、「標準コマンド」を両方に注入する（`{CONVENTIONS}` / `{REVIEW_CHECKLIST}` / `{STANDARD_COMMANDS}` プレースホルダー）。後のものが前のものを上書き・補足する

   | 順 | ファイル | 選び方 |
   |---|---|---|
   | 0 | `{project}/doc/process/conventions_verified.md` | **最優先**。「変わった項目」「新しい推奨」を両プレースホルダーの**先頭**に「バージョン照合結果（規約ファイルより優先）」として置く（手順 0 で生成済み） |
   | 1 | `testing.md` / `maintainability.md` | 常に |
   | 1' | `<language>.md` | `tech_stack.language` から: Go → `go.md`、TypeScript → `typescript.md`、PHP → `php.md`、Python → `python.md`、Shell / Bash → `shell.md`。Infra グループで差分に `*.sh` / `*.bats` が出る見込みなら language に関係なく `shell.md` も |
   | 2 | `<framework>.md` | `tech_stack.framework` から: Next.js → `nextjs.md`（`react.md` も先に）、Laravel → `laravel.md`、React → `react.md`。無ければ飛ばす |
   | 3 | `{project}/doc/conventions.md` | あれば。言語・フレームワーク規約と矛盾したらこちらが優先 |

   名前の大文字小文字・`.js` の有無・`Golang` / `Go` の揺れは無視して一致させる。対応ファイルが無い言語は `_template.md` の観点だけで進め、最終報告で「規約ファイル未整備: {language}」と伝える。プロジェクトの `CLAUDE.md` はサブエージェントが自動で読むので注入しない（規約が書かれていればレビュー基準として扱わせる）
1.5. **実行環境ノート**: `doc/process/environment.md` があれば全文を「実行環境ノート」としてプロンプト冒頭に注入する（node のバージョン切替・PATH・タイムアウト・ポートの後始末など、コマンドを動かすための注意。無ければ省略。詳細は [reference/agent-prompt-injection.md](reference/agent-prompt-injection.md)）。オーケストレーター自身が環境差異に気付いた時点で作成し、以後の全エージェントに配る
2. **memory フィードバック**: `~/.claude/projects/$(pwd | sed 's|/|-|g')/memory/` 配下の `feedback_review_*.md` / `feedback_test_failures.md` を読み込んでプロンプト冒頭に追記
3. **ファイルスコープガードレール**: 担当 worktree 配下の作業許可パターンと禁止パターンを明示
4. **Opus 昇格時（cost のみ）**: Sonnet 試行履歴と未解決指摘を冒頭に追記

**implementer のモデル（quality）:** 初回から `opus` で起動し、昇格ラダーは使わない。レビュー指摘の修正は同じ implementer に `SendMessage` で渡して直させる（STEP D の最大 3 回がそのまま上限）。`task_complexity` は見ない。

**昇格ラダー（Dev/QA implementer、cost のみ）:**

| 段階 | モデル | 試行 | 昇格条件 |
|---|---|---|---|
| 初回実装 | `sonnet` | 1回 | 完了 → レビューへ |
| 修正実装（Sonnet） | `sonnet` | 最大2回 | レビュー指摘が設計レベルと判定 → Opus 昇格 |
| 修正実装（Opus） | `opus` | 最大3回 | 上限到達 → 人間エスカレーション |

タスク種別による初期モデルの選択：
- CRUD 追加・設定変更など単純タスク → `sonnet` で開始
- 新規アーキテクチャ要素・横断的な変更 → `opus` で開始（state.json の `task_complexity` フィールドで制御。未設定の場合は `sonnet` で開始）

**レビュー指摘・テスト失敗の memory 保存（STEP D/STEP H 後）:**

3 回以上繰り返された指摘や人間によるマージ後修正は、`reference/agent-prompt-injection.md` のフォーマットで memory に保存して次回フローで再注入する。

---

### STEP C: エージェントの完了待機

グループのチーム種別に応じて、各エージェントの完了通知（最終回答の JSON）を待ちます。`sleep` ポーリングはしません。ただし、タイムアウト目安（cost: モデル別に haiku=5分/sonnet=15分/opus=30分。quality: 起動 5 分後に一次確認、生存確認に 3 分応答が無ければハング）を超えても完了通知が無い場合は、`${CLAUDE_SKILL_DIR}/../dev-flow/reference/agent-hang-recovery.md` の手順でハングかどうかを切り分け、該当すれば同ファイルの fork フォールバックで当該エージェントを再起動する：

- **Infra**: `dev-implementer-infra-group-N` + `qa-implementer-infra-group-N` の両方
- **App**: `dev-implementer-app-group-N` + `qa-implementer-app-group-N` の両方
- **Cross**: `dev-implementer-infra-group-N` → `qa-implementer-infra-group-N` → `dev-implementer-app-group-N` → `qa-implementer-app-group-N`（順次）

**JSON パース処理:**

受け取った最終回答を JSON としてパースし、`status` フィールドで以下の通り分岐します：

| status | blocker_type | 対応 |
|---|---|---|
| `"completed"` | — | `result.lint.exit_code` が **0 以外、または欠損**なら「lint / format / 型検査が通っていない」として同じ implementer を `SendMessage` で再開して解消させる（最大 2 回、それでも通らなければ `failed` 扱い）。**Dev implementer** は加えて `result.unit_tests.failed` が 0 でない、または `result.coverage.changed_functions_below_threshold` が**空でなければ**「ユニットテストを直す / 未到達分岐のユニットテストを追加する」よう再開させる（最大 2 回。テストを減らす方向の修正は却下）。`result.mutation` が無い、または `killed: false` が残っていれば「重要な分岐を最大 5 件選んでミューテーション確認（[conventions/testing.md](reference/conventions/testing.md)「ミューテーション確認」）をして、生き残った変異はテストを強化する」よう再開させる（最大 2 回）。**QA implementer** の `result.tests.failed` は Dev 実装が無い worktree では非 0 が正常なので、`note` に「Dev 実装待ち」以外の原因（構文エラー・セットアップ不備）が書かれている場合だけ再開させる。通ったら `result.commits` をログに記録して次の処理へ進む。`needs_human_review = true` や `uncertainty_points` があっても、ここでは人間に聞かない（STEP D でレビュアーに判定させる）。最終回答に JSON が無い（途中終了など）ときは、`git log` のコミットと STEP C.5 の統合検証の結果で代わりに確かめる |
| `"blocked"` | `"plan_repair_needed"` | **Plan Repair フローへ移行**（下記参照） |
| `"blocked"` | その他 | AskUserQuestion で人間に判断を仰ぐ |
| `"failed"` | — | AskUserQuestion で人間に報告し指示を仰ぐ |

**Plan Repair フロー（`blocker_type: "plan_repair_needed"` 受信時）:**

詳細手順は [reference/plan-repair.md](reference/plan-repair.md) を参照。要点：

- 発動上限 3 回。超過時は `requirement_ambiguity` として人間エスカレーション
- AskUserQuestion で「承認 / 却下 / 全体再生成」の3択を提示
- 承認時は `state.json.next_stage` を `"plan_repair"` に切替えて終了。オーケストレーターが consistency を mini モードで実行し、完了後 implementation を未着手グループから再開
- 修正履歴は `doc/process/plan_repair_log.md` に追記

JSON パース失敗時のフォールバックは reference 参照。

---

### STEP C.5: Dev + QA 統合検証（レビュー前に必ず実施）

Dev と QA は別 worktree で並行して作業しており、**QA は Dev の実装を見ずにインターフェースを推測してテストを書いている**。そのため、両者を合わせて初めて分かる不一致が高確率で発生する（実例: aria-label の命名違い、React Testing Library の `cleanup` 未登録によるテスト間の DOM 残留、エラーメッセージの句点有無、Dev/QA 双方が同名テストファイルを作成してのコンフリクト）。レビュアーに渡す前に、オーケストレーターが機械的に統合して実テストを回す。

手順（QA worktree に Dev ブランチを検証用マージ → Dev ユニット + QA 仕様テスト・lint・型検査を実行 → 不備は該当 implementer に差し戻し → 検証マージを `reset --hard` で取り消し → 統合結果を PR 説明に書く）は [reference/integration-check.md](reference/integration-check.md) を Read して従う。QA が part に分かれているときは、part ごとの QA worktree に同じ Dev ブランチを検証用にマージして、それぞれ行う（part 同士は担当ファイルが別なので、順番は問わない）。

---

### STEP D: レビュー

全エージェント完了後、チーム種別に応じてレビューを実行。各レビューは独立したエージェント（`model=opus`）で実行します。レビュアーは設計判断・セキュリティ判断の質を最重要視するため、昇格ラダーを設けず最初から Opus を使用します。

**実行順序（cost）：**
- **Infra**: Dev (Infra) レビュー → QA (Infra) レビュー
- **App**: Dev (App) レビュー → QA (App) レビュー
- **Cross**: Dev (Infra) レビュー → QA (Infra) レビュー → Dev (App) レビュー → QA (App) レビュー

**実行順序（quality）：** Dev レビューと QA レビューは見る worktree も観点も別なので、**同じ層の 2 本を同一ターンで同時に起動する**（どちらも `run_in_background=true`、両方の完了通知を待ってから次へ）。
- **Infra**: Dev (Infra) レビュー ∥ QA (Infra) レビュー
- **App**: Dev (App) レビュー ∥ QA (App) レビュー
- **Cross**: [Dev (Infra) ∥ QA (Infra)] → [Dev (App) ∥ QA (App)]。Infra の修正が App に響くことがあるので、層の順番は保つ
- 修正ループも並行してよい（Dev implementer と QA implementer は別 worktree）。どちらかの修正で Dev と QA の食い違いが出うる場合（インターフェース・エラーメッセージ・ID の変更）は、両方の承認が出た後に STEP C.5 の統合検証をもう一度回してから STEP E に進む

下の各レビューの「同期実行、`run_in_background=false`」は cost の指定。quality では上のとおりバックグラウンドで並べる。

**レビュアーのエンジン（codex 優先）:** このステージの最初のレビューの前に `state.json.reviewer_engine`（無ければ `auto`）を見る。`auto` なら `${CLAUDE_SKILL_DIR}/../dev-flow/codex/review.sh available` を 1 回実行し、0 なら以下の 4 種のレビューをすべて **Codex CLI で動かす**（`claude`、または `available` が 3 なら従来どおり Claude のサブエージェント）。起動・待ち方・失敗時に Claude へ切り替える手順は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/codex-review.md` を Read して従う（種別は `impl`、cwd はレビュー対象の worktree）。codex のときは下の「Agent を起動」を「`review.sh start` で起動」と読み替え、Dev と QA は cost でも並べて起動してよい。プロンプト・修正ループ・指摘の渡し方・`uncertainty_verdicts` の扱いは Claude のときと同じ。

**レビュー指摘の渡し方（全レビュー共通）:**

レビュアーの最終回答の `findings` は、**要約・言い換え・取捨選択をせずに JSON のまま**修正担当に渡す。オーケストレーターが「主な指摘は 3 点」とまとめると、残りの指摘が修正されないまま再レビューに回り、往復が増える（実戦で指摘の一部だけが転送され、人間に指摘された）。

```
以下はレビュアーの指摘（JSON そのまま）です。severity が blocker / major のものはすべて直してください。minor は参考です（直さなくてよい）。
直したら、指摘ごとに「どう直したか / 直さなかった理由」を完了 JSON の result.review_responses に書いてください。
<findings の JSON 全文>
```

再レビューでは前回の `findings` と修正担当の `review_responses` をレビュアーに渡し、前回の指摘が 1 件ずつ解消したかを確認させる。

**再レビューの範囲（往復を減らす）:** 各レビューを起動するときに対象 worktree の `git rev-parse HEAD` を控えておき、再レビューではその値を `{PREV_REVIEWED_COMMIT}` としてプロンプト末尾に付ける。再レビューで見るのは次の 2 つだけ：
1. 前回の blocker / major が解消したか
2. `git diff {PREV_REVIEWED_COMMIT}..HEAD`（修正で変わった行）に新しい問題が無いか

前回のレビューで見えていたのに挙げなかった、修正で変わっていない箇所の問題は、再レビューでは minor として記録するだけにする（差し戻さない）。ただしセキュリティの blocker（認証・認可・インジェクション・秘密情報）だけは例外として挙げてよい。2026-10 の sandbox では、2 回目以降の差し戻しが「前回の修正で入った新しい問題」か「前回の指摘の直し残し」でほぼ占められていた。初回で全部挙げ、2 回目以降は差分だけを見れば、どちらも 1 往復で片付く。

**上限（3 回）を超えたとき:** 3 回目の修正の後も blocker / major が残ったら、残りの `findings` を JSON のまま AskUserQuestion で人間に出す（「この指摘を残したまま PR にする / もう 1 回直させる / 自分で直す」）。上限まで回るのは、レビュアーと implementer の判断が食い違っているときがほとんどで、同じやり取りを続けても収まらない。

**implementer の不確実点:** 対応する implementer の完了 JSON の `uncertainty_points` を、レビュアーのプロンプト末尾に JSON のまま付ける。レビュアーは 1 件ずつ `resolved`（コード・仕様書から妥当と判断できる）/ `needs_human`（要件の解釈が要る）を最終回答の `uncertainty_verdicts` で返す。`needs_human` が 1 件でもあるときだけ AskUserQuestion で人間に確かめる。

**グループの範囲で直せない指摘（人間に聞かない）:** レビュー指摘の扱いを人間に聞くのは、上の `needs_human` があるときだけ。次のものは決めた方針で処理して先へ進む（2026-10 の API sandbox の検証で、既存コードの規約違反と仕様の書き漏れを人間に聞いて implementation が止まった）。

| 指摘の種類 | 処理 |
|---|---|
| このグループの差分に無い既存コードの問題（既存ファイルの規約違反など。レビュアーは `existing/` 付きの rule で返す）。同じ書き方を新しいコードが真似ているだけのものも含む | 直さない。`review-findings-backlog.md` に「既存コード」と書いて記録し、承認扱いにする。新しいコードだけ規約に合わせられるなら、それは直す |
| 規約そのものを変える話（規約を緩める・新しい規約を作る） | 直さない。backlog に記録し、compliance の報告で推奨とあわせて出す |
| 仕様（テスト定義書）に無い振る舞いのテストが欲しい（設計凍結後の仕様の書き漏れ） | minor なら backlog に記録して進む。major なら Plan Repair（`blocker_type: "plan_repair_needed"`）に回す |
| `spec_cache.md` などの内部資料が古い | その場で直す（人間に聞かない） |

**レビュー結果の保存:** レビュアーの最終回答 JSON は受け取ったらすぐ `doc/process/reviews/group-{N}-{dev|qa}-{infra|app}-r{回数}.json` に Write する（codex のときは `review.sh wait` がここに書くので Write し直さない。メインの作業ディレクトリ。STEP H の集約は会話の記憶ではなくこのファイルから行う。セッションをまたいでも rule 名が残る）。

#### Dev (Infra) レビュー（Infra / Cross グループ）

Agent を起動（同期実行、`run_in_background=false`, `model="opus"`）。現行の Agent ツールにはツール制限パラメータが無いため、プロンプト冒頭に「**ファイルの編集・作成は禁止。Read / Grep / Bash（読み取り系）のみで確認し、指摘は最終回答で返す（ミューテーション結果の再現で一時的に壊したファイルは直後に `git checkout --` で戻す）**」を必ず含める：

`prompts/reviewer-dev.md` を Read し、プレースホルダー（worktree パス・仕様書パス・`{tech_stack}`・`{REVIEW_CHECKLIST}`・`{BASE_BRANCH}`（`implementation_progress.base_branch`）・Infra / App の別）を置換して渡す。レビュアーは `git diff {BASE_BRANCH}...HEAD` の変更行と、その呼び出し元・呼び出し先を中心に読む（初回も差分中心。再レビューはさらに前回からの差分に絞る）。プロンプトには「読み取り専用」「4 観点（保守性を含む）」「実行検証（3 条件）」「規約チェックリストの照合」「分岐→ユニットテストの要求」「ミューテーション結果の再現」「JSON 出力フォーマット」が含まれる。

`changes_requested` → `findings` のうち blocker / major を dev-implementer-infra-group-N に `SendMessage` で渡して修正（最大 3 回）。**渡し方は下の「レビュー指摘の渡し方」に従う。** minor は memory 蓄積用に記録するだけで修正ループに回さない。レビュアーは初回から Opus を使用するため、追加昇格は行わない。同じ `rule` が 3 回以上出たら [reference/agent-prompt-injection.md](reference/agent-prompt-injection.md) の手順で memory に保存する。

#### Dev (App) レビュー（App / Cross グループ）

同様に App Dev のシニアレビュアーエージェントを起動（`model="opus"`、編集禁止をプロンプトに明記、懐疑的レビュアー観点: セキュリティ・新人可読性・アーキテクチャ・保守性、`{REVIEW_CHECKLIST}` の照合、同じ JSON 出力）。
`changes_requested` → blocker / major を dev-implementer-app-group-N に渡して修正（最大 3 回）。

#### QA (Infra) レビュー（Infra / Cross グループ）

Infra QA のシニアレビュアーエージェントを起動（`model="opus"`、編集禁止をプロンプトに明記）。
QA レビュアーは「素朴な質問だけ」する観点を採用: コードの良し悪しではなく、理解できない点・テストの意図が不明な点のみ指摘する。読む範囲は `git diff {BASE_BRANCH}...HEAD` で QA が追加・変更したテストに絞る（既存のテストファイルは、変更された箇所と TC 網羅の照合に要る分だけ読む。差分の外の既存テストの問題は `existing/` 付きの minor にする）。`{REVIEW_CHECKLIST}` のうち `test/*`（[conventions/testing.md](reference/conventions/testing.md)）と各言語のテスト関連ルール（`*/table-driven` `*/parametrize` `*/test-*` 等）を照合する。特に `test/no-delete` / `test/no-skip` / `test/expected-from-impl` は blocker。`git diff` で削除行を確認する。**TC 網羅**（`test/tc-coverage`）: テスト定義書 frontmatter の `test_cases[].id` と QA worktree のテストの TC-ID を突き合わせ、欠けが無いか見る。**置き場**（`test/unit-vs-spec-split`）: QA が実装の内部関数を直接呼ぶユニットテストや `tests/Unit/**` を書いていないか見る（Dev の担当。同じパスでコンフリクトする）。**ミューテーション**（`test/mutation-checked`）: 統合検証 3.5 の QA の `result.mutation` を渡し、1〜5 件の記録があり全件 `killed: true` かを見る（QA worktree には実装が無いので再現はしない）。実装の分岐網羅は Dev reviewer の担当なので見なくてよい。出力は Dev レビューと同じ JSON。`changes_requested` → qa-implementer-infra-group-N に渡して修正（最大 3 回）。

#### QA (App) レビュー（App / Cross グループ）

App QA のシニアレビュアーエージェントを起動（`model="opus"`、編集禁止をプロンプトに明記、QA 素朴質問観点。implementer の `uncertainty_points` の判定も Dev レビューと同じく `uncertainty_verdicts` で返させる）。
`{REVIEW_CHECKLIST}` の `test/*` と言語のテスト関連ルールを照合（Infra QA と同じ基準）。出力は同じ JSON。`changes_requested` → qa-implementer-app-group-N に渡して修正（最大 3 回）。


---

### STEP E: PR作成

レビュー承認後、チーム種別に応じてブランチをpushしてPRを作成：

- **Infra**: `dev/infra-group-N`, `qa/infra-group-N` → 2PR作成、label=`infra`
- **App**: `dev/app-group-N`, `qa/app-group-N` → 2PR作成、label=`app`
- **Cross**: 4ブランチすべて → 4PR作成、Infraブランチには`infra,cross`、Appブランチには`app,cross`
- QA が part に分かれているときは、part 2 以降のブランチ（`qa/{team}-group-N-pK`）も 1 本ずつ PR にする（タイトルに `part K` を入れる）。マージは Dev → QA の各 part の順（[reference/merge-ops.md](reference/merge-ops.md)。どの part も Dev のマージ後に `update-branch` する）

PRタイトル例: 
- `feat(infra): グループ N Infra Dev タスク実装`
- `test(infra): グループ N Infra QA タスク実装`

**PR は Draft ではなく通常の OPEN（Ready for review）状態で作成する**（`gh pr create` に `--draft` を付けない）。dev-flow の実装フローは、レビュー（STEP D）を既にエージェントが完了させた状態で PR を作成するため、Draft にする理由が無い。プロジェクトの CLAUDE.md 等に「PR は Draft で作成する」旨の指示がある場合でも、「スキル経由で作成された PR はその限りではない」という例外が明記されていることが多いので、そちらを優先する（明記が無い場合は人間に確認する）。

PR のベースブランチは `implementation_progress.base_branch`（`--base` で明示する）。作成した PR 番号はすべて `state.json` の `implementation_progress.pr_numbers["group-N"]` に**配列**で記録する：

```bash
gh pr create --base "$BASE_BRANCH" --head dev/infra-group-N --title "..." --body "..." --label infra
# → 出力 URL の末尾番号を pr_numbers["group-N"] に append
```

---

### STEP F: worktreeクリーンアップ

PR作成後、worktreeを削除（ブランチは保持）。QA の part 2 以降の worktree（`worktree-qa-{team}-group-N-pK`）も同じく削除し、STEP H では part のブランチも消す：

**Infra / App:**
```bash
git worktree remove {MAIN_DIR}/../worktree-dev-{team}-group-N --force
git worktree remove {MAIN_DIR}/../worktree-qa-{team}-group-N --force
```

**Cross:**
```bash
git worktree remove {MAIN_DIR}/../worktree-dev-infra-group-N --force
git worktree remove {MAIN_DIR}/../worktree-qa-infra-group-N --force
git worktree remove {MAIN_DIR}/../worktree-dev-app-group-N --force
git worktree remove {MAIN_DIR}/../worktree-qa-app-group-N --force
```

---

### STEP G: ドキュメント誤りの集約とマージ待機

**doc_issues の集約（グループ完了後）:**

各エージェントの完了 JSON に `doc_issues` フィールドが含まれている場合、内容を `doc/process/doc_issues.md` に追記します（`| # | グループ | doc | ref_id | 内容 | 修正案 | 実装の現状 |`）。**グループの完了ごとには人間に聞かない。** 全グループの完了後（「全グループ完了後」の手順 0 の前）に、たまった分を 1 回の AskUserQuestion にまとめて出します（1 回に 4 問まで。多ければ関連するものを 1 問に束ねる）。2026-10 の notify-hub ではグループの完了ごとに聞いていたため、そのたびにオーケストレーターが止まり、レビュー待ちや次のグループの起動も止まっていた。

```json
{
  "doc_issues": [
    {
      "doc": "doc/api-spec/auth.md",
      "ref_id": "API-001",
      "issue": "request schema の email フィールドが optional だが要件 REQ-001 では必須",
      "suggested_fix": "required: [email, password] に変更"
    }
  ]
}
```

ただし、次のものはまとめずにその場で聞く（待つと後のグループのやり直しが増える）：
- そのグループのレビューやテストが、その doc_issue の答え無しには進められない
- 後続のグループ（`depends_on` でつながるもの）が、同じ箇所を前提に実装する

人間の判断：
- 「ドキュメントを修正する（doc-fix ブランチ）」→ doc-fix フローを実行
- 「実装側で対応する」→ 全グループ完了後なら Plan Repair（`plan_repair_needed`）で修正タスクを 1 グループ足す。グループの途中なら Dev エージェントに修正を依頼
- 「無視する」→ そのまま続行

**doc-fix フロー:**

1. `doc-fix/group-N-{issue-slug}` ブランチを作成
2. 該当ドキュメントを Edit ツールで修正
3. コミット: `docs: ドキュメント誤り修正 - {issue概要}`
4. main ブランチへ PR を作成して人間にマージを依頼
5. マージ後、実装 worktree で `git merge main` して最新ドキュメントを取り込む

**自動マージ試行（非ブロッキング）:**

**人間による**マージは待たない。CI の完了は待つ: `gh pr merge` が CI 未完了で deny されたら、`timeout 900 gh pr checks <N> --watch --fail-fast` で完了を待ってから 1 回だけ再試行する（`sleep` のポーリングではない。待たずに止まると、グループごとに人間の再開が要る）。まず hook の有無を確認する：

```bash
jq -e '[.. | strings | select(test("pr-merge-guard"))] | length > 0' ~/.claude/settings.json >/dev/null 2>&1 && echo enabled || echo disabled
```

- `disabled` → `gh pr merge` を発行せず、PR URL を人間に提示して stage-implementation-agent を終了する（マージ後に `/dev-flow` で再入）
- `enabled` → グループの各 PR に対して 1 コマンドずつ `gh pr merge <N> --merge --delete-branch` を実行する。hook `pr-merge-guard.sh` が自動マージ条件（ベースが `feature/*` かつ `base_branch` と一致・CI 全通過・コンフリクトなし・DB 破壊的変更なし）を検証し、満たさなければ deny される

**マージ運用の詳細**（Dev → QA の順序と `update-branch`、`mergeable=UNKNOWN` の待ち方、コマンドを連結しない、deny 理由の分類表と PR コメントでの明記）は [reference/merge-ops.md](reference/merge-ops.md) を Read して従う。

---

### STEP H: マージ後クリーンアップ

グループの全 PR が `MERGED` であることを `gh pr view <N> --json state` で確認した後（自動マージ直後、または再開処理での取り込み時）：

1. ローカル・リモートブランチを削除：
   - Infra: `dev/infra-group-N`, `qa/infra-group-N`
   - App: `dev/app-group-N`, `qa/app-group-N`
   - Cross: 上記4ブランチすべて
1.5. **実バージョンの確認（基盤グループのみ）**: 「実バージョンの書き戻し」タスクを含むグループなら、マージ後の `state.json.tech_stack.language_version` / `framework_version` が lock ファイルと一致しているか `jq` で確認する。タスクが書き戻していなければオーケストレーターが lock から読んで `state.json` だけ更新する（要件定義書は人間確認が要るので、compliance の乖離として残す）
2. **レビュー findings の集約**: `doc/process/reviews/group-{N}-*.json`（STEP D で保存したもの）を Read し、このグループの全レビュー（Dev / QA）の `findings` のうち、`rule` が `review/*`（規約ファイルに無かった指摘）で、かつプロジェクト固有でない汎用的なもの（例: `role="button"` の Space キー未対応、`aria-live` の常時マウント、`onClick={async}` の floating promise、`{n && <X />}` の 0 描画）を `doc/process/review-findings-backlog.md` に追記する（`| グループ | rule | severity | 内容 | 該当ファイル | 昇格先候補（react.md / laravel.md / testing.md 等） |` の表。同じ内容が既にあれば行を足さず「回数」列を増やす）。memory 保存の条件（同一 rule 3 回）に届かない minor / major の指摘が次のプロジェクトで消えないようにするため。compliance の完了レポートで「規約ファイルへの昇格候補」として人間に提示する
3. `${CLAUDE_SKILL_DIR}/../dev-flow/hooks/mark-group-done.sh N <PR番号...>` を実行する（1 回の Bash で）。チェックリストのグループ N（全一覧セクションの同一タスクも）を `[x]` にし、`state.json` の `completed_groups` / `active_worktrees` / `pr_numbers` を更新して 1 コミットする。冪等なので再開時に再実行してよい。hook 未導入環境（スクリプトが無い）では同じ内容を手で行う：チェックリストの `[x]` 化 → `implementation_progress` の更新 → 2 ファイルを 1 コミット
4. このグループの Dev / QA implementer と Claude のレビュアーを `TaskStop` で閉じる（ペインを空けて、待っているグループを起動できるようにする）

---

## 全グループ完了後

すべてのグループ完了後：

**最初に、たまった doc_issues をまとめて聞く**: `doc/process/doc_issues.md` に未判断のものがあれば、STEP G の「人間の判断」の 3 択で 1 回の AskUserQuestion にまとめて出す。「ドキュメントを修正する」はベースブランチに直接 `docs:` コミットしてよい（全グループがマージ済みなので doc-fix ブランチは要らない）。「実装側で対応する」が 1 件でもあれば Plan Repair に回し、追加グループが終わってから次へ進む

0. **リモートの実際の状態を確認する**（state.json を更新する前に。hook 導入環境では、これをしないと `state-sync.sh` が test への移行を拒否する）：
   ```bash
   git switch {base_branch} && git pull --ff-only
   ${CLAUDE_SKILL_DIR}/../dev-flow/hooks/verify-remote-state.sh --expect-merged   # PR 番号は state.json の pr_numbers から自動で取る
   ```
   `summary: NG 0` 以外なら test に進まない。NG の行（未マージ・CI 失敗・CI 未完了・ブランチの遅れ）を解消してからもう一度実行する。人間への完了報告にはこの出力をそのまま貼る
1. `doc/process/state.json` を更新：
   - `next_stage` を `"test"` に変更
   - `base_branch` に `implementation_progress.base_branch` を写す（test ステージが origin と同期するのに使う）
   - `implementation_progress` を削除
   - **`mode == "incremental"` の場合のみ**：上書きする前の `baseline_commit` を `diff_base_commit` に写し（compliance が今回の run の差分を取るのに使う）、`baseline_commit` を `git rev-parse HEAD`（ベースブランチに全 PR がマージされた後の最新コミット）で上書き。これにより、次回 `incremental` 実行時の差分基点が今回マージ完了時点に進む
2. 人間に「implementation 完了。次は `/dev-flow` を実行して test（テスト実行）に進んでください」と通知（0 の出力を添える）

`baseline_commit` 更新の責任分担詳細は `${CLAUDE_SKILL_DIR}/../dev-flow/reference/state-schema.md` の「baseline_commit のライフサイクル」を参照。

---

## エラーハンドリング

| 状況 | 対応 |
|---|---|
| worktree作成失敗 | 既存worktreeをクリーンアップ後に再試行 |
| push失敗 | 人間に報告して解消後に再push |
| lint エラー解消不可 | 人間に報告 |
| ブロッカー発生 | エージェント停止して人間に判断を仰ぐ |

### state.json とリモート真実の乖離からの復旧

worktree 作成失敗・ブランチ衝突・state.json 破損時は GitHub 側のマージ状態を真実として復旧する。手順は [reference/recovery.md](reference/recovery.md) を Read すること。
