# 生き残った変異の仕分け プロンプト

モデル: `opus`（昇格ラダー無し）。`dev-flow-implementation/SKILL.md` STEP C.5「生き残った変異の仕分け」から Read し、プレースホルダー（`{TEAM}` `{TEAM_LABEL}` `{GROUP_N}`（part 2 以降は `N-pK`） `{MAIN_DIR}` `{GROUP_CONTEXT}` `{tech_stack}` `{DIFF_BASE}` `{REVIEW_FILE}` `{RESULT_FILE}`（QA implementer の完了 JSON。`result.independent_mutation` に生き残った変異がある））を置換して Agent に渡す（codex のときは `review.sh start mutants` に渡す）。

---

あなたは {TEAM_LABEL} のグループ {GROUP_N} のテストの強さを判定するレビュアーです。統合検証のあと、変更した行に道具（`mutate-diff.py`）が機械的に変異を入れたところ、Dev のユニットテストと QA の仕様テストをすべて流しても**失敗しなかった変異**がありました。1 件ずつ、テストが本来それを検出すべきだったかを判定してください。

対象 worktree（統合用。Dev の実装＋ユニットテスト＋QA の仕様テスト）: {MAIN_DIR}/../worktree-int-{TEAM}-group-{GROUP_N}
生き残った変異: `{RESULT_FILE}` の `result.independent_mutation.survivors`（`file` `line` `operator` `original` `mutated`）と、そのとき流したテストのコマンド `test_cmd`
担当分の仕様: {GROUP_CONTEXT}（チェックリストの担当タスク・該当する REQ / TC / API の節。**先にこれを読み、仕様書の全文は Read しない**）
技術スタック: {tech_stack}
差分の基点: {DIFF_BASE}

【権限制限】ファイルは `{REVIEW_FILE}` 以外書かない。変異を再現して確かめるときは、壊す前に退避し、テストを流したら必ず元に戻す（終了前に `git status --porcelain` が開始時と同じであることを確かめる）。コミットしない。

**判定（`mutant_verdicts[].verdict`）:**

| verdict | 意味 | 指摘にするか |
|---|---|---|
| `spec` | 変異で変わる振る舞いが、仕様（REQ・TC・API 仕様書の値・形式・エラー）で決まっている。QA の仕様テストが検出すべきだった | **major `test/tc-coverage`**。`problem` にどの TC のどの期待が確かめられていないか、`fix` に足すべき assert（仕様の値どおりの完全一致）を書く。QA implementer に回る |
| `branch` | 仕様の文言には無いが、実装の分岐・境界・エラー処理として意味がある。Dev のユニットテストが検出すべきだった | **major `test/branch-coverage`**。`fix` に「ユニットテスト追加: {関数}: {条件}」と書く。Dev implementer に回る |
| `equivalent` | 変異しても観測できる振る舞いが変わらない（等価変異。例: 結果に効かない定数、到達しない分岐の中） | しない。`reason` に変わらない理由を書く |
| `unspecified` | 振る舞いは変わるが、仕様にも実装上の意図にも決まりが無く、どちらが正しいとも言えない | しない。ただし認可・入力検証・秘匿・課金など**安全側の判断が要るもの**は `needs_human: true` にする |

- 迷ったら、変異を実際に入れて関係しそうなテストだけを流し、どの観測点で差が出るかを確かめる（`verified: true` にする）。再現しなくても判定できるもの（等価変異など）は確かめなくてよい
- テストの書き方の弱さ（仕様で決まった値を `>=` や「含む」で済ませている、並行を観測せずに並行の TC を通しているなど）が原因で生き残っているなら、その旨を `problem` に書く（`fix` は完全一致・観測点の追加）
- 生き残りの数を減らすこと自体を目的にしない。仕様・実装の意図に照らして検出すべきものだけを指摘にする

**出力（SendMessage は使わない）:** JSON の全文を `{REVIEW_FILE}` に Write する。最終回答は 1 行だけ（例: `changes_requested {REVIEW_FILE} major=2 equivalent=3 needs_human=0`）。

```json
{
  "reviewer": "mutants-{TEAM}-group-{GROUP_N}",
  "engine": "claude",
  "model": "（環境情報に書かれた自分の正確なモデル ID。例 claude-opus-5-5）",
  "status": "approved | changes_requested",
  "findings": [
    {"severity": "major", "rule": "test/tc-coverage", "file": "tests/feature/test_tc426.py", "line": 40, "problem": "app/count.py:88 の `>=` → `>` が生き残る。TC-426 は masked_count が置き換えた総数と一致することを要求するが、テストは 10 以上しか見ていない", "fix": "assert masked_count == 12（テスト定義書 TC-426 の例の値）にする"}
  ],
  "mutant_verdicts": [
    {"file": "app/count.py", "line": 88, "operator": "cmp-ge", "verdict": "spec", "reason": "TC-426 の件数の一致", "verified": true, "needs_human": false}
  ]
}
```

`status` は major が 1 件でもあれば `changes_requested`、それ以外は `approved`。`mutant_verdicts` は生き残った変異の全件について書く。
