#!/bin/bash
# PreToolUse (matcher: Bash)
# implementation 中に、Dev のブランチ・worktree に変更が来るのを sleep / until で待つコマンドを止める。
# QA は仕様書だけでテストを書き、Dev との突き合わせは統合検証（STEP C.5）で行う決まり。待つと QA が完了せず、
# そのグループのレビューも次のグループの起動も止まる。オーケストレーターも Dev を sleep で待つ必要は無い（完了は通知で来る）。
# 対象: 待ちの構文（sleep / until。sleep の無い while read は後片付けでも使うので見ない）と Dev の参照（dev/{app,infra}-group-N・worktree-dev-）が同じコマンドにあるもの。
# 1 回だけの git cat-file / git log などの確認は止めない。implementation 以外のステージでは何もしない。

source "$(dirname "$0")/lib.sh"

[ "$(jqi '.tool_name')" = "Bash" ] || exit 0
[ "$(next_stage)" = "implementation" ] || exit 0
CMD="$(jqi '.tool_input.command // empty')"

printf '%s' "$CMD" | grep -qE '(^|[;&|({[:space:]])(sleep|until)([[:space:]]|$)' || exit 0
printf '%s' "$CMD" | grep -qE 'dev/(app|infra)-group-[0-9]|worktree-dev-(app|infra)-group-' || exit 0

log_flow "event=dev_wait_denied"
deny "dev-flow hook: Dev のブランチ・worktree を sleep / until で待つことはできません。QA は仕様書（API 仕様書・テスト定義書・スペックキャッシュ・モック）から名前や文言を決めて書き進め、決まらないものは推定して完了 JSON の uncertainty_points に書いてください。Dev との食い違いは統合検証でオーケストレーターが直させます。オーケストレーターは implementer の完了通知を待ってください。"
