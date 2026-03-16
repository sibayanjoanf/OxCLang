@echo off
taskkill /f /im explorer.exe
timeout /t 2 /nobreak >nul
start explorer.exe
exit