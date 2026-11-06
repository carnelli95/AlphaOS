# macOS 개발 환경 세팅

Windows PC에서 작업하던 AlphaOS를 맥북에서 이어서 작업하기 위한 절차.

## 1. 코드 외에 따로 옮겨야 하는 것 (git에 없음)

| 항목 | 이유 | 옮기는 방법 |
|---|---|---|
| `.env` | API 키·DB 비밀번호 | 직접 복사 (`.env.example` 참고) |
| `frontend/.env.local` | 결제 클라이언트 키 | 직접 복사 |
| DB 덤프 (`quant_screener.dump`) | 데이터는 로컬 Postgres에만 있음 | USB / 클라우드 드라이브 |
| `qa_snapshots/` | IC 실험 t0 고정 스냅샷 (재생성 불가) | 같이 복사 |
| `qa_results.csv` | QA 수동 채점 결과 | 같이 복사 |

> 키·덤프 파일은 git이나 공개 링크로 옮기지 말 것.

## 2. 필수 도구 설치

```bash
brew install python@3.12 postgresql@18 redis node
brew services start postgresql@18
brew services start redis
```

## 3. 코드 + Python 환경

```bash
git clone https://github.com/carnelli95/AlphaOS.git
cd AlphaOS
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 이후 실제 값으로 채우기 (또는 PC의 .env 그대로 복사)
```

## 4. DB 복원

PC에서 덤프 (PowerShell, 비밀번호 입력 필요):

```powershell
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -U postgres -h localhost -Fc -f quant_screener.dump quant_screener
```

맥에서 복원:

```bash
createdb quant_screener
pg_restore --no-owner --no-acl -d quant_screener quant_screener.dump
```

맥 Homebrew Postgres의 기본 사용자는 맥 계정명(비밀번호 없음)이므로
`.env`의 `DATABASE_URL`을 맞게 수정:

```
DATABASE_URL=postgresql+psycopg2://<맥계정명>@localhost:5432/quant_screener
```

확인:

```bash
psql quant_screener -c "select count(*) from ai_reports;"
```

## 5. 실행

```bash
# API
uvicorn app.main:app --reload --port 8000

# Celery (별도 터미널 2개)
celery -A app.celery_app worker --loglevel=info
celery -A app.celery_app beat --loglevel=info

# 프론트엔드
cd frontend && npm install && npm run dev
```

## 6. 주의

- **Celery Beat는 한 대에서만 실행.** PC와 맥이 각자 DB를 갖고 있어 양쪽에서 돌리면
  데이터가 갈라진다. 작업 기기를 옮길 때마다 DB 덤프도 같이 옮기거나, 한쪽을 주 운영 기기로 정할 것.
- `celerybeat-schedule.*`, `logs/`, `qa_reports/`는 기기별 런타임 파일이라 옮길 필요 없음.
- 이후 PC ↔ 맥 이동 시: 코드는 `git push` / `git pull`, 데이터는 DB 덤프 재복원.
