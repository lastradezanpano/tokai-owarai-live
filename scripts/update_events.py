#!/usr/bin/env python3
"""FANYの公開検索APIから東海3県のお笑い公演を更新する。"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "data" / "events.json"
FANY_API = "https://ticket.fany.lol/search/event_more"
PREFS = {"21": "岐阜", "23": "愛知", "24": "三重"}
EXCLUDED_WORDS = ("ファンイベント", "ゴルフ", "ライオンズカップ")


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


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date().isoformat()
    data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    existing = data.get("events", [])
    old_cities = {
        (e.get("date", ""), e.get("title", "")): e.get("city", "")
        for e in existing if e.get("src") == "fany_ticket"
    }
    retained = [
        e for e in existing
        if e.get("src") != "fany_ticket" and e.get("date", "") >= today
    ]
    fany_events = []
    for code, pref in PREFS.items():
        for performance in fetch_all(code):
            event = normalize(performance, pref, old_cities)
            if event and event["date"] >= today:
                fany_events.append(event)
    unique = {}
    for event in retained + fany_events:
        unique[(event["date"], event["title"], event["venue"])] = event
    data["last_updated"] = today
    data["last_updated_note"] = "GitHub ActionsでFANY公開検索と既存確認済み情報を更新"
    data["events"] = sorted(
        unique.values(),
        key=lambda e: (e.get("date", ""), e.get("pref", ""), e.get("title", "")),
    )
    EVENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"updated {len(data['events'])} events ({len(fany_events)} from FANY)")


if __name__ == "__main__":
    main()
