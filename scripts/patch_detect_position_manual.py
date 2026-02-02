#!/usr/bin/env python3
"""
수동 PATCH /api/detect-position — 100 서버 스펙: uid, position 만 전송.

전송 payload: { "uid": "<string>", "position": <float> } (thumbnail 파라미터 없음)
응답(상태/본문)을 출력하므로 404/500/연결 실패 원인 확인 가능.

사용: track 루트에서
  python3 scripts/patch_detect_position_manual.py
  python3 scripts/patch_detect_position_manual.py 20260130_164541_476 5.3
"""
import sys
from pathlib import Path

track_root = Path(__file__).resolve().parent.parent
if str(track_root) not in sys.path:
    sys.path.insert(0, str(track_root))

import config
import requests

def main():
    uid = sys.argv[1] if len(sys.argv) > 1 else "20260130_164541_476"
    try:
        pos = float(sys.argv[2]) if len(sys.argv) > 2 else 5.3
    except ValueError:
        pos = 5.3
    base_url = getattr(config, "API_BASE_URL", "http://192.168.1.100:8000/api")
    url = f"{base_url}/detect-position"
    payload = {"uid": uid, "position": pos}

    print("PATCH /api/detect-position (100 서버 스펙: uid, position 만)")
    print(f"  URL: {url}")
    print(f"  payload: {payload}")
    try:
        resp = requests.patch(url, json=payload, timeout=5)
        print(f"  status_code: {resp.status_code}")
        print(f"  response: {resp.text}")
        if resp.status_code in (200, 201, 204):
            data = resp.json() if resp.text else {}
            if data.get("success"):
                print("→ API 성공. MongoDB에서 db.track.findOne({uid: \"%s\"}) 로 position 확인." % uid)
            else:
                print("→ API가 success=false 반환. 위 response 확인.")
        elif resp.status_code == 404:
            print("→ 404: 해당 uid 문서가 track 컬렉션에 없음. POST /api/track 으로 먼저 등록하거나 uid 확인.")
        else:
            print("→ 비정상 응답. parcel-api 로그 또는 위 response 확인.")
    except requests.exceptions.ConnectionError as e:
        print(f"  error: Connection failed — {e}")
        print("→ parcel-api(100번)가 실행 중인지, URL(%s)이 맞는지 확인." % base_url)
    except Exception as e:
        print(f"  error: {e}")

if __name__ == "__main__":
    main()
