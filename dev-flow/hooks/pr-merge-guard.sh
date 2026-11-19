#!/bin/bash
# PreToolUse (matcher: Bash)
# `gh pr merge` を捕まえ、自動マージ条件を機械的に検証する。プロンプトの指示では緩和できない。
#
#   判定は 3 種類:
#     常に deny … マージ自体ができない・書式の誤り（Draft / コンフリクト / OPEN でない / 方式・番号の指定ミス）
#     人間の許可で allow … 自動マージの条件を外れるもの（保護ブランチ・対象外のベース・CI 未通過・
#             DB 破壊的変更・テストの削除/スキップ）。直近の人間の発言に「マージ」と「許可」の両方が
#             含まれていれば allow、無ければ deny（human_approved_merge を参照）
#     allow … 下の条件をすべて満たす
#
#   "ask"（確認ダイアログ）を使わない理由: bypassPermissions モードでは hook の ask が確認なしで
#   許可される（2026-11-19 に実測。捨て PR が確認なしでマージされた）。dev-flow は bypass で
#   動かすことが多いので、ask にすると main 向けまで無人でマージされてしまう。
#   許可条件:   すべて満たすこと
#     - ベースが DEV_FLOW_AUTO_MERGE_BASE_PATTERN（既定 ^feature/）に一致し、
#       state.json.implementation_progress.base_branch があればそれと一致
#     - PR が OPEN かつ Draft でない
#     - mergeable == MERGEABLE（コンフリクトなし）
#     - CI チェックがすべて成功（チェックが 1 つも無ければ拒否）
#     - diff に DB の破壊的変更が含まれない（db-destructive-patterns.txt。テストファイル内の文字列は対象外）
#     - diff にテストの削除・スキップ・無効化が含まれない（test-guard-patterns.txt）
#     - マージ方式は --merge のみ（--squash / --rebase / --auto / --admin は拒否）
#     - 1 コマンドにつき 1 PR、番号または URL で明示（-R / --repo の指定は gh pr view / diff にも引き継ぐ）
#
# `gh pr merge` を含まないコマンドでは何もしない。

source "$(dirname "$0")/lib.sh"

[ "$(jqi '.tool_name')" = "Bash" ] || exit 0
CMD_RAW="$(jqi '.tool_input.command // empty')"
# heredoc（<<EOF ... EOF / <<'EOF' / <<-EOF）の本文は「データ」であってコマンドではないので判定から外す。
# ドキュメントやスクリプトに書かれた "gh pr merge" という文字列で誤検知しないため。
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
# 引用符の中の "gh pr merge"（grep のパターン・echo のメッセージ等）もデータなので外す
# （コミットメッセージのように複数行にわたる引用もあるので、行単位の sed ではなく全体で処理する）
CMD="$(printf '%s' "$CMD" | python3 -c '
import re, sys
s = sys.stdin.read()
pat = re.compile(r"gh\s+pr\s+merge")
s = re.sub(r"\x27[^\x27]*\x27|\"(?:[^\"\\]|\\.)*\"", lambda m: "" if pat.search(m.group(0)) else m.group(0), s)
sys.stdout.write(s)
')"
printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge' || exit 0

PATTERNS="$(dirname "$0")/db-destructive-patterns.txt"
TEST_PATTERNS="$(dirname "$0")/test-guard-patterns.txt"
BASE_PATTERN="${DEV_FLOW_AUTO_MERGE_BASE_PATTERN:-^feature/}"
PROTECTED='^(main|master|develop|release/.*|hotfix/.*)$'

# ---- コマンド形式 ----
if [ "$(printf '%s' "$CMD" | grep -oE 'gh[[:space:]]+pr[[:space:]]+merge' | wc -l)" -ne 1 ]; then
  deny "dev-flow hook: gh pr merge は 1 コマンドにつき 1 PR にしてください。"
fi

MERGE_ARGS="$(printf '%s' "$CMD" | sed -E 's/.*gh[[:space:]]+pr[[:space:]]+merge[[:space:]]*//; s/[[:space:]]*(&&|\|\||;|\|).*$//')"

if printf ' %s ' "$MERGE_ARGS" | grep -qE -- '[[:space:]](--squash|-s|--rebase|-r|--auto|--admin|--disable-auto)[[:space:]]'; then
  deny "dev-flow hook: 自動マージで許可される方式は --merge のみです（--squash / --rebase / --auto / --admin は拒否）。"
fi
if ! printf ' %s ' "$MERGE_ARGS" | grep -qE -- '[[:space:]](--merge|-m)[[:space:]]'; then
  deny "dev-flow hook: gh pr merge には --merge を明示してください（マージコミット方式のみ許可）。"
fi

PR=""
REPO=""
next_is_repo=""
for tok in $MERGE_ARGS; do
  if [ -n "$next_is_repo" ]; then REPO="$tok"; next_is_repo=""; continue; fi
  case "$tok" in
    -R | --repo) next_is_repo=1; continue ;;
    --repo=*) REPO="${tok#--repo=}"; continue ;;
    -*) continue ;;
  esac
  [ -z "$PR" ] && PR="$tok"
done
case "$PR" in
  *://*/pull/*)
    # URL 指定なら owner/repo も URL から取る（リポジトリ外の cwd でも gh pr view できるように）
    if [ -z "$REPO" ]; then REPO="${PR#*://*/}"; REPO="${REPO%%/pull/*}"; fi
    PR="${PR##*/pull/}"; PR="${PR%%[^0-9]*}" ;;
esac
GH_REPO_ARGS=()
[ -n "$REPO" ] && GH_REPO_ARGS=(-R "$REPO")

# 人間の明示的な許可があればマージしてよい理由を集める（最後にまとめて ask する）
NEEDS=""
need() { NEEDS="${NEEDS}- ${1}
"; }
if ! printf '%s' "$PR" | grep -qE '^[0-9]+$'; then
  deny "dev-flow hook: マージ対象の PR を番号（または URL）で明示してください。カレントブランチ推定やブランチ名指定は拒否します。"
fi

# ---- PR 情報 ----
command -v gh >/dev/null 2>&1 || deny "dev-flow hook: gh コマンドが見つからないため自動マージ条件を検証できません。"

INFO="$(gh pr view "$PR" ${GH_REPO_ARGS[@]+"${GH_REPO_ARGS[@]}"} --json number,state,isDraft,baseRefName,headRefName,mergeable,statusCheckRollup,url 2>&1)" \
  || deny "dev-flow hook: gh pr view $PR に失敗しました: $INFO"

pv() { printf '%s' "$INFO" | jq -r "$1"; }
PR_STATE="$(pv '.state')"
DRAFT="$(pv '.isDraft')"
BASE="$(pv '.baseRefName')"
HEAD_REF="$(pv '.headRefName')"
MERGEABLE="$(pv '.mergeable')"
URL="$(pv '.url')"

[ "$PR_STATE" = "OPEN" ] || deny "dev-flow hook: PR #$PR は $PR_STATE です（OPEN のみマージ可）。"
[ "$DRAFT" != "true" ] || deny "dev-flow hook: PR #$PR は Draft です。Ready for review にしてから再試行してください。"

# ---- ベースブランチ ----
if printf '%s' "$BASE" | grep -qE "$PROTECTED"; then
  need "ベース '$BASE' は保護ブランチ（main / develop 系）"
elif ! printf '%s' "$BASE" | grep -qE "$BASE_PATTERN"; then
  need "ベース '$BASE' は自動マージ対象（${BASE_PATTERN}）ではない"
fi
if state_valid; then
  EXPECTED_BASE="$(state_get '.implementation_progress.base_branch')"
  if [ -n "$EXPECTED_BASE" ] && [ "$EXPECTED_BASE" != "$BASE" ]; then
    need "ベース '$BASE' が state.json の base_branch '$EXPECTED_BASE' と一致しない"
  fi
fi

# ---- コンフリクト ----
[ "$MERGEABLE" = "MERGEABLE" ] || deny "dev-flow hook: PR #$PR は mergeable=$MERGEABLE です（コンフリクトまたは判定中）。解消後に再試行してください。$URL"

# ---- CI ----
CHECKS="$(pv '
  .statusCheckRollup
  | if length == 0 then "NONE"
    else (
      map(
        if .__typename == "CheckRun" then
          (if .status == "COMPLETED" and ((.conclusion // "") | IN("SUCCESS","NEUTRAL","SKIPPED")) then empty else (.name // "check") + "=" + (if ((.conclusion // "") != "") then .conclusion else (.status // "PENDING") end) end)
        else
          (if .state == "SUCCESS" then empty else (.context // "status") + "=" + (.state // "PENDING") end)
        end
      ) | if length == 0 then "OK" else join(", ") end
    ) end')"
case "$CHECKS" in
  OK) ;;
  NONE) need "CI チェックが 1 つも無い" ;;
  *) need "CI が通っていない: ${CHECKS}" ;;
esac

# ---- DB 破壊的変更 ----
DIFF="$(gh pr diff "$PR" ${GH_REPO_ARGS[@]+"${GH_REPO_ARGS[@]}"} 2>/dev/null)" || deny "dev-flow hook: gh pr diff $PR に失敗しました。"
ADDED="$(printf '%s\n' "$DIFF" | grep -E '^\+[^+]' || true)"
# DB 検査はテストファイル以外の追加行だけを見る（SQL インジェクション対策テストのデータ "'; DROP TABLE x; --" 等で
# 毎回止まるのを避ける）。テスト削除検査と Terraform 検査は従来どおり全 diff を対象にする。
NON_TEST_ADDED="$(printf '%s\n' "$DIFF" | awk '
  /^diff --git / { f = $0; sub(/^diff --git a\/[^ ]* b\//, "", f); skip = is_test(f); next }
  /^\+[^+]/ && !skip { print }
  function is_test(p) {
    if (p ~ /(^|\/)doc\/test-spec\//) return 1
    if (p ~ /(_test\.go|_test\.py|\.test\.(ts|tsx|js|jsx)|\.spec\.(ts|tsx|js|jsx)|Test\.php|_spec\.rb)$/) return 1
    if (p ~ /(^|\/)test_[^\/]*\.py$/) return 1
    if (p ~ /(^|\/)(tests?|__tests__|spec|e2e)\//) return 1
    return 0
  }')"
HITS="$(printf '%s\n' "$NON_TEST_ADDED" | grep -iE -f <(grep -vE '^\s*(#|$)' "$PATTERNS") | head -5 || true)"
# WHERE 句の無い DELETE FROM（全行削除）
DEL_ALL="$(printf '%s\n' "$NON_TEST_ADDED" | grep -iE 'DELETE[[:space:]]+FROM[[:space:]]' | grep -ivE '[[:space:]]WHERE[[:space:]]' | head -5 || true)"
HITS="$(printf '%s\n%s' "$HITS" "$DEL_ALL" | sed '/^$/d')"
TF_DEL="$(printf '%s\n' "$DIFF" | grep -E '^-[^-]' | grep -E 'resource[[:space:]]+"(aws_(db_instance|rds_cluster|rds_cluster_instance|dynamodb_table|elasticache_cluster|elasticache_replication_group|redshift_cluster|docdb_cluster|neptune_cluster)|google_sql_database_instance|azurerm_(mssql|postgresql|mysql)_[a-z_]*server)"' | head -5 || true)"
if [ -n "$HITS" ] || [ -n "$TF_DEL" ]; then
  need "DB の破壊的変更を含む:
$(printf '%s\n%s' "$HITS" "$TF_DEL" | sed '/^$/d' | sed 's/^/    /')"
fi

# ---- テストの削除・スキップ ----
# テストファイルに限らず全 diff を見る（テストヘルパーやテーブルの行削除も拾いたい）。
# 削除行の検出は「同じ行も同じ TC-ID のテスト定義も追加行に無い」ものだけ（移動・名前の変更は許容）。
REMOVED="$(printf '%s\n' "$DIFF" | grep -E '^-[^-]' || true)"
# その場での書き換え: 連続した変更行のかたまり（-/+ の行が続く範囲）で、テスト定義の追加数が削除数以上なら、
# そのかたまりで消えたテスト定義は置き換えとみなす（2026-10 の change の検証で、仕様変更に合わせて
# 「10 件ちょうどは有効」を「20 件ちょうどは有効」に書き換えた TC-ID の無いテストで止まった）。
# 削除だけのかたまり（テストを消して CI を通す）はこれまでどおり止める。TC-ID を持つテストはこの判定に使わない。
REWRITTEN="$(printf '%s\n' "$DIFF" | TEST_PATTERNS="$TEST_PATTERNS" python3 -c '
import os, re, sys
pats = []
for line in open(os.environ["TEST_PATTERNS"], encoding="utf-8"):
    if line.startswith("[removed] "):
        p = line[len("[removed] "):].rstrip("\n")
        pats.append(re.compile(p[2:] if p.startswith("^-") else p))
def is_def(body):
    return any(p.match(body) for p in pats)
block = []
def flush():
    rem = [l for l in block if l.startswith("-") and is_def(l[1:])]
    add = [l for l in block if l.startswith("+") and is_def(l[1:])]
    if rem and len(add) >= len(rem):
        for l in rem:
            # TC-ID を持つテストは TC-ID の照合（下）だけで判定する（別の TC への置き換えを許さない）
            if not re.search(r"tc[-_]?[0-9]+", l, re.I):
                print(l)
for line in sys.stdin.read().split("\n"):
    if (line.startswith("-") and not line.startswith("---")) or (line.startswith("+") and not line.startswith("+++")):
        block.append(line)
    else:
        flush(); block = []
flush()
' 2>/dev/null || true)"
TEST_HITS=""
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  kind="${line%%]*}"; kind="${kind#[}"
  pat="${line#*] }"
  if [ "$kind" = "removed" ]; then
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      # 削除行と同じ内容（先頭の - を + に変えたもの）が追加行にあれば移動とみなす
      body="${hit#-}"
      printf '%s\n' "$ADDED" | grep -qxF -- "+$body" && continue
      printf '%s\n' "$REWRITTEN" | grep -qxF -- "$hit" && continue
      # 同じ TC-ID（tc043 / TC-043 / TC_043）を持つテスト定義が追加行にあれば名前の変更とみなす
      # （2026-10 の change の検証で、期待値が変わった TC のメソッド名を変えただけで止まった）
      tc="$(printf '%s' "$hit" | grep -oiE 'tc[-_]?[0-9]+' | head -1 | tr 'A-Z' 'a-z' | tr -d '_-')"
      if [ -n "$tc" ] && printf '%s\n' "$ADDED" | grep -E -- "^\+${pat#^-}" | tr 'A-Z' 'a-z' | tr -d '_-' | grep -qE "${tc}([^0-9]|$)"; then
        continue
      fi
      TEST_HITS="${TEST_HITS}${hit}
"
    done <<< "$(printf '%s\n' "$REMOVED" | grep -E -- "$pat" || true)"
  else
    h="$(printf '%s\n' "$ADDED" | grep -E -- "$pat" || true)"
    [ -n "$h" ] && TEST_HITS="${TEST_HITS}${h}
"
  fi
done < "$TEST_PATTERNS"
TEST_HITS="$(printf '%s' "$TEST_HITS" | sed '/^$/d' | head -8)"
if [ -n "$TEST_HITS" ]; then
  need "テストの削除・スキップ・無効化を含む（通らないならプロダクションコードを直すのが原則・DocDD）:
$(printf '%s\n' "$TEST_HITS" | sed 's/^/    /')"
fi

if [ -n "$NEEDS" ]; then
  if human_approved_merge; then
    log_flow "event=merge_human_approved pr=$PR base=$BASE head=$HEAD_REF"
    allow "dev-flow hook: PR #${PR}（$HEAD_REF → ${BASE}）は自動マージ条件を満たしていませんが、直近の人間の発言でマージが明示的に許可されているため通します。${URL}
${NEEDS}"
  fi
  log_flow "event=merge_needs_approval pr=$PR base=$BASE head=$HEAD_REF"
  deny "dev-flow hook: PR #${PR}（$HEAD_REF → ${BASE}）は自動マージ条件を満たしていません。${URL}
${NEEDS}人間にマージを依頼するか、人間が「マージを許可する」のように明示的に許可した後で再実行してください（サブエージェントからの実行では許可は効きません）。"
fi

log_flow "event=auto_merge_allowed pr=$PR base=$BASE head=$HEAD_REF"
allow "dev-flow hook: PR #${PR}（$HEAD_REF → ${BASE}）は自動マージ条件（CI 全通過・コンフリクトなし・DB 破壊的変更なし・テスト削除/スキップなし・--merge）を満たしています。"
