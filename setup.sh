#!/bin/bash
# dev-flow スキル集のセットアップ
#   1. ~/.claude/skills/ に各スキルへのシンボリックリンクを作成
#      ~/.claude/agents/ に cost プロファイル用のステージエージェント定義へのリンクを作成
#   2. ~/.claude/settings.json に dev-flow の hooks を登録（--no-hooks で省略）
#
# 使い方: bash setup.sh [--no-hooks]
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
# 旧版の退避先。~/.claude/skills/ 配下に置くと Claude Code がスキルとして読み込んでしまうので外に出す
BACKUP_DIR="$HOME/.claude/skills-backup/$(date +%Y%m%d%H%M%S)"
SETTINGS="$HOME/.claude/settings.json"
HOOKS_JSON="$REPO_DIR/dev-flow/hooks/hooks.json"

INSTALL_HOOKS=true
for arg in "$@"; do
  case "$arg" in
    --no-hooks) INSTALL_HOOKS=false ;;
    -h | --help)
      sed -n '2,6p' "$0"
      exit 0
      ;;
    *)
      echo "不明なオプション: $arg" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. スキルのシンボリックリンク
# ---------------------------------------------------------------------------
mkdir -p "$SKILLS_DIR"

for dir in "$REPO_DIR"/dev-flow*/; do
  dir="${dir%/}"
  name="$(basename "$dir")"
  target="$SKILLS_DIR/$name"

  if [ -e "$target" ] && [ ! -L "$target" ]; then
    mkdir -p "$BACKUP_DIR"
    echo "バックアップ: $target -> $BACKUP_DIR/$name"
    mv "$target" "$BACKUP_DIR/$name"
  fi
  # 旧 setup.sh が skills/ 配下に作った *.bak はスキルとして誤認識されるので同じ退避先へ移す
  if [ -d "$target.bak" ]; then
    mkdir -p "$BACKUP_DIR"
    echo "移動: $target.bak -> $BACKUP_DIR/$name"
    mv "$target.bak" "$BACKUP_DIR/$name"
  fi

  # -n: target が既にディレクトリへのリンクでも、その中に新しいリンクを作らず置き換える
  ln -sfn "$dir" "$target"
  echo "リンク作成: $target -> $dir"

  # 旧 setup.sh（ln -sf）が作ってしまった自己参照リンクを掃除
  if [ -L "$dir/$name" ]; then
    rm "$dir/$name"
    echo "掃除: $dir/${name}（旧バージョンの自己参照リンク）"
  fi
done

chmod +x "$REPO_DIR"/dev-flow/hooks/*.sh "$REPO_DIR"/dev-flow-*/scripts/*.sh 2>/dev/null || true

# cost プロファイルのステージエージェント（stage-*-agent）定義。
# ~/.claude/agents/ を新しく作った場合、起動中の Claude Code には再起動するまで認識されない
AGENTS_DIR="$HOME/.claude/agents"
mkdir -p "$AGENTS_DIR"
for f in "$REPO_DIR"/agents/stage-*-agent.md; do
  target="$AGENTS_DIR/$(basename "$f")"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    mkdir -p "$BACKUP_DIR"
    echo "バックアップ: $target -> $BACKUP_DIR/"
    mv "$target" "$BACKUP_DIR/"
  fi
  ln -sfn "$f" "$target"
  echo "リンク作成: $target -> $f"
done

# ---------------------------------------------------------------------------
# 2. hooks の登録
# ---------------------------------------------------------------------------
if [ "$INSTALL_HOOKS" = true ]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "警告: jq が見つからないため hooks の登録をスキップします（hooks 自体も jq を使用します）" >&2
  else
    mkdir -p "$(dirname "$SETTINGS")"
    [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

    if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
      echo "エラー: $SETTINGS が不正な JSON です。hooks の登録を中止します" >&2
      exit 1
    fi

    backup="$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
    cp "$SETTINGS" "$backup"

    # 既存の dev-flow hooks（command に dev-flow/hooks/ を含むグループ）を取り除いてから追加する（冪等）
    tmp="$(mktemp)"
    jq -s '
      def strip_devflow:
        map(select(((.hooks // []) | any(.command // "" | tostring | test("dev-flow/hooks/"))) | not));
      .[0] as $cur | .[1].hooks as $new
      | $cur
      | .hooks = ((.hooks // {}) | with_entries(.value |= strip_devflow))
      | reduce ($new | to_entries[]) as $e (.; .hooks[$e.key] = ((.hooks[$e.key] // []) + $e.value))
    ' "$SETTINGS" "$HOOKS_JSON" > "$tmp"
    mv "$tmp" "$SETTINGS"

    echo "hooks 登録: ${SETTINGS}（バックアップ: ${backup}）"
    echo "  SessionStart / PreToolUse(Agent) / PostToolUse(Write|Edit, Agent) / Stop"
    echo "  Slack 通知を有効にする場合は settings.json の env に DEV_FLOW_SLACK_CHANNEL を設定してください"
  fi
else
  echo "hooks 登録: スキップ（--no-hooks）"
fi

echo ""
echo "セットアップ完了"
