@echo off
:: =============================================
:: Safe per-user .oxc icon installer (no admin)
:: =============================================

set "BATCH_PATH=%~dp0"
set "BATCH_PATH=%BATCH_PATH:~0,-1%"
set "ICON_FILE=%BATCH_PATH%\Lexer\static\oxc.ico"

:: Escape backslashes for .reg file BEFORE the block
set "ICON_ESC=%ICON_FILE:\=\\%"

(
echo Windows Registry Editor Version 5.00
echo.
echo [HKEY_CURRENT_USER\Software\Classes\.oxc]
echo @="OxclangFile"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\OxclangFile]
echo @="OxC Lang Source File"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\OxclangFile\DefaultIcon]
echo @="%ICON_ESC%"
) > "%BATCH_PATH%\oxc_icon.reg"

reg import "%BATCH_PATH%\oxc_icon.reg"

echo Done!
pause