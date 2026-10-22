@echo off
setlocal
cd /d "%~dp0"
title ReviewLab

echo ReviewLab - local setup and launch
set "REVIEWLAB_PYTHON="
py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
if not errorlevel 1 set "REVIEWLAB_PYTHON=py -3.12"
if defined REVIEWLAB_PYTHON goto python_ready
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" goto local_python

echo Python 3.12 is required. Installing it with the official winget package...
where winget >nul 2>&1
if errorlevel 1 goto missing_python
winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto missing_python
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" goto local_python
py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
if errorlevel 1 goto missing_python
set "REVIEWLAB_PYTHON=py -3.12"
goto python_ready

:local_python
set "REVIEWLAB_PYTHON="%LocalAppData%\Programs\Python\Python312\python.exe""

:python_ready
if not exist "app.py" goto missing_files
if not exist "requirements.txt" goto missing_files
if exist ".venv\Scripts\python.exe" goto environment_ready
%REVIEWLAB_PYTHON% -m venv .venv
if errorlevel 1 goto failed

:environment_ready
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed

echo.
echo Starting ReviewLab. Keep this window open.
echo Open http://127.0.0.1:8501 in your browser after the URL appears below.
echo Press Ctrl+C to stop the app.
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo.
echo Python installation could not finish automatically.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Then close this window and double-click Start_ReviewLab.cmd again.
pause
exit /b 1

:missing_files
echo.
echo Extract ALL files from the ZIP first. Do not run this file inside the ZIP.
pause
exit /b 1

:failed
echo.
echo Setup or startup failed. Please share the error text shown above.
echo Do not change firewall or proxy settings to fix a Python setup error.
pause
exit /b 1
