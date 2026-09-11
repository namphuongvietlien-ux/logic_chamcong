# Build and Test Instructions

## ⚠️ Important: Windows Desktop Application

**This is a Windows desktop application.** Build and testing must be done on a Windows machine with Python 3.10-3.14 installed. This cloud development environment (Linux) is used for code editing only.

## Overview

AttendanceApp v1.0.6 adds improved OCR for Timemark photos with white watermarks on bright backgrounds, plus Photo Code extraction for manual verification when OCR fails.

### Key Changes in v1.0.6

1. **Three-pass OCR strategy**: Standard overlay → OpenCV enhancement → white-on-bright preprocessing (invert, CLAHE, adaptive threshold, Otsu)
2. **Photo Code extraction**: Extracts ~14-char alphanumeric codes from right-edge vertical text for Timemark verification
3. **Better white-on-bright handling**: Multiple preprocessing variants tested per ROI to handle white text on tiles, receipts, blue panels
4. **Photo Code persistence**: Codes cached alongside timestamps, appear in OCR logs and exception reports
5. **Official verification workflow**: Documentation for using https://verify.timemark.com when OCR fails

For Timemark Photo Code verification details, see **TIMEMARK_VERIFY.md**.

### Previous Version (v1.0.5)

1. **Frozen builds (.exe)** use RapidOCR (ONNX Runtime) instead of EasyOCR/torch
2. **Dev mode** can still use EasyOCR if available, or falls back to RapidOCR
3. No more torch/torchvision dependencies in frozen builds
4. Much smaller frozen executable size
5. No more `STATUS_STACK_BUFFER_OVERRUN` crashes during OCR

## Prerequisites

### System Requirements

- **Operating System**: Windows 10/11 (64-bit)
- **Python**: 3.10, 3.11, 3.12, or 3.14 (3.11/3.12 recommended)
- **RAM**: Minimum 4GB (8GB+ recommended for building)
- **Disk**: ~2GB free space for dependencies + build artifacts

### For Development (Windows)

```bash
# Python 3.10-3.14 (recommended: 3.11 or 3.12)
py -m pip install --upgrade pip

# Install all dependencies
py -m pip install -r requirements.txt
```

**Note**: On Linux/macOS, use `python3` instead of `py`. However, the frozen executable can only be built on Windows.

### For Building Frozen Executable

```bash
# Same as dev, plus ensure PyInstaller is installed
py -m pip install pyinstaller>=6.22.2
```

## Development Mode (Non-Frozen)

### On Windows

Run the app directly with Python:

```bash
py app.py
```

### Code Verification (Linux/macOS)

If you're developing on Linux/macOS (e.g., in a cloud dev environment), you can verify syntax:

```bash
python3 -m py_compile app.py
python3 -m py_compile processing/ocr_processor.py
```

However, **you cannot run the GUI or build the frozen exe** on non-Windows platforms. The app uses Windows-specific features (CustomTkinter, database paths, etc.).

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

**Important**: Test with real Timemark photos that have:
- White watermark text on bright backgrounds (tiles, white receipts, blue panels)
- Visible Photo Code on the right edge (vertical text near "Timemark Verified")
- Vietnamese date format (e.g., "07 Tháng 8, 2026")

### Test CLI Mode (Skip GUI)

```bash
cd dist\AttendanceApp
AttendanceApp.exe --cli --excel "path\to\fingerprint.xlsx" --images "Images" --out "output"
```

### Expected OCR Behavior (v1.0.6)

**Frozen build (.exe):**
- Uses RapidOCR (ONNX Runtime)
- Three-pass OCR: standard → OpenCV → white-on-bright
- Log shows: "Khởi tạo RapidOCR (ONNX Runtime) cho bản frozen..."
- Photo Code extraction attempts on all images
- Log entries include Photo Code when found:
  ```
  [OK] Nguyễn Văn A/photo1.jpg: 2026-09-10 16:23:00 (Photo Code: XTRDEY34BPT12 (variant_0) | white-on-bright preprocess)
  ```
  Or for failures:
  ```
  [BỎ QUA] Trần Thị B/photo2.jpg: OCR có giờ 12:41, không thấy ngày — Cần verify Timemark với Photo Code: ABC123XYZ4567 [Photo Code: ABC123XYZ4567]
  ```

**Dev mode (py app.py):**
- Uses EasyOCR if available, otherwise RapidOCR
- Same three-pass strategy
- Can use ProcessPool for parallel OCR (faster)

### Verify Photo Code Extraction

1. Check OCR log output for "Photo Code: XXXXX" entries
2. Open a test image in an image viewer
3. Zoom into the right edge near "Timemark Verified" text
4. Compare the vertical alphanumeric code with the log
5. If codes match → extraction successful
6. If codes differ or missing → check image quality, compression, cropping

### Test Verification Workflow

When OCR fails but Photo Code is found:

1. Copy the Photo Code from the log (e.g., `XTRDEY34BPT12`)
2. Open https://verify.timemark.com in a browser
3. Paste the code or upload the photo
4. Verify that Timemark returns the correct timestamp
5. Use the **Corrections** tab to manually add the verified punch

See **TIMEMARK_VERIFY.md** for complete verification workflow documentation.

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
- Three-pass OCR strategy (v1.0.6+): standard → OpenCV → white-on-bright
- Photo Code extraction on all images (v1.0.6+)

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
**Solution (v1.0.6+):** The new three-pass OCR strategy should handle most cases:
- Pass 1: Standard yellow overlay detection
- Pass 2: OpenCV contrast enhancement
- Pass 3: White-on-bright preprocessing (invert, CLAHE, Otsu)

If OCR still fails but Photo Code is extracted:
- Use https://verify.timemark.com to verify the timestamp
- See TIMEMARK_VERIFY.md for complete workflow
- Manually add the verified punch via the Corrections tab

Check that:
- Images contain Timemark yellow overlay with "Điểm danh HH:MM" OR white bottom-left timestamp
- Photo Code is visible on the right edge (vertical text)
- Vietnamese date text visible (e.g., "07 Tháng 8, 2026")
- Image quality is sufficient (not too blurry or heavily compressed)

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

To deploy v1.0.6:

1. Build the exe as described above
2. Create a zip of the entire `dist/AttendanceApp/` folder
3. Name it `AttendanceApp-v1.0.6.zip`
4. Upload to GitHub Release as an asset
5. Include TIMEMARK_VERIFY.md in the release notes
6. Users can extract and run `AttendanceApp.exe`

The auto-updater will detect the new version and offer to update.

## Rollback to Previous Version

If issues occur with the new OCR strategy:

1. Checkout the previous commit (v1.0.5)
2. Rebuild with the old spec
3. Note: The old version has simpler two-pass OCR and no Photo Code extraction

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

**v1.0.6** (Timemark OCR + Photo Code):
1. `processing/ocr_processor.py`:
   - Added `preprocess_for_white_on_bright()`: multiple variants (invert, CLAHE, Otsu) for white text on bright backgrounds
   - Added `extract_photo_code()`: OCR vertical right-edge strip, extract 12-16 alphanumeric code
   - Updated `_ocr_image()`: three-pass strategy, return photo_code tuple
   - Updated `_cache_payload()`, `_datetime_from_cache()`: persist/load photo codes
   - Updated `ocr_image_job()`, `_apply_ocr_result()`: handle photo_code parameter
   - Updated parallel/sequential workers: persist photo codes
   - Enhanced `_overlay_rois()`: added bottom_half ROI for better coverage
2. `processing/timemark_utils.py`: New utility module with Photo Code helpers (copy to clipboard, open verify URL, format notes)
3. `TIMEMARK_VERIFY.md`: Complete documentation for Photo Code verification workflow
4. `BUILD_AND_TEST.md`: Updated with v1.0.6 features, testing instructions for Photo Codes

**v1.0.5** (RapidOCR):
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
