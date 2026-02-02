#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시간순 프레임 버퍼: 카메라별 deque, timestamp 기준 가장 오래된 프레임을 한 장씩 소비.
disappeared 오판 방지를 위한 프레임 처리 순서 보장.
프레임 수신 카운터(1초/250ms 구간)로 병목·짝 생성률 분석 지원.
"""
import threading
import time
from collections import deque
from typing import Optional, Tuple, List, Dict, Any

import numpy as np

# 500ms 세트: RPI 카메라 ID (대표 선택 시 중간 인덱스), USB_LOCAL (RPI 평균에 가장 가까운 것)
RPI_CAM_IDS = ("RPI_USB1", "RPI_USB2", "RPI_USB3")
USB_LOCAL_ID = "USB_LOCAL"


class TimeOrderedFrameBuffer:
    """카메라별 프레임 버퍼. get_oldest()로 timestamp가 가장 작은 (cam_id, frame, ts) 반환 및 소비."""

    def __init__(self, cam_ids: List[str], maxlen_per_cam: int = 60):
        self._lock = threading.Lock()
        self._buffers: Dict[str, deque] = {
            cid: deque(maxlen=maxlen_per_cam) for cid in cam_ids
        }
        self._cam_ids = list(cam_ids)
        # 1초 구간별 프레임 수신 카운트 (put 시마다 증가)
        self._frame_counts: Dict[str, int] = {cid: 0 for cid in cam_ids}
        # 250ms 구간별 카메라별 수신 수 (0~3)
        self._quarter_counts: List[Dict[str, int]] = [
            {cid: 0 for cid in cam_ids} for _ in range(4)
        ]
        self._window_start: float = time.time()

    def put(self, cam_id: str, frame: np.ndarray, timestamp: float) -> None:
        if cam_id not in self._buffers:
            return
        receive_ts = time.time()
        with self._lock:
            self._buffers[cam_id].append({
                "frame": frame.copy() if frame is not None else None,
                "timestamp": timestamp,
                "receive_ts": receive_ts,
            })
            self._frame_counts[cam_id] = self._frame_counts.get(cam_id, 0) + 1
            elapsed = time.time() - self._window_start
            quarter = int(elapsed / 0.25)
            if 0 <= quarter <= 3:
                self._quarter_counts[quarter][cam_id] = self._quarter_counts[quarter].get(cam_id, 0) + 1

    def peek_oldest_per_cam(self) -> List[Tuple[str, float]]:
        """
        카메라별 버퍼의 가장 오래된 프레임의 (cam_id, timestamp) 목록 반환. 제거하지 않음.
        짝 맞춤 모니터링용: 4개 카메라가 모두 있는지, timestamp 범위는 얼마인지 확인.
        """
        with self._lock:
            out = []
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                if not buf:
                    continue
                head = buf[0]
                if head["frame"] is None:
                    continue
                out.append((cid, head["timestamp"]))
            return out

    def get_oldest(self) -> Optional[Tuple[str, np.ndarray, float]]:
        """
        모든 버퍼 중 timestamp가 가장 작은 (cam_id, frame, ts) 반환. 해당 항목은 제거됨.
        None이면 처리할 프레임 없음.
        """
        with self._lock:
            candidates = []
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                if not buf:
                    continue
                head = buf[0]
                if head["frame"] is None:
                    buf.popleft()
                    continue
                candidates.append((head["timestamp"], cid, head))
            if not candidates:
                return None
            candidates.sort(key=lambda x: x[0])
            ts, cid, head = candidates[0]
            self._buffers[cid].popleft()
            return (cid, head["frame"], ts)

    def get_all_cam_ids(self) -> List[str]:
        return list(self._cam_ids)

    def buffer_lengths(self) -> Dict[str, int]:
        """디버그/모니터링: 카메라별 버퍼 길이."""
        with self._lock:
            return {cid: len(self._buffers[cid]) for cid in self._cam_ids}

    def get_stats_and_reset(self) -> Tuple[Dict[str, int], List[Dict[str, int]]]:
        """
        지난 1초 구간의 프레임 수신 수·250ms 구간별 수를 반환하고 카운터를 리셋.
        Returns: (frame_counts, quarter_counts)
          frame_counts: cam_id -> 수신 프레임 수
          quarter_counts: [q0, q1, q2, q3], 각 q는 cam_id -> 해당 250ms 구간 수신 수
        """
        with self._lock:
            frame_counts = dict(self._frame_counts)
            quarter_counts = [dict(q) for q in self._quarter_counts]
            self._frame_counts = {cid: 0 for cid in self._cam_ids}
            self._quarter_counts = [{cid: 0 for cid in self._cam_ids} for _ in range(4)]
            self._window_start = time.time()
        return frame_counts, quarter_counts

    def get_frames_in_interval(
        self, start_ts: float, end_ts: float
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        구간 [start_ts, end_ts) 내 프레임을 카메라별로 수집 (receive_ts 기준). 제거하지 않음.
        Returns: { cam_id: [ {"frame", "timestamp", "receive_ts"}, ... ], ... }
        """
        with self._lock:
            out: Dict[str, List[Dict[str, Any]]] = {}
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                items = [
                    {"frame": x["frame"], "timestamp": x["timestamp"], "receive_ts": x["receive_ts"]}
                    for x in buf
                    if x["frame"] is not None
                    and start_ts <= x["receive_ts"] < end_ts
                ]
                if items:
                    out[cid] = items
            return out

    def remove_frame(self, cam_id: str, receive_ts: float) -> bool:
        """
        해당 cam의 deque에서 receive_ts가 일치하는 첫 번째 항목 1개만 제거.
        Returns: True if removed, False if not found.
        """
        if cam_id not in self._buffers:
            return False
        with self._lock:
            buf = self._buffers[cam_id]
            kept = []
            removed = False
            for _ in range(len(buf)):
                x = buf.popleft()
                if not removed and x.get("receive_ts") == receive_ts:
                    removed = True
                    continue
                kept.append(x)
            for x in kept:
                buf.append(x)
            return removed

    def remove_frames_in_interval(self, start_ts: float, end_ts: float) -> None:
        """구간 [start_ts, end_ts) 내 모든 프레임을 receive_ts 기준으로 카메라별 deque에서 제거."""
        with self._lock:
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                kept = [
                    x
                    for x in buf
                    if not (start_ts <= x.get("receive_ts", x["timestamp"]) < end_ts)
                ]
                buf.clear()
                for x in kept:
                    buf.append(x)

    def get_min_timestamp(self) -> Optional[float]:
        """버퍼에 있는 카메라별 가장 오래된 프레임의 receive_ts 중 최소값. 한 cam이라도 비어 있으면 None."""
        with self._lock:
            ts_list = []
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                if not buf:
                    return None
                head = buf[0]
                if head["frame"] is None:
                    return None
                ts_list.append(head.get("receive_ts", head["timestamp"]))
            return min(ts_list) if ts_list else None

    def extract_set_for_interval(
        self, start_ts: float, end_ts: float
    ) -> Optional[Dict[str, Tuple[np.ndarray, float]]]:
        """
        구간 [start_ts, end_ts) (receive_ts 기준) 에서 4 cam 대표 1장씩 선택해 세트로 반환하고, 해당 4장을 버퍼에서 제거.
        RPI: 구간 내 중간 인덱스; USB_LOCAL: RPI 3대표 timestamp 평균에 가장 가까운 1장.
        Returns: { cam_id: (frame, timestamp), ... } — downstream은 원본 캡처 timestamp 사용.
        """
        with self._lock:
            # 수집: receive_ts 기준 (서버 수신 시각으로 윈도우 정렬 → 클럭 스큐 방지)
            by_cam: Dict[str, List[Dict[str, Any]]] = {}
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                items = [
                    {"frame": x["frame"], "timestamp": x["timestamp"], "receive_ts": x.get("receive_ts", x["timestamp"])}
                    for x in buf
                    if x["frame"] is not None
                    and start_ts <= x.get("receive_ts", x["timestamp"]) < end_ts
                ]
                if not items:
                    return None
                by_cam[cid] = items
            if len(by_cam) != len(self._cam_ids):
                return None

            # RPI 대표: 중간 인덱스
            rpi_timestamps = []
            selected: Dict[str, Tuple[np.ndarray, float]] = {}
            selected_receive_ts: Dict[str, float] = {}
            for cid in self._cam_ids:
                if cid in RPI_CAM_IDS:
                    lst = by_cam[cid]
                    idx = len(lst) // 2
                    item = lst[idx]
                    selected[cid] = (item["frame"], item["timestamp"])
                    selected_receive_ts[cid] = item["receive_ts"]
                    rpi_timestamps.append(item["timestamp"])
                # USB_LOCAL_ID는 아래에서 rpi_avg 구한 뒤 선택

            if not rpi_timestamps:
                return None
            rpi_avg = sum(rpi_timestamps) / len(rpi_timestamps)

            # USB_LOCAL: RPI 평균에 가장 가까운 1장
            if USB_LOCAL_ID in self._cam_ids:
                usb_list = by_cam[USB_LOCAL_ID]
                best = min(usb_list, key=lambda x: abs(x["timestamp"] - rpi_avg))
                selected[USB_LOCAL_ID] = (best["frame"], best["timestamp"])
                selected_receive_ts[USB_LOCAL_ID] = best["receive_ts"]

            if len(selected) != len(self._cam_ids):
                return None

            # 버퍼에서 선택된 4장만 제거 (receive_ts로 일치)
            for cid in self._cam_ids:
                rt = selected_receive_ts[cid]
                buf = self._buffers[cid]
                kept = []
                removed = False
                for _ in range(len(buf)):
                    x = buf.popleft()
                    if not removed and x.get("receive_ts") == rt:
                        removed = True
                        continue
                    kept.append(x)
                for x in kept:
                    buf.append(x)

            return selected
