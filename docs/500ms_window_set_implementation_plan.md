# 500ms 윈도우 기반 프레임 세트 구성 — 이해 및 구현 계획

## 1. 이해 정리

### 1.1 이전 오해 (정정)

- **잘못된 "Actual sets"**: 기존에는 `get_oldest()` 호출 전마다 peek으로 "4 cam + ts spread ≤ 500ms"인 경우를 세서 `complete_count_this_second`에 더했음. USB가 빠르면 초당 peek이 수십 번이라 **52.8/sec** 같은 비현실적 수치가 나옴.
- **잘못된 "Theoretical max"**: `min(frame_counts)` = 병목 카메라 fps(2.5)를 그대로 썼음. 세트는 "500ms당 1개" 단위이므로 **초당 이론적 최대 세트 수 = 2**가 맞음.
- **잘못된 Efficiency**: actual/theoretical를 52.8/2.5 등으로 계산해 **2640%** 같은 무의미한 값이 나옴.

### 1.2 올바른 정의

| 항목 | 의미 |
|------|------|
| **Theoretical max sets** | **2.0/sec** — 1초를 500ms 구간 2개로 나누고, 각 구간에서 최대 1개 세트만 만들 수 있음. RPI 2.5fps → 500ms당 약 1.25장이므로 구간당 1세트가 상한. |
| **Actual sets created** | 지난 1초(또는 통계 구간) 동안 **500ms 윈도우 기준으로 실제로 완성한 세트 수** (0, 1, 2). |
| **Efficiency** | `actual_sets / theoretical_max_sets` → 0%, 50%, 100% 등 의미 있는 비율. |

### 1.3 물리적 의미

- 500ms 동안 컨베이어 이동: 약 **18cm** (36.6 cm/s × 0.5s).
- **1세트** = 그 500ms 구간의 4개 카메라 이미지 1장씩.
- 초당 2세트 = 1초에 약 36cm 구간을 두 번 촬영 → **현재 하드웨어(2.5fps 병목) 기준 최대 성능**.

---

## 2. 500ms 윈도우 세트 구성 로직 (요구사항 반영)

### 2.1 시간 기준: 스트림 시간 + wall-clock 500ms 상한 (채택)

- **주 동작**: 윈도우는 **스트림 시간**(버퍼에 쌓인 프레임의 timestamp)으로만 정의.
  - 현재 스트림 윈도우: `[T_cur, T_cur + 0.5)` (초 단위).
  - 버퍼에서 `T_cur <= frame['timestamp'] < T_cur + 0.5` 인 프레임만 사용. 4 cam 모두 1장 이상 있으면 세트 형성 → 처리 후 `T_cur += 0.5` 로 진행.
- **상한**: **wall-clock 500ms**를 “최대 기다리는 값”으로 사용.
  - 마지막으로 세트를 처리한 wall-clock 시각 `t_last_set` 유지.
  - **트리거 1 (우선)**: 현재 스트림 윈도우 `[T_cur, T_cur+0.5)`에 4 cam 프레임이 모두 모이면 → 즉시 세트 형성·처리, `T_cur += 0.5`, `t_last_set = time.time()`.
  - **트리거 2 (상한)**: `time.time() - t_last_set >= 0.5` 이면 → 아직 4 cam이 안 모였어도 **스트림 윈도우만 진행** (`T_cur += 0.5`). 해당 윈도우는 세트 미완성으로 두고, actual_sets 에는 포함하지 않음. (예외 처리에서 “일부 카메라 미수신”과 동일하게 처리 가능.)
- **효과**: RPI가 나중에 더 빠르면(예: 5fps) 4장이 200ms 만에 모일 수 있음 → 500ms를 기다리지 않고 바로 처리해 지연 감소. 한쪽 카메라가 멈추면 500ms 후 윈도우만 진행해 블로킹 방지.
- **정리**: “스트림 시간 500ms 구간”은 유지하고, “기계적으로 wall-clock 500ms 대기”는 하지 않음. 4장 모이면 바로 처리; 500ms는 최대 대기만 담당.

### 2.2 세트 생성 알고리즘 (한 500ms 스트림 구간)

입력: `start_ts`, `end_ts` (= T_cur, T_cur + 0.5)

1. **구간 내 프레임 수집**  
   카메라별로 버퍼에서 `start_ts <= frame['timestamp'] < end_ts`인 항목만 수집.  
   - USB_LOCAL: 많음(예: ~25).  
   - RPI_USB1/2/3: 1~2장.

2. **RPI 프레임 없으면**  
   → 세트 불가, `None` 반환.

3. **카메라별 대표 프레임 선택**  
   - **RPI_USB1, RPI_USB2, RPI_USB3**: 구간 내 수집된 프레임이 여러 개면 **중간 인덱스** 것 선택 (`len//2`); 1개면 그대로.  
   - **USB_LOCAL**: RPI 3대표의 timestamp 평균 `rpi_avg`에 대해, 구간 내 USB 프레임 중 `|ts - rpi_avg|`가 최소인 1장 선택.

4. **4장 모두 선택되면**  
   → `{ cam_id: (frame, timestamp), ... }` 형태의 세트 반환.  
   하나라도 없으면 `None`.

5. **버퍼에서 제거**  
   세트로 선택된 4장만 해당 카메라 deque에서 제거(중복 제거, 한 cam당 1장).

### 2.3 예외: 구간 내 일부(또는 복수) 카메라가 프레임을 못 보낸 경우

- **정책**: 해당 스트림 윈도우에서는 **세트를 만들지 않음** (4 cam 미만이면 `extract_set_for_interval` 이 `None` 반환).
- **동작**:
  1. **스트림 윈도우 진행**: `T_cur += 0.5` 로 다음 구간으로 넘어감. (이미 “4장 모이면 즉시 처리” + “500ms 상한” 로직에서, 500ms 상한 시에도 T_cur 를 진행하기로 했으므로 일관됨.)
  2. **버퍼 정리 (권장)**: 해당 구간 `[T_cur, T_cur+0.5)` 에 속한 프레임은 **다음 윈도우에 재사용하지 않도록** 버퍼에서 제거. 그렇지 않으면 한 카메라만 계속 지연될 때 같은 “과거” 프레임이 여러 윈도우에 걸쳐 남아 의미가 애매해짐. 제거 시: `remove_frames_in_interval(start_ts, end_ts)` 같은 API로 구간 내 모든 항목 삭제.
  3. **로깅**: 해당 윈도우에서 어떤 카메라가 비었는지 기록 (예: `FRAME_SET_INCOMPLETE` 이벤트에 `window: [start_ts, end_ts]`, `missing_cameras: [...]`). 모니터/디버깅용.
- **actual_sets**: 이 윈도우는 세트를 만들지 않았으므로 0으로 집계. Efficiency는 그대로 actual/theoretical 로 계산.
- **다운스트림(비전)**: 3 cam만 있는 “부분 세트”는 **YOLO/매칭에 넣지 않음** (스킵). 매칭/API가 4 cam 전제이므로 불완전한 세트로 비전 처리하면 오동작 가능성이 큼.
- **다운스트림(시간 기반)**: 세트를 스킵했어도 컨베이어는 움직이므로 **이동 거리만 시간 기반으로 갱신**한다. 기준 시각은 **500ms 고정이 아니라 “현재 스트림 시각”**으로 둔다. 즉 `now_s = T_cur + 0.5`(스킵한 윈도우의 끝)를 쓰고, `elapsed = now_s - start_time` 으로 계산. 직전 처리 시각과의 차이(Δ)가 묵시적으로 반영된다: 마지막 처리 시각이 T_cur 였다면 이번에는 T_cur+0.5 이므로 Δ=0.5; 마지막이 T_cur-0.5 였다면 Δ=1.0 등. 즉 **고정 500ms가 아니라, “그 시점(now_s)까지 흐른 시간”**으로 rem_dist 를 갱신한다.

### 2.4 세트 처리(다운스트림)

- 세트가 생성되면 **라우트 순서**(예: USB_LOCAL → RPI_USB1 → RPI_USB2 → RPI_USB3)대로 각 `(cam, frame, ts)`에 대해 기존 `process_one_frame(cam, frame, ts)` 호출.
- `last_consumed_ts`, resolve_pending, API 등은 기존과 동일하게 유지.
- **T_cur 초기값**: 버퍼에 프레임이 처음 들어온 시점에서 “버퍼 내 최소 timestamp”를 T_cur 로 두거나, 첫 세트 형성 시 그 구간의 start_ts 를 T_cur 로 사용하면 됨. 이후에는 세트 처리할 때마다 `T_cur += 0.5`.

---

## 3. 구현 계획 (파일/모듈별)

### 3.1 버퍼: `ingest/time_ordered_buffer.py`

- **유지**: `put()`, `get_stats_and_reset()`, `buffer_lengths()`, `get_all_cam_ids()`.
- **추가**:
  - `get_frames_in_interval(start_ts, end_ts)`  
    - 락 하에, 카메라별 deque를 순회하며 `start_ts <= item['timestamp'] < end_ts`인 항목만 모음.  
    - 반환: `{ cam_id: [ {"frame": ndarray, "timestamp": float}, ... ] }` (제거하지 않음).
  - `remove_frame(cam_id, timestamp)`  
    - 해당 cam의 deque에서 주어진 `timestamp`와 일치하는 **첫 번째** 항목 1개만 제거.  
    - (해당 deque 순회하며 popleft/재적재로 구현.)
  - **선택**: `extract_set_for_interval(start_ts, end_ts)`  
    - 위 수집 + 대표 선택 로직(RPI 중간, USB는 RPI 평균에 가장 가까운 것)을 버퍼 내부 또는 별도 헬퍼에서 수행 후, 선택된 4장에 대해 `remove_frame` 호출하고 세트 `{ cam_id: (frame, ts), ... }` 반환.  
    - 한 cam이라도 구간 내 프레임 없으면 `None` 반환, 제거 없음.
  - **예외 처리용**: `remove_frames_in_interval(start_ts, end_ts)`  
    - 구간 내 모든 프레임을 카메라별 deque에서 제거. 세트 미완성 윈도우를 넘길 때 해당 구간 프레임을 버퍼에서 비우기 위해 사용.

- **제거/변경**:  
  - 시간순 1장씩 소비하는 **`get_oldest()`** 는 “500ms 세트 모드”에서는 사용하지 않음.  
  - 기존 호출부를 세트 기반으로 바꾸면 제거 가능. (다른 모드/테스트용으로 남겨둘지는 선택.)

### 3.2 메인 루프: `main.py`

- **시간순 버퍼 사용 시 동작 변경** (스트림 시간 + wall-clock 500ms 상한):
  - **기존**: 매 반복에서 `peek_oldest_per_cam()` → FRAME_SET_* 로그 → `get_oldest()` → 1장 처리.
  - **변경**:
    1. **스트림 윈도우**: `T_cur` 유지 (초 단위). 초기값은 버퍼 최소 timestamp 또는 첫 세트 시 start_ts.
    2. **wall-clock 상한**: `t_last_set = time.time()` (마지막 세트 처리 시각). 매 루프에서 `time.time() - t_last_set >= 0.5` 인지 확인.
    3. **매 루프**:
       - **우선**: `extract_set_for_interval(T_cur, T_cur + 0.5)` 호출. 4 cam 세트가 반환되면 → 처리, `sets_formed_this_second += 1`, `T_cur += 0.5`, `t_last_set = time.time()`.
       - **상한**: 세트가 없는데 `time.time() - t_last_set >= 0.5` 이면 → 해당 윈도우 포기: **time-based position update** 실행(TRACKING/PENDING master들의 rem_dist·api_update_position), `remove_frames_in_interval(T_cur, T_cur + 0.5)` 호출(버퍼 정리), `T_cur += 0.5`, `t_last_set = time.time()`. (선택: FRAME_SET_INCOMPLETE 로그에 missing_cameras 등 기록.)
    4. **세트 처리**: 라우트 순서로 4개 `(cam, img, ts)`에 대해 `process_one_frame(cam, img, ts)` 호출.
    5. **1초마다 FRAME_STATS**  
       - `theoretical_max_sets`: **2** (고정).  
       - `actual_sets_created`: 방금 리셋 전까지의 `sets_formed_this_second`.  
       - `efficiency`: `actual_sets_created / 2` (0 ~ 1 또는 0% ~ 100%로 저장).

- **로그**:
  - FRAME_SET_COMPLETE / SPREAD / INCOMPLETE 같은 **peek 기반** 이벤트는 제거하거나, 500ms 세트가 실제로 생성된 시점에만 "FRAME_SET_COMPLETE" 한 번 로깅하도록 변경 가능.
  - FRAME_STATS 필드: `theoretical_max_sets` = 2, `actual_sets_created`, `efficiency`(실제/2) 명확히.

### 3.3 설정: `config.py`

- **추가 권장**:
  - `WINDOW_SET_INTERVAL_SEC = 0.5` (500ms).
  - `THEORETICAL_MAX_SETS_PER_SEC = 2` (상수 또는 설정).
- 기존 `USE_TIME_ORDERED_BUFFER`가 True일 때 위 “500ms 세트 모드”를 사용할지,  
  또는 `USE_500MS_WINDOW_SET = True` 같은 플래그로 분리할지 결정 가능.

### 3.4 모니터: `monitoring/frame_sync_monitor.py`

- **입력**: 기존과 동일하게 `frame_sync_events.jsonl`의 `FRAME_STATS` 이벤트.
- **변경**:
  - `theoretical_max_sets`: 로그에 2로 기록되므로, 모니터는 그대로 읽어서 표시 (또는 2 고정 표기).
  - `actual_sets_created`: 0/1/2 단위로 표시.
  - `efficiency`: `(actual_sets_created / theoretical_max_sets) * 100` → 최대 100%.
- **출력 문구**:  
  - "Theoretical max sets: 2.0/sec"  
  - "Actual sets created: N/sec" (N은 0, 1, 2 근처)  
  - "Efficiency: N%" (0~100%).

---

## 4. 메트릭 요약 (최종)

| 메트릭 | 의미 | 예시 |
|--------|------|------|
| Camera FPS | 카메라별 초당 수신 프레임 수 | USB_LOCAL 48.7, RPI_USB1 2.5 |
| Bottleneck | FPS 최소인 카메라 | RPI_USB1 |
| Theoretical max sets | 초당 최대 세트 수 (500ms×2) | 2.0/sec |
| Actual sets created | 1초 동안 실제 완성한 500ms 세트 수 | 0, 1, 2 |
| Efficiency | actual / theoretical | 0%, 50%, 100% |
| Quarter (250ms) | 보조: 해당 250ms에 4 cam 모두 수신 여부 | 선택 유지 |

---

## 5. 대안/개선 아이디어 (참고)

- **스트림 시간 + wall-clock 500ms 상한**:  
  사용자 제안대로 채택함. 스트림 윈도우로 “4장 모이면 즉시 처리”하고, 500ms는 “최대 기다리는 값”만 담당. RPI 속도 개선 시에도 불필요한 대기 없음.

- **대표 선택**:  
  - RPI: 중간 인덱스 대신 “구간 내 RPI 3장의 평균 timestamp에 가장 가까운 RPI 프레임”을 쓰는 방식도 가능.  
  - USB: 현재처럼 “RPI 3대표의 평균에 가장 가까운 USB 1장” 유지.

- **sync_quality**:  
  - 세트 내 4장의 timestamp spread (max - min)를 계산해 로그/모니터에 남기면, 나중에 동기 품질 분석에 유용.

---

## 6. 구현 순서 제안

1. **버퍼**: `get_frames_in_interval`, `remove_frame`, `remove_frames_in_interval` 구현 → 검증 후 `extract_set_for_interval` 구현.
2. **main.py**: `T_cur`, `t_last_set`, 스트림 윈도우 + 500ms 상한 루프, `extract_set_for_interval` / `remove_frames_in_interval` 호출, 세트 처리, `sets_formed_this_second`와 FRAME_STATS 수정. **미완성 윈도우 시**: 비전은 스킵하고 **time-based position update**만 수행(TRACKING/PENDING master에 대해 rem_dist·api_update_position), 버퍼 정리, FRAME_SET_INCOMPLETE 로그 기록.
3. **config.py**: `WINDOW_SET_INTERVAL_SEC = 0.5`, `WINDOW_MAX_WAIT_WALL_SEC = 0.5` 등 상수 추가.
4. **frame_sync_monitor.py**: theoretical=2 반영, actual/efficiency 표시 수정.
5. **정리**: 기존 peek 기반 FRAME_SET_* 로그 정리 및 문서 업데이트.

---

## 7. 의견 요약 (구현 방향 결정)

- **스트림 시간 + wall-clock 500ms 상한**: 제안하신 대로 반영함. 4장 모이면 즉시 처리하고, 500ms는 “최대 기다리는 값”만 씀. RPI가 빨라져도 불필요한 대기 없고, 한쪽이 멈추면 500ms 후 윈도우만 진행해 블로킹을 막음.
- **예외(일부 카메라 미수신)**: 해당 윈도우는 **세트 미완성**으로 두고, 비전(YOLO/매칭)은 스킵. 스트림 윈도우만 진행(`T_cur += 0.5`), 구간 내 프레임은 버퍼에서 제거, 누락 카메라는 로그. 그 대신 **시간 기반 이동 거리 갱신**만 수행: TRACKING/PENDING master에 대해 `rem_dist = total_dist - elapsed*BELT_SPEED`, `api_update_position` 호출 → UI/백엔드에 “그 구간만큼 이동했다”를 반영.

이 계획대로 구현하면 “500ms당 1세트, 초당 최대 2세트”라는 하드웨어 한계와 일치하고, Actual sets / Efficiency가 의미 있는 지표가 됨.

---

## 8. 전체 로직 점검 및 최종 구현 계획

### 8.1 시간 기준 정리 (position update)

- **세트 처리 시**: `process_one_frame(cam, img, ts)` 에서 `time_s = ts` 로 쓰므로, position update 의 `elapsed = time_s - start_time` — 직전 처리와의 차이가 아니라 “그 프레임 시각까지 흐른 시간”.
- **스킵 시**: `now_s = T_cur + 0.5` (스트림 상 “현재 시각”). `elapsed = now_s - start_time` 으로 rem_dist 갱신. **500ms 고정이 아님**: 직전 처리 스트림 시각이 T_cur 였다면 이번 Δ=0.5초, 그 전이 T_cur-0.5 였다면 Δ=1.0초 등으로 자동 반영됨.

### 8.2 PENDING / DISAPPEAR 논리 — 4 cam 부족으로 인한 허위 없음

- **처리 단위**: 비전 처리(YOLO/매칭)는 **완성된 4 cam 세트**가 있을 때만 수행하고, 세트 내 4장을 **라우트 순서**(USB_LOCAL → RPI_USB1 → RPI_USB2 → RPI_USB3)로 연속 호출.
- **PENDING**: 한 cam 프레임에서 물체가 사라졌을 때만 PENDING 설정. 그 직후 **같은 세트**에서 다음 cam 3장을 바로 처리하므로, 실제로 다음 cam에 나타나면 같은 세트 안에서 매칭되어 TRACKING 유지. “한 cam만 처리하고 다음 cam 프레임이 아직 안 와서” PENDING 에 머무르는 상황은 **없음** (다음 cam 프레임은 같은 윈도우에 이미 있음).
- **resolve_pending**: 세트 처리 중 한 프레임의 `time_s` 로 호출. Phase 2 에서 “이미 next_cam 에서 매칭됐으면 DISAPPEAR 하지 않음” 이 있으므로, 같은 세트에서 다음 cam 처리 후 resolve_pending 이 호출되면 이미 uids[next_cam] 이 채워져 있어 허위 DISAPPEAR 가 나지 않음.
- **스킵 윈도우**: 비전을 전혀 하지 않으므로 resolve_pending 도 호출하지 않음. PENDING 인 물체는 다음 **완성 세트**가 처리될 때까지 유지되고, 그때 다시 판단. 따라서 “4개 카메라 이미지가 빠져서 PENDING/disappeared 되는 현상”은 **없음**.

### 8.3 불필요한 것 정리

| 항목 | 조치 |
|------|------|
| 매 루프 `peek_oldest_per_cam()` + FRAME_SET_COMPLETE/SPREAD/INCOMPLETE 로그 | **제거**. 세트 실제 생성/스킵 시점에만 로그 (FRAME_SET_COMPLETE 1회, FRAME_SET_INCOMPLETE + missing_cameras). |
| `get_oldest()` 호출 루프 | **제거**. 500ms 세트 모드에서는 `extract_set_for_interval` + 세트 처리만 사용. |
| `complete_count_this_second` (peek 기반) | **제거**. 대신 `sets_formed_this_second` (실제 세트 처리 횟수)만 사용. |
| 버퍼 `get_oldest()` | **유지 또는 제거**. 세트 모드에서만 쓸 경우 제거해도 됨. 테스트/레거시용으로 남기려면 유지. |
| Phase 4 (stale, ts_ahead) | **유지**. process_one_frame 내부에서 여전히 유효. |
| Quarter (250ms) 집계 | **유지**. get_stats_and_reset() 로 FPS/quarter 는 그대로 두고, FRAME_STATS 에 actual/theoretical 만 정리. |

### 8.4 최종 구현 계획 (승인 후 적용)

1. **버퍼 (`time_ordered_buffer.py`)**
   - `get_frames_in_interval(start_ts, end_ts)` 추가.
   - `remove_frame(cam_id, timestamp)` 추가.
   - `remove_frames_in_interval(start_ts, end_ts)` 추가.
   - `extract_set_for_interval(start_ts, end_ts)` 추가 (수집 + RPI 중간/USB RPI평균 근접 선택 + 4장 제거 후 세트 반환).
   - `get_min_timestamp()` 또는 peek_oldest_per_cam 활용해 “버퍼 내 최소 ts” 반환 (T_cur 초기화용). 필요 시 유지.

2. **main.py**
   - **제거**: 시간순 분기 내 peek_oldest_per_cam → FRAME_SET_* 로그 → get_oldest() → process_one_frame 1회.
   - **추가/변경**:
     - `T_cur`: 초기값 = 버퍼에 데이터 있을 때 버퍼 최소 timestamp (한 번만); 없으면 대기.
     - `t_last_set = time.time()` (마지막 세트 처리 wall-clock).
     - `sets_formed_this_second` (1초마다 리셋).
     - 매 루프: `T_cur` 가 아직 없으면 버퍼에서 최소 timestamp 취해 설정; 버퍼 비어 있으면 sleep 후 계속.  
       (1) `set_ = frame_sink.extract_set_for_interval(T_cur, T_cur + 0.5)`.  
       (2) set_ 있으면: 라우트 순서로 4회 `process_one_frame`, `sets_formed_this_second += 1`, `T_cur += 0.5`, `t_last_set = time.time()`, (선택) FRAME_SET_COMPLETE 로그.  
       (3) set_ 없는데 `time.time() - t_last_set >= 0.5`: **time_based_position_update(now_s=T_cur+0.5)** 호출, `remove_frames_in_interval(T_cur, T_cur+0.5)`, `T_cur += 0.5`, `t_last_set = time.time()`, FRAME_SET_INCOMPLETE 로그 (missing_cameras 등).  
       (4) 1초마다 FRAME_STATS (theoretical_max_sets=2, actual_sets_created=sets_formed_this_second, efficiency).
   - **time_based_position_update(now_s)**: process_one_frame 의 “Distance / position API” 블록만 추출한 함수. TRACKING/PENDING master 에 대해 `elapsed = now_s - start_time`, `rem_dist`, `api_update_position` 호출.

3. **config.py**
   - `WINDOW_SET_INTERVAL_SEC = 0.5`, `WINDOW_MAX_WAIT_WALL_SEC = 0.5` 추가.

4. **frame_sync_monitor.py**
   - FRAME_STATS 기준으로 theoretical=2, actual_sets_created, efficiency(0~100%) 표시만 유지/정리.

5. **테스트**
   - 4 cam 세트 처리 시 PENDING → 다음 cam 에서 매칭되면 TRACKING 유지 확인.
   - 스킵 시 time_based_position_update 만 호출되고 resolve_pending 미호출 확인.
   - FRAME_STATS 에 actual 0/1/2, efficiency 의미 있게 나오는지 확인.
