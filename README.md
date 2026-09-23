# Get_NOTAM

FAA **tfr.faa.gov** の「**SPACE OPERATIONS**」TFR（＝打上げ・宇宙運用の一時飛行制限空域）を
定期取得し、座標付き多角形の JSON にして公開するデータリポジトリ。
OP's LAB Maps 本体が `raw.githubusercontent` 経由で読む（Get_NAVWARN と同じ運用）。

## 公式ソース（キー不要）

| 用途 | URL |
|---|---|
| 一覧(JSON) | `https://tfr.faa.gov/tfrapi/exportTfrList` |
| 詳細(XML)  | `https://tfr.faa.gov/download/detail_<id>.xml`（`<id>` は NOTAM番号の `/`→`_`） |

一覧の各要素は `{notam_id, type, facility, state, description, creation_date}`。
`type == "SPACE OPERATIONS"` を抽出して詳細XMLを取得する。
座標は XNOTAM形式 `.../TfrNot/TFRAreaGroup/abdMergedArea/Avx/geoLat|geoLong`（十進度＋N/S/E/W）。

## 出力

`data/space_ops.json`

```json
{
  "generated_utc": "...Z",
  "source": "https://tfr.faa.gov (SPACE OPERATIONS)",
  "count": N,
  "tfrs": [
    {
      "notam_id": "6/7491",
      "type": "SPACE OPERATIONS",
      "code_type": "91.143",
      "facility": "ZHU", "state": "TX",
      "description": "...", "creation_date": "07/24/2026",
      "effective_utc": "2026-07-25T22:30:00",
      "expires_utc":  "2026-07-26T00:54:00",
      "alt_lower": "0 FT", "alt_upper": "999 FL",
      "detail_page": "https://tfr.faa.gov/tfr3/?page=detail_6_7491",
      "areas": [ [ [lat,lon], ... ] ]     // 多角形の配列（[lat,lon]十進度・西経は負）
    }
  ]
}
```

## 実行

```bash
python fetch_notam.py      # 依存なし（標準ライブラリのみ）・data/space_ops.json を更新
```

GitHub Actions（`.github/workflows/fetch.yml`）が毎時実行してコミットする。
打上げ前後に頻度を上げたいときは cron の分フィールドを増やす。

## 対象範囲

- **米国のみ**（tfr.faa.gov は FAA）。国際打上げ・海外FIRの再突入NOTAMは含まない。
- 国際対応は下の `fetch_notices.py`（space-notices.com）で補う。

---

# もうひとつの取得系統：space-notices.com（各国のNOTAM・航行警報）

`fetch_notices.py` → `data/notices.json`

FAA の TFR が米国だけなのに対し、こちらは**中国・ニュージーランド・日本を含む各国の NOTAM**を持つ。
打上げとの紐づけ（どの便の空域か）と、電文全文・多角形・有効期間が揃った形で取れる。

## なぜここから取るか

各国の一次ソースは、いずれも自動取得の道が塞がっている。

| 経路 | 状態 |
|---|---|
| FAA NOTAM Search (`notams.aim.faa.gov`) | 家庭回線・Actions・実ブラウザすべて **403**（Akamai のデータセンター遮断） |
| FAA 公式API (`external-api.faa.gov`) | **401**＝到達はするが認証キーが要る（申請が必要・未取得） |
| Airways NZ の IFIS | **規約で自動取得を明確に禁止** |
| Flight Advisor NZ | データは素で返るが**規約が IFIS と同文で禁止** |

space-notices.com は `robots.txt` が全許可で、各通知に一次ソース（FAA／NGA）へのリンクを明示している。

## 取得の作法（節度）

- **差分取得**。`sitemap.xml` の `lastmod` と `data/_notices_cache.json` を突き合わせ、
  新規・更新のものだけ取りに行く。普段の実行は数件で済む
- ★**sitemap だけでは取りこぼす**。新しい通知が sitemap に載るまで遅れがあり、
  実測で 14 件が漏れていた（`A0540/26`＝STARSHIP FLT 14 LAUNCH MALFUNCTION など）。
  そこで **打上げのページ（`/entry/`・49件）からも通知を辿る**。こちらも `lastmod` で
  差分を取り、1回に見る数は `NOTICES_MAX_ENTRY`（既定12件）まで。
  どこで見つけたかは `via`（sitemap / entry）に残す
- 1件ごとに間隔を空ける（`NOTICES_SLEEP`・既定 0.4 秒／Actions では 0.6 秒）
- 1回の実行の上限は `NOTICES_MAX_FETCH`（既定 200 件）。未取得が残れば次の実行で埋まる
- User-Agent にアプリ名と連絡先を名乗る
- **Actions は毎時43分**（`fetch_notices.yml`）。他の Get_* と同じ毎時で、分だけずらしている

## 出力

```json
{
  "generated_utc": "...Z",
  "source": "space-notices.com",
  "total_known": 1188,
  "pending": 0,
  "count": N,
  "notices": [
    {
      "id": "notam-NZZC-B4624/26",
      "name": "B4624/26",
      "kind": "NOTAM",                 // NOTAM / NAVWARNING / LNM / BNM / INFOPAGE
      "reason": "ROCKET LAUNCH",
      "cancelled": false,              // 取消＝打上げスリップの一次証拠
      "height": "Unlimited",
      "raw": "B4624/26 NOTAMN\r\nQ) ...",   // 電文全文
      "areas": [ [ [lat,lon], ... ] ], // [lat,lon]十進度・西経/南緯は負（TFR側と同じ向き）
      "markers": [ [lat,lon], ... ],
      "dates": [ {"start":"...Z","end":"...Z"} ],   // くり返す時間帯は展開済み
      "launches": [ {"id":"launch-...","title":"Owl By The Dozen (StriX Launch 12)"} ],
      "source": {"name":"US Federal Aviation Administration","link":"https://notams.aim.faa.gov/..."},
      "page": "https://space-notices.com/notice/...",
      "updated": "...Z"                 // サイトがこの通知を最後に更新した時刻（最新の電文を選ぶのに使う）
    }
  ]
}
```

- **期限切れは落とす**。最後の `end` が `NOTICES_KEEP_DAYS`（既定30日）より古いものは出力に入れない。
  中身は `data/_notices_cache.json` に残るので再取得はしない
- `_notices_cache.json` は**取得済み全件の保管庫**。一度取ったものは消さない。
  サイト側の sitemap から消えたものは `"gone":"YYYY-MM-DD"` の印を付けて残す
  （向こうの都合でこちらの記録が欠けないように）。過去の電文はここから読める

## 表示するときの約束

アプリ側に **出典を必ず出す**。通知そのものの出所（`source.name`＝FAA／NGA）と、
経由した `space-notices.com` の両方。`page` と `source.link` をリンクにする。

## 実行

```bash
python fetch_notices.py                      # 差分のみ（上限200件）
NOTICES_MAX_FETCH=1200 python fetch_notices.py   # 初回の全件取得
```
