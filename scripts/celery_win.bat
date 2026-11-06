@echo off
REM Windows + WSL Redis 용 Celery 실행기.
REM WSL localhost 포워딩이 안 되는 환경에서 WSL 내부 IP로 Redis에 직접 연결한다.
REM 사용: scripts\celery_win.bat worker --pool=solo
REM       scripts\celery_win.bat beat
cd /d %~dp0..
for /f "tokens=1" %%i in ('wsl hostname -I') do set WSL_IP=%%i
set REDIS_URL=redis://%WSL_IP%:6379/0
echo [celery_win] REDIS_URL=%REDIS_URL%
call venv\Scripts\activate
celery -A app.celery_app %* --loglevel=info
