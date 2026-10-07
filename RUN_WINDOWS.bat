@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================================
echo   DARK CRIMENET v4.5.0
echo  Windows launcher - FIR-first / 3-role / single localhost port
echo ==========================================================

echo.
echo [1/4] Detecting Python 3.11+...
set "PYEXE="

py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3.13"
if not defined PYEXE (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3.12"
)
if not defined PYEXE (
    py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3.11"
)
if not defined PYEXE (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo.
    echo ERROR: No supported Python 3.11+ installation was found.
    echo Run: py --list
    echo Run: python --version
    echo Run: where.exe python
    pause
    exit /b 1
)

echo Using Python: %PYEXE%
%PYEXE% --version

echo.
echo [2/4] Creating or validating the local virtual environment...
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Existing venv is invalid. Recreating it...
        rmdir /s /q "venv"
    )
)
if not exist "venv\Scripts\python.exe" (
    %PYEXE% -m venv venv
    if errorlevel 1 goto :venv_failed
)
venv\Scripts\python.exe -c "import sys; print('VENV PYTHON:', sys.executable); print('VENV VERSION:', sys.version.split()[0])"

echo.
echo [3/4] Installing / checking dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :deps_failed
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :deps_failed
venv\Scripts\python.exe -c "import sklearn,fastapi,uvicorn; print('scikit-learn:', sklearn.__version__)"
if errorlevel 1 goto :deps_failed

echo.
echo [4/4] Starting the application...
echo The release starts with a clean database. Random initial passwords are shown once in the API window.
start "DARK CRIMENET" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
timeout /t 4 /nobreak >nul
start "" http://localhost:8000

echo.
echo ==========================================================
echo App:   http://localhost:8000   (UI and API on one port)
echo Login: first start prints the initial account passwords in the server window (also saved to initial_credentials.txt). See DEMO_ACCOUNTS.md.
echo Forgot a password? Run: venv\Scripts\python.exe scripts\set_password.py USERNAME
echo ==========================================================
endlocal
exit /b 0

:venv_failed
echo.
echo ERROR: Could not create the local Python environment.
pause
exit /b 1

:deps_failed
echo.
echo ERROR: Dependency installation failed. Check the Python version and network access.
pause
exit /b 1
