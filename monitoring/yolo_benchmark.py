#!/usr/bin/env python3
"""
YOLO 처리 속도 벤치마크: imgsz, batch, 모델 정보.
실측 데이터 수집용. 실행: python3 monitoring/yolo_benchmark.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

TRACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRACK_ROOT))
import config as track_config
from ultralytics import YOLO


def get_model_info():
    """모델 파일 및 기본 정보."""
    model_path = track_config.MODEL_PATH
    info = {"model_path": str(model_path), "exists": model_path.exists()}
    if not model_path.exists():
        return info
    try:
        m = YOLO(str(model_path))
        # ultralytics model attributes
        if hasattr(m.model, "names"):
            info["names_len"] = len(m.model.names)
        if hasattr(m, "task"):
            info["task"] = m.task
        if hasattr(m, "names"):
            info["names_sample"] = list(m.names.values())[:3] if m.names else []
        # default predict uses imgsz=640
        info["detector_call"] = "model(img, conf=0.25, iou=0.45, verbose=False)"
        info["imgsz_default"] = 640
    except Exception as e:
        info["load_error"] = str(e)
    return info


def run_inference_benchmark(model_path, imgsz_list=(640, 480, 320), warmup=3, repeat=20):
    """
    단일 이미지 추론: imgsz별 ms 측정.
    이미지 크기: CAM 설정 기준 1280x720 (USB) 사용.
    """
    from logic.detector import YOLODetector

    detector = YOLODetector(model_path)
    h, w = 720, 1280  # USB_LOCAL 해상도
    img = np.zeros((h, w, 3), dtype=np.uint8) + 128
    cfg = track_config.CAM_SETTINGS.get("USB_LOCAL", {"roi_y": 750, "roi_margin": 100})

    results = {}
    for imgsz in imgsz_list:
        times_ms = []
        for i in range(warmup + repeat):
            t0 = time.perf_counter()
            detector.model(img, conf=0.25, iou=0.45, verbose=False, imgsz=imgsz)[0]
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if i >= warmup:
                times_ms.append(elapsed_ms)
        results[f"imgsz_{imgsz}"] = {
            "mean_ms": round(sum(times_ms) / len(times_ms), 2),
            "min_ms": round(min(times_ms), 2),
            "max_ms": round(max(times_ms), 2),
            "n": len(times_ms),
        }
    return results


def run_batch_benchmark(model_path, batch_sizes=(1, 4), imgsz=640, warmup=2, repeat=10):
    """배치 추론: batch=1 vs batch=4 (4장 동시)."""
    from ultralytics import YOLO

    m = YOLO(str(model_path))
    h, w = 720, 1280
    single = np.zeros((h, w, 3), dtype=np.uint8) + 128

    results = {}
    for bs in batch_sizes:
        times_ms = []
        if bs == 1:
            for i in range(warmup + repeat):
                t0 = time.perf_counter()
                m(single, conf=0.25, iou=0.45, verbose=False, imgsz=imgsz)[0]
                times_ms.append((time.perf_counter() - t0) * 1000)
        else:
            batch = [single] * bs
            for i in range(warmup + repeat):
                t0 = time.perf_counter()
                m(batch, conf=0.25, iou=0.45, verbose=False, imgsz=imgsz)
                times_ms.append((time.perf_counter() - t0) * 1000)
        times_ms = times_ms[warmup:]
        mean_ms = sum(times_ms) / len(times_ms)
        results[f"batch_{bs}"] = {
            "mean_ms": round(mean_ms, 2),
            "min_ms": round(min(times_ms), 2),
            "max_ms": round(max(times_ms), 2),
            "per_image_ms": round(mean_ms / bs, 2),
            "n": len(times_ms),
        }
    return results


def main():
    out_path = TRACK_ROOT / "monitoring" / "yolo_benchmark_results.json"
    model_path = track_config.MODEL_PATH

    report = {
        "model_path": str(model_path),
        "model_info": get_model_info(),
        "input_resolution_capture": "1280x720 (USB_LOCAL from config)",
        "detector_imgsz": "default 640 (not passed in detector.py)",
        "tensorrt": "not used (no export to engine)",
        "batch_in_detector": "1 (single image per call)",
    }

    if not model_path.exists():
        report["benchmark_error"] = "model file not found"
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return

    try:
        report["imgsz_benchmark"] = run_inference_benchmark(model_path)
    except Exception as e:
        report["imgsz_benchmark_error"] = str(e)
    try:
        report["batch_benchmark"] = run_batch_benchmark(model_path)
    except Exception as e:
        report["batch_benchmark_error"] = str(e)

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
