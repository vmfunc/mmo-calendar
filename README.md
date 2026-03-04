# mmo-calendar

auto-updating ics feeds for FFXIV and PSO2 NGS. two separate calendars so you can sub to one or both.

runs daily via github actions - scrapes event data, generates .ics files, commits them. subscribe in google calendar (or whatever) and forget about it.

## subscribe

| game | subscribe url |
|------|---------------|
| FFXIV | `https://raw.githubusercontent.com/vmfunc/mmo-calendar/main/ffxiv.ics` |
| PSO2 NGS | `https://raw.githubusercontent.com/vmfunc/mmo-calendar/main/pso2.ics` |

paste either url into your calendar app (google calendar: "other calendars" -> "from url"). google re-fetches roughly every 12-24h.

## what it scrapes

**FFXIV** - seasonal events from [lodestonenews.com](https://lodestonenews.com) (lodestone topics API). tries to extract real start/end dates from the event pages, falls back to announcement date + 14 days if parsing fails. also picks up maintenance windows.

**PSO2 NGS** - event/campaign posts from [pso2.jp](https://pso2.jp/players/news/event/). titles are in japanese - the EN site is fully JS-rendered so no dice without a headless browser. date ranges parsed from japanese date patterns in each article.

## local

```bash
pip install requests beautifulsoup4 icalendar python-dateutil
python generate_calendar.py
```
