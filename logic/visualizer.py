# visualizer.py - track/logic
import sys
from pathlib import Path
import cv2

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config


class TrackingVisualizer:
    def __init__(self, enabled=None):
        self.enabled = enabled if enabled is not None else config.SAVE_VIDEO
        self.writers = {}

    def draw_and_write(self, cam, img, detections, masters, frame_ts, active_tracks):
        if not self.enabled:
            return

        disp = img.copy()

        for det in detections:
            x1, y1, x2, y2 = det['box']
            cx, cy = det['center']

            color = (0, 0, 255)
            display_text = "Unmatched"

            for uid, info in active_tracks.get(cam, {}).items():
                if info["last_pos"] == (cx, cy):
                    mid = info["master_id"]
                    if mid and mid in masters:
                        status = masters[mid].get("status")
                        if status == "MISSING":
                            color = (255, 0, 255)
                            display_text = f"!! MISSING !! ID: {mid}"
                        else:
                            color = (0, 255, 0)
                            display_text = f"ID: {mid}"
                    else:
                        color = (0, 255, 255)
                        display_text = uid
                    break

            cv2.rectangle(disp, (x1, y1), (x2, y2), color, 3 if display_text.startswith("!!") else 2)
            (w, h), _ = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(disp, (x1, y1 - 25), (x1 + w, y1), color, -1)
            cv2.putText(disp, display_text, (x1, y1 - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        if cam not in self.writers:
            h, w = disp.shape[:2]
            config.VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            self.writers[cam] = cv2.VideoWriter(
                str(config.VIDEO_DIR / f"{cam}_output.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h)
            )
        self.writers[cam].write(disp)

    def release_all(self):
        for w in self.writers.values():
            w.release()
        self.writers.clear()
