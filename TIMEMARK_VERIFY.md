# Timemark Photo Verification Guide

## Overview

This application now extracts **Timemark Photo Codes** from attendance photos and supports official verification workflows when OCR timestamps are uncertain or missing.

## What is a Timemark Photo Code?

Timemark watermarks each photo with:
- **Visible timestamp overlay** (white text, bottom-left): Date and time in Vietnamese format (e.g., "16:23 / 26-09-10 Thứ năm")
- **Photo Code** (vertical text, right edge): A unique ~14-character alphanumeric identifier near "Timemark Verified" / "100% Chân thực"

The Photo Code can be used to verify the authenticity and retrieve the original metadata from Timemark's servers.

## When Photo Codes Are Used

The application extracts Photo Codes in these scenarios:

1. **OCR Success with Code**: Watermark timestamp is successfully read, and the Photo Code is logged for reference
2. **OCR Partial Failure**: Date or time is missing, but Photo Code is available — manual verification can recover the timestamp
3. **OCR Complete Failure**: Neither date nor time could be read, but Photo Code extraction succeeded — verification is the only way to recover the timestamp

### Exception Tab Markers

When OCR fails or is uncertain, the Photo Code appears in:
- **Note field**: "Cần verify Timemark với Photo Code: XXXXXXXXXX"
- **Photo Code column** (if displayed in reports/exports)
- **Log output** during processing

## Official Verification Methods

### ⚠️ Important: Use Official Timemark Tools Only

**Do NOT**:
- Reverse-engineer Timemark's internal APIs
- Scrape or probe private endpoints
- Automate verification without official support

**DO**:
- Use the official Timemark web verification portal
- Use the official Timemark mobile app
- Follow Timemark's published documentation

### Method 1: Web Verification Portal

1. Open the official verification page:
   ```
   https://verify.timemark.com
   ```

2. Enter the Photo Code manually or paste it from the application log/report

3. **OR** upload the original photo (Timemark will detect the embedded code automatically)

4. View the verified metadata:
   - Original timestamp (date + time)
   - Location data (if enabled)
   - Device information
   - Authenticity status

### Method 2: Timemark Mobile App

1. Open the Timemark app on iOS or Android
2. Navigate to **Verify Photo**
3. Enter the Photo Code or scan the photo
4. Review the verified timestamp

### Method 3: Teamspace Webhook (Future/Optional)

For organizations using Timemark Teamspace:
- Timemark can POST photo metadata to a webhook endpoint when photos are uploaded
- The webhook payload includes `photo_code` and `photo_timestamp`
- This integration requires a paid Teamspace plan and is **NOT** implemented in offline folder-based OCR workflows

**Note**: The current application processes photos from `Images\<employee>\*.jpg` folders offline. Webhook integration would apply only to future cloud-based attendance workflows where HR uploads photos directly to Timemark Teamspace.

## Application Features

### OCR Improvements (v1.0.6+)

The OCR pipeline now includes:

1. **Three-pass OCR strategy**:
   - **Pass 1**: Standard overlay ROI detection (yellow "Điểm danh" zones)
   - **Pass 2**: OpenCV contrast enhancement
   - **Pass 3**: White-on-bright preprocessing (invert, CLAHE, adaptive threshold, Otsu binarization)

2. **White-on-bright challenges solved**:
   - White Timemark text on white tiles
   - White text on bright blue DaviSoft fingerprint panels
   - White text on white receipts
   - Multiple preprocessing variants tested per ROI

3. **Photo Code extraction**:
   - Crops right-edge vertical strip (~15% of width)
   - Rotates 90° to horizontal
   - Tests 4 preprocessing variants (scale, invert, CLAHE, Otsu)
   - Filters out "Timemark Verified" / "© 100% Chân thực" decorative text
   - Extracts 12-16 alphanumeric characters

### Cache and Database

- Photo Codes are stored in `ocr_cache.json` alongside timestamps
- Cached codes persist across runs (no need to re-extract)
- Photo Code column appears in OCR logs and exception reports

### Reports and Exports

When OCR fails but a Photo Code exists:
- The "Note" field includes: `"Cần verify Timemark với Photo Code: XXXXXXXXXX"`
- HR can copy the code and verify manually
- After manual verification, HR can use the **Corrections** tab to input the correct timestamp

## Example Workflow

### Scenario: White Text on White Receipt

1. **OCR Run**:
   ```
   [BỎ QUA] Nguyễn Văn A/photo1.jpg: OCR có giờ 16:23, không thấy ngày — Cần verify Timemark với Photo Code: XTRDEY34BPT12
   ```

2. **HR Action**:
   - Copy Photo Code: `XTRDEY34BPT12`
   - Open https://verify.timemark.com
   - Paste code or upload `photo1.jpg`
   - Timemark returns: **2026-09-10 16:23**

3. **Correction**:
   - Open **Corrections** tab in the application
   - Find employee "Nguyễn Văn A" on 2026-09-10
   - Add manual punch: `16:23` (Timemark verified)
   - Save and re-run analysis (or lock month if final)

## Testing Photo Code Extraction

### Test with sample images

Place Timemark photos in:
```
Images\TestEmployee\photo1.jpg
Images\TestEmployee\photo2.jpg
```

Run in CLI mode:
```bash
AttendanceApp.exe --cli --images "Images\TestEmployee" --excel dummy.xlsx --out test_output
```

Check the log for:
```
[OK] TestEmployee/photo1.jpg: 2026-09-10 16:23:00 (Photo Code: XTRDEY34BPT12 (variant_0) | white-on-bright preprocess)
```

Or:
```
[BỎ QUA] TestEmployee/photo2.jpg: OCR có giờ 12:41, không thấy ngày — Cần verify Timemark với Photo Code: ABC123XYZ4567
```

### Verify extraction accuracy

1. Open the original photo in an image viewer
2. Zoom into the right edge near "Timemark Verified"
3. Compare the vertical text with the extracted Photo Code
4. If the code differs, it may indicate:
   - OCR read similar characters incorrectly (O/0, I/1, etc.)
   - Photo quality is too low (blur, compression artifacts)
   - Code is partially obscured

In such cases, **upload the photo** to verify.timemark.com instead of entering the code manually.

## Troubleshooting

### Photo Code Not Found

**Causes**:
- Photo is not from Timemark (e.g., regular camera photo, screenshot)
- Right edge is cropped or obscured
- Compression artifacts destroyed the code text
- Photo is too old (pre-Photo Code Timemark version)

**Solution**:
- Use EXIF timestamp as fallback (if camera date/time was correct)
- Manually verify with Timemark support

### Wrong Photo Code Extracted

**Causes**:
- OCR misread similar characters (0/O, 1/I, 5/S, 8/B, etc.)
- Decorative text confused the extractor

**Solution**:
- Upload the photo to verify.timemark.com (Timemark reads embedded metadata, not OCR)
- Do not manually correct the code — Timemark's verification is authoritative

### Verify Portal Returns "Not Found"

**Causes**:
- Photo Code was OCR-misread
- Photo was edited or stripped of metadata
- Photo was not uploaded to Timemark's servers (e.g., offline mode)

**Solution**:
- Upload the **original photo file** to verify.timemark.com
- Timemark embeds the code in EXIF/metadata, not just the visible watermark

## Official Timemark Resources

- **Verification Portal**: https://verify.timemark.com
- **Help Documentation**: https://help.timemark.com/en/app/authenticity/verify-photo/
- **Teamspace Webhook**: https://help.timemark.com/en/teamspace/webhooks/ (requires paid plan)

## Privacy and Security

- Photo Codes are **public identifiers** — they do not contain employee names, location, or sensitive data
- Verification only retrieves the timestamp and device info — HR must still match the photo to the employee (via folder name in this app)
- Timemark's privacy policy: https://timemark.com/privacy

## Future Enhancements (Not Implemented)

- **Button in UI**: "Copy Photo Code" next to failed OCR entries
- **Batch verification**: Export all failed OCR rows with Photo Codes to CSV for bulk verification
- **Teamspace webhook integration**: Auto-import verified timestamps from Timemark's POST endpoint (requires cloud-based architecture)

---

**Version**: 1.0.6+  
**Last Updated**: 2026-09-11
