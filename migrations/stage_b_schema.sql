-- Stage B v1: 동종 업종 Peer Benchmark 섹션 컬럼 추가
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS stage_b_sections JSONB;
