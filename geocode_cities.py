"""
Looks up latitude/longitude for every unique (city, country) pair
currently in shows.json and writes them to city_coordinates.json, for
map-plotting. This is a standalone utility, not part of run_scrapers.py --
it only needs to run when a scrape adds a city that isn't in
city_coordinates.json yet, not on every scraper run.

Uses OpenStreetMap's free Nominatim geocoding API
(https://nominatim.openstreetmap.org). No API key needed, but their
usage policy requires staying at/under 1 request/second and sending a
descriptive User-Agent identifying the application -- both handled
below (REQUEST_DELAY, USER_AGENT). Docs:
https://operations.osmfoundation.org/policies/nominatim/

City-level accuracy only (a city-center point, not the specific venue)
-- good enough for the intended map feature.

Incremental like check_artist_status.py: cities already present in
city_coordinates.json are skipped, so a rerun after a scrape only looks
up newly-appeared cities.

Known limitation: Nominatim's structured city= search only recognizes
official city-level places, so small villages/districts/hamlets (e.g.
"Wimborne Saint Giles", a small English village) often return nothing
even though they're real, findable places. When the structured search
comes up empty, this falls back to Nominatim's general free-text search
(q=), which is far more permissive and usually finds these -- at some
cost to precision (it can occasionally match the wrong same-named place
instead of the intended one). A result is only kept if one of the two
searches returns a match at all; a name-plus-country pair that returns
nothing from both is skipped and logged rather than guessed at, and
would need a manual entry in city_coordinates.json to override.

Also known: some "city" values are actually counties/regions/vague area
descriptors ("Cheshire", "Swiss Alps", "South West London") rather than
an actual single city -- likely a source reporting region-level location
instead of a specific city. No single coordinate is meaningfully correct
for these, so a persistent miss on both searches may just mean this
rather than a geocoding shortfall.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

import shows_common as common

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

COORDINATES_JSON = "city_coordinates.json"
BASE_URL = "https://nominatim.openstreetmap.org/search"
REQUEST_DELAY = 1.1  # Nominatim's policy: stay at/under 1 req/sec

USER_AGENT = "gigApp/1.0 (personal project, contact: vipp@crimson.ee)"


def load_coordinates(path=COORDINATES_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_coordinates(coords, path=COORDINATES_JSON):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def coord_key(city, country):
    return f"{city}|{country}"


def _search(params):
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode(city, country_code):
    structured_params = {
        "city": city,
        "countrycodes": country_code.lower(),
        "format": "json",
        "limit": 1,
    }
    try:
        results = _search(structured_params)
    except Exception as e:
        print(f"  error geocoding {city}, {country_code}: {e}", file=sys.stderr)
        return None

    if not results:
        # structured search only recognizes official city-level places --
        # fall back to free-text search for villages/districts it misses
        time.sleep(REQUEST_DELAY)
        freetext_params = {
            "q": city,
            "countrycodes": country_code.lower(),
            "format": "json",
            "limit": 1,
        }
        try:
            results = _search(freetext_params)
        except Exception as e:
            print(f"  error geocoding {city}, {country_code} (fallback): {e}", file=sys.stderr)
            return None

    if not results:
        return None
    return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])}


def main():
    shows = common.load_shows()
    unique_cities = sorted(set((s["city"], s["country"]) for s in shows if s["city"]))

    coords = load_coordinates()
    found_count = 0
    missing = []

    for i, (city, country) in enumerate(unique_cities, 1):
        key = coord_key(city, country)
        if key in coords:
            continue

        print(f"[{i}/{len(unique_cities)}] {city}, {country}")
        result = geocode(city, country)
        if result:
            coords[key] = result
            found_count += 1
        else:
            missing.append(key)
            print(f"  no match found", file=sys.stderr)
        time.sleep(REQUEST_DELAY)

    save_coordinates(coords)

    print(f"\nDone. {found_count} new cities geocoded, {len(coords)} total in {COORDINATES_JSON}.")
    if missing:
        print(f"{len(missing)} not found (left for manual review): {', '.join(missing)}")


if __name__ == "__main__":
    main()
