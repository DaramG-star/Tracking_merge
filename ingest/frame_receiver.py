#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZMQ SUB 수신, LZ4/JSON/Base64 디코딩. output_bgr=True 시 BGR 반환.
"""
import time
import logging
import threading
import json
import base64
from typing import Optional, Dict, Any, Callable

import cv2
import numpy as np
import zmq
import lz4.frame

logger = logging.getLogger(__name__)


class FrameReceiver:
    def __init__(self, zmq_socket: zmq.Socket, use_lz4: bool = True, output_bgr: bool = True):
        self.zmq_socket = zmq_socket
        self.use_lz4 = use_lz4
        self.output_bgr = output_bgr
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_buffers: Dict[str, Dict[str, Any]] = {}
        self.frame_counts: Dict[str, int] = {}
        self.error_counts: Dict[str, int] = {}
        self.frame_callback: Optional[Callable[[str, np.ndarray, float], None]] = None

    def set_frame_callback(self, callback: Callable[[str, np.ndarray, float], None]):
        self.frame_callback = callback

    def _decode_frame(self, message_data: bytes) -> Optional[Dict[str, Any]]:
        try:
            if self.use_lz4:
                json_bytes = lz4.frame.decompress(message_data)
            else:
                json_bytes = message_data
            return json.loads(json_bytes.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to decode message: %s", e)
            return None

    def _process_frame(self, camera_name: str, message: Dict[str, Any]):
        try:
            frame_b64 = message.get("frame", "")
            img_bytes = base64.b64decode(frame_b64)
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR if self.output_bgr else cv2.IMREAD_GRAYSCALE)
            if frame is None:
                logger.warning("%s: Failed to decode image", camera_name)
                return
            if not self.output_bgr and len(frame.shape) == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            timestamp = message.get("timestamp", time.time())
            self.frame_buffers[camera_name] = {
                "frame": frame,
                "timestamp": timestamp,
                "width": message.get("width", frame.shape[1]),
                "height": message.get("height", frame.shape[0]),
            }
            self.frame_counts[camera_name] = self.frame_counts.get(camera_name, 0) + 1
            if self.frame_callback:
                try:
                    self.frame_callback(camera_name, frame, timestamp)
                except Exception as e:
                    logger.error("Frame callback error: %s", e)
        except Exception as e:
            logger.error("%s frame processing error: %s", camera_name, e)
            self.error_counts[camera_name] = self.error_counts.get(camera_name, 0) + 1

    def _receive_loop(self):
        logger.info("Frame receiver started")
        while self.running:
            try:
                parts = self.zmq_socket.recv_multipart(zmq.NOBLOCK)
                if len(parts) >= 2:
                    topic = parts[0].decode("utf-8")
                    message = self._decode_frame(parts[1])
                    if message is not None:
                        camera_name = message.get("camera", topic)
                        self._process_frame(camera_name, message)
            except zmq.Again:
                time.sleep(0.01)
            except Exception as e:
                logger.error("Receive loop error: %s", e)
                time.sleep(0.1)
        logger.info("Frame receiver stopped")

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def get_frame(self, camera_name: str) -> Optional[np.ndarray]:
        if camera_name in self.frame_buffers:
            return self.frame_buffers[camera_name]["frame"].copy()
        return None
