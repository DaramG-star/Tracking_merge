#!/usr/bin/env python3
"""
yolo_processing_times.jsonl 집계: detection_wall_sec, process_wall_sec, set_total_wall_sec 통계.
실행: python3 monitoring/analyze_yolo_times.py [--path ...] [--last N]
"""
import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

TRACK_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = TRACK_ROOT / "output" / "Parcel_Integration_Log_FIFO" / "yolo_processing_times.jsonl"


def analyze(log_path: Path, last_n: int = 0):
    if not log_path.exists():
        return None, f"Log not found: {log_path}"

    rows = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") != "PROCESSING_TIMES":
                continue
            rows.append(ev)

    if last_n > 0 and len(rows) > last_n:
        rows = rows[-last_n:]

    if not rows:
        return None, "No PROCESSING_TIMES events (run track with time-ordered buffer and form at least one set)."

    n = len(rows)
    detection_wall = [r["detection_wall_sec"] for r in rows]
    process_wall = [r["process_wall_sec"] for r in rows]
    set_total = [r["set_total_wall_sec"] for r in rows]

    # per_cam detection (sample first row keys)
    cams = list(rows[0].get("detection_per_cam_sec", {}).keys())
    detection_per_cam = defaultdict(list)
    process_per_cam = defaultdict(list)
    for r in rows:
        for c, v in r.get("detection_per_cam_sec", {}).items():
            detection_per_cam[c].append(v)
        for c, v in r.get("process_per_cam_sec", {}).items():
            process_per_cam[c].append(v)

    def stats(lst):
        if not lst:
            return {}
        return {
            "mean_sec": round(sum(lst) / len(lst), 4),
            "min_sec": round(min(lst), 4),
            "max_sec": round(max(lst), 4),
            "n": len(lst),
        }

    result = {
        "n_sets": n,
        "detection_wall_sec": stats(detection_wall),
        "process_wall_sec": stats(process_wall),
        "set_total_wall_sec": stats(set_total),
        "detection_per_cam_sec": {c: stats(detection_per_cam[c]) for c in cams},
        "process_per_cam_sec": {c: stats(process_per_cam[c]) for c in cams},
    }
    return result, None


def main():
    p = argparse.ArgumentParser(description="Analyze yolo_processing_times.jsonl")
    p.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Path to jsonl")
    p.add_argument("--last", type=int, default=0, help="Use only last N events (0=all)")
    args = p.parse_args()
    log_path = args.path if args.path.is_absolute() else TRACK_ROOT / args.path
    result, err = analyze(log_path, last_n=args.last)
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
