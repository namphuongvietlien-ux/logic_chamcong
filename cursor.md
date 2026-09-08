Act as an Expert Python Developer and Data Analyst. I need to build a local desktop application to process employee time attendance data from fingerprint machines and timestamped photos. It must handle variable shifts (e.g., 8-hour vs 12-hour), dynamic lunch breaks, generate specific reports, and be packaged into a standalone Windows executable (.exe).

1. CONTEXT & FOLDER STRUCTURE:

/attendance_project

  ├── Employee_Master_Data.xlsx (Contains shift configs per employee)

  ├── CÔNG CHECK VÂN TAY THÁNG.xlsx (Raw data from fingerprint machine)

  ├── /Images (Folder containing timestamped photos)

```
   ├── /Nguyen Van A 

   │    ├── img1.jpg

   └── /Tran Thi B
```

1. CORE FEATURES REQUIRED:

Module 0: Master Data Loading

- Read "Employee_Master_Data.xlsx". Expected columns: [Employee Name, Standard Shift (hours), Shift Start, Shift End, Lunch Start, Lunch End].
- Store this configuration in a dictionary or dataframe for fast lookup.

Module 1: Excel Data Processing (Fingerprint)

- Read "CÔNG CHECK VÂN TAY THÁNG.xlsx". Extract Check-in and Check-out times per employee per day.

Module 2: Image OCR Processing (Timemark Photos)

- Iterate through the "/Images" folder (subfolders are employee names).
- Use EasyOCR (or pytesseract) to scan images, extract the date and time printed on the photo, and store them in a dataframe.

Module 3: Data Merging & Smart Lunch Deduction Logic

- Merge fingerprint and OCR data based on Employee Name and Date.
- Final Check-in = EARLIEST time (fingerprint vs photo).
- Final Check-out = LATEST time (fingerprint vs photo).
- SMART LUNCH DEDUCTION ALGORITHM: 
  - Look up the employee's Lunch Start and Lunch End from the Master Data.
  - Calculate the overlap between their Actual Working Period (Final Check-in to Final Check-out) and their Configured Lunch Break (Lunch Start to Lunch End) for that day.
  - Actual Work Hours = (Final Check-out - Final Check-in) minus the Overlapping Lunch Duration.
  - Calculate Standardized Workday = Actual Work Hours / Standard Shift hours (e.g., if Actual is 8h and Standard is 8h, it's 1 workday. If Standard is 12h, it's 0.66 workday).

1. OUTPUT REPORT STRUCTURES (Module 4):

Report 1: Detailed Daily Attendance

Columns: Date | Employee Name | Standard Shift (h) | Final Check-In | Final Check-Out | Deducted Lunch Time (h) | Actual Work Hours | Standardized Workday (Day count) | Remarks

Report 2: Monthly Summary

Columns: Employee Name | Total Standardized Workdays | Total Late Arrivals (Count) | Total Missing Punches (Count) | Remarks

IMPORTANT LOCALE NOTE: If writing any raw Excel formulas via openpyxl/pandas to the output files, use semicolon (;) as the argument separator instead of a comma (,) because the target system uses (;) for Excel functions.

1. APP UI & PACKAGING TO .EXE (Module 5):

- UI: Build a modern GUI using CustomTkinter.
  - Buttons to select: Master Data file, Raw Excel file, and Images folder.
  - A "Run Analysis" button.
  - A Text Log widget to show processing steps and OCR progress.
- Packaging: Provide the exact PyInstaller setup, spec file configurations, and terminal commands to package this app into a standalone Windows .exe. Ensure the spec file includes EasyOCR/Tesseract models/binaries.

1. TECH STACK: CustomTkinter, pandas, openpyxl, cv2, datetime, EasyOCR.
2. YOUR TASK:

- Write the complete, robust Python code ([app.py](http://app.py)).
- Implement precise datetime overlap logic for the smart lunch deduction.
- Provide requirements.txt.
- Provide step-by-step instructions for running and compiling to .exe. Ensure errors (like failing to OCR a blurry image) are logged in the UI but do not crash the app.

Act as an Expert Python Developer and HR Data Analyst. I need to update my local desktop time attendance application. Please rewrite the logic to include a highly formatted individual report generated via openpyxl, with automatic highlighting for missing time punches.

1. CONTEXT:

Sources:

- Raw Fingerprint Excel ("CÔNG CHECK VÂN TAY THÁNG.xlsx") containing multiple punches.
- OCR Timestamped Photos (from "/Images" folder).
- Master Data Excel ("Employee_Master_Data.xlsx").

1. NEW MASTER DATA STRUCTURE:

Expected columns: [Employee ID, Employee Name, Department, Standard Shift Hours, Shift Start, Shift End, Lunch Duration Hours]

(e.g., ID: 00192, Name: Lê Nam Phương, Dept: Sales, Shift Hours: 8, Start: 08:00, End: 17:00, Lunch: 1.0)

1. SMART LUNCH & TIME LOGIC:

For each employee per date, combine/sort all punches (fingerprint + OCR).

- Map punches to max 4 slots: In1, Out1, In2, Out2.
- Late Minutes (Trễ): Max(0, In1 - Shift Start).
- Early Minutes (Sớm): Max(0, Shift End - Last Out).
- Actual Work Hours: 
  - If punches span across midday (duration > 5 hours), deduct 'Lunch Duration Hours'.
  - Otherwise, do not deduct lunch.
- Workday Count (Công): 
  - Work Ratio = Actual Work Hours / Standard Shift Hours.
  - Ratio >= 0.85 -> 1.0 (Full day)
  - 0.45 <= Ratio < 0.85 -> 0.5 (Half day)
  - Ratio < 0.45 -> 0

1. OUTPUT REPORTS (EXCEL):

Generate 3 reports.

Report 1: Daily Raw Flat Table

Report 2: Monthly Summary ("CCONG TH")

REPORT 3: INDIVIDUAL DETAILED ATTENDANCE SHEET (CRITICAL REQUIREMENT)

Create a new Excel workbook (e.g., "Chi_Tiet_Cham_Cong.xlsx"). Create one Sheet PER EMPLOYEE named by their Employee Name.

Use `openpyxl` to strictly format each sheet to match this exact template structure:

- Row 1: "BẢNG CHI TIẾT CHẤM CÔNG" (Merged A to O, Font size 16, Bold, Centered).
- Row 2: "Mã nhân viên: [ID]" (Cols A-C) | "Tên nhân viên: [Name]" (Cols D-J) | "Phòng ban: [Dept]" (Cols K-O). Background Fill: Yellow (#FFFF00), Bold.
- Row 3: "Tổng giờ" | [Total Hours] | "Số lần trễ" | "Số phút trễ" | [Total Late Mins]
- Row 4: "Tổng công" | [Total Workdays] | "Số lần sớm" | "Số phút sớm" | [Total Early Mins]
- Row 5: "Tăng ca" | 0 | "Vắng KP" | "Vắng CP" | 0
- Row 7 (Header 1): A-B empty, C-D merged as "1", E-F merged as "2", G-O merged as "Chi tiết". Centered, Bold.
- Row 8 (Header 2): Ngày (Date) | Thứ (Day of week) | Vào (In1) | Ra (Out1) | Vào (In2) | Ra (Out2) | Trễ (Late mins) | Sớm (Early mins) | Về trễ | Giờ (Actual Hours) | Công (Workday)
- Data Rows (Row 9 onwards): Fill daily data. Format times as HH:MM.
- HIGHLIGHT MISSING PUNCHES: If there is data for a specific day but an expected punch (e.g., In1 or Out1) is missing/blank, use openpyxl.styles.PatternFill to fill that specific empty cell with a light red color (hex #FF9999) to alert the user.
- Footer Row (After last data row): "TỔNG CỘNG: [Total Workdays] NGÀY + [Total Hours] GIỜ" (Merged, Bold).
- Styling: Apply thin borders to all data and header cells. Auto-adjust column widths for readability.

1. TECH STACK & PACKAGING:

- UI: CustomTkinter.
- Processing: pandas, openpyxl, EasyOCR.
- Package to standalone Windows .exe using PyInstaller (ensure EasyOCR models are in the .spec).
- Ensure exact formatting for Report 3 using openpyxl.styles (PatternFill, Border, Alignment, Font).

1. YOUR TASK:

Generate the complete Python code implementing this logic. Ensure robust error handling for missing punches or failed OCR.