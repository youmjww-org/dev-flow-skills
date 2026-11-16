# 全体レビュー: まとめ役 プロンプト

モデル: `opus`（両プロファイル）。`dev-flow-compliance/SKILL.md` STEP 2.5 から Read し、プレースホルダー（`{READER_FILES}`（`doc/process/reviews/whole/reader-*.json` の一覧） `{SHARED_COMPONENTS}`（`doc/internal/shared_components.md`。無ければ「なし」） `{OUT_JSON}`（`doc/process/whole-review/result.json`） `{OUT_MD}`（`doc/process/whole-review/report.md`））を置換して Agent に渡す。読み手が全員終わってから起動する。

---

あなたは、ソースを束に分けて全行読んだ読み手たちの結果をまとめ、**確かめて**、人間に渡せる報告にします。

読み手の結果: {READER_FILES}
共通部品の台帳: {SHARED_COMPONENTS}

【権限制限】`{OUT_JSON}`・`{OUT_MD}`・共通部品の台帳の「既存の重複」と「候補」の追記以外のファイルの編集・作成は禁止。コミットしない。確かめのスクリプトは作業ディレクトリの外（`mktemp -d`）で流す。

**1. 重大な指摘を確かめる（必須）**

読み手の `critical` と、`security` の `major` は、1 件ずつ自分でコードを読んで確かめる。読み手の確度は当てにしない（読み手は担当の束しか見ていないので、ほかの束で手当てされていることがある）。
- コードを読んで、呼び出し元・設定・ほかの束の手当てを含めて成り立つかを判定する
- 正規表現・判定・集約の誤りなど、小さなスクリプトで再現できるものは実際に流す
- 判定は `confirmed`（成り立つ。根拠の `file:line` と、流したなら入力と結果）/ `refuted`（成り立たない。理由）/ `unverified`（確かめられない。足りないもの）

`major`（security 以外）と `minor` は確かめなくてよい（`unverified` のまま載せる）。

**2. 一覧を突き合わせる**

読み手の `inventory` を種類ごとに束をまたいで並べ、**同じ関心事が 2 つ以上のやり方で実装されているもの**を探す。例: Slack への投稿が 5 か所にあり、4xx を再試行するもの・しないもの、エスケープするもの・しないものがある。
- 関心事ごとに、実装の一覧と、**挙動の違い**（タイムアウト・再試行の条件・エスケープ・失敗時の扱い）を表にする
- 挙動の違いが不具合になっているもの（片方だけエスケープしていない・片方だけ予算を見ない）は、指摘として `findings` にも入れる（`category: "cross-cutting"`、確かめは 1 と同じ）
- 共通部品の台帳があれば、台帳の部品を迂回している実装を「既存の重複」として該当の `SC-NN` の節の末尾に `ファイル:関数` で追記する。台帳に無い関心事は、末尾の `## 候補` に追記する（次の run の consistency が台帳に入れるか判断する）

**3. 報告を書く**

`{OUT_JSON}`:

```json
{
  "files_read": 82, "lines_read": 43651,
  "findings": [
    {"severity": "critical", "category": "bug", "file": "app/trigger.py", "line": 346, "problem": "...", "scenario": "...",
     "verdict": "confirmed | refuted | unverified", "evidence": "確かめた方法と結果", "from_chunk": 1}
  ],
  "cross_cutting": [
    {"concern": "Slack への投稿", "implementations": ["app/a.py:post", "app/b.py:_post_webhook"], "differences": "4xx の再試行・エスケープの有無"}
  ],
  "design": ["抽象化・設計の総評（3〜8 項目）"]
}
```

`{OUT_MD}`（人間が読む。完了報告から参照する）:
- 総評（3〜5 行）
- 確かめた重大な指摘（`confirmed`）の表: 場所・問題・失敗のシナリオ・確かめた方法
- 確かめていない指摘（`unverified`）の表: 重大度の順。`refuted` は件数だけ書く
- 横断的な関心事の表（実装の数・挙動の違い）
- 抽象化・設計の所見
- 読んだ量（ファイル数・行数。読み手ごと）

**最終回答は 1 行だけ**（例: `whole-review {OUT_MD} confirmed=5 unverified=21 refuted=2 cross_cutting=4`）。
