#!/bin/bash
# PreToolUse (matcher: Bash)
# doc/process/state.json を Bash（リダイレクト・mv・cp・tee・sed -i・スクリプトの open(..., "w")）で書き換えるのを止める。
# state.json の検証・flow.log への遷移記録・task_checklist.md の同期は state-sync.sh（PostToolUse の Write|Edit）が行うので、
# Bash で書くとそれらがすべて抜ける（2026-10 の通し検証で quality のオーケストレーターが jq + mv で書き、遷移が記録されなかった）。
# 読むだけのコマンド（jq . state.json、cat 等）と mark-group-done.sh は対象外。
# state.json の無いプロジェクトでは何もしない。

source "$(dirname "$0")/lib.sh"

[ "$(jqi '.tool_name')" = "Bash" ] || exit 0
state_exists || exit 0
CMD_RAW="$(jqi '.tool_input.command // empty')"
case "$CMD_RAW" in *state.json*) ;; *) exit 0 ;; esac

# heredoc の本文（ドキュメント・コミットメッセージ等）は判定から外す。ただし本文がスクリプトとして
# 実行される場合（python3 - <<EOF）は open(..., "w") を見るため、本文も別に残す。
CMD="$(printf '%s\n' "$CMD_RAW" | awk '
  term != "" { if ($0 == term || $0 == "\t" term) { term = "" }; next }
  {
    line = $0
    if (match(line, /<<-?[[:space:]]*["'"'"']?[A-Za-z_][A-Za-z0-9_]*["'"'"']?/)) {
      tag = substr(line, RSTART, RLENGTH)
      sub(/^<<-?[[:space:]]*/, "", tag); gsub(/["'"'"']/, "", tag)
      term = tag
    }
    print line
  }')"

T='[^;&|]*state\.json'           # 同じ単純コマンド内の state.json
hit=""
printf '%s' "$CMD" | grep -qE ">>?[[:space:]]*[\"']?${T}" && hit="リダイレクト"
[ -z "$hit" ] && printf '%s' "$CMD" | grep -qE "(^|[;&|[:space:]])(mv|cp|install)[[:space:]]${T}[\"']?[[:space:]]*($|[;&|)])" && hit="mv / cp"
[ -z "$hit" ] && printf '%s' "$CMD" | grep -qE "(^|[;&|[:space:]])(tee|sponge)[[:space:]]${T}" && hit="tee"
[ -z "$hit" ] && printf '%s' "$CMD" | grep -qE "(^|[;&|[:space:]])sed[[:space:]]+(-[a-zA-Z]*i|--in-place)${T}" && hit="sed -i"
# スクリプトは、パスを変数に入れてから open(p, "w") する書き方もある（2026-10 の change の検証で
# stage-requirements-agent が p='doc/process/state.json' → open(p,'w') で書いた）。state.json を含む
# スクリプトに書き込み操作があれば止める。見るのはインタプリタに渡すスクリプトの本文
# （python3 - <<EOF の heredoc と -c / -e の引数）だけ。コミットメッセージ等に同じ文字列があっても止めない。
WRITE_OPS="open\([^)]*,[[:space:]]*[\"'][wa]|\.write_text\(|json\.dump\(|writeFileSync|writeFile\(|File\.write"
if [ -z "$hit" ]; then
  SCRIPTS="$(printf '%s' "$CMD_RAW" | python3 -c '
import re, sys
s = sys.stdin.read()
interp = r"(?:^|[\s;&|(])(?:python3?|node|ruby|perl)\b"
for m in re.finditer(interp + r"[^\n]*?<<-?\s*[\"\x27]?(\w+)[\"\x27]?[^\n]*\n(.*?)\n[ \t]*\1[ \t]*(?:\n|$)", s, re.S):
    print(m.group(2))
for m in re.finditer(interp + r"(?:\s+-\w+)*\s+-[ce]\s+(\x27[^\x27]*\x27|\"(?:[^\"\\]|\\.)*\")", s):
    print(m.group(1))
' 2>/dev/null)"
  if printf '%s' "$SCRIPTS" | grep -q 'state\.json' && printf '%s' "$SCRIPTS" | grep -qE "$WRITE_OPS"; then
    hit="スクリプトからの書き込み"
  fi
fi
[ -n "$hit" ] || exit 0

log_flow "event=state_bash_write_denied via=$(printf '%s' "$hit" | tr ' ' '_')"
deny "dev-flow hook: state.json を Bash（${hit}）で書き換えないでください。Read してから Write / Edit ツールで書き換えてください。Bash で書くと state-sync.sh が動かず、値の検証・flow.log へのステージ遷移の記録・task_checklist.md の同期が抜けます（jq で値を確かめる・組み立てるのは構いません。書き込みだけ Write / Edit で行う）。"
