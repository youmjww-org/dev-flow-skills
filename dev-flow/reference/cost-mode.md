# cost プロファイルでのステージ実行

`--profile=cost`（`state.json.profile == "cost"`）のときだけ読む。quality では使わない。

## 目次

- ステージ対応表
- サブエージェントの起動
- 中間管理エージェントがハングしたとき
- Plan Repair の流れ

## ステージ対応表

| next_stage | タスク名 | エージェント name | モデル | スキルファイル |
|---|---|---|---|---|
| なし / `requirements` | 1. requirements: 要件定義 | `stage-requirements-agent` | opus | `dev-flow-requirements/SKILL.md` |
| `spec` | 2. spec: 仕様書生成 | `stage-spec-agent` | haiku | `dev-flow-spec/SKILL.md` |
| `consistency` | 3. consistency: 整合性チェック | `stage-consistency-agent` | haiku | `dev-flow-consistency/SKILL.md` |
| `plan_repair` | 3'. plan_repair: 計画修正 | `stage-plan-repair-agent` | haiku | `dev-flow-consistency/SKILL.md` |
| `implementation` | 4. implementation: 並列実装 | `stage-implementation-agent` | haiku | `dev-flow-implementation/SKILL.md` |
| `test` | 5. test: テスト実行 | `stage-test-agent` | haiku | `dev-flow-test/SKILL.md` |
| `compliance` | 6. compliance: 準拠チェック・完了 | `stage-compliance-agent` | opus | `dev-flow-compliance/SKILL.md` |
| （`--bootstrap` または STEP 1.5 で選択） | 0. bootstrap: as-is ドキュメント生成 | `stage-bootstrap-agent` | opus | `dev-flow-bootstrap/SKILL.md` |

スキルファイルは dev-flow と同じ階層（`dev-flow/` の隣の `dev-flow-<stage>/`）にある。`モデル` 列は各下流スキルの frontmatter (`model:`) と一致させてある。食い違っていたらスキル frontmatter を信頼し、この表を直す。

起動前に「▶ {タスク名} を開始（{エージェント name} / {モデル}）」と一行で表示し、`harness.stage_history` に `{stage, model, started_at}` を追記する。エージェントが戻ったら、その要素に `completed_at` / `duration_seconds` / `outcome`（`next_stage` が進んだ / 人間待ち / エスカレーション）を書く。ステージエージェントは `stage_history` を書かない（quality と同じ形でオーケストレーターだけが書く）。Task 系ツール（`TaskCreate` 等）は使わない。進捗の永続化は `task_checklist.md` と `flow.log`（hook）が担う。

## サブエージェントの起動

ステージエージェントは `~/.claude/agents/stage-*-agent.md` に定義してある（リポジトリの `agents/`。`setup.sh` がリンクする）。定義にモデルと「下流スキルの SKILL.md を全文 Read して STEP 0 から実行する」指示が入っているので、オーケストレーターはスキルを読んだり抜き出したりせず、run の情報だけを渡す。

```
Agent(
  subagent_type: "{エージェント name}",
  name: "{エージェント name}",
  run_in_background: false,
  prompt: """
作業ディレクトリ: {pwd の結果}
状態ファイル: doc/process/state.json
引数: {/dev-flow の引数}
変更種別: {kind} / タスク: {task} / profile: cost
開発モード: {mode} / baseline_commit: {baseline_commit}
hooks: {enabled / disabled}
"""
)
```

- `name` は hook（`pre-agent-check.sh` / `agent-complete.sh`）がエージェントを見分けるのに使うので、`subagent_type` と同じ値を必ず渡す
- `model` は渡さない（定義の `model` を使う。Agent ツールの `model` を渡すと定義より優先される）
- ステージエージェントは子エージェントを `run_in_background=false` で起動するよう定義に書いてある（バックグラウンドで起動すると、子の完了前に最終回答が返ってステージが途中で終わる。2026-10 の通し検証で spec と implementation で起きた）。それでも途中で戻ってきたら（`next_stage` が進んでおらず、人間への質問も無い）、`SendMessage` で子の結果を渡して続けさせる
- 定義が見つからない（`subagent_type` が使えない）ときは、`setup.sh` の再実行と Claude Code の再起動を案内する。それまでの間は `subagent_type: "general-purpose"`・`model` に対応表のモデルを指定し、`agents/stage-*-agent.md` の本文をそのままプロンプトの先頭に付けて起動する

hook の `pre-agent-check.sh` が起動前に、プランモード・下流スキルの欠損・state.json 不正・階層深さ（`agent_hierarchy.current_depth >= 4`）・ステージとエージェントの不一致・同じステージ 5 回以上を検証する。`deny` / `ask` されたら理由を人間に伝え、回避策を取らない。hook 未導入環境では同じ確認を自分で行う（階層深さは起動時に `+1`、完了時に `-1`）。

## 中間管理エージェントがハングしたとき

`stage-*-agent` がハングしても **fork では再起動しない**。fork は `Agent` ツールで子サブエージェントを起動できないため、Dev/QA/レビュアーを起動する設計の `stage-implementation-agent` などは何もできずに終わる。代わりに `TaskStop` で止め、オーケストレーター自身が該当ステージの SKILL.md を読んで STEP 0 から直接実行する（quality と同じやり方。手順は `agent-hang-recovery.md`「中間管理エージェントがハングした場合」）。人間には「{エージェント名} がハングしたため、オーケストレーターが直接そのステージを実行しています」と一行で伝える。

## Plan Repair の流れ

| # | アクター | 動作 |
|---|---|---|
| 1 | `stage-implementation-agent` | グループから `status: "blocked"` / `blocker_type: "plan_repair_needed"` を受け取る |
| 2 | `stage-implementation-agent` | AskUserQuestion で「承認 / 却下 / 全体再生成」を出す。承認なら `next_stage` を `"plan_repair"` にして終了 |
| 3 | オーケストレーター | `next_stage = "plan_repair"` を見て `stage-plan-repair-agent`（consistency の mini モード）を起動 |
| 4 | `stage-plan-repair-agent` | 未着手グループのチェックリストだけ作り直し、`next_stage` を `"implementation"` に戻して終了 |
| 5 | オーケストレーター | implementation を未着手グループから再開 |

発動は最大 3 回。超えたら `requirement_ambiguity` として人間にエスカレーションする（`dev-flow-implementation/reference/plan-repair.md`）。
