@echo off
setlocal EnableExtensions
REM Data Agent local launcher. Do not put credentials in this script.
cd /d "%~dp0.."

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher ^(py^) was not found. Install Python 3.10+ and try again.
  exit /b 1
)

if not exist ".env" (
  echo [INFO] No .env file found. Copying .env.example to .env...
  copy /Y ".env.example" ".env" >nul
  echo [INFO] Configure your own provider Key in .env or after startup in the web UI.
)

py -3 -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing runtime dependencies from requirements.txt...
  py -3 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    exit /b 1
  )
)

set DATA_AGENT_HOST=127.0.0.1
if not "%DATA_AGENT_PORT%"=="" goto start
set DATA_AGENT_PORT=8000

:start
echo [INFO] Starting Data Agent at http://%DATA_AGENT_HOST%:%DATA_AGENT_PORT%/
echo [INFO] First run: open the page and click "模型/API 设置" to enter your own provider Key.
echo [INFO] Your .env and local provider settings are excluded from Git.
py -3 -m uvicorn src.server:app --host %DATA_AGENT_HOST% --port %DATA_AGENT_PORT%
endlocal
