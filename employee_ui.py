"""Tab Nhân viên: SQLite roster CRUD + import from Excel."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from datetime import date

from date_picker import DatePickerField
from processing.database import delete_employee, list_employees, sync_employees_from_excel, upsert_employee
from processing.leave import leave_period_labels, prorated_base_leave, seniority_leave, used_leave
from processing.utils import net_shift_hours, shift_window_hours
from tree_theme import (
    SEARCH_PLACEHOLDER,
    apply_tree_style,
    bind_hover,
    employee_search_match,
    replace_tree_rows,
    stripe_tags,
)

SHIFT_HOURS_TOLERANCE = 0.05
SHIFT_WINDOW_TOLERANCE = 0.25


def _ask_ctk_warning(master, title: str, message: str, *, confirm: bool = False) -> bool:
    """CTkMessagebox warning; confirm=True asks Lưu/Hủy. Falls back to tk dialogs."""
    try:
        from CTkMessagebox import CTkMessagebox

        if confirm:
            box = CTkMessagebox(
                master=master,
                title=title,
                message=message,
                icon="warning",
                option_1="Hủy",
                option_2="Lưu",
            )
            return str(box.get() or "") == "Lưu"
        CTkMessagebox(
            master=master,
            title=title,
            message=message,
            icon="warning",
            option_1="Đóng",
        )
        return True
    except Exception:
        if confirm:
            return bool(messagebox.askyesno(title, message))
        messagebox.showwarning(title, message)
        return True


class EmployeePanel(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected_id: int | None = None
        self._rows: list[dict] = []
        self.search_var = ctk.StringVar(value="")
        self._loaded_shift_hours = 8.0
        self._suppress_shift_events = False
        self._data_loaded = False
        self._build()

    def invalidate(self) -> None:
        self._data_loaded = False

    def ensure_loaded(self) -> None:
        if not self._data_loaded:
            self.refresh()

    def _build(self) -> None:
        ctk.CTkLabel(
            self,
            text=(
                "Danh sách nhân viên lưu trong CSDL nội bộ (không cần file Excel khi chấm công). "
                + leave_period_labels()["hint"]
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", padx=4, pady=(0, 8))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            tools,
            text="Nhập dữ liệu từ Excel",
            width=200,
            command=self._sync_from_excel,
        ).pack(side="left")
        ctk.CTkButton(tools, text="Làm mới", width=100, command=self.refresh).pack(side="left", padx=8)
        self.count_var = ctk.StringVar(value="")
        ctk.CTkLabel(tools, textvariable=self.count_var, text_color=("#2B6CB0", "#90CDF4")).pack(side="right")

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.pack(fill="x", pady=(0, 8))
        self.search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self.search_var,
            placeholder_text=SEARCH_PLACEHOLDER,
            height=36,
        )
        self.search_entry.pack(fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self._on_search)

        table_wrap = ctk.CTkFrame(self, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = (
            "code",
            "name",
            "dept",
            "shift",
            "start",
            "end",
            "lunch",
            "join",
            "official",
            "base",
            "carry",
            "active",
        )
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=14)
        headings = {
            "code": ("Mã NV", 70),
            "name": ("Họ và tên", 180),
            "dept": ("Phòng ban", 110),
            "shift": ("Giờ ca", 70),
            "start": ("Vào ca", 70),
            "end": ("Tan ca", 70),
            "lunch": ("Nghỉ trưa", 70),
            "join": ("Ngày vào", 100),
            "official": ("Ngày chính thức", 110),
            "base": ("Phép gốc", 70),
            "carry": (leave_period_labels()["carry"], 90),
            "active": ("Hiệu lực", 70),
        }
        apply_tree_style(self.tree)
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="center")
            self.tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        bind_hover(self.tree)

        form = ctk.CTkFrame(self, corner_radius=12)
        form.pack(fill="x", pady=(0, 8))
        self.id_var = ctk.StringVar()
        self.name_var = ctk.StringVar()
        self.dept_var = ctk.StringVar()
        self.shift_var = ctk.StringVar(value="8")
        self.start_var = ctk.StringVar(value="08:00")
        self.end_var = ctk.StringVar(value="17:00")
        self.lunch_var = ctk.StringVar(value="0")
        self.join_var = ctk.StringVar()
        self.official_var = ctk.StringVar()
        self.base_var = ctk.StringVar(value="12")
        self.carry_var = ctk.StringVar(value="0")
        self.active_var = ctk.BooleanVar(value=True)
        self.shift_hint_var = ctk.StringVar(value="")
        self.leave_hint_var = ctk.StringVar(value="")
        row1 = (
            ("Mã NV", self.id_var, 90),
            ("Họ và tên", self.name_var, 180),
            ("Phòng ban", self.dept_var, 130),
            ("Giờ ca", self.shift_var, 70),
            ("Vào ca", self.start_var, 70),
            ("Tan ca", self.end_var, 70),
            ("Nghỉ trưa", self.lunch_var, 70),
        )
        row2 = (
            ("Ngày vào", self.join_var, 110),
            ("Ngày chính thức", self.official_var, 120),
            ("Phép gốc / năm", self.base_var, 80),
            (leave_period_labels()["carry"], self.carry_var, 90),
        )
        self._shift_entries: dict[str, ctk.CTkEntry] = {}
        for col, (label, var, width) in enumerate(row1):
            ctk.CTkLabel(form, text=label).grid(row=0, column=col, padx=8, pady=(10, 0), sticky="w")
            entry = ctk.CTkEntry(form, textvariable=var, width=width)
            entry.grid(row=1, column=col, padx=8, pady=(0, 6))
            if var is self.shift_var:
                entry.bind("<FocusOut>", self._on_shift_standard_focus)
                self._shift_entries["shift"] = entry
            elif var in (self.start_var, self.end_var, self.lunch_var):
                entry.bind("<FocusOut>", self._on_shift_times_focus)
        for col, (label, var, width) in enumerate(row2):
            ctk.CTkLabel(form, text=label).grid(row=2, column=col, padx=8, pady=(4, 0), sticky="w")
            if var in (self.join_var, self.official_var):
                DatePickerField(form, variable=var, width=width).grid(
                    row=3, column=col, padx=8, pady=(0, 8), sticky="w"
                )
            else:
                ctk.CTkEntry(form, textvariable=var, width=width).grid(row=3, column=col, padx=8, pady=(0, 8))
        ctk.CTkCheckBox(form, text="Đang làm việc", variable=self.active_var).grid(
            row=3, column=len(row2), padx=8, pady=(0, 8), sticky="w"
        )
        ctk.CTkLabel(
            form,
            textvariable=self.shift_hint_var,
            text_color=("#2B6CB0", "#90CDF4"),
        ).grid(row=3, column=len(row2) + 1, columnspan=3, padx=8, pady=(0, 8), sticky="w")
        ctk.CTkLabel(
            form,
            textvariable=self.leave_hint_var,
            text_color=("#2B6CB0", "#90CDF4"),
            wraplength=1100,
            justify="left",
            anchor="w",
        ).grid(row=4, column=0, columnspan=8, padx=8, pady=(0, 4), sticky="w")
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=5, column=0, columnspan=8, padx=8, pady=(0, 12), sticky="w")
        ctk.CTkButton(buttons, text="Thêm mới / lưu", width=140, command=self._save).pack(side="left")
        ctk.CTkButton(buttons, text="Xóa dòng", width=100, fg_color="#C53030", command=self._delete).pack(
            side="left", padx=8
        )
        ctk.CTkButton(buttons, text="Xóa form", width=100, command=self._clear).pack(side="left")
        for var in (self.shift_var, self.start_var, self.end_var, self.lunch_var):
            var.trace_add("write", self._on_shift_fields_changed)
        for var in (self.join_var, self.official_var, self.base_var, self.carry_var):
            var.trace_add("write", self._on_leave_fields_changed)
        self._refresh_shift_hint()
        self._refresh_leave_hint()

    def refresh(self) -> None:
        self._rows = list_employees(active_only=False)
        self._render_rows()
        self._data_loaded = True

    def _on_search(self, _event=None) -> None:
        self._render_rows()

    def _render_rows(self) -> None:
        query = self.search_var.get()
        rows: list[tuple[str, tuple, tuple]] = []
        visible = 0
        for item in self._rows:
            code = item.get("employee_id") or ""
            name = item.get("employee_name") or ""
            if not employee_search_match(query, code, name):
                continue
            rows.append(
                (
                    str(item["id"]),
                    (
                        code,
                        name,
                        item.get("department") or "",
                        item.get("standard_shift_hours") or "",
                        item.get("shift_start") or "",
                        item.get("shift_end") or "",
                        item.get("lunch_duration_hours") or 0,
                        item.get("join_date") or "",
                        item.get("official_start_date") or item.get("join_date") or "",
                        item.get("base_leave") or 12,
                        item.get("carryover_leave") or 0,
                        "Có" if item.get("active") else "Ẩn",
                    ),
                    stripe_tags(visible),
                )
            )
            visible += 1
        replace_tree_rows(self.tree, rows)
        total = len(self._rows)
        if str(query or "").strip():
            self.count_var.set(f"{visible}/{total} nhân viên")
        else:
            self.count_var.set(f"{total} nhân viên trong CSDL")

    def _row_by_id(self, pk: int) -> dict | None:
        for item in self._rows:
            if int(item.get("id") or 0) == int(pk):
                return item
        return None

    def _on_select(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        item = self._row_by_id(int(iid))
        if item is None:
            return
        self._selected_id = int(iid)
        self._suppress_shift_events = True
        try:
            self.id_var.set(item.get("employee_id") or "")
            self.name_var.set(item.get("employee_name") or "")
            self.dept_var.set(item.get("department") or "")
            self.shift_var.set(str(item.get("standard_shift_hours") or 8))
            self.start_var.set(item.get("shift_start") or "")
            self.end_var.set(item.get("shift_end") or "")
            self.lunch_var.set(str(item.get("lunch_duration_hours") or 0))
            self.join_var.set(str(item.get("join_date") or ""))
            self.official_var.set(str(item.get("official_start_date") or item.get("join_date") or ""))
            self.base_var.set(str(item.get("base_leave") or 12))
            self.carry_var.set(str(item.get("carryover_leave") or 0))
            self.active_var.set(bool(item.get("active")))
            try:
                self._loaded_shift_hours = float(item.get("standard_shift_hours") or 8)
            except (TypeError, ValueError):
                self._loaded_shift_hours = 8.0
        finally:
            self._suppress_shift_events = False
        self._refresh_shift_hint()
        self._refresh_leave_hint()

    def _clear(self) -> None:
        self._selected_id = None
        self._suppress_shift_events = True
        try:
            self.id_var.set("")
            self.name_var.set("")
            self.dept_var.set("")
            self.shift_var.set("8")
            self.start_var.set("08:00")
            self.end_var.set("17:00")
            self.lunch_var.set("0")
            self.join_var.set("")
            self.official_var.set("")
            self.base_var.set("12")
            self.carry_var.set("0")
            self.active_var.set(True)
            self._loaded_shift_hours = 8.0
        finally:
            self._suppress_shift_events = False
        self.tree.selection_remove(self.tree.selection())
        self._refresh_shift_hint()
        self._refresh_leave_hint()

    def _current_shift_hours(self) -> float:
        try:
            return float(str(self.shift_var.get() or "8").replace(",", "."))
        except (TypeError, ValueError):
            return 8.0

    def _current_lunch_hours(self) -> float:
        try:
            return float(str(self.lunch_var.get() or "0").replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    def _refresh_shift_hint(self, *_args) -> None:
        standard = self._current_shift_hours()
        lunch = self._current_lunch_hours()
        actual = net_shift_hours(self.start_var.get(), self.end_var.get(), lunch)
        if actual is None:
            self.shift_hint_var.set("Thực tế: — (nhập HH:MM)")
            return
        mark = "✓" if actual + SHIFT_HOURS_TOLERANCE >= standard else "!"
        self.shift_hint_var.set(f"{mark} Thực tế: {actual:g}h  /  Giờ ca: {standard:g}h")

    def _on_shift_fields_changed(self, *_args) -> None:
        if self._suppress_shift_events:
            return
        self._refresh_shift_hint()

    def _on_leave_fields_changed(self, *_args) -> None:
        if self._suppress_shift_events:
            return
        self._refresh_leave_hint()

    def _refresh_leave_hint(self, *_args) -> None:
        labels = leave_period_labels()
        year = date.today().year
        try:
            base = float(str(self.base_var.get() or 12).replace(",", "."))
        except (TypeError, ValueError):
            base = 12.0
        try:
            carry = float(str(self.carry_var.get() or 0).replace(",", "."))
        except (TypeError, ValueError):
            carry = 0.0
        official = self.official_var.get() or self.join_var.get()
        prorated = prorated_base_leave(official, base, year)
        senior = seniority_leave(self.join_var.get(), year)
        used = 0.0
        if self._selected_id is not None:
            try:
                used = used_leave(self._selected_id, year)
            except Exception:
                used = 0.0
        remain = prorated + senior + carry - used
        self.leave_hint_var.set(
            f"{labels['carry']} (nhập tay) = {carry:g}  ·  "
            f"{labels['remain']} = {prorated:g} + {senior:g} + {carry:g} − {used:g} = {remain:g}"
        )

    def _standard_hours_changed(self) -> bool:
        return abs(self._current_shift_hours() - float(self._loaded_shift_hours)) > SHIFT_HOURS_TOLERANCE

    def _times_mismatch_new_standard(self) -> bool:
        if not self._standard_hours_changed():
            return False
        window = shift_window_hours(self.start_var.get(), self.end_var.get())
        if window is None:
            return False
        expected = self._current_shift_hours() + self._current_lunch_hours()
        return abs(window - expected) > SHIFT_WINDOW_TOLERANCE

    def _warn_standard_changed(self) -> None:
        if self._suppress_shift_events or not self._times_mismatch_new_standard():
            return
        standard = self._current_shift_hours()
        _ask_ctk_warning(
            self,
            "Cảnh báo",
            f"Cảnh báo: Giờ ca đã thay đổi thành {standard:g}h. "
            "Vui lòng thiết lập lại giờ Vào ca và Tan ca cho phù hợp.",
        )

    def _on_shift_standard_focus(self, _event=None) -> None:
        self._refresh_shift_hint()
        self._warn_standard_changed()

    def _on_shift_times_focus(self, _event=None) -> None:
        self._refresh_shift_hint()

    def _confirm_shift_before_save(self) -> bool:
        standard = self._current_shift_hours()
        lunch = self._current_lunch_hours()
        actual = net_shift_hours(self.start_var.get(), self.end_var.get(), lunch)
        if self._times_mismatch_new_standard():
            _ask_ctk_warning(
                self,
                "Cảnh báo",
                f"Cảnh báo: Giờ ca đã thay đổi thành {standard:g}h. "
                "Vui lòng thiết lập lại giờ Vào ca và Tan ca cho phù hợp.",
            )
        if actual is not None and actual + SHIFT_HOURS_TOLERANCE < standard:
            return _ask_ctk_warning(
                self,
                "Cảnh báo",
                (
                    f"Cảnh báo: Thời gian làm việc thực tế ({actual:g}h) đang ít hơn "
                    f"Giờ ca chuẩn ({standard:g}h). Bạn có chắc chắn muốn lưu?"
                ),
                confirm=True,
            )
        return True

    def _save(self) -> None:
        if not self._confirm_shift_before_save():
            return
        try:
            upsert_employee(
                {
                    "id": self._selected_id,
                    "employee_id": self.id_var.get(),
                    "employee_name": self.name_var.get(),
                    "department": self.dept_var.get(),
                    "standard_shift_hours": self._current_shift_hours(),
                    "shift_start": self.start_var.get(),
                    "shift_end": self.end_var.get(),
                    "lunch_duration_hours": self._current_lunch_hours(),
                    "join_date": self.join_var.get(),
                    "official_start_date": self.official_var.get() or self.join_var.get(),
                    "base_leave": int(float(self.base_var.get() or 12)),
                    "carryover_leave": float(self.carry_var.get() or 0),
                    "active": bool(self.active_var.get()),
                }
            )
        except Exception as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        self._loaded_shift_hours = self._current_shift_hours()
        self.app._append_log(f"Đã lưu nhân viên: {self.name_var.get().strip()}")
        self.refresh()
        if getattr(self.app, "correction", None):
            self.app.correction._master_cache = None
        if getattr(self.app, "refresh_roster_views", None):
            self.app.refresh_roster_views(skip="employees")

    def _delete(self) -> None:
        if self._selected_id is None:
            messagebox.showwarning(self.app.title(), "Chọn một nhân viên trên bảng.")
            return
        if not messagebox.askyesno(self.app.title(), "Xóa nhân viên này khỏi CSDL?"):
            return
        delete_employee(self._selected_id)
        self._clear()
        self.refresh()
        if getattr(self.app, "correction", None):
            self.app.correction._master_cache = None
        if getattr(self.app, "refresh_roster_views", None):
            self.app.refresh_roster_views(skip="employees")

    def _sync_from_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn Employee_Master_Data.xlsx",
            filetypes=[("Excel", "*.xlsx"), ("Tất cả", "*.*")],
            initialdir=str(Path.cwd()),
        )
        if not path:
            return
        try:
            stats = sync_employees_from_excel(path, log=self.app._append_log)
        except Exception as exc:
            messagebox.showerror(self.app.title(), str(exc))
            return
        self.app.master_var.set(path)
        self.refresh()
        if getattr(self.app, "correction", None):
            self.app.correction._master_cache = None
        if getattr(self.app, "refresh_roster_views", None):
            self.app.refresh_roster_views(skip="employees")
        added = int(stats.get("inserted") or 0)
        updated = int(stats.get("updated") or 0)
        messagebox.showinfo(
            self.app.title(),
            f"Đồng bộ thành công! Đã thêm mới {added} nhân viên, cập nhật {updated} nhân viên.",
        )
