#!/usr/bin/env python3
"""
모니터링 결과 JSON 분석 및 보고서 생성.
사용: python analyze_results.py <results_YYYYMMDD_HHMMSS.json>
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None


def analyze_results(results_file: str) -> str:
    with open(results_file, encoding="utf-8") as f:
        data = json.load(f)

    report = []
    report.append("=" * 70)
    report.append("Track Service Performance Analysis Report")
    report.append("=" * 70)
    report.append(f"\nStart: {data.get('start_time', 'N/A')}")
    report.append(f"End:   {data.get('end_time', 'N/A')}")

    report.append("\n## 1. Camera Reception Analysis")
    cam_stats = data.get("camera_stats", {})
    for i in range(4):
        cam = cam_stats.get(i) or cam_stats.get(str(i), {})
        total = cam["received"]
        dropped = cam["dropped"]
        drop_rate = dropped / max(total, 1) * 100
        fps_list = cam.get("fps") or []
        fps_avg = float(np.mean(fps_list)) if np and fps_list else 0.0
        fps_std = float(np.std(fps_list)) if np and len(fps_list) > 1 else 0.0
        status = "PASS" if drop_rate < 5 and fps_avg >= 15 else "FAIL"
        report.append(f"\nCamera {i}: [{status}]")
        report.append(f"  Total frames received: {total}")
        report.append(f"  Dropped: {dropped} ({drop_rate:.2f}%)")
        report.append(f"  Average FPS: {fps_avg:.1f} +/- {fps_std:.1f}")

    report.append("\n## 2. Latency Analysis")
    for key, values in data["latency_stats"].items():
        if values:
            arr = np.array(values) if np else values
            avg = float(np.mean(arr)) if np else sum(arr) / len(arr)
            p95 = float(np.percentile(arr, 95)) if np else avg
            p99 = float(np.percentile(arr, 99)) if np else avg
            threshold = {"frame_to_detection": 50, "detection_to_tracking": 50, "total_pipeline": 100}
            status = "PASS" if avg < threshold.get(key, 100) else "FAIL"
            report.append(f"\n{key}: [{status}]")
            report.append(f"  Average: {avg:.2f}ms, P95: {p95:.2f}ms, P99: {p99:.2f}ms")
        else:
            report.append(f"\n{key}: (no data)")

    report.append("\n## 3. API Calls Analysis")
    disappeared_calls = data["api_calls"].get("detect-disappear", 0)
    baseline = 100
    reduction = (baseline - disappeared_calls) / baseline * 100 if baseline else 0
    status = "PASS" if reduction >= 90 or disappeared_calls <= 10 else "FAIL"
    report.append(f"\ndetect-disappear: [{status}]")
    report.append(f"  Total calls: {disappeared_calls}")
    report.append(f"  Reduction from baseline ({baseline}): {reduction:.1f}%")
    for api, count in data["api_calls"].items():
        if api != "detect-disappear":
            report.append(f"  {api}: {count}")

    report.append("\n## 4. Errors")
    if data.get("errors"):
        report.append(f"  {len(data['errors'])} errors:")
        for err in data["errors"][:10]:
            report.append(f"    - {err}")
    else:
        report.append("  No errors")

    report.append("\n## 5. Overall Assessment")
    report.append("  Review PASS/FAIL above. Key: detect-disappear reduction >= 90%, FPS >= 15, drop < 5%.")

    return "\n".join(report)


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_results.py <results_file.json>")
        sys.exit(1)
    results_file = sys.argv[1]
    if not Path(results_file).exists():
        print(f"File not found: {results_file}")
        sys.exit(1)
    report = analyze_results(results_file)
    print(report)
    report_path = Path(results_file).with_suffix("").as_posix() + "_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
