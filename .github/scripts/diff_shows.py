"""
Compares shows.json before/after a scrape run and prints an HTML snippet
listing the shows that are new (by shows_common.show_key -- the same
band+date+city+country key the dedup logic uses, so a show that just
picked up a second source isn't reported as "new"), grouped by artist.
Used by the biweekly scrape workflow to build the email summary body --
the workflow sends this as text/html so each show's `url` renders as a
clickable link.

Also writes `count=<N>` to $GITHUB_OUTPUT (if set) so the workflow can
put the new-show count in the email subject without having to count HTML
lines itself, which no longer lines up 1:1 with new shows now that each
artist gets its own heading.

Usage: python diff_shows.py <before.json> <after.json>
Prints nothing (besides the GITHUB_OUTPUT write) if there are no new shows.
"""

import html
import itertools
import json
import os
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
    new_shows.sort(key=lambda s: (s["band"], s["date"]))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"count={len(new_shows)}\n")

    for band, group in itertools.groupby(new_shows, key=lambda s: s["band"]):
        print(f"<p><strong>{html.escape(band)}</strong></p>")
        print("<ul>")
        for s in group:
            text = f"{s['date']} -- {s['city']}, {s['country']}"
            if s.get("venue"):
                text += f" ({s['venue']})"
            if s.get("fest"):
                text += f" [{s['fest']}]"
            text = html.escape(text)
            if s.get("url"):
                text = f'<a href="{html.escape(s["url"])}">{text}</a>'
            print(f"<li>{text}</li>")
        print("</ul>")


if __name__ == "__main__":
    main()
