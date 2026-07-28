"""
Scrapes upcoming shows for artists in artists.csv from the Ticketmaster
Discovery API and merges them into shows.json, preserving any
manually-edited fields on entries that already exist.

This script only fetches show data (band, date, city, venue, url, ...) —
it does not compute flight info. Run enrich_flights.py afterward to fill
in flightTLL/flightRIX/note for any shows missing them.

Requires a Ticketmaster Discovery API key: https://developer.ticketmaster.com
Put it in a .env file (gitignored) in this directory:

    TICKETMASTER_API_KEY=your-key-here

Each event carries a dates.status.code ("onsale", "offsale", "cancelled",
"postponed", "rescheduled", ...); cancelled/postponed events are skipped,
otherwise a cancelled show would get imported as if it were a real,
bookable date (found via a *CANCELLED* Crowbar @ Wrocław listing that
had made it into shows.json). This filter only applies going forward --
a re-scrape is what actually removes an already-imported cancelled show,
not this fix by itself.
"""

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timezone

from dotenv import load_dotenv

import shows_common as common

load_dotenv()

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_KEY = os.environ.get("TICKETMASTER_API_KEY")
BASE_URL = "https://app.ticketmaster.com/discovery/v2"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Ticketmaster"
REQUEST_DELAY = 0.25  # ~4 req/sec, under the 5 req/sec limit
MAX_RATE_LIMIT_RETRIES = 3

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}


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


def api_get(path, params, retries=MAX_RATE_LIMIT_RETRIES):
    params = dict(params, apikey=API_KEY)
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            available = e.headers.get("Rate-Limit-Available")
            if retries <= 0 or available == "0":
                reset_ms = e.headers.get("Rate-Limit-Reset")
                reset_msg = ""
                if reset_ms:
                    reset_time = datetime.fromtimestamp(int(reset_ms) / 1000, timezone.utc)
                    reset_msg = f" Quota resets at {reset_time.isoformat()}."
                sys.exit(f"Ticketmaster daily quota exhausted.{reset_msg} Aborting rather than burning through every remaining artist with failed requests.")
            print(f"  rate limited, backing off 5s ({retries} retries left)", file=sys.stderr)
            time.sleep(5)
            return api_get(path, params, retries=retries - 1)
        print(f"  HTTP {e.code} on {path}: {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  error on {path}: {e}", file=sys.stderr)
        return {}


def find_attraction_id(artist):
    data = api_get("attractions.json", {"keyword": artist, "size": 10})
    attractions = (data.get("_embedded") or {}).get("attractions") or []
    for a in attractions:
        if a.get("name", "").strip().lower() == artist.strip().lower():
            return a["id"]
    return None


def fetch_events(attraction_id):
    events = []
    page = 0
    while True:
        data = api_get("events.json", {"attractionId": attraction_id, "size": 200, "page": page})
        page_events = (data.get("_embedded") or {}).get("events") or []
        events.extend(page_events)
        total_pages = (data.get("page") or {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
        time.sleep(REQUEST_DELAY)
    return events


def to_show(artist, event):
    # dates.status.code is "onsale"/"offsale"/"cancelled"/"postponed"/"rescheduled";
    # only a still-on-sale-or-not-yet-onsale date is a real, bookable show --
    # a cancelled/postponed one would otherwise show up as a phantom show.
    status_code = ((event.get("dates") or {}).get("status") or {}).get("code") or ""
    if status_code in ("cancelled", "postponed"):
        return None

    venues = (event.get("_embedded") or {}).get("venues") or [{}]
    venue = venues[0]
    country_code = ((venue.get("country") or {}).get("countryCode")) or ""
    if country_code not in common.ALLOWED_COUNTRIES:
        return None

    event_date = ((event.get("dates") or {}).get("start") or {}).get("localDate") or ""
    if not event_date or event_date < date.today().isoformat():
        return None

    attractions = (event.get("_embedded") or {}).get("attractions") or []
    is_festival = len(attractions) > 3 or "festival" in event.get("name", "").lower()

    city = venue.get("city", {}).get("name") or ""

    return {
        "band": artist,
        "date": event_date,
        "city": city,
        "country": country_code,
        "venue": venue.get("name") or "",
        "fest": "FESTIVAL" if is_festival else None,
        "source": SOURCE_NAME,
        "url": event.get("url") or "",
        # placeholders — run enrich_flights.py to fill these in properly
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    if not API_KEY:
        sys.exit("Set the TICKETMASTER_API_KEY environment variable first.")

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
        attraction_id = find_attraction_id(artist)
        time.sleep(REQUEST_DELAY)
        if attraction_id:
            for event in fetch_events(attraction_id):
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
