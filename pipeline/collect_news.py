"""
뉴스 이벤트 수집기 — 네이버 뉴스 검색 API (MVP)

stage_d_sections fact 스키마와 동일한 구조로 정규화하여 반환.
source_type = "news" (SOURCE_PRIORITY = 60)

사전 준비:
  1. https://developers.naver.com/apps/ 에서 애플리케이션 등록
  2. "검색" API 권한 추가
  3. Client ID / Client Secret 발급 후 .env에 추가:
     NAVER_CLIENT_ID=...
     NAVER_CLIENT_SECRET=...

API 한도: 25,000 calls/day (검색 API)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import requests

# 네이버 뉴스 API 엔드포인트
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

# stage_d와 동일한 source_type 식별자
SOURCE_TYPE = "news"

# 검색 윈도우 (DART 수시공시와 동일하게 180일)
DEFAULT_WINDOW_DAYS = 180

# 종목명 + 키워드로 관련성 필터링
_FINANCIAL_KEYWORDS = [
    "계약", "수주", "공급", "합병", "분할", "증자", "자기주식", "배당",
    "투자", "인수", "실적", "매출", "영업이익", "대표이사", "사장", "제재",
    "소송", "파산", "회생", "신공장", "설비", "특허", "수출", "MOU",
]


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
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return text.strip()


def _classify_event_type(title: str, description: str) -> str:
    """뉴스 제목/내용으로 event_type 분류."""
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
    """뉴스 기사의 재무 영향도 추정 (보수적 기본값 low)."""
    combined = title + " " + description
    high_keywords = ["합병", "인수", "유상증자", "대형 계약", "조 원", "억 달러"]
    medium_keywords = ["수주", "계약", "배당", "자기주식", "투자"]
    if any(k in combined for k in high_keywords):
        return "high"
    if any(k in combined for k in medium_keywords):
        return "medium"
    return "low"


def fetch_news_events(
    company_name: str,
    client_id: str,
    client_secret: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_items: int = 20,
) -> list[dict]:
    """
    네이버 뉴스 API로 기업 관련 뉴스를 수집하고
    stage_d_sections fact 형식으로 정규화하여 반환한다.

    반환 형식 (stage_d_sections facts와 동일):
    [
      {
        "date":             "20260318",
        "recency_days":     45,
        "type":             "contract",
        "fact":             "대형 공급계약 체결 보도",
        "financial_impact": "medium",
        "importance":       2,
        "dart_id":          "",           # 뉴스는 dart_id 없음
        "source_type":      "news",
        "news_url":         "https://...",
      }
    ]
    """
    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query":  company_name,
        "display": min(max_items, 100),
        "start":   1,
        "sort":    "date",
    }
    try:
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[collect_news] 네이버 API 오류: {exc}")
        return []

    items = data.get("items", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    facts: list[dict] = []

    for item in items:
        title = _strip_html(item.get("title", ""))
        desc  = _strip_html(item.get("description", ""))
        pub   = item.get("pubDate", "")
        url   = item.get("originallink") or item.get("link", "")

        date_str, recency = _parse_naver_date(pub)
        if recency > window_days:
            continue

        # 재무·기업 관련성 필터 — 일반 뉴스 제외
        combined = title + " " + desc
        if not any(k in combined for k in _FINANCIAL_KEYWORDS):
            continue

        ev_type = _classify_event_type(title, desc)
        impact  = _classify_impact(title, desc)

        facts.append({
            "date":             date_str,
            "recency_days":     recency,
            "type":             ev_type,
            "fact":             title[:100],
            "financial_impact": impact,
            "importance":       2,          # 뉴스는 기본 중요도 2 (DART보다 보수적)
            "dart_id":          "",
            "source_type":      SOURCE_TYPE,
            "news_url":         url,
        })

    # 중요도·최신성 순 정렬
    facts.sort(key=lambda f: (f["recency_days"], -f.get("importance", 2)))
    return facts[:5]


def merge_with_dart_facts(
    dart_facts: list[dict],
    news_facts: list[dict],
) -> list[dict]:
    """
    DART facts와 뉴스 facts를 SOURCE_PRIORITY 기준으로 병합한다.

    규칙:
    - DART facts는 그대로 유지 (변경 없음)
    - 뉴스 fact 중 같은 날짜(date) + 같은 type으로 DART fact가 이미 있으면 제외
      (DART가 더 신뢰할 수 있으므로 news는 DART의 보완 역할만 수행)
    - 뉴스끼리는 중복 제거하지 않음 (같은 날 여러 이벤트 허용)
    - importance 내림차순 → recency_days 오름차순 정렬 후 최대 5개
    """
    # DART facts의 (date, type) 집합 — 뉴스 중복 체크용
    dart_keys: set[tuple] = {
        (f.get("date", ""), f.get("type", "other")) for f in dart_facts
    }

    # DART에 없는 뉴스만 보완 대상으로 선택
    additional_news = [
        f for f in news_facts
        if (f.get("date", ""), f.get("type", "other")) not in dart_keys
    ]

    # DART는 항상 먼저 → 남은 슬롯(최대 5-len(dart))을 뉴스로 채움
    dart_sorted = sorted(
        dart_facts,
        key=lambda f: (-f.get("importance", 2), f.get("recency_days", 9999)),
    )
    slots_left = max(0, 5 - len(dart_sorted))
    news_sorted = sorted(
        additional_news,
        key=lambda f: (-f.get("importance", 2), f.get("recency_days", 9999)),
    )[:slots_left]

    return (dart_sorted + news_sorted)[:5]


if __name__ == "__main__":
    import io, sys
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
    print(f"뉴스 수집: {company}")
    events = fetch_news_events(company, CLIENT_ID, CLIENT_SECRET)
    print(f"수집된 이벤트: {len(events)}개")
    for e in events:
        print(f"  [{e['date']}] +{e['recency_days']}일  [{e['type']}] {e['fact']}")
