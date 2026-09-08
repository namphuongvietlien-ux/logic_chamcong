"""Reusable calendar popup for CustomTkinter date fields (SQLite y-mm-dd)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional
import tkinter as tk

import customtkinter as ctk

DATE_PATTERN = "y-mm-dd"
PLACEHOLDER = "YYYY-MM-DD"
POPUP_HEIGHT = 280
POPUP_WIDTH = 300


def parse_picker_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


class DatePickerField(ctk.CTkFrame):
    """Entry + calendar button. Selected date is written as YYYY-MM-DD."""

    def __init__(
        self,
        master,
        variable: tk.Variable,
        width: int = 120,
        command: Optional[Callable[[], None]] = None,
        placeholder: str = PLACEHOLDER,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.variable = variable
        self._command = command
        self._popup: ctk.CTkToplevel | None = None
        self.entry = ctk.CTkEntry(
            self,
            textvariable=variable,
            width=width,
            placeholder_text=placeholder,
        )
        self.entry.pack(side="left")
        self.button = ctk.CTkButton(
            self,
            text="📅",
            width=34,
            height=28,
            command=self.open_calendar,
        )
        self.button.pack(side="left", padx=(4, 0))

    def bind(self, sequence=None, command=None, add=True):  # type: ignore[override]
        if add not in ("+", True):
            add = True
        return self.entry.bind(sequence, command, add=True)

    def _popup_origin(self) -> tuple[int, int]:
        """Place the calendar below the field, or above if it would hit the taskbar."""
        widget = self.button
        widget.update_idletasks()
        x = int(widget.winfo_rootx())
        y = int(widget.winfo_rooty())
        h = int(widget.winfo_height())
        screen_height = int(widget.winfo_screenheight())
        screen_width = int(widget.winfo_screenwidth())
        if y + h + POPUP_HEIGHT > screen_height:
            popup_y = y - POPUP_HEIGHT
        else:
            popup_y = y + h
        popup_y = max(0, popup_y)
        if x + POPUP_WIDTH > screen_width:
            x = max(0, screen_width - POPUP_WIDTH)
        return x, popup_y

    def open_calendar(self) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.lift()
            self._popup.focus_force()
            return
        from tkcalendar import Calendar

        current = parse_picker_date(self.variable.get()) or date.today()
        win = ctk.CTkToplevel(self)
        self._popup = win
        win.title("Chọn ngày")
        win.resizable(False, False)
        win.transient(self.winfo_toplevel())
        win.protocol("WM_DELETE_WINDOW", self.close_popup)

        holder = tk.Frame(win, bg="#FFFFFF")
        holder.pack(padx=12, pady=(12, 6))
        cal_kwargs = dict(
            selectmode="day",
            date_pattern=DATE_PATTERN,
            year=current.year,
            month=current.month,
            day=current.day,
            showweeknumbers=False,
            background="white",
            foreground="#1A202C",
            headersbackground="#1F6AA5",
            headersforeground="white",
            selectbackground="#1F6AA5",
            selectforeground="white",
            weekendbackground="#FEE2E2",
            weekendforeground="#9B2C2C",
            othermonthwebackground="#F7FAFC",
            othermonthforeground="#A0AEC0",
        )
        try:
            calendar = Calendar(holder, locale="vi_VN", **cal_kwargs)
        except Exception:
            calendar = Calendar(holder, **cal_kwargs)
        calendar.pack()
        calendar.selection_set(current)
        calendar.bind("<Double-1>", lambda _e: self._apply(calendar))

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(pady=(4, 12))
        ctk.CTkButton(actions, text="Chọn", width=100, command=lambda: self._apply(calendar)).pack(
            side="left", padx=6
        )
        ctk.CTkButton(
            actions,
            text="Hủy",
            width=100,
            fg_color="#718096",
            hover_color="#4A5568",
            command=self.close_popup,
        ).pack(side="left", padx=6)

        x, popup_y = self._popup_origin()
        win.geometry(f"+{x}+{popup_y}")
        win.bind("<Escape>", lambda _e: self.close_popup())
        win.lift()
        win.focus_force()
        win.grab_set()
        win.after(30, win.lift)
        win.after(50, win.focus_force)

    def _apply(self, calendar) -> None:
        self.variable.set(str(calendar.get_date() or ""))
        self.close_popup()
        if self._command:
            self._command()

    def close_popup(self) -> None:
        popup = self._popup
        self._popup = None
        if popup is None:
            return
        try:
            if popup.winfo_exists():
                popup.grab_release()
                popup.destroy()
        except Exception:
            pass

    def _close_popup(self) -> None:
        self.close_popup()
