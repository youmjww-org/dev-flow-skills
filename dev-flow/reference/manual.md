# dev-flow マニュアル

`/dev-flow --man` で全体、`/dev-flow --man <topic>` で 1 節だけ表示する。各節の見出し末尾の英字（例: `profiles`）がトピック名。設計の背景は dev-flow-skills の README.md、各ステージの手順は `~/.claude/skills/dev-flow-<stage>/SKILL.md` にある。

## 目次

- オプション（`--man options`）
- 変更種別（`--man kinds`）
- 実行プロファイル（`--man profiles`）
- ステージ（`--man stages`）
- 状態ファイル（`--man state`）
- hook（`--man hooks`）
- PR とマージ（`--man merge`）
- 生成物（`--man outputs`）
- トラブルシューティング（`--man troubleshooting`）

## オプション {#options}

| オプション | 値 | 説明 |
|---|---|---|
| （引数なし） | — | `doc/process/state.json` の `next_stage` から続きを実行する |
| `"やりたいこと"` | 自由文 | `--` で始まらない部分がタスク説明（`state.json.task`） |
| `--kind=` | `feature` / `change` / `fix` / `refactor` | 変更種別。通るステージが変わる（`--man kinds`）。省略時、実装コードがあれば起動時に選択肢が出る。実装コードが無ければ常に `feature` |
| `--profile=` | `quality`（既定）/ `cost` | 実行プロファイル（`--man profiles`）。run の途中でも切り替えられる |
| `--from=` | `requirements` / `spec` / `consistency` / `implementation` / `test` / `compliance` | そのステージから再開する。`requirements` 以外は state.json が必要。`plan_repair` は指定できない |
| `--bootstrap` | — | 既存コードから as-is ドキュメントを逆生成する。ドキュメントの無い既存プロジェクトで最初に 1 回 |
| `--dry-run` | — | 何も書き換えず、実行計画と下流スキルの有無だけ表示する |
| `--help`, `-h` | — | 短い使い方を表示する |
| `--man` | `[topic]` または `=topic` | このマニュアルを表示する |

`--no-gui` / `--no-api` のようなプロジェクトタイプのフラグは無い。GUI / API / インフラ / E2E の有無は requirements の対話で決まる。

## 変更種別 {#kinds}

| kind | 用途 | 通るステージ | 最初のステージ |
|---|---|---|---|
| `feature` | 新機能 | requirements → spec → consistency → implementation → test → compliance | requirements |
| `change` | 既存機能の要件変更 | 全ステージ（requirements は修正モード、spec は差分更新、consistency は Impact Analysis、implementation は影響グループのみ） | requirements |
| `fix` | 不具合修正（要件は変えない） | spec（再現 TC 追加のみ）→ consistency（lite）→ implementation（1 グループ）→ test → compliance | spec |
| `refactor` | 挙動を変えない内部改善 | consistency（lite）→ implementation（1 グループ）→ test → compliance（全 ID で挙動不変を確認） | consistency |

人間の確認待ちで止まるのは requirements の後だけ。`fix` / `refactor` は要件定義書と `tech_stack` が既にあることが前提（無ければ先に `--bootstrap`）。

## 実行プロファイル {#profiles}

| | quality（既定） | cost（`--profile=cost`） |
|---|---|---|
| 向いている場面 | 大きめの機能、正確さが大事な変更 | 小さな fix / refactor、試し打ち |
| ステージの実行 | オーケストレーター（Opus）が各ステージの SKILL.md を全文読んで直接実行 | ステージごとに `stage-*-agent` を起動（spec / consistency / implementation / test は Haiku） |
| writer・implementer | Opus。昇格なし | Sonnet から始め、設計レベルの指摘で Opus に昇格 |
| レビュー | Opus。Dev と QA を同時に | Opus。Dev → QA の順 |
| test | Opus で最大 5 回 | Haiku 2 回 → Sonnet 3 回 |
| ハング検知 | 起動 5 分で確認、生存確認 3 分で判定 | モデル別（haiku 5 分 / sonnet 15 分 / opus 30 分） |

選んだプロファイルは `state.json.profile` に保存され、再開時も引き継ぐ。役割ごとの正本は `~/.claude/skills/dev-flow/reference/profiles.md`。

## ステージ {#stages}

| # | stage | やること | 終わったら |
|---|---|---|---|
| 0 | bootstrap | 既存コードの棚卸し、as-is の要件定義書・テスト定義書・API / インフラ仕様書・カバレッジ行列を生成 | 人間が確認してから `/dev-flow --kind=...` |
| 1 | requirements | 対話で要件を深掘りし、REQ-NNN 付きの要件定義書と用語集を作る | **人間が確認**してから `/dev-flow` |
| 2 | spec | テスト定義書（TC）・API 仕様書・インフラ仕様書・UI モックを並列で作り、reviewer が確認 | 自動で次へ |
| 3 | consistency | ID の整合性、カバレッジ行列、依存関係付きのタスク分解（`task_checklist.md`）、設計凍結 | 自動で次へ |
| — | plan_repair | implementation 中に計画誤りが見つかったとき、未着手グループのチェックリストだけ作り直す（最大 3 回） | implementation に戻る |
| 4 | implementation | グループごとに worktree を作り、Dev / QA が並列で実装 → 統合検証 → レビュー → PR → 条件付き自動マージ | PR が人間マージ待ちならそこで止まる。マージ後に `/dev-flow` |
| 5 | test | origin の最新に揃えて全テストを実行し、プロダクションコードだけを直して全通過させる（テストは変更禁止） | 自動で次へ |
| 6 | compliance | カバレッジ行列で要件・TC・実装の対応を機械検証し、完了報告 | `next_stage = completed` |

## 状態ファイル {#state}

- 場所: `doc/process/state.json`。compliance の後も消さない（`next_stage: "completed"` で残し、`tech_stack` などを次の run が使う）
- 主なフィールド: `next_stage`（次に実行するステージ）/ `kind` / `task` / `profile` / `mode`（`full` = 実装コード無し、`incremental` = あり）/ `baseline_commit` / `tech_stack` / `implementation_progress`（implementation 中だけ）
- 進捗の見える化: `doc/process/task_checklist.md`（ステージ進捗とグループのタスク）、`doc/process/flow.log`（hook が記録するイベント）
- 完全なスキーマ: `~/.claude/skills/dev-flow/reference/state-schema.md`

## hook {#hooks}

`setup.sh` が `~/.claude/settings.json` に登録する。プロンプトでは緩められない機械的なチェック。

| いつ | hook | やること |
|---|---|---|
| `stage-*-agent` 起動前（cost のみ） | `pre-agent-check.sh` | プランモード・下流スキルの欠損・state.json 不正・階層の深さ・同じステージの繰り返しを止める |
| state.json 書き込み後 | `state-sync.sh` | 値域の検証、チェックリストの同期、flow.log 記録。implementation → test は remote の確認が済んでいなければ差し戻す |
| 仕様書・チェックリスト書き込み後 | `doc-validate.sh` | frontmatter の ID・`covers`・`implemented_by` などを検証 |
| テストコード書き込み後 | `test-lint.sh` | skip・assert なし・空テスト・エラー握りつぶしを差し戻す |
| test ステージでの書き込み前 | `test-stage-guard.sh` | テストコードとテスト定義書の変更を拒否 |
| `gh pr merge` の前 | `pr-merge-guard.sh` | 自動マージ条件を検証（`--man merge`） |
| セッション開始・応答完了 | `session-start.sh` / `stop-summary.sh` | 進行中の run と次のアクションを表示 |

hook が入っていない（`setup.sh --no-hooks`）環境では自動マージをしない。詳細は `~/.claude/skills/dev-flow/hooks/README.md`。

## PR とマージ {#merge}

- ブランチ: `main ← develop ← feature/xxx ← dev/app-group-N, qa/app-group-N, ...`
- implementation は各グループの作業ブランチから `feature/xxx` へ PR を作り、`gh pr merge <N> --merge` を試す
- 自動マージされる条件: ベースが `feature/*` で state.json の `base_branch` と一致、CI が全部成功（CI の無い PR は対象外）、コンフリクトなし、テストの削除・スキップなし、DB の破壊的変更なし、`--merge` 方式
- 条件を満たさない PR は人間がレビュー・マージする。オーケストレーターは待たずに止まるので、マージ後に `/dev-flow` で続ける
- `feature/xxx → develop`、`develop → main` は常に人間がマージする

## 生成物 {#outputs}

| パス | 中身 |
|---|---|
| `doc/requirements/*.md`、`_glossary.md` | 要件定義書（REQ-NNN）と用語集 |
| `doc/test-spec/` | テスト定義書（TC-NNN、Gherkin） |
| `doc/api-spec/` | API 仕様書（API-NNN、OpenAPI 3.1.0） |
| `doc/infra-spec/` | インフラ仕様書（INFRA-NNN） |
| `doc/mock/*.html` | UI モック |
| `doc/process/state.json` | フローの状態 |
| `doc/process/task_checklist.md` | ステージ進捗とタスク（依存関係付き） |
| `doc/process/coverage_matrix.md` | REQ × TC × API のカバレッジ行列 |
| `doc/process/flow.log` | hook のイベントログ |
| `doc/process/plan_repair_log.md` | Plan Repair の履歴 |
| `doc/process/escalation_*.md` | 人間への報告（発生時のみ） |

## トラブルシューティング {#troubleshooting}

| 症状 | 対処 |
|---|---|
| 進行中の run を捨てて最初からやり直したい | `jq '.next_stage = "completed" \| del(.implementation_progress)' doc/process/state.json > /tmp/s && mv /tmp/s doc/process/state.json` のあと `/dev-flow --kind=... "..."` |
| 特定のステージからやり直したい | `/dev-flow --from=<stage>`（`--from` を `implementation` より前にすると、worktree と未マージ PR が残る旨の確認が出る） |
| プランモードで止まった | Shift+Tab でプランモードを抜けてから再実行する（推奨は acceptEdits 以上） |
| サブエージェントが何もせず止まる | ハング検知のあと fork で起動し直す（`~/.claude/skills/dev-flow/reference/agent-hang-recovery.md`） |
| `gh pr merge` が拒否された | 同じコマンドを再試行しない。拒否理由が CI 未完了なら完了を待って 1 回だけ、それ以外は人間がマージする |
| Plan Repair が繰り返し起きる | `doc/process/plan_repair_log.md` を見てタスク分類を確認する。3 回で人間に上がるので、`task_checklist.md` を直してから `/dev-flow` |
| state.json が壊れた | `~/.claude/skills/dev-flow/reference/error-handling.md` |
| ドキュメントの無い既存プロジェクト | `/dev-flow --bootstrap` を先に実行する |
