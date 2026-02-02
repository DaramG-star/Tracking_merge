#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로컬 USB 카메라 OpenCV 캡처. get_latest_frame(), latest_timestamp, start(), stop().
"""
import time
import logging
import threading
from typing import Optional, Dict, Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class USBCameraWorker:
    def __init__(self, camera_name: str, camera_config: Dict[str, Any]):
        self.camera_name = camera_name
        self.camera_config = camera_config
        self.cap = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_count = 0
        self.error_count = 0
        self.last_capture_time = None
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_timestamp: float = 0.0

    def initialize(self) -> bool:
        try:
            device = self.camera_config.get("device", "/dev/video0")
            if isinstance(device, str) and device.startswith("/dev/video"):
                try:
                    device_num = int(device.replace("/dev/video", ""))
                except ValueError:
                    device_num = device
            else:
                device_num = device
            self.cap = cv2.VideoCapture(device_num, cv2.CAP_V4L2)
            if not self.cap.isOpened():
                logger.error("Failed to open USB camera: %s", device)
                return False
            ret, _ = self.cap.read()
            if ret:
                pass
            w = self.camera_config.get("width", 1280)
            h = self.camera_config.get("height", 720)
            fps = self.camera_config.get("fps", 20)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            self.cap.set(cv2.CAP_PROP_FPS, fps)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info("USB camera initialized: %s (%s)", self.camera_name, device)
            return True
        except Exception as e:
            logger.error("Failed to initialize USB camera %s: %s", self.camera_name, e)
            return False

    def _capture_usb_frame(self) -> Optional[np.ndarray]:
        if self.cap is None or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        return frame if ret and frame is not None else None

    def _worker_loop(self):
        while self.running:
            frame = self._capture_usb_frame()
            if frame is not None:
                self.latest_frame = frame.copy()
                self.latest_timestamp = time.time()
                self.frame_count += 1
                self.last_capture_time = time.time()
            else:
                self.error_count += 1
            time.sleep(0.01)
        logger.info("USB camera worker stopped: %s", self.camera_name)

    def start(self) -> bool:
        if self.running:
            return True
        if not self.initialize():
            return False
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def get_latest_frame(self) -> Optional[np.ndarray]:
        if self.latest_frame is not None:
            return self.latest_frame.copy()
        return None
