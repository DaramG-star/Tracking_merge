# 의존성 및 설정 · 독립 실행

## 1. Python 의존성 (requirements.txt)

```
# ZMQ 수신
pyzmq>=25.0.0
lz4>=4.0.0

# 이미지 처리
opencv-python>=4.5.0
numpy>=1.19.0

# YOLO
ultralytics>=8.0.0

# API · 스캐너
requests>=2.25.0
python-socketio[client]>=5.0.0
```

- **websocket-client**: python-socketio 클라이언트 사용 시 필요할 수 있음. 설치 후 Socket.io 연결 확인.
- **Python**: 3.8+ 권장 (multiprocessing.shared_memory 사용 시 3.8+, 현재 설계는 단일 프로세스라 필수는 아님).

## 2. 설정 파일 (config.py 단일 소스)

- **설정은 `config.py` 하나만 사용**합니다. `config.json`은 사용하지 않습니다.
- **위치**: `track/config.py`. ingest/config_loader는 이 모듈에서 RBP_CLIENTS, LOCAL_USB_CAMERAS, STREAM_USE_LZ4 등을 읽습니다.
- **주요 섹션**:
  - **경로**: TRACK_ROOT, MODEL_PATH, OUT_DIR, VIDEO_DIR, CROP_DIR.
  - **Ingest**: RBP_CLIENTS (RPi 목록 id, ip, port, cameras), LOCAL_USB_CAMERAS (로컬 USB device, width, height, fps, enabled), STREAM_USE_LZ4.
  - **트래킹**: CAM_SETTINGS, AVG_TRAVEL, TIME_MARGIN, ROUTE_TOTAL_DIST, BELT_SPEED, ZMQ_CAM_MAPPING, TRACKING_CAMS.
  - **API/Scanner**: API_BASE_URL, SCANNER_HOST, SCANNER_PORT.

## 3. 독립 실행 체크리스트

- [ ] `track/` 이외 디렉터리(apsr, zMQ, trackingLogic, Tracking)를 `sys.path`에 추가하는 코드가 track 내부에 없음.
- [ ] `import zMQ`, `from zMQ.server`, `from trackingLogic`, `from Tracking` 등 상위 패키지 import 없음.
- [ ] `config.py` 및 기타 모듈의 모든 Path가 `Path(__file__).resolve().parent` (또는 그 하위) 기준.
- [ ] 설정은 config.py 단일 소스; config.json 미사용.
- [ ] YOLO 모델 파일(`parcel_ver0123.pt`)이 track 루트 또는 track/models/에 존재.
- [ ] 다른 PC에 track/ 복사 시: 동일 Python 버전 + `pip install -r requirements.txt` + config.py에서 IP/디바이스만 수정 후 `python main.py` 실행 가능.

## 4. CLI 옵션 vs 필수 동작

- **필수**(항상 활성화): ScannerListener(스캐너 연동), API 호출(api_helper).
- **CLI 옵션**(미지정 시 비활성화):
  - `--csv`: CSV 로그 기록 활성화.
  - `--video`: 비디오 저장(visualizer) 활성화.
  - `--display`: 실시간 창 표시. 미지정 시 해당 기능 비활성화.

## 5. 네트워크/디바이스 요약

- **RPi A**: 192.168.1.111, port 5555, 토픽 usb1, usb2 (cam1, cam2).
- **RPi B**: 192.168.1.112, port 5555, 토픽 usb3 (cam3).
- **로컬 USB**: /dev/video0 (또는 config.py의 LOCAL_USB_CAMERAS device).
- **스캐너/API**: 192.168.1.200:3000 (Socket.io + REST). config 또는 api_helper에서 호스트/포트 설정.

**타임스탬프**: 목표 간격 250ms(멀티캠 트래킹 처리 시간까지 고려). `time.time()` 등 sub-second 단위 사용.

이 문서는 개발 완료 후 “다른 PC에 복사해서 실행” 시 검증용 체크리스트로 사용한다.
