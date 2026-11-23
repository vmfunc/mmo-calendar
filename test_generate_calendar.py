import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import generate_calendar as g

SCHEDULE = 'From Wednesday, November 18, 2026 at 12:00 a.m. (PST) to Wednesday, December 9, 2026 at 6:59 a.m. (PST)'
HTML = '<h4>Event Schedule</h4><p>' + SCHEDULE + '</p>'
POST = datetime(2026, 11, 11, 8, tzinfo=timezone.utc)
TITLE = 'FINAL FANTASY XIV x FINAL FANTASY XVI Crossover Event Returns!'

class Response:
    def __init__(self, text='', data=None): self.text, self.data = text, data
    def json(self): return self.data

class ScheduleTests(unittest.TestCase):
    def parse(self, html): return g.extract_inline_schedule(BeautifulSoup(html, 'html.parser'))
    def events(self, html, special=None, title=TITLE):
        item = {'title': title, 'url': 'https://example.test/topic', 'time': POST.isoformat()}
        def get(url):
            if '/news/topics' in url: return Response(data=[item])
            if '/news/maintenance' in url: return Response(data=[])
            if url == item['url']: return Response(text=html)
            raise AssertionError('unexpected URL: ' + url)
        with patch.object(g, 'get', side_effect=get), patch.object(g, 'extract_dates_from_lodestone_page', return_value=special or (None, None)):
            return g.get_ffxiv_events()[0]
    def test_actual_lodestone_markup(self):
        html = (Path(__file__).parent / "tests/fixtures/ffxiv_xvi_schedule.html").read_text()
        self.assertEqual(self.parse(html), self.parse(HTML))
        ev = self.events(html)
        self.assertEqual(ev.decoded("dtend"), datetime(2026,12,9,14,59,tzinfo=timezone.utc))
    def test_pst(self):
        self.assertEqual(self.parse(HTML), (datetime(2026,11,18,8,tzinfo=timezone.utc), datetime(2026,12,9,14,59,tzinfo=timezone.utc)))
    def test_pdt(self):
        self.assertEqual(self.parse(HTML.replace('PST','PDT'))[0].hour, 7)
    def test_unsupported_timezone(self):
        self.assertEqual(self.parse(HTML.replace('PST','EST')), (None,None))
    def test_label_required(self):
        self.assertEqual(self.parse('<p>'+SCHEDULE+'</p>'), (None,None))
    def test_multiple_windows_rejected(self):
        self.assertEqual(self.parse(HTML+'<p>'+SCHEDULE+'</p>'), (None,None))
    def test_multiple_labels_rejected(self):
        self.assertEqual(self.parse(HTML+HTML), (None,None))
    def test_reversed_rejected(self):
        self.assertEqual(self.parse(HTML.replace('December 9','November 9')), (None,None))
    def test_next_heading_boundary(self):
        self.assertEqual(self.parse('<h4>Event Schedule</h4><h4>Other</h4><p>'+SCHEDULE+'</p>'), (None,None))
    def test_integration_uid_preserved(self):
        ev = self.events(HTML+'<a href="https://sqex.to/foo">special site</a>')
        self.assertEqual(ev.decoded('dtstart'), self.parse(HTML)[0])
        self.assertEqual(ev.decoded('dtend'), self.parse(HTML)[1])
        self.assertEqual(str(ev['uid']), g.stable_uid('ffxiv', '[FFXIV] '+TITLE+str(POST)))
        self.assertIn(b'DTEND:20261209T145900Z', ev.to_ical())
    def test_ambiguous_preserves_fallback(self):
        ev = self.events(HTML+HTML)
        self.assertEqual(ev.decoded('dtstart'), POST)
        self.assertEqual(ev.decoded('dtend'), POST+g.timedelta(days=14))
    def test_special_page_precedence_unchanged(self):
        dates = (POST, POST+g.timedelta(days=5))
        ev = self.events(HTML+'<a href="/lodestone/special/2026/test/">special</a>', dates)
        self.assertEqual(ev.decoded('dtstart'), POST)
        self.assertEqual(ev.decoded('dtend'), dates[1])
    def test_title_uid_preserved(self):
        title = 'Event Begins November 18!'
        ev = self.events(HTML, title=title)
        old_start = g.extract_date_from_title(title, POST)
        self.assertEqual(str(ev['uid']), g.stable_uid('ffxiv','[FFXIV] '+title+str(old_start)))

if __name__ == '__main__': unittest.main()
