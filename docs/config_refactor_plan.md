# 설정(config) 통합 리팩터링: 최종 계획

**상태**: 분석·설계 완료 후 **리팩터링 수행 완료**  
**대상**: `track/config.json`(제거됨), `track/config.py`(단일 소스)  
**목표**: **설정 파일을 단 하나만 사용** — `config.py` 단일 소스로 통합, `config.json` 완전 제거.

> **승인 전 유의**: 이 문서를 검토하여 승인하기 **전까지** 실제 코드나 설정 파일(`config.py`, `config.json`)을 변경하지 않으며, PR/커밋은 “분석·통합 계획 문서 추가/갱신” 수준으로만 둡니다. 승인 후 “계획에 따라 config.py 단일 소스로 통합하고 config.json을 제거하는 리팩터링을 수행하라”는 지시가 있을 때만 실제 리팩터링을 진행합니다.

---

## 1. 현 구조의 문제 (요약)

- JSON은 주로 **ingest 관련 설정**(ZMQ 클라이언트, 로컬 USB, stream 옵션)에 쓰이고,
- `config.py`는 **트래킹 / API / 경로 / 플래그** 등에 쓰이다 보니
  - **역할이 반쯤 나뉘고**, “어디가 단일 소스인가?”가 불명확하다.
- 신규 설정을 추가할 때 **JSON에 넣을지, config.py에 넣을지** 애매하다.
- 환경별 설정(개발/스테이징/운영)이 **두 파일에 흩어져** 있어, 어떤 환경에서 어떤 값이 적용되는지 직관적이지 않다.

---

## 2. 코드 관점에서의 사용처 분석

### 2.1 config.json이 사용되는 방식

| 구분 | 내용 |
|------|------|
| **로딩** | `ingest/config_loader.py`의 `ConfigLoader`가 `track/config.json` 경로로 파일을 읽음. |
| **호출** | `main.py`: `ConfigLoader()` 생성 → `load()` → `get_stream_config()`, `get_rbp_clients()`, `get_local_usb_cameras()` 호출. |
| **get_rbp_clients()** | `config["rbp_clients"]`가 있으면 그대로 반환; 없으면 `config["rbp"]`, `config["server"]`, `config["cameras"]`로 폴백하여 1개 항목 리스트 생성. |
| **get_stream_config()** | `config.get("stream", {})` 반환. |
| **get_local_usb_cameras()** | `config.get("local_usb_cameras", {})` 반환. |
| **하위 사용** | `main.py`: ZMQ 소켓용 `client["id"]`, `client["ip"]`, `client["port"]`, `client["cameras"]`; stream용 `stream_cfg.get("use_lz4", True)`; USB용 `loader.get_local_usb_cameras()` → `usb_camera_worker`에 전달. |
| **usb_camera_worker** | 전달받은 `camera_config` dict에서 `device`, `width`, `height`, `fps`, `enabled` 사용. |

즉, **config.json에서 실제로 쓰이는 키**는 `rbp_clients`, `stream`(그중 `use_lz4`), `local_usb_cameras`이며, `server`, `rbp`, `cameras`는 `rbp_clients`가 없을 때만 폴백에서 사용된다. `server.ip`, `stream.send_interval_ms`, `stream.jpeg_quality`, `logging`은 **어디에서도 읽지 않음**.

### 2.2 config.py가 사용되는 방식

| 구분 | 내용 |
|------|------|
| **로딩** | Python `import config` (또는 `from track import config`)로 모듈 로드. |
| **참조처** | `main.py`, `logic/detector.py`, `logic/matcher.py`, `logic/visualizer.py`, `logic/api_helper.py`, `logic/utils.py`, `logic/scanner_listener.py`. |
| **사용 심볼** | TRACK_ROOT(내부만), MODEL_PATH, BASE_DIR(CAM_SETTINGS 정의용), OUT_DIR, VIDEO_DIR, CROP_DIR, CAM_SETTINGS, AVG_TRAVEL, TIME_MARGIN, BELT_SPEED, ROUTE_TOTAL_DIST, SAVE_VIDEO, WAIT_FOR_FIRST_SCAN, TRACKING_CAMS, ZMQ_CAM_MAPPING, API_BASE_URL, SCANNER_HOST, SCANNER_PORT. |
| **미참조** | LOG_CSV, REALTIME_FRAME_MAX_SHAPE, CAM_SETTINGS[*].path(코드에서 `cam_cfg["path"]` 참조 없음). |

---

## 3. 유지 / 삭제 / 이전 항목 표

아래 표에서 **조치**는 다음 의미이다.

- **유지**: `config.py`에 그대로 두고 사용처 유지.
- **삭제**: 더 이상 사용하지 않으므로 제거 대상.
- **이전**: 현재 `config.json`에만 있는 유효 설정을 `config.py`로 옮긴다.

### 3.1 config.json 항목

| config.json 경로 | 조치 | 사용처(모듈/함수) | 비고(위험요소, 확인 필요) |
|------------------|------|-------------------|---------------------------|
| server | 삭제 | get_rbp_clients() 폴백에서 server.get("port")만 사용. 현재는 rbp_clients가 있어 미사용 | JSON 제거 시 폴백 로직 자체를 제거하므로 server 블록 불필요 |
| server.ip | 삭제 | 미사용 | 문서화용으로만 존재 가능성; 다른 레포 참조 여부만 확인 |
| server.port | 삭제 | 폴백 시에만 사용 | 이전 시 RBP_CLIENTS에 port 포함되므로 별도 server 불필요 |
| rbp | 삭제 | 폴백에서 rbp.get("ip") | RBP_CLIENTS 이전 후 폴백 제거 |
| rbp.ip | 삭제 | 폴백에서만 | 동일 |
| rbp_clients | **이전** | main.py: ZMQ 소켓 연결(id, ip, port, cameras) | **현재 config.json 값**을 config.py의 기본값으로 이전. 운영에서 쓰는 실제 값일 가능성 높음 |
| cameras | 삭제 | get_rbp_clients() 폴백에서만 | rbp_clients 이전 후 폴백 제거 |
| stream | 이전(일부) | main.py: stream_cfg.get("use_lz4", True) | use_lz4만 사용 → config.py에 STREAM_USE_LZ4 등으로 이전 |
| stream.send_interval_ms | 삭제 | 미사용(track 수신 측) | 송신 측(zMQ 등)에서만 사용 가능; track에서는 제거 |
| stream.jpeg_quality | 삭제 | 미사용 | 동일 |
| stream.use_lz4 | **이전** | main.py | config.py에 반영(예: STREAM_USE_LZ4 = True) |
| local_usb_cameras | **이전** | main.py, ingest/usb_camera_worker.py | **현재 config.json 값**을 config.py 기본값으로 이전 |
| logging | 삭제 | 미사용 | ConfigLoader 등에서 참조 없음 |

### 3.2 config.py 항목

| config.py 심볼 | 조치 | 사용처(모듈/함수) | 비고(위험요소, 확인 필요) |
|----------------|------|-------------------|---------------------------|
| TRACK_ROOT | 유지 | config.py 내부(MODEL_PATH 등 경로 계산) | 그대로 유지 |
| MODEL_PATH | 유지 | main.py, logic/detector.py | 유지 |
| BASE_DIR | 유지 | CAM_SETTINGS 내 path 계산용 | path 키 삭제 시 BASE_DIR은 유지해도 됨(다른 용도 대비) |
| OUT_DIR | 유지 | main.py | 유지 |
| VIDEO_DIR | 유지 | main.py, logic/visualizer.py, logic/utils.py | 유지 |
| CROP_DIR | 유지 | main.py: mkdir만 | 실제 파일 쓰기 없음이지만 디렉터리 생성용으로 유지 |
| LOG_CSV | **삭제** | 미사용(main은 OUT_DIR / "tracking_logs_live.csv" 사용) | 제거 |
| CAM_SETTINGS | 유지 | main.py, logic/detector.py | 유지 |
| CAM_SETTINGS[*].path | **삭제** | 미사용(코드에서 cam_cfg["path"] 참조 없음) | 제거 또는 주석 처리. 파일 기반 로더 유산 |
| AVG_TRAVEL | 유지 | logic/matcher.py | 유지 |
| TIME_MARGIN | 유지 | logic/matcher.py | 유지 |
| BELT_SPEED | 유지 | main.py | 유지 |
| ROUTE_TOTAL_DIST | 유지 | logic/matcher.py, main.py | 유지 |
| SAVE_VIDEO | 유지 | logic/visualizer.py, logic/utils.py | 유지 |
| WAIT_FOR_FIRST_SCAN | 유지 | main.py | 유지 |
| TRACKING_CAMS | 유지 | main.py | 유지 |
| ZMQ_CAM_MAPPING | 유지 | main.py | 유지 |
| REALTIME_FRAME_MAX_SHAPE | **삭제** | 미사용 | 제거 |
| API_BASE_URL | 유지 | logic/api_helper.py | 유지. API/Scanner는 config.py 기준 |
| SCANNER_HOST | 유지 | main.py, logic/scanner_listener.py | 유지 |
| SCANNER_PORT | 유지 | main.py, logic/scanner_listener.py | 유지 |

---

## 4. 중복 설정 처리 기준 및 최종 채택 값

- **API / Scanner / Tracking 관련**  
  - **기준**: `config.py`  
  - 코드가 이미 전부 config.py만 참조하므로, **현재 config.py 값**을 그대로 최종 값으로 사용.

- **ZMQ / Raspberry Pi 관련**  
  - **기준**: 현재 **config.json에 있는 값**이 실제 운영 값일 가능성이 높다고 보고, 그 값을 **config.py의 기본값으로 이전**한다.
  - 구체적으로:
    - **rbp_clients**  
      - config.json 현재 값(예: rpi1 192.168.1.111:5555 usb1/usb2, rpi2 192.168.1.112:5555 usb3)을 config.py에 `RBP_CLIENTS`(또는 동일 구조의 상수)로 정의.
    - **stream.use_lz4**  
      - config.json의 `true` → config.py에 `STREAM_USE_LZ4 = True` 등으로 반영.
    - **local_usb_cameras**  
      - config.json 현재 값(예: usb_local_0, device/width/height/fps/enabled)을 config.py에 `LOCAL_USB_CAMERAS`로 정의.

- **중복으로 보였던 항목**  
  - config.json `server.ip`(192.168.1.200)와 config.py `SCANNER_HOST`(192.168.1.200): 코드는 Scanner에 대해 config.py만 사용하므로 **config.py 기준 유지**. server.ip는 **삭제**하고, JSON 제거 시 함께 사라짐.

---

## 5. 최종 방향(결정 사항)

- **설정 파일은 항상 하나만 사용**한다.
  - **현 단계**: **Canonical 설정 소스 = `config.py` 하나**로 통합.
  - **config.json**은 **사용하지 않고 완전히 제거**한다.
- 나중에 환경별 설정(JSON 장점)을 제대로 쓰고 싶을 때는:
  - 그때 **config.py 내용을 전부 config.json 계열로 이전**하고,
  - **“단일 설정 소스 = JSON”** 구조로 다시 전환한다.
- 즉, “지금은 config.py 단일 소스, 향후 필요 시 JSON 단일 소스로 완전 전환”이므로, **어떤 시점이든 설정 파일은 하나만 사용**한다.

---

## 6. 단일 소스 구조 설계

- **최종 구조**
  - **설정은 `config.py` 하나만 읽어서** 모든 설정을 공급한다.
  - `config.json`은 읽지 않고, 파일 자체를 제거한다.

- **환경 변수(선택)**
  - 필요 시 `config.py` 내부에서 `os.environ.get("ENV")`, `os.environ.get("STAGE")` 등을 읽어 일부 값만 분기할 수 있게 둔다. (이번 리팩터링에서 반드시 구현할 필요는 없고, 확장 포인트로만 명시해도 됨.)

- **config.py 내부 정리(네임스페이스/섹션)**
  - 전체 서비스 설정을 한눈에 보이도록, 아래처럼 **섹션별로 그룹**을 나누어 정리한다. (실제 심볼명은 구현 시 일관되게 조정 가능.)
  - 예시 구조:
    - **경로**: TRACK_ROOT, MODEL_PATH, BASE_DIR, OUT_DIR, VIDEO_DIR, CROP_DIR
    - **Ingest(ZMQ/로컬 USB/stream)**: RBP_CLIENTS, LOCAL_USB_CAMERAS, STREAM_USE_LZ4
    - **Tracking**: CAM_SETTINGS, AVG_TRAVEL, TIME_MARGIN, BELT_SPEED, ROUTE_TOTAL_DIST, TRACKING_CAMS, ZMQ_CAM_MAPPING
    - **실시간/플래그**: SAVE_VIDEO, WAIT_FOR_FIRST_SCAN
    - **API/Scanner**: API_BASE_URL, SCANNER_HOST, SCANNER_PORT

- **로딩 계층(향후 JSON 전환 대비)**
  - 지금은 `config.py`만 사용하지만, **나중에 config.json으로 전환하기 쉽게** 하기 위해:
  - **중앙 설정 로더** 또는 **`load_settings()` 같은 함수**를 둘 수 있다.
  - 현재 단계에서는 `load_settings()`는 “config 모듈을 import하고 필요한 값만 노출하는 래퍼” 수준으로 두고, **실제 소스는 config.py만** 사용한다.
  - 추후 JSON 전환 시, 같은 `load_settings()` 안에서 “config.json 존재 시 해당 파일을 읽어 덮어쓰기” 또는 “JSON만 읽기”로 바꾸면 된다.

---

## 7. 6단계 마이그레이션 플랜

### 1단계: 문서 확정

- **내용**
  - 이 문서(`docs/config_refactor_plan.md`)에 현 구조 분석, 유지/삭제/이전 표, 최종 구조 및 로딩 전략, 위험 요소·롤백 전략을 반영하고 **최종안으로 확정**한다.
- **산출물**
  - 갱신된 `docs/config_refactor_plan.md`.
- **위험 요소·롤백**
  - 리팩터링 중 ingest/트래킹 동작이 깨질 수 있음 → 단계별로 테스트하고, 필요 시 config.json과 ConfigLoader를 임시로 되돌릴 수 있도록 브랜치/커밋 단위로 진행.

### 2단계: 미사용 설정 제거

- **대상**
  - **config.py**: LOG_CSV, REALTIME_FRAME_MAX_SHAPE 제거. CAM_SETTINGS 각 캠에서 `"path"` 키 제거(또는 주석/미사용 표시).
  - **config.json**: 이 단계에서는 아직 파일을 건드리지 않고, “논리적으로 제거 대상”으로만 표시. 실제 삭제는 5단계에서 수행.
- **영향**
  - LOG_CSV, REALTIME_FRAME_MAX_SHAPE를 참조하는 코드가 없으므로 제거만 하면 됨. CAM_SETTINGS["path"]를 읽는 코드가 없으므로 path 제거 시 동작 변화 없음.
- **확인**
  - grep으로 LOG_CSV, REALTIME_FRAME_MAX_SHAPE, `["path"]`(CAM_SETTINGS 관련) 참조가 없음을 재확인.

### 3단계: config.py에 ingest 관련 기본값 추가

- **내용**
  - 기존 config.json에만 있던 **유효 설정**을 config.py로 옮겨, **config.py만 읽어도 동작**하도록 한다.
  - 추가할 항목(현재 config.json 값 기준):
    - **RBP_CLIENTS**: rpi1(192.168.1.111, 5555, usb1, usb2), rpi2(192.168.1.112, 5555, usb3) 구조의 리스트.
    - **LOCAL_USB_CAMERAS**: usb_local_0 등 현재 local_usb_cameras와 동일한 구조의 dict.
    - **STREAM_USE_LZ4**: True(현재 stream.use_lz4 값).
  - 타입·기본값·주석을 명확히 적어, 개발자가 한눈에 볼 수 있게 한다.
- **영향**
  - main.py 등에서는 아직 ConfigLoader를 쓰는 상태. 4단계에서 ConfigLoader를 “config.py에서 읽기”로 바꾼 뒤, 이 값들을 사용하도록 수정.

### 4단계: 로딩 계층 통일

- **내용**
  - 애플리케이션 전역에서 **설정을 읽는 방식**을 통일한다.
  - **중앙 설정 로더** 또는 **Settings 객체 / load_settings()**를 설계한다.
  - 현재는 **config.py만** 사용하되, 추후 config.json을 끼워 넣을 수 있는 계층으로 만든다.
  - 구체 예:
    - `ingest/config_loader.py`(또는 공통 `config_loader.py`)를 수정하여, **config.json을 읽지 않고** config 모듈에서 RBP_CLIENTS, LOCAL_USB_CAMERAS, STREAM_USE_LZ4 등을 가져오는 함수(예: `get_rbp_clients()`, `get_local_usb_cameras()`, `get_stream_use_lz4()`)를 제공.
    - 또는 config 모듈에 `load_settings()`를 두고, “현재는 config.py 심볼만 반환, 추후에는 JSON merge 로직 추가” 가능하도록 인터페이스만 고정.
  - main.py 및 기타 호출부는 **ConfigLoader(config.json)** 대신 **config 모듈(또는 load_settings())**만 참조하도록 바꾼다.
- **영향**
  - main.py에서 ConfigLoader 인스턴스 생성·load()·get_*() 호출을, config에서 직접 읽는 방식(또는 load_settings() 경유)으로 변경. ingest/config_loader.py의 역할은 “config에서 ingest 관련 값 반환”으로 축소되거나, config를 re-export하는 형태로 조정.

### 5단계: JSON 축소 및 제거

- **내용**
  - 코드가 **모두 config.py(또는 load_settings())를 단일 소스**로 참조하게 만든다.
  - **config.json에 대한 모든 참조**를 제거한다(ConfigLoader의 config.json 로딩, config.json 경로, 테스트에서의 임시 config.json 등).
  - **config.json 파일 자체를 제거**한다. (필요 시 백업은 docs나 커밋 히스토리로 남긴다.)
- **영향**
  - ingest/config_loader.py에서 JSON 로딩·폴백 로직 제거. 테스트는 config.py 기반 또는 메모리 dict 기반으로 수정.

### 6단계: 검증

- **기능 검증**
  - ingest(ZMQ 수신, 로컬 USB, stream use_lz4), tracking(CAM_SETTINGS, matcher, detector), API/Scanner(SCANNER_HOST, SCANNER_PORT, API_BASE_URL) 등 **기존 기능이 정상 동작**하는지 확인.
- **설정 값 검증**
  - RBP_CLIENTS(IP/port/cameras), LOCAL_USB_CAMERAS(device/width/height/fps/enabled), STREAM_USE_LZ4 등 **변경 가능한 설정**이 의도한 값으로 적용되는지 체크리스트로 확인.
- **테스트**
  - **유닛 테스트**: config_loader 관련 테스트가 config.py 기반(또는 인메모리 설정)으로 통과하는지.
  - **통합/수동 테스트**: `python main.py`(및 --csv, --video, --display) 실행, ZMQ/USB/Scanner 연동이 기존과 동일하게 동작하는지.
- **문서**
  - README, docs/05_DEPENDENCIES_AND_CONFIG.md 등에 **“설정은 config.py 단일 소스, config.json은 사용하지 않음”**을 명시하고, 필요한 경우 환경 변수 오버레이 안내를 추가한다.

---

## 8. 위험 요소 및 롤백 전략

| 위험 | 대응 |
|------|------|
| ingest 쪽이 config.py만 읽도록 바꾼 후 ZMQ/USB 동작 이상 | 3·4단계 후 즉시 수동 테스트. 문제 시 ConfigLoader와 config.json 로딩을 임시로 복구할 수 있도록 커밋을 나누어 진행. |
| 다른 레포에서 track/config.json을 참조 | config.json 제거 전에 다른 레포 사용처 확인. 필요 시 해당 레포에서만 config.json 유지하거나, track은 config.py만 제공하도록 문서화. |
| 환경별로 다른 값(예: RBP IP) 사용 중 | 현재 config.json이 운영 값이라고 가정하고 config.py 기본값으로 이전. 이후 환경별 차이는 환경 변수 또는 추후 “JSON 단일 소스” 전환 시 처리. |

---

## 9. 문서 요약 및 승인 전 유의사항

- **현 구조**
  - config.json: ingest(ZMQ, 로컬 USB, stream) 위주. 실제 사용 키는 rbp_clients, stream.use_lz4, local_usb_cameras. server/rbp/cameras는 폴백용. server.ip, send_interval_ms, jpeg_quality, logging은 미사용.
  - config.py: 경로, 트래킹, API/Scanner, 플래그 등 전부. LOG_CSV, REALTIME_FRAME_MAX_SHAPE, CAM_SETTINGS[*].path는 미사용.

- **통합 방향(확정)**
  - **설정 파일 단일화**: 당 단계에서는 **config.py만** 사용하고, **config.json은 완전 제거**.
  - 향후 환경별 설정이 필요해지면, 그때 config.py 내용을 전부 config.json 계열로 이전하여 “단일 설정 소스 = JSON”으로 전환.

- **실제 코드/설정 변경**
  - 위 계획은 **문서로만 확정**하며, **승인 전까지는 config.py, config.json, ConfigLoader, main.py 등 어떤 코드나 설정 파일도 변경하지 않습니다.**
  - 검토 후 “이제 계획에 따라 config.py 단일 소스로 통합하고 config.json을 제거하는 리팩터링을 수행하라”는 지시가 있을 때만 리팩터링을 진행합니다.

---

## 10. 리팩터링 수행 완료 요약

- **config.py**: LOG_CSV, REALTIME_FRAME_MAX_SHAPE 제거. CAM_SETTINGS에서 `path` 제거. RBP_CLIENTS, LOCAL_USB_CAMERAS, STREAM_USE_LZ4 추가. 섹션별(경로 / Ingest / Tracking / 실시간·플래그 / API·Scanner) 정리.
- **ingest/config_loader.py**: config.json 로딩 제거. config 모듈에서 RBP_CLIENTS, LOCAL_USB_CAMERAS, STREAM_USE_LZ4 읽어 get_stream_config(), get_rbp_clients(), get_local_usb_cameras() 반환. ConfigLoader.load()는 호환용 no-op.
- **config.json**: 파일 삭제.
- **tests/test_config_loader.py**: temp JSON 제거. config 모듈 기반 테스트로 변경. file_not_found 테스트 제거.
- **README.md, docs/05_DEPENDENCIES_AND_CONFIG.md**: “설정은 config.py 단일 소스, config.json 미사용” 반영.

---

**문서 끝.**
