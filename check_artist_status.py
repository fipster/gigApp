"""
Checks each artist in artists.csv against MusicBrainz's public API to
find out whether they're disbanded/deceased ("ended", in MusicBrainz's
terms), and writes the result to artist_status.json.

Two-tier system, both of which start from this file's output:
  - artists.csv's own "ignore" column (a permanent, manually-set hard
    skip -- see load_artists()) means a scrape_*.py never even attempts
    that artist.
  - artist_status.json's "active" flag (this script's output) is softer:
    scrape_bandsintown.py skips searching for an inactive artist outright
    (see shows_common.is_inactive()) since it's a paid-per-call source,
    but every other scrape_*.py still searches and only gates a *found*
    show through shows_common.confirm_inactive_artist_show() -- an
    inactive artist reuniting is rare enough that a free source doesn't
    need to skip the search entirely, just double-check before trusting
    a hit.

MusicBrainz is a free, open, contributor-maintained artist database
with no bot-detection at all -- unlike the ticket sites, this is a
completely different (much friendlier) class of API. It does ask
unauthenticated clients to stay at or under 1 request/second and to
send a descriptive User-Agent identifying the application, which this
script does (see USER_AGENT below). Docs:
https://musicbrainz.org/doc/MusicBrainz_API

Coverage/accuracy caveat: MusicBrainz's "ended" flag depends on
contributor edits, so it's not perfectly complete or current -- some
genuinely disbanded acts won't be marked, and a marked-ended act could
reunite later. To avoid confidently mislabeling an ambiguous case (e.g.
a common word/name matching an unrelated artist entry), a result is
only trusted when the top match's search score is high (>=90) and its
name matches the query case-insensitively -- anything less ambiguous
is left alone (treated as active/unknown) rather than guessed at.

artist_status.json entries look like:
  {"active": false, "source": "musicbrainz", "checked": "2026-07-27"}

Entries with "source": "manual" are never overwritten by this script --
edit the file by hand (set "active" and change "source" to "manual")
to correct a case MusicBrainz got wrong, and reruns will leave it alone.

Like the scrapers, a per-artist recheck is throttled via scrape_state.json
(shared file, own "MusicBrainz" key) so a rerun only re-queries artists
not already confirmed within the window -- this used to re-query
essentially all ~900 artists every single run (~20+ minutes, almost all
of it reconfirming already-known, rarely-changing facts). This only
applies to a *resolved* check (active is not None) -- an artist MusicBrainz
couldn't confidently match stays unthrottled, so it's retried every run
rather than waiting out the window for a result that was never cached in
the first place.
"""

import json
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

ARTISTS_CSV = "artists.csv"
STATUS_JSON = "artist_status.json"
BASE_URL = "https://musicbrainz.org/ws/2/artist/"
REQUEST_DELAY = 1.5  # MusicBrainz asks unauthenticated clients to stay at/under 1 req/sec -- a bit of margin since shared-IP traffic can trip their limiter sooner than that
SOURCE_NAME = "MusicBrainz"

# MusicBrainz requires a descriptive User-Agent identifying the application for unauthenticated use
USER_AGENT = "gigApp/1.0 (+https://github.com/fipster/gigApp)"

MIN_SCORE = 90  # only trust a match this confident -- see module docstring


def load_status(path=STATUS_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_status(status, path=STATUS_JSON):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


MAX_BUSY_RETRIES = 4
BUSY_BACKOFF_SECONDS = 5


def search_artist(name, retries=MAX_BUSY_RETRIES):
    query = f'artist:"{name}"'
    params = {"query": query, "fmt": "json", "limit": 5}
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 503 and retries > 0:
            # MusicBrainz returns 503 for "busy" (often really a rate-limit
            # signal, e.g. from shared-IP traffic) -- back off and retry
            # rather than giving up on the artist immediately
            print(f"  MusicBrainz busy for {name}, backing off {BUSY_BACKOFF_SECONDS}s ({retries} retries left)", file=sys.stderr)
            time.sleep(BUSY_BACKOFF_SECONDS)
            return search_artist(name, retries=retries - 1)
        print(f"  HTTP {e.code} for {name}: {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  error for {name}: {e}", file=sys.stderr)
        return None


def resolve_active(name, data):
    """Returns True/False once confidently matched to a MusicBrainz artist,
    or None if there's no confident match at all (left for a future rerun).
    MusicBrainz sets life-span.ended = true for a confirmed end (death /
    disbandment) but leaves it null for ongoing acts rather than setting
    it false, so null here means "presumed active", not "unresolved"."""
    if not data:
        return None
    candidates = data.get("artists") or []
    if not candidates:
        return None

    best = candidates[0]
    if best.get("score", 0) < MIN_SCORE:
        return None
    if best.get("name", "").strip().lower() != name.strip().lower():
        return None

    life_span = best.get("life-span") or {}
    return not bool(life_span.get("ended"))


def main():
    artists = common.load_artists(ARTISTS_CSV)
    status = load_status()
    scrape_state = common.load_scrape_state()

    checked_count = 0
    inactive_count = 0
    skipped_count = 0

    for i, artist in enumerate(artists, 1):
        existing = status.get(artist)
        if existing and existing.get("source") == "manual":
            print(f"[{i}/{len(artists)}] {artist} — skipped, manual override in place")
            continue
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            skipped_count += 1
            continue

        print(f"[{i}/{len(artists)}] {artist}")
        data = search_artist(artist)
        active = resolve_active(artist, data)

        if active is not None:
            status[artist] = {
                "active": active,
                "source": "musicbrainz",
                "checked": date.today().isoformat(),
            }
            common.mark_checked(scrape_state, artist, SOURCE_NAME)
            checked_count += 1
            if not active:
                inactive_count += 1
                print(f"  -> marked inactive")

        time.sleep(REQUEST_DELAY)

    save_status(status)
    common.save_scrape_state(scrape_state)
    print(f"\nDone. {checked_count} artists resolved, {inactive_count} newly marked inactive, "
          f"{skipped_count} skipped (checked recently).")


if __name__ == "__main__":
    main()
