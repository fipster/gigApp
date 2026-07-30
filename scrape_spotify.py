"""
Scrapes upcoming shows for artists in artists.csv from Spotify's "Concerts"
card (the same data shown on an artist's open.spotify.com page), via
Spotify's internal/undocumented GraphQL API (api-partner.spotify.com/
pathfinder) -- there is no public, ToS-compliant API for this data.

Auth: an anonymous web-player access token, obtained the same way the
open.spotify.com web player itself does -- a TOTP code derived from a
secret cipher embedded in the web player's JS bundle (see totp_secret()
below; bytes/version sourced from the public reverse-engineering project
https://github.com/misiektoja/spotify_profile_monitor). No login/password
needed, but note: the token endpoint's own JSON response includes an
explicit notice that this endpoint's usage is against Spotify's Developer
Terms. This script exists because the user asked to try it anyway, with
that warning surfaced and acknowledged -- if TOTP_VERSION/TOTP_SECRET_CIPHER
ever stop working (Spotify rotates them periodically), that's expected and
not worth chasing.

Two GraphQL calls per artist:
  1. searchDesktop -- resolves an artist name to a spotify:artist:<id> URI
     (skipped once found; cached in scrape_state.json like other sources).
  2. queryArtistOverview -- returns (among other things)
     data.artist.goods.events.concerts, the same list the "Concerts" card
     on the artist page shows.

Known limitation: queryArtistOverview's concerts list is hard-capped at 4
items (confirmed -- extra limit/offset-style variables are silently
ignored, since a persisted query's server-side query text is fixed). The
artist's full tour list (up to dozens of dates) IS available, but only via
open.spotify.com/artist/<id>/concerts, a separate page that's server-side
rendered -- and that SSR content only comes back for requests that pass
Spotify's edge bot/IP-reputation check, which a plain script request from
this environment does not (confirmed: identical headers to a real browser
still get a stripped, event-less shell). So this scraper only sees each
artist's *next 4* upcoming concerts. For an artist on a long overseas tour
that front-loads non-EU dates, this can miss real, later EU dates entirely
until they roll into that top-4 window on a future run -- for an EU-based
artist (or one about to play EU dates soon), it reliably catches them, as
confirmed live against Dry Cleaning's Italy dates.

Unlike Bandsintown/Ticketmaster, Spotify's concert entries give a venue
name + city + lat/lon but NOT a country. Country is derived by reverse-
geocoding the lat/lon via Nominatim (same free API geocode_cities.py /
geocode_countries.py already use, 1 req/sec). A cheap lat/lon bounding-box
pre-filter (covers Europe/Middle East/Africa -- the same region
countries.json's allowlist spans) skips the Nominatim call entirely for
obviously-out-of-region shows (most touring bands play far more North
American/Asian dates than European ones), so most artists cost 0 geocode
calls.

Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b32decode, b32encode
from datetime import date
from hashlib import sha1
from hmac import new as hmac_new

import shows_common as common

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SOURCE_NAME = "Spotify"
REQUEST_DELAY = 0.4
GEO_REQUEST_DELAY = 1.1  # Nominatim policy: stay at/under 1 req/sec
GEO_CACHE_JSON = "spotify_geo_cache.json"
GEO_USER_AGENT = "gigApp/1.0 (personal project, contact: vipp@crimson.ee)"

# rough bounding box covering Europe + Middle East + Africa (the regions
# countries.json's allowlist spans) -- cheap pre-filter to avoid a
# reverse-geocode call for shows nowhere near it (the Americas, East
# Asia, Australia/NZ)
REGION_LAT_RANGE = (-36.0, 72.0)
REGION_LON_RANGE = (-26.0, 64.0)

# reverse-engineered web-player token flow -- see module docstring
TOTP_VERSION = 61
TOTP_SECRET_CIPHER = (44, 55, 47, 42, 70, 40, 34, 114, 76, 74, 50, 111, 120, 97,
                       75, 76, 94, 102, 43, 69, 49, 120, 118, 80, 64, 78)

PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v1/query"
TOKEN_URL = "https://open.spotify.com/api/token"

SEARCH_HASH = "0dff51c99e552b992377a2a6f40d213dc42b62db86ca0bcf16cf3934aec1aae6"
ARTIST_OVERVIEW_HASH = "433e28d1e949372d3ca3aa6c47975cff428b5dc37b12f5325d9213accadf770a"

NO_FLIGHT_INFO = {"direct": False, "seasonal": False, "duration_minutes": None}


def load_artists(path):
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header row: artist_name,playlist,active,ignore
        return [row[0].strip() for row in reader if row and row[0].strip()
                and (len(row) < 3 or row[2].strip().lower() != "false")
                and (len(row) < 4 or row[3].strip().lower() != "true")]


def totp_secret():
    xored = [b ^ ((i % 33) + 9) for i, b in enumerate(TOTP_SECRET_CIPHER)]
    digits = "".join(str(x) for x in xored)
    return b32encode(bytes.fromhex(digits.encode().hex())).decode().rstrip("=")


def totp_now(secret):
    key = b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = int(time.time() // 30)
    digest = hmac_new(key, counter.to_bytes(8, "big"), sha1).digest()
    offset = digest[-1] & 0xF
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def fetch_access_token():
    code = totp_now(totp_secret())
    params = {
        "reason": "init",
        "productType": "web-player",
        "totp": code,
        "totpVer": str(TOTP_VERSION),
        "totpServer": code,
        "ts": str(int(time.time() * 1000)),
    }
    url = f"{TOKEN_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    token = data.get("accessToken")
    if not token:
        raise RuntimeError(f"no accessToken in token response: {data}")
    return token


def graphql(token, operation_name, variables, sha256_hash):
    params = {
        "operationName": operation_name,
        "variables": json.dumps(variables),
        "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}}),
    }
    url = f"{PATHFINDER_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_artist_uri(token, artist_name):
    """Returns spotify:artist:<id> URI for an exact (case-insensitive) name
    match among search results, or None if no exact match is found."""
    variables = {
        "searchTerm": artist_name,
        "offset": 0,
        "limit": 10,
        "numberOfTopResults": 10,
        "includeAudiobooks": False,
        "includePreReleases": False,
        "includeLocalConcertsField": False,
        "includeAuthors": False,
    }
    data = graphql(token, "searchDesktop", variables, SEARCH_HASH)
    items = (((data.get("data") or {}).get("searchV2") or {}).get("artists") or {}).get("items") or []
    target = artist_name.strip().lower()
    for item in items:
        profile = (item.get("data") or {}).get("profile") or {}
        if (profile.get("name") or "").strip().lower() == target:
            return (item.get("data") or {}).get("uri")
    return None


def fetch_concerts(token, artist_uri):
    variables = {"uri": artist_uri, "locale": "", "includePrerelease": True, "enableAssociatedVideos": False}
    data = graphql(token, "queryArtistOverview", variables, ARTIST_OVERVIEW_HASH)
    artist = (data.get("data") or {}).get("artist") or {}
    events = ((artist.get("goods") or {}).get("events") or {}).get("concerts") or {}
    return events.get("items") or []


def load_geo_cache(path=GEO_CACHE_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_geo_cache(cache, path=GEO_CACHE_JSON):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def geo_cache_key(lat, lon):
    return f"{round(lat, 2)},{round(lon, 2)}"


def in_region_bounding_box(lat, lon):
    return REGION_LAT_RANGE[0] <= lat <= REGION_LAT_RANGE[1] and REGION_LON_RANGE[0] <= lon <= REGION_LON_RANGE[1]


def reverse_geocode_country(lat, lon, cache):
    key = geo_cache_key(lat, lon)
    if key in cache:
        return cache[key]
    params = {"lat": lat, "lon": lon, "format": "json", "zoom": 3, "addressdetails": 1}
    url = f"https://nominatim.openstreetmap.org/reverse?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": GEO_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        code = ((result.get("address") or {}).get("country_code") or "").upper()
    except Exception as e:
        print(f"    reverse-geocode error for {lat},{lon}: {e}", file=sys.stderr)
        code = ""
    cache[key] = code
    time.sleep(GEO_REQUEST_DELAY)
    return code


def to_show(artist, concert, country_code):
    d = concert.get("date") or {}
    if not d.get("year"):
        return None
    show_date = f"{d['year']:04d}-{d.get('month', 1):02d}-{d.get('day', 1):02d}"
    if show_date < date.today().isoformat():
        return None

    venue = concert.get("venue") or {}
    city = ((venue.get("location") or {}).get("name") or "").strip()

    return {
        "band": artist,
        "date": show_date,
        "city": city,
        "country": country_code,
        "venue": venue.get("name") or "",
        "fest": "FESTIVAL" if concert.get("festival") else None,
        "source": SOURCE_NAME,
        "url": f"https://open.spotify.com/concert/{concert.get('id', '')}",
        "flightTLL": dict(NO_FLIGHT_INFO),
        "flightRIX": dict(NO_FLIGHT_INFO),
        "note": "",
    }


def main():
    artists = load_artists(common.ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()
    artist_status = common.load_artist_status()
    geo_cache = load_geo_cache()

    print("fetching access token...")
    token = fetch_access_token()
    print("token ok\n")

    for i, artist in enumerate(artists, 1):
        if common.is_inactive(artist_status, artist):
            print(f"[{i}/{len(artists)}] {artist} — skipped, marked inactive")
            continue
        if common.already_checked_recently(scrape_state, artist, SOURCE_NAME):
            print(f"[{i}/{len(artists)}] {artist} — skipped, checked recently")
            continue

        try:
            artist_uri = resolve_artist_uri(token, artist)
        except Exception as e:
            print(f"[{i}/{len(artists)}] {artist} — search error: {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
            continue

        if not artist_uri:
            print(f"[{i}/{len(artists)}] {artist} — not found, won't retry")
            common.mark_not_found(scrape_state, artist, SOURCE_NAME)
            time.sleep(REQUEST_DELAY)
            continue

        time.sleep(REQUEST_DELAY)
        try:
            concerts = fetch_concerts(token, artist_uri)
        except Exception as e:
            print(f"[{i}/{len(artists)}] {artist} — concerts error: {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
            continue

        new_count = 0
        for concert in concerts:
            coords = ((concert.get("venue") or {}).get("coordinates")) or {}
            lat, lon = coords.get("latitude"), coords.get("longitude")
            if lat is None or lon is None or not in_region_bounding_box(lat, lon):
                continue
            country_code = reverse_geocode_country(lat, lon, geo_cache)
            if not country_code or country_code not in common.ALLOWED_COUNTRIES:
                continue
            show = to_show(artist, concert, country_code)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            merged[key] = show
            new_count += 1

        print(f"[{i}/{len(artists)}] {artist} — {len(concerts)} concert(s), {new_count} new show(s)")
        common.mark_checked(scrape_state, artist, SOURCE_NAME)
        time.sleep(REQUEST_DELAY)

    save_geo_cache(geo_cache)
    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new).")


if __name__ == "__main__":
    main()
