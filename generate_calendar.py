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
from icalendar import Calendar, Event

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get(url, **kw):
    try:
        time.sleep(0.5)
        r = SESSION.get(url, timeout=15, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [WARN] GET {url}: {e}", file=sys.stderr)
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
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ffxiv = get_ffxiv_events()
    print(f"FFXIV total: {len(ffxiv)}")
    write_cal(ffxiv,
              "FFXIV Events",
              "FFXIV seasonal events and maintenance (auto-updated daily)",
              "ffxiv.ics")

    pso2 = get_pso2_events()
    print(f"PSO2 total:  {len(pso2)}")
    write_cal(pso2,
              "PSO2 NGS Events",
              "PSO2 New Genesis events and campaigns (auto-updated daily)",
              "pso2.ics")

if __name__ == "__main__":
    main()
