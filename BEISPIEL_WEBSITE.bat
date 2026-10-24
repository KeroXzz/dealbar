@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem ---- Python suchen ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3" && goto found
python --version >nul 2>nul && set "PY=python" && goto found
if exist "%USERPROFILE%\anaconda3\python.exe" set "PY="%USERPROFILE%\anaconda3\python.exe"" && goto found
if exist "%USERPROFILE%\miniconda3\python.exe" set "PY="%USERPROFILE%\miniconda3\python.exe"" && goto found
if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY="%LOCALAPPDATA%\anaconda3\python.exe"" && goto found
echo.
echo Python wurde nicht gefunden.
echo Bitte Python von https://www.python.org/downloads/ installieren
echo und dabei "Add python.exe to PATH" anhaken. Danach erneut starten.
echo.
pause
exit /b 1

:found
rem ---- Pakete beim ersten Start installieren ----
%PY% -c "import PIL, numpy, openpyxl, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
  echo Erstmalige Einrichtung: benoetigte Pakete werden installiert ...
  %PY% -m pip install --user --quiet pillow numpy openpyxl imageio-ffmpeg
  if errorlevel 1 (
    echo Installation fehlgeschlagen. Bitte Internetverbindung pruefen.
    pause
    exit /b 1
  )
)

%PY% programm\beispiel.py
if not errorlevel 1 start "" "website_beispiel\index.html"
echo.
pause
