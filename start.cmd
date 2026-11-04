@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\containers.ps1" -Action start %*
exit /b %errorlevel%
