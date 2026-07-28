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

import scrape_ticketmaster
import scrape_fienta
import scrape_kultuurikava
import scrape_allevents_lt
import scrape_songkick
import scrape_bandsintown
import shows_common as common
import enrich_flights

FREE_SCRAPERS = [
    scrape_ticketmaster,
    scrape_fienta,
    scrape_kultuurikava,
    scrape_allevents_lt,
    scrape_songkick,
]

PAID_SCRAPERS = [
    scrape_bandsintown,  # last: costs money per run (Apify)
]


def main():
    for scraper in FREE_SCRAPERS + PAID_SCRAPERS:
        print(f"\n==== {scraper.__name__} ====")
        scraper.main()

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
