# utils.py - track/logic
import os
import sys
import base64
from pathlib import Path
from typing import Optional, Tuple
import re
import cv2
import numpy as np

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config

# NFS 마운트 경로 (200번 서버 → 100번 서버). 썸네일 파일 저장.
THUMBNAIL_NFS_DIR = "/mnt/thumbnails"


def save_thumbnail_to_nfs(
    uid: str,
    thumbnail_image: np.ndarray,
    max_size: Tuple[int, int] = (80, 80),
    jpeg_quality: int = 85,
) -> Optional[str]:
    """
    NFS를 통해 썸네일을 100번 서버에 저장.

    Args:
        uid: 택배 고유 ID
        thumbnail_image: OpenCV 이미지 (numpy array)
        max_size: 저장 전 리사이즈 (기본 80×80)
        jpeg_quality: JPEG 품질 (기본 85)

    Returns:
        성공 시 웹 URL 경로 "/thumbnails/{uid}.jpg", 실패 시 None
    """
    if thumbnail_image is None or not isinstance(thumbnail_image, np.ndarray) or thumbnail_image.size == 0:
        return None
    if not uid or not uid.strip():
        return None
    try:
        out_dir = THUMBNAIL_NFS_DIR
        os.makedirs(out_dir, exist_ok=True)
        filepath = os.path.join(out_dir, f"{uid}.jpg")
        resized = cv2.resize(thumbnail_image, max_size)
        success = cv2.imwrite(
            filepath,
            resized,
            [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
        )
        if not success:
            print(f"[NFS thumbnail] save failed: {filepath}")
            return None
        print(f"[NFS thumbnail] saved: {filepath}")
        return f"/thumbnails/{uid}.jpg"
    except Exception as e:
        print(f"[NFS thumbnail] error: {filepath} — {e}")
        return None


def image_to_thumbnail_base64(
    img: np.ndarray,
    max_size: Tuple[int, int] = (80, 80),
    jpeg_quality: int = 70,
) -> Optional[str]:
    """
    이미지를 리사이즈·JPEG 인코딩 후 base64 문자열로 반환.
    detect-position 썸네일 규격: 80×80, JPEG 품질 70.
    """
    if img is None or not isinstance(img, np.ndarray) or img.size == 0:
        return None
    try:
        resized = cv2.resize(img, max_size)
        _, buffer = cv2.imencode(
            ".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        return base64.b64encode(buffer).decode("utf-8")
    except Exception:
        return None


def extract_ts(name):
    m = re.search(r"(?:\d{8}_)?(\d{6}_\d+)", name)
    return m.group(1) if m else None


def ts_to_seconds(ts):
    try:
        h = int(ts[0:2])
        m = int(ts[2:4])
        s = int(ts[4:6])
        ms_part = ts.split('_')[1]
        ms = int(ms_part) / 1000
        return h * 3600 + m * 60 + s + ms
    except (IndexError, ValueError):
        return 0.0


class VideoManager:
    def __init__(self, video_dir=None):
        self.video_dir = video_dir or config.VIDEO_DIR
        self.writers = {}
        self.enabled = config.SAVE_VIDEO

    def init_writer(self, cam, frame_shape):
        if not self.enabled:
            return
        if cam not in self.writers:
            h, w = frame_shape[:2]
            self.video_dir.mkdir(parents=True, exist_ok=True)
            path = str(self.video_dir / f"{cam}_FIFO_master.mp4")
            self.writers[cam] = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h))

    def write_frame(self, cam, frame):
        if not self.enabled or cam not in self.writers:
            return
        self.writers[cam].write(frame)

    def release_all(self):
        for w in self.writers.values():
            w.release()
        self.writers.clear()
