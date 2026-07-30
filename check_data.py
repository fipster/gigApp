"""
Validates shows.json and artists.csv against the schema/vocabulary the
rest of this project (scrapers, app.js) assumes but nothing previously
enforced. Standalone script, not a test suite -- matches this project's
existing tooling style (run by hand, or after editing artists.csv by
hand, per CLAUDE.md).

This exists because every issue it checks for has already happened for
real at least once: a stray "DJ"/"est" value crossing between artists.csv's
category and priority columns silently produced a "priority": "DJ" on
live shows the UI's filter chips don't recognize (see git history for the
fix), and a comma inside a band name once got truncated into a wrong
artist name in shows.json. A script that would have caught both
automatically, rather than by eyeballing the live site, is the actual
fix -- see CLAUDE.md's "Where the good docs already are" for why this
project leans on scripts like this instead of a test framework.

Exit code is nonzero if anything fails, so this can be used as a
pre-commit hook or CI check later without extra wiring.
"""

import csv
import json
import sys
from collections import Counter
from datetime import datetime

import shows_common as common

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VALID_PRIORITIES = {"", "1", "2", "est"}
VALID_CATEGORIES = {"", "DJ"}
REQUIRED_SHOW_FIELDS = ("band", "date", "city", "country", "venue")


def check_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def check_shows(errors):
    shows = common.load_shows()
    if not shows:
        errors.append("shows.json is empty or missing")
        return

    with open(common.COUNTRIES_JSON, encoding="utf-8") as f:
        valid_countries = set(json.load(f).keys())

    seen_keys = Counter()
    for i, s in enumerate(shows):
        loc = f"shows.json[{i}] ({s.get('band', '?')!r} / {s.get('date', '?')!r})"
        for field in REQUIRED_SHOW_FIELDS:
            if not (s.get(field) or "").strip():
                errors.append(f"{loc}: empty required field {field!r}")
        if "date" in s and not check_date(s["date"]):
            errors.append(f"{loc}: date {s['date']!r} is not YYYY-MM-DD")
        if s.get("priority", "") not in VALID_PRIORITIES:
            errors.append(f"{loc}: priority {s.get('priority')!r} not in {sorted(VALID_PRIORITIES)}")
        if s.get("country") and s["country"] not in valid_countries:
            errors.append(f"{loc}: country {s['country']!r} not a known country code")
        if s.get("band") and s.get("date") and s.get("city") and s.get("country"):
            seen_keys[common.show_key(s)] += 1

    for key, count in seen_keys.items():
        if count > 1:
            errors.append(f"shows.json: {count} exact-duplicate entries for {key}")

    print(f"shows.json: {len(shows)} shows checked")


def check_artists(errors):
    with open(common.ARTISTS_CSV, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)

    seen_names = Counter()
    for i, row in enumerate(rows, start=2):  # +2: header is row 1, csv rows are 1-indexed
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        seen_names[name] += 1

        category = row[4].strip() if len(row) > 4 else ""
        priority = row[5].strip() if len(row) > 5 else ""
        if category not in VALID_CATEGORIES:
            errors.append(f"artists.csv:{i} ({name!r}): category {category!r} not in {sorted(VALID_CATEGORIES)}")
        if priority not in VALID_PRIORITIES:
            errors.append(f"artists.csv:{i} ({name!r}): priority {priority!r} not in {sorted(VALID_PRIORITIES)}")

    for name, count in seen_names.items():
        if count > 1:
            errors.append(f"artists.csv: {name!r} appears {count} times")

    print(f"artists.csv: {sum(seen_names.values())} artists checked")


def main():
    errors = []
    check_shows(errors)
    check_artists(errors)

    if errors:
        print(f"\n{len(errors)} problem(s) found:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
