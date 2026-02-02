# Track 성능 계측 및 Jetson 엣지 이식 계획서

**목적**: 192.168.1.200 PC에서 동작하는 Track 시스템의 **CPU/GPU/메모리 사용량**과 **Parcel 처리량·지연**을 계량하고, 그 근거로 **Jetson Orin Nano 4GB** 등 엣지 디바이스 이식 가능성을 판단할 수 있게 한다.  
**제한**: 이 문서 검토·승인 전까지 **계측 코드 삽입, ONNX/TensorRT 변환, Jetson 배포 작업을 하지 않는다.** 설계·문서화만 수행한다.

---

## ⛔ 승인 전 변경·실행 금지

**이 문서를 검토하고 승인하기 전까지:**

- Track 본 코드에 계측/로깅 코드를 **실제로 넣지 말 것.**
- Jetson으로의 ONNX/TensorRT export·배포를 **시작하지 말 것.**
- 192.168.1.200에서 장시간 성능 로깅을 **실행하지 말 것.**

**수행 범위**: 측정 지표 정의, 로깅 방안·CSV 스키마 설계, Jetson 이식 전략 개요 문서화. 승인 후 계측 모듈 추가·CSV 수집·최적화 작업을 단계적으로 진행한다.

---

## 1. 현재 애플리케이션 성능 지표 정의

### 1-1. 수집할 지표 목록

#### (1) 리소스 사용량

| 지표 | 정의 | 단위 | 수집 주기(권장) | Jetson 비교 시 필요 여부 |
|------|------|------|-----------------|---------------------------|
| **CPU 전체 사용률** | 시스템 전체 CPU 사용률 | % | 1~5초 | ○ (Orin Nano CPU 부하 가늠) |
| **Track 프로세스 CPU** | Track(Python) 프로세스의 CPU 사용률 | % | 1~5초 | ○ |
| **시스템 메모리 사용량** | 전체/사용 중 RAM | MB | 1~5초 | ○ (4GB 한계와 비교) |
| **Track 프로세스 RSS** | Track 프로세스의 Resident Set Size | MB | 1~5초 | ○ |
| **GPU Utilization** | GPU 연산 사용률 | % | 1~5초 | ○ (Orin Nano GPU vs 현재 GPU) |
| **GPU 메모리 사용량** | GPU VRAM 사용량 | MB | 1~5초 | ○ (4GB 한계) |
| **GPU 전력(선택)** | GPU 전력 소비 | W | 5초 이상 | △ (엣지 전력 설계 시) |

- **수집 도구**: `psutil`(CPU/메모리), `nvidia-smi` 또는 **pynvml**(NVML)(GPU). Jetson에서도 `tegrastats`/NVML 유사 사용 가능.

#### (2) 처리량·지연

| 지표 | 정의 | 단위 | 수집 방식 | Jetson 비교 시 필요 여부 |
|------|------|------|-----------|---------------------------|
| **Parcel 이벤트 수** | detection/tracking 기준 이벤트 건수 | 건/초, 건/분, 건/시간 | 기존 CSV 집계 또는 별도 카운터 | ○ (목표 처리량 대비) |
| **프레임→Detection 완료 지연** | `aggregator.get` 직후 ~ `get_detections()` 반환까지 | ms | 메인 루프 내 구간 측정 | ○ (실시간성) |
| **YOLO 추론 단독 지연** | `get_detections()` 내부 추론 시간 | ms | detector 내부 또는 호출 전후 타임스탬프 | ○ (모델 최적화 효과) |
| **카메라별 유효 FPS** | 카메라당 실제 처리된 프레임 수/초 | FPS | 처리된 프레임 수 / 경과 시간 | ○ (4캠 × N FPS 목표) |
| **파이프라인 전체 FPS** | 4캠 합산 처리 프레임 수/초 | FPS | 동일 | ○ |

#### (3) 모델/파이프라인 레벨

| 지표 | 정의 | 용도 |
|------|------|------|
| **YOLO 추론 FPS/latency** | 모델 단독 기준 (입력 1장당 ms, FPS) | ONNX/TensorRT 변환 전·후 비교 |
| **Tracking 파이프라인 FPS** | 카메라 4개 전체·카메라당 유효 FPS | Jetson 목표(예: 4캠 × 10 FPS) 달성 여부 판단 |

- 위 지표는 **Jetson Orin Nano 4GB** 스펙(CPU/GPU 성능, 메모리 4GB)과 비교 가능한 형태로 정의한다.

---

## 2. 기존 `--csv` 로깅과의 관계 (중복 방지)

### 2-1. 현재 `python3 main.py --csv` 로깅 내용 분석

| 항목 | 내용 |
|------|------|
| **생성 파일** | **단일 파일**: `config.OUT_DIR / "tracking_logs_live.csv"` → `track/output/Parcel_Integration_Log_FIFO/tracking_logs_live.csv` |
| **컬럼(헤더)** | `timestamp`, `cam`, `local_uid`, `master_id`, `route`, `x1`, `y1`, `x2`, `y2`, `event` |
| **기록 트리거** | **Parcel/이벤트 단위**: (1) detection·매칭 시 TRACKING/MATCHED/MISSING 한 행, (2) resolve_pending 시 PICKUP/DISAPPEAR 한 행. 프레임마다가 아니라 **이벤트 발생 시마다** 기록. |
| **timestamp** | 프레임/이벤트 시점의 epoch 초(실수). |
| **활용 용도** | 디버깅, 사후 분석(언제/어느 캠/어떤 master_id/route/event인지), 처리량 집계(행 수 = 이벤트 수). |

- **기록 주기**: 이벤트 발생 시에만. 고정 1초/5초 간격이 아님.
- **미포함**: CPU/GPU/메모리, 추론 지연(ms), FPS, distance(남은 거리).

### 2-2. 신규 계측 항목 vs 기존 CSV 비교

| 분류 | 항목 | 설명 |
|------|------|------|
| **A. 이미 존재(재활용)** | timestamp, cam, local_uid, master_id, route, event, x1,y1,x2,y2 | 기존 `tracking_logs_live.csv`에 있음. **처리량(건/분, 건/시간)** 은 이 CSV 행 수로 집계 가능. 중복 수집 불필요. |
| **B. 부분 중복** | timestamp | 이벤트 시각은 있으나, **“처리 완료 시각”**이 없어 **지연(ms)** 계산 불가. “프레임 수신 시각” vs “detection 완료 시각” 같은 쌍이 없음. |
| **C. 완전히 신규** | CPU 전체/Track 프로세스, 시스템/Track 메모리, GPU Util, GPU 메모리, 추론 지연(ms), 카메라별/전체 FPS | 기존 CSV에 전혀 없음. **별도 CSV 또는 최소 컬럼**으로 추가 필요. |

### 2-3. 신규 CSV/컬럼 설계 시 중복 방지 원칙

1. **Parcel 단위 필드 중복 금지**  
   - `uid`, `camera_id`, `route_code`, `event` 등은 이미 `tracking_logs_live.csv`에 있으므로, **성능 전용 CSV에는 넣지 않는다.**  
   - 리소스·성능 중심 항목만 신규로 수집한다.

2. **시스템 리소스 → 별도 CSV**  
   - **파일명 예**: `system_metrics.csv`  
   - **수집 주기**: 1초 또는 5초 간격(고정).  
   - **컬럼 예**: `timestamp,cpu_total_pct,cpu_track_pct,mem_total_mb,mem_used_mb,mem_track_rss_mb,gpu_util_pct,gpu_mem_used_mb`  
   - Parcel 이벤트와 무관하게 **주기적으로** 한 행 추가.

3. **처리량**  
   - 기존 `tracking_logs_live.csv`를 Pandas로 읽어 `timestamp` 구간별 행 수를 세면 **건/분, 건/시간** 계산 가능. **별도 처리량 전용 CSV는 두지 않는다.**

4. **지연(latency)**  
   - **옵션 1**: 기존 CSV 구조를 유지하고, **한두 컬럼만 추가** (예: `inference_ms`). 해당 행이 나온 “그 프레임”의 추론 시간(ms)을 넣는다. 이벤트가 없는 프레임은 CSV에 행이 없으므로, “프레임별 지연”을 모두 남기려면 **일부 행만** 채우거나, **별도 샘플링**이 필요함.  
   - **옵션 2(권장)**: 지연은 **system_metrics.csv**에 구간 통계로만 기록. 예: `inference_ms_avg`, `inference_ms_p99` (해당 1~5초 구간 내 측정값의 평균/백분위). 그러면 기존 parcel CSV는 **전혀 수정하지 않아도** 됨.

5. **정리**  
   - **기존 `--csv` 결과 구조는 유지.**  
   - **신규**: `system_metrics.csv` (리소스 + 선택적으로 구간별 지연 통계).  
   - 필요 시 나중에 기존 CSV에 `latency_ms` 한 컬럼만 추가하는 **최소 변경**을 검토할 수 있으나, 우선은 **별도 system_metrics** 로 설계한다.

---

## 3. 측정/로깅 방안 설계 (192.168.1.200 PC용)

### 3-1. CPU/메모리/GPU 모니터링

- **도구**:  
  - **psutil**: 시스템 CPU 사용률, 메모리(total/used), Track 프로세스(PID)의 CPU%, RSS.  
  - **pynvml** 또는 **nvidia-smi** 파싱: GPU Utilization(%), GPU 메모리 사용량(MB). Jetson에서도 NVML/tegrastats 사용 가능.
- **실행 방식 후보**  
  - **방안 A(권장)**: Track과 **분리된 별도 프로세스**로 모니터링 스크립트 실행.  
    - 예: `python monitor.py --pid <track_pid> --interval 5 --output system_metrics.csv`  
    - Track PID는 사용자가 넘기거나, `--track-pattern "python.*main.py"` 로 탐색.  
    - **장점**: Track 메인 루프에 전혀 영향 없음.  
  - **방안 B**: Track 내부에서 **별도 스레드**로 주기적으로 psutil/NVML 읽고 CSV에 한 줄씩 append.  
    - **장점**: 한 프로세스로 관리 가능. **단점**: I/O·lock 등으로 메인 루프에 약간 영향 줄 수 있음. 최소화하려면 버퍼링 후 비동기 flush.
- **CSV 구조 예시 (system_metrics.csv)**  

  ```text
  timestamp,cpu_total_pct,cpu_track_pct,mem_total_mb,mem_used_mb,mem_track_rss_mb,gpu_util_pct,gpu_mem_used_mb
  1706123456.12,45.2,38.1,32768,18200,1200.5,62.0,2048
  ```

- **로깅 주기**: 1초 또는 5초. 10시간 연속 시 5초 간격이면 7200행. CSV 한 행 약 80~100바이트로 가정하면 **약 0.7~1 MB**. 부담 적음.

### 3-2. Parcel 처리량·지연 로깅

- **처리량**  
  - **기존 CSV 활용**: `tracking_logs_live.csv`의 `timestamp` 기준으로 구간별 행 수를 세면 **건/분, 건/시간** 계산. 별도 파일 없음.
- **지연**  
  - **구간 통계를 system_metrics에 포함** (옵션 2):  
    - 메인 루프에서 “프레임 수신 → get_detections 완료” 구간을 매번 측정하고, 1~5초 구간 내 샘플의 평균/최대/ P99를 계산해 `system_metrics.csv`에 컬럼 추가.  
    - 예: `inference_ms_avg,inference_ms_p99,pipeline_fps` (해당 구간의 파이프라인 유효 FPS).  
  - **구현 시**: 메인 루프에 `time.perf_counter()` 두 번(구간 시작/끝)만 넣고, 결과를 **스레드 안전 큐**에 넣으면 모니터링 스레드가 주기적으로 꺼내서 통계 내고 CSV 행에 넣을 수 있음. 또는 별도 프로세스에서는 “지연”을 직접 측정할 수 없으므로, **지연은 Track 내부 스레드로 수집**하고, **리소스만 별도 프로세스**로 수집하는 혼합도 가능.

### 3-3. CSV 포맷 요약

| 파일 | 트리거 | 주요 컬럼 | 비고 |
|------|--------|-----------|------|
| **tracking_logs_live.csv** (기존) | Parcel/이벤트 발생 시 | timestamp, cam, local_uid, master_id, route, x1,y1,x2,y2, event | **변경 없음.** 처리량은 여기서 집계. |
| **system_metrics.csv** (신규) | 1~5초 간격 | timestamp, cpu_total_pct, cpu_track_pct, mem_total_mb, mem_used_mb, mem_track_rss_mb, gpu_util_pct, gpu_mem_used_mb [, inference_ms_avg, inference_ms_p99, pipeline_fps ] | 리소스 + 선택적으로 구간별 지연·FPS. |

### 3-4. 코드 통합 방식 제안 (설계만)

- **옵션 구조**  
  - **`--csv`**: 기존처럼 parcel 이벤트만 `tracking_logs_live.csv`에 기록. **그대로 유지.**  
  - **성능 계측**  
    - **옵션 1**: `--metrics-csv` 를 새로 두고, 이 플래그가 있을 때만 `system_metrics.csv` 기록 (내부 스레드 또는 별도 프로세스로).  
    - **옵션 2**: `--csv` 가 켜져 있을 때만 `system_metrics.csv` 도 함께 기록. (관리 단순, 10시간 테스트 시 한 번에 두 종류 로그 확보 가능.)
- **부하·파일 크기**  
  - system_metrics: 5초 간격, 10시간 → 약 7200행, **~1 MB 미만**.  
  - 기존 parcel CSV와 독립이므로 I/O 부하는 작음.  
  - **꼭 필요한 최소 항목만** 넣어서 “Jetson 이식 판단 + 현재 PC 성능 계량”에 충분하도록 한다.

### 3-5. Jetson 이식 시 재사용

- **psutil**: Jetson Linux에서 동일 사용 가능.  
- **GPU**: Jetson은 **NVML** 또는 **tegrastats**로 GPU/메모리 사용량 확인. 설계 시 “nvidia-smi 또는 NVML”로 통일해 두면, Jetson에서는 JetPack 제공 API/스크립트로 같은 컬럼을 채우도록 하면 된다.  
- **system_metrics.csv** 스키마를 PC와 Jetson에서 **동일**하게 두면, 수집된 CSV만으로 PC vs Jetson 성능 비교가 가능하다.

---

## 4. Jetson Orin Nano 4GB 대상 성능 추정·모델 최적화 방안 (계획만)

### 4-1. 현재 PC 지표 → Jetson 요구 성능 추정

- **계측 후 비교 방식**  
  - 현재 PC에서 수집할 값:  
    - YOLO 추론 평균/최대 지연(ms), 입력 해상도(예: 640×640).  
    - 4캠 전체 파이프라인 FPS, 카메라당 유효 FPS.  
    - GPU Util(%), GPU 메모리(MB), CPU%, RAM( Track 프로세스).  
  - **Jetson Orin Nano 4GB** 제원(공개 스펙)과 비교:  
    - GPU: 1024 CUDA Core, 32 Tensor Core, FP16 성능 등.  
    - 메모리: 4GB 통합 메모리(CPU·GPU 공유).  
    - CPU: 6-core Arm.  
  - **목표선 예**: “4캠 × 10 FPS를 실시간으로 유지하려면, 카메라당 추론이 평균 100 ms 이하여야 한다” 등을 계측 데이터로 검증하고, Jetson에서 동일 모델(또는 ONNX/TensorRT 변환)로 측정했을 때 그 목표를 만족하는지 판단.

- **결론 도출 예시**  
  - “현재 PC에서 GPU Util 60%, 추론 15 ms일 때 4캠 × 약 12 FPS. Jetson Orin Nano 4GB에서 TensorRT FP16으로 추론 80 ms면 4캠 × 약 3 FPS. 10 FPS 목표라면 해상도 축소 또는 INT8 양자화 필요.”  
  - 위와 같은 **구체적·논리적 결론**을 내리려면, **먼저 192.168.1.200에서 위 지표를 CSV로 수집**하는 것이 전제이다.

### 4-2. 모델 최적화 전략: Ultralytics YOLO → ONNX / TensorRT

- **ONNX**  
  - Ultralytics YOLO를 ONNX로 export (이미 지원).  
  - Jetson에서 **ONNX Runtime GPU** 또는 **TensorRT** 백엔드로 추론.
- **TensorRT**  
  - Jetson 환경에서 ONNX → TensorRT 엔진 변환:  
    - `trtexec` 또는 TensorRT Python API 사용.  
  - **FP16** 또는 **INT8** 양자화로 지연·메모리 절감.
- **파이프라인 개요(계획)**  
  1. PC에서 학습/튜닝된 YOLO(또는 현재 `parcel_ver0123.pt`) 유지.  
  2. PC에서 ONNX export.  
  3. ONNX를 Jetson으로 복사 후, JetPack/TensorRT로 엔진 빌드(FP16/INT8 선택).  
  4. Track의 detector를 “ONNX Runtime” 또는 “TensorRT” 백엔드로 교체하는 래퍼 작성.  
  5. Jetson에서 동일한 `system_metrics.csv` + parcel CSV 수집으로 성능 비교.

- **도구**: JetPack, TensorRT 버전, Ultralytics export 형식은 구현 단계에서 버전 고정.

### 4-3. Jetson에서의 멀티카메라 처리 구조 초안

- **후보**  
  - **A**: 4캠을 모두 Jetson에 직접 연결(USB 또는 CSI). 추론·트래킹 전부 Jetson에서 수행.  
  - **B**: 캠/전처리는 Jetson, Heavy YOLO는 서버(192.168.1.200 등)에서 수행. (네트워크 지연·대역폭 고려 필요.)  
- **리소스 한계 대응**  
  - 입력 해상도 축소(예: 640→480), FPS 제한(예: 5 FPS), batch size=1, 간단한 tracker 유지.  
  - 4GB 공유 메모리이므로 GPU·CPU 사용량을 동시에 모니터링해 OOM을 피하는 전략 필요.

- **“Jetson Orin Nano 4GB로 4캠 동시 실시간 처리 가능 여부”**는, **현재 PC에서 수집한 system_metrics + parcel CSV**로 “필요한 최소 성능”을 산출한 뒤, Jetson에서 동일 계측을 돌려 비교하는 흐름으로 판단한다.

---

## 5. 구현 방안 요약 (옵션, 코드 변경 전 제안)

### 5-1. 기존 `--csv` 유지

- `python3 main.py --csv` 시 **tracking_logs_live.csv** 는 현재와 동일하게 유지.  
- **컬럼 추가·삭제 없음.**  
- 처리량(건/분, 건/시간)은 이 파일만으로 Pandas 집계.

### 5-2. 신규 계측

- **system_metrics.csv** (별도 파일)  
  - **주기**: 1초 또는 5초.  
  - **컬럼**: timestamp, cpu_total_pct, cpu_track_pct, mem_total_mb, mem_used_mb, mem_track_rss_mb, gpu_util_pct, gpu_mem_used_mb.  
  - 선택: inference_ms_avg, inference_ms_p99, pipeline_fps (구간별).  
- **수집 방식**  
  - **우선 제안**: Track과 **별도 프로세스** `monitor.py` (psutil + nvidia-smi/NVML). Track PID 인자로 전달.  
  - **대안**: Track 내부 **별도 스레드**에서 동일 항목 수집 후 CSV append. 메인 루프는 최소 침범(버퍼·비동기 flush).

### 5-3. 옵션 플래그 제안

- **`--metrics-csv`** (신규): 켜져 있을 때만 system_metrics.csv 기록.  
  - 또는 **`--csv`** 가 켜져 있을 때만 system_metrics도 함께 기록하도록 할 수 있음(문서 단계에서는 두 옵션 모두 허용).  
- **지연 수집**: Track 내부에서만 측정 가능하므로, `--metrics-csv`(또는 동일 조건)일 때 메인 루프에 **최소한의 타이밍 측정**만 넣고, 결과를 모니터링 스레드에 넘겨 system_metrics 행에 넣는 방식.

### 5-4. 10시간 연속 시 파일 크기 추정

- **tracking_logs_live.csv**: 기존과 동일. 이벤트 수에 따라 수 MB~수십 MB.  
- **system_metrics.csv**: 5초 간격, 10시간 → 7200행, **~1 MB 미만.**

---

## 6. 문서 요약

### 6-1. 어떤 지표를, 어떤 방식으로, 어느 파일에 로깅할지

| 지표 | 수집 방식 | 파일 | 비고 |
|------|-----------|------|------|
| CPU 전체/Track, 메모리(시스템/Track RSS), GPU Util, GPU 메모리 | psutil + NVML/nvidia-smi, 1~5초 간격 | **system_metrics.csv** (신규) | 기존 parcel CSV와 분리. |
| Parcel 처리량(건/분, 건/시간) | 기존 CSV 행 수 집계 | **tracking_logs_live.csv** (기존) | 변경 없음. |
| 추론 지연(ms), 파이프라인 FPS | 메인 루프 구간 측정 → 구간별 통계 | **system_metrics.csv** (신규, 선택 컬럼) | 기존 CSV에 parcel 필드 중복하지 않음. |

### 6-2. Jetson Orin Nano 4GB에서 4캠 돌리기 위한 전략 개요

1. **현재 PC(192.168.1.200)** 에서 **system_metrics.csv** + 기존 parcel CSV를 수집해,  
   - YOLO 추론 시간(ms), 4캠 FPS, GPU/CPU/메모리 사용량을 **수치로 확보**.
2. **Jetson Orin Nano 4GB** 스펙과 비교해,  
   - “4캠 × N FPS” 목표에 필요한 **최소 성능**을 정의.
3. **Ultralytics YOLO → ONNX → TensorRT(FP16/INT8)** 로 변환해 Jetson에서 추론하고,  
   - 동일한 **system_metrics** 스키마로 수집해 “충분/부족”을 판단.
4. **멀티카메라 구조**는 4캠 전부 Jetson 직접 연결을 기본으로 하고,  
   - 리소스 한계 시 해상도·FPS 제한, INT8 등으로 조정.

**승인 후**: 192.168.1.200에 모니터링 코드(또는 monitor.py)를 설치하고 CSV를 수집한 뒤, 수치를 바탕으로 Jetson 이식·최적화를 단계적으로 진행한다.

---

**문서 끝. 승인 전까지 계측 코드 삽입·ONNX/TensorRT 작업·장시간 로깅 실행 금지.**
