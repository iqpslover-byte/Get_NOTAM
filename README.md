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
- 国際対応は将来、地域別ソース（autorouter=欧州EAD / DINS など）を別途追加する想定。
