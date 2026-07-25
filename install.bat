@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo(
echo  ===============================================
echo   J.A.R.V.I.S - one-time setup
echo  ===============================================
echo(
echo  This installs everything JARVIS needs into a local .venv folder
echo  and puts a shortcut on your Desktop. Your system Python is left alone.
echo(
echo  Downloads about 500 MB (Chromium + a small speech model). Takes a few
echo  minutes. Safe to run again later - it skips whatever is already done.
echo(
pause

REM ---------------------------------------------------------------- Python
echo(
echo  [1/6] Looking for Python 3.12...
REM JARVIS needs Python 3.12 SPECIFICALLY. Python 3.13 REMOVED the standard
REM library `audioop` and `aifc` modules (PEP 594). SpeechRecognition imports
REM both at module level, and jarvis\voice\wake.py imports audioop to resample
REM the mic. pip installs cleanly on 3.13 (PyAudio ships a cp313 wheel), so
REM choosing the wrong interpreter here does NOT fail the install - it prints
REM "Done." and then silently kills EVERY voice feature at first use, on a
REM voice-driven agent. A bare `py -3` selects the NEWEST interpreter, which is
REM exactly the wrong one; that was the bug this gate exists to prevent.
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if defined PY goto :pyok
python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto :pyok

echo        Python 3.12 not found ^(3.13 and newer will NOT work^).
echo        Trying to install Python 3.12 with winget...
winget --version >nul 2>&1
if errorlevel 1 goto :nopython
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if defined PY goto :pyok
echo(
echo  ERROR: Python 3.12 was installed but this window cannot see it yet.
echo         Close this window, open a NEW one, and run install.bat again.
echo(
pause
exit /b 1

:nopython
echo(
echo  ERROR: Python 3.12 is not installed and winget is unavailable.
echo         Install Python 3.12 from https://www.python.org/downloads/
echo         Tick "Add python.exe to PATH" during setup, then re-run this.
echo         NOTE: JARVIS needs 3.12 - Python 3.13 and newer are NOT supported
echo         (they removed the audioop module that all voice input needs).
echo(
pause
exit /b 1

:pyok
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo        Using %%v

REM ---------------------------------------------------------------- venv
echo(
echo  [2/6] Creating the local environment (.venv)...
if exist ".venv\Scripts\python.exe" (
    echo        Already there - skipping.
) else (
    %PY% -m venv ".venv"
    if errorlevel 1 goto :failed
)
set "VPY=%CD%\.venv\Scripts\python.exe"
set "VPYW=%CD%\.venv\Scripts\pythonw.exe"

REM ---------------------------------------------------------------- deps
echo(
echo  [3/6] Installing dependencies (this is the slow part)...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

REM pywin32's COM registration does NOT happen automatically inside a venv.
REM win32com powers DPAPI encryption, the Recycle Bin and shortcuts, so skipping
REM this makes JARVIS fail later in ways that look unrelated to setup.
if exist ".venv\Scripts\pywin32_postinstall.py" (
    echo        Registering pywin32 COM components...
    "%VPY%" ".venv\Scripts\pywin32_postinstall.py" -install >nul 2>&1
)

REM ---------------------------------------------------------------- browser
echo(
echo  [4/6] Downloading Chromium for web automation...
"%VPY%" -m playwright install chromium
if errorlevel 1 goto :failed

REM ---------------------------------------------------------------- models
echo(
echo  [5/6] Downloading the local models (memory + wake word)...
"%VPY%" -m jarvis.core.embedder --setup
if errorlevel 1 goto :failed
REM openwakeword ships WITHOUT its .onnx model files; without this the
REM "hey jarvis" wake-word toggle silently fails to turn on. Non-fatal:
REM wake word is optional, so a failure here doesn't sink the whole install.
"%VPY%" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
if errorlevel 1 (
    echo        Wake-word model download failed - everything else still works;
    echo        wake word will be unavailable until you re-run install.bat.
)

REM ---------------------------------------------------------------- shortcut
echo(
echo  [6/6] Creating a Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'J.A.R.V.I.S.lnk'));" ^
  "$s.TargetPath='%VPYW%'; $s.Arguments='tray_start.pyw'; $s.WorkingDirectory='%CD%';" ^
  "$s.Description='Start JARVIS'; $s.Save()"
if errorlevel 1 (
    echo        Could not create the shortcut - you can still start JARVIS with:
    echo        "%VPYW%" tray_start.pyw
)

REM ---------------------------------------------------------------- done
echo(
echo  ===============================================
echo   Done.
echo  ===============================================
echo(
echo  Double-click "J.A.R.V.I.S" on your Desktop to start it.
echo  A tray icon appears; the HUD opens at http://127.0.0.1:8000
echo(
echo  On first launch JARVIS will ask for a free Gemini API key
echo  (get one at https://aistudio.google.com/apikey) - paste it
echo  into the setup panel and you are running.
echo(
echo  NOTE: JARVIS can run shell commands and delete files on this PC.
echo        It asks before anything irreversible - read those prompts.
echo        See README.md for what it can do and how to switch parts off.
echo(
choice /c YN /n /m "  Start JARVIS now? [Y/N] "
if errorlevel 2 goto :end
start "" "%VPYW%" tray_start.pyw
goto :end

:failed
echo(
echo  ===============================================
echo   SETUP FAILED - see the error above.
echo  ===============================================
echo(
echo  Nothing was half-configured on purpose: fix the error and run
echo  install.bat again. It skips the steps that already succeeded.
echo(
pause
exit /b 1

:end
echo(
pause
endlocal
REM `choice` sets errorlevel 1/2 for Y/N; without this a successful install
REM that simply declined the launch prompt would exit non-zero and look failed.
exit /b 0
