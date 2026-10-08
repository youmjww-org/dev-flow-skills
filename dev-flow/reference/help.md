dev-flow — 要件定義から実装・テスト・準拠チェックまでを順に進める開発フロー

使い方:
  /dev-flow [オプション] "やりたいこと"
  /dev-flow                       進行中の run を続きから再開する

オプション:
  --kind=feature|change|fix|refactor
                     変更種別。省略時、実装コードがあれば起動時に選択肢が出る
                       feature  新機能（requirements から）
                       change   既存機能の要件変更（requirements の修正モードから）
                       fix      不具合修正（spec の再現 TC 追加から）
                       refactor 挙動を変えない内部改善（consistency から）
  --profile=quality|cost
                     quality（既定）: 速さと正確さ優先。全員 Opus、オーケストレーターが直接実行
                     cost: コスト重視。ステージごとに Haiku の管理役、Sonnet から始めて昇格
  --from=<stage>     指定ステージから再開（requirements / spec / consistency /
                     implementation / test / compliance）。requirements 以外は state.json が必要
  --bootstrap        既存コードから as-is ドキュメントを逆生成する（導入時に 1 回）
  --dry-run          何も実行せず、実行計画と下流スキルの有無だけ表示する
  --help, -h         この使い方を表示する
  --man [topic]      詳しいマニュアルを表示する。topic を付けるとその節だけ
                     topic: options kinds profiles stages state hooks merge outputs troubleshooting

例:
  /dev-flow "ユーザー招待機能を追加したい"
  /dev-flow --kind=fix "退会後もログインできてしまう"
  /dev-flow --profile=cost --kind=refactor "認証ミドルウェアを分割"
  /dev-flow --from=test
  /dev-flow --man profiles

状態は doc/process/state.json に保存される。各ステージの後は /dev-flow を再実行するだけで次へ進む。
