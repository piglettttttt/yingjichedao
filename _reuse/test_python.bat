@echo off
rem ============================================================================
rem  Regression tests: Python side + C side (+ optional evidence packaging).
rem
rem  The Python test bodies live inline in the scripts they cover, prefixed
rem  check_<func> (per the project rule), so they are never separated from the
rem  code they cover:
rem    _reuse\runmeta.py          check_sha256_file / check_roi_meta_* / ...
rem    _reuse\run_pipeline.py     check_norm / check_tighten_candidates / ...
rem    _reuse\merge_tracks.py     check_pick_two_frames / ...
rem    _reuse\stage1_vehicles.py  check_load_roi_and_bottom_span
rem  tests\test_pipeline.py just collects and runs them. No pytest needed.
rem
rem  The C-side tests come from build_c.bat:
rem    dist\test_logic.exe     expect "50 passed, 0 failed"
rem    dist\test_report.exe    expect ALL PASSED
rem    dist\test_package.exe   real end-to-end check of "generate report"
rem                            (evidence frames + both report variants); only runs
rem                            when PKG is set to an initial-result folder:
rem                                set PKG=downloads\xiangtang
rem                                _reuse\test_python.bat
rem                            it regenerates that folder's review output in place.
rem
rem  WARNING: the two C test exes use hard-coded fixture folders
rem  (C:\Users\Lhh\pr_test2 / pr_test3) and delete them at start, so never run
rem  this script twice at the same time - they kill each other's fixture and hang.
rem
rem  Pure ASCII on purpose - see the note in run_pipeline.bat. cmd reads .bat in
rem  the OEM code page, and caret-escaped angle brackets inside a parenthesised
rem  block break the whole block (this already happened once here).
rem ============================================================================
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" ( echo [ERROR] python not found: %PY% & exit /b 2 )

rem  Text hygiene first: a one-second scan that catches the mechanical mistakes
rem  (non-ASCII in .bat / PlateReview.cpp, LF-only .bat) which otherwise only
rem  surface much later as confusing errors.
"%PY%" "%~dp0tests\check_text_hygiene.py"
if errorlevel 1 (
  echo TESTS FAILED - fix the text hygiene problems above first
  endlocal & exit /b 1
)

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
rem  test_report needs downloads\ax285e_test, which is GENERATED from a result
rem  folder by make_test_case.py. If it is missing, skip the test instead of
rem  failing: a missing fixture is not a code defect. (The file name inside is
rem  Chinese, so it is matched with a wildcard - a .bat must stay pure ASCII.)
if not exist "%~dp0..\downloads\ax285e_test\*.csv" goto :no_fixture
if exist "%~dp0dist\test_report.exe" (
  "%~dp0dist\test_report.exe"
  if errorlevel 1 set "RC=1"
) else (
  echo  [SKIP] dist\test_report.exe missing - run build_c.bat
)
goto :after_report

:no_fixture
echo  [SKIP] test_report - downloads\ax285e_test is missing.
echo         regenerate it:  .venv\Scripts\python.exe _reuse\make_test_case.py
echo         it copies the materials from a fresh result folder

:after_report
echo.

rem  Keep this test out of the parenthesised block: the folder path may contain
rem  characters cmd would reinterpret. Use a subroutine instead.
if not defined PKG goto :no_pkg
if not exist "%~dp0dist\test_package.exe" goto :no_pkg_exe
echo.
echo ============================================================
echo  Evidence packaging on %PKG%
echo ============================================================
"%~dp0dist\test_package.exe" "%PKG%"
if errorlevel 1 set "RC=1"
goto :summary

:no_pkg_exe
echo  [SKIP] dist\test_package.exe missing - run build_c.bat
goto :summary

:no_pkg
echo.
echo  [SKIP] evidence-packaging test - set PKG to an initial-result folder, e.g.
echo         set PKG=downloads\xiangtang

:summary
rem  Light-weight report links: run this AFTER the packaging test, because that
rem  is what generates a report to check. Skips cleanly when none exists yet.
echo.
echo ============================================================
echo  Light-weight report links
echo ============================================================
"%PY%" "%~dp0tests\check_report_links.py"
if errorlevel 1 set "RC=1"

echo.
if "%RC%"=="0" (echo ALL TESTS PASSED) else (echo TESTS FAILED)
endlocal & exit /b %RC%
