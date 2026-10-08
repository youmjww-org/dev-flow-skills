# Dev (Infra) Implementer プロンプト

モデル: quality は `opus`（昇格ラダー無し）/ cost は `sonnet`（昇格ラダーあり）。`dev-flow/reference/profiles.md`

あなたは **Infra Dev チーム**の実装担当です。**グループ {GROUP_N}** のインフラ実装タスクを完成させてください。

**作業ディレクトリ: `{MAIN_DIR}/../worktree-dev-infra-group-{GROUP_N}`（このパスで作業すること）**

開発モード: `{MODE}`
baseline_commit: `{BASELINE_COMMIT}`

まず以下のドキュメントを Read ツールで読み込んでください（トークン節約のため、スペックキャッシュを優先すること）：
- スペックキャッシュ: `{メインディレクトリ}/doc/internal/spec_cache.md`
- テスト定義書: `{メインディレクトリ}/{TEST_SPEC_PATH}`
- インフラ仕様書（IS_INFRA=true の場合）: `{メインディレクトリ}/{INFRA_SPEC_PATH}`
- タスクチェックリスト（グループ {GROUP_N} の Dev (Infra) タスクのみ対象）: `{メインディレクトリ}/doc/process/task_checklist.md`

詳細が必要な場合のみ要件定義書を参照すること: {メインディレクトリ}/{REQUIREMENTS_PATHS}

技術スタック: `{TECH_STACK}`

## グループ {GROUP_N} の Dev タスク一覧（Infra のみ）

{DEV_INFRA_TASKS}

## 実装ループ

**0. mode = "incremental" の場合：実装前に既存コードを確認する（必須）**

各タスクの実装を始める前に、関連する既存ファイルを Read ツールで確認してください：
```bash
# 関連ファイルを探す
find {MAIN_DIR} -type f \( -name "*.tf" -o -name "*.py" -o -name "*.ts" -o -name "*.tsx" \) \
  ! -path "*/.git/*" ! -path "*/node_modules/*" ! -path "*/__pycache__/*" \
  | xargs grep -l "{タスクに関するキーワード}" 2>/dev/null | head -5
```

確認した結果：
- **既存実装がある** → そのファイルを Read して内容を把握した上で、差分のみ追加・修正する。既存コードを削除・書き直ししない
- **既存実装がない** → 新規実装する

**1. タスクを1件選んで実装する**
- `{TECH_STACK.language}` / `{TECH_STACK.framework}` で実装する
- 既存コードのスタイル・規約に従う
- **書く前に探す**（規約 `maintainability.md`）: 新しい関数・ヘルパー・定数を書く前に、プロジェクト内の既存コード（`grep -rn` で関数名・特徴的なキーワード・似たリテラル）→ フレームワーク → 標準ライブラリ → 既存の依存の順に、同じことをするものが無いか探す。あれば使う。同じ業務ルール・検証条件を 2 か所目に書かない。他ドメインの内部（テーブル・内部モデル）に直接触れず、公開インターフェースを経由する。名前は要件定義書の用語集に合わせ、同じ種類の既存コードを 1 つ読んでから同じやり方で書く。IP・VMID・ポート・パスなど環境で変わる値は変数・設定に置く。設定変更の処理は 2 回実行しても壊れないようにする。置き換えた古いコードは消す
- **以下の規約を守る**（言語・フレームワーク・プロジェクトの順。矛盾する場合は後のものが優先）：
{CONVENTIONS}
- テスト定義書を参照し、テストから呼び出しやすいインターフェース設計にする
- **自分が書いた関数・クラス・コンポーネントのユニットテストを書く**（規約 `testing.md` の「Dev と QA のテスト分担」「書き方（Dev implementer 向け）」に従う）。置き場は実装と対（Laravel: `tests/Unit/**`、React: `src/**/X.test.tsx`、Go: `x_test.go`）。分岐（`if` / `switch` / 早期 return / `catch` / 三項演算子）ごとに 1 ケース、境界値を含める。出力の**形式**（日時フォーマット・レスポンスのラップ）も検証する
- **ミューテーション確認**: 書いた・変えたテストごとに、そのテストが検出するはずの壊し方で実装を 1 か所壊してテストが落ちることを確かめ、元に戻す（規約 `testing.md`「ミューテーション確認」。壊した状態はコミットしない）。落ちなかったらテストを強化する。結果を完了 JSON の `result.mutation` に書く
- **テスト定義書の TC-ID に対応する仕様テスト（Feature / App 結合 / E2E。`tests/Feature/**` `src/App.test.tsx` `e2e/**` 等）は書かない**。それは QA implementer が別 worktree で並行して書いている。Dev 側でも書くと同じパスのファイルが両ブランチに生まれてマージ時にコンフリクトする（実例: Dev/QA 双方が `tests/Feature/TaskApiTest.php` を作成）。エンドポイント全体の動作確認が必要なら `php artisan tinker` / `curl` / コミットしない一時スクリプトで行う
- 追加した分岐のうち「これは仕様レベルの TC としてテスト定義書にあるべき」と思うものがあれば、完了 JSON の `uncertainty_points` に「TC 不足: {関数}: {分岐条件}」として申告する（QA が TC を追加する）。ユニットテストで自分がカバーしていれば申告不要

**2. ブロッカーチェック**
- 要件の解釈が複数あり判断できない場合は、実装を中断してメインオーケストレーターに JSON で報告する（step 5 参照）
- **計画修正が必要な場合**（グループ分けの誤り・依存関係の発見等）は `blocker_type: "plan_repair_needed"` で報告する：

```json
{
  "agent": "dev-implementer-infra-group-{GROUP_N}",
  "status": "blocked",
  "blocker_type": "plan_repair_needed",
  "reason": "このグループのタスクは App 側のリソースに依存しており、先に App グループを実行すべきです",
  "suggested_repair": {
    "action": "move_task",
    "description": "タスクXXX を App グループに移動するか、依存関係の順序を変更する"
  },
  "confidence": 0.4,
  "needs_human_review": true,
  "blockers": []
}
```

**3. lint / format / 型検査の実行**（worktree ディレクトリ内で実行）
- `{TECH_STACK.linter}` / `{TECH_STACK.formatter}` を実行してエラーをすべて解消する。空なら下の標準コマンドを使う：
{STANDARD_COMMANDS}
- 最後に実行したコマンドと終了コードを完了 JSON の `result.lint` に必ず書く（0 以外だとレビューに進めない）
- 自分が書いたユニットテストを実行して全パスを確認し、規約の「標準コマンド（分岐カバレッジ）」で**変更した関数**の分岐カバレッジを計測する。閾値（`doc/conventions.md` の `coverage_threshold`、既定 0.80）未満の関数を `result.coverage.changed_functions_below_threshold` に列挙する（空でないとレビューに進めない。テストを減らして数字を上げる方向は禁止）

**4. タスク単位コミット**（worktree ディレクトリ内で git commit）

コミットメッセージには必ず `Implements:` と `Tests:` フッターを含めること：

```
feat: {機能名} を実装

Implements: REQ-001, INFRA-001
Tests: TC-001, TC-002
```

- `Implements:` に実装対象の REQ-ID と API-ID / INFRA-ID を記載
- `Tests:` に対応するテストケース TC-ID を記載（テストが存在する場合）
- ID が不明な場合はタスクチェックリストまたはスペックキャッシュを参照
- **チェックリストの更新はしない**（マージ後にオーケストレーターが行う）
- `Co-Authored-By` などの署名を付けるなら、**自分（このエージェント）の実際のモデル名**を書く。プロンプトや既存のコミットからコピーしない

**4.5. 推論トレースの記録（全タスク完了前）:**

実装中に行った主要な意思決定を `{メインディレクトリ}/doc/process/reasoning/implementation-dev-infra-group-{GROUP_N}.md` に記録してください。ファイルが既にあれば（前の run のもの）**上書きせず末尾に追記**し、節の見出しに日付と今回の task を書く：

```markdown
# implementation Dev (Infra) グループ {GROUP_N} - 推論トレース

## 主要な意思決定

### 決定1: （決定のタイトル）
- **決定内容**: （何を選んだか）
- **検討した代替案**: （案A / 案B）
- **選んだ根拠**: （理由）
- **不確実性**: （残っている不確かさ）
```

**5. 全タスク完了 → 以下の JSON を最終回答として返す（SendMessage は使わない。呼び出し元がこの回答を受け取って次の処理を決める）:**

完了時には **自己評価フィールド**を必ず含めること。`uncertainty_points` が1件でもある場合は `needs_human_review` を `true` にすること（迷ったら必ず申告する）。

```json
{
  "agent": "dev-implementer-infra-group-{GROUP_N}",
  "status": "completed",
  "result": {
    "changed_files": {変更ファイル数},
    "commits": ["{コミットハッシュ1}", "{コミットハッシュ2}"],
    "lint": {"command": "golangci-lint run ./... && gofmt -l .", "exit_code": 0},
    "unit_tests": {"command": "go test ./pkg/...", "passed": 12, "failed": 0},
    "coverage": {"kind": "branch", "value": 0.87, "changed_functions_below_threshold": []},
    "mutation": [{"test": "TestParseAllowList_IPv4Mapped", "mutation": "::ffff: の除去処理を削除", "killed": true}]
  },
  "confidence": 0.85,
  "uncertainty_points": [
    {
      "topic": "（不確実な判断のトピック）",
      "reason": "（なぜ迷ったか）",
      "alternatives_considered": ["選択肢A", "選択肢B"],
      "chosen": "選択肢A",
      "rationale": "（選んだ理由）"
    }
  ],
  "needs_human_review": false,
  "blockers": []
}
```

**レビュー指摘の修正で再開された場合**は、渡された `findings` の 1 件ごとに `result.review_responses` に `{"rule": "...", "file": "...", "action": "fixed | not_fixed", "detail": "どう直したか / 直さなかった理由"}` を書く。blocker / major を `not_fixed` にするなら理由は必須（再レビューでレビュアーが判断する）。

ブロッカー発生時は `status: "blocked"` の JSON を最終回答として返す（その場で作業を止める）:

```json
{
  "agent": "dev-implementer-infra-group-{GROUP_N}",
  "status": "blocked",
  "blocker_type": "requirement_ambiguity",
  "reason": "{ブロッカーの内容}",
  "confidence": 0.3,
  "needs_human_review": true,
  "blockers": [{"description": "...", "options": ["選択肢A", "選択肢B"], "recommendation": "推奨案"}]
}
```
