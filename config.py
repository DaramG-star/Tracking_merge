"""
track 프로젝트 설정. 단일 소스: 이 파일만 사용. (config.json 미사용)
"""
from pathlib import Path

# -----------------------------------------------------------------------------
# 경로 (track 루트 기준)
# -----------------------------------------------------------------------------
TRACK_ROOT = Path(__file__).resolve().parent
MODEL_PATH = TRACK_ROOT / "parcel_ver0123.pt"
BASE_DIR = TRACK_ROOT / "0127Tracking"  # 파일 기반 로더용 (선택)

# YOLO 추론 입력 크기 (Ultralytics). 정수 하나면 정사각형: 모델 입력 = imgsz × imgsz (letterbox)
# 캡처 해상도(예: 960×570, 1280×720)는 비율 유지한 채 640×640 안에 맞춰 스케일되고, 남는 부분 패딩.
# 기본 640 → 실제 모델 입력은 640×640.
YOLO_IMGSZ = 640
OUT_DIR = TRACK_ROOT / "output" / "Parcel_Integration_Log_FIFO"
VIDEO_DIR = OUT_DIR / "videos"
CROP_DIR = OUT_DIR / "crops"

# -----------------------------------------------------------------------------
# Ingest (ZMQ / 로컬 USB / stream)
# -----------------------------------------------------------------------------
# Raspberry Pi ZMQ 클라이언트 목록 (id, ip, port, cameras)
RBP_CLIENTS = [
    {"id": "rpi1", "ip": "192.168.1.111", "port": 5555, "cameras": ["usb1", "usb2"]},
    {"id": "rpi2", "ip": "192.168.1.112", "port": 5555, "cameras": ["usb3"]},
]

# 로컬 USB 카메라 (device, width, height, fps, enabled)
LOCAL_USB_CAMERAS = {
    "usb_local_0": {
        "type": "usb",
        "device": "/dev/video0",
        "width": 1280,
        "height": 720,
        "fps": 20,
        "enabled": True,
    }
}

# ZMQ 수신 시 LZ4 압축 해제 사용 여부
STREAM_USE_LZ4 = True

# -----------------------------------------------------------------------------
# Tracking (카메라별 ROI, 이동 시간, 매칭)
# -----------------------------------------------------------------------------
CAM_SETTINGS = {
    "Scanner": {"dist": -2.3},
    "USB_LOCAL": {
        "rotate": 90,
        "roi_y_rate": 0.586,     
        "roi_margin_rate": 0.078,
        "roi_x_center_rate": 0.5,   
        "roi_x_range_rate": 0.8,
        "dist_eps_rate": 0.047,   
        "max_dy_rate": 0.094,     
        "forward_sign": -1,
        "dist": 0.0
    },
    "RPI_USB1": {
        "rotate": 270,
        "roi_y_rate": 0.417,     
        "roi_margin_rate": 0.031,
        "roi_x_center_rate": 0.5,  
        "roi_x_range_rate": 0.8, 
        "dist_eps_rate": 0.036,   
        "max_dy_rate": 0.083,     
        "forward_sign": 1,
        "dist": 5.88
    },
    "RPI_USB2": {
        "rotate": 270,
        "roi_y_rate": 0.167,     
        "roi_margin_rate": 0.031,
        "roi_x_center_rate": 0.5,  
        "roi_x_range_rate": 0.8, 
        "dist_eps_rate": 0.036,
        "max_dy_rate": 0.083,
        "forward_sign": -1,
        "dist": 9.47
    },
    "RPI_USB3": {
        "rotate": 270,
        "roi_y_rate": 0.417,     
        "roi_margin_rate": 0.042,
        "roi_x_center_rate": 0.5,  
        "roi_x_range_rate": 0.8, 
        "eol_y_rate": 0.719,     
        "eol_margin_rate": 0.031,
        "dist_eps_rate": 0.036,
        "max_dy_rate": 0.083,
        "forward_sign": 1,
        "dist": 12.8
    }
}

AVG_TRAVEL = {
    ("Scanner", "USB_LOCAL"): 2.5,
    ("USB_LOCAL", "RPI_USB1"): 18.38,
    ("RPI_USB1", "RPI_USB2"): 9.8,
    ("RPI_USB2", "RPI_USB3"): 9.1,
    ("RPI_USB3", "RPI_USB3_EOL"): 3.5
}

TIME_MARGIN = {
    ("Scanner", "USB_LOCAL"): 3.0,
    ("USB_LOCAL", "RPI_USB1"): 2.5,
    ("RPI_USB1", "RPI_USB2"): 2.0,
    ("RPI_USB2", "RPI_USB3"): 2.0,
    ("RPI_USB3", "RPI_USB3_EOL"): 2.0
}

BELT_SPEED = 0.366  # m/s
ROUTE_TOTAL_DIST = {"XSEA": 9.47, "XSEB": 12.8}

# ZMQ (rpi_id, topic) / local:camera_name -> Tracking cam_id
ZMQ_CAM_MAPPING = {
    "rpi1:usb1": "RPI_USB1",
    "rpi1:usb2": "RPI_USB2",
    "rpi2:usb3": "RPI_USB3",
    "local:usb_local_0": "USB_LOCAL",
}

# 메인 루프에서 처리할 카메라 목록 (Scanner 제외)
TRACKING_CAMS = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]

# -----------------------------------------------------------------------------
# 실시간 모드 / 플래그
# -----------------------------------------------------------------------------
SAVE_VIDEO = False
# False: 스캐너 대기 없이 메인 루프 시작 (카메라/디스플레이 테스트용)
WAIT_FOR_FIRST_SCAN = False

# -----------------------------------------------------------------------------
# 개선: 시간순 처리 / PENDING / 예외 처리 (track_service_improvement_plan)
# -----------------------------------------------------------------------------
# True: TimeOrderedFrameBuffer 사용 (프레임을 timestamp 순으로 처리). False: 기존 FrameAggregator + cam 순서.
USE_TIME_ORDERED_BUFFER = True
# 시간순 버퍼당 카메라별 최대 프레임 수
TIME_ORDERED_BUFFER_MAXLEN = 60
# PENDING 해제 시 추가 대기 시간(초). expected += 이 값 후 now_s >= expected 일 때만 DISAPPEAR.
PENDING_EXTRA_MARGIN_SEC = 0
# 프레임 ts가 현재 시각보다 이 값(초) 이상 과거면 resolve_pending 호출 생략 (stale frame).
STALE_FRAME_SEC = 30
# now_s가 "모든 카메라의 최근 소비 ts 중 최소값"보다 이 값(초) 이상 크면 resolve_pending 생략 (한쪽 cam 지연 대응).
RESOLVE_PENDING_TS_AHEAD_SEC = 5
# 500ms 윈도우 세트: 스트림 시간 구간(초), wall-clock 최대 대기(초)
WINDOW_SET_INTERVAL_SEC = 0.5
WINDOW_MAX_WAIT_WALL_SEC = 0.5

# -----------------------------------------------------------------------------
# API / Scanner (FastAPI on 192.168.1.100)
# -----------------------------------------------------------------------------
API_BASE_URL = "http://192.168.1.100:8000/api"
SCANNER_HOST = "192.168.1.100"
SCANNER_PORT = 8000
