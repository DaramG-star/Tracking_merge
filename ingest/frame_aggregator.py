#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카메라 ID별 최신 (frame, timestamp) 버퍼. 스레드 안전.
"""
import threading
from typing import Optional, Tuple, List

import numpy as np


class FrameAggregator:
    """put(cam_id, frame, ts), get(cam_id) -> (frame, ts) 복사본, get_all_cam_ids()."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buffers: dict = {}  # cam_id -> {"frame": ndarray, "timestamp": float}

    def put(self, cam_id: str, frame: np.ndarray, timestamp: float) -> None:
        with self._lock:
            self._buffers[cam_id] = {
                "frame": frame.copy() if frame is not None else None,
                "timestamp": timestamp,
            }

    def get(self, cam_id: str) -> Optional[Tuple[np.ndarray, float]]:
        with self._lock:
            if cam_id not in self._buffers:
                return None
            buf = self._buffers[cam_id]
            f, ts = buf["frame"], buf["timestamp"]
            if f is None:
                return None
            return (f.copy(), ts)

    def get_all_cam_ids(self) -> List[str]:
        with self._lock:
            return list(self._buffers.keys())
