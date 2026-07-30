"""
Scrapes upcoming shows for artists in artists.csv from Fienta's public
events API and merges them into shows.json.

The search query itself is NOT restricted by country -- it searches
across all of Fienta's coverage (Fienta supports organizers in ~46
countries, not just Estonia), so an artist with a Fienta-ticketed show
anywhere can be found. However, Fienta's event objects have no
separate country field, only a free-text address (e.g. "Vana-Kalamaja
tänav 1, 10412 Tallinn, Harju maakond" or, for a non-Estonian example,
"1200-243 Lisboa, Lisboa" -- note neither includes an explicit country
name). The only address pattern reliably recognized here is Estonia's
"<postcode> <city>, <region> maakond" convention; results whose address
doesn't match it are excluded rather than guessed at, since we can't
confidently confirm which country they're in. This means non-Estonian
Fienta results are effectively dropped for now, even though the search
itself does surface them -- worth revisiting with more per-country
address patterns if broader Fienta coverage is wanted later.

This script only fetches show data -- it does not compute flight info.
Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.

No API key needed: Fienta's public events API
(https://fienta.com/api/v1/public/events) is fully open, no
authentication required. Documented at https://fienta.com/help/api.
Rate limited to 80 requests/minute per IP -- REQUEST_DELAY keeps us
comfortably under that.

Known limitation: the `search` parameter matches against event title,
description, AND venue name -- not a dedicated artist/performer field.
A short or common-word artist name (e.g. an Estonian word that also
appears in unrelated titles/descriptions) can produce false positives
from the API itself. To filter these out, a result is only kept if the
artist name appears in the event's own TITLE as a whole word -- but
residual noise is still possible for very short/common names sharing a
word with an unrelated event title.

In practice this residual noise is common, not rare: a manual pass over
new Fienta-sourced shows.json entries after each run is expected every
time, not just a one-off cleanup -- checked ~22 of 25 were false
positives (wrong person/venue/event-branding matches, or tribute/cover
nights) on 2026-07-28. Don't treat a clean-looking run as reason to skip
the manual check.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date

import shows_common as common

# some artist names contain characters the default Windows console codepage can't print
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://fienta.com/api/v1/public/events"
ARTISTS_CSV = "artists.csv"
SOURCE_NAME = "Fienta"
REQUEST_DELAY = 0.8  # 80 req/min limit -> stay comfortably under it

# Estonia's addresses reliably end in "<region> maakond" (county); this is the
# only country we can confidently recognize from Fienta's free-text address,
# so it's the only one we resolve to an ISO code -- see module docstring.
ESTONIA_ADDRESS_PATTERN = re.compile(r"\bmaakond\b", re.IGNORECASE)


def search_events(artist):
    """Returns a list of events, or None if the request failed -- the
    caller must treat None as "retry next run", not "confirmed zero
    results", so a transient error doesn't get cached as a clean check."""
    params = {
        "search": artist,
        "locale": "en",
        "per_page": 50,
        "starts_from": date.today().isoformat(),
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    # Fienta's Cloudflare WAF returns 403 (error 1010) for the default
    # "Python-urllib/x.y" User-Agent; any normal browser-style UA passes.
    request = urllib.request.Request(url, headers=common.REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("events") or []
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {artist}: {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  error for {artist}: {e}", file=sys.stderr)
        return None


def extract_city(address):
    match = re.search(r"\d{4,6}\s+([^,]+)", address or "")
    return match.group(1).strip() if match else ""


def resolve_country(address):
    if ESTONIA_ADDRESS_PATTERN.search(address or ""):
        return "EE"
    return None


def to_show(artist, event):
    title = event.get("title") or ""
    if not re.search(rf"\b{re.escape(artist)}\b", title, re.IGNORECASE):
        return None  # search matched venue/description text, not the artist itself

    country_code = resolve_country(event.get("address"))
    if not country_code or country_code not in common.ALLOWED_COUNTRIES:
        return None

    event_date = (event.get("starts_at") or "")[:10]
    if not event_date or event_date < date.today().isoformat():
        return None

    return {
        "band": artist,
        "date": event_date,
        "city": extract_city(event.get("address")),
        "country": country_code,
        "venue": event.get("venue") or "",
        "fest": "FESTIVAL" if "festival" in title.lower() else None,
        "source": SOURCE_NAME,
        "url": event.get("url") or "",
        "flightTLL": dict(common.NO_FLIGHT_INFO),
        "flightRIX": dict(common.NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    artists = common.load_artists(ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()
    artist_status = common.load_artist_status()

    for i, artist in enumerate(artists, 1):
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        events = search_events(artist)
        if events is None:
            print(f"[{i}/{len(artists)}] {artist} — error, will retry next run", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
            continue

        new_count = 0
        for event in events:
            show = to_show(artist, event)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            if not common.confirm_inactive_artist_show(show, artist_status):
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
