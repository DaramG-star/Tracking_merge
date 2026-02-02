# api_helper.py - track/logic
import sys
from pathlib import Path
from typing import Any, Optional

import requests

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config
from logic.utils import save_thumbnail_to_nfs

BASE_URL = getattr(config, "API_BASE_URL", "http://192.168.1.100:8000/api")
_missing_api_count = 0


def api_scan(uid, route_code):
    try:
        payload = {"uid": uid, "route_code": route_code}
        requests.post(f"{BASE_URL}/track", json=payload, timeout=2)
    except Exception as e:
        print(f"API Error (Scan): {e}")


def api_update_position(
    uid: str,
    pos: float,
    thumbnail_image: Optional[Any] = None,
):
    """
    PATCH /api/detect-position.
    100 서버 스펙: 요청 body = {"uid": "<string>", "position": <float>} 만 사용. thumbnail 파라미터 없음.
    thumbnail_image가 주어지면 NFS에만 저장 (/mnt/thumbnails/{uid}.jpg). API에는 보내지 않음.
    """
    try:
        if thumbnail_image is not None:
            save_thumbnail_to_nfs(uid, thumbnail_image)
        payload = {"uid": uid, "position": pos}
        requests.patch(f"{BASE_URL}/detect-position", json=payload, timeout=2)
    except Exception as e:
        print(f"API Error (Position Update): {e}")


def api_pickup(uid):
    try:
        requests.patch(f"{BASE_URL}/detect-pickup", json={"uid": uid, "received": True}, timeout=2)
    except Exception as e:
        print(f"API Error (Pickup): {e}")


def api_missing(uid):
    global _missing_api_count
    _missing_api_count += 1
    try:
        requests.patch(f"{BASE_URL}/detect-missing", json={"uid": uid, "missed": True}, timeout=2)
    except Exception as e:
        print(f"API Error (Missing): {e}")


def api_eol(uid):
    try:
        requests.delete(f"{BASE_URL}/detect-eol/{uid}", timeout=2)
    except Exception as e:
        print(f"API Error (EOL): {e}")


def get_missing_api_count():
    return _missing_api_count


def api_disappear(uid):
    try:
        requests.patch(
            f"{BASE_URL}/detect-disappear",
            json={"uid": uid, "disappear": True},
            timeout=2
        )
    except Exception as e:
        print(f"API Error (Disappear): {e}")
