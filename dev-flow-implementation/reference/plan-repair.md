# Plan Repair フロー詳細

`dev-flow-implementation/SKILL.md` STEP C で `blocker_type: "plan_repair_needed"` を受信したときに実行する手順の詳細。

## Contents
- 発動上限
- ユーザー提示フォーマット
- 選択肢別の動作
- 修正履歴の記録

## 発動上限

Plan Repair の発動は同一フロー内で最大3回まで。上限到達後は `plan_repair_needed` を `requirement_ambiguity` として処理（人間にエスカレーション）。

## 手順

1. 現在進行中の worktree の作業状況をメモ（`state.json` の `implementation_progress.plan_repair_memo` に記録）
2. **人間に聞くかどうかを決める。** 要件・仕様の解釈が変わらない修正（仕様にある TC のテスト不足を QA タスクで補う、タスクの分け方・依存の直し、チェックリストの書き漏れ）は人間に聞かず、「計画修正を承認」として 3 に進む（人間に確認するのは requirements と spec の 2 か所だけ）。要件の解釈が要るもの・仕様書の変更が要るものだけ、次の形で人間に出す（AskUserQuestion。cost のステージエージェントは最終回答の「## 人間への質問」で返す）：

```
## 計画修正リクエスト

エージェント: {agent}
理由: {reason}

提案された修正:
{suggested_repair を表示}

どのように対処しますか？
```

| 選択肢 | 動作 |
|---|---|
| 「計画修正を承認」 | consistency を mini モード（`plan_repair`）で再実行（差分修正のみ） |
| 「却下して当初計画で続行」 | そのまま implementation 継続（worktree を再開） |
| 「全体を consistency から再生成」 | task_checklist.md を全体作り直し（`next_stage` を `"consistency"` に戻して終了） |

3. 「計画修正を承認」選択時:
   - `state.json.implementation_progress.completed_groups` は維持（完了済みグループは再実行しない）
   - `state.json.next_stage` を一時的に `"plan_repair"` に設定して終了
   - オーケストレーターが `stage-plan-repair-agent` を起動し、consistency を mini モードで実行（詳細は `dev-flow-consistency/SKILL.md` 参照）
   - mini モード完了後、`state.json.next_stage` が `"implementation"` に戻り、次回起動で implementation を未着手グループから再開

4. 修正履歴を `doc/process/plan_repair_log.md` に記録：

```markdown
# Plan Repair ログ

## 修正 {N} - {日時}
- **エージェント**: {agent}
- **理由**: {reason}
- **選択**: {人間の選択}
- **修正内容**: {suggested_repair}
```

## パース失敗時のフォールバック

JSON パースに失敗した場合は、テキストに「実装完了」が含まれれば `completed` 扱い、「ブロッカー」または「blocked」が含まれれば `blocked` 扱いとする。
