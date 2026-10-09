---
name: dev-flow-test
description: AI駆動開発フローの test ステージ（5/6: テスト実行）。quality プロファイル（既定）は Opus で最大 5 回、cost プロファイルは Haiku で最大 2 回試行し失敗時に Sonnet（最大 3 回）へ昇格してテストを全通過させます。テストコードは修正せず、プロダクションコードのみを修正する DocDD ルールを適用し、E2E テストにも対応します。implementation 完了後、または `--from=test` 起動時に使用します。
model: haiku
allowed-tools: Read Write Edit Bash Agent SendMessage TaskStop AskUserQuestion
disable-model-invocation: true
---


# Stage 5/6 test: テスト実行

## 入力

状態ファイル `doc/process/state.json` から読み込み：
- tech_stack
- is_e2e

## STEP 0: 実行モデル（チーム機能は使わない）

Agent Teams（`TeamCreate` / `team_name`）は使用しません。テストランナーは本エージェントが **同期サブエージェント**（`run_in_background=false`）として順に起動し、**最終回答**で結果を受け取ります。ランナーは SendMessage を送りません。中間オーケストレーター（旧 `test-orchestrator`）も置きません。

技術スタック: `{tech_stack}`
E2E テストあり: `{IS_E2E}`

## STEP 0.5: テスト対象を origin の最新に揃える（必須）

テストはローカルのチェックアウトに対して走る。implementation の PR は GitHub 上でマージされるので、ローカルのベースブランチを pull していないと**古いコードをテストして「全件パス」になる**。

```bash
BASE="$(jq -r '.base_branch // empty' doc/process/state.json)"   # 無ければ現在のブランチ
[ -n "$BASE" ] && git switch "$BASE"
git pull --ff-only
${CLAUDE_SKILL_DIR}/../dev-flow/hooks/verify-remote-state.sh
```

- `summary: NG 0` でなければテストを始めない。ローカルに未 push のコミットがあり、それが `doc/process/` だけの変更（state.json・チェックリスト・flow.log などフローの記録）なら `git push` してから続ける（`git log origin/$BASE..HEAD --name-only` で確かめる）。それ以外の未 push コミットがある、または `git pull --ff-only` が失敗するなら人間に報告して止める
- テストランナーには、この出力と `git rev-parse --short HEAD` を渡す。最終報告の先頭に「テスト対象: {branch}@{短いハッシュ}（origin と同期済み）」と書く

## STEP 0.7: プロファイルによる分岐

`state.json.profile`（無ければ `quality`）で起動するランナーが変わる（`${CLAUDE_SKILL_DIR}/../dev-flow/reference/profiles.md`）。

| profile | 起動するランナー | 続き |
|---|---|---|
| `quality` | `test-runner-opus`（`name="test-runner-opus"`, `run_in_background=false`, `model="opus"`）。プロンプトは下の STEP 3 の test-runner-sonnet プロンプトから「【モデル昇格通知】」と「Haiku 試行履歴」を除き、**試行上限を 5 回**（連続無進捗の上限 2 回はそのまま）、「全テスト通過（Sonnet）」を「全テスト通過（Opus）」に読み替えたもの | 最終回答が「全テスト通過（Opus）」なら STEP 4、エスカレーション報告なら STEP 3 末尾のエスカレーション処理（報告フォーマットの「Haiku 試行履歴」「Sonnet 試行履歴」は「Opus 試行履歴（1〜5回目）」1 つにまとめる）。途中終了・エラーならテストを一度 Bash で実行して現状を確認し、通過していれば STEP 4、失敗が残っていれば同じランナーを 1 回だけ起動し直す |
| `cost` | 下の STEP 1 から（Haiku → Sonnet） | STEP 2・3 のとおり |

## STEP 1: test-runner-haiku を同期起動（cost のみ）

以下の設定で `test-runner-haiku` を起動します（`name="test-runner-haiku"`, `run_in_background=false`, `model="haiku"`）：

**test-runner-haiku プロンプト:**

テストを実行し、全テストが通過するまでプロダクションコードを修正してください。

**厳守事項**: テストコードの修正は絶対禁止。プロダクションコードのみ修正すること。hook 導入環境では `test-stage-guard.sh` がテストファイルへの書き込みを機械的に deny する。テストが誤っていると考える場合は、直さずにエスカレーション報告に「どのテストが・なぜ誤りか・期待値の根拠（テスト定義書の TC-ID）」を書いて終了する。`t.Skip` / `it.skip` / `@pytest.mark.skip` 等でテストを無効化することも禁止（PR マージ時に hook が検出して止める）。

技術スタック: `{tech_stack}`
E2E テストあり: `{IS_E2E}`（true の場合は E2E テストも対象に含める）

**試行上限**: 2回

**ループ: 全テスト通過または試行上限まで繰り返す**

1. ユニットテストを `{tech_stack.test_framework}` で実行し、失敗数を記録する
2. IS_E2E=true の場合は `{tech_stack.e2e_framework}` で E2E テストも実行し、失敗数を合算する
3. 全テスト通過 → 最終回答として「全テスト通過（Haiku）」を返して終了
4. 試行回数が2回に達した場合 → 以下のフォーマットを最終回答として返して終了：

```
## Haiku 試行上限到達

### 試行履歴
| 試行 | 失敗数 | 主な失敗テスト | 試みた修正 |
|---|---|---|---|
| 1回目 | X件 | ... | ... |
| 2回目 | Y件 | ... | ... |

### 現在も失敗しているテスト
（テスト名・失敗理由の一覧）
```

5. テストの期待値を正として、プロダクションコードの問題を特定する
6. プロダクションコードを修正する
7. 修正した変更を git commit する（コミットメッセージ例: `fix: {失敗テスト名} を修正`）
8. 試行回数を +1 して 1 に戻る

---

## STEP 2: test-runner-haiku の結果判定（cost のみ）

Agent 呼び出しが返ったら最終回答を読み取ります：

- **「全テスト通過（Haiku）」** → STEP 4（出力）へ進む
- **「Haiku 試行上限到達」** → STEP 3（Sonnet 昇格）へ進む
- どちらでもない（途中終了・エラー）→ テストを一度 Bash で実行して現状を確認し、失敗が残っていれば STEP 3 へ、通過していれば STEP 4 へ

## STEP 3: Sonnet へ昇格（cost で Haiku が2回失敗した場合のみ実行）

以下の設定で `test-runner-sonnet` を起動します（`name="test-runner-sonnet"`, `run_in_background=false`, `model="sonnet"`）：

**test-runner-sonnet プロンプト:**

【モデル昇格通知】
Haiku が2回試行しましたが全テスト通過に至りませんでした。
以下の履歴を参考に、より高度な分析で問題を解決してください。

### Haiku 試行履歴
{Haiku からの試行履歴をここに挿入}

テストを実行し、全テストが通過するまでプロダクションコードを修正してください。

**厳守事項**: テストコードの修正は絶対禁止。プロダクションコードのみ修正すること。

技術スタック: `{tech_stack}`
E2E テストあり: `{IS_E2E}`（true の場合は E2E テストも対象に含める）

**試行上限**: 3回
**連続無進捗の上限**: 2回（直前2回で失敗数が変化しない場合）

**ループ: 全テスト通過または試行上限まで繰り返す**

1. ユニットテストを `{tech_stack.test_framework}` で実行し、失敗数を記録する
2. IS_E2E=true の場合は `{tech_stack.e2e_framework}` で E2E テストも実行し、失敗数を合算する
3. 全テスト通過 → 最終回答として「全テスト通過（Sonnet）」を返して終了
4. **上限チェック**:
   - 試行回数が3回に達した場合 → 下記フォーマットのエスカレーション報告を最終回答として返して終了
   - 直前2回の失敗数が同じ場合 → 同上
5. テストの期待値を正として、プロダクションコードの問題を特定する
6. プロダクションコードを修正する
7. 修正した変更を git commit する（コミットメッセージ例: `fix: {失敗テスト名} を修正`）
8. 試行回数を +1 して 1 に戻る

**エスカレーション時の報告フォーマット:**

```
## テスト実行エスカレーション

### Haiku 試行履歴（1〜2回目）
| 試行 | 失敗数 | 主な失敗テスト | 試みた修正 |
|---|---|---|---|
| 1回目 | X件 | ... | ... |
| 2回目 | Y件 | ... | ... |

### Sonnet 試行履歴（3〜5回目: Haiku 2回 + Sonnet 最大3回）
| 試行 | 失敗数 | 主な失敗テスト | 試みた修正 |
|---|---|---|---|
| 3回目（Sonnet 1回目） | Z件 | ... | ... |
| 4回目（Sonnet 2回目） | ... | ... | ... |
| 5回目（Sonnet 3回目） | ... | ... | ... |

### 現在も失敗しているテスト
（テスト名・失敗理由・試みた修正の一覧）

### エスカレーション理由
（上限到達 / 無進捗のどちらか）

### AIが判断できなかった理由
（設計の矛盾 / 要件の曖昧さ / その他）
```

---

`test-runner-sonnet`（quality では `test-runner-opus`）の最終回答がエスカレーション報告だった場合は、`doc/process/escalation_test_{timestamp}.md` に保存したうえで AskUserQuestion で人間に状況を報告して指示を仰ぐ（`${CLAUDE_SKILL_DIR}/../dev-flow/reference/escalation-format.md` 参照）。state.json は更新しない。

## STEP 4: 出力

全テスト通過を確認したら、以下を実行：

1. `doc/process/state.json` を更新（`next_stage` を `"compliance"` に）
2. 人間に「test 完了。次は `/dev-flow` を実行して compliance（準拠チェック）に進んでください」と通知
