# track/logic/detector.py
import sys
from pathlib import Path
import cv2
from ultralytics import YOLO

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config as track_config

class YOLODetector:
    def __init__(self, model_path=None):
        path = model_path or track_config.MODEL_PATH
        self.model = YOLO(str(path))

    def get_detections(self, img, cam_cfg, cam_id):

        # 1. YOLO 추론 실행 (이미지는 main에서 이미 회전/리사이징됨)
        results = self.model(img, conf=0.25, iou=0.45, verbose=False)[0]

        # 2. ROI 및 가로/세로 영역 설정
        H, W = img.shape[:2]
        roi_y = cam_cfg.get("roi_y", 0)
        roi_margin = cam_cfg.get("roi_margin", 0)
        roi_top, roi_bot = roi_y - roi_margin, roi_y + roi_margin

        # 가로 범위 설정 (main에서 계산해서 넘겨준 값 사용)
        roi_x_min = cam_cfg.get('roi_x_min', 0)
        roi_x_max = cam_cfg.get('roi_x_max', W)

        eol_top = eol_bot = None
        if cam_id == "RPI_USB3":
            eol_y = cam_cfg.get("eol_y", 0)
            eol_margin = cam_cfg.get("eol_margin", 0)
            eol_top, eol_bot = eol_y - eol_margin, eol_y + eol_margin

        filtered_detections = []

        # 3. 결과 필터링
        for b in results.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            # 가로/세로 범위 동시 체크
            in_x_range = roi_x_min <= cx <= roi_x_max
            in_roi = (roi_top < cy < roi_bot) and in_x_range
            in_eol = (eol_top < cy < eol_bot) and in_x_range if eol_top is not None else False

            # 모든 감지 결과를 반환하되, 영역 내 여부(in_roi) 플래그를 정확히 전달
            filtered_detections.append({
                "box": (x1, y1, x2, y2),
                "center": (cx, cy),
                "in_roi": in_roi,
                "in_eol": in_eol,
                "width": (x2 - x1)
            })

        return filtered_detections