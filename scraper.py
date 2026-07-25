"""
Scrapes upcoming shows for artists in artists.csv from the Ticketmaster
Discovery API and merges them into shows.json, preserving any
manually-edited fields (flightTLL, flightRIX, note) on entries that
already exist.

Requires a Ticketmaster Discovery API key: https://developer.ticketmaster.com
Put it in a .env file (gitignored) in this directory:

    TICKETMASTER_API_KEY=your-key-here
"""

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("TICKETMASTER_API_KEY")
BASE_URL = "https://app.ticketmaster.com/discovery/v2"
""" ARTISTS_CSV = "artists.csv" """
ARTISTS_CSV = "artists_test.csv"
SHOWS_JSON = "shows.json"
COUNTRIES_JSON = "countries.json"
DIRECT_FLIGHTS_JSON = "direct_flights.json"
CITY_HUB_OVERRIDES_JSON = "city_hub_overrides.json"
REQUEST_DELAY = 0.25  # ~4 req/sec, under the 5 req/sec limit

with open(COUNTRIES_JSON, encoding="utf-8") as f:
    ALLOWED_COUNTRIES = json.load(f).keys()

with open(DIRECT_FLIGHTS_JSON, encoding="utf-8") as f:
    DIRECT_FLIGHTS = json.load(f)["destinations"]

if os.path.exists(CITY_HUB_OVERRIDES_JSON):
    with open(CITY_HUB_OVERRIDES_JSON, encoding="utf-8") as f:
        CITY_HUB_OVERRIDES = json.load(f)
else:
    CITY_HUB_OVERRIDES = {}

NO_DIRECT = {"direct": False, "seasonal": False, "duration_minutes": None}


def flight_info(city, origin):
    override = CITY_HUB_OVERRIDES.get(city, {})
    hub = override.get(f"{origin}_hub", city)
    info = dict(DIRECT_FLIGHTS.get(hub, {}).get(origin, NO_DIRECT))
    forced = override.get(f"{origin}_direct")
    if forced is not None:
        info["direct"] = forced

    # RIX and TLL durations are close enough that we only track one number;
    # always report TLL's duration regardless of which origin this is for.
    tll_hub = override.get("TLL_hub", city)
    info["duration_minutes"] = DIRECT_FLIGHTS.get(tll_hub, {}).get("TLL", NO_DIRECT)["duration_minutes"]

    return info


def load_artists(path):
    with open(path, encoding="utf-8") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]


def api_get(path, params):
    params = dict(params, apikey=API_KEY)
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("  rate limited, backing off 5s", file=sys.stderr)
            time.sleep(5)
            return api_get(path, {k: v for k, v in params.items() if k != "apikey"})
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
    venues = (event.get("_embedded") or {}).get("venues") or [{}]
    venue = venues[0]
    country_code = ((venue.get("country") or {}).get("countryCode")) or ""
    if country_code not in ALLOWED_COUNTRIES:
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
        "source": "Ticketmaster",
        "url": event.get("url") or "",
        "flightTLL": flight_info(city, "TLL"),
        "flightRIX": flight_info(city, "RIX"),
        "note": CITY_HUB_OVERRIDES.get(city, {}).get("note", ""),
    }


SPELLING_VARIANTS = {
    "theatre": "theater",
    "centre": "center",
}


def normalize_venue(name):
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    words = [SPELLING_VARIANTS.get(w, w) for w in name.split()]
    return " ".join(words)


def show_key(show):
    return (show["band"], show["date"], normalize_venue(show["venue"]))


def main():
    if not API_KEY:
        sys.exit("Set the TICKETMASTER_API_KEY environment variable first.")

    artists = load_artists(ARTISTS_CSV)

    if os.path.exists(SHOWS_JSON) and os.path.getsize(SHOWS_JSON) > 0:
        with open(SHOWS_JSON, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = []

    existing_by_key = {show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    for i, artist in enumerate(artists, 1):
        print(f"[{i}/{len(artists)}] {artist}")
        attraction_id = find_attraction_id(artist)
        time.sleep(REQUEST_DELAY)
        if not attraction_id:
            continue

        for event in fetch_events(attraction_id):
            show = to_show(artist, event)
            if show is None:
                continue
            key = show_key(show)
            if key in existing_by_key:
                continue
            merged[key] = show
        time.sleep(REQUEST_DELAY)

    today = date.today().isoformat()
    result = sorted((s for s in merged.values() if s["date"] >= today), key=lambda s: (s["date"], s["band"]))

    with open(SHOWS_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
