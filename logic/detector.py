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
        [수정] 이미지는 main에서 회전/리사이징되어 오므로 여기서는 추론과 필터링만 수행합니다.
        """
        # 1. YOLO 추론 실행 (회전 로직 삭제됨)
        # iou와 conf 수치를 필요시 조정하세요.
        results = self.model(img, conf=0.25, iou=0.45, verbose=False)[0]

        # 2. ROI 및 EOL 영역 설정
        # main에서 이미 비율로 계산된 roi_y, roi_margin 값이 cam_cfg에 들어있습니다.
        roi_y = cam_cfg.get("roi_y", 0)
        roi_margin = cam_cfg.get("roi_margin", 0)
        
        roi_top = roi_y - roi_margin
        roi_bot = roi_y + roi_margin

        eol_top = eol_bot = None
        if cam_id == "RPI_USB3":
            eol_y = cam_cfg.get("eol_y", 0)
            eol_margin = cam_cfg.get("eol_margin", 0)
            eol_top = eol_y - eol_margin
            eol_bot = eol_y + eol_margin

        filtered_detections = []

        # 3. 결과 필터링
        for b in results.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            # 설정된 영역 내에 중심점이 있는지 확인
            in_roi = roi_top < cy < roi_bot
            in_eol = (cam_id == "RPI_USB3" and eol_top < cy < eol_bot) if eol_top is not None else False

            # 시각화를 위해 모든 감지 결과를 반환하되, 영역 내 여부만 표시
            filtered_detections.append({
                "box": (x1, y1, x2, y2),
                "center": (cx, cy),
                "in_roi": in_roi,
                "in_eol": in_eol,
                "width": (x2 - x1)
            })

        return filtered_detections