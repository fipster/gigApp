"""
Scrapes upcoming shows for artists in artists.csv from Songkick's public
website and merges them into shows.json.

Songkick's own developer API is closed to hobbyist/educational use (a
$500/month license fee as of 2026), so this instead uses the public
songkick.com website itself: an artist search page
(/search?query=...&type=artists) and, for the matched artist, its
calendar page (/artists/{id}-{slug}/calendar). Both are plain HTML, not
blocked by any bot-detection -- confirmed working with a default
urllib request, no special headers needed beyond a normal User-Agent.

Each event on the calendar page embeds a full schema.org MusicEvent as
JSON-LD (<script type="application/ld+json"> inside a "microformat"
div), which is far cleaner to parse than the surrounding HTML: exact
start date/time, venue name, city, and an already-ISO country code.

This script only fetches show data -- it does not compute flight info.
Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.

Known limitation: the search endpoint returns many near-matches
(misspellings, tribute acts, unrelated artists sharing a word) mixed in
with the real artist -- e.g. searching "Metallica" also returns
"Meatallica", "Metallica Reloaded", and several tribute bands. A result
is only used if its name matches the artist exactly (case-insensitive),
same principle as the other scrapers, just applied to picking the right
search result instead of filtering event titles. If no exact match is
found, the artist is skipped rather than guessing at a near-match.

No documented rate limit for the public website (unlike the official
API); REQUEST_DELAY is a conservative self-imposed pause regardless.

Known limitation: the JSON-LD addressCountry field is inconsistent --
sometimes a 2-letter ISO code ("US"), sometimes a full English country
name ("Latvia", "Lithuania"), and sometimes "UK" (neither the ISO code
"GB" nor the full name "United Kingdom"). resolve_country() handles all
three via the same countries.json name lookup scrape_bandsintown.py
uses plus an explicit "UK" alias, but a country in some other/localized
form would still fall through and silently exclude an otherwise-
legitimate show.
"""

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date

import shows_common as common

# Songkick's JSON-LD addressCountry is inconsistent -- sometimes a 2-letter
# ISO code (e.g. "US"), sometimes a full English country name (e.g.
# "Latvia", "Lithuania"). Build a name -> code lookup from countries.json
# (same approach as scrape_bandsintown.py) to handle both.
with open(common.COUNTRIES_JSON, encoding="utf-8") as f:
    _countries_data = json.load(f)
COUNTRY_NAME_TO_CODE = {info["name"].lower(): code for code, info in _countries_data.items()}
# Songkick uses "UK" for the United Kingdom, not the ISO code "GB" and not
# the full name either -- add it as an explicit alias rather than guessing
# at other possible abbreviations.
COUNTRY_NAME_TO_CODE["uk"] = "GB"

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://www.songkick.com"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Songkick"
REQUEST_DELAY = 0.8

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}

SEARCH_RESULT_RE = re.compile(
    r'<a href="(/artists/(\d+)-[^"]*)"[^>]*class="search-link"><strong>([^<]*)</strong></a>\s*</p>\s*([\d,]+) upcoming',
    re.DOTALL,
)
MICROFORMAT_RE = re.compile(r'<div class="microformat">\s*<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


def load_artists(path):
    with open(path, encoding="utf-8") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]


def fetch(url):
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as resp:
        return resp.read().decode("utf-8")


def find_artist(artist):
    """Returns the matched artist's href (e.g. /artists/331163-metallica), or None."""
    params = {"query": artist, "type": "artists"}
    url = f"{BASE_URL}/search?{urllib.parse.urlencode(params)}"
    try:
        html = fetch(url)
    except Exception as e:
        print(f"  error searching for {artist}: {e}", file=sys.stderr)
        return None

    for m in SEARCH_RESULT_RE.finditer(html):
        href, artist_id, name, count = m.groups()
        if name.strip().lower() == artist.strip().lower():
            return href
    return None


def fetch_calendar_events(artist_href):
    url = f"{BASE_URL}{artist_href}/calendar"
    try:
        html = fetch(url)
    except Exception as e:
        print(f"  error fetching calendar {artist_href}: {e}", file=sys.stderr)
        return []

    events = []
    for m in MICROFORMAT_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        events.extend(data if isinstance(data, list) else [data])
    return events


def resolve_country(raw_country):
    raw_country = (raw_country or "").strip()
    if not raw_country:
        return None
    if raw_country.upper() in common.ALLOWED_COUNTRIES:
        return raw_country.upper()
    return COUNTRY_NAME_TO_CODE.get(raw_country.lower())


def to_show(artist, event):
    location = event.get("location") or {}
    address = location.get("address") or {}
    country_code = resolve_country(address.get("addressCountry"))
    if not country_code or country_code not in common.ALLOWED_COUNTRIES:
        return None

    start = event.get("startDate") or ""
    event_date = start[:10]
    if not event_date or event_date < date.today().isoformat():
        return None

    name = event.get("name") or ""

    return {
        "band": artist,
        "date": event_date,
        "city": address.get("addressLocality") or "",
        "country": country_code,
        "venue": location.get("name") or "",
        "fest": "FESTIVAL" if "festival" in name.lower() else None,
        "source": SOURCE_NAME,
        "url": (event.get("url") or "").split("?")[0],
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
    artist_status = common.load_artist_status()

    for i, artist in enumerate(artists, 1):
        if common.is_inactive(artist_status, artist):
            print(f"[{i}/{len(artists)}] {artist} — skipped, marked inactive")
            continue
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        print(f"[{i}/{len(artists)}] {artist}")
        artist_href = find_artist(artist)
        time.sleep(REQUEST_DELAY)
        if artist_href:
            for event in fetch_calendar_events(artist_href):
                show = to_show(artist, event)
                if show is None:
                    continue
                key = common.show_key(show)
                if key in existing_by_key:
                    continue
                merged[key] = show
            time.sleep(REQUEST_DELAY)

        common.mark_checked(scrape_state, artist, SOURCE_NAME)

    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
