# track 프로젝트 개요

## 1. 현재 상황 정리

### 1.1 기존 코드 분포

| 구분 | 경로 | 역할 | 상태 |
|------|------|------|------|
| YOLO 단일 캠 | `trackingLogic/Tracking_test1/main2.py` | 서버 USB cam0만 사용, YOLO+Tracking 실시간 | ✅ 정상 동작 |
| YOLO 멀티 캠 (파일) | `trackingLogic/Tracking_test1/loader.py` + `main.py` | 저장된 이미지 4개 카메라 시간순 처리 | ✅ 정상 동작 |
| 트래킹 로직 | `trackingLogic/Tracking_test1/` (matcher, detector, visualizer, config, api_helper, scanner_listener, utils) | FIFO 매칭, ROI/EOL, 스캐너 연동 | ✅ 정상 동작 |
| zMQ 서버 | `zMQ/server/main.py` | 4캠 수신(로컬 USB + RPi 2대) 후 화면 표시 | ✅ 정상 동작 |
| 실시간 연동 시도 | `Tracking/` (launcher, frame_receiver_node, tracking_main, shared_memory_manager) | ZMQ↔SharedMemory↔트래킹 멀티프로세스 | ✅ 동작하나 **apsr 루트 및 zMQ 경로 의존** |
| 목표 진입점 | `trackingLogic/Tracking_test1/main.py` | **파일 기반**만 지원, 실시간 4캠 미연동 | ❌ 미구현 |

### 1.2 카메라 구성 (사양)

- **cam0**: 서버 PC USB 직결 (`usb_local_0` → Tracking ID: `USB_LOCAL`)
- **cam1, cam2**: RPi A (192.168.1.111) USB 2대 → zMQ 토픽 `usb1`, `usb2` → Tracking ID: `RPI_USB1`, `RPI_USB2`
- **cam3**: RPi B (192.168.1.112) USB 1대 → zMQ 토픽 `usb3` → Tracking ID: `RPI_USB3`

zMQ는 250ms 간격으로 JPEG+Base64+LZ4 압축 전송, 수신 측에서 Grayscale 디코딩 후 사용 가능(필요 시 BGR 변환).

### 1.3 요구사항 재구성

1. **main.py (track 진입점)**  
   - 서버 USB cam0 + zMQ로 수신하는 cam1, cam2, cam3를 **동시에 실시간 수신**.
2. **각 카메라 프레임**을 기존 **YOLO + Tracking 로직**에 연결해 **Multi-camera tracking** 수행.
3. **기존 분산 코드**를 **track/** 한 디렉터리 안으로 구조화.
4. **설계·개발 계획, 아키텍처, TODO**는 **track/docs/** 에 작성.
5. **독립 실행**: track/ 만 다른 PC에 복사해 실행 가능하도록, **프로젝트 외부 경로(zMQ, trackingLogic, apsr 루트)에 대한 import/참조 제거**.

---

## 2. 목표 정의

- **기능 목표**: 4캠(서버 USB cam0 + zMQ cam1, cam2, cam3) 실시간 수신 → YOLO 탐지 → FIFO 글로벌 매칭 → 스캐너/API 연동까지 **하나의 진입점(main.py)** 으로 수행.
- **구조 목표**: 모든 실행 코드·설정·문서가 **track/** 아래에만 존재하고, 상대 경로는 모두 `track/` 기준으로 통일.
- **운영 목표**: `track/` 디렉터리만 복사 + `pip install -r requirements.txt` (+ 선택적 config 수정)으로 다른 PC에서 실행 가능.

---

## 3. 제약 사항

- **zMQ 프로토콜**: 기존 rbp(zMQ 클라이언트)와 호환 유지. 메시지 형식(토픽, JSON, LZ4, Base64 JPEG) 변경 없음.
- **Tracking 로직**: 기존 `trackingLogic`의 matcher, detector, config(CAM_SETTINGS, AVG_TRAVEL, TIME_MARGIN 등), api_helper, scanner_listener 동작 보존.
- **카메라 ID 매핑**: Tracking 쪽 카메라 ID(`USB_LOCAL`, `RPI_USB1`, `RPI_USB2`, `RPI_USB3`)와 zMQ의 (rpi_id, topic) 및 로컬 USB 이름 매핑을 config로 명시.

---

## 4. 문서 구성 (track/docs/)

| 문서 | 내용 |
|------|------|
| 01_PROJECT_OVERVIEW.md | 현재 문서. 상황 정리, 요구사항, 목표, 제약 |
| 02_ARCHITECTURE.md | 아키텍처, 데이터 흐름, 컴포넌트 역할 |
| 03_DESIGN_SPEC.md | 디렉터리 구조, 모듈 설계, 인터페이스, 설정 스키마 |
| 04_DEVELOPMENT_PLAN.md | 단계별 개발 계획, TODO, 마일스톤 |
| 05_DEPENDENCIES_AND_CONFIG.md | 의존성, 설정 파일, 독립 실행 체크리스트 |

이어서 02~05 문서에서 아키텍처, 상세 설계, 개발 계획, 의존성·설정을 정리한다.
