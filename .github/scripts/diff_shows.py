"""
Compares shows.json before/after a scrape run and prints the shows that
are new (by shows_common.show_key -- the same band+date+city+country key
the dedup logic uses, so a show that just picked up a second source isn't
reported as "new"). Used by the biweekly scrape workflow to build the
email summary body.

Usage: python diff_shows.py <before.json> <after.json>
Prints one line per new show; prints nothing if there are none.
"""

import json
import sys

sys.path.insert(0, ".")
import shows_common as common


def main():
    before_path, after_path = sys.argv[1], sys.argv[2]
    with open(before_path, encoding="utf-8") as f:
        before = json.load(f)
    with open(after_path, encoding="utf-8") as f:
        after = json.load(f)

    before_keys = {common.show_key(s) for s in before}
    new_shows = [s for s in after if common.show_key(s) not in before_keys]
    new_shows.sort(key=lambda s: (s["date"], s["band"]))

    for s in new_shows:
        line = f"{s['date']}  {s['band']} -- {s['city']}, {s['country']}"
        if s.get("venue"):
            line += f" ({s['venue']})"
        if s.get("fest"):
            line += f" [{s['fest']}]"
        print(line)


if __name__ == "__main__":
    main()
