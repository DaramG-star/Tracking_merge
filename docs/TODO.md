# track TODO (개발 승인 후 사용)

아래는 04_DEVELOPMENT_PLAN.md의 단계별 TODO를 한곳에 모은 체크리스트입니다. 승인 후 개발 시 이 파일을 기준으로 진행해도 됩니다.

## M1: track 뼈대 및 설정

- [ ] `track/` 루트 생성
- [ ] `track/config.py` (경로·CAM_SETTINGS·ZMQ_CAM_MAPPING 등, track 기준)
- [ ] `track/config.json` (rbp_clients, local_usb_cameras, stream)
- [ ] `track/requirements.txt`
- [ ] `track/parcel_ver0123.pt` 복사 또는 배치

## M2: ingest 모듈

- [ ] `track/ingest/__init__.py`
- [ ] `track/ingest/config_loader.py`
- [ ] `track/ingest/frame_receiver.py`
- [ ] `track/ingest/usb_camera_worker.py`
- [ ] `track/ingest/frame_aggregator.py`

## M3: logic 모듈 이전

- [ ] `track/logic/__init__.py`
- [ ] `track/logic/detector.py`
- [ ] `track/logic/matcher.py`
- [ ] `track/logic/visualizer.py`
- [ ] `track/logic/api_helper.py`
- [ ] `track/logic/scanner_listener.py`
- [ ] `track/logic/utils.py`

## M4: main.py 통합

- [ ] `track/main.py` (4캠 수신 → aggregator → 메인 루프 → YOLO/Tracking)
- [ ] CLI 옵션: `--csv`, `--video`, `--display`만 옵션(미지정 시 비활성화). ScannerListener·API 호출은 필수.
- [ ] 타임스탬프: sub-second 단위(목표 250ms, 멀티캠 처리 시간 고려).
- [ ] Grayscale/BGR 처리
- [ ] 실행 테스트 (4캠 수신·트래킹 동작)

## M5: 독립 실행 검증

- [ ] track 내부에 상위 경로(apsr, zMQ, trackingLogic) import 없음 확인
- [ ] track/ 복사본으로 다른 경로에서 실행 검증

## M6: 문서·정리

- [ ] `track/README.md`
- [ ] 05_DEPENDENCIES_AND_CONFIG 체크리스트 반영
