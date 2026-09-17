"""
Compares shows.json before/after a scrape run and prints an HTML snippet
listing the shows that are new (by shows_common.show_key -- the same
band+date+city+country key the dedup logic uses, so a show that just
picked up a second source isn't reported as "new"). Used by the biweekly
scrape workflow to build the email summary body -- the workflow sends
this as text/html so each show's `url` renders as a clickable link.

Usage: python diff_shows.py <before.json> <after.json>
Prints one <p> per new show; prints nothing if there are none.
"""

import html
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
        text = f"{s['date']}  {s['band']} -- {s['city']}, {s['country']}"
        if s.get("venue"):
            text += f" ({s['venue']})"
        if s.get("fest"):
            text += f" [{s['fest']}]"
        text = html.escape(text)
        if s.get("url"):
            text = f'<a href="{html.escape(s["url"])}">{text}</a>'
        print(f"<p>{text}</p>")


if __name__ == "__main__":
    main()
