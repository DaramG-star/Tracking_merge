# 썸네일 base64 규격 및 적용 계획

**NFS 전환 완료 (현재)**  
detect-position은 이제 **base64 대신 NFS 파일 경로**를 사용한다.  
- 클라이언트: 썸네일 이미지 → `/mnt/thumbnails/{uid}.jpg` 저장 → API에 **thumbnail_path** (`/thumbnails/{uid}.jpg`) 전송.  
- 서버: MongoDB `thumbnail` 필드에 경로 문자열만 저장.  
- 상세: `track/docs/thumbnail_storage_nfs_migration_plan.md`

---

## 1. 규격 (문서 반영) — base64 (레거시/테스트용)

detect-position으로 **과거** 전송하던 썸네일 base64 문자열 규격 (NFS 저장 시에도 동일 리사이즈·품질 적용 가능).

| 항목 | 값 | 비고 |
|------|-----|------|
| **리사이즈** | **80×80** | base64 인코딩 전에 `cv2.resize(img, (80, 80))` 적용. 용량·전송량 축소. |
| **JPEG 품질** | **70** | `cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])`. 기존 80 → 70으로 변경. |

- 적용 순서: **원본(또는 crop) → 80×80 리사이즈 → JPEG 품질 70 인코딩 → base64**.
- 빈 이미지/크롭 실패 시 base64 생성 생략(또는 None 반환 후 API 호출 시 thumbnail 미포함).
- **요청 필드명 (NFS 전환 후)**: parcel-api는 **`thumbnail_path`** (예: `/thumbnails/{uid}.jpg`)를 받음. `api_update_position`은 **payload["thumbnail_path"]** 로 전송.

---

## 2. 적용 대상

| 대상 | 현재 상태 | 적용 내용 |
|------|-----------|-----------|
| **trackingLogic/Tracking_test1/main2.py** | `get_base64_image(img)` 에서 품질 80, 리사이즈 없음 | 품질 70, 리사이즈 80×80 반영 |
| **track/logic/api_helper.py** | detect-position 요청 | **thumbnail_path** (NFS 경로) 전송. `thumbnail_image` 주면 NFS 저장 후 경로 전송. 완료. |
| **track/logic/utils.py** | NFS 저장 | `save_thumbnail_to_nfs(uid, img)` — 80×80, JPEG 85, `/mnt/thumbnails/{uid}.jpg`. 완료. |
| **track** (향후 썸네일 구현 시) | 썸네일 전송 로직 없음 | crop → `save_thumbnail_to_nfs` → `api_update_position(..., thumbnail_path=path)` 또는 `thumbnail_image=img` |

---

## 3. 코드 적용 계획 (승인 후 구현)

### 3.1 trackingLogic/Tracking_test1/main2.py

- **함수**: `get_base64_image(img)`.
- **변경**:
  1. `img`가 비어 있거나 크기가 0이면 `None` 반환.
  2. `cv2.resize(img, (80, 80))` 로 80×80으로 축소.
  3. `cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 70])` (기존 80 → 70).
  4. `base64.b64encode(buffer).decode('utf-8')` 그대로 사용.
- **영향**: main2.py에서 crop 이미지를 썸네일로 보낼 때 80×80, 품질 70으로 전송됨.

### 3.2 track 쪽 (썸네일 구현 시 사용할 규격)

- **공용 헬퍼 위치**: `track/logic/utils.py` (또는 `track/logic/image_utils.py`).
- **함수 시그니처 예**:  
  `def image_to_thumbnail_base64(img, max_size=(80, 80), jpeg_quality=70) -> Optional[str]:`
  - 내부: 80×80 리사이즈 → JPEG 품질 70 → base64 반환. 실패 시 `None`.
- **사용 시점**: main.py에서 detect-position용 썸네일을 넣을 때(예: position 업데이트 직전에 해당 물체 crop → `image_to_thumbnail_base64(crop)` → `api_update_position(..., image_base64=...)`).  
  (썸네일 생성·전달 로직 자체는 별도 작업이므로, 본 계획에서는 **규격과 헬퍼 추가**만 명시.)

### 3.3 문서 갱신

- **track/docs/detect_position_thumbnail_analysis.md**:  
  - "썸네일 base64 규격" 문단 추가 → 80×80 리사이즈, JPEG 품질 70, 적용 순서 명시.
- **본 문서 (thumbnail_base64_spec_and_plan.md)**:  
  - 위 규격·대상·계획을 유지(이미 반영됨).

---

## 4. 구현 순서 (승인 후)

1. **main2.py**: `get_base64_image` 에 80×80 리사이즈 + JPEG 품질 70 반영.
2. **track**: `logic/utils.py`(또는 동의한 모듈)에 `image_to_thumbnail_base64(img, max_size=(80, 80), jpeg_quality=70)` 추가.
3. **문서**: `detect_position_thumbnail_analysis.md` 에 썸네일 base64 규격(80×80, 품질 70) 섹션 추가.

이후 track에서 썸네일 전송을 구현할 때는 이 헬퍼와 규격을 사용하면 됨.

---

## 5. 요약

- **규격**: 리사이즈 **80×80**, JPEG 품질 **70**.
- **즉시 적용**: `trackingLogic/Tracking_test1/main2.py` 의 `get_base64_image`.
- **track 적용**: 공용 헬퍼 추가 + (별도 작업) main.py에서 crop → 헬퍼 → api_update_position(thumbnail) 연동.

검토 후 승인해 주시면 위 순서대로 코딩 반영하겠습니다.
