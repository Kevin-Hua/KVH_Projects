@echo off
setlocal
cd /d "%~dp0"

set APP=AutoID

echo ============================================================
echo  Packing %APP%
echo ============================================================


:: ── Regenerate version_info.txt from source version string ────
echo [1/3] Generating version_info.txt ...
python ..\\_gen_version.py %APP%
if errorlevel 1 ( pause & exit /b 1 )

:: ── Regenerate spec ───────────────────────────────────────────
echo [2/3] Generating %APP%.spec ...
pyi-makespec %APP%.py ^
    --onefile --noconsole ^
    --name %APP% ^
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