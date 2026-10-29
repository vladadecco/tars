@echo off
setlocal
chcp 65001 >nul
title Instalare TARS
cd /d "%~dp0"

echo.
echo   =============================================
echo              TARS - instalare
echo   =============================================
echo.

rem ---- 1. Python 3.12 ----
set "PY="
py -3.12 -c "print(1)" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  echo [1/4] Nu am gasit Python 3.12. Il instalez acum ^(dureaza un minut^)...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  py -3.12 -c "print(1)" >nul 2>&1 && set "PY=py -3.12"
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
)
if not defined PY (
  echo.
  echo   Nu am reusit sa instalez Python automat.
  echo   Instaleaza Python 3.12 de pe https://www.python.org/downloads/ ^(bifeaza "Add to PATH"^)
  echo   apoi porneste din nou acest fisier.
  pause
  exit /b 1
)
echo [1/4] Python gasit.

rem ---- 2. Mediu separat ----
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creez mediul pentru TARS...
  %PY% -m venv .venv || (echo Eroare la crearea mediului. & pause & exit /b 1)
) else (
  echo [2/4] Mediul exista deja.
)

rem ---- 3. Pachete ----
echo [3/4] Instalez pachetele ^(prima data dureaza cateva minute^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   Instalarea pachetelor a dat eroare. Trimite-mi ce scrie mai sus.
  pause
  exit /b 1
)

rem ---- 4. Modelul de recunoastere vocala + scurtaturi ----
echo [4/4] Descarc modelul de recunoastere vocala ^(~480 MB, o singura data^)...
".venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('Model gata.')"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell);" ^
  "$d=[Environment]::GetFolderPath('Desktop');" ^
  "$l=$s.CreateShortcut((Join-Path $d 'TARS.lnk'));" ^
  "$l.TargetPath='%~dp0.venv\Scripts\pythonw.exe';" ^
  "$l.Arguments='\"%~dp0main.py\"';" ^
  "$l.WorkingDirectory='%~dp0';" ^
  "$l.IconLocation='%~dp0ui\tars.ico';" ^
  "$l.Save()"

echo.
set /p AUTO="Vrei ca TARS sa porneasca automat cand pornesti PC-ul? (d/n): "
if /i "%AUTO%"=="d" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s=(New-Object -ComObject WScript.Shell);" ^
    "$d=[Environment]::GetFolderPath('Startup');" ^
    "$l=$s.CreateShortcut((Join-Path $d 'TARS.lnk'));" ^
    "$l.TargetPath='%~dp0.venv\Scripts\pythonw.exe';" ^
    "$l.Arguments='\"%~dp0main.py\"';" ^
    "$l.WorkingDirectory='%~dp0';" ^
    "$l.IconLocation='%~dp0ui\tars.ico';" ^
    "$l.Save()"
  echo   Pornire automata activata.
)

echo.
echo   Gata! Ai pe Desktop scurtatura "TARS".
echo   La prima pornire se deschid Setarile: pune cheia Claude API.
echo.
set /p RUN="Pornesc TARS acum? (d/n): "
if /i "%RUN%"=="d" start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
endlocal
