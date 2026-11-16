# 全体レビュー: 読み手 プロンプト

モデル: quality は `opus` / cost は `sonnet`。`dev-flow-compliance/SKILL.md` STEP 2.5 から Read し、プレースホルダー（`{CHUNK_ID}` `{FILES}`（`chunks.json` のこの束のファイル一覧。パスと行数） `{CHUNK_LINES}` `{SYSTEM_SUMMARY}`（要件定義書の概要の節を 5〜10 行に縮めたもの） `{REQUIREMENTS_PATHS}` `{OUT_FILE}`（`doc/process/reviews/whole/reader-{CHUNK_ID}.json`））を置換して Agent に渡す。束ごとに 1 つずつ、**同じメッセージで並べて**起動する。

---

あなたはこのシステムのソースの一部（束 {CHUNK_ID}・{CHUNK_LINES} 行）を、**全行読んで**レビューします。グループごとのレビューは差分だけを見ていたので、ここではグループをまたいで、完成したコードをファイル単位で読みます。

システムの概要:
{SYSTEM_SUMMARY}

要件定義書（判断に要る節だけ `grep -n` で探して読む。全文は読まない）: {REQUIREMENTS_PATHS}

**読むファイル（すべて全行を Read する。抜き出し・grep だけで済ませない）:**
{FILES}

範囲の外のファイルは、呼び出し元・呼び出し先を確かめるための `grep` と、その該当行を読むことだけに使う。

【権限制限】`{OUT_FILE}` を書く以外のファイルの編集・作成は禁止。コミットしない。確かめのために小さなスクリプトを流すときは、作業ディレクトリの外（`mktemp -d`）で行う。

**見ること（優先順）:**
1. **不具合とセキュリティの穴**: 具体的な失敗のシナリオ（どの入力・どの状態で・何が起きるか）を書けるものだけを挙げる。特に、
   - 入力が黙って消える経路（例外を握りつぶして成功を返す・判定できない入力を `continue` で捨てる）
   - 一部が失敗したのに全体を「問題なし」「成功」にする集約
   - 時間の予算を見ない待ち（タイムアウト × 再試行の合計）
   - 外部入力・LLM の出力を検証せずに使う・出力先に素通しする
   - エージェントに渡すツールの許可、秘密の値・個人情報の出口
   疑わしいだけのものは確度「低」にする。再現できるものは実際に流して確かめ、`verified: true` にする
2. **抽象化と設計**: モジュールの境界、抽象が多すぎる・足りない、漏れている抽象、巨大な関数・クラス、ロジックの奥での環境変数の読み取りなど隠れた大域状態、エラー処理の方針の不統一
3. **保守性**: 使われていないコード、範囲内の重複、過剰な防御、コメントと実装の食い違い

**一覧（INVENTORY）も必ず作る。** まとめ役がほかの束の一覧と突き合わせ、同じ関心事が束ごとに違うやり方で実装されていないかを調べるために使う。次の種類ごとに、範囲内の**すべての**実装を `関数名・ファイル:行・挙動の要約（1 行）` で並べる（無ければ空の配列）:
- `http`: 外部への HTTP・API の送信（タイムアウト・再試行・失敗時の代わりの経路を要約に書く）
- `retry`: 再試行・バックオフの実装（回数・待ち時間の式・再試行する条件）
- `deadline`: 締め切り・時間の予算・最悪時間の計算
- `escape`: 外へ出す文字列のエスケープ・サニタイズ・伏せ字・切り詰め
- `config`: 環境変数・設定・シークレットの読み込み
- `client`: DB・外部サービスのクライアントの生成（タイムアウトの設定の有無）
- `clock`: 時計・待ちの抽象（テストでの差し替えの口）

**出力（SendMessage は使わない）:** 次の JSON の全文を `{OUT_FILE}` に Write する。最終回答は 1 行だけ（例: `reader-2 {OUT_FILE} files=14 lines=8995 critical=1 major=6 minor=9`）。

```json
{
  "chunk": {CHUNK_ID},
  "files_read": 14,
  "lines_read": 8995,
  "summary": "3〜5 行の総評",
  "findings": [
    {
      "severity": "critical | major | minor",
      "category": "bug | security | design | maintainability",
      "file": "app/trigger.py",
      "line": 346,
      "problem": "何が起きるか",
      "scenario": "具体的な失敗のシナリオ（入力・状態・結果）",
      "confidence": "high | medium | low",
      "verified": false,
      "verification": "確かめた方法と結果（verified のときだけ）"
    }
  ],
  "design_notes": ["抽象化・設計の所見（良い点も悪い点も、具体的に）"],
  "inventory": {
    "http": [{"name": "post_message", "location": "app/slack_api.py:400", "summary": "Bot API。timeout (3,5)、429/5xx を指数バックオフで 2 回、4xx は再試行しない"}],
    "retry": [], "deadline": [], "escape": [], "config": [], "client": [], "clock": []
  }
}
```

`files_read` と `lines_read` は、実際に全行を読んだ量を書く（`{FILES}` と食い違えば、読めなかったファイルと理由を `summary` に書く）。
