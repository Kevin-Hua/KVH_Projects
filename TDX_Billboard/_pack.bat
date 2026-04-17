@echo off
setlocal
cd /d "%~dp0"

set APP=TDX_Billboard
set KEY=%~dp0TDX_Kindway.key

echo ============================================================
echo  Packing %APP%
echo ============================================================


:: ── Regenerate version_info.txt from source version string ────
echo [1/3] Generating version_info.txt ...
python ..\\_gen_version.py %APP%
if errorlevel 1 ( pause & exit /b 1 )

:: ── Regenerate spec ───────────────────────────────────────────
echo [2/3] Generating TDX_Billboard.spec ...
pyi-makespec TDX_Billboard.py ^
    --onefile --noconsole ^
    --name %APP% ^
    --add-data "TDX_Billboard.key;." ^
    --version-file version_info.txt
if errorlevel 1 ( pause & exit /b 1 )

:: ── Build EXE ─────────────────────────────────────────────────
echo [3/3] Building EXE ...
pyinstaller %APP%.spec --clean
if errorlevel 1 ( pause & exit /b 1 )

echo.
echo ============================================================
echo  Done: dist\%APP%.exe
echo ============================================================
pause
