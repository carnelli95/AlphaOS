# QA Report Spec v1

> 작성: 2026-07-02. v1 구현 완료 (`pipeline/stage_result.py`, `pipeline/generate_qa_report.py`, 각 배치 스크립트).

## 1. 목적

뉴스/공시 수집 → Stage C → Stage D → Stage E(Thesis) 파이프라인 실행 후, 사람이 매일 전체 결과를 읽지 않고도 "확인이 필요한 소수"만 골라낼 수 있도록 하는 일일 리포트.

**역할 경계**: 이 리포트는 AI 결과가 "맞았는지" 판정하지 않는다. 근거 개수·Stage 성공 여부 같은 기계적으로 셀 수 있는 것만 집계해 이상 신호를 추리는 역할에 한정한다. 최종 판단은 사람 또는 별도의 LLM Judge(beta-v3, [[project_phase_roadmap]])의 몫.

**위치**: 파이프라인의 일부가 아니라 그 뒤에 붙는 독립 마지막 단계.
```
뉴스/공시 수집 → Stage C → Stage D → Stage E → QA Generator (본 스펙)
```
Stage E 내부 로직 변경이 QA Generator에 영향을 주지 않아야 하고, QA Generator는 단독으로도 재실행 가능해야 한다 (입력 = 그날 DB/로그에 이미 쌓인 결과).

## 2. 저장 방식

JSON 파일 + 로그. DB는 나중에 대시보드/통계가 필요해지면 추가.

```
qa_reports/
    2026-07-02.json
    2026-07-03.json
    latest.json          # 최신 리포트의 복사본 (심볼릭 링크 대신 파일 복사 — Windows 환경 고려)
    stage_runs/
        2026-07-02_stage_c.json       # 각 배치 스크립트가 남기는 중간 산출물 (QA Generator 입력)
        2026-07-02_stage_d_dart.json
        2026-07-02_stage_d_news.json
        2026-07-02_stage_e.json
```

`stage_runs/*.json`은 각 배치 스크립트(`batch_generate_stage_c.py` 등)가 실행 종료 시 `pipeline/stage_result.py`의 `write_stage_result()`를 통해 저장하는 구조화된 결과다 (§7 참고 — stdout 파싱 아님). QA Generator(`pipeline/generate_qa_report.py`)는 이 파일들만 읽어 최종 리포트를 합성한다.

**보존 기간**: 초기에는 무기한 보관(파일당 수십 KB 수준, 부담 적음). 90일 경과 시 정리할지는 검증 기간 이후 재검토.

**Git 포함 여부**: `.gitignore`에 추가 — 매일 생성되는 운영 데이터이므로 커밋 대상 아님(기존 `qa_snapshots/`, 로그 파일들과 동일 취급).

## 3. JSON 스키마

```json
{
  "date": "2026-07-02",
  "generated_at": "2026-07-02T23:50:00+09:00",
  "run_mode": "incremental",
  "triggers": ["incremental_scan", "missing_news"],
  "pipeline_version": {
    "git_commit": "846570d",
    "git_describe": "beta-v1"
  },
  "collection": {
    "news_count": 128,
    "disclosure_count": 19
  },
  "stages": {
    "stage_c": {
      "run_mode": "incremental", "trigger": "new_stock",
      "started_at": "...", "finished_at": "...", "duration_sec": 300.0,
      "target_count": 5, "success": 5, "fail": 0, "skip": 0
    },
    "stage_d_dart": {
      "run_mode": "incremental", "trigger": "new_stock", "step": "collect+generate",
      "started_at": "...", "finished_at": "...", "duration_sec": 300.0,
      "target_count": 124, "success": 124, "fail": 2, "skip": 0
    },
    "stage_d_news": {
      "run_mode": "incremental", "trigger": "missing_news",
      "started_at": "...", "finished_at": "...", "duration_sec": 120.0,
      "target_count": 124, "success": 124, "fail": 0, "skip": 0
    },
    "stage_e": {
      "run_mode": "incremental", "trigger": "incremental_scan",
      "started_at": "...", "finished_at": "...", "duration_sec": 480.0,
      "target_count": 27, "success": 27, "fail": 0, "skip": 0,
      "quality": { "high": 14, "medium": 9, "low": 4 }
    }
  },
  "attribution": {
    "retried": 1, "permanent_fail": 0, "retry_rate_pct": 3.7
  },
  "issues": [
    {
      "severity": "warning",
      "stock_code": "005930",
      "name": "삼성전자",
      "category": "dart_missing",
      "reason": "정량은 우수하나 DART 사업보고서 근거 미확인",
      "action": "Thesis 검토 권장"
    },
    {
      "severity": "critical",
      "stock_code": null,
      "name": null,
      "category": "stage_failure",
      "reason": "Stage D 배치 2건 실패",
      "action": "로그 확인 후 재실행"
    }
  ],
  "summary": {
    "total_theses": 27,
    "issue_count": 2,
    "issue_rate_pct": 7.4
  },
  "delta_vs_previous": {
    "collection.news_count": -37,
    "collection.disclosure_count": 0,
    "summary.issue_count": -2,
    "stage_e.quality.low": 6,
    "attribution.retried": 1
  }
}
```

## 4. 필드 정의

| 필드 | 타입 | 설명 |
|---|---|---|
| `run_mode` / `triggers` | string / array | 그날 실행된 Stage들 중 하나라도 `--force`(전체 재생성)면 `full_rebuild`, 아니면 `incremental`. `triggers`는 실행된 Stage들의 `trigger` 값 집합 — "왜 오늘 Thesis가 N개나 다시 생성됐는지" 바로 추적하기 위함 (§6 참조) |
| `collection.news_count` / `disclosure_count` | int | 당일 신규 수집 건수 |
| `stages.*.run_mode` / `trigger` | string | 해당 Stage 단독의 실행 모드/트리거 (아래 §6) |
| `stages.*.success` / `fail` / `skip` | int | Stage별 처리 건수 (기존 배치 로그 용어 그대로 사용, [[project_stage_c_status]]). Validator 영구 실패는 `stages.stage_c.validator.permanent_fail` / `attribution.permanent_fail`처럼 별도 필드 |
| `stages.stage_e.quality` | object | `compute_confidence()`(`pipeline/generate_thesis.py:126`)의 `level` 분포를 그대로 집계 |
| `attribution` | object | 기존 `_retry_stats`(`pipeline/generate_thesis.py:674`) 재사용 |
| `issues[]` | array | "주의 필요" 항목. 아래 4.1 참조 |
| `delta_vs_previous` | object | 전일 `qa_reports/{어제날짜}.json`과 비교한 증감. 전일 리포트가 없으면 해당 필드 생략(에러 아님) |

### 4.1 `issues[]` — severity / category

**severity**
- `critical`: 파이프라인·Stage 자체가 실패해 결과물이 없거나 신뢰 불가 (재실행 필요)
- `warning`: 결과물은 있으나 근거가 약함 (사람 검토 권장)
- `info`: 자동 복구됨 (예: validator retry 성공) — 참고용, 조치 불요

**category** (원인별 분류 — 통계 집계 단위)
| category | 의미 | severity 기본값 | 소스 |
|---|---|---|---|
| `data_missing` | 정량·DART 근거 모두 없음 | warning | `compute_confidence().caution` |
| `quant_missing` | 정량 데이터 없음, DART만 있음 | warning | 〃 |
| `dart_missing` | DART 근거 없음, 정량만 있음 | warning | 〃 |
| `stage_failure` | Stage C/D/E 처리 자체가 실패 | critical | 배치 스크립트 에러 로그 |
| `pipeline_failure` | 수집~저장 사이 예외 (DB 연결 등) | critical | 배치 스크립트 에러 로그 |
| `attribution_retry` | Attribution validator 재시도 발생 | info (성공) / critical (영구 실패) | `_retry_stocks`, `_retry_stats["permanent_fail"]` |
| `timeout` | 외부 API(LLM/DART) 타임아웃 | critical | 배치 스크립트 에러 로그 |
| `quality_warning` | `level == "Low"` (= `caution is not None`, 4.2 참조) | warning | `compute_confidence()` |

**주석**: `compute_confidence()`를 코드로 확인한 결과, `level == "Low"`와 `caution is not None`은 동치 조건이다(quant_count==0 또는 dart_count==0일 때만 Low이자 caution 설정됨). 따라서 `quality_warning`과 `data_missing`/`quant_missing`/`dart_missing`은 사실상 같은 신호를 다른 각도로 표현한 것 — `issues[]`에는 `caution` 문자열 기반으로 `data_missing`/`quant_missing`/`dart_missing` 중 하나만 기록하고, `quality_warning`은 통계 집계(quality 분포)에서만 쓴다. 별도 카테고리로 중복 기록하지 않는다.

### 4.2 Human Action Required

모든 `issues[]` 항목은 `action` 필드를 포함해 "무엇을 해야 하는지"를 사람이 바로 알 수 있게 한다. 정형 문구 세트로 시작 (자유 텍스트 아님 — 나중에 자동 알림 라우팅에 쓸 수 있도록):
- `"Thesis 검토 권장"` (warning, 사람이 내용 확인)
- `"로그 확인 후 재실행"` (critical, 파이프라인 재실행)
- `"조치 불요 (자동 복구됨)"` (info)

## 5. Δ 계산 규칙

- `qa_reports/{date}.json` 생성 직전, `qa_reports/{date-1}.json`을 읽어 `collection.*`, `summary.issue_count`, `stages.*.quality.low` 등 핵심 지표만 비교해 `delta_vs_previous`에 기록.
- 전일 파일이 없으면(첫 실행, 파이프라인 미실행일 등) `delta_vs_previous` 필드 자체를 생략 — null로 채우지 않음(값 0과 혼동 방지).
- 비교 대상 필드는 화이트리스트로 관리 (스키마 전체를 자동 diff하지 않음) — 새 필드 추가 시 Δ 대상에 넣을지 별도 판단.

## 6. 운영 정책 — Incremental vs Full Rebuild (2026-07-02 확정)

**기본 원칙**: 매일 자동 실행되는 배치는 신규/변경분만 처리(Incremental). 전체 재생성(Full Rebuild)은 Stage 알고리즘·프롬프트·confidence 계산 변경처럼 과거 결과까지 다시 만들어야 하는 예외 상황에서만 수동 실행한다. 이 구분은 이미 각 배치 스크립트의 `--force` 플래그로 구현되어 있다 — 새로 만든 개념이 아니라 기존 CLI 플래그를 공식 운영 정책으로 확정한 것.

| run_mode | 의미 | 발동 조건 |
|---|---|---|
| `incremental` | 신규/변경분만 처리 (기본, 매일 자동) | `--force` 미지정 |
| `full_rebuild` | 대상 전체 재생성 | `--force` 지정 (수동 실행, [[feedback_batch_execution]] — 명령어만 제시하고 직접 실행 금지) |

**Stage별 `trigger` (incremental 모드에서 왜 이 종목이 대상이 됐는지)**:
| stage | trigger | 조건 |
|---|---|---|
| stage_c | `new_stock` | `stage_c_sections IS NULL` |
| stage_c | `upgrade_format` | `--upgrade` (구 포맷 재처리) |
| stage_d_dart | `new_disclosure` | 신규 종목(`stage_d_sections` 없음) ∪ 워터마크 이후 시장 전체 공시 스캔에서 새 공시가 생긴 종목 ∪ `stage_d_raw.pending_generate` |
| stage_d_dart | `rebuild_window` | `--rebuild-window` (최근 180일 전체 재스캔, 수동 실행) |
| stage_d_news | `rescan_due` | `news_scanned_at` 없음 또는 7일 경과, 오래된 순 150개/일 |
| stage_e | `incremental_scan` | `new_stock`(thesis 없음) ∪ `stage_d_updated`(아래) 스캔 결과 |

**Stage E 재생성 트리거 — 타임스탬프 비교 (신규 구현, 2026-07-02):** 기존에는 `thesis_sections IS NULL`인 종목(한 번도 생성 안 됨)만 incremental 대상이었음 — 이미 Thesis가 있는데 그 이후 Stage D에 새 뉴스/공시가 추가된 경우를 놓치는 구멍이 있었다. `pipeline/batch_generate_thesis.py`의 `get_stage_d_updated_codes()`가 `stage_d_sections->>'generated_at' > thesis_sections->>'generated_at'`인 종목을 추가로 찾아 기본 incremental 대상에 포함시킨다. 이를 위해 `pipeline/generate_stage_d_news.py`의 `add_news_to_stage_d()`가 facts가 실제로 바뀔 때 `stage_d_sections.generated_at`을 갱신하도록 수정함 (기존에는 뉴스 병합 후에도 원래 DART 생성 시각이 그대로 남아있던 버그).

**Stage D 증분 갱신 (2026-11-06):** 07-04~07-15 운영 QA Report에서 공시 수집이 12일 연속 0건으로 확인됨. 원인은 (1) Stage D DART/뉴스가 "비어 있는 종목만 채우는" 구조라 최초 생성 후 갱신이 없었고, (2) 공시 유형 필터가 `A`(실제로는 정기공시)·`B`·`F`라 거래소 수시공시(`I`: 공급계약·잠정실적·배당·소각)가 아예 수집되지 않았던 것. 수정:
- DART: `pipeline_state.stage_d_dart_scanned_through` 워터마크 ~ 오늘의 시장 전체 공시를 corp_code 없이 조회 → 유니버스 종목별 `stage_d_raw`에 rcept_no 기준 병합 → 새 공시가 생긴 종목만 Fact 재생성. raw 저장 후 생성 전 실패는 `pending_generate` 플래그로 다음 실행에서 재시도. 워터마크가 없으면 Stage 오류로 멈추고 `--rebuild-window` 수동 실행을 요구.
- 뉴스: `news_scanned_at` 기반 주기 재검색. 기존 news facts와 합집합(URL 기준) 후 상위 5개 유지 — 재검색이 과거 주요 기사를 덮어쓰지 않도록. 내용이 같으면 `generated_at`을 건드리지 않아 Thesis 불필요 재생성 방지.

**알려진 한계**: Stage C는 아직 `generated_at`을 기록하지 않아 이 비교에서 제외됨 — 사업보고서 기반이라 갱신 빈도가 낮아 v1에서는 범위 밖으로 뒀다. 필요해지면 Stage C 결과에도 `generated_at`을 추가하고 트리거 목록에 편입.

## 7. 확정된 결정 사항 (2026-07-02)

**stdout 파싱 금지, 구조화된 결과만 사용.** stdout은 사람이 보는 로그일 뿐 시스템 간 인터페이스가 아니다 — 로그 문구가 바뀌면 파싱이 깨진다. 대신 각 배치 스크립트(`batch_generate_stage_c.py`, `batch_generate_stage_d.py`, `batch_generate_stage_d_news.py`, `batch_generate_thesis.py`)의 `run()`/`main()`이 실행 종료 시 구조화된 결과 dict를 만들어 `pipeline/stage_result.py`의 `write_stage_result(stage, result)`로 `qa_reports/stage_runs/{date}_{stage}.json`에 저장하고, 함수 자신도 그 dict를 반환한다. QA Generator는 이 JSON 파일만 읽는다.

**시작/종료 시각은 각 Stage가 직접 기록.** 배치 스크립트 자신이 `started_at`/`finished_at`/`duration_sec`을 재서 결과 dict에 포함시킨다 (QA Generator가 별도로 계산하지 않음). Celery Beat 태스크 메타데이터에는 의존하지 않음 — Stage 자체가 CLI로 단독 실행되는 경우(배치 재실행, 수동 재생성)에도 항상 정확한 시각을 남기기 위함.

**Build Version 메타데이터 추가.** `pipeline_version.git_commit`(짧은 SHA)과 `git_describe`(최근 태그 기준, 예: `beta-v1`)를 리포트 최상위와 각 `stage_runs/*.json`에 포함 (`stage_result.py`의 `git_commit_short()` / `git_describe()`). 배포 시점과 품질 변화 시점을 나중에 대조하기 위함.

**구현된 파일**: `pipeline/stage_result.py`(공유 헬퍼), `pipeline/generate_qa_report.py`(QA Generator, `--date` 옵션으로 특정 날짜 재실행 가능), 4개 배치 스크립트 수정.

**알려진 근사치 (v1 한계)**:
- `collection.news_count`는 원본 API(Naver) 수집 건수가 아니라 Stage D 병합 후 남은 news fact 수(`stage_d_news.news_facts`)를 사용 — DART 근거와 병합·필터링을 거친 값이라 실제 수집량보다 적을 수 있음. 원본 수집 건수를 별도로 트래킹하려면 `collect_news.py`/`generate_stage_d_news.py`쪽에 카운터 추가가 필요 (v2 후보).
- `timeout` 카테고리는 아직 배치 스크립트가 예외 유형을 구분해 기록하지 않아 `issues[]`에서 실제로 발생하지 않음 (모든 예외가 `pipeline_failure`로 뭉뚱그려짐). 세분화하려면 각 배치의 `except` 블록에서 예외 종류별로 카운트해야 함.
- `batch_generate_stage_d_news.py`의 기본 incremental 대상은 "news fact가 하나도 없는 종목"이다 — 한 번이라도 뉴스가 붙은 종목은 이후 새 뉴스가 나와도 재스캔되지 않는다(뉴스는 매번 API로 새로 수집하는 구조라 "새 뉴스 발생"을 감지하는 별도 트리거가 없음). Stage E처럼 타임스탬프 비교로 개선하려면 "언제 마지막으로 뉴스를 스캔했는지"를 stage_d_sections에 별도 기록해야 함 — v1 범위 밖, 필요해지면 추가.

## 8. Celery Beat 연결 (2026-07-02 구현 완료)

`app/tasks.py`에 `run_daily_pipeline` 태스크 추가, `app/celery_app.py`의 `beat_schedule`에 `run-daily-pipeline`(24시간마다) 등록 완료.

**실행 순서 보장**: Stage C → Stage D(DART) → Stage D(뉴스) → Stage E → QA Report를 순차 실행하되, 각 Stage 함수가 예외를 던지면(=Stage 전체가 죽음, 개별 종목 실패가 아님) 이후 Stage를 스킵하고 실패한 Stage 자리에 `fail=1`짜리 결과를 직접 남긴다. 이러면 기존 QA Generator의 `stage_failure`/`pipeline_failure` 감지 로직을 그대로 재사용할 수 있어 QA Generator 쪽 코드는 건드릴 필요가 없었다. QA Report는 성공/실패 여부와 무관하게 항상 마지막에 생성한다.

**전제 조건이었던 리팩터링**: `batch_generate_stage_d.py`/`batch_generate_stage_d_news.py`의 `main()`이 argparse를 함수 내부에서 직접 파싱하고 있어 Celery 태스크에서 직접 호출하면 워커 프로세스의 실제 `sys.argv`를 파싱하려다 깨지는 문제가 있었음 — `run(...)`(파라미터 기반)과 `main()`(CLI 진입점, argparse 후 `run()` 호출)으로 분리해 해결. `batch_generate_stage_c.py`/`batch_generate_thesis.py`는 이미 이 구조였음.

**자동 재시도 없음(의도적 결정)**: 다른 기존 태스크(`update_stock_prices` 등)는 `self.retry(exc=exc, countdown=...)`를 쓰지만, `run_daily_pipeline`은 재시도하지 않는다. LLM/DART/Naver API 호출 비용이 크고, 원인 파악 전에 실패한 배치를 자동으로 반복 실행하면 비용·API 쿼터를 낭비할 위험이 있음 — 실패 시 사람이 QA Report의 `issues[]`(`category: pipeline_failure`)를 보고 원인을 파악한 뒤 수동으로 재실행하는 것을 의도함.

**스케줄 주기**: 기존 3개 태스크와 동일하게 `schedule: 60*60*24`(Beat 시작 시점 기준 24시간 간격, 고정 시각이 아님) 방식을 그대로 따름 — 특정 시각(예: 새벽 2시) 고정이 필요하면 `celery.schedules.crontab`으로 쉽게 전환 가능.

**다음 단계**: 없음 — QA Report 인프라, 증분 정책, Beat 연결까지 v1 범위는 완료. 이제부터는 [[project_verification_phase]]에 따라 실제 운영하며 QA Report로 이상 사례를 수집하고 필요 시 임계값/트리거를 조정하는 단계.
