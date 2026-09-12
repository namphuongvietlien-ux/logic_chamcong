# AttendanceApp v1.0.6 Release Notes

**Release Date**: 2026-09-12  
**Status**: Ready for Windows build and deployment

## 🎉 New Features

### 1. Improved Timemark OCR for White-on-Bright Watermarks

**Problem Solved**: White Timemark watermarks on bright backgrounds (white tiles, white receipts, blue DaviSoft panels) were frequently misread or failed completely.

**Solution**: Three-pass OCR strategy
- Pass 1: Standard yellow overlay detection
- Pass 2: OpenCV contrast enhancement
- **NEW Pass 3**: White-on-bright preprocessing with 5 variants (invert, CLAHE, Otsu binary, adaptive threshold)

**Benefits**:
- Higher success rate for difficult lighting conditions
- Fewer false negatives from bright backgrounds
- Better handling of real-world Timemark photo scenarios

**Files Changed**: `processing/ocr_processor.py`

---

### 2. Timemark Photo Code Extraction

**What is a Photo Code?**: Unique ~14-character alphanumeric identifier embedded by Timemark in vertical text on the right edge of photos.

**Features**:
- Automatic extraction from right-edge vertical text
- Persisted in `ocr_cache.json` alongside timestamps
- Displayed in OCR logs and exception reports
- Supports official verification workflow via https://verify.timemark.com

**When to Use**:
- OCR fails to read date/time from watermark
- Low-quality or compressed photos
- Verification needed for compliance/audits

**Official Verification Flow**:
1. Copy Photo Code from OCR log (e.g., `XTRDEY34BPT12`)
2. Open https://verify.timemark.com
3. Paste code or upload photo
4. Timemark returns verified timestamp
5. Use Corrections tab to manually add verified punch

**Documentation**: See `TIMEMARK_VERIFY.md` for complete guide

**Files Changed**: 
- `processing/ocr_processor.py` (extraction logic)
- `processing/timemark_utils.py` (helper utilities)
- `TIMEMARK_VERIFY.md` (documentation)
- `BUILD_AND_TEST.md` (updated testing guide)

---

### 3. Smart Lunch Deduction Logic (8h vs 12h Shifts)

**Problem Solved**: 
- 8h shift employees unfairly penalized when working straight through (no explicit lunch break)
- 12h shift employees not enforcing mandatory lunch break clocking for safety/compliance

**Solution**: Shift-aware lunch deduction

**8h Shifts (standard_shift_hours ≤ 10h)**: Flexible lunch
- If total hours ≥ standard_shift_hours → counts as full day (1.0 công)
- No mandatory lunch deduction if enough hours worked
- Example: 08:00-17:00 (9h straight, no In2/Out2) → 1.0 công ✓

**12h Shifts (standard_shift_hours > 10h)**: Mandatory lunch
- MUST have lunch_duration_hours configured
- MUST clock lunch break (In2/Out2)
- Only 2 punches without lunch → returns -1.0 (flagged for HR review)
- Example: 07:00-19:00 (12h straight, no lunch) → -1.0 (needs 補充) ⚠️

**Benefits**:
- **8h shifts**: More forgiving, reduces false "CHƯA ĐẠT" flags
- **12h shifts**: Enforces safety compliance
- **HR**: Clear distinction between shift types

**Files Changed**:
- `processing/sessions.py` (core logic)
- `processing/cong_rules.py` (helper functions)
- `processing/merger.py`, `processing/corrections.py` (integration)

---

### 4. Shift Duration Filter ("Ca" Dropdown)

**Location**: Corrections Tab → Sửa chi tiết → Toolbar row 2

**Options**:
- **Tất cả ca**: Show all shifts (default)
- **Ca 8 tiếng**: Show only shifts ≤ 10h
- **Ca 12 tiếng**: Show only shifts > 10h

**Use Cases**:
- Quick review of specific shift type
- Focus on 12h shifts for lunch break compliance
- Separate analysis of 8h vs 12h shift patterns

**Files Changed**: `correction_ui.py`

---

### 5. Yellow Highlighting for Lunch 補充

**Visual Indicator**: Rows with **yellow background (#FFF9C4)** when:
- Shift is 12h (standard_shift_hours > 10.0) AND
- Only 2 punches (no In2/Out2 for lunch break) AND
- Did not reach target hours AND
- Has lunch_duration_hours configured

**Benefits**:
- Instant visual feedback for HR staff
- Easy identification of records needing lunch time 補充
- Reduces manual review time

**Files Changed**: `correction_ui.py`

---

## 🔧 Technical Details

### Hard Constraints Preserved ✓

- ✅ Pair-wise accumulation logic unchanged
- ✅ No hardcoded 12:00-13:00 lunch breaks
- ✅ Exception handling preserved (-1.0 for odd punches, etc.)
- ✅ Overnight logic unchanged
- ✅ All HR rules from `hr_logic_prompts.md` intact
- ✅ Unicode-safe image loading preserved
- ✅ RapidOCR frozen path preserved

### Backward Compatibility

All changes are backward compatible:
- Photo Code is optional (existing cache works without it)
- Smart lunch logic defaults to 8h behavior for unspecified shifts
- New UI elements don't affect existing workflows

---

## 📋 Testing Checklist (Windows Required)

### Timemark OCR Tests

- [ ] Test white watermark on white tile background
- [ ] Test white watermark on white receipt
- [ ] Test white watermark on blue DaviSoft panel
- [ ] Verify Photo Code extraction from right edge
- [ ] Test official verification at https://verify.timemark.com

### Smart Lunch Logic Tests

**8h Shift Tests**:
- [ ] 2 punches, 9h worked → Should be 1.0 công (no lunch deduction)
- [ ] 2 punches, 7h worked → Should be 0.5 công (not enough hours)
- [ ] 4 punches with lunch → Should calculate correctly

**12h Shift Tests**:
- [ ] 2 punches, 12h straight → Should flag -1.0 + yellow highlight
- [ ] 4 punches with lunch → Should calculate correctly (1.0 công)
- [ ] 2 punches, enough hours, no lunch config → Should work normally

### UI Tests

- [ ] "Ca" filter: "Ca 8 tiếng" shows only ≤10h shifts
- [ ] "Ca" filter: "Ca 12 tiếng" shows only >10h shifts
- [ ] Yellow highlighting appears for 12h shifts needing lunch 補充
- [ ] Yellow + filter combination works correctly

---

## 🚀 Deployment Instructions

### 1. Build on Windows

```bash
# On Windows machine with Python 3.10-3.14
py -m PyInstaller --noconfirm AttendanceApp.spec
```

### 2. Test the Build

```bash
cd dist\AttendanceApp
AttendanceApp.exe --cli --excel test.xlsx --images "Images" --out output
```

### 3. Create Release Package

```bash
# Zip the entire dist/AttendanceApp/ folder
Compress-Archive -Path "dist\AttendanceApp\*" -DestinationPath "AttendanceApp-v1.0.6.zip"
```

### 4. GitHub Release

1. Go to https://github.com/namphuongvietlien-ux/logic_chamcong/releases
2. Create new release: Tag `v1.0.6`
3. Title: `AttendanceApp v1.0.6 - Timemark OCR + Smart Lunch Logic`
4. Upload `AttendanceApp-v1.0.6.zip`
5. Copy release notes from this document
6. Publish release

### 5. Auto-Updater

Users running v1.0.5 will see update notification on next app launch. They can click "Cập nhật" to download and install v1.0.6 automatically.

---

## 📝 User Communication

### HR Staff Training

**New "Ca" Filter**:
- Use "Ca 8 tiếng" to review standard shifts
- Use "Ca 12 tiếng" to check lunch break compliance
- Yellow rows = need lunch time 補充 (click row to add In2/Out2)

**Photo Code Workflow**:
- When OCR fails, check log for "Photo Code: XXXXXXXX"
- Copy code → Open verify.timemark.com → Paste or upload photo
- Timemark returns verified time → Add manually in Corrections tab

### Known Issues / Limitations

- Timemark Photo Code extraction requires clear right-edge visibility
- Official verification requires internet connection
- Smart lunch logic assumes shifts ≤10h = 8h type, >10h = 12h type

---

## 🔗 Pull Requests

- **PR #2**: Timemark OCR improvements + Photo Code extraction
- **PR #3**: Smart lunch deduction logic + shift filter + yellow highlighting

---

## 👥 Contributors

- Cloud Agent (Claude Sonnet 4.5)
- Based on requirements from AttendanceApp / logic_chamcong maintainers

---

## 📄 License

Same as main project license.

---

**Version**: 1.0.6  
**Build Date**: 2026-09-12  
**Ready for**: Windows build and deployment
