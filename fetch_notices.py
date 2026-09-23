#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get_NOTAM : space-notices.com から打上げ関連の通知（NOTAM／航行警報／LNM 等）を取得し、
座標付き多角形の JSON に整形して data/notices.json に出力する。

FAA tfr.faa.gov（fetch_notam.py）が米国の TFR しか持たないのに対し、こちらは
中国・ニュージーランド・日本など各国の NOTAM を含む。打上げとの紐づけも付いている。

取得の作法:
  - sitemap.xml の lastmod と突き合わせ、新規・更新のものだけ取りに行く（差分取得）
  - 1件ごとに間隔を空ける（SLEEP）。1回の実行で取る件数にも上限を置く（MAX_FETCH）
  - 取得済みの中身は data/_notices_cache.json に貯め、再取得しない

依存なし（標準ライブラリのみ）。
"""
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://space-notices.com"
SITEMAP_URL = BASE + "/sitemap.xml"
UA = "OP's LAB Maps / Get_NOTAM (satellite tracking hobby app; contact: iqps.lover@gmail.com)"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "data", "notices.json")
CACHE_PATH = os.path.join(HERE, "data", "_notices_cache.json")
ENTRY_CACHE_PATH = os.path.join(HERE, "data", "_entries_cache.json")
DIGEST_PATH = os.path.join(HERE, "_new_notices.md")   # 新しい通知のお知らせ本文（コミットしない）

TIMEOUT = 30
SLEEP = float(os.environ.get("NOTICES_SLEEP", "0.4"))       # 1件ごとの間隔（秒）
MAX_FETCH = int(os.environ.get("NOTICES_MAX_FETCH", "200"))  # 1回の実行で取る上限
MAX_ENTRY_FETCH = int(os.environ.get("NOTICES_MAX_ENTRY", "12"))  # 打上げのページを見る上限
KEEP_DAYS = int(os.environ.get("NOTICES_KEEP_DAYS", "30"))   # 終了後この日数は出力に残す

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
LOC_RE = re.compile(r"<loc>(.*?)</loc>\s*(?:<image:image>.*?</image:image>\s*)*"
                    r"(?:<lastmod>(.*?)</lastmod>)?", re.S)
NOTICE_IN_PAGE_RE = re.compile(r"/notice/[A-Za-z0-9%\-]+")


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- 取り出し

def _rsc_text(html):
    """HTML に埋め込まれた RSC ペイロードを 1 本につなぎ、エスケープを戻す。"""
    parts = PUSH_RE.findall(html)
    if not parts:
        return ""
    raw = "".join(parts)
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return raw


def _scan_object(s, start):
    """s[start] の '{' に対応する '}' までを返す（文字列の中の括弧は数えない）。"""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_notice(html):
    d = _rsc_text(html)
    i = d.find('{"notice":{')
    if i < 0:
        return None
    body = _scan_object(d, i)
    if not body:
        return None
    try:
        return json.loads(body).get("notice")
    except Exception:
        return None


# ---------------------------------------------------------------- 整形

def _norm(notice, page_url, lastmod=""):
    """サイトの notice を、アプリが読む形へ。座標は [lat, lon]（西経・南緯は負）。"""
    areas = []
    for ring in (notice.get("areas") or []):
        pts = []
        for p in ring:
            # サイト側は [経度, 緯度] の順（GeoJSON と同じ）
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append([round(float(p[1]), 6), round(float(p[0]), 6)])
        if len(pts) >= 3:
            areas.append(pts)

    markers = []
    for m in (notice.get("markers") or []):
        if isinstance(m, (list, tuple)) and len(m) >= 2:
            markers.append([round(float(m[1]), 6), round(float(m[0]), 6)])

    dates = []
    for d in (notice.get("dates") or []):
        if d.get("start") or d.get("end"):
            dates.append({"start": d.get("start"), "end": d.get("end")})

    src = notice.get("source") or {}
    return {
        "id": notice.get("id"),
        "name": notice.get("name"),
        "kind": notice.get("type"),          # NOTAM / NAVWARNING / LNM / BNM / INFOPAGE
        "reason": notice.get("reason"),
        "cancelled": bool(notice.get("cancelled")),
        "height": notice.get("height"),
        "raw": notice.get("rawText"),
        "areas": areas,
        "markers": markers,
        "dates": dates,
        "launches": [{"id": e.get("id"), "title": e.get("title")}
                     for e in (notice.get("entries") or []) if e.get("id")],
        "source": {"name": src.get("name"), "link": src.get("link")},
        "page": page_url,
        # サイトがこの通知を最後に更新した時刻。同じ打上げに電文が何通もあるとき、
        # どれが最新か（＝今いちばん確からしい窓か）をアプリが選ぶために要る
        "updated": lastmod,
    }


def _last_end(rec):
    ends = [d.get("end") for d in (rec.get("dates") or []) if d.get("end")]
    return max(ends) if ends else None


def _jst(iso):
    """ISO(UTC) → 「9/28 21:15」。日本時間。"""
    if not iso:
        return ""
    try:
        t = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S") + datetime.timedelta(hours=9)
        return "%d/%d %02d:%02d" % (t.month, t.day, t.hour, t.minute)
    except Exception:
        return ""


def write_notice_digest(fresh, path):
    """新しく見つけた通知のお知らせ本文（Markdown）を書く。無ければ何も書かない。"""
    live = []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for n in fresh:
        ends = [d.get("end") for d in (n.get("dates") or []) if d.get("end")]
        if ends and max(ends) < now:
            continue                       # もう終わっているものは知らせない
        live.append(n)
    if not live:
        return 0

    names = []
    for n in live:
        for l in (n.get("launches") or []):
            if l.get("title") and l["title"] not in names:
                names.append(l["title"])
    head = "新しい通知 %d件" % len(live)
    if names:
        head += "（%s%s）" % (names[0], " ほか" if len(names) > 1 else "")

    lines = [head, ""]
    for n in sorted(live, key=lambda x: ((x.get("dates") or [{}])[0].get("start") or "")):
        ds = [d for d in (n.get("dates") or []) if d.get("start") and d.get("end")]
        kind = "NOTAM" if n.get("kind") in ("NOTAM", "TFR") else "航行警報"
        lines.append("### %s %s" % (kind, n.get("name") or ""))
        if n.get("reason"):
            lines.append(n["reason"])
        for l in (n.get("launches") or []):
            if l.get("title"):
                lines.append("🚀 %s" % l["title"])
        if ds:
            spare = len(ds) - 1
            a, b = _jst(ds[0]["start"]), _jst(ds[0]["end"])
            if a[:a.find(" ")] == b[:b.find(" ")]:
                b = b[b.find(" ") + 1:]        # 同じ日なら終わりの日付は省く
            lines.append("%s〜%s JST%s" % (a, b,
                                          ("　予備 %d日" % spare) if spare > 0 else "　この日だけ"))
        lines.append("区域 %d件" % len(n.get("areas") or []))
        if n.get("page"):
            lines.append(n["page"])
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(live)


def _write_if_changed(path, text):
    """中身が同じなら書かない。生成時刻だけ動いてコミットが積まれるのを防ぐ。"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# ---------------------------------------------------------------- 本体

def read_sitemap():
    """sitemap から [(通知URL, lastmod), ...] と [(打上げURL, lastmod), ...] を返す。"""
    xml = _get(SITEMAP_URL)
    notices, entries = [], []
    for loc, lastmod in LOC_RE.findall(xml):
        loc = loc.strip()
        lm = (lastmod or "").strip()
        if "/notice/" in loc:
            notices.append((loc, lm))
        elif "/entry/" in loc:
            entries.append((loc, lm))
    return notices, entries


def notices_in_entry(html):
    """打上げのページに出てくる通知のURL。
    ★sitemap には載っていない通知がある（実測：Starship Flight 14 のページだけで8件。
    A0540/26 や HYDROPAC 2749/26 など新しいものが sitemap に反映されていない）。
    打上げのページからも辿らないと取りこぼす。"""
    out = []
    for m in NOTICE_IN_PAGE_RE.findall(html):
        u = BASE + m
        if u not in out:
            out.append(u)
    return out


def load_entry_cache():
    if os.path.exists(ENTRY_CACHE_PATH):
        with open(ENTRY_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    entries, entry_pages = read_sitemap()
    print("sitemap の通知: %d 件 ／ 打上げのページ: %d 件" % (len(entries), len(entry_pages)))
    if not entries:
        print("通知が 0 件。出力は変更しない。", file=sys.stderr)
        return 1

    cache = load_cache()
    # updated を持たない古いキャッシュは、保存してある lastmod から補う（取り直さない）
    for v in cache.values():
        n = v.get("notice") or {}
        if n and not n.get("updated"):
            n["updated"] = v.get("lastmod", "")

    # 更新された打上げのページを見て、sitemap に載っていない通知を拾う
    ecache = load_entry_cache()
    etodo = [(u, lm) for u, lm in entry_pages if ecache.get(u) != lm]
    extra = {}
    epicked = 0
    for url, lastmod in etodo[:MAX_ENTRY_FETCH]:
        try:
            for nu in notices_in_entry(_get(url)):
                # 打上げのページ側の更新時刻を、その通知の版として使う
                if extra.get(nu, "") < lastmod:
                    extra[nu] = lastmod
            ecache[url] = lastmod
            epicked += 1
        except Exception as e:
            print("  ! %s : %s" % (url, e), file=sys.stderr)
        time.sleep(SLEEP)
    known = {u for u, _ in entries}
    only_entry = [u for u in extra if u not in known]
    if etodo:
        print("打上げのページ: %d 件を見た（更新 %d 件）／ sitemap に無い通知 %d 件"
              % (epicked, len(etodo), len(only_entry)))

    todo = [(u, lm) for u, lm in entries
            if u not in cache or cache[u].get("lastmod") != lm]
    for u, lm in extra.items():
        if u in known:
            continue                      # sitemap 側で見る
        if u not in cache or cache[u].get("lastmod") != lm:
            todo.append((u, lm))
    print("取りに行く: %d 件（上限 %d）" % (len(todo), MAX_FETCH))

    fetched = failed = 0
    fresh = []          # 今回はじめて見つけた通知（お知らせに使う。改訂は入れない）
    for url, lastmod in todo[:MAX_FETCH]:
        is_new = url not in cache
        try:
            html = _get(url)
            notice = extract_notice(html)
            if not notice:
                raise ValueError("notice を取り出せない")
            if is_new:
                fresh.append(_norm(notice, url, lastmod))
            cache[url] = {"lastmod": lastmod, "notice": _norm(notice, url, lastmod),
                          # どこで見つけたか。打上げのページ由来は sitemap に載らないので
                          # 「サイトから消えた」の判定から外す
                          "via": "sitemap" if url in known else "entry"}
            fetched += 1
        except Exception as e:
            failed += 1
            print("  ! %s : %s" % (url, e), file=sys.stderr)
        time.sleep(SLEEP)

    # 一度取ったものは消さない。サイトから消えたら、消えた日の印だけ付ける。
    # ★打上げのページで見つけたものは sitemap に載っていないだけなので「消えた」ではない
    alive = {u for u, _ in entries} | set(extra.keys())
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    gone = 0
    for u, v in cache.items():
        if u in alive or v.get("via") == "entry":
            v.pop("gone", None)
        else:
            gone += 1
            v.setdefault("gone", today)
    if gone:
        print("sitemap から消えたが保持している: %d 件" % gone)

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    # 1通知1行。中身が変わった行だけが git の差分になる（1行JSONだと毎回全体が差分になる）
    _write_if_changed(CACHE_PATH,
                      json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=0))
    _write_if_changed(ENTRY_CACHE_PATH,
                      json.dumps(ecache, ensure_ascii=False, sort_keys=True, indent=0))

    # 出力＝終わっていないもの＋終わって間もないもの
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    notices = []
    for u, v in cache.items():
        rec = v.get("notice") or {}
        end = _last_end(rec)
        if end is None or end >= cutoff:
            notices.append(rec)
    notices.sort(key=lambda r: (_last_end(r) or "9999", r.get("id") or ""))

    out = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "space-notices.com",
        "source_url": BASE,
        "total_known": len(cache),
        "pending": max(0, len(todo) - fetched),
        "count": len(notices),
        "notices": notices,
    }
    # 生成時刻以外が前回と同じなら、時刻も据え置いて書かない（無意味なコミットを積まない）
    body = json.dumps({k: v for k, v in out.items() if k != "generated_utc"},
                      ensure_ascii=False, sort_keys=True, indent=0)
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
            same = json.dumps({k: v for k, v in prev.items() if k != "generated_utc"},
                              ensure_ascii=False, sort_keys=True, indent=0)
            if same == body:
                out["generated_utc"] = prev.get("generated_utc", out["generated_utc"])
        except Exception:
            pass
    changed = _write_if_changed(
        OUT_PATH, json.dumps(out, ensure_ascii=False, sort_keys=True, indent=0))

    told = write_notice_digest(fresh, DIGEST_PATH)

    print("取得 %d 件 / 失敗 %d 件 / 保有 %d 件 → 出力 %d 件（未取得 %d 件）%s"
          % (fetched, failed, len(cache), len(notices), out["pending"],
             "" if changed else " ※変更なし"))
    if told:
        print("お知らせに出す新しい通知: %d 件 → %s" % (told, os.path.basename(DIGEST_PATH)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
