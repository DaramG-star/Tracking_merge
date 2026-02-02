#!/usr/bin/env python3
"""Unit tests for ingest.time_ordered_buffer (TimeOrderedFrameBuffer)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.time_ordered_buffer import TimeOrderedFrameBuffer


class TestTimeOrderedFrameBuffer(unittest.TestCase):
    def test_put_get_oldest_order(self):
        buf = TimeOrderedFrameBuffer(["A", "B", "C"], maxlen_per_cam=10)
        buf.put("A", np.zeros((10, 10, 3), dtype=np.uint8), 100.0)
        buf.put("B", np.ones((10, 10, 3), dtype=np.uint8), 99.0)
        buf.put("C", np.ones((10, 10, 3), dtype=np.uint8) * 2, 101.0)
        item = buf.get_oldest()
        self.assertIsNotNone(item)
        cam, frame, ts = item
        self.assertEqual(cam, "B")
        self.assertEqual(ts, 99.0)
        item2 = buf.get_oldest()
        self.assertIsNotNone(item2)
        self.assertEqual(item2[0], "A")
        self.assertEqual(item2[2], 100.0)
        item3 = buf.get_oldest()
        self.assertIsNotNone(item3)
        self.assertEqual(item3[0], "C")
        self.assertEqual(item3[2], 101.0)
        self.assertIsNone(buf.get_oldest())

    def test_get_oldest_empty_returns_none(self):
        buf = TimeOrderedFrameBuffer(["A"], maxlen_per_cam=5)
        self.assertIsNone(buf.get_oldest())

    def test_buffer_lengths(self):
        buf = TimeOrderedFrameBuffer(["X", "Y"], maxlen_per_cam=5)
        buf.put("X", np.zeros((5, 5, 3), dtype=np.uint8), 1.0)
        buf.put("X", np.zeros((5, 5, 3), dtype=np.uint8), 2.0)
        buf.put("Y", np.zeros((5, 5, 3), dtype=np.uint8), 1.5)
        lengths = buf.buffer_lengths()
        self.assertEqual(lengths["X"], 2)
        self.assertEqual(lengths["Y"], 1)

    def test_get_all_cam_ids(self):
        buf = TimeOrderedFrameBuffer(["USB_LOCAL", "RPI_USB1"], maxlen_per_cam=10)
        self.assertEqual(buf.get_all_cam_ids(), ["USB_LOCAL", "RPI_USB1"])


if __name__ == "__main__":
    unittest.main()
