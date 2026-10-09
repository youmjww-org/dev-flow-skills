# 実行プロファイル（quality / cost）

dev-flow は 2 つの実行プロファイルを持つ。**既定は `quality`**（速さと正確さを優先し、コストは気にしない）。`/dev-flow --profile=cost ...` を付けたときだけ `cost`（従来のコスト重視の流れ）で動く。

プロファイルは `state.json.profile` に保存し、run の途中で再開したときもその値を使う。`--profile=` を明示した場合は state.json の値を上書きする。`profile` が無い state.json（このファイル導入前の run）は `quality` として扱う。

各スキルのモデル指定はこの表に従う。スキル本文に `model="sonnet"` のような値が書かれている箇所は、**cost の値**として読む（quality では下の表の値に置き換える）。

## 役割ごとのモデル

| 役割 | スキル | quality | cost |
|---|---|---|---|
| オーケストレーター | dev-flow | opus | opus（起動と受け渡しだけ） |
| ステージの実行者 | 全ステージ | **オーケストレーターが SKILL.md を読んで直接実行**（`stage-*-agent` を起動しない） | `stage-*-agent` を起動（requirements / compliance / bootstrap は opus、それ以外は haiku） |
| bootstrap の棚卸し・仕様書逆生成・規約草案 | dev-flow-bootstrap | opus | sonnet |
| bootstrap の要件定義書逆生成 | dev-flow-bootstrap | opus | opus |
| spec の writer / reviewer | dev-flow-spec | opus | sonnet |
| consistency STEP 0 Impact Analysis | dev-flow-consistency | opus | sonnet |
| consistency の整合性チェック | dev-flow-consistency | opus | opus |
| checklist-writer / spec-cache-writer | dev-flow-consistency | opus | sonnet |
| conventions-verifier | dev-flow-implementation | opus | sonnet |
| Dev / QA implementer | dev-flow-implementation | **opus で開始・昇格ラダー無し**（修正は同じ implementer に最大 5 回） | sonnet で開始 → 設計レベルの指摘で opus に昇格（`task_complexity` で初期値を変更可） |
| Dev / QA レビュアー | dev-flow-implementation | opus。**Dev レビューと QA レビューを同時に起動する** | opus。Dev → QA の順に直列 |
| test runner | dev-flow-test | **opus 1 本で最大 5 回**（連続無進捗 2 回で打ち切り） | haiku 最大 2 回 → sonnet 最大 3 回 |
| compliance の準拠チェック | dev-flow-compliance | opus | opus |

## 待ち時間とハング検知

| 項目 | quality | cost |
|---|---|---|
| ハングの一次確認 | モデルに関係なく起動 **5 分後** | タイムアウト目安の半分（haiku 2.5 分 / sonnet 7.5 分 / opus 15 分） |
| ハングの最終判定 | 一次確認で変化が無く、生存確認に **3 分**応答が無ければハング | タイムアウト目安（haiku 5 分 / sonnet 15 分 / opus 30 分）に達した時点 |
| 起動方式 | pane 型（`model` を明示）。ハングしたら fork に切り替える | 同じ |

quality でも末端の実行者を最初から fork にはしない。fork は親（オーケストレーター）の会話を丸ごと引き継ぐので、implementer やレビュアーが他のステージの経緯に引きずられ、役割ごとに文脈を分けている意味が薄れるため。

## プロンプトの渡し方

| 項目 | quality | cost |
|---|---|---|
| ステージ手順 | オーケストレーターが SKILL.md の**全文**を Read して従う（抜き出さない） | `stage-*-agent`（`agents/` の定義）が SKILL.md の全文を Read して従う。オーケストレーターは run の情報だけを渡す |
| 末端の実行者へのプロンプト | `prompts/*.md` を全文置換して渡す | 同じ |

## quality でオーケストレーターが直接実行するときの注意

- オーケストレーター自身が Opus であることが前提。`dev-flow/SKILL.md` の frontmatter（`model: opus` / `effort: high`）は起動したターンにしか効かないので、ステージを始める前に自分のモデルを確かめ、Opus でなければ `/dev-flow` の打ち直しか `/model opus` を案内して止まる。人間への質問は AskUserQuestion で行い、同じターンの中で続ける
- Read で読んだ下流の SKILL.md では `${CLAUDE_SKILL_DIR}` が展開されない。そのスキルのディレクトリ（dev-flow と同じ階層の `dev-flow-<stage>/`）と読み替える
- `stage-*-agent` を起動しないので、hook の `pre-agent-check.sh`（プランモード検出・階層深さ・ループ検出）と `agent-complete.sh`（所要時間の記録）は動かない。代わりに：
  - プランモードなら開始前に「プランモードを抜けて（Shift+Tab）から再実行してください」と伝えて終了する
  - 同じステージを 5 回以上やり直していないかを `harness.stage_history` で自分で確認する
  - ステージ開始・完了時に `harness.stage_history` へ `{stage, model, started_at, completed_at, duration_seconds}` を追記する
- `state-sync.sh`・`doc-validate.sh`・`test-lint.sh`・`test-stage-guard.sh`・`pr-merge-guard.sh` は書き込み・コマンド単位で動くので、quality でもそのまま効く。ただし `state-sync.sh` は Write / Edit にしか反応しないので、**state.json は必ず Read してから Write / Edit で書く**（`jq ... > tmp && mv` などの Bash では書かない。`state-write-guard.sh` が deny する）
- 下流スキルに「終了する」「最終回答を返す」と書かれている箇所は、オーケストレーターにとっては「そのステージを終えて `dev-flow/SKILL.md` STEP 5 に戻る」と読む。下流スキルに「オーケストレーターに返す」とある JSON・報告は、自分で STEP 5 の判定に使う
- 下流スキルの AskUserQuestion は、オーケストレーターがそのまま人間に出す
