"""Tab 'Xử lý Ngoại lệ': missing in/out/lunch punches, manual add, re-export all reports."""

from __future__ import annotations

import threading
import traceback
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import customtkinter as ctk
import pandas as pd

from processing.cong_rules import month_from_merged
from processing.corrections import (
    apply_missing_punch_correction,
    clock_text,
    collect_missing_punches,
    day_review_count,
    days_in_table,
    load_attendance_source,
    milestone_from_clocks,
    review_counts_by_day,
    roster_names,
    rows_for_day,
    rows_for_employee,
    upsert_clocks,
    reexport_corrected,
)
from processing.sessions import STATUS_MISSING_IN, STATUS_MISSING_OUT
from processing.database import is_month_locked, month_key, persist_attendance_from_merged
from processing.master_data import load_master_data
from processing.pipeline import parse_work_start
from processing.utils import name_match_key, parse_date_value, parse_time_value

ALL_EMPLOYEES = "Tất cả nhân viên"
VIEW_BY_DAY = "Theo ngày"
VIEW_BY_NAME = "Theo nhân viên"

TREE_STYLE = "Attendance.Treeview"
TREE_ODD_BG = "#FFFFFF"
TREE_EVEN_BG = "#F4F6F7"
TREE_HOVER_BG = "#EAF2F8"
TREE_SELECT_BG = "#D4E6F1"
TREE_SELECT_FG = "#1B4F72"
TREE_HEAD_BG = "#2C3E50"
TREE_HEAD_FG = "#FFFFFF"


class CorrectionPanel(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.df = None
        self.year = None
        self.month = None
        self.current_day: date | None = None
        self._editing_day: date | None = None
        self._master_cache = None
        self._counts: dict | None = None
        self._loading = False
        self._ignore_tree = False
        self._editing_name = ""
        self._row_meta: dict[str, tuple] = {}
        self.file_var = ctk.StringVar(value="")
        self.filter_var = ctk.StringVar(value="Cần xem")
        self.view_var = ctk.StringVar(value="Theo ngày")
        self.day_var = ctk.StringVar(value="")
        self.name_var = ctk.StringVar(value="Tất cả nhân viên")
        self.name_search_var = ctk.StringVar(value="")
        self.info_var = ctk.StringVar(value="Chọn file K9_07.2026.xlsx hoặc Du_lieu_sua_gio_YYYY-MM.xlsx")
        self.edit_name_var = ctk.StringVar(value="Chọn một dòng trong bảng để sửa giờ")
        self.hours_var = ctk.StringVar(value="")
        self.badge_var = ctk.StringVar(value="")
        self.cong_var = ctk.StringVar(value="")
        self.overnight_var = ctk.BooleanVar(value=False)
        self._ignore_editor = False
        self._ignore_name = False
        self._hover_iid = ""
        self._exceptions: list[dict] = []
        self.fix_info_var = ctk.StringVar(value="Chọn một dòng thiếu mốc để bù giờ.")
        self.fix_time_var = ctk.StringVar(value="")
        self.exc_count_var = ctk.StringVar(value="")
        self.exc_search_var = ctk.StringVar(value="")
        self._exc_meta: dict[str, dict] = {}
        self._build()

    def _build(self) -> None:
        self.subtabs = ctk.CTkTabview(self)
        self.subtabs.pack(fill="both", expand=True)
        self.subtabs.add("Bù giờ (1 mốc)")
        self.subtabs.add("Sửa chi tiết")
        self._build_missing_tab(self.subtabs.tab("Bù giờ (1 mốc)"))
        self._build_detail_tab(self.subtabs.tab("Sửa chi tiết"))

    def _build_missing_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab,
            text=(
                "Ngày chỉ còn 1 giờ sau khi gộp vân tay + ảnh: trước 12:00 = Thiếu Giờ Ra, "
                "từ 12:00 = Thiếu Giờ Vào. Nhập giờ còn thiếu rồi bấm Lưu bù giờ."
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", padx=4, pady=(0, 8))
        search_row = ctk.CTkFrame(tab, fg_color="transparent")
        search_row.pack(fill="x", pady=(0, 8))
        self.exc_search = ctk.CTkEntry(
            search_row,
            textvariable=self.exc_search_var,
            placeholder_text="🔍 Tìm kiếm theo Tên hoặc Mã NV...",
            height=36,
        )
        self.exc_search.pack(side="left", fill="x", expand=True)
        self.exc_search.bind("<KeyRelease>", lambda _e: self._render_exceptions())
        ctk.CTkLabel(search_row, textvariable=self.exc_count_var, text_color=("#2B6CB0", "#90CDF4")).pack(
            side="right", padx=(12, 0)
        )

        table_wrap = ctk.CTkFrame(tab, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("code", "name", "date", "recorded", "status")
        self.exc_tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=14)
        from tree_theme import apply_tree_style, bind_hover

        apply_tree_style(self.exc_tree)
        headings = {
            "code": ("Mã NV", 90),
            "name": ("Tên", 240),
            "date": ("Ngày", 110),
            "recorded": ("Giờ Đã Ghi Nhận", 130),
            "status": ("Trạng Thái", 160),
        }
        for key, (title, width) in headings.items():
            self.exc_tree.heading(key, text=title, anchor="center")
            self.exc_tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.exc_tree.yview)
        self.exc_tree.configure(yscrollcommand=scroll.set)
        self.exc_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        bind_hover(self.exc_tree)
        self.exc_tree.bind("<<TreeviewSelect>>", self._on_exc_select)

        form = ctk.CTkFrame(tab, corner_radius=12)
        form.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(form, text="Bù giờ thủ công", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=4, padx=12, pady=(10, 4), sticky="w"
        )
        ctk.CTkLabel(form, textvariable=self.fix_info_var, text_color=("#2B6CB0", "#90CDF4")).grid(
            row=1, column=0, columnspan=4, padx=12, pady=(0, 8), sticky="w"
        )
        ctk.CTkLabel(form, text="Nhập giờ bổ sung (HH:MM)").grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")
        self.fix_time = ctk.CTkEntry(form, textvariable=self.fix_time_var, width=140, placeholder_text="17:30")
        self.fix_time.grid(row=2, column=1, padx=8, pady=(0, 12), sticky="w")
        ctk.CTkButton(form, text="Lưu bù giờ", width=160, fg_color="#2B6CB0", command=self._save_bu_gio).grid(
            row=2, column=2, padx=8, pady=(0, 12), sticky="w"
        )

    def _build_detail_tab(self, tab) -> None:
        hint = ctk.CTkLabel(
            tab,
            text=(
                "Chọn dòng → sửa HH:MM bên dưới. Có thể sửa theo ngày hoặc theo tên nhân viên. "
                "Ca đêm: tick «Tăng ca qua đêm» rồi nhập giờ RA (gắn +1 ngày)."
            ),
            text_color=("#4A5568", "#A0AEC0"),
            wraplength=1100,
            justify="left",
        )
        hint.pack(anchor="w", padx=4, pady=(0, 8))

        bar = ctk.CTkFrame(tab, corner_radius=12)
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(bar, text="File K9 / dữ liệu sửa", width=160, anchor="w").grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w"
        )
        ctk.CTkEntry(bar, textvariable=self.file_var).grid(row=0, column=1, sticky="ew", pady=10)
        ctk.CTkButton(bar, text="Chọn...", width=90, command=self._pick_file).grid(
            row=0, column=2, padx=6, pady=10
        )
        ctk.CTkButton(bar, text="Tải file", width=100, command=self._load_file).grid(
            row=0, column=3, padx=(0, 12), pady=10
        )
        bar.grid_columnconfigure(1, weight=1)

        tools = ctk.CTkFrame(tab, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(tools, text="Xem").pack(side="left")
        self.view_combo = ctk.CTkComboBox(
            tools,
            variable=self.view_var,
            values=[VIEW_BY_DAY, VIEW_BY_NAME],
            width=150,
            command=self._on_view_change,
        )
        self.view_combo.pack(side="left", padx=(8, 12))
        ctk.CTkLabel(tools, text="Ngày").pack(side="left")
        self.day_combo = ctk.CTkComboBox(
            tools, variable=self.day_var, values=["—"], width=180, command=self._on_day_change
        )
        self.day_combo.pack(side="left", padx=(8, 12))
        ctk.CTkLabel(tools, text="Nhân viên").pack(side="left")
        self.name_combo = ctk.CTkComboBox(
            tools, variable=self.name_var, values=[ALL_EMPLOYEES], width=260, command=self._on_name_change
        )
        self.name_combo.pack(side="left", padx=(8, 8))
        self.name_search = ctk.CTkEntry(
            tools, textvariable=self.name_search_var, width=160, placeholder_text="Tìm tên..."
        )
        self.name_search.pack(side="left", padx=(0, 8))
        self.name_search.bind("<KeyRelease>", lambda _e: self._on_name_search())
        self.name_search.bind("<Return>", lambda _e: self._on_name_search(select_first=True))

        tools2 = ctk.CTkFrame(tab, fg_color="transparent")
        tools2.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(tools2, text="Lọc").pack(side="left")
        self.filter_combo = ctk.CTkComboBox(
            tools2,
            variable=self.filter_var,
            values=["Cần xem", "Thiếu giờ", "Chưa đạt mốc", "Tất cả", "Đã sửa tay"],
            width=160,
            command=self._on_filter_change,
        )
        self.filter_combo.pack(side="left", padx=(8, 16))
        ctk.CTkLabel(tools2, text="Ca").pack(side="left")
        self.shift_filter_var = ctk.StringVar(value="Tất cả ca")
        self.shift_combo = ctk.CTkComboBox(
            tools2,
            variable=self.shift_filter_var,
            values=["Tất cả ca", "Ca 8 tiếng", "Ca 12 tiếng"],
            width=140,
            command=self._on_filter_change,
        )
        self.shift_combo.pack(side="left", padx=(8, 16))
        ctk.CTkButton(tools2, text="Thêm người", width=120, command=self._add_person).pack(side="left")
        ctk.CTkButton(
            tools2,
            text="Cập nhật tất cả báo cáo",
            width=200,
            fg_color="#1F6AA5",
            command=self._save_all,
        ).pack(side="right")
        ctk.CTkLabel(tools2, textvariable=self.info_var, text_color=("#2B6CB0", "#90CDF4")).pack(
            side="right", padx=12
        )

        table_wrap = ctk.CTkFrame(tab, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(4, 6))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("date", "name", "issue", "in1", "out1", "in2", "out2", "fraction", "badge", "cong")
        self.tree = ttk.Treeview(
            inner,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=16,
            style=TREE_STYLE,
        )
        self._apply_tree_style()
        headings = {
            "date": ("Ngày", 100),
            "name": ("Nhân viên", 200),
            "issue": ("Tình trạng", 170),
            "in1": ("Vào", 70),
            "out1": ("Ra", 70),
            "in2": ("Vào", 70),
            "out2": ("Ra", 70),
            "fraction": ("Giờ / mốc", 90),
            "badge": ("Kết quả", 90),
            "cong": ("Công", 80),
        }
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="center")
            self.tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Motion>", self._on_tree_hover)
        self.tree.bind("<Leave>", self._on_tree_leave)

        editor = ctk.CTkFrame(tab, corner_radius=12)
        editor.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(editor, textvariable=self.edit_name_var, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=10, padx=12, pady=(10, 4), sticky="w"
        )
        labels = ("Vào", "Ra", "Vào", "Ra")
        self.e_in1 = ctk.CTkEntry(editor, width=80)
        self.e_out1 = ctk.CTkEntry(editor, width=80)
        self.e_in2 = ctk.CTkEntry(editor, width=80)
        self.e_out2 = ctk.CTkEntry(editor, width=80)
        for col, (lab, entry) in enumerate(zip(labels, (self.e_in1, self.e_out1, self.e_in2, self.e_out2))):
            ctk.CTkLabel(editor, text=lab, width=40).grid(row=1, column=col * 2, padx=(12, 2), pady=(0, 10))
            entry.grid(row=1, column=col * 2 + 1, padx=(0, 8), pady=(0, 10))
            entry.bind("<KeyRelease>", lambda _e: self._on_edit_change())
            entry.bind("<FocusOut>", lambda _e: self._apply_editor(save=True))
            entry.bind("<Return>", lambda _e: self._apply_editor(save=True))
        self.hours_lbl = ctk.CTkLabel(
            editor, textvariable=self.hours_var, width=90, font=ctk.CTkFont(size=18, weight="bold")
        )
        self.hours_lbl.grid(row=1, column=8, padx=8, pady=(0, 10))
        self.badge_lbl = ctk.CTkLabel(
            editor, textvariable=self.badge_var, width=110, font=ctk.CTkFont(weight="bold")
        )
        self.badge_lbl.grid(row=1, column=9, padx=4, pady=(0, 10))
        ctk.CTkLabel(editor, textvariable=self.cong_var, width=80).grid(
            row=1, column=10, padx=8, pady=(0, 10)
        )
        self.overnight_chk = ctk.CTkCheckBox(
            editor,
            text="Tăng ca qua đêm (+1 ngày)",
            variable=self.overnight_var,
            command=self._on_overnight_toggle,
            font=ctk.CTkFont(weight="bold"),
        )
        self.overnight_chk.grid(row=2, column=0, columnspan=6, padx=12, pady=(0, 12), sticky="w")
        ctk.CTkButton(
            editor,
            text="Cập nhật dòng",
            width=140,
            command=lambda: self._apply_editor(save=True),
        ).grid(row=2, column=8, columnspan=3, padx=8, pady=(0, 12), sticky="e")

    def _apply_tree_style(self) -> None:
        style = ttk.Style(self.winfo_toplevel())
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            TREE_STYLE,
            background=TREE_ODD_BG,
            fieldbackground=TREE_ODD_BG,
            foreground="#1A202C",
            rowheight=28,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            f"{TREE_STYLE}.Heading",
            background=TREE_HEAD_BG,
            foreground=TREE_HEAD_FG,
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padding=6,
        )
        style.map(
            TREE_STYLE,
            background=[("selected", TREE_SELECT_BG)],
            foreground=[("selected", TREE_SELECT_FG)],
        )
        style.map(f"{TREE_STYLE}.Heading", background=[("active", "#34495E")])
        self.tree.tag_configure("odd", background=TREE_ODD_BG)
        self.tree.tag_configure("even", background=TREE_EVEN_BG)
        self.tree.tag_configure("hover", background=TREE_HOVER_BG)
        self.tree.tag_configure("ok", foreground="#276749")
        self.tree.tag_configure("short", foreground="#C53030")
        self.tree.tag_configure("issue", foreground="#9B2C2C")
        self.tree.tag_configure("needs_lunch", background="#FFF9C4")  # Yellow highlight for 12h shifts needing lunch 補充

    def _status_tag(self, issue, reached: bool) -> str:
        if issue:
            return "issue"
        return "ok" if reached else "short"

    def _row_tags(self, index: int, status: str, hover: bool = False, needs_lunch_補充: bool = False) -> tuple:
        stripe = "even" if index % 2 == 0 else "odd"
        tags = [stripe, status]
        if needs_lunch_補充:
            tags.append("needs_lunch")
        if hover:
            tags.append("hover")
        return tuple(tags)

    def _tags_for_iid(self, iid: str, status: str) -> tuple:
        children = list(self.tree.get_children())
        try:
            index = children.index(iid)
        except ValueError:
            index = 0
        return self._row_tags(index, status, hover=iid == self._hover_iid)

    def _on_tree_hover(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid == self._hover_iid:
            return
        self._clear_hover()
        if not iid or iid in self.tree.selection():
            return
        self._hover_iid = iid
        tags = [t for t in self.tree.item(iid, "tags") if t != "hover"]
        tags.append("hover")
        self.tree.item(iid, tags=tags)

    def _on_tree_leave(self, _event=None) -> None:
        self._clear_hover()

    def _clear_hover(self) -> None:
        iid = self._hover_iid
        self._hover_iid = ""
        if iid and iid in self.tree.get_children():
            tags = [t for t in self.tree.item(iid, "tags") if t != "hover"]
            self.tree.item(iid, tags=tags)

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn K9_MM.YYYY.xlsx hoặc Du_lieu_sua_gio_YYYY-MM.xlsx",
            filetypes=[("Excel", "*.xlsx"), ("Tất cả", "*.*")],
            initialdir=self.app.output_var.get() or str(Path.cwd()),
        )
        if path:
            self.file_var.set(path)

    def _master(self):
        if self._master_cache is None:
            path = self.app.master_var.get().strip()
            self._master_cache = load_master_data(path or None)
        return self._master_cache

    def _load_file(self) -> None:
        path = self.file_var.get().strip()
        if not path:
            default = Path(self.app.output_var.get() or ".")
            matches = sorted(default.glob("K9_*.xlsx"), reverse=True) + sorted(
                default.glob("Du_lieu_sua_gio_*.xlsx"), reverse=True
            )
            matches = [p for p in matches if not p.name.startswith("~$")]
            if matches:
                path = str(matches[0])
                self.file_var.set(path)
            else:
                messagebox.showwarning(self.app.title(), "Hãy chọn file K9 hoặc Du_lieu_sua_gio.")
                return
        if self._loading:
            return
        self._loading = True
        self.info_var.set("Đang tải file trên nền (không khóa giao diện)...")
        master_path = self.app.master_var.get().strip() or None

        def job() -> None:
            try:
                master = load_master_data(master_path)
                df, year, month = load_attendance_source(
                    path, master=master, log=lambda m: self.app._log_queue.put(("log", m))
                )
                counts = review_counts_by_day(df)
                self.after(0, lambda: self._finish_load(df, year, month, path, master, None, counts))
            except Exception as exc:  # noqa: BLE001
                err = exc
                self.after(0, lambda e=err: self._finish_load(None, None, None, path, None, e, None))

        threading.Thread(target=job, daemon=True).start()

    def _finish_load(self, df, year, month, path, master, error, counts=None) -> None:
        self._loading = False
        if error is not None:
            messagebox.showerror(self.app.title(), str(error))
            self.info_var.set("Tải file thất bại.")
            return
        if master is not None:
            self._master_cache = master
        self._apply_table(df, year, month, path, counts)

    def load_after_analysis(self, paths: dict) -> None:
        working = paths.get("working") or paths.get("k9")
        if not working:
            return
        self.file_var.set(str(working))

    def load_from_merged(self, merged: pd.DataFrame, paths: dict | None = None) -> None:
        """Use the in-memory analysis result — do not re-read K9 on the UI thread."""
        if merged is None or merged.empty or self._loading:
            return
        path = ""
        if paths:
            path = str(paths.get("working") or paths.get("k9") or "")
        if path:
            self.file_var.set(path)
        self._loading = True
        self.info_var.set("Đang chuẩn bị bảng sửa giờ...")

        def job() -> None:
            try:
                df = merged.copy()
                year, month = month_from_merged(df)
                counts = review_counts_by_day(df)
                self.after(0, lambda: self._finish_load(df, year, month, path, None, None, counts))
            except Exception as exc:  # noqa: BLE001
                err = exc
                self.after(0, lambda e=err: self._finish_load(None, None, None, path, None, e, None))

        threading.Thread(target=job, daemon=True).start()

    def _apply_table(self, df, year, month, path, counts=None) -> None:
        self.df = df
        self.year, self.month = year, month
        self._counts = counts if counts is not None else review_counts_by_day(df)
        days = days_in_table(df)
        labels = []
        for d in days:
            review, _m, _s = self._counts.get(d, (0, 0, 0))
            labels.append(f"{d.strftime('%d/%m/%Y')}  ({review} cần xem)")
        values = labels or ["—"]
        self.current_day = days[0] if days else None
        self.day_combo.configure(values=values)
        if values:
            self.day_combo.set(values[0])
        self.app._append_log(f"Sửa giờ: đã tải {Path(path).name if path else 'dữ liệu phân tích'}")
        self._refresh_name_combo()
        self._render_table()
        self._refresh_exceptions()

    def _refresh_exceptions(self) -> None:
        self._exceptions = collect_missing_punches(self.df) if self.df is not None else []
        self._render_exceptions()

    def _render_exceptions(self) -> None:
        from tree_theme import employee_search_match, stripe_tags

        query = self.exc_search_var.get()
        self.exc_tree.delete(*self.exc_tree.get_children())
        self._exc_meta = {}
        visible = 0
        for item in self._exceptions:
            if not employee_search_match(query, item.get("employee_id"), item.get("employee_name")):
                continue
            day = item.get("date")
            iid = f"{name_match_key(item.get('employee_name') or '')}|{day.isoformat() if day else ''}"
            self._exc_meta[iid] = item
            self.exc_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("employee_id") or "",
                    item.get("employee_name") or "",
                    day.strftime("%d/%m/%Y") if day else "",
                    item.get("recorded_text") or "",
                    item.get("status") or "",
                ),
                tags=stripe_tags(visible),
            )
            visible += 1
        total = len(self._exceptions)
        if str(query or "").strip():
            self.exc_count_var.set(f"{visible}/{total} ngoại lệ")
        else:
            self.exc_count_var.set(f"{total} ngày thiếu 1 mốc")

    def _on_exc_select(self, _event=None) -> None:
        selected = self.exc_tree.selection()
        if not selected:
            self.fix_info_var.set("Chọn một dòng thiếu mốc để bù giờ.")
            return
        item = self._exc_meta.get(selected[0]) or {}
        day = item.get("date")
        day_text = day.strftime("%d/%m/%Y") if day else ""
        status = item.get("status") or ""
        self.fix_info_var.set(
            f"{item.get('employee_id') or '—'}  ·  {item.get('employee_name') or ''}  ·  "
            f"{day_text}  ·  đã ghi {item.get('recorded_text') or '—'}  ·  {status}"
        )
        if status == STATUS_MISSING_IN:
            self.fix_time.configure(placeholder_text="08:00")
        else:
            self.fix_time.configure(placeholder_text="17:30")

    def _save_bu_gio(self) -> None:
        if self.df is None:
            messagebox.showwarning(self.app.title(), "Hãy chạy phân tích hoặc tải file dữ liệu sửa giờ trước.")
            return
        if self._period_locked():
            messagebox.showwarning(
                self.app.title(),
                f"Kỳ {month_key(self.year, self.month)} đã chốt công. Không sửa giờ.",
            )
            return
        selected = self.exc_tree.selection()
        if not selected:
            messagebox.showwarning(self.app.title(), "Chọn một dòng trên bảng ngoại lệ.")
            return
        item = self._exc_meta.get(selected[0])
        if not item or not item.get("date"):
            messagebox.showwarning(self.app.title(), "Không đọc được dòng đã chọn.")
            return
        extra = (self.fix_time_var.get() or "").strip()
        try:
            self.df = apply_missing_punch_correction(
                self.df,
                str(item.get("employee_name") or ""),
                item["date"],
                extra,
                master=self._master(),
            )
        except Exception as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        if self.year is not None and self.month is not None:
            persist_attendance_from_merged(self.df, self.year, self.month)
        self._counts = None
        self.fix_time_var.set("")
        self.fix_info_var.set(
            f"Đã bù giờ {extra} cho {item.get('employee_name')} ngày "
            f"{item['date'].strftime('%d/%m/%Y')}."
        )
        self.app._append_log(
            f"Bù giờ: {item.get('employee_name')} {item['date'].strftime('%d/%m/%Y')} + {extra}"
        )
        self._refresh_exceptions()
        self._render_table()

    def _selected_day(self) -> date | None:
        text = (self.day_var.get() or self.day_combo.get() or "").strip()
        if not text or text == "—":
            return self.current_day
        stamp = text.split()[0]
        try:
            return datetime_from_dmy(stamp)
        except ValueError:
            return self.current_day

    def _view_by_name(self) -> bool:
        return (self.view_var.get() or VIEW_BY_DAY) == VIEW_BY_NAME

    def _selected_employee(self) -> str:
        name = (self.name_var.get() or "").strip()
        if not name or name == ALL_EMPLOYEES:
            return ""
        return name

    def _row_iid(self, name: str, day: date | None) -> str:
        stamp = day.isoformat() if day else ""
        return f"{stamp}::{name}"

    def _parse_iid(self, iid: str) -> tuple[str, date | None]:
        text = str(iid or "")
        if "::" in text:
            stamp, name = text.split("::", 1)
            try:
                return name, date.fromisoformat(stamp)
            except ValueError:
                return name, self.current_day
        return text, self.current_day

    def _work_date(self, value) -> date | None:
        return parse_date_value(value)

    def _all_employee_names(self) -> list[str]:
        return roster_names(self.df, self._master()) if self.df is not None else []

    def _refresh_name_combo(self, names: list[str] | None = None) -> None:
        roster = names if names is not None else self._all_employee_names()
        values = [ALL_EMPLOYEES] + roster
        current = self.name_var.get()
        self._ignore_name = True
        self.name_combo.configure(values=values or [ALL_EMPLOYEES])
        if current in values:
            self.name_combo.set(current)
        elif self._view_by_name() and roster:
            self.name_var.set(roster[0])
            self.name_combo.set(roster[0])
        else:
            self.name_var.set(ALL_EMPLOYEES)
            self.name_combo.set(ALL_EMPLOYEES)
        self._ignore_name = False

    def _on_name_search(self, select_first: bool = False) -> None:
        query = name_match_key(self.name_search_var.get())
        roster = self._all_employee_names()
        if query:
            roster = [n for n in roster if query in name_match_key(n)]
        self._refresh_name_combo(roster)
        if select_first and roster:
            self._ignore_name = True
            self.name_var.set(roster[0])
            self.name_combo.set(roster[0])
            self._ignore_name = False
            self._on_name_change()

    def _on_view_change(self, _value=None) -> None:
        self._apply_editor(save=True)
        if self._view_by_name() and not self._selected_employee():
            roster = self._all_employee_names()
            if roster:
                self._ignore_name = True
                self.name_var.set(roster[0])
                self.name_combo.set(roster[0])
                self._ignore_name = False
        self._render_table()

    def _on_name_change(self, _value=None) -> None:
        if getattr(self, "_ignore_name", False):
            return
        self._apply_editor(save=True)
        self._render_table()

    def _on_day_change(self, _value=None) -> None:
        self._apply_editor(save=True)
        self.current_day = self._selected_day()
        self._render_table()

    def _on_filter_change(self, _value=None) -> None:
        self._apply_editor(save=True)
        self._render_table()

    def _filtered_rows(self) -> pd.DataFrame:
        if self.df is None:
            return pd.DataFrame()
        if self._view_by_name():
            name = self._selected_employee()
            if not name:
                return pd.DataFrame()
            all_rows = rows_for_employee(self.df, name)
        else:
            if self.current_day is None:
                return pd.DataFrame()
            all_rows = rows_for_day(self.df, self.current_day, missing_only=False)
            picked = self._selected_employee()
            if picked and not all_rows.empty:
                all_rows = all_rows[all_rows["employee_name"].map(lambda v: name_match_key(v) == name_match_key(picked))]
        if all_rows.empty:
            return all_rows
        
        # Shift filter (NEW)
        shift_filter = self.shift_filter_var.get() if hasattr(self, 'shift_filter_var') else "Tất cả ca"
        if shift_filter == "Ca 8 tiếng":
            all_rows = all_rows[all_rows["standard_shift_hours"].fillna(8.0).astype(float) <= 10.0]
        elif shift_filter == "Ca 12 tiếng":
            all_rows = all_rows[all_rows["standard_shift_hours"].fillna(8.0).astype(float) > 10.0]
        
        mode = self.filter_var.get()
        if mode in {"Tất cả", "Tất cả ngày này"}:
            return all_rows
        if mode == "Đã sửa tay":
            if "manual_edit" in all_rows.columns:
                return all_rows[all_rows["manual_edit"].fillna(False).astype(bool)]
            return all_rows.iloc[0:0]
        if mode == "Thiếu giờ":
            return all_rows[all_rows["_issue"].astype(str).str.len() > 0]
        if mode == "Chưa đạt mốc":
            return all_rows[~all_rows["_reached"].astype(bool)]
        return all_rows[all_rows["_needs_review"].astype(bool)]

    def _render_table(self) -> None:
        self._ignore_tree = True
        self.tree.delete(*self.tree.get_children())
        if self.df is None:
            self.info_var.set("Chưa có dữ liệu.")
            self._ignore_tree = False
            return
        if self._counts is None:
            self._counts = review_counts_by_day(self.df)
        part = self._filtered_rows()
        if self._view_by_name():
            name = self._selected_employee() or "—"
            review = int(part["_needs_review"].astype(bool).sum()) if not part.empty else 0
            missing = int((part["_issue"].astype(str).str.len() > 0).sum()) if not part.empty else 0
            short = int((~part["_reached"].astype(bool)).sum()) if not part.empty else 0
            self.info_var.set(
                f"Kỳ {self.month:02d}/{self.year} · {name} · "
                f"{len(part)} ngày · {review} cần xem ({missing} thiếu giờ, {short} chưa đủ mốc)"
            )
        else:
            if self.current_day is None:
                self.info_var.set("Chưa có dữ liệu.")
                self._ignore_tree = False
                return
            review, missing, short = self._counts.get(self.current_day, day_review_count(self.df, self.current_day))
            who = self._selected_employee()
            suffix = f" · {who}" if who else ""
            self.info_var.set(
                f"Kỳ {self.month:02d}/{self.year} · {self.current_day.strftime('%d/%m')}{suffix} · "
                f"{review} cần xem ({missing} thiếu giờ, {short} chưa đủ 8h/12h)"
            )
        first = None
        self._row_meta = {}
        self._hover_iid = ""
        row_index = 0
        for _, rec in part.iterrows():
            name = str(rec.get("employee_name") or "")
            if not name:
                continue
            day = self._work_date(rec.get("date")) or self.current_day
            issue = rec.get("_issue") or ""
            info = milestone_from_clocks(
                rec.get("in1"), rec.get("out1"), rec.get("in2"), rec.get("out2"),
                rec.get("standard_shift_hours"), rec.get("lunch_duration_hours"),
                overnight=bool(rec.get("overnight")),
                work_date=day,
            )
            iid = self._row_iid(name, day)
            self._row_meta[iid] = (
                rec.get("standard_shift_hours"),
                rec.get("lunch_duration_hours"),
                bool(rec.get("overnight")),
            )
            tag = self._status_tag(issue, info["reached"])
            
            # Check if 12h shift needs lunch 補充 (yellow highlighting)
            needs_lunch_補充 = False
            shift_hours = float(rec.get("standard_shift_hours") or 8.0)
            lunch_hours = float(rec.get("lunch_duration_hours") or 0.0)
            if shift_hours > 10.0:  # 12h shift
                # Check if only 2 punches (no lunch break clocked)
                has_in1 = rec.get("in1") is not None
                has_out1 = rec.get("out1") is not None
                has_in2 = rec.get("in2") is not None
                has_out2 = rec.get("out2") is not None
                punch_count = sum([has_in1, has_out1, has_in2, has_out2])
                # If only 2 punches and didn't reach target, needs lunch 補充
                if punch_count == 2 and not info["reached"] and lunch_hours > 0:
                    needs_lunch_補充 = True
            
            inserted = self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    day.strftime("%d/%m/%Y") if day else "",
                    name,
                    issue or "Đủ mốc chấm",
                    clock_text(rec.get("in1"), rec.get("in1_plus") or 0),
                    clock_text(rec.get("out1"), rec.get("out1_plus") or 0),
                    clock_text(rec.get("in2"), rec.get("in2_plus") or 0),
                    clock_text(rec.get("out2"), rec.get("out2_plus") or 0),
                    info["fraction"],
                    info["badge"],
                    info["cong_text"],
                ),
                tags=self._row_tags(row_index, tag, needs_lunch_補充=needs_lunch_補充),
            )
            row_index += 1
            if first is None:
                first = inserted
        if first:
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.tree.see(first)
            self._load_editor_from_tree(first)
        else:
            self._editing_name = ""
            self._editing_day = None
            self.edit_name_var.set("Không có dòng với bộ lọc này. Dùng Thêm người nếu cần.")
            for entry in (self.e_in1, self.e_out1, self.e_in2, self.e_out2):
                entry.delete(0, "end")
            self.overnight_var.set(False)
            self.hours_var.set("")
            self.badge_var.set("")
            self.cong_var.set("")
        self._ignore_tree = False

    def _on_tree_select(self, _event=None) -> None:
        if self._ignore_tree:
            return
        self._apply_editor(save=True)
        selected = self.tree.selection()
        if selected:
            self._load_editor_from_tree(selected[0])

    def _load_editor_from_tree(self, iid: str) -> None:
        values = self.tree.item(iid, "values")
        if not values:
            return
        self._ignore_editor = True
        name, day = self._parse_iid(iid)
        if not name and len(values) > 1:
            name = str(values[1])
        if day is None and values:
            try:
                day = datetime_from_dmy(str(values[0]))
            except ValueError:
                day = self.current_day
        self._editing_name = name
        self._editing_day = day
        if day:
            self.current_day = day
        label_day = day.strftime("%d/%m/%Y") if day else ""
        self.edit_name_var.set(f"{name}  ·  {label_day}" if label_day else name)
        clocks = values[3:7]
        for entry, text in zip((self.e_in1, self.e_out1, self.e_in2, self.e_out2), clocks):
            entry.delete(0, "end")
            entry.insert(0, text)
        overnight = False
        if iid in self._row_meta and len(self._row_meta[iid]) > 2:
            overnight = bool(self._row_meta[iid][2])
        if not overnight:
            overnight = any("(+1)" in str(t) for t in clocks)
        self.overnight_var.set(overnight)
        self._ignore_editor = False
        self._on_edit_change()

    def _selected_name(self) -> str:
        return (self._editing_name or "").strip()

    def _editor_clocks(self):
        return (
            parse_time_value(self.e_in1.get()),
            parse_time_value(self.e_out1.get()),
            parse_time_value(self.e_in2.get()),
            parse_time_value(self.e_out2.get()),
        )

    def _fields_stable(self) -> bool:
        for entry in (self.e_in1, self.e_out1, self.e_in2, self.e_out2):
            text = entry.get().strip()
            if text and parse_time_value(text) is None:
                return False
        return True

    def _row_standard_lunch(self, name: str):
        iid = self._row_iid(name, self._editing_day or self.current_day)
        if iid in self._row_meta:
            meta = self._row_meta[iid]
            return meta[0], meta[1]
        if name in self._row_meta:
            meta = self._row_meta[name]
            return meta[0], meta[1]
        return 8.0, None

    def _editor_overnight(self) -> bool:
        if bool(self.overnight_var.get()):
            return True
        return any("(+1)" in entry.get() for entry in (self.e_in1, self.e_out1, self.e_in2, self.e_out2))

    def _on_overnight_toggle(self) -> None:
        if self._ignore_editor:
            return
        self._on_edit_change()
        if self._fields_stable():
            self._apply_editor(save=True)

    def _on_edit_change(self) -> None:
        name = self._selected_name()
        if not name or name.startswith("Không có") or name.startswith("Chọn"):
            return
        in1, out1, in2, out2 = self._editor_clocks()
        standard, lunch = self._row_standard_lunch(name)
        info = milestone_from_clocks(
            in1, out1, in2, out2, standard, lunch,
            overnight=self._editor_overnight(),
            work_date=self._editing_day or self.current_day,
        )
        self.hours_var.set(info["fraction"])
        self.badge_var.set(info["badge"])
        self.cong_var.set(info["cong_text"])
        color = ("#276749", "#9AE6B4") if info["reached"] else ("#C53030", "#FEB2B2")
        self.hours_lbl.configure(text_color=color)
        self.badge_lbl.configure(text_color=color)

    def _period_locked(self) -> bool:
        if self.year is None or self.month is None:
            return False
        return is_month_locked(month_key(self.year, self.month))

    def _apply_editor(self, save: bool = False) -> None:
        day = self._editing_day or self.current_day
        if not save or self.df is None or day is None:
            return
        if self._period_locked():
            messagebox.showwarning(
                self.app.title(),
                f"Kỳ {month_key(self.year, self.month)} đã chốt công. Không sửa giờ.",
            )
            return
        name = self._selected_name()
        if not name:
            return
        if not self._fields_stable():
            return
        in1, out1, in2, out2 = self._editor_clocks()
        overnight = self._editor_overnight()
        self.df = upsert_clocks(
            self.df,
            name,
            day,
            in1,
            out1,
            in2,
            out2,
            master=self._master(),
            manual=True,
            overnight=overnight,
        )
        if self._counts is not None:
            self._counts[day] = day_review_count(self.df, day)
        iid = self._row_iid(name, day)
        if iid in self.tree.get_children():
            part = rows_for_day(self.df, day, missing_only=False)
            hit = part[part["employee_name"].astype(str) == name]
            if not hit.empty:
                rec = hit.iloc[0]
                issue = rec.get("_issue") or ""
                info = milestone_from_clocks(
                    rec.get("in1"), rec.get("out1"), rec.get("in2"), rec.get("out2"),
                    rec.get("standard_shift_hours"), rec.get("lunch_duration_hours"),
                    overnight=bool(rec.get("overnight")),
                    work_date=day,
                )
                self._row_meta[iid] = (
                    rec.get("standard_shift_hours"),
                    rec.get("lunch_duration_hours"),
                    bool(rec.get("overnight")),
                )
                tag = self._status_tag(issue, info["reached"])
                self.tree.item(
                    iid,
                    values=(
                        day.strftime("%d/%m/%Y"),
                        name,
                        issue or "Đủ mốc chấm",
                        clock_text(rec.get("in1"), rec.get("in1_plus") or 0),
                        clock_text(rec.get("out1"), rec.get("out1_plus") or 0),
                        clock_text(rec.get("in2"), rec.get("in2_plus") or 0),
                        clock_text(rec.get("out2"), rec.get("out2_plus") or 0),
                        info["fraction"],
                        info["badge"],
                        info["cong_text"],
                    ),
                    tags=self._tags_for_iid(iid, tag),
                )
        if self.year is not None and self.month is not None:
            persist_attendance_from_merged(self.df, self.year, self.month)
        self._on_edit_change()
        self._refresh_exceptions()

    def _add_person(self) -> None:
        if self.df is None or self.current_day is None:
            messagebox.showwarning(self.app.title(), "Hãy tải file K9 / dữ liệu sửa giờ trước.")
            return
        names = roster_names(self.df, self._master())
        if not names:
            messagebox.showwarning(self.app.title(), "Không có danh sách nhân viên (cần master hoặc file K9).")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Thêm người để sửa giờ")
        dialog.geometry("420x160")
        dialog.transient(self.winfo_toplevel())
        ctk.CTkLabel(dialog, text=f"Ngày {self.current_day.strftime('%d/%m/%Y')}").pack(pady=(16, 6))
        combo = ctk.CTkComboBox(dialog, values=names, width=360)
        combo.set(names[0])
        combo.pack(pady=6)

        def confirm() -> None:
            name = combo.get().strip()
            dialog.destroy()
            if not name:
                return
            self._apply_editor(save=True)
            self.df = upsert_clocks(
                self.df, name, self.current_day, None, None, None, None, master=self._master(), manual=True
            )
            self._counts = None
            self.filter_var.set("Đã sửa tay")
            self.filter_combo.set("Đã sửa tay")
            self._ignore_name = True
            self.name_var.set(name)
            self.name_combo.set(name)
            self._ignore_name = False
            self._render_table()
            iid = self._row_iid(name, self.current_day)
            if iid in self.tree.get_children():
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self._load_editor_from_tree(iid)
            self.app._append_log(f"Thêm {name} vào {self.current_day.isoformat()} để sửa giờ.")

        ctk.CTkButton(dialog, text="Thêm", command=confirm).pack(pady=12)

    def _save_all(self) -> None:
        if self.df is None or self.year is None:
            messagebox.showwarning(self.app.title(), "Chưa có dữ liệu sửa giờ.")
            return
        if self._period_locked():
            messagebox.showwarning(
                self.app.title(),
                f"Kỳ {month_key(self.year, self.month)} đã chốt công. Không xuất lại báo cáo.",
            )
            return
        output = self.app.output_var.get().strip()
        if not output:
            messagebox.showwarning(self.app.title(), "Hãy chọn thư mục xuất báo cáo.")
            return
        self._apply_editor(save=True)
        try:
            work_start = parse_work_start(self.app.work_start_var.get())
        except ValueError as exc:
            messagebox.showwarning(self.app.title(), str(exc))
            return
        self.app._set_busy(True)
        self.app.status_var.set("Đang cập nhật báo cáo...")
        df = self.df.copy()
        year, month = self.year, self.month

        def job() -> None:
            def log(message: str) -> None:
                self.app._log_queue.put(("log", message))

            try:
                persist_attendance_from_merged(df, year, month)
                paths = reexport_corrected(df, output, year, month, work_start=work_start, log=log)
                self.app._log_queue.put(("status", "Đã cập nhật báo cáo"))
                self.app._log_queue.put(("done", {"paths": paths, "merged": df}))
            except Exception as exc:  # noqa: BLE001
                log(traceback.format_exc())
                self.app._log_queue.put(("error", str(exc)))
                self.app._log_queue.put(("status", "Lỗi"))

        threading.Thread(target=job, daemon=True).start()


def datetime_from_dmy(text: str) -> date:
    from datetime import datetime as dt

    return dt.strptime(text.strip(), "%d/%m/%Y").date()
