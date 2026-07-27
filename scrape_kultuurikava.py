"""
Scrapes upcoming shows for artists in artists.csv from Kultuurikava.ee's
JSON API and merges them into shows.json.

Kultuurikava.ee is an Estonia-wide event listing site (not limited to
Tartu). There is no official public API documentation -- this endpoint
and its parameters were reverse-engineered from the site's own frontend
JS bundle (static/js/main.*.js). The `token` below is embedded in that
public bundle and used by every visitor's browser; it is not a secret
credential, just an unpublished API. Because the whole site only covers
Estonia, every result is confidently "EE" -- unlike Fienta, there's no
country-resolution ambiguity here.

This script only fetches show data -- it does not compute flight info.
Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.

No rate limit is documented for this endpoint; REQUEST_DELAY is a
conservative self-imposed pause between requests regardless.

Known limitation: the `sw` (search word) parameter matches against
event name, excerpt, AND description -- not a dedicated artist field.
As with Fienta, a result is only kept if the artist name appears in the
event's own NAME as a whole word, to filter out matches that only hit
the description text.

Known limitation: a single event on Kultuurikava can bundle an entire
tour's worth of stops into one `times` array (each with its own date,
venue, and city) sharing one event page URL. Each entry in `times`
becomes its own show here; all of them link back to that same shared
event URL since there's no per-date ticket link in the API response.

Known limitation: some `times` entries have city="Muu" ("other" in
Estonian) instead of a real city name, when Kultuurikava's own venue
data isn't classified. These are kept with an empty city rather than
guessed at, since city drives the flight-hub lookup in enrich_flights.py.

Known limitation: the same real-world date can appear from more than
one raw event object -- Kultuurikava returns both a single "tour" event
whose `times` array bundles every stop, AND separate one-off event
objects that mirror individual stops from that same tour, sometimes
with a differently-formatted venue name and/or a populated city where
the bundled version left it blank (e.g. venue "Tartu Antoniuse õu" with
no city vs. venue "Antoniuse õu" with city "Tartu" for the same date).
Since shows_common.show_key() dedupes on (band, date, city, country) --
deliberately not venue, see shows_common.py -- occurrences here are
deduped by date alone per artist (preferring whichever representation
has a non-empty city) before being turned into show entries. A touring
artist playing two different Estonian shows on the same calendar day
is not a real scenario this needs to handle.
"""

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

import shows_common as common

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://www.kultuurikava.ee/api/"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Kultuurikava"
REQUEST_DELAY = 0.5

# public token embedded in kultuurikava.ee's own frontend JS bundle -- see module docstring
API_TOKEN = "1499ddfb3cb59057a9f201a4e6faf6fe68bde294"

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}

COUNTRY_CODE = "EE"  # whole site is Estonia-only, see module docstring


def load_artists(path):
    with open(path, encoding="utf-8") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]


def _last_sunday(year, month):
    d = date(year, month, 31)
    while d.weekday() != 6:  # Monday=0 .. Sunday=6
        d -= timedelta(days=1)
    return d


def _estonia_utc_offset_hours(dt_utc):
    # EU DST rule: EEST (UTC+3) from last Sunday of March 01:00 UTC to
    # last Sunday of October 01:00 UTC; EET (UTC+2) otherwise. Implemented
    # by hand since Windows Python here has no tzdata package for zoneinfo.
    year = dt_utc.year
    dst_start = datetime.combine(_last_sunday(year, 3), datetime.min.time()) + timedelta(hours=1)
    dst_end = datetime.combine(_last_sunday(year, 10), datetime.min.time()) + timedelta(hours=1)
    return 3 if dst_start <= dt_utc < dst_end else 2


def unix_to_estonian_date(ts):
    dt_utc = datetime.utcfromtimestamp(ts)
    offset = _estonia_utc_offset_hours(dt_utc)
    return (dt_utc + timedelta(hours=offset)).date().isoformat()


def search_events(artist):
    params = {
        "do": "events",
        "token": API_TOKEN,
        "lang": "nat",
        "sw": artist,
        "order": "starta",
        "start": 0,
        "limit": 50,
        "format": "json",
        "showall": "false",
        "alltimes": "true",
        "ignoremuuseums": "true",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("events", {}).get("results") or []
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {artist}: {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  error for {artist}: {e}", file=sys.stderr)
        return []


def collect_occurrences(artist, events):
    """Flattens every matching event's `times` entries across all raw
    events returned for this artist, deduping by date alone since the
    same real-world date can appear in more than one raw event object
    with a differently-formatted venue/city (see module docstring)."""
    best = {}
    today_iso = date.today().isoformat()
    for event in events:
        name = event.get("name") or ""
        if not re.search(rf"\b{re.escape(artist)}\b", name, re.IGNORECASE):
            continue  # sw= matched excerpt/description text, not the event's own name

        is_festival = "festival" in name.lower()
        event_url = event.get("url") or ""
        for occurrence in event.get("times") or []:
            start_time = occurrence.get("start_time")
            if not start_time:
                continue
            event_date = unix_to_estonian_date(start_time)
            if event_date < today_iso:
                continue

            venue = occurrence.get("place_name") or ""
            city = occurrence.get("city") or ""
            if city.strip().lower() == "muu":
                city = ""

            current = best.get(event_date)
            if current is None or (not current["city"] and city):
                best[event_date] = {"date": event_date, "city": city, "venue": venue, "url": event_url, "fest": is_festival}
    return list(best.values())


def to_show(artist, occurrence):
    return {
        "band": artist,
        "date": occurrence["date"],
        "city": occurrence["city"],
        "country": COUNTRY_CODE,
        "venue": occurrence["venue"],
        "fest": "FESTIVAL" if occurrence["fest"] else None,
        "source": SOURCE_NAME,
        "url": occurrence["url"],
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    artists = load_artists(ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()

    for i, artist in enumerate(artists, 1):
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        print(f"[{i}/{len(artists)}] {artist}")
        events = search_events(artist)
        for occurrence in collect_occurrences(artist, events):
            show = to_show(artist, occurrence)
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            merged[key] = show

        common.mark_checked(scrape_state, artist, SOURCE_NAME)
        time.sleep(REQUEST_DELAY)

    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
