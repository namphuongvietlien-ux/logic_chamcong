# Windows Testing Guide for PR #1

## Summary

This PR fixes the frozen OCR crash by replacing torch/EasyOCR with RapidOCR/ONNX. **Testing must be done on Windows** where the app will be deployed.

## Quick Test Steps (Windows Only)

### 1. Clone and Checkout the Branch

```cmd
git clone https://github.com/namphuongvietlien-ux/logic_chamcong.git
cd logic_chamcong
git checkout cursor/fix-frozen-ocr-rapidocr-3462
```

Or if you already have the repo:

```cmd
cd logic_chamcong
git fetch origin
git checkout cursor/fix-frozen-ocr-rapidocr-3462
```

### 2. Install Dependencies

```cmd
py -m pip install -r requirements.txt
```

This will install:
- `rapidocr-onnxruntime` (new, for frozen builds)
- `onnxruntime` (new, ONNX inference engine)
- All existing dependencies (pandas, openpyxl, customtkinter, etc.)
- `easyocr`, `torch`, `torchvision` (kept for dev mode compatibility, but NOT used in frozen builds)

### 3. Test Dev Mode (Optional but Recommended)

```cmd
py app.py
```

Expected behavior:
- GUI opens normally
- If EasyOCR is installed: "Mô hình EasyOCR sẵn sàng"
- If EasyOCR not installed: "RapidOCR sẵn sàng (fallback từ EasyOCR)"
- Try running analysis with images to verify OCR works

### 4. Build the Frozen Executable

```cmd
py -m PyInstaller --noconfirm AttendanceApp.spec
```

Expected output:
- `dist\AttendanceApp\` directory created
- `AttendanceApp.exe` with `_internal\` folder
- No errors about missing torch models
- Build completes successfully

**Build time**: 2-5 minutes depending on your machine.

### 5. Test the Frozen Executable

#### Basic GUI Test

```cmd
cd dist\AttendanceApp
AttendanceApp.exe
```

Expected:
- GUI opens (no crash on startup)
- App shows "Đối soát chấm công" window
- Check version: should show **v1.0.5**

#### Test Excel-Only (Baseline)

```cmd
AttendanceApp.exe --cli --excel "..\..\test.xlsx" --out "..\..\output_test" --skip-ocr
```

Expected:
- Processes Excel successfully
- No crash
- Output files generated

#### Test OCR (Critical Test)

Create test structure:

```cmd
mkdir Images
mkdir "Images\Nguyễn Văn A"
mkdir "Images\Trần Thị B"
```

Copy test images with Timemark overlays to these folders, then:

```cmd
AttendanceApp.exe --cli --excel "..\..\test.xlsx" --images "Images" --out "..\..\output_ocr"
```

**Expected log output:**

```
Khởi tạo RapidOCR (ONNX Runtime) cho bản frozen...
RapidOCR sẵn sàng (ONNX, không dùng torch).
OCR tuần tự trong bản .exe (tránh lỗi process + torchvision::nms).
OCR <N> ảnh từ Images — cache 0, cần đọc <N> (file ocr_cache.json).
[OK] <employee>/<image.jpg>: 2026-08-07 16:53:00
...
Đã lưu cache OCR: ocr_cache.json (<N> ảnh).
```

**Critical success indicators:**
- ✅ No `STATUS_STACK_BUFFER_OVERRUN` crash
- ✅ No hang or freeze during OCR
- ✅ Log shows "RapidOCR sẵn sàng (ONNX, không dùng torch)"
- ✅ Vietnamese timestamps parsed correctly
- ✅ Output files generated successfully

#### Test Unicode Paths

Test with employee names containing Vietnamese characters:

```cmd
mkdir "Images\Nguyễn Thị Minh Châu"
mkdir "Images\Đặng Văn Hải"
```

Add test images and run OCR - should handle Unicode paths correctly.

### 6. Compare with Previous Version (Optional)

If you have the old frozen exe (v1.0.2 or v1.0.4):

1. Rename it to `AttendanceApp_old.exe`
2. Run it with OCR: likely crashes or hangs
3. Run the new v1.0.5: should work smoothly

## What Changed in v1.0.5

### User-Visible Changes
- ✅ Frozen exe no longer crashes during OCR
- ✅ Smaller exe size (~30-40% reduction without torch)
- ✅ Faster startup (no torch initialization)
- ✅ Version shown as **1.0.5** in GUI

### Technical Changes
- 🔧 RapidOCR replaces EasyOCR in frozen builds
- 🔧 ONNX Runtime replaces torch in frozen builds
- 🔧 Sequential OCR (no ProcessPool in frozen mode)
- 🔧 Auto-download ONNX models on first OCR run

### Unchanged
- ✅ HR logic (pair-wise In-Out, lunch rules, etc.)
- ✅ Database location (beside exe)
- ✅ Excel processing
- ✅ All GUI features
- ✅ CLI flags (`--cli`, `--skip-ocr`, etc.)

## Troubleshooting

### "Thiếu RapidOCR" Error

**Cause**: rapidocr-onnxruntime not installed.

**Solution**:
```cmd
py -m pip install rapidocr-onnxruntime onnxruntime
py -m PyInstaller --noconfirm AttendanceApp.spec
```

### OCR Not Reading Timestamps

**Possible causes**:
1. Image quality too low (blurry, dark)
2. Timemark overlay not visible
3. Text too small

**Solution**: Check that test images have clear yellow Timemark overlay with "Điểm danh HH:MM" and date.

### Build Fails

**Solution**:
```cmd
rmdir /s /q build dist
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm AttendanceApp.spec
```

### Comparing OCR Engines

To see which OCR engine is being used:

**Dev mode:**
```cmd
py app.py
# Check log: "EasyOCR" or "RapidOCR"
```

**Frozen mode:**
```cmd
dist\AttendanceApp\AttendanceApp.exe
# Should always show: "RapidOCR (ONNX Runtime)"
```

## Success Criteria

Before approving this PR, verify:

- [ ] Frozen exe builds successfully on Windows
- [ ] Frozen exe opens (no startup crash)
- [ ] Version shows **v1.0.5** in GUI
- [ ] Excel-only mode works (`--skip-ocr`)
- [ ] OCR mode works (no crash, timestamps parsed)
- [ ] Log shows "RapidOCR (ONNX, không dùng torch)" in frozen mode
- [ ] Unicode employee names work (Vietnamese characters in paths)
- [ ] Output Excel files generated correctly
- [ ] Dev mode (`py app.py`) still works

## Deployment

After successful testing:

1. Merge PR #1 to main
2. Tag release v1.0.5
3. Build the final exe: `py -m PyInstaller --noconfirm AttendanceApp.spec`
4. Zip `dist\AttendanceApp\` folder → `AttendanceApp-v1.0.5.zip`
5. Upload to GitHub Releases
6. Users with v1.0.2-1.0.4 will see auto-update notification

## Questions?

- See [BUILD_AND_TEST.md](BUILD_AND_TEST.md) for detailed build instructions
- Check the PR description for technical architecture notes
- Review the code changes in `processing/ocr_processor.py` for OCR engine logic

## Report Issues

If you encounter problems during testing:

1. Check `crash.log` beside the exe (if app crashed)
2. Check console output or GUI log panel
3. Report on PR #1 with:
   - Windows version
   - Python version
   - Error message or crash log
   - Steps to reproduce
