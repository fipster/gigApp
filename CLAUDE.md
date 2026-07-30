# gigApp

A personal tour-date tracker: a static site (HTML/CSS/vanilla JS, no build
step) backed by `shows.json`, which a set of Python scrapers keep populated
with upcoming shows for the artists in `artists.csv`. Deployed via GitHub
Pages, so everything in the repo root is publicly fetchable -- there are no
secrets in tracked files (see **Setup** below for where the real ones live).

## Setup

Python dependencies (no `requirements.txt` yet -- install by hand):

```
pip install python-dotenv playwright
playwright install chromium
```

`python-dotenv` is used by `scrape_ticketmaster.py` and `scrape_bandsintown.py`.
`playwright` (+ its Chromium binary) is used by `scrape_spotify.py`'s stage 2
only -- see that file's docstring for why a real browser engine is needed
there instead of a plain HTTP request.

Create a `.env` file in the repo root (gitignored) with:

```
TICKETMASTER_API_KEY=...   # https://developer.ticketmaster.com
APIFY_API_TOKEN=...        # https://apify.com -- runs the Bandsintown actor
```

Everything else either needs no key (Fienta, Kultuurikava, AllEvents.lt,
Songkick, Skene, MusicBrainz -- all public/reverse-engineered endpoints,
see each script's own docstring) or derives its own credential internally
(Spotify's TOTP-based anonymous token, see `scrape_spotify.py`).

## Running things

**Full scrape pipeline**: `python run_scrapers.py` -- runs every free
source, then the paid one (`scrape_bandsintown.py`, costs real money per
run via Apify), then resolves cross-source duplicates, tags priority, and
fills in flight info. This is the normal way to refresh `shows.json`; see
`run_scrapers.py`'s own docstring for the exact ordering rationale.

**A single source**: run any `scrape_<source>.py` directly. Useful for
testing one source or re-running just the paid one on its own.

**`check_artist_status.py`**: checks artists.csv against MusicBrainz for
disbanded/deceased status, independent of the scrape pipeline -- run it
occasionally, not on every scrape (it's throttled like the scrapers, so a
rerun within the recheck window mostly no-ops anyway).

**`geocode_cities.py` / `geocode_countries.py`**: standalone, only need
rerunning when a scrape adds a city/country not already cached in
`city_coordinates.json` / `country_coordinates.json` (used by the map view).

**The frontend**: no build step. Serve the repo root and open `index.html`
-- e.g. `python -m http.server 8000` (matches the `gigapp-static` launch
config in `.claude/launch.json`).

**Validation**: `python check_data.py` checks `shows.json`/`artists.csv`
against the schema/vocabulary the rest of the project assumes (run it
after hand-editing `artists.csv` -- it catches exactly the kind of
cross-column mistake that's happened before). `python -m unittest
test_shows_common.py` runs unit tests for the dedup/matching logic in
`shows_common.py`. Neither makes network calls.

## Data model

**`artists.csv`** columns: `artist_name, playlist, active, ignore, category,
priority`.
- `active`/`ignore`: either one (in a scraper's `load_artists()`, now
  `shows_common.load_artists()`) skips the row entirely -- `active=false`
  for "not in the current source playlist," `ignore=true` for "confirmed
  disbanded/inactive" (see `check_artist_status.py`).
- `category`: `DJ` for DJ sets, otherwise blank.
- `priority`: `1` (highest), `2`, `est` (Estonian-scene artists), or blank.
  Drives the priority filter chips and list/weekly-view highlighting in
  `app.js`.

**`shows.json`**: one flat array, each entry `{band, date, city, country,
venue, fest, source, url, flightTLL, flightRIX, note, priority}`. Deduped
across sources by `(band, date, city, country)` -- see
`shows_common.show_key()` and the two resolvers that run after all
scrapers finish (`resolve_same_date_conflicts`, `resolve_festival_duplicates`).

**Region allowlist**: `countries.json` tags every country with a `region`
(`europe`/`middle_east`/`africa`); `shows_common.ALLOWED_COUNTRIES` is just
that file's key set. Every scraper filters shows to this allowlist -- it's
the actual mechanism deciding "is this show close enough to be worth
flying to from TLL/RIX," not just a display filter.

## Where the good docs already are

Most of the genuinely tricky logic is documented in-place rather than
here -- read the module docstring before touching:
- `shows_common.py` -- dedup/conflict-resolution logic, the region
  allowlist, city/band name aliasing.
- `enrich_flights.py` -- flight-info derivation; `direct_flights.json`'s
  own `_generation_notes` block documents that data's sourcing and refresh
  process in detail.
- `scrape_spotify.py` -- by far the most fragile source (reverse-engineered
  auth, a Playwright-dependent second stage, a documented Developer Terms
  violation this project accepts knowingly); read this one in full before
  changing it.
- Each other `scrape_*.py`'s docstring documents that source's specific
  quirks (false-positive rates, country-resolution limitations, rate
  limits) -- several are known-noisy and expect a manual review pass after
  each run, not just a clean-looking show count.
