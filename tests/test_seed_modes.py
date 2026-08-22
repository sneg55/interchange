"""The console seed's churn mode really does clear R5's two gates.

Run with: python3 -m unittest discover -s tests -v

The default six-cycle, five-minute seed gives 25 minutes of history, so R5
returns INSUFFICIENT_HISTORY for every publisher and the console's Churn column
reads the same on all forty rows. That is R5 behaving correctly and the board
reporting it honestly, but it means the column demonstrates nothing.

`--churn-window` exists to seed a history R5 can actually speak about. Its two
constants are derived from R5's own thresholds rather than typed in, and this
pins the derivation: if `R5_WINDOW_SECONDS` or `R5_MIN_POLLS` ever moves, a seed
that no longer clears the gates fails here rather than on the board.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dump_console_data import (
    CHURN_CYCLES,
    CHURN_STEP_SECONDS,
    DEFAULT_STEP_SECONDS,
)
from src.features.trust_scorer.churn import R5_MIN_POLLS, R5_WINDOW_SECONDS


class TestChurnWindowSeed(unittest.TestCase):
    def test_the_span_reaches_back_a_whole_window(self):
        """Gate one: the oldest retained poll must be at or before now minus the
        window, or R5 cannot tell a frozen feed from a young one."""
        span = (CHURN_CYCLES - 1) * CHURN_STEP_SECONDS
        self.assertGreaterEqual(span, R5_WINDOW_SECONDS)

    def test_enough_polls_land_inside_the_window(self):
        """Gate two, and the one a longer step alone would fail: spanning the
        window with three sparse polls still leaves R5 unable to speak."""
        in_window = R5_WINDOW_SECONDS // CHURN_STEP_SECONDS + 1
        self.assertGreaterEqual(in_window, R5_MIN_POLLS)

    def test_it_is_the_cheapest_seed_that_does_so(self):
        """One cycle fewer fails a gate. Stated as a test because every extra
        cycle is a full pass over the whole fixture fleet, and a seed that
        quietly grew to thirty cycles would cost minutes per run for nothing."""
        span = (CHURN_CYCLES - 2) * CHURN_STEP_SECONDS
        in_window = min(CHURN_CYCLES - 1, R5_WINDOW_SECONDS // CHURN_STEP_SECONDS + 1)
        self.assertTrue(span < R5_WINDOW_SECONDS or in_window < R5_MIN_POLLS)

    def test_the_default_seed_still_cannot_clear_them(self):
        """The control. The default is deliberately short and fast, and this
        records that its Churn column reading INSUFFICIENT_HISTORY everywhere is
        the expected outcome rather than a defect someone should chase."""
        span = (6 - 1) * DEFAULT_STEP_SECONDS
        self.assertLess(span, R5_WINDOW_SECONDS)


if __name__ == "__main__":
    unittest.main()
