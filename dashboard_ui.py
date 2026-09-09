"""Tab Bảng điều khiển & Chốt công: monthly metrics, payroll lock, MoM variance."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from processing.database import (
    backup_database,
    employee_month_stats,
    get_setting,
    is_month_locked,
    list_attendance_months,
    list_employees,
    lock_info,
    lock_month,
    month_key,
    month_totals,
    previous_month_key,
    set_setting,
    unlock_month,
)
from processing.leave import (
    approved_leave_days_in_month,
    company_remaining_leave,
    leave_balance,
    leave_period_labels,
    used_leave_in_month,
)
from processing.utils import name_match_key
from processing.variance import export_mom_excel, mom_variance
from tree_theme import SEARCH_PLACEHOLDER, apply_tree_style, bind_hover, employee_search_match, stripe_tags


def _default_month() -> str:
    saved = get_setting("last_dashboard_month")
    if saved:
        return saved
    months = list_attendance_months()
    if months:
        return months[0]
    today = date.today()
    return month_key(today.year, today.month)


class DashboardPanel(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.month_var = ctk.StringVar(value=_default_month())
        self.lock_var = ctk.StringVar(value="")
        self.m_work = ctk.StringVar(value="0")
        self.m_ot = ctk.StringVar(value="0")
        self.m_leave = ctk.StringVar(value="0")
        self.m_remain = ctk.StringVar(value="0")
        self.mom_info_var = ctk.StringVar(value="")
        self.only_delta_var = ctk.BooleanVar(value=True)
        self.search_var = ctk.StringVar(value="")
        self.mom_search_var = ctk.StringVar(value="")
        self._lock_rows: list[dict] = []
        self._mom_rows = None
        self._build()
        self.refresh()

    def _build(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(bar, text="Kỳ chấm công", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.month_combo = ctk.CTkComboBox(
            bar, values=self._month_choices(), variable=self.month_var, width=140, command=self._on_month
        )
        self.month_combo.pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Xem kỳ này", width=110, command=self.refresh).pack(side="left")
        ctk.CTkButton(bar, text="Sao lưu CSDL", width=130, command=self._backup_database).pack(side="left", padx=8)
        ctk.CTkLabel(bar, textvariable=self.lock_var, text_color=("#C53030", "#FC8181")).pack(side="right")

        self.inner = ctk.CTkTabview(self)
        self.inner.pack(fill="both", expand=True)
        self.inner.add("Chốt công")
        self.inner.add("Phân tích biến động (MoM)")
        self._build_lock_tab(self.inner.tab("Chốt công"))
        self._build_mom_tab(self.inner.tab("Phân tích biến động (MoM)"))

    def _build_lock_tab(self, tab) -> None:
        cards = ctk.CTkFrame(tab, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 8))
        cards.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self._metric_card(cards, 0, "Tổng công (tháng)", self.m_work)
        self._metric_card(cards, 1, "Tổng giờ OT", self.m_ot)
        self._metric_card(cards, 2, f"Tổng {leave_period_labels()['used']}", self.m_leave)
        self._metric_card(cards, 3, f"Tổng {leave_period_labels()['remain']} toàn công ty", self.m_remain)

        search_row = ctk.CTkFrame(tab, fg_color="transparent")
        search_row.pack(fill="x", pady=(0, 8))
        self.search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self.search_var,
            placeholder_text=SEARCH_PLACEHOLDER,
            height=36,
        )
        self.search_entry.pack(fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self._on_search)

        table_wrap = ctk.CTkFrame(tab, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("code", "name", "workdays", "ot", "used", "remain")
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=12)
        headings = {
            "code": ("Mã NV", 90),
            "name": ("Tên NV", 240),
            "workdays": ("Công", 90),
            "ot": ("OT (giờ)", 90),
            "used": (leave_period_labels()["used"], 120),
            "remain": (leave_period_labels()["remain"], 120),
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

        bottom = ctk.CTkFrame(tab, corner_radius=12)
        bottom.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            bottom,
            text="Nạp dữ liệu từ file Chi tiết (Sheet Tổng hợp)",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2B6CB0",
            hover_color="#2C5282",
            command=self._import_history,
        ).pack(fill="x", padx=16, pady=(14, 0))
        self.lock_btn = ctk.CTkButton(
            bottom,
            text="CHỐT CÔNG THÁNG NÀY",
            height=48,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#C53030",
            hover_color="#9B2C2C",
            command=self._toggle_lock,
        )
        self.lock_btn.pack(fill="x", padx=16, pady=14)

    def _build_mom_tab(self, tab) -> None:
        tools = ctk.CTkFrame(tab, fg_color="transparent")
        tools.pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(tools, textvariable=self.mom_info_var, text_color=("#2B6CB0", "#90CDF4")).pack(side="left")
        ctk.CTkCheckBox(
            tools,
            text="Chỉ hiển thị nhân viên có biến động",
            variable=self.only_delta_var,
            command=self._render_mom,
        ).pack(side="right", padx=8)
        ctk.CTkButton(tools, text="Xuất báo cáo chênh lệch", width=200, command=self._export_mom).pack(side="right")

        mom_search = ctk.CTkFrame(tab, fg_color="transparent")
        mom_search.pack(fill="x", pady=(0, 8))
        self.mom_search_entry = ctk.CTkEntry(
            mom_search,
            textvariable=self.mom_search_var,
            placeholder_text=SEARCH_PLACEHOLDER,
            height=36,
        )
        self.mom_search_entry.pack(fill="x", expand=True)
        self.mom_search_entry.bind("<KeyRelease>", self._on_mom_search)

        table_wrap = ctk.CTkFrame(tab, corner_radius=12)
        table_wrap.pack(fill="both", expand=True, pady=(0, 8))
        inner = tk.Frame(table_wrap, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=8, pady=8)
        columns = ("code", "name", "prev", "now", "delta", "note")
        self.mom_tree = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse", height=16)
        headings = {
            "code": ("Mã NV", 90),
            "name": ("Tên NV", 220),
            "prev": ("Công tháng trước", 130),
            "now": ("Công tháng này", 130),
            "delta": ("Chênh lệch", 110),
            "note": ("Ghi chú", 260),
        }
        apply_tree_style(self.mom_tree)
        self.mom_tree.tag_configure("up", foreground="#276749")
        self.mom_tree.tag_configure("down", foreground="#C53030")
        for key, (title, width) in headings.items():
            self.mom_tree.heading(key, text=title, anchor="center")
            self.mom_tree.column(key, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.mom_tree.yview)
        self.mom_tree.configure(yscrollcommand=scroll.set)
        self.mom_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        bind_hover(self.mom_tree)

    def _metric_card(self, parent, column: int, title: str, variable) -> None:
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=0, column=column, padx=6, sticky="ew")
        ctk.CTkLabel(card, text=title, text_color=("#4A5568", "#A0AEC0")).pack(anchor="w", padx=14, pady=(12, 0))
        ctk.CTkLabel(card, textvariable=variable, font=ctk.CTkFont(size=26, weight="bold")).pack(
            anchor="w", padx=14, pady=(4, 14)
        )

    def _month_choices(self) -> list[str]:
        today = date.today()
        choices = [month_key(today.year, today.month)]
        for item in list_attendance_months():
            if item not in choices:
                choices.append(item)
        for offset in range(1, 6):
            month = today.month - offset
            year = today.year
            while month <= 0:
                month += 12
                year -= 1
            key = month_key(year, month)
            if key not in choices:
                choices.append(key)
        return choices

    def _on_month(self, _value=None) -> None:
        self.refresh()

    def _import_history(self) -> None:
        from history_ui import pick_and_preview_history

        pick_and_preview_history(self.app)

    def refresh(self) -> None:
        key = self.month_var.get().strip() or _default_month()
        self.month_var.set(key)
        self.month_combo.configure(values=self._month_choices())
        set_setting("last_dashboard_month", key)
        totals = month_totals(key)
        leave_taken = approved_leave_days_in_month(key)
        remain_all = company_remaining_leave()
        self.m_work.set(f"{totals['workdays']:.2f}")
        self.m_ot.set(f"{totals['overtime']:.2f}")
        self.m_leave.set(f"{leave_taken:g}")
        self.m_remain.set(f"{remain_all:g}")

        stats = {row["name_key"]: row for row in employee_month_stats(key)}
        year = date.today().year
        cached: list[dict] = []
        for emp in list_employees(active_only=True):
            name_key = emp.get("name_key") or name_match_key(emp.get("employee_name") or "")
            month_row = stats.get(name_key, {})
            balance = leave_balance(emp, year)
            used_month = used_leave_in_month(int(emp["id"]), key)
            cached.append(
                {
                    "employee_id": emp.get("employee_id") or "",
                    "employee_name": emp.get("employee_name") or "",
                    "workdays": float(month_row.get("workdays") or 0),
                    "overtime": float(month_row.get("overtime") or 0),
                    "used": used_month,
                    "remain": balance["remaining"],
                }
            )
        self._lock_rows = cached
        self._render_lock_rows()
        self._refresh_lock_button(key)
        self._refresh_mom(key)

    def _on_search(self, _event=None) -> None:
        self._render_lock_rows()

    def _render_lock_rows(self) -> None:
        query = self.search_var.get()
        self.tree.delete(*self.tree.get_children())
        visible = 0
        for row in self._lock_rows:
            if not employee_search_match(query, row.get("employee_id"), row.get("employee_name")):
                continue
            self.tree.insert(
                "",
                "end",
                values=(
                    row.get("employee_id") or "",
                    row.get("employee_name") or "",
                    f"{float(row.get('workdays') or 0):.2f}",
                    f"{float(row.get('overtime') or 0):.2f}",
                    f"{row.get('used') or 0:g}",
                    f"{row.get('remain') or 0:g}",
                ),
                tags=stripe_tags(visible),
            )
            visible += 1

    def _refresh_mom(self, key: str) -> None:
        prev = previous_month_key(key)
        self._mom_rows = mom_variance(key, prev)
        changed = int((self._mom_rows["variance"] != 0).sum()) if self._mom_rows is not None and not self._mom_rows.empty else 0
        self.mom_info_var.set(f"{prev} → {key}  ·  {changed} nhân viên có chênh lệch công")
        self._render_mom()

    def _on_mom_search(self, _event=None) -> None:
        self._render_mom()

    def _render_mom(self) -> None:
        self.mom_tree.delete(*self.mom_tree.get_children())
        frame = self._mom_rows
        if frame is None or frame.empty:
            return
        view = frame
        if self.only_delta_var.get():
            view = frame[frame["variance"] != 0]
        query = self.mom_search_var.get()
        visible = 0
        for rec in view.itertuples(index=False):
            if not employee_search_match(query, rec.employee_id, rec.employee_name):
                continue
            delta = float(rec.variance)
            extra = "up" if delta > 0 else ("down" if delta < 0 else "")
            self.mom_tree.insert(
                "",
                "end",
                values=(
                    rec.employee_id or "",
                    rec.employee_name or "",
                    f"{float(rec.cong_prev):.2f}",
                    f"{float(rec.cong_now):.2f}",
                    f"{delta:+.2f}",
                    rec.note or "",
                ),
                tags=stripe_tags(visible, extra),
            )
            visible += 1

    def _backup_database(self) -> None:
        from processing.resources import database_path

        src = database_path()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = filedialog.asksaveasfilename(
            title="Sao lưu CSDL",
            defaultextension=".db",
            initialfile=f"{src.stem}_{stamp}.db",
            filetypes=[("SQLite", "*.db"), ("Tất cả", "*.*")],
        )
        if not dest:
            return
        try:
            saved = backup_database(dest)
        except Exception as exc:
            messagebox.showerror(self.app.title(), f"Không sao lưu được CSDL:\n{exc}")
            return
        self.app._append_log(f"Đã sao lưu CSDL → {saved}")
        messagebox.showinfo(self.app.title(), f"Đã sao lưu CSDL:\n{saved}")

    def _export_mom(self) -> None:
        key = self.month_var.get().strip()
        prev = previous_month_key(key)
        frame = self._mom_rows
        if frame is None or frame.empty:
            messagebox.showwarning(self.app.title(), "Chưa có dữ liệu chấm công để so sánh hai tháng.")
            return
        if self.only_delta_var.get():
            frame = frame[frame["variance"] != 0]
        default_dir = self.app.output_var.get().strip() or str(Path.cwd())
        dest = filedialog.asksaveasfilename(
            title="Xuất báo cáo chênh lệch MoM",
            defaultextension=".xlsx",
            initialdir=default_dir,
            initialfile=f"Chenh_lech_cong_{key.replace('/', '-')}.xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not dest:
            return
        path = export_mom_excel(frame, dest, key, prev)
        self.app._append_log(f"Đã xuất MoM: {path.name}")
        messagebox.showinfo(self.app.title(), f"Đã xuất:\n{path}")

    def _refresh_lock_button(self, key: str) -> None:
        info = lock_info(key)
        if info and info.get("is_locked"):
            self.lock_var.set(f"ĐÃ CHỐT · {info.get('locked_at') or ''} · {info.get('locked_by') or 'HR'}")
            self.lock_btn.configure(text="ĐÃ CHỐT — bấm để mở khóa (xác nhận)", fg_color="#2F855A", hover_color="#276749")
        else:
            self.lock_var.set("Chưa chốt — vẫn có thể chạy phân tích / sửa giờ.")
            self.lock_btn.configure(text="CHỐT CÔNG THÁNG NÀY", fg_color="#C53030", hover_color="#9B2C2C")

    def _toggle_lock(self) -> None:
        key = self.month_var.get().strip()
        if is_month_locked(key):
            if not messagebox.askyesno(self.app.title(), f"Mở khóa kỳ {key}? Phân tích sẽ được phép ghi đè lại."):
                return
            unlock_month(key)
            self.app._append_log(f"Đã mở khóa kỳ {key}.")
        else:
            if not messagebox.askyesno(
                self.app.title(),
                f"Chốt công kỳ {key}?\nSau khi chốt, không chạy phân tích và không sửa giờ tháng này.",
            ):
                return
            lock_month(key, locked_by="HR")
            self.app._append_log(f"Đã chốt công kỳ {key}.")
        self.refresh()
