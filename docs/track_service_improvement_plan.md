# Track 서비스 로직 개선 계획서

## 1. 개요

- **목적**: disappeared 오판 방지, 멀티 카메라 tracking 안정성 확보.
- **범위**:
  - `track/ingest/frame_aggregator.py` (버퍼 확장 또는 신규 `TimeOrderedFrameBuffer`)
  - `track/main.py` (메인 루프: 시간순 소비, resolve_pending 재확인, 예외 처리)
  - `track/logic/matcher.py` (resolve_pending 재확인, optional extra margin)
  - `track/config.py` (PENDING_EXTRA_MARGIN, optional DISAPPEAR/health 설정)
  - 신규: `track/logic/track_state.py` (선택, confidence·예측용)
- **예상 작업 기간**: 6.5~8시간 (Phase 1~4 + 테스트).

---

## 2. 개선 1: 프레임 시간순 처리

### 2-1. 선택한 방법

- **선택: 방법 A (카메라별 버퍼 + timestamp 기준 소비)**  
- **선택 근거**:
  1. **기존 API 유지**: 현재 `FrameAggregator.put(cam_id, frame, ts)`가 ZMQ/USB 콜백에서 호출됨. 방법 A는 `put()`을 “버퍼에 append”로만 바꾸면 되어, **콜백 시그니처·스레드 구조 변경 없음**.
  2. **단일 소비자**: 메인 루프 하나가 “가장 오래된 프레임”만 꺼내 처리하면 되므로, **방법 B의 “풀에 모은 뒤 정렬”과 동일한 시간순**을 얻으면서도, 풀을 위한 별도 스레드/동기화가 필요 없음.
  3. **메모리 예측 가능**: 카메라당 `maxlen=N`으로 상한 고정 → 풀 overflow·무한 성장 위험 없음.
  4. **디버깅**: 카메라별 버퍼 상태(길이, 최소/최대 ts) 로깅이 쉬움.

방법 B(단일 풀 + 정렬)는 “모든 cam 프레임을 한 큐에 넣고 ts 정렬”이 필요해, 넣는 쪽이 4개 스레드일 때 동기화·정렬 비용이 크고, 풀 크기 상한을 정하기도 애매함.

### 2-2. 구현 계획

#### 데이터 구조

- **버퍼**: 카메라별 `collections.deque(maxlen=N)` 사용. N은 설정값(예: 30~60).
- **프레임 항목**: `(timestamp: float, frame: np.ndarray)` 또는 `dict`로 통일.

```python
# track/ingest/frame_aggregator.py 확장 (또는 track/ingest/time_ordered_buffer.py 신규)

from collections import deque
import threading
from typing import Optional, Tuple, List, Dict, Any
import numpy as np

class TimeOrderedFrameBuffer:
    """카메라별 프레임 버퍼. timestamp 기준 가장 오래된 프레임을 한 장씩 소비."""

    def __init__(self, cam_ids: List[str], maxlen_per_cam: int = 60):
        self._lock = threading.Lock()
        self._buffers: Dict[str, deque] = {
            cid: deque(maxlen=maxlen_per_cam) for cid in cam_ids
        }
        self._cam_ids = cam_ids

    def put(self, cam_id: str, frame: np.ndarray, timestamp: float) -> None:
        if cam_id not in self._buffers:
            return
        with self._lock:
            self._buffers[cam_id].append({
                "frame": frame.copy() if frame is not None else None,
                "timestamp": timestamp,
            })

    def get_oldest(self) -> Optional[Tuple[str, np.ndarray, float]]:
        """모든 버퍼 중 timestamp가 가장 작은 (cam_id, frame, ts) 반환. 소비(제거)함."""
        with self._lock:
            candidates = []
            for cid in self._cam_ids:
                buf = self._buffers[cid]
                if not buf:
                    continue
                head = buf[0]
                if head["frame"] is None:
                    buf.popleft()
                    continue
                candidates.append((head["timestamp"], cid, head))
            if not candidates:
                return None
            candidates.sort(key=lambda x: x[0])
            ts, cid, head = candidates[0]
            self._buffers[cid].popleft()
            return (cid, head["frame"], ts)

    def get_all_cam_ids(self) -> List[str]:
        return list(self._cam_ids)
```

- **호환**: 기존 `FrameAggregator`를 그대로 두고, **설정 플래그**(예: `config.USE_TIME_ORDERED_BUFFER = True`)일 때만 `TimeOrderedFrameBuffer`를 사용하도록 하면, 롤백이 쉬움.

#### 처리 플로우

1. 각 카메라(ZMQ/USB)에서 프레임 수신 → `put(cam_id, frame, ts)`로 해당 cam 버퍼에만 append.
2. 메인 루프: `get_oldest()` 한 번 호출 → (cam_id, frame, ts) 또는 None.
3. None이면 `time.sleep(0.01)` 후 다시 시도.
4. 반환받으면: 해당 `cam_id`에 대해 `CAM_SETTINGS[cam_id]`, `detector.get_detections(img, cfg, cam_id)`, `active_tracks[cam_id]` 갱신, `matcher.try_match(..., time_s)`, PENDING 설정, **resolve_pending(mid, time_s)** (현재 프레임의 `time_s` 사용).
5. 다음 반복에서 다시 `get_oldest()`.

#### main.py 변경 pseudo-code

```python
# main.py: aggregator 대신 buffer 사용 시

buffer = TimeOrderedFrameBuffer(config.TRACKING_CAMS, maxlen_per_cam=60)

# ZMQ/USB 콜백은 buffer.put(cam_id, frame, ts) 호출로 동일

while _running:
    item = buffer.get_oldest()
    if item is None:
        time.sleep(0.01)
        if args.display:
            _handle_display()
        continue

    cam, img, ts = item
    time_s = ts
    cfg = config.CAM_SETTINGS.get(cam)
    if not cfg:
        continue

    detections = detector.get_detections(img, cfg, cam)
    new_active = {}
    # ... (기존과 동일: detection → best_uid, try_match, new_active, PENDING 설정)

    for mid in list(matcher.masters.keys()):
        result = matcher.resolve_pending(mid, time_s)
        if result:
            # ... PICKUP / DISAPPEAR 처리 (기존 + 재확인 로직)
    # ... distance API, video, display
    active_tracks[cam] = new_active

    if args.display:
        _handle_display()
    time.sleep(0.001)  # CPU 절약
```

### 2-3. 예상 문제 및 해결

| 문제 | 해결 방안 |
|------|-----------|
| 특정 카메라만 프레임이 계속 들어와 다른 cam 버퍼가 비어 있음 | `get_oldest()`는 “버퍼에 있는 cam 중 가장 오래된 것”만 반환. 한 cam만 있어도 그 cam 프레임은 처리됨. 다만 한 cam이 장시간 끊기면 그 cam 구간의 PENDING은 다른 cam의 ts로 resolve됨 → 아래 “stale frame” 처리 적용. |
| 너무 오래된 프레임(stale)이 버퍼에 남아 있음 | `get_oldest()` 반환 후, `time_s`가 “현재 시각”보다 일정 초(예: 30초) 이상 과거면 **resolve_pending에 사용하지 않거나**, 해당 프레임만 detection/tracking만 하고 **resolve_pending(mid, time_s) 호출은 skip**하여, 오래된 ts로 DISAPPEAR가 나오지 않게 함. |
| 버퍼 overflow | `maxlen_per_cam`으로 상한. 오래된 프레임은 자동 popleft되어, 가장 오래된 것만 유지되는 것이 아니라 “최근 N개”만 유지됨. 따라서 **지연이 큰 cam**은 오래된 프레임이 버려질 수 있음 → 해당 cam health를 로깅하고, “버퍼 드롭률” 모니터링 권장. |
| latency 증가 | “가장 오래된” 프레임을 처리하므로, 실시간과 비교해 최대 N프레임×평균 프레임 간격만큼 지연 가능. N=60, 20fps면 이론상 최대 약 3초. 실제로는 여러 cam이 골고루 들어오면 수백 ms 수준. |

### 2-4. 성능 영향

- **예상 latency 증가**: 평균 0.2~0.5초 (버퍼에 4~10프레임 쌓였다가 시간순으로 소비하는 효과). 최악(한 cam만 유입) 시 해당 cam 기준 수백 ms.
- **throughput**: 처리량은 “전체 유입 fps”에 맞춰지며, `get_oldest()` + detection 1회가 1프레임이므로 **기존과 동일한 fps 수준 유지**.
- **메모리**: 카메라당 N프레임(예: 60)×약 2.7MB(1280×720×3) ≈ 162MB/cam, 4cam ≈ 650MB. 필요 시 N을 20~30으로 줄여 약 220MB 수준으로 조정 가능.

---

## 3. 개선 2: PENDING 해제 시 재확인 로직

### 3-1. is_same_parcel() / merge_tracks() (현재 구조에서는 생략)

- **is_same_parcel() 별도 구현 불필요**: 현재 아키텍처에서는 “같은 물리 택배”가 **try_match 성공 시** `matcher.masters[mid]["uids"][next_cam] = local_uid`로만 기록됨. 따라서 “다음 카메라에서 이미 매칭되었는가?”는 **`info["uids"].get(next_cam)` 존재 여부**로 판단하면 됨 (bbox IoU/feature 유사도 불필요).
- **merge_tracks() 불필요**: master 하나에 카메라별 `uids`만 있으므로, “merge”는 상태 복구(TRACKING + pending_from_cam 제거)만 하면 됨.

### 3-2. 현재 구조와의 매핑

- 현재는 **master_id(mid)** 단위로만 상태가 있음. 카메라 간 “같은 물리 택배”는 **try_match**로만 연결됨.
- **재확인**이 필요한 상황: `resolve_pending(mid, now_s)`가 DISAPPEAR를 반환하려는 순간, **이미 “다음 카메라”에서 해당 mid가 매칭되어 있으면** DISAPPEAR를 내면 안 됨.

### 3-3. 구체적 구현 (matcher + main 연동)

- **재확인 위치**: `matcher.resolve_pending()` **내부**, `decision = "DISAPPEAR"`로 정한 **직후**, 실제로 DISAPPEAR를 반환하기 **전**에 한 번 더 검사.

**판단 기준 (is_same_parcel 대신 현재 구조에 맞게)**  
- 현재 코드에는 **master당 `info["uids"]`**가 있음: `uids[cam_id] = local_uid` (해당 카메라에서 매칭된 local_uid).
- 따라서 **“다음 카메라에서 이미 매칭되었는가?”** = `info["uids"].get(next_cam)` 존재 여부로 판단 가능.
- **같은 물리 택배인지**를 bbox IoU 등으로 다시 볼 필요 없음: **이미 try_match로 next_cam에 매칭됐다면** `uids[next_cam]`이 설정되어 있음.  
- **문제 시나리오**: 시간순 처리로 바꾸면, “다음 카메라” 프레임이 “이전 카메라” 프레임보다 **먼저** 처리될 수 있음. 그 경우 해당 mid는 next_cam에서 먼저 try_match되어 `uids[next_cam]`이 채워지고, 나중에 from_cam 프레임에서 PENDING이 되었다가 곧바로 resolve될 때 **이미 next_cam에 있으므로** DISAPPEAR를 내지 않도록 하면 됨.

**제안 로직**:

```python
# track/logic/matcher.py :: resolve_pending() 내부, decision 설정 직후

# decision = "DISAPPEAR" 등 설정 후
if decision == "DISAPPEAR":
    # 재확인: 이미 다음 카메라에서 이 master가 매칭되었으면 DISAPPEAR 하지 않음
    if info.get("uids") and info["uids"].get(next_cam):
        # PENDING만 정리하고, 상태를 TRACKING으로 복구 (이미 next_cam에서 본 것)
        info["status"] = "TRACKING"
        info["pending_from_cam"] = None
        self.cancel_pending(from_cam, mid)  # 필요 시 유지
        return None  # DISAPPEAR 이벤트 없음
return {"decision": decision, ...}
```

- **merge_tracks 불필요**: 현재 아키텍처는 “master 하나 + 카메라별 local_uid”이므로, **같은 master가 next_cam에서 이미 보였다면 uids[next_cam]만 있으면 되고**, 별도 track merge는 없음.

### 3-4. PENDING_TIMEOUT (현재 구조에서의 의미)

- 현재는 **구간별 expected**만 사용: `expected = last_time + AVG_TRAVEL[(from_cam, next_cam)] + TIME_MARGIN[(from_cam, next_cam)]`.
- **별도 PENDING_TIMEOUT 상수**는 도입하지 않고, **아래 개선 3**에서 `TIME_MARGIN` 증분 또는 **PENDING_EXTRA_MARGIN_SEC**를 두어 “expected까지의 시간”을 늘리는 방식으로 조정 권장.
- 만약 “PENDING 상태가 된 지 N초 후에만 DISAPPEAR 허용”을 넣고 싶다면: `resolve_pending`에서 `now_s >= expected` 조건에 더해 `now_s >= pending_since + PENDING_MIN_SEC` 같은 조건을 추가할 수 있음. 기본 제안은 **extra margin으로만** 완화.

### 3-5. 예외 처리 (false positive/negative 최소화)

- **False positive (정상인데 DISAPPEAR)**: 재확인으로 **uids[next_cam] 존재 시 DISAPPEAR 미반환**하면 대부분 감소.
- **False negative (실제 소실인데 DISAPPEAR 안 함)**: uids가 잘못 남아 있는 경우는 거의 없음(try_match 성공 시에만 설정). 다만 **다른 mid가 next_cam에서 잘못 매칭**되면 그 mid는 DISAPPEAR되지 않을 수 있음 → 시간순 처리로 try_match 성공률을 올리는 것이 우선.

---

## 4. 개선 3: DISAPPEAR threshold / margin 검토

### 4-1. 현재 값

- **단일 DISAPPEAR_THRESHOLD 없음.**  
- **구간별** `expected = info["last_time"] + config.AVG_TRAVEL[(from_cam, next_cam)] + config.TIME_MARGIN[(from_cam, next_cam)]`.  
- 예: USB_LOCAL → RPI_USB1: **16.06 + 1.0 = 17.06초** (last_time 기준).  
- `resolve_pending`에서 **now_s >= expected**이면 DISAPPEAR(또는 루트별 PICKUP) 반환.

### 4-2. 제안 값

- **전역 DISAPPEAR_BASE_THRESHOLD 미사용**: 현재는 구간별 `AVG_TRAVEL + TIME_MARGIN`으로 expected를 쓰므로, “30초 + 카메라당 10초” 같은 단일 상수 대신 **구간별 expected + 전역 여유 초** 방식 채택.
- **방식 1 (권장)**: 기존 구조 유지 + **전역 여유 시간 추가**  
  - `config.py`에 `PENDING_EXTRA_MARGIN_SEC = 10` (또는 15) 추가.  
  - `resolve_pending`에서  
    `expected = info["last_time"] + config.AVG_TRAVEL[key] + config.TIME_MARGIN[key] + getattr(config, "PENDING_EXTRA_MARGIN_SEC", 0)`  
  - 효과: USB_LOCAL→RPI_USB1 기준 **17.06 + 10 = 27.06초**까지 기다린 뒤 DISAPPEAR.  
- **방식 2**: 구간별 `TIME_MARGIN`만 증가  
  - 예: `("USB_LOCAL", "RPI_USB1"): 1.0 → 5.0`, `("RPI_USB1", "RPI_USB2"): 1.0 → 4.0` 등.  
  - 장점: 구간별로 세밀 조정 가능. 단점: 키가 많아 관리 부담.

**권장**: 방식 1로 **PENDING_EXTRA_MARGIN_SEC = 10** 적용. 필요 시 15~20으로 상향.

### 4-3. 근거

- **카메라 간 이동**: AVG_TRAVEL이 이미 9~16초 구간이므로, “물리 이동 시간”은 반영됨.
- **프레임 드롭·지연**: ZMQ/네트워크로 2~5초 지연 가능. detection 1~2프레임 놓칠 수 있음 → **+10초**면 1~2초 단위 지연을 여러 번 견딜 수 있음.
- **실제 소실 vs 일시적 미detection**: 10초 추가로 “일시적 미detection”으로 인한 오판을 줄이고, 실제 소실은 27초대 이후에만 보고되도록 함.

### 4-4. Dynamic threshold (선택 사항)

- **카메라별 FPS/health**가 낮으면 해당 구간만 margin을 더 주는 방식 가능.  
- 예: `resolve_pending`에서 `next_cam`에 대해 `camera_health[next_cam]["recent_fps"] < 10`이면 `extra = 5` 추가.  
- 초기에는 **고정 PENDING_EXTRA_MARGIN_SEC**만 적용하고, 모니터링 수집 후 동적 보정을 도입해도 됨.

---

## 5. 개선 4: 프레임 수신 예외 상황 처리

### 5-1. time-based prediction (선택 사항, Phase 4)

- **목표**: 특정 카메라가 일시적으로 프레임을 안 주어도, **즉시 DISAPPEAR**하지 않고 “예측으로 유지”했다가, 일정 시간 후에만 DISAPPEAR.
- **알고리즘**: **Constant velocity model** 또는 **Kalman Filter(선택)**.  
  - 상태: (x, y, vx, vy).  
  - 예측: `x_next = x + vx*dt`, `y_next = y + vy*dt`.  
- **적용 범위**: 현재는 **master 단위**가 “카메라별 last_pos”를 가지지 않고, `active_tracks[cam]`만 per-cam last_pos를 가짐. 따라서 “프레임 없음” 시 **해당 cam의 active_tracks만 유지**하고, **resolve_pending에 쓰는 now_s**를 “해당 cam이 오랫동안 프레임을 안 주면, 그 cam의 마지막 ts를 고정해 두고 다른 cam의 ts만으로 resolve하지 않도록” 하는 것이 더 단순하고 안전함.

**단순안 (우선 권장)**  
- **프레임 수신 실패** = 해당 cam에 대해 `get_oldest()`가 한동안 그 cam에서만 나오지 않음.  
- **정책**: “어떤 cam의 프레임으로 resolve_pending(mid, now_s)를 호출할 때, **그 now_s가 “모든 cam의 최근 수신 ts” 중 가장 오래된 것보다 일정 초(예: 5초) 이상 크면** resolve_pending을 skip”하면, “한 cam이 끊겨서 오래된 ts만 들어오는” 상황에서 잘못된 DISAPPEAR를 줄일 수 있음.  
- 즉, **time-based prediction 없이**, “resolve에 사용하는 시간의 유효성”만 제한해도 상당 부분 예외 완화 가능.

**Prediction 도입 시 (Phase 4 고도화)**  
- `TrackState` 같은 구조에 `last_pos`, `last_vel`, `last_ts`를 두고, detection 있을 때만 update, 없을 때는 `predict_next_position()`로 위치만 추정.  
- **disappeared 후보**는 “실제 detection이 N초 이상 없고 + confidence < threshold”일 때만.  
- Kalman은 `filterpy.kalman.KalmanFilter` 등으로 dim_x=4, dim_z=2 (x,y) 구성 가능.

### 5-2. confidence 관리 (선택)

- **규칙**: detection 매칭 시 confidence = min(1.0, confidence + 0.2); 매칭 실패(해당 프레임에서 안 보임) 시 confidence = max(0.0, confidence - 0.1).  
- **PENDING 전환**: confidence < 0.3이면 PENDING 후보.  
- **DISAPPEAR**: `resolve_pending`에서 **confidence < 0.2이고** time_since_last_detection > (expected + extra)일 때만 DISAPPEAR 허용 등.  
- 현재 **master**에는 confidence 필드가 없으므로, 도입 시 `matcher.masters[mid]["confidence"]`를 추가하고, main 루프에서 detection 매칭/미매칭 시 갱신.

### 5-3. 프레임 수신 실패 감지

- **감지**:  
  - **버퍼 방식**: 카메라별로 “마지막으로 꺼낸(또는 put된) ts”를 저장. `get_oldest()`가 특정 cam에서만 계속 반환하지 않으면, 그 cam은 “최근에 프레임이 없음”.  
  - **주기적 체크**: `time.time() - last_received_ts[cam] > 5.0`이면 “해당 cam 5초 이상 미수신”으로 플래그.  
- **일시 vs 지속**: 연속 5초 미수신 = 일시, 30초 미수신 = 지속. 지속 시 해당 cam은 **resolve_pending의 now_s 소스로 사용하지 않음** (또는 해당 cam 프레임만 skip).  
- **카메라별 health**: `camera_health[cam] = {"last_ts": 0, "fail_count": 0}`. put() 시 last_ts 갱신, get_oldest()에서 해당 cam에서 꺼낼 때도 갱신. 주기적으로 “현재 시각 - last_ts > THRESHOLD”이면 fail_count 증가 등.

### 5-4. 복구 로직

- **프레임 복구**: 해당 cam에서 다시 `put()` → 버퍼에 들어가고, `get_oldest()`로 나오면 **기존과 동일하게** detection → try_match → active_tracks 갱신.  
- **예측 track과 실제 detection 매칭**: prediction을 도입한 경우에만, “예측 위치와 detection 거리”로 기존 track에 매칭 후 correction.

### 5-5. 최악 시나리오 (전체 카메라 다운)

- **모든 cam에서 일정 시간(예: 60초) 프레임 없음**:  
  - **resolve_pending 호출 중단** (now_s를 “실제 수신한 마지막 프레임 ts”로만 쓰고, 새 ts가 없으면 resolve 자체를 하지 않음).  
  - 로그/알림: “All cameras no frame for 60s”.  
  - **DISAPPEAR 처리 일시 중단** 플래그: `DISAPPEAR_PROCESSING_ENABLED = False`로 두고, 복구 후 True로 복원.  
- **복구 시**: 첫 프레임이 다시 들어오면 기존 루프대로 처리, PENDING은 그때의 ts로 다시 resolve 가능.

---

## 6. 구현 순서

1. **Phase 1: 프레임 시간순 처리** (2~2.5시간)  
   - `TimeOrderedFrameBuffer` 구현 및 config 플래그.  
   - main 루프를 `get_oldest()` 기반으로 변경.  
   - stale ts 정책(예: 현재 시각 - time_s > 30이면 resolve_pending skip) 적용.  
   - 단위/통합 테스트.

2. **Phase 2: PENDING 재확인** (0.5~1시간)  
   - `resolve_pending` 내 DISAPPEAR 반환 전 `uids.get(next_cam)` 체크 및 TRACKING 복구.  
   - 테스트.

3. **Phase 3: threshold 조정** (약 30분)  
   - `PENDING_EXTRA_MARGIN_SEC` 추가 및 `resolve_pending`에 반영.  
   - 테스트.

4. **Phase 4: 예외 처리** (2~3시간)  
   - 카메라별 last_ts/fail_count, “resolve에 사용할 now_s 유효성” 제한.  
   - (선택) confidence 필드, Kalman 예측, 전체 카메라 다운 시 DISAPPEAR 중단.  
   - 테스트.

**총 예상 시간: 6.5~8시간**

---

## 7. 테스트 계획

### 7-1. 단위 테스트

- [ ] `TimeOrderedFrameBuffer.put` / `get_oldest`: 순서 보장, 빈 버퍼 시 None.
- [ ] `resolve_pending`: `uids[next_cam]` 존재 시 DISAPPEAR 미반환.
- [ ] `expected` 계산: `PENDING_EXTRA_MARGIN_SEC` 반영 여부.

### 7-2. 통합 테스트

- [ ] 4 cam 동시: 시간순으로 프레임 처리되는지 (로그로 ts 순서 확인).
- [ ] 카메라 간 이동: USB_LOCAL → RPI_USB1 → … 에서 try_match 성공, DISAPPEAR 미발생.
- [ ] 한 cam 지연 시뮬레이션: 해당 cam만 2초 늦게 넣어도, 시간순으로 처리 후 매칭 성공.
- [ ] 한 cam 일시 중단: 5초간 put 중단 후 재개 시 복구 및 DISAPPEAR 과다 호출 없음.

### 7-3. 성능 테스트

- [ ] FPS: 시간순 버퍼 도입 전후 비교 (목표: 5% 이내).
- [ ] 지연: 첫 프레임 수신 → 처리 완료까지 평균/최대 ms.
- [ ] 메모리: 버퍼 maxlen=60 기준 4cam 약 650MB 이내.
- [ ] 장시간(8시간) 안정성: 메모리 증가 없음, disappeared 호출 빈도 정상 범위.

---

## 8. 롤백 계획

- **백업**: `main.py`, `frame_aggregator.py`(또는 신규 파일), `matcher.py`, `config.py` 수정 전 복사본 저장 (예: `*.backup.YYYYMMDD`).  
- **플래그**: `config.USE_TIME_ORDERED_BUFFER = False`로 되돌리면 기존 `FrameAggregator` + cam 순서 루프로 동작하도록 분기 유지.  
- **배포**: 1대에서 먼저 적용 → 로그/메트릭 확인 후 전면 적용.

---

## 9. 모니터링 강화

- **추가 로그/메트릭**  
  - detect-disappear 호출 빈도 (분당/시간당).  
  - 카메라별 버퍼 길이, 마지막 수신 ts, “stale skip” 횟수.  
  - PENDING → TRACKING (재확인으로 복구) 횟수.  
  - 구간별 try_match SUCCESS / OUT_OF_MARGIN 비율.  
- **알림**: disappeared 호출이 N건/분 초과 시 알림, “모든 cam 60초 미수신” 시 알림.

---

## 10. 나의 의견 및 제언

### 개선 우선순위

1. **프레임 시간순 처리 (최우선)**  
   - disappeared 오판의 **근본 원인**이 “프레임 순서 + ts skew”이므로, 이를 해결해야 재확인·margin만으로는 한계가 있음.  
   - 효과: try_match 성공률 상승 → PENDING 과다 해소 → DISAPPEAR 급감.

2. **PENDING 재확인 + DISAPPEAR margin (즉시)**  
   - 구현이 가볍고, 시간순 처리와 함께 적용 시 이중 안전장치.  
   - `PENDING_EXTRA_MARGIN_SEC = 10`만 해도 “너무 짧은 대기”로 인한 오판이 줄어듦.

3. **프레임 수신 예외 처리**  
   - “resolve에 사용하는 now_s 유효성” 제한 + 카메라별 last_ts 모니터링을 먼저 도입.  
   - Kalman/confidence는 **Phase 4 선택**으로 두고, 운영 데이터를 본 뒤 필요 시 추가.

4. **PENDING 재확인**  
   - `uids[next_cam]` 체크는 코드 한 블록 수준이라 Phase 2에서 반드시 포함 권장.

### 추가 제언

- **타임스탬프 동기화**: 가능하다면 RPI/서버에 NTP 또는 PTP로 시각 동기화를 두면, ts skew가 줄어 시간순 처리 품질이 좋아짐.  
- **adaptive margin**: 구간별 OUT_OF_MARGIN 비율을 주기적으로 보고, 특정 구간만 TIME_MARGIN을 소폭 상향하는 자동화 검토.  
- **시각화**: 실시간으로 “버퍼 길이·최소 ts·resolve_pending skip 횟수”를 간단한 대시나 로그에 노출하면 디버깅과 운영에 유리함.

---

*계획서 작성일: 2025-01-28. 코드 기준: apsr/track (main.py, logic/matcher.py, config.py, ingest).*
