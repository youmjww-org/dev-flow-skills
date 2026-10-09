# タスクチェックリスト生成プロンプト

開発モード: `{MODE}`
baseline_commit: `{BASELINE_COMMIT}`

以下のドキュメントを Read ツールで読み込み、実装・QA タスクを最小単位で抽出して `doc/process/task_checklist.md` を作成してください。

- 要件定義書（全ファイルを順に読み込んでください）: {REQUIREMENTS_PATHS}
- テスト定義書: `{TEST_SPEC_PATH}`
- API仕様書（IS_API=true の場合）: `{API_SPEC_PATH}`
- インフラ仕様書（IS_INFRA=true の場合）: `{INFRA_SPEC_PATH}`

**mode = "incremental" の場合の重要な制約：**

整合性チェックエージェントが検出した「未実装の差分」のみをタスク化してください。  
具体的には：
1. `git diff {BASELINE_COMMIT}..HEAD -- doc/` で変更されたドキュメントを確認する
2. 追加・変更された要件のうち、整合性チェックで「未実装」と判定された箇所のみをタスクにする
3. **既存の実装がある箇所はタスク化しない**（重複実装を防ぐ）
4. タスク数が少なくて正常。差分が小さければ1〜数タスクになることもある

**チーム分類ルール：**

各タスクについて、影響範囲に基づいてチーム種別を判定してください：

| チーム種別 | 判定基準 | 例 |
|---|---|---|
| `Infra` | インフラのみ変更。`terraform/`, `k8s/`, `.github/workflows/`, `cloudformation/`, `ansible/` 等に限定 | EC2 インスタンスタイプ変更、S3 バケット追加 |
| `App` | アプリのみ変更。`app/`, `src/`, `backend/`, `frontend/`, `lib/`, `cmd/` 等に限定 | API エンドポイント追加、UI コンポーネント変更 |
| `Cross` | インフラとアプリの両方に変更が必要 | 環境変数追加（インフラで設定、アプリで読み込み）、新規データベース追加（インフラで構築、アプリで利用） |

判定は要件の内容から推定してください。不明な場合は `Cross` を選択すること。

**CI整備ルール（新規プロジェクトセットアップ時に必須）:**

要件定義書・タスク一覧から、このタスクが**新規にプロジェクト基盤を作る**（`composer create-project` / `npm create vite` 相当のセットアップ、初めてのmigration・初めてのpackage.json 等）と判断できる場合、そのタスクを含むグループの Dev タスクに、以下を1項目として必ず追加すること：

- `{言語・フレームワークに応じた}CI設定: GitHub Actions等でlint/型検査/テストを実行するワークフローを追加する（PRマージの自動化条件を満たすため）`

例: Laravel なら「Pest でテストを実行するCI設定」、React/TypeScript なら「tsc --noEmit / eslint / vitest を実行するCI設定」。CI設定タスクは、そのプロジェクト基盤を作るグループ（`depends_on: []` の最初のグループであることが多い）に含め、後続グループが依存する形にする。**mode = "incremental" の場合はスキップしてよい**（既存プロジェクトには既にCIがある前提）。

CI 設定タスクの説明には次の注意を添える：「`.github/workflows/*.yml` を push するには gh CLI のトークンに `workflow` スコープが必要。無ければ `gh auth refresh -h github.com -s workflow` でデバイス認証を行う（人間のブラウザ操作が要る）。また CI で使う言語ランタイムのバージョンは、ローカルの実バージョン（`php --version` / `node --version` 等）と lock ファイルが要求するバージョンに合わせる（例: ローカル PHP 8.5 で composer.lock を作ったのに CI を PHP 8.3 にすると依存解決で失敗する）」。

**実バージョンの書き戻し（新規プロジェクトセットアップ時）:**

要件定義書の技術スタック表に書かれたバージョン（例: PHP 8.3 / Laravel 12）と、`composer create-project` / `npm install` が実際に解決したバージョン（lock ファイル）は食い違うことが多い（実戦: 要件は Laravel 12 / Vite 6、実際は Laravel 13 / Vite 8）。乖離を compliance まで持ち越すと人間判断が 1 回余分に要る。基盤グループの**最後の Dev タスク**として次を含める：

- `実バージョンの書き戻し: composer.lock / package-lock.json / go.sum / uv.lock 等から言語・フレームワーク・主要ライブラリの実バージョンを読み取り、(1) doc/process/state.json の tech_stack.language_version / framework_version を更新、(2) 要件定義書の技術スタック表の該当行を実バージョンに更新して「lock ファイルより（YYYY-MM-DD）」と注記する。要件定義書のバージョンが「以上」「以下」の制約として書かれている場合は制約を満たすか確認し、満たさなければ blocked で報告する`

`mode = "incremental"` ではこのタスクは不要（bootstrap または前回の run で書き戻し済み。依存を上げるタスクがある場合だけ、そのタスク内で同じ書き戻しを行う）。

**スキャフォールド不要物の除去（新規プロジェクトセットアップ時）:**

`composer create-project` / `npm create` / `rails new` 等のスキャフォールドは、要件に無いものを大量に生成する（例: API 専用なのに `routes/web.php` の welcome ビューと `resources/views/`、`tests/Feature/ExampleTest.php` `tests/Unit/ExampleTest.php`、フロントを別に持つのに `backend/package.json` `vite.config.js` `resources/js`）。さらに **`CLAUDE.md` / `AGENTS.md` を生成するもの**（Laravel Boost 等）があり、サブエージェントがそれを読むと「`composer require xxx` を実行せよ」等の指示に従って要件外の依存を増やしかねない。CI 整備タスクと同じ基盤グループに、次のタスクを必ず含める：

- `スキャフォールド不要物の除去: 要件定義書の技術スタック（API 専用 / Blade 不使用 / フロントは別ディレクトリ 等）に照らして、生成物のうち使わないもの（ビュー・サンプルテスト・不要な package.json / vite 設定・サンプルルート）を削除する。生成された CLAUDE.md / AGENTS.md はプロジェクトの規約（doc/conventions.md）に置き換えるか削除する。削除したものの一覧を PR 説明に書く`

**E2E グループの Dev / QA 分担ルール:**

E2E テスト（Playwright 等）のグループは「環境構築」と「テストシナリオ実装」が不可分で、Dev と QA に分けると**双方が同じ `e2e/` ディレクトリ一式を作ってしまう**（実例あり）。E2E グループは次のいずれかにする：

- **推奨: Dev タスクのみにする**。Dev タスクに「E2E 環境セットアップ」「E2E シナリオ実装（TC-NNN, TC-NNN）」を並べ、QA タスク欄は「（E2E は Dev が環境とシナリオを一括実装するため QA タスクなし。TC-NNN は Dev タスクで実装される）」と注記する。implementation は QA worktree を作らない（STEP A のルール）
- 分ける場合は、ファイルパス単位で担当を明記する（例: Dev = `e2e/playwright.config.ts` と `e2e/scripts/`、QA = `e2e/tests/*.spec.ts`）。担当外のパスには触らないことをタスク文に書く

**タスクチェックリストフォーマット:**

```markdown
# タスクチェックリスト

## ステージ進捗

- [x] 1. requirements: 要件定義
- [x] 2. spec: 仕様書生成
- [x] 3. consistency: 整合性チェック・設計凍結
- [ ] 4. implementation: 並列実装（Dev / QA）
- [ ] 5. test: テスト実行
- [ ] 6. compliance: ドキュメント準拠チェック・完了

（この 6 行は hook が `state.json.next_stage` に合わせて自動同期する。行頭の `- [ ] N. <stage>:` の形式を変えないこと）

## 並列実行グループ

グループをタスクの依存関係に基づいて分割すること（独立したタスクを同一グループに、依存があるタスクを別グループに配置）。

**重要: 各グループには必ずチーム種別（Infra / App / Cross）を明記すること。**

## グループ間依存DAG

グループ間の依存関係を以下のフォーマットで必ず明記すること。依存が無いグループは `depends_on: []` とし、implementation で並列実行される。

```
- グループ 1 (Infra) — depends_on: []
- グループ 2 (App)   — depends_on: []
- グループ 3 (Cross) — depends_on: [group-1, group-2]
```

**並列実行ルール**: `depends_on` が空のグループ、またはすべての依存先が完了済みのグループは同時起動可能。

### グループ 1 (Infra) — depends_on: []

#### Dev タスク (Infra)
- [ ] {機能名}: {具体的な実装内容}

#### QA タスク (Infra)
- [ ] {テストケース名}: {テストの内容}

### グループ 2 (App) — depends_on: []

#### Dev タスク (App)
- [ ] {機能名}: {具体的な実装内容}

#### QA タスク (App)
- [ ] {テストケース名}: {テストの内容}

### グループ 3 (Cross) — depends_on: [group-1, group-2]

#### Dev タスク (Infra)
- [ ] {インフラ側の実装内容}

#### Dev タスク (App)
- [ ] {アプリ側の実装内容}

#### QA タスク (App)
- [ ] {テストケース名}: {テストの内容}

## 実装タスク（Dev チーム）全一覧
- [ ] {機能名}: {具体的な実装内容}
...

## QA タスク（QA チーム）全一覧
- [ ] {テストケース名}: {テストの内容}
...

## 手動 QA タスク（自動化不可・デプロイ後確認等）
（自動テストでカバーできない結合確認・インフラ確認等がある場合のみ追加。なければこのセクションは省略する）
- [ ] {確認内容}: {手順と期待結果}
...
```

各タスクは1コミットで完結できる粒度（1〜2時間程度）に分割すること。グループ分けは依存関係を基準にし、独立タスクはできるだけ同一グループにまとめて並列効率を高めること。

**テストの分担（タスク文に反映すること）:** Dev タスクは「実装 + その関数・クラス・コンポーネントのユニットテスト（分岐網羅）」を 1 タスクに含む（粒度の見積もりにユニットテスト分を入れる）。QA タスクは「テスト定義書の TC-NNN を仕様テスト（Feature / App 結合 / E2E）として実装する」もの。同じ TC-ID を Dev タスクにも書かない。ただし、今回の実装で**既存テストの期待値が変わる**もの（`status: modified` の TC、usage・出力形式の変更で落ちる既存のテスト）は、挙動を変える Dev タスクに「既存テストの期待値を更新: {ファイル}（TC-NNN）」として入れ、QA タスクには書かない（分けると Dev / QA どちらの PR も単独で CI を通らない）。詳細は `dev-flow-implementation/reference/conventions/testing.md` の「Dev と QA のテスト分担」。
完了したら SendMessage は使わず、最終回答として「checklist 生成完了: doc/process/task_checklist.md」と生成内容の要約（グループ数・タスク数・判断に迷った点）を返してください。
