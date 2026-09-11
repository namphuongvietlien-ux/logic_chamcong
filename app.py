"""Desktop GUI: fingerprint Excel + photo OCR attendance processor."""

from __future__ import annotations

import os
import sys

# Must run before NumPy/Torch: duplicate OpenMP aborts the frozen exe (window just closes).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

if getattr(sys, "frozen", False):
    import multiprocessing

    multiprocessing.freeze_support()

def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

def _safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        raw = (str(message) + "\n").encode("utf-8", errors="replace")
        try:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
        except Exception:
            sys.__stdout__.write(str(message).encode("ascii", "replace").decode("ascii") + "\n")

_configure_stdio()

import argparse
import ctypes
import faulthandler
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path

try:
    _crash_path = (
        Path(sys.executable).resolve().parent / "crash.log"
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent / "crash.log"
    )
    _crash_fh = open(_crash_path, "a", encoding="utf-8")
    faulthandler.enable(file=_crash_fh, all_threads=True)
    sys._chamcong_crash_log = _crash_fh  # noqa: SLF001
except Exception:
    pass

import customtkinter as ctk
from tkinter import filedialog, messagebox

from processing.database import (
    backup_database,
    employees_frame,
    import_employees_from_excel,
    init_db,
    is_month_locked,
    month_key,
)
from processing.folders import create_employee_folders
from processing.holidays import ensure_holiday_years
from processing.pipeline import parse_work_start, run_pipeline
from processing.resources import database_path
from correction_ui import CorrectionPanel
from dashboard_ui import DashboardPanel
from employee_ui import EmployeePanel
from holiday_ui import HolidayPanel
from leave_ui import LeavePanel
from auto_updater import CURRENT_VERSION, prompt_update_check, schedule_update_check

APP_TITLE = "Đối soát chấm công"


def _install_crash_log() -> None:
    """Attach Python exception hooks; faulthandler is enabled at import."""
    handle = getattr(sys, "_chamcong_crash_log", None)
    if handle is None:
        return

    def _hook(exc_type, exc, tb) -> None:
        try:
            handle.write("".join(traceback.format_exception(exc_type, exc, tb)))
            handle.flush()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT_DIR = _app_dir()
DEFAULT_EXCEL = ROOT_DIR / "CÔNG CHECK VÂN TAY THÁNG.xlsx"
DEFAULT_MASTER = ROOT_DIR / "Employee_Master_Data.xlsx"
DEFAULT_IMAGES = ROOT_DIR / "Images"
DEFAULT_OUTPUT = ROOT_DIR / "output"


def _enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class AttendanceApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x920")
        self.minsize(1080, 720)
        self.configure(fg_color=("#F4F7FB", "#1A1D23"))

        self._log_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_poll = False

        self.excel_var = ctk.StringVar(value=str(DEFAULT_EXCEL if DEFAULT_EXCEL.exists() else ""))
        self.master_var = ctk.StringVar(value=str(DEFAULT_MASTER if DEFAULT_MASTER.exists() else ""))
        self.images_var = ctk.StringVar(value=str(DEFAULT_IMAGES if DEFAULT_IMAGES.exists() else ""))
        self.output_var = ctk.StringVar(value=str(DEFAULT_OUTPUT))
        self.work_start_var = ctk.StringVar(value="08:00")
        self.skip_ocr_var = ctk.BooleanVar(value=False)
        self.delete_images_var = ctk.BooleanVar(value=False)
        self.status_var = ctk.StringVar(value="Sẵn sàng")
        self.alert_var = ctk.StringVar(value="")

        self._bootstrap_store()
        self._build_ui()
        self.after(120, self._poll_log)
        schedule_update_check(self)

    def _bootstrap_store(self) -> None:
        init_db()
        ensure_holiday_years()
        if employees_frame(active_only=False).empty and DEFAULT_MASTER.exists():
            try:
                import_employees_from_excel(DEFAULT_MASTER)
            except Exception:
                pass

    def _build_ui(self) -> None:
        pad = {"padx": 18, "pady": (16, 8)}
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", **pad)
        ctk.CTkLabel(
            header,
            text="Đối soát chấm công vân tay + ảnh",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
        ).pack(anchor="w")
        sub = ctk.CTkFrame(header, fg_color="transparent")
        sub.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            sub,
            text="Phân tích  ·  Ngoại lệ  ·  Nhân viên  ·  Phép  ·  Ngày lễ  ·  Chốt công",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=("#4A5568", "#A0AEC0"),
        ).pack(side="left")
        ctk.CTkButton(
            sub,
            text="Cập nhật",
            width=100,
            command=lambda: prompt_update_check(self),
        ).pack(side="right")
        ctk.CTkLabel(
            sub,
            text=f"v{CURRENT_VERSION}",
            text_color=("#4A5568", "#A0AEC0"),
        ).pack(side="right", padx=10)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        self.tabs.add("Chạy Phân Tích")
        self.tabs.add("Xử lý Ngoại lệ")
        self.tabs.add("Nhân viên")
        self.tabs.add("Quản lý Phép")
        self.tabs.add("Ngày lễ")
        self.tabs.add("Bảng điều khiển & Chốt công")
        tab1 = self.tabs.tab("Chạy Phân Tích")

        card = ctk.CTkFrame(tab1, corner_radius=16)
        card.pack(fill="x", padx=8, pady=8)

        db_row = ctk.CTkFrame(card, fg_color="transparent")
        db_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            db_row,
            text=f"CSDL nội bộ (giữ file này khi cập nhật exe): {database_path()}",
            text_color=("#4A5568", "#A0AEC0"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            db_row,
            text="Sao lưu CSDL",
            width=130,
            command=self._backup_database,
        ).pack(side="right", padx=(8, 0))
        self._path_row(card, 1, "File Excel vân tay", self.excel_var, self._pick_excel)
        self._path_row(card, 2, "Thư mục ảnh (Images)", self.images_var, self._pick_images)
        self._path_row(card, 3, "Thư mục xuất báo cáo", self.output_var, self._pick_output)

        options = ctk.CTkFrame(card, fg_color="transparent")
        options.grid(row=4, column=0, columnspan=3, sticky="ew", padx=14, pady=(4, 6))
        ctk.CTkLabel(options, text="Giờ vào mặc định (khi NV chưa có trong CSDL):").pack(side="left")
        ctk.CTkEntry(options, textvariable=self.work_start_var, width=80).pack(side="left", padx=(8, 20))
        ctk.CTkCheckBox(
            options,
            text="Bỏ qua OCR (chỉ dùng vân tay)",
            variable=self.skip_ocr_var,
        ).pack(side="left")

        extras = ctk.CTkFrame(card, fg_color="transparent")
        extras.grid(row=5, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 14))
        ctk.CTkCheckBox(
            extras,
            text="Xóa ảnh cũ sau khi xử lý xong",
            variable=self.delete_images_var,
        ).pack(side="left")

        card.grid_columnconfigure(1, weight=1)

        actions = ctk.CTkFrame(tab1, fg_color="transparent")
        actions.pack(fill="x", padx=8, pady=(4, 8))
        self.folder_btn = ctk.CTkButton(
            actions,
            text="Tạo thư mục nhân viên",
            height=42,
            width=200,
            font=ctk.CTkFont(family="Segoe UI", size=14),
            command=self._on_create_folders,
        )
        self.folder_btn.pack(side="left", padx=(0, 8))
        self.run_btn = ctk.CTkButton(
            actions,
            text="Chạy phân tích",
            height=42,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            command=self._on_run,
        )
        self.run_btn.pack(side="left")
        self.progress = ctk.CTkProgressBar(actions, height=16)
        self.progress.pack(side="left", fill="x", expand=True, padx=16)
        self.progress.set(0)
        ctk.CTkLabel(actions, textvariable=self.status_var).pack(side="left")

        self.alert_label = ctk.CTkLabel(
            tab1,
            textvariable=self.alert_var,
            text_color=("#C53030", "#FC8181"),
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            wraplength=1100,
            justify="left",
            anchor="w",
        )
        self.alert_label.pack(fill="x", padx=16, pady=(0, 4))

        log_frame = ctk.CTkFrame(tab1, corner_radius=16)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        ctk.CTkLabel(log_frame, text="Nhật ký xử lý", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=13))
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_box.insert("end", "Chọn file Excel vân tay và thư mục ảnh, rồi nhấn Chạy phân tích.\n")
        self.log_box.tag_config("alert", foreground="#E53E3E")
        self.log_box.configure(state="disabled")

        self.correction = CorrectionPanel(self.tabs.tab("Xử lý Ngoại lệ"), self)
        self.correction.pack(fill="both", expand=True, padx=4, pady=4)
        self.employees = EmployeePanel(self.tabs.tab("Nhân viên"), self)
        self.employees.pack(fill="both", expand=True, padx=4, pady=4)
        self.leave_panel = LeavePanel(self.tabs.tab("Quản lý Phép"), self)
        self.leave_panel.pack(fill="both", expand=True, padx=4, pady=4)
        self.holidays = HolidayPanel(self.tabs.tab("Ngày lễ"), self)
        self.holidays.pack(fill="both", expand=True, padx=4, pady=4)
        self.dashboard = DashboardPanel(self.tabs.tab("Bảng điều khiển & Chốt công"), self)
        self.dashboard.pack(fill="both", expand=True, padx=4, pady=4)
        self.tabs.configure(command=self._on_tab_changed)

    def refresh_roster_views(self, skip: str | None = None) -> None:
        """Mark the other roster tab stale. Do not rebuild until that tab is opened."""
        if skip != "employees" and getattr(self, "employees", None):
            try:
                self.employees.invalidate()
            except Exception:
                pass
        if skip != "leave" and getattr(self, "leave_panel", None):
            try:
                self.leave_panel.invalidate()
            except Exception:
                pass

    def _on_tab_changed(self) -> None:
        name = self.tabs.get()
        if name == "Nhân viên" and getattr(self, "employees", None):
            self.employees.ensure_loaded()
        elif name == "Quản lý Phép" and getattr(self, "leave_panel", None):
            self.leave_panel.ensure_loaded()

    def _path_row(self, parent, row: int, label: str, variable, picker) -> None:
        ctk.CTkLabel(parent, text=label, width=180, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(14, 8), pady=8
        )
        ctk.CTkEntry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=8)
        ctk.CTkButton(parent, text="Chọn...", width=90, command=picker).grid(
            row=row, column=2, padx=(8, 14), pady=8
        )

    def _pick_master(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn Employee_Master_Data.xlsx",
            filetypes=[("Excel", "*.xlsx *.xls"), ("Tất cả", "*.*")],
        )
        if path:
            self.master_var.set(path)

    def _pick_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn file CÔNG CHECK VÂN TAY THÁNG.xlsx",
            filetypes=[("Excel", "*.xlsx *.xls"), ("Tất cả", "*.*")],
        )
        if path:
            self.excel_var.set(path)

    def _pick_images(self) -> None:
        path = filedialog.askdirectory(title="Chọn thư mục Images")
        if path:
            self.images_var.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Chọn thư mục xuất báo cáo")
        if path:
            self.output_var.set(path)

    def _backup_database(self) -> None:
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
            messagebox.showerror(self.title(), f"Không sao lưu được CSDL:\n{exc}")
            return
        self._append_log(f"Đã sao lưu CSDL → {saved}")
        messagebox.showinfo(self.title(), f"Đã sao lưu CSDL:\n{saved}")

    def _append_log(self, message: str, alert: bool = False) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        text = str(message)
        alert = alert or text.startswith("CẢNH BÁO:")
        line = f"[{stamp}] {text}\n"
        self.log_box.configure(state="normal")
        if alert:
            self.log_box.insert("end", line, "alert")
        else:
            self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log(self) -> None:
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    current, total, label = payload
                    fraction = current / total if total else 0
                    self.progress.set(min(max(fraction, 0), 1))
                    self.status_var.set(f"OCR {current}/{total}: {label}")
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "done":
                    self._set_busy(False)
                    self.progress.set(1)
                    payload_paths = payload.get("paths") if isinstance(payload, dict) and "paths" in payload else payload
                    paths = payload_paths or {}
                    daily = paths.get("daily")
                    monthly = paths.get("monthly")
                    if getattr(self, "correction", None):
                        try:
                            merged = payload.get("merged") if isinstance(payload, dict) else None
                            if merged is not None:
                                self.correction.load_from_merged(merged, paths)
                            elif paths.get("working") or paths.get("k9"):
                                self.correction.load_after_analysis(paths)
                        except Exception:
                            pass
                    if getattr(self, "dashboard", None):
                        try:
                            self.dashboard.refresh()
                        except Exception:
                            pass
                    if getattr(self, "leave_panel", None):
                        try:
                            self.leave_panel.invalidate()
                        except Exception:
                            pass
                    messagebox.showinfo(
                        APP_TITLE,
                        "Xuất báo cáo thành công:\n"
                        f"- {daily}\n"
                        f"- {monthly}\n"
                        + (f"- {paths['cham_cong']}\n" if paths.get("cham_cong") else "")
                        + (f"- {paths['chi_tiet']}\n" if paths.get("chi_tiet") else "")
                        + (f"- {paths['k9']}\n" if paths.get("k9") else "")
                        + (f"- {paths['working']}" if paths.get("working") else "")
                        + "\n\nMở thẻ Xử lý Ngoại lệ để chỉnh thiếu vào/ra/ăn trưa rồi cập nhật lại mọi file.",
                    )
                elif kind == "folders_done":
                    self._set_busy(False)
                    self.status_var.set("Đã tạo thư mục nhân viên")
                    messagebox.showinfo(APP_TITLE, str(payload))
                elif kind == "missing_master":
                    message, _names, ack = payload
                    self.alert_var.set(str(message))
                    self.status_var.set("Cảnh báo: thiếu Master Data — bấm OK để tiếp tục")
                    messagebox.showwarning(APP_TITLE, str(message))
                    if ack is not None:
                        ack.set()
                elif kind == "error":
                    self._set_busy(False)
                    self.progress.set(0)
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        if not self._stop_poll:
            self.after(80, self._poll_log)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.run_btn.configure(state=state)
        self.folder_btn.configure(state=state)

    def _on_create_folders(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        master = self.master_var.get().strip()
        images = self.images_var.get().strip()
        if employees_frame(active_only=True).empty:
            messagebox.showwarning(APP_TITLE, "Chưa có nhân viên trong CSDL. Mở thẻ Nhân viên để thêm hoặc nhập Excel.")
            return
        if not images:
            messagebox.showwarning(APP_TITLE, "Hãy chọn thư mục Images.")
            return
        self._set_busy(True)
        self.status_var.set("Đang tạo thư mục nhân viên...")
        self._append_log("Tạo thư mục nhân viên từ master data...")

        def job() -> None:
            def log(message: str) -> None:
                self._log_queue.put(("log", message))

            try:
                stats = create_employee_folders(master, images, log=log)
                self._log_queue.put(
                    (
                        "folders_done",
                        f"Đã kiểm tra thư mục ảnh.\nTạo mới: {stats['created']}\nĐã có: {stats['existed']}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log(traceback.format_exc())
                self._log_queue.put(("error", str(exc)))
                self._log_queue.put(("status", "Lỗi"))

        self._worker = threading.Thread(target=job, daemon=True)
        self._worker.start()

    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        excel = self.excel_var.get().strip()
        images = self.images_var.get().strip()
        output = self.output_var.get().strip()
        if not excel:
            messagebox.showwarning(APP_TITLE, "Hãy chọn file Excel vân tay.")
            return
        if not Path(excel).exists():
            messagebox.showwarning(APP_TITLE, f"Không tìm thấy file:\n{excel}")
            return
        if not output:
            messagebox.showwarning(APP_TITLE, "Hãy chọn thư mục xuất báo cáo.")
            return
        try:
            parse_work_start(self.work_start_var.get())
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        from processing.period import period_from_text

        guessed = period_from_text(Path(excel).stem) or period_from_text(Path(excel).name)
        if guessed and is_month_locked(month_key(*guessed)):
            key = month_key(*guessed)
            messagebox.showwarning(APP_TITLE, f"Kỳ {key} đã chốt công. Không chạy phân tích.")
            return
        self._set_busy(True)
        self.progress.set(0)
        self.alert_var.set("")
        self.status_var.set("Đang chạy (OCR nền, không khóa giao diện)...")
        self._append_log("Bắt đầu tiến trình — OCR đa process + cache; giao diện vẫn dùng được.")
        self._worker = threading.Thread(target=self._run_job, daemon=False)
        self._worker.start()

    def _run_job(self) -> None:
        def log(message: str) -> None:
            self._log_queue.put(("log", message))

        def progress(current: int, total: int, label: str) -> None:
            self._log_queue.put(("progress", (current, total, label)))

        def on_missing_master(names: list, message: str) -> None:
            ack = threading.Event()
            self._log_queue.put(("missing_master", (message, list(names), ack)))
            ack.wait()

        try:
            result = run_pipeline(
                excel_path=self.excel_var.get().strip(),
                images_folder=self.images_var.get().strip() or None,
                output_dir=self.output_var.get().strip(),
                work_start=self.work_start_var.get(),
                skip_ocr=bool(self.skip_ocr_var.get()),
                master_path=self.master_var.get().strip() or None,
                log=log,
                progress=progress,
                delete_images=bool(self.delete_images_var.get()),
                on_missing_master=on_missing_master,
            )
            self._log_queue.put(("status", "Hoàn tất"))
            self._log_queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001 — surface any pipeline failure in the UI
            log(traceback.format_exc())
            self._log_queue.put(("error", str(exc)))
            self._log_queue.put(("status", "Lỗi"))


def run_cli(args: argparse.Namespace) -> int:
    def log(message: str) -> None:
        _safe_print(message)

    def progress(current: int, total: int, label: str) -> None:
        _safe_print(f"OCR {current}/{total}: {label}")

    result = run_pipeline(
        excel_path=args.excel,
        images_folder=args.images,
        output_dir=args.out,
        work_start=args.work_start,
        skip_ocr=args.skip_ocr,
        master_path=args.master,
        log=log,
        progress=progress,
        delete_images=args.delete_images,
    )
    _safe_print("Daily: " + str(result["paths"]["daily"]))
    _safe_print("Monthly: " + str(result["paths"]["monthly"]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--cli", action="store_true", help="Chạy không GUI")
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL if DEFAULT_EXCEL.exists() else ""))
    parser.add_argument("--master", default=str(DEFAULT_MASTER if DEFAULT_MASTER.exists() else ""))
    parser.add_argument("--images", default=str(DEFAULT_IMAGES if DEFAULT_IMAGES.exists() else ""))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--work-start", default="08:00")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--delete-images", action="store_true", help="Xóa jpg/png trong thư mục NV sau khi xuất xong")
    parser.add_argument("--setup-folders", action="store_true", help="Chỉ tạo thư mục nhân viên rồi thoát")
    args, unknown = parser.parse_known_args()
    if unknown and not args.cli:
        pass
    if args.setup_folders:
        from processing.folders import create_employee_folders

        def log(message: str) -> None:
            _safe_print(message)

        create_employee_folders(args.master, args.images, log=log)
        return 0
    if args.cli:
        return run_cli(args)
    _install_crash_log()
    _enable_dpi()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = AttendanceApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    raise SystemExit(main())
