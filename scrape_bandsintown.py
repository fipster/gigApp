"""
Scrapes upcoming shows for artists in artists.csv from Bandsintown, via the
Apify "Bandsintown Scraper" actor (hoholabs/bandsintown-scraper), and merges
them into shows.json.

This script only fetches show data (band, date, city, venue, url, ...) --
it does not compute flight info. Run enrich_flights.py afterward to fill
in flightTLL/flightRIX/note for any shows missing them.

Requires an Apify API token: https://apify.com
Put it in a .env file (gitignored) in this directory:

    APIFY_API_TOKEN=your-token-here

Known limitation: Bandsintown sometimes returns venue city/country in a
localized script (e.g. a Japanese venue returning "日本" instead of
"Japan") rather than consistent English. The country-name match below only
recognizes English names (from countries.json), so a localized country
name that doesn't match falls through and the show is excluded -- same
fail-safe behavior as an unrecognized country, just worth knowing this can
silently drop a legitimate in-region show if Bandsintown localizes its
name. Spot-check results after a run if a show you expect is missing.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date

from dotenv import load_dotenv

import shows_common as common

load_dotenv()

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_TOKEN = os.environ.get("APIFY_API_TOKEN")
ACTOR_ID = "hoholabs~bandsintown-scraper"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Bandsintown"
REQUEST_DELAY = 0.5

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}

# reverse lookup (English country name -> ISO code), built from the same
# countries.json the region allowlist itself comes from
with open(common.COUNTRIES_JSON, encoding="utf-8") as f:
    _countries_data = json.load(f)
COUNTRY_NAME_TO_CODE = {info["name"].lower(): code for code, info in _countries_data.items()}


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


def run_status_message(run_id):
    url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={API_TOKEN}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", {}).get("statusMessage") or ""
    except Exception:
        return ""


def call_actor(artist):
    """Returns (events, not_found). events is None if a real error occurred
    (network/HTTP/etc) -- distinct from events=[] (a successful call that
    confirmed zero upcoming shows) -- so the caller can skip mark_checked
    and let this artist be retried next run instead of silently caching the
    error as "checked, nothing found" for SKIP_IF_CHECKED_WITHIN_DAYS. not_found
    is True only when the actor confirms the artist doesn't exist on
    Bandsintown at all -- distinct from the artist existing but simply
    having no upcoming shows right now."""
    url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items?token={API_TOKEN}"
    body = json.dumps({"artist": artist, "queryType": "events", "date": "upcoming"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8")), False
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "ignore")
        run_match = re.search(r"run ID: (\w+)", body_text)
        if run_match and run_status_message(run_match.group(1)).startswith("Artist not found"):
            return [], True
        print(f"  HTTP {e.code} for {artist}: {body_text[:200]}", file=sys.stderr)
        return None, False
    except Exception as e:
        print(f"  error for {artist}: {e}", file=sys.stderr)
        return None, False


def to_show(artist, event):
    venue = event.get("venue") or {}
    country_name = (venue.get("country") or "").strip()
    country_code = COUNTRY_NAME_TO_CODE.get(country_name.lower())
    if not country_code or country_code not in common.ALLOWED_COUNTRIES:
        return None

    event_date = (event.get("datetime") or "")[:10]
    if not event_date or event_date < date.today().isoformat():
        return None

    lineup = event.get("lineup") or []
    title = event.get("title") or ""
    is_festival = len(lineup) > 3 or "festival" in title.lower()

    return {
        "band": artist,
        "date": event_date,
        "city": venue.get("city") or "",
        "country": country_code,
        "venue": venue.get("name") or "",
        "fest": "FESTIVAL" if is_festival else None,
        "source": SOURCE_NAME,
        "url": event.get("url") or "",
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    if not API_TOKEN:
        sys.exit("Set the APIFY_API_TOKEN environment variable first.")

    artists = load_artists(ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()
    artist_status = common.load_artist_status()

    for i, artist in enumerate(artists, 1):
        # unlike the free scrapers, Bandsintown is paid per call (see
        # run_scrapers.py) -- an inactive artist reuniting there specifically
        # is a narrow enough case that it's not worth paying to search for,
        # so this one keeps skipping inactive artists outright
        if common.is_inactive(artist_status, artist):
            print(f"[{i}/{len(artists)}] {artist} — skipped, marked inactive")
            continue
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        events, not_found = call_actor(artist)
        if not_found:
            print(f"[{i}/{len(artists)}] {artist} — not found, won't retry")
            common.mark_not_found(scrape_state, artist, SOURCE_NAME)
            time.sleep(REQUEST_DELAY)
            continue
        if events is None:
            print(f"[{i}/{len(artists)}] {artist} — error, will retry next run")
            time.sleep(REQUEST_DELAY)
            continue

        new_count = 0
        for event in events:
            if event.get("type") != "event":
                continue
            show = to_show(artist, event)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            merged[key] = show
            new_count += 1
        print(f"[{i}/{len(artists)}] {artist} — {new_count} new show(s)")

        common.mark_checked(scrape_state, artist, SOURCE_NAME)
        time.sleep(REQUEST_DELAY)

    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
