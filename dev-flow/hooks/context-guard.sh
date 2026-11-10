#!/bin/bash
# PostToolUse (matcher: Bash|Read|Agent|SendMessage|TaskStop)
# オーケストレーター（メインの会話）の文脈の大きさを transcript の直近の usage から測り、
# doc/process/.context_status に書く。閾値を超えたときだけ Claude に知らせる。
#
# 文脈はターンごとに丸ごと読み直されるので、利用量は「文脈の大きさ × ターン数」で効く。
# 2026-10〜11 の photo-incident-triage の run では、メインの文脈が中央値 27〜53 万・最大 97 万トークンまで
# 伸び、メイン 4 セッションだけで全体の約 3 割を使っていた。
#
# 閾値: DEV_FLOW_CONTEXT_LIMIT（既定 350000 トークン）。
# サブエージェントの呼び出し（agent_id がある / transcript が subagents/ 配下）では何もしない。

source "$(dirname "$0")/lib.sh"

state_exists || exit 0
[ -n "$(jqi '.agent_id // empty')" ] && exit 0
TRANSCRIPT="$(jqi '.transcript_path // empty')"
[ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ] || exit 0
case "$TRANSCRIPT" in */subagents/*) exit 0 ;; esac

STAGE="$(next_stage)"
case "$STAGE" in "" | completed) exit 0 ;; esac

# 直近の assistant メッセージの usage（input + cache_read + cache_creation）が今の文脈の大きさ
TOKENS="$(tail -c 3000000 "$TRANSCRIPT" 2>/dev/null \
  | grep '"type":"assistant"' | grep '"usage"' | tail -n 1 \
  | jq -r '(.message.usage // {}) | ((.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0))' 2>/dev/null)"
case "$TOKENS" in '' | *[!0-9]*) exit 0 ;; esac

LIMIT="${DEV_FLOW_CONTEXT_LIMIT:-350000}"
STATUS_FILE="$PROCESS_DIR/.context_status"
if [ ! -f "$STATUS_FILE" ] && git -C "$PROCESS_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && ! grep -qx '.context_status' "$PROCESS_DIR/.gitignore" 2>/dev/null; then
  printf '.context_status\n' >> "$PROCESS_DIR/.gitignore"
fi

PREV_OVER="$(sed -n 's/^over=//p' "$STATUS_FILE" 2>/dev/null | head -n 1)"
OVER=0
[ "$TOKENS" -ge "$LIMIT" ] && OVER=1
printf 'over=%s\ntokens=%s\nlimit=%s\nstage=%s\nupdated=%s\n' \
  "$OVER" "$TOKENS" "$LIMIT" "$STAGE" "$(TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S%z')" > "$STATUS_FILE"

# 閾値を下から上へ越えたときだけ知らせる（毎回は出さない）
[ "$OVER" = 1 ] && [ "${PREV_OVER:-0}" != 1 ] || exit 0
log_flow "event=context_high tokens=$TOKENS limit=$LIMIT stage=$STAGE"
post_context "dev-flow hook: オーケストレーターの文脈が約 $((TOKENS / 1000))k トークンになり、閾値 $((LIMIT / 1000))k を超えました。以後のターンはすべてこの大きさで読み直されます。implementation 中なら新しいグループを起動せず、動いているグループの完了を待ってバッチを締めてください（dev-flow-implementation/SKILL.md「文脈によるバッチの早期締め」）。consistency から implementation へ移るところなら、implementation を始めずに止まって /clear を依頼してください（dev-flow/SKILL.md STEP 5）。"
