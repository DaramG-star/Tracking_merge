# detect-position 전체 호출 흐름 및 MongoDB 반영 검증

## 1. 호출 체인 (요약)

```
[클라이언트]
  track/main.py
    → api_helper.api_update_position(mid, step_dist, thumbnail_path=current_thumbnail_paths.get(mid))
  또는
  track/scripts/patch_detect_position_manual.py
    → api_helper.api_update_position(uid, pos, thumbnail_path=f"/thumbnails/{uid}.jpg")
  또는
  track/tests/test_detect_position_api.py
    → requests.patch(url, json={uid, position, thumbnail_path})

[track/logic/api_helper.py]
  BASE_URL = config.API_BASE_URL  # 기본 http://192.168.1.100:8000/api
  payload = {"uid": uid, "position": pos, "thumbnail_path": path}  # path는 항상 설정됨
  requests.patch(f"{BASE_URL}/detect-position", json=payload, timeout=2)

[parcel-api · 100번]
  PATCH /api/detect-position
  → api/hardware.py detect_position(request: DetectPositionRequest)
  → request.uid, request.position, request.thumbnail_path (우선), request.thumbnail (legacy base64)
  → 콘솔 로그: [Hardware API] detect-position received: uid=..., position=..., thumbnail_path=..., thumbnail_len=...
  → track_collection.update_one({"uid": request.uid}, {"$set": { "position": ..., "thumbnail": ... }})
  → thumbnail_path 있으면 thumbnail 필드에 경로 저장, 없고 thumbnail 있으면 base64 저장
```

## 2. MongoDB에 position/thumbnail이 안 보일 때 점검

| 단계 | 확인 항목 |
|------|-----------|
| 1. PATCH가 호출되는가? | track 서비스가 해당 uid로 TRACKING/PENDING 상태에서 api_update_position을 호출하는지, 또는 수동 스크립트/테스트를 실행했는지 |
| 2. 올바른 API로 가는가? | track의 config.API_BASE_URL이 실제 parcel-api 주소인지 (예: http://192.168.1.100:8000/api). 다른 포트/호스트면 다른 DB에 쓸 수 있음 |
| 3. parcel-api가 받는가? | parcel-api 콘솔에 `[Hardware API] detect-position received: uid=..., position=..., thumbnail_path=...` 로그가 찍히는지 |
| 4. 문서가 있는가? | track 컬렉션에 해당 uid 문서가 있어야 함. 없으면 404. POST /track으로 먼저 생성됨 |
| 5. 같은 DB를 보는가? | parcel-api가 연결한 MongoDB와 사용자가 조회하는 DB/컬렉션이 동일한지 |

## 3. 즉시 검증 절차

1. **수동 PATCH 한 번 호출**
   ```bash
   cd /home/piapp/apsr/track
   python3 scripts/patch_detect_position_manual.py 20260130_164541_476 5.3
   ```
2. **parcel-api 로그 확인**  
   같은 시점에 100번 서버에서 parcel-api를 실행 중이라면, 콘솔에  
   `[Hardware API] detect-position received: uid=20260130_164541_476, position=5.3, thumbnail_path=/thumbnails/20260130_164541_476.jpg`  
   가 나와야 함. 안 나오면 요청이 다른 서버로 가거나 API가 꺼져 있는 것.
3. **MongoDB에서 문서 확인**
   ```javascript
   db.track.findOne({ uid: "20260130_164541_476" })
   ```
   `position`, `thumbnail` 필드가 갱신되었는지 확인.  
   (컬렉션 이름은 parcel-api config의 `collections["track"]` 값과 동일해야 함.)

## 4. 기존 문서에 position/thumbnail이 없는 이유 (가능성)

- **POST /track 시점에 스키마에 없었음**  
  예전 버전에서는 track 문서에 `position`, `thumbnail` 필드를 넣지 않고 만들었을 수 있음.  
  그러면 문서는 uid만 있고, **PATCH /detect-position이 한 번이라도 성공해야** `$set`으로 두 필드가 추가됨.
- **PATCH가 한 번도 호출되지 않음**  
  track 서비스가 해당 uid로 동작하지 않거나, 수동/테스트 호출을 하지 않았을 수 있음.
- **다른 API/DB로 요청이 감**  
  track의 API_BASE_URL이 100번 parcel-api가 아닌 다른 주소를 가리키면, 갱신은 다른 MongoDB에 적용됨.

## 5. 코드 위치 (참고)

| 역할 | 파일: 위치 |
|------|------------|
| API 호출 | track/logic/api_helper.py: api_update_position, payload 구성 및 requests.patch |
| URL 설정 | track/config.py: API_BASE_URL |
| 수동 PATCH | track/scripts/patch_detect_position_manual.py |
| 서버 수신·DB 갱신 | pwa/PWAforDHL/parcel-api/api/hardware.py: DetectPositionRequest, detect_position, update_one |
