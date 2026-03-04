#!/usr/bin/env python3
"""
MMO Calendar Generator
Scrapes FFXIV and PSO2 NGS events and generates two subscribable ICS files:
  ffxiv.ics  – FFXIV seasonal events + maintenance
  pso2.ics   – PSO2 NGS events and campaigns
"""

import re
import sys
import time
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from dateutil.rrule import DAILY, WEEKLY
from icalendar import Calendar, Event

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get(url, **kw):
    # longer pause for lodestone pages - they rate limit aggressively in CI
    delay = 2.0 if "finalfantasyxiv.com" in url else 0.5
    for attempt in range(3):
        try:
            time.sleep(delay if attempt == 0 else 2.0 * (attempt + 1))
            r = SESSION.get(url, timeout=20, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  [WARN] GET {url} (attempt {attempt+1}/3): {e}", file=sys.stderr)
    return None

def stable_uid(source, key):
    h = hashlib.sha1(f"{source}:{key}".encode()).hexdigest()[:12]
    return f"{h}@mmo-calendar"

def make_event(summary, dtstart, dtend=None, description="", url="", source=""):
    ev = Event()
    ev.add("summary", summary)
    ev.add("dtstart", dtstart)
    ev.add("dtend",   dtend or dtstart + timedelta(days=1))
    if description:
        ev.add("description", description)
    if url:
        ev.add("url", url)
    ev.add("uid",     stable_uid(source, summary + str(dtstart)))
    ev.add("dtstamp", datetime.now(timezone.utc))
    return ev

RECURRING_FFXIV = [
    ("FFXIV Weekly Reset",   "TU", 8,  WEEKLY),
    ("FFXIV Daily Reset",    None, 15, DAILY),
    ("FFXIV Jumbo Cactpot",  "SA", 20, WEEKLY),
]

RECURRING_PSO2 = [
    ("PSO2 NGS Weekly Reset", "WE", 13, WEEKLY),
    ("PSO2 NGS Daily Reset",  None, 13, DAILY),
]

def make_recurring(summary, byday, hour, freq, source):
    ev = Event()
    start = datetime(2025, 1, 1, hour, 0, 0, tzinfo=timezone.utc)
    # pick a start date that lands on the right weekday
    if byday:
        day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
        while start.weekday() != day_map[byday]:
            start += timedelta(days=1)
    ev.add("summary", summary)
    ev.add("dtstart", start)
    ev.add("dtend", start + timedelta(hours=1))
    ev.add("uid", stable_uid(source, summary))
    ev.add("dtstamp", datetime.now(timezone.utc))
    rrule = {"freq": "weekly" if freq == WEEKLY else "daily"}
    if byday:
        rrule["byday"] = byday
    ev.add("rrule", rrule)
    return ev

def write_cal(events, name, desc, outfile):
    cal = Calendar()
    cal.add("prodid",  f"-//mmo-calendar//{name}//EN")
    cal.add("version", "2.0")
    cal.add("calname", name)
    cal.add("caldesc", desc)
    cal.add("refresh-interval;value=duration", "P1D")
    cal.add("x-published-ttl", "P1D")
    for ev in events:
        cal.add_component(ev)
    with open(outfile, "wb") as f:
        f.write(cal.to_ical())
    print(f"Written {len(events)} events → {outfile}")

# ──────────────────────────────────────────────────────────────────────────────
# FFXIV – lodestonenews.com API + Lodestone special event pages
# ──────────────────────────────────────────────────────────────────────────────

FFXIV_EVENT_KEYWORDS = [
    "event", "faire", "wake", "celebration", "begins", "returns",
    "hatching", "valentione", "ladies", "heavensturn", "make it rain",
    "rising", "starlight", "moonfire", "moogle treasure", "doman",
    "ceremony", "campaign", "collaboration", "collab",
]

_DATE_BLOCK = (
    r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
    r"(\w+ \d{1,2},? \d{4})"
    r"(?:\s+at\s+(\d{1,2}:\d{2}\s+[ap]\.m\.))?(?:\s+\([A-Z]{2,4}\))?"
)
RE_DATE_RANGE = re.compile(
    r"[Ff]rom\s+" + _DATE_BLOCK + r"\s+to\s+" + _DATE_BLOCK,
    re.IGNORECASE,
)

def parse_lodestone_date(date_str, time_str=None):
    date_str = date_str.replace(",", "").strip()
    fmt = "%B %d %Y"
    try:
        d = datetime.strptime(date_str, fmt)
        if time_str:
            t = time_str.replace(".", "").replace("a m", "AM").replace("p m", "PM")
            t = re.sub(r"\s+", " ", t).strip()
            try:
                dt = datetime.strptime(f"{date_str} {t}", f"{fmt} %I:%M %p")
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

RE_TITLE_DATE = re.compile(
    r"(?:begins?|starts?|on|from)\s+(\w+ \d{1,2})(?:!|$|\s)",
    re.IGNORECASE,
)

def extract_date_from_title(title, reference_time):
    """pull event start date from title text like 'Begins February 26!'"""
    m = RE_TITLE_DATE.search(title)
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%B %d").replace(
            year=reference_time.year, tzinfo=timezone.utc,
        )
        # if the parsed month is way before the announcement, it's probably next year
        if d.month < reference_time.month - 2:
            d = d.replace(year=reference_time.year + 1)
        return d
    except ValueError:
        return None

def extract_dates_from_lodestone_page(url):
    r = get(url)
    if not r:
        return None, None
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
    m = RE_DATE_RANGE.search(text)
    if not m:
        return None, None
    return (
        parse_lodestone_date(m.group(1), m.group(2)),
        parse_lodestone_date(m.group(3), m.group(4)),
    )

def get_ffxiv_events():
    events = []
    print("Fetching FFXIV topics from lodestonenews.com…")
    r = get("https://lodestonenews.com/news/topics?locale=na")
    if r:
        for item in r.json():
            title = item.get("title", "")
            if not any(kw in title.lower() for kw in FFXIV_EVENT_KEYWORDS):
                continue
            print(f"  {title}")
            topic_url = item.get("url", "")
            post_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
            start, end = None, None
            # try scraping the special event page for a full date range
            tr = get(topic_url)
            if tr:
                soup = BeautifulSoup(tr.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if re.search(r"/lodestone/special/\d{4}/", href) and "utm" not in href:
                        full = href if href.startswith("http") else f"https://na.finalfantasyxiv.com{href}"
                        start, end = extract_dates_from_lodestone_page(full)
                        if start:
                            break
            # fallback: parse date from the title itself ("Begins February 26!")
            if not start:
                start = extract_date_from_title(title, post_time)
            if not start:
                start = post_time
            if not end:
                end = start + timedelta(days=14)
            events.append(make_event(
                summary=f"[FFXIV] {title}",
                dtstart=start, dtend=end,
                description=item.get("description", ""),
                url=topic_url, source="ffxiv",
            ))

    print("Fetching FFXIV maintenance from lodestonenews.com…")
    r2 = get("https://lodestonenews.com/news/maintenance?locale=na")
    if r2:
        for item in r2.json():
            post_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
            events.append(make_event(
                summary=f"[FFXIV] {item.get('title','')}",
                dtstart=post_time,
                dtend=post_time + timedelta(hours=6),
                description=item.get("description", ""),
                url=item.get("url", ""), source="ffxiv-maint",
            ))
    return events

# ──────────────────────────────────────────────────────────────────────────────
# PSO2 NGS – pso2.jp event/campaign pages
# ──────────────────────────────────────────────────────────────────────────────

RE_JP_DATE_FULL  = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
RE_JP_RANGE = re.compile(
    r"(\d{4})[/年](\d{1,2})[/月](\d{1,2})日?"
    r"[^〜～\n]{0,30}[〜～][^(\d]{0,10}"
    r"(\d{4})?[/年]?(\d{1,2})[/月](\d{1,2})日?"
)

def parse_jp_date(year, month, day):
    try:
        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return None

def extract_pso2_dates(url, current_year):
    r = get(url)
    if not r:
        return None, None
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
    m = RE_JP_RANGE.search(text)
    if m:
        y1 = m.group(1) or str(current_year)
        y2 = m.group(4) or y1
        return parse_jp_date(y1, m.group(2), m.group(3)), parse_jp_date(y2, m.group(5), m.group(6))
    dates = RE_JP_DATE_FULL.findall(text)
    if dates:
        start = parse_jp_date(*dates[0])
        end   = parse_jp_date(*dates[-1]) if len(dates) >= 2 else None
        return start, end
    return None, None

SKIP_TITLES = {
    "ニュース", "イベント・キャンペーン", "最新記事", "お知らせ",
    "メンテナンス", "アップデート", "メディア", "ニュース ナビゲーション",
}

def get_pso2_events():
    events = []
    print("Fetching PSO2 NGS events from pso2.jp…")
    r = get("https://pso2.jp/players/news/event/")
    if not r:
        return events
    soup = BeautifulSoup(r.text, "html.parser")
    links = list(dict.fromkeys(
        a["href"] for a in soup.find_all("a", href=True)
        if re.match(r"https://pso2\.jp/players/news/\d+", a["href"])
    ))
    year = datetime.now().year
    for url in links[:20]:
        print(f"  {url}")
        r2 = get(url)
        if not r2:
            continue
        soup2 = BeautifulSoup(r2.text, "html.parser")
        # Title: first h-tag not matching known nav strings
        title = ""
        for el in soup2.find_all(["h1","h2","h3"]):
            cls = " ".join(el.get("class") or [])
            if "logo" in cls or "nav" in cls.lower() or "heading" in cls.lower():
                continue
            t = el.get_text(strip=True)
            if len(t) > 5 and t not in SKIP_TITLES:
                title = t
                break
        # Post date for fallback
        dm = RE_JP_DATE_FULL.search(soup2.get_text())
        post_date = parse_jp_date(*dm.groups()) if dm else None
        start, end = extract_pso2_dates(url, year)
        if not start:
            start = post_date
        if start and title:
            events.append(make_event(
                summary=f"[PSO2] {title}",
                dtstart=start,
                dtend=end or start + timedelta(days=7),
                description=f"Source: {url}",
                url=url, source="pso2",
            ))
    return events

# ──────────────────────────────────────────────────────────────────────────────
# PSO2 NGS – UQ predictions from nekobot.io
# ──────────────────────────────────────────────────────────────────────────────

UQ_API = "https://nekobot.io/api/pso2/uq-prediction"
UQ_NOTE = "Source: nekobot.io/pso2/global/uq-predictions \u2014 predictions may shift"

def get_ngs_uq_predictions():
    events = []
    print("Fetching NGS UQ predictions from nekobot.io\u2026")
    r = get(UQ_API)
    if not r:
        return events
    data = r.json().get("ngs", {})
    predictions = data.get("next_uq_predictions", [])
    # top 3 by probability - always include #1, skip rest if < 50%
    top = sorted(predictions, key=lambda p: p["probability"], reverse=True)[:3]
    for i, p in enumerate(top):
        prob = p["probability"]
        if prob < 0.5 and i > 0:
            continue
        start = datetime.fromtimestamp(p["start"], tz=timezone.utc)
        is_concert = p.get("is_concert", False)
        summary = "[PSO2] NGS Concert + UQ" if is_concert else "[PSO2] NGS Urgent Quest"
        desc = f"Probability: {prob*100:.0f}%\n{UQ_NOTE}"
        ev = Event()
        ev.add("summary", summary)
        ev.add("dtstart", start)
        ev.add("dtend", start + timedelta(hours=1))
        ev.add("description", desc)
        ev.add("uid", stable_uid("uq-prediction", str(p["start"])))
        ev.add("dtstamp", datetime.now(timezone.utc))
        events.append(ev)
        print(f"  {summary} @ {start.isoformat()} ({prob*100:.0f}%)")
    return events

# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ffxiv = get_ffxiv_events()
    for summary, byday, hour, freq in RECURRING_FFXIV:
        ffxiv.append(make_recurring(summary, byday, hour, freq, "ffxiv"))
    print(f"FFXIV total: {len(ffxiv)}")
    write_cal(ffxiv,
              "FFXIV Events",
              "FFXIV seasonal events and maintenance (auto-updated daily)",
              "ffxiv.ics")

    pso2 = get_pso2_events()
    pso2.extend(get_ngs_uq_predictions())
    for summary, byday, hour, freq in RECURRING_PSO2:
        pso2.append(make_recurring(summary, byday, hour, freq, "pso2"))
    print(f"PSO2 total:  {len(pso2)}")
    write_cal(pso2,
              "PSO2 NGS Events",
              "PSO2 New Genesis events and campaigns (auto-updated daily)",
              "pso2.ics")

if __name__ == "__main__":
    main()
