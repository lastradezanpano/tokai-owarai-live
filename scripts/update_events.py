#!/usr/bin/env python3
"""FANY・バス比較なび・イープラス・名古屋よしもと・よしもとライブから東海3県のお笑い公演を更新する。

FANYは2026-10-22以降HTTP 403で拒否されている(回避はしない)。

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


def _get_browser_html(url: str, after_load=None) -> str | None:
    """Playwrightで実ブラウザを起動してレンダリング済みHTMLを取得する。

    playwrightが利用できない(未インストール)環境ではNoneを返し、
    呼び出し側でそのソースの取得をスキップできるようにする。
    after_load(page)を渡すと、HTMLを取る前に実行する(描画完了待ち等)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[warn] playwright未インストールのためスキップ: {url}")
        return None
    last_error: Exception | None = None
    with sync_playwright() as p:
        # 一部サイトはCIからのHTTP/2接続をbot対策としてリセットするらしく
        # net::ERR_HTTP2_PROTOCOL_ERRORになることがあるため、HTTP/2を無効化
        # しつつ2回まで試行する。
        browser = p.chromium.launch(args=["--disable-http2"])
        try:
            for attempt in range(2):
                try:
                    page = browser.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
                    # "networkidle"はアナリティクス等の常時通信でタイムアウトしや
                    # すいため、DOM構築完了を待ってから固定時間だけ追加待機する。
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    if after_load:
                        after_load(page)
                    html = page.content()
                    page.close()
                    return html
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    print(f"[warn] {url} 取得{attempt + 1}回目失敗: {exc!r}")
        finally:
            browser.close()
    if last_error:
        raise last_error
    return None


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
    # &nbsp; 等の文字参照が「先着 アブナイ&nbsp;ヨセ」のように公演名に
    # 残っていたため、文字に戻してから余分な空白をまとめる(2026-11-25修正)。
    import html as _h
    text = _h.unescape(TAG_RE.sub(" ", fragment)).replace("\xa0", " ")
    return re.sub(r"[ \t\r\n]+", " ", text).strip()


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


def _eplus_wait_until_loaded(page) -> None:
    """一覧の描画が終わり、件数が増えなくなるまで待つ。

    以前は固定3秒待ちだけで、日によって1件しか取れない(描画途中の
    HTMLを取ってしまう)ことがあった(2026-10〜11の実績: 1〜20件で変動)。
    スクロールで追加表示される分も拾えるよう、下までスクロールしながら
    件数が2回続けて変わらなくなるまで待つ(最大15回)。
    """
    selector = "a.ticket-item--kouen"
    try:
        page.wait_for_selector(selector, timeout=20000)
    except Exception:  # noqa: BLE001 - 本当に0件の場合もある
        return
    last, stable = -1, 0
    for _ in range(15):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        count = page.locator(selector).count()
        stable = stable + 1 if count == last else 0
        last = count
        if stable >= 2:
            break


def fetch_eplus(pref_slug: str) -> list[dict]:
    url = f"https://eplus.jp/sf/play/comedy/{pref_slug}"
    html = _get_browser_html(url, _eplus_wait_until_loaded)
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


# ---------------------------------------------------------------------------
# 吉本興業の公式サイト(名古屋よしもと・よしもとライブ)
#
# FANYチケットの検索APIが2026-10-22以降HTTP 403で拒否されるようになった
# ため、その代わりとして追加(2026-11-25)。どちらもrobots.txtで対象ページ
# は制限されておらず、吉本興業サイトのチケット約款等にも自動取得の禁止
# 規定は見当たらなかった。相手サーバーに負担をかけないよう、正直な
# User-Agentを名乗り、リクエストの間に1秒以上の間隔を空ける。
# ---------------------------------------------------------------------------
import html as _html
import time as _time

POLITE_HEADERS = {
    "User-Agent": (
        "Tokai-Owarai-Live-Tracker/1.0 "
        "(+https://github.com/lastradezanpano/tokai-owarai-live)"
    )
}
POLITE_INTERVAL_SEC = 1.5
NAGOYA_SCHEDULE_URL = "https://nagoya.yoshimoto.co.jp/schedule/"
YLIVE_BASE = "https://live.yoshimoto.co.jp"
YLIVE_PREF_CODES = {"23": "愛知", "21": "岐阜", "24": "三重"}

# 会場名から県を判定するための手がかり。名古屋よしもとの公演は大半が
# 愛知県内なので、ここに当てはまらなければ愛知とみなす。
MIE_HINTS = ("三重", "アスト津", "津市", "四日市", "鈴鹿", "伊勢", "桑名", "松阪",
             "名張", "亀山", "伊賀", "鳥羽", "尾鷲", "志摩")
GIFU_HINTS = ("岐阜", "大垣", "多治見", "各務原", "高山", "中津川", "可児", "羽島",
              "関市", "美濃", "土岐", "瑞浪", "恵那", "郡上", "じゅうろくプラザ")

_last_polite_request = [0.0]


def _polite_get(url: str) -> str:
    wait = POLITE_INTERVAL_SEC - (_time.monotonic() - _last_polite_request[0])
    if wait > 0:
        _time.sleep(wait)
    request = urllib.request.Request(url, headers=POLITE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    finally:
        _last_polite_request[0] = _time.monotonic()


def _html_to_lines(fragment: str) -> list[str]:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", fragment, flags=re.I)
    text = _html.unescape(TAG_RE.sub("", text)).replace("　", " ")
    return [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if line.strip()]


def guess_pref(*texts: str) -> str:
    joined = " ".join(texts)
    if any(h in joined for h in MIE_HINTS):
        return "三重"
    if any(h in joined for h in GIFU_HINTS):
        return "岐阜"
    return "愛知"


NAGOYA_ITEM_RE = re.compile(
    r'<li[^>]*>\s*<a href="([^"]+)">.*?<time datetime="(\d{4}-\d{2}-\d{2})".*?'
    r'<h3 class="entrytitle">(.*?)</h3>',
    re.DOTALL,
)


def parse_nagoya_schedule(page_html: str) -> list[dict]:
    """名古屋よしもと公式のスケジュール一覧HTMLから公演を取り出す。

    1件ごとに「公演名<br>[会場]…<br>[時間]…開場｜…開演<br>[出演者]…」の形。
    同じ日に同名公演が複数回ある(UDE-DA-MESHI等)ため、その場合は公演名に
    開演時刻を付けて別カードとして区別する。
    """
    page_html = re.sub(r"<!--.*?-->", "", page_html.replace("\r", ""), flags=re.DOTALL)
    start = page_html.find("sec-news")
    if start < 0:
        raise RuntimeError("スケジュール一覧(sec-news)が見つからない。ページ構造が変わった可能性")
    raw = []
    for href, date, h3 in NAGOYA_ITEM_RE.findall(page_html[start:]):
        lines = _html_to_lines(h3)
        if not lines:
            continue
        info = {"title": lines[0], "venue": "", "time": ""}
        for line in lines[1:]:
            if line.startswith("[会場]"):
                info["venue"] = line[len("[会場]"):].strip()
            elif line.startswith("[時間]"):
                m = re.search(r"(\d{1,2}:\d{2})開演", line)
                info["time"] = m.group(1) if m else ""
        raw.append({**info, "date": date, "url": href})
    counts: dict[tuple[str, str], int] = {}
    for r in raw:
        counts[(r["date"], r["title"])] = counts.get((r["date"], r["title"]), 0) + 1
    results = []
    for r in raw:
        title = r["title"]
        if counts[(r["date"], title)] > 1 and r["time"]:
            title = f"{title}（{r['time']}開演）"
        results.append({
            "date": r["date"], "title": title, "venue": r["venue"],
            "city": "", "pref": guess_pref(r["venue"], r["title"]),
            "status": "unknown", "src": "nagoya_yoshimoto", "url": r["url"],
        })
    return results


def fetch_nagoya_yoshimoto() -> list[dict]:
    return parse_nagoya_schedule(_polite_get(NAGOYA_SCHEDULE_URL))


YLIVE_CARD_RE = re.compile(
    r'<li id="post-\d+">\s*<a href="([^"]*/live/live-\d+/)">(.*?)</a>', re.DOTALL
)
YLIVE_PERIOD_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
# 2026年12月12日 / 12月12日 / 12/12 のいずれかの日付表記
# 2026年12月12日 / 2027/1/31 / 12月12日 / 12/12( のいずれかの日付表記
DATE_ANY_RE = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"|(\d{4})/(\d{1,2})/(\d{1,2})"
    r"|(?<![\d/:])(\d{1,2})/(\d{1,2})(?=\s*[（(])"
)
REGION_WORDS = {"愛知": "愛知", "名古屋": "愛知", "岐阜": "岐阜", "三重": "三重"}


def parse_ylive_list(page_html: str) -> list[dict]:
    """よしもとライブの都道府県検索結果から、詳細ページURL・会場欄・期間を取る。"""
    cards = []
    for url, body in YLIVE_CARD_RE.findall(page_html):
        venue_m = re.search(r'class="venue">(.*?)</div>', body, re.DOTALL)
        label = _clean_text(venue_m.group(1)) if venue_m else ""
        dates = YLIVE_PERIOD_RE.findall(body)
        if not dates:
            continue
        start = "-".join(dates[0])
        end = "-".join(dates[-1])
        cards.append({"url": url, "label": label, "start": start, "end": end})
    return cards


def _dates_in(text: str, start: str, end: str) -> list[str]:
    """文中の日付をISO形式にする。

    年の書いていない日付は、一覧の掲載期間の終わり(end)以前で最も新しい年と
    みなす(ただし期間の始まりの1年前より古くはしない)。一覧の期間がツアー
    最終日だけのこともあるため(例: 2027.02.12 のみ表示で、愛知公演は12/12)。
    """
    out = []
    lo = (datetime.fromisoformat(start) - timedelta(days=366)).date().isoformat()
    for m in DATE_ANY_RE.finditer(text):
        g = m.groups()
        if g[1]:
            y, mo, d = g[0], g[1], g[2]
        elif g[3]:
            y, mo, d = g[3], g[4], g[5]
        else:
            y, mo, d = None, g[6], g[7]
        try:
            if y:
                iso = f"{int(y)}-{int(mo):02d}-{int(d):02d}"
                datetime.fromisoformat(iso)
                out.append(iso)
                continue
            for year in range(int(end[:4]), int(lo[:4]) - 1, -1):
                iso = f"{year}-{int(mo):02d}-{int(d):02d}"
                datetime.fromisoformat(iso)
                if lo <= iso <= end:
                    out.append(iso)
                    break
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def _ylive_table(page_html: str) -> dict[str, str]:
    rows = {}
    for th, td in re.findall(r"<th>(.*?)</th>\s*<td>(.*?)</td>", page_html, re.DOTALL):
        rows[_clean_text(th)] = td
    return rows


def parse_ylive_detail(page_html: str, card: dict, pref_hint: str) -> list[dict]:
    """よしもとライブの詳細ページから東海3県分の公演を取り出す。

    - 単独公演ページ(一覧の会場欄が「愛知県」等): 公演日程欄の日付すべてと会場欄。
    - ツアーページ(会場欄が「ツアー」等): 公演日程欄を【愛知】＜愛知公演＞●…愛知…
      のような見出しで区切り、東海3県の見出しのブロックに日付が書かれている
      ものだけ採用する。日付が読み取れない場合は推測で載せない
      (sources.jsonのチケットぴあ注記と同じ方針: 日付未確認の公演は掲載しない)。
    """
    title_m = re.search(r"<title>(.*?)</title>", page_html, re.DOTALL)
    if not title_m:
        return []
    title = _html.unescape(title_m.group(1)).split(" – ")[0].replace("　", " ").strip()
    title = re.sub(r"^【(?:全国|愛知|岐阜|三重|名古屋)】\s*", "", title)
    rows = _ylive_table(page_html)
    sched_html = next((v for k, v in rows.items() if "公演日程" in k), "")
    single = card["label"] in ("愛知県", "岐阜県", "三重県")
    if sched_html:
        lines = _html_to_lines(sched_html)
    elif not single:
        # 日程表が無く、説明文に「【愛知】11/28(土)～29(日) 📍御園座」のように
        # 書かれているツアーページ向け。本文を「各公演情報」の手前まで読む。
        body = re.sub(r"<(script|style)\b.*?</\1>", "", page_html, flags=re.DOTALL | re.I)
        body = body[body.find("<body"):] if "<body" in body else body
        lines = []
        for line in _html_to_lines(body):
            if "各公演情報" in line or "公演一覧へ戻る" in line:
                break
            lines.append(line)
    else:
        return []
    venue_lines = _html_to_lines(next((v for k, v in rows.items() if k == "会場"), ""))
    venue = venue_lines[0] if venue_lines else ""
    events = []

    def add(date: str, venue_name: str, pref: str) -> None:
        events.append({
            "date": date, "title": title, "venue": venue_name, "city": "",
            "pref": pref, "status": "unknown", "src": "yoshimoto_live",
            "url": card["url"],
        })

    if single:
        pref = card["label"][:-1]
        for date in _dates_in("\n".join(lines), card["start"], card["end"]):
            add(date, venue, pref)
        return events

    parse_tour_lines(lines, card, add)
    return events


def parse_tour_lines(lines: list[str], card: dict, add) -> None:
    """ツアーの日程文を見出しごとのブロックに分け、東海3県分を add() する。

    見出し例: 【愛知公演】 / ＜愛知公演＞ / ●…～名古屋～ / ●2/28(日) 開場…
    県の判定は見出し、またはブロック内の「（愛知県）」等の表記で行う。
    日付は見出し・ブロック内の両方から探す。読み取れなければ何もしない。
    """
    header_re = re.compile(r"^(?:【[^】]*】|＜[^＞]*＞|<[^>]*>|●.*|■.*|◆.*)")
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        if header_re.match(line):
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)
    for header, body in blocks:
        pref = next((p for w, p in REGION_WORDS.items() if w in header), "")
        venue = ""
        if not pref:
            for line in body:
                m = re.search(r"[（(](愛知|岐阜|三重)県[）)]", line)
                if m:
                    pref = m.group(1)
                    venue = re.sub(r"[（(](?:愛知|岐阜|三重)県[）)]", "", line).strip()
                    break
        if not pref:
            continue
        dates = _dates_in("\n".join([header] + body), card["start"], card["end"])
        if not dates:
            continue
        if not venue:
            for line in [header] + body:
                m = re.search(r"(?:会場[：:]|📍)\s*(.+)", line)
                if m:
                    venue = m.group(1).strip()
                    break
        if not venue:
            # 「日付行の次の行が会場」という書き方に対応
            for i, line in enumerate(body):
                if DATE_ANY_RE.search(line) and i + 1 < len(body):
                    nxt = body[i + 1]
                    if not re.match(r"(主催|出演|※|料金|チケット|日時)", nxt) and not DATE_ANY_RE.search(nxt):
                        venue = nxt
                    break
        for date in dates:
            add(date, venue, pref)


def fetch_yoshimoto_live(today: str) -> tuple[list[dict], int]:
    """戻り値: (東海3県の公演一覧, 東海公演を読み取れなかった詳細ページ数)"""
    cards: dict[str, dict] = {}
    for code, pref in YLIVE_PREF_CODES.items():
        listing = _polite_get(f"{YLIVE_BASE}/?pref={code}&ym=&s=")
        for card in parse_ylive_list(listing):
            if card["end"] >= today:
                cards.setdefault(card["url"], {**card, "pref_hint": pref})
    events, unparsed = [], 0
    for card in cards.values():
        found = parse_ylive_detail(_polite_get(card["url"]), card, card["pref_hint"])
        if not found:
            unparsed += 1
            print(f"[info] よしもとライブ: 東海3県の日程を読み取れず掲載見送り {card['url']}")
        events.extend(e for e in found if e["date"] >= today)
    return events, unparsed


_TITLE_NOISE_RE = re.compile(r"[\s!！「」『』～〜\-ー]")


def _title_key(title: str) -> str:
    # 全角・半角の違い(「トークライブ２」と「トークライブ2」等)で同じ公演が
    # 別カードにならないよう、NFKC正規化してから比較する(2026-11-25追加)。
    import unicodedata
    return _TITLE_NOISE_RE.sub("", unicodedata.normalize("NFKC", title)).lower()


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


def _run_source(label: str, fetch_fn, diagnostics: dict) -> tuple[list[dict], bool]:
    """1情報源の取得を実行する。例外が起きても他ソースを巻き込まない。

    戻り値の2つ目はこの取得が成功したかどうか。失敗した場合、
    呼び出し側は既存データを温存し、誤って全消去しないようにする。
    結果は diagnostics[label] にも記録し、CIログにサインインしなくても
    後から原因を追えるように data/scrape_status.json へ書き出す。
    """
    try:
        events = fetch_fn()
        print(f"[ok] {label}: {len(events)}件取得")
        diagnostics[label] = {"ok": True, "count": len(events), "error": None}
        return events, True
    except Exception as exc:  # noqa: BLE001 - 1ソースの失敗で全体を止めない
        message = f"{type(exc).__name__}: {exc}"
        print(f"[warn] {label}の取得に失敗したためスキップ: {message}")
        diagnostics[label] = {"ok": False, "count": 0, "error": message}
        return [], False


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date().isoformat()
    data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    existing = data.get("events", [])
    old_cities = {
        (e.get("date", ""), e.get("title", "")): e.get("city", "")
        for e in existing if e.get("src") == "fany_ticket"
    }

    def fetch_fany_all() -> list[dict]:
        out = []
        for code, pref in PREFS.items():
            for performance in fetch_all(code):
                event = normalize(performance, pref, old_cities)
                if event and event["date"] >= today:
                    out.append(event)
        return out

    def fetch_bushikaku_all() -> list[dict]:
        out = []
        for slug, pref in BUSHIKAKU_PREFS.items():
            for raw in fetch_bushikaku(slug):
                event = normalize_bushikaku(raw, pref)
                if event and event["date"] >= today:
                    out.append(event)
        return out

    def fetch_eplus_all() -> list[dict]:
        out = []
        for slug, pref in EPLUS_PREFS.items():
            for raw in fetch_eplus(slug):
                event = normalize_eplus(raw, pref)
                if event and event["date"] >= today:
                    out.append(event)
        return out

    def fetch_ltike_all() -> list[dict]:
        out = []
        for raw in fetch_ltike():
            event = normalize_ltike(raw)
            if event and event["date"] >= today:
                out.append(event)
        return out

    def fetch_ylive_all() -> list[dict]:
        events, unparsed = fetch_yoshimoto_live(today)
        ylive_unparsed[0] = unparsed
        return events

    ylive_unparsed = [0]
    diagnostics: dict[str, dict] = {}
    fany_events, fany_ok = _run_source("FANY", fetch_fany_all, diagnostics)
    bushikaku_events, bushikaku_ok = _run_source("バス比較なび", fetch_bushikaku_all, diagnostics)
    eplus_events, eplus_ok = _run_source("イープラス", fetch_eplus_all, diagnostics)
    nagoya_events, nagoya_ok = _run_source(
        "名古屋よしもと",
        lambda: [e for e in fetch_nagoya_yoshimoto() if e["date"] >= today],
        diagnostics,
    )
    ylive_events, ylive_ok = _run_source("よしもとライブ", fetch_ylive_all, diagnostics)
    if ylive_ok:
        diagnostics["よしもとライブ"]["unparsed_pages"] = ylive_unparsed[0]
    # ローソンチケットは2026-09-29に自動化を断念(詳細はSETUP.md参照)。
    # Playwright+HTTP/2無効化+リトライでも net::ERR_HTTP2_PROTOCOL_ERROR が
    # 再現し、同条件でイープラスは成功しているためブラウザ側の問題ではなく
    # GitHub Actionsのクラウド上のIPアドレス自体を相手側がネットワーク
    # レベルでブロックしていると判断した。fetch_ltike自体は温存してあるので
    # 状況が変われば呼び出しを復活させればよい。
    ltike_events: list[dict] = []
    ltike_ok = False
    diagnostics["ローソンチケット"] = {
        "ok": False, "count": 0,
        "error": "自動化を断念(2026-09-29): net::ERR_HTTP2_PROTOCOL_ERRORが解消せずIPブロックと判断。詳細はSETUP.md参照",
    }
    # イープラスはbot対策で無言のまま0件になり得る(bot検知でブロックされ
    # ても例外を投げないことがある)。愛知・岐阜・三重3県合計で0件は現実的
    # にまず起きないので、0件は「取得失敗」とみなして既存データを維持する。
    if eplus_ok and not eplus_events:
        print("[warn] イープラス: 0件のため取得失敗とみなし既存データを維持")
        eplus_ok = False
        diagnostics["イープラス"] = {"ok": False, "count": 0, "error": "0件(bot対策等でブロックされた可能性)"}
    # 名古屋よしもとは毎週公演があるため、0件はページ構造の変更等とみなす。
    if nagoya_ok and not nagoya_events:
        print("[warn] 名古屋よしもと: 0件のため取得失敗とみなし既存データを維持")
        nagoya_ok = False
        diagnostics["名古屋よしもと"] = {"ok": False, "count": 0, "error": "0件(ページ構造が変わった可能性)"}

    # 取得に成功したソースの既存データだけを入れ替え対象にする。失敗した
    # ソース(bot対策強化やサイト構造変更等)は既存データをそのまま温存し、
    # 手動収集分(チケットぴあ等)と合わせて残す。
    replaced_sources = set()
    if fany_ok:
        replaced_sources.add("fany_ticket")
    if bushikaku_ok:
        replaced_sources.add("bushikaku_chubu")
    if eplus_ok:
        replaced_sources.update({"eplus_aichi", "eplus_gifu", "eplus_mie"})
    if nagoya_ok:
        replaced_sources.add("nagoya_yoshimoto")
    if ylive_ok:
        replaced_sources.add("yoshimoto_live")
    if ltike_ok:
        replaced_sources.add("ltike")

    # 重複時にどの情報源のカードを残すかの優先順。販売状況(受付中など)が
    # 分かる情報源を先にする。
    fetched_all = (
        eplus_events + fany_events + ltike_events + bushikaku_events
        + ylive_events + nagoya_events
    )

    # 取得が「成功」でも、描画途中や一時的なブロックで件数が急減することが
    # ある(イープラスで20件→1件など)。前回の掲載件数(未来日分)の半分未満
    # しか取れなかった情報源は入れ替えず、既存データに新規分を足す形で統合
    # して、一時的な取りこぼしで公演が消えないようにする。
    fetched_by_src: dict[str, int] = {}
    for e in fetched_all:
        fetched_by_src[e["src"]] = fetched_by_src.get(e["src"], 0) + 1
    merged_notes = []
    for src in sorted(replaced_sources):
        before = sum(
            1 for e in existing if e.get("src") == src and e.get("date", "") >= today
        )
        after = fetched_by_src.get(src, 0)
        if before >= 2 and after * 2 < before:
            print(f"[warn] {src}: {before}件→{after}件に急減したため既存データと統合")
            replaced_sources.discard(src)
            merged_notes.append(f"{src}: 前回{before}件→今回{after}件")

    write_status(today, diagnostics, merged_notes)

    retained = [
        e for e in existing
        if e.get("src") not in replaced_sources and e.get("date", "") >= today
    ]
    # 同じ日付・同一公演らしきもの(タイトルの部分一致)が別の情報源に既に
    # あれば、表記の揺れ(記号・スペース・省略)による重複カード化を避ける
    # ためスキップする。同じ情報源の中での重複判定はしない(同日の別公演を
    # 誤って消さないため)。
    accepted_by_date: dict[str, list[tuple[str, str]]] = {}
    for e in retained:
        accepted_by_date.setdefault(e["date"], []).append((_title_key(e["title"]), e.get("src", "")))

    def is_duplicate(event: dict) -> bool:
        key = _title_key(event["title"])
        for other, src in accepted_by_date.get(event["date"], []):
            # 同じ情報源同士は、同日の別公演(2回公演・ネタ/トーク等)を誤って
            # 消さないよう重複判定しない。ただし、よしもとライブはツアー全体の
            # ページと各県の個別ページに同じ公演が載るため同士でも判定する。
            if src == event["src"] != "yoshimoto_live":
                continue
            if len(key) >= 3 and len(other) >= 3 and (key in other or other in key):
                return True
        return False

    new_events = []
    for e in fetched_all:
        if is_duplicate(e):
            continue
        new_events.append(e)
        accepted_by_date.setdefault(e["date"], []).append((_title_key(e["title"]), e["src"]))

    unique = {}
    for event in retained + new_events:
        unique[(event["date"], event["title"], event["venue"])] = event
    all_ok = all([fany_ok, bushikaku_ok, eplus_ok, nagoya_ok, ylive_ok])
    data["last_updated"] = today
    data["last_updated_note"] = (
        "GitHub ActionsでFANY・バス比較なび・イープラス・名古屋よしもと・"
        "よしもとライブと既存確認済み情報を更新"
        + ("" if all_ok else "（一部ソースは取得失敗のため既存データを維持）")
    )
    data["events"] = sorted(
        unique.values(),
        key=lambda e: (e.get("date", ""), e.get("pref", ""), e.get("title", "")),
    )
    EVENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"updated {len(data['events'])} events total "
        f"(FANY {len(fany_events)}/{'ok' if fany_ok else 'FAILED'}, "
        f"bushikaku {len(bushikaku_events)}/{'ok' if bushikaku_ok else 'FAILED'}, "
        f"eplus {len(eplus_events)}/{'ok' if eplus_ok else 'FAILED'}, "
        f"nagoya {len(nagoya_events)}/{'ok' if nagoya_ok else 'FAILED'}, "
        f"ylive {len(ylive_events)}/{'ok' if ylive_ok else 'FAILED'})"
    )


# ---------------------------------------------------------------------------
# 取得状況の記録と停止検知
#
# 2026-10-22〜11-24にFANYが毎日403で失敗し続けていたのに、1か月以上
# 気付けなかった反省から追加。情報源ごとに「最後に成功した日」と
# 「連続失敗日数」を scrape_status.json に残し、ALERT_DAYS日以上続けて
# 失敗している情報源があれば ALERT_PATH にお知らせ文を書き出す。
# GitHub Actions側でこのファイルがあればIssueを作成(=メール通知)する。
# ---------------------------------------------------------------------------
STATUS_PATH = ROOT / "data" / "scrape_status.json"
ALERT_PATH = ROOT / "alert.md"
ALERT_DAYS = 3
NOT_MONITORED = {"ローソンチケット"}  # 自動化を断念済みのため通知対象外


def write_status(today: str, diagnostics: dict, merged_notes: list[str]) -> None:
    try:
        previous = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        previous = {}
    prev_history = previous.get("history", {})
    prev_checked = previous.get("checked_at")
    history = {}
    for label, info in diagnostics.items():
        h = dict(prev_history.get(label, {"last_ok": None, "fail_streak": 0}))
        if info["ok"]:
            h["last_ok"] = today
            h["fail_streak"] = 0
        elif prev_checked != today:  # 同じ日の再実行では数えない
            h["fail_streak"] = h.get("fail_streak", 0) + 1
        h.setdefault("last_ok", None)
        history[label] = h
    STATUS_PATH.write_text(
        json.dumps(
            {"checked_at": today, "sources": diagnostics,
             "merged_due_to_drop": merged_notes, "history": history},
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    failing = [
        (label, h) for label, h in history.items()
        if label not in NOT_MONITORED and h["fail_streak"] >= ALERT_DAYS
    ]
    if not failing:
        if ALERT_PATH.exists():
            ALERT_PATH.unlink()
        return
    lines = [
        "東海お笑いライブ帳の自動更新で、次の情報源が続けて取得に失敗しています。",
        "失敗中の情報源は以前のデータを残したままなので、新しい公演が追加されていません。",
        "",
        "| 情報源 | 連続失敗 | 最後に成功した日 | 直近のエラー |",
        "|---|---|---|---|",
    ]
    for label, h in failing:
        err = str(diagnostics[label].get("error") or "").replace("|", "/")
        lines.append(f"| {label} | {h['fail_streak']}日 | {h['last_ok'] or '不明'} | {err} |")
    lines += [
        "",
        f"確認日: {today}",
        "",
        "このIssueは全ての情報源が復旧すると自動で閉じられます。"
        "詳しくはリポジトリの `data/scrape_status.json` を確認してください。",
    ]
    ALERT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
