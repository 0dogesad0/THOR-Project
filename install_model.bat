@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  Thor - modelo com VISAO + tool calling (instalacao 1 vez)
rem ============================================================
rem  Descarrega o MiMo-V2.6-Distill-Qwen-9B (GGUF Q4_K_M) e o
rem  seu projetor multimodal (mmproj). Precisa de internet SO UMA
rem  VEZ; depois disto o chat corre 100%% offline.
rem
rem  Ficheiros:
rem    model\model.gguf   5.84 GB  (linguagem + codigo + tool calling)
rem    model\mmproj.gguf  0.92 GB  (visor: enxerga imagens)
rem ============================================================

set REPO=https://huggingface.co/bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF/resolve/main
set MFILE=MiMo-V2.6-Distill-Qwen-9B-Q4_K_M.gguf
set PFILE=mmproj-MiMo-V2.6-Distill-Qwen-9B-f16.gguf

if not exist "model" mkdir model

echo ============================================
echo  Thor - instalar modelo com visao
echo ============================================
echo.
echo Precisa de internet SO UMA VEZ (~6.8 GB no total).
echo Depois disto, tudo corre local - sem rede nenhuma.
rem --- acha o curl (vem com o Windows 10/11) ---
where curl >nul 2>&1
if errorlevel 1 (
  echo [erro] curl nao encontrado - precisa do Windows 10 ou superior.
  pause
  exit /b 1
)

if not exist "model\model.gguf" (
  echo [1/2] A descarregar o modelo MiMo-V2.6-9B ^(5.84 GB^)...
  curl -L --retry 5 --retry-delay 3 --continue-at - --progress-bar ^
       -o "model\model.gguf.part" "%REPO%/%MFILE%"
  if errorlevel 1 (
    echo [erro] download do modelo falhou - tenta de novo ^(o .part retoma de onde parou^).
    del /Q "model\model.gguf.part" 2>nul
    pause
    exit /b 1
  )
  move /Y "model\model.gguf.part" "model\model.gguf" >nul
) else (
  echo [1/2] model\model.gguf ja existe - a saltar.
)

if not exist "model\mmproj.gguf" (
  echo [2/2] A descarregar o projetor de visao ^(0.92 GB^)...
  curl -L --retry 5 --retry-delay 3 --continue-at - --progress-bar ^
       -o "model\mmproj.gguf.part" "%REPO%/%PFILE%"
  if errorlevel 1 (
    echo [erro] download do mmproj falhou - tenta de novo.
    del /Q "model\mmproj.gguf.part" 2>nul
    pause
    exit /b 1
  )
  move /Y "model\mmproj.gguf.part" "model\mmproj.gguf" >nul
) else (
  echo [2/2] model\mmproj.gguf ja existe - a saltar.
)

echo.
echo ============================================
echo  Feito! Modelo com visao instalado.
echo.
echo  Agora abre start.bat - o chat passa a ver
echo  imagens e continua 100%% offline.
echo ============================================
pause
goto fim

:fim
endlocal
