@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set BUN_VER=1.4.4
set SDK_VER=2.2.6

echo ============================================
echo  Thor - motor OpenCode (instalacao 1 vez)
echo ============================================
echo.
echo Precisa de internet SO UMA VEZ. Depois disto, o
echo OpenCode corre 100%% local (sem rede nenhuma).

if exist "bun.exe" if exist "node_modules\" goto ja

if not exist "bun.exe" (
  echo.
  echo [1/2] A descarregar o Bun %BUN_VER% (~110 MB)...
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/oven-sh/bun/releases/download/bun-v%BUN_VER%/bun-windows-x64.zip' -OutFile "$env:TEMP\bun-thor.zip" -UseBasicParsing; Expand-Archive -Path "$env:TEMP\bun-thor.zip" -DestinationPath "$env:TEMP\bun-thor" -Force; Move-Item -Path "$env:TEMP\bun-thor\bun-windows-x64\bun.exe" -Destination "%~dp0bun.exe" -Force"
  if not exist "bun.exe" (
    echo [erro] nao foi possivel obter o bun.exe - verifica a internet.
    pause
    exit /b 1
  )
)

if not exist "node_modules\" (
  echo [2/2] A instalar @opencode/sdk + @opencode/ai...
  ".\bun.exe" add "@opencode/sdk@%SDK_VER%" "@opencode/ai@%SDK_VER%"
  if errorlevel 1 (
    echo [erro] "bun add" falhou - verifica a internet e tenta de novo.
    pause
    exit /b 1
  )
)

echo.
echo Feito! Abre start.bat e escolhe "Agente: OpenCode" no topo do chat.
pause
goto fim

:ja
echo Ja instalado (bun.exe + node_modules presentes).
pause

:fim
endlocal
