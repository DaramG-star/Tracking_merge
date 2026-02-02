# test_detect_position_api 가 통과하는 이유

## 테스트가 “통과”한다는 의미

- **test_api_helper_sends_thumbnail_path**: mock 사용, 네트워크/파일 시스템 무관. 항상 통과 가능.
- **test_detect_position_with_thumbnail_path**: **실제 NFS 쓰기** + **실제 PATCH /api/detect-position** 호출.  
  이 테스트가 **스킵 없이 OK**라면, 아래 두 가지가 모두 성공했다는 뜻이다.

---

## 1. NFS가 동작해서 테스트가 스킵되지 않음

### 테스트 코드 동작

```python
path = save_thumbnail_to_nfs(TEST_UID, img)
if path is None:
    self.skipTest("NFS 저장 실패 (예: /mnt/thumbnails 미마운트)")
# 이하: payload에 path 넣고 PATCH, assert 2xx
```

- `save_thumbnail_to_nfs()` 가 **None이 아니면** 스킵하지 않고 PATCH까지 진행한다.
- None을 반환하는 경우는 `logic/utils.py` 기준으로:
  - 이미지가 비어 있거나, uid가 비어 있거나
  - **`os.makedirs(THUMBNAIL_NFS_DIR, exist_ok=True)` 또는 `cv2.imwrite(filepath, ...)` 실패**
  - 즉, **`/mnt/thumbnails` 에 쓰기 권한이 없거나, 디렉터리가 없거나, NFS가 안 붙어 있으면** None → 스킵.

### 이전에 스킵됐던 이유 (permission denied)

- 터미널에서 `imwrite_('/mnt/thumbnails/...'): can't open file for writing: permission denied` 가 나왔을 때는:
  - `/mnt/thumbnails` 가 마운트는 되어 있지만, **테스트를 실행한 사용자(piapp)에게 쓰기 권한이 없었던 상태**였을 가능성이 크다.
  - 그때는 `save_thumbnail_to_nfs()` 가 실패 → `path is None` → `skipTest(...)` 로 “스킵”만 되고, PATCH는 실행되지 않았다.

### 지금 통과한다는 것의 의미

- **같은 테스트 코드**인데 스킵 없이 OK가 나왔다는 것은:
  1. **`/mnt/thumbnails` 가 존재하고,**
  2. **NFS가 마운트되어 있고,**
  3. **현재 테스트를 실행한 사용자(piapp@piapp-seoul)가 해당 경로에 쓰기 가능**
- 그래서 `save_thumbnail_to_nfs(TEST_UID, img)` 가 **실제로** `/mnt/thumbnails/20260130_164541_476.jpg` 를 쓰고, **`"/thumbnails/20260130_164541_476.jpg"` 를 반환**했다.
- 즉, “NFS is working” 은 **이 환경(piapp-seoul, 해당 사용자)에서 NFS 쓰기가 성공했다**는 뜻이다.  
  코드를 바꿔서 NFS 없이 통과시키는 식으로 하지 않았고, **환경이 맞아서** 통과한 것이다.

---

## 2. detect-position API가 동작함

- 스킵되지 않았으므로, 테스트는 **반환된 path**로 payload를 만들어 **실제로**  
  `PATCH {API_BASE_URL}/detect-position` 를 호출한다.
- 테스트는 `resp.status_code in (200, 201, 204)` 와 `data.get("success") == True` 를 검사한다.
- **OK가 나왔다는 것은:**
  1. **parcel-api(100번)가 떠 있고**,  
  2. **`config.API_BASE_URL` 이 100번 API를 가리키고**,  
  3. **track 컬렉션에 uid `20260130_164541_476` 문서가 존재하고**,  
  4. **요청 body(uid, position, thumbnail_path)가 서버 스키마와 맞아서**  
  → 2xx + `success: true` 가 반환된 상태라는 뜻이다.

즉, “detect-position was working” 은 **그 시점에 200번 → 100번으로 PATCH가 성공하고, DB 업데이트까지 정상 처리됐다**는 의미다.

---

## 3. 정리: 왜 지금 통과하는가?

| 요인 | 이전 (스킵) | 지금 (OK) |
|------|-------------|-----------|
| **NFS** | `/mnt/thumbnails` 쓰기 실패 (permission denied 등) → `save_thumbnail_to_nfs` → None → 스킵 | 같은 코드인데 NFS 쓰기 성공 → path 반환 → 스킵 안 함 |
| **API** | 스킵되면 PATCH 자체를 안 함 | PATCH 실행 → 2xx + success → assert 통과 |

**코드 변경으로 “NFS 없이 통과”하도록 만든 부분은 없다.**  
테스트는 여전히:

- NFS 쓰기 실패 → `path is None` → 스킵  
- NFS 쓰기 성공 → PATCH 호출 → API 성공 시에만 통과  

그래서 **지금 통과한 이유**는:

1. **piapp-seoul(200번)에서 `/mnt/thumbnails` 가 NFS로 마운트되어 있고, 실행 사용자(piapp)가 쓰기 가능한 상태가 되었고**
2. **100번 parcel-api가 떠 있고, 해당 uid 문서가 있어서 PATCH가 정상 처리되었기 때문**이다.

즉, **환경(NFS 마운트/권한 + API 서버/DB)** 이 맞춰져서, 설계한 대로 “NFS 저장 → detect-position 호출” 전체 플로우가 동작한 것이다.
