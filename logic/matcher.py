# matcher.py - track/logic
import sys
from pathlib import Path
from collections import deque
import heapq

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config


class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        self.queues = {
            "q_scan": [],
            "q01": deque(), "q12": deque(), "q23": deque(), "q3e": deque()
        }
        self.last_match_attempt = None

    def _get_next_cam(self, route, cam):
        if route == "XSEA":
            order = ["Scanner", "USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]
        else:
            order = ["Scanner", "USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3", "RPI_USB3_EOL"]

        if cam not in order:
            return None
        idx = order.index(cam)
        return order[idx + 1] if idx + 1 < len(order) else None

    def add_scanner_data(self, uid, route_code, time_s):
        mid = uid
        total_dist = config.ROUTE_TOTAL_DIST.get(route_code, 14.08)

        self.masters[mid] = {
            "last_cam": "Scanner",
            "last_time": time_s,
            "last_width": 0,
            "uids": {},
            "route_code": route_code,
            "status": "TRACKING",
            "start_time": time_s,
            "total_dist": total_dist,
            "pending_from_cam": None
        }
        heapq.heappush(self.queues["q_scan"], (mid, route_code))
        # 큐 상태 확인
        print(f"📥 [Matcher] Q_SCAN updated. Current size: {len(self.queues['q_scan'])}")

    def _try_fifo(self, q_key, prev_cam, cam, time_s, width, uid, next_q_key=None):
        is_q_scan = q_key == "q_scan"
        queue = self.queues[q_key]

        if not queue:
            return {"mid": None, "reason": "EMPTY_QUEUE", "prev_cam": prev_cam}

        item = queue[0]
        mid = item[0] if (is_q_scan or isinstance(item, tuple)) else item
        info = self.masters.get(mid)

        if not info:
            return {"mid": None, "reason": "INVALID_MASTER", "prev_cam": prev_cam}

        if cam in info.get("uids", {}):
            info.update({"last_width": width})
            return {
                "mid": mid, "reason": "ALREADY_MATCHED_CONTINUE", 
                "actual_time": round(time_s, 3), "prev_cam": prev_cam
            }
        
        avg_travel = config.AVG_TRAVEL.get((prev_cam, cam), 0)
        expected = info["last_time"] + avg_travel
        margin = config.TIME_MARGIN.get((prev_cam, cam), 2.0)
        diff = time_s - expected

        # 상세 매칭 정보 생성
        attempt_detail = {
            "mid": mid,
            "prev_cam": prev_cam,
            "prev_time": round(info["last_time"], 3),
            "expected_time": round(expected, 3),
            "actual_time": round(time_s, 3),
            "diff": round(diff, 3),
            "margin": margin
        }

        if abs(diff) > margin:
            attempt_detail["mid"] = None
            attempt_detail["reason"] = "OUT_OF_MARGIN"
            return attempt_detail

        if time_s <= info["last_time"]:
            attempt_detail["mid"] = None
            attempt_detail["reason"] = "TIME_REVERSED"
            return attempt_detail

        # 매칭 성공 처리
        if is_q_scan: heapq.heappop(self.queues[q_key])
        else: queue.popleft()

        info.update({
            "last_cam": cam, 
            "last_time": time_s, 
            "last_width": width, 
            "status": "TRACKING"
        })
        info["uids"][cam] = uid
        if info["start_time"] is None: info["start_time"] = time_s
        if next_q_key: self.queues[next_q_key].append(mid)

        attempt_detail["reason"] = "SUCCESS"
        return attempt_detail

    def try_match(self, cam, time_s, width, uid):
        # 반환값이 이제 mid가 아니라 딕셔너리입니다.
        if cam == "USB_LOCAL":
            return self._try_fifo("q_scan", "Scanner", cam, time_s, width, uid, "q01")
        elif cam == "RPI_USB1":
            return self._try_fifo("q01", "USB_LOCAL", cam, time_s, width, uid, "q12")
        elif cam == "RPI_USB2":
            return self._try_fifo("q12", "RPI_USB1", cam, time_s, width, uid, "q23")
        elif cam == "RPI_USB3":
            return self._try_fifo("q23", "RPI_USB2", cam, time_s, width, uid, "q3e")
        elif cam == "RPI_USB3_EOL":
            return self._try_fifo("q3e", "RPI_USB3", cam, time_s, width, uid)
        return {"mid": None, "reason": "UNKNOWN_CAM"}

    def resolve_pending(self, mid, now_s):
        info = self.masters[mid]
        if info["status"] not in ["PENDING", "TRACKING"]:
            return None
        from_cam = info.get("pending_from_cam") or info["last_cam"]
        route = info["route_code"]
        next_cam = self._get_next_cam(route, from_cam)
        if not next_cam:
            return None
        key = (from_cam, next_cam)
        if key not in config.AVG_TRAVEL:
            return None
        extra_margin = getattr(config, "PENDING_EXTRA_MARGIN_SEC", 0)
        expected = info["last_time"] + config.AVG_TRAVEL[key] + config.TIME_MARGIN[key] + extra_margin
        if now_s < expected:
            return None
        if route == "XSEA":
            decision = "PICKUP" if next_cam in ["RPI_USB2", "RPI_USB3"] else "DISAPPEAR"
        elif route == "XSEB":
            decision = "PICKUP" if next_cam in ["RPI_USB3", "RPI_USB3_EOL"] else "DISAPPEAR"
        else:
            decision = "DISAPPEAR"
        # Phase 2: 재확인 — 이미 다음 카메라에서 이 master가 매칭되었으면 DISAPPEAR 하지 않음
        if decision == "DISAPPEAR" and info.get("uids") and info["uids"].get(next_cam):
            info["status"] = "TRACKING"
            info["pending_from_cam"] = None
            self.cancel_pending(from_cam, mid)
            return None
        info["status"] = decision
        self.cancel_pending(from_cam, mid)
        return {"decision": decision, "from_cam": from_cam, "next_cam": next_cam, "expected": expected}

    def cancel_pending(self, from_cam, mid):
        q_map = {"Scanner": "q_scan", "USB_LOCAL": "q01", "RPI_USB1": "q12", "RPI_USB2": "q23", "RPI_USB3": "q3e"}
        q_key = q_map.get(from_cam)
        if q_key and self.queues[q_key]:
            if q_key == "q_scan":
                if self.queues[q_key][0][0] == mid:
                    heapq.heappop(self.queues[q_key])
                    return True
            else:
                if self.queues[q_key][0] == mid:
                    self.queues[q_key].popleft()
                    return True
        return False
