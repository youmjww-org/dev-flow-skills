#!/bin/bash
# dev-flow/codex/review.sh のテスト
#   codex は DEV_FLOW_CODEX_BIN で tests/codex/fake-codex に差し替えるので、LLM もネットワークも使わない。
#
# 使い方: bash tests/codex/run.sh
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REVIEW="$ROOT/dev-flow/codex/review.sh"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m %s\n         %s\n' "$1" "${2:-}"; }
assert_eq() { [ "$2" = "$3" ] && ok "$1" || fail "$1" "expected: $3 / got: $2"; }
assert_contains() {
  case "$2" in *"$3"*) ok "$1" ;; *) fail "$1" "expected to contain: $3 / got: $(printf '%s' "$2" | head -c 300)" ;; esac
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/dev-flow-codex-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
export TMPDIR="$WORK/tmp"; mkdir -p "$TMPDIR"

# codex はスタブに差し替える（実機の codex の状態に左右されない）
export DEV_FLOW_CODEX_BIN="$ROOT/tests/codex/fake-codex"
export FAKE_CODEX_ARGS="$WORK/args"
unset DEV_FLOW_CODEX DEV_FLOW_CODEX_MODEL DEV_FLOW_CODEX_EFFORT

new_repo() {
  local dir="$WORK/$1"
  mkdir -p "$dir" && git -C "$dir" init -q
  printf 'original\n' > "$dir/a.txt"
  git -C "$dir" add a.txt
  git -C "$dir" -c user.email=t@example.com -c user.name=t commit -qm init
  printf '%s\n' "$dir"
}

echo "== available"
out="$(DEV_FLOW_CODEX=off "$REVIEW" available)"; assert_eq "DEV_FLOW_CODEX=off なら 3" "$?" 3
assert_contains "off の理由を出す" "$out" "DEV_FLOW_CODEX=off"
out="$(FAKE_CODEX_LOGIN=1 "$REVIEW" available)"; assert_eq "未ログインなら 3" "$?" 3
assert_contains "未ログインの理由を出す" "$out" "ログインしていない"
out="$("$REVIEW" available)"; assert_eq "ログイン済みなら 0" "$?" 0
out="$(DEV_FLOW_CODEX_BIN="$WORK/no-such-codex" "$REVIEW" available)"; assert_eq "codex が無ければ 3" "$?" 3
assert_contains "codex が無い理由を出す" "$out" "見つからない"

echo "== impl: 正常終了と worktree の後始末"
repo="$(new_repo impl)"
printf 'dirty before\n' > "$repo/pre.txt"           # 開始前から汚れているファイル（触らない）
printf 'prompt body\n' > "$WORK/prompt.md"
export FAKE_CODEX_ACTION='echo mutated > a.txt; echo junk > junk.txt; echo changed >> pre.txt'
export FAKE_CODEX_REPLY='{"reviewer":"dev-app-group-1","status":"approved","findings":[{"severity":"major","rule":"maint/dup","file":"a.txt","line":1,"problem":"p","fix":"f"}],"checked_rules":[],"uncertainty_verdicts":[]}'
job="$("$REVIEW" start impl "$repo" "$WORK/prompt.md" "$WORK/out/impl.json")"; assert_eq "start は 0" "$?" 0
out="$("$REVIEW" wait "$job" 30)"; assert_eq "wait は 0" "$?" 0
assert_contains "完了行を出す" "$out" "done: status=changes_requested"
assert_eq "major があれば changes_requested に直す" "$(jq -r .status "$WORK/out/impl.json")" "changes_requested"
assert_eq "engine を記録する" "$(jq -r .engine "$WORK/out/impl.json")" "codex"
assert_eq "戻したファイルを記録する" "$(jq -c '.restored_files | sort' "$WORK/out/impl.json")" '["a.txt","junk.txt"]'
assert_eq "壊された追跡ファイルを戻す" "$(cat "$repo/a.txt")" "original"
[ ! -e "$repo/junk.txt" ] && ok "新しく作られたファイルを消す" || fail "新しく作られたファイルを消す"
assert_eq "開始前から汚れていたファイルには触らない" "$(cat "$repo/pre.txt")" "$(printf 'dirty before\nchanged')"
args="$(cat "$FAKE_CODEX_ARGS")"
assert_contains "impl は workspace-write" "$args" "-s workspace-write"
assert_contains "impl はネットワークを許可" "$args" "sandbox_workspace_write.network_access=true"
assert_contains "出力スキーマを渡す" "$args" "schema-impl-review.json"
assert_contains "既定の推論の強さは high" "$args" 'model_reasoning_effort="high"'
assert_contains "前置きをプロンプトの先頭に付ける" "$(head -n 1 "$WORK/args.stdin")" "Codex で動くときの読み替え"
assert_contains "レビュアープロンプトを続ける" "$(cat "$WORK/args.stdin")" "prompt body"
out2="$("$REVIEW" wait "$job")"; assert_eq "完了後の wait は同じ結果を返す" "$out2" "$out"

echo "== spec: read-only・後始末しない"
repo="$(new_repo spec)"
unset FAKE_CODEX_ACTION
export FAKE_CODEX_REPLY='{"reviewer":"test-spec-reviewer","target":"doc/test-spec/x.md","status":"changes_requested","issues":[{"location":"TC-001","problem":"p","fix":"f"}]}'
job="$(DEV_FLOW_CODEX_MODEL=gpt-test DEV_FLOW_CODEX_EFFORT= "$REVIEW" start spec "$repo" "$WORK/prompt.md" "$WORK/out/spec.json")"
out="$("$REVIEW" wait "$job" 30)"; assert_eq "wait は 0" "$?" 0
assert_contains "指摘の件数を出す" "$out" "指摘=1 件"
assert_eq "issues をそのまま残す" "$(jq -r '.issues[0].location' "$WORK/out/spec.json")" "TC-001"
args="$(cat "$FAKE_CODEX_ARGS")"
assert_contains "spec は read-only" "$args" "-s read-only"
assert_contains "モデルを指定できる" "$args" "-m gpt-test"
case "$args" in *model_reasoning_effort*) fail "EFFORT が空なら渡さない" ;; *) ok "EFFORT が空なら渡さない" ;; esac

echo "== 失敗"
export FAKE_CODEX_REPLY='{"reviewer":"x","status":"approved","findings":[],"checked_rules":[],"uncertainty_verdicts":[]}'
job="$(FAKE_CODEX_EXIT=1 "$REVIEW" start impl "$repo" "$WORK/prompt.md" "$WORK/out/fail.json")"
out="$("$REVIEW" wait "$job" 30)"; assert_eq "codex exec が失敗したら 4" "$?" 4
assert_contains "失敗の理由を出す" "$out" "exit 1"

export FAKE_CODEX_REPLY='これは JSON ではない'
job="$("$REVIEW" start impl "$repo" "$WORK/prompt.md" "$WORK/out/bad.json")"
out="$("$REVIEW" wait "$job" 30)"; assert_eq "最終回答が JSON でなければ 4" "$?" 4
[ ! -e "$WORK/out/bad.json" ] && ok "失敗したら出力を書かない" || fail "失敗したら出力を書かない"

out="$("$REVIEW" start impl "$WORK/no-such-dir" "$WORK/prompt.md" "$WORK/out/x.json")"; assert_eq "cwd が無ければ 4" "$?" 4
out="$(FAKE_CODEX_LOGIN=1 "$REVIEW" start impl "$repo" "$WORK/prompt.md" "$WORK/out/x.json")"; assert_eq "未ログインなら start も 4" "$?" 4

echo "== まだ動いている"
export FAKE_CODEX_REPLY='{"reviewer":"x","status":"approved","findings":[],"checked_rules":[],"uncertainty_verdicts":[]}'
job="$(FAKE_CODEX_SLEEP=3 "$REVIEW" start impl "$repo" "$WORK/prompt.md" "$WORK/out/slow.json")"
out="$("$REVIEW" wait "$job" 0)"; assert_eq "待ち時間を超えたら 124" "$?" 124
assert_contains "もう一度 wait するよう出す" "$out" "もう一度 wait"
out="$("$REVIEW" wait "$job" 30)"; assert_eq "終われば 0" "$?" 0
assert_eq "approved のまま" "$(jq -r .status "$WORK/out/slow.json")" "approved"

echo
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -eq 0 ]
