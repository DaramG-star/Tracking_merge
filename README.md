# track – Multi-camera tracking

Real-time multi-camera tracking: ZMQ + local USB camera ingest, YOLO detection, FIFO matcher, ScannerListener and API integration. Self-contained under `track/` (no dependency on parent `apsr`, `zMQ`, or `trackingLogic`).

## Requirements

- Python 3.8+
- USB camera and/or Raspberry Pi cameras streaming via ZMQ
- YOLO model: `parcel_ver0123.pt` in `track/` (or symlink from `trackingLogic/Tracking_test1/`)
- Optional: Scanner/API server for scan events and position updates

## Install

From the `track/` directory:

```bash
pip install -r requirements.txt
```

(Use a virtual environment if desired: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.)

## Configuration

- **설정은 `config.py` 단일 소스**입니다. `config.json`은 사용하지 않습니다.
- **config.py**: 경로, ingest(ZMQ/로컬 USB/stream), 트래킹 파라미터, API/Scanner 등 전부. `RBP_CLIENTS`, `LOCAL_USB_CAMERAS`, `STREAM_USE_LZ4`, `CAM_SETTINGS`, `ZMQ_CAM_MAPPING`, `API_BASE_URL`, `SCANNER_HOST`/`SCANNER_PORT` 등. 환경에 맞게 수정.

## Run

From the `track/` directory:

```bash
python3 main.py [--csv] [--video] [--display]
```

- **No options**: Tracking only; ScannerListener and API calls are always enabled.
- **--csv**: Write tracking logs to `output/Parcel_Integration_Log_FIFO/tracking_logs_live.csv`.
- **--video**: Save per-camera videos under `output/.../videos/`.
- **--display**: Show real-time camera windows (press `q` in a window to quit).

To run without waiting for the first scanner event, set `WAIT_FOR_FIRST_SCAN = False` in `config.py`.

## Tests

From the `track/` directory:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests cover: `ingest.config_loader`, `ingest.frame_aggregator`, `logic.matcher`, `logic.utils`.

## Layout

- **main.py**: Entry point; ZMQ/USB ingest, aggregator, detector, matcher, ScannerListener, main loop.
- **config.py**: 단일 설정 소스 (경로, ingest, 트래킹, API/Scanner).
- **ingest/**: Config loader, frame receiver (ZMQ), USB camera worker, frame aggregator.
- **logic/**: YOLO detector, FIFO matcher, visualizer, API helper, scanner listener, utils.
- **docs/**: Design and development docs.
- **tests/**: Unit tests.

Design details: see `docs/01_PROJECT_OVERVIEW.md` and `docs/03_DESIGN_SPEC.md`.
