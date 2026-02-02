# scanner_listener.py - track/logic
import json
import re
import threading
import time
import sys
from pathlib import Path
from datetime import datetime

import socketio

_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config
from .matcher import FIFOGlobalMatcher


class ScannerListener:
    def __init__(self, matcher: FIFOGlobalMatcher, host=None, port=None,
                 max_retry_time=300, retry_interval=5):
        self.matcher = matcher
        self.host = host or getattr(config, "SCANNER_HOST", "192.168.1.100")
        self.port = port if port is not None else getattr(config, "SCANNER_PORT", 8000)
        self.max_retry_time = max_retry_time
        self.retry_interval = retry_interval
        self.running = False
        self.thread = None
        self.first_retry_time = None

        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=self.retry_interval,
            reconnection_delay_max=self.retry_interval * 2
        )
        self._register_handlers()

    def _register_handlers(self):
        @self.sio.event
        def connect():
            self.first_retry_time = None

        @self.sio.event
        def disconnect():
            if not self.running:
                pass
            else:
                if self.first_retry_time is None:
                    self.first_retry_time = time.time()

        @self.sio.event
        def connect_error(data):
            if self.first_retry_time is None:
                self.first_retry_time = time.time()
            if self.max_retry_time is not None:
                elapsed = time.time() - self.first_retry_time
                if elapsed >= self.max_retry_time:
                    self.running = False
                    self.sio.disconnect()

        @self.sio.on('parcelUpdate')
        def on_parcel_update(data):
            try:
                operation_type = data.get('type') if isinstance(data, dict) else None
                if operation_type == 'insert':
                    self._handle_message(data)
            except Exception as e:
                import traceback
                traceback.print_exc()

    def _parse_timestamp(self, ts_str):
        try:
            m = re.search(r"(?:\d{8}_)?(\d{6}_\d+)", str(ts_str))
            if m:
                ts_part = m.group(1)
                h = int(ts_part[0:2])
                m_val = int(ts_part[2:4])
                s = int(ts_part[4:6])
                ms = int(ts_part.split('_')[1]) / 1000
                return h * 3600 + m_val * 60 + s + ms
            if isinstance(ts_str, (int, float)):
                return float(ts_str)
            if 'T' in str(ts_str):
                dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
                return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1000000
            return time.time()
        except Exception:
            return time.time()

    def _handle_message(self, data):
        try:
            if isinstance(data, str):
                message = json.loads(data)
            elif isinstance(data, dict):
                message = data
            else:
                return

            data_dict = message.get('data') or message.get('fullDocument') or message
            if isinstance(data_dict, dict):
                uid = data_dict.get('uid') or data_dict.get('_id')
                route_code = data_dict.get('route_code') or data_dict.get('route')
            else:
                uid = route_code = None
            if not uid:
                uid = message.get('uid') or message.get('_id') or message.get('id')
            if not route_code:
                route_code = message.get('route_code') or message.get('route')

            if not uid or not route_code:
                return

            time_s = self._parse_timestamp(uid)
            self.matcher.add_scanner_data(uid, route_code, time_s)
        except Exception as e:
            import traceback
            traceback.print_exc()

    def _connect_loop(self):
        url = f"http://{self.host}:{self.port}"
        while self.running:
            try:
                if not self.sio.connected:
                    if self.first_retry_time is None:
                        self.first_retry_time = time.time()
                    if self.max_retry_time is not None:
                        elapsed = time.time() - self.first_retry_time
                        if elapsed >= self.max_retry_time:
                            self.running = False
                            break
                    try:
                        self.sio.connect(
                            url,
                            wait_timeout=10,
                            socketio_path="/socket.io",
                            transports=["websocket", "polling"]
                        )
                        if self.sio.connected:
                            self.first_retry_time = None
                    except Exception:
                        time.sleep(self.retry_interval)
                else:
                    time.sleep(1)
            except Exception:
                time.sleep(self.retry_interval)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._connect_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.sio.connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=2)
