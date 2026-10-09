#!/bin/bash
# dev-flow hooks のスモークテスト
#   stdin に hook JSON を渡して各スクリプトを単体実行し、出力（permissionDecision /
#   additionalContext / exit code / 副作用ファイル）を検証する。
#   gh は tests/hooks/fixtures/gh のスタブに差し替えるためネットワーク不要。
#   GNU (Linux) / BSD (macOS) どちらの coreutils でも動くこと。
#
# 使い方: bash tests/hooks/run.sh
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOKS="$ROOT/dev-flow/hooks"
FIXTURES="$ROOT/tests/hooks/fixtures"

PASS=0
FAIL=0
CURRENT=""

# 下流スキルの存在チェックはリポジトリ内のスキルを指す偽 HOME で行う（インストール状態に依存しない）
FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/dev-flow-hooks-home.XXXXXX")"
mkdir -p "$FAKE_HOME/.claude/skills"
for d in "$ROOT"/dev-flow*/; do
  d="${d%/}"
  ln -s "$d" "$FAKE_HOME/.claude/skills/$(basename "$d")"
done
export HOME="$FAKE_HOME"

# ---------------------------------------------------------------------------
# アサーション
# ---------------------------------------------------------------------------
ok()   { PASS=$((PASS + 1)); printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m %s\n         %s\n' "$1" "${2:-}"; }

# assert_contains <label> <haystack> <needle>
assert_contains() {
  case "$2" in *"$3"*) ok "$1" ;; *) fail "$1" "expected to contain: $3 / got: $(printf '%s' "$2" | head -c 300)" ;; esac
}
assert_not_contains() {
  case "$2" in *"$3"*) fail "$1" "expected NOT to contain: $3" ;; *) ok "$1" ;; esac
}
assert_eq() { [ "$2" = "$3" ] && ok "$1" || fail "$1" "expected: $3 / got: $2"; }
assert_empty() { [ -z "$2" ] && ok "$1" || fail "$1" "expected empty / got: $(printf '%s' "$2" | head -c 200)"; }

# decision <hook stdout> → permissionDecision の値（無ければ空）
decision() { printf '%s' "$1" | jq -r '.hookSpecificOutput.permissionDecision // empty' 2>/dev/null; }
reason()   { printf '%s' "$1" | jq -r '.hookSpecificOutput.permissionDecisionReason // .hookSpecificOutput.additionalContext // empty' 2>/dev/null; }

# ---------------------------------------------------------------------------
# テスト用プロジェクトの生成
# ---------------------------------------------------------------------------
new_project() {
  local dir
  dir="$(mktemp -d "${TMPDIR:-/tmp}/dev-flow-hooks-test.XXXXXX")"
  mkdir -p "$dir/doc/process"
  printf '%s\n' "$dir"
}

# write_state <dir> <next_stage | null> [extra jq filter]
write_state() {
  local dir="$1" stage="$2" extra="${3:-.}"
  jq -n --arg s "$stage" '
    {next_stage: (if $s == "null" then null else $s end), mode: "full",
     agent_hierarchy: {max_depth: 4, current_depth: 1},
     harness: {stage_history: []}}' | jq "$extra" > "$dir/doc/process/state.json"
}

write_checklist() {
  cat > "$1/doc/process/task_checklist.md" <<'EOF'
# タスクチェックリスト

## ステージ進捗
- [ ] 1. requirements: 要件定義
- [ ] 2. spec: 仕様書生成
- [ ] 3. consistency: 整合性チェック
- [ ] 4. implementation: 並列実装
- [ ] 5. test: テスト実行
- [ ] 6. compliance: 準拠チェック

## グループ 1 (App)
- [ ] TASK-001
EOF
}

# run_hook <script> <dir> <json> → stdout。stderr と exit code はファイル経由で hook_err / hook_rc から取れる
# （呼び出し側が $(...) で受けるため、変数では持ち出せない）
HOOK_ERR_FILE="$(mktemp)"
HOOK_RC_FILE="$(mktemp)"
trap 'rm -f "$HOOK_ERR_FILE" "$HOOK_RC_FILE"' EXIT
run_hook() {
  local script="$1" dir="$2" json="$3" rc
  (
    cd "$dir" || exit 1
    export CLAUDE_PROJECT_DIR="$dir" PATH="$FIXTURES:$PATH" GH_FIXTURE_DIR="$FIXTURES"
    printf '%s' "$json" | "$HOOKS/$script" 2>"$HOOK_ERR_FILE"
  )
  rc=$?
  printf '%s' "$rc" > "$HOOK_RC_FILE"
  return 0
}
hook_rc()  { cat "$HOOK_RC_FILE"; }
hook_err() { cat "$HOOK_ERR_FILE"; }

agent_json() { jq -n --arg n "$1" --arg m "${2:-haiku}" --arg pm "${3:-acceptEdits}" '{tool_name:"Agent",permission_mode:$pm,tool_input:{name:$n,model:$m}}'; }
bash_json()  { jq -n --arg c "$1" '{tool_name:"Bash",tool_input:{command:$c}}'; }
write_json() { jq -n --arg f "$1" '{tool_name:"Write",tool_input:{file_path:$f}}'; }

section() { CURRENT="$1"; printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# lib.sh: GNU/BSD ヘルパー
# ---------------------------------------------------------------------------
section "lib.sh ヘルパー"
(
  # shellcheck disable=SC1091
  source "$HOOKS/lib.sh" </dev/null
  f="$(mktemp)"; touch "$f"
  m="$(file_mtime "$f")"
  now="$(date +%s)"
  [ "$m" -gt 0 ] && [ $((now - m)) -lt 5 ] && ok "file_mtime が現在時刻付近を返す" || fail "file_mtime" "got $m"
  e="$(iso_to_epoch '2026-09-03T19:43:00+0900')"
  assert_eq "iso_to_epoch が固定文字列を epoch に変換する" "$e" "1788432180"
  assert_empty "iso_to_epoch は不正文字列で空を返す" "$(iso_to_epoch 'not-a-date')"
  assert_eq "stage_rank(implementation)" "$(stage_rank implementation)" "3"
  assert_eq "expected_agent_for_stage(test)" "$(expected_agent_for_stage test)" "stage-test-agent"
  assert_contains "stage_label(completed)" "$(stage_label completed)" "完了"
  assert_eq "legacy_stage(phase_4_5_mini) は旧値を読み替える" "$(legacy_stage phase_4_5_mini)" "plan_repair"
  rm -f "$f"
  exit $FAIL
)
sub_fail=$?; FAIL=$((FAIL + sub_fail)); PASS=$((PASS + 7 - sub_fail))

# ---------------------------------------------------------------------------
# 静的チェック
# ---------------------------------------------------------------------------
section "静的チェック"
# bash 3.2 は `$VAR（` のように変数名の直後にマルチバイト文字が続くと変数名を誤認して unbound variable になる
bad="$(grep -nP '\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]' "$HOOKS"/*.sh "$ROOT/setup.sh" 2>/dev/null || true)"
assert_empty "変数直後にマルチバイト文字が続く箇所が無い（\${VAR} を使う）" "$bad"
for f in "$HOOKS"/*.sh "$ROOT/setup.sh"; do bash -n "$f" 2>/dev/null || fail "構文: $f"; done
ok "全 hook が bash -n を通る"
conv="$(python3 "$ROOT/tests/check-conventions.py" 2>&1)"
assert_contains "conventions/*.md の構造（セクション・ルール ID・重大度）" "$conv" "conventions: 0 errors"

# ---------------------------------------------------------------------------
# pre-agent-check.sh
# ---------------------------------------------------------------------------
section "pre-agent-check.sh"
dir="$(new_project)"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json some-other-agent)")"
assert_empty "stage-*-agent 以外は素通り" "$out"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-requirements-agent opus)")"
assert_empty "requirements は state.json 無しでも許可" "$out"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_eq "state.json 無しで stage-spec-agent は deny" "$(decision "$out")" "deny"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-requirements-agent opus plan)")"
assert_eq "プランモードでは requirements でも deny" "$(decision "$out")" "deny"
assert_contains "プランモード deny の理由" "$(reason "$out")" "プランモード"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json some-other-agent haiku plan)")"
assert_empty "プランモードでも stage-*-agent 以外は素通り" "$out"

printf '{broken' > "$dir/doc/process/state.json"
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_eq "state.json 不正 JSON は deny" "$(decision "$out")" "deny"

write_state "$dir" spec
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_empty "next_stage=spec + stage-spec-agent は許可（出力なし）" "$out"
assert_contains "agent_start が flow.log に記録される" "$(cat "$dir/doc/process/flow.log")" "event=agent_start agent=stage-spec-agent stage=spec"

# /cd で移動した後: CLAUDE_PROJECT_DIR は起動時のディレクトリのまま、hook 入力の cwd が移動先を指す
other="$(mktemp -d)"
json="$(agent_json stage-spec-agent | jq --arg c "$dir" '. + {cwd: $c}')"
out="$( ( cd "$other" && export CLAUDE_PROJECT_DIR="$other" PATH="$FIXTURES:$PATH" && printf '%s' "$json" | "$HOOKS/pre-agent-check.sh" ) 2>/dev/null )"
assert_empty "CLAUDE_PROJECT_DIR より hook 入力の cwd の state.json を優先する（/cd 後）" "$out"
rm -rf "$other"

out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-test-agent)")"
assert_eq "ステージとエージェントの不一致は ask" "$(decision "$out")" "ask"
assert_contains "不一致理由に期待エージェント名" "$(reason "$out")" "stage-spec-agent"

write_state "$dir" spec '.agent_hierarchy.current_depth = 4'
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_eq "階層深さ上限で ask" "$(decision "$out")" "ask"

write_state "$dir" spec '.harness.stage_history = [range(5) | {stage:"spec"}]'
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_eq "同一ステージ 5 回で ask（ループ検出）" "$(decision "$out")" "ask"

rm -f "$dir/doc/process/state.json"
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-bootstrap-agent opus)")"
assert_empty "bootstrap は state.json 無しで許可" "$out"
write_state "$dir" spec
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-bootstrap-agent opus)")"
assert_eq "進行中 run があるときの bootstrap は ask" "$(decision "$out")" "ask"
write_state "$dir" completed
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-bootstrap-agent opus)")"
assert_empty "completed なら bootstrap は許可" "$out"

printf '{"current_phase":"phase_2","mode":"full"}' > "$dir/doc/process/state.json"
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_empty "旧スキーマ current_phase=phase_2 は next_stage=spec として許可" "$out"
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-test-agent)")"
assert_eq "旧スキーマでも不一致は ask" "$(decision "$out")" "ask"

HOME_BAK="$HOME"; export HOME="$(mktemp -d)"
out="$(run_hook pre-agent-check.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_eq "下流スキル欠損は deny" "$(decision "$out")" "deny"
export HOME="$HOME_BAK"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# state-sync.sh
# ---------------------------------------------------------------------------
section "state-sync.sh"
dir="$(new_project)"

out="$(run_hook state-sync.sh "$dir" "$(write_json src/main.go)")"
assert_empty "doc/process 以外の書き込みは素通り" "$out"

printf '{broken' > "$dir/doc/process/state.json"
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "state.json が不正 JSON なら exit 2" "$(hook_rc)" "2"
assert_contains "exit 2 の理由が stderr に出る" "$(hook_err)" "不正な JSON"

write_state "$dir" implementation
write_checklist "$dir"
out="$(run_hook state-sync.sh "$dir" "$(write_json "$dir/doc/process/state.json")")"
assert_eq "正常な state.json は exit 0" "$(hook_rc)" "0"
assert_contains "additionalContext に次ステージ" "$(reason "$out")" "Stage 4/6 implementation: 並列実装"
cl="$(cat "$dir/doc/process/task_checklist.md")"
assert_contains "requirements が [x] に同期" "$cl" "- [x] 1. requirements"
assert_contains "consistency が [x] に同期" "$cl" "- [x] 3. consistency"
assert_contains "implementation は [ ] のまま" "$cl" "- [ ] 4. implementation"
assert_contains "ステージ進捗以外のチェックボックスは触らない" "$cl" "- [ ] TASK-001"
assert_contains "stage_transition が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=stage_transition stage=implementation"

# 巻き戻し（--from=spec 相当）
write_state "$dir" spec
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
cl="$(cat "$dir/doc/process/task_checklist.md")"
assert_contains "巻き戻しで spec が [ ] に戻る" "$cl" "- [ ] 2. spec"
assert_contains "巻き戻しでも requirements は [x]" "$cl" "- [x] 1. requirements"

# escalation
printf '# エスカレーション報告: test\n' > "$dir/doc/process/escalation_test_20260921.md"
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/escalation_test_20260921.md)")"
assert_eq "escalation 書き込みは exit 0" "$(hook_rc)" "0"
assert_contains "escalation が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=escalation file=escalation_test_20260921.md"

# state.json の値域
write_state "$dir" bogus_stage
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "next_stage の値域外は exit 2" "$(hook_rc)" "2"
write_state "$dir" spec '.kind = "hotfix"'
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "kind の値域外は exit 2" "$(hook_rc)" "2"
write_state "$dir" spec '.kind = "fix"'
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "正しい next_stage / kind は exit 0" "$(hook_rc)" "0"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# doc-validate.sh / doc-validate.py
# ---------------------------------------------------------------------------
section "doc-validate.sh / doc-validate.py"
SAMPLE="$ROOT/evals/fixtures/sample-project"
dir="$(new_project)"; cp -R "$SAMPLE/." "$dir/"

out="$(run_hook doc-validate.sh "$dir" "$(write_json src/main.go)")"
assert_empty "doc/ 以外の書き込みは素通り" "$out"

out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "正しいテスト定義書は exit 0" "$(hook_rc)" "0"
assert_contains "検証 OK が additionalContext に出る" "$(reason "$out")" "スキーマ検証 OK"

out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/process/task_checklist.md)")"
assert_eq "正しいチェックリストは exit 0" "$(hook_rc)" "0"

# covers に存在しない REQ
sed -i.bak 's/covers: \[REQ-004\]/covers: [REQ-099]/' "$dir/doc/test-spec/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "存在しない REQ を covers すると exit 2" "$(hook_rc)" "2"
assert_contains "違反理由が stderr に出る" "$(hook_err)" "REQ-099 が doc/requirements/ に存在しません"
assert_contains "違反行の行番号が付く" "$(hook_err)" "doc/test-spec/auth.md:1"
assert_contains "直し方（→）が付く" "$(hook_err)" "→ doc/requirements/*.md の frontmatter にある ID"
assert_contains "3 回で blocked の案内" "$(hook_err)" "3 回差し戻された場合は"
cp "$SAMPLE/doc/test-spec/auth.md" "$dir/doc/test-spec/auth.md"

# 書きかけ: frontmatter にあるが本文が未完成
python3 - "$dir/doc/test-spec/auth.md" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text(); p.write_text(s[:s.index('### TC-003')])
PY
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "本文未完成（マーカー無し）は exit 2" "$(hook_rc)" "2"
assert_contains "3 件以上ならマーカーのヒント" "$(hook_err)" "in-progress --> を置くと"
printf '\n<!-- dev-flow: in-progress -->\n' >> "$dir/doc/test-spec/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "書きかけマーカー付きなら exit 0" "$(hook_rc)" "0"
assert_contains "マーカーの WARN が出る" "$(reason "$out")" "書きかけマーカー"
all_out="$(python3 "$HOOKS/doc-validate.py" --project-dir "$dir" --all || true)"
assert_contains "--all ではマーカーの残存が ERROR" "$all_out" "ERROR doc/test-spec/auth.md"
assert_contains "  マーカーが理由" "$all_out" "書きかけマーカー <!-- dev-flow: in-progress --> が残っています"
cp "$SAMPLE/doc/test-spec/auth.md" "$dir/doc/test-spec/auth.md"

# 本文にあるが frontmatter に無い
printf '\n### TC-099: 異常系: 迷子の見出し\n' >> "$dir/doc/test-spec/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "本文にだけある ID は exit 2" "$(hook_rc)" "2"
assert_contains "frontmatter への追加を促す" "$(hook_err)" "TC-099: 本文に見出しがあるが frontmatter にありません"
cp "$SAMPLE/doc/test-spec/auth.md" "$dir/doc/test-spec/auth.md"

# implemented_by の関数が無い
sed -i.bak 's/TestLogin_WrongPassword$/TestLogin_Missing/' "$dir/doc/test-spec/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "implemented_by の関数が無ければ exit 2" "$(hook_rc)" "2"
cp "$SAMPLE/doc/test-spec/auth.md" "$dir/doc/test-spec/auth.md"

# frontmatter なし
printf '# API\n本文のみ\n' > "$dir/doc/api-spec/x.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/api-spec/x.md)")"
assert_eq "frontmatter 無しは exit 2" "$(hook_rc)" "2"
assert_contains "doc_invalid が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=doc_invalid file=doc/api-spec/x.md"

# ID 重複（requirements）
sed -i.bak 's/  - id: REQ-005/  - id: REQ-004/' "$dir/doc/requirements/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/requirements/auth.md)")"
assert_eq "REQ の重複は exit 2" "$(hook_rc)" "2"
assert_contains "重複の理由" "$(hook_err)" "REQ-004: ID が重複しています"

# 旧形式のチェックリスト
printf '# x\n\n## フェーズ進捗\n- [ ] Phase 1-2\n' > "$dir/doc/process/task_checklist.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/process/task_checklist.md)")"
assert_eq "旧形式のチェックリストは exit 2" "$(hook_rc)" "2"

# --all（eval 用の入口）
out="$(cd "$SAMPLE" && python3 "$HOOKS/doc-validate.py" --all)"
assert_contains "--all でサンプルプロジェクトが通る" "$out" "summary: 0 errors"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# test-lint.sh / test-lint.py
# ---------------------------------------------------------------------------
section "test-lint.sh / test-lint.py"
TL="$ROOT/tests/test-lint"
dir="$(new_project)"
mkdir -p "$dir/pkg/auth" "$dir/tests" "$dir/src" "$dir/tests/Feature"

out="$(run_hook test-lint.sh "$dir" "$(write_json pkg/auth/login.go)")"
assert_empty "テストファイル以外は素通り" "$out"

cp "$TL/good/login_test.go" "$dir/pkg/auth/login_test.go"
out="$(run_hook test-lint.sh "$dir" "$(write_json pkg/auth/login_test.go)")"
assert_eq "正しい Go テストは exit 0" "$(hook_rc)" "0"
assert_contains "検証 OK が additionalContext に出る" "$(reason "$out")" "テストコード静的検証 OK"

cp "$TL/bad/login_test.go" "$dir/pkg/auth/login_test.go"
out="$(run_hook test-lint.sh "$dir" "$(write_json pkg/auth/login_test.go)")"
assert_eq "skip / assert なしの Go テストは exit 2" "$(hook_rc)" "2"
assert_contains "no-skip が理由に出る" "$(hook_err)" "test/no-skip"
assert_contains "assert-present が理由に出る" "$(hook_err)" "test/assert-present"
assert_contains "test_lint_failed が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=test_lint_failed file=pkg/auth/login_test.go"

for pair in "good/test_login.py:tests/test_login.py" "good/login.test.ts:src/login.test.ts" "good/LoginTest.php:tests/Feature/LoginTest.php"; do
  src="${pair%%:*}"; dst="${pair#*:}"
  cp "$TL/$src" "$dir/$dst"
  out="$(run_hook test-lint.sh "$dir" "$(write_json "$dst")")"
  assert_eq "正しい $dst は exit 0" "$(hook_rc)" "0"
done
for pair in "bad/test_login.py:tests/test_login.py" "bad/login.test.ts:src/login.test.ts" "bad/LoginTest.php:tests/Feature/LoginTest.php"; do
  src="${pair%%:*}"; dst="${pair#*:}"
  cp "$TL/$src" "$dir/$dst"
  out="$(run_hook test-lint.sh "$dir" "$(write_json "$dst")")"
  assert_eq "違反のある $dst は exit 2" "$(hook_rc)" "2"
done

# WARN だけのファイルは exit 0 + 警告通知
cat > "$dir/pkg/auth/warn_test.go" <<'GO'
package auth
import ("testing"; "time")
func TestWarnOnly(t *testing.T) {
	time.Sleep(10 * time.Millisecond)
	if Issue(time.Now()) == "" { t.Fatal("empty") }
}
GO
out="$(run_hook test-lint.sh "$dir" "$(write_json pkg/auth/warn_test.go)")"
assert_eq "WARN のみは exit 0" "$(hook_rc)" "0"
assert_contains "WARN が additionalContext に出る" "$(reason "$out")" "test/deterministic"

# --all（eval 用）
out="$(python3 "$HOOKS/test-lint.py" --all "$TL/good")"
assert_contains "--all で good が 0 errors" "$out" "summary: 0 errors"
out="$(python3 "$HOOKS/test-lint.py" --all "$TL/bad" || true)"
assert_contains "--all で bad の全ルールが検出される（no-skip）" "$out" "test/no-skip"
for r in assert-present empty-test swallowed-error zero-assertions commented-out deterministic tautology ts-ignore; do
  assert_contains "  ルール test/$r" "$out" "test/$r"
done
rm -rf "$dir"

# ---------------------------------------------------------------------------
# mark-group-done.sh
# ---------------------------------------------------------------------------
section "mark-group-done.sh"
dir="$(new_project)"
(cd "$dir" && git init -q && git config user.email t@example.com && git config user.name t)
cat > "$dir/doc/process/task_checklist.md" <<'EOF'
# タスクチェックリスト

## ステージ進捗

- [ ] 4. implementation: 並列実装

### グループ 1 (App) — depends_on: []

#### Dev タスク (App)
- [ ] migration 作成
- [x] 既に完了のタスク

#### QA タスク (App)
- [ ] TC-001 の Feature テスト

### グループ 2 (App) — depends_on: [group-1]

#### Dev タスク (App)
- [ ] API 実装

## 実装タスク（Devチーム）全一覧
- [ ] migration 作成
- [ ] API 実装

## QAタスク（QAチーム）全一覧
- [ ] TC-001 の Feature テスト
EOF
write_state "$dir" implementation '.implementation_progress = {total_groups: 2, completed_groups: [], active_worktrees: ["worktree-dev-app-group-1", "worktree-qa-app-group-1", "worktree-dev-app-group-2"], pr_numbers: {}}'
(cd "$dir" && git add -A && git commit -qm init)
out="$(cd "$dir" && CLAUDE_PROJECT_DIR="$dir" bash "$HOOKS/mark-group-done.sh" 1 12 13 2>&1)"; rc=$?
assert_eq "正常終了" "$rc" "0"
assert_contains "グループ 1 の Dev タスクが [x]" "$(sed -n '/### グループ 1/,/### グループ 2/p' "$dir/doc/process/task_checklist.md")" "- [x] migration 作成"
assert_contains "グループ 1 の QA タスクが [x]" "$(cat "$dir/doc/process/task_checklist.md")" "- [x] TC-001 の Feature テスト"
assert_contains "グループ 2 のタスクは [ ] のまま" "$(cat "$dir/doc/process/task_checklist.md")" "- [ ] API 実装"
assert_eq "全一覧の同一タスクも [x]" "$(grep -c '^- \[x\] migration 作成' "$dir/doc/process/task_checklist.md")" "2"
assert_eq "completed_groups に追加" "$(jq -c .implementation_progress.completed_groups "$dir/doc/process/state.json")" '["group-1"]'
assert_eq "group-1 の worktree だけ除去" "$(jq -c .implementation_progress.active_worktrees "$dir/doc/process/state.json")" '["worktree-dev-app-group-2"]'
assert_eq "pr_numbers に記録" "$(jq -c '.implementation_progress.pr_numbers["group-1"]' "$dir/doc/process/state.json")" '[12,13]'
assert_contains "コミットされる" "$(cd "$dir" && git log --oneline -1)" "グループ 1 完了"
assert_contains "flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=group_done group=group-1"
out="$(cd "$dir" && CLAUDE_PROJECT_DIR="$dir" bash "$HOOKS/mark-group-done.sh" 1 12 13 --no-commit 2>&1)"
assert_eq "冪等（2 回目も成功、completed_groups は重複しない）" "$(jq -c .implementation_progress.completed_groups "$dir/doc/process/state.json")" '["group-1"]'
out="$(cd "$dir" && CLAUDE_PROJECT_DIR="$dir" bash "$HOOKS/mark-group-done.sh" 9 --no-commit 2>&1)"; rc=$?
assert_eq "存在しないグループは失敗" "$rc" "1"
assert_contains "  理由を表示" "$out" "セクションが見つかりません"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# agent-complete.sh
# ---------------------------------------------------------------------------
section "agent-complete.sh"
dir="$(new_project)"
write_state "$dir" spec

out="$(run_hook agent-complete.sh "$dir" "$(agent_json some-other-agent)")"
assert_empty "stage-*-agent 以外は素通り" "$out"

# agent_start を 90 秒前に偽装（log_flow と同じ書式）
ts="$(TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S%z')"
(
  source "$HOOKS/lib.sh" </dev/null
  past="$(( $(iso_to_epoch "$ts") - 90 ))"
  # epoch → ISO（GNU / BSD）
  iso="$(TZ=Asia/Tokyo date -d "@$past" '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || TZ=Asia/Tokyo date -j -r "$past" '+%Y-%m-%dT%H:%M:%S%z')"
  printf '%s event=agent_start agent=stage-requirements-agent stage=requirements model=opus\n' "$iso" > "$dir/doc/process/flow.log"
)
out="$(run_hook agent-complete.sh "$dir" "$(agent_json stage-requirements-agent opus)")"
assert_contains "完了が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=agent_complete agent=stage-requirements-agent"
dur="$(grep -o 'duration_seconds=[0-9]*' "$dir/doc/process/flow.log" | cut -d= -f2)"
[ -n "$dur" ] && [ "$dur" -ge 89 ] && [ "$dur" -le 95 ] && ok "所要時間が算出される（GNU/BSD date）" || fail "所要時間" "got: ${dur:-empty}"
assert_contains "requirements 完了時は人間確認ゲートを念押し" "$(reason "$out")" "人間確認ゲート"

# 起動直後（agent_start から数秒）の PostToolUse は「完了」ではなく「起動」として記録
printf '%s event=agent_start agent=stage-spec-agent stage=spec model=haiku\n' "$ts" >> "$dir/doc/process/flow.log"
out="$(run_hook agent-complete.sh "$dir" "$(agent_json stage-spec-agent)")"
assert_contains "数秒以内の PostToolUse は agent_spawned として記録" "$(cat "$dir/doc/process/flow.log")" "event=agent_spawned agent=stage-spec-agent"
assert_not_contains "agent_complete は記録しない" "$(grep stage-spec-agent "$dir/doc/process/flow.log")" "event=agent_complete"
assert_contains "起動のみで完了ではない旨を通知" "$(reason "$out")" "実行はまだ完了していません"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# session-start.sh / stop-summary.sh
# ---------------------------------------------------------------------------
section "session-start.sh / stop-summary.sh"
dir="$(new_project)"

out="$(run_hook session-start.sh "$dir" '{}')"
assert_empty "state.json 無しは何も出さない" "$out"

write_state "$dir" implementation '.implementation_progress = {completed_groups:["group-1"], pr_numbers:{"group-1":[101],"group-2":[102]}, base_branch:"feature/xxx"}'
out="$(run_hook session-start.sh "$dir" '{}')"
assert_contains "次ステージを表示" "$out" "next_stage=implementation"
assert_contains "マージ待ち PR を表示（完了グループは除外）" "$out" "group-2: PR #102"
assert_not_contains "完了グループの PR は表示しない" "$out" "group-1: PR #101"

out="$(run_hook stop-summary.sh "$dir" '{}')"
assert_empty "flow.log が無ければ stop-summary は沈黙" "$out"
printf '%s event=stage_transition stage=implementation\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" > "$dir/doc/process/flow.log"
out="$(run_hook stop-summary.sh "$dir" '{}')"
assert_contains "直近イベントがあれば systemMessage を出す（file_mtime）" "$(printf '%s' "$out" | jq -r '.systemMessage')" "Stage 4/6 implementation: 並列実装"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# pr-merge-guard.sh
# ---------------------------------------------------------------------------
section "pr-merge-guard.sh（gh はスタブ）"
dir="$(new_project)"
write_state "$dir" implementation '.implementation_progress = {base_branch:"feature/xxx"}'

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'git status')")"
assert_empty "gh pr merge を含まないコマンドは素通り" "$out"

M="gh pr merge"
out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json "cat > note.md <<'EOF'
手順: $M 123 --merge を実行する
EOF
echo done")")"
assert_empty "heredoc 本文に含まれる gh pr merge は素通り（誤検知しない）" "$out"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json "python3 - <<EOF
print('$M は禁止')
EOF
$M 102 --merge")")"
assert_eq "heredoc の後に本物の gh pr merge があれば検査する" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 101 --merge && gh pr merge 102 --merge')")"
assert_eq "1 コマンド 2 PR は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 101 --squash')")"
assert_eq "--squash は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 101')")"
assert_eq "--merge 省略は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge --merge')")"
assert_eq "PR 番号省略は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 101 --merge')")"
assert_eq "条件を満たす PR は allow" "$(decision "$out")" "allow"
assert_contains "allow が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=auto_merge_allowed pr=101"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge https://github.com/o/r/pull/101 --merge')")"
assert_eq "URL 指定でも allow" "$(decision "$out")" "allow"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 102 --merge')")"
assert_eq "main 向けは deny" "$(decision "$out")" "deny"
assert_contains "保護ブランチの理由" "$(reason "$out")" "保護ブランチ"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 103 --merge')")"
assert_eq "CI 失敗は deny" "$(decision "$out")" "deny"
assert_contains "CI 失敗の理由にチェック名" "$(reason "$out")" "ci=FAILURE"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 104 --merge')")"
assert_eq "CI なしは deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 105 --merge')")"
assert_eq "コンフリクトは deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 106 --merge')")"
assert_eq "DB 破壊的変更は deny" "$(decision "$out")" "deny"
assert_contains "DB 破壊的変更の該当行を提示" "$(reason "$out")" "DROP COLUMN"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 107 --merge')")"
assert_eq "Draft は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 108 --merge')")"
assert_eq "state.json の base_branch と不一致は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 999 --merge')")"
assert_eq "存在しない PR は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 113 --merge')")"
assert_eq "テストファイル内の DROP TABLE / DELETE FROM 文字列は allow" "$(decision "$out")" "allow"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 114 --merge')")"
assert_eq "マイグレーションの DROP TABLE は deny（テストファイルの同文字列は無視）" "$(decision "$out")" "deny"
assert_contains "deny 理由にマイグレーション側の行" "$(reason "$out")" "legacy_tasks"
assert_not_contains "deny 理由にテストファイル側の行は出ない" "$(reason "$out")" "'; DROP TABLE tasks"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 115 --merge')")"
assert_eq "Tailwind の truncate クラスは DB 破壊的変更と誤検知しない（allow）" "$(decision "$out")" "allow"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 116 --merge')")"
assert_eq "TRUNCATE TABLE は deny" "$(decision "$out")" "deny"
assert_contains "TRUNCATE TABLE の該当行を提示" "$(reason "$out")" "TRUNCATE TABLE tasks"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 117 --merge')")"
assert_eq "小文字の truncate <table>; と TRUNCATE \"t\" RESTART ... も deny" "$(decision "$out")" "deny"
assert_contains "小文字の truncate の該当行を提示" "$(reason "$out")" "truncate users"
assert_contains "引用符付きテーブル名の該当行を提示" "$(reason "$out")" "audit_logs"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 109 --merge')")"
assert_eq "テスト関数の削除は deny" "$(decision "$out")" "deny"
assert_contains "削除された関数名を提示" "$(reason "$out")" "TestLogin_WrongPassword"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 110 --merge')")"
assert_eq "t.Skip の追加は deny" "$(decision "$out")" "deny"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 111 --merge')")"
assert_eq "テスト関数の移動（削除+同内容の追加）は allow" "$(decision "$out")" "allow"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 112 --merge')")"
assert_eq "pytest.mark.skip / it.skip は deny" "$(decision "$out")" "deny"
assert_contains "複数言語のヒットを提示" "$(reason "$out")" "it.skip"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 118 --merge')")"
assert_eq "同じ TC-ID のテストの名前変更は allow" "$(decision "$out")" "allow"

out="$(run_hook pr-merge-guard.sh "$dir" "$(bash_json 'gh pr merge 119 --merge')")"
assert_eq "TC-ID が違うテストへの置き換えは deny" "$(decision "$out")" "deny"
assert_contains "消えた TC のテストを提示" "$(reason "$out")" "test_tc043"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# test-stage-guard.sh
# ---------------------------------------------------------------------------
section "test-stage-guard.sh"
dir="$(new_project)"
tsw() { jq -n --arg f "$1" --arg t "${2:-Edit}" '{tool_name:$t,tool_input:{file_path:$f}}'; }

write_state "$dir" implementation
out="$(run_hook test-stage-guard.sh "$dir" "$(tsw pkg/auth/login_test.go)")"
assert_empty "implementation ステージではテストファイルを編集できる" "$out"

write_state "$dir" test
out="$(run_hook test-stage-guard.sh "$dir" "$(tsw pkg/auth/login.go)")"
assert_empty "test ステージでもプロダクションコードは編集できる" "$out"

for f in pkg/auth/login_test.go tests/test_login.py src/login.test.ts tests/Feature/LoginTest.php doc/test-spec/auth.md __tests__/x.spec.tsx; do
  out="$(run_hook test-stage-guard.sh "$dir" "$(tsw "$f")")"
  assert_eq "test ステージでは $f を deny" "$(decision "$out")" "deny"
done
out="$(run_hook test-stage-guard.sh "$dir" "$(tsw "$dir/pkg/auth/login_test.go" Write)")"
assert_eq "絶対パスの Write も deny" "$(decision "$out")" "deny"
assert_contains "deny が flow.log に記録" "$(cat "$dir/doc/process/flow.log")" "event=test_stage_write_denied file=pkg/auth/login_test.go"
out="$(run_hook test-stage-guard.sh "$dir" "$(bash_json 'rm pkg/auth/login_test.go')")"
assert_empty "Bash は対象外（pr-merge-guard が diff で拾う）" "$out"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# state-sync.sh: implementation → test のリモート確認ゲート
# ---------------------------------------------------------------------------
section "state-sync.sh: implementation → test ゲート"
dir="$(new_project)"
write_checklist "$dir"
write_state "$dir" implementation
run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)" >/dev/null
write_state "$dir" test
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "remote_verified 無しで test に進むと exit 2" "$(hook_rc)" "2"
assert_contains "verify-remote-state.sh の実行を促す" "$(hook_err)" "verify-remote-state.sh --expect-merged"
assert_not_contains "拒否した遷移は flow.log に記録しない" "$(cat "$dir/doc/process/flow.log")" "event=stage_transition stage=test"
printf '2026-09-25T10:00:00+0900 event=remote_verified result=ng branch=feature/x ng=1 prs=101\n' >> "$dir/doc/process/flow.log"
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "remote_verified が NG なら exit 2" "$(hook_rc)" "2"
printf '2026-09-25T10:01:00+0900 event=remote_verified result=ok branch=feature/x prs=101\n' >> "$dir/doc/process/flow.log"
out="$(run_hook state-sync.sh "$dir" "$(write_json doc/process/state.json)")"
assert_eq "遷移後に OK が記録されていれば exit 0" "$(hook_rc)" "0"
assert_contains "test への遷移が記録される" "$(cat "$dir/doc/process/flow.log")" "event=stage_transition stage=test prev=implementation"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# verify-remote-state.sh
# ---------------------------------------------------------------------------
section "verify-remote-state.sh"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
vr_root="$(mktemp -d "${TMPDIR:-/tmp}/dev-flow-verify.XXXXXX")"
git init -q --bare "$vr_root/origin.git"
git clone -q "$vr_root/origin.git" "$vr_root/work" 2>/dev/null
git clone -q "$vr_root/origin.git" "$vr_root/other" 2>/dev/null
( cd "$vr_root/work" && git checkout -q -b feature/x && mkdir -p doc/process && echo a > a && git add a && git commit -qm a && git push -q origin feature/x 2>/dev/null )
vr() {
  ( cd "$vr_root/work" && CLAUDE_PROJECT_DIR="$vr_root/work" PATH="$FIXTURES:$PATH" GH_FIXTURE_DIR="$FIXTURES" \
      "$HOOKS/verify-remote-state.sh" "$@" ) 2>&1
}
out="$(vr 101)"; rc=$?
assert_contains "同期済みブランチは OK" "$out" "OK   branch feature/x: origin/feature/x と同期"
assert_contains "CI 成功の OPEN PR は OK" "$out" "OK   PR #101: state=OPEN checks=1/1"
assert_contains "NG 0" "$out" "summary: NG 0"
assert_contains "OK が flow.log に記録" "$(cat "$vr_root/work/doc/process/flow.log")" "event=remote_verified result=ok"
out="$(vr --expect-merged 101 120)"
assert_contains "--expect-merged で未マージは NG" "$out" "NG   PR #101: state=OPEN checks=1/1 成功 https://github.com/o/r/pull/101 未マージ"
assert_contains "マージ済みは OK" "$out" "OK   PR #120: state=MERGED"
out="$(vr 103)"
assert_contains "CI 失敗は NG（チェック名付き）" "$out" "失敗: ci=FAILURE"
out="$(vr --expect-merged 122)"
assert_contains "マージ済み PR の CI 失敗は INFO（人間がマージした）" "$out" "INFO PR #122: state=MERGED"
assert_contains "  NG には数えない" "$out" "summary: NG 0"
out="$(vr 121)"
assert_contains "CI 実行中は NG（推測で書かせない）" "$out" "未完了: ci"
( cd "$vr_root/other" && git fetch -q origin && git checkout -q feature/x && echo b > b && git add b && git commit -qm b && git push -q origin feature/x 2>/dev/null )
out="$(vr)"
assert_contains "origin より遅れていれば NG" "$out" "NG   branch feature/x: origin/feature/x より 1 コミット遅れている"
assert_contains "NG が flow.log に記録" "$(tail -1 "$vr_root/work/doc/process/flow.log")" "event=remote_verified result=ng"
( cd "$vr_root/work" && CLAUDE_PROJECT_DIR="$vr_root/work" "$HOOKS/verify-remote-state.sh" >/dev/null 2>&1 ); rc=$?
assert_eq "NG があれば exit 1" "$rc" "1"
rm -rf "$vr_root"

# ---------------------------------------------------------------------------
# test-lint: シェル（インフラの結合テスト）
# ---------------------------------------------------------------------------
section "test-lint: シェル"
dir="$(new_project)"
mkdir -p "$dir/tests"
for f in nginx_test.sh proxy.bats; do
  cp "$TL/good/$f" "$dir/tests/$f"
  out="$(run_hook test-lint.sh "$dir" "$(write_json "tests/$f")")"
  assert_eq "正しい tests/$f は exit 0" "$(hook_rc)" "0"
  cp "$TL/bad/$f" "$dir/tests/$f"
  out="$(run_hook test-lint.sh "$dir" "$(write_json "tests/$f")")"
  assert_eq "違反のある tests/$f は exit 2" "$(hook_rc)" "2"
done
out="$(python3 "$HOOKS/test-lint.py" "$TL/bad/nginx_test.sh" "$TL/bad/proxy.bats" || true)"
for r in self-compare grep-count-lines restore-trap restore-warn-only ipv4-only deterministic no-skip assert-present empty-test; do
  assert_contains "  ルール test/$r" "$out" "test/$r"
done
out="$(python3 "$HOOKS/test-lint.py" "$TL/good/nginx_test.sh" "$TL/good/proxy.bats")"
assert_contains "good のシェルは 0 errors, 0 warnings" "$out" "summary: 0 errors, 0 warnings"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# doc-validate: 未カバー REQ の WARN
# ---------------------------------------------------------------------------
section "doc-validate: 未カバー REQ"
dir="$(new_project)"; cp -R "$SAMPLE/." "$dir/"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_eq "未カバーは WARN なので exit 0" "$(hook_rc)" "0"
assert_contains "テスト定義書から漏れた REQ を通知" "$(reason "$out")" "テスト定義書（doc/test-spec/）のどの covers にも無い REQ があります: REQ-005"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/api-spec/auth.md)")"
assert_contains "仕様書から漏れた REQ を通知" "$(reason "$out")" "仕様書（doc/infra-spec/ ・ doc/api-spec/）のどの covers にも無い REQ があります: REQ-005"
sed -i.bak 's/covers: \[REQ-004\]/covers: [REQ-004, REQ-005]/' "$dir/doc/test-spec/auth.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/test-spec/auth.md)")"
assert_not_contains "全 REQ をカバーすれば WARN は出ない" "$(reason "$out")" "covers にも無い REQ"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# state-write-guard.sh
# ---------------------------------------------------------------------------
section "state-write-guard.sh"
dir="$(new_project)"
write_state "$dir" implementation
for c in 'jq ".next_stage=\"test\"" doc/process/state.json > /tmp/s.json && mv /tmp/s.json doc/process/state.json' \
         'echo "{}" > doc/process/state.json' \
         'jq . x.json | tee doc/process/state.json' \
         "sed -i 's/a/b/' doc/process/state.json" \
         'cp /tmp/s.json doc/process/state.json' \
         "python3 -c 'import json; json.dump({}, open(\"doc/process/state.json\", \"w\"))'" \
         "python3 - <<'EOF'
import json
p='doc/process/state.json'
d=json.load(open(p,encoding='utf-8'))
d['next_stage']='spec'
open(p,'w',encoding='utf-8').write(json.dumps(d))
EOF"; do
  out="$(run_hook state-write-guard.sh "$dir" "$(bash_json "$c")")"
  assert_eq "書き込みは deny: ${c:0:50}" "$(decision "$out")" "deny"
done
for c in 'jq -r .next_stage doc/process/state.json' \
         'cat doc/process/state.json > /tmp/backup.json' \
         'cp doc/process/state.json /tmp/backup.json' \
         'git add doc/process/state.json && git commit -m x' \
         "git commit -F - <<'EOF'
state.json を jq > state.json で書いていたのを直す
EOF" \
         'bash ~/.claude/skills/dev-flow/hooks/mark-group-done.sh 1 12 13' \
         "python3 -c 'import json; print(json.load(open(\"doc/process/state.json\"))[\"next_stage\"])'" \
         "python3 tests/check.py && git commit -qm \"state.json を p='doc/process/state.json' → open(p,'w') で書くのを止める\""; do
  out="$(run_hook state-write-guard.sh "$dir" "$(bash_json "$c")")"
  assert_empty "読むだけ・対象外は素通り: ${c:0:50}" "$out"
done
rm -f "$dir/doc/process/state.json"
out="$(run_hook state-write-guard.sh "$dir" "$(bash_json 'echo "{}" > doc/process/state.json')")"
assert_empty "state.json が無いプロジェクトでは何もしない" "$out"
rm -rf "$dir"

# flow.log は git で追跡しない（新しく作るとき doc/process/.gitignore に足す）
dir="$(new_project)"; git -C "$dir" init -q
write_state "$dir" implementation
run_hook state-write-guard.sh "$dir" "$(bash_json 'echo "{}" > doc/process/state.json')" >/dev/null
assert_eq "git 管理下で flow.log を作ると .gitignore に足す" "$(cat "$dir/doc/process/.gitignore" 2>/dev/null)" "flow.log"
run_hook state-write-guard.sh "$dir" "$(bash_json 'echo "{}" > doc/process/state.json')" >/dev/null
assert_eq "  2 回目は足さない" "$(grep -c . "$dir/doc/process/.gitignore")" "1"
rm -rf "$dir"

# ---------------------------------------------------------------------------
# doc-validate: _ で始まる補助ファイル
# ---------------------------------------------------------------------------
section "doc-validate: 補助ファイル（_glossary.md 等）"
dir="$(new_project)"; cp -R "$SAMPLE/." "$dir/"
printf '# 用語集\n\n| 用語 | 定義 | 備考 |\n|---|---|---|\n| 単語 | 空白で区切られた文字列 | REQ-002 |\n' > "$dir/doc/requirements/_glossary.md"
out="$(run_hook doc-validate.sh "$dir" "$(write_json doc/requirements/_glossary.md)")"
assert_eq "frontmatter の無い _glossary.md は差し戻さない" "$(hook_rc)" "0"
out="$(python3 "$HOOKS/doc-validate.py" --project-dir "$dir" --all)"
assert_not_contains "--all でも _glossary.md を検証しない" "$out" "_glossary.md"
rm -rf "$dir"

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
