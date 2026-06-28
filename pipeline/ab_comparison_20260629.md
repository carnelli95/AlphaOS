# Stage D A/B 비교 실험 — 1차 검증 결과

**실험일:** 2026-06-29  
**브랜치:** feature/stage-e-ab-comparison  
**실험 스크립트:** `pipeline/ab_comparison.py`  
**결과 파일:** `pipeline/ab_comparison_result.json`

---

## 실험 목적

Stage D(DART 수시공시 이벤트 레이어)가 Investment Thesis 품질을 실제로 향상시키는지 소규모 표본으로 확인.  
Stage D는 Alpha Score 향상이 목적이 아니라, **최신 공시를 Thesis에 반영하는 보완 레이어**가 설계 의도였음.

---

## 방법

- Stage C only: `build_thesis_prompt(stage_d=None)` — DB 저장 없음, 메모리 생성
- Stage C+D: 기존 DB의 Thesis (Stage D 통합 후 재생성된 버전)
- 대상: 30일 이내 `financial_impact=high` 이벤트 보유 종목 중 이벤트 유형별 균등 선택

---

## 표본 개요

| 항목 | 내용 |
|------|------|
| 대상 종목 수 | 8개 (목표 20개, 유형별 데이터 부족으로 8개 선택됨) |
| 이벤트 유형 | contract 1개, finance 2개, management 4개, risk 1개 |
| 성공 | 8/8 |

---

## 정량 결과

| 측정 항목 | 수치 | 비고 |
|----------|------|------|
| recent_events 추가 | 5/8 (62%) | Stage C only에서 null → Stage C+D에서 값 있음 |
| dart risks 증가 | 2/8 (25%) | Stage C+D에서 dart 소스 리스크 수 증가 |
| 변화 없음 | 1/8 (12%) | 세기상사 — 정정공시만 존재, Stage D 효과 없음 |

### 보정 관찰

Stage C only에서 이미 `recent_events`가 생성된 케이스 2건 존재:
- **대한항공(003490)**: 사업보고서 내 "인천-푸저우 노선 신규 취항" 발췌 → Stage C only에도 recent_events 있음
- **아시아나항공(020560)**: 사업보고서 내 "A321NEO 도입" 발췌 → Stage C only에도 recent_events 있음

이 2건은 Stage D가 `recent_events`를 추가한 것이 아니라 **내용을 교체**한 케이스.  
(예: 아시아나 — C: 기재 도입 → C+D: 합병 결정 공시로 대체)  
→ 이 2건은 recent_events를 새로 생성한 것이 아니라, 사업보고서 기반 내용을 더 최신의 수시공시 기반 내용으로 교체한 사례였다.  
  (참고 해석: 이 기준까지 포함하면 Stage D가 최신성에 기여한 비율은 최대 7/8로 볼 수도 있으나, 집계 기준에 따라 다를 수 있음)

---

## 케이스별 하이라이트

### ✅ 가장 효과적인 케이스: 콘텐트리중앙(036420) — risk

| | Stage C only | Stage C+D |
|---|---|---|
| recent_events | null | 회생절차 개시 신청 보고서 발표됨 |
| risks | 2개 (COVID, 경쟁심화) | 3개 (+회생절차 리스크) |
| investment_point | 영업이익률 5.6%, 콘텐트 매출 60% | 동일 — Stage C 근거 유지 |

→ Stage C만으로는 회생절차 신청을 알 수 없음. Stage D가 핵심 리스크를 보완.

### ✅ 수시공시로 교체: 아시아나항공(020560) — management

- Stage C only: 항공기 도입 이력 (사업보고서 발췌)
- Stage C+D: 합병 결정 공시 (수시공시)

→ 더 최신성 있는 이벤트로 대체됨. Stage D 설계 의도 부합.

### ⚠ 효과 없음: 세기상사(002420) — management

- Stage D facts가 모두 "정정 공시"만 존재 → LLM이 신규 이벤트로 판단하지 않음
- C only와 C+D 모두 recent_events=null, dart_risks=2 (동일)

→ 허용 가능한 edge case. 정정 공시 처리 정책을 향후 문서화 필요.

### ⚠ 해석적 표현 잔존: 광명전기(017040)

- C+D recent_events: "감자결정을 공시했다는 점에서 재무적 영향을 **미칠 것으로 보인다**"
- 기술 부채로 기록됨 — 사실형 문장("감자결정을 공시하였다")으로 개선 대상

---

## 한계 및 주의사항

1. **표본 8개로 통계적 유의성 없음** — 방향성 확인 수준
2. **이벤트 유형 편향** — management 4개, operations 0개
3. **블라인드 평가 없음** — 사람 또는 LLM 심사 평가 미수행
4. **Stage C only에서도 recent_events 생성 가능** — LLM이 사업보고서 내 최신 정보를 발췌하는 경우 존재

---

## 결론

Stage D는 설계 의도에 부합함:
- 최신 수시공시 정보를 Thesis에 반영하는 보완 레이어로 동작
- 기존 Stage C 분석(investment_point, strengths)을 덮어쓰지 않음
- 회생절차, 합병, 유상증자 등 중요 이벤트가 risks/recent_events에 반영됨

다만 **현 결과로 "Stage D가 Thesis 품질을 유의하게 향상시킨다"고 결론 내리기는 이름**.  
표본 확대(20개+), 이벤트 유형 균형, 블라인드 평가 포함한 2차 검증 권장.

---

## 향후 계획

- [ ] `--codes`로 대표 종목 20개 수동 지정 후 2차 검증
- [ ] operations 유형 종목 추가 확보
- [ ] recent_events 문장 품질 개선 (사실형 표현 강화)
- [ ] 정정공시 처리 정책 문서화
