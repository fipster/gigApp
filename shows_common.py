"""
Shared utilities for source-specific scrapers (scrape_ticketmaster.py, and
any future scrape_<source>.py). Handles the parts every source needs the
same way: the region allowlist, show deduplication, and loading/saving
shows.json (with past-date pruning). Flight enrichment is handled
separately by enrich_flights.py — it's source-agnostic and doesn't belong
in any one scraper.
"""

import csv
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta

SHOWS_JSON = "shows.json"
COUNTRIES_JSON = "countries.json"
SCRAPE_STATE_JSON = "scrape_state.json"
ARTIST_STATUS_JSON = "artist_status.json"
ARTISTS_CSV = "artists.csv"
CITY_NAME_ALIASES_JSON = "city_name_aliases.json"
BAND_NAME_ALIASES_JSON = "band_name_aliases.json"
SKIP_IF_CHECKED_WITHIN_DAYS = 14

with open(COUNTRIES_JSON, encoding="utf-8") as f:
    ALLOWED_COUNTRIES = json.load(f).keys()

with open(CITY_NAME_ALIASES_JSON, encoding="utf-8") as f:
    _city_aliases_raw = json.load(f)
CITY_NAME_ALIASES = {k: v for k, v in _city_aliases_raw.items() if not k.startswith("_")}

with open(BAND_NAME_ALIASES_JSON, encoding="utf-8") as f:
    _band_aliases_raw = json.load(f)
BAND_NAME_ALIASES = {k: v for k, v in _band_aliases_raw.items() if not k.startswith("_")}


def load_artist_priorities(path=ARTISTS_CSV):
    # maps each artist to its priority tier (6th column of artists.csv --
    # see that file's header), so every show can be tagged with how much
    # attention its artist warrants, both for steering scrapers and for display
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header row
        return {row[0].strip(): row[5].strip() for row in reader if row and row[0].strip() and len(row) > 5}


def normalize_city(city):
    # maps a local/native-language city name to the canonical English name
    # (matching direct_flights.json's destination keys) via city_name_aliases.json,
    # so the same real show reported in different languages by different
    # sources doesn't dedupe as two different cities -- see that file's _comment.
    city = (city or "").strip()
    return CITY_NAME_ALIASES.get(city, city)


def normalize_band(band):
    # maps an old/alternate band name to the canonical one in artists.csv
    # (e.g. Thee Oh Sees -> Oh Sees) via band_name_aliases.json, so a source
    # reporting the old name doesn't dedupe as a different band -- see that
    # file's _comment.
    band = (band or "").strip()
    return BAND_NAME_ALIASES.get(band, band)


SOURCE_PRIORITY = {"Skene": 0, "Bandsintown": 1, "Ticketmaster": 2, "Songkick": 3, "Spotify": 4}


def resolve_same_date_conflicts(shows):
    # a band can't play two real shows on the same date, so if sources
    # disagree (different city/venue for the same band+date), keep only
    # the highest-priority source's entry and drop the rest
    groups = defaultdict(list)
    for s in shows:
        groups[(normalize_band(s["band"]), s["date"])].append(s)

    resolved, dropped = [], []
    for group in groups.values():
        if len(group) == 1:
            resolved.append(group[0])
            continue
        group.sort(key=lambda s: SOURCE_PRIORITY.get(s.get("source"), 99))
        resolved.append(group[0])
        dropped.append((group[0], group[1:]))

    return resolved, dropped


FESTIVAL_MERGE_WINDOW_DAYS = 5


def resolve_festival_duplicates(shows):
    # a festival appearance sometimes gets scraped twice: once as a
    # generic festival-name listing, once as the artist's specific day/
    # venue, with each source's date guess drifting by a few days. If a
    # band has two entries in the same city within a few days of each
    # other and at least one is flagged as a festival, they're almost
    # certainly the same real appearance -- merge them via union-find
    # (a cluster can have 3+ mutually-overlapping entries) and keep the
    # highest-priority source's entry. Note: when a cluster's entries
    # share the same source, SOURCE_PRIORITY doesn't differentiate and
    # the survivor is effectively "whichever sorted first" -- not a
    # deliberate choice, just the tiebreak's fallback behavior.
    #
    # Recall gap: `fest` is set inconsistently per-source (see
    # scrape_bandsintown.py/scrape_songkick.py/scrape_ticketmaster.py's
    # own festival-detection heuristics; AllEvents.lt/Fienta never set
    # it), so a genuine festival dupe with fest=None on both sides will
    # slip through this filter undetected -- same "manual review still
    # expected" caveat as the rest of this pipeline.
    by_group = defaultdict(list)
    for i, s in enumerate(shows):
        key = (normalize_band(s["band"]), _fold_city_for_matching(normalize_city(s["city"])), s["country"])
        by_group[key].append(i)

    parent = list(range(len(shows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for idxs in by_group.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                s1, s2 = shows[i], shows[j]
                gap = abs((date.fromisoformat(s1["date"]) - date.fromisoformat(s2["date"])).days)
                if gap <= FESTIVAL_MERGE_WINDOW_DAYS and (s1.get("fest") == "FESTIVAL" or s2.get("fest") == "FESTIVAL"):
                    union(i, j)

    clusters = defaultdict(list)
    for i in range(len(shows)):
        clusters[find(i)].append(shows[i])

    resolved, dropped = [], []
    for cluster in clusters.values():
        if len(cluster) == 1:
            resolved.append(cluster[0])
            continue
        cluster.sort(key=lambda s: SOURCE_PRIORITY.get(s.get("source"), 99))
        resolved.append(cluster[0])
        dropped.append((cluster[0], cluster[1:]))

    return resolved, dropped


# letters NFKD doesn't decompose into a base + combining mark (each is its
# own distinct codepoint), so they'd otherwise survive diacritic-stripping
# untouched and keep two spellings of the same city apart (e.g. Polish
# "Bielsko-Biała" vs "Bielsko-biala")
_NON_DECOMPOSING_LETTERS = str.maketrans({
    "ł": "l", "Ł": "L",
    "ø": "o", "Ø": "O",
    "đ": "d", "Đ": "D",
    "ß": "ss",
    "ı": "i",
})


def _fold_city_for_matching(city):
    # beyond the alias table (genuinely different words, e.g. "Wien" vs
    # "Vienna"), sources also disagree on formatting for the *same* word --
    # case ("Gdansk" vs "gdansk"), diacritics ("Gdańsk"), hyphen-vs-space
    # ("Stoke-On-Trent" vs "Stoke On Trent"), apostrophes ("St David's" vs
    # "St Davids"), and trailing parentheticals ("Alicante (Alacant)").
    # This folds all of that away for dedup-key comparison only -- the
    # stored/displayed city text (via normalize_city) is untouched, so
    # whichever source's spelling happens to win the merge is still shown
    # as-is, just no longer creates a duplicate entry.
    city = re.sub(r"\s*\([^)]*\)\s*$", "", city)
    city = city.translate(_NON_DECOMPOSING_LETTERS)
    city = unicodedata.normalize("NFKD", city)
    city = "".join(c for c in city if not unicodedata.combining(c))
    city = city.replace("'", "")
    city = re.sub(r"[-/]", " ", city)
    city = re.sub(r"\s+", " ", city).strip()
    return city.lower()


def show_key(show):
    # dedupe on band+date+city+country rather than venue name: different
    # sources sometimes describe the "venue" differently for the same real
    # event (e.g. Ticketmaster reports the physical venue, Bandsintown
    # reports the festival name), so matching on venue text misses
    # cross-source duplicates. A band playing two different venues in the
    # same city on the same day is rare enough to accept as a tradeoff.
    folded_city = _fold_city_for_matching(normalize_city(show["city"]))
    return (normalize_band(show["band"]), show["date"], folded_city, show["country"])


def load_shows(path=SHOWS_JSON):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_shows(shows, path=SHOWS_JSON):
    today = date.today().isoformat()
    result = sorted((s for s in shows if s["date"] >= today), key=lambda s: (s["date"], s["band"]))
    for s in result:
        s["city"] = normalize_city(s["city"])
        s["band"] = normalize_band(s["band"])
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


def load_artist_status(path=ARTIST_STATUS_JSON):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def is_inactive(artist_status, artist):
    # unknown artists default to active (not skipped) -- only an explicit
    # active: false (from check_artist_status.py or a manual edit) skips them
    return artist_status.get(artist, {}).get("active", True) is False


def confirm_inactive_artist_show(show, artist_status):
    # a show turning up for an artist already confirmed inactive is unusual
    # enough to need a human's yes -- could be a real reunion, but is often
    # a mismatched or tribute-act listing (e.g. a deceased artist's "show"
    # that's actually a cover act or memorial concert); artists are no
    # longer skipped from searching just for being marked inactive (a
    # reunion would otherwise never be found), so this is the safety net
    # instead. Only runs for attended/manual scrape runs -- input() would
    # hang a scheduled/background one.
    if not is_inactive(artist_status, show["band"]):
        return True
    prompt = (f"    inactive artist {show['band']!r} has a listed show: "
              f"{show['date']} {show['city']} — {show['venue']} ({show['source']}). Keep it? [y/N] ")
    return input(prompt).strip().lower() == "y"
