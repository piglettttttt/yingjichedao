@echo off
rem ============================================================================
rem  One-shot end-to-end run:
rem    ROI -> vehicle detect -> two-stage plate OCR -> track merge -> outputs
rem
rem  Usage:
rem    run_pipeline.bat <video> <roi.json> <output-dir> [base-timestamp]
rem
rem  Example:
rem    run_pipeline.bat downloads\BV1CFRGBVEpo_00m24s-00m59s_h264.mp4 ^
rem        _reuse\roi\roi_image50.json downloads\xiangtang_v2
rem
rem  Notes:
rem    - output-dir must be pure ASCII (the review GUI cannot read CJK paths)
rem    - video without a dashcam timestamp overlay: set TIME_MODE=video first
rem        set TIME_MODE=video
rem        run_pipeline.bat ...
rem    - arg 4 is the dashcam base time (e.g. 2023-03-04 15:38:02). It MUST be
rem      given again for every new video, otherwise the built-in base time of the
rem      first test video is reused and the time column is wrong.
rem    - every step writes run_meta.json next to its output: which ROI file
rem      (sha256), which parameters, which video. ROI is re-drawn per video, so
rem      this is how a run stays reproducible.
rem
rem  This file is deliberately pure ASCII: cmd.exe reads .bat files using the OEM
rem  code page (GBK on Chinese Windows), so UTF-8 Chinese text in here corrupts
rem  the commands themselves. Same rule as build_c.bat.
rem ============================================================================
setlocal enabledelayedexpansion
set "PY=%~dp0..\.venv\Scripts\python.exe"
set "REUSE=%~dp0"
set "ROOT=%~dp0.."
set "FFPROBE=%ROOT%\bin\ffprobe.exe"
set "FFMPEG=%ROOT%\bin\ffmpeg.exe"

if "%~1"=="" (
  echo Usage: run_pipeline.bat ^<video^> ^<roi.json^> ^<output-dir^> [base-timestamp] [seconds]
  echo   ROI json comes from parse_roi.bat
  echo   seconds = how much of the video to analyse, default 30.
  echo   Use a few seconds while wiring things up; the chain is identical.
  exit /b 2
)

set "VIDEO=%~f1"
set "ROI=%~f2"
set "OUTDIR=%~f3"
set "BASETS=%~4"
set "SECS=%~5"
if "%SECS%"=="" set "SECS=30"

if not exist "%PY%"    ( echo [ERROR] python not found: %PY% & exit /b 2 )
if not exist "%VIDEO%" ( echo [ERROR] video not found: %VIDEO% & exit /b 2 )
if not exist "%ROI%"   ( echo [ERROR] roi not found: %ROI% & exit /b 2 )

rem  NOTE: the "output dir must be pure ASCII" check is NOT done here. cmd's
rem  findstr "[^ -~]" does not detect CJK under the GBK OEM code page (measured:
rem  it misses every time), so make_outputs.py rejects a non-ASCII outdir itself.
rem  Lesson (same family as the pipe note below): do not validate text in .bat.

set "RUN=%ROOT%\_reuse\run\%~n3"
set "VEH=%RUN%\veh_%~n3.jsonl"

echo ============================================================
echo  video    : %VIDEO%
echo  roi      : %ROI%
echo  outdir   : %OUTDIR%
echo  workdir  : %RUN%
echo  analyse  : first %SECS% s of the video
if "%TIME_MODE%"=="" set "TIME_MODE=clock"
if not "%TIME_MODE%"=="clock" echo  time mode: %TIME_MODE%
echo ============================================================

echo.
echo [0/4] codec check - AV1 makes the pipeline ~20x slower
rem  NOTE: never put "| findstr" inside a parenthesised block. cmd.exe parses the
rem  pipe at parse time and the block breaks ("... was unexpected at this time"),
rem  which silently leaves CODEC empty so the AV1 guard never fires.
rem  Run ffprobe into a temp file first, then read it line by line.
set "CODEC="
"%FFPROBE%" -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "%VIDEO%" > "%TEMP%\dsh_codec.txt" 2>nul
for /f "usebackq tokens=*" %%c in ("%TEMP%\dsh_codec.txt") do if not defined CODEC set "CODEC=%%c"
del "%TEMP%\dsh_codec.txt" >nul 2>nul
if not defined CODEC echo        [WARN] ffprobe could not read the codec - check the video plays.
if defined CODEC echo        codec = %CODEC%
if /i "%CODEC%"=="av1" goto :av1

rem  weights live in the repo root (only one copy is kept, see .gitignore notes).
rem  Do NOT point this at %REUSE%\yolo11n.pt: that duplicate was removed, and a
rem  missing path makes ultralytics silently download the file into the current
rem  directory instead of failing.
set "WEIGHTS=%ROOT%\yolo11n.pt"
if not exist "%WEIGHTS%" (
  echo [ERROR] weights not found: %WEIGHTS%
  echo         keep exactly one copy at the repo root; do not let ultralytics
  echo         download it - it would silently write a second copy here.
  exit /b 2
)

echo.
echo [1/4] vehicle detection - YOLO
"%PY%" "%REUSE%stage1_vehicles.py" --video "%VIDEO%" --roi "%ROI%" --out "%VEH%" --weights "%WEIGHTS%" --end %SECS%
if errorlevel 1 ( echo [FAILED] stage1_vehicles.py & exit /b 1 )

echo.
echo [2/4] two-stage plate OCR: locate plate, crop+upscale, recognize
"%PY%" "%REUSE%pipeline_v2.py" --video "%VIDEO%" --roi "%ROI%" --veh-jsonl "%VEH%" --outdir "%RUN%" --no-mask-detect --onnx "%REUSE%onnx"
if errorlevel 1 ( echo [FAILED] pipeline_v2.py & exit /b 1 )

echo.
echo [3/4] track merge - fixes the inflated case count
"%PY%" "%REUSE%merge_tracks.py" --result "%RUN%\result.json" --veh-jsonl "%VEH%" --out "%RUN%\result_merged.json" --time-tol-frames 12 --dist-max 1.5
if errorlevel 1 ( echo [FAILED] merge_tracks.py & exit /b 1 )

echo.
echo [4/4] write outputs: CSV / JSON / evidence frames
rem  NOTE: no parenthesised if/else block around the python call. Keep the three
rem  cases as separate jumps - a nested ( ) else ( ) block around a quoted
rem  command line is what produced the garbled "'Ov' is not recognized" error.
if "%TIME_MODE%"=="video" goto :out_video
if "%BASETS%"=="" goto :out_clock
goto :out_clock_base

:out_video
"%PY%" "%REUSE%make_outputs.py" --result "%RUN%\result_merged.json" --video "%VIDEO%" --outdir "%OUTDIR%" --time-mode video
goto :out_done

:out_clock
"%PY%" "%REUSE%make_outputs.py" --result "%RUN%\result_merged.json" --video "%VIDEO%" --outdir "%OUTDIR%" --time-mode clock
goto :out_done

:out_clock_base
"%PY%" "%REUSE%make_outputs.py" --result "%RUN%\result_merged.json" --video "%VIDEO%" --outdir "%OUTDIR%" --time-mode clock --base-ts "%BASETS%"
goto :out_done

:out_done
if errorlevel 1 ( echo [FAILED] make_outputs.py & exit /b 1 )

echo.
echo ============================================================
echo  DONE. Review GUI input dir:
echo    %OUTDIR%
echo  open: "%REUSE%dist\PlateReview.exe"
echo ============================================================
endlocal
exit /b 0

:av1
echo        [WARN] AV1 input. Transcode to H.264 first, e.g.:
echo          "%FFMPEG%" -y -i "%VIDEO%" -c:v libx264 -preset veryfast -crf 18 -c:a copy "%~dpn1_h264.mp4"
endlocal
exit /b 3
