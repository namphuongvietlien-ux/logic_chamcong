@echo off
setlocal
cd /d "%~dp0"

REM Do not use .venv here: it may point at a Python install that no longer exists.
REM Do not use --onefile. OCR/torch needs the whole dist\AttendanceApp folder.

if not exist "models\craft_mlt_25k.pth" (
  echo Missing EasyOCR models. Downloading into models\ ...
  py download_ocr_models.py
  if errorlevel 1 (
    echo Failed to download OCR models.
    pause
    exit /b 1
  )
)

echo Building onedir exe (dist\AttendanceApp\AttendanceApp.exe)...
if /I "%~1"=="full" (
  echo Full rebuild with --clean
  py -m PyInstaller --noconfirm --clean AttendanceApp.spec
) else (
  echo Incremental rebuild
  py -m PyInstaller --noconfirm AttendanceApp.spec
)

if exist "README_CHAY.txt" copy /Y "README_CHAY.txt" "dist\AttendanceApp\README_CHAY.txt" >nul
if exist "HUONG_DAN.txt" copy /Y "HUONG_DAN.txt" "dist\AttendanceApp\HUONG_DAN.txt" >nul

echo.
echo Done. Zip and send the entire folder dist\AttendanceApp\
echo Run: dist\AttendanceApp\AttendanceApp.exe
pause
