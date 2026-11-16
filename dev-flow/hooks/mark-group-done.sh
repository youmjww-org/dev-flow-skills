#!/bin/bash
# implementation STEP H の機械的作業をまとめて行う（hook ではなく、オーケストレーターが Bash から呼ぶ）。
#
#   mark-group-done.sh <N> [PR番号...] [--no-commit]
#
#   1. doc/process/task_checklist.md の「### グループ N」セクションの - [ ] を - [x] に
#   2. 同じタスク文が「全一覧」セクションにもあれば、そちらも - [x] に
#   3. doc/process/state.json の implementation_progress を更新
#        completed_groups に group-N を追加 / active_worktrees から group-N を除去 / pr_numbers.group-N に PR 番号
#   4. group-ledger.py でレビュー結果・完了 JSON の要点を doc/process/metrics/review-ledger.jsonl に追記
#      （失敗しても止めない。警告だけ出す）
#   5. 上記のファイルを 1 コミット（--no-commit で抑止）
#
# 冪等: 2 回実行しても結果は同じ。プロジェクトルートは CLAUDE_PROJECT_DIR かカレント。
set -u
source "$(dirname "$0")/lib.sh" </dev/null

usage() { sed -n '2,12p' "$0" >&2; exit 2; }
[ $# -ge 1 ] || usage
N="$1"; shift
case "$N" in ''|*[!0-9]*) echo "グループ番号は整数で指定してください: $N" >&2; exit 2 ;; esac
COMMIT=true; PRS=()
for a in "$@"; do
  case "$a" in
    --no-commit) COMMIT=false ;;
    ''|*[!0-9]*) echo "PR 番号は整数で指定してください: $a" >&2; exit 2 ;;
    *) PRS+=("$a") ;;
  esac
done
command -v python3 >/dev/null 2>&1 || { echo "python3 が必要です" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "jq が必要です" >&2; exit 2; }

CHECKLIST="$PROJECT_DIR/doc/process/task_checklist.md"
[ -f "$CHECKLIST" ] || { echo "$CHECKLIST がありません" >&2; exit 1; }
[ -f "$STATE" ] || { echo "$STATE がありません" >&2; exit 1; }

# ---- 1, 2: チェックリスト ----
CHANGED="$(python3 - "$CHECKLIST" "$N" <<'PY'
import re, sys
path, n = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
lines = text.split("\n")
# グループ N のセクション範囲（### グループ N ... から次の ### / ## まで）
start = end = None
for i, l in enumerate(lines):
    if start is None and re.match(rf"^###\s+グループ\s*{n}\b", l):
        start = i; continue
    if start is not None and i > start and re.match(r"^(###|##)\s", l):
        end = i; break
if start is None:
    sys.exit(f"「### グループ {n}」セクションが見つかりません")
end = end or len(lines)
tasks, changed = set(), 0
for i in range(start, end):
    m = re.match(r"^(\s*- \[)( |x)(\] )(.*)$", lines[i])
    if not m: continue
    tasks.add(m.group(4).strip())
    if m.group(2) == " ":
        lines[i] = f"{m.group(1)}x{m.group(3)}{m.group(4)}"; changed += 1
# 全一覧セクション: 同じタスク文を [x] に
in_all = False
for i, l in enumerate(lines):
    if re.match(r"^##\s", l):
        in_all = "全一覧" in l
        continue
    if not in_all: continue
    m = re.match(r"^(\s*- \[)( )(\] )(.*)$", l)
    if m and m.group(4).strip() in tasks:
        lines[i] = f"{m.group(1)}x{m.group(3)}{m.group(4)}"; changed += 1
open(path, "w", encoding="utf-8").write("\n".join(lines))
print(changed)
PY
)" || { echo "$CHANGED" >&2; exit 1; }

# ---- 3: state.json ----
PR_JSON="$(printf '%s\n' "${PRS[@]:-}" | sed '/^$/d' | jq -sc 'map(tonumber)')"
TMP="$(mktemp)"
jq --arg g "group-$N" --argjson prs "$PR_JSON" '
  .implementation_progress = (.implementation_progress // {})
  | .implementation_progress.completed_groups = ((.implementation_progress.completed_groups // []) + [$g] | unique)
  | .implementation_progress.active_worktrees = ((.implementation_progress.active_worktrees // []) | map(select(test("(^|[^0-9])" + ($g | sub("group-"; "group-")) + "([^0-9]|$)") | not)))
  | if ($prs | length) > 0 then .implementation_progress.pr_numbers[$g] = $prs else . end
' "$STATE" > "$TMP" && mv "$TMP" "$STATE" || { rm -f "$TMP"; echo "state.json の更新に失敗" >&2; exit 1; }

log_flow "event=group_done group=group-$N checklist_updated=$CHANGED${PRS:+ prs=$(IFS=,; echo "${PRS[*]}")}"

# ---- 4: 台帳 ----
if LEDGER_MSG="$(python3 "$(dirname "$0")/group-ledger.py" "$N" --root "$PROJECT_DIR" 2>&1)"; then :; else
  echo "警告: 台帳に追記できませんでした（グループの完了処理は続けます）: $LEDGER_MSG" >&2
  LEDGER_MSG=""
fi
LEDGER_FILE="doc/process/metrics/review-ledger.jsonl"

# ---- 5: コミット ----
if [ "$COMMIT" = true ]; then
  (cd "$PROJECT_DIR" && { [ ! -f "$LEDGER_FILE" ] || git add "$LEDGER_FILE"; } \
    && git add doc/process/task_checklist.md doc/process/state.json \
    && git commit -q -m "chore: グループ $N 完了（チェックリスト・state.json 更新${PRS:+、PR #$(IFS='#'; echo "${PRS[*]}" | sed 's/#/, #/g')}）" ) \
    || { echo "コミットに失敗（変更が無い可能性）" >&2; exit 1; }
fi
echo "グループ $N 完了: チェックリスト ${CHANGED} 行更新、state.json 更新${PRS:+、PR ${PRS[*]}}$( [ "$COMMIT" = true ] && echo '、コミット済み' )"
[ -z "$LEDGER_MSG" ] || echo "$LEDGER_MSG"
