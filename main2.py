#!/usr/bin/env python3
"""
Multi-camera tracking (Async Mode): Process frames immediately as they arrive.
Run from track/ directory: python main2.py [--csv] [--video] [--display]
"""
import argparse
import csv
import json
import os
import signal
import sys
import time
import cv2
import threading
from pathlib import Path

# Ensure track root is on path
TRACK_ROOT = Path(__file__).resolve().parent
if str(TRACK_ROOT) not in sys.path:
    sys.path.insert(0, str(TRACK_ROOT))

import config
from ingest.config_loader import ConfigLoader
from ingest.frame_aggregator import FrameAggregator
from ingest.frame_receiver import FrameReceiver
from ingest.usb_camera_worker import USBCameraWorker
from logic.detector import YOLODetector
from logic.matcher import FIFOGlobalMatcher
from logic.visualizer import TrackingVisualizer
from logic import api_helper
from logic.scanner_listener import ScannerListener
from logic.utils import save_thumbnail_to_nfs

def parse_args():
    p = argparse.ArgumentParser(description="Multi-camera tracking (Async Mode)")
    p.add_argument("--csv", action="store_true", help="Enable CSV logging")
    p.add_argument("--video", action="store_true", help="Enable video saving")
    p.add_argument("--display", action="store_true", help="Enable real-time display")
    return p.parse_args()

def main():
    args = parse_args()

    # 폴더 생성
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CROP_DIR.mkdir(parents=True, exist_ok=True)
    if args.video:
        config.VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    loader = ConfigLoader()
    loader.load()
    
    # 동기화 버퍼 대신 즉시 접근 가능한 Aggregator 사용
    frame_sink = FrameAggregator()
    
    # 전역 상태 변수
    global _running
    _running = True
    
    active_tracks = {cam: {} for cam in config.TRACKING_CAMS}
    local_uid_counter = {cam: 0 for cam in config.TRACKING_CAMS}
    last_processed_ts = {cam: 0 for cam in config.TRACKING_CAMS}
    target_interval = 0.25  # 250ms 간격 처리 (초과 프레임 스킵용)

    # 로직 객체 초기화
    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer(enabled=args.video)

    # ZMQ 수신 설정
    import zmq
    ctx = zmq.Context()
    receivers = []
    stream_cfg = loader.get_stream_config()
    use_lz4 = stream_cfg.get("use_lz4", True)

    def make_zmq_callback(rpi_id):
        def cb(camera_name, frame, ts):
            key = f"{rpi_id}:{camera_name}"
            cam_id = config.ZMQ_CAM_MAPPING.get(key)
            if cam_id:
                frame_sink.put(cam_id, frame, ts)
        return cb

    for client in loader.get_rbp_clients():
        rpi_id = client.get("id", "rpi1")
        ip = client.get("ip", "127.0.0.1")
        port = client.get("port", 5555)
        addr = f"tcp://{ip}:{port}"
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.connect(addr)
        recv = FrameReceiver(sock, use_lz4=use_lz4, output_bgr=True)
        recv.set_frame_callback(make_zmq_callback(rpi_id))
        recv.start()
        receivers.append(recv)

    # USB 카메라 설정
    usb_workers = []
    for cam_name, cam_cfg in loader.get_local_usb_cameras().items():
        if not cam_cfg.get("enabled", True): continue
        key = f"local:{cam_name}"
        if key not in config.ZMQ_CAM_MAPPING: continue
        cam_id = config.ZMQ_CAM_MAPPING[key]
        worker = USBCameraWorker(cam_name, cam_cfg)
        if worker.start():
            usb_workers.append((worker, cam_id))

    def usb_feeder_loop():
        while _running:
            for worker, cam_id in usb_workers:
                frame = worker.get_latest_frame()
                if frame is not None:
                    frame_sink.put(cam_id, frame, worker.latest_timestamp)
            time.sleep(0.02)

    usb_feeder = threading.Thread(target=usb_feeder_loop, daemon=True)
    usb_feeder.start()

    # 스캐너 리스너
    scanner_listener = ScannerListener(matcher, host=config.SCANNER_HOST, port=config.SCANNER_PORT)
    scanner_listener.start()

    # CSV 설정
    csv_file = None
    csv_writer = None
    if args.csv:
        csv_path = config.OUT_DIR / "tracking_logs_async.csv"
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.DictWriter(csv_file, fieldnames=["timestamp", "cam", "local_uid", "master_id", "event"])
        csv_writer.writeheader()

    def shutdown(*_):
        global _running
        _running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[main2] Async Mode Started. Processing frames as they arrive.")

    try:
        while _running:
            # 4개 카메라를 순회하며 개별적으로 프레임이 있는지 확인
            for cam in config.TRACKING_CAMS:
                pair = frame_sink.get(cam)
                if pair is None: continue
                
                img, ts = pair
                # 이미 처리한 타임스탬프거나 너무 짧은 간격이면 스킵 (중복 처리 방지)
                print(f"[{cam}] Resolution: {img.shape[1]}x{img.shape[0]}")
                if ts <= last_processed_ts[cam] or (ts - last_processed_ts[cam]) < target_interval * 0.8:
                    continue
                
                last_processed_ts[cam] = ts
                
                # 1. Detection
                cfg = config.CAM_SETTINGS.get(cam)
                if not cfg: continue
                detections = detector.get_detections(img, cfg, cam)
                
                # 2. Tracking & Matching (즉시 실행)
                new_active = {}
                for det in detections:
                    cx, cy = det["center"]
                    x1, y1, x2, y2 = det["box"]
                    
                    # 기존 Local Track 찾기
                    best_uid, best_score = None, 1e9
                    for uid, info in active_tracks[cam].items():
                        dx = abs(cx - info["last_pos"][0])
                        dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                        if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                        score = dx + dy * 0.3
                        if score < best_score:
                            best_uid, best_score = uid, score

                    mid = None
                    event_type = "TRACKING"
                    
                    if best_uid:
                        mid = active_tracks[cam][best_uid]["master_id"]
                    else:
                        # 신규 진입 물체 매칭 시도
                        local_uid_counter[cam] += 1
                        best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                        match_cam = "RPI_USB3_EOL" if det.get("in_eol") else cam
                        mid = matcher.try_match(match_cam, ts, det["width"], best_uid)
                        event_type = "MATCHED" if mid else "UNMATCHED"

                    if mid:
                        new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
                        
                        # Position API 계산
                        total_dist = config.ROUTE_TOTAL_DIST.get(matcher.masters[mid]["route_code"], 12.8)
                        elapsed = ts - matcher.masters[mid]["start_time"]
                        rem_dist = max(0.0, total_dist - (elapsed * config.BELT_SPEED))
                        step_dist = round(rem_dist / 0.5) * 0.5
                        
                        if cam == "USB_LOCAL":
                            h, w = img.shape[:2]
                            crop = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                            if crop.size > 0:
                                save_thumbnail_to_nfs(mid, crop)
                        
                        # 위치 정보 전송 (모든 카메라 공통)
                        api_helper.api_update_position(mid, step_dist)

                # 3. Pending & Resolve (사라진 물체 처리)
                for old_uid, old_info in active_tracks[cam].items():
                    if old_uid not in new_active:
                        mid = old_info["master_id"]
                        if mid and mid in matcher.masters:
                            matcher.masters[mid]["status"] = "PENDING"
                            matcher.masters[mid]["pending_from_cam"] = cam

                # Pending 해소 시도
                for mid in list(matcher.masters.keys()):
                    result = matcher.resolve_pending(mid, ts)
                    if result:
                        if result["decision"] == "PICKUP": api_helper.api_pickup(mid)
                        elif result["decision"] == "DISAPPEAR": api_helper.api_disappear(mid)

                active_tracks[cam] = new_active

                # 시각화 (옵션)
                if args.display:
                    cv2.imshow(f"track_{cam}", img)
                    if cv2.waitKey(1) & 0xFF == ord('q'): _running = False

            time.sleep(0.01)

    finally:
        _running = False
        for recv in receivers: recv.stop()
        for worker, _ in usb_workers: worker.stop()
        scanner_listener.stop()
        if csv_file: csv_file.close()
        cv2.destroyAllWindows()
        ctx.term()

if __name__ == "__main__":
    main()