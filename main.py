#!/usr/bin/env python3
"""
Multi-camera tracking: ZMQ + USB ingest, YOLO detection, FIFO matcher, API/Scanner.
Run from track/ directory: python main.py [--csv] [--video] [--display]
"""
import argparse
import csv
import json
import os
import signal
import sys
import time
import cv2
import numpy as np
from pathlib import Path

# 4개 카메라 짝 맞춤 로그: timestamp 범위 이하면 COMPLETE, 초과면 SPREAD (초 단위)
FRAME_SET_SPREAD_THRESHOLD_SEC = 0.5

# Silence "QFontDatabase: Cannot find font directory .../cv2/qt/fonts" when using --display (OpenCV Qt backend)
# 1) Create cv2/qt/fonts as a symlink to system fonts so Qt finds them
_sys_fonts = "/usr/share/fonts/truetype/dejavu"
for _p in sys.path:
    if not _p or not os.path.isdir(_p):
        continue
    _cv2_qt = os.path.join(_p, "cv2", "qt")
    if os.path.isdir(_cv2_qt):
        _qt_fonts = os.path.join(_cv2_qt, "fonts")
        if not os.path.exists(_qt_fonts) and os.path.isdir(_sys_fonts):
            try:
                os.symlink(_sys_fonts, _qt_fonts)
            except OSError:
                pass
        break
# 2) Suppress Qt font warnings if symlink failed or path differs
if "QT_LOGGING_RULES" not in os.environ:
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false"

# Ensure track root is on path
TRACK_ROOT = Path(__file__).resolve().parent
if str(TRACK_ROOT) not in sys.path:
    sys.path.insert(0, str(TRACK_ROOT))

import config
from ingest.config_loader import ConfigLoader
from ingest.frame_aggregator import FrameAggregator
from ingest.frame_receiver import FrameReceiver
from ingest.time_ordered_buffer import TimeOrderedFrameBuffer
from ingest.usb_camera_worker import USBCameraWorker
from logic.detector import YOLODetector
from logic.matcher import FIFOGlobalMatcher
from logic.visualizer import TrackingVisualizer
from logic import api_helper
from logic.scanner_listener import ScannerListener
from logic.utils import save_thumbnail_to_nfs


def parse_args():
    p = argparse.ArgumentParser(description="Multi-camera tracking (ZMQ + USB)")
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

    # Ingest: config loader and frame sink (time-ordered buffer or legacy aggregator)
    loader = ConfigLoader()
    loader.load()
    use_time_ordered = getattr(config, "USE_TIME_ORDERED_BUFFER", False)
    if use_time_ordered:
        maxlen = getattr(config, "TIME_ORDERED_BUFFER_MAXLEN", 60)
        frame_sink = TimeOrderedFrameBuffer(config.TRACKING_CAMS, maxlen_per_cam=maxlen)
    else:
        frame_sink = FrameAggregator()

    # ZMQ: one SUB socket per rbp_client, FrameReceiver with callback
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

    # USB: local cameras -> aggregator with USB_LOCAL
    usb_workers = []
    for cam_name, cam_cfg in loader.get_local_usb_cameras().items():
        if not cam_cfg.get("enabled", True):
            continue
        key = f"local:{cam_name}"
        if key not in config.ZMQ_CAM_MAPPING:
            continue
        cam_id = config.ZMQ_CAM_MAPPING[key]
        worker = USBCameraWorker(cam_name, cam_cfg)
        if worker.start():
            usb_workers.append((worker, cam_id))

    # USB feeder thread: periodically put latest frame into sink
    def usb_feeder_loop():
        while _running:
            for worker, cam_id in usb_workers:
                frame = worker.get_latest_frame()
                if frame is not None:
                    frame_sink.put(cam_id, frame, worker.latest_timestamp)
            time.sleep(0.02)
    import threading
    _running = True

    def shutdown(*_):
        nonlocal _running
        _running = False

    usb_feeder = threading.Thread(target=usb_feeder_loop, daemon=True)
    usb_feeder.start()

    # Logic: detector, matcher, visualizer (optional)
    detector = YOLODetector(config.MODEL_PATH)
    # 워밍업: YOLO11+ThreadPool 시 setup_model/fuse를 메인 스레드에서 먼저 실행해 fuse() Conv.bn 오류 방지
    if use_time_ordered:
        import numpy as np
        _dummy = np.zeros((720, 1280, 3), dtype=np.uint8)
        _cfg = config.CAM_SETTINGS.get("USB_LOCAL", {})
        if _cfg:
            detector.get_detections(_dummy, _cfg, "USB_LOCAL")
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer(enabled=args.video)

    # Scanner listener (required)
    scanner_listener = ScannerListener(matcher, host=config.SCANNER_HOST, port=config.SCANNER_PORT)
    scanner_listener.start()

    # Install signal handler after scanner start so Ctrl+C sets _running and exits wait/main loop
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Optional: wait for first scan (set WAIT_FOR_FIRST_SCAN = False in config.py to run without scanner)
    if config.WAIT_FOR_FIRST_SCAN:
        print("[main] Waiting for first scanner event... (set WAIT_FOR_FIRST_SCAN = False in config.py to skip)")
        while _running and len(matcher.queues["q_scan"]) == 0:
            signal.signal(signal.SIGINT, shutdown)  # re-assert so Ctrl+C works if socketio overwrote it
            signal.signal(signal.SIGTERM, shutdown)
            time.sleep(0.1)
        if _running:
            print("[main] First scan received, starting main loop.")

    print("[main] To stop: press Ctrl+C, or (with --display) press 'q' in a window or close the window.")

    # CSV files (optional)
    csv_header = ["timestamp", "cam", "local_uid", "master_id", "route", "x1", "y1", "x2", "y2", "event"]
    csv_file = None
    csv_writer = None
    if args.csv:
        config.OUT_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = config.OUT_DIR / "tracking_logs_live.csv"
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.DictWriter(csv_file, fieldnames=csv_header)
        csv_writer.writeheader()

    # 4개 카메라 짝 맞춤 로그 (시간순 버퍼 사용 시만, high-level 이벤트)
    frame_sync_log_file = None
    processing_times_log_file = None
    last_stats_time = time.time()
    T_cur = None
    t_last_set = time.time()
    sets_formed_this_second = 0
    if use_time_ordered:
        config.OUT_DIR.mkdir(parents=True, exist_ok=True)
        frame_sync_log_path = config.OUT_DIR / "frame_sync_events.jsonl"
        frame_sync_log_file = open(frame_sync_log_path, "a", encoding="utf-8")
        processing_times_log_path = config.OUT_DIR / "yolo_processing_times.jsonl"
        processing_times_log_file = open(processing_times_log_path, "a", encoding="utf-8")

    active_tracks = {cam: {} for cam in config.TRACKING_CAMS}
    # 세트 내 detection crop → position API 호출 시 NFS 저장용 (필수: /mnt/thumbnails/{uid}.jpg)
    set_thumbnail_crops = {}
    local_uid_counter = {cam: 0 for cam in config.TRACKING_CAMS}
    last_processed_ts = {cam: None for cam in config.TRACKING_CAMS}
    target_interval = 0.25  # 250ms sub-second target
    # Phase 4: 카메라별 최근 소비 ts (resolve_pending now_s 유효성 제한용)
    last_consumed_ts = {cam: None for cam in config.TRACKING_CAMS}
    stale_sec = getattr(config, "STALE_FRAME_SEC", 30)
    resolve_ts_ahead_sec = getattr(config, "RESOLVE_PENDING_TS_AHEAD_SEC", 5)

    def process_one_frame(cam, img, ts, time_s):
        """한 카메라 프레임에 대한 전처리 및 감지 로직 호출."""
        cfg = config.CAM_SETTINGS.get(cam)
        if not cfg:
            return

        # 1. 이미지 회전
        rotate_val = cfg.get("rotate", 0)
        if rotate_val == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rotate_val == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif rotate_val == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # 2. 병목 방지를 위한 640 리사이징
        target_w = 640
        h_orig, w_orig = img.shape[:2]
        scale = target_w / w_orig
        target_h = int(h_orig * scale)
        img = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_AREA)

        # 3. 실시간 해상도 기반 비율 설정 계산하여 cfg 업데이트
        H, W = img.shape[:2]
        cfg['roi_y'] = int(H * cfg.get('roi_y_rate', 0))
        cfg['roi_margin'] = int(H * cfg.get('roi_margin_rate', 0))
        cfg['dist_eps'] = int(H * cfg.get('dist_eps_rate', 0))
        cfg['max_dy'] = int(H * cfg.get('max_dy_rate', 0))
        if 'eol_y_rate' in cfg:
            cfg['eol_y'] = int(H * cfg['eol_y_rate'])
            cfg['eol_margin'] = int(H * cfg['eol_margin_rate'])

        # 4. ROI 가이드라인 시각화 (display 옵션 시)
        if args.display:
            cv2.line(img, (0, cfg['roi_y']), (W, cfg['roi_y']), (0, 255, 255), 2)
            overlay = img.copy()
            y_min = max(0, cfg['roi_y'] - cfg['roi_margin'])
            y_max = min(H, cfg['roi_y'] + cfg['roi_margin'])
            cv2.rectangle(overlay, (0, y_min), (W, y_max), (0, 0, 255), -1)
            cv2.addWeighted(overlay, 0.2, img, 0.8, 0, img)
            if 'eol_y' in cfg:
                cv2.line(img, (0, cfg['eol_y']), (W, cfg['eol_y']), (255, 0, 255), 2)

        detections = detector.get_detections(img, cfg, cam)
        _process_with_detections(cam, img, ts, time_s, detections)

    def _process_with_detections(cam, img, ts, time_s, detections, thumbnail_crops=None):
        """detection 결과를 받아 matching, pending, resolve, position API, video/display 수행."""
        global cv2
        cfg = config.CAM_SETTINGS.get(cam)
        if not cfg:
            return
        
        # (img는 process_one_frame에서 이미 회전/리사이징됨)
        new_active = {}
        if thumbnail_crops is None:
            thumbnail_crops = {}

        for det in detections:
            x1, y1, x2, y2 = det["box"]
            cx, cy = det["center"]

            best_uid, best_score = None, 1e9
            for uid, info in active_tracks[cam].items():
                dx = abs(cx - info["last_pos"][0])
                dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]:
                    continue
                score = dx + dy * 0.3
                if score < best_score:
                    best_uid, best_score = uid, score

            route, mid, event_type = "UNKNOWN", None, "UNMATCHED"

            if best_uid:
                mid = active_tracks[cam][best_uid]["master_id"]
                if mid and mid in matcher.masters:
                    route = matcher.masters[mid]["route_code"]
                    if matcher.masters[mid]["status"] == "MISSING":
                        continue
                    event_type = "TRACKING"
            else:
                local_uid_counter[cam] += 1
                best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                match_cam = "RPI_USB3_EOL" if det.get("in_eol") else cam
                mid = matcher.try_match(match_cam, time_s, det["width"], best_uid)

                if mid and mid in matcher.masters:
                    route = matcher.masters[mid]["route_code"]
                    if (route == "XSEA" and cam == "RPI_USB3") or (route == "XSEB" and match_cam == "RPI_USB3_EOL"):
                        if matcher.masters[mid]["status"] != "MISSING":
                            matcher.masters[mid]["status"] = "MISSING"
                            api_helper.api_missing(mid)
                        event_type = "MISSING"
                    else:
                        matcher.masters[mid]["status"] = "TRACKING"
                        event_type = "MATCHED"

            if args.csv and csv_writer:
                csv_writer.writerow({
                    "timestamp": ts, "cam": cam, "local_uid": best_uid, "master_id": mid or "",
                    "route": route, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "event": event_type
                })
            
            if event_type != "MISSING":
                new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
                # 썸네일: USB_LOCAL 일 때만 TRACKING/MATCHED 시 crop → NFS 저장
                if mid and event_type in ("TRACKING", "MATCHED"):
                    if cam == "USB_LOCAL":
                        h, w = img.shape[:2]
                        crop = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                        if crop.size > 0:
                            save_thumbnail_to_nfs(mid, crop)
                            set_thumbnail_crops[mid] = crop

        # Pending: mark as PENDING if no longer in frame
        for old_uid, old_info in active_tracks[cam].items():
            if old_uid not in new_active:
                mid = old_info["master_id"]
                if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
                    matcher.masters[mid]["status"] = "PENDING"
                    matcher.masters[mid]["pending_from_cam"] = cam

        # Resolve pending (Phase 4: stale 또는 now_s가 너무 앞서면 생략)
        skip_resolve = False
        if time.time() - time_s > stale_sec:
            skip_resolve = True
        else:
            last_consumed_ts[cam] = time_s
            min_consumed = min((t for t in last_consumed_ts.values() if t is not None), default=time_s)
            if time_s > min_consumed + resolve_ts_ahead_sec:
                skip_resolve = True
        
        if not skip_resolve:
            for mid in list(matcher.masters.keys()):
                result = matcher.resolve_pending(mid, time_s)
                if result:
                    decision = result["decision"]
                    if decision == "PICKUP":
                        api_helper.api_pickup(mid)
                        if args.csv and csv_writer:
                            csv_writer.writerow({
                                "timestamp": ts, "cam": result["from_cam"], "local_uid": "",
                                "master_id": mid, "route": matcher.masters[mid]["route_code"],
                                "x1": "", "y1": "", "x2": "", "y2": "", "event": "PICKUP"
                            })
                    elif decision == "DISAPPEAR":
                        api_helper.api_disappear(mid)
                        if args.csv and csv_writer:
                            csv_writer.writerow({
                                "timestamp": ts, "cam": "", "local_uid": "", "master_id": mid,
                                "route": matcher.masters[mid]["route_code"],
                                "x1": "", "y1": "", "x2": "", "y2": "", "event": "DISAPPEAR"
                            })

        # Distance / position API (TRACKING or PENDING)
        for mid, m_info in matcher.masters.items():
            if m_info["status"] in ["TRACKING", "PENDING"] and m_info.get("start_time") is not None:
                total_dist = config.ROUTE_TOTAL_DIST.get(m_info["route_code"], 12.8)
                elapsed_time = time_s - m_info["start_time"]
                rem_dist = max(0.0, total_dist - (elapsed_time * config.BELT_SPEED))
                step_dist = round(rem_dist / 0.5) * 0.5
                if m_info.get("last_sent_dist") != step_dist:
                    api_helper.api_update_position(
                        mid, step_dist,
                        thumbnail_image=set_thumbnail_crops.get(mid),
                    )
                    m_info["last_sent_dist"] = step_dist

        if args.video:
            atracks = dict(active_tracks)
            atracks[cam] = new_active
            visualizer.draw_and_write(cam, img, detections, matcher.masters, ts, atracks)
        
        if args.display:
            cv2.putText(img, f"CAM: {cam} | Resized: {img.shape[1]}x{img.shape[0]}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow(f"track_{cam}", img)
        
        active_tracks[cam] = new_active

    def _timed_get_detections(cam, img, cfg):
        """get_detections 실행 + 소요 시간(초) 반환."""
        t0 = time.perf_counter()
        dets = detector.get_detections(img, cfg, cam)
        return (dets, time.perf_counter() - t0)

    def run_detections_for_set(set_):
        """4 cam detection을 병렬 실행."""
        from concurrent.futures import ThreadPoolExecutor
        out = {}
        per_cam_sec = {}
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {}
            for cam in config.TRACKING_CAMS:
                if cam not in set_:
                    continue
                img, _ = set_[cam]
                cfg = config.CAM_SETTINGS.get(cam)
                if not cfg or img is None:
                    continue
                
                # 1. 회전
                rotate_val = cfg.get("rotate", 0)
                if rotate_val == 90: img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
                elif rotate_val == 180: img = cv2.rotate(img, cv2.ROTATE_180)
                elif rotate_val == 270: img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

                # 2. 리사이징
                target_w = 640
                h_orig, w_orig = img.shape[:2]
                scale = target_w / w_orig
                img = cv2.resize(img, (target_w, int(h_orig * scale)), interpolation=cv2.INTER_AREA)

                # 3. 비율 기반 계산
                H_new, W_new = img.shape[:2]
                cfg['roi_y'] = int(H_new * cfg.get('roi_y_rate', 0))
                cfg['roi_margin'] = int(H_new * cfg.get('roi_margin_rate', 0))
                cfg['dist_eps'] = int(H_new * cfg.get('dist_eps_rate', 0))
                cfg['max_dy'] = int(H_new * cfg.get('max_dy_rate', 0))
                if 'eol_y_rate' in cfg:
                    cfg['eol_y'] = int(H_new * cfg['eol_y_rate'])
                    cfg['eol_margin'] = int(H_new * cfg['eol_margin_rate'])
                
                # 가이드라인 시각화 (set 모드에서도 시각화 필요시)
                if args.display:
                    cv2.line(img, (0, cfg['roi_y']), (W_new, cfg['roi_y']), (0, 255, 255), 2)

                futures[cam] = ex.submit(_timed_get_detections, cam, img, cfg)
            
            for cam, fut in futures.items():
                dets, elapsed = fut.result()
                out[cam] = dets
                per_cam_sec[cam] = round(elapsed, 4)
        wall_sec = time.perf_counter() - t0
        return out, per_cam_sec, wall_sec

    def time_based_position_update(now_s: float) -> None:
        """세트 스킵 시 now_s 기준으로 거리 갱신."""
        for mid, m_info in matcher.masters.items():
            if m_info["status"] in ["TRACKING", "PENDING"] and m_info.get("start_time") is not None:
                total_dist = config.ROUTE_TOTAL_DIST.get(m_info["route_code"], 12.8)
                elapsed_time = now_s - m_info["start_time"]
                rem_dist = max(0.0, total_dist - (elapsed_time * config.BELT_SPEED))
                step_dist = round(rem_dist / 0.5) * 0.5
                if m_info.get("last_sent_dist") != step_dist:
                    api_helper.api_update_position(
                        mid, step_dist,
                        thumbnail_image=set_thumbnail_crops.get(mid),
                    )
                    m_info["last_sent_dist"] = step_dist

    # 500ms 윈도우 세트 모드
    window_interval = getattr(config, "WINDOW_SET_INTERVAL_SEC", 0.5)
    max_wait_wall = getattr(config, "WINDOW_MAX_WAIT_WALL_SEC", 0.5)
    THEORETICAL_MAX_SETS_PER_SEC = 2

    try:
        while _running:
            if use_time_ordered:
                if T_cur is None:
                    T_cur = frame_sink.get_min_timestamp()
                    if T_cur is None:
                        time.sleep(0.01)
                        continue

                set_ = frame_sink.extract_set_for_interval(T_cur, T_cur + window_interval)
                if set_ is not None:
                    set_thumbnail_crops.clear()
                    dets_per_cam, detection_per_cam_sec, detection_wall_sec = run_detections_for_set(set_)
                    for cam in config.TRACKING_CAMS:
                        if cam not in set_: continue
                        img, ts = set_[cam]
                        if img is None: continue
                        # _process_with_detections 내부에서 img를 사용하여 display/video 처리
                        # run_detections_for_set 내부에서 전처리된 img를 다시 가져오는 구조는 복잡하므로 
                        # 여기서는 단순화하여 process_one_frame 흐름과 유사하게 맞춤
                        process_one_frame(cam, img, ts, ts) 

                    sets_formed_this_second += 1
                    T_cur += window_interval
                    t_last_set = time.time()
                elif time.time() - t_last_set >= max_wait_wall:
                    time_based_position_update(T_cur + window_interval)
                    frame_sink.remove_frames_in_interval(T_cur, T_cur + window_interval)
                    T_cur += window_interval
                    t_last_set = time.time()
                else:
                    time.sleep(0.01)
            else:
                # 일반 FrameAggregator 모드 (개별 카메라 순회)
                for cam in config.TRACKING_CAMS:
                    pair = frame_sink.get(cam)
                    if pair is None: continue
                    img, ts = pair
                    if img is None: continue
                    if last_processed_ts[cam] is not None and abs(ts - last_processed_ts[cam]) < target_interval * 0.5:
                        continue
                    last_processed_ts[cam] = ts
                    process_one_frame(cam, img, ts, ts)

            if args.display:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    _running = False
            time.sleep(0.01)

    finally:
        _running = False
        for recv in receivers: recv.stop()
        for worker, _ in usb_workers: worker.stop()
        scanner_listener.stop()
        visualizer.release_all()
        if csv_file: csv_file.close()
        if frame_sync_log_file: frame_sync_log_file.close()
        if args.display: cv2.destroyAllWindows()
        ctx.term()


if __name__ == "__main__":
    main()