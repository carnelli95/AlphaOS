-- 밸류에이션 판단을 카테고리별(valuation/growth/profitability/stability)로
-- 분리해서 저장하기 위한 컬럼 추가. 기존 valuation_opinion(TEXT)은
-- 호환을 위해 그대로 유지 (섹션들을 이어붙인 폴백 텍스트가 들어감).

ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS valuation_sections JSONB;
