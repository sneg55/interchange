"""Registry to poll to verdict, over the real snapshot. Sections 6.1 to 6.4.

Run with: python3 -m unittest discover -s tests -v

Section 6.4 states expected verdicts against live data: Utah DOT quarantines on
R2 and independently on R4, Hawaii DOT quarantines on R2. Those are claims about
the product, not about the code, and they are worth failing a build over. What is
NOT asserted is any exact count or percentage: the feeds move, and section 5
records NJIT coverage ranging 87.3 to 100.0 percent across five runs in two days.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.poller import Poller
from src.features.registry_warden.records import entry_url, publisher_key
from src.features.registry_warden.warden import RegistryWarden
from src.features.trust_scorer.scorer import TrustScorer
from src.features.trust_scorer.verdicts import NOT_APPLICABLE, QUARANTINE
from src.services.fixtures import FixtureFeedSource, FixtureRegistrySource, FixtureSet
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry

# Later than any fixture's capture date, so staleness is measured against a fixed
# point rather than against the wall clock. Without this the suite's verdicts
# would drift as the snapshot ages.
NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)


class TestFleetEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.warden = RegistryWarden(FixtureRegistrySource(cls.fixtures))
        cls.records = cls.warden.reconcile(cls.warden.pull(), {}, NOW.isoformat()).records
        cls.poller = Poller(
            FixtureFeedSource(cls.fixtures),
            SchemaRegistry(FixtureSchemaLoader(cls.fixtures)),
        )
        cls.scorer = TrustScorer()

    def score(self, org, feedname):
        record = self.records[publisher_key(org, feedname)]
        observation = self.poller.poll(
            record.publisher_key, record.url, record.declared_version, now=NOW
        )
        result = self.scorer.score(
            observation, [], record.fleet_state, record.declared_cadence_seconds, NOW
        )
        return observation, result

    def verdicts(self, result):
        return {r.rule_id: r.verdict for r in result.evaluation.results}

    def test_utah_quarantines_on_r2_and_independently_on_r4(self):
        """The stronger result: R4 alone would still catch Utah if it began
        refreshing its timestamp, which no timestamp check would notice."""
        _, result = self.score("Utah DOT", "udot")
        verdicts = self.verdicts(result)
        self.assertEqual(verdicts["R2"], QUARANTINE)
        self.assertEqual(verdicts["R4"], QUARANTINE)
        self.assertEqual(result.state, QUARANTINE)
        self.assertEqual(result.transition.primary_rule_id, "R2")

    def test_utah_passes_the_official_schema_it_declares(self):
        """The point of the whole product. The federal validator says Utah is
        fine; R2 and R4 say otherwise, and both are looking at the same bytes."""
        observation, result = self.score("Utah DOT", "udot")
        self.assertEqual(observation.schema_error_count, 0)
        self.assertEqual(self.verdicts(result)["R3"], "ADMIT")

    def test_hawaii_quarantines_on_r2_but_r4_cannot_speak(self):
        """Hawaii publishes no event_status at all, so a contradiction
        percentage over zero active zones is undefined and must read as
        NOT_APPLICABLE rather than as a clean pass."""
        observation, result = self.score("Hawaii DOT", "hidot")
        verdicts = self.verdicts(result)
        self.assertEqual(verdicts["R2"], QUARANTINE)
        self.assertEqual(observation.active_count, 0)
        self.assertEqual(verdicts["R4"], NOT_APPLICABLE)

    def test_churn_is_never_measurable_from_a_single_poll(self):
        """INSUFFICIENT_HISTORY, not a demerit and not a pass. Without it every
        publisher looks frozen at fleet launch."""
        for org, feedname in (("Utah DOT", "udot"), ("Hawaii DOT", "hidot")):
            _, result = self.score(org, feedname)
            self.assertEqual(self.verdicts(result)["R5"], NOT_APPLICABLE, org)

    def test_every_captured_feed_polls_and_scores_without_raising(self):
        """A publisher that breaks the pipeline is worse than one that scores
        badly: it removes itself from the evidence rather than appearing in it."""
        scored = 0
        for entry in self.warden.pull():
            url = entry_url(entry)
            if self.fixtures.entry_for_url(url) is None:
                continue
            key = publisher_key(entry["issuingorganization"], entry["feedname"])
            record = self.records[key]
            observation = self.poller.poll(key, url, record.declared_version, now=NOW)
            result = self.scorer.score(
                observation, [], record.fleet_state, record.declared_cadence_seconds, NOW
            )
            self.assertIn(result.state, ("ADMIT", "WATCH", "QUARANTINE"), key)
            self.assertEqual(len(result.evaluation.results), 6, key)
            scored += 1
        self.assertGreaterEqual(scored, 8, "the snapshot should cover at least 8 feeds")

    def test_no_rule_returns_admit_for_want_of_an_input(self):
        """The invariant, checked across every captured publisher at once: a rule
        that could not run reports NOT_APPLICABLE, never ADMIT."""
        for entry in self.warden.pull():
            url = entry_url(entry)
            if self.fixtures.entry_for_url(url) is None:
                continue
            key = publisher_key(entry["issuingorganization"], entry["feedname"])
            record = self.records[key]
            observation = self.poller.poll(key, url, record.declared_version, now=NOW)
            result = self.scorer.score(
                observation, [], record.fleet_state, record.declared_cadence_seconds, NOW
            )
            verdicts = self.verdicts(result)
            if observation.schema_version_used == "SCHEMA_UNKNOWN":
                self.assertEqual(verdicts["R3"], NOT_APPLICABLE, key)
            if observation.active_count == 0:
                self.assertEqual(verdicts["R4"], NOT_APPLICABLE, key)
            if observation.update_age_seconds is None:
                self.assertEqual(verdicts["R2"], NOT_APPLICABLE, key)


if __name__ == "__main__":
    unittest.main()
