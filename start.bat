@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set WEBPORT=8090
set MODEL=model\model.gguf
set PY=runtime\python.exe
set PYTHONIOENCODING=utf-8

echo ============================================
echo  Thor - Chat Web - 100%% offline
echo ============================================
echo.
echo O modelo e gerido pelo proprio chat (modo Auto):
echo   GPU dedicada se existir, senao CPU. Mudas no topo do chat.
echo.

if not exist "%MODEL%" (
  echo [erro] Modelo nao encontrado: %MODEL%
  pause
  exit /b 1
)
if not exist "runtime\llama-server.exe" (
  if not exist "runtime-gpu\llama-server.exe" (
    echo [erro] llama-server nao encontrado (nem em runtime nem em runtime-gpu)
    pause
    exit /b 1
  )
)
if not exist "%PY%" (
  echo [erro] Python portatil nao encontrado: %PY%
  pause
  exit /b 1
)

rem --- chat web: se ja esta a servir, so abre o browser ---
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%WEBPORT%/' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto web_ok

echo A abrir o chat em http://127.0.0.1:%WEBPORT%/ ...
echo (o modelo carrega em background no motor escolhido)
"%PY%" -u server.py
goto fim

:web_ok
echo O chat web ja esta ativo em http://127.0.0.1:%WEBPORT%/ - abre o browser.
start "" "http://127.0.0.1:%WEBPORT%/"
echo.
echo Os servidores ja estao a correr; podes fechar esta janela.
timeout /t 5 /nobreak >nul

:fim
endlocal
