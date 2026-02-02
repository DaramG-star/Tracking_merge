#!/usr/bin/env python3
"""Unit tests for ingest.frame_aggregator."""
import threading
import unittest
from pathlib import Path

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.frame_aggregator import FrameAggregator


class TestFrameAggregator(unittest.TestCase):
    def test_put_get_single_cam(self):
        agg = FrameAggregator()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        agg.put("cam1", frame, 123.45)
        out = agg.get("cam1")
        self.assertIsNotNone(out)
        f, ts = out
        self.assertEqual(f.shape, frame.shape)
        self.assertTrue(np.array_equal(f, frame))
        self.assertEqual(ts, 123.45)

    def test_get_returns_copy(self):
        agg = FrameAggregator()
        frame = np.ones((50, 50, 3), dtype=np.uint8)
        agg.put("cam1", frame, 1.0)
        out1, _ = agg.get("cam1")
        out2, _ = agg.get("cam1")
        self.assertIsNot(out1, out2)
        self.assertTrue(np.array_equal(out1, out2))

    def test_get_unknown_cam_returns_none(self):
        agg = FrameAggregator()
        self.assertIsNone(agg.get("nonexistent"))

    def test_get_all_cam_ids(self):
        agg = FrameAggregator()
        self.assertEqual(agg.get_all_cam_ids(), [])
        agg.put("cam1", np.zeros((10, 10, 3), dtype=np.uint8), 1.0)
        agg.put("cam2", np.zeros((10, 10, 3), dtype=np.uint8), 2.0)
        ids = agg.get_all_cam_ids()
        self.assertEqual(set(ids), {"cam1", "cam2"})

    def test_put_overwrites_previous(self):
        agg = FrameAggregator()
        f1 = np.ones((10, 10, 3), dtype=np.uint8) * 1
        f2 = np.ones((10, 10, 3), dtype=np.uint8) * 2
        agg.put("cam1", f1, 1.0)
        agg.put("cam1", f2, 2.0)
        out, ts = agg.get("cam1")
        self.assertTrue(np.array_equal(out, f2))
        self.assertEqual(ts, 2.0)

    def test_put_none_frame_get_returns_none(self):
        agg = FrameAggregator()
        agg.put("cam1", None, 1.0)
        self.assertIsNone(agg.get("cam1"))

    def test_thread_safety(self):
        agg = FrameAggregator()
        results = []

        def writer(cam_id, value, ts):
            for _ in range(100):
                frame = np.full((20, 20, 3), value, dtype=np.uint8)
                agg.put(cam_id, frame, ts)

        def reader():
            for _ in range(200):
                for cid in ["a", "b"]:
                    r = agg.get(cid)
                    if r is not None:
                        results.append((cid, r[1]))

        t1 = threading.Thread(target=writer, args=("a", 1, 1.0))
        t2 = threading.Thread(target=writer, args=("b", 2, 2.0))
        t3 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t3.start()
        t1.join()
        t2.join()
        t3.join()
        self.assertEqual(len(agg.get_all_cam_ids()), 2)
        self.assertIn("a", agg.get_all_cam_ids())
        self.assertIn("b", agg.get_all_cam_ids())


if __name__ == "__main__":
    unittest.main()
