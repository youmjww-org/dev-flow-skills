#!/bin/bash
# implementer の完了 JSON（{RESULT_FILE}）が STEP C の条件を満たすかを確かめ、要点だけを出す。
#   使い方: check-result.sh <dev|qa> <完了 JSON のパス>
#   出力: 1 行目に status と要点、続いて OK / NG <理由> の行。NG が 1 つでもあれば終了コード 1。
#
# オーケストレーターが完了 JSON を丸ごと読むと、その全文が以後の会話で毎回読み直される。
# 条件の判定はここで機械的に行い、オーケストレーターには判定結果だけを渡す。
set -euo pipefail

role="${1:?role (dev|qa) を指定してください}"
file="${2:?完了 JSON のパスを指定してください}"
case "$role" in dev|qa) ;; *) echo "check-result: role は dev か qa: $role" >&2; exit 2 ;; esac
if [ ! -f "$file" ]; then echo "NG 完了 JSON がありません: $file"; exit 1; fi
if ! jq -e . "$file" >/dev/null 2>&1; then echo "NG JSON として読めません: $file"; exit 1; fi

jq -r '"status=\(.status) confidence=\(.confidence // "-") commits=\(.result.commits // [] | length) uncertainty=\(.uncertainty_points // [] | length) doc_issues=\(.doc_issues // [] | length)"' "$file"

status="$(jq -r '.status' "$file")"
if [ "$status" != "completed" ]; then
  jq -r '"NG status=\(.status) blocker_type=\(.blocker_type // "-") reason=\(.reason // "-")"' "$file"
  exit 1
fi

ng=0
check() { # check <jq の条件> <NG のときの理由>
  if jq -e "$1" "$file" >/dev/null 2>&1; then echo "OK $2"; else echo "NG $2"; ng=1; fi
}

check '.result.lint.exit_code == 0' "lint の終了コードが 0"
if [ "$role" = "dev" ]; then
  check '.result.unit_tests.failed == 0' "ユニットテストの失敗が 0"
  check '(.result.coverage.changed_functions_below_threshold // null) == []' "分岐カバレッジが閾値未満の関数が無い"
  check '(.result.mutation | type == "array" and length > 0) and ([.result.mutation[] | select(.killed == false)] | length == 0)' "ミューテーション確認があり、生き残った変異が無い"
else
  check '.result.tests | has("failed")' "仕様テストの実行結果がある（失敗は Dev 実装待ちなら正常）"
  # 統合検証（STEP C.5）の後だけ入る
  if jq -e '.result.integration' "$file" >/dev/null 2>&1; then
    check '.result.integration.failed == 0' "統合検証の失敗が 0"
    check '([.result.mutation[]? | select(.killed == false)] | length == 0)' "統合検証のミューテーション確認で生き残った変異が無い"
  fi
fi
exit "$ng"
