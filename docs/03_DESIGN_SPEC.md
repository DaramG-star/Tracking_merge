# track 상세 설계

## 1. 디렉터리 구조 (목표)

```
track/
├── main.py                 # 진입점: 4캠 수신 + 메인 루프 + YOLO/Tracking
├── config.py               # 경로·CAM_SETTINGS·AVG_TRAVEL·ZMQ_CAM_MAPPING 등 (모두 track 기준 경로)
├── config.json             # ZMQ/로컬 USB 설정 (또는 config/cameras.json 등으로 분리 가능)
├── requirements.txt        # pyzmq, lz4, opencv, numpy, ultralytics, requests, python-socketio 등
├── parcel_ver0123.pt       # YOLO 모델 (또는 models/ 하위)
│
├── ingest/                 # 프레임 수집 전담 (외부 경로 의존 없음)
│   ├── __init__.py
│   ├── config_loader.py    # config.json 로드 (rbp_clients, local_usb_cameras, stream)
│   ├── frame_receiver.py   # ZMQ SUB 수신, LZ4/JSON/Base64 디코딩 (Grayscale → BGR 옵션)
│   ├── usb_camera_worker.py # 로컬 USB OpenCV 캡처 (기존 zMQ/server 와 동일 로직)
│   └── frame_aggregator.py # 카메라별 최신 (frame, timestamp) 버퍼 + 스레드 안전 접근
│
├── logic/                  # Tracking 로직 (기존 trackingLogic 복사·정리)
│   ├── __init__.py
│   ├── detector.py        # YOLODetector (ultralytics YOLO, ROI/EOL 필터)
│   ├── matcher.py         # FIFOGlobalMatcher (q_scan, q01~q3e, try_match, resolve_pending)
│   ├── visualizer.py      # TrackingVisualizer (draw_and_write, SAVE_VIDEO)
│   ├── api_helper.py      # API 호출 (scan, update_position, pickup, missing, eol)
│   ├── scanner_listener.py # Socket.io 클라이언트 → matcher.add_scanner_data
│   └── utils.py           # extract_ts, ts_to_seconds, VideoManager 등
│
├── docs/                   # 설계·계획 문서
│   ├── 01_PROJECT_OVERVIEW.md
│   ├── 02_ARCHITECTURE.md
│   ├── 03_DESIGN_SPEC.md
│   ├── 04_DEVELOPMENT_PLAN.md
│   └── 05_DEPENDENCIES_AND_CONFIG.md
│
├── output/                 # 실행 시 생성 (로그, 비디오, crops 등)
│   └── (OUT_DIR, LOG_CSV, VIDEO_DIR, CROP_DIR은 config에서 output/ 하위로 지정)
│
└── (선택) models/          # 모델 파일만 둘 경우
    └── parcel_ver0123.pt
```

- **진입점**: `track/main.py`만 실행. 상위 디렉터리(apsr, zMQ, trackingLogic)를 sys.path에 넣지 않음.
- **설정**: `config.py`의 `MODEL_PATH`, `OUT_DIR`, `BASE_DIR` 등은 모두 `Path(__file__).resolve().parent` 기준 상대 경로.
- **config.json**: `track/config.json` 한 파일에 `rbp_clients`, `local_usb_cameras`, `stream` 포함. 필요 시 `logic`용 파라미터(CAM_SETTINGS 등)는 config.py에 하드코딩하거나 config.json에 확장.

## 2. 모듈 역할 및 인터페이스

### 2.1 ingest.config_loader

- **역할**: track/config.json 로드, `get_rbp_clients()`, `get_stream_config()`, `get_local_usb_cameras()` 제공.
- **인터페이스**: 기존 zMQ/server/config_loader와 동일한 반환 형태. 경로는 `config_path` 인자 또는 기본값 `track/config.json` (Path(__file__).parent 기준).

### 2.2 ingest.frame_receiver

- **역할**: ZMQ SUB 소켓으로 multipart [topic, body] 수신 → LZ4 decompress → JSON → Base64 decode → cv2.imdecode (Grayscale 또는 BGR).
- **인터페이스**:
  - `__init__(zmq_socket, use_lz4=True, output_bgr=False)`
  - `set_frame_callback(callback: (camera_name: str, frame: np.ndarray, timestamp: float) -> None)`
  - `start()` / `stop()` (내부 스레드 수신 루프).
- **의존**: zmq, lz4, cv2, numpy, json, base64. **경로/상위 패키지 의존 없음.**

### 2.3 ingest.usb_camera_worker

- **역할**: 로컬 USB 카메라 OpenCV VideoCapture, 주기적으로 최신 프레임 갱신.
- **인터페이스**: 기존 zMQ/server/usb_camera_worker와 동일 (`get_latest_frame()`, `latest_timestamp`, `start()`, `stop()`).
- **의존**: cv2, numpy, threading. **경로 의존 없음.**

### 2.4 ingest.frame_aggregator

- **역할**: 카메라 ID별 최신 (frame, timestamp) 보관. ZMQ 콜백과 USB 워커가 여기에 write; 메인 루프가 read.
- **인터페이스**:
  - `put(cam_id: str, frame: np.ndarray, timestamp: float) -> None`
  - `get(cam_id: str) -> Optional[Tuple[np.ndarray, float]]`  # 스냅샷 반환 (복사 권장)
  - `get_all_cam_ids() -> List[str]`
- **스레드 안전**: threading.Lock 또는 최신값만 덮어쓰기(쓰기 1회만 읽기 시 복사).

### 2.5 logic (detector, matcher, visualizer, api_helper, scanner_listener, utils)

- **역할**: 기존 trackingLogic/Tracking_test1과 동일. 경로만 track 기준으로 변경.
- **config.py**: `MODEL_PATH = (track_root / "parcel_ver0123.pt")`, `OUT_DIR = track_root / "output" / "Parcel_Integration_Log_FIFO"` 등. `CAM_SETTINGS`, `AVG_TRAVEL`, `TIME_MARGIN`, `ROUTE_TOTAL_DIST`, `BELT_SPEED`, `ZMQ_CAM_MAPPING` 등 유지.
- **ZMQ_CAM_MAPPING**: `{"rpi1:usb1": "RPI_USB1", "rpi1:usb2": "RPI_USB2", "rpi2:usb3": "RPI_USB3", "local:usb_local_0": "USB_LOCAL"}` 형태를 config.py 또는 config.json에서 로드.

### 2.6 main.py (진입점)

- **역할**:
  1. config 로드 (ingest.config_loader + logic.config).
  2. Frame aggregator 생성.
  3. ZMQ: rbp_clients별 소켓 + FrameReceiver, 콜백에서 `(rpi_id, topic) → cam_id` 매핑 후 aggregator.put(cam_id, frame, ts).
  4. USB: local_usb_cameras에 대해 USBCameraWorker 시작, 주기적으로 aggregator.put(USB_LOCAL, frame, ts).
  5. ScannerListener 시작 (기존과 동일).
  6. 메인 루프: `while True`에서 cam_id 순회, aggregator.get(cam_id) → 있으면 한 프레임씩 기존 main/loader와 동일한 파이프라인 (detector → matcher → resolve_pending → [필수] API·ScannerListener, [옵션] CSV/비디오/시각화). 중복 처리 방지용 last_processed_ts 사용. 타임스탬프는 sub-second 단위(목표 250ms 간격, 멀티캠 처리 시간 고려).
  7. **CLI 옵션**(미지정 시 비활성화): `--csv`(CSV 로그), `--video`(비디오 저장), `--display`(실시간 창). ScannerListener·API 호출은 **필수**(항상 활성화).
  8. 시그널 처리 및 정리 (receiver.stop(), worker.stop(), scanner_listener.stop()).

- **의존**: track 내부 패키지만 import (`ingest.*`, `logic.*`, `config`).

## 3. 설정 스키마 (config.json)

- **server**: (선택) ip/port는 수신 측에서는 불필요. 문서화용.
- **rbp_clients**: `[{ "id": "rpi1", "ip": "192.168.1.111", "port": 5555, "cameras": ["usb1", "usb2"] }, ...]`
- **local_usb_cameras**: `{ "usb_local_0": { "device": "/dev/video0", "width": 1280, "height": 720, "fps": 20, "enabled": true } }`
- **stream**: `{ "send_interval_ms": 250, "jpeg_quality": 80, "use_lz4": true }`

CAM_SETTINGS 등 트래킹 파라미터는 config.py에 두고, 필요 시 config.json에 "tracking" 섹션을 두어 오버라이드할 수 있게 확장 가능.

## 4. Grayscale vs BGR

- zMQ 수신 프레임: 현재 zMQ/server/frame_receiver는 Grayscale로 디코딩. YOLO와 시각화는 BGR을 쓰므로, track의 frame_receiver에서는 `output_bgr=True` 옵션으로 cv2.imdecode 후 BGR 변환하거나, 메인 루프에서 `cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)` 적용 (기존 Tracking/tracking_main과 동일).

## 5. CLI 옵션 vs 필수 동작

- **필수**(항상 활성화): ScannerListener(스캐너 연동), API 호출(api_helper).
- **CLI 옵션**(미지정 시 비활성화):
  - `--csv`: CSV 로그 기록 (LOG_CSV 등) 활성화.
  - `--video`: 비디오 저장 (visualizer, SAVE_VIDEO) 활성화.
  - `--display`: 실시간 창 표시. 미지정 시 해당 기능은 비활성화.

## 6. Best practice 반영

- **단일 진입점**: `python main.py` 또는 `python -m track` (패키지 구조 시) 만으로 전체 실행.
- **상대 경로**: 모든 파일·디렉터리 참조는 `Path(__file__).resolve().parent` 기준.
- **의존성**: requirements.txt에만 명시, 설치 시 `pip install -r requirements.txt`로 충당.
- **설정**: 한 곳(config.json + config.py)에서 관리, 기본값은 config.py에 두고 config.json으로 네트워크/디바이스만 오버라이드.
- **로깅**: logging 모듈 사용, 레벨/포맷은 config 또는 환경 변수로 조정 가능하게.

이 설계를 기준으로 04_DEVELOPMENT_PLAN에서 단계별 작업 목록을 정리한다.
