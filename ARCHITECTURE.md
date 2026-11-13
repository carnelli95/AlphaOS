# AlphaOS 아키텍처

> 작성: 2026-11-12 (0단계 재파악). 코드와 DB를 직접 읽어 확인한 내용만 적었다. 출처가 없는 수치는 "미확인"으로 표시했다.
> DB 수치는 2026-11-12 기준 `quant_screener`에서 SELECT로 조회한 값이다.
> 관련 문서: [docs/plan.md](docs/plan.md)(단계 계획), [스펙.md](스펙.md)(이전 스펙, 일부 내용이 오래됨), [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md), [docs/qa_report_spec.md](docs/qa_report_spec.md), [docs/SETUP_MAC.md](docs/SETUP_MAC.md)

---

## 1. 전체 구조

```
                 ┌───────────── 외부 소스 ─────────────┐
                 │ DART OpenAPI · FinanceDataReader ·  │
                 │ pykrx · Naver 뉴스 API · OpenAI     │
                 └──────────────────┬──────────────────┘
                                    │
 ┌──────────────── pipeline/ (배치 스크립트, 수동 또는 Celery 호출) ────────────────┐
 │ 수집: collect_* / dart_report_collector / batch_collect_runner                   │
 │ 분석: Stage A(재무·Alpha Score) → B(업종 Peer) → C(사업보고서) → D(공시·뉴스) → E(Thesis) │
 │ 관측: stage_result → generate_qa_report  (qa_reports/*.json)                      │
 │ 검증: backtest / alpha_ic / qa_ic / optimize_*                                   │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 │ PostgreSQL 18 · quant_screener (13 테이블)
 ┌──────────── app/ (FastAPI + Celery) ────────────┐
 │ main.py: /api/screener /api/stock /api/ranking …│◄──── frontend/ (Next.js 16, :3000)
 │ celery_app.py + tasks.py: 일일 수집 chain       │       localhost:8000 하드코딩
 └─────────────────────────────────────────────────┘
```

- 백엔드: Python 3.12 (`.venv`, uv), FastAPI, SQLAlchemy(동기), PostgreSQL 18
- 비동기 작업: Celery 5.6.3 + Redis. worker는 `--pool=solo`로 실행하고, beat는 현재 쓰지 않는다(수동 실행 중).
- LLM: OpenAI `gpt-4o-mini` (`pipeline/generate_ai_report.py:call_llm`, 다른 Stage도 이 함수를 같이 쓴다). Anthropic SDK는 없다.
- 프론트엔드: Next.js 16.2.9, React 19.2.4, Recharts, Node v26.13.0 (`frontend/.node-version`)

---

## 2. DB 테이블 (2026-11-12 조회)

| 테이블 | 행 수 | 내용 / 쓰는 곳 |
|---|---:|---|
| `stocks` | 946 | 종목 마스터. KOSPI 946건 중 `corp_code`가 있는 것은 836건이다. 나머지 110건은 코드 끝자리가 0이 아니어서 우선주 등으로 보인다. |
| `financial_data` | 44,667 → 63,574 (11-12 2023·2025 재수집 후) → 66,125 (11-13 폐지 종목 2,551행 추가) | EAV 재무 데이터 (code, year[varchar], fs_type, account_nm, amount). 유니크 제약은 `uq_financial_data_fs (code, year, fs_type, account_nm)`. **공시일 컬럼 없음**. 833종목, 2022–2025 |
| `financial_data_fix_log` | 1,645 | 2026-06-23~24 재무 재수집 로그 (2023: 812, 2025: 833) |
| `price_history` | 1,099,589 → 1,123,188 (11-13 폐지 종목 35개 23,599행 추가) | 일별 OHLCV. 2022-01-03 ~ 2026-11-11, `stocks` 946종목 + 폐지 종목 35개 + 지수 2개(`IDX_KOSPI`, `IDX_KOSDAQ`). **거래정지일도 행이 있다**: open·high·low·volume이 NULL이고 close는 정지 직전 가격(pykrx가 0/직전가를 주고 수집기가 0을 NULL로 저장) |
| `alpha_score_history` | 3,221 → 3,297 (11-13 재계산) | 연도별 과거 시점 Alpha Score. 11-13 시점 기준 유니버스: 2022: 775, 2023: 836, 2024: 845, 2025: 841 (이후 폐지 종목 포함) |
| `ai_reports` | 833 | 종목당 1행. Stage A~E 결과가 JSONB 컬럼으로 저장된다 (`overview`, `valuation_sections`, `metrics_snapshot`, `stage_b_sections`, `stage_c_sections`, `stage_d_raw`, `stage_d_sections`, `thesis_sections`, `alpha_score`) |
| `business_reports_raw` | 831 | DART 사업보고서 원문 섹션 |
| `business_report_collection_log` | 836 | 사업보고서 수집 상태 |
| `pipeline_state` | 1 | 증분 워터마크. `stage_d_dart_scanned_through = 2026-11-10` |
| `dart_corp_codes` | 0 | 비어 있음 |
| `delisted_stocks` | 43 (11-13 생성) | 상장폐지 종목 메타데이터 (code, name, corp_code, listing_date, delisting_date, reason, reason_category). KOSPI 보통주, 폐지일 2022-01-01 이후. `migrations/create_delisted_stocks.sql`, `pipeline/load_delisted_stocks.py` |
| `users` / `subscriptions` / `payments` | 2 / 1 / 1 | 회원·구독·결제 (범위 밖 기능) |

ORM 모델(`app/models.py`)은 `stocks`, `financial_data`, `users`, `subscriptions`, `payments`, `price_history`만 정의한다. `app/main.py`가 시작할 때 `Base.metadata.create_all()`을 호출한다. 나머지 테이블은 `migrations/*.sql`이나 각 스크립트의 `CREATE TABLE IF NOT EXISTS`(예: `alpha_score_history`)로 만들어졌다.
`models.FinancialData.year`는 Integer로 선언돼 있지만 실제 컬럼은 varchar다.

---

## 3. 분석 Stage별 정리

모든 `pipeline/` 스크립트는 `pipeline/` 디렉터리 기준으로 실행한다. 형제 모듈을 `sys.path`로 import하기 때문이다. 작업 전에 `source .venv/bin/activate`를 한다.

### Stage A — 재무 데이터 + Alpha Score

| 항목 | 내용 |
|---|---|
| 입력 | DART `fnlttSinglAcnt`(사업보고서 `reprt_code=11011`의 주요 계정), FDR 시세·시총, `price_history` |
| 출력 | `financial_data`, `stocks.price/marcap`, `ai_reports.overview/valuation_sections/metrics_snapshot/alpha_score`, `alpha_score_history` |
| 수집 파일 | `app/tasks.py:update_financial_data`(Celery, 전년도 1개 연도), `pipeline/collect_financial_data.py --year N`(수동), `app/services.py:save_financial_data`(초기 적재용), `pipeline/collect_price_history.py`(수동, pykrx) |
| 생성 파일 | `pipeline/generate_ai_report.py`(LLM 개요·밸류에이션 서술) → `batch_generate_ai_reports.py` |
| 점수 파일 | `pipeline/compute_alpha_score.py` (기본 모드 = 현재 점수를 `ai_reports.alpha_score`에 저장, `--year N` = 과거 시점 점수를 `alpha_score_history`에 저장) |
| 실행 | `python compute_alpha_score.py` / `python compute_alpha_score.py --year 2024` / `python collect_price_history.py --from 2025-01-01` |

### Stage B — 업종 Peer Benchmark (LLM 없음)

| 항목 | 내용 |
|---|---|
| 입력 | `stocks.sector/induty_code`, `financial_data`, 시총 |
| 출력 | `ai_reports.stage_b_sections` (ROE/ROA/PER/PBR/영업이익률의 업종 내 percentile) |
| 파일 | `collect_sector_dart.py`, `fetch_sector_data.py`(업종 매핑) → `generate_stage_b.py` → `batch_generate_stage_b.py` |
| 실행 | `python batch_generate_stage_b.py` (Celery 체인에는 없고 수동 실행) |

### Stage C — 사업보고서 원문 분석 (LLM)

| 항목 | 내용 |
|---|---|
| 입력 | `business_reports_raw` (`dart_report_collector.py` + `batch_collect_runner.py`로 수집) |
| 출력 | `ai_reports.stage_c_sections` (growth_drivers / risks / moat의 facts와 서술) |
| 파일 | `generate_stage_c.py`(일반론 validator와 retry 포함) → `batch_generate_stage_c.py` |
| 실행 | `python batch_generate_stage_c.py`. 일일 체인에서는 `run(sleep_seconds=0.5, limit=None)`으로 호출하고 신규 종목만 처리한다. |

### Stage D-DART — 수시공시 이벤트 (LLM)

| 항목 | 내용 |
|---|---|
| 입력 | DART 공시 목록 (유형 B 주요사항 / I 거래소 / F 외부감사). 워터마크(`pipeline_state`) 이후 공시만 스캔한다. |
| 출력 | `ai_reports.stage_d_raw`, `stage_d_sections.facts` (`source_type=dart`) |
| 파일 | `collect_stage_d.py`(시장 스캔·병합·`pending_generate`) → `generate_stage_d.py` → `batch_generate_stage_d.py` |
| 실행 | `python batch_generate_stage_d.py` (`--rebuild-window` = 180일 재스캔) |
| 최근 실행 | 11-10: 공시 132건, 99종목 재생성, Fact 431개, 실패 0 |

### Stage D-뉴스 — 뉴스 이벤트 (LLM) — **비활성 대상, 수정 금지**

| 항목 | 내용 |
|---|---|
| 입력 | Naver 뉴스 검색 API. 대상은 `stage_d_sections.news_scanned_at`이 7일 넘게 지난 종목이다. |
| 출력 | `stage_d_sections.facts` (`source_type=news`, `relevance`, `event_type`, `event_weight`, `weighted_score`), `news_scanned_at` |
| 파일 | `collect_news.py`(2단계 LLM 관련성 필터) → `generate_stage_d_news.py` → `batch_generate_stage_d_news.py` |
| 실행 | 일일 체인에서 `run(limit=150)`으로 호출한다. 현재 상태는 §7.3 참고. |

### Stage E — Investment Thesis (LLM)

| 항목 | 내용 |
|---|---|
| 입력 | `ai_reports`의 Stage A~D 결과 |
| 출력 | `ai_reports.thesis_sections` (summary, investment_point, strengths, risks, recent_events, reason_graph, confidence, generated_at). 821/833종목에 있다. |
| 파일 | `generate_thesis.py`(결정론적 이벤트 셀렉터, Attribution Validator(최대 2회 retry), 결정론적 `compute_confidence()`) → `batch_generate_thesis.py` |
| 실행 | `python batch_generate_thesis.py`. 증분 모드는 Stage D가 Thesis보다 최신인 종목만 처리한다. |
| 최근 실행 | 11-10: 대상 111, 성공 96, skip 15, 실패 0 |

### QA Report — 운영 관측

| 항목 | 내용 |
|---|---|
| 입력 | `qa_reports/stage_runs/{date}_{stage}.json` (각 배치가 `stage_result.write_stage_result`로 기록한다) |
| 출력 | `qa_reports/{date}.json`, `qa_reports/latest.json` (severity × category, 전일 대비 Δ) |
| 파일 | `stage_result.py`, `generate_qa_report.py`. 스펙은 `docs/qa_report_spec.md`에 있다. |
| 현황 | 11-09, 11-10 생성. 11-10 결과: Thesis 96건 중 이슈 12건(전부 `warning/dart_missing`) |
| 비고 | `qa_reports/`는 `.gitignore` 대상이다. |

### 검증·실험 스크립트 (수동)

| 분류 | 파일 | 결과물 |
|---|---|---|
| 연간 백테스트 | `backtest.py` (`--multi`, `--score-year/--return-year`) | 표준 출력만 (파일로 저장 안 함) |
| 단기 IC | `alpha_snapshot.py` → `alpha_ic.py` (`--snapshot`, `--rolling`) | `qa_snapshots/*.csv` (로컬에 디렉터리 없음, 맥 이전 때 안 옮긴 것으로 보임) |
| QA 품질 IC | `qa_snapshot.py` → `qa_ic.py` | 같음 |
| 가중치 탐색 | `optimize_v.py`, `optimize_g.py`, `optimize_m.py`, `debug_backtest.py` | 표준 출력 |
| 진단 | `diagnose_quality.py`, `diagnose_risks.py`, `validate_stage_c.py`, `validate_importance.py`, `coverage_report.py` | 표준 출력 |
| 유니버스 | `load_delisted_stocks.py` (`--dry-run`, `--since`) | `delisted_stocks` 업서트 (FDR KRX-DELISTING + DART corpCode) |
| 유니버스 | `collect_price_history.py --delisted`, `collect_financial_data.py --year N --delisted` | `delisted_stocks` 중 `stocks`에 없는 종목의 시세(폐지일까지)·재무 수집 |
| 유니버스 | `check_delisted_coverage.py` | 폐지 종목별 시세·정지일·마지막 실거래일·재무 누락 연도 (읽기 전용) |
| A/B | `ab_comparison.py` | `ab_comparison_20260629.md`, `ab_comparison_result.json` |

---

## 4. Celery 작업

설정 파일: [app/celery_app.py](app/celery_app.py). 브로커와 백엔드는 `REDIS_URL`(기본값 `redis://localhost:6379/0`)이고, `timezone=Asia/Seoul`이다.

| 작업 | 위치 | 하는 일 | 재시도 |
|---|---|---|---|
| `update_stock_prices` | `app/tasks.py` | `fdr.StockListing('KOSPI')`로 **`stocks.price/marcap`만** UPDATE한다. `price_history`에는 쓰지 않는다. | 3회, 60초 |
| `update_financial_data` | `app/tasks.py` | `corp_code`가 있는 종목 전체에 대해 **전년도(현재 2025)** 사업보고서 주요 계정을 upsert한다(`ON CONFLICT ON CONSTRAINT uq_financial_data_fs`. CFS와 OFS를 둘 다 저장. 11-12 수정, §7.1). | 3회, 300초 |
| `run_daily_pipeline` | `app/tasks.py` | Stage C → D-DART → D-뉴스(limit 150) → E → QA Report. 앞 Stage가 통째로 실패하면 뒤 Stage는 건너뛰고, QA Report는 항상 생성한다. **OpenAI 비용 발생** | 없음 (의도적) |
| `renew_subscriptions` | `app/tasks.py` | 기간이 지난 구독을 `expired`로 바꾼다. 재결제는 하지 않는다. | 3회, 300초 |
| `daily_collection` | `app/celery_app.py` | beat 트리거용. 위 세 작업(시세 → 재무 → 파이프라인)을 immutable chain으로 발행한다. | — |

**Beat 스케줄 (현재 코드, 커밋 `cebccf4` 2026-11-10)**

| 이름 | 작업 | 스케줄 |
|---|---|---|
| `daily-collection` | `app.celery_app.daily_collection` | `crontab(hour=16, minute=30, day_of_week="mon-fri")` 평일 16:30 KST |
| `renew-subscriptions` | `app.tasks.renew_subscriptions` | `crontab(hour=0, minute=10)` 매일 00:10 KST |

테스트는 `app/tests/test_beat_schedule.py`에 있다. 실행 예: `celery -A app.celery_app worker --pool=solo -l info`, `celery -A app.celery_app beat -l info`(현재 미사용).

**Celery 밖의 수동 스크립트**: `pipeline/collect_price_history.py`가 `price_history`를 채우는 유일한 경로다(pykrx로 종목별 범위 호출, `ON CONFLICT DO NOTHING`, 재시작 가능). 대상은 `SELECT code FROM stocks`다. 일일 체인에 들어 있지 않아서 직접 돌리지 않으면 `price_history`가 갱신되지 않는다. 이 밖에 Stage B 배치, `compute_alpha_score.py`(현재 점수, 연도별 점수 모두), `batch_generate_ai_reports.py`도 Celery 체인에 없다.

---

## 5. 팩터 스코어링과 IC 계산

서로 다른 점수가 **세 가지** 있다. 메모에 적힌 "PBR + ROE 복합 점수, IC +0.21"은 둘을 섞어 쓴 표현이다.

| # | 이름 | 위치 | 공식 | 쓰는 곳 | IC 검증 |
|---|---|---|---|---|---|
| ① | 스크리너 점수 | `app/main.py:/api/screener` + `app/factor.py` | PBR 오름차순 순위 × 0.5 + ROE(50% 상한) 내림차순 순위 × 0.5. 최신 연도 자본총계·당기순이익과 실시간 FDR 시총을 쓴다. | 프론트 스크리너 | **없음** |
| ② | Alpha Score (현재) | `compute_alpha_score.py` 기본 모드 → `ai_reports.alpha_score` | V(ROE·ROA·PER·PBR·영업이익률의 업종 percentile, Stage B) × 0.55 + S(Stage C LLM 리스크 fact를 rational decay) × 0.45 → z-score sigmoid | `/api/ranking`, 종목 상세 | 단기 롤링 IC만 (`alpha_ic.py`) |
| ③ | Alpha Score (과거 시점) | `compute_alpha_score.py --year N` → `alpha_score_history` | V(ROE·ROA·영업이익률·RE/Assets의 업종 percentile, PER/PBR 제외) × 0.55 + S × 0.45 | `backtest.py` | **IC +0.21의 출처** |

- 업종 percentile 계산(②, ③)은 `_pct()`로 한다. 업종 안에서 자기보다 작은 값의 비율이다. 업종은 현재 `stocks.sector`를 쓴다(③의 폐지 종목은 `delisted_stocks.sector`, 같은 KSIC 앞 2자리 규칙).
- ③의 유니버스(11-13~)는 `stocks` ∪ `delisted_stocks` 중 그해 재무가 있고 N+1년 1월 1일 전에 폐지되지 않은 종목이다. 계산에 없는 종목의 기존 행은 지운다.
- ③의 S와 G/M은 **현재** `ai_reports.stage_c_sections`(최신 사업보고서로 만든 LLM fact)를 모든 과거 연도에 똑같이 쓴다(코드 출력에도 "proxy"라고 명시돼 있다). ROE<0이면 그해 재무로 리스크 fact를 주입한다.

**IC 계산 코드**

| 파일 | 방식 |
|---|---|
| `backtest.py:compute_ic_stats` | Spearman ρ (순위 차 공식), p값은 정규 근사 t검정, 95% CI는 Fisher z. 점수 연도 N → 수익률 연도 N+1. `--multi`는 기간별 IC의 평균, 표준편차, t-stat을 내고, 상장폐지 포함/제외 IC 비교표와 처리 상태 표를 함께 출력한다. |
| `backtest.py:period_return` (11-13~) | 종목 수익률. 시작가는 N+1년 첫 시장 거래일(d0) 종가(그날 거래정지면 제외, d0 행이 없으면 1~3월 첫 행 = 신규 상장). 종료가는 10~12월 마지막 거래일 종가. 구간 중 폐지 → 마지막 실거래 종가. 구간 끝 정지 → 이후 폐지면 정리매매 종가, 재개면 재개 후 첫 종가, 미해소면 제외. +950% 초과는 제외, −95% 미만은 폐지 관련이 아니면 제외. 거래정지일 = `volume IS NULL`. 지수는 기존 `load_annual_returns`(1~3월 첫 종가 → 10~12월 마지막 종가). 테스트: `app/tests/test_backtest_period_return.py` |
| `alpha_ic.py` | `scipy.stats.spearmanr`. 단일 기준일(`--snapshot`) 또는 주간 롤링(`--rolling`). 5/20/60 달력일 forward return, 업종 중립 IC, 5분위, 상·하위 10% 스프레드. 롤링 모드는 **현재** `ai_reports.alpha_score` 하나를 모든 기준일에 적용한다. |
| `qa_ic.py` | QA 사람 점수와 수익률의 Spearman 상관. 10종목 모두 만점이라 분산이 0이어서 계산할 수 없다. |

---

## 6. 프론트엔드 ↔ 백엔드

- 프론트(`frontend/app`, :3000)가 백엔드(:8000)를 `fetch`로 직접 호출한다. 백엔드 주소는 **5개 파일 9곳에(인계 메모의 "6개 파일"은 실제로 5개) `http://localhost:8000`으로 하드코딩**돼 있고 환경변수가 없다(`frontend/.env.example`에는 `NEXT_PUBLIC_TOSS_CLIENT_KEY`만 있다).
- CORS: `allow_origins=["http://localhost:3000"]`
- 인증: JWT Bearer (`app/auth.py`). `SECRET_KEY`가 없으면 코드에 하드코딩된 기본값을 쓴다.

| 화면 (파일) | 호출 API | 백엔드 처리 |
|---|---|---|
| 홈 `app/page.js` | `GET /api/ranking?sector=&limit=20` | `ai_reports.alpha_score` 점수순 상위 N (②) |
| 홈 `app/page.js` | `GET /api/search?q=` | `stocks.name LIKE` |
| 홈 `app/page.js` | `GET /api/screener?pbr_min…&limit` | ① 스크리너 점수. **요청마다 `fdr.StockListing('KOSPI')`를 외부 호출한다.** |
| 종목 상세 `app/stock/[code]/page.js` | `GET /api/stock/{code}` | 재무 시계열과 PBR/ROE/PER. 이것도 요청마다 FDR을 호출한다. |
| 종목 상세 | `GET /api/stock/{code}/report` | `ai_reports`의 Stage A~E 섹션 |
| `login`, `register` | `POST /api/auth/login`, `/register` | `users` |
| `subscribe/success` | `POST /api/billing/confirm` | Toss 결제 승인 → `payments`, `subscriptions` |

---

## 7. 불일치 확인 결과 (2026-11-12)

### 7.1 `financial_data` 행 수: 23,316 vs 44,667

- **현재 실제 값은 44,667행**(833종목)이다. 23,316은 지금 DB에서 재현되지 않고, 행 수 이력도 남아 있지 않아 출처를 확인할 수 없다(`docs/plan.md`에만 있음). 2024+2025 합계(23,288)와 가깝지만 일치하지 않는다.

| year | CFS 행 | OFS 행 | 종목 수 | 주요 14계정 행 |
|---|---:|---:|---:|---:|
| 2022 | 8,430 | 1,308 | 747 (자본총계 기준) | 9,738 |
| 2023 | 11,641 | 0 | 808 | 11,092 |
| 2024 | 9,742 | 1,596 | 826 | 11,292 |
| 2025 | 600 | 11,350 | 833 | 11,386 |

- **새로 발견한 데이터 오류 (2025년은 대부분 별도재무제표다)**: `financial_data`에 유니크 제약이 두 개 있다.
  - `uq_financial_data (code, year, account_nm)` ← 예전 제약. `migrations/add_fs_type_to_financial_data.sql`은 `financial_data_code_year_account_nm_key`라는 이름으로 DROP하려 했지만, 실제 이름이 달라서 지워지지 않았다.
  - `uq_financial_data_fs (code, year, fs_type, account_nm)` ← 새 제약.
  - 수집 코드 세 곳(`app/tasks.py`, `app/services.py`, `pipeline/collect_financial_data.py`)이 모두 `ON CONFLICT ON CONSTRAINT uq_financial_data DO UPDATE SET amount, fs_type`을 쓴다. DART 응답에서 OFS 행이 CFS 뒤에 오면 **CFS 값을 OFS로 덮어쓴다**(커밋 `0670ba4`, 2026-11-06에 이 제약을 쓰도록 바뀜).
  - 예: 삼성전자 2025 자본총계는 OFS 254.3조다(2024 CFS는 402.2조). 2025년 주요 14계정은 사실상 전 종목이 OFS다.
  - 영향: `alpha_score_history` 2025와 2025→2026 백테스트, 현재 스크리너(①은 CFS 우선이지만 2025년에 CFS가 없으면 OFS를 씀), Stage A/B의 최신 연도 지표.
- **수정 완료 (2026-11-12)**:
  - `migrations/drop_legacy_financial_data_unique.sql`로 예전 제약을 삭제했다.
  - upsert 3곳을 `uq_financial_data_fs`로 바꿨다. 이제 CFS와 OFS를 둘 다 저장하고, 조회할 때 CFS를 우선한다.
  - `collect_financial_data.py --year 2025 --refresh`로 836종목을 재수집했다(21,343건 저장, 데이터 없음 3, 오류 0).
  - 결과: 전체 54,584행. 2025년 자본총계 CFS 보유 종목이 2/833에서 **725/833(87%)**로 돌아왔다. 2022년 87%, 2024년 86%와 같은 수준이다. 삼성전자 2025 자본총계는 CFS 436.3조, OFS 254.3조로 둘 다 저장돼 있다.
  - 점검: `pipeline/check_financial_data.py`(읽기 전용, 예전 제약이 남아 있으면 종료 코드 1)
  - 수정 전 백업: `pg_dump -t financial_data` (세션 스크래치 디렉터리, 영구 보관 아님)
  - **남은 문제**:
    - ~~파생 데이터가 OFS 기준~~ → 11-12에 다시 계산했다(LLM 없음).
      - 기존 행이 있는 종목만 Stage B를 다시 계산했다(821/821 성공). 새 `ai_reports` 행이 생기지 않게 일회성 실행기를 썼다.
      - 이어서 `compute_alpha_score.py`(현재 점수, 819종목)와 `compute_alpha_score.py --year 2025`(833종목)를 실행했다.
      - 수정 전과 비교:
        - Stage B ROE 값이 768/821종목에서 바뀌었다. Stage B 쿼리가 "CFS 우선 → 최신 연도" 순서라서, 전에는 대부분 2024 CFS를 쓰다가 이제 2025 CFS를 쓴다. 같은 시점에 바뀐 `stocks.marcap`도 함께 반영됐다.
        - 현재 alpha_score는 수정 전과의 순위상관이 0.691이고, 평균 |Δ| = 11.7점이다.
        - history 2025는 순위상관 0.816, 평균 |Δ| = 8.4점이다.
      - 백업: `pg_dump -t ai_reports -t alpha_score_history` (세션 스크래치)
      - Stage A의 LLM 서술(`overview`, `valuation_sections`, `metrics_snapshot`)과 Thesis(Stage E)는 다시 생성하지 않았다. 비용이 들기 때문이다. 그래서 텍스트 속 수치는 예전 기준일 수 있다.
    - Stage B 쿼리가 종목마다 "CFS 중 최신 연도"를 고른다. CFS가 끊긴 종목은 더 오래된 연도를 쓰게 되어, 업종 percentile이 서로 다른 연도끼리 비교될 수 있다.
    - **2023년 fs_type 표시 오류 확인 (2026-11-12, DART `fnlttSinglAcnt` 원본과 대조, 읽기 전용)**:
      - 대조 계정: 자본총계·자산총계·당기순이익(손실)·매출액

      | 연도 | 대조 종목 | 별도 값인데 CFS로 표시된 종목 (자본총계 기준) | 값 불일치 |
      |---|---:|---:|---|
      | 2022 | 747 | 0 | 고려아연(010130)만 |
      | 2023 | 812 | **113** (행 기준 4계정 합쳐 약 450행) | 고려아연만 |
      | 2024 | 826 | 0 | 고려아연만 |
      | 2025 | — | 11-12에 고친 수집기로 재수집해 DART `fs_div`를 그대로 저장 | — |

      - 113종목 중 112종목은 DART에 연결재무제표 자체가 없다. 별도 값이 CFS로 표시돼 있다. 금액은 맞고 표시만 틀렸다.
      - 나머지 1종목(한화 000880)은 DART 연결 응답에 자본총계가 없어서, 별도 자본총계가 CFS로 표시돼 연결 순이익과 섞여 있다. 금액 자체가 다른 기준이다.
      - 2023년 데이터는 6월 일회성 재수집 스크립트(삭제됨)로 들어왔다. 이 스크립트는 연결이 없거나 계정이 빠지면 별도 값으로 채우고 CFS로 표시한 것으로 추정된다. 2023년에만 핵심 14계정 외 계정(영업비용, 보험계약부채 등 549행)이 있는 것도 이 스크립트 때문으로 보인다. 이 추가 계정들은 코드 어디에서도 쓰이지 않는다.
      - **고려아연 불일치의 원인은 정정공시다.** DART는 정정이 반영된 최신 값을 돌려준다. 2023년 사업보고서 응답의 접수번호가 `20260813…`이다. 즉 `fnlttSinglAcnt`의 `rcept_no`는 최초 공시일이 아니라 **마지막 정정일**이다. 1단계 공시일 기준 작업에서 이 점을 고려해야 한다.
      - **수정 완료 (2026-11-12)**:
        - 2023년 행 11,641개를 삭제하고 `collect_financial_data.py --year 2023 --refresh`로 다시 받았다. 첫 실행에서 오류 1종목이 났고, 재시도로 채웠다. 최종 20,631행, 812종목(삭제 전과 같음)이다. 데이터 없음 24종목은 2023 사업보고서가 없는 종목이다.
        - 2023년 자본총계 CFS 보유 종목은 695/808(86%)로 다른 연도와 같은 수준이 됐다. 연결재무제표가 없는데 2023년만 CFS로 표시되던 종목은 100개에서 5개로 줄었다. 남은 5개는 DART `fs_div` 그대로라 실제로 2023년에 연결재무제표가 있었던 회사다. 쓰이지 않던 추가 계정 549행은 사라졌다.
        - 파생 점수 재계산: Stage B 821/821, 현재 alpha_score 819종목(수정 전과 순위상관 0.909), `alpha_score_history` 2023 812종목(순위상관 0.973).
        - 백테스트 2023→2024 IC: +0.2316 → **+0.2258**
        - 백업: `pg_dump` (세션 스크래치)
      - 같은 구조의 문제가 수정된 수집기에도 남아 있다. 연결 응답에서 특정 계정이 빠지면 그 계정만 OFS로 저장되고, 조회 쪽의 "CFS 우선"이 계정 단위로 적용되어 연결과 별도가 섞일 수 있다.
    - 2023년은 100% CFS로 표시되는데, 2024·2025년에 OFS만 있는 100종목도 2023년에는 CFS로 표시돼 있다. 라벨이 틀렸을 가능성이 높다(6월 재수집 스크립트나 마이그레이션 기본값 'CFS' 때문으로 추정).

### 7.2 Celery beat: "평일 16:30" vs "60*60*24 간격"

- **현재 코드는 평일 16:30 KST다** (`crontab(hour=16, minute=30, day_of_week="mon-fri")`, 커밋 `cebccf4` 2026-11-10).
- 이전 코드(`cebccf4^`)에서는 4개 작업이 모두 `60*60*24` 간격이었다. 이 간격은 beat 시작 시각을 기준으로 돌아서 실행 시각이 고정되지 않는다.
- `스펙.md` §3과 `docs/PROJECT_OVERVIEW.md` §2.2는 아직 "24시간 간격"으로 적혀 있다(문서가 오래됨). 현재 beat는 쓰지 않으므로 실제로 자동 실행된 적은 없다.

### 7.3 Stage D 뉴스: "모든 종목 1.25점 동일" vs "뉴스 대상 0개"

**두 증상은 원인이 다르다.**

- **"뉴스 대상 0개"는 정상 동작이고 버그가 아니다.** 833종목 전부의 `news_scanned_at`이 2026-11-06이다(공백 기간 복구 때 전체를 재검색했다). 재검색 주기가 7일이라 11-09, 11-10 실행에서는 대상이 0개였다(`stage_runs/2026-11-0{9,10}_stage_d_news.json`의 target_count가 0). 11-13 이후 실행부터 다시 대상이 생긴다. 다만 833종목이 같은 날 한꺼번에 기한이 돼서, 하루 150개 제한으로는 약 6일 동안 몰려서 처리된다.
- **"모든 종목 1.25점 동일"은 현재 DB에서 재현되지 않는다.** 1.25는 `event_weight` 최댓값이다(major_contract/acquisition 원점수 5 ÷ 4). 현재 뉴스 fact는 192개뿐이고, **693/833종목(83%)은 뉴스 fact가 0개다.** `weighted_score`는 0.16~1.25 사이에 13종류 값이 있다.
- 실제로 관찰되는 이상은 따로 있다. **LLM이 매긴 `relevance`가 0.85에 몰려 있다**(192개 중 152개가 0.85, 26개가 0.84, 12개가 1.0, 2개가 0.8). 그래서 `weighted_score = relevance² × event_weight`가 사실상 event_type만으로 정해진다(0.722, 0.903, 0.542 등). 예전 "1.25 동일" 메모는 이 양자화 현상이나 event_weight 표시를 본 것일 수 있지만, 확인할 수는 없다.
- 계획대로 Stage D는 수정하지 않았다.

### 7.4 IC +0.21의 출처

- 출처는 `pipeline/backtest.py`(Alpha Score v2.6 **과거 시점 점수 ③** → 다음 해 연간 수익률, Spearman)다. "PBR+ROE 점수(①)"의 IC가 아니다. 결과 파일은 저장되지 않았고, `스펙.md`의 문장 하나("IC +0.21로 v2.6 확정")만 남아 있다.
- 현재 DB로 `python backtest.py --multi`를 다시 돌린 결과 (2026-11-12, SELECT만 함):

| 점수 연도 → 수익률 연도 | IC | p | 사용/유니버스 |
|---|---:|---:|---|
| 2022 → 2023 | +0.110 | 0.003 | 747 / 750 |
| 2023 → 2024 | +0.232 → **+0.226** (11-12 2023 재수집 후) | <0.001 | 810 / 812 |
| 2024 → 2025 | +0.193 | <0.001 | 824 / 826 |
| 2025 → 2026 | +0.081 → **+0.087** (11-12 CFS 복구 후) | 0.020 → 0.012 | 823 / 833 |
| 4기간 평균 | +0.154 → +0.155 → **+0.154** (11-12 두 차례 수정 후) | (sd 0.070 → 0.066) | |

**11-13 상장폐지 규칙 적용 후** (`alpha_score_history` 2022~2025 재계산, `backtest.py --multi`, 2026→ 구간은 11-11까지 부분년도)

| 점수 연도 → 수익률 연도 | IC 폐지 포함 | 사용/유니버스 | IC 폐지 제외(현재 생존 종목만) | n | 폐지 관련 사용 종목 |
|---|---:|---|---:|---:|---:|
| 2022 → 2023 | +0.101 (p 0.005) | 763 / 775 | +0.085 | 734 | 7 |
| 2023 → 2024 | +0.253 (p<0.001) | 820 / 836 | +0.241 | 796 | 10 |
| 2024 → 2025 | +0.176 (p<0.001) | 817 / 845 | +0.176 | 802 | 8 |
| 2025 → 2026 | +0.103 (p 0.003) | 801 / 841 | +0.097 | 794 | 7 |
| 4기간 평균 | **+0.158** (sd 0.072) | | +0.150 | | |

- 폐지 종목을 넣으면 IC가 평균 **+0.009** 오른다. 부실 폐지 종목이 낮은 점수에 몰려 있어서다(예: 2023→2024 카프로·한창·웰바이오텍·국보는 점수 백분위 1~13, 수익률 −99%). 합병·완전자회사화 종목은 프리미엄을 받고 나가 수익률이 대체로 양수다.
- 이전 표(+0.154)와 차이는 폐지 종목 추가만이 아니라 시작 시점 정지 종목 제외(9~25개), 구간 끝 정지 처리, 점수 재계산(유니버스 변화로 percentile·z-score가 바뀜)이 함께 반영된 결과다.
- 남은 생존 편향: 구간 끝에 정지 중이고 아직 해소되지 않은 종목은 제외된다(2024→2025 9개, 2025→2026 14개). 이 중 앞으로 폐지되는 종목은 다시 돌리면 정리매매 종가로 들어온다.
- 폐지 종목은 Stage C가 없어 S가 중립(0.5)이다. 생존 종목의 S는 현재 LLM fact(미래 정보)를 쓴다. 두 그룹의 S 기준이 다르다는 한계가 있다(S 처리 방식은 1단계에서 따로 정한다).

- 2023→2024와 2024→2025의 평균이 **+0.212**다. +0.21은 이 두 기간의 평균이었을 가능성이 높다(**추정**, 기록 없음).
- 요인 분해: 2023→2024는 V 단독 +0.227, S 단독 +0.142, 2024→2025는 V 단독 +0.197이다.
- `--multi`의 t-stat은 4개 기간에 정규 근사 p값을 써서 "p=0.0000"으로 나온다. 자유도 3인 t분포로 계산하면 p≈0.02 수준이다. 과대 표시된 것이다.
- 단기 롤링 IC(`alpha_ic.py --rolling`, 2026-07-01~10-31, 주간)는 스펙에 5D +0.018 / 20D +0.004 / 60D −0.026으로 기록돼 있다. 결과 CSV(`qa_snapshots/`)는 이 맥에 없다.

---

## 8. 1단계(IC 재검증)를 위한 조사

### 8.1 재무 데이터의 시점 기준

- **회계연도 기준으로만 저장돼 있다.** `financial_data`에는 `year`(사업연도)만 있고, `rcept_no`·`rcept_dt` 같은 공시일 컬럼이 없다. DART `fnlttSinglAcnt` 응답에는 `rcept_no`(앞 8자리가 접수일)가 들어 있지만, 수집 코드 세 곳 모두 저장하지 않는다. `business_reports_raw`나 `stage_d_raw`에 공시 정보가 일부 있지만, 재무 행과 연결돼 있지 않다.
- **백테스트의 미래 정보 혼입 경로**:
  1. 점수 연도 N의 사업보고서는 보통 N+1년 3월 말에 공시된다. 그런데 `backtest.py`의 시작가는 **N+1년 1~3월 중 첫 거래일(대부분 1월 초)** 종가다. 약 3개월 동안은 공시 전 정보로 수익률을 맞힌 셈이다. 그 기간의 실적 발표·잠정실적 효과가 IC에 섞인다.
  2. ③의 S(리스크)와 G/M은 **현재 시점의 LLM fact**를 모든 과거 연도에 쓴다. 2023→2024 기준 S 단독 IC는 +0.142다.
  3. 업종 분류도 현재 `stocks.sector`를 쓴다(영향은 작을 것으로 추정).
  4. §7.1 버그로 2025년 재무가 OFS였다. 11-12에 원천 데이터와 `alpha_score_history` 2025를 모두 고쳤다(2025→2026 IC +0.081 → +0.087).

### 8.2 유니버스와 상장폐지 종목

- `stocks` 946건은 **어느 시점에 FDR `StockListing('KOSPI')`로 받은 현재 상장 종목**이다(`app/services.py:save_stock_list`). 날짜 컬럼이 없어 적재 시점은 확인할 수 없다.
- `price_history`의 종목 코드는 지수 2개를 빼면 **모두 `stocks`에 있다**. 2022~2025 사이에 상장폐지된 종목은 시세도 재무도 수집되지 않았다 → **생존 편향이 있다.**
- `stocks`에 있지만 시세가 끊긴 종목은 9개다(마지막 거래일이 2026-06-29~10-29, 예: 일정실업, 더존비즈온, 현대홈쇼핑, 동양생명). 적재 이후 상장폐지되거나 거래가 정지된 것으로 보인다. 첫 거래일이 2022-01-31 이후인 신규 상장은 48개다.
- (11-12 시점) `backtest.py`의 "제외 (상장폐지·데이터 부족·이상값)" 문구와 달리, 실제 상장폐지 처리 로직은 없었다. 종료가가 없으면 그 종목을 **빼버렸다**. → 11-13 `period_return`으로 대체(§5).
- 연도별 점수 유니버스는 "그해 `financial_data`가 있는 현재 상장 종목"이다(2022: 750 → 2025: 833).

**폐지 종목 현황 (2026-11-13, `delisted_stocks`)**

FDR KRX-DELISTING 기준 KOSPI 보통주 폐지 43개. DART corp_code 43/43 매핑, pykrx 시세 43/43 조회 가능(폐지 전 120일 구간 확인).

| 폐지 연도 | 계 | 부실 | 비부실 | 기타 | `stocks`·`price_history`에 있음 |
|---|---:|---:|---:|---:|---:|
| 2022 | 5 | 1 | 4 | 0 | 0 |
| 2023 | 4 | 1 | 3 | 0 | 0 |
| 2024 | 7 | 1 | 5 | 1 | 0 |
| 2025 | 10 | 4 | 6 | 0 | 0 |
| 2026 (~11월) | 17 | 9 | 8 | 0 | 8 |
| 계 | 43 | 16 | 26 | 1 | 8 |

- 부실(감사의견 거절·계속성·시총 미달 등): 장기 거래정지 뒤 정리매매 7거래일만 거래되고 가격이 크게 하락한다(예: 비케이탑스 76→8원, 한창 112→11원).
- 비부실(완전자회사화·해산 사유=흡수합병·자진): 폐지 약 1개월 전 거래 종료, 가격은 안정적이다.
- 수익률 규칙은 DECISIONS.md 2026-11-13 항목을 따른다. backtest.py에는 아직 반영하지 않았다.
- **11-13 시세·재무 보충**: `stocks`에 없던 35개의 시세(2022-01-01~폐지일, 23,599행)와 재무(2022~2025, 2,551행)를 수집했다. 백업: 스크래치 `backup/price_history_before_delisted.dump`, `financial_data_before_delisted.dump`. `check_delisted_coverage.py` 결과는 다음과 같다.
  - 시세 없음 0/43. 모든 종목의 마지막 실거래일이 폐지일 40일 이내다.
  - 재무 누락(사업보고서가 나왔어야 하는 연도 기준) 6종목: 메리츠증권·우리종금 2022, 비케이탑스 2023(부실), 엔에이치스팩19호 2022(SPAC), 코오롱모빌리티그룹 2022(2023 분할 상장), 동양생명 2022.
  - **폐지 후에도 재무가 있는 종목이 있다**(예: 메리츠화재 2023~2025, 완전자회사로 공시 계속). 백테스트 유니버스는 재무 유무가 아니라 상장 기간으로 걸러야 한다.
  - **부실 폐지 종목은 기간 대부분이 거래정지다**(에코바이브 454행 중 447, 청호ICT 830행 중 823). 이 경우 시작가도 정지 직전 가격이라 실제로는 살 수 없는 종목이 유니버스에 들어간다. 기존 `stocks` 종목도 230종목에 정지일 행(16,351행)이 있다.

---

## 9. 미완성이거나 깨진 부분

| # | 영역 | 문제 | 근거 | 심각도 |
|---|---|---|---|---|
| 1 | 재무 데이터 | ~~예전 유니크 제약이 남아 있어 CFS가 OFS로 덮어써짐~~ → 11-12 수정·재수집·파생 점수(Stage B, Alpha Score) 재계산 완료. 2023년 fs_type 표시도 재수집으로 수정함. LLM 서술 수치, Stage B 연도 혼합, 계정 단위 CFS/OFS 혼합(예: 한화 2023 자본총계) 문제는 남음 | §7.1 | 중간 (남은 문제) |
| 15 | 표현 | `compute_alpha_score.py` 콘솔 출력이 점수 구간을 "BUY/HOLD/CAUTION"으로 표시함. 투자 조언 표현 금지 원칙과 충돌함 | `compute_alpha_score.py:run` | 낮음 (공개 전 수정) |
| 2 | 재무 데이터 | 공시일(`rcept_no`/`rcept_dt`)을 저장하지 않음 | §8.1 | 높음 (1단계 선결 조건) |
| 3 | 백테스트 | 수익률 시작이 1월 초여서 공시일보다 앞섬. 과거 점수가 현재 LLM fact를 씀 | `backtest.py:load_annual_returns`, `compute_alpha_score.py:run_year` | 높음 |
| 4 | 유니버스 | 상장폐지 종목이 없음 (생존 편향). 폐지 종목은 제외될 뿐 손실로 반영되지 않음. **11-13 해결**: `delisted_stocks` 43행, 폐지 종목 35개 시세·재무 수집, 점수 유니버스 시점 기준화, backtest.py 규칙 반영(§5, §7.4). 남은 것: 미해소 정지 종목 제외, 폐지 종목 S 중립 | §8.2 | 높음 |
| 5 | 백테스트 | 다기간 t-stat에 정규 근사를 써서 p값이 과소 표시됨. 결과를 파일로 남기지 않음 | §7.4 | 중간 |
| 6 | 시세 | `update_stock_prices`가 `price_history`를 갱신하지 않음. 일일 시세 적재는 수동 스크립트에만 의존 | §4 | 중간 |
| 7 | Stage D 뉴스 | 83% 종목에 뉴스 fact 0개. relevance가 0.85에 몰려 weighted_score가 양자화됨. 833종목 재검색 기한이 같은 날에 몰림 | §7.3 | 비활성 예정, 수정 안 함 |
| 8 | 문서 | `스펙.md`, `PROJECT_OVERVIEW.md`의 beat 설명(24시간)이 오래됨. OVERVIEW는 `update_stock_prices`가 `price_history`를 갱신한다고 잘못 적음 | §4, §7.2 | 낮음 |
| 9 | 모델/스키마 | `models.FinancialData.year`는 Integer인데 DB는 varchar. `dart_corp_codes`는 0행 | §2 | 낮음 |
| 10 | 프론트 | 백엔드 주소가 9곳에 하드코딩됨 | §6 | 낮음 |
| 11 | API | `/api/screener`와 `/api/stock/{code}`가 요청마다 FDR을 외부 호출함 (느리고 실패에 취약) | `app/main.py` | 낮음 |
| 12 | 공개 범위 | 홈 화면이 `/api/ranking`으로 **현재 시점 점수 순위**를 보여줌. 스크리너도 현재 점수 순위를 냄. CLAUDE.md의 "현재 시점 점수·순위는 공개하지 않는다"와 충돌함 (공개 전 결정 필요) | §6 | 결정 필요 |
| 13 | 보안 | `SECRET_KEY`가 없으면 하드코딩된 JWT 키를 씀 | `app/auth.py` | 공개 전 확인 |
| 14 | 실험 결과 | `qa_snapshots/`(롤링 IC CSV)가 이 맥에 없음 | §3 | 낮음 |

테스트: `pytest app/tests` → 18 passed (2026-11-12. 구독 테스트는 SQLite 메모리 DB를 쓴다).
