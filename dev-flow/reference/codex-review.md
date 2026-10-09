# レビュアーを Codex で動かす

dev-flow のレビュアー（spec の reviewer 4 種、implementation の Dev / QA レビュー）は、**codex が使えれば Codex CLI（`codex exec`）で動かし、使えなければ従来どおり Claude のサブエージェントで動かす**。プロファイル（quality / cost）に関係なく同じ。

## 目次

- なぜ codex にするか
- どのエンジンで動かすか
- 起動と待ち方
- 失敗したとき
- codex 特有の注意

## なぜ codex にするか

- レビュアーは「書いた人と違う観点で見る」役割で、書いた側（Claude）と系統の違うモデルにすると見落としが重なりにくい
- レビューはファイルを書かない（ミューテーションの再現で一時的に壊すだけ）ので、Claude Code の hook（`test-lint.sh` などは Write / Edit にしか反応しない）を通らなくても失うものが無い
- 結果は JSON の判定で、出力スキーマで形を固定できる。codex が失敗しても Claude のレビュアーに切り替えれば済む
- Claude のトークンを最も多く使う役割の 1 つ（Opus を毎回・往復ごとに起動する）を外せる

implementer・writer・test runner は codex にしない（hook による検証が外れるため）。

## どのエンジンで動かすか

`state.json.reviewer_engine`（`/dev-flow --reviewer=` で設定。無ければ `auto`）で決める。

| reviewer_engine | 動作 |
|---|---|
| `auto`（既定） | ステージの最初に `~/.claude/skills/dev-flow/codex/review.sh available` を 1 回実行し、0 なら codex、3 なら Claude（理由を 1 行で人間に伝える。例「codex 未ログインのため、レビューは Claude で行います」） |
| `claude` | 常に Claude のサブエージェント（従来の手順そのまま） |

codex でも Claude でも、レビュアーに渡すプロンプト（`prompts/*.md` を置換したもの・前回の findings・`uncertainty_points`）と、受け取った JSON の扱い（修正ループ・保存・`uncertainty_verdicts` の判定）は同じ。違うのは起動と待ち方だけ。

## 起動と待ち方

1. 置換済みのレビュアープロンプトを Write でファイルに書く: `doc/process/reviews/prompts/{レビュー結果と同じ名前}.md`（例 `group-2-dev-app-r1.md`、`spec-test-spec-reviewer-r1.md`）
2. 起動する。種別は implementation が `impl`（cwd はレビュー対象の worktree）、spec が `spec`（cwd はメインの作業ディレクトリ）

   ```bash
   ~/.claude/skills/dev-flow/codex/review.sh start impl {worktree} doc/process/reviews/prompts/group-2-dev-app-r1.md doc/process/reviews/group-2-dev-app-r1.json
   ```

   最後の行にジョブディレクトリが出る。並べて動かすレビュー（quality の Dev ∥ QA、spec の複数 reviewer）は `start` を続けて実行してから待つ。**codex のレビューは cost でも並列に起動してよい**（cost が Dev → QA を直列にしているのは Claude の同時実行数を抑えるため）
3. 待つ: `~/.claude/skills/dev-flow/codex/review.sh wait {ジョブディレクトリ}`（Bash の `timeout` は 600000 を指定）
   - `0` → 完了。出力 JSON（2 の最後の引数）がレビュー結果。以降はスキル本文どおり（保存は済んでいるので Write し直さない）
   - `124` → まだ動いている。同じ `wait` をもう一度実行する（1 回あたり最大 9 分待つ。`sleep` を挟まない）
   - `4` → 失敗。下の「失敗したとき」
   - quality のオーケストレーターは、他のグループの完了通知も受け取れるよう `wait` を `run_in_background=true` で実行してよい（終わると通知が来る）。`stage-*-agent`（cost）は前景で `wait` を繰り返す（バックグラウンドにすると最終回答が先に返ってしまう）

出力 JSON には `engine: "codex"` と `restored_files`（レビュアーが worktree に残した変更を `wait` が戻したパス）が付く。`status` は、blocker / major の指摘があれば `changes_requested` に揃えてある。

再レビューも `start` からやり直す（codex のセッションは引き継がない。前回の findings と `review_responses` はプロンプトに入れる。Claude のレビュアーを起動し直すときと同じ）。

## 失敗したとき

`wait` が 4（codex exec の失敗・タイムアウト・最終回答が JSON でない）を返したら、**そのレビューだけ** Claude のサブエージェントで同じプロンプトを使ってやり直す（スキル本文の従来の手順）。人間には聞かない。理由を 1 行伝える（例「group-2 Dev レビュー: codex がタイムアウトしたため Claude でレビューします」）。

同じステージで codex が 2 回続けて失敗したら、そのステージの残りのレビューは Claude で行う（`reviewer_engine` は書き換えない。次のステージでは `available` からやり直す）。

codex のハングは `review.sh` の上限（`DEV_FLOW_CODEX_TIMEOUT`、既定 1800 秒）で打ち切られるので、`agent-hang-recovery.md` の手順は使わない。

## codex 特有の注意

- `review.sh` はレビュアープロンプトの前に `codex/preamble-impl.md` / `preamble-spec.md`（Claude Code のツール名の読み替え・JSON だけを返す・sandbox の制約）を付けて渡し、出力を `codex/schema-*.json` のスキーマで固定する
- sandbox: `impl` は workspace-write（worktree の中でテスト・lint を実行でき、ネットワークも使える）、`spec` は read-only
- sandbox では `.git` に書けないので、ミューテーションの再現は `cp` で退避・書き戻しをさせている。戻し忘れは `wait` が `git checkout` で戻し、`restored_files` に記録する（開始前から汚れていたファイルには触らない）
- codex はプロジェクトの `AGENTS.md` を読む。Claude Code の `CLAUDE.md` は読まないので、規約は従来どおり `{REVIEW_CHECKLIST}` で渡す
- モデル・推論の強さは環境変数 `DEV_FLOW_CODEX_MODEL` / `DEV_FLOW_CODEX_EFFORT`（既定 `high`）で変えられる。codex を一時的に止めるだけなら `DEV_FLOW_CODEX=off`
