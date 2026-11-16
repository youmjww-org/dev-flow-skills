---
name: dev-flow-test-prune
description: dev-flow で増え続けたテストを最適化する（ほかのテストで代わりが利くテストを、テストごとのカバレッジと変異の殺し方から洗い出して取り除き、殺せる変異が減らないことを確かめて PR にする）。compliance の完了報告が「テストの最適化を勧める」と書いたとき、または人間が `/dev-flow-test-prune` で起動したときに使う。今は Python（pytest）のプロジェクトだけに対応する。
model: opus
allowed-tools: Read Write Edit Bash Agent
disable-model-invocation: true
---

# テストの最適化（test-prune）

dev-flow のテストは増える一方で、減る仕組みが無い。TC は追加されるだけで、テストの削除は hook が止め（`test/no-delete`）、ある テストが要らないかどうかはグループの中では判断できない（ほかのテストとの重なりを見ないと決められない）。2026-11 の photo-incident-triage では、テストのコードが本体の 3.8 倍・1.8 万件になっていた。

このスキルは、**消してよいかを数字で決める**。テストごとに「通った実装の行」と「殺した変異」を集め、両方ともほかのテストで代わりが利くテストだけを取り除き候補にする。取り除いた後に同じ変異をもう一度流し、殺せる変異が 1 つも減っていないことを確かめてから PR にする。マージは人間が行う（テストを消す PR は hook が自動マージしない）。

## 入力

- 作業ディレクトリ: 対象プロジェクトのルート（`doc/process/state.json` があれば `base_branch` を使う。無ければ既定のブランチ）
- 対応する言語: Python（pytest・pytest-cov・pytest-xdist）。それ以外なら「未対応: {言語}」と報告して止まる

## STEP 0: 準備

1. 作業ツリーがきれいであることを確かめる（`git status --porcelain --untracked-files=no` が空）。きれいでなければ止まる
2. ベースブランチの最新から `chore/test-prune-{YYYYMMDD}` を作る
3. 実装のソースの場所を決める: `.coveragerc` / `pyproject.toml` の `[tool.coverage.run] source` があればそれ、無ければ `tests/` 以外のパッケージのディレクトリ
4. 並列数 K を決める: `nproc` の 8 割（最大 24）。メモリが 8GB 未満なら 4

## STEP 1: テストごとのカバレッジを集める

変異なしで、スイート全体を 1 回流す（テストの出力は会話に入れない。ファイルに書いて末尾の 1 行だけ見る）:

```bash
mkdir -p doc/process/reviews/prune
python -m pytest tests -q -n {K} -p no:cacheprovider -o addopts= --tb=no \
  --cov={source} --cov-context=test --cov-report= \
  -o junit_family=xunit1 --junitxml=doc/process/reviews/prune/base.xml \
  --basetemp="$(mktemp -d)/bt" > doc/process/reviews/prune/base.log 2>&1
tail -1 doc/process/reviews/prune/base.log
cp .coverage doc/process/reviews/prune/base.coverage
```

- `--basetemp` は毎回新しい場所にする（テストが読み取り専用のディレクトリを残すと、pytest の既定の一時ディレクトリの片付けに失敗し、後の実行で数百件がエラーになる。道具も変異ごとに新しい basetemp を使う）
- 失敗するテストがあってもよい（変異なしで落ちたテストは、殺した変異を数えられないので取り除き候補から外す）。ただし失敗が全体の 5% を超えたら、環境が揃っていない（DB・外部ファイル）ので止まって人間に報告する
- **`coverage json --show-contexts` は使わない。** 1.8 万件のテストで 17GB のメモリを使い、サーバーが止まりかけた。道具は `.coverage`（SQLite）を直接読む

## STEP 2: 取り除き候補を洗い出す

```bash
python ${CLAUDE_SKILL_DIR}/scripts/test-prune.py analyze --worktree . \
  --coverage-json doc/process/reviews/prune/base.coverage --junit doc/process/reviews/prune/base.xml \
  --runner "python -m pytest -q -p no:cacheprovider -o addopts= --tb=no" \
  --mutants 150 --workers {K} --out doc/process/reviews/prune/prune.json
```

- 変異 150 件で、1.8 万件のプロジェクトなら 30〜60 分かかる（変異 1 件ごとに、その行を通るテストだけを流す）。`run_in_background` で流し、終わるのを待つ（`sleep` で見に行かない）
- 標準出力は 1 行の要約だけ。詳しい結果は `prune.json`:
  - `removable_functions`: 取り除き候補（実行時間の長い順）。TC-ID を名前に持つテストは、TC ごとに 1 つ必ず残してある
  - `zero_kill_functions`: 通った行の変異を 1 つも殺さなかったテスト（確かめていることが弱い）。**自動では消さない**
  - `parametrize_candidates`: 同じファイルで、値だけが違う同じ形のテスト関数の組

## STEP 3: 取り除く

Agent（`name="test-pruner"`、`model="opus"`）に、`prune.json` のパスと次の指示を渡して編集させる:

- `removable_functions` のテスト関数を消す。消したことでファイルにテストが 1 つも残らなければファイルごと消す。ほかのテストが使っている補助関数・フィクスチャは消さない（`grep -rn` で使われていないことを確かめてから消す）
- クラスの中のテストは、そのメソッドだけを消す。`parametrize` の行の一部だけが候補になることは無い（関数単位で判定している）
- `parametrize_candidates` は、assert の中身まで同じ形のときだけ 1 つの `parametrize` にまとめる（TC-ID はテスト名かコメントに残す）。迷うものはまとめない
- 消したテスト関数の一覧を `doc/process/reviews/prune/prune.json` の `removed_functions`（`[{"test": nodeid}]`）に書き足す（実際に消したものだけ。消さなかった候補は理由を `kept_candidates` に）
- 取り除きとまとめは別のコミットにする（`test: 代わりの利くテストを取り除く（test-prune）` / `test: 同じ形のテストを parametrize にまとめる`）

## STEP 4: 確かめる

1. `python ${CLAUDE_SKILL_DIR}/scripts/test-prune.py verify --worktree . --prune doc/process/reviews/prune/prune.json --runner "…" --workers {K} --out doc/process/reviews/prune/verify.json`
   - 取り除く前に殺せていた変異を、残したテストでもう一度流し、すべてまだ殺せることを確かめる
   - `NG` なら、`verify.json` の `lost` の変異を殺していたテスト（`prune.json` の `mutant_list[].killed_by`）を元に戻し（`git checkout {取り除く前} -- {ファイル}` の該当関数だけ戻す）、もう一度 verify する。2 回で OK にならなければ、取り除きをすべて戻して人間に報告する
2. スイート全体を STEP 1 と同じコマンド（カバレッジ付き）で流し、失敗が STEP 1 から増えていないこと、カバレッジの合計（`coverage report` の TOTAL）が下がっていないことを確かめる。下がっていれば、下がった行を通っていたテストを戻す

## STEP 5: PR を作る

`gh pr create`（ベースはベースブランチ、ラベル `claude`、Draft ではなく OPEN）。タイトルは「テストの最適化: テスト関数 N 個を取り除く（実行時間 −X%）」。本文:

| 項目 | 前 | 後 |
|---|---|---|
| テスト関数 / テスト（パラメータ込み） | | |
| スイートの実行時間（直列の合計） | | |
| カバレッジ（TOTAL） | | |
| 殺した変異（標本 150 件のうち） | | |

- 取り除いたテストの一覧（ファイルごと。件数が多ければ `<details>` に）と、まとめた組
- `zero_kill_functions` の件数と上位 20 件（「確かめていることが弱い。強めるか消すかは人間が判断」）
- 限界: 変異は標本なので、殺した変異が同じでも標本の外の不具合を片方だけが捕まえることはある。同じ行を通るテストでも assert は違いうる。レビューでは、取り除いたテストの assert が残したテストに無い**仕様の値**を確かめていないかを見てほしい
- **この PR は hook が自動マージしない**（テストの削除を含むため）。人間がレビューしてマージする

PR を作ったら URL と前後の表を人間に報告して終わる。

## 出典

| 項目 | 出典 |
|---|---|
| テストごとのカバレッジ（動的な文脈） | https://coverage.readthedocs.io/en/latest/contexts.html |
| pytest-cov の `--cov-context=test` | https://pytest-cov.readthedocs.io/en/latest/contexts.html |
| テストスイートの縮小（test suite minimization）とミューテーションで強さを保つ考え方 | https://doi.org/10.1002/stvr.430 （Yoo & Harman, Regression testing minimization, selection and prioritization: a survey） |
