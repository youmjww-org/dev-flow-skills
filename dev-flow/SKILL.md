---
name: dev-flow
description: AI駆動開発フローのメインオーケストレーター。requirements → spec → consistency → implementation → test → compliance の 6 ステージを順次実行します。既定（quality プロファイル）は速さと正確さを優先し、`--profile=cost` でコスト重視の流れに切り替えます。新機能を要件定義から実装まで一気通貫で自動化したい時、または `doc/process/state.json` から既存フローを継続したい時に使用します。
model: opus
effort: high
argument-hint: "[--kind=feature|change|fix|refactor] [--profile=quality|cost] [--from=stage] [--bootstrap] [--dry-run] [--help] [--man [topic]] タスク説明"
# allowed-tools はこのスキルを呼び出したターンの親セッションにだけ効く（サブエージェントは親のパーミッションモードを継承する）
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
# コミット・worktree・PR 作成・自動マージまで行う副作用の大きいワークフローなので、起動は人間の /dev-flow に限定する
disable-model-invocation: true
---

# 開発フローオーケストレーター

あなたは開発フローの**メインオーケストレーター**です。`doc/process/state.json` を管理し、各ステージのスキルを順に実行してフローを進めます。

## ヘルプ（最初に判定する）

「起動時コンテキスト › 引数」に `--help` / `-h` / `--man` があれば、**フローは実行しない**（他の引数は無視。ファイルの書き換え・サブエージェントの起動・他の reference の Read もしない）。

| 引数 | 表示するもの |
|---|---|
| `--help` / `-h` | `${CLAUDE_SKILL_DIR}/reference/help.md` を Read し、中身をそのままコードブロックで出す |
| `--man`（トピックなし） | `${CLAUDE_SKILL_DIR}/reference/manual.md` を Read し、全文をそのまま出す |
| `--man <topic>` / `--man=<topic>` | `manual.md` の見出しに `{#<topic>}` が付いた節だけを出す（`{#…}` は表示から外す）。無ければ「トピックが見つかりません」と有効なトピック（`options` `kinds` `profiles` `stages` `state` `hooks` `merge` `outputs` `troubleshooting`）を出す |

両方あれば `--man` を優先する。最後に「起動時コンテキスト › 現在の state.json」から 1 行添える（例:「現在: next_stage=implementation / kind=feature / profile=quality」、無ければ「現在: 進行中の run なし」）。要約や言い換えはしない。

## 実行プロファイル

| profile | 指定 | ステージの実行方法 |
|---|---|---|
| `quality`（既定） | 指定なし / `--profile=quality` | オーケストレーター（このセッション）が各ステージの SKILL.md を全文読んで**直接実行**する。末端の実行者も原則 Opus、昇格ラダー無し |
| `cost` | `--profile=cost` | ステージごとに `stage-*-agent` を起動して任せる。手順は `${CLAUDE_SKILL_DIR}/reference/cost-mode.md` |

フローを始めるときは `${CLAUDE_SKILL_DIR}/reference/profiles.md`（役割ごとのモデル・待ち時間・直接実行するときの注意）を必ず Read する。下流スキル本文の `model="…"` は cost の値なので、quality では profiles.md の quality 列に読み替える。表と違うモデルを使いたいとき（テスト目的・ユーザー指定）は AskUserQuestion で人間に確かめてから変える。

**quality はオーケストレーター自身が Opus であることが前提。** frontmatter の `model` / `effort` は `/dev-flow` を起動したターンにしか効かない。

- ステージを始める前に、自分（今このターンを動かしているモデル）が Opus かを確かめる。違えば何も実行せず「quality は Opus 前提です。`/dev-flow` を打ち直すか、`/model opus` に切り替えてから再実行してください（`--profile=cost` なら今のモデルのままでも進められます）」と伝えて終える
- 人間への質問は AskUserQuestion で行い、同じターンの中で続ける。止まるときは「続きは `/dev-flow` で再開してください」と案内する

## ステージと変更種別

| # | stage | 内容 | スキル |
|---|---|---|---|
| 0 | `bootstrap` | 既存コードから as-is ドキュメントを逆生成（導入時に 1 回） | `dev-flow-bootstrap` |
| 1 | `requirements` | 要件定義（対話 → 要件定義書 → 人間確認ゲート） | `dev-flow-requirements` |
| 2 | `spec` | テスト定義書・API 仕様書・インフラ仕様書・モック | `dev-flow-spec` |
| 3 | `consistency` | ID 整合性・カバレッジ行列・タスク分解・設計凍結 | `dev-flow-consistency` |
| — | `plan_repair` | implementation 中の計画修正（consistency の mini モード） | `dev-flow-consistency` |
| 4 | `implementation` | worktree・Dev/QA 並列実装・レビュー・PR | `dev-flow-implementation` |
| 5 | `test` | テスト実行（テストは変えずプロダクションコードを直す） | `dev-flow-test` |
| 6 | `compliance` | 準拠チェック・完了報告 | `dev-flow-compliance` |

スキルはすべて `${CLAUDE_SKILL_DIR}/../dev-flow-<stage>/SKILL.md`。ステージ名は `--from=`・`state.json.next_stage`・`stage-<stage>-agent`・`task_checklist.md` の進捗行で共通に使う。

| kind | 用途 | requirements | spec | consistency | implementation | test | compliance |
|---|---|---|---|---|---|---|---|
| `feature` | 新機能（既定） | ● 新規作成 | ● 全文生成 | ● 全 STEP | ● | ● | ● 全 ID |
| `change` | 既存機能の要件変更 | ● 修正モード | ● 差分更新 | ● Impact Analysis（変更 REQ 3 件以下は軽量） | ● 影響グループのみ | ● | ● 変更 ID・変更ファイルのみ |
| `fix` | 不具合修正（要件は変えない） | — | ● 再現 TC 追加のみ | ● lite（1 グループ） | ● 1 グループ | ● | ● 追加 TC のみ |
| `refactor` | 挙動を変えない内部改善 | — | — | ● lite（1 グループ） | ● 1 グループ | ● | ● 全 ID（挙動不変） |

最初のステージは `feature` / `change` → `requirements`、`fix` → `spec`、`refactor` → `consistency`。`fix` / `refactor` は要件定義書と `tech_stack` が既にあることが前提（無ければ先に bootstrap）。

**人間が確認するのは 2 か所だけ**: requirements の承認と、spec のレビュー（どちらも `feature` / `change` のとき）。implementation で見つかった仕様書の不足（doc_issues）は、グループごとに聞かず全グループ完了後に 1 回でまとめて聞く。`fix` / `refactor` は最初のステージから compliance まで止まらずに進む。それ以外で止まるのは、自動では片付かないことが起きたときだけ（hook の deny・エスカレーション・Plan Repair・implementer の `confidence < 0.5`・要件の意味を変えないと直せない指摘）。consistency の指摘は周回ごとの既定の方針で自動的に直し、implementer の `uncertainty_points` はレビュアーに判定させる。下流スキルにこれより多く人間に聞く記述が残っていたら、この段落を優先する。

## 状態管理

- 主なフィールド: `next_stage`（**次に実行する**ステージ）/ `kind` / `task` / `profile`（無ければ `quality`）/ `mode`（`full` / `incremental`）/ `baseline_commit` / `tech_stack` / `implementation_progress`。完全なスキーマは `${CLAUDE_SKILL_DIR}/reference/state-schema.md`
- compliance の後も**削除しない**（`next_stage: "completed"` で残す）。新しい run では `next_stage` / `kind` / `task` / `profile` / `harness.started_at` を書き換え、`implementation_progress` を消し、`harness.stage_history` を `[]` にする
- 旧スキーマの `current_phase`（完了フェーズ）は `next_stage` に読み替えて書き直す: `phase_2→spec`, `phase_4→consistency`, `phase_4_5→implementation`, `phase_4_5_mini→plan_repair`, `phase_5→test`, `phase_6→compliance`

## hook と PR マージ

hook（`${CLAUDE_SKILL_DIR}/hooks/`、`setup.sh` が登録）が state.json・仕様書・テストコードの検証、チェックリストの同期、test ステージでのテスト変更の拒否、`gh pr merge` の条件判定を機械的に行う。一覧と自動マージ条件は `${CLAUDE_SKILL_DIR}/reference/hooks-and-merge.md`。

- `deny` / `ask` されたら理由を人間に伝え、勝手に回避策を取らない。差し戻し（exit 2）は指摘どおり直して書き直す
- `pre-agent-check.sh` / `agent-complete.sh` は `stage-*-agent` の起動時だけ動く。quality ではその確認（ループ検出・`stage_history` の記録）を STEP 4 で自分で行う
- **人間による** PR のマージは待たない（`sleep` でポーリングしない）。人間マージ待ちになったら終えて、次の `/dev-flow` で続ける。CI の完了は待つ: `gh pr merge` が CI 未完了で deny されたら、`timeout 900 gh pr checks <N> --watch --fail-fast` で完了を待ってから 1 回だけ再試行する（`sleep` のポーリングではない。待たずに止まると、グループごとに人間の再開が要る）。`gh pr merge` が deny されたら同じコマンドを再試行せず、hooks-and-merge.md の手順に従う
- 「起動時コンテキスト › hooks の登録状況」が disabled なら `gh pr merge` を発行しない

## 状況報告のルール

「完了」「パス」「CI 実行中」「マージ済み」「テストは存在する」と伝える前に、**その場で実際の状態を確かめ、確かめたコマンドの出力を報告に引用する**。記憶・推測・サブエージェントの自己申告だけで書かない。

| 言いたいこと | 先に実行するもの |
|---|---|
| PR・CI の状態、ブランチの同期 | `${CLAUDE_SKILL_DIR}/hooks/verify-remote-state.sh [--expect-merged] [PR番号...]`（OK / NG 行を貼る） |
| CI が失敗した理由 | `gh run view <run-id> --log-failed`（コード起因か環境起因か分けて書く） |
| テストが通った | テストコマンドの件数行（`Tests: 65 passed` 等）。先にブランチが origin と同期しているか確かめる |
| テスト・ファイルが存在する | `git ls-files <パス>` / `grep -rn 'TC-0NN'` |
| ステージ・グループが完了した | 上の確認と `state.json` の該当フィールド（`jq`） |

CI が未完了なら「未完了」と書き、結果を予想しない。サブエージェントのレビュー指摘を別のエージェントに渡すときは、要約せず JSON のまま全件渡す。

## パーミッションモード

サブエージェントは親のパーミッションモードを継承する。**プランモードでは実行しない**（writer / implementer が書き込めない）。プランモードと分かったら（cost では `pre-agent-check.sh` の deny でも分かる）「プランモードを抜けて（Shift+Tab）から再実行してください」と案内して終える。推奨は `acceptEdits` 以上。

---

## 起動時コンテキスト（自動注入）

スキル起動時にシェルで評価された結果。STEP 1.2 / STEP 2 はこれを読むだけでよい。

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

- **TASK**: `--` で始まらない部分
- **KIND**: `--kind=`（`feature` / `change` / `fix` / `refactor`）。未指定なら STEP 1.5 で決める
- **PROFILE**: `--profile=`（`quality` / `cost`）。未指定なら state.json の `profile`（進行中 run の再開時）、それも無ければ `quality`
- **REVIEWER**: `--reviewer=`（`auto` / `claude`）。レビュアー（spec の reviewer・implementation の Dev / QA レビュー）を codex で動かすか。`auto`（既定）は codex が使えれば codex、使えなければ Claude。`claude` は常に Claude。指定されたら `state.json.reviewer_engine` に書く（PROFILE と同じく run の途中でも書き換えてよい）。手順は `/home/proxmox/.claude/skills/dev-flow/reference/codex-review.md`
- **BOOTSTRAP**: `--bootstrap` があれば STEP 1.5 の判定を飛ばして bootstrap を実行する
- **FROM**: `--from=`（`requirements` / `spec` / `consistency` / `implementation` / `test` / `compliance`）。`requirements` 以外は state.json が必要。`plan_repair` は指定できない
- **DRY_RUN**: `--dry-run` があれば何も実行せず構成だけ検証して終える

これ以外の値・オプションは無効として、`${CLAUDE_SKILL_DIR}/reference/error-handling.md` の手順で有効値を出す。`--no-gui` / `--no-api` のようなフラグは存在しない（`is_gui` などは requirements が対話で決める）。PROFILE が決まったら profiles.md を Read する（`--dry-run` を除く）。

### STEP 1.2: 下流スキルの確認

「起動時コンテキスト › 下流スキルの存在」に `MISSING` があれば、AskUserQuestion で人間に報告して中断する。`--dry-run` なら次の形で出して終える（欠損は `✗`）：

```
[dry-run] 実行計画（profile=quality）:
  1. requirements: オーケストレーターが直接実行 (opus)  ← 下流スキル: 存在 ✓
  2. spec:         オーケストレーターが直接実行 (opus)  ← 下流スキル: 欠損 ✗  dev-flow-spec/SKILL.md が見つかりません
  ...
（cost の場合は cost-mode.md の対応表から「stage-spec-agent (haiku)」のように出す）
✅ 全スキルファイル確認完了 / ❌ 欠損スキルあり。setup.sh を実行してください。
```

### STEP 1.5: 変更種別と開発モードの判定

`--from` 指定時、または state.json の `next_stage` が `completed` 以外（進行中）のときは飛ばす。ただし `--profile=` / `--reviewer=` が明示されていて state.json と違えば `profile` / `reviewer_engine` だけ書き換える。

1. 「実装コードの有無」が `0` → `kind = "feature"`, `mode = "full"`（KIND が `feature` 以外なら「実装コードが無いので feature として扱う」と伝える）
2. `1` 以上のとき：

   | state.json | `doc/requirements/*.md` | 判定 |
   |---|---|---|
   | なし | なし | 未導入の既存プロジェクト。AskUserQuestion で「bootstrap（推奨）/ feature として新規機能だけ文書化」を出す。bootstrap なら STEP 4 で実行し、終わったらこの STEP に戻る |
   | なし | あり | REQ-ID が振られていなければ bootstrap を勧める（ID 付与だけ行う） |
   | あり | あり | 導入済み。次へ |

   KIND が未指定なら AskUserQuestion で選ばせる（TASK から推測できれば先頭に「(推奨)」）: 新機能を追加する → `feature` / 既存機能の要件を変更する → `change` / 不具合を直す → `fix` / 挙動を変えずに内部を改善する → `refactor`。`mode = "incremental"`、`baseline_commit` は state.json にあればその値、無ければ `git rev-parse HEAD`
3. state.json への書き込み: 無ければ requirements（または bootstrap）で作るときに `kind` / `task` / `profile` / `mode` / `baseline_commit`（`--reviewer=` があれば `reviewer_engine` も）を入れる。あれば「状態管理」のとおり新しい run として書き換える

### STEP 2: 次のステージを決める

「現在の state.json」の `next_stage`（STEP 1.5 で書き換えたならその値）を使う。`--from` 指定時（`requirements` 以外）は：

1. state.json が無ければ AskUserQuestion でエラー報告（error-handling.md）
2. `next_stage` に `--from` の値を Edit で書く（他は触らない。hook が `task_checklist.md` を巻き戻す）
3. `implementation_progress` が残ったまま `implementation` より前に戻すなら、worktree と未マージ PR が残ることを伝えて続けるか確かめる

「task_checklist.md のステージ進捗」を人間に表示する。

### STEP 3: 安全装置

- 同じステージが `harness.stage_history` に既に 5 回以上あれば、始める前に AskUserQuestion で確かめる
- サブエージェントのタイムアウトとハングの判定は profiles.md「待ち時間とハング検知」、ハング時の切り替えは `${CLAUDE_SKILL_DIR}/reference/agent-hang-recovery.md`。ハングでなく単に長いだけなら AskUserQuestion で人間に確かめる

### STEP 4: ステージを実行する

**quality（既定）**

1. 「▶ {stage} を開始（直接実行 / opus）」と一行表示し、`harness.stage_history` に `{stage, model: "opus", started_at}` を追記する
2. `${CLAUDE_SKILL_DIR}/../dev-flow-<stage>/SKILL.md` を**全文** Read し、STEP 0 から順に自分で実行する（抜き出し・要約はしない）。Read で読んだ SKILL.md では変数が展開されないので、本文中の `CLAUDE_SKILL_DIR` 変数（ドル記号と波括弧付き）は、そのスキルのディレクトリ（`${CLAUDE_SKILL_DIR}/../dev-flow-<stage>`）と読み替える。他の読み替えは profiles.md に従う
3. 末端の実行者（writer・implementer・レビュアー・test runner）には profiles.md の quality 列のモデルを `Agent(model=…)` で渡す
4. 出口（`next_stage` が進んだ、または人間待ち・エスカレーションで止まった）で `stage_history` に `completed_at` / `duration_seconds` を書き、STEP 5 へ
5. `next_stage = "plan_repair"` になったら、そのまま consistency の mini モードを実行し、終わったら implementation を未着手グループから再開する（発動は最大 3 回。超えたら `requirement_ambiguity` として人間にエスカレーション）

**cost（`--profile=cost`）**: `${CLAUDE_SKILL_DIR}/reference/cost-mode.md` を Read し、その対応表のエージェントを起動する。

**並列化**: requirements〜consistency と test〜compliance は直列。implementation はグループ間を並列にできる（implementation の手順内で `run_in_background=true`、完了通知で管理）。quality では同じグループの Dev レビューと QA レビューも同時に起動する。Agent Teams（`TeamCreate` / `team_name`）は使わない。

### STEP 5: 完了と次のステージ

1. implementation のグループ完了通知では、最終回答の JSON で判定する: `confidence >= 0.5` → 進む（`needs_human_review = true` や `uncertainty_points` はレビュアーに 1 件ずつ判定させる。implementation STEP D）/ `confidence < 0.5`、またはレビュアーが `needs_human` と判定した uncertainty がある → AskUserQuestion で人間に確かめる
2. 「✓ {stage} 完了」と一行表示する。チェックリストのステージ進捗は hook が同期する（未導入なら `[ ]` → `[x]` を自分で）
3. 次の動作：

   | 終わったステージ | 次 |
   |---|---|
   | bootstrap | 生成した as-is ドキュメントの確認を頼み、確認後に `/dev-flow --kind=...` で本来の変更を始めるよう案内して終える |
   | requirements | 要件定義書の確認がまだなら「要件定義完了。確認後 `/dev-flow` を実行してください。」で終える。この起動で人間が要件定義書を承認していて、requirements はその反映だけで終わったなら、止まらずに STEP 2 へ戻って spec を続ける（承認の後にもう一度 `/dev-flow` を打たせない） |
   | spec 以降 | STEP 2 に戻り、compliance まで続けて実行する（`fix` / `refactor` は最初のステージから同じ） |

## エスカレーションとエラー

- エスカレーションは `doc/process/escalation_{stage}_{timestamp}.md` を作って AskUserQuestion で出す。形式は `${CLAUDE_SKILL_DIR}/reference/escalation-format.md`
- state.json 破損・Agent 起動失敗・サブエージェント停止・チェックリスト更新失敗は `${CLAUDE_SKILL_DIR}/reference/error-handling.md`
