# urawa-new-store(浦和新店舗情報)

さいたま市浦和区の「新規オープン店舗」を、求人サイトの一覧ページから自動で見つけて蓄積し、静的サイトとして公開するための小さなプロジェクトです。

- 公開URL: https://netafull.github.io/urawa-new-store/
- 取得するのは、店名・勤務地・開店時期だけです。求人の本文・タイトル・画像・給与は保存も表示もしません。
- 求人サイトの掲載内容から「オープン予定らしい」と判断した情報です。開店日・営業内容は店舗の公式発表をご確認ください。

## 構成

```
config.json          設定(サイト名・取得元・上限・間隔・User-Agent など)
scripts/
  crawl.py           取得と判定のエントリ(data/stores.json を更新)
  parsers.py         一覧・詳細ページのHTMLから必要な項目を取り出す
  rules.py           新店シグナル・除外・浦和区判定・名寄せ・公開可否の判定
  period.py          開店時期の正規化
  fetch.py           HTTP取得(間隔・上限・robots.txt・403/429で中止)
  generate_site.py   docs/ の静的サイトを生成
data/
  stores.json        店舗レコード(これが蓄積データ)
docs/                生成されたサイト(GitHub Pages で公開)
tests/               pytest
.github/workflows/update.yml   更新ジョブ(workflow_dispatch のみ)
```

## 使い方

```
pip install -r requirements.txt pytest
python3 scripts/crawl.py            # バイトル+求人ボックスを取得(約5分)
python3 scripts/crawl.py baitoru    # 片方だけ(kyujinbox も同様)
python3 scripts/generate_site.py    # docs/ を作り直す(ネットワーク不要)
python3 -m pytest tests
```

- 手元で見る: `python3 -m http.server 8765 --directory docs` を起動して http://localhost:8765/ を開く(確認後は Ctrl+C で止める)
- `python3 scripts/generate_site.py --today 2026-10-24` で「今日」を固定できる(環境変数 `URAWA_SITE_TODAY` でも可)
- `URAWA_SAVE_HTML_DIR=<dir> python3 scripts/crawl.py` ... 取得した一覧HTMLを `<dir>/baitoru/` `<dir>/kyujinbox/` に保存する(テスト・再判定用)
- `URAWA_OFFLINE_HTML_DIR=<dir> python3 scripts/crawl.py` ... ネットワークに出ず、保存済みHTMLを読んで判定だけやり直す(`run_summary.json` の `mode` が `offline`)

## 更新の仕組み

`.github/workflows/update.yml` は `workflow_dispatch` だけで動く(GitHub の schedule は使わない)。Google Cloud Scheduler のジョブ `urawa-update`(毎朝7時 JST)が workflow_dispatch API を呼んで起動する。GitHub の schedule は実行の欠測が長く出ることがあるため避けている。

**GitHub Actions 上ではバイトルを取得しない**: Actions のIPからバイトルは毎回403になるため、環境変数 `GITHUB_ACTIONS=true` のときは `config.json` の `skip_sites_in_ci`(`["baitoru"]`)に挙げたサイトへ、robots.txt を含めて一切リクエストを出さない(拒否されている相手に毎朝アクセスし続けないため。回避はしない)。`run_summary.json` の該当サイトは `status="skipped_in_ci"`。手元のMacでは従来どおりバイトルも取得する。

ジョブの流れ: 取得(`crawl.py`)→ サイト生成(`generate_site.py`)→ 変更があればコミットして push。取得対象のサイト(スキップしたサイトを除く)で一覧が1件も取れなかったとき(403/429など)は `crawl.py` が非ゼロで終了し、`data/` と `docs/` を更新せずにジョブが失敗する。通知は未設定。

## 出力

`data/stores.json` の項目(これだけ。求人の文言は1文字も入らない):

| 項目 | 内容 |
|---|---|
| `store_name` | 店名(取れなければ空) |
| `location` / `station` | 勤務地(区・町名・最寄駅) / 最寄駅 |
| `ward_status` / `ward_basis` | 浦和区か区不明か / 判定根拠(定型文) |
| `open_period` / `open_period_precision` / `open_period_start` / `open_period_estimated` | 正規化した開店時期(下記) |
| `sources[]` | 出典サイト名・出典の一覧ページURL・最終確認日 |
| `first_seen` / `last_seen` | 初回発見日 / 最終確認日 |
| `publishable` / `publish_reason` | 公開表示してよいか / その理由コード |
| `key` | 名寄せキー(正規化した店名+駅。店名なしは句のハッシュ8桁) |

- `ward_status` が `浦和区` のものが新店候補、`区不明` は別枠(判定保留)
- 今回の取得に現れなかった店舗も stores.json に残る。`last_seen` は更新しない(最後に確認できた日がそのまま残る)。自動では消さない
- `data/run_summary.json` は実行ログ(件数・除外理由・サイト別ステータス)で、コミットしない

## 取得元と上限(config.json で変更可能)

1. バイトル: 浦和区のオープニングスタッフ・新着順の一覧。最大5ページ
2. 求人ボックス: 「{キーワード}の仕事-さいたま市浦和区」
   - キーワード オープニングスタッフ / オープニング / 新規オープン: 各最大5ページ
   - キーワード 店長候補 / オープニングメンバー / 新規開院: 各最大2ページ。キーワード自体は新店の根拠にならず、開店語との組み合わせが必須
3. バイトルの求人詳細(`/jobdetail/{ID}/`): 新店シグナルがあり、除外されず、浦和区か区不明の求人だけ。同一店舗は1件のみ、最大15件(`max_detail_fetches`)。店名・住所(区の判定)・開店時期の判定にだけ使い、読んだ文言は保存しない

1実行の最大リクエスト数は 2(robots) + 5 + 15 + 6 + 15 = 43。上限は `max_requests_per_run`=50。

## 守っている取得ルール

- 全リクエストの間隔は6秒(下限5秒)
- User-Agent は `urawa-new-store/0.1 (+https://netafull.github.io/urawa-new-store/)`(サイトの運営者が見て分かる自己識別型)。403/429が返ったら偽装・回避はせず、そのサイト(詳細取得なら詳細取得)を止めて記録する
- robots.txt を毎回読み、Disallow のパスは取らない。加えて自前の禁止リストを持つ。求人ボックスの詳細系(/jb/ /jbn/ /jbi/ /rd/ /api/ ほか)は一切取らない。バイトルの詳細は robots が許す範囲で、上記の条件の店だけ
- **保存しないもの**: 求人の本文・タイトル・スニペットとその断片、画像、給与、求人詳細URL、掲載開始日。判定のために文言を読むのはメモリ内だけで、stores.json・run_summary.json・ログには書き出さない
- 各求人サイトの運営会社とは関係のない個人のサイトです

## 判定ルール

1. 新店シグナル: タイトル/スニペット/店名欄に オープン・OPEN・新店・開園・開校・開業・開院・新規出店 のいずれか。次は根拠にしない: 「オープニング」だけの定型文、「店長候補」「オープニングメンバー」、オープンキッチン、「オープン3年以内」、リニューアルオープン、「新店舗続々」、営業時間の「~OPEN」、「年間N教室オープン」、「16OPEN」のような数字+OPEN、「2026年もNEW OPEN」「今年もOPEN」「今後も出店/開院を予定」のような会社の宣伝文
2. 求人ボックスのスニペットは定型句が多いため、タイトル/店名欄に開店語が無くスニペットだけの場合は、開店時期(月・予定など)が読めるか、「新店の…」と言い切っているものだけ拾う
3. 除外(理由は run_summary に件数): 警備・誘導 / 試験 / イベント・催事・物産展 / 増員・欠員 / 短期・単発 / 「11/20~12/6」型の日付範囲 / 日払いだけが売り / すでに開店済み(already_open) / 開店の句に他の市・県・浦和区以外のさいたま市の区が書かれている(other_area_in_text)
4. 浦和区判定: 住所に「さいたま市浦和区」→浦和区。住所が無く最寄駅が 浦和駅・北浦和駅 →浦和区。他の区・市・県は対象外。武蔵浦和・中浦和・東浦和・浦和美園・与野・さいたま新都心・大宮などの駅は浦和区外。住所は浦和区でも最寄駅が浦和区外の駅、または店名に与野・大宮・川口などを含む場合は「区不明」。判定できないものも「区不明」
5. 名寄せ: 店名(全角半角・空白・記号・末尾の「店」をそろえる)+最寄駅で1店舗1レコード。出典は配列。店名なしのレコードは、最寄駅が同じで町名が食い違わず、かつ次のいずれかのときだけ店名ありに寄せる(迷ったら統合しない): (a) 同じ実行内で、判定に使った句(メモリ上の一時値)が一致または先頭一致(8文字以上) (b) 町名が両方わかっていて、正規化した開店時期(日/旬/月/季節)が一致

## 開店時期の正規化(`scripts/period.py`)

- `open_period`(表示用)/ `open_period_precision`(`day` `ten_days` `month` `season` `year` `unknown`)/ `open_period_start`(ISO日付。並べ替え用。不明は null)/ `open_period_estimated`
- 例: 「12月上旬OPEN」→ 2026年12月上旬 / 「11月24日OPEN」→ 2026年11月24日 / 「令和9年4月に開園」→ 2027年4月 / 「今秋オープン」→ 2026年秋(season)
- 季節: 春=3〜5月 夏=6〜8月 秋=9〜11月 冬=12〜2月。`start` は季節の最初の日(冬は12/1)。旬は 上旬/初旬=1日、中旬/中頃=11日、下旬=21日
- 年のない月・日は実行日から補う: 今月以降は今年 / 過去2か月以内の月は開店済みの可能性として新店候補から外す / それより前の月は翌年に補い `open_period_estimated=true`・表示は「2027年4月（推定）」
- 年つきの日付が実行日の2か月以上前、または「オープンした」「開院済み」などの過去表現 → `already_open` として除外
- 開店語はあるが時期が読めない場合は「時期不明（開店予定の募集あり）」、precision=unknown

## publishable(公開表示してよいか)

サイトに載せるレコードを絞るフラグ。`publishable=true` は「店名があり、浦和区で、店名が業種名・求人の役職名・宣伝文・途切れた文字列ではない」レコード。`false` の理由コード(`publish_reason`): `no_store_name`(店名なし)/ `ward_unknown`(区不明)/ `name_is_category`(業種名)/ `name_is_job_title`(役職名・募集語)/ `name_is_promo`(宣伝文の記号)/ `name_truncated`(途切れ)。`true` でも新店であることの保証ではない。

## サイト(docs/)

`generate_site.py` が `data/stores.json` の `publishable=true` だけから `docs/index.html`(一覧)、`docs/about.html`、`docs/style.css`、`docs/assets/icon.svg`、`docs/.nojekyll` を作る(毎回作り直す)。外部CDN・外部スクリプトは使わない。

- 一覧は「近日中（今後2か月）」「それ以降」「時期不明」「オープンしたとみられるお店」の順。開店時期の「終わり」(日=その日、上旬/中旬/下旬=10日/20日/月末、月=月末、季節=最終月の月末、年=12月31日)を過ぎた店は開店済みとみなし、`recent_open_days`(60)日以内なら最下部の「オープンしたとみられるお店」に出し、それより古いものはサイトに出さない(stores.json には残る)。件数表示は実際に載せた数。初回発見日から `new_days`(2)日間は「新着」(発見日を1日目と数える。`new_badge_from` より前に見つけた初回の一括取得ぶんには付けない)、最終確認日が `stale_days`(30)日以上前なら薄く表示
- 出典リンクは `rel="noopener noreferrer nofollow"` の別タブ。同じ求人サイトの出典は1つにまとめる
- config.json: `site_name`、`site_tagline`、`site_color`、`noindex`(既定 false。true で全ページに noindex,nofollow)、`recent_open_days`(60)、`skip_sites_in_ci`、`stale_days`、`new_days`、`new_badge_from`、`near_months`
- canonical・OGP・サイトマップ・GA4・CNAME は未設定(必要になったら追加する)

## テストのフィクスチャ

求人サイトの実HTML(`tests/fixtures/`)は、求人本文を含むためリポジトリに入れていない(`.gitignore`)。フィクスチャが無い環境では、それを使うテスト(`tests/test_parsers.py`)は自動でスキップされ、失敗しない。ほかのテストは合成データで動く。取り直す手順は `tests/fixtures_manifest.txt` を参照(`URAWA_SAVE_HTML_DIR` で保存した一覧HTMLを、その表の名前に付け替えて置く)。

## 既知の弱点

- 求人ボックスの詳細は取らないため、店名が会社名しか取れず店名なしになるレコードが残る(publishable=false になる)
- 一覧にその店の開店の記載が無い求人は拾えない
- 「新店オープン」だけで時期が読めない店は `unknown` になる
- 会社全体の宣伝文を完全には除外できない(`publishable=true` でも新店でない場合がある)
- 年のない月は推定で補うため、実際とずれることがある(推定フラグで明示)
- 検索結果の上位ページ以外にある店は拾えない
