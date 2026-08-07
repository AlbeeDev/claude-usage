@echo off
REM Run the checker without having to know what Python is called here.
REM py comes first on purpose: on a clean Windows machine "python" is often the
REM Microsoft Store stub, which opens the Store instead of running anything.

py -3 --version >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0usage.py" %*
    exit /b %errorlevel%
)

python --version >nul 2>&1
if %errorlevel%==0 (
    python "%~dp0usage.py" %*
    exit /b %errorlevel%
)

echo No Python found. Install Python 3 from python.org, then run this again. 1>&2
exit /b 1
