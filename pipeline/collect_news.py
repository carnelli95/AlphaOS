"""
뉴스 이벤트 수집기 — 네이버 뉴스 검색 API

2단계 필터:
  1. 규칙 기반 (Rule-based): 시장·업종 일반 뉴스 즉시 제거 (토큰 비용 없음)
  2. LLM 관련성 필터 (Relevance): 기사 묶음을 한 번에 전송, 0~1 점수 반환
     ≥ 0.75 → 채택 / 0.5~0.75 → 후보 / < 0.5 → 제거

stage_d_sections fact 스키마와 동일한 구조로 정규화.
source_type = "news" (SOURCE_PRIORITY = 60)

사전 준비:
  .env에 다음 추가:
    NAVER_CLIENT_ID=...
    NAVER_CLIENT_SECRET=...

API 한도: 25,000 calls/day (검색 API)
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from generate_ai_report import call_llm

NAVER_NEWS_URL    = "https://openapi.naver.com/v1/search/news.json"
SOURCE_TYPE       = "news"
DEFAULT_WINDOW_DAYS = 180

# ─── 1단계: 규칙 기반 제거 패턴 ────────────────────────────────────────
# 제목에 이 패턴이 있으면 회사 직접 이벤트가 아닌 시장/업종 뉴스로 간주 → 즉시 제거
_NOISE_TITLE_PATTERNS: list[str] = [
    "코스피", "코스닥", "증시", "뉴욕증시", "나스닥", "다우존스",
    "외국인 순매수", "외국인 순매도", "기관 순매수", "기관 순매도",
    "업종 전반", "반도체 업황", "시장 전망", "증시 전망", "주식시장",
    "개장 시황", "마감 시황", "오후장", "오전장", "장 초반",
    "ETF", "레버리지 ETF", "공모펀드", "리포트", "애널리스트",
    "투자의견", "목표주가", "목표 주가", "TP 상향", "TP 하향",
    "주간 전망", "월간 전망", "글로벌 증시", "연준", "금리",
    "환율", "유가", "원자재", "인플레이션",
]

# ─── 2단계: LLM 관련성 임계값 ───────────────────────────────────────────
RELEVANCE_THRESHOLD  = 0.75   # 이 이상만 채택
CANDIDATE_THRESHOLD  = 0.50   # 이 미만은 즉시 제거


def _parse_naver_date(pub_date: str) -> tuple[str, int]:
    """
    네이버 API pubDate ("Tue, 01 Apr 2025 10:00:00 +0900") 파싱.
    (date_str "YYYYMMDD", recency_days) 반환.
    """
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        now = datetime.now(timezone.utc)
        recency = max(0, (now - dt).days)
        return dt.strftime("%Y%m%d"), recency
    except ValueError:
        return "", 9999


def _strip_html(text: str) -> str:
    """네이버 뉴스 제목/설명의 HTML 태그와 엔티티 제거."""
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&").replace("&quot;", '"'))
    return text.strip()


def _rule_filter(company_name: str, title: str, desc: str) -> bool:
    """
    True → 1단계 통과 (LLM으로 넘길 후보)
    False → 즉시 제거

    기준:
    - 제목에 회사명이 있어야 함 (회사를 단순 언급하는 기사 제외)
    - 제목에 시장/업종 노이즈 패턴이 없어야 함
    """
    if company_name not in title:
        return False
    combined = title + " " + desc
    if any(p in combined for p in _NOISE_TITLE_PATTERNS):
        return False
    return True


# event_type 정의 — 점수 구간과 1:1 대응
# Stage E·랭킹에서 LLM 재호출 없이 타입별 가중치 적용 가능
NEWS_EVENT_TYPES = {
    "major_contract",    # 대규모 수주·공급계약  (0.85+)
    "earnings",          # 실적 발표            (0.85+)
    "capex",             # 공장·설비 증설·투자   (0.85+)
    "acquisition",       # M&A·합병·분할        (0.85+)
    "fundraising",       # 유상증자·CB·자회사IPO (0.85+)
    "regulation",        # 규제 승인·제재·소송   (0.85+)
    "product_launch",    # 신제품·서비스 출시    (0.5~0.84)
    "executive_comment", # 경영진 전략 인터뷰    (0.5~0.84)
    "partnership",       # MOU·파트너십(소규모)  (0.5~0.84)
    "restructuring",     # 구조조정·인원 감축    (0.5~0.84)
    "other",             # 위 분류 외            (any)
    "noise",             # 제거 대상             (0.0~0.49)
}

# 이벤트 유형별 투자 중요도 가중치 (Stage E 활용 예정)
EVENT_TYPE_WEIGHT: dict[str, int] = {
    "major_contract":    5,
    "earnings":          4,
    "capex":             4,
    "acquisition":       5,
    "fundraising":       3,
    "regulation":        4,
    "product_launch":    2,
    "executive_comment": 1,
    "partnership":       2,
    "restructuring":     2,
    "other":             1,
    "noise":             0,
}


def _llm_relevance_filter(
    company_name: str,
    articles: list[dict],  # [{"id": int, "title": str, "desc": str}, ...]
) -> list[dict]:
    """
    LLM으로 기사 관련성 일괄 평가.
    반환: [{"id": int, "relevance": float, "event_type": str, "reason": str}, ...]
    """
    if not articles:
        return []

    items_block = "\n".join(
        f'[{a["id"]}] 제목: {a["title"]}\n    설명: {a["desc"][:100]}'
        for a in articles
    )

    prompt = f"""너는 투자 분석용 뉴스 필터링 AI야.
회사명: "{company_name}"

아래 기사들이 "{company_name}"의 **투자 Thesis에 실질적으로 영향을 주는 이벤트**인지 0~1 점수로 평가해.
기업 관련 기사라도 Thesis에 영향이 없으면 낮게 줘야 해.

[채점 기준 — 엄격하게 적용]
■ 0.85~1.0 (채택)
  대규모 수주·공급계약, 분기/연간 실적 발표, 신규 사업 투자 결정,
  공장·설비 증설, M&A·합병·분할, 유상증자·전환사채, 자회사 IPO,
  규제 승인·인가·특허, 주요 소송·제재·과징금

■ 0.5~0.84 (경계 — 핵심 정보가 있을 때만)
  경영진 전략 인터뷰(실적·사업 전망 포함), 신제품·서비스 출시,
  파트너십·MOU(소규모), 구조조정·인원 감축

■ 0.0~0.49 (제거)
  행사·포럼 참가, 수상, ESG·CSR 단순 활동,
  업계 일반 동향(기업이 사례로만 언급), 주가·시황 기사,
  애널리스트 목표주가 변경, 내용 없는 단순 인터뷰

[event_type 값 (반드시 아래 중 하나만 사용)]
major_contract | earnings | capex | acquisition | fundraising | regulation |
product_launch | executive_comment | partnership | restructuring | other | noise

[기사 목록]
{items_block}

[출력 — 반드시 아래 JSON 형식, 다른 텍스트 절대 금지]
{{"evaluations": [
  {{"id": 번호, "relevance": 0.0~1.0, "event_type": "위 목록 중 하나", "reason": "채택/제거 근거 한 줄"}}
]}}"""

    raw = call_llm(prompt)

    # call_llm은 json_object 모드라 항상 dict 반환
    # LLM이 사용하는 키 이름을 순서대로 시도
    items: list = []
    if isinstance(raw, dict):
        for key in ("evaluations", "items", "results", "articles", "scores", "data"):
            if key in raw and isinstance(raw[key], list):
                items = raw[key]
                break
        else:
            # fallback: dict 내 첫 번째 list 값
            items = next((v for v in raw.values() if isinstance(v, list)), [])
    elif isinstance(raw, list):
        items = raw

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            event_type = str(item.get("event_type", "other"))
            if event_type not in NEWS_EVENT_TYPES:
                event_type = "other"
            results.append({
                "id":         int(item["id"]),
                "relevance":  float(item.get("relevance", 0.0)),
                "event_type": event_type,
                "reason":     str(item.get("reason", "")),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return results


def _classify_event_type(title: str, description: str) -> str:
    combined = title + " " + description
    if any(k in combined for k in ["계약", "수주", "공급계약", "MOU"]):
        return "contract"
    if any(k in combined for k in ["합병", "분할", "인수", "대표이사", "사장", "최대주주"]):
        return "management"
    if any(k in combined for k in ["공장", "설비", "생산", "준공"]):
        return "operations"
    if any(k in combined for k in ["증자", "자기주식", "배당", "전환사채", "CB", "BW"]):
        return "finance"
    if any(k in combined for k in ["소송", "제재", "조사", "적발", "과징금"]):
        return "risk"
    return "other"


def _classify_impact(title: str, description: str) -> str:
    combined = title + " " + description
    if any(k in combined for k in ["합병", "인수", "유상증자", "대형 계약", "조 원", "억 달러"]):
        return "high"
    if any(k in combined for k in ["수주", "계약", "배당", "자기주식", "투자"]):
        return "medium"
    return "low"


def fetch_news_events(
    company_name: str,
    client_id: str,
    client_secret: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_fetch: int = 30,
    verbose: bool = False,
) -> list[dict]:
    """
    네이버 뉴스 API로 기업 관련 뉴스를 수집 → 2단계 필터 적용 → 정규화.

    반환 형식 (stage_d_sections facts와 동일):
    [
      {
        "date":             "YYYYMMDD",
        "recency_days":     int,
        "type":             "contract|operations|management|finance|risk|other",
        "fact":             "기사 제목 (최대 100자)",
        "financial_impact": "none|low|medium|high",
        "importance":       int(1~3),
        "dart_id":          "",
        "source_type":      "news",
        "news_url":         "https://...",
        "relevance":        float,   # LLM 관련성 점수
      }
    ]
    최대 5개 반환.
    """
    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query":   company_name,
        "display": min(max_fetch, 100),
        "start":   1,
        "sort":    "date",
    }
    try:
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        raw_items = resp.json().get("items", [])
    except Exception as exc:
        # 빈 리스트로 삼키면 호출 측이 "뉴스 없음"으로 보고 news_scanned_at을 기록해
        # 실제로는 검색 안 된 종목이 재검색 주기 동안 빠진다 → 실패로 올림
        raise RuntimeError(f"네이버 API 오류: {exc}") from exc

    # ── 기간 필터 + 파싱 ──────────────────────────────────────────────
    parsed: list[dict] = []
    for item in raw_items:
        title = _strip_html(item.get("title", ""))
        desc  = _strip_html(item.get("description", ""))
        date_str, recency = _parse_naver_date(item.get("pubDate", ""))
        if recency > window_days:
            continue
        parsed.append({
            "title":    title,
            "desc":     desc,
            "date_str": date_str,
            "recency":  recency,
            "url":      item.get("originallink") or item.get("link", ""),
        })

    if verbose:
        print(f"  [필터] API 수신: {len(raw_items)}건 → 기간내: {len(parsed)}건")

    # ── 1단계: 규칙 기반 필터 ─────────────────────────────────────────
    candidates = [a for a in parsed if _rule_filter(company_name, a["title"], a["desc"])]

    if verbose:
        removed_by_rule = len(parsed) - len(candidates)
        print(f"  [필터 1단계] 규칙 기반 제거: {removed_by_rule}건 → 후보: {len(candidates)}건")
        for a in candidates:
            print(f"    • {a['title'][:70]}")

    if not candidates:
        return []

    # ── 2단계: LLM 관련성 필터 ────────────────────────────────────────
    llm_input = [{"id": i, "title": a["title"], "desc": a["desc"]}
                 for i, a in enumerate(candidates)]
    llm_results = _llm_relevance_filter(company_name, llm_input)

    # id → relevance 매핑
    rel_map = {r["id"]: r for r in llm_results}

    if verbose:
        print(f"  [필터 2단계] LLM 관련성 평가:")
        for i, a in enumerate(candidates):
            r = rel_map.get(i, {})
            score      = r.get("relevance", 0)
            event_type = r.get("event_type", "other")
            reason     = r.get("reason", "")
            mark = "✅" if score >= RELEVANCE_THRESHOLD else ("⚠" if score >= CANDIDATE_THRESHOLD else "❌")
            print(f"    {mark} [{score:.2f}] [{event_type}] {a['title'][:55]}")
            if reason:
                print(f"         → {reason}")

    # ── 정규화 + 임계값 적용 ──────────────────────────────────────────
    facts: list[dict] = []
    for i, a in enumerate(candidates):
        r          = rel_map.get(i, {})
        relevance  = float(r.get("relevance", 0.0))
        if relevance < RELEVANCE_THRESHOLD:
            continue

        event_type = r.get("event_type", "other")
        if event_type not in NEWS_EVENT_TYPES:
            event_type = "other"

        title, desc = a["title"], a["desc"]
        importance  = 3 if relevance >= 0.9 else 2

        # event_weight: 기준 = earnings/capex/regulation = 1.0 (÷4 정규화)
        # event_confidence = relevance: LLM 분류 불확실성 패널티
        #   → weighted_score = relevance² × event_weight
        #   → 높은 relevance(0.95) → 패널티 미미, 낮은 relevance(0.76) → 자동 감점
        raw_weight        = EVENT_TYPE_WEIGHT.get(event_type, 1)
        event_weight      = round(raw_weight / 4, 3)
        event_confidence  = round(relevance, 2)       # LLM 분류 확신도 = relevance
        weighted_score    = round(relevance * event_weight * event_confidence, 3)

        facts.append({
            "date":             a["date_str"],
            "recency_days":     a["recency"],
            "type":             _classify_event_type(title, desc),
            "event_type":       event_type,
            "fact":             title[:100],
            "financial_impact": _classify_impact(title, desc),
            "importance":       importance,
            "dart_id":          "",
            "source_type":      SOURCE_TYPE,
            "news_url":         a["url"],
            "relevance":        round(relevance, 2),
            "event_confidence": event_confidence,
            "event_weight":     event_weight,
            "weighted_score":   weighted_score,
        })

    # weighted_score 내림차순 정렬 후 event diversity 적용
    # — 동일 event_type 최대 2개: major_contract 3건이 상위를 독점하는 현상 방지
    facts.sort(key=lambda f: (-f.get("weighted_score", 0), f.get("recency_days", 9999)))
    seen: Counter = Counter()
    diverse: list[dict] = []
    for f in facts:
        et = f.get("event_type", "other")
        if seen[et] < 2:
            diverse.append(f)
            seen[et] += 1
    return diverse[:5]


def merge_with_dart_facts(
    dart_facts: list[dict],
    news_facts: list[dict],
) -> list[dict]:
    """
    DART facts와 뉴스 facts를 병합한다.

    규칙:
    - DART facts는 항상 먼저 (source_priority=100)
    - 뉴스 fact 중 같은 날짜(date) + 같은 type으로 DART fact가 이미 있으면 제외
    - DART 슬롯 이후 남은 자리(max 5)를 뉴스로 채움
    """
    dart_keys: set[tuple] = {
        (f.get("date", ""), f.get("type", "other")) for f in dart_facts
    }
    additional_news = [
        f for f in news_facts
        if (f.get("date", ""), f.get("type", "other")) not in dart_keys
    ]

    dart_sorted = sorted(dart_facts,
                         key=lambda f: (-f.get("importance", 2), f.get("recency_days", 9999)))
    slots_left  = max(0, 5 - len(dart_sorted))
    news_sorted = sorted(additional_news,
                         key=lambda f: (-f.get("importance", 2), f.get("recency_days", 9999)))

    return (dart_sorted + news_sorted[:slots_left])[:5]


if __name__ == "__main__":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
    CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

    if not CLIENT_ID:
        print("❌ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수를 .env에 추가하세요.")
        sys.exit(1)

    company = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
    print(f"=== 뉴스 수집 + 2단계 필터: {company} ===\n")
    events = fetch_news_events(company, CLIENT_ID, CLIENT_SECRET, verbose=True)
    print(f"\n최종 채택: {len(events)}개")
    for e in events:
        print(f"  [{e['date']}] [{e['relevance']:.2f}] [{e['type']}] {e['fact']}")
