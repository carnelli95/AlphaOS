-- stocks 테이블에 업종 컬럼 추가
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS induty_code VARCHAR(10);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sector VARCHAR(50);
