#!/bin/bash
# dev-flow のレビュアーを Codex CLI（codex exec）で動かすラッパー。
# 手順は dev-flow/reference/codex-review.md。LLM を呼ぶのは start が起動する codex だけ。
#
#   review.sh available
#       codex でレビューできるか。0: できる / 3: できない（理由を 1 行出す）
#   review.sh start <impl|spec> <cwd> <prompt-file> <out-json>
#       バックグラウンドで codex exec を起動し、ジョブディレクトリのパスを 1 行出す
#       impl: implementation の Dev / QA レビュー（cwd は worktree。workspace-write でテストを実行できる）
#       spec: spec の reviewer（cwd はメインの作業ディレクトリ。read-only）
#   review.sh wait <job-dir> [秒（既定 540）]
#       終わるまで待つ。0: 完了（out-json を書いた）/ 124: まだ動いている（もう一度 wait する）/
#       4: 失敗（理由を出す。Claude のレビュアーに切り替える）
#
# 環境変数:
#   DEV_FLOW_CODEX=off           codex を使わない（available が 3 を返す）
#   DEV_FLOW_CODEX_BIN           codex コマンド（既定 codex）
#   DEV_FLOW_CODEX_MODEL         codex exec -m に渡すモデル（既定は codex の設定のまま）
#   DEV_FLOW_CODEX_EFFORT        model_reasoning_effort（既定 high。空文字なら渡さない）
#   DEV_FLOW_CODEX_TIMEOUT       codex exec 1 回の上限秒数（既定 1800）
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
CODEX="${DEV_FLOW_CODEX_BIN:-codex}"

die()  { printf '%s\n' "$*"; exit 4; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

abspath() {
  local d b
  d="$(dirname "$1")"; b="$(basename "$1")"
  mkdir -p "$d" && printf '%s/%s\n' "$(cd "$d" && pwd)" "$b"
}

# git status の 1 行（porcelain v1）からパスを取り出す。引用符付き（空白を含む）なら外す
status_path() {
  local p="${1:3}"
  case "$p" in *' -> '*) p="${p##* -> }" ;; esac
  case "$p" in \"*\") p="${p#\"}"; p="${p%\"}" ;; esac
  printf '%s\n' "$p"
}

git_status() {
  git -C "$1" -c core.quotePath=false status --porcelain=v1 --untracked-files=all 2>/dev/null
}

cmd_available() {
  if [ "${DEV_FLOW_CODEX:-}" = "off" ]; then echo "DEV_FLOW_CODEX=off"; return 3; fi
  if ! command -v "$CODEX" >/dev/null 2>&1; then echo "codex が見つからない"; return 3; fi
  if ! "$CODEX" login status >/dev/null 2>&1; then echo "codex にログインしていない（codex login）"; return 3; fi
  echo "ok: $("$CODEX" --version 2>/dev/null)"
}

cmd_start() {
  [ $# -eq 4 ] || usage
  local kind="$1" cwd="$2" prompt="$3" out="$4" mode schema preamble
  case "$kind" in
    impl) mode="workspace-write"; schema="$HERE/schema-impl-review.json"; preamble="$HERE/preamble-impl.md" ;;
    spec) mode="read-only";       schema="$HERE/schema-spec-review.json"; preamble="$HERE/preamble-spec.md" ;;
    *) usage ;;
  esac
  [ -d "$cwd" ] || die "cwd が無い: $cwd"
  [ -f "$prompt" ] || die "プロンプトが無い: $prompt"
  cmd_available >/dev/null || die "codex が使えない: $(cmd_available)"

  local job
  job="$(mktemp -d "${TMPDIR:-/tmp}/dev-flow-codex.XXXXXX")" || die "ジョブディレクトリを作れない"
  cwd="$(cd "$cwd" && pwd)"
  out="$(abspath "$out")" || die "出力先を作れない: $out"
  printf '%s\n' "$kind" > "$job/kind"
  printf '%s\n' "$cwd"  > "$job/cwd"
  printf '%s\n' "$out"  > "$job/out"
  git_status "$cwd" > "$job/before"
  cat "$preamble" "$prompt" > "$job/prompt.md"

  local args=(exec -C "$cwd" -s "$mode" --ephemeral --skip-git-repo-check --color never
              --output-schema "$schema" -o "$job/last.json")
  [ "$kind" = impl ] && args+=(-c sandbox_workspace_write.network_access=true)
  [ -n "${DEV_FLOW_CODEX_MODEL:-}" ] && args+=(-m "$DEV_FLOW_CODEX_MODEL")
  local effort="${DEV_FLOW_CODEX_EFFORT-high}"
  [ -n "$effort" ] && args+=(-c "model_reasoning_effort=\"$effort\"")
  args+=(-)

  {
    echo '#!/bin/bash'
    printf 'cd %q || exit 99\n' "$cwd"
    printf 'timeout -k 30 %q %q' "${DEV_FLOW_CODEX_TIMEOUT:-1800}" "$CODEX"
    printf ' %q' "${args[@]}"
    printf ' < %q > %q 2>&1\n' "$job/prompt.md" "$job/log"
    printf 'echo $? > %q\n' "$job/exit"
  } > "$job/run.sh"

  setsid nohup bash "$job/run.sh" >/dev/null 2>&1 < /dev/null &
  echo $! > "$job/pid"
  printf '%s\n' "$job"
}

# impl レビューが worktree に残した変更（ミューテーション確認の戻し忘れ等）を元に戻す。
# 開始前から汚れていたパスには触らない。戻したパスを 1 行ずつ出す
restore_worktree() {
  local job="$1" cwd top line p
  cwd="$(cat "$job/cwd")"
  top="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || return 0
  git_status "$cwd" > "$job/after"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    grep -qxF -- "$line" "$job/before" && continue
    p="$(status_path "$line")"
    case "$p" in /*|../*|*/../*) continue ;; esac
    if [ "${line:0:2}" = "??" ]; then
      rm -f -- "$top/$p"
    else
      git -C "$top" checkout HEAD -- "$p" 2>/dev/null || git -C "$top" checkout -- "$p" 2>/dev/null || continue
    fi
    printf '%s\n' "$p"
  done < "$job/after"
}

cmd_wait() {
  [ $# -ge 1 ] || usage
  local job="$1" limit="${2:-540}" waited=0 code kind out restored
  [ -f "$job/pid" ] || die "ジョブが無い: $job"
  out="$(cat "$job/out")"
  if [ -f "$job/done" ]; then cat "$job/done"; return 0; fi

  while [ ! -f "$job/exit" ]; do
    if ! kill -0 "$(cat "$job/pid")" 2>/dev/null; then
      sleep 1; [ -f "$job/exit" ] && break
      die "codex のプロセスが結果を残さずに終わった（ログ: $job/log）"
    fi
    if [ "$waited" -ge "$limit" ]; then
      echo "running: ${waited} 秒待った。もう一度 wait する（ログ: $job/log）"
      return 124
    fi
    sleep 5; waited=$((waited + 5))
  done

  kind="$(cat "$job/kind")"
  restored="[]"
  if [ "$kind" = impl ]; then
    restored="$(restore_worktree "$job" | jq -R . | jq -sc .)"
  fi

  code="$(cat "$job/exit")"
  [ "$code" = 124 ] || [ "$code" = 137 ] && die "codex がタイムアウトした（${DEV_FLOW_CODEX_TIMEOUT:-1800} 秒。ログ: $job/log）"
  [ "$code" = 0 ] || die "codex exec が失敗した（exit $code）: $(tail -n 5 "$job/log" | tr '\n' ' ')"
  jq -e '.status == "approved" or .status == "changes_requested"' "$job/last.json" >/dev/null 2>&1 \
    || die "最終回答が JSON でない、または status が無い（$job/last.json）"

  # blocker / major があるのに approved なら changes_requested に直す（レビュアープロンプトの判定規則）
  jq --argjson restored "$restored" '
    (if (.findings // []) | any(.severity == "blocker" or .severity == "major")
     then .status = "changes_requested" else . end)
    + {engine: "codex", restored_files: $restored}' "$job/last.json" > "$out.tmp" && mv "$out.tmp" "$out" \
    || die "出力を書けない: $out"

  jq -r --arg out "$out" '"done: status=\(.status) 指摘=\((.findings // .issues // []) | length) 件 戻したファイル=\(.restored_files | length) 件 → \($out)"' "$out" \
    | tee "$job/done"
}

case "${1:-}" in
  available) shift; cmd_available "$@" ;;
  start)     shift; cmd_start "$@" ;;
  wait)      shift; cmd_wait "$@" ;;
  *) usage ;;
esac
