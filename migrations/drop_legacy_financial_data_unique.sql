-- financial_data 예전 유니크 제약 제거 (2026-11-12)
--
-- add_fs_type_to_financial_data.sql은 예전 제약을 'financial_data_code_year_account_nm_key'
-- 이름으로 DROP하려 했으나 실제 이름은 'uq_financial_data'여서 제거되지 않았다.
-- 그 결과 (code, year, account_nm) 제약이 남아 같은 계정의 CFS/OFS가 공존할 수 없었고,
-- ON CONFLICT ON CONSTRAINT uq_financial_data 업서트가 CFS 값을 OFS로 덮어썼다
-- (2025년 자본총계 CFS 보유 종목 2/833).
--
-- 이후 업서트는 uq_financial_data_fs (code, year, fs_type, account_nm)만 사용한다.
-- 데이터는 바꾸지 않는다. 덮어써진 연도는 collect_financial_data.py --refresh로 재수집.

BEGIN;

ALTER TABLE financial_data
    DROP CONSTRAINT IF EXISTS uq_financial_data;

COMMIT;
