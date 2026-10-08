# エージェント起動前のプロンプト注入詳細

`dev-flow-implementation/SKILL.md` STEP B で各エージェントを起動する前に、プロンプトへ追加注入する要素の詳細仕様。

## Contents
- 言語・フレームワーク規約の注入
- 実行環境ノートの注入
- memory フィードバックの注入
- レビュー指摘・テスト失敗の memory 保存
- ファイルスコープガードレール
- Sonnet 昇格時のプロンプト追記

## 言語・フレームワーク規約の注入

`state.json.tech_stack.language` / `.framework` から `dev-flow-implementation/SKILL.md` STEP B「言語・フレームワーク規約」の表でファイルを決め、Read して各プレースホルダーに入れる：

| プレースホルダー | 入れるもの | 渡す相手 |
|---|---|---|
| `{CONVENTIONS}` | 各ファイルの「## 書き方」セクション（言語 → フレームワーク → `doc/conventions.md` の順に連結） | implementer（Dev / QA） |
| `{REVIEW_CHECKLIST}` | 各ファイルの「## レビューチェックリスト」の表を連結 | reviewer |
| `{STANDARD_COMMANDS}` | 最も具体的なファイル（フレームワーク > 言語）の「## 標準コマンド」 | 両方 |

`doc/process/conventions_verified.md`（version-check の結果）があれば、「変わった項目」「新しい推奨」を `{CONVENTIONS}` と `{REVIEW_CHECKLIST}` の先頭に、「非推奨になった API」を `{REVIEW_CHECKLIST}` に `version/deprecated-*`（major）として追加する。

`doc/conventions.md` にはセクション見出しが無くてもよい。全文を `{CONVENTIONS}` と `{REVIEW_CHECKLIST}` の両方の末尾に「## プロジェクト固有規約（最優先）」として付ける。

reviewer の findings で同じ `rule` が 3 回以上出た場合は下記「memory 保存」のフォーマットで保存する。`rule` がキーになるので表記を揃えること。

## 実行環境ノートの注入

規約（どう書くか）とは別に、**この環境でコマンドを動かすための注意**を全 implementer / reviewer / test-runner に共通で渡す。`doc/process/environment.md` があれば全文を「## 実行環境ノート（コマンド実行前に必ず読む）」としてプロンプト冒頭に注入する。無ければ省略する。

`environment.md` は requirements または bootstrap ステージで tech_stack を確定した際、あるいは implementation の最初のグループでオーケストレーターが環境差異に気付いた時点で作る。書くべき典型例：

```markdown
# 実行環境ノート

- Node.js: シェル既定は v10（古い）。**すべての node / npm / npx コマンドの前に `source ~/.nvm/nvm.sh && nvm use 22 &&` を付ける**（nvm は非対話シェルで自動ロードされない）
- PHP: 8.5（`composer.json` の `^8.3` より新しい。lock ファイルは 8.5 で解決済みなので CI も 8.5 にする）
- composer: `/opt/homebrew/bin/composer`。`create-project` / `install` は `--no-interaction` を付け、`timeout 180` で囲む
- ポート: backend 8000 / frontend 5173。E2E 実行前に `lsof -ti:8000 -sTCP:LISTEN | xargs -r kill` で残留プロセスを止める
- worktree は `vendor/` `node_modules/` `.env` を含まない（gitignore）。各 worktree で最初に `composer install` / `npm install` / `.env` 作成が要る
- 長時間コマンドは必ず `timeout N` を付ける。3 分応答が無ければ中断して別手段
- シェル: Bash ツールは zsh で動く。変数は必ずクォートし、変数の直後に文字が続くときは `${var}` と書く（zsh は `$file:t` を「ファイル名部分」の修飾子として解釈する）。heredoc の区切り文字は本文に出てこない一意な名前（`EOF_SCRIPT` 等）にする
- Docker: コンテナで lint / テストを動かすときは `--user "$(id -u):$(id -g)"` を付ける。付けないと root 所有の `~/.cache` やビルド成果物が残り、同じマシンの CI ランナーが権限エラーで壊れる
- ポート 80: ホストで使用中。`:80` を bind する Docker のテストはローカルでは失敗するので CI で確認する（ローカルでは別ポートに割り当てて試す）
```

`environment.md` を作るとき・最初のグループを起動する前に、オーケストレーターが次を実際に確かめて書く（推測で書かない）：

| 確認 | コマンド | 書くこと |
|---|---|---|
| シェル | `echo "$SHELL"; ps -p $$ -o comm=` | zsh ならクォート・`${var}` の注意 |
| ランタイムのバージョン | `node -v` / `php -v` / `python3 -V` / `go version` | 要求バージョンと違えば切り替え方法 |
| 使用中のポート | `ss -ltn`（macOS は `lsof -iTCP -sTCP:LISTEN -n -P`） | テストが使うポートとの衝突 |
| Docker の実行ユーザー | `docker info --format '{{.SecurityOptions}}'`、`id -u` | rootless か。`--user` の要否 |
| ホームの権限 | `find ~ -maxdepth 2 -user root 2>/dev/null` | root 所有のファイルが既に残っていないか（あれば人間に報告） |

これが無いと、オーケストレーターが毎回すべてのエージェントのプロンプトに同じ注意を手書きすることになり、書き漏らしたエージェントがハング・失敗する（実例: nvm 未指定で Vite が動かない、`timeout` 無しで `composer create-project` が固まる）。

## memory フィードバックの注入

各エージェントを起動する前に、プロジェクトの memory ディレクトリから関連する feedback を読み込んでプロンプト冒頭に注入する。

```bash
MEMORY_DIR="~/.claude/projects/$(pwd | sed 's|/|-|g')/memory"
ls "${MEMORY_DIR}/feedback_review_infra.md" 2>/dev/null
ls "${MEMORY_DIR}/feedback_review_app.md" 2>/dev/null
ls "${MEMORY_DIR}/feedback_test_failures.md" 2>/dev/null
```

ファイルが存在する場合、プロンプトの先頭に以下を追記：

```
## 過去のレビューで指摘された再発項目（必ず確認してから実装・レビューすること）

{feedback_review_infra.md または feedback_review_app.md の内容}
```

## レビュー指摘・テスト失敗の memory 保存（STEP D / STEP H 後）

レビュー指摘が 3 回以上繰り返されたパターン、または人間によるマージ後修正があった場合、以下のフォーマットで memory に保存する：

```markdown
---
name: feedback-review-infra-{date}
description: Infra レビューで再発する指摘パターン（{date} 記録）
metadata:
  type: feedback
---

## 再発指摘パターン

- **{指摘カテゴリ}**: {具体的な指摘内容}
  - 発生回数: {N}回
  - 典型例: {コード例または説明}
  - 対処方法: {推奨する実装アプローチ}
```

## ファイルスコープガードレール

各エージェントには **作業対象ファイルパスの制約** を明示してプロンプトに含める：

| エージェント | 作業許可ディレクトリ | 禁止ディレクトリ例 |
|---|---|---|
| Dev (Infra) | `{MAIN_DIR}/../worktree-dev-infra-group-N/` 配下のインフラ関連ファイル | フロントエンド、アプリ層 |
| Dev (App) | `{MAIN_DIR}/../worktree-dev-app-group-N/` 配下のアプリ関連ファイル | Terraform、インフラ設定 |
| QA (Infra) | `{MAIN_DIR}/../worktree-qa-infra-group-N/` 配下のインフラテスト | アプリテスト |
| QA (App) | `{MAIN_DIR}/../worktree-qa-app-group-N/` 配下のアプリテスト | インフラテスト |

**プロンプトに追記する文言:**

```
【ファイルスコープ制限】
担当タスク（{DEV/QA_TASKS}）に直接関係するファイルのみ変更すること。
- 許可: {作業許可ディレクトリのパターン}（例: `*.tf`, `pkg/auth/**`, `tests/auth/**`）
- 禁止: 担当範囲外のファイル（例: フロントエンド、他チームのモジュール）

タスクに関係ないファイルを変更しそうになった場合は変更せず、`status: "blocked"`, `blocker_type: "out_of_scope_change"` の JSON を最終回答として返して報告してください。
```

## Sonnet 昇格時のプロンプト追記（cost プロファイルのみ）

```
【モデル昇格通知】
Haiku による修正試行が上限に達したか、設計レベルの指摘が含まれるため Sonnet に昇格しました。
以下のレビュー指摘履歴を参考に、より高度な判断で問題を解決してください。

### Haiku 試行履歴
{Haiku の試行回数と主な失敗内容}

### 未解決の指摘
{レビュアーからの指摘内容}
```
