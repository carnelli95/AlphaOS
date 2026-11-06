# AlphaOS — 전체 기능 요약 & 파일 맵

> 작성: 2026-11-06. 설계 원칙·로드맵은 [`스펙.md`](../스펙.md), 운영 리포트 규격은 [`qa_report_spec.md`](qa_report_spec.md), 맥북 이전은 [`SETUP_MAC.md`](SETUP_MAC.md) 참고.

## 1. 한 줄 요약

KOSPI 약 836종목을 대상으로 **재무 정량 점수(Alpha Score) + DART 사업보고서·수시공시 + 뉴스 + LLM 분석**을 결합해, 기업이 *왜* 좋은지/위험한지를 설명하는 한국 주식 리서치 앱. 매일 Celery가 데이터 수집 → AI 분석 → QA 리포트까지 자동 실행한다.

## 2. 기능 요약

### 2.1 분석 레이어 (Stage A~E)

| Stage | 하는 일 | LLM | 저장 위치 (`ai_reports` 컬럼 등) |
|---|---|---|---|
| **A** 재무·Alpha Score | 재무제표 기반 기업개요/밸류에이션 + Alpha Score v2.6 (`V×0.55 + S×0.45`) | 개요만 사용 | `overview`, `valuation_sections`, `alpha_score` + `alpha_score_history` 테이블 |
| **B** Peer Benchmark | 업종 내 percentile 비교 | 없음 | `stage_b_sections` |
| **C** 사업보고서 분석 | 사업보고서 원문 → 성장동력·리스크·경제적 해자 | 사용 | `stage_c_sections` |
| **D** 이벤트 (DART) | 수시공시(B 주요사항·I 거래소·F 외부감사) → Fact 추출 | 사용 | `stage_d_raw`, `stage_d_sections` |
| **D** 이벤트 (뉴스) | 네이버 뉴스 → 관련성 필터 → DART Fact와 병합 | 사용 | `stage_d_sections` (`source_type=news`) |
| **E** Investment Thesis | A~D 종합 → Thesis·강점·리스크·Reason Graph·Confidence | 사용 | `thesis_sections` |

- Stage E는 Attribution Validator(출처 오귀속 차단, 최대 2회 재시도)와 결정론적 `compute_confidence()`를 포함.
- Alpha Score에서 G(성장)/M(모멘텀)은 백테스트상 IC를 희석해 최종 점수에서 제외(연구용으로만 유지).

### 2.2 자동 운영 (Celery Beat, 모두 24시간 주기)

| 태스크 | 내용 |
|---|---|
| `update_financial_data` | DART 재무데이터 갱신 |
| `update_stock_prices` | 시세 갱신 (`price_history`) |
| `renew_subscriptions` | 구독 만료/갱신 처리 |
| `run_daily_pipeline` | Stage C → D(DART) → D(뉴스, 하루 150종목) → E(Thesis) → QA Report 순차 실행 |

**증분 갱신 정책 (2026-11-06 개편)**
- **DART**: 시장 전체 공시를 워터마크(`pipeline_state.stage_d_dart_scanned_through`) 이후분만 스캔 → 신규 공시가 있는 종목만 `pending_generate` 표시 후 재생성. 실패해도 다음 실행에서 이어서 처리.
- **뉴스**: `news_scanned_at`이 7일 지난 종목부터 재검색, 기존 뉴스와 URL 중복 제거 후 병합.
- **Thesis**: Stage D가 Thesis보다 최신인 종목만 재생성.
- **LLM 크레딧 소진** 시 `LLMQuotaExhausted`로 배치 즉시 중단(남은 종목은 다음 실행에서 처리).
- 전체 재생성(`--force`)은 수동 실행만.

### 2.3 QA·검증

- **일일 QA Report**: 각 배치가 `qa_reports/stage_runs/{date}_{stage}.json`을 남기고, `generate_qa_report.py`가 severity×category로 분류해 `qa_reports/{date}.json` 생성.
- **사람 QA (beta-v1)**: 표본 추출 → 반자동 체크 → 6항목 채점(Thesis/Strength/Risk/Reason Graph/Confidence/Factuality).
- **IC 실험** (2026-11 기준 결과)
  - QA 품질 IC: 10종목 모두 만점이라 분산 0 → IC 계산 불가.
  - Alpha Score 롤링 IC (07~10월, 주간): 5D +0.018 / 20D +0.004 / 60D −0.026 → 단기 예측력 유의하지 않음.

### 2.4 웹 앱

- **Backend (FastAPI)**: 스크리너, 종목 상세, 검색, 랭킹, AI 리포트 API + JWT 인증 + Toss Payments 결제/구독.
- **Frontend (Next.js)**: 홈(스크리너), 종목 상세, 로그인/회원가입, 구독(성공/실패) 페이지.

## 3. 매일 데이터 흐름

```
DART 공시 ─┐                         ┌─ Stage D facts ─┐
           ├─ collect_stage_d ──────►│                 │
네이버 뉴스 ─┴─ collect_news ────────►│                 ├─► Stage E Thesis ─► QA Report
사업보고서 ──── Stage C ─────────────────────────────────┤
재무/시세 ──── Alpha Score (A), Peer (B) ────────────────┘
```

## 4. 파일 맵

### 4.1 루트 & 문서

| 파일 | 설명 |
|---|---|
| `스펙.md` | 프로젝트 스펙·원칙·로드맵·진행 상태 |
| `docs/PROJECT_OVERVIEW.md` | 이 문서 |
| `docs/qa_report_spec.md` | QA Report 규격 + 증분 갱신 정책 |
| `docs/SETUP_MAC.md` | 맥북 환경 구성 / DB 복원 |
| `requirements.txt`, `.env.example`, `frontend/.env.example` | 의존성·환경변수 템플릿 |
| `migrations/*.sql` | 스키마 변경 이력 (sector, fs_type, Stage B/C, ai_report 등) |

### 4.2 `app/` — 백엔드 & 스케줄러

| 파일 | 설명 |
|---|---|
| `main.py` | FastAPI 엔트리. `/api/screener`, `/api/stock/{code}`, `/api/search`, `/api/ranking`, `/api/stock/{code}/report` |
| `routers/auth.py` | 회원가입/로그인/내 정보 (JWT) |
| `routers/billing.py` | 결제 확인/구독 조회/해지 |
| `auth.py`, `payment.py` | JWT 유틸, Toss Payments 호출 |
| `models.py`, `schemas.py`, `database.py` | ORM 모델, Pydantic 스키마, DB 연결 |
| `services.py`, `factor.py` | 스크리너 계산 로직, 팩터 계산 |
| `celery_app.py` | Celery 설정 + Beat 스케줄 |
| `tasks.py` | Beat 태스크 (재무/시세/구독/`run_daily_pipeline`) |
| `tests/test_renew_subscriptions.py` | 구독 갱신 테스트 |
| `tests/test_stage_d_incremental.py` | Stage D 증분 로직 테스트 (공시 분류·병합·뉴스 병합·루틴 공시 필터) |

### 4.3 `pipeline/` — 운영 파이프라인 (매일 사용)

| 단계 | 수집 | 생성 (단일 종목) | 배치 실행기 |
|---|---|---|---|
| A | `collect_financial_data.py`, `collect_price_history.py` | `generate_ai_report.py` (+ 공용 `call_llm`) , `compute_alpha_score.py` | `batch_generate_ai_reports.py` |
| B | `collect_sector_dart.py`, `fetch_sector_data.py` | `generate_stage_b.py` | `batch_generate_stage_b.py` |
| C | `dart_report_collector.py` | `generate_stage_c.py` | `batch_collect_runner.py` (원문 수집), `batch_generate_stage_c.py` |
| D (DART) | `collect_stage_d.py` (시장 스캔·워터마크·병합) | `generate_stage_d.py` | `batch_generate_stage_d.py` |
| D (뉴스) | `collect_news.py` | `generate_stage_d_news.py` | `batch_generate_stage_d_news.py` |
| E | — | `generate_thesis.py` | `batch_generate_thesis.py` |
| QA | — | `generate_qa_report.py` | `stage_result.py` (배치 결과 JSON 공용 헬퍼) |

### 4.4 `pipeline/` — QA·실험·검증 (수동 실행)

| 분류 | 파일 |
|---|---|
| 사람 QA | `qa_sample.py` → `qa_auto.py` → `qa_score.py` |
| QA IC 실험 | `qa_snapshot.py`, `qa_ic.py` |
| Alpha Score IC 실험 | `alpha_snapshot.py`, `alpha_ic.py` (`--snapshot` / `--rolling`) |
| 백테스트·팩터 최적화 | `backtest.py`, `debug_backtest.py`, `optimize_v.py`, `optimize_g.py`, `optimize_m.py` |
| 품질 진단 | `diagnose_quality.py`, `diagnose_risks.py`, `validate_stage_c.py`, `validate_importance.py`, `coverage_report.py` |
| A/B 실험 | `ab_comparison.py` (+ 결과 `ab_comparison_20260629.md`, `ab_comparison_result.json`) |

> 과거 일회성 수정·점검 스크립트(`fix_*`, `check_*`, `_check_*`, `migrate_add_source_type.py`)는 2026-11-06 삭제. 필요하면 git 이력에서 복원.

### 4.5 `frontend/` — Next.js

| 경로 | 화면 |
|---|---|
| `app/page.js` | 홈 / 스크리너 |
| `app/stock/[code]/page.js` | 종목 상세 (AI 리포트) |
| `app/login`, `app/register` | 인증 |
| `app/subscribe` (+ `success`, `fail`) | 구독 결제 |

### 4.6 git 미추적 데이터 (로컬 전용)

| 경로 | 내용 |
|---|---|
| `.env`, `frontend/.env.local` | API 키·DB 접속 정보 |
| `qa_reports/` | 일일 QA 리포트, `stage_runs/` 배치 결과 |
| `qa_snapshots/` | IC 실험용 스냅샷 CSV |

## 5. 주요 DB 테이블 (`quant_screener`)

| 테이블 | 내용 |
|---|---|
| `stocks`, `dart_corp_codes` | 종목·업종, DART 기업코드 |
| `financial_data`, `financial_data_fix_log` | 재무제표 (EAV, CFS/OFS) |
| `price_history` | 일별 시세 |
| `alpha_score_history` | Alpha Score 및 V/G/M/S 분해 |
| `business_reports_raw`, `business_report_collection_log` | 사업보고서 원문 |
| `ai_reports` | Stage A~E 결과 (JSONB 섹션별) |
| `pipeline_state` | 증분 수집 워터마크 |
| `users`, `subscriptions`, `payments` | 회원·구독·결제 |

## 6. 현재 상태 (2026-11-06)

- 07-16~11-05 Celery 미가동 공백 → 공시 180일 재스캔, 뉴스 전체 재검색, Thesis 821종목 재생성으로 **현재 상태는 오늘 기준으로 복구**. 해당 기간 일일 QA 기록은 복구 불가.
- 다음: Celery Worker/Beat 재가동 → 운영 검증 기간 재시작. 관찰 지표: 일일 공시 수집 수 > 0, D/E 재생성 수(하루 약 50), 뉴스 재검색 순환.
- 관찰 메모(수정 보류): Stage D `other` 유형 비중 22%, 뉴스 Fact 0개 종목 82%.
