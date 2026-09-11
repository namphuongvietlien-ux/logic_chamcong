# Build and Test Instructions

## Overview

AttendanceApp v1.0.5 now uses **RapidOCR with ONNX Runtime** for the frozen (PyInstaller) build to avoid the torch/EasyOCR crashes that occurred with PyInstaller + Python 3.14.

### Key Changes in v1.0.5

1. **Frozen builds (.exe)** use RapidOCR (ONNX Runtime) instead of EasyOCR/torch
2. **Dev mode** can still use EasyOCR if available, or falls back to RapidOCR
3. No more torch/torchvision dependencies in frozen builds
4. Much smaller frozen executable size
5. No more `STATUS_STACK_BUFFER_OVERRUN` crashes during OCR

## Prerequisites

### For Development

```bash
# Python 3.10-3.14 (recommended: 3.11 or 3.12)
py -m pip install --upgrade pip

# Install all dependencies
py -m pip install -r requirements.txt
```

### For Building Frozen Executable

```bash
# Same as dev, plus ensure PyInstaller is installed
py -m pip install pyinstaller>=6.22.2
```

## Development Mode (Non-Frozen)

Run the app directly with Python:

```bash
py app.py
```

The app will use:
- EasyOCR if available (requires torch, downloads models on first run)
- Falls back to RapidOCR if EasyOCR is not installed

To force RapidOCR in dev mode:
```bash
# Uninstall EasyOCR temporarily
py -m pip uninstall easyocr torch torchvision -y
py app.py
```

## Building the Frozen Executable

### Step 1: Install Dependencies

```bash
py -m pip install -r requirements.txt
```

**Note:** For frozen builds, torch/EasyOCR are NOT needed in the final exe. The spec file excludes them.

### Step 2: Build with PyInstaller

```bash
py -m PyInstaller --noconfirm AttendanceApp.spec
```

This will:
- Create `dist/AttendanceApp/` directory
- Bundle RapidOCR and ONNX Runtime
- Create `AttendanceApp.exe` with `_internal/` folder
- RapidOCR models are downloaded automatically on first OCR run

**Build output:**
```
dist/
└── AttendanceApp/
    ├── AttendanceApp.exe
    └── _internal/
        ├── onnxruntime DLLs
        ├── rapidocr models (auto-downloaded)
        └── other dependencies
```

### Step 3: Verify the Build

```bash
cd dist\AttendanceApp
AttendanceApp.exe --help
```

## Testing OCR on Unicode Paths

### Create Test Structure

```bash
# Inside dist/AttendanceApp/
mkdir "Images"
mkdir "Images\Nguyễn Văn A"
mkdir "Images\Trần Thị B"

# Copy test images with Vietnamese overlay text to:
# Images\Nguyễn Văn A\photo1.jpg
# Images\Trần Thị B\photo2.jpg
```

### Test CLI Mode (Skip GUI)

```bash
cd dist\AttendanceApp
AttendanceApp.exe --cli --excel "path\to\fingerprint.xlsx" --images "Images" --out "output"
```

### Test GUI Mode

```bash
cd dist\AttendanceApp
AttendanceApp.exe
```

1. Select fingerprint Excel file
2. Select Images folder
3. Click "Chạy phân tích" (Run Analysis)
4. Check the log for OCR progress
5. Verify output files are generated

### Expected OCR Behavior

**Frozen build (.exe):**
- Uses RapidOCR (ONNX Runtime)
- No torch/EasyOCR initialization
- Log should show: "Khởi tạo RapidOCR (ONNX Runtime) cho bản frozen..."
- Sequential OCR processing (no ProcessPool in frozen mode)
- Should handle Unicode paths correctly

**Dev mode (py app.py):**
- Uses EasyOCR if available, otherwise RapidOCR
- Log shows which OCR engine is loaded
- Can use ProcessPool for parallel OCR

### Common Issues

#### Issue: "Thiếu RapidOCR"
**Solution:** Install rapidocr-onnxruntime
```bash
py -m pip install rapidocr-onnxruntime onnxruntime
```

#### Issue: OCR text not detected from Timemark overlay
**Solution:** Check that:
- Images contain Timemark yellow overlay with "Điểm danh HH:MM"
- Vietnamese date text visible (e.g., "07 Tháng 8, 2026")
- Image quality is sufficient (not too blurry)

#### Issue: Build fails with missing modules
**Solution:**
```bash
# Clean build artifacts
rmdir /s /q build dist
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm AttendanceApp.spec
```

## Testing Both Dev and Frozen Modes

### 1. Test Dev Mode First

```bash
py app.py --cli --excel test.xlsx --images Images --out output_dev --skip-ocr
```

Verify Excel-only processing works.

### 2. Test Dev Mode with OCR

```bash
py app.py --cli --excel test.xlsx --images Images --out output_dev
```

Check if OCR reads timestamps correctly.

### 3. Build and Test Frozen

```bash
py -m PyInstaller --noconfirm AttendanceApp.spec
cd dist\AttendanceApp
AttendanceApp.exe --cli --excel ..\..\test.xlsx --images ..\..\Images --out output_frozen
```

Compare output_dev and output_frozen to verify consistency.

## Verifying Vietnamese Text Recognition

RapidOCR supports Vietnamese via PP-OCRv4/v5/v6 models. To verify:

1. Create test image with Vietnamese text overlay:
   - "Điểm danh 16:53"
   - "07 Tháng 8, 2026"

2. Run OCR:
```bash
AttendanceApp.exe --cli --images "Images\TestEmployee" --excel dummy.xlsx --out test_output
```

3. Check the log or output Excel for parsed timestamps

## Database Location

The SQLite database (`hr_system.db` or `data/tas.db`) is stored **beside the exe**, NOT inside `_internal/`. This ensures:
- Data persists across updates
- No data loss when updating the exe
- User can backup the .db file easily

## Deployment

To deploy v1.0.5:

1. Build the exe as described above
2. Create a zip of the entire `dist/AttendanceApp/` folder
3. Name it `AttendanceApp-v1.0.5.zip`
4. Upload to GitHub Release as an asset
5. Users can extract and run `AttendanceApp.exe`

The auto-updater will detect the new version and offer to update.

## Rollback to Previous Version

If issues occur with RapidOCR:

1. Checkout the previous commit (before this fix)
2. Rebuild with the old spec (torch/EasyOCR)
3. Note: The old version has the frozen OCR crash issue

## Architecture Notes

### Why RapidOCR for Frozen Builds?

1. **ONNX Runtime is PyInstaller-friendly**: No custom ops registration issues
2. **No torch dependency**: Avoids OpenMP conflicts and DLL loading issues
3. **Smaller exe size**: ONNX models are smaller than torch models
4. **Better Windows compatibility**: No STATUS_STACK_BUFFER_OVERRUN crashes
5. **Auto-model download**: RapidOCR downloads models on first run if missing

### OCR Flow in Code

```
app.py (frozen check)
  ↓
processing/pipeline.py (skip_ocr flag)
  ↓
processing/ocr_processor.py
  ↓
_init_reader() 
  ↓ (if frozen)
  RapidOCR (ONNX Runtime)
  ↓ (if dev + EasyOCR available)
  EasyOCR (torch)
  ↓ (if dev + no EasyOCR)
  RapidOCR (fallback)
```

### Code Changes Summary

1. `requirements.txt`: Added `rapidocr-onnxruntime` and `onnxruntime`
2. `processing/ocr_processor.py`:
   - Added `USE_RAPIDOCR` flag based on `sys.frozen`
   - Modified `_init_reader()` to support both engines
   - Updated `_readtext()` to handle RapidOCR API differences
3. `AttendanceApp.spec`:
   - Removed torch/torchvision/easyocr from hiddenimports
   - Added rapidocr_onnxruntime and onnxruntime
   - Changed runtime hook to `pyi_rth_rapidocr.py`
   - Removed EasyOCR model bundling requirement
4. `packaging/pyi_rth_rapidocr.py`: New runtime hook for ONNX
5. `auto_updater.py`: Bumped version to 1.0.5

## Support

For issues, check:
- Build log for PyInstaller warnings
- App log (in GUI or console) for OCR engine loaded
- crash.log beside the exe if app crashes

## License

Same as the main project.
