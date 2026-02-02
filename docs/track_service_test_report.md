# Track 서비스 개선 후 테스트 보고서

## 테스트 개요

- **일시**: (테스트 실행 일시 기입)
- **테스트 시간**: (예: 1.5시간)
- **환경**: 200 서버, 4개 카메라 (1 USB + 3 Pi)
- **수정 적용**: Phase 1~4 (시간순 버퍼, PENDING 재확인, threshold, 예외 처리)

## 수정 내용 요약

1. **프레임 시간순 처리**: `TimeOrderedFrameBuffer` 도입, `get_oldest()` 기준 소비
2. **PENDING 재확인**: `resolve_pending`에서 `uids[next_cam]` 존재 시 DISAPPEAR 미반환
3. **DISAPPEAR threshold**: `PENDING_EXTRA_MARGIN_SEC = 10` 추가 (구간별 expected + 10초)
4. **예외 처리**: stale frame(30초) 시 resolve 생략, now_s가 min_consumed + 5초 초과 시 resolve 생략

## 테스트 결과

### 1. 카메라 수신 성능

(monitoring/analyze_results.py 결과 또는 수동 측정 결과 붙여넣기)

```
[Camera Reception]
  Camera 0: ...
  Camera 1: ...
  ...
```

### 2. Latency 측정

(결과 붙여넣기)

### 3. API 호출 통계

(결과 붙여넣기)

- **detect-disappear 호출**: 개선 전 대비 (목표 90% 이상 감소)

### 4. 개선 효과

- disappeared 오판: **개선 전 X건/시간 → 개선 후 Y건/시간** (Z% 감소)
- tracking ID 유지율: (카메라 간 이동 시나리오 결과)
- 평균 latency: (ms)

### 5. 검증 항목 체크

- [ ] 4개 카메라 모두 프레임 수신 정상 (FPS 15+)
- [ ] 프레임 드롭률 5% 이하
- [ ] 카메라 간 이동 시 ID 유지율 95%+
- [ ] detect-disappear 호출 90% 이상 감소
- [ ] 프레임 수신 실패/지연 시 resolve 생략 동작 확인

### 6. 발견된 문제점 (있다면)

(문제 설명 및 추가 개선 방안)

## 결론

(개선 성공 여부 및 향후 계획)

---

*보고서 템플릿. 실제 테스트 후 결과로 채워서 사용.*
