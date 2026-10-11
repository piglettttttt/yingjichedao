@echo off
rem Build the plate-review GUI. Pure Win32 + GDI+, no third-party dependencies.
rem Usage: double-click, or run on a machine with Visual Studio 2022 C++ tools.
rem
rem NOTE: the source is PlateReview.cpp. It MUST be C++ and MUST keep the .cpp
rem       extension: GDI+ is exposed through C++ interfaces only, so a .c file
rem       fails to compile.
rem
rem This file is deliberately pure ASCII: cmd.exe reads .bat files using the
rem OEM code page (GBK on a Chinese Windows), so UTF-8 Chinese text in here
rem corrupts the commands themselves.
setlocal

set VCVARS="C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"
if not exist %VCVARS% (
  echo [ERROR] vcvarsall.bat not found. Install the VS2022 C++ build tools.
  exit /b 1
)

call %VCVARS% x64 >nul
if errorlevel 1 (
  echo [ERROR] vcvarsall init failed
  exit /b 1
)

if not exist "dist" mkdir "dist"

rem /D_CRT_SECURE_NO_WARNINGS : we use _wfopen/ftell etc.
rem /utf-8 : tell MSVC the sources are UTF-8 in AND out. Without this, MSVC
rem          reads the sources using the OEM code page (GBK here) and encodes
rem          narrow string literals the same way, which silently corrupts
rem          Chinese text written to UTF-8 files (warning C4819 is the early
rem          symptom). PlateReview.cpp is pure ASCII by convention, but the
rem          test sources carry Chinese comments and assertion labels.
set CFLAGS=/nologo /O2 /W3 /utf-8 /DUNICODE /D_UNICODE /EHsc /D_CRT_SECURE_NO_WARNINGS
set GUI_LIBS=/link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib gdiplus.lib comdlg32.lib shell32.lib ole32.lib

echo [1/4] compiling PlateReview.cpp ...
cl %CFLAGS% /Fe:"dist\PlateReview.exe" PlateReview.cpp %GUI_LIBS%
if errorlevel 1 (
  echo [ERROR] build failed
  exit /b 1
)

echo [2/4] compiling test_logic.cpp ...
cl %CFLAGS% /Fe:"dist\test_logic.exe" test_logic.cpp ^
   /link /SUBSYSTEM:CONSOLE user32.lib gdi32.lib ole32.lib shell32.lib
if errorlevel 1 (
  echo [ERROR] test build failed
  exit /b 1
)

echo [3/4] compiling test_report.cpp ...
cl %CFLAGS% /Fe:"dist\test_report.exe" test_report.cpp ^
   /link /SUBSYSTEM:CONSOLE user32.lib gdi32.lib gdiplus.lib comdlg32.lib shell32.lib ole32.lib
if errorlevel 1 (
  echo [ERROR] test build failed
  exit /b 1
)

echo [4/4] compiling test_package.cpp ...
cl %CFLAGS% /Fe:"dist\test_package.exe" test_package.cpp ^
   /link /SUBSYSTEM:CONSOLE user32.lib gdi32.lib gdiplus.lib comdlg32.lib shell32.lib ole32.lib
if errorlevel 1 (
  echo [ERROR] test build failed
  exit /b 1
)

rem clean intermediate files
if exist "PlateReview.obj" del /q "PlateReview.obj"
if exist "test_logic.obj"  del /q "test_logic.obj"
if exist "test_report.obj" del /q "test_report.obj"
if exist "test_package.obj" del /q "test_package.obj"

echo.
echo Build finished. Output in dist\ :
dir /b "dist"
endlocal
