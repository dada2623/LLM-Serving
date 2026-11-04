@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\containers.ps1" -Action stop %*
exit /b %errorlevel%
