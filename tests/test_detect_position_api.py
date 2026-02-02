#!/usr/bin/env python3
"""
API Assert 테스트: PATCH /api/detect-position + NFS 썸네일 저장

100 서버 스펙: 요청 body = {"uid": "<string>", "position": <float>} 만 사용. thumbnail 파라미터 없음.
NFS: /mnt/thumbnails/{uid}.jpg 저장 후 로그 확인.

테스트 데이터:
  uid = '20260130_164541_476'
  position = 7.5

사전 조건: track 컬렉션에 uid 문서가 있어야 함. NFS 저장 테스트 시 /mnt/thumbnails 쓰기 가능해야 함.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import requests

# track 루트를 path에 추가
_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))

import config
from logic.api_helper import api_update_position
from logic.utils import save_thumbnail_to_nfs

# 테스트 데이터 (시나리오 명세)
TEST_UID = "20260130_164541_476"
TEST_POSITION = 7.5
TEST_IMAGE_PATH = _track_root / "tests" / "parcel.jpeg"


class TestDetectPositionApi(unittest.TestCase):
    """PATCH /api/detect-position API Assert 테스트 (100 서버: uid, position 만) + NFS 저장 로그 확인."""

    @classmethod
    def setUpClass(cls):
        cls.base_url = getattr(config, "API_BASE_URL", "http://192.168.1.100:8000/api")
        cls.url = f"{cls.base_url}/detect-position"

    def test_detect_position_uid_position_only(self):
        """uid, position 만으로 API 호출 후 응답 검증 (thumbnail 파라미터 없음)."""
        payload = {"uid": TEST_UID, "position": TEST_POSITION}
        resp = requests.patch(self.url, json=payload, timeout=5)
        self.assertIn(
            resp.status_code,
            (200, 201, 204),
            f"API 실패: status={resp.status_code} body={resp.text}",
        )
        if resp.text:
            data = resp.json()
            self.assertTrue(data.get("success", False), f"응답 success 아님: {data}")

    def test_api_helper_sends_uid_position_only(self):
        """api_update_position이 payload에 uid, position 만 전송하는지 검증 (100 서버 스펙)."""
        with patch("logic.api_helper.requests.patch") as mock_patch:
            api_update_position(TEST_UID, TEST_POSITION)
            mock_patch.assert_called_once()
            call_kwargs = mock_patch.call_args[1]
            self.assertIn("json", call_kwargs)
            payload = call_kwargs["json"]
            self.assertEqual(payload.get("uid"), TEST_UID)
            self.assertEqual(payload.get("position"), TEST_POSITION)
            self.assertNotIn("thumbnail", payload, "100 서버: thumbnail 파라미터 보내지 말 것")
            self.assertNotIn("thumbnail_path", payload, "100 서버: thumbnail_path 파라미터 보내지 말 것")

    def test_nfs_save_then_api(self):
        """NFS에 {uid}.jpg 저장 후 API 호출 — 로그에서 [NFS thumbnail] saved/error 확인."""
        self.assertTrue(TEST_IMAGE_PATH.exists(), f"테스트 이미지 없음: {TEST_IMAGE_PATH}")
        img = cv2.imread(str(TEST_IMAGE_PATH))
        self.assertIsNotNone(img, "parcel.jpeg 로드 실패")
        path = save_thumbnail_to_nfs(TEST_UID, img)
        if path is None:
            self.skipTest("NFS 저장 실패 — 로그 [NFS thumbnail] error 확인, /mnt/thumbnails 권한/마운트 확인")
        self.assertEqual(path, f"/thumbnails/{TEST_UID}.jpg")
        payload = {"uid": TEST_UID, "position": TEST_POSITION}
        resp = requests.patch(self.url, json=payload, timeout=5)
        self.assertIn(
            resp.status_code,
            (200, 201, 204),
            f"API 실패: status={resp.status_code} body={resp.text}",
        )
        if resp.text:
            data = resp.json()
            self.assertTrue(data.get("success", False), f"응답 success 아님: {data}")


if __name__ == "__main__":
    unittest.main()
