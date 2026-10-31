@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 (
    echo Could not create .venv. Install Python 3.11 or newer and try again.
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed.
    exit /b 1
  )
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv\Scripts\pythonw.exe is missing.
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0keyware.pyw" %*
exit /b 0
