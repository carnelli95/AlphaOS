-- Stage C: 성장동력/리스크/경제적 해자 섹션 컬럼 추가
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS stage_c_sections JSONB;
