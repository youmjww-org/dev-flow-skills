#!/bin/bash
# 状況報告・ステージ移行の前に「実際の状態」を確認する（hook ではなく、オーケストレーター / 各ステージが Bash から呼ぶ）。
#
#   verify-remote-state.sh [--base BRANCH] [--expect-merged] [PR番号...]
#
#   1. git fetch origin して、現在のブランチが origin より遅れていないか（ahead / behind）
#   2. --base（省略時は state.json の implementation_progress.base_branch → base_branch）が現在のブランチと
#      違えば、origin/<base> のうち HEAD に取り込まれていないコミット数（参考表示）
#   3. PR（省略時は state.json の implementation_progress.pr_numbers 全部）の state と CI チェックの結果
#
# 出力は 1 行 1 項目で、先頭が OK / NG / INFO。状況報告ではこの出力をそのまま引用する（推測で「CI 実行中」「全件パス」と書かない）。
# 終了コード: 0 = NG なし / 1 = NG あり（behind・CI 失敗・CI 未完了・--expect-merged で未マージ）/ 2 = 使い方の誤り
# マージ済み PR の CI 失敗（人間が判断してマージしたもの）は NG ではなく INFO にする。
# flow.log に event=remote_verified result=ok|ng を記録する（state-sync.sh が implementation → test の移行時に確認する）。
set -u
source "$(dirname "$0")/lib.sh" </dev/null

usage() { sed -n '2,15p' "$0" >&2; exit 2; }
BASE=""; EXPECT_MERGED=false; PRS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --base) [ $# -ge 2 ] || usage; BASE="$2"; shift ;;
    --expect-merged) EXPECT_MERGED=true ;;
    -h|--help) usage ;;
    ''|*[!0-9]*) echo "不明な引数: $1" >&2; usage ;;
    *) PRS+=("$1") ;;
  esac
  shift
done

cd "$PROJECT_DIR" || exit 2
git rev-parse --git-dir >/dev/null 2>&1 || { echo "NG git リポジトリではありません: $PROJECT_DIR"; exit 1; }

NG=0
ng()   { NG=$((NG + 1)); echo "NG   $*"; }
ok()   { echo "OK   $*"; }
info() { echo "INFO $*"; }

# ---- 1. 現在のブランチと origin ----
if git fetch -q origin 2>/dev/null; then
  ok "git fetch origin"
else
  ng "git fetch origin に失敗（ネットワーク / 認証）。以下の ahead/behind は古い可能性がある"
fi

CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if git rev-parse -q --verify "refs/remotes/origin/$CUR" >/dev/null; then
  AHEAD="$(git rev-list --count "origin/$CUR..HEAD")"
  BEHIND="$(git rev-list --count "HEAD..origin/$CUR")"
  if [ "$BEHIND" -gt 0 ]; then
    ng "branch $CUR: origin/$CUR より $BEHIND コミット遅れている（ahead=${AHEAD}）。git pull --ff-only してからテスト・報告する"
  else
    ok "branch $CUR: origin/$CUR と同期（ahead=$AHEAD behind=0）"
  fi
else
  info "branch $CUR: origin/$CUR がありません（未 push）"
fi

# ---- 2. ベースブランチ ----
if [ -z "$BASE" ] && state_valid; then
  BASE="$(state_get '.implementation_progress.base_branch // .base_branch // empty')"
fi
if [ -n "$BASE" ] && [ "$BASE" != "$CUR" ]; then
  if git rev-parse -q --verify "refs/remotes/origin/$BASE" >/dev/null; then
    info "base origin/$BASE: HEAD に未取り込みのコミット $(git rev-list --count "HEAD..origin/$BASE") 件"
  else
    info "base origin/$BASE がありません"
  fi
fi

# ---- 3. PR と CI ----
if [ ${#PRS[@]} -eq 0 ] && state_valid; then
  while read -r n; do [ -n "$n" ] && PRS+=("$n"); done < <(jq -r '
    (.implementation_progress.pr_numbers // {}) | to_entries[] | .value
    | if type == "array" then .[] else . end | select(. != null)' "$STATE" 2>/dev/null)
fi
if [ ${#PRS[@]} -gt 0 ]; then
  if ! command -v gh >/dev/null 2>&1; then
    ng "gh がありません。PR ${PRS[*]} の状態を確認できない"
  fi
fi
if [ ${#PRS[@]} -gt 0 ] && command -v gh >/dev/null 2>&1; then
  for n in "${PRS[@]}"; do
    J="$(gh pr view "$n" --json number,state,isDraft,mergeable,statusCheckRollup,url 2>/dev/null)" || { ng "PR #$n: 取得に失敗"; continue; }
    STATE_="$(printf '%s' "$J" | jq -r '.state')"
    # CheckRun は status / conclusion、StatusContext は state を持つ
    CHECKS="$(printf '%s' "$J" | jq -r '
      [(.statusCheckRollup // [])[] |
        {name: (.name // .context // "?"),
         r: (if .__typename == "StatusContext" then .state
             elif (.status // "COMPLETED") != "COMPLETED" then "PENDING"
             else (.conclusion // "PENDING") end)}]
      | {total: length,
         ok: map(select(.r == "SUCCESS" or .r == "NEUTRAL" or .r == "SKIPPED")) | length,
         pending: map(select(.r == "PENDING" or .r == "QUEUED" or .r == "IN_PROGRESS" or .r == "EXPECTED" or .r == "WAITING")) | map(.name) | join(","),
         failed: map(select((.r == "SUCCESS" or .r == "NEUTRAL" or .r == "SKIPPED" or .r == "PENDING" or .r == "QUEUED" or .r == "IN_PROGRESS" or .r == "EXPECTED" or .r == "WAITING") | not)) | map("\(.name)=\(.r)") | join(",")}
      | "\(.total)|\(.ok)|\(.pending)|\(.failed)"')"
    IFS="|" read -r TOTAL OKN PENDING FAILED <<< "$CHECKS"
    URL="$(printf '%s' "$J" | jq -r '.url // empty')"
    DESC="PR #$n: state=$STATE_ checks=$OKN/$TOTAL 成功 ${URL}"
    if [ -n "$FAILED" ] && [ "$STATE_" = "MERGED" ]; then
      # 自動マージ（pr-merge-guard）は CI 失敗の PR を通さないので、これは人間の判断でマージされたもの。
      # マージ済み PR のチェック結果は後から変わらないため NG にはしない（統合後のコードは test ステージで全件実行する）。
      info "$DESC マージ済みだが PR 単体の CI は失敗: ${FAILED}（人間がマージした。統合後のテスト結果で判断する）"
    elif [ -n "$FAILED" ]; then
      ng "$DESC 失敗: ${FAILED}（gh run view --log-failed で原因を見る）"
    elif [ -n "$PENDING" ] && [ "$STATE_" != "MERGED" ]; then
      ng "$DESC 未完了: ${PENDING}（完了を待つ。推測で結果を書かない）"
    elif [ "$EXPECT_MERGED" = true ] && [ "$STATE_" != "MERGED" ]; then
      ng "$DESC 未マージ"
    else
      ok "$DESC"
    fi
  done
fi

if [ "$NG" -eq 0 ]; then
  log_flow "event=remote_verified result=ok branch=$CUR prs=${PRS[*]:-none}"
  echo "summary: NG 0"
  exit 0
fi
log_flow "event=remote_verified result=ng branch=$CUR ng=$NG prs=${PRS[*]:-none}"
echo "summary: NG $NG"
exit 1
