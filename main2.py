#!/usr/bin/env python3
"""
Multi-camera tracking (Async Mode): Process frames immediately as they arrive.
Horizontal ROI Filtering + Real-time Status Visualization
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
        csv_path = config.OUT_DIR / "tracking_logs_detailed.csv"
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        fieldnames = [
            "timestamp", "cam", "local_uid", "master_id", "event", 
            "reason", "prev_cam", "prev_time", "expected_time", "diff", "margin"
        ]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()

    def shutdown(*_):
        global _running
        _running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[main2] Async Mode Started (Horizontal ROI Filter + Visualization)")

    try:
        while _running:
            for cam in config.TRACKING_CAMS:
                pair = frame_sink.get(cam)
                if pair is None: continue
                
                img, ts = pair
                if ts <= last_processed_ts[cam] or (ts - last_processed_ts[cam]) < target_interval * 0.8:
                    continue
                last_processed_ts[cam] = ts

                import datetime
                dt = datetime.datetime.fromtimestamp(ts)
                ts_today = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1000000.0
            
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

                # 3. 해상도 기반 비율 픽셀 계산
                H, W = img.shape[:2]
                cfg['roi_y'] = int(H * cfg.get('roi_y_rate', 0))
                cfg['roi_margin'] = int(H * cfg.get('roi_margin_rate', 0))
                
                x_center = int(W * cfg.get('roi_x_center_rate', 0.5))
                x_half_range = int(W * cfg.get('roi_x_range_rate', 1.0) / 2)
                cfg['roi_x_min'] = x_center - x_half_range
                cfg['roi_x_max'] = x_center + x_half_range

                cfg['dist_eps'] = int(H * cfg.get('dist_eps_rate', 0))
                cfg['max_dy'] = int(H * cfg.get('max_dy_rate', 0))
                if 'eol_y_rate' in cfg:
                    cfg['eol_y'] = int(H * cfg['eol_y_rate'])
                    cfg['eol_margin'] = int(H * cfg['eol_margin_rate'])

                # 4. ROI 가이드라인 시각화
                if args.display:
                    overlay = img.copy()
                    cv2.rectangle(overlay, (0, 0), (cfg['roi_x_min'], H), (0, 0, 0), -1)
                    cv2.rectangle(overlay, (cfg['roi_x_max'], 0), (W, H), (0, 0, 0), -1)
                    y_min = max(0, cfg['roi_y'] - cfg['roi_margin'])
                    y_max = min(H, cfg['roi_y'] + cfg['roi_margin'])
                    cv2.rectangle(overlay, (cfg['roi_x_min'], y_min), (cfg['roi_x_max'], y_max), (0, 0, 255), -1)
                    cv2.addWeighted(overlay, 0.3, img, 0.7, 0, img)
                    cv2.line(img, (cfg['roi_x_min'], cfg['roi_y']), (cfg['roi_x_max'], cfg['roi_y']), (0, 255, 255), 2)
                    if 'eol_y' in cfg:
                        cv2.line(img, (cfg['roi_x_min'], cfg['eol_y']), (cfg['roi_x_max'], cfg['eol_y']), (255, 0, 255), 2)

                # 5. Detection (YOLO 추론)
                inf_t0 = time.perf_counter()
                detections = detector.get_detections(img, cfg, cam)
                inf_dt = time.perf_counter() - inf_t0
                
                new_active = {}
                for det in detections:
                    cx, cy = det["center"]
                    x1, y1, x2, y2 = det["box"]

                    # NameError 방지를 위한 초기화
                    mid = None
                    best_uid = None
                    event_type = "TRACKING"

                    # 1. 가로(X) 범위 체크
                    in_x_range = (cfg['roi_x_min'] <= cx <= cfg['roi_x_max'])
                    if not in_x_range:
                        if args.display:
                            cv2.rectangle(img, (x1, y1), (x2, y2), (150, 150, 150), 1)
                        continue

                    # 2. 세로(Y) ROI 통과 여부 체크
                    in_y_roi = abs(cy - cfg['roi_y']) <= cfg['roi_margin']

                    # 기존 추적 확인
                    current_best_uid, best_score = None, 1e9
                    for uid, info in active_tracks[cam].items():
                        dx = abs(cx - info["last_pos"][0])
                        dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                        if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                        score = dx + dy * 0.3
                        if score < best_score:
                            current_best_uid, best_score = uid, score

                    if current_best_uid:
                        best_uid = current_best_uid
                        mid = active_tracks[cam][best_uid]["master_id"]
                    elif in_y_roi:
                        local_uid_counter[cam] += 1
                        best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                        match_cam = "RPI_USB3_EOL" if det.get("in_eol") else cam
                        
                        match_result = matcher.try_match(match_cam, ts_today, det["width"], best_uid)
                        mid = match_result.get("mid")
                        reason = match_result.get("reason") # 리즌 값 가져오기

                        if not mid:
                            print(f"❌ 매칭 실패: UID={best_uid}, 사유={reason}") # 터미널에 실패 사유 출력
                        event_type = "MATCHED" if mid else "UNMATCHED"
                        
                        if args.csv and csv_writer:
                            log_row = {
                                "timestamp": ts_today,
                                "cam": cam,
                                "local_uid": best_uid,
                                "master_id": mid if mid else "",
                                "event": event_type,
                                "reason": match_result.get("reason", ""),
                                "prev_cam": match_result.get("prev_cam", ""),
                                "prev_time": match_result.get("prev_time", ""),
                                "expected_time": match_result.get("expected_time", ""),
                                "diff": match_result.get("diff", ""),
                                "margin": match_result.get("margin", "")
                            }
                            csv_writer.writerow(log_row)
                    else:
                        continue

                    # 상태별 시각화
                    if args.display and best_uid:
                        color = (0, 255, 0) if mid else (0, 0, 255)
                        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                        label = f"ID: {mid}" if mid else "UNMATCHED"
                        cv2.putText(img, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

                    # 매칭된 경우 후속 작업 (거리 보고 등)
                    if mid is not None:
                        new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
                        
                        # # 거리 수정 로직 적용
                        # cam_pos = config.CAM_SETTINGS[cam].get("dist", 0.0) 
                        # total_route_dist = config.ROUTE_TOTAL_DIST.get(matcher.masters[mid]["route_code"], 12.8)
                        # rem_dist = max(0.0, total_route_dist - cam_pos)
                        # step_dist = round(rem_dist / 0.5) * 0.5
                        
                        m_info = matcher.masters[mid]
                        if m_info.get("start_time") is not None:
                            # 1. 해당 경로의 전체 거리 가져오기 (기본값 12.8m)
                            total_dist = config.ROUTE_TOTAL_DIST.get(m_info["route_code"], 12.8)
                            
                            # 2. 현재 시간(ts_today)과 시작 시간 차이 계산
                            elapsed_time = ts_today - m_info["start_time"]
                            
                            # 3. 잔여 거리 계산: 전체 거리 - (경과 시간 * 벨트 속도)
                            rem_dist = max(0.0, total_dist - (elapsed_time * config.BELT_SPEED))
                            
                            # 4. 0.5m 단위로 절삭(Rounding)
                            step_dist = round(rem_dist / 0.5) * 0.5
                            
                            # 5. 값이 변했을 때만 API 호출 (중복 호출 방지)
                            if m_info.get("last_sent_dist") != step_dist:
                                api_helper.api_update_position(mid, step_dist, thumbnail_image=None)
                                m_info["last_sent_dist"] = step_dist

                        if cam == "USB_LOCAL":
                            crop = img[max(0, y1):min(H, y2), max(0, x1):min(W, x2)]
                            if crop.size > 0:
                                save_thumbnail_to_nfs(mid, crop)
                        
                        # api_helper.api_update_position(mid, step_dist, thumbnail_image=None)

                    # CSV 로그 (모든 감지 건에 대해 기록)
                    if args.csv and csv_writer and best_uid:
                        csv_writer.writerow({
                            "timestamp": ts_today,
                            "cam": cam, 
                            "local_uid": best_uid,
                            "master_id": mid if mid else "", 
                            "event": event_type
                        })

                # PENDING 처리
                for old_uid, old_info in active_tracks[cam].items():
                    if old_uid not in new_active:
                        m_id = old_info["master_id"]
                        if m_id and m_id in matcher.masters:
                            matcher.masters[m_id]["status"] = "PENDING"
                            matcher.masters[m_id]["pending_from_cam"] = cam

                for mid_res in list(matcher.masters.keys()):
                    result = matcher.resolve_pending(mid_res, ts_today)
                    if result:
                        if result["decision"] == "PICKUP": api_helper.api_pickup(mid_res)
                        elif result["decision"] == "DISAPPEAR": api_helper.api_disappear(mid_res)

                active_tracks[cam] = new_active

                if args.display:
                    cv2.putText(img, f"CAM: {cam} | Resized: {W}x{H}", (10, 30), 
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