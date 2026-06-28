-- Stage A: AI 리포트 (기업개요 + 밸류에이션) 저장 테이블

CREATE TABLE IF NOT EXISTS ai_reports (
    id                   SERIAL PRIMARY KEY,
    stock_code           VARCHAR(10) NOT NULL UNIQUE REFERENCES stocks(code),
    overview             TEXT,
    valuation_opinion    TEXT,
    metrics_snapshot     JSONB,   -- 생성 시점의 PER/PBR/ROE 등 원본 수치 (재현/검증용)
    data_basis           JSONB,   -- 섹션별 근거 태깅 (사업보고서 기반 vs LLM 추론)
    generated_at         TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_reports_stock_code ON ai_reports (stock_code);
