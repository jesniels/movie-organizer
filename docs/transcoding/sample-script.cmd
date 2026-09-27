@echo off
setlocal enabledelayedexpansion

rem Define paths
set "INPUT_DIR=D:\Downloaded\Netflix"
set "OUTPUT_DIR=%INPUT_DIR%\Netflix-transcoded"
set "OUTPUT_ORIGINAL=%INPUT_DIR%\Netflix-original"
set "FFMPEG_PATH=d:\tools\ffmpeg\bin\ffmpeg.exe"
set "FFPROBE_PATH=d:\tools\ffmpeg\bin\ffprobe.exe"

rem Routing logic based on argument
if "%1"=="scan" goto :MAIN_LOOP
if "%1"=="convert" goto :MAIN_LOOP

echo Usage: transcode.bat [scan ^| convert]
exit /b

:MAIN_LOOP
if "%1"=="convert" (
    if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
    if not exist "%OUTPUT_ORIGINAL%" mkdir "%OUTPUT_ORIGINAL%"
    echo --- STARTING GPU CONVERSION ---
) else (
    echo --- SCANNING FOR OUTLIERS ---
)

rem Single unified loop over your MKV files
rem OLD dynamic: for %%F in ("%INPUT_DIR%\*.mkv") do (
rem New static:
rem for /f "delims=" %%F in ('where /r "%INPUT_DIR%" *.mkv') do (


rem Build static list once
dir /b /a-d "%INPUT_DIR%\*.mkv" > "%TEMP%\filelist.txt"

rem Load all filenames into array
set "FILECOUNT=0"
for /f "delims=" %%F in (%TEMP%\filelist.txt) do (
    set /a FILECOUNT+=1
    set "FILE[!FILECOUNT!]=%%F"
)

echo "Found !FILECOUNT! MKV files to process."
echo "***************************************"
rem Now iterate with counter (responds better to Ctrl+C)
set "CURRENTIDX=0"

:PROCESS_LOOP
set /a CURRENTIDX+=1
if !CURRENTIDX! gtr !FILECOUNT! goto :CLEANUP

call set "FILENAME=%%FILE[!CURRENTIDX!]%%"
set "FILEPATH=%INPUT_DIR%\!FILENAME!"
set "ENG_AUDIO_INDEX="

if "%1"=="convert" echo.

rem Call the shared function to find the track index for this specific file
call :FIND_TRACK "!FILEPATH!"

rem Process the result based on whether we are scanning or converting
if not defined ENG_AUDIO_INDEX (
    if "%1"=="scan" echo [OUTLIER] "!FILENAME!" - Missing proper/clean English audio track.
    if "%1"=="convert" echo [ERROR] Skipping "!FILENAME!" - No clean English audio track found. Added to fail list.
) else (
    if "%1"=="scan" (
        echo [OK] "!FILENAME!" - Found clean English audio at stream index !ENG_AUDIO_INDEX!
    )
    if "%1"=="convert" (
        echo Processing: "!FILENAME!"
        echo Target English Audio Track: Index !ENG_AUDIO_INDEX!
        
        rem Run the GPU transcode. Added -v error and -stats to clean up console output.

rem: THis failed before - maybe if there are multiple audio tracks ???
rem  "%FFMPEG_PATH%" -v error -stats -i "!FILEPATH!" -c:v hevc_amf -rc 1 -qp_i 22 -qp_p 22 -map 0:v:0 -map 0:a:!ENG_AUDIO_INDEX! -map 0:s:? -c:a copy -c:s copy "%OUTPUT_DIR%\!FILENAME!"
rem Now I changed it to and it seem to work:
        "%FFMPEG_PATH%" -v error -stats -i "!FILEPATH!" -c:v hevc_amf -rc 1 -qp_i 22 -qp_p 22 -map 0:v:0 -map 0:!ENG_AUDIO_INDEX! -map 0:s:? -c:a copy -c:s copy "%OUTPUT_DIR%\!FILENAME!"            
        if !errorlevel! EQU 0 (
            echo Success. Moving original to %OUTPUT_ORIGINAL%
            move "!FILEPATH!" "%OUTPUT_ORIGINAL%\"

        ) else (
            echo [ERROR] FFmpeg failed with exit code !errorlevel!. Keeping original in place.
        )
    )
)

goto :PROCESS_LOOP

:CLEANUP
del /q "%TEMP%\filelist.txt" 2>nul
exit /b

:FIND_TRACK
rem --- SHARED AUDIO TRACK DETECTION FUNCTION ---
set "ENG_AUDIO_INDEX="

rem 1. Run ffprobe and dump output to a temporary text file safely
"%FFPROBE_PATH%" -v error -select_streams a -show_entries stream=index:stream_tags=language,title:stream_disposition=comment,hearing_impaired -of csv=p=0 "%~1" > "%TEMP%\ffprobe_out.txt"

rem 2. Parse the clean temporary file without command-line nesting headaches
for /f "tokens=1* delims=," %%A in (%TEMP%\ffprobe_out.txt) do (
    if not defined ENG_AUDIO_INDEX (
        set "TRACK_LINE=%%B"
        echo !TRACK_LINE! | findstr /I "eng" >nul
        if !errorlevel! EQU 0 (
            echo !TRACK_LINE! | findstr /I "description descriptive commentary sdh visual" >nul
            if !errorlevel! NEQ 0 (
                set "ENG_AUDIO_INDEX=%%A"
            )
        )
    )
)

rem 3. Clean up the temp file
if exist "%TEMP%\ffprobe_out.txt" del "%TEMP%\ffprobe_out.txt"
goto :eof
