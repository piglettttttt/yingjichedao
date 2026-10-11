@echo off
rem ============================================================================
rem  Step 0 of every task: turn the user's marked screenshot into an ROI json.
rem
rem  Usage:
rem    parse_roi.bat <marked-screenshot> <video> <roi-output-dir> [mode] [t]
rem
rem  Example:
rem    parse_roi.bat downloads\image_50.png ^
rem        downloads\BV1CFRGBVEpo_00m24s-00m59s_h264.mp4 _reuse\roi\xt  polygon
rem
rem    mode = polygon (closed area / multi-point, uses convex hull)  [default line]
rem           line    (one line; the side given by --side is the emergency lane)
rem    t    = timestamp (seconds) of the frame to draw the verification image on
rem
rem  ROI MUST be re-drawn for every new video: the emergency lane sits at a
rem  different place in each clip, and automatic lane detection was measured to be
rem  unreliable (210px off once, an absurd slope of -6.19 another time).
rem
rem  Output: <roi-output-dir>\roi.json (+ roi_verify.jpg, + run_meta.json).
rem  ALWAYS open roi_verify.jpg afterwards: it overlays the parsed polygon on the
rem  video frame. If the overlay does not match what you marked, the screenshot had
rem  black bars or was scaled - reframe and re-run. (A real case: the parsed
rem  polygon ran past the right edge of the video and got clipped.)
rem
rem  Pure ASCII on purpose - see the note in run_pipeline.bat.
rem ============================================================================
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
set "MODE=%~4"
set "T=%~5"
if "%MODE%"=="" set "MODE=polygon"
if "%T%"=="" set "T=0"

if "%~1"=="" (
  echo Usage: parse_roi.bat ^<marked-screenshot^> ^<video^> ^<roi-output-dir^> [mode] [t]
  exit /b 2
)
if not exist "%PY%" ( echo [ERROR] python not found: %PY% & exit /b 2 )
if not exist "%~f1" ( echo [ERROR] screenshot not found: %~f1 & exit /b 2 )
if not exist "%~f2" ( echo [ERROR] video not found: %~f2 & exit /b 2 )

echo  mode = %MODE%   (echo of the shot/video paths follows from python)
"%PY%" "%~dp0parse_roi.py" --shot "%~f1" --video "%~f2" --out "%~f3" --mode %MODE% --t %T%
if errorlevel 1 ( echo [FAILED] parse_roi.py & exit /b 1 )

echo.
echo  PARSE DONE. Open the verification image and confirm the overlay matches:
echo    %~f3\roi_verify.jpg
endlocal
