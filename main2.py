#!/usr/bin/env python3
"""
Multi-camera tracking (Async Mode): Process frames immediately as they arrive.
Full Detection Visualization (Gray: Detected, Green: Matched, Red: Unmatched)
"""
import argparse
import csv
import os
import signal
import sys
import time
import cv2
import threading
import numpy as np
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

    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CROP_DIR.mkdir(parents=True, exist_ok=True)
    if args.video:
        config.VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    loader = ConfigLoader()
    loader.load()
    
    frame_sink = FrameAggregator()
    
    global _running
    _running = True
    
    active_tracks = {cam: {} for cam in config.TRACKING_CAMS}
    local_uid_counter = {cam: 0 for cam in config.TRACKING_CAMS}
    last_processed_ts = {cam: 0 for cam in config.TRACKING_CAMS}
    target_interval = 0.25

    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer(enabled=args.video)

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

    scanner_listener = ScannerListener(matcher, host=config.SCANNER_HOST, port=config.SCANNER_PORT)
    scanner_listener.start()

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

    print("[main2] Async Mode Started (Total Detection Visualization)")

    try:
        while _running:
            for cam in config.TRACKING_CAMS:
                pair = frame_sink.get(cam)
                if pair is None: continue
                
                img, ts = pair
                if ts <= last_processed_ts[cam] or (ts - last_processed_ts[cam]) < target_interval * 0.8:
                    continue
                last_processed_ts[cam] = ts
                
                cfg = config.CAM_SETTINGS.get(cam)
                if not cfg: continue

                # 1. 이미지 회전
                rotate_val = cfg.get("rotate", 0)
                if rotate_val == 90:
                    img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
                elif rotate_val == 180:
                    img = cv2.rotate(img, cv2.ROTATE_180)
                elif rotate_val == 270:
                    img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

                # 2. 640 리사이징
                target_w = 640
                h_orig, w_orig = img.shape[:2]
                scale = target_w / w_orig
                target_h = int(h_orig * scale)
                img = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_AREA)

                # 3. 해상도 및 비율 계산
                H, W = img.shape[:2]
                cfg['roi_y'] = int(H * cfg.get('roi_y_rate', 0))
                cfg['roi_margin'] = int(H * cfg.get('roi_margin_rate', 0))
                cfg['dist_eps'] = int(H * cfg.get('dist_eps_rate', 0))
                cfg['max_dy'] = int(H * cfg.get('max_dy_rate', 0))
                if 'eol_y_rate' in cfg:
                    cfg['eol_y'] = int(H * cfg['eol_y_rate'])
                    cfg['eol_margin'] = int(H * cfg['eol_margin_rate'])

                # 4. ROI 가이드라인 시각화
                if args.display:
                    cv2.line(img, (0, cfg['roi_y']), (W, cfg['roi_y']), (0, 255, 255), 2)
                    overlay = img.copy()
                    y_min, y_max = max(0, cfg['roi_y'] - cfg['roi_margin']), min(H, cfg['roi_y'] + cfg['roi_margin'])
                    cv2.rectangle(overlay, (0, y_min), (W, y_max), (0, 0, 255), -1)
                    cv2.addWeighted(overlay, 0.2, img, 0.8, 0, img)
                    if 'eol_y' in cfg:
                        cv2.line(img, (0, cfg['eol_y']), (W, cfg['eol_y']), (255, 0, 255), 2)

                # 5. Detection
                inf_t0 = time.perf_counter()
                detections = detector.get_detections(img, cfg, cam)
                detections = detector.get_detections(img, cfg, cam)
                inf_dt = time.perf_counter() - inf_t0 # 소요 시간 계산
                
                # [작동 확인용 로그] 실시간으로 추론 시간이 찍히면 모델이 돌고 있는 것입니다.
                print(f"[{cam}] Inference active: {len(detections)} objects found | Time: {inf_dt:.4f}s")
                
                new_active = {}
                for det in detections:
                    cx, cy = det["center"]
                    x1, y1, x2, y2 = det["box"]

                    # 1) 기본 시각화 (YOLO가 찾은 모든 물체 - 회색)
                    # ROI 통과 전에도 "찾았음"을 알 수 있게 함
                    if args.display:
                        cv2.rectangle(img, (x1, y1), (x2, y2), (180, 180, 180), 1)

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
                        local_uid_counter[cam] += 1
                        best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                        match_cam = "RPI_USB3_EOL" if det.get("in_eol") else cam
                        mid = matcher.try_match(match_cam, ts, det["width"], best_uid)
                        event_type = "MATCHED" if mid else "UNMATCHED"

                    # 2) 상태별 강조 시각화 (매칭 여부에 따라 덮어쓰기)
                    if args.display:
                        # 매칭 성공(초록), 매칭 시도 구역인데 실패(빨강)
                        if mid:
                            color, thickness = (0, 255, 0), 2
                            label = f"ID: {mid}"
                        elif det.get("in_roi", False): # ROI 안에는 있는데 매칭 안 된 경우
                            color, thickness = (0, 0, 255), 2
                            label = "UNMATCHED"
                        else:
                            color, thickness = (180, 180, 180), 1 # ROI 밖: 기본 회색 유지
                            label = "DETECTED"

                        cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)
                        cv2.putText(img, label, (x1, y1 - 10), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

                    if mid:
                        new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
                        total_dist = config.ROUTE_TOTAL_DIST.get(matcher.masters[mid]["route_code"], 12.8)
                        elapsed = ts - matcher.masters[mid]["start_time"]
                        rem_dist = max(0.0, total_dist - (elapsed * config.BELT_SPEED))
                        step_dist = round(rem_dist / 0.5) * 0.5
                        
                        if cam == "USB_LOCAL":
                            h_in, w_in = img.shape[:2]
                            crop = img[max(0, y1):min(h_in, y2), max(0, x1):min(w_in, x2)]
                            if crop.size > 0:
                                save_thumbnail_to_nfs(mid, crop)
                        
                        api_helper.api_update_position(mid, step_dist, thumbnail_image=None)

                    if args.csv and csv_writer:
                        csv_writer.writerow({
                            "timestamp": ts, "cam": cam, "local_uid": best_uid,
                            "master_id": mid if mid else "", "event": event_type
                        })

                # PENDING 및 Resolve 로직 (생략 없음)
                for old_uid, old_info in active_tracks[cam].items():
                    if old_uid not in new_active:
                        m_id = old_info["master_id"]
                        if m_id and m_id in matcher.masters:
                            matcher.masters[m_id]["status"] = "PENDING"
                            matcher.masters[m_id]["pending_from_cam"] = cam

                for mid_res in list(matcher.masters.keys()):
                    result = matcher.resolve_pending(mid_res, ts)
                    if result:
                        if result["decision"] == "PICKUP": api_helper.api_pickup(mid_res)
                        elif result["decision"] == "DISAPPEAR": api_helper.api_disappear(mid_res)

                active_tracks[cam] = new_active

                if args.display:
                    cv2.putText(img, f"CAM: {cam} | {W}x{H} | TS: {ts:.2f}", (10, 30), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
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