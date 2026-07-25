"""
Fills in flightTLL/flightRIX/note for every show in shows.json, using
direct_flights.json and city_hub_overrides.json. Source-agnostic: run
this after any scraper (scrape_ticketmaster.py, or future
scrape_<source>.py files) — it doesn't care where a show came from, only
its city and date. Makes no network calls, so it's free to re-run any
time the flight data or matching logic changes, without needing to
re-scrape from any source.
"""

import json
import re
import sys

import shows_common as common

DIRECT_FLIGHTS_JSON = "direct_flights.json"
CITY_HUB_OVERRIDES_JSON = "city_hub_overrides.json"

with open(DIRECT_FLIGHTS_JSON, encoding="utf-8") as f:
    DIRECT_FLIGHTS = json.load(f)["destinations"]

try:
    with open(CITY_HUB_OVERRIDES_JSON, encoding="utf-8") as f:
        CITY_HUB_OVERRIDES = json.load(f)
except FileNotFoundError:
    CITY_HUB_OVERRIDES = {}

NO_DIRECT = {"direct": False, "seasonal": False, "duration_minutes": None}

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_seasonal_months(seasonal_months):
    if "–" in seasonal_months or "-" in seasonal_months:
        start, end = re.split(r"[–-]", seasonal_months)
        start_idx = MONTH_NAMES.index(start)
        end_idx = MONTH_NAMES.index(end)
        months = set()
        i = start_idx
        while True:
            months.add(i)
            if i == end_idx:
                break
            i = (i + 1) % 12
        return months
    return {MONTH_NAMES.index(m.strip()) for m in seasonal_months.split(",")}


def in_season(event_date, seasonal_months):
    month_idx = int(event_date[5:7]) - 1
    return month_idx in parse_seasonal_months(seasonal_months)


def flight_info(city, origin, event_date):
    override = CITY_HUB_OVERRIDES.get(city, {})
    hub = override.get(f"{origin}_hub", city)
    info = dict(DIRECT_FLIGHTS.get(hub, {}).get(origin, NO_DIRECT))
    forced = override.get(f"{origin}_direct")
    if forced is not None:
        info["direct"] = forced
    elif info["seasonal"] and info.get("seasonal_months") and not in_season(event_date, info["seasonal_months"]):
        info["direct"] = False

    # RIX and TLL durations are close enough that we only track one number;
    # always report TLL's duration regardless of which origin this is for.
    tll_hub = override.get("TLL_hub", city)
    info["duration_minutes"] = DIRECT_FLIGHTS.get(tll_hub, {}).get("TLL", NO_DIRECT)["duration_minutes"]

    return info


def main():
    shows = common.load_shows()
    if not shows:
        sys.exit("shows.json is empty — run a scraper first.")

    for show in shows:
        show["flightTLL"] = flight_info(show["city"], "TLL", show["date"])
        show["flightRIX"] = flight_info(show["city"], "RIX", show["date"])
        show["note"] = CITY_HUB_OVERRIDES.get(show["city"], {}).get("note", "")

    result = common.save_shows(shows)
    print(f"Enriched {len(result)} shows with flight info.")


if __name__ == "__main__":
    main()
