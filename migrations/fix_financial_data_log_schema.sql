CREATE TABLE IF NOT EXISTS financial_data_fix_log (
    code         VARCHAR(10) NOT NULL,
    year         VARCHAR(10) NOT NULL,
    status       VARCHAR(20) NOT NULL,  -- 'success' | 'error'
    message      TEXT,
    attempted_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (code, year)
);
