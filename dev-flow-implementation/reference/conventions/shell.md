# シェルスクリプト（Bash）

`tech_stack.language` が Shell / Bash のとき、または Infra グループの差分に `*.sh` / `*.bats` があるときに注入する。インフラの結合テスト（nginx・systemd・ネットワーク設定を実際に変えて確かめるテスト）の書き方は後半の「テスト」を読む。テスト共通のルールは `testing.md`。

## 書き方（implementer 向け）

### 命名・構成
- 先頭は `#!/bin/bash`（または `#!/usr/bin/env bash`）と `set -euo pipefail`。`sh` 互換が要件でない限り Bash 前提で書く
- 変数は必ずダブルクォートで囲む（`"$var"` / `"${arr[@]}"`）。変数の直後に日本語などのマルチバイト文字が続くときは `${var}` と書く
- 関数内の変数は `local`。定数は大文字、ローカル変数は小文字
- 一時ファイルは `mktemp` で作り、`trap 'rm -f "$tmp"' EXIT` で消す
- GNU / BSD で挙動が違うコマンド（`sed -i`、`date -d`、`stat -c`）は、対象環境を限定するか両対応のヘルパーに寄せる

### エラー処理
- 失敗したら**非 0 で終わる**。`|| true` で握りつぶすのは、失敗してよい理由をコメントに書いた場合だけ
- エラーメッセージは stderr（`>&2`）に出す
- `cd` は `cd dir || exit 1`、パイプの途中の失敗は `pipefail` で拾う

### 並行処理・非同期
- バックグラウンドジョブ（`&`）は `wait` で回収し、終了コードを確認する
- 待ち合わせは固定秒数の `sleep` ではなく、上限付きのポーリング（`for i in $(seq 30); do check && break; sleep 1; done`）

### 依存・境界
- 外部入力（引数・環境変数・ファイルの中身）を `eval` やコマンド文字列の組み立てに使わない
- シークレットをスクリプトに書かない。環境変数か、権限を絞ったファイルから読む

### テスト（インフラの結合テスト）
- **状態を壊す操作（サービス停止・設定ファイルの書き換え・iptables の変更）の前に `trap restore EXIT` を置く**。途中で失敗しても環境が元に戻るようにする（bats は `teardown`）
- **復元の失敗はテストの失敗にする**。`|| echo "WARN: 復元に失敗"` で流さない
- 期待値はテスト定義書のリテラルにする。`[ "$x" = "$x" ]` のように同じ値どうしを比べない。「インストール済みのバージョン」と比べるなら、比較対象はパッケージマネージャ（`dpkg-query -W -f='${Version}' nginx`）から取る
- 設定変更・プロビジョニングのテストでは、**同じ処理を 2 回実行し、2 回目に何も変わらない**ことも確かめる（設定ファイルの行が重複しない、Ansible の 2 回目が `changed=0`。`maintainability.md` の `maint/idempotent`）
- `grep -c` は**一致した行数**を返す。1 行に複数回出る値の出現回数なら `grep -o PATTERN | wc -l`
- アドレスの照合は IPv4 射影 IPv6（`::ffff:192.0.2.1`）でも来ることを前提にする
- 実行はクリーンなコンテナ（例: `docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/w" -w /w debian:13 …`）で行う。`--user` を付けないと root 所有のファイル（`~/.cache` 等）が残り、以後の CI が壊れる

## レビューチェックリスト（reviewer 向け）

| ルール ID | 重大度 | 確認内容 | 確認方法 |
|---|---|---|---|
| `sh/eval-input` | blocker | 外部入力を `eval` / `bash -c` / コマンド文字列の組み立てに使っていない | `grep -nE '\beval\b\|bash -c\|sh -c'` |
| `sh/secret-hardcode` | blocker | パスワード・トークンがスクリプトに書かれていない | `grep -niE 'pass(word)?=\|token=\|secret='` |
| `sh/strict-mode` | major | `set -euo pipefail`（または同等のエラー処理）がある | 先頭 10 行 |
| `sh/shellcheck` | major | `shellcheck -S warning` が 0 件 | 標準コマンド |
| `sh/quote` | major | 変数展開がクォートされている | shellcheck の SC2086 / SC2046 |
| `sh/restore-trap` | major | 状態を壊す操作の前に `trap … EXIT`（bats は `teardown`）で復元している | `test-lint.py` の `test/restore-trap` WARN と、`systemctl stop` / `sed -i` / `iptables` の周辺 |
| `sh/silent-failure` | major | 失敗を `\|\| true` / `\|\| echo WARN` で理由なく握りつぶしていない | `grep -nE '[\|]{2} *(true\|echo\|:)'` |
| `sh/tmp-cleanup` | minor | `mktemp` で作ったファイルを `trap` で消している | `grep -n mktemp` |
| `sh/portable` | minor | GNU / BSD で挙動が違うコマンドの使い方が対象環境に合っている | `sed -i`、`date -d`、`stat -c` |

## 標準コマンド（tech_stack が空のときのフォールバック）

| 用途 | コマンド |
|---|---|
| format | `shfmt -d -i 2 .` |
| lint | `shellcheck -S warning $(git ls-files '*.sh' '*.bats')` |
| 型検査 | —（なし） |
| test | `bats -r -j "$(nproc)" --no-parallelize-within-files tests/`（`-j` には GNU parallel が要る。無ければ `bats -r tests/`。bats が無ければ `for t in tests/*_test.sh; do bash "$t" || exit 1; done`） |

## 出典と対象バージョン

このファイルは執筆時点（2026-09）の知識で書かれている。`verified_against` と違うバージョンでは [version-check.md](version-check.md) の手順で照合する。

| 項目 | 出典 | 備考 |
|---|---|---|
| verified_against | Bash 5.2 / ShellCheck 0.10 / bats-core 1.11 | |
| `set -euo pipefail`・クォート | https://www.gnu.org/software/bash/manual/bash.html 、https://www.shellcheck.net/wiki/SC2086 | |
| スタイル全般 | https://google.github.io/styleguide/shellguide.html | `[opinion]` |
| `trap` による後始末 | https://www.gnu.org/software/bash/manual/bash.html#index-trap | |
| `grep -c` は行数 | https://www.gnu.org/software/grep/manual/grep.html#General-Output-Control | |
| IPv4 射影 IPv6 アドレス | https://www.rfc-editor.org/rfc/rfc4291#section-2.5.5.2 | |
| bats | https://bats-core.readthedocs.io/ | |
