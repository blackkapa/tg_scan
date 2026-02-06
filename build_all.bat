@echo off
chcp 65001 >nul
REM Один exe: бот + сверка AD в 01:00 + реестр в 07:00
REM Запуск в папке проекта: build_all.bat

echo ============================================
echo   Сборка tg_scan.exe (бот + сверка + реестр)
echo ============================================
echo.

pip install pyinstaller -q
pyinstaller --noconfirm tg_scan.spec
if %ERRORLEVEL% NEQ 0 (exit /b 1)

echo.
echo Готово: dist\tg_scan.exe
echo Рядом с exe: config.ini, config.example.ini, data\, ad_export.json
echo По умолчанию — бот; сверка AD каждый день 01:00, реестр 07:00.
echo Только реестр: tg_scan.exe --registry
