#!/usr/bin/env python3
"""
4개 카메라 짝 맞춤 모니터링 (병목·FPS·이론/실제 짝·250ms 구간 분석)

확인 항목:
1. 각 카메라의 초당 프레임 수 (fps)
2. 어느 카메라가 병목인가
3. 이론적 최대 짝 수 (500ms×2 = 2.0/sec 고정) vs 실제 짝 수 → 효율 (0~100%)
4. 250ms 구간별 4 cam 모두 있는가, 어느 카메라가 어느 구간에서 누락되는가

track을 시간순 버퍼로 실행하면 frame_sync_events.jsonl 에 FRAME_STATS(1초마다)가 쌓임.
사용: python main.py
      python3 monitoring/frame_sync_monitor.py [--log-path ...] [--last N]
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

TRACK_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = TRACK_ROOT / "output" / "Parcel_Integration_Log_FIFO" / "frame_sync_events.jsonl"


def analyze_frame_sync_log(log_path: Path, last_n: int = 0):
    """
    FRAME_STATS 이벤트만 읽어서 분석.
    last_n: 0이면 전체, >0이면 마지막 N개 FRAME_STATS만 사용.
    """
    if not log_path.exists():
        return None, f"Log file not found: {log_path}"

    stats_list = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") != "FRAME_STATS":
                continue
            stats_list.append(ev)

    if last_n > 0 and len(stats_list) > last_n:
        stats_list = stats_list[-last_n:]

    if not stats_list:
        return None, "No FRAME_STATS events (run track with time-ordered buffer for at least 1 second)."

    # 집계
    cams = list(stats_list[0].get("frame_counts", {}).keys()) or []
    n_seconds = len(stats_list)
    frame_totals = defaultdict(int)
    actual_sets_total = 0
    theoretical_total = 0
    quarter_complete_count = 0
    quarter_total = 0
    cam_missing_in_quarter = defaultdict(int)  # cam -> 횟수 (해당 cam이 missing인 quarter 수)

    for ev in stats_list:
        fc = ev.get("frame_counts", {})
        for c, cnt in fc.items():
            frame_totals[c] += cnt
        actual_sets_total += ev.get("actual_sets_created", 0)
        theoretical_total += ev.get("theoretical_max_sets", 0)
        for q in ev.get("quarters", []):
            quarter_total += 1
            if q.get("set_possible"):
                quarter_complete_count += 1
            for c in q.get("missing_cameras", []):
                cam_missing_in_quarter[c] += 1

    # FPS = 1초당 평균이므로, frame_totals[c] / n_seconds
    fps = {c: frame_totals[c] / n_seconds for c in cams}
    bottleneck = min(cams, key=lambda c: fps.get(c, 0)) if cams else None
    theoretical_per_sec = theoretical_total / n_seconds if n_seconds else 0
    actual_per_sec = actual_sets_total / n_seconds if n_seconds else 0
    efficiency = (actual_sets_total / theoretical_total) if theoretical_total > 0 else 0.0
    quarter_success_rate = (quarter_complete_count / quarter_total * 100) if quarter_total else 0.0

    result = {
        "n_seconds": n_seconds,
        "cams": cams,
        "fps": fps,
        "bottleneck_camera": bottleneck,
        "theoretical_max_sets_per_sec": round(theoretical_per_sec, 2),
        "actual_sets_per_sec": round(actual_per_sec, 2),
        "efficiency": round(efficiency * 100, 1),
        "quarter_total": quarter_total,
        "quarter_complete_count": quarter_complete_count,
        "quarter_success_rate": round(quarter_success_rate, 1),
        "cam_missing_in_quarter": dict(cam_missing_in_quarter),
    }
    return result, None


def print_report(result: dict, log_path: Path):
    print("\n" + "=" * 60)
    print("Frame Sync Analysis (4-cam pair)")
    print("=" * 60)
    print(f"  Log: {log_path}")
    print(f"  Period: last {result['n_seconds']} seconds (FRAME_STATS events)")
    print()
    print("[1] Camera Frame Rates (fps)")
    for c in result["cams"]:
        f = result["fps"].get(c, 0)
        mark = "  BOTTLENECK" if c == result["bottleneck_camera"] else ""
        print(f"  {c}: {f:.1f} fps{mark}")
    print()
    print("[2] Theoretical vs Actual Sets")
    print(f"  Bottleneck: {result['bottleneck_camera']} ({result['fps'].get(result['bottleneck_camera'], 0):.1f} fps)")
    print(f"  Theoretical max sets: {result['theoretical_max_sets_per_sec']}/sec")
    print(f"  Actual sets created:  {result['actual_sets_per_sec']}/sec")
    print(f"  Efficiency: {result['efficiency']}%")
    print()
    print("[3] Quarter-Second (250ms) Analysis")
    print(f"  Quarters with all 4 cameras: {result['quarter_complete_count']}/{result['quarter_total']} ({result['quarter_success_rate']}%)")
    print("  Quarters missing per camera:")
    max_miss = max(result["cam_missing_in_quarter"].values() or [0])
    for c in result["cams"]:
        cnt = result["cam_missing_in_quarter"].get(c, 0)
        mark = "  Most problematic" if cnt == max_miss and max_miss > 0 else ""
        print(f"    {c}: {cnt}{mark}")
    print()
    print("=" * 60)


def main():
    p = argparse.ArgumentParser(description="Frame sync monitor: FPS, bottleneck, efficiency, quarter analysis")
    p.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="Path to frame_sync_events.jsonl")
    p.add_argument("--last", type=int, default=0, help="Use only last N FRAME_STATS (0 = all)")
    p.add_argument("--watch", type=float, default=0, help="Re-run every N seconds (0 = once)")
    args = p.parse_args()

    log_path = args.log_path if args.log_path.is_absolute() else TRACK_ROOT / args.log_path

    if args.watch > 0:
        import time
        try:
            while True:
                result, err = analyze_frame_sync_log(log_path, last_n=args.last or 10)
                if err:
                    print(err)
                else:
                    print_report(result, log_path)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nStopped")
    else:
        result, err = analyze_frame_sync_log(log_path, last_n=args.last)
        if err:
            print(err, file=sys.stderr)
            sys.exit(1)
        print_report(result, log_path)


if __name__ == "__main__":
    main()
