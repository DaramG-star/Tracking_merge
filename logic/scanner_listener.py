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
            print(f"✅ [ScannerListener] Connected to http://{self.host}:{self.port}")
            self.first_retry_time = None

        @self.sio.event
        def disconnect():
            print("❌ [ScannerListener] Disconnected from server")
            if self.running and self.first_retry_time is None:
                self.first_retry_time = time.time()

        @self.sio.event
        def connect_error(data):
            print(f"⚠️ [ScannerListener] Connection Error: {data}")
            if self.first_retry_time is None:
                self.first_retry_time = time.time()
            if self.max_retry_time is not None:
                elapsed = time.time() - self.first_retry_time
                if elapsed >= self.max_retry_time:
                    print("🚫 [ScannerListener] Max retry time reached. Stopping.")
                    self.running = False
                    self.sio.disconnect()

        @self.sio.on('parcelUpdate')
        def on_parcel_update(data):
            """스캐너 서버로부터 이벤트를 직접 수신하는 지점"""
            print(f"📡 [ScannerListener] Event 'parcelUpdate' received!")
            try:
                # 1. 수신한 원본 데이터 구조 확인을 위한 로그
                # print(f"📦 [ScannerListener] Raw Data: {json.dumps(data, indent=2, ensure_ascii=False)}")
                
                # 2. operation_type 확인 (insert가 아닐 경우를 대비)
                operation_type = data.get('type') if isinstance(data, dict) else None
                if operation_type == 'insert':
                    self._handle_message(data)
                else:
                    print(f"ℹ️ [ScannerListener] Ignored operation type: {operation_type}")
            except Exception as e:
                print(f"🚨 [ScannerListener] Error in on_parcel_update: {e}")
                import traceback
                traceback.print_exc()

    def _parse_timestamp(self, ts_str):
        """UID 문자열에서 오늘 0시 기준 누적 초를 추출"""
        try:
            m = re.search(r"(?:\d{8}_)?(\d{6}_\d+)", str(ts_str))
            if m:
                ts_part = m.group(1)
                h = int(ts_part[0:2])
                m_val = int(ts_part[2:4])
                s = int(ts_part[4:6])
                ms = int(ts_part.split('_')[1]) / 1000
                return h * 3600 + m_val * 60 + s + ms
            
            # 파싱 실패 시 17억 초(Unix Time) 방지를 위해 오늘 기준 초 반환
            print(f"⚠️ [ScannerListener] Regex fail for UID: {ts_str}. Using current time.")
            now = datetime.now()
            return now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1000000
        except Exception as e:
            print(f"🚨 [ScannerListener] Timestamp parse error: {e}")
            now = datetime.now()
            return now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1000000

    def _handle_message(self, data):
        """데이터 파싱 및 Matcher 전달 로직"""
        try:
            if isinstance(data, str):
                message = json.loads(data)
            elif isinstance(data, dict):
                message = data
            else:
                return

            # 데이터 추출 (MongoDB insert 구조 반영)
            data_dict = message.get('data') or message.get('fullDocument') or message
            
            uid = None
            route_code = None
            
            if isinstance(data_dict, dict):
                uid = data_dict.get('uid') or data_dict.get('_id')
                route_code = data_dict.get('route_code') or data_dict.get('route')
            
            # fallback 필드 체크
            if not uid:
                uid = message.get('uid') or message.get('_id') or message.get('id')
            if not route_code:
                route_code = message.get('route_code') or message.get('route')

            if not uid or not route_code:
                print(f"❓ [ScannerListener] Missing UID or Route. UID={uid}, Route={route_code}")
                return

            time_s = self._parse_timestamp(uid)
            
            # 최종 성공 로그
            print(f"✅ [ScannerListener] SUCCESS: UID={uid} | Route={route_code} | Time={time_s:.3f}")
            
            self.matcher.add_scanner_data(uid, route_code, time_s)
            
        except Exception as e:
            print(f"🚨 [ScannerListener] Message handling error: {e}")
            import traceback
            traceback.print_exc()

    def _connect_loop(self):
        url = f"http://{self.host}:{self.port}"
        print(f"🚀 [ScannerListener] Starting connection loop to {url}")
        while self.running:
            try:
                if not self.sio.connected:
                    if self.first_retry_time is None:
                        self.first_retry_time = time.time()
                    
                    try:
                        self.sio.connect(
                            url,
                            wait_timeout=10,
                            socketio_path="/socket.io",
                            transports=["websocket", "polling"]
                        )
                    except Exception:
                        time.sleep(self.retry_interval)
                else:
                    time.sleep(1)
            except Exception as e:
                print(f"🚨 [ScannerListener] Loop error: {e}")
                time.sleep(self.retry_interval)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._connect_loop, daemon=True)
        self.thread.start()

    def stop(self):
        print("🛑 [ScannerListener] Stopping...")
        self.running = False
        if self.sio.connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=2)