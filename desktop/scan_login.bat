@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "OPENBLAS_NUM_THREADS=1"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0..\qoder\wx_quick_login.py" --site screen
if errorlevel 1 pause
