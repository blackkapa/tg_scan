@echo off
chcp 65001 >nul
REM Один exe в dist\tg_scan.exe. Рядом: config.ini, data\
REM Сборка: build_all.bat (закройте старый exe перед сборкой)

echo ============================================
echo   Сборка одного exe: tg_scan.exe
echo ============================================
echo.

if exist .venv\Scripts\pyinstaller.exe (
    .venv\Scripts\pyinstaller.exe --noconfirm tg_scan.spec
) else (
    pip install pyinstaller -q
    pyinstaller --noconfirm tg_scan.spec
)
if %ERRORLEVEL% NEQ 0 (exit /b 1)

if exist config.example.ini copy /Y config.example.ini dist\
if not exist dist\data mkdir dist\data

echo.
echo Готово: dist\tg_scan.exe (один файл)
echo Рядом с exe: config.ini, папка data\
echo Режим только реестр: tg_scan.exe --registry [путь]
pause
