@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"

echo ==========================================
echo    zglmbot - One Click Start
echo ==========================================

echo.
echo [1/2] SnowLuma (QQ online) ...
call :port_up 5099
if %errorlevel% equ 0 (
    echo        already running, skip
) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%SnowLuma\node.exe' -ArgumentList 'index.mjs' -WorkingDirectory '%ROOT%SnowLuma' -WindowStyle Hidden"
    call :wait_port 5099 60
    if errorlevel 1 (echo        WARN: not ready in 60s) else (echo        OK port 5099, hidden window)
)

echo [2/2] zglmbot (AI) ...
call :port_check_ws 3001
if %errorlevel% equ 0 (
    echo        already running, skip
    echo.
    echo Done. SnowLuma runs hidden (no window); zglmbot is the single console window.
    >nul ping -n 3 127.0.0.1
    exit /b 0
)

echo        starting zglmbot (this window becomes its console)...
start "zglmbot" /d "%ROOT%zglmbot" "%PY%" "%ROOT%zglmbot\zglmbot.py"
call :wait_port 5099 15
if errorlevel 1 (echo        WARN: SnowLuma not ready) else (echo        OK SnowLuma 5099)
echo.
echo zglmbot window is open; keep it running.
echo Press Ctrl+C in the zglmbot window to stop.
>nul ping -n 5 127.0.0.1
exit /b 0

:port_up
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%1);exit 0}catch{exit 1}"
exit /b %errorlevel%

:port_check_ws
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue; if($c){exit 0}else{exit 1}"
exit /b %errorlevel%

:wait_port
set /a "_n=0"
:wait_loop
call :port_up %1
if %errorlevel% equ 0 exit /b 0
set /a "_n+=1"
if %_n% geq %2 exit /b 1
timeout /t 1 /nobreak >nul
goto wait_loop
