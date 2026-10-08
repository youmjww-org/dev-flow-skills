# テストコード（言語横断）

言語に関係なく**常に**注入する。言語別ファイル（`go.md` 等）の「テスト」節はこのファイルの上に読む。Dev implementer / QA implementer / 両 reviewer に渡す。

## 目次

- Dev と QA のテスト分担
- 書き方（Dev implementer 向け: ユニットテスト）
- 書き方（QA implementer 向け: 仕様テスト）
- ミューテーション確認（提出前の自己検証）
- レビューチェックリスト（Dev reviewer と QA reviewer で分担。「担当」列を見る）
- 標準コマンド（分岐カバレッジ）
- 出典と対象バージョン

## Dev と QA のテスト分担

Dev と QA は別 worktree で並行して書く。**同じ種類のテストを両方が書くと同じパスのファイルが二重に生まれる**ので、種類とファイルの置き場で分ける：

| | Dev implementer が書く | QA implementer が書く |
|---|---|---|
| 種類 | **ユニットテスト**（ホワイトボックス） | **仕様テスト**（ブラックボックス） |
| 対象 | 自分が書いたクラス・関数・コンポーネント単体。Form Request / Resource / サービス / フック / 純粋関数 | テスト定義書の TC-NNN。エンドポイント（HTTP 経由）・画面（App 全体をレンダリング）・E2E |
| 網羅の基準 | **分岐網羅**（`if` / `switch` / 早期 return / `catch` / 三項演算子ごとに 1 ケース、境界値含む） | **TC 網羅**（テスト定義書の全 TC を 1 つずつ。TC-ID をテスト名かコメントに書く） |
| 実装を見るか | 見る（自分が書いた） | 見ない（テスト定義書だけから書く。Dev 実装は別 worktree で並行中） |
| 置き場（例） | Laravel: `tests/Unit/**`。React: `src/components/X.test.tsx` `src/api/x.test.ts`（実装と同じディレクトリ・同じ基底名）。Go: `pkg/x/x_test.go`。Python: `tests/unit/` | Laravel: `tests/Feature/**`。React: `src/App.test.tsx`（結合）。E2E: `e2e/**`。Go: `tests/integration/` `*_integration_test.go`。Python: `tests/feature/` `tests/e2e/` |
| モック | 境界（HTTP・DB・時計）だけ。同一モジュール内はモックしない | 外部境界だけ（fetch・DB は実物か fake）。**実装の内部関数はそもそも知らないので触れない** |
| 完了 JSON の `result.coverage` | 変更した関数の**分岐カバレッジ**を書く（閾値ゲートの対象） | 書かなくてよい（実装が無い worktree では測れない。統合検証で測る） |

境界の判断に迷ったら「テスト定義書に TC-ID があるか」で決める。TC-ID があるものは QA、無い（実装の都合で生まれた分岐）ものは Dev。Dev が「この分岐は仕様レベルで TC があるべき」と思ったら、完了 JSON の `uncertainty_points` に「TC 不足: {関数}: {分岐条件}」と書き、QA がテスト定義書に TC を足す（`status: added`）。

この分担にした理由（実戦での事例）: QA は実装を見ずに書くため、`TaskResource` が日時を UTC で返すバグ（`+09:00` で返す仕様）を Feature テストでは検出できず、レビュアーが手動で見つけた。Dev が Resource のユニットテストを書いていれば自分で気付けた。逆に Dev が「動作確認のため」に Feature テストまで書いたところ、QA と同じパス（`tests/Feature/TaskApiTest.php`）にファイルが生まれてマージ時にコンフリクトした。

## 書き方（Dev implementer 向け: ユニットテスト）

- 自分が書いた・変更した**関数・クラス・コンポーネントごと**にテストファイルを対で置く（上の表の置き場）
- 分岐ごとに 1 ケース。`if` / `switch` / 早期 `return` / `catch` / 三項演算子 / `??` / オプショナルチェーンの分岐を数え、それぞれを通る入力を用意する。境界値（0・1・上限・上限+1・空・null・空白のみ）は分岐の一種
- 例外・エラーの**種類**まで検証する（`ValidationException` が投げられる、`NotFoundError` のインスタンスである、HTTP 422 のレスポンスである）
- 出力の**形式**まで検証する（日時が `+09:00` 付き ISO 8601 か、`{ data: {...} }` でラップされているか）。仕様書に形式の記述があるものは特に
- フレームワークの機能（Form Request のバリデーション、Resource のシリアライズ、React のレンダリング）は実物で動かす。フレームワーク自体をモックしない
- 期待値はリテラルで書く。実装と同じ計算式で期待値を作らない
- テスト名は「条件 → 期待結果」（`it('returns 422 when title is whitespace only')`、`test('空白のみのタイトルはトリム後に required で 422 になる')`）
- 完了 JSON の `result.coverage.changed_functions_below_threshold` に、閾値（`doc/conventions.md` の `coverage_threshold`、既定 0.80）未満の関数を列挙する。空でなければレビューに進めない

## 書き方（QA implementer 向け: 仕様テスト）

### 何をテストするか
- **テスト定義書の TC を 1 つずつ実装する**。TC-ID をテスト名またはコメントに書く（`// TC-003`）。compliance がこれで対応を検証する
- **正常系だけで終わらない**。公開関数・エンドポイントごとに、少なくとも 1 つの異常系（不正入力・権限なし・存在しないリソース・依存先の失敗）を書く。テスト定義書に異常系の TC が無ければ、**テストを省略せずテスト定義書に TC を追加する**（`status: added`）
- **仕様の分岐ごとに 1 ケース**。テスト定義書・要件定義書・API 仕様書に書かれた条件（バリデーションルール、ステータスコードの分かれ目、表示の切り替え条件）を数え、それぞれを通る TC があるか確認する。境界値（0・1・上限・上限+1・空・null）は仕様の分岐として扱う。**実装コードの `if` を数える必要はない**（それは Dev のユニットテストの責務。QA worktree に実装は無い）。Dev から `uncertainty_points` で「TC 不足」の申告があれば、その分岐を TC としてテスト定義書に追加（`status: added`）してから実装する
- 例外・エラーの**種類**まで検証する（「エラーになる」ではなく「`ErrNotFound` / HTTP 404 / `ValidationError` になる」）
- 副作用を検証する（DB に行が入った・メールが送られた・イベントが発火した）。戻り値だけ見て終わらない

### どう書くか
- **1 テスト 1 振る舞い**。1 つのテストで 5 個の assert を並べるなら、5 つの振る舞いを混ぜていないか疑う
- テストは**独立**。実行順序・他テストの残した状態・共有のグローバルに依存しない。並列実行しても通る
- **決定的**。`time.Now()` / 乱数 / ネットワーク / 環境変数に依存しない。時計は注入、外部は境界でフェイク
- モックするのは**境界だけ**（HTTP・DB・時計・メール・キュー）。テスト対象自身やその内部関数をモックしたら、それはテストではない
- 期待値は**リテラルで書く**。実装と同じ計算式で期待値を作ると常に通る（`expected := calc(x); assert(calc(x) == expected)`）
- テスト名は「条件 → 期待結果」が読める形（`TestLogin_WrongPassword_Returns401`、`it("returns 401 when password is wrong")`）。`test1` / `testCase` 禁止
- スナップショットテストは UI の構造確認にだけ使い、ロジックの検証に使わない

### やってはいけないこと
- **テストを消さない・スキップしない・コメントアウトしない**。通らないなら、プロダクションコードを直すか、テスト定義書の誤りとしてエスカレーションする。`t.Skip` / `it.skip` / `@pytest.mark.skip` / `markTestSkipped` は PR マージ時に hook が検出して止める
- **期待値を実行結果に合わせて書き換えない**。テストは仕様（テスト定義書）から書く。実装を動かして出た値を貼るのは「テスト」ではなく「記録」
- `assert true` / assert の無いテスト / `try { ... } catch {}` で例外を握りつぶすテストを書かない
- `sleep` でタイミングを合わせない。非同期は完了を待つ仕組み（`waitFor` / channel / `Eventually`）で

## ミューテーション確認（提出前の自己検証）

テストが通ることは、テストが**バグを検出できる**ことの証明にならない（常に通るテストも通る）。新しく書いた・変えたテストごとに、**そのテストが検出するはずの壊し方で実装を 1 か所壊し、テストが失敗することを確かめてから元に戻す**。実戦では、レビュアーがこの手順で「常に真の比較」「行数を数えていた `grep -c`」「WARN だけ出して通る復元処理」を見つけて 3〜4 往復の差し戻しになっていた。書いた本人が先にやれば 1 往復で済む。

| 誰が | どこで | いつ |
|---|---|---|
| Dev implementer | 自分の worktree（実装がある） | 完了 JSON を返す前 |
| QA implementer | STEP C.5 の統合検証で Dev ブランチを検証用マージした QA worktree（自分の worktree には実装が無い） | オーケストレーターが統合テスト全パスの後に `SendMessage` で依頼したとき |

壊し方の例: 条件の反転（`>` → `>=`、`!` の除去）、境界値の ±1、早期 return の削除、例外を投げずに握りつぶす、設定ファイルの 1 行削除（nginx の `allow` 行、`server_tokens off`）、ポート番号・`max_fails` の変更、パッケージのバージョン固定の変更。

- 壊したら**そのテストだけ**を実行し、失敗することを確認する。元に戻す（`git checkout -- <file>` / `git stash`）。**壊した状態をコミットしない**
- テストが失敗しなかった（変異が生き残った）ら、テストを強化する。期待値をリテラルにする、検証対象を狭める、観測点を変える
- 結果を完了 JSON の `result.mutation` に書く。reviewer はここから 1〜2 件を選んで再現する：

```json
"mutation": [
  {"test": "TC-068", "mutation": "restore() から systemctl start nginx を削除", "killed": true},
  {"test": "TestParseAllowList_IPv4Mapped", "mutation": "::ffff: の除去処理を削除", "killed": true}
]
```

## レビューチェックリスト（Dev reviewer と QA reviewer で分担。「担当」列を見る）

担当の凡例: **Dev** = Dev worktree のユニットテストを Dev reviewer が見る / **QA** = QA worktree の仕様テストを QA reviewer が見る / **両方** = それぞれの worktree で各 reviewer が見る。

「機械」列が **hook** のものは `test-lint.py`（Write / Edit 時）または `pr-merge-guard.sh`（PR マージ時）が機械的に判定する。reviewer はその結果（`flow.log` の `test_lint_failed`、hook の WARN）を前提にし、**判断が要るルール**に時間を使う。

| ルール ID | 重大度 | 機械 | 確認内容 | 確認方法（reviewer） |
|---|---|---|---|---|
| `test/no-delete` | blocker | hook（PR 時） | 既存テストが削除・コメントアウトされていない（移動は可） | hook が deny 済み。`test/commented-out` の WARN を確認 |
| `test/no-skip` | blocker | hook（Write 時・PR 時） | スキップ・無効化が追加されていない | hook が exit 2 / deny 済み |
| `test/expected-from-impl` | blocker | — | 期待値が実装の出力を貼ったものでない（テスト定義書の値と一致） | テスト定義書の「具体的な入出力値」と突き合わせ |
| `test/error-cases` | major | — | 変更した公開関数・エンドポイントごとに異常系が 1 つ以上ある | 関数ごとに正常系 / 異常系のテスト数を数える |
| `test/branch-coverage` | major | — | **Dev**: 変更した関数の `if` / `switch` / 早期 return / `catch` それぞれを通るユニットテストがある | Dev reviewer が実装の分岐を列挙して Dev worktree のユニットテストと突き合わせる。`result.coverage` の未到達分岐。無ければ `fix` に「ユニットテスト追加: {関数}: {分岐条件}」と書いて Dev implementer に差し戻す（QA には回さない） |
| `test/tc-coverage` | major | — | **QA**: テスト定義書の TC が仕様テストとして 1 つずつ実装されている（TC-ID で対応が取れる） | QA reviewer が frontmatter の `test_cases[].id` と QA worktree のテストの TC-ID を突き合わせる。欠けていれば QA implementer に差し戻す |
| `test/unit-vs-spec-split` | major | — | **両方**: Dev がテスト定義書の TC-ID 付き仕様テスト（Feature / App 結合 / E2E）を書いていない。QA が実装の内部関数をモック・直接呼び出ししていない | 置き場の表（「Dev と QA のテスト分担」）と照合。Dev の `tests/Feature/` 追加や QA の `tests/Unit/` 追加は差し戻す（両 worktree に同じパスのファイルが生まれてコンフリクトする） |
| `test/assert-present` | major | hook（Write 時、error） | すべてのテストに意味のある assert がある | hook が exit 2 済み。`assert true` 型は目視 |
| `test/empty-test` | major | hook（Write 時、error） | 本体が空のテストが無い | hook が exit 2 済み |
| `test/swallowed-error` | major | hook（Write 時、error） | テスト内で例外・エラーを握りつぶしていない | hook が exit 2 済み |
| `test/no-sut-mock` | major | — | テスト対象自身・同一モジュールの内部関数をモックしていない | モック対象がインターフェース境界か |
| `test/tautology` | major | hook（Write 時、warn） | 期待値を実装と同じロジックで計算していない | hook の WARN を確認し、該当箇所を判断 |
| `test/independent` | major | — | 順序依存・共有可変状態・グローバルに依存していない | 単体で実行して通るか、並列実行フラグ |
| `test/deterministic` | major | hook（Write 時、warn） | 時刻・乱数・実ネットワーク・`sleep` に依存していない | hook の WARN を確認し、正当な例外（タイムアウトのテスト等）か判断 |
| `test/error-type` | major | — | 異常系が「エラーになる」ではなくエラーの種類・コードを検証している | `assert.Error` だけで終わっていないか |
| `test/mutation-checked` | major | — | **両方**: 新しく書いた・変えたテストごとに `result.mutation` があり、すべて `killed: true` | `result.mutation` から 1〜2 件選び、同じ壊し方で実装を壊してテストが落ちることを再現する（再現後は必ず元に戻す）。欠けていれば差し戻す |
| `test/self-compare` | major | hook（Write 時、error） | 同じ値どうしを比較していない（`[ "$x" = "$x" ]` 等、常に真） | hook が exit 2 済み |
| `test/grep-count-lines` | major | hook（Write 時、warn） | 出現回数を `grep -c`（一致した行数）で数えていない | hook の WARN を確認し、1 行に複数回出うる値なら `grep -o … \| wc -l` を要求 |
| `test/restore-trap` | major | hook（Write 時、warn） | 状態を壊す操作（サービス停止・設定の書き換え）の前に `trap … EXIT` / `teardown` で復元している | hook の WARN と、該当操作の周辺 |
| `test/restore-warn-only` | major | hook（Write 時、warn） | 復元・検証の失敗を WARN 表示だけで流していない | hook の WARN。失敗はテスト失敗（exit 1）にさせる |
| `test/ipv4-only` | minor | hook（Write 時、warn） | アドレスの照合が IPv4 射影 IPv6（`::ffff:`）を取りこぼさない | hook の WARN と、対象サービスが IPv6 で待ち受けるか |
| `test/side-effects` | minor | — | 副作用（DB・通知・イベント）が検証されている | 変更系の処理に `assertDatabaseHas` 相当 |
| `test/tc-id` | minor | — | テストに TC-ID が対応付けられている | `grep -c 'TC-[0-9]'` |
| `test/naming` | minor | — | テスト名から条件と期待結果が読める | 目視 |
| `test/one-behavior` | minor | — | 1 テストに複数の振る舞いを詰めていない | assert の数と対象 |
| `test/commented-out` | minor | hook（Write 時、warn） | コメントアウトされたテストが無い | 消すか復活させるかを人間に確認 |
| `test/ts-ignore` | minor | hook（Write 時、warn） | テスト内に `@ts-ignore` / `as any` が無い | 型の抜け穴が結果を隠していないか |

## 標準コマンド（分岐カバレッジ）

**Dev implementer** は完了 JSON の `result.coverage` に**分岐カバレッジ**（無ければ行カバレッジ）を書く。閾値は `doc/conventions.md` の `coverage_threshold`（既定 **0.80**）。**変更した関数**の分岐カバレッジが閾値未満ならレビューに進まない（lint と同じゲート）。既存コード全体の数字ではなく、`git diff` で変更した関数に絞って見る。QA implementer は書かなくてよい（QA worktree には実装が無く測れない）。オーケストレーターは STEP C.5 の統合検証で Dev ユニットテスト + QA 仕様テストを合わせたカバレッジを参考値として記録する。

| 言語 | コマンド | 備考 |
|---|---|---|
| Go | `go test -coverprofile=cover.out ./... && go tool cover -func=cover.out` | Go は行（ステートメント）カバレッジのみ。分岐は reviewer が目視 |
| Python | `pytest --cov=src --cov-branch --cov-report=term-missing` | `--cov-branch` で分岐 |
| TypeScript | `vitest run --coverage`（`@vitest/coverage-v8`）/ `jest --coverage` | レポートの `Branch` 列 |
| PHP | `./vendor/bin/phpunit --coverage-text`（Xdebug / PCOV 必要）/ `php artisan test --coverage` | 行のみ |

完了 JSON の例：

```json
"result": {
  "lint": {"command": "...", "exit_code": 0},
  "coverage": {"kind": "branch", "value": 0.87, "changed_functions_below_threshold": []}
}
```

`changed_functions_below_threshold` が空でなければ、その関数の未到達分岐に対応する TC をテスト定義書に追加して実装する（テストを減らして数字を上げる方向は禁止）。

## 出典と対象バージョン

| 項目 | 出典 | 備考 |
|---|---|---|
| verified_against | 言語非依存 | ツール固有の項目は各言語ファイルの出典を参照 |
| 境界をモックし SUT をモックしない・独立・決定的 | https://martinfowler.com/articles/mocksArentStubs.html 、https://testing.googleblog.com/2016/05/testing-on-toilet-change-detector-tests.html | `[opinion]` 寄りだが広く合意 |
| 分岐ごとのテスト（分岐網羅） | https://en.wikipedia.org/wiki/Code_coverage#Basic_coverage_criteria | 分岐網羅は業界標準の用語 |
| 期待値を実装から作らない（tautological test） | https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html | |
| テストを消す・スキップするのは最終手段 | — | DocDD の「ドキュメントが正解・コードを直す」から導かれる本フロー固有のルール |
| `sleep` ではなく完了待ち | https://testing-library.com/docs/dom-testing-library/api-async/ 、https://pkg.go.dev/github.com/stretchr/testify/assert#Eventually | |
| カバレッジ閾値 80% | — | `[opinion]`。`doc/conventions.md` の `coverage_threshold` で変更 |
| ミューテーション確認 | https://en.wikipedia.org/wiki/Mutation_testing | ツール（Stryker / mutmut / Infection 等）を入れず、書いた本人が手で 1 件ずつ行う簡易版 |
