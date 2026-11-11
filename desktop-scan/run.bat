@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] Running selftest (4 rounds: negative / positive / degraded / login-code loop)...
python "%~dp0scan_helper.py" --selftest
echo.
echo [2/2] Starting hotkey mode.
echo       *** KEEP THIS WINDOW OPEN - do NOT close it ***
echo       Go to the login window, press Alt+Q to scan the QR code.
echo       Press Ctrl+C in this window to quit.
python "%~dp0scan_helper.py"
pause
