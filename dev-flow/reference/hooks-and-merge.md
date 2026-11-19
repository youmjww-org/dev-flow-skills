# hook の一覧と PR マージの扱い

`dev-flow/SKILL.md`「hook と PR マージ」の詳細。hook の deny / ask の意味を確かめたいとき、PR のマージで迷ったときに読む。

## 目次

- hook の一覧
- PR マージの分担（非ブロッキング）
- 自動マージ条件
- gh pr merge が deny されたとき

## hook の一覧

`setup.sh` が `~/.claude/settings.json` に登録する（`dev-flow/hooks/`）。プロンプトでは緩められない。スキルの frontmatter の `hooks:` はサブエージェントのツール呼び出しでは動かないので使わない（README「Hook 連携」）。hook が動いていれば該当する手作業は不要（結果は `additionalContext` で届く）。未導入環境（`setup.sh --no-hooks`）では各 STEP の記述どおり手で行う。

| タイミング | hook | 内容 |
|---|---|---|
| `stage-*-agent` 起動前（cost のみ） | `pre-agent-check.sh` | 下流スキル欠損・state.json 不正・階層深さ超過は `deny`。ステージとエージェントの不一致・同じステージ 5 回以上は `ask`。プランモードなら `deny` |
| `stage-*-agent` 完了後（cost のみ） | `agent-complete.sh` | `flow.log` に完了と所要時間を記録。requirements 完了時は人間確認ゲートを念押し。起動直後（数秒以内）の呼び出しは `agent_spawned` として記録するだけ（pane 型は起動直後に返るため。完了は最終回答 / task notification で判断する） |
| `state.json` 書き込み後 | `state-sync.sh` | JSON 不正・`next_stage` / `kind` の値域外は exit 2 で差し戻す。implementation → test は直前に `verify-remote-state.sh` が OK を記録していなければ差し戻す。`task_checklist.md` の「ステージ進捗」を同期し、`flow.log` に遷移を記録 |
| `escalation_*.md` 生成後 | `state-sync.sh` | `flow.log` に記録。`DEV_FLOW_SLACK_CHANNEL` 設定時は Slack 通知 |
| `doc/{requirements,test-spec,api-spec,infra-spec}/*.md`・`task_checklist.md` 書き込み後 | `doc-validate.sh` | frontmatter の ID 形式・重複・`covers` の REQ 実在・`implemented_by` の関数実在・見出しの対応・`status` の値域・チェックリストの 6 行を検証。違反は差し戻し。どの `covers` にも無い REQ は WARN |
| テストコード書き込み後 | `test-lint.sh` | skip・assert なし・空テスト・エラー握りつぶし・（シェル）同じ値どうしの比較は差し戻し。sleep / 現在時刻 / 乱数 / tautology、（シェル）`grep -c`・trap の無い復元・WARN だけの失敗・IPv4 限定の照合は WARN |
| test ステージでの書き込み前 | `test-stage-guard.sh` | テストコード・テスト定義書への Write / Edit を `deny` |
| `gh pr merge` 実行前 | `pr-merge-guard.sh` | 下の「自動マージ条件」を検証し、満たさなければ `deny` |
| Bash 実行前 | `state-write-guard.sh` | `state.json` を Bash（リダイレクト・`mv` / `cp`・`tee`・`sed -i`・スクリプトの書き込み）で書き換えるのを `deny`。state.json は必ず Read してから Write / Edit で書く |
| セッション開始 / 応答完了 | `session-start.sh` / `stop-summary.sh` | 進行中の run の次ステージとアクションを表示。ブランチが origin より遅れていれば警告 |
| （Bash から呼ぶ） | `verify-remote-state.sh` | `git fetch` してブランチの ahead / behind、PR の state、CI の結果を 1 行ずつ OK / NG で出す |

## PR マージの分担（非ブロッキング）

オーケストレーターは**人間による** PR のマージを待たない（`sleep` でポーリングしない）。人間マージ待ちになったらフローを終え、次の `/dev-flow` で続きを進める。CI の完了は待つ: `gh pr merge` が CI 未完了で deny されたら、`timeout 900 gh pr checks <N> --watch --fail-fast` で完了を待ってから 1 回だけ再試行する（`sleep` のポーリングではない。待たずに止まると、グループごとに人間の再開が要る）。

| アクター | 責任 |
|---|---|
| implementation の実行者（quality: オーケストレーター / cost: `stage-implementation-agent`） | グループの実装が終わったら `gh pr create` で PR を作り、番号を `implementation_progress.pr_numbers["group-N"]`（**配列**。1 グループ 2〜4 PR）に記録。続けて各 PR に `gh pr merge <N> --merge` を試す。全 PR がマージされたグループは STEP H で `completed_groups` に入れる。deny された PR が残るグループは「人間マージ待ち」にし、依存の無い他グループがあれば続け、無ければ PR URL と deny 理由を人間に出して終える |
| オーケストレーター（再開時） | `next_stage = "implementation"` で `implementation_progress` が残っていれば、そのまま implementation を再開する（PR の状態確認と取り込みは implementation 側の再開処理が行う）。自分で `gh pr view` をポーリングしない |
| 人間 | 自動マージ条件を満たさない PR のレビュー・マージ。`main` / `develop` 向けの PR も人間がマージするか、会話で明示的に許可する（下の「人間の明示的な許可」） |

hook 未導入環境（起動時コンテキストの「hooks: disabled」）では `gh pr merge` を一切発行せず、すべて人間に任せる。

## 自動マージ条件

`pr-merge-guard.sh` が機械的に検証する。すべて満たしたときだけ通る（一部は人間の明示的な許可でも通る。下の「人間の明示的な許可」）。

- ベースブランチが `feature/*`（`DEV_FLOW_AUTO_MERGE_BASE_PATTERN` で変更可）で、`implementation_progress.base_branch` と一致する。`main` / `master` / `develop` / `release/*` / `hotfix/*` は人間の明示的な許可が無ければ拒否
- CI チェックがすべて成功している（チェックが 1 つも無い PR は拒否）
- `mergeable == MERGEABLE`（コンフリクトなし）
- diff にテストの削除・スキップ・無効化（`t.Skip` / `it.skip` / `@pytest.mark.skip` / `markTestSkipped` / テスト関数の削除。移動は可）が無い。パターンは `hooks/test-guard-patterns.txt`
- diff に DB の破壊的変更（DROP / TRUNCATE / カラム削除・型変更・リネーム、ORM マイグレーションの remove / rename / alter 系、Terraform の DB リソース削除や `skip_final_snapshot = true` 等）が無い。パターンは `hooks/db-destructive-patterns.txt`
- マージ方式は `--merge` のみ（`--squash` / `--rebase` / `--auto` / `--admin` は拒否）。1 コマンド 1 PR、番号か URL で指定

## gh pr merge が deny されたとき

同じコマンドを再試行しない（条件が変わらない限り結果は同じ）。deny 理由を読み、CI 未完了なら `timeout 900 gh pr checks <N> --watch --fail-fast` で完了を待って 1 回だけ再試行する。CI 失敗・コンフリクト・main 向けなど満たせないものは「人間マージ待ち」として PR URL と deny 理由を人間に 1 回出す。hook を迂回して `gh api` 等でマージしない。

### 人間の明示的な許可

自動マージ条件のうち、**ベースブランチ（保護ブランチ・対象外・`base_branch` との不一致）・CI 未通過/CI なし・DB 破壊的変更・テストの削除/スキップ**は、人間が会話で明示的に許可すれば通る。

- 判定: transcript の**直近の人間の発言**（`origin.kind = "human"`。作業中に送った発言を含む）に「マージ」と「許可」の両方が含まれ、「許可しない」等の否定が無いこと。例: 「いいよマージして、明示的に許可する」
- 効くのはその発言に応えている間だけ。人間が次の発言をすると失効する
- **サブエージェントからの実行には効かない**（hook 入力に `agent_id` があれば常に不可）。dev-flow の自動実行に人間の許可を流用させないため
- Claude の出力・ツール結果・タスク通知は `origin` が human にならないので、許可の代わりにならない
- Draft・コンフリクト・OPEN でない・`--merge` 以外の方式・PR 番号の指定漏れは、許可があっても deny（マージ自体ができない、または書式の誤り）
- 確認ダイアログ（`permissionDecision: "ask"`）は使わない。bypassPermissions モードでは hook の ask が確認なしで許可される（2026-11-19 に実測）ため
- `gh pr merge` の `-R owner/repo` と PR URL の owner/repo は `gh pr view` / `gh pr diff` に引き継ぐので、リポジトリ外の cwd からでも判定できる
