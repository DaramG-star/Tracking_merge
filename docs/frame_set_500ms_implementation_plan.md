# 500ms 구간 기반 프레임 세트 구성 — 이해 및 구현 계획

## 1. 잘못된 계산의 원인 (정정)

### 왜 "Actual sets: 52.8/sec", "Efficiency: 2640%"가 나왔는가

- **현재 로직**: 매 **프레임 1장 소비**할 때마다 `peek_oldest_per_cam()`으로 4 cam 있으면 `FRAME_SET_COMPLETE` 로그 + `complete_count_this_second += 1`.
- **의미**: “1초 동안 버퍼에 4 cam이 모두 있었던 **소비 스텝**” 횟수 ≈ 초당 소비 프레임 수(USB 48fps 수준)에 가깝게 나옴.
- **실제 “세트”**: 4개 카메라에서 **각 1장씩**, **같은 시간 구간(500ms)**에 해당하는 프레임을 골라 **1묶음**으로 만든 것이 “1세트”.
- 따라서 **52.8은 “세트 수”가 아니라 “4 cam 있는 소비 스텝 수”**라서, 실제 세트 수(이론상 최대 2~2.5/sec)와 효율(%)을 이걸로 나누면 말이 안 됨.

**정리**:  
- **Theoretical max sets = 2.0/sec** → 올바름 (병목 2.5fps → 초당 2.5세트 상한).  
- **Actual sets created**는 “500ms 구간당 실제로 만든 **세트 개수**”로 재정의해야 함.  
- **Efficiency** = actual_sets_created / theoretical_max_sets (최대 100% 근처).

---

## 2. 목표 동작 (이해한 내용)

### 2.1 현실적인 처리 능력

- 병목: RPI 쪽 ~2.5fps → **초당 최대 약 2.5개 세트**.
- 즉 **500ms당 1개 세트**가 최대에 가까운 처리 단위.
- 500ms 동안 벨트 이동 ≈ 18cm → “18cm 구간을 4 cam으로 한 번 촬영” = 1세트.

### 2.2 “1세트”의 정의

- **시간 구간**: 500ms (예: 전반 0~500ms, 후반 501~1000ms).
- **세트 구성**:
  1. 구간 [t_start, t_end) 안에 들어온 프레임만 사용.
  2. **RPI 3대**: 구간 내 프레임이 있으면, 각 카메라당 **1장** 선택 (여러 장이면 “중간” 등 대표 1장).
  3. **USB_LOCAL**: RPI 3장의 timestamp 평균에 **가장 가까운** 1장을 구간 내에서 선택.
  4. 4장이 모두 골라지면 → **1세트 완성** → 이 4장을 시간순으로 처리.

### 2.3 지표 정의 (수정 후)

- **frame_counts (fps)**: 구간별로 버퍼에 **put된** 프레임 수 → 그대로 유지 (cam별 수신률).
- **theoretical_max_sets**: min(cam별 fps) = 병목 fps → 그대로.
- **actual_sets_created**: “해당 1초 안의 **500ms 창 2개** 중, **세트를 실제로 만든 횟수**” (0 / 1 / 2).
- **efficiency**: actual_sets_created / theoretical_max_sets (최대 100% 근처).

---

## 3. 구현 계획 (요약)

### 3.1 처리 모델 변경

- **현재**: `get_oldest()` 1장씩 소비 → 매 스텝마다 4 cam 있으면 COMPLETE 카운트 (→ 잘못된 actual/efficiency).
- **변경**:  
  - **500ms 단위 창**을 두고,  
  - 각 창에서 “구간 내 프레임만 모아 1세트 구성” →  
  - 세트가 만들어지면 그 4장만 **시간순**으로 처리하고,  
  - “actual_sets_created”는 **세트를 만든 500ms 창 개수**로만 집계.

### 3.2 버퍼/데이터 구조

- **필요 능력**:  
  - 구간 [t_start, t_end)에 해당하는 프레임을 **cam별로** 조회.  
  - 세트로 선택한 프레임은 버퍼에서 **제거** (중복 처리 방지).
- **선택지**:
  - **A**: 기존 `TimeOrderedFrameBuffer`를 확장  
    - put 시 `received_at = time.time()` 저장.  
    - `get_frames_in_interval(cam, t_start, t_end)` → 해당 구간 (received_at 기준)의 (frame, ts) 목록 반환.  
    - `take_set(t_start, t_end)`에서 위 목록으로 “1 cam당 1장” 선택 후, 선택된 항목만 버퍼에서 제거하고 반환.
  - **B**: 500ms 창을 **버퍼 시간이 아닌 wall clock**으로 고정  
    - 예: `t_start = ref_time + n * 0.5`, `t_end = t_start + 0.5`.  
    - ref_time = 프로세스 시작 시각 또는 첫 put 시각.
- **제안**:  
  - 창은 **wall clock** 500ms 단위 (구현 단순, “지난 0.5초” 의미 명확).  
  - 버퍼 항목에 `(frame, ts, received_at)` 저장, 구간 필터는 `received_at` 기준.

### 3.3 세트 구성 알고리즘 (제안)

- 입력: `frames_in_interval[cam]` = 구간 [t_start, t_end) 내 (frame, ts) 리스트.
- RPI 3 cam 각각:
  - 리스트 비어 있으면 → 세트 불가, 반환 None.
  - 비어 있지 않으면: **중간 인덱스** 1장 선택 (예: `frames[len(frames)//2]`).
- RPI 3장의 timestamp 평균 `t_rpi_avg` 계산.
- USB_LOCAL:
  - 리스트 비어 있으면 → None.
  - 비어 있지 않으면: `argmin |ts - t_rpi_avg|` 인 1장 선택.
- 반환: `{cam_id: (frame, ts), ...}` 4개.  
  - 이 4장에 대해 버퍼에서 해당 항목 제거 후, **ts 오름차순**으로 `process_one_frame` 호출.

### 3.4 메인 루프 (개념)

- 시작 시 `ref_time = time.time()` 저장.
- 루프:
  - 현재 시각 기준 **다음 500ms 경계**까지 sleep (또는 경계 도달 시까지 다른 작업).
  - `window_start = ref_time + n * 0.5`, `window_end = window_start + 0.5` (n = 0, 1, 2, ...).
  - `set_frames = frame_sink.take_set(window_start, window_end)` (위 알고리즘 + 버퍼 제거).
  - `set_frames`가 4개면: ts 순 정렬 후 각각 `process_one_frame(cam, img, ts)` 호출;  
    해당 1초 구간의 `actual_sets_created` += 1.  
  - 4개 미만이면: INCOMPLETE 로그만 (actual_sets_created 증가 없음).
- **1초마다**:  
  - 기존처럼 `get_stats_and_reset()`으로 frame_counts(수신 fps), quarter 등 수집.  
  - **actual_sets_created** = 방금 정의한 “500ms 창당 세트 성공 횟수”만 사용.  
  - FRAME_STATS에 theoretical_max_sets, actual_sets_created, efficiency(actual/theoretical) 기록.

### 3.5 버퍼 정리

- `take_set(t_start, t_end)` 시:
  - 구간 내에서 “세트로 선택된” 프레임만 제거.
  - 구간 **이전** (ts 또는 received_at < t_start) 프레임은 “이미 지난 창”이므로 제거해도 됨 → 메모리 상한 유지 (선택).

### 3.6 기존 FRAME_SET_* 로그

- 500ms 창 단위로 바꾼 뒤에는:
  - “세트 완성” = 500ms 창에서 4장 골라서 처리한 경우 1번만 카운트.
  - FRAME_SET_COMPLETE/INCOMPLETE/SPREAD는 “창 단위 결과”에 맞게 재정의하거나, FRAME_STATS의 actual_sets_created / quarters로 대체 가능.

---

## 4. 제가 이해한 “확인 사항” 요약

- 500ms 단위로 **1세트** (4 cam 각 1장, 구간 내에서 선택).
- 이론적 최대 = 병목 fps (예: 2.5) → 초당 2.5세트, **실제 세트 수**는 그 이하 (0~2).
- Actual sets created = “실제로 만든 세트 수” (500ms 창당 0 또는 1).
- Efficiency = actual / theoretical, 최대 100% 근처.
- 2640% 같은 값은 나오지 않도록, “세트” 정의를 위와 같이 엄격히 둠.

---

## 5. 구현 순서 제안

1. **버퍼 확장**  
   - put 시 `received_at` 저장.  
   - `get_frames_in_interval(cam, t_start, t_end)` (읽기만).  
   - `take_set(t_start, t_end)`: 위 세트 알고리즘으로 4장 선택 → 선택분만 제거 후 반환.

2. **메인 루프**  
   - 500ms 창(ref_time + n*0.5) 루프로 변경.  
   - take_set → 4장이면 ts 순으로 process_one_frame 4번, actual_sets_created += 1.

3. **FRAME_STATS**  
   - actual_sets_created를 “해당 1초 내 500ms 창 중 세트 성공 횟수”로만 집계.  
   - efficiency = actual_sets_created / theoretical_max_sets.

4. **모니터**  
   - FRAME_STATS만 읽어서 FPS, 병목, actual/theoretical, efficiency 출력 (기존 보고 형식 유지).

5. **테스트**  
   - RPI 2.5fps 환경에서 actual ≤ 2~3, efficiency ≤ 100% 근처인지 확인.

---

## 6. 열어둔 점 (선택 사항)

- 500ms 창을 **wall clock**으로 할지, **첫 프레임 ts 기준**으로 할지 (제안: wall clock).
- 구간 이전 프레임 버퍼 삭제 정책: 매 take_set 시 ts < t_start 제거할지, maxlen만 둘지.

이 계획대로 구현하면 “Actual sets”와 “Efficiency”가 의도한 의미와 일치할 것입니다.  
수정·보완할 부분 있으면 알려 주시면 반영한 뒤, 승인해 주시면 그때 코딩하겠습니다.
