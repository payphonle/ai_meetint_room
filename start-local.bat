@echo off
cd /d %~dp0
title AI Group Chat
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.main
) else (
    python -m app.main
)
pause
