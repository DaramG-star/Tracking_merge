# track 개발 계획

## 1. 마일스톤

| 단계 | 목표 | 완료 기준 |
|------|------|------------|
| M1 | track 뼈대 및 설정 | track/ 디렉터리 구조 생성, config.py 경로 독립, config.json 스키마 확정 |
| M2 | ingest 모듈 (수신·수집) | config_loader, frame_receiver, usb_camera_worker, frame_aggregator 구현 및 단위 테스트 |
| M3 | logic 모듈 이전 | detector, matcher, visualizer, api_helper, scanner_listener, utils를 track/logic으로 복사·경로 수정 |
| M4 | main.py 통합 | 4캠 수신 → aggregator → 메인 루프 → YOLO/Tracking 연동 동작 |
| M5 | 독립 실행 검증 | apsr/zMQ, trackingLogic 미참조 상태에서 track/ 만으로 실행 및 검증 |
| M6 | 문서·정리 | README, 실행 방법, 05_DEPENDENCIES_AND_CONFIG 체크리스트 반영 |

## 2. 단계별 TODO

### M1: track 뼈대 및 설정

- [ ] `track/` 루트 생성.
- [ ] `track/config.py` 작성: `track_root = Path(__file__).resolve().parent`, `MODEL_PATH = track_root / "parcel_ver0123.pt"`, `OUT_DIR = track_root / "output" / "Parcel_Integration_Log_FIFO"`, `LOG_CSV`, `VIDEO_DIR`, `CROP_DIR`, `CAM_SETTINGS`, `AVG_TRAVEL`, `TIME_MARGIN`, `ROUTE_TOTAL_DIST`, `BELT_SPEED`, `ZMQ_CAM_MAPPING`, `SAVE_VIDEO`, `WAIT_FOR_FIRST_SCAN` 등. (trackingLogic/Tracking_test1/config.py 내용 이전, 경로만 track 기준.)
- [ ] `track/config.json` 작성: rbp_clients, local_usb_cameras, stream. (zMQ/config.json과 동일 스키마.)
- [ ] `track/requirements.txt` 작성: pyzmq, lz4, opencv-python, numpy, ultralytics, requests, python-socketio 등 (05 문서와 맞춤).
- [ ] `track/parcel_ver0123.pt` 복사 또는 심볼릭 링크 (또는 models/ 하위로 배치).

### M2: ingest 모듈

- [ ] `track/ingest/__init__.py` 생성.
- [ ] `track/ingest/config_loader.py`: config.json 로드, get_rbp_clients(), get_stream_config(), get_local_usb_cameras(). 기본 경로는 `Path(__file__).resolve().parent.parent / "config.json"`.
- [ ] `track/ingest/frame_receiver.py`: zMQ/server/frame_receiver.py 로직 이전. LZ4/JSON/Base64 디코딩, `output_bgr` 옵션 추가 시 BGR로 반환. **상위 경로 import 제거.**
- [ ] `track/ingest/usb_camera_worker.py`: zMQ/server/usb_camera_worker.py 이전. **상위 경로 import 제거.**
- [ ] `track/ingest/frame_aggregator.py`: 새로 구현. put(cam_id, frame, ts), get(cam_id), get_all_cam_ids(), 스레드 안전.

### M3: logic 모듈 이전

- [ ] `track/logic/__init__.py` 생성.
- [ ] `track/logic/detector.py`: trackingLogic/Tracking_test1/detector.py 복사. config는 `from ..config import ...` 또는 `from track.config import ...` (패키지 이름에 맞춤).
- [ ] `track/logic/matcher.py`: trackingLogic/Tracking_test1/matcher.py 복사. config 참조를 track.config로.
- [ ] `track/logic/visualizer.py`: trackingLogic/Tracking_test1/visualizer.py 복사. config 참조를 track.config로.
- [ ] `track/logic/api_helper.py`: trackingLogic/Tracking_test1/api_helper.py 복사. BASE_URL 등은 config에서 읽도록 변경 가능.
- [ ] `track/logic/scanner_listener.py`: trackingLogic/Tracking_test1/scanner_listener.py 복사. matcher는 인자로만 받고, config 호스트/포트는 config에서.
- [ ] `track/logic/utils.py`: trackingLogic/Tracking_test1/utils.py 복사. config 참조를 track.config로.

### M4: main.py 통합

- [ ] `track/main.py` 작성:
  - ingest.config_loader로 config.json 로드.
  - ZMQ context, rbp_clients별 SUB 소켓 + FrameReceiver, 콜백에서 ZMQ_CAM_MAPPING으로 cam_id 결정 후 aggregator.put.
  - local_usb_cameras에 대해 USBCameraWorker 시작, 주기적으로 aggregator.put(USB_LOCAL, ...).
  - ScannerListener 시작.
  - 메인 루프: TRACKING_CAMS 순회, aggregator.get(cam_id) → last_processed_ts 스킵 로직 → detector.get_detections → local/global matcher 로직 → resolve_pending → (필수) API·ScannerListener, (옵션) visualizer/CSV. 타임스탬프 sub-second(목표 250ms).
  - CLI 옵션(미지정 시 비활성화): `--csv`, `--video`, `--display`. ScannerListener·API 호출은 필수.
  - SIGINT/SIGTERM 시 receiver.stop(), worker.stop(), scanner_listener.stop(), 리소스 정리.
- [ ] Grayscale 수신 시 BGR 변환 처리 (frame_receiver 또는 main 루프에서).
- [ ] CLI 옵션 파싱: `--csv`, `--video`, `--display`만 옵션(미지정 시 비활성화). ScannerListener·API 호출은 필수(항상 활성화).
- [ ] 실행 테스트: 서버 USB 1대 + RPi 2대 연결 상태에서 4캠 수신 및 트래킹 동작 확인.

### M5: 독립 실행 검증

- [ ] apsr, zMQ, trackingLogic을 sys.path에 넣는 코드가 track 내부에 없음 확인 (grep 검색).
- [ ] 다른 디렉터리(예: /tmp/track_copy)에 track/ 복사 후 `pip install -r requirements.txt` && `python main.py` 실행.
- [ ] config.json만 수정하여 RPi IP/로컬 USB 디바이스 맞추면 동작하는지 확인.

### M6: 문서·정리

- [ ] `track/README.md`: 목적, 요구사항, 설치(pip install -r requirements.txt), 설정(config.json), 실행 방법(python main.py), 카메라 구성 설명.
- [ ] `track/docs/05_DEPENDENCIES_AND_CONFIG.md`: 의존성 목록, 설정 항목 설명, 독립 실행 체크리스트 최종 반영.
- [ ] 필요 시 track/docs/ 에 실행 시퀀스 다이어그램 또는 트러블슈팅 추가.

## 3. 작업 순서 권장

1. M1 → M2 → M3 → M4 → M5 → M6 순으로 진행.
2. M2에서 frame_receiver, usb_camera_worker는 기존 코드 복사 후 import 경로만 제거/수정하여 빠르게 검증.
3. M4에서 메인 루프는 기존 trackingLogic/main.py(파일 기반) + Tracking/tracking_main.py(SharedMemory 기반)의 “한 캠 한 프레임 처리” 블록을 그대로 가져와 aggregator.get() 결과에 적용.

## 4. 리스크 및 대응

- **ZMQ 수신 지연/순서**: 카메라별 독립 버퍼이므로 순서 보장은 하지 않고 “최신만” 처리. 필요 시 타임스탬프 기반 동기화는 후속 개선.
- **스캐너/API 서버 미동작**: WAIT_FOR_FIRST_SCAN=False로 두면 스캔 없이 메인 루프만 돌려서 4캠 수신·YOLO 동작 확인 가능.
- **Grayscale/BGR 불일치**: 수신 단에서 BGR로 맞추거나, 메인 루프에서 한 곳에서만 cvtColor 적용해 일관 유지.

이 계획을 승인 후 위 TODO 순서대로 구현하면 된다.
