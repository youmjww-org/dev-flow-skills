# dev-flow hooks

`dev-flow` オーケストレーターがプロンプトの指示（LLM の判断）に頼っていた検証・記録・同期を、Claude Code の hooks で**決定的に**実行するスクリプト群です。`setup.sh` が `~/.claude/settings.json` に登録します（`--no-hooks` で省略可）。

## 一覧

| イベント | matcher | スクリプト | 役割 |
|---|---|---|---|
| `SessionStart` | — | `session-start.sh` | `doc/process/state.json` があれば次ステージ・次アクション・直近ログをコンテキストに注入。ブランチが origin より遅れていれば警告（fetch は 5 秒で打ち切り） |
| `PreToolUse` | `Agent` | `pre-agent-check.sh` | `stage-*-agent` 起動前にプランモードでないこと・下流スキル存在・state.json 妥当性・階層深さ・ステージ対応・ループ回数を検証。問題があれば `deny` / `ask` |
| `PostToolUse` | `Write\|Edit` | `state-sync.sh` | `state.json` の JSON 妥当性検証（壊れていれば exit 2 で差し戻し）、`task_checklist.md` ステージ進捗の自動同期、`flow.log` 記録。implementation → test の移行は、直前に `verify-remote-state.sh` が OK を記録していなければ exit 2。`escalation_*.md` 生成時は Slack 通知（opt-in） |
| `PostToolUse` | `Write\|Edit` | `doc-validate.sh`（→ `doc-validate.py`） | `doc/{requirements,test-spec,api-spec,infra-spec}/*.md` と `task_checklist.md` のスキーマ検証。ID 形式・重複・`covers` の REQ 実在・`implemented_by` の関数実在・本文見出し・`status` 値域・ステージ進捗 6 行。要件定義書の REQ がテスト定義書（または infra-spec / api-spec）のどの `covers` にも無ければ WARN。ERROR は exit 2 で差し戻し、WARN は `additionalContext` で通知。python3 が無い環境ではスキップ |
| `PostToolUse` | `Write\|Edit` | `test-lint.sh`（→ `test-lint.py`） | テストコード（Go / Python / TS・JS / PHP / シェル）の静的検証。skip・assert なし・空テスト・エラー握りつぶし・`expect.assertions(0)`・（シェル）同じ値どうしの比較は exit 2 で差し戻し、sleep / 現在時刻 / 乱数 / tautology / コメントアウト / `@ts-ignore`・（シェル）`grep -c`・trap の無い復元・WARN だけの失敗・IPv4 限定の照合は WARN で通知 |
| （hook ではない） | — | `verify-remote-state.sh [--base BRANCH] [--expect-merged] [PR番号...]` | 状況報告・ステージ移行の前の確認。`git fetch` してブランチの ahead / behind、PR の state と CI の結果を OK / NG で 1 行ずつ出す。NG があれば exit 1。`flow.log` に `remote_verified` を記録（`state-sync.sh` の test 移行ゲートが参照） |
| （hook ではない） | — | `mark-group-done.sh <N> [PR番号...] [--no-commit]` | implementation STEP H の機械的作業。グループ N のチェックリスト（全一覧も）を `[x]` に、`state.json` の `completed_groups` / `active_worktrees` / `pr_numbers` を更新し、1 コミットする。冪等 |
| `PostToolUse` | `Agent` | `agent-complete.sh` | `stage-*-agent` 完了を `flow.log` に記録し所要時間を算出。requirements 完了時は人間確認ゲートを念押し |
| `PreToolUse` | `Write\|Edit\|NotebookEdit` | `test-stage-guard.sh` | `next_stage == test` のとき、テストファイル（`*_test.go` / `test_*.py` / `*.test.ts` / `tests/` 等）と `doc/test-spec/` への書き込みを `deny`。test ステージはプロダクションコードしか直せない（DocDD） |
| `PreToolUse` | `Bash` | `state-write-guard.sh` | `doc/process/state.json` を Bash（リダイレクト・`mv` / `cp`・`tee`・`sed -i`・スクリプトの `open(..., "w")`）で書き換えるのを `deny`。Bash で書くと `state-sync.sh`（Write / Edit のみ）が動かず、検証と `flow.log` への遷移記録が抜けるため。読むだけのコマンドは素通り |
| `PreToolUse` | `Bash` | `pr-merge-guard.sh` | `gh pr merge` を捕まえ、自動マージ条件を検証。`main`/`develop`/`release/*`/`hotfix/*` 向けは常に `deny`。`feature/*` 向けは CI 全通過・コンフリクトなし・DB 破壊的変更なし（`db-destructive-patterns.txt`）・テストの削除/スキップなし（`test-guard-patterns.txt`）。DB 検査はテストファイル内の文字列（SQL インジェクション対策テストのデータ等）を対象外にする・`--merge` 方式のときだけ `allow` |
| `Stop` | — | `stop-summary.sh` | 直近 10 分以内に dev-flow イベントがあった場合のみ、次ステージとアクションを表示 |

`stage-*-agent` 以外の Agent 呼び出し・`doc/process/` 以外への書き込みでは何もしないため、dev-flow を使わないプロジェクトへの影響はありません。

## 生成されるファイル

- `doc/process/flow.log` — 時系列イベントログ（`event=agent_start|agent_complete|stage_transition|escalation|doc_invalid|auto_merge_allowed|test_stage_write_denied|test_lint_failed|agent_spawned|group_done|remote_verified|stage_gate_denied`）。デバッグと所要時間の把握に使います。git 管理して構いません。

## Slack 通知（opt-in）

`~/.claude/settings.json` の `env` に以下を設定すると、エスカレーション報告が生成されたときに Slack へ投稿します。未設定なら外向き通信は一切行いません。

```json
{
  "env": {
    "SLACK_BOT_TOKEN": "xoxb-...",
    "DEV_FLOW_SLACK_CHANNEL": "#dev-flow-alerts"
  }
}
```

## 動作確認

スモークテストを用意しています。gh はスタブに差し替えるためネットワーク不要で、GNU（Linux）/ BSD（macOS）どちらの coreutils でも通ります。

```bash
bash tests/hooks/run.sh
```

hook を変更したら必ずこれを通してください。`stat` / `date` の方言差は `lib.sh` の `file_mtime` / `iso_to_epoch` に閉じ込めているので、新しい hook でもこれらを経由すること。

stdin に hook JSON を渡して単体で実行することもできます。

```bash
cd /path/to/project   # doc/process/state.json があるディレクトリ
echo '{"tool_name":"Write","tool_input":{"file_path":"doc/process/state.json"},"cwd":"'"$PWD"'"}' \
  | ~/.claude/skills/dev-flow/hooks/state-sync.sh
```

Claude Code 内では `/hooks` で登録状況を確認できます。

## 自動マージ（pr-merge-guard.sh）

想定ブランチ構成: `main ← develop ← feature/xxx ← 作業ブランチ（dev/…, qa/…）`。作業ブランチ → `feature/*` の PR だけが自動マージ対象です。

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `DEV_FLOW_AUTO_MERGE_BASE_PATTERN` | `^feature/` | 自動マージを許可するベースブランチの正規表現。保護ブランチ（main/master/develop/release/hotfix）はこの設定に関係なく常に拒否 |

DB 破壊的変更の判定は PR diff の追加行を `db-destructive-patterns.txt` で grep し、Terraform の DB リソース削除（`resource "aws_db_instance"` 等の削除行）も検出します。誤検知は「自動マージしない」側に倒れるだけなので、パターンは広めです。プロジェクト固有のパターンを足す場合はこのファイルに追記してください。

## 設計上の制約（hook では実現しないもの）

- **requirements → spec の自動続行**: hook はエージェントを起動できず、また要件定義後の人間確認は意図的なゲートです。hook はゲートの存在を念押しするだけに留めています。
- **PR マージの「待機」**: hook は待ちません。オーケストレーターも待たず、マージ待ちが出たら終了して次回 `/dev-flow` で再入します（`session-start.sh` が待ち PR を表示）。
