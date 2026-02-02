# YOLO 처리 속도 개선 제안서

## 1. 현재 구현 상태 (측정·벤치마크 준비)

### 1.1 성능 측정 코드 (추가 완료)

- **위치**: `main.py` (500ms 세트 처리 분기)
- **로그 파일**: `config.OUT_DIR / "yolo_processing_times.jsonl"`  
  → `track/output/Parcel_Integration_Log_FIFO/yolo_processing_times.jsonl`
- **기록 내용** (세트 1회당 1줄 JSON):
  - `event`: `"PROCESSING_TIMES"`
  - `detection_wall_sec`: 4 cam detection 병렬 구간 wall-clock (초)
  - `detection_per_cam_sec`: 카메라별 detection 소요 (초)
  - `process_per_cam_sec`: 카메라별 matching/API 소요 (초)
  - `process_wall_sec`: 4 cam 순차 process 구간 합 (초)
  - `set_total_wall_sec`: 세트 1회 전체 소요 (초)

### 1.2 처리 시간 로그 집계 스크립트

- **실행**: `python3 monitoring/analyze_yolo_times.py [--path ...] [--last N]`
- **입력**: `yolo_processing_times.jsonl`
- **출력**: `n_sets`, `detection_wall_sec`(mean/min/max), `process_wall_sec`, `set_total_wall_sec`, 카메라별 detection/process 통계

### 1.3 imgsz·배치 벤치마크 스크립트

- **실행**: `python3 monitoring/yolo_benchmark.py`
- **출력 파일**: `monitoring/yolo_benchmark_results.json`
- **내용**:
  - `model_info`: 모델 경로, 존재 여부, task, imgsz 기본값
  - `imgsz_benchmark`: imgsz=640/480/320 단일 추론 mean/min/max (ms)
  - `batch_benchmark`: batch=1, batch=4 추론 mean/per_image (ms)

---

## 2. 현재 설정 (코드 기준)

| 항목 | 값 | 근거 |
|------|-----|------|
| **모델 파일** | `track/parcel_ver0123.pt` | `config.MODEL_PATH` |
| **프레임워크** | Ultralytics YOLO | `logic/detector.py`: `from ultralytics import YOLO` |
| **모델 세대** | .pt → YOLOv8 호환 | Ultralytics 표준 포맷 |
| **입력 해상도(캡처)** | 1280×720 | `config.LOCAL_USB_CAMERAS` (USB_LOCAL), RPI 전송 프레임 동일 가정 |
| **추론 입력(imgsz)** | **640** (기본값) | 정수 하나 = **정사각형 640×640**. 캡처(예: 960×570)는 letterbox로 640×640에 맞춤(비율 유지+패딩). |
| **TensorRT** | **미사용** | `.engine` export/load 없음 |
| **배치 처리** | **1** | 매 프레임 `model(img, ...)[0]` 단일 호출 (4 cam은 스레드 4개로 병렬) |
| **conf / iou** | 0.25 / 0.45 | `detector.get_detections` 내 하드코딩 |

---

## 3. 실측 데이터 수집 방법 (필수)

### A. 처리 시간 로그 (실제 트래킹 부하)

1. `python main.py` 로 track 실행 (시간순 버퍼 + 4 cam 세트 모드).
2. **최소 10초 ~ 1분** 동안 세트가 생성되도록 유지 (프레임 수신 정상).
3. 종료 후:
   ```bash
   python3 monitoring/analyze_yolo_times.py --path output/Parcel_Integration_Log_FIFO/yolo_processing_times.jsonl
   ```
4. 출력된 `detection_wall_sec`, `process_wall_sec`, `set_total_wall_sec` mean/max 를 아래 표에 채워 넣기.

### B. imgsz·배치 벤치마크 (동일 HW에서 비교)

1. `python3 monitoring/yolo_benchmark.py` 실행 (모델 파일 필요).
2. `monitoring/yolo_benchmark_results.json` 생성 확인.
3. `imgsz_benchmark`, `batch_benchmark` 수치를 아래 표에 채워 넣기.

**데이터 수집 후** 아래 표를 채우고, 제안서 하단 "실측 결과"에 `analyze_yolo_times.py` / `yolo_benchmark_results.json` 요약을 붙여 넣기.

### 실측 데이터 표 (수집 후 채움)

| 구분 | 항목 | 값 (수집 후 기입) |
|------|------|-------------------|
| **실제 세트** | n_sets (분석 구간) | |
| | detection_wall_sec mean (초) | |
| | detection_wall_sec max (초) | |
| | process_wall_sec mean (초) | |
| | set_total_wall_sec mean (초) | |
| | set_total_wall_sec max (초) | |
| **벤치(단일)** | imgsz=640 mean (ms) | |
| | imgsz=480 mean (ms) | |
| | imgsz=320 mean (ms) | |
| **벤치(배치)** | batch=1 mean (ms) | |
| | batch=4 total (ms) / per_image (ms) | |

---

## 4. 개선 방안 (실측 후 우선순위 확정)

아래는 **일반적인 영향** 기준 제안이다. **실측 데이터 수집 후** 벤치마크/로그로 효과를 확인하고 우선순위를 정한다.

### 4.1 입력 해상도(imgsz) 축소

- **내용**: `detector.get_detections` 호출 시 `imgsz=480` 또는 `imgsz=320` 명시.
- **기대 효과**: imgsz 640→320 시 추론 시간 약 30~50% 단축 (모델/GPU에 따라 상이). 정확도 일부 하락 가능.
- **측정**: `yolo_benchmark.py`의 `imgsz_benchmark` (640/480/320) 비교.
- **우선순위**: 실측에서 detection_wall_sec가 세트 시간의 주된 원인일 때 **높음**.

### 4.2 배치 추론 (4 cam 1회 호출)

- **내용**: 4장을 리스트로 넘겨 `model([img1,img2,img3,img4], ...)` 한 번 호출 후 결과를 카메라별로 분리.
- **기대 효과**: GPU 활용률 상승으로 4회 단일 호출 대비 **총 소요 시간 단축** (예: 4×100ms → 1×150ms 수준). 실측 필수.
- **측정**: `yolo_benchmark.py`의 `batch_benchmark` (batch=1 vs batch=4) 비교.
- **우선순위**: batch=4 per_image_ms가 batch=1 mean_ms 대비 크게 낮을 때 **높음**.

### 4.3 TensorRT / ONNX 변환

- **내용**: `.pt` → TensorRT `.engine` 또는 ONNX 후 해당 런타임으로 추론.
- **기대 효과**: 동일 imgsz에서 1.5~3배 추론 가속 (HW/드라이버 의존). 별도 벤치마크 필요.
- **우선순위**: imgsz·배치 적용 후에도 set_total_wall_sec가 목표(예: 세트당 &lt;0.5초)를 넘을 때 **검토**.

### 4.4 모델 경량화 (nano/small)

- **내용**: 동일 클래스로 YOLOv8n/v8s 등 작은 모델 학습·교체.
- **기대 효과**: 추론 시간 대폭 감소, 정확도 트레이드오프. 재학습·검증 필요.
- **우선순위**: 위 옵션만으로 부족할 때 **중장기**.

### 4.5 GPU 메모리 사전 할당 / warmup

- **내용**: 첫 1~2회 추론을 “웜업”으로 두고, 필요 시 CUDA 캐시 고정.
- **기대 효과**: 첫 세트 지연 완화. 전체 평균 처리 시간에는 제한적.
- **우선순위**: **낮음** (첫 구간 지연이 문제일 때만).

---

## 5. 권장 진행 순서

1. **실측 수집**  
   - track 10초~1분 → `analyze_yolo_times.py` 실행 → `yolo_processing_times.jsonl` 기준 통계 확보.  
   - `yolo_benchmark.py` 실행 → `yolo_benchmark_results.json` 확보.

2. **제안서 갱신**  
   - 위 “실측 데이터 표”와 “개선 방안”에 실측 수치 반영.  
   - detection_wall_sec vs process_wall_sec 비중으로 병목 구간 확인.

3. **우선순위 확정**  
   - 실측에 따라 4.1~4.5 순서 및 적용 여부 결정.

4. **승인 후 구현**  
   - 선택한 항목만 코드 반영 (imgsz 옵션, 배치 추론 등).  
   - 변경 후 동일 방법으로 재측정·검증.

---

## 6. 요약

- **측정 코드·스크립트**: 적용 완료. `yolo_processing_times.jsonl` + `analyze_yolo_times.py`, `yolo_benchmark.py`로 실측 가능.
- **현재 설정**: 모델 `parcel_ver0123.pt`, imgsz 기본 640, TensorRT 미사용, 배치 1.
- **다음 단계**: 10초~1분 실측 수집 → 표 채우기 → 개선안 우선순위 확정 → 승인 후 구현.
