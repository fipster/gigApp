"""
Scrapes upcoming shows for artists in artists.csv from AllEvents.lt's
search endpoint and merges them into shows.json.

AllEvents.lt is a Lithuania-wide meta-aggregator that pulls listings from
several underlying ticket vendors (Bilietai, Kakava, Ticketmarket,
Shownet, Paysera, Manobilietas, and others), so this one source covers
a fair amount of Lithuania's ticketed events even though most of those
underlying vendors are individually either bot-protected (Bilietai has
real Cloudflare Bot Management) or don't expose a usable search API.
AllEvents.lt itself has no bot-detection at all -- confirmed working
via plain unauthenticated requests, no special headers required beyond
a normal User-Agent.

There's no official public API or documentation for this; the
`/action` endpoint and its parameters were reverse-engineered from the
site's own frontend JS (tmpl/js/main.js). It's a plain HTML-fragment
response (not JSON) -- each result is an `<a class='col eventCard'>`
block, parsed here with regexes rather than an HTML parser library
since the structure is simple and consistent, and no other scraper in
this project has an HTML-parsing dependency yet.

An initial GET with ?lang=en sets an AEltLang=en cookie that the search
endpoint honors, giving English month names and easy date parsing.
Without it, dates come back in Lithuanian.

The site is Lithuania-only, so every result is confidently "LT" -- no
country-resolution ambiguity like Fienta.

This script only fetches show data -- it does not compute flight info.
Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.

Known limitation: the search matches more than just the event title
(e.g. searching "Pitbull" surfaced an unrelated "Lil Jon" VIP-upgrade
listing), so results are kept only if the artist name appears in the
event's own title as a whole word -- same anti-false-positive approach
used for Fienta and Kultuurikava.

Known limitation: bus/transport packages to a concert (titled e.g.
"Autobusas į <artist> koncertą iš <departure city>") show up as
separate search hits and DO contain the artist's name as a whole word,
so the title-match filter alone doesn't catch them -- and they'd
otherwise look like a second show in the departure city instead of the
actual venue's city. These are excluded by checking for the Lithuanian
word "Autobusas" in the title.

Known limitation: the search spans every category AllEvents.lt has, not
just concerts -- a short/common artist name matches circus shows
(href /kiti/...), theatre plays (/teatras/...), stand-up comedy nights,
etc. that happen to share a word with the artist's name (e.g. "Odyssey"
matched a circus show, "Up" matched a stand-up comedy night, "Down"
matched a strip revue titled "...Thunder from Down Under"). Results are
now restricted to hrefs starting with /koncertai/ (the concerts
category), which rules out most of these -- but not all, since some
non-concert events (that strip revue) are still filed under /koncertai/
on AllEvents.lt's own side. Titles containing "the best of" or
"tribute" are also excluded to catch tribute-act nights (e.g. a "Ruta
Sciogolevaite: The Best of Whitney Houston" listing matching "Whitney").
Manual review after each run is still expected, same as Fienta and
Kultuurikava -- these filters catch the common cases, not all of them.

No rate limit is documented for this endpoint; REQUEST_DELAY is a
conservative self-imposed pause between requests regardless.

Known limitation: the returned date usually has no year, only "Month
Day" (and for multi-day listings, "Month Day - Month Day", of which
only the start date is used here) -- except events landing in the next
calendar year, which get an explicit year prefix instead (e.g. "2027
May 10 - 20:00" vs. plain "August 12 - 20:00" for this year). When
there's no explicit year, it's inferred: this year, unless that date
has already passed, in which case next year.
"""

import csv
import html
import http.cookiejar
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

import shows_common as common

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://allevents.lt"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "AllEvents.lt"
REQUEST_DELAY = 0.5
ITEMS_PER_PAGE = 50

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}
COUNTRY_CODE = "LT"  # site is Lithuania-only, see module docstring

CARD_RE = re.compile(r"<a href='([^']*)' class='col eventCard'>(.*?)</a>", re.DOTALL)
DATE_RE = re.compile(r"class='date row j-start'>\s*<span[^>]*>[^<]*</span>([^<]*)</div>")
TITLE_RE = re.compile(r"<h4 class='mt-10'>(.*?)</h4>", re.DOTALL)
PLACE_RE = re.compile(r"class='placeName mt-10 row j-start'>\s*([^<]*)</div>", re.DOTALL)

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))


def load_artists(path):
    # "active" (3rd column) marks an artist deliberately excluded (e.g. not
    # in the current source playlist); "ignore" (4th column) flags a band
    # confirmed inactive/disbanded (see check_artist_status.py). Either one
    # skips the row entirely so scrapers never even attempt it.
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header row: artist_name,playlist,active,ignore
        return [row[0].strip() for row in reader if row and row[0].strip()
                and (len(row) < 3 or row[2].strip().lower() != "false")
                and (len(row) < 4 or row[3].strip().lower() != "true")]


def init_session():
    # establishes the AEltLang=en cookie so search results come back with English month names
    request = urllib.request.Request(f"{BASE_URL}/?lang=en", headers=REQUEST_HEADERS)
    _opener.open(request, timeout=15).read()


def search_events(artist):
    params = {
        "ext": "events",
        "action": "getEventsList",
        "cid": 0,
        "city": "",
        "place": "",
        "page": 1,
        "itemsPerPage": ITEMS_PER_PAGE,
        "text": artist,
        "dates": "",
        "popular": 0,
        "search": 1,
        "hideCinema": "false",
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    headers = dict(REQUEST_HEADERS, **{"Content-Type": "application/x-www-form-urlencoded"})
    request = urllib.request.Request(f"{BASE_URL}/action", data=data, headers=headers)
    try:
        with _opener.open(request, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  error for {artist}: {e}", file=sys.stderr)
        return ""


def parse_month_day(month_day, today):
    # a leading 4-digit year means an explicit next-year date; otherwise
    # infer the year (this year, or next year if that date already passed)
    year_match = re.match(r"^(\d{4})\s+(.*)$", month_day)
    explicit_year, rest = (int(year_match.group(1)), year_match.group(2)) if year_match else (None, month_day)
    try:
        if explicit_year:
            return datetime.strptime(f"{rest} {explicit_year}", "%B %d %Y").date()
        parsed = datetime.strptime(f"{rest} {today.year}", "%B %d %Y").date()
    except ValueError:
        return None
    if parsed < today:
        parsed = parsed.replace(year=today.year + 1)
    return parsed


def to_show(artist, href, block, today):
    if not href.startswith("/koncertai/"):
        return None  # not in the concerts category -- see module docstring

    title_match = TITLE_RE.search(block)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""
    if not re.search(rf"\b{re.escape(artist)}\b", title, re.IGNORECASE):
        return None  # search matched something other than the event's own title

    if re.search(r"\bautobusas\b", title, re.IGNORECASE):
        return None  # bus-trip package to the show, not the show itself -- see module docstring

    if re.search(r"\bthe best of\b|\btribute\b", title, re.IGNORECASE):
        return None  # a tribute/cover act, not the real artist -- see module docstring

    date_match = DATE_RE.search(block)
    if not date_match:
        return None
    month_day = date_match.group(1).strip().split(" - ")[0].strip()
    event_date = parse_month_day(month_day, today)
    if not event_date or event_date.isoformat() < today.isoformat():
        return None

    place_match = PLACE_RE.search(block)
    place_text = html.unescape(place_match.group(1).strip()) if place_match else ""
    city, _, venue = place_text.partition(", ")

    return {
        "band": artist,
        "date": event_date.isoformat(),
        "city": city,
        "country": COUNTRY_CODE,
        "venue": venue,
        "fest": "FESTIVAL" if "festival" in title.lower() else None,
        "source": SOURCE_NAME,
        "url": f"{BASE_URL}{href}",
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    init_session()

    artists = load_artists(ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()
    artist_status = common.load_artist_status()
    today = date.today()

    for i, artist in enumerate(artists, 1):
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        print(f"[{i}/{len(artists)}] {artist}")
        html_fragment = search_events(artist)
        for match in CARD_RE.finditer(html_fragment):
            href, block = match.groups()
            show = to_show(artist, href, block, today)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            if not common.confirm_inactive_artist_show(show, artist_status):
                continue
            merged[key] = show

        common.mark_checked(scrape_state, artist, SOURCE_NAME)
        time.sleep(REQUEST_DELAY)

    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
