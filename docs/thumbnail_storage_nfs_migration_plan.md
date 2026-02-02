# Thumbnail 저장 방식 변경 계획서

## 1. 현재 코드 분석 결과

### A. 관련 파일 목록

| 파일 | 역할 | 라인 |
|------|------|------|
| **track/logic/api_helper.py** | `api_update_position(uid, pos, image_base64=None)` — PATCH `/api/detect-position` 호출, payload `uid`, `position`, `thumbnail`(base64) | 22-28 |
| **track/main.py** | position 갱신 시 `api_update_position(mid, step_dist)` 호출 (**썸네일 미전달**) | 316, 366 |
| **track/logic/utils.py** | `image_to_thumbnail_base64(img, max_size=(80,80), jpeg_quality=70)` — 리사이즈·JPEG·base64 반환 | 16-34 |
| **track/tests/test_detect_position_api.py** | API Assert 테스트 — parcel.jpeg → base64 → PATCH with `thumbnail` | 48-74, 77-87 |
| **trackingLogic/Tracking_test1/main2.py** | **실제 썸네일 생성·전송 있는 유일한 클라이언트**: crop → `get_base64_image` → `api_update_position(..., image_base64=img_b64)` | 23-31, 133-136, 154-157 |
| **trackingLogic/Tracking_test1/api_helper.py** | main2 전용: `payload["image"]` 로 전송 (서버는 `thumbnail` 기대) | 16-35 |
| **pwa/PWAforDHL/parcel-api/api/hardware.py** | `DetectPositionRequest(uid, position, thumbnail: Optional[str])`, PATCH handler, MongoDB `thumbnail` 필드에 base64 저장 | 27-31, 103-137 |
| **pwa/PWAforDHL/parcel-api/schemas/parcel.py** | `build_track_document`: 초기 `thumbnail: ""` | 135 |

**작업 주 대상**: 200번 서버에서 실행되는 **track/** 및 **parcel-api(100번)**.  
**참고**: trackingLogic/Tracking_test1 은 별도 클라이언트이며, 동일 NFS 방식 적용 시 main2.py·api_helper 수정 대상.

---

### B. 현재 Workflow

```
[track/main.py — 현재]
1. Detection/매칭 완료
   ↓
2. api_update_position(mid, step_dist)  ← 썸네일 인자 없음 (라인 316, 366)
   → payload: {"uid": mid, "position": step_dist, "thumbnail": ""}

[trackingLogic/Tracking_test1/main2.py — 썸네일 있는 흐름]
1. Detection 완료 (cam별)
   ↓
2. Thumbnail crop (main2.py 134-136)
   crop_img = frame_img[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
   ↓
3. get_base64_image(crop_img) (main2.py 23-31)
   - cv2.resize(img, (80, 80))
   - cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 70])
   - base64.b64encode(buffer).decode('utf-8')
   ↓
4. current_thumbnails[mid] = base64 문자열
   ↓
5. api_helper.api_update_position(m_id, step_dist, image_base64=img_b64) (main2.py 157)
   → track/logic/api_helper.py: payload["thumbnail"] = image_base64
   → requests.patch(f"{BASE_URL}/detect-position", json=payload)
   ↓
6. parcel-api (hardware.py 103-137): request.thumbnail → MongoDB "thumbnail" 필드에 base64 저장
```

---

### C. 변경 대상 코드 (정확한 위치)

#### 파일 1: track/logic/api_helper.py

**현재 (라인 22-28):**
```python
def api_update_position(uid, pos, image_base64=None):
    """PATCH /api/detect-position. 서버는 payload["thumbnail"](base64)을 기대함."""
    try:
        payload = {"uid": uid, "position": pos}
        payload["thumbnail"] = image_base64 if image_base64 else ""
        requests.patch(f"{BASE_URL}/detect-position", json=payload, timeout=2)
    except Exception as e:
        print(f"API Error (Position Update): {e}")
```

- **변경 방향**: 썸네일을 **이미지(numpy)** 또는 **경로(str)** 로 받아, NFS에 저장 후 **경로만** API로 전송.  
  또는 별도 함수 `save_thumbnail_to_nfs(uid, img)` 호출 후 호출부에서 `api_update_position(uid, pos, thumbnail_path=path)` 형태로 분리 가능.

---

#### 파일 2: track/main.py

**현재 (라인 314-316, 364-366):**
```python
if m_info.get("last_sent_dist") != step_dist:
    api_helper.api_update_position(mid, step_dist)
    m_info["last_sent_dist"] = step_dist
```

- **현재**: 썸네일을 넘기지 않음.  
- **변경 후**: (향후 썸네일 연동 시) 해당 master_id에 대한 crop 이미지를 NFS에 저장한 뒤 `api_update_position(mid, step_dist, thumbnail_path=path)` 호출.  
- **이번 NFS 전환 작업 범위**: api_helper 시그니처·payload를 경로 기반으로 바꾸고, main.py는 그대로 두어도 됨 (썸네일 전달은 추후 별도 작업).

---

#### 파일 3: track/logic/utils.py

**현재 (라인 16-34):**  
`image_to_thumbnail_base64()` — 80×80, JPEG 70, base64 반환.

- **변경 방향**:  
  - **유지**: 테스트·레거시에서 base64가 필요할 수 있으므로 함수는 유지.  
  - **추가**: NFS 저장용 `save_thumbnail_to_nfs(uid, img, ...)` 를 새로 두거나, 별도 모듈(`logic/thumbnail_nfs.py` 등)에 둠.

---

#### 파일 4: trackingLogic/Tracking_test1/main2.py

**썸네일 생성 (라인 133-136):**
```python
current_thumbnails[mid] = get_base64_image(crop_img)
```

**API 호출 (라인 154-157):**
```python
if m_info.get("last_sent_dist") != step_dist:
    img_b64 = current_thumbnails.get(m_id)
    api_helper.api_update_position(m_id, step_dist, image_base64=img_b64)
    m_info["last_sent_dist"] = step_dist
```

- **변경 방향**:  
  - crop 이미지를 NFS에 저장하는 함수 호출 → `thumbnail_path = save_thumbnail_to_nfs(m_id, crop_img)` (또는 공용 헬퍼 사용).  
  - `api_update_position(m_id, step_dist, thumbnail_path=thumbnail_path)` 로 변경.  
  - `get_base64_image` 호출 제거(또는 fallback용으로만 유지).

---

#### 파일 5: parcel-api (100번 서버) — pwa/PWAforDHL/parcel-api/api/hardware.py

**요청 스키마 (라인 27-31):**
```python
class DetectPositionRequest(BaseModel):
    uid: str = Field(...)
    position: float = Field(...)
    thumbnail: Optional[str] = Field(None, description="Thumbnail image (base64)")
```

**핸들러 (라인 127-132):**
```python
update_data = {"position": request.position}
if request.thumbnail is not None:
    update_data["thumbnail"] = request.thumbnail
```

- **변경 방향**:  
  - **옵션 A**: 필드명 유지 `thumbnail`, 의미만 경로로 변경 — `thumbnail: Optional[str]`에 `/thumbnails/{uid}.jpg` 저장. 클라이언트는 base64 대신 경로 문자열 전송.  
  - **옵션 B**: `thumbnail_path: Optional[str]` 추가, MongoDB에는 기존 필드 `thumbnail`에 경로 저장 (`update_data["thumbnail"] = request.thumbnail_path or request.thumbnail`).  
  - **권장**: 요청 필드를 **`thumbnail_path`** 로 명시하면, “base64 vs 경로” 혼동을 막기 좋음. DB 필드명은 그대로 `thumbnail`(값만 경로 문자열)로 둬도 됨.

---

### D. API 엔드포인트·설정

| 구분 | 값 |
|------|-----|
| **track (config)** | `config.API_BASE_URL = "http://192.168.1.100:8000/api"` |
| **실제 URL** | `PATCH http://192.168.1.100:8000/api/detect-position` |
| **메서드** | PATCH (POST 아님) |

---

### E. 의존성

| 파일 | base64 | cv2 | requests | os |
|------|--------|-----|----------|-----|
| track/logic/api_helper.py | 사용 안 함 | 사용 안 함 | ✅ | NFS 저장 시 추가 |
| track/logic/utils.py | ✅ (image_to_thumbnail_base64) | ✅ | 사용 안 함 | NFS 저장 함수 추가 시 필요 |
| track/main.py | 사용 안 함 | (display 시) | 사용 안 함 | ✅ 이미 있음 |
| main2.py | ✅ (get_base64_image) | ✅ | 사용 안 함 (api_helper가 requests 사용) | NFS 저장 시 추가 |

- **base64**: NFS 전환 후 클라이언트가 API로 base64를 안 보내면, **api_helper에서 base64 제거 가능**. utils.py의 `image_to_thumbnail_base64`는 테스트·다른 경로용으로 유지 가능.  
- **os**: NFS 경로 조합·파일 존재 확인용으로 추가.

---

## 2. 변경 계획

### Phase 1: 파일 저장 함수 생성

**새 함수 추가 위치:** `track/logic/utils.py` (또는 `track/logic/thumbnail_nfs.py`)

**상수:**  
- NFS 마운트 경로: `THUMBNAIL_NFS_DIR = '/mnt/thumbnails'` (200번 서버에서 100번 서버로 연결됨)

**함수 예시:**
```python
import os
# THUMBNAIL_NFS_DIR = '/mnt/thumbnails'

def save_thumbnail_to_nfs(uid: str, thumbnail_image, jpeg_quality: int = 85) -> Optional[str]:
    """
    NFS를 통해 thumbnail을 100번 서버에 저장.

    Args:
        uid: 택배 고유 ID
        thumbnail_image: OpenCV 이미지 (numpy array). 80×80 등 규격은 호출부에서 맞춤 가능.
        jpeg_quality: JPEG 품질 (기본 85)

    Returns:
        성공 시 웹 URL 경로 "/thumbnails/{uid}.jpg", 실패 시 None
    """
    filepath = os.path.join(THUMBNAIL_NFS_DIR, f'{uid}.jpg')
    # (선택) 80×80 리사이즈: 기존 규격과 맞추려면 cv2.resize 적용
    success = cv2.imwrite(
        filepath,
        thumbnail_image,
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    )
    if not success:
        return None
    return f'/thumbnails/{uid}.jpg'
```

- **JPEG 품질**: 계획서 예시 85 유지. 기존 전송 규격(70)과 맞추려면 70으로 통일 가능.  
- **리사이즈**: 저장 전 80×80 등 호출부 또는 내부에서 적용 가능 (한 곳에서만 하면 됨).

---

### Phase 2: API 호출 부분 수정 (track)

**파일:** `track/logic/api_helper.py`

**시그니처·payload 변경:**

- **옵션 1**: `api_update_position(uid, pos, thumbnail_path=None)`  
  - `thumbnail_path`가 있으면 payload에 `thumbnail_path` (또는 서버가 기대하는 필드명) 로 전송.  
- **옵션 2**: `api_update_position(uid, pos, thumbnail_image=None, thumbnail_path=None)`  
  - `thumbnail_image`가 있으면 내부에서 `save_thumbnail_to_nfs(uid, thumbnail_image)` 호출 후 경로 전송.  
  - `thumbnail_path`가 있으면 그대로 전송.

**payload:**

- **변경 전:** `{"uid": uid, "position": pos, "thumbnail": base64_string}`  
- **변경 후:** `{"uid": uid, "position": pos, "thumbnail_path": "/thumbnails/{uid}.jpg"}`  
  - 서버가 `thumbnail` 필드만 받도록 되어 있으면, **서버를 경로를 저장하도록 수정한 뒤** 클라이언트는 `"thumbnail": "/thumbnails/{uid}.jpg"` 로 보내도 됨 (필드명 하나로 통일).

---

### Phase 3: track/main.py

- **현재**: `api_update_position(mid, step_dist)` 만 호출.  
- **이번 단계**: 썸네일 생성·전달 로직은 **추가하지 않아도 됨**.  
- **추후**: detection crop → `save_thumbnail_to_nfs(mid, crop_img)` → `api_update_position(mid, step_dist, thumbnail_path=path)` 연동.

---

### Phase 4: parcel-api 스키마·핸들러 변경 (100번)

**파일:** `pwa/PWAforDHL/parcel-api/api/hardware.py`

**요청 모델:**

- **변경 전:** `thumbnail: Optional[str] = Field(None, description="Thumbnail image (base64)")`  
- **변경 후 (권장):**  
  - `thumbnail_path: Optional[str] = Field(None, description="Thumbnail file path e.g. /thumbnails/{uid}.jpg")`  
  - 또는 기존 `thumbnail` 필드 유지하고 설명만 "path or base64" → 이후 base64 제거 시 "path only"로 정리.

**저장 로직:**

- **변경 전:** `update_data["thumbnail"] = request.thumbnail` (base64 문자열 저장)  
- **변경 후:** `update_data["thumbnail"] = request.thumbnail_path` (또는 request.thumbnail이 경로로 해석되도록) — **MongoDB에는 경로 문자열만 저장** (`/thumbnails/{uid}.jpg`).

- **주의**: 웹/앱 클라이언트는 해당 경로를 100번 nginx 기준 URL로 조합해 이미지 요청 (예: `http://192.168.1.100/thumbnails/{uid}.jpg`).

---

### Phase 5: trackingLogic/Tracking_test1 (선택)

- **main2.py**: crop → `save_thumbnail_to_nfs(m_id, crop_img)` (track 쪽 헬퍼 재사용 또는 동일 규격 구현) → `api_update_position(m_id, step_dist, thumbnail_path=path)`.  
- **api_helper (Tracking_test1)**: `payload["thumbnail_path"]` (또는 서버와 합의한 필드명) 로 전송, BASE_URL은 100번 API로 통일하는 것이 좋음.

---

### Phase 6: 테스트·문서

- **track/tests/test_detect_position_api.py**:  
  - 기존: base64 `thumbnail` 전송.  
  - 변경 후: NFS에 테스트 이미지 저장 → `thumbnail_path` 전송 → 2xx 및 success 검증.  
- **test_api_helper_sends_thumbnail_key**:  
  - payload에 `thumbnail_path` 키가 들어가는지 검증하도록 수정.  
- **문서**: `thumbnail_base64_spec_and_plan.md`, `detect_position_thumbnail_analysis.md` 에 “NFS 전환: 저장은 /mnt/thumbnails, API에는 경로만 전송” 내용 반영.

---

## 3. 위험 요소 및 대응

| 위험 | 대응 |
|------|------|
| **NFS 마운트 실패** | `save_thumbnail_to_nfs` 내부 또는 호출 전 `os.path.ismount(THUMBNAIL_NFS_DIR)` 확인. 실패 시 로그 후 None 반환 또는 fallback(기존 base64 전송) 선택 가능. |
| **디스크 풀** | `shutil.disk_usage(THUMBNAIL_NFS_DIR)` 로 여유 공간 확인. 10GB 이하 등 임계치에서 경고 또는 저장 스킵. |
| **파일 쓰기 권한** | 200번에서 `touch /mnt/thumbnails/test_write.jpg` 등으로 쓰기 테스트. 실패 시 100번 NFS export·권한 조정. |
| **API 스키마 불일치** | track과 parcel-api를 **동시에** 배포. 또는 기간 동안 서버가 `thumbnail`(base64)과 `thumbnail_path`(경로) 둘 다 받아서 처리한 뒤, 클라이언트 전환 후 base64 제거. |
| **기존 DB thumbnail 필드** | 이미 base64로 저장된 문서는 그대로 두고, 이후 업데이트부터 경로만 저장. 웹/앱에서 값이 `/thumbnails/`로 시작하면 URL로 사용, 아니면 기존 base64 표시(또는 무시) 로직 가능. |

---

## 4. 테스트 계획

1. **NFS 저장 단위 테스트**  
   - `save_thumbnail_to_nfs("test_uid", dummy_cv2_image)` 호출 후 `/mnt/thumbnails/test_uid.jpg` 존재 여부 및 100번 서버 해당 경로(/home/piapp/apsr/apsr-dhl/api/web/thumbnails/) 반영 확인.  
2. **API 통합 테스트**  
   - `api_update_position(uid, pos, thumbnail_path="/thumbnails/xxx.jpg")` 로 PATCH → MongoDB에 경로만 저장되는지 확인.  
3. **웹/앱 확인**  
   - `http://192.168.1.100/thumbnails/{uid}.jpg` 로 이미지 노출 확인.  
4. **롤백**  
   - Git 또는 백업으로 api_helper·utils·parcel-api hardware.py 복구 후 재시작.

---

## 5. 롤백 계획

- **track**: `git checkout HEAD~1 track/logic/api_helper.py track/logic/utils.py` (및 변경한 파일들).  
- **parcel-api**: `pwa/PWAforDHL/parcel-api/api/hardware.py` 이전 버전 복구.  
- **서비스 재시작**: tracking 서비스·parcel-api 재시작.

---

## 6. 승인 요청 사항

1. [ ] 변경 범위가 적절한가? (track + parcel-api + 선택적으로 trackingLogic)  
2. [ ] NFS 경로 `/mnt/thumbnails` 사용 확인.  
3. [ ] API payload 필드명: `thumbnail_path` vs 기존 `thumbnail`에 경로 문자열 저장 중 선택.  
4. [ ] JPEG 품질: NFS 저장 시 85 vs 70 (기존 규격) 중 선택.  
5. [ ] API 서버(parcel-api) 코드 동시 수정 진행 여부.  
6. [ ] 테스트 계획 및 롤백 절차 적절한가?

---

## 7. 다음 단계

승인해 주시면:

1. Phase 1: `save_thumbnail_to_nfs()` 추가 (경로·품질 확정 반영).  
2. Phase 2: `api_helper.api_update_position` 시그니처·payload를 경로 기반으로 수정.  
3. Phase 4: parcel-api `DetectPositionRequest` 및 핸들러를 경로 저장으로 수정.  
4. Phase 6: 테스트 수정 및 문서 갱신.  
5. (선택) Phase 5: main2.py NFS 전환.

이후 필요 시 track/main.py에 썸네일 crop → NFS 저장 → API 연동을 추가하면 됨.
