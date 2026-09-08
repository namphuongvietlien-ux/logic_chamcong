"""Tab Ngày lễ: generate Vietnamese public holidays and edit company days."""

from __future__ import annotations

from datetime import date, datetime
from tkinter import messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from date_picker import DatePickerField
from processing.database import delete_holiday, list_holidays, upsert_holiday
from processing.holidays import ensure_holiday_years, seed_year
from tree_theme import apply_tree_style, bind_hover, stripe_tags


class HolidayPanel(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected_id: int | None = None
        self.year_var = ctk.StringVar(value=str(date.today().year))
        self.date_var = ctk.StringVar()
        self.name_var = ctk.StringVar()
        self.kind_var = ctk.StringVar(value="company")
        self.paid_var = ctk.BooleanVar(value=True)
        self.info_var = ctk.StringVar(value="")
        self._build()
        ensure_holiday_years()
        self.refresh()

    def _build(self) -> None:
        ctk.CTkLabel(
            self,
            text=(
                "Ngày lễ Việt Nam (Tết Dương lịch, Tết Âm lịch 5 ngày, Giỗ Tổ, 30/4, 1/5, Quốc khánh 2 ngày). "
                "Chủ nhật trùng lễ được thêm nghỉ bù. Ngày lễ có lương = 1 công nếu không chấm công."
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", padx=4, pady=(0, 8))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(tools, text="Năm").pack(side="left")
        years = [str(y) for y in range(date.today().year - 2, date.today().year + 4)]
        self.year_combo = ctk.CTkComboBox(tools, values=years, variable=self.year_var, width=100, command=self._on_year)
        self.year_combo.pack(side="left", padx=8)
        ctk.CTkButton(tools, text="Tạo / làm mới lễ quốc gia", width=200, command=self._regenerate).pack(side="left")
        ctk.CTkLabel(tools, textvariable=self.info_var, text_color=("#2B6CB0", "#90CDF4")).pack(side="right")

        table_wrap = ctk.CTkFrame(self, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("date", "name", "kind", "paid", "source")
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=16)
        headings = {
            "date": ("Ngày", 120),
            "name": ("Tên ngày lễ", 320),
            "kind": ("Loại", 120),
            "paid": ("Có lương", 90),
            "source": ("Nguồn", 90),
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
        ctk.CTkLabel(form, text="Ngày (YYYY-MM-DD)").grid(row=0, column=0, padx=8, pady=(10, 0), sticky="w")
        DatePickerField(form, variable=self.date_var, width=140).grid(
            row=1, column=0, padx=8, pady=(0, 10), sticky="w"
        )
        ctk.CTkLabel(form, text="Tên ngày lễ").grid(row=0, column=1, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkEntry(form, textvariable=self.name_var, width=280).grid(row=1, column=1, padx=8, pady=(0, 10))
        ctk.CTkLabel(form, text="Loại").grid(row=0, column=2, padx=8, pady=(10, 0), sticky="w")
        ctk.CTkComboBox(
            form,
            values=["public", "company", "compensatory"],
            variable=self.kind_var,
            width=140,
        ).grid(row=1, column=2, padx=8, pady=(0, 10))
        ctk.CTkCheckBox(form, text="Nghỉ có lương (1 công)", variable=self.paid_var).grid(
            row=1, column=3, padx=8, pady=(0, 10)
        )
        ctk.CTkButton(form, text="Thêm / lưu ngày lễ", width=160, command=self._save).grid(
            row=1, column=4, padx=8, pady=(0, 10)
        )
        ctk.CTkButton(form, text="Xóa", width=80, fg_color="#C53030", command=self._delete).grid(
            row=1, column=5, padx=8, pady=(0, 10)
        )

    def _year(self) -> int:
        try:
            return int(self.year_var.get())
        except ValueError:
            return date.today().year

    def _on_year(self, _value=None) -> None:
        ensure_holiday_years([self._year()])
        self.refresh()

    def _regenerate(self) -> None:
        year = self._year()
        count = seed_year(year)
        self.app._append_log(f"Đã tạo {count} ngày lễ quốc gia cho năm {year}.")
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        rows = list_holidays(self._year())
        kind_label = {"public": "Quốc gia", "company": "Công ty", "compensatory": "Nghỉ bù"}
        for index, item in enumerate(rows):
            raw = str(item.get("holiday_date") or "")
            try:
                shown = date.fromisoformat(raw).strftime("%d/%m/%Y")
            except ValueError:
                shown = raw
            self.tree.insert(
                "",
                "end",
                iid=str(item["id"]),
                values=(
                    shown,
                    item.get("name") or "",
                    kind_label.get(str(item.get("kind") or ""), item.get("kind") or ""),
                    "Có" if item.get("paid") else "Không",
                    "Tự động" if item.get("source") == "auto" else "Nhập tay",
                ),
                tags=stripe_tags(index),
            )
        self.info_var.set(f"{len(rows)} ngày lễ năm {self._year()}")

    def _on_select(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        values = self.tree.item(iid, "values")
        self._selected_id = int(iid)
        try:
            self.date_var.set(datetime.strptime(str(values[0]), "%d/%m/%Y").date().isoformat())
        except ValueError:
            self.date_var.set(str(values[0]))
        self.name_var.set(values[1])
        reverse = {"Quốc gia": "public", "Công ty": "company", "Nghỉ bù": "compensatory"}
        self.kind_var.set(reverse.get(str(values[2]), "company"))
        self.paid_var.set(str(values[3]) == "Có")

    def _save(self) -> None:
        try:
            upsert_holiday(
                {
                    "holiday_date": self.date_var.get().strip(),
                    "name": self.name_var.get().strip(),
                    "kind": self.kind_var.get() or "company",
                    "paid": bool(self.paid_var.get()),
                    "source": "manual",
                }
            )
        except Exception as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        self.app._append_log(f"Đã lưu ngày lễ: {self.date_var.get()} {self.name_var.get()}")
        self.refresh()

    def _delete(self) -> None:
        if self._selected_id is None:
            messagebox.showwarning(self.app.title(), "Chọn một ngày lễ trên bảng.")
            return
        if not messagebox.askyesno(self.app.title(), "Xóa ngày lễ này?"):
            return
        delete_holiday(self._selected_id)
        self._selected_id = None
        self.refresh()
