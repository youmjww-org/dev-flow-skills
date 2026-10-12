# ドキュメント誤り（doc_issues）の集約と判断

implementer の完了 JSON に `doc_issues` があれば、`doc/process/doc_issues.md` に追記する（`| # | グループ | doc | ref_id | 内容 | 修正案 | 実装の現状 |`）。

```json
{
  "doc_issues": [
    {
      "doc": "doc/api-spec/auth.md",
      "ref_id": "API-001",
      "issue": "request schema の email フィールドが optional だが要件 REQ-001 では必須",
      "suggested_fix": "required: [email, password] に変更"
    }
  ]
}
```

## いつ聞くか

**その場では聞かない。** 保留中の判断（`pending_decisions.md`）と一緒に、聞くタイミング（起動できるグループが無くなったとき・バッチの区切り）でまとめて聞く。タイミングと聞き方は [pending-decisions.md](pending-decisions.md)。聞くたびにオーケストレーターが止まり、レビュー待ちや次のグループの起動も止まるため。

それまでは実装の現状を仮の答えにして進める。後続のグループ（`depends_on` でつながるもの）が同じ箇所を前提に実装するなら、その仮の答えを後続のプロンプトに書く（答えが違ったときは Plan Repair で直す）。そのグループのレビューやテストが doc_issue の答え無しには進められないときだけ、そのグループを保留にする。

## 人間の判断（3 択）

| 選択 | 処理 |
|---|---|
| ドキュメントを修正する | 全グループ完了後なら、ベースブランチに直接 `docs: ドキュメント誤り修正 - {概要}` をコミットする。グループの途中なら下の doc-fix フロー |
| 実装側で対応する | 全グループ完了後なら Plan Repair（`plan_repair_needed`）で修正タスクを 1 グループ足し、終わってから test へ進む。グループの途中なら Dev implementer に `SendMessage` で依頼する |
| 無視する | そのまま続ける |

## doc-fix フロー（グループの途中で直すとき）

1. `doc-fix/group-N-{issue-slug}` ブランチを作る
2. 該当ドキュメントを Edit で直す
3. コミット: `docs: ドキュメント誤り修正 - {issue概要}`
4. ベースブランチへ PR を作り、人間にマージを頼む
5. マージ後、実装 worktree で `git merge {base_branch}` して最新のドキュメントを取り込む
