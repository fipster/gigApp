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

Two stages:

  Stage 1 -- queryArtistOverview (GraphQL, plain HTTP+TOTP token). Fast,
  cheap, works for every artist. Its concerts list is hard-capped at 4
  items (confirmed -- extra limit/offset-style variables are silently
  ignored, since a persisted query's server-side query text is fixed) but
  its `totalCount` field is accurate, so we always know when more exist.
  If totalCount <= the items we got, that's the complete list already. If
  not, the artist is queued for stage 2.

  Stage 2 -- for artists stage 1 couldn't fully cover, fetches the real
  open.spotify.com/artist/<id>/concerts page (server-rendered, confirmed
  via `data-ssr="1"` in the raw response) via Playwright, a real headless
  Chromium doing a genuine top-level navigation. This is necessary because
  that page's SSR content is gated behind something that distinguishes a
  true top-level navigation from *any* scripted request -- confirmed this
  gate is not headers or cookies: a plain Python request with
  browser-identical headers (including Sec-Fetch-Mode/Dest) gets a
  stripped, event-less shell, and so does a fetch() issued from *inside*
  the real Spotify page with a live session cookie and credentials
  included. Only genuine navigation gets through. Playwright's headless
  Chromium (a real browser engine doing real navigations, not a scripted
  fetch) does pass this gate -- confirmed live against Protomartyr (36/36
  concerts) and Dry Cleaning (22/22, vs. only 4 visible in stage 1).

  Gotcha specific to this environment: Playwright must use a mobile user
  agent (see STAGE2_USER_AGENT) -- without it, Spotify serves its desktop
  web-player build, which has a different DOM structure than the one this
  script's extraction targets, silently producing empty venue/city text
  for every concert.

Unlike Bandsintown/Ticketmaster, Spotify doesn't give a country directly.
Stage 1's concerts have venue lat/lon, reverse-geocoded to a country via
Nominatim (same free API geocode_cities.py/geocode_countries.py use, 1
req/sec) behind a cheap lat/lon bounding-box pre-filter (covers
Europe/Middle East/Africa -- the region countries.json's allowlist spans)
that skips the Nominatim call entirely for obviously-out-of-region shows.
Stage 2's concerts only have a city name (no coordinates), so country
comes from forward-geocoding the city name instead (Nominatim free-text
search, no country hint) -- inherently ambiguous for city names that exist
in multiple countries (e.g. "Cambridge", "Santiago"); accepted as a known,
undocumented-further limitation, same spirit as this project's other
geocoding caveats (see geocode_cities.py's docstring).

Run enrich_flights.py afterward to fill in flightTLL/flightRIX/note.
"""

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
    """Returns (items, total_count). items is capped at 4 by the persisted
    query itself; total_count is accurate, so total_count > len(items)
    means this artist needs stage 2 to get the rest."""
    variables = {"uri": artist_uri, "locale": "", "includePrerelease": True, "enableAssociatedVideos": False}
    data = graphql(token, "queryArtistOverview", variables, ARTIST_OVERVIEW_HASH)
    artist = (data.get("data") or {}).get("artist") or {}
    events = ((artist.get("goods") or {}).get("events") or {}).get("concerts") or {}
    return events.get("items") or [], events.get("totalCount") or 0


def load_json_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_json_cache(cache, path):
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


def build_show(artist, show_date, city, venue, country_code, url, fest=None):
    if not show_date or show_date < date.today().isoformat():
        return None
    return {
        "band": artist,
        "date": show_date,
        "city": city,
        "country": country_code,
        "venue": venue,
        "fest": fest,
        "source": SOURCE_NAME,
        "url": url,
        "flightTLL": dict(common.NO_FLIGHT_INFO),
        "flightRIX": dict(common.NO_FLIGHT_INFO),
        "note": "",
    }


def show_from_overview_concert(artist, concert, country_code):
    d = concert.get("date") or {}
    if not d.get("year"):
        return None
    show_date = f"{d['year']:04d}-{d.get('month', 1):02d}-{d.get('day', 1):02d}"
    venue = concert.get("venue") or {}
    city = ((venue.get("location") or {}).get("name") or "").strip()
    return build_show(
        artist, show_date, city, venue.get("name") or "", country_code,
        f"https://open.spotify.com/concert/{concert.get('id', '')}",
        fest="FESTIVAL" if concert.get("festival") else None,
    )


# --- stage 2: full concert list via Playwright (see module docstring) ---

CITY_COUNTRY_CACHE_JSON = "spotify_city_country_cache.json"
QUEUE_JSON = "spotify_needs_full_list.json"

# without a mobile UA, Spotify serves its desktop web-player build, which
# has a different DOM structure than STAGE2_EXTRACT_JS targets -- confirmed
# live: id/date still come through, but venue/city silently comes back
# empty for every concert
STAGE2_USER_AGENT = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

# each concert is <a href="/concert/<id>"><time datetime="ISO">...</time>
# ...<p><time>display text, no datetime attr</time>Venue, City</p></a> --
# confirmed via live DOM inspection, not guessed
STAGE2_EXTRACT_JS = """
els => els.map(a => {
  const id = (a.getAttribute('href') || '').replace('/concert/', '');
  const timeEl = a.querySelector('time[datetime]');
  const iso = timeEl ? timeEl.getAttribute('datetime') : null;
  const p = a.querySelector('p');
  let venueCity = '';
  if (p) {
    const clone = p.cloneNode(true);
    const innerTime = clone.querySelector('time');
    if (innerTime) innerTime.remove();
    venueCity = clone.textContent.trim();
  }
  return {id, iso, venueCity};
})
"""


def parse_venue_city(text):
    if ", " not in text:
        return "", text.strip()
    venue, city = text.rsplit(", ", 1)
    return venue.strip(), city.strip()


def _nominatim_search(params):
    url = f"https://nominatim.openstreetmap.org/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": GEO_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Nominatim's structured city= and free-text q= search will happily return
# a "top match" for anything, including venue-page city text that isn't a
# real place at all -- confirmed live: "C A B A" (as Spotify literally
# displays "Ciudad Autónoma de Buenos Aires") top-matched a Ukrainian
# archaeological site, and "Luxembourg-city" top-matched the Luxembourg
# embassy in London -- both would have silently mistagged a real show with
# a wrong country. A result is only trusted if EITHER (a) its place name
# reasonably matches the query after accent/case/punctuation folding (via
# shows_common._fold_city_for_matching, already used for this exact kind
# of comparison elsewhere in the project), which catches the vast majority
# of real places including small towns, OR (b) it's a high-"importance"
# match (>=0.7, roughly "the internationally well-known name of a country
# capital or similarly major city") -- a fallback for legitimate
# English-vs-local-name mismatches folding alone can't bridge (e.g.
# "Prague" vs Nominatim's "Praha", "Vienna" vs "Wien"). Anything that
# clears neither bar is treated as unresolvable (returns "") rather than
# risking a wrong country -- a missed show is a far better failure mode
# here than a real show silently mistagged into (or out of) the region.
GEO_VALID_CLASSES = {"place", "boundary"}
GEO_HIGH_IMPORTANCE = 0.7

# Confirmed false positives from this scraper's first full run -- each of
# these has a same-named place outside our target region that's at least
# as prominent (by touring-venue traffic) as its EU/ME/Africa namesake,
# and the class+importance validation above wasn't enough to avoid it:
#   - "Bellingen" -> resolved DE; the real show was Bellingen, NSW, Australia
#   - "London" (bare) -> resolved GB; real show was London, Ontario, Canada
#     (note: this only blocks the bare string "london" -- "Stoke Newington,
#     London" etc. are unaffected, since cache/denylist keys are exact city
#     text, not substrings)
#   - "Salamanca" -> resolved ES; real show was Salamanca, NY, USA
#   - "Santa Cruz" -> resolved ES; real show was Santa Cruz, CA, USA (hit
#     3 separate times across different artists)
#   - "Sale" -> resolved IT; real show was Sale, Greater Manchester, UK
#     (BEC Arena -- Spotify separately also listed the same show under
#     "Manchester", which resolved correctly, so no coverage lost)
#   - "Reading" -> resolved GB; real show was Reading, PA, USA
#   - "Thornbury" -> resolved GB; real show was Thornbury, VIC, Australia
#   - "Cambridge" -> resolved GB; both real shows hit were Cambridge, MA, USA
# Second full run turned up 3 more of the same pattern:
#   - "Laval" -> resolved FR; real show was Laval, QC, Canada (Metric --
#     a Canadian band -- at Place Bell, a well-known Laval, QC arena)
#   - "Portsmouth" -> resolved GB; both real shows hit were Portsmouth,
#     NH, USA (3S Artspace and Prescott Park are both real NH venues)
#   - "Birmingham" -> resolved GB; both real shows hit were Birmingham,
#     AL, USA (WorkPlay and Iron City are both real Alabama venues) --
#     note this only blocks Spotify's own forward-geocoding; other
#     sources (Bandsintown/Songkick/Ticketmaster) report real Birmingham,
#     UK shows correctly via their own APIs, unaffected by this denylist
# Each was individually verified (web search against the actual venue name)
# before being added here -- this is a deny-by-evidence list, not a guess.
# Treated as permanently unresolvable rather than retried, since nothing
# about the query itself signals which "Cambridge" etc. is meant. Given
# this list has grown on both the first AND second full run, expect it to
# keep growing -- this is an inherent limit of forward-geocoding a bare
# city name with no country hint, not a bug to eventually fully fix.
GEO_AMBIGUOUS_CITY_DENYLIST = {
    "bellingen", "london", "salamanca", "santa cruz", "sale", "reading",
    "thornbury", "cambridge", "laval", "portsmouth", "birmingham",
}


def resolve_country_from_city(city, cache):
    """Forward-geocodes a bare city name (no country hint, unlike stage 1's
    lat/lon) to a country code via Nominatim. See GEO_VALID_CLASSES et al
    above for the validation this applies before trusting a match, and
    GEO_AMBIGUOUS_CITY_DENYLIST for names known to defeat that validation.
    Still ambiguous for other real city names that exist in multiple
    countries -- picks Nominatim's top-ranked valid match. Accepted as a
    known limitation, same spirit as geocode_cities.py's own caveats."""
    key = city.strip().lower()
    if not key or key in GEO_AMBIGUOUS_CITY_DENYLIST:
        return ""
    if key in cache:
        return cache[key]

    folded_query = common._fold_city_for_matching(city)
    code = ""
    try:
        for params in (
            {"city": city, "format": "json", "limit": 5, "addressdetails": 1},
            {"q": city, "format": "json", "limit": 5, "addressdetails": 1},
        ):
            results = _nominatim_search(params)
            time.sleep(GEO_REQUEST_DELAY)
            for r in results:
                if r.get("class") not in GEO_VALID_CLASSES:
                    continue
                addr = r.get("address") or {}
                place_name = (addr.get("city") or addr.get("town") or addr.get("village")
                              or addr.get("municipality") or (r.get("display_name") or "").split(",")[0])
                folded_place = common._fold_city_for_matching(place_name)
                name_matches = folded_query in folded_place or folded_place in folded_query
                high_confidence = (r.get("importance") or 0) >= GEO_HIGH_IMPORTANCE
                if name_matches or high_confidence:
                    code = (addr.get("country_code") or "").upper()
                    break
            if code:
                break
    except Exception as e:
        print(f"    city-geocode error for {city!r}: {e}", file=sys.stderr)

    cache[key] = code
    return code


def fetch_full_concert_list(page, artist_uri):
    artist_id = artist_uri.split(":")[-1]
    url = f"https://open.spotify.com/artist/{artist_id}/concerts"
    page.goto(url, wait_until="networkidle", timeout=30000)
    return page.eval_on_selector_all('a[href^="/concert/"]', STAGE2_EXTRACT_JS)


def run_stage2(queue, merged, existing_by_key, city_country_cache, artist_status):
    if not queue:
        return 0
    from playwright.sync_api import sync_playwright

    new_count = 0
    print(f"\nstage 2: fetching full list for {len(queue)} artist(s) via Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=STAGE2_USER_AGENT)
        for i, entry in enumerate(queue, 1):
            artist, artist_uri = entry["artist"], entry["uri"]
            try:
                items = fetch_full_concert_list(page, artist_uri)
            except Exception as e:
                print(f"  [{i}/{len(queue)}] {artist} — full-list fetch error: {e}", file=sys.stderr)
                continue

            artist_new = 0
            for item in items:
                venue, city = parse_venue_city(item.get("venueCity") or "")
                if not city:
                    continue
                country_code = resolve_country_from_city(city, city_country_cache)
                if not country_code or country_code not in common.ALLOWED_COUNTRIES:
                    continue
                iso = item.get("iso") or ""
                show_date = iso[:10] if len(iso) >= 10 else ""
                concert_id = item.get("id") or ""
                show = build_show(artist, show_date, city, venue, country_code,
                                   f"https://open.spotify.com/concert/{concert_id}")
                if show is None:
                    continue
                key = common.show_key(show)
                if key in existing_by_key:
                    continue
                if not common.confirm_inactive_artist_show(show, artist_status):
                    continue
                merged[key] = show
                artist_new += 1
                new_count += 1

            print(f"  [{i}/{len(queue)}] {artist} — {len(items)} concert(s) total, {artist_new} new show(s)")
        browser.close()

    return new_count


def main():
    artists = common.load_artists(common.ARTISTS_CSV)
    existing = common.load_shows()
    existing_by_key = {common.show_key(s): s for s in existing}
    merged = dict(existing_by_key)

    scrape_state = common.load_scrape_state()
    artist_status = common.load_artist_status()
    geo_cache = load_json_cache(GEO_CACHE_JSON)
    city_country_cache = load_json_cache(CITY_COUNTRY_CACHE_JSON)

    print("fetching access token...")
    token = fetch_access_token()
    print("token ok\n")

    queue = []  # artists whose totalCount exceeds stage 1's 4-item preview

    for i, artist in enumerate(artists, 1):
        # unlike scrape_bandsintown.py, this is a free source, so an
        # inactive artist reuniting isn't a cost concern -- searched like
        # every other free scraper, with confirm_inactive_artist_show()
        # gating any show it actually finds (below) instead of skipping
        # the search outright
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
            concerts, total_count = fetch_concerts(token, artist_uri)
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
            show = show_from_overview_concert(artist, concert, country_code)
            if show is None:
                continue
            key = common.show_key(show)
            if key in existing_by_key:
                continue
            if not common.confirm_inactive_artist_show(show, artist_status):
                continue
            merged[key] = show
            new_count += 1

        needs_full_list = total_count > len(concerts)
        if needs_full_list:
            queue.append({"artist": artist, "uri": artist_uri})

        suffix = " (queued for full list)" if needs_full_list else ""
        print(f"[{i}/{len(artists)}] {artist} — {len(concerts)}/{total_count} concert(s), {new_count} new show(s){suffix}")
        common.mark_checked(scrape_state, artist, SOURCE_NAME)
        time.sleep(REQUEST_DELAY)

    save_json_cache(geo_cache, GEO_CACHE_JSON)
    save_json_cache({"queued": queue}, QUEUE_JSON)

    stage2_new = run_stage2(queue, merged, existing_by_key, city_country_cache, artist_status)
    save_json_cache(city_country_cache, CITY_COUNTRY_CACHE_JSON)

    result = common.save_shows(list(merged.values()))
    common.save_scrape_state(scrape_state)

    print(f"\nDone. {len(result)} total shows ({len(result) - len(existing)} new, {stage2_new} via stage 2).")


if __name__ == "__main__":
    main()
