# detect-position 썸네일(base64) 미동작 원인 정리

**NFS 전환 후 (현재)**  
썸네일은 base64가 아닌 **NFS 파일 경로**로 전송·저장된다.  
- 클라이언트: `save_thumbnail_to_nfs(uid, img)` → `/mnt/thumbnails/{uid}.jpg` 저장 후 `api_update_position(uid, pos, thumbnail_path="/thumbnails/{uid}.jpg")`.  
- 서버: `DetectPositionRequest.thumbnail_path` → MongoDB `thumbnail` 필드에 경로 저장.  
- 상세: `track/docs/thumbnail_storage_nfs_migration_plan.md`

---

## 썸네일 base64 규격 (레거시/저장 규격 참고)

detect-position으로 전송하는 썸네일 base64는 아래 규격을 따른다.

| 항목 | 값 | 비고 |
|------|-----|------|
| **리사이즈** | **80×80** | base64 인코딩 전에 `cv2.resize(img, (80, 80))` 적용. |
| **JPEG 품질** | **70** | `cv2.IMWRITE_JPEG_QUALITY, 70`. |

- **적용 순서**: 원본(또는 crop) → 80×80 리사이즈 → JPEG 품질 70 인코딩 → base64.
- 빈 이미지/크롭 실패 시 base64 생성 생략(또는 None 반환 후 API 호출 시 thumbnail 미포함).
- track 쪽 공용 헬퍼: `track/logic/utils.py` 의 `image_to_thumbnail_base64(img, max_size=(80, 80), jpeg_quality=70)`.

---

## 관련 코드 위치

### 1. track (클라이언트)

| 파일 | 역할 |
|------|------|
| **track/logic/api_helper.py** | `api_update_position(uid, pos, thumbnail_path=None, thumbnail_image=None)` — PATCH `/api/detect-position`, payload에 **thumbnail_path** (NFS 경로) 포함 |
| **track/main.py** | position 갱신 시 `api_update_position(mid, step_dist)` 호출 (2곳: process_one_frame 내부, time_based_position_update) |

### 2. parcel-api (서버)

| 파일 | 역할 |
|------|------|
| **pwa/PWAforDHL/parcel-api/api/hardware.py** | `DetectPositionRequest`: `uid`, `position`, **`thumbnail_path`**(Optional). `PATCH /detect-position`에서 `request.thumbnail_path`가 있으면 `update_data["thumbnail"]`에 경로 저장 |

---

## 원인 (2가지)

### A. 요청 필드 이름 불일치

- **track/api_helper.py** (27행): `payload["image"] = image_base64 ...` → JSON 키 **`"image"`** 로 전송.
- **parcel-api/hardware.py** (31행): `DetectPositionRequest.thumbnail` → Pydantic이 **`"thumbnail"`** 키만 바인딩.
- **결과**: 클라이언트가 `image`로 보내면 서버는 `thumbnail` 필드만 읽기 때문에 **thumbnail이 항상 None** → DB에 thumbnail이 반영되지 않음.

**수정**: `api_helper.py`에서 `payload["image"]` → **`payload["thumbnail"]`** 로 변경.

---

### B. track에서 썸네일을 아예 보내지 않음

- **track/main.py** (316행, 366행): `api_helper.api_update_position(mid, step_dist)` 만 호출 → **세 번째 인자 `image_base64`를 넘기지 않음.**
- 따라서 현재 track 서비스는 detect-position 호출 시 썸네일을 생성·전송하는 로직이 없음.

**참고**: `trackingLogic/Tracking_test1/main2.py` 에는 다음 로직이 있음.
- detection 박스 기준으로 프레임을 crop → `get_base64_image(crop_img)` → `current_thumbnails[mid]`에 저장.
- position 업데이트 시 `api_helper.api_update_position(m_id, step_dist, image_base64=img_b64)` 로 썸네일 전달.

**수정 방향**: main.py에서 position 업데이트 시, 해당 master_id에 대응하는 detection 박스로 현재 프레임을 crop → base64 인코딩 후 `api_update_position(mid, step_dist, image_base64=...)` 로 전달하는 로직 추가 필요. (어느 카메라/프레임에서 crop할지, 4 cam 세트 구조와 어떻게 맞출지 설계 필요.)

---

## 요약

| 구분 | 위치 | 내용 |
|------|------|------|
| **필드명** | track/logic/api_helper.py | `payload["image"]` → **`payload["thumbnail"]`** 로 변경하면, 보낸 base64가 서버에 반영됨. |
| **썸네일 생성·전달** | track/main.py | `api_update_position(mid, step_dist)` 호출만 있음. crop + base64 생성 후 `image_base64` 인자로 넘기는 코드 없음. |

A만 고치면: 다른 클라이언트가 thumbnail을 보낼 때는 서버가 정상 반영.  
B까지 고치면: track이 position 업데이트 시 썸네일을 생성해 보내고, detect-position으로 thumbnail base64가 업데이트되도록 동작함.
