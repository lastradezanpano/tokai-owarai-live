#!/usr/bin/env python3
"""FANYの公開検索APIとバス比較なびから東海3県のお笑い公演を更新する。

イープラス・チケットぴあは、単純なHTTPリクエストではbot対策(503)や
安定した一覧URLの不在によりCIから自動取得できないため対象外。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "data" / "events.json"
FANY_API = "https://ticket.fany.lol/search/event_more"
PREFS = {"21": "岐阜", "23": "愛知", "24": "三重"}
EXCLUDED_WORDS = ("ファンイベント", "ゴルフ", "ライオンズカップ")

BUSHIKAKU_BASE = "https://www.bushikaku.net/expedition/play/shows/403/region-chubu"
BUSHIKAKU_PREFS = {"aichi": "愛知", "gifu": "岐阜", "mie": "三重"}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}


def fetch_page(pref_code: str, offset: int) -> list[dict]:
    query = urllib.parse.urlencode({
        "keywords": "", "from": "", "to": "", "prefectures": pref_code,
        "genre": "30", "search_type": "form", "offset": offset,
    })
    request = urllib.request.Request(
        f"{FANY_API}?{query}",
        headers={"User-Agent": "Tokai-Owarai-Live-Tracker/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return payload.get("performances", [])


def fetch_all(pref_code: str) -> list[dict]:
    performances = []
    for offset in range(0, 1000, 10):
        page = fetch_page(pref_code, offset)
        if not page:
            break
        performances.extend(page)
        if len(page) < 10:
            break
    return performances


def sales_status(performance: dict) -> tuple[str, str]:
    sales = performance.get("performance_sales") or []
    general = [s for s in sales if s.get("limited_order_class") == "00"]
    candidates = general or sales
    order = {"open": 0, "soon": 1, "closed": 2, "unknown": 3}
    ranked = []
    for sale in candidates:
        label = str(sale.get("display_sales_status", ""))
        if "発売中" in label or "受付中" in label:
            status = "open"
        elif "発売前" in label or "受付前" in label:
            status = "soon"
        elif "終了" in label or "完売" in label:
            status = "closed"
        else:
            status = "unknown"
        ranked.append((order[status], status, str(sale.get("destination_url", ""))))
    if not ranked:
        return "unknown", ""
    _, status, url = min(ranked, key=lambda item: item[0])
    return status, url


def normalize(performance: dict, pref: str, old_cities: dict) -> dict | None:
    title = str(performance.get("name", "")).strip()
    if not title or any(word in title for word in EXCLUDED_WORDS):
        return None
    date_match = re.match(r"(\d{4})/(\d{2})/(\d{2})", str(performance.get("performance_date", "")))
    if not date_match:
        return None
    date = "-".join(date_match.groups())
    venue = re.sub(r"[（(](?:岐阜|愛知|三重)県[）)]$", "", str(performance.get("venue_name", ""))).strip()
    status, url = sales_status(performance)
    event = {
        "date": date, "title": title, "venue": venue,
        "city": old_cities.get((date, title), ""), "pref": pref,
        "status": status, "src": "fany_ticket",
    }
    if url:
        event["url"] = url
    return event


TAG_RE = re.compile(r"<[^>]+>")
TITLE_LINK_RE = re.compile(
    r'expedition-concert__table__body__data__title__link[^"]*"\s+href="([^"]*)">([^<]*)</a>'
)
DATE_LINK_RE = re.compile(
    r'expedition-concert__table__body__data__date-link[^"]*"\s+href="[^"]*">(.*?)</a>',
    re.DOTALL,
)
VENUE_LINK_RE = re.compile(
    r'expedition-concert__table__body__data__venue-link[^"]*"\s+href="([^"]*)">([^<]*)</a>'
)


def _clean_text(fragment: str) -> str:
    return TAG_RE.sub(" ", fragment).strip()


def parse_bushikaku_dates(date_fragment: str) -> tuple[str, str] | None:
    text = date_fragment.replace("<br/>", " ").replace("<br>", " ")
    matches = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not matches:
        return None
    def to_iso(parts: tuple[str, str, str]) -> str:
        y, m, d = parts
        return f"{y}-{int(m):02d}-{int(d):02d}"
    start = to_iso(matches[0])
    end = to_iso(matches[-1]) if len(matches) > 1 else ""
    return start, end


def fetch_bushikaku(slug: str) -> list[dict]:
    url = f"{BUSHIKAKU_BASE}/{slug}/"
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    results = []
    for block in html.split('<tbody class="expedition-concert__table__body')[1:]:
        title_match = TITLE_LINK_RE.search(block)
        date_match = DATE_LINK_RE.search(block)
        venue_match = VENUE_LINK_RE.search(block)
        if not (title_match and date_match and venue_match):
            continue
        dates = parse_bushikaku_dates(date_match.group(1))
        if not dates:
            continue
        start, end = dates
        detail_href = venue_match.group(1)
        detail_url = urllib.parse.urljoin(url, detail_href) if detail_href else url
        results.append({
            "title": _clean_text(title_match.group(2)),
            "date": start,
            "date_end": end,
            "venue": _clean_text(venue_match.group(2)),
            "url": detail_url,
        })
    return results


_TITLE_NOISE_RE = re.compile(r"[\s!！「」『』～〜\-ー]")


def _title_key(title: str) -> str:
    return _TITLE_NOISE_RE.sub("", title).lower()


def normalize_bushikaku(raw: dict, pref: str) -> dict | None:
    title = raw["title"].strip()
    if not title or any(word in title for word in EXCLUDED_WORDS):
        return None
    event = {
        "date": raw["date"], "title": title, "venue": raw["venue"],
        "city": "", "pref": pref, "status": "unknown", "src": "bushikaku_chubu",
        "url": raw["url"],
    }
    if raw.get("date_end"):
        event["date_end"] = raw["date_end"]
    return event


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date().isoformat()
    data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    existing = data.get("events", [])
    old_cities = {
        (e.get("date", ""), e.get("title", "")): e.get("city", "")
        for e in existing if e.get("src") == "fany_ticket"
    }
    auto_sources = {"fany_ticket", "bushikaku_chubu"}
    retained = [
        e for e in existing
        if e.get("src") not in auto_sources and e.get("date", "") >= today
    ]
    fany_events = []
    for code, pref in PREFS.items():
        for performance in fetch_all(code):
            event = normalize(performance, pref, old_cities)
            if event and event["date"] >= today:
                fany_events.append(event)
    retained_by_date: dict[str, list[str]] = {}
    for e in retained:
        retained_by_date.setdefault(e["date"], []).append(_title_key(e["title"]))

    def is_duplicate_of_retained(event: dict) -> bool:
        # イープラス等、既により良い情報源で同じ日付・同一公演らしきものが
        # 登録済みなら、タイトル表記の揺れ(記号・スペース・省略)による
        # 重複カード化を避けるため部分一致でスキップする。
        key = _title_key(event["title"])
        for other in retained_by_date.get(event["date"], []):
            if len(key) >= 3 and len(other) >= 3 and (key in other or other in key):
                return True
        return False

    bushikaku_events = []
    for slug, pref in BUSHIKAKU_PREFS.items():
        for raw in fetch_bushikaku(slug):
            event = normalize_bushikaku(raw, pref)
            if not event or event["date"] < today:
                continue
            if is_duplicate_of_retained(event):
                continue
            bushikaku_events.append(event)
    unique = {}
    for event in retained + fany_events + bushikaku_events:
        unique[(event["date"], event["title"], event["venue"])] = event
    data["last_updated"] = today
    data["last_updated_note"] = "GitHub ActionsでFANY公開検索・バス比較なびと既存確認済み情報を更新"
    data["events"] = sorted(
        unique.values(),
        key=lambda e: (e.get("date", ""), e.get("pref", ""), e.get("title", "")),
    )
    EVENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"updated {len(data['events'])} events "
        f"({len(fany_events)} from FANY, {len(bushikaku_events)} from bushikaku)"
    )


if __name__ == "__main__":
    main()
