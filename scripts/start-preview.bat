@echo off
setlocal
set "QIMING_LIVE_PREVIEW=true"
call "%~dp0start.bat"
exit /b %errorlevel%
