"""
Shared utilities for source-specific scrapers (scrape_ticketmaster.py, and
any future scrape_<source>.py). Handles the parts every source needs the
same way: the region allowlist, show deduplication, and loading/saving
shows.json (with past-date pruning). Flight enrichment is handled
separately by enrich_flights.py — it's source-agnostic and doesn't belong
in any one scraper.
"""

import json
import os
from datetime import date, timedelta

SHOWS_JSON = "shows.json"
COUNTRIES_JSON = "countries.json"
SCRAPE_STATE_JSON = "scrape_state.json"
SKIP_IF_CHECKED_WITHIN_DAYS = 14

with open(COUNTRIES_JSON, encoding="utf-8") as f:
    ALLOWED_COUNTRIES = json.load(f).keys()

def show_key(show):
    # dedupe on band+date+city+country rather than venue name: different
    # sources sometimes describe the "venue" differently for the same real
    # event (e.g. Ticketmaster reports the physical venue, Bandsintown
    # reports the festival name), so matching on venue text misses
    # cross-source duplicates. A band playing two different venues in the
    # same city on the same day is rare enough to accept as a tradeoff.
    return (show["band"], show["date"], show["city"].strip().lower(), show["country"])


def load_shows(path=SHOWS_JSON):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_shows(shows, path=SHOWS_JSON):
    today = date.today().isoformat()
    result = sorted((s for s in shows if s["date"] >= today), key=lambda s: (s["date"], s["band"]))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return result


def load_scrape_state(path=SCRAPE_STATE_JSON):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_scrape_state(scrape_state, path=SCRAPE_STATE_JSON):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scrape_state, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


NOT_FOUND = "not_found"


def already_checked_recently(scrape_state, artist, source_name, within_days=SKIP_IF_CHECKED_WITHIN_DAYS):
    last_checked = scrape_state.get(artist, {}).get(source_name)
    if not last_checked:
        return False
    if last_checked == NOT_FOUND:
        return True
    cutoff = date.today() - timedelta(days=within_days)
    return date.fromisoformat(last_checked) > cutoff


def mark_checked(scrape_state, artist, source_name):
    scrape_state.setdefault(artist, {})[source_name] = date.today().isoformat()


def mark_not_found(scrape_state, artist, source_name):
    # permanent skip, unlike mark_checked's normal within_days recheck window --
    # an artist confirmed absent from a source won't suddenly appear tomorrow
    scrape_state.setdefault(artist, {})[source_name] = NOT_FOUND
