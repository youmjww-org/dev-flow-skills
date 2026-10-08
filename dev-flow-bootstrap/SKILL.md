---
name: dev-flow-bootstrap
description: AI駆動開発フローの bootstrap ステージ（0: 既存プロジェクトの導入）。ドキュメントの無い既存コードから技術スタック・コード棚卸し・as-is 要件定義書（REQ-NNN）・テスト定義書（既存テストを TC-NNN に対応付け）・API 仕様書・インフラ仕様書・カバレッジ行列を逆生成し、`doc/process/state.json` を作って以降の change / fix / refactor が差分で動ける状態にします。既存プロジェクトで最初の 1 回だけ、`/dev-flow --bootstrap` または dev-flow の STEP 1.5 から起動します。
model: opus
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---

# Stage 0 bootstrap: 既存コードからの as-is ドキュメント生成

## 目的

dev-flow の `change` / `fix` / `refactor` は「REQ / TC / API の ID が振られたドキュメントがあり、`baseline_commit` からの差分で影響範囲を出せる」ことを前提にしている。bootstrap はその前提を既存プロジェクトに作る**一度きりの導入ステージ**で、以下を生成する：

| 生成物 | 内容 |
|---|---|
| `doc/process/inventory.md` | コード棚卸し（モジュール・エントリポイント・ルート・DB・既存テスト・IaC） |
| `doc/requirements/as-is-*.md` | コードが**現に満たしている**振る舞いを REQ-NNN で記述した as-is 要件定義書 |
| `doc/test-spec/as-is.md` | 既存テストを TC-NNN に対応付けたテスト定義書（`implemented_by` で実テストを指す） |
| `doc/api-spec/as-is.md` | ルート定義から逆生成した OpenAPI（is_api のとき） |
| `doc/infra-spec/as-is.md` | IaC から逆生成したインフラ仕様書（is_infra のとき） |
| `doc/process/coverage_matrix.md` | REQ × TC × API。**テストの無い REQ** が一覧になる |
| `doc/process/state.json` | `next_stage: "completed"` の状態で作成。以降の run が `tech_stack` 等を引き継ぐ |

**as-is の原則**: 「あるべき姿」ではなく「コードが今どう振る舞うか」を書く。推測した箇所は `confidence: low` を付けて人間に確認を回す。曖昧表現リントは適用しない（as-is 文書は仕様ではなく観測記録）。

## 入力

- 作業ディレクトリ（git リポジトリであること）
- `doc/process/state.json` は**存在しない**か `next_stage: "completed"` であること（進行中 run があれば中断して人間に確認）
- 引数 TASK があれば「対象範囲のヒント」として扱う（例: `--bootstrap "認証まわりだけ"`）

## STEP 1: 対象範囲と技術スタックの確定

**1a. 規模の確認**

```bash
git ls-files | grep -cE '\.(go|py|ts|tsx|js|jsx|rb|java|rs|kt|swift|c|cpp|cs)$'
git ls-files | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -20
```

実装ファイルが **300 を超える**場合は AskUserQuestion で対象ディレクトリを絞らせる（全体 / 上位ディレクトリの複数選択）。絞った範囲は `state.json.bootstrap_scope` に記録し、以降の STEP はその範囲だけを対象にする。

**1b. 技術スタックの検出**

`go.mod` / `package.json` / `pyproject.toml` / `requirements.txt` / `Gemfile` / `pom.xml` / `Cargo.toml` / `*.tf` / `Dockerfile` / `.github/workflows` / lint・formatter 設定を Bash で探し、次を提案する：

```json
{
  "tech_stack": { "language": "", "language_version": "", "framework": "", "framework_version": "", "test_framework": "", "db": "", "linter": "", "formatter": "", "e2e_framework": null },
  "is_api": true, "is_gui": false, "is_infra": false, "is_e2e": false
}
```

`language_version` / `framework_version` はマニフェストの値（`go.mod` の `go 1.23`、`package.json` の `dependencies.next`、`composer.json` の `require.laravel/framework`、`pyproject.toml` の `requires-python`）から取る（`dev-flow-implementation/reference/conventions/version-check.md` の「バージョン検出」表）。AskUserQuestion で提案を提示して確定する（間違いは人間に直させる）。

## STEP 2: コード棚卸し（inventory）

`prompts/inventory.md` を Read し、プレースホルダー（`{SCOPE}`, `{tech_stack}`）を置換して Agent を起動（`name="bootstrap-inventory"`, `run_in_background=false`, `model` は quality: `"opus"` / cost: `"sonnet"`。`dev-flow/reference/profiles.md`）。

出力 `doc/process/inventory.md` は機械可読な表で、以降の writer が全員これを入力にする。inventory が空同然（エントリポイントもテストも見つからない）なら人間に報告して中断する。

## STEP 3: as-is 要件定義書の生成

**3a. 既存の要件定義書がある場合**

`doc/requirements/*.md` に手書きの文書がある場合は AskUserQuestion で「ID を付与して再利用 / 破棄して as-is を生成」を選ばせる。再利用なら `prompts/assign-ids.md` で既存文書に frontmatter と REQ-NNN を付与するだけにする。

**3b. 生成**

`prompts/as-is-requirements.md` を Read し、プレースホルダー（`{INVENTORY_PATH}`, `{SCOPE}`, `{tech_stack}`）を置換して Agent を起動（`name="bootstrap-requirements-writer"`, `run_in_background=false`, `model="opus"`）。

- 機能領域ごとに `doc/requirements/as-is-{領域}.md` を分ける（1 ファイル 30 REQ を目安）
- 各 REQ に `source`（根拠のファイル・関数）と `confidence`（high / medium / low）を付ける
- writer の最終回答から `confidence: low` の REQ 一覧を受け取り、STEP 5 の人間レビューに回す

## STEP 4: 仕様書の逆生成（並列）

以下を **同一ターンで同時に**起動する（`run_in_background=true`, `model` は quality: `"opus"` / cost: `"sonnet"`）。各プロンプトを Read してプレースホルダー（`{INVENTORY_PATH}`, `{REQUIREMENTS_PATHS}`, `{tech_stack}`, 出力パス）を置換する。Agent Teams は使わない。

| name | プロンプト | 出力 | 起動条件 |
|---|---|---|---|
| `bootstrap-test-spec-writer` | `prompts/as-is-test-spec.md` | `doc/test-spec/as-is.md` | 常に |
| `bootstrap-api-spec-writer` | `prompts/as-is-api-spec.md` | `doc/api-spec/as-is.md` | is_api |
| `bootstrap-infra-spec-writer` | `prompts/as-is-infra-spec.md` | `doc/infra-spec/as-is.md` | is_infra |

完了通知（最終回答）を全部受け取ってから STEP 5 へ。届かない場合は出力ファイルの存在を確認し、無ければ同じ `name` で再起動する。

**test-spec の要点**: 既存テスト 1 つを TC 1 つに対応付け、frontmatter の `test_cases[].implemented_by` に `path::関数名` を入れる。compliance の機械的検証はこれを使って「TC が実装されているか」を判定する。既存テストが無い REQ には TC を**作らない**（as-is に無いものを捏造しない）。

## STEP 4.5: プロジェクト規約の草案

`doc/conventions.md` が無ければ、inventory と実コードから**観測できた**規約を草案として書く（`prompts/as-is-conventions.md`、`model` は quality: `"opus"` / cost: `"sonnet"`、同期）。命名パターン・ディレクトリ構成・エラー処理の流儀・テストの書き方・使っているリンタ設定を「現状こうなっている」として列挙し、`dev-flow-implementation/reference/conventions/<language>.md` と食い違う点は「言語標準と異なる（意図的か要確認）」と印を付ける。人間が STEP 5 で確認し、不要なら削除してよい。

## STEP 5: カバレッジ行列と人間レビュー

**5a. カバレッジ行列**

`${CLAUDE_SKILL_DIR}/../dev-flow-consistency/SKILL.md` の STEP 3 と同じ形式で `doc/process/coverage_matrix.md` を生成する。bootstrap では **未カバー REQ（テストが無い振る舞い）が主役**なので、行列の先頭に「未カバー REQ 一覧」を置く。

**5b. 人間レビュー**

AskUserQuestion で以下をまとめて提示する：

- 生成した文書の一覧とサイズ（`doc/conventions.md` 草案を含む。言語標準と異なる点の一覧）
- `confidence: low` の REQ 一覧（推測した振る舞い）
- 未カバー REQ の件数と上位 10 件
- 選択肢: 「このまま確定」/「low の REQ を一緒に確認する」/「範囲を変えてやり直す」

「一緒に確認する」なら low の REQ を 1 件ずつ AskUserQuestion で「正しい / 修正する / 削除する」を聞いて反映する（件数が 20 を超える場合は先頭 20 件だけ聞き、残りは文書内の `confidence: low` を残したまま確定してよい旨を伝える）。

## STEP 6: state.json の作成と確定コミット

`doc/process/state.json` を作成する（既存があれば `tech_stack` 等を上書き）：

```json
{
  "next_stage": "completed",
  "kind": "bootstrap",
  "task": "{TASK または \"as-is ドキュメント生成\"}",
  "mode": "incremental",
  "baseline_commit": "{git rev-parse HEAD}",
  "bootstrap_scope": ["{絞った場合のディレクトリ}"],
  "requirements_paths": ["doc/requirements/as-is-xxx.md", "..."],
  "test_spec_path": "doc/test-spec/as-is.md",
  "api_spec_path": "doc/api-spec/as-is.md または null",
  "infra_spec_path": "doc/infra-spec/as-is.md または null",
  "mock_path": null,
  "tech_stack": { "...": "STEP 1 で確定した値" },
  "is_gui": false, "is_api": true, "is_infra": false, "is_e2e": false,
  "agent_hierarchy": { "max_depth": 4, "current_depth": 1, "stack": ["dev-flow"] },
  "harness": { "skill_versions": { "dev-flow": "{git -C ${CLAUDE_SKILL_DIR}/../dev-flow rev-parse --short HEAD}" }, "started_at": "{ISO8601}", "stage_history": [] }
}
```

`mock_path` は as-is では作らない（UI は実物があるため。GUI の `change` で初めて生成する）。

```bash
git add doc/
git commit -m "docs: bootstrap で as-is ドキュメントを生成する"
```

## 出力

人間に以下を伝えて終了する：

```
bootstrap 完了。
- 要件定義書: N ファイル / REQ M 件（confidence low: K 件）
- テスト定義書: TC X 件（既存テスト Y 件に対応付け）
- 未カバー REQ: Z 件（doc/process/coverage_matrix.md 参照）
次の変更は以下で始められます：
  /dev-flow --kind=change "..."   要件を変える
  /dev-flow --kind=fix "..."      不具合を直す
  /dev-flow --kind=refactor "..." 挙動を変えず改善する
未カバー REQ にテストを足したい場合は /dev-flow --kind=fix "REQ-0NN の再現テストを追加" が使えます。
```
