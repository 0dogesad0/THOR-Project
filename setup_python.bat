@echo off
rem ============================================================
rem  setup_python.bat - setup UNICO (precisa de internet 1 vez)
rem  Completa o Python do runtime com pip + pandas + matplotlib
rem  para os exercicios gerados pelo Thor correrem offline.
rem  Depois disto, nada mais precisa de internet.
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python.exe"
set "INST=%TEMP%\python-3.12.10-amd64.exe"

if not exist "%PY%" (
  echo [erro] falta runtime\python.exe - extrai primeiro a release v1.0.
  exit /b 1
)

"%PY%" -c "import pandas, matplotlib" 2>nul
if not errorlevel 1 (
  echo [ok] pandas e matplotlib ja disponiveis - nada a fazer.
  exit /b 0
)

echo A descarregar Python 3.12.10 (so nesta vez, precisa de internet)...
curl -sL -o "%INST%" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
if not exist "%INST%" (
  echo [erro] sem internet - corre este script uma vez com internet.
  exit /b 1
)

echo A instalar o Python completo por cima do runtime...
"%INST%" /quiet InstallAllUsers=0 TargetDir="%ROOT%runtime" Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0 PrependPath=0 Include_launcher=0 Translations=0
del "%INST%" >nul 2>&1
rem o Python "embutido" usa ficheiros de isolamento - remover para o modo completo
del "%ROOT%runtime\python312._pth" >nul 2>&1
del "%ROOT%runtime\python312.zip" >nul 2>&1

"%PY%" -m ensurepip --upgrade >nul 2>&1
echo A instalar pandas + matplotlib...
"%PY%" -m pip install --no-warn-script-location --disable-pip-version-check pandas matplotlib
if errorlevel 1 (
  echo [erro] falhou o pip install.
  exit /b 1
)

"%PY%" -c "import pandas, matplotlib, numpy; print('[ok] runtime pronto: pandas %s | matplotlib %s' % (pandas.__version__, matplotlib.__version__))"
exit /b 0
