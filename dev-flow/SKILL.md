---
name: dev-flow
description: AI駆動開発フローのメインオーケストレーター。requirements → spec → consistency → implementation → test → compliance の 6 ステージを順次実行します。既定（quality プロファイル）は速さと正確さを優先し、`--profile=cost` でコスト重視の流れに切り替えます。新機能を要件定義から実装まで一気通貫で自動化したい時、または `doc/process/state.json` から既存フローを継続したい時に使用します。
model: opus
argument-hint: "[--kind=feature|change|fix|refactor] [--profile=quality|cost] [--from=stage] [--bootstrap] [--dry-run] [--help] [--man [topic]] タスク説明"
# allowed-tools はこのスキルを呼び出したターンの親セッションにだけ効く（サブエージェントは親のパーミッションモードを継承する）
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
# コミット・worktree・PR 作成・自動マージまで行う副作用の大きいワークフローなので、起動は人間の /dev-flow に限定する
disable-model-invocation: true
---

# 開発フローオーケストレーター

あなたは開発フローの**メインオーケストレーター**です。状態ファイルを管理し、各ステージのスキルを順次実行してフローを進めます。

## ヘルプ（最初に判定する）

下の「起動時コンテキスト › 引数」に `--help` / `-h` / `--man` が含まれていたら、**フローは実行しない**。他のオプションやタスク説明が一緒に書かれていても無視する。state.json・ファイルの書き換え、サブエージェントの起動、`profiles.md` の Read はしない。

| 引数 | 表示するもの |
|---|---|
| `--help` / `-h` | `~/.claude/skills/dev-flow/reference/help.md` を Read し、中身をそのままコードブロックで出す |
| `--man`（トピックなし） | `~/.claude/skills/dev-flow/reference/manual.md` を Read し、全文をそのまま出す |
| `--man <topic>` / `--man=<topic>` | `manual.md` の見出しに `{#<topic>}` が付いた節だけを出す（見出しの `{#…}` は表示から外す）。該当が無ければ「トピックが見つかりません」と有効なトピック（`options` `kinds` `profiles` `stages` `state` `hooks` `merge` `outputs` `troubleshooting`）を出す |

`--help` と `--man` の両方があれば `--man` を優先する。表示の最後に、「起動時コンテキスト › 現在の state.json」から現在の状態を 1 行添える（例:「現在: next_stage=implementation / kind=feature / profile=quality」、state.json が無ければ「現在: 進行中の run なし」）。要約や言い換えはしない。

## 実行プロファイル

| profile | 指定 | ステージの実行方法 | モデル |
|---|---|---|---|
| `quality`（既定） | 指定なし、または `--profile=quality` | オーケストレーター（このセッション）が各ステージの SKILL.md を全文読んで**直接実行**する | 末端の実行者（writer・implementer・レビュアー・test runner）も原則 opus。昇格ラダー無し。Dev / QA レビューは同時に起動 |
| `cost` | `--profile=cost` | ステージごとに `stage-*-agent` を起動して任せる（従来の流れ） | 中間管理は haiku、末端は sonnet から始めて失敗時に昇格 |

役割ごとのモデル・待ち時間・quality で直接実行するときの注意は `~/.claude/skills/dev-flow/reference/profiles.md` にまとめてある。**フロー開始時に必ず Read する**。下流スキル本文の `model="…"` は cost の値なので、quality では profiles.md の表の値に読み替える。

## ステージ一覧

| # | stage | 内容 | スキル |
|---|---|---|---|
| 1 | `requirements` | 要件定義（対話 → 要件定義書 → 人間確認ゲート） | `dev-flow-requirements` |
| 2 | `spec` | 仕様書生成（テスト定義書・API 仕様書・インフラ仕様書・モック → 人間レビュー） | `dev-flow-spec` |
| 3 | `consistency` | 整合性チェック（ID 整合性・カバレッジ行列・タスク分解・設計凍結） | `dev-flow-consistency` |
| — | `plan_repair` | 計画修正（implementation 内部で発動する consistency の縮小版） | `dev-flow-consistency` |
| 4 | `implementation` | 並列実装（worktree・Dev/QA・レビュー・PR） | `dev-flow-implementation` |
| 5 | `test` | テスト実行（quality: Opus / cost: Haiku → Sonnet 昇格） | `dev-flow-test` |
| 6 | `compliance` | 準拠チェック・完了報告 | `dev-flow-compliance` |
| 0 | `bootstrap` | 既存コードから as-is ドキュメントを逆生成する導入ステージ（既存プロジェクトで最初の 1 回だけ） | `dev-flow-bootstrap` |

ステージ名は `--from=` の値・`state.json.next_stage` の値・エージェント名（`stage-<stage>-agent`）・`task_checklist.md` の進捗行で共通に使う。番号は表示用。

## 変更種別（kind）と通るステージ

`state.json.kind` で、どのステージを通るかが決まる：

| kind | 用途 | requirements | spec | consistency | implementation | test | compliance |
|---|---|---|---|---|---|---|---|
| `feature` | 新機能（既定） | ● 新規作成 | ● 全文生成 | ● 全 STEP | ● | ● | ● 全 ID |
| `change` | 既存機能の要件変更 | ● 修正モード | ● 差分更新 | ● Impact Analysis | ● 影響グループのみ | ● | ● 変更 ID のみ |
| `fix` | 不具合修正（要件は変わらない） | — | ● 再現 TC 追加のみ | ● lite（1 グループ） | ● 1 グループ | ● | ● 追加 TC のみ |
| `refactor` | 挙動を変えない内部改善 | — | — | ● lite（1 グループ） | ● 1 グループ | ● | ● 全 ID（挙動不変の確認） |

最初に実行するステージ: `feature` / `change` → `requirements`、`fix` → `spec`、`refactor` → `consistency`。人間確認ゲートは requirements の後のみ（`fix` / `refactor` は最初のステージから自動で進む）。

`fix` / `refactor` は要件定義書・`tech_stack` が既に存在することが前提。`doc/process/state.json` が無い既存プロジェクトでは先に `bootstrap` を実行する。

---

## 状態管理

状態ファイル: `doc/process/state.json`

主要フィールド: `next_stage`（**次に実行する**ステージ名）/ `kind` / `task` / `profile`（`quality` or `cost`。無ければ `quality`）/ `mode`（full or incremental）/ `baseline_commit` / `tech_stack` / `implementation_progress`

state.json は **compliance 完了後も削除しない**（`next_stage: "completed"` のまま残す）。`tech_stack` / 各ドキュメントパス / `is_*` フラグ / `baseline_commit` はプロジェクトの永続情報で、次の `change` / `fix` / `refactor` がそのまま使う。新しい run を始めるときは `next_stage` / `kind` / `task` / `profile` / `harness.started_at` を書き換え、`implementation_progress` と `harness.stage_history` を初期化する。

旧スキーマ（`current_phase: "phase_2"` 等 = 完了フェーズ）の state.json を見つけたら、hook と同じ対応で `next_stage` に読み替えて書き直す: `phase_2→spec`, `phase_4→consistency`, `phase_4_5→implementation`, `phase_4_5_mini→plan_repair`, `phase_5→test`, `phase_6→compliance`。

スキーマ詳細・PR マージ待機ロジック・harness メタデータは必要時に Read すること:
`~/.claude/skills/dev-flow/reference/state-schema.md`

---

## Hook 連携

`setup.sh` が `~/.claude/settings.json` に登録する hooks（`~/.claude/skills/dev-flow/hooks/`）が、以下を**プロンプトの指示ではなく決定的に**実行する。hook が動作している環境では該当ステップの手動実施は不要（結果は `additionalContext` で通知される）。hook 未導入の環境（`setup.sh --no-hooks`）では各 STEP の記述どおり手動で行う。

| タイミング | hook | オーケストレーターへの影響 |
|---|---|---|
| `stage-*-agent` 起動前 | `pre-agent-check.sh` | 下流スキル欠損・state.json 不正・階層深さ超過は `deny`、ステージとエージェントの不一致・同一ステージ 5 回以上は `ask` で止まる。STEP 1.2 / 3.5 の検証を機械的に補完 |
| `state.json` 書き込み後 | `state-sync.sh` | JSON 不正・`next_stage` / `kind` の値域外なら exit 2 で差し戻し。implementation → test は、直前に `verify-remote-state.sh` が OK を記録していなければ exit 2。`task_checklist.md` の「ステージ進捗」を `next_stage` に同期（STEP 5-2 の自動化）。`flow.log` に遷移を記録 |
| `doc/{requirements,test-spec,api-spec,infra-spec}/*.md`・`task_checklist.md` 書き込み後 | `doc-validate.sh` | frontmatter の ID 形式・重複・`covers` の REQ 実在・`implemented_by` の関数実在・本文見出しの対応・`status` の値域・チェックリストの 6 行を検証。違反は exit 2 で差し戻す（writer は指摘どおり直して書き直す）。要件定義書の REQ がテスト定義書 / 仕様書のどの `covers` にも無ければ WARN |
| `escalation_*.md` 生成後 | `state-sync.sh` | `flow.log` に記録。`DEV_FLOW_SLACK_CHANNEL` 設定時は Slack 通知 |
| `stage-*-agent` 完了後 | `agent-complete.sh` | `flow.log` に完了・所要時間を記録。requirements 完了時は人間確認ゲートを念押し |
| Agent 起動直後（数秒以内の PostToolUse） | `agent-complete.sh` | `agent_spawned` として記録するだけ。「完了」は最終回答 / task notification で判断する（pane 型は起動直後に PostToolUse が返るため） |
| テストコード書き込み後 | `test-lint.sh` | skip・assert なし・空テスト・エラー握りつぶし・（シェル）同じ値どうしの比較は exit 2 で差し戻し。sleep / 現在時刻 / 乱数 / tautology、（シェル）`grep -c`・trap の無い復元・WARN だけの失敗・IPv4 限定の照合は WARN |
| test ステージでのテストファイル書き込み前 | `test-stage-guard.sh` | テストコード・テスト定義書への Write / Edit を `deny`（プロダクションコードだけ直す） |
| `gh pr merge` 実行前 | `pr-merge-guard.sh` | 自動マージ条件（ベースブランチ・CI・コンフリクト・DB 破壊的変更・テスト削除/スキップ・`--merge`）を検証し、満たさなければ `deny`。`main` / `develop` 向けは常に拒否 |
| セッション開始 / 応答完了 | `session-start.sh` / `stop-summary.sh` | 進行中フローの次ステージとアクションを表示。ブランチが origin より遅れていれば警告 |
| 状況報告・ステージ移行の前（hook ではなく Bash から呼ぶ） | `verify-remote-state.sh` | `git fetch` してブランチの ahead / behind、PR の state と CI の結果を 1 行ずつ OK / NG で出す。下の「状況報告のルール」を参照 |

`pre-agent-check.sh` と `agent-complete.sh` は `stage-*-agent` の起動時だけ動くので、ステージを直接実行する quality では動かない。その分の確認（プランモード・ループ検出・`harness.stage_history` の記録）は `reference/profiles.md` の「quality でオーケストレーターが直接実行するときの注意」に従って自分で行う。

hook からの `additionalContext` に「task_checklist.md のステージ進捗は自動同期済み」とあれば STEP 5-2 の Edit をスキップする。`deny` / `ask` された場合は理由を人間に伝え、勝手に回避策を取らない。

**`gh pr merge` が deny されたとき:** 同じコマンドを再試行しない（条件が変わらない限り結果は同じ）。deny 理由を読み、CI 未完了なら完了を待ってから 1 回だけ再試行、CI 失敗・コンフリクト・main 向けなど条件を満たせないものは「人間マージ待ち」として PR URL と deny 理由を人間に 1 回提示する。人間から「マージしてよい」と明示された場合も、hook の条件は緩まないので人間自身にマージしてもらう。

---

## 状況報告のルール

人間やエージェントに「完了」「パス」「CI 実行中」「マージ済み」「テストは存在する」と伝える前に、**その場で実際の状態を確認し、確認したコマンドの出力を報告に引用する**。記憶・推測・サブエージェントの自己申告だけで書かない。実戦で、すでに失敗していた CI を「まだ実行中」と報告した、origin より 44 コミット遅れたブランチで「全件パス」と報告した、存在しないテストを「全グループ分ある」と報告した、という誤りを人間が訂正した。

| 言いたいこと | 先に実行するもの |
|---|---|
| PR・CI の状態、ブランチの同期 | `~/.claude/skills/dev-flow/hooks/verify-remote-state.sh [--expect-merged] [PR番号...]`（出力の OK / NG 行を貼る） |
| CI が失敗した理由 | `gh run view <run-id> --log-failed`（コード起因か環境起因か分けて書く） |
| テストが通った | テストコマンドの実行結果の件数行（`Tests: 65 passed` 等）。ブランチが origin と同期していることを先に確認する |
| テスト・ファイルが存在する | `git ls-files <パス>` / `grep -rn 'TC-0NN'` の結果 |
| ステージ・グループが完了した | 上の確認と、`state.json` の該当フィールド（`jq`） |

- CI が未完了なら「未完了」と書き、結果を予想しない。待つ場合は `sleep` せず、次回 `/dev-flow` で再確認する
- サブエージェントのレビュー指摘を別のエージェントに渡すときは、要約せず JSON のまま全件渡す（`dev-flow-implementation/SKILL.md`「レビュー指摘の渡し方」）

---

## パーミッションモードの前提

サブエージェントは**親セッションのパーミッションモードを継承**する（Agent ツールの `mode` 引数は無視される）。そのため：

- **プランモード（読み取り専用）で `/dev-flow` を起動しない。** writer / implementer が書き込めずに止まる。hook 導入環境では `pre-agent-check.sh` が `permission_mode = "plan"` のとき `stage-*-agent` の起動を `deny` する。deny されたら「プランモードを抜けて（Shift+Tab）から再実行してください」と案内して終了する。quality は `stage-*-agent` を起動しないので hook では止まらない。プランモードだと分かった時点で同じ案内をして終了する
- 推奨は `acceptEdits` 以上。`default` でも動くが、各サブエージェントの Write / Bash がすべて親セッションの確認プロンプトに上がってくる
- 計画はプランモードではなく requirements / spec / consistency のドキュメントと人間確認ゲートが担う。プランモードを併用しない

---

## 起動時コンテキスト（自動注入）

以下はスキル起動時にシェルで評価され、結果がここに埋め込まれる。STEP 1.2 / STEP 2 はこの結果を読むだけでよく、改めて Bash で確認する必要はない。

### 引数

```
$ARGUMENTS
```

### 下流スキルの存在

```!
for f in requirements spec consistency implementation test compliance bootstrap; do
  p="$HOME/.claude/skills/dev-flow-$f/SKILL.md"
  [ -f "$p" ] && echo "OK      dev-flow-$f" || echo "MISSING dev-flow-$f"
done
```

### 現在の state.json

```!
if [ -f doc/process/state.json ]; then cat doc/process/state.json; else echo "(state.json なし)"; fi
```

### task_checklist.md のステージ進捗

```!
if [ -f doc/process/task_checklist.md ]; then sed -n '/^## ステージ進捗/,/^## /p' doc/process/task_checklist.md | grep -E '^- \[' || true; else echo "(task_checklist.md なし)"; fi
```

### 実装コードの有無（テスト系を除く）

```!
git ls-files 2>/dev/null | grep -vE '(^|/)(tests?|spec|__tests__)/' | grep -vE '\.(test|spec)\.(ts|tsx|js|jsx|py|rb)$' | grep -vE '_test\.(go|py|rb)$' | grep -cE '\.(go|py|ts|tsx|js|jsx|rb|java|rs|kt|swift|c|cpp|cs)$'; true
```

### hooks の登録状況

```!
jq -e '[.. | strings | select(test("dev-flow/hooks/"))] | length > 0' "$HOME/.claude/settings.json" >/dev/null 2>&1 && echo "hooks: enabled" || echo "hooks: disabled（setup.sh 未実行。各 STEP の検証を手動で行う）"
```

---

## フロー実行

### STEP 1: 引数の解析

上の「引数」を解析：

- **TASK**: `--` で始まらない部分
- **KIND**: `--kind=` の値（`feature` / `change` / `fix` / `refactor`）。未指定時は STEP 1.5 で決める
- **PROFILE**: `--profile=` の値（`quality` / `cost`）。未指定なら state.json の `profile`（進行中 run の再開時）、それも無ければ `quality`。上記以外の値は無効として有効値を提示する
- **BOOTSTRAP**: 引数に `"--bootstrap"` が含まれる場合は `true`。STEP 1.5 の判定を飛ばして `bootstrap` ステージを起動する
- **FROM**: `--from=` の値（指定時は state.json の `next_stage` にその値を書いてから開始する）
- **DRY_RUN**: 引数に `"--dry-run"` が含まれる場合は `true`。サブエージェントを起動せずフロー構成を検証して終了する
- `--help` / `-h` / `--man` はここに来る前に「ヘルプ」で処理済み（フローは実行しない）

`--from` の有効値はステージ名そのもの（`requirements` / `spec` / `consistency` / `implementation` / `test` / `compliance`）。`requirements` は state.json 不要（requirements ステージが生成する）、それ以外は必要。`plan_repair` は `--from` では指定できない（implementation 内部からのみ遷移）。

上記以外の値は無効。`reference/error-handling.md` の手順で有効値を提示する。`--from` による書き換えは STEP 2 で行い、hook 導入環境では `state-sync.sh` が同時に `task_checklist.md` を巻き戻す。

PROFILE が決まったら、ステージを始める前に `reference/profiles.md` を Read する（`--dry-run` を除く）。

`--no-gui` / `--no-api` のようなプロジェクトタイプ指定フラグは**存在しない**。`is_gui` / `is_api` / `is_infra` / `is_e2e` は requirements ステージが対話で確定して state.json に書く。

### STEP 1.2: 下流スキルファイルの事前検証

「起動時コンテキスト › 下流スキルの存在」の結果で判定する（`--dry-run` の有無に関わらず）：

| 検証結果 | DRY_RUN=false | DRY_RUN=true |
|---|---|---|
| `MISSING` 行が1件以上 | AskUserQuestion で人間に報告して中断 | 下記 dry-run 出力で欠損行を `✗` で示してから終了 |
| すべて存在 | STEP 1.5 へ進む | 下記 dry-run 出力で全行 `✓` を示してから終了（STEP 1.5 以降はスキップ） |

**`--dry-run` 時の出力例:**

```
[dry-run] 実行計画（profile=quality）:
  1. requirements: オーケストレーターが直接実行 (opus)  ← 下流スキル: 存在 ✓
  2. spec:         オーケストレーターが直接実行 (opus)  ← 下流スキル: 欠損 ✗  ~/.claude/skills/dev-flow-spec/SKILL.md が見つかりません
  ...
（profile=cost の場合は「stage-spec-agent (haiku)」のように起動するエージェントとモデルを出す）
✅ 全スキルファイル確認完了 / ❌ 欠損スキルあり。setup.sh を実行してください。
```

### STEP 1.5: 変更種別（kind）と開発モードの判定

`--from` 指定時、または state.json の `next_stage` が `completed` 以外で進行中のときはスキップ（進行中 run の `kind` をそのまま使う）。ただし `--profile=` が明示されていて state.json の `profile` と違う場合は、`profile` だけ書き換える（途中から切り替えてよい）。

**1. 既存実装の確認:**

「起動時コンテキスト › 実装コードの有無」の数値で判定する（テスト系ファイルは除外済み）。`1` 以上 → 実装コードあり。`0` → `kind = "feature"`, `mode = "full"` で確定（KIND 引数があってもそちらは無視せず、`feature` 以外なら「実装コードが無いので feature として扱う」と伝える）。

**2. 実装コードがある場合:**

まず state.json と要件定義書の有無を見る：

| state.json | `doc/requirements/*.md` | 判定 |
|---|---|---|
| なし | なし | **未導入の既存プロジェクト**。AskUserQuestion で「`bootstrap`（既存コードから as-is ドキュメントを生成。推奨）/ `feature` として新規機能だけ文書化して進める」を提示。`bootstrap` を選んだら STEP 4 で `stage-bootstrap-agent` を起動し、完了後にこの STEP に戻る |
| なし | あり | 手書きの要件定義書がある。REQ-ID が振られていなければ `bootstrap` を勧める（ID 付与だけ行う） |
| あり（`completed`） | あり | 導入済み。次の判定へ |

KIND が未指定なら AskUserQuestion で確認（TASK の文面から推測できる場合は推測値を先頭に「(推奨)」を付けて提示）：

| 選択肢 | kind | 最初のステージ |
|---|---|---|
| 新機能を追加する | `feature` | requirements |
| 既存機能の要件を変更する | `change` | requirements（修正モード） |
| 不具合を直す（要件は変わらない） | `fix` | spec（再現 TC 追加） |
| 挙動を変えずに内部を改善する | `refactor` | consistency（lite） |

`mode` は実装コードがある時点で `"incremental"`、`baseline_commit` は state.json に既にあればその値、無ければ `git rev-parse HEAD`。

**3. state.json への書き込み:**

- state.json が無い（`feature` / `change` で bootstrap を飛ばした場合）→ requirements ステージが生成するので、`kind` / `task` / `profile` / `mode` / `baseline_commit` を渡す（quality では自分で requirements を実行するので、state.json を書くときにこれらを入れる）
- state.json がある → `next_stage` を kind の最初のステージ、`kind`、`task`、`profile`、`harness.started_at` を書き換え、`implementation_progress` を削除、`harness.stage_history` を `[]` にして保存

### STEP 2: 状態ファイルの読み込み

「起動時コンテキスト › 現在の state.json」から `next_stage` を確認（旧スキーマなら「状態管理」の対応で読み替える）。STEP 1.5 で書き換えた場合はそちらが優先。`completed` なら STEP 1.5 で新しい run として書き換え済みのはずなので、その値を使う。

`--from` が指定されている場合（`requirements` 以外）:
1. state.json が無ければ AskUserQuestion でエラー報告（`reference/error-handling.md`）
2. `next_stage` に `--from` の値を Edit で書き込む（他フィールドは触らない）
3. `implementation_progress` が残っている状態で `implementation` より前に戻す場合は、worktree と未マージ PR が残ることを人間に伝えて続行可否を確認する

### STEP 3: タスクチェックリストの確認・表示

「起動時コンテキスト › task_checklist.md のステージ進捗」を人間に表示（Read し直す必要はない）。

### STEP 3.5: エージェント階層安全装置の確認

**1. 階層深さ上限チェック:** `state.json.agent_hierarchy.current_depth >= max_depth(4)` → AskUserQuestion でエスカレーション。それ以外 → 起動時に `+1`、完了時に `-1`。

**2. 無限ループ検出:** 同じ `(stage, agent_name)` の組み合わせが `harness.stage_history` に5回以上あれば AskUserQuestion で確認。

**3. タイムアウト目安:** haiku=5分 / sonnet=15分 / opus=30分（cost）。quality はモデルに関係なく起動 5 分後に一次確認し、生存確認に 3 分応答が無ければハングとみなす（`reference/profiles.md`「待ち時間とハング検知」）。超過時は AskUserQuestion で人間に確認する前に、まず `reference/agent-hang-recovery.md` の検知手順でハング（環境起因で pane 型サブエージェントが一切ツールを実行しない状態）かどうかを切り分ける。

**cost で STEP 4 で起動する `stage-*-agent`（中間管理エージェント）がハングした場合は fork フォールバックを使わない**（fork は `Agent` ツールで子サブエージェントを起動できないため、`stage-implementation-agent` のように内部で Dev/QA/レビュアーをさらに起動する設計のエージェントでは fork にしても実質何もできず終了する。実地で確認済み）。この場合は `reference/agent-hang-recovery.md` の「中間管理エージェントがハングした場合」の手順に従い、**オーケストレーター（このセッション）が該当ステージのスキル（`~/.claude/skills/dev-flow-<stage>/SKILL.md`）を自分で読み込み、STEP 0 以降を直接実行する**（worktree 作成・state.json 更新・Dev/QA/レビュアーの起動を自分の Agent ツールで行う。これらは末端の実行者なので通常どおり fork フォールバックが効く）。

単に処理が長い場合（ハングでない）は従来どおり AskUserQuestion で人間に確認する。

1・2 は hook 導入環境では `pre-agent-check.sh` が Agent 起動時に機械的に検証する（違反時は `ask` で停止）。実測の所要時間は `doc/process/flow.log` の `duration_seconds` で確認できる。

### STEP 4: ステージを実行する

**quality（既定）: オーケストレーターが直接実行する**

0. 同じ stage が `stage_history` に既に 5 回以上あれば、始める前に AskUserQuestion で確認する（cost では hook がやる確認）
1. 起動前に「▶ {タスク名} を開始（直接実行 / opus）」と一行で表示し、`harness.stage_history` に `{stage, model: "opus", started_at}` を追記する
2. 下の対応表のスキルファイルを**全文** Read し、そのスキルの STEP 0 から順に自分で実行する（抜き出し・要約はしない）。stage-*-agent 向けに書かれた箇所の読み替えは `reference/profiles.md` に従う
3. スキル内で Writer・Dev/QA implementer・レビュアー・test runner などの末端の実行者を起動するときは、`reference/profiles.md`「役割ごとのモデル」の quality 列のモデルを `Agent(model=…)` に渡す
4. そのステージの出口（state.json の `next_stage` が次に進んだ、または人間待ち・エスカレーションで止まった）に来たら `stage_history` の該当要素に `completed_at` / `duration_seconds` を書き、STEP 5 へ進む

**cost（`--profile=cost`）: ステージエージェントに任せる**

以下の対応表と「サブエージェント起動」に従う。

**ステージ対応表:**

| next_stage | タスク名 | エージェント name（cost） | モデル（cost） | スキルファイル |
|---|---|---|---|---|
| なし / `requirements` | 1. requirements: 要件定義 | `stage-requirements-agent` | opus | `dev-flow-requirements/SKILL.md` |
| `spec` | 2. spec: 仕様書生成 | `stage-spec-agent` | haiku | `dev-flow-spec/SKILL.md` |
| `consistency` | 3. consistency: 整合性チェック | `stage-consistency-agent` | haiku | `dev-flow-consistency/SKILL.md` |
| `plan_repair` | 3'. plan_repair: 計画修正 | `stage-plan-repair-agent` | haiku | `dev-flow-consistency/SKILL.md` |
| `implementation` | 4. implementation: 並列実装 | `stage-implementation-agent` | haiku | `dev-flow-implementation/SKILL.md` |
| `test` | 5. test: テスト実行 | `stage-test-agent` | haiku | `dev-flow-test/SKILL.md` |
| `compliance` | 6. compliance: 準拠チェック・完了 | `stage-compliance-agent` | opus | `dev-flow-compliance/SKILL.md` |
| （state.json なし・`--bootstrap` または STEP 1.5 で選択） | 0. bootstrap: as-is ドキュメント生成 | `stage-bootstrap-agent` | opus | `dev-flow-bootstrap/SKILL.md` |

スキルファイルのパスはすべて `~/.claude/skills/` 配下。

**`plan_repair` 特別処理:**

Plan Repair によって設定される一時ステージ。発動シーケンスは下記：

| # | アクター | 動作 |
|---|---|---|
| 1 | `stage-implementation-agent` | implementation 中にグループから `status: "blocked"` / `blocker_type: "plan_repair_needed"` を受信 |
| 2 | `stage-implementation-agent` | AskUserQuestion で「承認 / 却下 / 全体再生成」を提示。承認時に `state.json.next_stage` を `"plan_repair"` に書き換えて終了 |
| 3 | `dev-flow` オーケストレーター | 次イテレーションで `next_stage = "plan_repair"` を検出し `stage-plan-repair-agent`（consistency の mini モード）を起動 |
| 4 | `stage-plan-repair-agent` | 未着手グループのみチェックリストを再生成し、完了後 `next_stage` を `"implementation"` に書き戻して終了 |
| 5 | `dev-flow` オーケストレーター | `next_stage = "implementation"` を検出して implementation を未着手グループから再開 |

quality では、表の `stage-implementation-agent` と `stage-plan-repair-agent` はどちらもオーケストレーター自身と読み替える（「終了」は STEP 5 に戻ること。`next_stage = "plan_repair"` を見たら、そのまま consistency の mini モードを実行する）。

Plan Repair の発動上限は **3 回**。超過時は `requirement_ambiguity` として人間エスカレーション。詳細は `~/.claude/skills/dev-flow-implementation/SKILL.md` の「Plan Repair フロー」および `dev-flow-implementation/reference/plan-repair.md` を参照。

**進捗の表示:** cost では起動前に「▶ {タスク名} を開始（{エージェント name} / {モデル}）」と人間に一行で表示する。Task 系ツール（`TaskCreate` 等）は使わない。進捗の永続化は `task_checklist.md` と `flow.log`（hook）が担う。

**サブエージェント起動（cost）:** オーケストレーターがスキルファイルを事前 Read し、ステージに必要なセクションのみ抽出してプロンプトに直接埋め込む（トークン削減）。2000トークン以下なら全文渡し可。

| stage | 渡すセクション | 省略するセクション |
|---|---|---|
| requirements | 要件定義手順・出力フォーマット・AskUserQuestion 指示 | フロー全体像・他ステージ手順 |
| spec | ドキュメント生成手順・各仕様書フォーマット | フロー全体像・実装手順 |
| consistency / plan_repair | 整合性チェック手順・差分検出方法・修正指示 | フロー全体像・実装手順 |
| implementation | 実装手順・worktree 管理・PR 作成方法 | フロー全体像・ドキュメント生成手順 |
| test | テスト実行手順・失敗時エスカレーション | フロー全体像・実装手順 |
| compliance | 準拠チェック手順・完了条件・最終コミット指示 | フロー全体像・実装手順 |

```
Agent(
  name: "{エージェント name}",
  model: "{モデル}",
  run_in_background: false,
  prompt: """
あなたは dev-flow の {stage} ステージ（{タスク名}）を担当するエージェントです。

作業ディレクトリ: {pwd の結果}
状態ファイル: doc/process/state.json
引数: {上の「引数」の内容}
変更種別: {kind} / タスク: {task}
開発モード: {mode} / baseline_commit: {baseline_commit}

## 実行する手順
{スキル内容の該当ステージ手順}
"""
)
```

**モデル指定のルール:**

- モデルは `reference/profiles.md`「役割ごとのモデル」の、run の `profile` の列に従う。表に無い値に勝手に変えない
- cost のステージ対応表の `モデル` 列は各下流スキルの frontmatter (`model:`) と一致しており、その値をそのまま `Agent(model=…)` に渡す。食い違っている場合はスキル frontmatter を信頼し、表側を修正する
- 下流スキル本文に書かれた末端の実行者の `model="…"` は cost の値。quality では profiles.md の quality 列で置き換える
- 表と違うモデルを使いたい場合（例: テスト目的・ユーザー指定）は AskUserQuestion で人間に確認してから変更する

**ステージ間依存関係と run_in_background:**

| stage | 並列実行可否 | run_in_background |
|---|---|---|
| requirements〜consistency | 不可（直列） | false |
| implementation 各グループ | グループ間は可（implementation の手順内で `run_in_background=true`）。quality では同じグループの Dev レビューと QA レビューも同時に起動する | false（PR マージは待たずに終了して再入する） |
| test〜compliance | 不可（直列） | false |

implementation 内のグループ並列化は、implementation の手順を実行している者（quality ではオーケストレーター、cost では `stage-implementation-agent`）が名前付きサブエージェントの完了通知（最終回答）で管理する。Agent Teams（`TeamCreate` / `team_name`）は使わない。Cross グループは直列。

**implementation の PR マージの責任分担（非ブロッキング）:**

オーケストレーターは PR のマージを**待たない**（`sleep` ポーリング禁止）。マージ待ちが発生したらフローを終了し、次回 `/dev-flow` 起動時に状態を確認して続きを進める。

| アクター | 責任 |
|---|---|
| implementation の実行者（quality: オーケストレーター / cost: `stage-implementation-agent`） | グループの実装完了後に `gh pr create` で PR を作成し、番号を `implementation_progress.pr_numbers["group-N"]`（**配列**。1 グループ 2〜4 PR）へ記録。続けて各 PR に `gh pr merge <N> --merge` を試行する。hook（`pr-merge-guard.sh`）が自動マージ条件を検証し、満たさなければ deny される。全 PR がマージ済みになったグループは STEP H で `completed_groups` へ追加。deny された PR が残るグループは「人間マージ待ち」とし、依存の無い他グループがあれば続行、無ければ人間に PR URL と deny 理由を提示して**終了**する |
| `dev-flow` オーケストレーター | `next_stage = "implementation"` で `implementation_progress` が残っていれば、そのまま implementation を再開する（quality は自分で、cost は `stage-implementation-agent` を起動する）（PR 状態の確認と取り込みは implementation 側の再開処理が行う）。自分で `gh pr view` をポーリングしない |
| 人間 | 自動マージ条件を満たさない PR のレビュー・マージ。`main` / `develop` 向け PR は常に人間がマージする |

**自動マージ条件（hook が機械的に検証する。プロンプトで緩和できない）:**

- ベースブランチが `feature/*`（`DEV_FLOW_AUTO_MERGE_BASE_PATTERN` で変更可）で、`state.json.implementation_progress.base_branch` と一致する。`main` / `master` / `develop` / `release/*` / `hotfix/*` は無条件で拒否
- CI チェックがすべて成功している（チェックが 1 つも無い PR は拒否）
- `mergeable == MERGEABLE`（コンフリクトなし）
- PR の diff にテストの削除・スキップ・無効化（`t.Skip` / `it.skip` / `@pytest.mark.skip` / `markTestSkipped` / テスト関数の削除。移動は可）が含まれない。パターンは `hooks/test-guard-patterns.txt`
- PR の diff に DB の破壊的変更が含まれない（DROP / TRUNCATE / カラム削除・型変更・リネーム、ORM マイグレーションの remove / rename / alter 系、Terraform の DB リソース削除や `skip_final_snapshot = true` 等。パターンは `hooks/db-destructive-patterns.txt`）
- マージ方式は `--merge` のみ。`--squash` / `--rebase` / `--auto` / `--admin` は拒否。1 コマンド 1 PR、番号または URL で明示

**hook 未導入環境では自動マージを行わない。** 「起動時コンテキスト › hooks の登録状況」が `disabled` なら `gh pr merge` を一切発行せず人間に委ねる（impl エージェントにもその旨をプロンプトで伝える）。

詳細は `~/.claude/skills/dev-flow/reference/state-schema.md` の「implementation の PR マージ待機ロジック」を参照。

### STEP 5: タスク完了 & チェックリスト更新 & 次ステージへの移行判定

**0. 動的ゲート判定（implementation のグループ完了通知時）:**

| confidence | needs_human_review | 動作 |
|---|---|---|
| ≥ 0.8 | false | 自動移行 |
| 0.5〜0.8 | false | 通知のみ表示して自動移行 |
| < 0.5 または — | true | AskUserQuestion で人間ゲート発動 |

`uncertainty_points` が空でない場合は `needs_human_review=true` として扱う。

**1.** 「✓ {タスク名} 完了」と人間に一行で表示する

**2. チェックリスト更新:** `task_checklist.md` が存在する場合、完了したステージ行の `[ ]` → `[x]` に更新して進捗を表示。hook 導入環境では `state.json` 書き込み時に `state-sync.sh` が自動同期するため、hook の `additionalContext` を確認したうえで Read して進捗を表示するだけでよい（「Hook 連携」参照）。

**3. 次ステージ移行:**

| 完了したステージ | 次の動作 |
|---|---|
| bootstrap | **手動確認**: 生成した as-is ドキュメントの確認を依頼し、確認後に `/dev-flow --kind=...` で本来の変更を始めるよう案内して終了 |
| requirements（next_stage が `spec` になった） | **手動確認**: 「要件定義完了。確認後 `/dev-flow` を実行してください。」 |
| spec 以降 | **自動移行**: STEP 2 に戻って次ステージを自動実行（compliance まで連続）。`fix` / `refactor` は最初のステージからこの扱い |
| plan_repair | `next_stage` が `"implementation"` に戻っていることを確認して implementation を再開 |

---

## エスカレーション

エスカレーションが必要な場合は `doc/process/escalation_{stage}_{timestamp}.md` を生成して AskUserQuestion で提示する。

フォーマット・recovery パス詳細は Read すること:
`~/.claude/skills/dev-flow/reference/escalation-format.md`

---

## エラーハンドリング

エラー対処の詳細は Read すること:
`~/.claude/skills/dev-flow/reference/error-handling.md`

主なケース: state.json 破損・Agent 起動失敗・サブエージェント停止・チェックリスト更新失敗
