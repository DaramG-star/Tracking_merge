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
        """한 카메라 프레임에 대한 detection, matching, pending, resolve, position API, video/display."""
        cfg = config.CAM_SETTINGS.get(cam)
        if not cfg:
            return
        detections = detector.get_detections(img, cfg, cam)
        _process_with_detections(cam, img, ts, time_s, detections)

    def _process_with_detections(cam, img, ts, time_s, detections, thumbnail_crops=None):
        """detection 결과를 받아 matching, pending, resolve, position API, video/display만 수행."""
        cfg = config.CAM_SETTINGS.get(cam)
        if not cfg:
            return
        
        rotate_val = cfg.get("rotate", 0)
        if rotate_val == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rotate_val == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif rotate_val == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

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
                # 썸네일: TRACKING/MATCHED 시 crop → NFS에 반드시 저장 (/mnt/thumbnails/{uid}.jpg). 100 서버 웹이 /thumbnails/{uid}.jpg 로 제공.
                if mid and event_type in ("TRACKING", "MATCHED"):
                    h, w = img.shape[:2]
                    crop = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                    if crop.size > 0:
                        save_thumbnail_to_nfs(mid, crop)
                        set_thumbnail_crops[mid] = crop  # position API 호출 시에도 NFS 저장용으로 전달

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
            import cv2
            cv2.imshow(f"track_{cam}", img)
        active_tracks[cam] = new_active

    def _timed_get_detections(cam, img, cfg):
        """get_detections 실행 + 소요 시간(초) 반환. (detections, elapsed_sec)."""
        t0 = time.perf_counter()
        dets = detector.get_detections(img, cfg, cam)
        return (dets, time.perf_counter() - t0)

    def run_detections_for_set(set_):
        """4 cam detection을 병렬 실행해 ({cam: detections}, detection_per_cam_sec, wall_sec) 반환."""
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
                futures[cam] = ex.submit(_timed_get_detections, cam, img, cfg)
            for cam, fut in futures.items():
                dets, elapsed = fut.result()
                out[cam] = dets
                per_cam_sec[cam] = round(elapsed, 4)
        wall_sec = time.perf_counter() - t0
        return out, per_cam_sec, wall_sec

    def time_based_position_update(now_s: float) -> None:
        """세트 스킵 시: 비전 없이 TRACKING/PENDING master의 남은 거리만 now_s 기준으로 갱신."""
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

    # 500ms 윈도우 세트 모드 (스트림 시간 + wall-clock 상한)
    window_interval = getattr(config, "WINDOW_SET_INTERVAL_SEC", 0.5)
    max_wait_wall = getattr(config, "WINDOW_MAX_WAIT_WALL_SEC", 0.5)
    THEORETICAL_MAX_SETS_PER_SEC = 2

    try:
        while _running:
            if use_time_ordered:
                # T_cur 초기화: 버퍼에 데이터 있을 때 최소 timestamp
                if T_cur is None:
                    T_cur = frame_sink.get_min_timestamp()
                    if T_cur is None:
                        time.sleep(0.01)
                        if time.time() - last_stats_time >= 1.0:
                            frame_counts, quarter_counts = frame_sink.get_stats_and_reset()
                            cams = config.TRACKING_CAMS
                            bottleneck_cam = min(cams, key=lambda c: frame_counts.get(c, 0)) if cams else None
                            stats_ev = {
                                "event": "FRAME_STATS",
                                "timestamp": time.time(),
                                "interval": "1s",
                                "frame_counts": frame_counts,
                                "bottleneck_camera": bottleneck_cam,
                                "theoretical_max_sets": THEORETICAL_MAX_SETS_PER_SEC,
                                "actual_sets_created": 0,
                                "efficiency": 0.0,
                                "quarters": [{"quarter": q, "frames_in_quarter": quarter_counts[q], "set_possible": False, "missing_cameras": cams} for q in range(min(4, len(quarter_counts)))],
                            }
                            if frame_sync_log_file:
                                frame_sync_log_file.write(json.dumps(stats_ev, ensure_ascii=False) + "\n")
                                frame_sync_log_file.flush()
                            last_stats_time = time.time()
                        continue

                set_ = frame_sink.extract_set_for_interval(T_cur, T_cur + window_interval)
                if set_ is not None:
                    # 4 cam detection 병렬 실행 후, 라우트 순서로 matching/API 처리
                    set_t0 = time.perf_counter()
                    set_thumbnail_crops.clear()  # 세트마다 NFS 저장용 crop 초기화
                    dets_per_cam, detection_per_cam_sec, detection_wall_sec = run_detections_for_set(set_)
                    process_per_cam_sec = {}
                    for cam in config.TRACKING_CAMS:
                        if cam not in set_:
                            continue
                        img, ts = set_[cam]
                        if img is None:
                            continue
                        pt0 = time.perf_counter()
                        _process_with_detections(cam, img, ts, ts, dets_per_cam.get(cam, []))
                        process_per_cam_sec[cam] = round(time.perf_counter() - pt0, 4)
                    process_wall_sec = time.perf_counter() - set_t0 - detection_wall_sec
                    set_total_wall_sec = time.perf_counter() - set_t0
                    if processing_times_log_file:
                        processing_times_log_file.write(json.dumps({
                            "event": "PROCESSING_TIMES",
                            "timestamp": time.time(),
                            "detection_wall_sec": round(detection_wall_sec, 4),
                            "detection_per_cam_sec": detection_per_cam_sec,
                            "process_per_cam_sec": process_per_cam_sec,
                            "process_wall_sec": round(process_wall_sec, 4),
                            "set_total_wall_sec": round(set_total_wall_sec, 4),
                        }, ensure_ascii=False) + "\n")
                        processing_times_log_file.flush()
                    sets_formed_this_second += 1
                    T_cur += window_interval
                    t_last_set = time.time()
                    if frame_sync_log_file:
                        ev = {"event": "FRAME_SET_COMPLETE", "timestamp": time.time(), "window": [T_cur - window_interval, T_cur]}
                        frame_sync_log_file.write(json.dumps(ev, ensure_ascii=False) + "\n")
                        frame_sync_log_file.flush()
                elif time.time() - t_last_set >= max_wait_wall:
                    # 스킵: time-based position update, 버퍼 정리, 윈도우 진행
                    now_s = T_cur + window_interval
                    time_based_position_update(now_s)
                    frames_in = frame_sink.get_frames_in_interval(T_cur, T_cur + window_interval)
                    missing_cameras = [c for c in config.TRACKING_CAMS if c not in frames_in]
                    frame_sink.remove_frames_in_interval(T_cur, T_cur + window_interval)
                    if frame_sync_log_file:
                        ev = {"event": "FRAME_SET_INCOMPLETE", "timestamp": time.time(), "window": [T_cur, T_cur + window_interval], "missing_cameras": missing_cameras}
                        frame_sync_log_file.write(json.dumps(ev, ensure_ascii=False) + "\n")
                        frame_sync_log_file.flush()
                    T_cur += window_interval
                    t_last_set = time.time()
                else:
                    # 4장 아직 안 모임, wall-clock 상한 전까지 대기
                    time.sleep(0.01)

                # 1초마다 FRAME_STATS
                if time.time() - last_stats_time >= 1.0:
                    frame_counts, quarter_counts = frame_sink.get_stats_and_reset()
                    cams = config.TRACKING_CAMS
                    bottleneck_cam = min(cams, key=lambda c: frame_counts.get(c, 0)) if cams else None
                    actual_sets = sets_formed_this_second
                    efficiency = (actual_sets / THEORETICAL_MAX_SETS_PER_SEC) if THEORETICAL_MAX_SETS_PER_SEC else 0.0
                    quarters_out = []
                    for q_idx, q in enumerate(quarter_counts):
                        set_possible = all(q.get(c, 0) > 0 for c in cams)
                        missing_cams = [c for c in cams if q.get(c, 0) == 0]
                        quarters_out.append({
                            "quarter": q_idx,
                            "frames_in_quarter": {c: q.get(c, 0) for c in cams},
                            "set_possible": set_possible,
                            "missing_cameras": missing_cams,
                        })
                    stats_ev = {
                        "event": "FRAME_STATS",
                        "timestamp": time.time(),
                        "interval": "1s",
                        "frame_counts": frame_counts,
                        "bottleneck_camera": bottleneck_cam,
                        "theoretical_max_sets": THEORETICAL_MAX_SETS_PER_SEC,
                        "actual_sets_created": actual_sets,
                        "efficiency": round(efficiency, 4),
                        "quarters": quarters_out,
                    }
                    if frame_sync_log_file:
                        frame_sync_log_file.write(json.dumps(stats_ev, ensure_ascii=False) + "\n")
                        frame_sync_log_file.flush()
                    sets_formed_this_second = 0
                    last_stats_time = time.time()
            else:
                for cam in config.TRACKING_CAMS:
                    pair = frame_sink.get(cam)
                    if pair is None:
                        continue
                    img, ts = pair
                    if img is None:
                        continue
                    if last_processed_ts[cam] is not None and abs(ts - last_processed_ts[cam]) < target_interval * 0.5:
                        continue
                    last_processed_ts[cam] = ts
                    time_s = ts
                    process_one_frame(cam, img, ts, time_s)

            if args.display:
                import cv2
                signal.signal(signal.SIGINT, shutdown)
                signal.signal(signal.SIGTERM, shutdown)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    _running = False
                for _cam in config.TRACKING_CAMS:
                    try:
                        if cv2.getWindowProperty(f"track_{_cam}", cv2.WND_PROP_VISIBLE) < 0:
                            _running = False
                            break
                    except Exception:
                        pass
            time.sleep(0.01)

    finally:
        _running = False
        for recv in receivers:
            recv.stop()
        for worker, _ in usb_workers:
            worker.stop()
        scanner_listener.stop()
        visualizer.release_all()
        if csv_file:
            csv_file.close()
        if frame_sync_log_file:
            frame_sync_log_file.close()
        if processing_times_log_file:
            processing_times_log_file.close()
        if args.display:
            import cv2
            cv2.destroyAllWindows()
        ctx.term()


if __name__ == "__main__":
    main()
