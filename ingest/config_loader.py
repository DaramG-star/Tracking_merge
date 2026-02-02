#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
설정 로더. 단일 소스: config.py. (config.json 미사용)
get_rbp_clients(), get_stream_config(), get_local_usb_cameras() 제공.
"""
import sys
from pathlib import Path
from typing import Dict, Any, List

# track 루트를 path에 넣어 config 모듈 로드
_track_root = Path(__file__).resolve().parent.parent
if str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
import config as _config


def get_stream_config() -> Dict[str, Any]:
    """stream 관련 설정. use_lz4 등."""
    return {"use_lz4": getattr(_config, "STREAM_USE_LZ4", True)}


def get_rbp_clients() -> List[Dict[str, Any]]:
    """ZMQ 수신용 RBP 클라이언트 목록."""
    return getattr(_config, "RBP_CLIENTS", [])


def get_local_usb_cameras() -> Dict[str, Any]:
    """로컬 USB 카메라 설정."""
    return getattr(_config, "LOCAL_USB_CAMERAS", {})


class ConfigLoader:
    """
    설정 로더. config.py 단일 소스.
    load()는 호환용 no-op. get_*()는 config 모듈에서 읽음.
    """

    def __init__(self, config_path=None):
        # config_path는 사용하지 않음 (호환용 인자)
        pass

    def load(self) -> Dict[str, Any]:
        """호환용. config.py만 사용하므로 no-op."""
        return {"_source": "config.py"}

    def get_stream_config(self) -> Dict[str, Any]:
        return get_stream_config()

    def get_rbp_clients(self) -> List[Dict[str, Any]]:
        return get_rbp_clients()

    def get_local_usb_cameras(self) -> Dict[str, Any]:
        return get_local_usb_cameras()
