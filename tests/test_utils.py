#!/usr/bin/env python3
"""Unit tests for logic.utils."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logic.utils import extract_ts, ts_to_seconds


class TestUtils(unittest.TestCase):
    def test_extract_ts_full_uid(self):
        self.assertEqual(extract_ts("20260127_081946_617"), "081946_617")

    def test_extract_ts_short(self):
        self.assertEqual(extract_ts("081946_617"), "081946_617")

    def test_extract_ts_filename(self):
        self.assertEqual(extract_ts("081946_617.jpg"), "081946_617")

    def test_extract_ts_no_match(self):
        self.assertIsNone(extract_ts("no_numbers"))

    def test_ts_to_seconds(self):
        s = ts_to_seconds("081946_617")
        expected = 8 * 3600 + 19 * 60 + 46 + 617 / 1000
        self.assertLess(abs(s - expected), 0.001)

    def test_ts_to_seconds_invalid(self):
        self.assertEqual(ts_to_seconds(""), 0.0)


if __name__ == "__main__":
    unittest.main()
