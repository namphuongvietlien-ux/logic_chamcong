# HR logic prompts — TAS / chấm công

Dùng file này làm nguồn sự thật khi làm việc trên máy khác (sau `git pull`).

**Lệnh gợi ý cho Cursor ngày mai**

```
@hr_logic_prompts.md Hãy đọc file này để nắm lại toàn bộ logic thuật toán đã chốt, sau đó áp dụng vào codebase hiện tại (không viết lại từ đầu, không phá các rule bên dưới).
```

Không hardcode nghỉ trưa 12:00–13:00. Giờ công = cộng từng cặp In–Out. Số lần quẹt lẻ → `-1.0` và tab Ngoại lệ; không đoán mốc thiếu. Không tự thêm giờ. Ô Excel dạng văn bản `'7:00` phải đọc đúng thành 07:00.

---

## 0. Đọc giờ từ Excel — có cơ sở, không bịa (đã chốt)

**File:** `processing/utils.py` → `parse_time_value`, `normalize_clock_text`

- Chỉ lấy mốc có trong file nguồn (vân tay / OCR / ô HR sửa). Ô trống, `-`, số nguyên kiểu `529` → bỏ, **không** điền 08:00.
- Excel hay lưu giờ kiểu văn bản có dấu nháy: `'7:00`, `'07:00`, `'7.00`. Bóc apostrophe / dấu ngoặc kép rồi parse. `7:00` không pad 0 vẫn là 07:00.
- Nghỉ trưa: chỉ trừ khi master có `lunch_duration_hours` > 0. Không tự trừ 1 giờ khi master để trống.

---

## 1. Thuật toán tính chẵn / lẻ (đã chốt — đang chạy)

**File:** `processing/sessions.py` → `calculate_smart_work_hours`, `hours_from_datetimes`, `missing_punch_status`  
**Gọi từ:** `processing/merger.py` (ưu tiên `punch_datetimes` đủ mốc, không chỉ 4 cột K9)

### Gom mốc trước khi tính

1. Dedupe đúng phút (`set`), sort sớm → muộn.
2. Gom nhiễu: hai mốc cách nhau **< 5 phút** = một sự kiện.
   - Cụm đầu ngày = Check-In → giữ **sớm nhất**.
   - Cụm cuối ngày = Check-Out → giữ **muộn nhất**.
   - Đúng 5 phút = hai mốc riêng.
3. Ngày lịch không trộn. Tăng ca qua đêm **chỉ** khi HR bật “Tăng ca qua đêm (+1 ngày)”.

### Cộng cặp In–Out

Hằng số: `MAX_SHIFT_HOURS = 16`, `MIN_WORK_MINUTES = 15`, `LUNCH_IF_SPAN_HOURS = 6`.

```python
# Ý chính (xem implementation đầy đủ trong processing/sessions.py)

def calculate_smart_work_hours(stamps: list[datetime], shift_lunch_hours: float) -> float:
    """Chẵn: cộng [0,1] + [2,3] + …  Lẻ hoặc < 2 mốc: -1.0 (HR xử lý)."""
    stamps = unique_sorted(stamps)
    n = len(stamps)
    if n < 2 or n % 2 == 1:
        return -1.0
    total = 0.0
    for i in range(0, n, 2):
        pair_h = _pair_span_hours(stamps[i], stamps[i + 1])  # OUT < IN → +1 ngày
        if pair_h > 16.0:          # quên quẹt ra / bẫy xuyên ngày
            return -1.0
        if pair_h < 15 / 60:       # quẹt rác — bỏ qua, không báo lỗi
            continue
        total += pair_h
    if n == 2 and total > 6.0:     # xuyên ca, không quẹt nghỉ → trừ định mức nghỉ
        total -= shift_lunch_hours
    return max(0.0, round(total, 2))
```

| Số mốc | Hành vi |
|---|---|
| 0 hoặc 1 | `-1.0` → UI: Thiếu Giờ Vào / Thiếu Giờ Ra |
| 2, 4, 6, 8… | Cộng từng cặp. 4+ lần **không** trừ thêm nghỉ (khoảng giữa cặp đã là ra ngoài / nghỉ). |
| 3, 5, 7… | `-1.0` → **Thiếu mốc quẹt**, tab Ngoại lệ. Không suy diễn. |
| Một cặp > 16h | `-1.0` → **Quên quẹt ra (vượt 16 giờ)** |
| Một cặp < 15 phút | Không cộng giờ, không đưa ngoại lệ |

`hours_from_datetimes` đổi `-1.0` thành **0 giờ** trên báo cáo; `missing_punch` trên dòng mới là cờ Ngoại lệ.

K9 chỉ có 4 cột; giờ công phải lấy **toàn bộ** `punch_datetimes` (5–6 lần quẹt việc riêng vẫn tính đủ cặp).

**Không** tính giờ = last − first rồi trừ cả cục `lunch_duration`.

---

## 2. Thuật toán giao thoa giờ nghỉ (overlap)

Dùng khi cần trừ đúng phần ca **giao** với khung nghỉ (ví dụ nghỉ 17:00–20:00), không trừ cả 3 giờ nếu người đó tan lúc 16:00.

Helper còn trong `processing/cong_rules.py` → `lunch_overlap_hours`. **Luồng giờ công chính hiện tại là cặp In–Out** (mục 1): 4 mốc đã loại khoảng nghỉ; 2 mốc xuyên ca thì trừ `shift_lunch_hours`.

Giữ overlap khi chỉnh ca chỉ có 1 cặp In/Out và master có `lunch_start` / `lunch_end`:

```python
def calculate_work_hours(actual_in, actual_out, break_start, break_end) -> float:
    if not actual_in or not actual_out:
        return 0.0
    if actual_out < actual_in:
        actual_out += timedelta(days=1)
    if break_end < break_start:
        break_end += timedelta(days=1)
    presence = (actual_out - actual_in).total_seconds()
    overlap_start = max(actual_in, break_start)
    overlap_end = min(actual_out, break_end)
    deduct = (overlap_end - overlap_start).total_seconds() if overlap_start < overlap_end else 0
    return round((presence - deduct) / 3600, 2)
```

Ví dụ: 08:00–22:00, nghỉ 17:00–20:00 → trừ 3h giao, net 11h. 08:00–16:00 cùng khung nghỉ → giao 0, net 8h.

Không hardcode 12:00–13:00. Lấy khung nghỉ từ master (`lunch_start`, `lunch_end`, `lunch_duration_hours`).

---

## 3. DatePicker chống taskbar

**File:** `date_picker.py` → `DatePickerField`  
**Dùng cho:** Ngày vào, Ngày chính thức, phép, ngày lễ.

- Entry + nút 📅, popup `tkcalendar.Calendar` trong `CTkToplevel`, `date_pattern='y-mm-dd'`.
- Nếu `y + h + 280 > screen_height` → mở **phía trên** field; không thì phía dưới. Clamp X theo `screen_width`.
- Đóng: X, **Hủy**, Escape. `grab_release()` rồi `destroy()`.
- `bind(..., add=True)` — CustomTkinter từ chối `add=None`.
- Locale `vi_VN` nếu babel có; fallback không locale.

Phụ thuộc: `tkcalendar==1.6.1`, `babel` trong `requirements.txt` và hiddenimports PyInstaller.

---

## 4. PyInstaller

### Onedir (khuyến nghị máy có EasyOCR/torch — khởi động nhanh)

`AttendanceApp.spec` + `build_exe.bat` hoặc:

```text
.\.venv\Scripts\python.exe build.py --onedir
```

Kết quả: `dist\AttendanceApp\` — **phải copy cả thư mục**, không chỉ file `.exe`. `console=False`.

Bắt buộc trong spec:

- `datas`: cả thư mục `customtkinter` (theme `.json` / font), `babel`, template `Template_Cham_Cong.xlsx`, `K9 08.2026.xlsx`.
- `hiddenimports`: `pandas`, `openpyxl`, `tkcalendar`, `babel.numbers`, `sqlite3`, `date_picker`, toàn bộ `processing.*`.
- EasyOCR/torch: `collect_all`; model `~/.EasyOCR` pack vào `.EasyOCR`.

### Onefile (máy khác không cần `_internal`)

```text
.\.venv\Scripts\python.exe build.py
```

`build.py` ghi `AttendanceApp.onefile.spec` (đường dẫn customtkinter động) rồi gọi PyInstaller `--onefile --windowed`. Lần mở đầu giải nén tạm, có thể chậm vì torch.

### SQLite — không ghi vào `_MEIPASS`

`processing/resources.py`: `writable_dir()` = thư mục cạnh exe (`data/tas.db`). Template đọc từ `_MEIPASS`; DB luôn cạnh exe / project.

Công thức Excel: dấu **chấm phẩy** `;` (`processing/excel_locale.py` → `excel_formula()`). Không dùng dấu phẩy.

---

## 5. Rule nghiệp vụ khác (đừng phá khi sửa app)

**Công / OT**

- Công = Actual / Standard Shift: ≥ 0.85 → 1.0; ≥ 0.45 → 0.5; không thì 0.
- OT = max(0, hours − standard).
- Thiếu giờ ra của một cặp → giờ cặp đó = 0.

**Phép**

- `official_start_date`: cùng năm → `(Base/12)*(12-Month+1)`; năm trước → full Base; năm sau → 0.
- Total = Prorated_Base + Seniority(`join_date`) + Carryover − Used(year).
- Phép tồn = tồn năm trước. Còn lại năm hiện tại **tính**, không paste Excel “còn lại hiện tại” vào Tồn.
- Tab Leave: `ensure_loaded` / `invalidate()`, không `refresh()` full mỗi lần đổi tab.

**Đồng bộ Excel**

- Không tính lại tháng đã khóa / lịch sử công khi sync Excel.

**Chạy dev**

```text
.\.venv\Scripts\python.exe app.py
```

Không dùng exe cũ trong `dist\` khi đang sửa logic.

---

## 6. Prompt gốc (giữ để tái sử dụng)

### 6.1 Pair-wise + ngoại lệ

Act as a Senior HR Tech Developer. Upgrade `calculate_smart_work_hours` to even/odd pair-wise accumulation. Even (2, 4, 6…): sum pairs `[0,1]`, `[2,3]`, … If exactly 2 punches and total > 6h, subtract `shift_lunch_hours`. Odd (3, 5, 7…): return `-1.0` so the UI sends the row to the Exception tab. Do not guess the missing punch. Then add MAX 16h per pair (cross-day forgotten checkout) and skip pairs under 15 minutes as junk.

### 6.2 Overlap nghỉ ca

Act as a Senior Python Developer. Replace lump lunch deduction with interval intersection `calculate_work_hours(actual_in, actual_out, break_start, break_end)`. Overnight: if out < in, add 1 day. Deduct only the overlap with the configured break window (e.g. 17:00–20:00). Never hardcode 12:00–13:00.

### 6.3 DatePicker

Smart position: if `y + h + 280 > screen_height`, open the calendar above the field; else below. Clamp X. Close with X, Hủy, Escape. `grab_release()` then `destroy()`. CTk `bind` must use `add=True`.

### 6.4 Packaging

Generate a PyInstaller spec/`build.py` that bundles CustomTkinter assets (locate the package folder into `datas`), hiddenimports `pandas`, `openpyxl`, `tkcalendar`, `babel.numbers`, `sqlite3`, windowed/no console. Onefile is optional; onedir is the EasyOCR-friendly default. Never write SQLite into `_MEIPASS`.
