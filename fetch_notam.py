#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get_NOTAM : FAA tfr.faa.gov から「SPACE OPERATIONS」TFR（＝打上げ空域）を取得し、
座標付き多角形のJSONに整形して data/space_ops.json に出力する。

公式ソース（キー不要）:
  一覧 : https://tfr.faa.gov/tfrapi/exportTfrList   … JSON配列 {notam_id,type,facility,state,description,creation_date}
  詳細 : https://tfr.faa.gov/download/detail_<id>.xml … XNOTAM形式（頂点=Avx/geoLat/geoLong 十進度）

依存なし（標準ライブラリのみ）。GitHub Actions で定期実行し data/ をコミットする。
"""
import json, sys, re, datetime, urllib.request, urllib.error
import xml.etree.ElementTree as ET
import os

UA = "Mozilla/5.0 (OP's LAB Maps Get_NOTAM; +https://github.com/)"
LIST_URL = "https://tfr.faa.gov/tfrapi/exportTfrList"
# 詳細XMLの候補（/download/ が実績あり・/save_pages/ は予備）
DETAIL_URLS = [
    "https://tfr.faa.gov/download/detail_{id}.xml",
    "https://tfr.faa.gov/save_pages/detail_{id}.xml",
]
OUT_PATH = os.path.join(os.path.dirname(__file__), "data", "space_ops.json")

TIMEOUT = 30


def _get(url, as_json=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    if as_json:
        return json.loads(raw.decode("utf-8", "replace"))
    return raw


def _local(tag):
    """名前空間を無視してローカル名だけ返す（{ns}Foo -> foo）。"""
    return tag.split("}")[-1].lower() if tag else ""


def _find_all_local(elem, name):
    """子孫から局所名一致の要素を全部集める（名前空間非依存）。"""
    name = name.lower()
    out = []
    for e in elem.iter():
        if _local(e.tag) == name:
            out.append(e)
    return out


def _first_text(elem, name):
    for e in _find_all_local(elem, name):
        if e.text and e.text.strip():
            return e.text.strip()
    return None


def parse_coord(s):
    """'25.93333333N' / '097.16666667W' → 符号付き十進度。DMS連結(DDMMSS)も一応対応。"""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    hemi = s[-1].upper()
    body = s[:-1] if hemi in "NSEW" else s
    sign = -1 if hemi in ("S", "W") else 1
    if "." in body or len(body) <= 3:
        # 十進度（tfr.faa.gov のdownload XMLはこれ）
        try:
            return sign * float(body)
        except ValueError:
            return None
    # 予備: DDMMSS / DDDMMSS 連結（小数点なし）
    m = re.match(r"^(\d{2,3})(\d{2})(\d{2}(?:\.\d+)?)$", body)
    if m:
        d, mi, se = float(m.group(1)), float(m.group(2)), float(m.group(3))
        return sign * (d + mi / 60 + se / 3600)
    try:
        return sign * float(body)
    except ValueError:
        return None


def parse_areas(root):
    """XNOTAMルートから TFRAreaGroup ごとに多角形（[[lat,lon],...]）を抽出。"""
    areas = []
    groups = _find_all_local(root, "TFRAreaGroup")
    if not groups:
        groups = [root]  # 念のため
    for g in groups:
        # 優先: abdMergedArea 配下の Avx。無ければグループ内の Avx を総取り。
        merged = _find_all_local(g, "abdMergedArea")
        scope = merged[0] if merged else g
        coords = []
        for avx in _find_all_local(scope, "Avx"):
            lat = parse_coord(_first_text(avx, "geoLat"))
            lon = parse_coord(_first_text(avx, "geoLong"))
            if lat is not None and lon is not None:
                coords.append([round(lat, 6), round(lon, 6)])
        if len(coords) >= 3:
            areas.append(coords)
    return areas


def parse_detail(xml_bytes, notam_id):
    root = ET.fromstring(xml_bytes)
    areas = parse_areas(root)
    rec = {
        "notam_id": notam_id,
        "code_type": _first_text(root, "codeType"),
        "effective_utc": _first_text(root, "dateEffective"),
        "expires_utc": _first_text(root, "dateExpire"),
        "alt_lower": _first_text(root, "valDistVerLower"),
        "alt_upper": _first_text(root, "valDistVerUpper"),
        "areas": areas,
    }
    return rec


def fetch_detail(notam_id):
    idu = notam_id.replace("/", "_")
    last_err = None
    for tpl in DETAIL_URLS:
        url = tpl.format(id=idu)
        try:
            return _get(url), url
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue
            raise
    raise last_err or RuntimeError("detail fetch failed: " + notam_id)


def main():
    lst = _get(LIST_URL, as_json=True)
    space = [x for x in lst if str(x.get("type", "")).upper() == "SPACE OPERATIONS"]
    print(f"一覧 {len(lst)}件 / SPACE OPERATIONS {len(space)}件", file=sys.stderr)

    tfrs = []
    for x in space:
        nid = x.get("notam_id")
        if not nid:
            continue
        try:
            xml_bytes, src = fetch_detail(nid)
            rec = parse_detail(xml_bytes, nid)
        except Exception as e:  # 1件の失敗で全体を止めない
            print(f"  WARN {nid}: {e}", file=sys.stderr)
            rec = {"notam_id": nid, "areas": [], "error": str(e)}
            src = None
        rec.update({
            "type": "SPACE OPERATIONS",
            "facility": x.get("facility"),
            "state": x.get("state"),
            "description": x.get("description"),
            "creation_date": x.get("creation_date"),
            "detail_page": f"https://tfr.faa.gov/tfr3/?page=detail_{nid.replace('/', '_')}",
        })
        tfrs.append(rec)
        n = sum(len(a) for a in rec.get("areas", []))
        print(f"  {nid}: {len(rec.get('areas', []))}区域 / 頂点{n} / {rec.get('code_type')}", file=sys.stderr)

    out = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "https://tfr.faa.gov (SPACE OPERATIONS)",
        "count": len(tfrs),
        "tfrs": tfrs,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"→ {OUT_PATH} 書き出し（{len(tfrs)}件）", file=sys.stderr)


if __name__ == "__main__":
    main()
