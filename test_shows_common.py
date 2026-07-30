"""
Unit tests for shows_common.py's pure functions -- the most-reused logic
in the project (every scraper calls into these) and the kind of thing
that's already regressed silently once before: a comma inside a band
name once got truncated into the wrong artist name in shows.json, fixed
by hand after the fact (see git history, commit 0b3940c). No network
calls needed, so these run fast and belong in any future CI setup.

Run with: python -m unittest test_shows_common.py
"""

import unittest

import shows_common as common


class FoldCityForMatchingTests(unittest.TestCase):
    """Pure text-normalization behavior -- doesn't depend on
    city_name_aliases.json's current contents, only on the folding rules
    documented in _fold_city_for_matching's own comment."""

    def test_case_insensitive(self):
        self.assertEqual(common._fold_city_for_matching("Gdansk"), common._fold_city_for_matching("gdansk"))

    def test_diacritics_stripped(self):
        self.assertEqual(common._fold_city_for_matching("Gdańsk"), common._fold_city_for_matching("Gdansk"))

    def test_non_decomposing_letters(self):
        # NFKD doesn't decompose these into base+combining-mark, so they'd
        # survive plain diacritic-stripping untouched without the explicit
        # translation table -- this is the docstring's own example
        self.assertEqual(common._fold_city_for_matching("Bielsko-Biała"), common._fold_city_for_matching("Bielsko-biala"))

    def test_hyphen_vs_space(self):
        self.assertEqual(common._fold_city_for_matching("Stoke-On-Trent"), common._fold_city_for_matching("Stoke On Trent"))

    def test_apostrophe_stripped(self):
        self.assertEqual(common._fold_city_for_matching("St David's"), common._fold_city_for_matching("St Davids"))

    def test_trailing_parenthetical_stripped(self):
        self.assertEqual(common._fold_city_for_matching("Alicante (Alacant)"), common._fold_city_for_matching("Alicante"))

    def test_distinct_cities_stay_distinct(self):
        self.assertNotEqual(common._fold_city_for_matching("Paris"), common._fold_city_for_matching("London"))


class NormalizeCityAndBandTests(unittest.TestCase):
    """normalize_city/normalize_band depend on the alias tables loaded from
    city_name_aliases.json/band_name_aliases.json -- monkeypatched here so
    these tests don't depend on (or break from changes to) those files'
    real current contents."""

    def setUp(self):
        self._orig_city_aliases = common.CITY_NAME_ALIASES
        self._orig_band_aliases = common.BAND_NAME_ALIASES
        common.CITY_NAME_ALIASES = {"Wien": "Vienna"}
        common.BAND_NAME_ALIASES = {"Thee Oh Sees": "Oh Sees"}

    def tearDown(self):
        common.CITY_NAME_ALIASES = self._orig_city_aliases
        common.BAND_NAME_ALIASES = self._orig_band_aliases

    def test_known_city_alias_mapped(self):
        self.assertEqual(common.normalize_city("Wien"), "Vienna")

    def test_unknown_city_passthrough(self):
        self.assertEqual(common.normalize_city("Marseille"), "Marseille")

    def test_known_band_alias_mapped(self):
        self.assertEqual(common.normalize_band("Thee Oh Sees"), "Oh Sees")

    def test_unknown_band_passthrough(self):
        self.assertEqual(common.normalize_band("Protomartyr"), "Protomartyr")

    def test_none_and_whitespace_handled(self):
        self.assertEqual(common.normalize_city(None), "")
        self.assertEqual(common.normalize_city("  Marseille  "), "Marseille")


class ShowKeyTests(unittest.TestCase):
    def setUp(self):
        self._orig_city_aliases = common.CITY_NAME_ALIASES
        self._orig_band_aliases = common.BAND_NAME_ALIASES
        common.CITY_NAME_ALIASES = {}
        common.BAND_NAME_ALIASES = {}

    def tearDown(self):
        common.CITY_NAME_ALIASES = self._orig_city_aliases
        common.BAND_NAME_ALIASES = self._orig_band_aliases

    def make_show(self, **overrides):
        show = {"band": "Protomartyr", "date": "2026-10-19", "city": "Prague", "country": "CZ", "venue": "MeetFactory"}
        show.update(overrides)
        return show

    def test_same_core_fields_same_key_regardless_of_venue(self):
        # deliberate tradeoff documented in show_key()'s own comment: venue
        # text differing across sources shouldn't create a duplicate
        a = self.make_show(venue="MeetFactory")
        b = self.make_show(venue="Festival name as reported by another source")
        self.assertEqual(common.show_key(a), common.show_key(b))

    def test_city_formatting_difference_still_dedupes(self):
        # same word, different case/diacritics -- _fold_city_for_matching's
        # job. A genuinely different-language exonym (e.g. "München" vs
        # "Munich") is NOT folded here -- that's normalize_city's/the
        # alias table's job instead, a separate mechanism.
        a = self.make_show(city="GDAŃSK")
        b = self.make_show(city="gdansk")
        self.assertNotEqual(a["city"], b["city"])
        self.assertEqual(common.show_key(a), common.show_key(b))

    def test_different_date_different_key(self):
        a = self.make_show(date="2026-10-19")
        b = self.make_show(date="2026-10-20")
        self.assertNotEqual(common.show_key(a), common.show_key(b))

    def test_different_country_different_key(self):
        a = self.make_show(country="CZ")
        b = self.make_show(country="SK")
        self.assertNotEqual(common.show_key(a), common.show_key(b))


class ResolveSameDateConflictsTests(unittest.TestCase):
    def make_show(self, source, **overrides):
        show = {"band": "Protomartyr", "date": "2026-10-19", "city": "Prague", "country": "CZ", "venue": "MeetFactory", "source": source}
        show.update(overrides)
        return show

    def test_single_entry_passes_through_untouched(self):
        shows = [self.make_show("Bandsintown")]
        resolved, dropped = common.resolve_same_date_conflicts(shows)
        self.assertEqual(resolved, shows)
        self.assertEqual(dropped, [])

    def test_conflict_resolved_by_source_priority(self):
        # Bandsintown (priority 1) must beat Songkick (priority 3) per
        # shows_common.SOURCE_PRIORITY
        low = self.make_show("Songkick", city="Prague")
        high = self.make_show("Bandsintown", city="Different City")
        resolved, dropped = common.resolve_same_date_conflicts([low, high])
        self.assertEqual(resolved, [high])
        self.assertEqual(len(dropped), 1)
        kept, removed = dropped[0]
        self.assertEqual(kept["source"], "Bandsintown")
        self.assertEqual([r["source"] for r in removed], ["Songkick"])

    def test_unranked_source_loses_to_ranked_one(self):
        ranked = self.make_show("Skene")  # priority 0, highest
        unranked = self.make_show("SomeNewSourceNotInTheDict")
        resolved, dropped = common.resolve_same_date_conflicts([unranked, ranked])
        self.assertEqual(resolved, [ranked])

    def test_no_conflict_across_different_bands(self):
        a = self.make_show("Bandsintown", band="Protomartyr")
        b = self.make_show("Songkick", band="Dry Cleaning")
        resolved, dropped = common.resolve_same_date_conflicts([a, b])
        self.assertEqual(len(resolved), 2)
        self.assertEqual(dropped, [])


class ResolveFestivalDuplicatesTests(unittest.TestCase):
    def make_show(self, source, date, city="Castelbuono", country="IT", fest=None, venue="Some Venue"):
        return {"band": "Dry Cleaning", "date": date, "city": city, "country": country, "venue": venue, "source": source, "fest": fest}

    def test_merges_when_one_side_is_festival_and_within_window(self):
        festival_listing = self.make_show("Bandsintown", "2026-08-06", fest="FESTIVAL", venue="Ypsigrock Festival 2026")
        specific_venue = self.make_show("Spotify", "2026-08-07", fest=None, venue="Piazza Castello")
        resolved, dropped = common.resolve_festival_duplicates([festival_listing, specific_venue])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["source"], "Bandsintown")  # higher SOURCE_PRIORITY wins
        self.assertEqual(len(dropped), 1)

    def test_does_not_merge_when_neither_side_is_a_festival(self):
        # real false-positive case this guards against: legitimate
        # multi-night stands at the same venue (e.g. an opera run) must
        # NOT collapse into one just for being close in date/city
        night_one = self.make_show("Bandsintown", "2026-08-06", fest=None)
        night_two = self.make_show("Songkick", "2026-08-07", fest=None)
        resolved, dropped = common.resolve_festival_duplicates([night_one, night_two])
        self.assertEqual(len(resolved), 2)
        self.assertEqual(dropped, [])

    def test_does_not_merge_beyond_the_window(self):
        far_apart_a = self.make_show("Bandsintown", "2026-08-01", fest="FESTIVAL")
        far_apart_b = self.make_show("Songkick", "2026-08-31", fest=None)
        resolved, dropped = common.resolve_festival_duplicates([far_apart_a, far_apart_b])
        self.assertEqual(len(resolved), 2)

    def test_transitive_three_way_cluster_merges(self):
        # a<->b qualifies (gap 4, a is festival), b<->c qualifies (gap 4, b
        # is festival), but a<->c does NOT directly qualify (gap 8, over
        # the 5-day window) -- all three must still end up in one cluster
        # via union-find's transitivity, connected through b (matches the
        # real "Maruja | Trento" 3-way case this was built for)
        a = self.make_show("Songkick", "2026-08-01", fest="FESTIVAL")
        b = self.make_show("Bandsintown", "2026-08-05", fest="FESTIVAL")
        c = self.make_show("Ticketmaster", "2026-08-09", fest=None)
        resolved, dropped = common.resolve_festival_duplicates([a, b, c])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["source"], "Bandsintown")
        self.assertEqual(len(dropped[0][1]), 2)

    def test_different_city_does_not_merge(self):
        a = self.make_show("Bandsintown", "2026-08-06", city="Castelbuono", fest="FESTIVAL")
        b = self.make_show("Songkick", "2026-08-07", city="Grottaglie", fest=None)
        resolved, dropped = common.resolve_festival_duplicates([a, b])
        self.assertEqual(len(resolved), 2)


if __name__ == "__main__":
    unittest.main()
