@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set PORT=8080
set MODEL=model\model.gguf
set LLAMA=runtime\llama-server.exe
set PY=runtime\python.exe
set PYTHONIOENCODING=utf-8

echo ============================================
echo  Thor Agent CLI - 100%% offline (terminal)
echo ============================================
echo.

if not exist "%MODEL%" (
  echo [erro] Modelo nao encontrado: %MODEL%
  pause
  exit /b 1
)
if not exist "%LLAMA%" (
  echo [erro] llama-server nao encontrado: %LLAMA%
  pause
  exit /b 1
)

powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto llama_ok

echo A arrancar llama-server no puerto %PORT% (16 threads)...
start "Thor llama-server" /min "%LLAMA%" -m "%MODEL%" --port %PORT% --host 127.0.0.1 -c 8192 --no-warmup -t 16
set SPAWNED_LLAMA=1

echo A aguardar o modelo carregar...
:waitloop
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 goto waitloop
:llama_ok

"%PY%" -u agent.py

:fim
echo.
if "%SPAWNED_LLAMA%"=="1" (
  echo A encerrar o llama-server que este arrancou...
  powershell -NoProfile -Command "Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force" >nul 2>&1
)
endlocal
