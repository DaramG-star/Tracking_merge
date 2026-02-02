# Track 서비스 disappeared 오작동 근본 원인 분석 보고서

## 1. 문제 요약

- **증상**: MongoDB `scan`/`track` collection의 대부분 데이터가 `disappeared: true` 상태.
- **원인**: 200 서버 track 서비스가 parcel을 제대로 tracking 못하고, **detect-disappear API를 과다 호출**함.
- **영향**: 정상 택배를 소실로 오판, 운영 불가.

---

## 2. 시스템 변경 사항

- **기존**: USB local cam **1개**만 사용. `trackingLogic/Tracking_test1`은 **디스크에서 프레임 로드** (`get_sorted_frames()`) → **전체 프레임을 시간순 정렬** 후 처리.
- **현재**: USB local cam 1개 + Raspberry Pi ZMQ 3개 = **총 4개 카메라**. `track/main.py`는 **실시간 ZMQ + USB** 수신, `FrameAggregator`로 카메라별 **최신 1프레임**만 유지.
- **변경 시기**: (코드베이스 기준, 정확한 배포일은 별도 확인 필요)
- **변경 코드**: `track/main.py`, `track/ingest/` (FrameAggregator, FrameReceiver, USBCameraWorker), `track/config.py` (RBP_CLIENTS, ZMQ_CAM_MAPPING, TRACKING_CAMS).

---

## 3. detect-disappear API 호출 분석

### 3-1. 호출 위치

| 항목 | 내용 |
|------|------|
| **파일** | `track/main.py` |
| **라인** | 258–268 |
| **함수** | `main()` 내부 메인 루프 (cam별 처리 블록) |
| **코드** | 아래 스니펫 |

```python
# track/main.py 258-268
for mid in list(matcher.masters.keys()):
    result = matcher.resolve_pending(mid, time_s)
    if result:
        decision = result["decision"]
        if decision == "PICKUP":
            api_helper.api_pickup(mid)
            # ... CSV 기록 ...
        elif decision == "DISAPPEAR":
            api_helper.api_disappear(mid)
            if args.csv and csv_writer:
                csv_writer.writerow({
                    "timestamp": ts, "cam": "", "local_uid": "", "master_id": mid,
                    "route": matcher.masters[mid]["route_code"],
                    "x1": "", "y1": "", "x2": "", "y2": "", "event": "DISAPPEAR"
                })
```

- **API 구현**: `track/logic/api_helper.py` 59–66행 `api_disappear(uid)` → `PATCH {BASE_URL}/detect-disappear`, `json={"uid": uid, "disappear": True}`.

### 3-2. 호출 조건 (resolve_pending)

**DISAPPEAR가 나오는 조건**은 `track/logic/matcher.py`의 `resolve_pending(mid, now_s)`에 의해 결정됨.

- **진입 조건**: `info["status"] in ["PENDING", "TRACKING"]`, `from_cam`/`next_cam` 및 `config.AVG_TRAVEL`/`TIME_MARGIN` 존재.
- **기대 도착 시각**:  
  `expected = info["last_time"] + config.AVG_TRAVEL[(from_cam, next_cam)] + config.TIME_MARGIN[(from_cam, next_cam)]`
- **판정**: `now_s >= expected` 이면 “다음 카메라에 기대 시각까지 도착하지 않음”으로 보고, 루트에 따라 `PICKUP` 또는 **`DISAPPEAR`** 반환.

```python
# track/logic/matcher.py 126-150
def resolve_pending(self, mid, now_s):
    info = self.masters[mid]
    if info["status"] not in ["PENDING", "TRACKING"]:
        return None
    from_cam = info.get("pending_from_cam") or info["last_cam"]
    route = info["route_code"]
    next_cam = self._get_next_cam(route, from_cam)
    if not next_cam:
        return None
    key = (from_cam, next_cam)
    if key not in config.AVG_TRAVEL:
        return None
    expected = info["last_time"] + config.AVG_TRAVEL[key] + config.TIME_MARGIN[key]
    if now_s < expected:
        return None
    if route == "XSEA":
        decision = "PICKUP" if next_cam in ["RPI_USB2", "RPI_USB3"] else "DISAPPEAR"
    elif route == "XSEB":
        decision = "PICKUP" if next_cam in ["RPI_USB3", "RPI_USB3_EOL"] else "DISAPPEAR"
    else:
        decision = "DISAPPEAR"
    info["status"] = decision
    self.cancel_pending(from_cam, mid)
    return {"decision": decision, "from_cam": from_cam, "next_cam": next_cam, "expected": expected}
```

- **PENDING이 되는 조건** (`track/main.py` 240–246): 해당 카메라에서 **한 프레임이라도** 해당 master에 매칭된 detection이 없으면 즉시 PENDING.

```python
# track/main.py 240-246
for old_uid, old_info in active_tracks[cam].items():
    if old_uid not in new_active:
        mid = old_info["master_id"]
        if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
            matcher.masters[mid]["status"] = "PENDING"
            matcher.masters[mid]["pending_from_cam"] = cam
```

**정리**:

- DISAPPEAR **threshold**는 구간별로 `AVG_TRAVEL + TIME_MARGIN` (예: USB_LOCAL→RPI_USB1 ≈ 16.06+1.0 ≈ 17초).
- 문제는 threshold 값 자체보다, **“다음 카메라에서 기대 시각 안에 매칭이 되어야 하는데, 실시간 파이프라인에서는 그 매칭이 실패하기 쉽다”**는 점임 (아래 4·6절).

### 3-3. 호출 빈도

- **루프 구조**: `for cam in config.TRACKING_CAMS:` 안에서, **매 cam·매 프레임**마다 `for mid in list(matcher.masters.keys()):`로 **모든 master**에 대해 `resolve_pending(mid, time_s)` 호출.
- 따라서 **한 번 PENDING이 된 master**는, `now_s >= expected`가 되는 **어느 카메라 프레임이든** 먼저 처리되는 순간 **한 번** DISAPPEAR로 결정되고 `api_disappear(mid)`가 호출됨.
- **과다 호출 원인**: PENDING이 과다 발생 (한 프레임 detection 누락·타임스탬프 순서 문제 등) → 기대 시각 경과 시마다 DISAPPEAR가 발생.

---

## 4. 멀티 카메라 처리 로직 분석

### 4-1. 카메라 입력 코드

- **현재 (4개 카메라)**  
  - `track/config.py`: `TRACKING_CAMS = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]`, `RBP_CLIENTS`(ZMQ), `LOCAL_USB_CAMERAS`, `ZMQ_CAM_MAPPING`.  
  - `track/main.py`: ZMQ `FrameReceiver` per RBP client, 로컬 USB는 `USBCameraWorker` + `usb_feeder_loop`가 주기적으로 `FrameAggregator.put(cam_id, frame, ts)` 호출.  
  - 메인 루프는 **cam 순서대로** `aggregator.get(cam)`으로 **해당 cam의 최신 1프레임**만 가져와 처리.

```python
# track/main.py 171-186 (요지)
while _running:
    for cam in config.TRACKING_CAMS:
        pair = aggregator.get(cam)
        if pair is None:
            continue
        img, ts = pair
        # ...
        time_s = ts
        detections = detector.get_detections(img, cfg, cam)
        # ... active_tracks[cam], matcher, resolve_pending(mid, time_s) ...
```

- **특징**:  
  - **프레임 순서**: “시간순”이 아니라 **고정 cam 순서**(USB_LOCAL → RPI_USB1 → RPI_USB2 → RPI_USB3).  
  - **타임스탬프**: 각 cam마다 **그 cam이 넣은 최신 프레임의 ts**만 사용. ZMQ/네트워크 지연으로 **RPI 쪽 ts가 USB_LOCAL보다 늦게 도착하거나, 시간적으로 뒤섞일 수 있음**.

### 4-2. Detection 처리

- **4개 프레임 처리 방식**: cam별로 **순차** 처리. 각 cam에 대해 `aggregator.get(cam)` → 1장씩 `detector.get_detections(img, cfg, cam)` 호출.  
- **즉, 4개 카메라 프레임이 모두 각각 detection에 들어감** (1개만 쓰는 구조 아님).  
- **문제점**:  
  - **같은 물리 시간대**에 대해 “다음 카메라”의 **프레임이 나중에 처리될 때**, 그 프레임의 `time_s`가 **이전 카메라의 last_time + AVG_TRAVEL ± TIME_MARGIN** 구간을 벗어나면, `try_match`에서 **OUT_OF_MARGIN**으로 매칭 실패 → PENDING 유지 → 기대 시각 지나면 DISAPPEAR.

### 4-3. Tracking 통합

- **방식**: **카메라별 독립 `active_tracks[cam]`** + **전역 FIFO matcher** (`FIFOGlobalMatcher`).  
  - 같은 물리 택배가 cam1→cam2로 넘어가도 **시각적 track ID는 cam마다 새로 부여** (예: `USB_LOCAL_001`, `RPI_USB1_002`).  
  - **카메라 간 연속성**은 **시간 기대값 + try_match**로만 유지: “다음 cam에서 기대 시각 안에 들어온 detection”이 있으면 그때 `try_match`로 master와 연결.  
- **문제점**:  
  - **프레임 처리 순서가 “시간순”이 아님**: 항상 USB_LOCAL → RPI_USB1 → … 순서.  
  - **각 cam의 `time_s`**는 “그 cam이 넣은 최신 프레임”의 ts이므로, **RPI 쪽이 지연되면** “실제로는 다음 cam에 도착한 시점”의 프레임이 **나중에** 처리되고, 그때의 `time_s`가 이미 **expected ± margin** 밖일 수 있음 → **try_match 실패** → 같은 택배가 다음 cam에서 “도착”했는데도 매칭되지 않고, 결국 **resolve_pending에서 DISAPPEAR**로 이어짐.

### 4-4. --display 실행 결과 (코드 기준)

- **표시**: `track/main.py` 286–288에서 `cv2.imshow(f"track_{cam}", img)`로 **cam별 창**이 따로 뜸. 즉 4개 카메라 화면은 각각 표시됨.  
- **Detection box**: `img`는 detection 후 시각화가 아니라 **원본 프레임**만 표시하는 구조이므로, `--video` 경로의 `visualizer.draw_and_write`와는 별개. `--display`만으로는 bbox가 그려지는지 여부는 `TrackingVisualizer` 사용 여부에 따름 (현재 `--display` 블록에는 draw 없음).  
- **Tracking ID**: 카메라별 `active_tracks[cam]`의 local_uid만 존재하며, 카메라 간 ID 연속성은 없음.  
- **결론**: 4개 화면은 나오지만, **타임스탬프 순서·지연**으로 인해 “같은 택배”가 다음 카메라에서 기대 시각 안에 매칭되지 않으면 DISAPPEAR가 발생하는 구조임.

---

## 5. 기존 trackingLogic과 비교

### 5-1. 프레임 처리 순서 (핵심 차이)

**기존 (trackingLogic/Tracking_test1)**  
- `get_sorted_frames()`: **모든 카메라의 프레임을 디스크에서 읽어 `time_s` 기준으로 정렬**한 리스트 생성.  
- 메인 루프: **정렬된 리스트를 한 번씩 순회** → **전역 시간순**으로 프레임 처리.

```python
# trackingLogic/Tracking_test1/loader.py
all_frames.sort(key=lambda x: x["time_s"])
return all_frames

# main: for frame in all_frames:
#   cam = frame["cam"], time_s = frame["time_s"]
#   → resolve_pending(mid, frame["time_s"]) 등
```

**현재 (track/main.py)**  
- **cam 순서 고정** (USB_LOCAL → RPI_USB1 → RPI_USB2 → RPI_USB3).  
- 매 루프마다 `aggregator.get(cam)`으로 **각 cam의 최신 1프레임**만 사용.  
- **시간순 보장 없음**: 네트워크/버퍼로 인해 “다음 카메라의, 실제 도착 시점 프레임”이 **나중에** 처리되거나, 그때의 `time_s`가 expected 구간을 벗어날 수 있음.

### 5-2. 변경된 로직과 문제 발생 이유

- **기존**: 시간순 프레임 → “다음 카메라 도착 시점”의 프레임이 **항상 올바른 시간 순서**로 들어와서, `try_match(..., time_s)`의 `time_s`가 대체로 `expected ± margin` 안에 들어옴 → 매칭 성공 → PENDING 해소.  
- **현재**: cam 순서 + “최신 1프레임”만 사용 →  
  - 같은 물리 시각에 대해 **USB_LOCAL이 먼저**, RPI는 나중에 처리되거나,  
  - RPI 프레임의 ts가 지연되어 **expected 구간을 벗어난 값**으로 들어오면  
  → **try_match 실패(OUT_OF_MARGIN)** → PENDING 유지 → `now_s >= expected` 되는 프레임에서 **DISAPPEAR** 호출.  
- 즉, **“프레임을 시간순으로 처리하지 않음”**과 **“cam별 최신 1프레임만 사용”**이 결합되어, **카메라 간 이동 시 매칭이 깨지고**, 그 결과 **과다 DISAPPEAR**가 발생함.

---

## 6. 근본 원인 (확정)

**원인**:  
실시간 파이프라인에서 **프레임을 카메라 순서로만 처리하고, 각 카메라별 “최신 1프레임”의 타임스탬프만 사용**하기 때문에, **“다음 카메라에 택배가 도착한 시점”의 프레임이 기대 도착 시각(expected ± margin) 안에 매칭되지 않음**.  
그 결과 해당 master는 PENDING 상태로 남고, `now_s >= expected`가 되는 시점에 **resolve_pending**이 DISAPPEAR를 반환하여 **detect-disappear API가 호출**됨.

**증거**:

1. **코드**  
   - `track/main.py`: 메인 루프가 `for cam in config.TRACKING_CAMS` 순서로만 진행하며, `time_s`는 `aggregator.get(cam)`으로 받은 그 프레임의 ts임.  
   - `track/logic/matcher.py` `_try_fifo`: `expected = info["last_time"] + avg_travel`, `abs(time_s - expected) > margin`이면 매칭 실패(OUT_OF_MARGIN).  
   - `resolve_pending`: `expected = last_time + AVG_TRAVEL + TIME_MARGIN`, `now_s >= expected`이면 DISAPPEAR (또는 루트에 따라 PICKUP).

2. **시뮬레이션**  
   - 택배가 USB_LOCAL에서 last_time=100으로 보이다가 사라짐 → PENDING (pending_from_cam=USB_LOCAL).  
   - expected ≈ 100 + 16.06 + 1.0 = 117.06.  
   - RPI_USB1에 택배가 도착한 “실제” 시점은 116초대인데, ZMQ 지연으로 RPI_USB1 쪽 최신 프레임이 ts=116.2로 들어옴.  
   - 루프 순서상 USB_LOCAL을 먼저 처리해, USB_LOCAL 쪽 ts=117.5 프레임이 먼저 처리되면, 그 시점에 `resolve_pending(mid, 117.5)`가 호출되어 **117.5 >= 117.06** → DISAPPEAR.  
   - 또는 RPI_USB1 프레임이 ts=116.2로 처리되더라도, 이미 다른 cam에서 ts=117.5가 먼저 처리되어 PENDING이 DISAPPEAR로 바뀐 뒤일 수 있음.  
   - 반대로 RPI_USB1 프레임이 ts=100 (오래된 버퍼)로만 들어오면, try_match(116.06 기대, ts=100) → OUT_OF_MARGIN → 매칭 실패, 이후 expected 경과 시 DISAPPEAR.

3. **재현 가능성**:  
   - 4 cam 실시간 수신 환경에서, ZMQ/버퍼 지연을 재현하거나, RPI 클라이언트 타임스탬프가 불안정하면, 동일 조건에서 DISAPPEAR 과다 호출 재현 가능.

**기타 가능 요인 (악화 요인)**  
- **한 프레임 detection 누락 → 즉시 PENDING** (main.py 240–246): detection이 한 프레임만 놓쳐도 해당 master가 PENDING이 되므로, RPI 쪽에서 detection이 불안정하거나 프레임 drop이 있으면 PENDING·DISAPPEAR가 더 자주 발생할 수 있음.  
- **RPI 카메라 각도/해상도**에서 모델 성능 저하 시, “다음 카메라”에서 try_match 전에 detection 자체가 안 나오면 매칭 실패 → 동일하게 DISAPPEAR로 이어짐.

**왜 발생했는가**:

- 기존 오프라인 로직은 **시간순 정렬된 단일 스트림**을 가정했으나, 실시간 전환 시 **“cam별 최신 1프레임 + 고정 cam 순서”**만 도입하고 **시간순 처리 또는 타임스탬프 동기화**를 도입하지 않음.  
- 멀티 카메라 + 네트워크 수신 시 **타임스탬프 지연/순서 뒤바뀜**을 전제한 설계가 없음.

---

## 7. 대책

### 7-1. 긴급 조치 (즉시)

1. **track 서비스 중지**  
   - `systemctl stop track.service` (또는 해당 프로세스 종료).

2. **MongoDB disappeared 필드 복구** (운영 정책에 따라 실행):  
   ```javascript
   db.scan.updateMany({}, { $set: { disappeared: false } });
   db.track.updateMany({}, { $set: { disappeared: false } });
   ```  
   - 필요 시 조건부 업데이트(예: 특정 기간만) 적용.

### 7-2. 코드 수정 (승인 후)

#### 수정 1: 프레임 처리 순서를 “시간순”에 가깝게 (권장)

- **목표**: `resolve_pending`과 `try_match`에 들어가는 `time_s`가 **전역 시간순**에 가깝게 처리되도록 함.  
- **방안 예**:  
  - 카메라별로 **최근 N프레임 버퍼**를 유지하고, **가장 오래된(또는 가장 이른 ts) 프레임**부터 소비하거나,  
  - **단일 “다중 cam 프레임 풀”**을 두고, **ts 기준으로 정렬한 뒤** 한 프레임씩 꺼내서 처리 (기존 `get_sorted_frames()`와 유사한 순서).  
- 이렇게 하면 “다음 카메라 도착 시점”의 프레임이 **expected ± margin** 안의 `time_s`로 처리될 가능성이 높아져, try_match 성공 → PENDING 해소 → 잘못된 DISAPPEAR 감소.

#### 수정 2: PENDING 해제 시 한 번 더 “다음 카메라” 확인

- **목표**: `now_s >= expected` 직전/직후에, **해당 master가 “다음 카메라”에서 이미 매칭되었는지** 확인.  
- **방안 예**: resolve_pending에서 DISAPPEAR 반환 전에, 해당 master가 다음 cam의 `active_tracks`에 이미 연결되어 있으면 DISAPPEAR를 내지 않고 PENDING만 정리하거나, 또는 “다음 카메라”의 미처리 버퍼에 아직 해당 시각대 프레임이 있으면 짧게 대기/재시도.  
- 보조 수정으로, **같은 물리 택배가 여러 카메라에 걸쳐 있을 때** 잘못된 DISAPPEAR를 줄이는 데 도움.

#### 수정 3: DISAPPEAR threshold / margin 검토

- **현재**: 구간별 `AVG_TRAVEL + TIME_MARGIN` (예: USB_LOCAL→RPI_USB1 ≈ 17초).  
- **검토**: 카메라 간 실제 이동 시간 + 네트워크 지연을 반영해 **TIME_MARGIN**을 일시적으로 늘리거나, “다음 카메라” 구간만 margin을 완화하는 방식으로 **잘못된 DISAPPEAR**를 줄일 수 있음.  
- 단, 근본 해결은 **시간순 처리(수정 1)**가 우선.

### 7-3. 테스트 계획

1. **단일 카메라**: USB_LOCAL만 사용해 스캔 → PENDING → resolve_pending 동작 확인.  
2. **카메라 간 이동**: 택배가 USB_LOCAL → RPI_USB1 → … 순으로 이동할 때, **시간순/버퍼 시뮬레이션**으로 try_match 성공 및 DISAPPEAR 미발생 확인.  
3. **4 cam 동시**: 모든 cam에서 프레임 수신·detection 동작 및 DISAPPEAR 호출 빈도 로그 수집.  
4. **장시간 운영**: ZMQ 지연·패킷 손실 시나리오에서 DISAPPEAR 오탐률 모니터링.

### 7-4. 재발 방지

1. **멀티 카메라 + 실시간** 시나리오 문서화: “프레임 순서 = 시간순” 요구사항, expected ± margin 의미.  
2. **통합 테스트**: “정렬된 프레임 스트림” vs “cam 순서 + 최신 1프레임” 시나리오 비교, DISAPPEAR 호출 횟수 assert.  
3. **모니터링**: detect-disappear 호출 빈도/건수 알림, PENDING 상태 수·평균 대기 시간 대시보드.

---

## 8. 첨부

- **detect-disappear 호출 경로**: `track/main.py` 258–268, `track/logic/api_helper.py` 59–66, `track/logic/matcher.py` 126–150.  
- **프레임 수집**: `track/main.py` 171–186, `track/ingest/frame_aggregator.py`, `track/config.py` (TRACKING_CAMS, AVG_TRAVEL, TIME_MARGIN).  
- **기존 시간순 처리**: `trackingLogic/Tracking_test1/loader.py` (`get_sorted_frames`), `main.py` (`for frame in all_frames`).

---

*보고서 작성일: 2025-01-28. 코드 기준: apsr/track (main.py, logic/matcher.py, logic/api_helper.py, config.py, ingest).*
