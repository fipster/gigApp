"""
Runs every source scraper in a fixed priority order -- free sources first,
paid sources last -- then flight enrichment. Paid sources cost real money
per run (e.g. scrape_bandsintown.py via Apify), so they should only run
after the free ones have already found what they can; this also means a
paid run's dedup step benefits from the free sources' results already
being in shows.json.

To add a new source scraper, import its module below and add it to
FREE_SCRAPERS or PAID_SCRAPERS as appropriate. Order within each list
also matters (earlier = runs first).
"""

import sys

import scrape_ticketmaster
import scrape_fienta
import scrape_kultuurikava
import scrape_allevents_lt
import scrape_songkick
import scrape_skene
import scrape_spotify
import scrape_bandsintown
import shows_common as common
import enrich_flights

FREE_SCRAPERS = [
    scrape_ticketmaster,
    scrape_fienta,
    scrape_kultuurikava,
    scrape_allevents_lt,
    scrape_songkick,
    scrape_skene,
    scrape_spotify,  # slowest free source (stage 2 drives a real Playwright browser) -- runs last among free sources
]

PAID_SCRAPERS = [
    scrape_bandsintown,  # last: costs money per run (Apify)
]


def main():
    common.tee_stdout_to_log("run_scrapers")

    for scraper in FREE_SCRAPERS + PAID_SCRAPERS:
        print(f"\n==== {scraper.__name__} ====")
        try:
            scraper.main()
        except SystemExit as e:
            # a scraper's own deliberate sys.exit() (e.g. Ticketmaster's
            # quota-exhaustion abort) -- worth knowing about, but shouldn't
            # take down the rest of the pipeline (dedup/enrich_flights
            # still need to run on whatever every other scraper found)
            print(f"{scraper.__name__} exited early: {e}", file=sys.stderr)
        except Exception as e:
            print(f"{scraper.__name__} failed: {e}", file=sys.stderr)

    print(f"\n==== resolve_date_conflicts ====")
    shows = common.load_shows()
    shows, same_date_dropped = common.resolve_same_date_conflicts(shows)
    shows, festival_dropped = common.resolve_festival_duplicates(shows)
    for kept, removed in same_date_dropped + festival_dropped:
        removed_desc = ", ".join(f"{r['source']}/{r['city']}/{r['venue']}" for r in removed)
        print(f"  {kept['band']} | {kept['date']}: kept {kept['source']}/{kept['city']}/{kept['venue']}, dropped {removed_desc}")
    result = common.save_shows(shows)
    print(f"Done. {len(result)} shows ({len(same_date_dropped)} same-date conflicts, {len(festival_dropped)} festival dupes resolved).")

    print(f"\n==== enrich_priority ====")
    priorities = common.load_artist_priorities()
    for s in result:
        s["priority"] = priorities.get(s["band"], "")
    result = common.save_shows(result)
    print(f"Done. {sum(1 for s in result if s['priority'])} of {len(result)} shows tagged with a priority.")

    print(f"\n==== enrich_flights ====")
    enrich_flights.main()


if __name__ == "__main__":
    main()
