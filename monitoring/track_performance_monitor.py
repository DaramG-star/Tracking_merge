#!/usr/bin/env python3
"""
Track 서비스 성능 모니터링 스크립트
- 4개 카메라 수신 상태 (HTTP API 연동 시)
- 프레임 처리 latency (API 연동 시)
- API 호출 통계 (MongoDB 또는 로그 기반)

참고: 현재 track 서비스(main.py)는 HTTP 서버를 띄우지 않음. Camera/Tracking/Latency는
HTTP API를 제공하는 별도 서비스가 있을 때만 값이 채워짐. 연결 거부/타임아웃은
'예상된 비가용'으로 간주하여 errors에 넣지 않음 (--strict 시에만 에러로 집계).
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

try:
    from pymongo import MongoClient
    from pymongo import errors as _pymongo_errors
    _ServerSelectionTimeoutError = getattr(_pymongo_errors, "ServerSelectionTimeoutError", None)
except ImportError:
    MongoClient = None
    _ServerSelectionTimeoutError = None

# 기본 설정
MONITOR_DURATION = 3600
SAMPLE_INTERVAL = 5
API_BASE = "http://localhost:8001"

results = {
    "start_time": None,
    "end_time": None,
    "camera_stats": {i: {"received": 0, "dropped": 0, "fps": []} for i in range(4)},
    "tracking_stats": {
        "total_tracks": 0,
        "active_tracks": 0,
        "disappeared_count": 0,
        "pending_count": 0,
    },
    "latency_stats": {
        "frame_to_detection": [],
        "detection_to_tracking": [],
        "total_pipeline": [],
    },
    "api_calls": {
        "detect-disappear": 0,
        "detect-position": 0,
        "detect-pickup": 0,
        "detect-eol": 0,
    },
    "errors": [],
    "warnings": [],  # 예상된 비가용(연결 거부 등) 요약, 1회만 기록
}


def _is_expected_unavailable(exc: Exception, strict: bool) -> bool:
    """연결 거부/타임아웃 등 '엔드포인트 미제공'으로 인한 예외면 True (strict=False일 때만)."""
    if strict:
        return False
    if requests:
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    if _ServerSelectionTimeoutError and isinstance(exc, _ServerSelectionTimeoutError):
        return True
    return False


def monitor_camera_reception(api_base: str, strict: bool = False):
    """4개 카메라 프레임 수신 상태 (HTTP API 연동 시)."""
    if not requests:
        return
    try:
        r = requests.get(f"{api_base}/api/camera-status", timeout=1)
        if r.status_code == 200:
            data = r.json()
            for i in range(4):
                cam = data.get(f"camera_{i}", {})
                results["camera_stats"][i]["received"] += 1
                results["camera_stats"][i]["fps"].append(cam.get("fps", 0))
                if cam.get("dropped", 0) > 0:
                    results["camera_stats"][i]["dropped"] += 1
    except Exception as e:
        if _is_expected_unavailable(e, strict):
            if not results["warnings"]:
                results["warnings"].append("HTTP API unavailable (connection refused/timeout) - not counted as errors")
        else:
            results["errors"].append(f"Camera monitoring: {e}")


def monitor_tracking_performance(api_base: str, strict: bool = False):
    """Tracking 성능 (API 연동 시)."""
    if not requests:
        return
    try:
        r = requests.get(f"{api_base}/api/tracking-stats", timeout=1)
        if r.status_code == 200:
            data = r.json()
            results["tracking_stats"]["total_tracks"] = data.get("total_tracks", 0)
            results["tracking_stats"]["active_tracks"] = data.get("active_tracks", 0)
            results["tracking_stats"]["pending_count"] = data.get("pending_count", 0)
    except Exception as e:
        if _is_expected_unavailable(e, strict):
            if not results["warnings"]:
                results["warnings"].append("HTTP API unavailable (connection refused/timeout) - not counted as errors")
        else:
            results["errors"].append(f"Tracking monitoring: {e}")


def monitor_latency(api_base: str, strict: bool = False):
    """파이프라인 latency (API 연동 시)."""
    if not requests:
        return
    try:
        r = requests.get(f"{api_base}/api/latency-stats", timeout=1)
        if r.status_code == 200:
            data = r.json()
            results["latency_stats"]["frame_to_detection"].append(data.get("frame_to_detection", 0))
            results["latency_stats"]["detection_to_tracking"].append(data.get("detection_to_tracking", 0))
            results["latency_stats"]["total_pipeline"].append(data.get("total_pipeline", 0))
    except Exception as e:
        if _is_expected_unavailable(e, strict):
            if not results["warnings"]:
                results["warnings"].append("HTTP API unavailable (connection refused/timeout) - not counted as errors")
        else:
            results["errors"].append(f"Latency monitoring: {e}")


def monitor_api_calls_mongo(
    uri: str = "mongodb://localhost:27017", db_name: str = "logistics", interval_sec: int = 5, strict: bool = False
):
    """API 호출 통계 (MongoDB api_logs 컬렉션 있을 때)."""
    if not MongoClient:
        return
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        db = client[db_name]
        recent = datetime.now().timestamp() - interval_sec
        for api in ["detect-disappear", "detect-position", "detect-pickup", "detect-eol"]:
            try:
                n = db.get_collection("api_logs").count_documents(
                    {"endpoint": f"/api/{api}", "timestamp": {"$gte": recent}}
                )
                results["api_calls"][api] += n
            except Exception:
                pass
        client.close()
    except Exception as e:
        if _is_expected_unavailable(e, strict):
            if not any("MongoDB" in w for w in results["warnings"]):
                results["warnings"].append("MongoDB unavailable (connection timeout) - not counted as errors")
        else:
            results["errors"].append(f"API MongoDB: {e}")


def print_live_stats():
    """실시간 통계 출력."""
    print("\n" + "=" * 70)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Track Service Monitoring")
    print("=" * 70)
    print("\n[Camera Reception]")
    for i in range(4):
        cam = results["camera_stats"][i]
        fps_avg = sum(cam["fps"][-10:]) / len(cam["fps"][-10:]) if cam["fps"] else 0
        dropped_rate = cam["dropped"] / max(cam["received"], 1) * 100
        print(f"  Camera {i}: Received={cam['received']}, Dropped={cam['dropped']} ({dropped_rate:.1f}%), FPS={fps_avg:.1f}")
    print("\n[Tracking Status]")
    ts = results["tracking_stats"]
    print(f"  Total tracks: {ts['total_tracks']}, Active: {ts['active_tracks']}, Pending: {ts['pending_count']}")
    print("\n[Latency (avg last 10)]")
    for key, values in results["latency_stats"].items():
        if values:
            avg = sum(values[-10:]) / len(values[-10:])
            print(f"  {key}: {avg:.2f}ms")
    print("\n[API Calls (total)]")
    for api, count in results["api_calls"].items():
        print(f"  {api}: {count}")
    if results.get("warnings"):
        print(f"\n[Note] {'; '.join(results['warnings'])}")
    if results["errors"]:
        print(f"\n[Errors] {len(results['errors'])} (see results JSON)")


def save_results(out_dir: Path):
    """최종 결과 저장."""
    results["end_time"] = datetime.now().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {path}")
    return path


def main():
    p = argparse.ArgumentParser(description="Track service performance monitor")
    p.add_argument("--duration", type=int, default=MONITOR_DURATION, help="Monitor duration (seconds)")
    p.add_argument("--interval", type=int, default=5, help="Sample interval (seconds)")
    p.add_argument("--api-base", default=API_BASE, help="Track service API base URL (optional)")
    p.add_argument("--mongo-uri", default="mongodb://localhost:27017", help="MongoDB URI for API logs")
    p.add_argument("--out-dir", default=None, help="Output directory for results JSON (default: monitoring/)")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat connection refused/timeout as errors (default: no, they are expected when track has no HTTP API)",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    results["start_time"] = datetime.now().isoformat()

    print(f"Monitoring for {args.duration}s, interval {args.interval}s, API base={args.api_base}")
    if not args.strict:
        print("Note: Connection refused/timeout are NOT counted as errors (track service has no HTTP API by default).")
    start = time.time()
    try:
        while time.time() - start < args.duration:
            monitor_camera_reception(args.api_base, strict=args.strict)
            monitor_tracking_performance(args.api_base, strict=args.strict)
            monitor_latency(args.api_base, strict=args.strict)
            monitor_api_calls_mongo(args.mongo_uri, interval_sec=args.interval, strict=args.strict)
            print_live_stats()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
    finally:
        save_results(out_dir)
        print("\n" + "=" * 70)
        print("FINAL SUMMARY")
        print("=" * 70)
        print_live_stats()


if __name__ == "__main__":
    main()
