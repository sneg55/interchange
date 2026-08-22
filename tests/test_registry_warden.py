"""Registry Warden and cadence parsing. Section 6.1 and 6.3.

Run with: python3 -m unittest discover -s tests -v

Every assertion here is about shape or direction, never about a live figure. The
registry moves: NJIT coverage ranged 87.3 to 100.0 percent across five runs in
two days, so a test asserting an exact percentage would fail for a reason that
has nothing to do with the code.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.error_ids import AppError
from src.features.registry_warden.cadence import (
    DEFAULT_CADENCE_SECONDS,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
    cadence_or_default,
    clamp,
    parse_cadence,
)
from src.features.registry_warden.records import PublisherRecord, needs_api_key, record_from_entry
from src.features.registry_warden.warden import RegistryWarden
from src.services.fixtures import FixtureRegistrySource, FixtureSet

NOW = "2026-08-07T00:00:00+00:00"
LATER = "2026-08-07T01:00:00+00:00"


def entry(org="Test DOT", feedname="test", **kw):
    base = {
        "issuingorganization": org,
        "feedname": feedname,
        "url": {"url": "https://example.test/feed.json"},
        "version": "4.2",
        "datafeed_frequency_update": "5m",
        "active": True,
    }
    base.update(kw)
    return base


class TestCadence(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_cadence("60s"), 60)
        self.assertEqual(parse_cadence("1m"), 60)
        self.assertEqual(parse_cadence("72h"), 259200)
        self.assertEqual(parse_cadence("168h"), 604800)

    def test_absent_is_none_not_default(self):
        """None and the default must stay distinguishable.

        Returning 3600 for an absent cadence would make it identical to a
        declared 60m, and section 6.1 emits CADENCE_CHANGED on exactly that
        difference.
        """
        self.assertIsNone(parse_cadence(None))
        self.assertIsNone(parse_cadence("  "))
        self.assertEqual(cadence_or_default(None), DEFAULT_CADENCE_SECONDS)

    def test_unparseable_raises_but_does_not_drop_the_publisher(self):
        with self.assertRaises(AppError):
            parse_cadence("every so often")
        self.assertEqual(cadence_or_default("every so often"), DEFAULT_CADENCE_SECONDS)

    def test_zero_is_rejected(self):
        with self.assertRaises(AppError):
            parse_cadence("0m")

    def test_clamp_bounds(self):
        self.assertEqual(clamp(60), MIN_POLL_SECONDS)
        self.assertEqual(clamp(604800), MAX_POLL_SECONDS)
        self.assertEqual(clamp(900), 900)


class TestRecordDerivation(unittest.TestCase):
    def test_key_gated_starts_no_access_and_is_never_polled(self):
        record = record_from_entry(entry(needapikey=True), NOW)
        self.assertEqual(record.fleet_state, "NO_ACCESS")
        self.assertFalse(record.is_pollable)
        self.assertEqual(record.poll_interval_seconds, 0)

    def test_open_publisher_starts_watch_not_admit(self):
        """Nothing has been observed. ADMIT here would record 'not checked' as
        'passed', which is the error the whole system exists to catch."""
        record = record_from_entry(entry(), NOW)
        self.assertEqual(record.fleet_state, "WATCH")
        self.assertEqual(record.churn_status, "INSUFFICIENT_HISTORY")

    def test_absent_needapikey_means_no_key(self):
        """26 of 40 active entries omit the field. Reading absence as 'gated'
        would drop two thirds of the fleet."""
        self.assertFalse(needs_api_key(entry()))
        self.assertFalse(needs_api_key(entry(needapikey=False)))
        self.assertTrue(needs_api_key(entry(needapikey=True)))

    def test_round_trips_through_a_document(self):
        record = record_from_entry(entry(), NOW)
        restored = PublisherRecord.from_doc(record.to_doc())
        self.assertEqual(restored, record)


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.warden = RegistryWarden(FixtureRegistrySource())

    def test_provisions_new_publishers(self):
        result = self.warden.reconcile([entry()], {}, NOW)
        self.assertTrue(result.accepted)
        self.assertEqual(len(result.records), 1)
        self.assertEqual([e.event for e in result.events], ["PROVISIONED"])

    def test_organization_name_is_not_the_key(self):
        """Colorado DOT appears twice, as cdot (WZDx 4.2) and cdot_cwz (CWZ 1.0).
        A fleet keyed on the organization collapses them into one agent."""
        entries = [
            entry("Colorado DOT", "cdot", version="4.2"),
            entry("Colorado DOT", "cdot_cwz", version="CWZ 1.0"),
        ]
        result = self.warden.reconcile(entries, {}, NOW)
        self.assertEqual(len(result.records), 2)

    def test_url_change_preserves_history(self):
        first = self.warden.reconcile([entry()], {}, NOW)
        moved = entry(url={"url": "https://example.test/v2/feed.json"})
        second = self.warden.reconcile([moved], first.records, LATER)
        record = next(iter(second.records.values()))
        self.assertEqual(record.first_seen, NOW)  # history intact
        self.assertEqual(record.url, "https://example.test/v2/feed.json")
        self.assertIn("URL_CHANGED", [e.event for e in second.events])
        self.assertNotIn("PROVISIONED", [e.event for e in second.events])

    def test_one_absence_is_not_a_delisting(self):
        state = self.warden.reconcile([entry(), entry(feedname="other")], {}, NOW)
        for _ in range(2):
            state = self.warden.reconcile([entry()], state.records, LATER)
            self.assertIsNone(state.records["Test DOT|other"].decommissioned_at)
        state = self.warden.reconcile([entry()], state.records, LATER)
        self.assertIsNotNone(state.records["Test DOT|other"].decommissioned_at)
        self.assertIn("DECOMMISSIONED", [e.event for e in state.events])

    def test_reappearing_before_the_third_pull_clears_the_count(self):
        state = self.warden.reconcile([entry(), entry(feedname="other")], {}, NOW)
        state = self.warden.reconcile([entry()], state.records, LATER)
        state = self.warden.reconcile([entry(), entry(feedname="other")], state.records, LATER)
        self.assertEqual(state.records["Test DOT|other"].absent_pull_count, 0)
        self.assertIsNone(state.records["Test DOT|other"].decommissioned_at)

    def test_decommission_disables_it_never_deletes(self):
        state = self.warden.reconcile([entry(), entry(feedname="other")], {}, NOW)
        for _ in range(3):
            state = self.warden.reconcile([entry()], state.records, LATER)
        self.assertIn("Test DOT|other", state.records)
        self.assertFalse(state.records["Test DOT|other"].is_pollable)

    def test_short_read_is_rejected_without_mutating_anything(self):
        """A partial Socrata response must not decommission the fleet in one run."""
        entries = [entry(feedname=f"f{i}") for i in range(10)]
        state = self.warden.reconcile(entries, {}, NOW)
        result = self.warden.reconcile(entries[:2], state.records, LATER)
        self.assertFalse(result.accepted)
        self.assertEqual(result.events, [])
        self.assertTrue(all(r.absent_pull_count == 0 for r in result.records.values()))

    def test_duplicate_key_in_one_pull_raises(self):
        with self.assertRaises(AppError):
            self.warden.reconcile([entry(), entry()], {}, NOW)

    def test_losing_the_key_requirement_admits_to_watch_not_admit(self):
        state = self.warden.reconcile([entry(needapikey=True)], {}, NOW)
        self.assertEqual(state.records["Test DOT|test"].fleet_state, "NO_ACCESS")
        state = self.warden.reconcile([entry()], state.records, LATER)
        record = state.records["Test DOT|test"]
        self.assertEqual(record.fleet_state, "WATCH")
        self.assertTrue(record.is_pollable)
        self.assertEqual(record.poll_interval_seconds, MIN_POLL_SECONDS)

    def test_cadence_change_compares_declared_not_clamped(self):
        """72h and 168h both clamp to the ceiling, so only a comparison of the
        DECLARED values can see the change. A post-clamp comparison passes the
        30m case and silently misses this one."""
        state = self.warden.reconcile([entry(datafeed_frequency_update="72h")], {}, NOW)
        result = self.warden.reconcile(
            [entry(datafeed_frequency_update="168h")], state.records, LATER
        )
        self.assertIn("CADENCE_CHANGED", [e.event for e in result.events])
        record = result.records["Test DOT|test"]
        self.assertEqual(record.declared_cadence_seconds, 604800)
        self.assertEqual(record.poll_interval_seconds, MAX_POLL_SECONDS)

    def test_a_clamped_change_still_moves_the_interval(self):
        state = self.warden.reconcile([entry(datafeed_frequency_update="1m")], {}, NOW)
        result = self.warden.reconcile(
            [entry(datafeed_frequency_update="30m")], state.records, LATER
        )
        self.assertEqual(result.records["Test DOT|test"].poll_interval_seconds, 1800)

    def test_the_short_read_denominator_excludes_decommissioned_history(self):
        """Delisting disables rather than deletes, so decommissioned records
        accumulate forever. Counting them in the denominator would eventually
        make a complete registry pull look like a short read and freeze the
        warden permanently."""
        fleet = [entry(feedname=f"f{i}") for i in range(40)]
        state = self.warden.reconcile(fleet, {}, NOW)
        # 41 delistings retained from earlier years, exactly the shape section
        # 6.1 promises to keep.
        for i in range(41):
            record = record_from_entry(entry(feedname=f"gone{i}"), NOW)
            record.decommissioned_at = NOW
            state.records[record.publisher_key] = record
        result = self.warden.reconcile(fleet, state.records, LATER)
        self.assertTrue(result.accepted, result.rejected)
        self.assertEqual(sum(1 for r in result.records.values() if r.decommissioned_at), 41)

    def test_a_genuine_short_read_is_still_rejected_alongside_retained_history(self):
        fleet = [entry(feedname=f"f{i}") for i in range(40)]
        state = self.warden.reconcile(fleet, {}, NOW)
        for i in range(41):
            record = record_from_entry(entry(feedname=f"gone{i}"), NOW)
            record.decommissioned_at = NOW
            state.records[record.publisher_key] = record
        result = self.warden.reconcile(fleet[:5], state.records, LATER)
        self.assertFalse(result.accepted)

    def test_going_key_gated_does_not_erase_a_quarantine(self):
        """NO_ACCESS is not a trust verdict. A publisher that was QUARANTINE has
        not recovered by becoming unreachable, and coming back at WATCH would
        discard both the finding and its recovery hysteresis."""
        state = self.warden.reconcile([entry()], {}, NOW)
        state.records["Test DOT|test"].fleet_state = "QUARANTINE"
        state = self.warden.reconcile([entry(needapikey=True)], state.records, LATER)
        self.assertEqual(state.records["Test DOT|test"].fleet_state, "NO_ACCESS")
        state = self.warden.reconcile([entry()], state.records, LATER)
        self.assertEqual(state.records["Test DOT|test"].fleet_state, "QUARANTINE")

    def test_a_never_observed_publisher_returns_to_watch(self):
        state = self.warden.reconcile([entry(needapikey=True)], {}, NOW)
        state = self.warden.reconcile([entry()], state.records, LATER)
        self.assertEqual(state.records["Test DOT|test"].fleet_state, "WATCH")


class TestAgainstTheSnapshot(unittest.TestCase):
    """Shape assertions against the real registry, never exact live figures."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.entries = FixtureRegistrySource(cls.fixtures).active_entries()

    def test_every_active_entry_provisions(self):
        result = RegistryWarden(FixtureRegistrySource(self.fixtures)).reconcile(
            self.entries, {}, NOW
        )
        self.assertTrue(result.accepted)
        self.assertEqual(len(result.records), len(self.entries))

    def test_the_pair_is_unique_across_the_live_registry(self):
        keys = {(e["issuingorganization"], e.get("feedname")) for e in self.entries}
        self.assertEqual(len(keys), len(self.entries))

    def test_organization_name_alone_is_not_unique(self):
        orgs = [e["issuingorganization"] for e in self.entries]
        self.assertLess(len(set(orgs)), len(orgs), "expected at least one repeated org")

    def test_every_declared_cadence_parses(self):
        for e in self.entries:
            declared = e.get("datafeed_frequency_update")
            if declared:
                self.assertIsInstance(parse_cadence(declared), int, declared)

    def test_gated_publishers_are_excluded_from_polling_not_counted_as_passing(self):
        result = RegistryWarden(FixtureRegistrySource(self.fixtures)).reconcile(
            self.entries, {}, NOW
        )
        gated = [r for r in result.records.values() if r.needs_api_key]
        self.assertTrue(gated, "the live registry has key-gated entries")
        for record in gated:
            self.assertEqual(record.fleet_state, "NO_ACCESS")
            self.assertFalse(record.is_pollable)


if __name__ == "__main__":
    unittest.main()
