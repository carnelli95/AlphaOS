-- financial_data CFS/OFS 구조 안정화
-- 기존: UNIQUE(code, year, account_nm) — CFS/OFS 구분 없음
-- 이후: UNIQUE(code, year, fs_type, account_nm) — 둘 다 저장, 쿼리 시 CFS 우선

BEGIN;

-- 1. fs_type 컬럼 추가 (기존 행은 'CFS'로 간주 — 수집 당시 CFS 우선 로직 적용됐으므로)
ALTER TABLE financial_data
    ADD COLUMN IF NOT EXISTS fs_type VARCHAR(3) NOT NULL DEFAULT 'CFS';

-- 2. 기존 유니크 제약 제거 (PostgreSQL 자동 생성 이름)
ALTER TABLE financial_data
    DROP CONSTRAINT IF EXISTS financial_data_code_year_account_nm_key;

-- 3. 새 유니크 제약 추가
ALTER TABLE financial_data
    ADD CONSTRAINT uq_financial_data_fs
    UNIQUE (code, year, fs_type, account_nm);

COMMIT;
