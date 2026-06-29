"""
DART 사업보고서 원문 수집기

흐름:
    1. corp_code로 최근 사업보고서(A001)의 rcept_no 검색 (list.json)
    2. rcept_no로 원문 zip 다운로드 (document.xml)
    3. zip 안의 xml/html 텍스트에서 "II. 사업의 내용" 섹션을 best-effort로 추출
    4. 원문 전체 텍스트 + 추출된 섹션을 모두 저장 (추출 실패해도 원문은 남는다)

주의:
    DART 원문 문서는 진짜 XML이 아니라 HTML 비슷한 커스텀 마크업이라
    회사/연도별로 태그 구조가 조금씩 다르다. extract_business_overview()는
    여러 회사 샘플로 실제 테스트하며 정규식을 보강해야 한다.
    지금 단계의 목표는 "완벽한 파싱"이 아니라 "원문을 놓치지 않고 모으는 것".
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

import requests

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"

# 정기공시 - 사업보고서
REPORT_TYPE_BUSINESS = "A001"

# 실제 헤딩은 ASCII 로마숫자(I, II, III...)를 쓰고, 본문 중 참조구문은
# 원형 숫자(Ⅰ, Ⅱ, Ⅲ...)를 인용부호로 감싸서 쓰는 경우가 많다 (실측 확인됨).
# "공백/문장 시작 바로 뒤에 오는 로마숫자"만 진짜 헤딩으로 인정하면
# 인용부호로 감싸인 참조구문(앞에 '가 붙음)은 lookbehind에서 자동 제외된다.
ROMAN_CHAR_CLASS = r"(?:[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])"
HEADING_GENERAL_RE = re.compile(rf"(?<!\S){ROMAN_CHAR_CLASS}\s*\.\s*[가-힣]")
TARGET_HEADING_RE = re.compile(rf"(?<!\S){ROMAN_CHAR_CLASS}\s*\.\s*사업의\s*내용")
# 헤딩 직후 인용부호가 오면 본문 참조일 가능성이 높아 추가 안전장치로 둔다
_REFERENCE_QUOTE_CHARS = ("'", "’", "‘", '"', "“", "”")


@dataclass
class ReportSearchResult:
    rcept_no: str
    corp_name: str
    report_nm: str
    rcept_dt: str  # YYYYMMDD


@dataclass
class CollectedReport:
    rcept_no: str
    raw_text: str
    business_overview: str | None
    parse_status: str  # 'ok' | 'partial' | 'failed'


def find_business_report_candidates(
    api_key: str, corp_code: str, year: int
) -> list[ReportSearchResult]:
    """
    해당 연도 사업보고서 후보들을 정렬해서 반환한다.
    "[첨부정정]" 등 정정공시는 본문 zip이 없는 경우가 있으므로 후순위로 두고,
    원본 사업보고서를 먼저 시도하도록 한다. 그래도 실패하면 호출 측에서
    다음 후보로 넘어갈 수 있다.
    """
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": f"{year}0101",
        "end_de": f"{year}1231",
        "pblntf_detail_ty": REPORT_TYPE_BUSINESS,
        "page_no": 1,
        "page_count": 10,
    }
    resp = requests.get(DART_LIST_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "000":
        return []  # '013' = 조회된 데이터 없음 등

    items = data.get("list", [])
    if not items:
        return []

    candidates = [
        ReportSearchResult(
            rcept_no=item["rcept_no"],
            corp_name=item.get("corp_name", ""),
            report_nm=item.get("report_nm", ""),
            rcept_dt=item.get("rcept_dt", ""),
        )
        for item in items
    ]

    def sort_key(r: ReportSearchResult) -> tuple[int, str]:
        is_correction = 1 if "정정" in r.report_nm else 0
        return (is_correction, r.rcept_dt)

    # 정정공시(is_correction=1)는 뒤로, 같은 그룹 내에서는 최신 날짜가 앞으로
    candidates.sort(key=sort_key)
    non_correction = [c for c in candidates if "정정" not in c.report_nm]
    correction = [c for c in candidates if "정정" in c.report_nm]
    non_correction.sort(key=lambda r: r.rcept_dt, reverse=True)
    correction.sort(key=lambda r: r.rcept_dt, reverse=True)
    return non_correction + correction


def find_latest_business_report(
    api_key: str, corp_code: str, year: int
) -> ReportSearchResult | None:
    """하위 호환용 단일 결과 버전 (가장 우선순위 높은 후보 1개)."""
    candidates = find_business_report_candidates(api_key, corp_code, year)
    return candidates[0] if candidates else None


def download_report_raw_text(api_key: str, rcept_no: str) -> str:
    """원문 zip을 받아서 안의 텍스트를 디코딩해 반환한다."""
    resp = requests.get(
        DART_DOCUMENT_URL,
        params={"crtfc_key": api_key, "rcept_no": rcept_no},
        timeout=60,
    )
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        # 보통 {rcept_no}.xml 하나만 들어있지만, 첨부파일이 여러 개인 경우도 있어
        # 가장 큰 파일(본문일 가능성 높음)을 사용
        biggest = max(names, key=lambda n: zf.getinfo(n).file_size)
        raw_bytes = zf.read(biggest)

    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 전부 실패하면 손실 감수하고 강제 디코딩
    return raw_bytes.decode("utf-8", errors="ignore")


def _strip_tags(html_like: str) -> str:
    """아주 단순한 태그 제거 (bs4 없이도 동작하도록).
    더 정교한 파싱이 필요하면 bs4.BeautifulSoup(html_like, 'html.parser')로 교체 가능."""
    text = re.sub(r"<[^>]+>", "\n", html_like)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def extract_business_overview(raw_text: str) -> tuple[str | None, str]:
    """
    raw_text에서 "사업의 내용" 섹션을 best-effort로 추출한다.

    DART 원문은 글자 단위로 태그가 쪼개져 있는 경우가 많아 줄 단위 매칭이
    불안정하다. 그래서 모든 공백/줄바꿈을 한 칸으로 정규화한 뒤 검색한다.

    같은 헤딩 텍스트가 목차/본문 중 참조구문/실제 섹션에서 여러 번 등장하므로:
      1) 헤딩 직후 인용부호가 오면 참조구문으로 보고 제외
      2) 남은 후보 중, 다음 헤딩까지의 본문이 가장 긴 것을 진짜 섹션으로 선택
         (목차/짧은 참조는 뒤에 내용이 거의 없음)

    반환: (추출된 텍스트 or None, parse_status)
    """
    plain = _strip_tags(raw_text)
    normalized = re.sub(r"\s+", " ", plain)

    all_headings = [m.start() for m in HEADING_GENERAL_RE.finditer(normalized)]

    candidates: list[int] = []
    for m in TARGET_HEADING_RE.finditer(normalized):
        following = normalized[m.end(): m.end() + 3]
        if any(q in following for q in _REFERENCE_QUOTE_CHARS):
            continue  # "...내용'을 참고하시기..." 같은 본문 중 참조 -> 제외
        candidates.append(m.start())

    if not candidates:
        return None, "failed"

    best_content = None
    best_length = -1
    for start in candidates:
        later = [h for h in all_headings if h > start]
        end = min(later) if later else len(normalized)
        content = normalized[start:end].strip()
        content = TARGET_HEADING_RE.sub("", content, count=1).strip()
        if len(content) > best_length:
            best_length = len(content)
            best_content = content

    if best_content is None:
        return None, "failed"
    if best_length < 200:
        return best_content[:50000], "partial"
    return best_content[:50000], "ok"


def collect_business_report(
    api_key: str, corp_code: str, stock_code: str, year: int
) -> CollectedReport | None:
    """
    후보 목록(원본 우선, 정정공시 후순위)을 순서대로 시도한다.
    zip 다운로드 실패 → 다음 후보로 넘어간다.
    다운로드 성공이지만 파싱 실패('failed') → 다음 후보도 시도한다.
    'ok' 결과를 얻으면 즉시 반환, 모든 후보 실패 시 partial > failed 순으로 최선 반환.
    모든 후보가 다운로드 자체에서 실패했을 때만 마지막 에러를 올린다.
    """
    candidates = find_business_report_candidates(api_key, corp_code, year)
    if not candidates:
        return None

    last_error: Exception | None = None
    best_result: CollectedReport | None = None

    for candidate in candidates:
        try:
            raw_text = download_report_raw_text(api_key, candidate.rcept_no)
        except Exception as e:  # noqa: BLE001 - 다음 후보로 넘어가기 위해 일단 잡아둔다
            last_error = e
            continue

        overview, status = extract_business_overview(raw_text)
        result = CollectedReport(
            rcept_no=candidate.rcept_no,
            raw_text=raw_text,
            business_overview=overview,
            parse_status=status,
        )
        if status == "ok":
            return result  # 완전 성공 → 즉시 반환
        # partial or failed: 더 나은 후보가 있을 수 있으므로 계속 시도
        if best_result is None or (
            status == "partial" and best_result.parse_status == "failed"
        ):
            best_result = result

    if best_result is not None:
        return best_result  # 다운로드는 성공했지만 파싱 최선 결과

    # 모든 후보가 다운로드 실패
    assert last_error is not None
    raise last_error


if __name__ == "__main__":
    import os

    api_key = os.environ["DART_API_KEY"]
    # 삼성전자로 동작 테스트
    result = collect_business_report(
        api_key, corp_code="00126380", stock_code="005930", year=2025
    )
    if result is None:
        print("보고서를 찾지 못함")
    else:
        print("rcept_no:", result.rcept_no)
        print("parse_status:", result.parse_status)
        print("미리보기:", (result.business_overview or "")[:300])