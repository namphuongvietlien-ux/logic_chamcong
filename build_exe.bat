@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtualenv...
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo First-time EasyOCR model download (if needed)...
python -c "import easyocr, pathlib; p=pathlib.Path.home()/'.EasyOCR'/'model'; p.mkdir(parents=True, exist_ok=True); easyocr.Reader(['en'], gpu=False, verbose=True, model_storage_directory=str(p))"

echo.
echo Building onedir exe (dist\AttendanceApp\AttendanceApp.exe)...
if /I "%~1"=="full" (
  echo Full rebuild with --clean
  pyinstaller --noconfirm --clean AttendanceApp.spec
) else (
  echo Incremental rebuild (reuse Analysis cache; pass "full" to clean)
  pyinstaller --noconfirm AttendanceApp.spec
)

echo.
echo Done. Distribute the entire folder dist\AttendanceApp\
echo Run: dist\AttendanceApp\AttendanceApp.exe
pause
