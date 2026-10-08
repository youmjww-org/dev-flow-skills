#!/bin/bash
# グループ用の worktree を作り（既にあれば再利用）、gitignore されている依存物と .env を用意する。
#   使い方: ensure-worktree.sh <dev|qa> <infra|app> <グループ番号>
#   メインの作業ディレクトリ（リポジトリのルート）で実行する。作った worktree のパスを最後に出力する。
#
# 依存物を用意するのは、各 implementer / reviewer が composer install / npm install / .env 作成を
# やり直す時間を省くため。node_modules はシンボリックリンクだと Vite / Vitest のパス解決で
# 問題が出るのでコピーする。メイン側に依存物が無ければ何もしない（最初の基盤グループでは implementer が install する）。
set -euo pipefail

role="${1:?role (dev|qa) を指定してください}"
team="${2:?team (infra|app) を指定してください}"
group="${3:?グループ番号を指定してください}"
case "$role" in dev|qa) ;; *) echo "ensure-worktree: role は dev か qa: $role" >&2; exit 2 ;; esac
case "$team" in infra|app) ;; *) echo "ensure-worktree: team は infra か app: $team" >&2; exit 2 ;; esac

main_dir="$(pwd)"
path="${main_dir}/../worktree-${role}-${team}-group-${group}"
branch="${role}/${team}-group-${group}"

if git worktree list --porcelain | grep -qx "worktree $(cd "$(dirname "$path")" && pwd)/$(basename "$path")"; then
  echo "${role} (${team}) worktree 既存 → 再利用" >&2
else
  git worktree add "$path" -b "$branch" >&2
fi

# サブプロジェクト（backend/ frontend/ 等）も含めて、メイン側にある依存ディレクトリを同じ相対パスへコピーする
for dep in vendor node_modules .venv; do
  find "$main_dir" -maxdepth 3 -type d -name "$dep" -not -path "*/$dep/*" -not -path "*/worktree-*" 2>/dev/null | while read -r src; do
    rel="${src#"$main_dir"/}"
    [ -e "$path/$rel" ] || { mkdir -p "$(dirname "$path/$rel")"; cp -R "$src" "$path/$rel"; }
  done
done

# .env は .env.example から作る（メインの .env には秘密が入っている可能性があるのでコピーしない）
find "$path" -maxdepth 3 -name ".env.example" -not -path "*/node_modules/*" -not -path "*/vendor/*" 2>/dev/null | while read -r ex; do
  [ -e "${ex%.example}" ] || cp "$ex" "${ex%.example}"
done

cd "$path" && pwd
