"""Preview popup + confirm flow for historical Chi tiết (sheet Tổng hợp) import."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from processing.database import list_attendance_months, month_key
from processing.history_import import (
    commit_history_import,
    extract_tong_hop,
    match_history_rows,
)
from tree_theme import apply_tree_style, bind_hover, stripe_tags


def show_ctk_messagebox(master, title: str, message: str, *, success: bool = True) -> None:
    """CTk-styled modal (CTkMessagebox-compatible look) without an extra package."""
    try:
        from CTkMessagebox import CTkMessagebox

        CTkMessagebox(
            master=master,
            title=title,
            message=message,
            icon="check" if success else "warning",
            option_1="Đóng",
        )
        return
    except Exception:
        pass
    win = ctk.CTkToplevel(master)
    win.title(title)
    win.geometry("460x220")
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
    accent = "#2F855A" if success else "#C53030"
    ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color=accent).pack(
        anchor="w", padx=20, pady=(18, 6)
    )
    ctk.CTkLabel(win, text=message, wraplength=400, justify="left").pack(anchor="w", padx=20, pady=(0, 12))
    ctk.CTkButton(win, text="Đóng", width=100, command=win.destroy).pack(pady=(0, 16))
    win.after(50, win.lift)


def month_choices(preferred: str = "") -> list[str]:
    today = date.today()
    choices: list[str] = []
    if preferred:
        choices.append(preferred)
    for item in list_attendance_months():
        if item not in choices:
            choices.append(item)
    year, month = today.year, today.month
    for _ in range(24):
        key = month_key(year, month)
        if key not in choices:
            choices.append(key)
        month -= 1
        if month <= 0:
            month = 12
            year -= 1
    return choices


class HistoryPreviewDialog(ctk.CTkToplevel):
    def __init__(self, master, app, payload: dict) -> None:
        super().__init__(master)
        self.app = app
        self.payload = payload
        self.rows = match_history_rows(payload.get("rows") or [])
        guessed = str(payload.get("month_year") or "")
        self.month_var = ctk.StringVar(value=guessed or month_choices()[0])
        self.title("Xem trước hồ sơ lịch sử — sheet Tổng hợp")
        self.geometry("920x560")
        self.minsize(780, 480)
        self.transient(master)
        self.grab_set()
        self._build()
        self.after(50, self.lift)

    def _build(self) -> None:
        matched = sum(1 for row in self.rows if row.get("matched"))
        missing = len(self.rows) - matched
        ctk.CTkLabel(
            self,
            text=(
                f'{Path(self.payload.get("path") or "").name}  ·  sheet "{self.payload.get("sheet")}"  ·  '
                f"{len(self.rows)} dòng  ·  khớp {matched}  ·  thiếu {missing}"
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=860,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(tools, text="Kỳ đích (tháng/năm)", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.month_combo = ctk.CTkComboBox(
            tools,
            values=month_choices(self.month_var.get()),
            variable=self.month_var,
            width=140,
        )
        self.month_combo.pack(side="left", padx=8)

        table_wrap = ctk.CTkFrame(self, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("code", "name", "workdays", "leave", "ot", "status")
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse")
        headings = {
            "code": ("Mã NV", 90),
            "name": ("Tên nhân viên", 240),
            "workdays": ("Tổng công", 100),
            "leave": ("Phép năm", 90),
            "ot": ("Tăng ca", 90),
            "status": ("Khớp CSDL", 160),
        }
        apply_tree_style(self.tree)
        self.tree.tag_configure("miss", foreground="#C53030")
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="center")
            self.tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        bind_hover(self.tree)
        for index, row in enumerate(self.rows):
            self.tree.insert(
                "",
                "end",
                values=(
                    row.get("employee_id") or "",
                    row.get("employee_name") or "",
                    f"{float(row.get('workdays') or 0):g}",
                    f"{float(row.get('used_leave') or 0):g}",
                    f"{float(row.get('overtime_hours') or 0):g}",
                    row.get("match_note") or "",
                ),
                tags=stripe_tags(index, "miss" if not row.get("matched") else ""),
            )

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bottom, text="Hủy", width=110, fg_color="#718096", command=self.destroy).pack(side="right")
        ctk.CTkButton(
            bottom,
            text="Xác nhận & Lưu lịch sử",
            width=220,
            fg_color="#2B6CB0",
            command=self._confirm,
        ).pack(side="right", padx=8)

    def _confirm(self) -> None:
        key = (self.month_var.get() or "").strip()
        if not key or "/" not in key:
            messagebox.showwarning(self.app.title(), "Chọn kỳ đích dạng MM/YYYY (ví dụ 07/2026).")
            return
        matched = sum(1 for row in self.rows if row.get("matched"))
        if matched <= 0:
            messagebox.showwarning(self.app.title(), "Không có nhân viên nào khớp CSDL. Không lưu.")
            return
        if not messagebox.askyesno(
            self.app.title(),
            f"Lưu {matched} dòng vào lịch sử kỳ {key}, chốt công tháng này, "
            f"và trừ Phép năm khỏi số ngày phép còn lại?",
        ):
            return
        try:
            result = commit_history_import(
                self.rows,
                key,
                source_file=self.payload.get("path") or "",
                error_dir=self.app.output_var.get().strip() or None,
            )
        except Exception as exc:
            messagebox.showerror(self.app.title(), str(exc))
            return
        miss = result.get("miss_log")
        extra = f"\nFile nhân viên không khớp: {miss}" if miss else ""
        self.app._append_log(
            f"Nạp lịch sử {key}: lưu {result['saved']} NV, trừ {result['leave_requests']} đơn phép năm, "
            f"đã chốt công.{' Thiếu ' + str(result['unmatched']) + ' NV.' if result['unmatched'] else ''}"
        )
        if hasattr(self.app, "dashboard") and self.app.dashboard:
            try:
                self.app.dashboard.month_var.set(key)
                self.app.dashboard.refresh()
            except Exception:
                pass
        self.destroy()
        show_ctk_messagebox(
            self.app,
            "Đã lưu lịch sử",
            f"Đã nạp {result['saved']} nhân viên vào kỳ {key}.\n"
            f"Kỳ này đã được chốt công.\n"
            f"Đã trừ {result['leave_requests']} khoản phép năm khỏi số ngày còn lại.{extra}",
            success=True,
        )


def pick_and_preview_history(app) -> None:
    start = app.output_var.get().strip() if hasattr(app, "output_var") else ""
    path = filedialog.askopenfilename(
        title="Chọn file Chi tiết chấm công (sheet Tổng hợp)",
        initialdir=start or str(Path.cwd()),
        filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")],
    )
    if not path:
        return
    try:
        payload = extract_tong_hop(path)
    except Exception as exc:
        messagebox.showerror(app.title(), str(exc))
        return
    HistoryPreviewDialog(app, app, payload)
