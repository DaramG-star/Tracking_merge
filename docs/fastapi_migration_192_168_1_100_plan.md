# track → FastAPI(192.168.1.100) 연동 이전 계획서

**문서 목적**: 192.168.1.200 서버에서 `track` 서비스를 구동할 때, FastAPI 서버가 **192.168.1.100**으로 이전·구축된 상태에 맞춰 `track` 쪽 설정·코드 변경 포인트를 분석·계획한다.  
**제한**: 이 문서 검토·승인 전까지 **설정/코드를 실제로 수정하지 않는다.** 분석·설계·문서 작성만 수행한다.

---

## ⛔ 승인 전 변경 금지

**이 문서(`track/docs/fastapi_migration_192_168_1_100_plan.md`)를 검토하고 승인하기 전까지:**

- `track` 프로젝트 내 **어떤 설정/코드도 수정하지 말 것.**
- 192.168.1.200에서 `track` 서비스 기동 또는 FastAPI 연결 설정을 변경하지 말 것.

**수행 범위**: 분석, 설계, 문서 작성만. 승인 후 한 번에 설정/코드 변경 및 track 구동을 진행한다.

---

## 0. FastAPI 서버 이전 요약

| 항목 | 이전 | 현재 |
|------|------|------|
| **FastAPI 서버 위치** | 192.168.1.200 (또는 동일 PC) | **192.168.1.100** |
| **포트** | 3000 (pwa/parcel-api 기준) | **8000** (api/ 배포 기준) |
| **접속 URL 예** | `http://192.168.1.200:3000` | **`http://192.168.1.100:8000`** |
| **API prefix** | `/api` | `/api` (동일) |
| **Socket.io path** | `/socket.io` | `/socket.io` (동일) |

- **track 실행 위치**: **192.168.1.200** 서버에서 `track` 서비스를 구동.
- **통신 방향**: 192.168.1.200(track) → **192.168.1.100**(FastAPI) 으로 모든 API·Socket.io 연결이 가야 한다.

---

## 1. FastAPI 서버 주소 변경 영향 분석

### 1-1. FastAPI 서버 주소/포트 사용처 (전수 조사)

`track` 프로젝트 전체에서 하드코딩된 IP/호스트/포트 및 설정 파일을 조사한 결과는 아래와 같다.

#### (1) 설정 파일 — `track/config.py`

| 파일 | 라인 | 현재 값 | 용도 |
|------|------|---------|------|
| track/config.py | 116 | `API_BASE_URL = "http://192.168.1.200:3000/api"` | REST API 베이스 URL (api_helper에서 사용) |
| track/config.py | 117 | `SCANNER_HOST = "192.168.1.200"` | Socket.io 연결 호스트 (scanner_listener, main.py) |
| track/config.py | 118 | `SCANNER_PORT = 3000` | Socket.io 연결 포트 (scanner_listener, main.py) |

- **config.json, .env**: `track/` 디렉터리에는 **없음**. API/Scanner 설정은 **config.py 단일 소스**로 사용 중.

#### (2) 코드 내 참조 — 설정에서 읽는 부분

| 파일 | 라인 | 참조 내용 | 용도 |
|------|------|-----------|------|
| track/main.py | 136 | `ScannerListener(matcher, host=config.SCANNER_HOST, port=config.SCANNER_PORT)` | Socket.io 클라이언트 연결용 host/port 전달 |
| track/logic/scanner_listener.py | 23–24 | `host or getattr(config, "SCANNER_HOST", "192.168.1.200")`, `port or getattr(config, "SCANNER_PORT", 3000)` | ScannerListener 생성 시 host/port (미전달 시 config 또는 폴백) |
| track/logic/api_helper.py | 11 | `BASE_URL = getattr(config, "API_BASE_URL", "http://192.168.1.200:3000/api")` | REST API 호출 베이스 URL (모듈 로드 시 1회 설정) |

- **결론**: 실제 연결 주소/포트는 **config.py 3개 변수**에서만 정의되어 있고, 나머지는 이 설정을 읽어 쓴다. **config.py만 수정하면** main.py, scanner_listener, api_helper는 코드 변경 없이 새 주소를 사용한다.

#### (3) 코드 내 하드코딩 (폴백용)

| 파일 | 라인 | 현재 값 | 용도 |
|------|------|---------|------|
| track/logic/scanner_listener.py | 23 | `"192.168.1.200"` | `config.SCANNER_HOST` 없을 때 폴백 |
| track/logic/scanner_listener.py | 24 | `3000` | `config.SCANNER_PORT` 없을 때 폴백 |
| track/logic/api_helper.py | 11 | `"http://192.168.1.200:3000/api"` | `config.API_BASE_URL` 없을 때 폴백 |

- **권장**: config.py를 canonical로 유지하고, **폴백 값만** 192.168.1.100:8000 으로 맞춰 두면, 설정 누락 시에도 잘못된 주소로 나가지 않는다.

#### (4) 기타 문서/주석 내 IP·포트 (참고용, 동작에는 무관)

| 파일 | 내용 |
|------|------|
| track/docs/config_refactor_plan.md | API_BASE_URL, SCANNER_HOST, SCANNER_PORT, 192.168.1.200 언급 |
| track/docs/05_DEPENDENCIES_AND_CONFIG.md | "192.168.1.200:3000 (Socket.io + REST)" |
| track/README.md | config.py 항목 설명, API_BASE_URL, SCANNER_HOST/SCANNER_PORT |
| track/main.py | 88 | ZMQ 클라이언트 `ip` 기본값 `"127.0.0.1"` — **RBP_CLIENTS용**, FastAPI와 무관 |

- **main.py 88행**: Raspberry Pi ZMQ 클라이언트 IP 기본값. FastAPI 주소와는 무관하다.

### 1-2. FastAPI 역할 및 track 연동 방식 재확인

- **REST API (api_helper.py)**  
  - **역할**: track → FastAPI 로 **이벤트 전송**.  
  - **호출 API**:  
    - `POST {BASE_URL}/track` — 스캔 이벤트  
    - `PATCH {BASE_URL}/detect-position` — 위치 갱신  
    - `PATCH {BASE_URL}/detect-pickup` — 픽업  
    - `PATCH {BASE_URL}/detect-missing` — 미수  
    - `DELETE {BASE_URL}/detect-eol/{uid}` — EOL  
    - `PATCH {BASE_URL}/detect-disappear` — 소실  
  - **BASE_URL**: `config.API_BASE_URL` (현재 `http://192.168.1.200:3000/api` → 목표 `http://192.168.1.100:8000/api`).

- **Socket.io (scanner_listener.py)**  
  - **역할**: FastAPI(Socket.io 서버) → track 으로 **parcelUpdate(스캔 데이터)** 수신.  
  - **연결 URL**: `http://{SCANNER_HOST}:{SCANNER_PORT}` (현재 `http://192.168.1.200:3000` → 목표 `http://192.168.1.100:8000`).  
  - **경로**: `socketio_path="/socket.io"` (api 서버와 동일).  
  - **이벤트**: `parcelUpdate` 수신 시 `matcher.add_scanner_data(uid, route_code, time_s)` 호출.

- **설정/route code/파라미터**: track이 FastAPI에서 **설정을 읽어오는 API는 없음**. route code 등은 FastAPI가 MongoDB/Change Stream에서 받아 Socket.io로 보내는 스캔 데이터에 포함된다.

- **192.168.1.200에서 track 구동 시**:  
  - **변경 후 기대 동작**: 모든 REST·Socket.io 연결이 **192.168.1.100:8000** 으로 간다.  
  - **현재 상태**: config.py가 192.168.1.200:3000 을 가리키므로, **로컬(200) 또는 예전 주소를 바라보고 있음**.  
  - **수정 필요**: config.py(및 폴백)를 192.168.1.100:8000 으로 바꾸면 된다.

---

## 2. 변경 대상 설정/코드 정리 (수정 금지 — 계획만)

### 2-1. 설정 파일

- **Canonical 설정**: `track/config.py` 단일 소스. config.json, .env 없음.

| 설정 키 | 현재 값 | 목표 값 | 실제 사용처 |
|---------|---------|---------|-------------|
| API_BASE_URL | `http://192.168.1.200:3000/api` | **`http://192.168.1.100:8000/api`** | logic/api_helper.py (BASE_URL) |
| SCANNER_HOST | `192.168.1.200` | **`192.168.1.100`** | main.py(ScannerListener 인자), logic/scanner_listener.py(self.host) |
| SCANNER_PORT | `3000` | **`8000`** | main.py(ScannerListener 인자), logic/scanner_listener.py(self.port → Socket.io URL) |

- **중복 정의**: 없음. **config.py만 수정**하면 된다.

### 2-2. 코드 내 하드코딩 (폴백 값)

설정 누락 시 사용되는 폴백을 목표 주소로 맞추면, 실수로 예전 주소를 쓰는 일을 막을 수 있다.

| 파일 | 라인 | 현재 값 | 목표 값 | 비고 |
|------|------|---------|---------|------|
| track/logic/scanner_listener.py | 23 | `"192.168.1.200"` | `"192.168.1.100"` | getattr(..., "192.168.1.200") 폴백 |
| track/logic/scanner_listener.py | 24 | `3000` | `8000` | getattr(..., 3000) 폴백 |
| track/logic/api_helper.py | 11 | `"http://192.168.1.200:3000/api"` | `"http://192.168.1.100:8000/api"` | getattr(..., ...) 폴백 |

- **리팩터링 방향**: “하드코딩 → 설정 참조”는 이미 되어 있음. **폴백 문자열/숫자만** 위 목표 값으로 변경하면 된다.

---

## 3. WAIT_FOR_FIRST_SCAN 플래그 검토

### 3-1. 정의 위치·용도

| 항목 | 내용 |
|------|------|
| **정의 위치** | `track/config.py` 111행 |
| **현재 값** | `WAIT_FOR_FIRST_SCAN = False` |
| **주석** | "False: 스캐너 대기 없이 메인 루프 시작 (카메라/디스플레이 테스트용)" |

### 3-2. 의미·시스템 동작

- **False (현재)**  
  - **의미**: “첫 스캔 데이터를 기다리지 않고, 메인 루프를 바로 시작한다.”  
  - **동작**: ScannerListener는 시작되고 Socket.io로 192.168.1.100(변경 후)에 연결을 시도하지만, **메인 루프는 곧바로** `while _running:` 에 진입해 카메라 프레임·YOLO·matcher를 처리한다. 스캔 데이터가 오는 대로 `matcher.add_scanner_data` 로 반영된다.  
  - **용도**: 스캐너/FastAPI 없이 카메라·디스플레이·YOLO만 확인할 때 유리.

- **True 로 두었을 때**  
  - **의미**: “최초 스캔이 한 번이라도 올 때까지 메인 루프 진입을 지연한다.”  
  - **동작**: `main.py` 144–150행에서 `len(matcher.queues["q_scan"]) == 0` 인 동안 `time.sleep(0.1)` 로 대기. Socket.io `parcelUpdate`(insert)가 와서 `matcher.add_scanner_data` 가 호출되면 `q_scan`에 데이터가 쌓이고, 그때부터 메인 루프가 시작된다.  
  - **용도**: “스캔이 한 번이라도 온 뒤에만 트래킹을 시작하고 싶을 때” 사용.

### 3-3. 사용처

| 파일 | 라인 | 사용 내용 |
|------|------|-----------|
| track/main.py | 144–150 | `if config.WAIT_FOR_FIRST_SCAN:` → True이면 `matcher.queues["q_scan"]`에 데이터가 올 때까지 대기 후 메인 루프 시작 |

- **다른 모듈**: WAIT_FOR_FIRST_SCAN을 참조하는 코드는 **main.py 이곳뿐**이다.

### 3-4. FastAPI 서버 192.168.1.100 이전과의 관계

- **연결 주소 변경과의 관계**  
  - WAIT_FOR_FIRST_SCAN은 “**언제** 메인 루프를 시작할지”만 제어한다.  
  - “**어디로** Socket.io 연결할지”(SCANNER_HOST/PORT)와는 **독립**이다.  
  - 따라서 FastAPI를 192.168.1.100:8000 으로 바꾼다고 해서 **WAIT_FOR_FIRST_SCAN의 의미나 동작 방식이 바뀌지 않는다.**

- **타이밍·동기화**  
  - **False**: 192.168.1.100 서버에 연결되기 전에도 메인 루프는 시작된다. 나중에 연결되면 그때부터 스캔 데이터가 반영된다.  
  - **True**: 192.168.1.100 서버에 연결되고, **최초 parcelUpdate(insert)가 한 번이라도 올 때까지** 메인 루프가 시작되지 않는다. 네트워크/서버 지연이 크면 “첫 스캔 대기” 시간이 길어질 수 있다.  
  - 즉, **FastAPI 이전으로 인해 “첫 스캔”이 도착하는 시점이 네트워크 홉 하나 더 있는 192.168.1.100 기준으로 바뀔 뿐**이고, 로직상 추가 변경은 필요 없다.

- **오류 처리**  
  - WAIT_FOR_FIRST_SCAN은 “연결 실패 시 어떻게 할지”를 바꾸지 않는다. 연결 실패·재연결은 **ScannerListener**의 `max_retry_time`, `reconnection_delay` 등으로 처리된다.

### 3-5. 결론·제안

| 항목 | 내용 |
|------|------|
| **현재 값 유지** | `WAIT_FOR_FIRST_SCAN = False` **그대로 두는 것을 권장.** |
| **이유** | FastAPI를 192.168.1.100으로 옮긴 것만으로는 이 값을 바꿀 이유가 없음. False면 스캔 유무와 관계없이 track이 바로 동작해, 192.168.1.200에서 기동·테스트가 단순함. |
| **True로 바꾸는 경우** | “반드시 첫 스캔이 온 뒤에만 트래킹을 시작하고 싶다”는 요구가 있을 때만 True로 변경. 그때는 192.168.1.100 서버 및 MongoDB에서 스캔 이벤트가 실제로 발생하는지, Socket.io 연결이 수립되는지 확인한 뒤 사용하는 것을 권장. |
| **이번 이전 작업 범위** | **WAIT_FOR_FIRST_SCAN 값은 변경하지 않는다.** (문서에서의 설계/제안만 반영.) |

---

## 4. 기타 FastAPI 서버 변경으로 영향받을 수 있는 부분

| 항목 | 내용 |
|------|------|
| **CORS** | FastAPI(api)는 `allow_origins=["*"]` 등으로 CORS를 열어 두는 경우가 많음. track은 **서버(192.168.1.200)에서 동작하는 Python 클라이언트**이므로 브라우저 CORS와 무관. **추가 설정 불필요.** |
| **WebSocket/Socket.io 엔드포인트** | api 서버는 `/socket.io` 경로로 Socket.io 서빙. track의 scanner_listener는 `socketio_path="/socket.io"` 사용. **포트만 8000으로 맞추면 됨.** |
| **헬스체크** | 운영/모니터링에서 “FastAPI 살아 있는지” 확인하려면 `http://192.168.1.100:8000/health` 등을 사용하면 됨. track 코드에는 헬스체크 호출이 없음. 필요 시 별도 스크립트/모니터링에서만 사용. |
| **로그/모니터링 URL** | 로그에 남기는 URL이 있다면 192.168.1.100:8000 으로 표기되도록, config 기반으로 출력하도록 두면 됨. 현재 track 코드에는 고정 URL 로그가 없음. |
| **방화벽** | 192.168.1.200 → 192.168.1.100 **TCP 8000** 허용 필요. (기존 3000이었다면 8000으로 변경.) |

---

## 5. 단계별 변경·검증 계획

### 1단계: 설정/코드 변경 포인트 정리 및 수정 (승인 후)

1. **track/config.py**  
   - `API_BASE_URL` → `"http://192.168.1.100:8000/api"`  
   - `SCANNER_HOST` → `"192.168.1.100"`  
   - `SCANNER_PORT` → `8000`

2. **track/logic/scanner_listener.py**  
   - getattr 폴백: `"192.168.1.200"` → `"192.168.1.100"`, `3000` → `8000`

3. **track/logic/api_helper.py**  
   - getattr 폴백: `"http://192.168.1.200:3000/api"` → `"http://192.168.1.100:8000/api"`

4. **(선택)** 문서 갱신: README.md, docs/05_DEPENDENCIES_AND_CONFIG.md 등에 “FastAPI는 192.168.1.100:8000 기준” 명시.

### 2단계: 스테이징/테스트 (192.168.1.200 → 192.168.1.100 연동)

1. 192.168.1.100에서 FastAPI(api) 기동 확인: `uvicorn main:socket_app --host 0.0.0.0 --port 8000`
2. 192.168.1.200에서 track 기동: `python main.py` (또는 기존 실행 방법)
3. 확인 항목:  
   - ScannerListener가 `http://192.168.1.100:8000` 으로 Socket.io 연결되는지,  
   - `parcelUpdate` 수신 시 matcher에 스캔 데이터가 쌓이는지,  
   - api_helper를 통한 REST 호출(track, detect-position, detect-pickup 등)이 192.168.1.100:8000 으로 나가는지,  
   - MongoDB/Change Stream 또는 테스트 이벤트 발생 시 track 쪽에서 정상 반영되는지.

### 3단계: 실제 운영 전환 및 모니터링

1. 위 테스트 통과 후, 192.168.1.200에서 track 서비스를 정식 기동(시스템드/슈퍼바이저 등).
2. 192.168.1.100 FastAPI 로그·헬스체크·Socket.io 연결 수 등 모니터링.
3. 192.168.1.200 track 로그에서 API 오류·Socket.io 끊김 없이 동작하는지 확인.

---

## 6. 요약 표 (변경 포인트 + WAIT_FOR_FIRST_SCAN)

| 구분 | 항목 | 현재 | 목표 | 비고 |
|------|------|------|------|------|
| **설정** | API_BASE_URL | http://192.168.1.200:3000/api | http://192.168.1.100:8000/api | config.py |
| **설정** | SCANNER_HOST | 192.168.1.200 | 192.168.1.100 | config.py |
| **설정** | SCANNER_PORT | 3000 | 8000 | config.py |
| **폴백** | scanner_listener.py | 23–24행 폴백 192.168.1.200, 3000 | 192.168.1.100, 8000 | 설정 누락 시 대비 |
| **폴백** | api_helper.py | 11행 폴백 URL 192.168.1.200:3000/api | 192.168.1.100:8000/api | 동일 |
| **플래그** | WAIT_FOR_FIRST_SCAN | False | **변경 없음(유지)** | FastAPI 이전과 무관, 현재 값 유지 권장 |

---

**문서 끝. 승인 전까지 설정/코드 수정 금지.**
