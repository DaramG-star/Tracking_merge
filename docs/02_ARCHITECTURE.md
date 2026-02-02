# track 아키텍처

## 1. 시스템 구성도

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                     track (단일 프로세스)                  │
                    │                                                           │
  ┌─────────────────┼─────────────────────────────────────────────────────────┤
  │  프레임 수집     │                                                           │
  │                 │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
  │  [서버 USB]      │   │ USB Capture  │  │ ZMQ Receiver │  │ ZMQ Receiver  │  │
  │  cam0           │   │ (cam0)       │  │ rpi1:usb1,usb2│  │ rpi2:usb3     │  │
  │                 │   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
  │  [RPi A]        │          │                 │                 │          │
  │  cam1, cam2     │          └─────────────────┼─────────────────┘          │
  │  (ZMQ 250ms)    │                            │                            │
  │                 │                            ▼                            │
  │  [RPi B]        │                   ┌─────────────────┐                  │
  │  cam3 (usb3)    │                   │  Frame Aggregator │                  │
  │  (ZMQ 250ms)    │                   │  (cam_id → 최신   │                  │
  │                 │                   │   frame + ts)     │                  │
  └─────────────────┼                   └────────┬─────────┘                  │
                    │                            │                            │
                    │                            ▼                            │
                    │   ┌─────────────────────────────────────────────────┐  │
                    │   │           Main Loop (단일 스레드 또는 순차 폴링)   │  │
                    │   │  - USB_LOCAL, RPI_USB1, RPI_USB2, RPI_USB3 순회  │  │
                    │   │  - 카메라별 최신 프레임 1장씩 가져와서 처리         │  │
                    │   └────────────────────────┬──────────────────────────┘  │
                    │                            │                            │
                    │                            ▼                            │
                    │   ┌─────────────────────────────────────────────────┐  │
                    │   │  TrackingLogic (기존 로직 재사용)                  │  │
                    │   │  - YOLODetector.get_detections(img, cfg, cam_id)  │  │
                    │   │  - FIFOGlobalMatcher (try_match, resolve_pending) │  │
                    │   │  - ScannerListener (Socket.io), api_helper       │  │
                    │   │  - TrackingVisualizer.draw_and_write             │  │
                    │   └─────────────────────────────────────────────────┘  │
                    └─────────────────────────────────────────────────────────┘
```

- **프레임 수집**: 서버 USB 1개 스레드 + RPi당 1개 ZMQ SUB 소켓(스레드 내 수신). 수신 결과는 **카메라 ID별 최신 프레임 버퍼**에만 갱신.
- **Main Loop**: Aggregator에서 카메라별로 “최신 프레임+타임스탬프”를 가져와, 기존 main/loader와 동일한 순서로 **카메라별 1프레임씩** YOLO → Local Tracking → Global Match → Pending Resolve → 시각화/로그/API 호출.

## 2. 데이터 흐름

1. **입력**
   - **cam0 (USB_LOCAL)**: OpenCV `VideoCapture` → 주기적으로 한 프레임 읽어서 버퍼에 저장(타임스탬프: `time.time()` 등 sub-second 단위. 목표 간격 250ms, 멀티캠 트래킹 처리 시간까지 고려).
   - **cam1, cam2, cam3 (RPI_USB1, RPI_USB2, RPI_USB3)**: ZMQ SUB 수신 → LZ4/JSON/Base64 디코딩 → Grayscale 또는 BGR 이미지 + `timestamp` → 해당 cam_id 버퍼에 저장.

2. **버퍼**
   - 구조: `{ cam_id: { "frame": np.ndarray, "timestamp": float } }` (스레드 안전: lock 또는 최신값만 덮어쓰기).
   - Main Loop는 “현재 시점의 최신”만 읽고, 중복 처리 방지를 위해 `last_processed_ts[cam_id]` 등으로 스킵 가능.

3. **처리 (기존 main/loader와 동일)**
   - 카메라별로 한 프레임씩: `detector.get_detections(img, cfg, cam_id)` → Local UID 매칭 → `matcher.try_match(...)` / `resolve_pending(...)` → (필수) API 호출·ScannerListener, (옵션) CSV/비디오/시각화.

4. **출력**
   - **필수**: ScannerListener(스캐너 연동), API 호출(api_helper). **CLI 옵션**(미지정 시 비활성화): `--csv`(CSV 로그), `--video`(비디오 저장), `--display`(실시간 창 표시).

## 3. 실행 모드 (선택)

- **단일 프로세스**: 모든 수집(ZMQ+USB)과 트래킹 루프를 한 프로세스에서 스레드로 구성. track/ 단독 실행 시 기본 권장.
- **멀티 프로세스 (선택)**: 기존 Tracking과 유사하게 “Producer(수신+버퍼 쓰기) / Consumer(트래킹)” 분리 시, **공유 버퍼는 track 내부 모듈**로 구현(SharedMemory 또는 Queue). 이 경우에도 외부 경로 의존 제거, 진입점은 track/main.py 하나.

문서 상 기본 권장은 **단일 프로세스 + 스레드 수집 + 메인 루프에서 버퍼 읽기**로, 구현 단순성과 이식성을 우선한다.

## 4. 카메라 ID 및 매핑

| 소스 | zMQ/설정 식별 | track 내부 cam_id |
|------|----------------|--------------------|
| 서버 USB | local_usb_cameras.usb_local_0 | USB_LOCAL |
| RPi A usb1 | rpi1, topic "usb1" | RPI_USB1 |
| RPi A usb2 | rpi1, topic "usb2" | RPI_USB2 |
| RPi B usb3 | rpi2, topic "usb3" | RPI_USB3 |

매핑은 `track/config.py` 또는 `track/config/cameras.json` 등에서 `ZMQ_CAM_MAPPING` 형태로 유지하고, zMQ 설정(rbp_clients, local_usb_cameras)은 track 전용 config에서 읽거나, 단일 `config.json` 한 파일로 통합해 경로를 track 기준으로만 참조한다.

## 5. 기존 코드와의 관계

- **trackingLogic/Tracking_test1/**: matcher, detector, visualizer, config(CAM_SETTINGS, AVG_TRAVEL 등), api_helper, scanner_listener, utils → **로직만 track/ 내부로 복사·조정** (경로를 track 기준 상대 경로로 변경).
- **zMQ/server**: FrameReceiver, ConfigLoader, USBCameraWorker → **동일 프로토콜·동일 동작을 하는 모듈을 track/ 내부에 구현** (또는 서브패키지로 복사). `import zMQ` 또는 `sys.path.insert(apsr)` 같은 **상위 디렉터리 참조 제거**.
- **Tracking/ (launcher, frame_receiver_node, tracking_main, shared_memory_manager)**: 설계 참고용. track은 “단일 프로세스 + 내부 버퍼”를 우선으로 하되, 필요 시 SharedMemory/Queue 설계를 track 전용으로 다시 정의.

이 아키텍처를 기준으로 03_DESIGN_SPEC에서 디렉터리·모듈·인터페이스를 구체화한다.
