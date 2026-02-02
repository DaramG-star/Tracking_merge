#!/usr/bin/env python3
"""Unit tests for ingest.config_loader (config.py 단일 소스)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.config_loader import ConfigLoader, get_stream_config, get_rbp_clients, get_local_usb_cameras
import config


class TestConfigLoader(unittest.TestCase):
    """ConfigLoader는 config.py에서 읽음. config.json 미사용."""

    def test_load(self):
        loader = ConfigLoader()
        cfg = loader.load()
        self.assertIsNotNone(cfg)

    def test_get_stream_config(self):
        loader = ConfigLoader()
        stream = loader.get_stream_config()
        self.assertIn("use_lz4", stream)
        self.assertIs(stream["use_lz4"], config.STREAM_USE_LZ4)

    def test_get_rbp_clients(self):
        loader = ConfigLoader()
        clients = loader.get_rbp_clients()
        self.assertEqual(clients, config.RBP_CLIENTS)
        self.assertGreaterEqual(len(clients), 1)
        self.assertIn("id", clients[0])
        self.assertIn("ip", clients[0])
        self.assertIn("port", clients[0])
        self.assertIn("cameras", clients[0])

    def test_get_local_usb_cameras(self):
        loader = ConfigLoader()
        cams = loader.get_local_usb_cameras()
        self.assertEqual(cams, config.LOCAL_USB_CAMERAS)
        if cams:
            name = next(iter(cams))
            self.assertIn("device", cams[name])
            self.assertIn("enabled", cams[name])

    def test_get_stream_config_without_load(self):
        loader = ConfigLoader()
        stream = loader.get_stream_config()
        self.assertTrue(stream.get("use_lz4") is True or stream.get("use_lz4") is False)

    def test_module_functions(self):
        self.assertEqual(get_rbp_clients(), config.RBP_CLIENTS)
        self.assertEqual(get_local_usb_cameras(), config.LOCAL_USB_CAMERAS)
        self.assertEqual(get_stream_config()["use_lz4"], config.STREAM_USE_LZ4)


if __name__ == "__main__":
    unittest.main()
