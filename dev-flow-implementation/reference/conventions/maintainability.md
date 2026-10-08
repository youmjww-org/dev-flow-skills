# 保守性（言語横断）

言語に関係なく**常に**注入する（`testing.md` と同じ扱い）。Dev implementer と Dev reviewer に渡す。閾値（複雑度・行数・ネスト）は `coverage_threshold` と同じく `doc/conventions.md` で変更できる。言語別ファイルの規約と矛盾する場合は言語別ファイルを優先する。

## 目次

- 書き方（implementer 向け）
- レビューチェックリスト（reviewer 向け）
- 標準コマンド（tech_stack が空のときのフォールバック）
- 出典と対象バージョン

## 書き方（implementer 向け）

### 書く前に探す（車輪の再発明をしない）
- 新しい関数・クラス・ヘルパーを書く前に、**同じことをするものが既にないか探す**。順番は ① プロジェクト内の既存コード（`grep -rn` で関数名・処理の特徴的なキーワード・似た定数を検索）→ ② フレームワークの機能 → ③ 言語の標準ライブラリ → ④ 既に入っている依存パッケージ
- 日付・時刻の計算、バリデーション、エスケープ・サニタイズ、パス操作、CSV / JSON / YAML の読み書き、リトライ、並行数の制御、認証・セッション、暗号・ハッシュは**自作しない**。自作が必要な理由があれば完了 JSON の `uncertainty_points` に書く
- 新しい依存パッケージを足す前に、既存の依存と標準ライブラリで賄えないか確認する。足すなら理由を PR 説明に書く

### DRY（同じ知識を一箇所に）
- 重複を避けるのは「コードの見た目」ではなく**知識**（業務ルール・バリデーション条件・定数・変換規則・エラーメッセージ）。同じ条件式やリテラルを 2 か所目に書こうとしたら、既存の定義を使うか一箇所に移す
- **認証・認可・入力検証・エスケープのロジックは絶対にコピーしない**。片方だけ直されて穴になる
- ただし偶然似ているだけの処理を無理に共通化しない（誤った抽象化の方が重複より高くつく）。目安は 3 回目で抽出（rule of three）。フラグ引数で分岐させる共通関数ができたら、共通化を疑う
- テストコードは読みやすさを優先し、ある程度の重複を許す（テストの DRY は fixture / ヘルパーに留める）

### ドメインごとの独立
- ディレクトリ・モジュールは**ドメイン（業務の単位）ごと**に分ける。技術レイヤー（controllers / models / services）だけで切らない。プロジェクトに既存の構成があればそれに合わせる
- 他ドメインの**内部**に触れない。他ドメインのテーブルを直接 SELECT / UPDATE しない、内部モデル・private 関数を import しない。そのドメインの公開インターフェース（サービス・API・イベント）を経由する
- モジュール間に**循環依存を作らない**。A → B → A になったら、共通部分を下位モジュールに出すかイベントで逆向きの依存を切る
- `shared/` `common/` `utils/` に置くのはドメイン知識を持たない汎用処理だけ。「注文の合計金額計算」は shared ではなく注文ドメインに置く

### 用語をそろえる
- クラス・関数・変数・テーブル・設定キーの名前には、要件定義書の「用語集」と同じ言葉を使う。用語集が「ロック」なら `lock` に統一し、同じ概念に `suspend` / `ban` / `block` を混ぜない
- 用語集に無い業務上の概念を新しく名付けたら、完了 JSON の `uncertainty_points` に「用語追加: {用語}: {意味}」と書く（要件定義書への追加は人間が判断する）

### 環境の値をコードに埋め込まない
- IP アドレス・ホスト名・VMID・ポート・絶対パス・URL・バケット名などの**環境で変わる値**は、変数・inventory・設定ファイル・環境変数に置き、コードやテンプレートの本文に直接書かない。同じ値を複数ファイルに書かない（ホストの移設や ID の振り直しで一斉修正になる）
- テストの期待値は例外。テスト定義書に書かれた値をリテラルで書く（`testing.md`）

### 何度実行しても同じ結果にする（冪等性）
- 設定変更・プロビジョニング・マイグレーション・デプロイの処理は、**2 回実行しても壊れず、2 回目は何も変えない**ようにする。追記（`>>`・`lineinfile` の無条件追加）で行が重複しない、既にある資源を作ろうとして失敗しない、既に止まっているサービスを止めてエラーにならない
- Ansible は `changed_when` / `creates` / `state:` で変更の有無を正しく報告させる。`command` / `shell` モジュールを使うなら `changed_when` を必ず書く
- テストで 2 回実行し、2 回目の変更が 0 件であることを確かめる（Ansible なら 2 回目の `changed=0`）

### 小さく保つ
- 関数の循環的複雑度・行数・ネストの深さは `doc/conventions.md` の値（既定: 複雑度 `complexity_threshold: 10`、行数 `function_max_lines: 60`、ネスト `max_nesting: 4`）以下にする。超えたら、早期 return・関数の抽出・データ駆動（分岐の表）で減らす
- 1 つの関数は 1 つのことをする。「A して B する」関数は分ける
- 意味のある数値・文字列（上限件数・タイムアウト・ステータス名）は名前付き定数か設定にする

### 使わないものを残さない
- 呼ばれなくなった関数・export・設定・フィーチャーフラグ・ファイルは、置き換えたその変更で消す。コメントアウトで残さない（履歴は git にある）
- 作り直したときに古い実装を並べて残さない（`xxxV2` / `xxx_old` / `new_xxx`）

### 既存のやり方に合わせる
- エラー応答の形、依存の渡し方、ログの書き方、ディレクトリ構成、テストの置き場は、**同じ種類の既存コードを 1 つ読んでから**同じやり方で書く。より良いやり方があると思っても、1 か所だけ変えない（変えるなら全体の置き換えとして人間に提案する）

## レビューチェックリスト（reviewer 向け）

| ルール ID | 重大度 | 確認内容 | 確認方法 |
|---|---|---|---|
| `maint/duplicated-security-logic` | blocker | 認証・認可・入力検証・エスケープのロジックがコピーされていない | 差分の認可チェック・検証条件を `grep -rn` し、同じ条件がリポジトリ内に複数あれば共通化を要求 |
| `maint/reinvented-wheel` | major | プロジェクト内の既存関数・フレームワーク・標準ライブラリ・既存依存で済む処理を自作していない | 差分で追加した関数ごとに、名前と処理の特徴的なキーワードで `grep -rn` し、フレームワーク・標準ライブラリの該当機能を確認する。**実際に探してから指摘する**（`fix` に使うべき既存の関数・API 名を書く） |
| `maint/duplicated-knowledge` | major | 同じ業務ルール・バリデーション条件・定数・変換規則が複数箇所に書かれていない | 差分で追加した条件式・リテラルを `grep -rn` してリポジトリ内の出現数を数える。2 か所以上なら一箇所に寄せる |
| `maint/domain-boundary` | major | 他ドメインの内部（テーブル・内部モデル・private 関数）に直接触れず、公開インターフェース経由 | 差分の import / use 文と SQL のテーブル名を見て、所属ドメイン外のものを列挙する |
| `maint/circular-dependency` | major | モジュール間の循環依存が無い | 差分の import で A → B → A ができていないか。ツールがあれば「標準コマンド」で確認 |
| `maint/idempotent` | major | 設定変更・プロビジョニング・マイグレーション・デプロイの処理が 2 回実行しても壊れず、2 回目は何も変えない | `>>` / 無条件の `lineinfile` / `command`・`shell` の `changed_when` 無し / `CREATE` の `IF NOT EXISTS` 無しを探す。テストに 2 回実行の確認があるか |
| `maint/hardcoded-env` | major | IP・ホスト名・VMID・ポート・絶対パス・URL がコードやテンプレートの本文に直接書かれていない（変数・inventory・設定経由） | 差分に対して `grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}\|:[0-9]{2,5}\b\|https?://\|/(home\|opt\|srv\|etc)/'`。テストの期待値は除く。同じ値が複数ファイルにあれば一箇所に寄せる |
| `maint/complexity` | major | 変更した関数の循環的複雑度・行数・ネストが `doc/conventions.md` の閾値（既定 10 / 60 行 / 4 段）以下 | 「標準コマンド」の複雑度ツール。無ければ分岐とネストを数える |
| `maint/glossary-terms` | minor | 名前が要件定義書の用語集と一致し、同じ概念に別の単語を混ぜていない | 要件定義書の用語集と、差分で追加した識別子を突き合わせる |
| `maint/dead-code` | minor | 使われなくなった関数・export・設定・フラグ・ファイル、コメントアウトしたコード、`_old` / `V2` の並存を残していない | 「標準コマンド」の未使用検出。差分で置き換えた関数の旧版を `grep -rn` |
| `maint/consistent-pattern` | minor | 同じ種類の処理（エラー応答・依存の渡し方・ログ・置き場）が既存コードと同じやり方 | 同じ種類の既存コードを 1 つ開いて比べる |
| `maint/shared-domain-leak` | minor | `shared` / `common` / `utils` にドメイン固有の知識を置いていない | 追加先パスと中身 |
| `maint/premature-abstraction` | minor | 1 か所でしか使わない抽象、偶然似ているだけの処理の共通化、フラグ引数で分岐する共通関数を作っていない | 新しい interface / 基底クラス / 共通関数の利用箇所数 |
| `maint/new-dependency` | minor | 新しい依存パッケージに理由がある（既存依存・標準ライブラリで賄えない） | `package.json` / `composer.json` / `go.mod` / `pyproject.toml` の diff と PR 説明 |
| `maint/magic-value` | minor | 意味のある数値・文字列が名前付き定数・設定になっている | 差分のリテラル |

## 標準コマンド（tech_stack が空のときのフォールバック）

どれも任意（入っていなければ reviewer が `grep` で代替する）。

| 用途 | コマンド |
|---|---|
| 重複検出 | `npx jscpd --min-lines 8 --reporters console <src>`（言語非依存） |
| 循環依存（TS/JS） | `npx madge --circular --extensions ts,tsx src` |
| 循環依存（PHP） | `vendor/bin/deptrac analyse`（レイヤー定義がある場合） |
| 循環依存（Go） | コンパイラが import cycle を拒否する（`go build ./...`） |
| 循環依存（Python） | `pylint --disable=all --enable=cyclic-import <pkg>` |
| 複雑度（TS/JS） | `npx eslint --rule '{"complexity":["error",10],"max-depth":["error",4],"max-lines-per-function":["error",60]}' <files>` |
| 複雑度（Go） | `gocyclo -over 10 .` |
| 複雑度（Python） | `radon cc -s -n C <pkg>`（C 以上 = 複雑度 11 以上） |
| 複雑度（PHP） | `vendor/bin/phpmd <src> text codesize` |
| 未使用（TS/JS） | `npx knip` |
| 未使用（Go） | `go run golang.org/x/tools/cmd/deadcode@latest ./...` |
| 未使用（Python） | `vulture <pkg>` |
| 冪等性（Ansible） | 同じ playbook を 2 回実行し、2 回目の `PLAY RECAP` が `changed=0` |

## 出典と対象バージョン

| 項目 | 出典 | 備考 |
|---|---|---|
| verified_against | 言語非依存 | ツールのオプションは各ツールの公式ドキュメントを参照 |
| DRY は「知識の重複」を避ける原則 | https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/ | 『達人プログラマー』 |
| 3 回目で抽出（rule of three） | https://en.wikipedia.org/wiki/Rule_of_three_(computer_programming) | `[opinion]` |
| 誤った抽象化は重複より高くつく | https://sandimetz.com/blog/2016/1/20/the-wrong-abstraction | `[opinion]` |
| ドメインごとの境界 | https://martinfowler.com/bliki/BoundedContext.html | |
| 循環依存を作らない（非循環依存関係の原則） | https://en.wikipedia.org/wiki/Acyclic_dependencies_principle | |
| jscpd | https://github.com/kucherenko/jscpd | |
| madge | https://github.com/pahen/madge | |
| 用語をそろえる（ユビキタス言語） | https://martinfowler.com/bliki/UbiquitousLanguage.html | |
| 冪等性・`changed_when` | https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_error_handling.html#defining-changed | |
| 循環的複雑度 | https://en.wikipedia.org/wiki/Cyclomatic_complexity | 閾値 10 は McCabe の提案値。`[opinion]` |
| ESLint `complexity` / `max-depth` / `max-lines-per-function` | https://eslint.org/docs/latest/rules/complexity | |
| knip | https://knip.dev/ | |
| deadcode（Go） | https://pkg.go.dev/golang.org/x/tools/cmd/deadcode | |
