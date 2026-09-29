#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
過去の航行警報を space-notices.com から取り込む（1回だけ流す想定・手動実行）。

★space-notices は打上げの後に便のページを消すが、通知のページは残している。
  sitemap にも載らないので、警報の番号を知っていないと辿れない。
  番号の出どころは2つ：
    1. NGA の broadcast-warn（取り消し済みを含む全件）… 1999年〜2024年5月まで残っている
    2. Get_NAVWARN の履歴（DailyMem の毎回の取得）… 2026年6月から
  （2024年5月〜2026年5月は NGA からも取れない＝sitemap で拾えた分だけ）

取り込んだ通知は _notices_cache.json に via="backfill" で入れる（sitemap に無いので
「サイトから消えた」の判定から外す）。試した番号は _backfill_tried.json に残し、引き直さない。

使い方: python backfill_notices.py <Get_NAVWARN のクローン（全履歴）>
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import fetch_notices as F

NGA = "https://msi.nga.mil/api/publications/broadcast-warn?navArea=%s&status=all&output=json"
NGA_AREAS = {"P": "HYDROPAC", "A": "HYDROLANT", "C": "HYDROARC",
             "4": "NAVAREA IV", "12": "NAVAREA XII"}
FROM_YEAR = 2022            # space-notices の記録は 2022年11月から
TRIED_PATH = os.path.join(F.HERE, "data", "_backfill_tried.json")
SLEEP = float(os.environ.get("BACKFILL_SLEEP", "1.0"))
MAX = int(os.environ.get("BACKFILL_MAX", "3000"))

# 打上げ・落下域・再突入らしい電文だけを問い合わせる（space-notices は打上げ関係の電文しか持たない）
KEY_RE = re.compile(r"ROCKET|SPACE|MISSILE|LAUNCH|DEBRIS|RE-?ENTRY|SPLASH", re.I)
HDR_RE = re.compile(r"(NAVAREA\s+(?:IV|XII)|HYDROLANT|HYDROPAC|HYDROARC)\s+(\d+)/(\d+)", re.I)


def names_from_nga():
    out = set()
    for code, head in NGA_AREAS.items():
        req = urllib.request.Request(NGA % code, headers={"User-Agent": F.UA})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                rows = json.loads(r.read().decode("utf-8", "replace")).get("broadcast-warn") or []
        except Exception as e:
            print("  ! NGA %s : %s" % (code, e), file=sys.stderr)
            continue
        n = 0
        for w in rows:
            y, num = w.get("msgYear"), w.get("msgNumber")
            if not y or not num or int(y) < FROM_YEAR:
                continue
            if not KEY_RE.search(w.get("text") or ""):
                continue
            out.add("%s %d/%02d" % (head, int(num), int(y) % 100))
            n += 1
        print("NGA %s: %d 件（全 %d 件）" % (head, n, len(rows)))
        time.sleep(2)
    return out


def names_from_navwarn(repo):
    """Get_NAVWARN の DailyMem の全版から、打上げらしい電文の番号を集める"""
    out = set()
    files = ["DailyMemPAC.txt", "DailyMemXII.txt", "DailyMemLAN.txt", "DailyMemIV.txt", "DailyMemARC.txt"]
    shas = subprocess.run(["git", "-C", repo, "log", "--format=%H", "--", "data"],
                          capture_output=True, text=True).stdout.split()
    for sha in shas:
        for f in files:
            r = subprocess.run(["git", "-C", repo, "show", "%s:data/%s" % (sha, f)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                continue
            t = r.stdout
            ms = list(HDR_RE.finditer(t))
            for i, m in enumerate(ms):
                block = t[m.start():ms[i + 1].start() if i + 1 < len(ms) else len(t)]
                if KEY_RE.search(block):
                    head = re.sub(r"\s+", " ", m.group(1).upper())
                    out.add("%s %d/%s" % (head, int(m.group(2)), m.group(3)))
    print("Get_NAVWARN の履歴: %d 版から %d 件" % (len(shas), len(out)))
    return out


def notice_url(name):
    return F.BASE + "/notice/nav-warning-" + urllib.parse.quote(name, safe="")


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else None
    names = names_from_nga()
    if repo:
        names |= names_from_navwarn(repo)
    cache = F.load_cache()
    have = {(v.get("notice") or {}).get("name") for v in cache.values()}
    tried = set(json.load(open(TRIED_PATH, encoding="utf-8"))) if os.path.exists(TRIED_PATH) else set()
    todo = sorted(n for n in names if n not in have and n not in tried)
    print("候補 %d 件 ／ 控えにある %d ／ 試し済み %d → 取りに行く %d 件（上限 %d）"
          % (len(names), len(names & have), len(names & tried), len(todo), MAX))

    got = miss = fail = 0
    for name in todo[:MAX]:
        url = notice_url(name)
        try:
            notice = F.extract_notice(F._get(url))
            if notice:
                cache[url] = {"lastmod": "", "notice": F._norm(notice, url, ""), "via": "backfill"}
                got += 1
            else:
                miss += 1            # 打上げ関係でない電文はページが無い
            tried.add(name)
        except Exception as e:
            fail += 1                # 通信の失敗は試し済みにしない（次に引き直す）
            print("  ! %s : %s" % (name, e), file=sys.stderr)
        time.sleep(SLEEP)

    F._write_if_changed(F.CACHE_PATH, json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=0))
    F._write_if_changed(TRIED_PATH, json.dumps(sorted(tried), ensure_ascii=False, indent=0))
    print("取り込み %d 件 ／ ページ無し %d 件 ／ 失敗 %d 件 ／ 残り %d 件"
          % (got, miss, fail, max(0, len(todo) - MAX)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
