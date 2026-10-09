# エラーハンドリング詳細

## state.json 関連

**破損している場合:**
1. `git log --oneline -- doc/process/state.json | head -5` でバックアップ確認
2. `git show HEAD:doc/process/state.json` で復元試行
3. 復元不可 → AskUserQuestion で選択：「requirements からやり直す」/「手動で修正する」

**必須フィールド欠損:**
- `next_stage` 不明（旧 `current_phase` も無い） → 人間に確認
- `mode` 不明 → デフォルト `"full"` を設定
- `requirements_paths` 空 → `doc/requirements/*.md` を列挙して確認

## サブエージェント関連

**Agent 起動失敗:**
1. `ls -la ~/.claude/skills/dev-flow-*/SKILL.md` でスキルファイルの存在確認
2. 見つからない場合は `~/.claude/skills/` 配下の `dev-flow-*` ディレクトリを列挙
3. 3回連続失敗 → 人間に報告して中断

**サブエージェント途中停止:**
- エージェントの最終出力を確認
- 成果物（ドキュメント等）が存在 → Read して品質確認し問題なければ次ステージへ
- 成果物なし → 同設定で再起動（最大2回）

## チェックリスト関連

**task_checklist.md 更新失敗:**
- Edit エラーを確認。`old_string` が見つからない場合は Read で再確認してから更新
- 3回失敗 → 人間に報告して手動更新を依頼

## その他

**`--from` 引数が不正:** 有効値（`requirements` / `spec` / `consistency` / `implementation` / `test` / `compliance`）を AskUserQuestion で提示

**`--reviewer` 引数が不正:** 有効値（`auto` / `claude`）を AskUserQuestion で提示

**`--from` 指定時に state.json なし:** AskUserQuestion でエラー報告

**予期しないエラー:** エラーメッセージ・スタックトレース・関連パスを含めて人間に報告し、可能であれば復旧手順を提案
