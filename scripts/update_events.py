#!/usr/bin/env python3
"""FANY・バス比較なび・イープラス・ローソンチケットから東海3県のお笑い公演を更新する。

イープラス・ローソンチケットは単純なHTTPリクエストだとbot対策で
ブロックされる(HTTP 503 / タイムアウト・強制切断)ため、Playwrightで
実際のChromiumを起動して取得する。playwrightが使えない環境
(ローカルでのテスト等)では警告を出してその2件をスキップする。

チケットぴあは、ジャンル×都道府県で絞り込める安定した一覧URLが
見つかっていないため引き続き対象外(手動でClaudeに調査を依頼する運用)。
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

EPLUS_PREFS = {"aichi": "愛知", "gifu": "岐阜", "mie": "三重"}
LTIKE_URL = "https://l-tike.com/search/?tig=240&pref=21%2C23%2C24"
LTIKE_PREF_BY_KEYWORD = {"愛知": "愛知", "岐阜": "岐阜", "三重": "三重"}


def _get_browser_html(url: str) -> str | None:
    """Playwrightで実ブラウザを起動してレンダリング済みHTMLを取得する。

    playwrightが利用できない(未インストール)環境ではNoneを返し、
    呼び出し側でそのソースの取得をスキップできるようにする。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[warn] playwright未インストールのためスキップ: {url}")
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
        finally:
            browser.close()
    return html


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


EPLUS_ITEM_RE = re.compile(
    r'<a class="ticket-item ticket-item--kouen" href="([^"]*)">(.*?)</a>\s*(?=<a class="ticket-item|$)',
    re.DOTALL,
)
EPLUS_YYYY_RE = re.compile(r'ticket-item__yyyy">([^<]*)<')
EPLUS_MMDD_RE = re.compile(r'ticket-item__mmdd">([^<]*)<')
EPLUS_TITLE_RE = re.compile(r'ticket-item__title">(.*?)</h3>', re.DOTALL)
EPLUS_VENUE_RE = re.compile(r'ticket-item__venue">\s*<p>(.*?)</p>', re.DOTALL)
EPLUS_STATUS_RE = re.compile(r'ticket-status__item[^"]*">([^<]*)<')


def fetch_eplus(pref_slug: str) -> list[dict]:
    url = f"https://eplus.jp/sf/play/comedy/{pref_slug}"
    html = _get_browser_html(url)
    if html is None:
        return []
    results = []
    for match in EPLUS_ITEM_RE.finditer(html):
        href, body = match.group(1), match.group(2)
        yyyy_match = EPLUS_YYYY_RE.search(body)
        mmdd_match = EPLUS_MMDD_RE.search(body)
        title_match = EPLUS_TITLE_RE.search(body)
        venue_match = EPLUS_VENUE_RE.search(body)
        if not (yyyy_match and mmdd_match and title_match and venue_match):
            continue
        md = re.match(r"(\d{1,2})/(\d{1,2})", mmdd_match.group(1))
        if not md:
            continue
        year = yyyy_match.group(1).strip("/")
        date = f"{year}-{int(md.group(1)):02d}-{int(md.group(2)):02d}"
        status_match = EPLUS_STATUS_RE.search(body)
        status_text = status_match.group(1) if status_match else ""
        if "受付中" in status_text or "発売中" in status_text:
            status = "open"
        elif "受付前" in status_text or "発売前" in status_text:
            status = "soon"
        elif "終了" in status_text or "完売" in status_text:
            status = "closed"
        else:
            status = "unknown"
        results.append({
            "title": _clean_text(title_match.group(1)),
            "date": date,
            "venue": re.sub(r"[（(](?:岐阜|愛知|三重)県[）)]$", "", _clean_text(venue_match.group(1))),
            "status": status,
            "url": urllib.parse.urljoin(url, href),
        })
    return results


def normalize_eplus(raw: dict, pref: str) -> dict | None:
    title = raw["title"].strip()
    if not title or any(word in title for word in EXCLUDED_WORDS):
        return None
    return {
        "date": raw["date"], "title": title, "venue": raw["venue"],
        "city": "", "pref": pref, "status": raw["status"],
        "src": f"eplus_{ {'愛知': 'aichi', '岐阜': 'gifu', '三重': 'mie'}[pref] }",
        "url": raw["url"],
    }


LTIKE_ITEM_RE = re.compile(
    r'<div class="ResultBox boxContents prfSummaryItem[^"]*"[^>]*>(.*?)'
    r'(?=<div class="ResultBox boxContents prfSummaryItem[^"]*"|$)',
    re.DOTALL,
)
LTIKE_TITLE_RE = re.compile(r'ResultBox__title">([^<]*)<')
LTIKE_VENUE_RE = re.compile(r'会場：</dt>\s*<dt class="ResultBox__informationText">([^<]*)<')
LTIKE_LCODE_RE = re.compile(r'data-lcode="(\d+)"')
LTIKE_PRFDATE_RE = re.compile(r'data-prfdate="(\d{8})')  # 複数日公演はカンマ区切りのため先頭日のみ取得
LTIKE_STATUS_RE = re.compile(r'ResultBox__status[^"]*">\s*([^<]*)<')


def fetch_ltike() -> list[dict]:
    html = _get_browser_html(LTIKE_URL)
    if html is None:
        return []
    results = []
    for match in LTIKE_ITEM_RE.finditer(html):
        block = match.group(1)
        title_match = LTIKE_TITLE_RE.search(block)
        venue_match = LTIKE_VENUE_RE.search(block)
        lcode_match = LTIKE_LCODE_RE.search(block)
        prfdate_match = LTIKE_PRFDATE_RE.search(block)
        if not (title_match and venue_match and lcode_match and prfdate_match):
            continue
        d = prfdate_match.group(1)
        date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        venue_raw = venue_match.group(1).strip()
        pref = next((p for kw, p in LTIKE_PREF_BY_KEYWORD.items() if kw in venue_raw), "")
        if not pref:
            continue
        venue = re.sub(r"[（(](?:岐阜|愛知|三重)県[）)]$", "", venue_raw).strip()
        status_match = LTIKE_STATUS_RE.search(block)
        status_text = status_match.group(1).strip() if status_match else ""
        if "受付中" in status_text or "発売中" in status_text:
            status = "open"
        elif "受付前" in status_text or "発売前" in status_text:
            status = "soon"
        elif "終了" in status_text or "完売" in status_text:
            status = "closed"
        else:
            status = "unknown"
        results.append({
            "title": _clean_text(title_match.group(1)),
            "date": date,
            "venue": venue,
            "pref": pref,
            "status": status,
            "url": f"https://l-tike.com/order/?gLcode={lcode_match.group(1)}",
        })
    return results


def normalize_ltike(raw: dict) -> dict | None:
    title = raw["title"].strip()
    if not title or any(word in title for word in EXCLUDED_WORDS):
        return None
    return {
        "date": raw["date"], "title": title, "venue": raw["venue"],
        "city": "", "pref": raw["pref"], "status": raw["status"],
        "src": "ltike", "url": raw["url"],
    }


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
    auto_sources = {
        "fany_ticket", "bushikaku_chubu",
        "eplus_aichi", "eplus_gifu", "eplus_mie", "ltike",
    }
    retained = [
        e for e in existing
        if e.get("src") not in auto_sources and e.get("date", "") >= today
    ]
    retained_by_date: dict[str, list[str]] = {}
    for e in retained:
        retained_by_date.setdefault(e["date"], []).append(_title_key(e["title"]))

    def is_duplicate_of_retained(event: dict) -> bool:
        # チケットぴあ等、既に手動で登録済みの情報と同じ日付・同一公演らしき
        # ものがあれば、タイトル表記の揺れ(記号・スペース・省略)による
        # 重複カード化を避けるため部分一致でスキップする。
        key = _title_key(event["title"])
        for other in retained_by_date.get(event["date"], []):
            if len(key) >= 3 and len(other) >= 3 and (key in other or other in key):
                return True
        return False

    fany_events = []
    for code, pref in PREFS.items():
        for performance in fetch_all(code):
            event = normalize(performance, pref, old_cities)
            if event and event["date"] >= today:
                fany_events.append(event)

    bushikaku_events = []
    for slug, pref in BUSHIKAKU_PREFS.items():
        for raw in fetch_bushikaku(slug):
            event = normalize_bushikaku(raw, pref)
            if not event or event["date"] < today:
                continue
            if is_duplicate_of_retained(event):
                continue
            bushikaku_events.append(event)

    eplus_events = []
    for slug, pref in EPLUS_PREFS.items():
        for raw in fetch_eplus(slug):
            event = normalize_eplus(raw, pref)
            if not event or event["date"] < today:
                continue
            if is_duplicate_of_retained(event):
                continue
            eplus_events.append(event)

    ltike_events = []
    for raw in fetch_ltike():
        event = normalize_ltike(raw)
        if not event or event["date"] < today:
            continue
        if is_duplicate_of_retained(event):
            continue
        ltike_events.append(event)

    unique = {}
    for event in retained + fany_events + bushikaku_events + eplus_events + ltike_events:
        unique[(event["date"], event["title"], event["venue"])] = event
    data["last_updated"] = today
    data["last_updated_note"] = (
        "GitHub ActionsでFANY・バス比較なび・イープラス・ローソンチケットと"
        "既存確認済み情報を更新"
    )
    data["events"] = sorted(
        unique.values(),
        key=lambda e: (e.get("date", ""), e.get("pref", ""), e.get("title", "")),
    )
    EVENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"updated {len(data['events'])} events "
        f"({len(fany_events)} FANY, {len(bushikaku_events)} bushikaku, "
        f"{len(eplus_events)} eplus, {len(ltike_events)} ltike)"
    )


if __name__ == "__main__":
    main()
