#!/bin/bash
# dev-flow hooks 共通ライブラリ
# 各 hook スクリプトから `source "$(dirname "$0")/lib.sh"` で読み込む。
# stdin の hook JSON を $INPUT に保持し、state.json / task_checklist.md / flow.log のパスを解決する。

set -u

INPUT="$(cat 2>/dev/null || true)"

# stdin JSON からフィールドを取り出す（存在しなければ空文字）
jqi() {
  printf '%s' "$INPUT" | jq -r "$@" 2>/dev/null || true
}

# hook 入力の cwd を優先する。CLAUDE_PROJECT_DIR はセッション起動時のディレクトリのまま
# /cd で移動しても変わらないため、先に見ると移動前のプロジェクトの state.json を参照してしまう。
# cwd の無い呼び出し（mark-group-done.sh 等の Bash 実行）では CLAUDE_PROJECT_DIR → $PWD の順に使う。
PROJECT_DIR="$(jqi '.cwd // empty')"
: "${PROJECT_DIR:=${CLAUDE_PROJECT_DIR:-$PWD}}"

PROCESS_DIR="$PROJECT_DIR/doc/process"
STATE="$PROCESS_DIR/state.json"   # 注意: 各 hook では STATE を再代入しないこと（PR の state 等は PR_STATE などを使う）
CHECKLIST="$PROCESS_DIR/task_checklist.md"
FLOW_LOG="$PROCESS_DIR/flow.log"

# ---------------------------------------------------------------------------
# state.json ヘルパー
# ---------------------------------------------------------------------------

state_exists() { [ -f "$STATE" ]; }

state_valid() { state_exists && jq -e . "$STATE" >/dev/null 2>&1; }

# state.json のフィールドを取得（不正 JSON・欠損時は空文字）
state_get() {
  state_valid || return 0
  jq -r "$1 // empty" "$STATE" 2>/dev/null || true
}

# ステージ定義
#   順序: requirements → spec → consistency → implementation → test → compliance → completed
#   plan_repair は implementation 内部の閉じたサイクル（順序上は consistency と同格）
#
# state.json の next_stage は「次に実行するステージ」。旧スキーマ（current_phase = 完了フェーズ）は
# legacy_stage で読み替える。

# 旧 current_phase 値 → next_stage 値
legacy_stage() {
  case "${1:-}" in
    phase_2) echo spec ;;
    phase_4) echo consistency ;;
    phase_4_5) echo implementation ;;
    phase_4_5_mini) echo plan_repair ;;
    phase_5) echo test ;;
    phase_6) echo compliance ;;
    phase_8 | done) echo completed ;;
    *) echo "$1" ;;
  esac
}

# state.json から次に実行するステージを取得（旧 current_phase も読み替える。無ければ空文字 = requirements）
next_stage() {
  local v
  v="$(state_get '.next_stage')"
  [ -n "$v" ] || v="$(legacy_stage "$(state_get '.current_phase')")"
  printf '%s\n' "$v"
}

# next_stage → 進捗ランク。task_checklist.md のステージ進捗行 N は rank >= N で [x] になる。
#   1: requirements 完了  2: spec 完了  3: consistency 完了
#   4: implementation 完了  5: test 完了  6: compliance 完了
stage_rank() {
  case "${1:-}" in
    spec) echo 1 ;;
    consistency) echo 2 ;;
    implementation | plan_repair) echo 3 ;;
    test) echo 4 ;;
    compliance) echo 5 ;;
    completed) echo 6 ;;
    *) echo 0 ;;
  esac
}

# next_stage → 表示名（番号付き）
stage_label() {
  case "${1:-}" in
    "" | null | requirements) echo "Stage 1/6 requirements: 要件定義" ;;
    spec) echo "Stage 2/6 spec: 仕様書生成" ;;
    consistency) echo "Stage 3/6 consistency: 整合性チェック" ;;
    plan_repair) echo "Stage 3/6 plan_repair: 計画修正（implementation 内サイクル）" ;;
    implementation) echo "Stage 4/6 implementation: 並列実装" ;;
    test) echo "Stage 5/6 test: テスト実行" ;;
    compliance) echo "Stage 6/6 compliance: 準拠チェック・完了報告" ;;
    completed) echo "完了（次の変更は /dev-flow --kind=change|fix|refactor で開始）" ;;
    *) echo "不明（$1）" ;;
  esac
}

# next_stage → オーケストレーターが起動するべきエージェント名
expected_agent_for_stage() {
  case "${1:-}" in
    "" | null | requirements) echo "stage-requirements-agent" ;;
    spec) echo "stage-spec-agent" ;;
    consistency) echo "stage-consistency-agent" ;;
    plan_repair) echo "stage-plan-repair-agent" ;;
    implementation) echo "stage-implementation-agent" ;;
    test) echo "stage-test-agent" ;;
    compliance) echo "stage-compliance-agent" ;;
    *) echo "" ;;
  esac
}

# エージェント名 → 下流スキルファイル
skill_file_for_agent() {
  local base="$HOME/.claude/skills"
  case "${1:-}" in
    stage-bootstrap-agent) echo "$base/dev-flow-bootstrap/SKILL.md" ;;
    stage-requirements-agent) echo "$base/dev-flow-requirements/SKILL.md" ;;
    stage-spec-agent) echo "$base/dev-flow-spec/SKILL.md" ;;
    stage-consistency-agent | stage-plan-repair-agent) echo "$base/dev-flow-consistency/SKILL.md" ;;
    stage-implementation-agent) echo "$base/dev-flow-implementation/SKILL.md" ;;
    stage-test-agent) echo "$base/dev-flow-test/SKILL.md" ;;
    stage-compliance-agent) echo "$base/dev-flow-compliance/SKILL.md" ;;
    *) echo "" ;;
  esac
}

# ---------------------------------------------------------------------------
# テストファイル判定（test-stage-guard / test-lint で共有）
# ---------------------------------------------------------------------------

# 引数のパス（相対でも絶対でも可）がテストコードまたはテスト定義書なら 0
is_test_file() {
  case "$1" in
    */doc/test-spec/*|doc/test-spec/*) return 0 ;;
    *_test.go|*_test.py|*/test_*.py|test_*.py|*.test.ts|*.test.tsx|*.test.js|*.test.jsx|*.spec.ts|*.spec.tsx|*.spec.js|*.spec.jsx|*Test.php|*_spec.rb|*.bats|*_test.sh|*/test_*.sh|test_*.sh) return 0 ;;
    */tests/*|tests/*|*/test/*|test/*|*/__tests__/*|__tests__/*|*/spec/*|spec/*|*/e2e/*|e2e/*) return 0 ;;
  esac
  return 1
}

# テストコード（テスト定義書は除く）なら 0
is_test_code() {
  case "$1" in */doc/test-spec/*|doc/test-spec/*) return 1 ;; esac
  is_test_file "$1"
}

# ---------------------------------------------------------------------------
# task_checklist.md 同期
# ---------------------------------------------------------------------------

# 「## ステージ進捗」セクション内の `- [ ] N. <stage>:` 行を next_stage に合わせて [x]/[ ] に揃える。
# 前進・巻き戻し（compliance の仕様変更フローで consistency に戻す等）の両方に対応する。
sync_checklist() {
  local stage="$1"
  [ -f "$CHECKLIST" ] || return 0
  local rank
  rank="$(stage_rank "$stage")"

  local tmp
  tmp="$(mktemp)"
  awk -v rank="$rank" '
    /^## / { in_progress = ($0 ~ /^## ステージ進捗/) }
    in_progress && /^- \[[ x]\] [1-6]\. / {
      line = 0
      if ($0 ~ /requirements/) line = 1
      else if ($0 ~ /spec/) line = 2
      else if ($0 ~ /consistency/) line = 3
      else if ($0 ~ /implementation/) line = 4
      else if ($0 ~ /test/) line = 5
      else if ($0 ~ /compliance/) line = 6
      if (line > 0) {
        mark = (rank >= line) ? "[x]" : "[ ]"
        sub(/^- \[[ x]\]/, "- " mark)
      }
    }
    { print }
  ' "$CHECKLIST" > "$tmp" && mv "$tmp" "$CHECKLIST"
  rm -f "$tmp" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# flow.log
# ---------------------------------------------------------------------------

# 時系列ログに 1 行追記。形式: `2026-09-03T19:43:00+0900 event=... key=value ...`
log_flow() {
  [ -d "$PROCESS_DIR" ] || return 0
  printf '%s %s\n' "$(TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$FLOW_LOG"
}

# flow.log に最後に記録された stage=... の値
last_logged_stage() {
  [ -f "$FLOW_LOG" ] || return 0
  grep -o 'stage=[^ ]*' "$FLOW_LOG" 2>/dev/null | tail -1 | cut -d= -f2
}

# flow.log の最終更新が N 秒以内か
flow_log_recent() {
  local within="${1:-600}"
  [ -f "$FLOW_LOG" ] || return 1
  local mtime now
  mtime="$(file_mtime "$FLOW_LOG")"
  now="$(date +%s)"
  [ $((now - mtime)) -le "$within" ]
}

# ---------------------------------------------------------------------------
# GNU / BSD 両対応ヘルパー
# ---------------------------------------------------------------------------

# ファイルの最終更新時刻（epoch 秒）。取得できなければ 0。
file_mtime() {
  local f="$1" m
  m="$(stat -c %Y "$f" 2>/dev/null)" \
    || m="$(stat -f %m "$f" 2>/dev/null)" \
    || m=0
  printf '%s\n' "${m:-0}"
}

# log_flow が書く ISO8601（例: 2026-09-03T19:43:00+0900）を epoch 秒に変換。変換できなければ空文字。
iso_to_epoch() {
  local ts="$1" e
  e="$(date -d "$ts" +%s 2>/dev/null)" \
    || e="$(date -j -f '%Y-%m-%dT%H:%M:%S%z' "$ts" +%s 2>/dev/null)" \
    || e=""
  printf '%s\n' "$e"
}

# ---------------------------------------------------------------------------
# Slack 通知（opt-in）
# ---------------------------------------------------------------------------

# DEV_FLOW_SLACK_CHANNEL と SLACK_BOT_TOKEN が両方設定されている場合のみ送信する。
# 未設定なら何もしない（外向き通信は明示的に有効化した環境に限定する）。
slack_notify() {
  local text="$1"
  [ -n "${DEV_FLOW_SLACK_CHANNEL:-}" ] || return 0
  [ -n "${SLACK_BOT_TOKEN:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0

  local payload
  payload="$(jq -n --arg c "$DEV_FLOW_SLACK_CHANNEL" --arg t "$text" '{channel: $c, text: $t, unfurl_links: false}')"
  curl -sS -m 10 -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H 'Content-type: application/json; charset=utf-8' \
    -d "$payload" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# 出力ヘルパー
# ---------------------------------------------------------------------------

# PreToolUse: 実行を拒否して理由を Claude に返す
deny() {
  jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  exit 0
}

# PreToolUse: 条件検証済みとして許可し、理由を Claude に返す
allow() {
  jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "allow", permissionDecisionReason: $r}}'
  exit 0
}

# PreToolUse: 人間に確認を求める
ask() {
  jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $r}}'
  exit 0
}

# PostToolUse: Claude に追加コンテキストを返す
post_context() {
  jq -n --arg c "$1" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $c}}'
  exit 0
}
