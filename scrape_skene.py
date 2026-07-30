"""
Scrapes upcoming shows for artists in artists.csv from skene.info's
public JSON API -- an Estonian "alternative" music events aggregator
(concerts, festivals, club nights, curated by hand/community
submission) -- and merges them into shows.json.

Unlike the other scrapers, this doesn't do a per-artist search request:
skene.info's API returns its whole current event list in one call
(https://www.skene.info/api/events.json, self-documented in its own
"_meta" block), so this fetches once and matches every artists.csv
artist against each event's performer list (the "b" field) locally --
much cheaper than a per-artist request loop, and there's no
already-checked-recently throttle here since there's no per-artist
network cost to throttle.

This script only fetches show data -- it does not compute flight info.
Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.

Only "kontsert" (concert) and "festival" event types are used --
"klubi" (club night), "reliis" (release) and "merch" entries are
skipped, since those aren't tour dates. Entries flagged "tba" (lineup
still being finalized) are also skipped rather than trusted.

Country resolution: an event's "c" field is "Tallinn" or "Tartu" for
Estonia-domestic shows. For anything else (a region label like
"Euroopa"/"Põhjamaad"), the actual city+country is in the free-text
"linn" field as "City, Country" in Estonian (e.g. "Bergen, Norra") --
ESTONIAN_COUNTRY_NAMES maps the country part to an ISO code. An event
whose country can't be confidently resolved this way is skipped rather
than guessed at, same principle as the other scrapers' country
fallbacks (e.g. scrape_fienta.py's address parsing).
"""

import csv
import json
import sys
import urllib.request
from datetime import date

import shows_common as common

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://www.skene.info/api/events.json"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Skene"
REQUEST_HEADERS = {"User-Agent": "gigApp/1.0 (personal project, contact: vipp@crimson.ee)"}

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}

EVENT_TYPES = {"kontsert", "festival"}

# skene.info's free-text "linn" field gives country names in Estonian
# (e.g. "Bergen, Norra") -- only names plausible for an Estonian band's
# touring reach are covered here; an unrecognized name means the show
# is skipped rather than guessed at.
ESTONIAN_COUNTRY_NAMES = {
    "eesti": "EE", "soome": "FI", "rootsi": "SE", "norra": "NO", "taani": "DK",
    "läti": "LV", "leedu": "LT", "saksamaa": "DE", "poola": "PL", "venemaa": "RU",
    "holland": "NL", "madalmaad": "NL", "belgia": "BE", "prantsusmaa": "FR",
    "suurbritannia": "GB", "inglismaa": "GB", "itaalia": "IT", "hispaania": "ES",
    "austria": "AT", "šveits": "CH", "tšehhi": "CZ", "ungari": "HU",
    "iirimaa": "IE", "portugal": "PT", "kreeka": "GR", "island": "IS",
    "sloveenia": "SI", "horvaatia": "HR",
}


def load_artists(path):
    # 3rd column marks an artist deliberately excluded (e.g. not in the
    # current source playlist); 4th flags a band confirmed inactive/
    # disbanded -- either one skips the row entirely
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header row: artist_name,playlist,active,ignore
        return [row[0].strip() for row in reader if row and row[0].strip()
                and (len(row) < 3 or row[2].strip().lower() != "false")
                and (len(row) < 4 or row[3].strip().lower() != "true")]


def fetch_events():
    request = urllib.request.Request(BASE_URL, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("entries") or []


def resolve_location(entry):
    city_category = entry.get("c") or ""
    if city_category in ("Tallinn", "Tartu"):
        return city_category, "EE"

    linn = entry.get("linn") or ""
    if "," not in linn:
        return None, None
    city_part, _, country_part = linn.rpartition(",")
    country_code = ESTONIAN_COUNTRY_NAMES.get(country_part.strip().lower())
    if not country_code:
        return None, None
    return city_part.strip(), country_code


def to_show(artist, entry):
    if entry.get("tba"):
        return None  # lineup/details not finalized yet -- don't trust it

    event_date = entry.get("d") or ""
    if not event_date or event_date < date.today().isoformat():
        return None

    city, country_code = resolve_location(entry)
    if not city or not country_code or country_code not in common.ALLOWED_COUNTRIES:
        return None

    url = entry.get("ou") or entry.get("pu") or entry.get("su") or ""

    return {
        "band": artist,
        "date": event_date,
        "city": city,
        "country": country_code,
        "venue": entry.get("v") or "",
        "fest": "FESTIVAL" if entry.get("t") == "festival" else None,
        "source": SOURCE_NAME,
        "url": url,
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    artists = load_artists(ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)
    artist_status = common.load_artist_status()

    try:
        entries = fetch_events()
    except Exception as e:
        # log and bail out of *this* scraper only -- run_scrapers.py runs
        # several sources plus a paid one (scrape_bandsintown.py) after
        # this one in FREE_SCRAPERS, and a transient skene.info hiccup
        # shouldn't take those down with it (sys.exit() previously did)
        print(f"error fetching skene.info events: {e}", file=sys.stderr)
        return

    events = [e for e in entries if e.get("t") in EVENT_TYPES]
    print(f"fetched {len(entries)} entries ({len(events)} concerts/festivals)")

    # index events by performer name (lowercased) for a direct artist lookup,
    # since this API returns everything in one call rather than per-artist search
    by_performer = {}
    for entry in events:
        for performer in entry.get("b") or []:
            by_performer.setdefault(performer.strip().lower(), []).append(entry)

    matched = 0
    for i, artist in enumerate(artists, 1):
        new_count = 0
        for entry in by_performer.get(artist.strip().lower(), []):
            show = to_show(artist, entry)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            if not common.confirm_inactive_artist_show(show, artist_status):
                continue
            merged[key] = show
            new_count += 1
        if new_count:
            matched += 1
            print(f"[{i}/{len(artists)}] {artist} — {new_count} new show(s)")

    result = common.save_shows(list(merged.values()))
    print(f"\nDone. {matched} artists matched. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
