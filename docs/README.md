# track 문서 인덱스

track 프로젝트의 설계·개발 계획 문서입니다. **구현 전** 요구사항·아키텍처·설계·TODO를 정리한 뒤 검토·승인 후 개발합니다.

| 문서 | 내용 |
|------|------|
| [01_PROJECT_OVERVIEW.md](01_PROJECT_OVERVIEW.md) | 현재 상황, 요구사항, 목표, 제약 |
| [02_ARCHITECTURE.md](02_ARCHITECTURE.md) | 시스템 구성, 데이터 흐름, 카메라 매핑 |
| [03_DESIGN_SPEC.md](03_DESIGN_SPEC.md) | 디렉터리 구조, 모듈·인터페이스, 설정 스키마 |
| [04_DEVELOPMENT_PLAN.md](04_DEVELOPMENT_PLAN.md) | 마일스톤, 단계별 TODO, 작업 순서 |
| [05_DEPENDENCIES_AND_CONFIG.md](05_DEPENDENCIES_AND_CONFIG.md) | 의존성, config, 독립 실행 체크리스트 |

## 요약

- **목표**: main.py에서 서버 USB cam0 + zMQ cam1/cam2/cam3를 실시간 수신하고, YOLO + Multi-camera tracking 수행. 기존 코드를 **track/** 안으로 구조화하고, **다른 PC에 track/ 만 복사해 실행 가능**하도록 함.
- **진행**: 위 문서 검토·승인 후 [04_DEVELOPMENT_PLAN.md](04_DEVELOPMENT_PLAN.md)의 TODO 순서대로 개발.
