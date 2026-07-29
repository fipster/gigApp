"""
Looks up a representative lat/lon for every country in countries.json
and writes them to country_coordinates.json, as a fallback for the map
view when a specific city has no entry in city_coordinates.json (e.g.
a small village Nominatim's city-level search can't find) -- better to
plot a show somewhere in the right country than drop it from the map
entirely.

Same Nominatim geocoding approach as geocode_cities.py (free, no API
key, rate-limited to 1 req/sec per their usage policy). Incremental:
countries already present in country_coordinates.json are skipped, so
a rerun only looks up newly-added countries.
"""

import json
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

COUNTRIES_JSON = "countries.json"
COORDINATES_JSON = "country_coordinates.json"
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


def geocode(country_name, country_code):
    params = {"country": country_name, "countrycodes": country_code.lower(), "format": "json", "limit": 1}
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            results = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  error geocoding {country_name}: {e}", file=sys.stderr)
        return None
    if not results:
        return None
    return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])}


def main():
    with open(COUNTRIES_JSON, encoding="utf-8") as f:
        countries = json.load(f)
    coords = load_coordinates()

    missing = [(code, info["name"]) for code, info in countries.items() if code not in coords]
    print(f"{len(missing)} countries to geocode ({len(coords)} already cached)")

    not_found = []
    for i, (code, name) in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {name} ({code})")
        result = geocode(name, code)
        if result:
            coords[code] = result
        else:
            not_found.append(f"{name} ({code})")
        time.sleep(REQUEST_DELAY)

    save_coordinates(coords)
    print(f"\nDone. {len(coords)} total in {COORDINATES_JSON}.")
    if not_found:
        print(f"{len(not_found)} not found (left for manual review): {', '.join(not_found)}")


if __name__ == "__main__":
    main()
