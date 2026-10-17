@echo off
chcp 65001 >nul
title glmate - QQ AI Bot
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
%PY% zglmbot.py
if errorlevel 1 (
    echo.
    echo [ERROR] glmate exited with error. Check zglmbot.log in state/.
    pause
)
