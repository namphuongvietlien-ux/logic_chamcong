"""Tab Quản lý Phép: balances, bulk join/carryover, leave requests."""

from __future__ import annotations

from datetime import date
from tkinter import messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from date_picker import DatePickerField
from processing.database import list_employees, update_employee_hr_fields
from processing.leave import (
    LEAVE_TYPE_PAID,
    LEAVE_TYPES,
    SESSION_UI,
    STATUSES,
    create_leave_request,
    delete_leave_request,
    formula_text,
    leave_balance,
    leave_period_labels,
    list_leave_requests,
    official_start_of,
    prorated_base_leave,
    seniority_leave,
    used_leave_by_employee,
    session_duration,
    session_label,
    working_days_between,
)
from processing.database import parse_iso_date
from tree_theme import apply_tree_style, bind_hover, replace_tree_rows, stripe_tags


class LeavePanel(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected_id: int | None = None
        self._official_vars: dict[int, ctk.StringVar] = {}
        self._carry_vars: dict[int, ctk.StringVar] = {}
        self.formula_var = ctk.StringVar(value="Chọn nhân viên để xem công thức phép năm.")
        self.start_var = ctk.StringVar()
        self.end_var = ctk.StringVar()
        self.days_var = ctk.StringVar(value="")
        self.note_var = ctk.StringVar()
        self.type_var = ctk.StringVar(value=LEAVE_TYPE_PAID)
        self.session_var = ctk.StringVar(value="Cả ngày")
        self.status_var = ctk.StringVar(value="approved")
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
                f"{leave_period_labels()['hint']} "
                "Ví dụ chuyển từ Excel: cột tồn năm trước → ô Tồn; cột còn lại hiện tại không nhập trực tiếp. "
                "NV mới / chưa có tồn năm trước = 0. "
                "Nghỉ không lương / BHXH / hiếu hỉ không trừ phép năm. Buổi Sáng hoặc Chiều = 0.5 ngày."
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", padx=4, pady=(0, 8))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            tools,
            text=(
                f"Nhập nhanh: Ngày chính thức + {leave_period_labels()['carry_long']}. "
                f"Cột {leave_period_labels()['remain']} do app tính, không gõ tay."
            ),
        ).pack(side="left")
        ctk.CTkButton(tools, text="Lưu hàng loạt", width=140, command=self._save_bulk).pack(side="right")
        ctk.CTkButton(tools, text="Làm mới", width=100, command=self.refresh).pack(side="right", padx=8)

        bulk_wrap = ctk.CTkFrame(self, corner_radius=12)
        bulk_wrap.pack(fill="x", pady=(0, 8))
        header = ctk.CTkFrame(bulk_wrap, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 0))
        for col, (text, width) in enumerate(
            (
                ("Mã NV / Nhân viên", 280),
                ("Ngày chính thức", 140),
                (leave_period_labels()["carry"], 110),
                (leave_period_labels()["remain"], 100),
            )
        ):
            ctk.CTkLabel(header, text=text, width=width, anchor="w", font=ctk.CTkFont(weight="bold")).grid(
                row=0, column=col, padx=4
            )
        self.bulk = ctk.CTkScrollableFrame(bulk_wrap, height=180)
        self.bulk.pack(fill="x", padx=6, pady=(0, 8))

        ctk.CTkLabel(
            self,
            textvariable=self.formula_var,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#1F6AA5", "#90CDF4"),
        ).pack(anchor="w", padx=8, pady=(0, 6))

        form = ctk.CTkFrame(self, corner_radius=12)
        form.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(form, text="Loại nghỉ phép").grid(row=0, column=0, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkOptionMenu(form, values=list(LEAVE_TYPES), variable=self.type_var, width=220).grid(
            row=1, column=0, padx=8, pady=(0, 10), sticky="w"
        )
        ctk.CTkLabel(form, text="Buổi nghỉ").grid(row=0, column=1, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkSegmentedButton(
            form,
            values=list(SESSION_UI),
            variable=self.session_var,
            command=lambda _v: self._recalc_days(),
            width=280,
        ).grid(row=1, column=1, padx=8, pady=(0, 10), sticky="w")
        ctk.CTkLabel(form, text="Từ ngày").grid(row=0, column=2, padx=8, pady=(10, 0), sticky="w")
        start = DatePickerField(form, variable=self.start_var, width=120, command=self._recalc_days)
        start.grid(row=1, column=2, padx=8, pady=(0, 10), sticky="w")
        start.bind("<KeyRelease>", lambda _e: self._recalc_days())
        ctk.CTkLabel(form, text="Đến ngày").grid(row=0, column=3, padx=8, pady=(10, 0), sticky="w")
        end = DatePickerField(form, variable=self.end_var, width=120, command=self._recalc_days)
        end.grid(row=1, column=3, padx=8, pady=(0, 10), sticky="w")
        end.bind("<KeyRelease>", lambda _e: self._recalc_days())
        ctk.CTkLabel(form, text="Số ngày").grid(row=0, column=4, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkEntry(form, textvariable=self.days_var, width=70).grid(row=1, column=4, padx=8, pady=(0, 10))
        ctk.CTkLabel(form, text="Trạng thái").grid(row=0, column=5, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkComboBox(form, values=list(STATUSES), variable=self.status_var, width=120).grid(
            row=1, column=5, padx=8, pady=(0, 10)
        )
        ctk.CTkLabel(form, text="Ghi chú").grid(row=0, column=6, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkEntry(form, textvariable=self.note_var, width=160).grid(row=1, column=6, padx=8, pady=(0, 10))
        ctk.CTkButton(form, text="Thêm đơn phép", width=140, command=self._add_request).grid(
            row=1, column=7, padx=8, pady=(0, 10)
        )
        ctk.CTkButton(form, text="Xóa đơn", width=90, fg_color="#C53030", command=self._delete_request).grid(
            row=1, column=8, padx=8, pady=(0, 10)
        )

        table_wrap = ctk.CTkFrame(self, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("type", "session", "start", "end", "days", "status", "note")
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=10)
        headings = {
            "type": ("Loại nghỉ phép", 180),
            "session": ("Buổi", 110),
            "start": ("Từ", 100),
            "end": ("Đến", 100),
            "days": ("Ngày", 70),
            "status": ("Trạng thái", 100),
            "note": ("Ghi chú", 220),
        }
        apply_tree_style(self.tree)
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="center")
            self.tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        bind_hover(self.tree)

    def refresh(self) -> None:
        for child in self.bulk.winfo_children():
            child.destroy()
        self._official_vars.clear()
        self._carry_vars.clear()
        year = date.today().year
        employees = list_employees(active_only=False)
        used_map = used_leave_by_employee(year)
        for index, item in enumerate(employees):
            pk = int(item["id"])
            official = str(item.get("official_start_date") or item.get("join_date") or "")
            carry_val = float(item.get("carryover_leave") or 0)
            carry = ctk.StringVar(value=str(item.get("carryover_leave") or 0))
            official_var = ctk.StringVar(value=official)
            self._official_vars[pk] = official_var
            self._carry_vars[pk] = carry
            try:
                prorated = prorated_base_leave(official_start_of(item), item.get("base_leave") or 12, year)
                senior = seniority_leave(item.get("join_date"), year)
                remain = prorated + senior + carry_val - used_map.get(pk, 0.0)
            except Exception:
                remain = 0
            row = ctk.CTkFrame(self.bulk, fg_color=("gray92", "gray20") if index % 2 else "transparent")
            row.pack(fill="x", pady=1)
            code = str(item.get("employee_id") or "").strip()
            name = str(item.get("employee_name") or "")
            label = f"{code}  {name}".strip() if code else name
            name_btn = ctk.CTkButton(
                row,
                text=label,
                width=280,
                fg_color="transparent",
                text_color=("#1A202C", "#E2E8F0"),
                anchor="w",
                command=lambda emp_id=pk: self._select_employee(emp_id),
            )
            name_btn.grid(row=0, column=0, padx=4, pady=2, sticky="w")
            DatePickerField(row, variable=official_var, width=110).grid(row=0, column=1, padx=4, pady=2, sticky="w")
            ctk.CTkEntry(row, textvariable=carry, width=100).grid(row=0, column=2, padx=4, pady=2)
            ctk.CTkLabel(row, text=f"{remain:g}", width=90).grid(row=0, column=3, padx=4, pady=2)
        self._data_loaded = True
        if self._selected_id:
            self._select_employee(self._selected_id)
        else:
            self._render_history()

    def _select_employee(self, employee_pk: int) -> None:
        self._selected_id = employee_pk
        try:
            balance = leave_balance(employee_pk)
            self.formula_var.set(formula_text(balance))
        except Exception as exc:
            self.formula_var.set(str(exc))
        self._render_history()

    def _render_history(self) -> None:
        if self._selected_id is None:
            replace_tree_rows(self.tree, [])
            return
        status_vn = {"approved": "Đã duyệt", "pending": "Chờ", "rejected": "Từ chối"}
        rows = []
        for index, item in enumerate(list_leave_requests(self._selected_id)):
            rows.append(
                (
                    str(item["id"]),
                    (
                        item.get("leave_type") or "",
                        session_label(item.get("session")),
                        item.get("start_date") or "",
                        item.get("end_date") or "",
                        item.get("days") or 0,
                        status_vn.get(str(item.get("status") or ""), item.get("status") or ""),
                        item.get("note") or "",
                    ),
                    stripe_tags(index),
                )
            )
        replace_tree_rows(self.tree, rows)

    def _recalc_days(self) -> None:
        start = parse_iso_date(self.start_var.get())
        end = parse_iso_date(self.end_var.get() or self.start_var.get())
        if start and end:
            days = working_days_between(start, end) * session_duration(self.session_var.get())
            self.days_var.set(str(days))

    def _save_bulk(self) -> None:
        count = 0
        try:
            for pk, official_var in self._official_vars.items():
                update_employee_hr_fields(
                    pk,
                    official_start_date=official_var.get(),
                    carryover_leave=float(self._carry_vars[pk].get() or 0),
                )
                count += 1
        except Exception as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        labels = leave_period_labels()
        self.app._append_log(f"Đã lưu ngày chính thức / {labels['carry']} cho {count} nhân viên.")
        self.refresh()
        if getattr(self.app, "refresh_roster_views", None):
            self.app.refresh_roster_views(skip="leave")
        messagebox.showinfo(self.app.title(), f"Đã lưu {count} dòng (ngày chính thức + {labels['carry']}).")

    def _add_request(self) -> None:
        if self._selected_id is None:
            messagebox.showwarning(self.app.title(), "Chọn một nhân viên trên danh sách.")
            return
        try:
            create_leave_request(
                {
                    "employee_id": self._selected_id,
                    "leave_type": self.type_var.get(),
                    "start_date": self.start_var.get(),
                    "end_date": self.end_var.get() or self.start_var.get(),
                    "days": self.days_var.get() or None,
                    "session": self.session_var.get(),
                    "status": self.status_var.get() or "approved",
                    "note": self.note_var.get(),
                }
            )
        except Exception as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        self.app._append_log(f"Đã thêm phép: {self.type_var.get()} {self.start_var.get()}")
        self.refresh()

    def _delete_request(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(self.app.title(), "Chọn một đơn phép trên bảng lịch sử.")
            return
        if not messagebox.askyesno(self.app.title(), "Xóa đơn phép này?"):
            return
        delete_leave_request(int(selected[0]))
        self.refresh()
