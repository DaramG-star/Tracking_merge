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
        # 설정을 통해 모델 경로를 로드합니다
        path = model_path or track_config.MODEL_PATH
        self.model = YOLO(str(path))

    def get_detections(self, img, cam_cfg, cam_id):
        """
        이미지를 회전시키고 객체를 탐지한 뒤 ROI/EOL 영역에 있는 것들만 필터링하여 반환합니다.
        """
        # 1. 이미지 회전 처리 (기존 Tracking 로직 이식)
        rotate_val = cam_cfg.get("rotate", 0)
        if rotate_val == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rotate_val == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif rotate_val == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # 2. YOLO 추론 실행
        results = self.model(img, conf=0.25, iou=0.45, verbose=False)[0]

        # 3. ROI 및 EOL 영역 설정
        roi_top = cam_cfg["roi_y"] - cam_cfg["roi_margin"]
        roi_bot = cam_cfg["roi_y"] + cam_cfg["roi_margin"]

        eol_top = eol_bot = None
        if cam_id == "RPI_USB3":
            eol_top = cam_cfg.get("eol_y", 0) - cam_cfg.get("eol_margin", 0)
            eol_bot = cam_cfg.get("eol_y", 0) + cam_cfg.get("eol_margin", 0)

        filtered_detections = []

        # 4. 결과 필터링
        for b in results.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            # 설정된 영역 내에 중심점이 있는지 확인
            in_roi = roi_top < cy < roi_bot
            in_eol = (cam_id == "RPI_USB3" and eol_top < cy < eol_bot) if eol_top is not None else False

            if in_roi or in_eol:
                filtered_detections.append({
                    "box": (x1, y1, x2, y2),
                    "center": (cx, cy),
                    "in_roi": in_roi,
                    "in_eol": in_eol,
                    "width": (x2 - x1)
                })

        return filtered_detections