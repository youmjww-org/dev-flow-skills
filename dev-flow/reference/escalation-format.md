# エスカレーション報告フォーマット

エスカレーション時は `doc/process/escalation_{stage}_{timestamp}.md` を生成してから AskUserQuestion で提示する。

## テンプレート

```markdown
# エスカレーション報告: {stage 名}

## 1. 試行履歴
| 試行 | モデル | 主な行動 | 結果 |
|---|---|---|---|
| 1 | haiku | （概要） | （結果） |

## 2. 現在の状態
（未解決事項を箇条書き）

## 3. AIが判断できなかった理由
- [ ] 要件の曖昧さ（具体的にどこ）
- [ ] 仕様間の矛盾（どの仕様とどの仕様）
- [ ] 技術的制約（どの技術スタックの限界）
- [ ] その他: {自由記述}

## 4. 選択肢
| 選択肢 | 期待される結果 |
|---|---|
| A: | |
| B: | |

## 5. 推奨 recovery パス
（下表を参照して記載）
```

## エスカレーション理由別 recovery パス

ユーザーが回答した後、以下の基準で再開ステージを決定する：

| 理由 | 再開ステージ | 推奨 `--from` 値 |
|---|---|---|
| 要件の曖昧さ | requirements | `requirements` |
| 仕様間の矛盾 | spec | `spec` |
| 技術的制約（技術スタック・実装方針の見直しが必要） | spec | `spec` |
| テスト失敗 | implementation（対象グループのみ） | `implementation` |
| その他 | 人間が判断 | 人間が `/dev-flow --from={値}` で指定 |

**重要：再開は必ず `--from` 引数経由で行う。**

オーケストレーターが `--from` を経由せずに `state.json.next_stage` を直接書き換える運用は禁止する。理由：

- `next_stage` のみ書き換えても `baseline_commit` / `implementation_progress` / `tech_stack` など他フィールドとの整合性が崩れる
- 直接書き換えは復旧パスから漏れやすく、トラブルシュート時にどのステージに戻したか追跡できなくなる
- `--from` 経由なら STEP 2 の state.json 読み込みロジックが整合性を再検証するため安全

エスカレーション報告では、AskUserQuestion で人間に「`/dev-flow --from={値}` を実行してください」と提示し、ユーザーに再開を委ねる。

**例外：`plan_repair` のみ、implementation 内部の閉じたサイクルとして `next_stage` を書き換える運用を許可する**（詳細は `dev-flow/SKILL.md` STEP 4 と `reference/cost-mode.md`「Plan Repair の流れ」を参照）。
