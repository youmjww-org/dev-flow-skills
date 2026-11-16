#!/bin/bash
# 修正ループで「同じ部品の迂回」が続いているか（もぐら叩き）を確かめる。implementation STEP D から呼ぶ。
#   使い方: repeat-bypass.sh <前回の REVIEW_FILE> <今回の REVIEW_FILE>
#   出力: 前回と今回の両方に、レビュアーが実行して確かめた（verified_by_execution: true）blocker / major があり、
#         rule か file が同じものを「REPEAT rule=… file=…」で 1 行ずつ出す
#   終了コード: 0 = 続いている（方式の見直しを頼む）/ 1 = 続いていない / 2 = 使い方の誤り・読めない
#
# 2026-11 の photo-incident-triage では、SQL の関数の許可リストを構文木で検査する方式に対して、
# 迂回の指摘 → その入力だけを塞ぐ修正 → 別の迂回、が r1〜r5 まで続いた（人間が「もぐら叩き」と判断して方式を変えた）。
# 2 回続いた時点で、入力を塞ぐ修正ではなく方式の見直しを頼めば、往復と人間の判断を減らせる。
set -uo pipefail
prev="${1:-}" cur="${2:-}"
[ -n "$prev" ] && [ -n "$cur" ] || { sed -n '2,8p' "$0" >&2; exit 2; }
for f in "$prev" "$cur"; do
  jq -e . "$f" >/dev/null 2>&1 || { echo "読めません: $f" >&2; exit 2; }
done
out="$(jq -rn --slurpfile p "$prev" --slurpfile c "$cur" '
  def serious(x): [x[0].findings[]? | select((.severity == "blocker" or .severity == "major") and .verified_by_execution == true)];
  serious($p) as $a | serious($c) as $b
  | [ $b[] as $y | select(any($a[]; .rule == $y.rule or ((.file // "") != "" and .file == $y.file)))
      | "REPEAT rule=\($y.rule) file=\($y.file // "-")" ] | unique | .[]')"
[ -n "$out" ] || exit 1
printf '%s\n' "$out"
