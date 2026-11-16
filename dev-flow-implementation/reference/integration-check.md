# STEP C.5: Dev + QA 統合検証の手順

`dev-flow-implementation/SKILL.md` STEP C.5 から参照。

**誰がやるか:** 0 の確認と int worktree の作成はオーケストレーター、2〜3.6 は **QA implementer**（オーケストレーターが `SendMessage` でこのファイルのパスと int worktree のパスを渡して頼む）。レビューと同時に走らせる（int worktree はレビュアーが見る worktree と別なので干渉しない）。テストの出力をオーケストレーターの文脈に入れないため。QA implementer は結果を `{RESULT_FILE}` の `result.integration`・`result.mutation`・`result.independent_mutation` に書いて 1 行で返し、オーケストレーターは `check-result.sh qa {RESULT_FILE}` で判定する。

**手順（グループごと、Dev/QA 両方の implementer が `completed` を返した後）:**

0. **Dev ブランチにコミットがあることを確認する**。`completed` が返っていても、コミット前に止まっていたり別ブランチにコミットしていたりすると、空のブランチをマージして「統合テストが通らない」原因を取り違える：
   ```bash
   git -C {MAIN_DIR} rev-list --count {base_branch}..dev/{team}-group-N   # 0 なら止まる
   git -C {MAIN_DIR} log --oneline {base_branch}..dev/{team}-group-N       # 完了 JSON の result.commits と一致するか
   ```
   0 件、または `result.commits` のハッシュが含まれていなければ、マージせずに Dev implementer に `SendMessage` で「worktree `{MAIN_DIR}/../worktree-dev-{team}-group-N` のブランチ dev/{team}-group-N にコミットしてから完了を返す」よう依頼する。QA ブランチも同じ確認をする
1. オーケストレーターが統合用の使い捨て worktree を作る（毎回作り直す。push しない。Dev / QA のブランチは書き換えない）：
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/ensure-worktree.sh int {team} N dev/{team}-group-N qa/{team}-group-N
   ```
   - **終了コード 3（コンフリクト）の場合**: 同じパスのファイルを Dev/QA 双方が作っている。テストファイルなら QA 側を正とし、Dev implementer に「そのファイルを `git rm` して再コミット」を `SendMessage` で依頼する。実装ファイルなら QA implementer に QA 側の変更を取り消させる。直ったら 1 からやり直す
2. int worktree（`{MAIN_DIR}/../worktree-int-{team}-group-N`）で **Dev のユニットテストと QA の仕様テストの両方**・lint・型検査を**実際に実行**する（`environment.md` の「スイート全体のコマンド」。無ければ `tech_stack` の標準コマンド。スイート全体は 1 回だけ流し、失敗したら直したあと失敗したファイルだけ流し直す。依存物が無ければ `composer install` / `npm install` 等を先に行う）。あわせて規約の「標準コマンド（分岐カバレッジ）」で統合カバレッジを計測し、参考値として STEP E の PR 説明に書く（ゲートは Dev の `result.coverage` で既に掛かっているので、ここでは記録のみ）。スイートは実行時間つきで流し（pytest `--durations=10` など）、1 件が 2 秒を超えるテストを `result.integration.slow_tests`（`[{"test": "...", "seconds": 11.0}]`）に書く（ゲートではない。レビュアーと台帳が見る）
3. 結果で分岐：
   | 結果 | 対応 |
   |---|---|
   | 全パス | 3.5 へ |
   | テストコード側の不備（セットアップ漏れ・文言のタイプミス・セレクタの推測違い等） | QA implementer が **QA の worktree で**直してコミットし、`cause = "test"` で返す（int worktree での修正はコミットしない。消えるため）。オーケストレーターが 1 からやり直す |
   | 実装側の不備（QA の期待がテスト定義書どおりで、実装がそれに従っていない） | QA implementer は `result.integration.cause = "impl"` と失敗の切り分けを書いて返す。オーケストレーターが Dev implementer に `SendMessage` で `{RESULT_FILE}` のパスを渡して直させ、直った後に QA へ 1 からのやり直しを頼む |
   | テスト定義書自体の矛盾 | QA implementer は `cause = "spec"` と `doc_issues` を書いて返す。オーケストレーターは `doc_issues` に記録し、実装の現状を仮の答えにして進める（人間に聞くのは聞くタイミングでまとめて。[pending-decisions.md](pending-decisions.md)） |
3.5. **ミューテーション確認（3 で全パスした後）**: QA implementer が続けて、「この int worktree で、あなたが書いた仕様テストのうち異常系・境界値を優先して最大 5 件、[conventions/testing.md](conventions/testing.md) の『ミューテーション確認』をして、`result.mutation` を返す（壊した実装は必ず元に戻す。コミットしない）」を行う。QA は自分の worktree に実装が無いので、ここが唯一できる場所。`killed: false` があればテストを強化し、2 からやり直す。結果は `{RESULT_FILE}` に残り、QA reviewer がそこから読む
3.6. **独立ミューテーション（3.5 の後）**: QA implementer が続けて、変異を**道具に選ばせる**確認を行う。3.5 は書いた本人が壊し方を選ぶので「検出できる壊し方」に偏る（2026-09〜11 の photo-incident-triage では、記録された 461 件がすべて killed なのに、レビュアーが弱いテストを 43 件見つけていた）。ここでは変更した行に機械的な変異を入れ、Dev のユニットテストと QA の仕様テストをまとめて流す：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/mutate-diff.py --worktree {MAIN_DIR}/../worktree-int-{team}-group-N \
     --base {DIFF_BASE} --test-cmd "<このグループのユニットテストと仕様テストを流すコマンド>" --result-file {RESULT_FILE}
   ```
   - `--test-cmd` は、このグループの変更に関係するテスト（Dev のユニットテストと QA の仕様テストのファイル）に絞ったコマンドにする。変異 1 件ごとに流すので、スイート全体が 2 分を超えるなら絞る。絞りすぎると生き残りが増えるだけで、結果をよく見せることはできない
   - 変異は最大 12 件（`--max`）。同じコミットなら毎回同じ変異が選ばれる。終わるとファイルは必ず元に戻る（戻せなかったら止まって知らせる）
   - **生き残った変異があってもテストを直さない**。何を直すべきかは、オーケストレーターが起動するレビュアーが仕分けて指摘にする（SKILL.md STEP C.5「生き残った変異の仕分け」）。生き残りを減らすこと自体を目的にすると、変異だけを殺す弱いテストが増える
   - 変更が設定・テストだけで変異を入れる行が無ければ、`total: 0` が書かれる（それでよい）。ツールが動かない言語・環境なら `result.independent_mutation = {"skipped": "<理由>"}` を書く
4. QA implementer は `result.integration` に、検証した Dev / QA ブランチの HEAD（`dev_head` / `qa_head`。`git rev-parse dev/{team}-group-N qa/{team}-group-N`）を書く。オーケストレーターはこれが今の HEAD と一致するかで、最後の修正の後に統合検証が済んでいるかを判断する。int worktree は次の検証か STEP F で消すので、取り消しは要らない
5. オーケストレーターは `check-result.sh qa {RESULT_FILE}` の 1 行目と統合検証の件数を STEP E の PR 説明に 1 行で書く

この STEP を飛ばすと、レビュアーが「QA テストは Dev 実装に対して通るか」を自前で検証することになり時間が掛かるうえ、PR マージ後の test ステージで初めて失敗が露見する。
