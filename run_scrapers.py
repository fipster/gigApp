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

    print(f"\n==== enrich_flights ====")
    enrich_flights.main()


if __name__ == "__main__":
    main()
