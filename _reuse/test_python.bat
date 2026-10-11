@echo off
rem ============================================================================
rem  Python-side regression tests.
rem
rem  The test bodies live inline in the scripts they cover, prefixed check_<func>
rem  (per the project rule), so they are never separated from the code:
rem    _reuse\runmeta.py         check_sha256_file / check_roi_meta_... / ...
rem    _reuse\run_pipeline.py    check_norm / check_tighten_candidates / ...
rem    _reuse\stage1_vehicles.py check_load_roi_and_bottom_span
rem  This just runs all of them. No pytest needed (and none is installed).
rem
rem  The C-side tests are separate: build_c.bat produces dist\test_logic.exe
rem  (expect 27 passed, 0 failed) and dist\test_report.exe (expect ALL PASSED).
rem
rem  Pure ASCII on purpose - see the note in run_pipeline.bat.
rem ============================================================================
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" ( echo [ERROR] python not found: %PY% & exit /b 2 )

"%PY%" "%~dp0tests\test_pipeline.py"
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================
echo  C-side tests (needs build_c.bat to have run once)
echo ============================================================
if exist "%~dp0dist\test_logic.exe" (
  "%~dp0dist\test_logic.exe"
  if errorlevel 1 set "RC=1"
) else (
  echo  [SKIP] dist\test_logic.exe missing - run build_c.bat
)
if exist "%~dp0dist\test_report.exe" (
  "%~dp0dist\test_report.exe"
  if errorlevel 1 set "RC=1"
) else (
  echo  [SKIP] dist\test_report.exe missing - run build_c.bat
)

echo.
if "%RC%"=="0" (echo ALL TESTS PASSED) else (echo TESTS FAILED)
endlocal & exit /b %RC%
