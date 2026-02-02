#!/usr/bin/env python3
"""Unit tests for logic.matcher (FIFOGlobalMatcher)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logic.matcher import FIFOGlobalMatcher


class TestFIFOGlobalMatcher(unittest.TestCase):
    def test_add_scanner_data(self):
        m = FIFOGlobalMatcher()
        m.add_scanner_data("uid_001", "XSEA", 100.0)
        self.assertIn("uid_001", m.masters)
        self.assertEqual(m.masters["uid_001"]["route_code"], "XSEA")
        self.assertEqual(m.masters["uid_001"]["last_cam"], "Scanner")
        self.assertEqual(len(m.queues["q_scan"]), 1)
        self.assertEqual(m.queues["q_scan"][0][0], "uid_001")

    def test_try_match_usb_local_empty_scan(self):
        m = FIFOGlobalMatcher()
        mid = m.try_match("USB_LOCAL", 106.0, 50, "USB_LOCAL_001")
        self.assertIsNone(mid)
        self.assertEqual(m.last_match_attempt["status"], "EMPTY_QUEUE")

    def test_try_match_usb_local_success(self):
        m = FIFOGlobalMatcher()
        m.add_scanner_data("uid_001", "XSEA", 100.0)
        mid = m.try_match("USB_LOCAL", 106.0, 50, "USB_LOCAL_001")
        self.assertEqual(mid, "uid_001")
        self.assertEqual(m.masters["uid_001"]["last_cam"], "USB_LOCAL")
        self.assertEqual(m.masters["uid_001"]["last_time"], 106.0)
        self.assertEqual(len(m.queues["q_scan"]), 0)
        self.assertEqual(len(m.queues["q01"]), 1)

    def test_try_match_out_of_margin(self):
        m = FIFOGlobalMatcher()
        m.add_scanner_data("uid_001", "XSEA", 100.0)
        mid = m.try_match("USB_LOCAL", 120.0, 50, "USB_LOCAL_001")
        self.assertIsNone(mid)
        self.assertEqual(m.last_match_attempt["status"], "OUT_OF_MARGIN")

    def test_get_next_cam(self):
        m = FIFOGlobalMatcher()
        self.assertEqual(m._get_next_cam("XSEA", "Scanner"), "USB_LOCAL")
        self.assertIsNone(m._get_next_cam("XSEA", "RPI_USB3"))
        self.assertEqual(m._get_next_cam("XSEB", "RPI_USB3"), "RPI_USB3_EOL")

    def test_resolve_pending_not_pending_or_tracking(self):
        m = FIFOGlobalMatcher()
        m.add_scanner_data("uid_001", "XSEA", 100.0)
        m.try_match("USB_LOCAL", 106.0, 50, "u1")
        # After match, status is TRACKING; resolve_pending can still return DISAPPEAR if now_s is past expected.
        # So set status to a terminal state and then resolve_pending should return None.
        m.masters["uid_001"]["status"] = "PICKUP"
        result = m.resolve_pending("uid_001", 200.0)
        self.assertIsNone(result)

    def test_cancel_pending(self):
        m = FIFOGlobalMatcher()
        m.add_scanner_data("uid_001", "XSEA", 100.0)
        self.assertEqual(len(m.queues["q_scan"]), 1)
        m.cancel_pending("Scanner", "uid_001")
        self.assertEqual(len(m.queues["q_scan"]), 0)


if __name__ == "__main__":
    unittest.main()
