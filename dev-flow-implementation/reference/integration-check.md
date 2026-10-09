# STEP C.5: Dev + QA 統合検証の手順

`dev-flow-implementation/SKILL.md` STEP C.5 から参照。

**手順（グループごと、Dev/QA 両方の implementer が `completed` を返した後）:**

0. **Dev ブランチにコミットがあることを確認する**。`completed` が返っていても、コミット前に止まっていたり別ブランチにコミットしていたりすると、空のブランチをマージして「統合テストが通らない」原因を取り違える（実戦で、コミットの無い Dev ブランチを QA 側でマージしようとして空振りした）：
   ```bash
   git -C {MAIN_DIR} rev-list --count {base_branch}..dev/{team}-group-N   # 0 なら止まる
   git -C {MAIN_DIR} log --oneline {base_branch}..dev/{team}-group-N       # 完了 JSON の result.commits と一致するか
   ```
   0 件、または `result.commits` のハッシュが含まれていなければ、マージせずに Dev implementer に `SendMessage` で「worktree `{MAIN_DIR}/../worktree-dev-{team}-group-N` のブランチ dev/{team}-group-N にコミットしてから完了を返す」よう依頼する。QA ブランチも同じ確認をする
1. QA worktree に Dev ブランチを**検証用に**マージする（QA 側で行う。Dev 側には QA を混ぜない）：
   ```bash
   cd {MAIN_DIR}/../worktree-qa-{team}-group-N
   git merge dev/{team}-group-N -m "merge: 検証用（後で取り消す）"
   ```
   - **コンフリクトした場合**: 同じパスのファイルを Dev/QA 双方が作っている。テストファイルなら QA 側を正とし、Dev implementer に「そのファイルを `git rm` して再コミット」を `SendMessage` で依頼する。実装ファイルなら QA 側の変更を取り消す
2. QA worktree で **Dev のユニットテストと QA の仕様テストの両方**・lint・型検査を**実際に実行**する（`tech_stack` の標準コマンド。依存物が無ければ `composer install` / `npm install` 等を先に行う）。あわせて規約の「標準コマンド（分岐カバレッジ）」で統合カバレッジを計測し、参考値として STEP E の PR 説明に書く（ゲートは Dev の `result.coverage` で既に掛かっているので、ここでは記録のみ）
3. 結果で分岐：
   | 結果 | 対応 |
   |---|---|
   | 全パス | 3.5 へ |
   | テストコード側の不備（セットアップ漏れ・文言のタイプミス・セレクタの推測違い等） | QA implementer に `SendMessage` で修正を依頼する（軽微で明白なら オーケストレーターが直接直してもよい）。直った後 1 からやり直す |
   | 実装側の不備（QA の期待がテスト定義書どおりで、実装がそれに従っていない） | Dev implementer に `SendMessage` で修正を依頼する。直った後 1 からやり直す |
   | テスト定義書自体の矛盾 | STEP G の `doc_issues` に記録する（人間に聞くのは全グループ完了後にまとめて。ただしこのグループが先に進めないものはその場で聞く） |
3.5. **ミューテーション確認（3 で全パスした後）**: QA implementer に `SendMessage` で「この統合済み worktree で、あなたが書いた仕様テストのうち異常系・境界値を優先して最大 5 件、[conventions/testing.md](conventions/testing.md) の『ミューテーション確認』をして、`result.mutation` を返す（壊した実装は必ず元に戻す。コミットしない）」と依頼する。QA は自分の worktree に実装が無いので、ここが唯一できる場所。`killed: false` があれば QA にテストを強化させ、2 からやり直す。結果は PR 説明に書き、QA reviewer に渡す
4. 検証用マージを**必ず取り消す**（PR の diff に Dev の変更が混ざらないようにする）：
   ```bash
   git reset --hard {マージ前の QA コミット}
   ```
   取り消し前に QA 側で修正コミットを積んだ場合は、`git stash` → `reset --hard` → `stash pop` → 再コミットで修正だけを残す
5. 統合で全パスした事実（Dev ユニットテスト件数 + QA 仕様テスト件数、統合カバレッジ、QA のミューテーション確認の結果）を STEP E の PR 説明に書く

この STEP を飛ばすと、レビュアーが「QA テストは Dev 実装に対して通るか」を自前で検証することになり時間が掛かるうえ、PR マージ後の test ステージで初めて失敗が露見する。
