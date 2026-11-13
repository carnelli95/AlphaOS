-- 상장폐지 종목 메타데이터 (2026-11-13)
--
-- stocks 테이블은 현재 상장 종목 스냅샷이라 기간 중 폐지된 종목이 빠져 있다(생존 편향).
-- 이 테이블은 폐지 종목의 상장일·폐지일·사유를 담아 백테스트가 기준 시점에 상장돼 있던
-- 종목으로 유니버스를 구성할 수 있게 한다. 출처: FinanceDataReader KRX-DELISTING.
--
-- reason_category: '부실'(감사의견·계속성·시총 미달 등) / '비부실'(완전자회사화·합병·자진) / '기타'(SPAC 등)
-- 수익률 규칙(DECISIONS.md 2026-11-13): 종료가는 사유와 무관하게 마지막 거래일 종가.
-- 카테고리는 보고·민감도 분석용이다.

BEGIN;

CREATE TABLE IF NOT EXISTS delisted_stocks (
    code            VARCHAR(6)  NOT NULL,
    name            VARCHAR(100) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    corp_code       VARCHAR(8),
    listing_date    DATE,
    delisting_date  DATE        NOT NULL,
    reason          TEXT,
    reason_category VARCHAR(10) NOT NULL
        CHECK (reason_category IN ('부실', '비부실', '기타')),
    source          VARCHAR(40) NOT NULL DEFAULT 'FDR KRX-DELISTING',
    updated_at      TIMESTAMP   NOT NULL DEFAULT now(),
    PRIMARY KEY (code, delisting_date)
);

CREATE INDEX IF NOT EXISTS ix_delisted_stocks_delisting_date
    ON delisted_stocks (delisting_date);

-- 업종: 과거 Alpha Score의 업종 내 percentile 계산용. stocks.sector와 같은 규칙
-- (KSIC 업종코드 앞 2자리 → 업종명)으로 채운다. DART company.json의 induty_code 사용.
ALTER TABLE delisted_stocks ADD COLUMN IF NOT EXISTS induty_code VARCHAR(10);
ALTER TABLE delisted_stocks ADD COLUMN IF NOT EXISTS sector      VARCHAR(50);

COMMIT;
