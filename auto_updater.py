"""In-app OTA updater for the frozen AttendanceApp (PyInstaller onedir).

Architecture: download the GitHub Release asset on a worker thread, then a
Windows ``update.bat`` replaces files after this process exits (batch handoff).

The live SQLite DB (``hr_system.db`` / ``data\\tas.db``) is never deleted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

CURRENT_VERSION = "1.0.6"
GITHUB_OWNER = "namphuongvietlien-ux"
GITHUB_REPO = "logic_chamcong"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
EXE_NAME = "AttendanceApp.exe"
STAGING_EXE = "app_new.exe"
STAGING_ZIP = "_update_package.zip"
STAGING_DIR = "_update_staging"
UPDATE_BAT = "update.bat"
USER_AGENT = "AttendanceApp-Updater"
CHUNK_SIZE = 256 * 1024

LogFn = Optional[Callable[[str], None]]


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return _app_dir() / EXE_NAME


def parse_version(text: str) -> tuple[int, int, int]:
    raw = str(text or "").strip().lstrip("vV")
    nums: list[int] = []
    for part in raw.replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
        if len(nums) == 3:
            break
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def is_newer(remote: str, local: str = CURRENT_VERSION) -> bool:
    return parse_version(remote) > parse_version(local)


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Thiếu thư viện requests. Cài: py -m pip install requests") from exc
    return requests


def _http_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }


def fetch_latest_release() -> dict[str, Any]:
    """Return {version, tag, name, notes, download_url, filename, size, kind}."""
    requests = _requests()
    response = requests.get(GITHUB_API, headers=_http_headers(), timeout=20)
    if response.status_code == 404:
        raise RuntimeError("Chưa có GitHub Release. Hãy tạo Release và đính kèm AttendanceApp.zip.")
    response.raise_for_status()
    payload = response.json()
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag:
        raise RuntimeError("Release trên GitHub không có số phiên bản (tag).")
    assets = payload.get("assets") or []
    chosen = _pick_asset(assets)
    if chosen is None:
        raise RuntimeError(
            "Release chưa có file cài đặt. Đính kèm AttendanceApp.zip "
            "(cả thư mục exe + _internal), không chỉ một file .exe."
        )
    name = str(chosen.get("name") or "")
    kind = "zip" if name.lower().endswith(".zip") else "exe"
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "name": str(payload.get("name") or tag),
        "notes": str(payload.get("body") or "").strip(),
        "download_url": chosen.get("browser_download_url"),
        "filename": name,
        "size": int(chosen.get("size") or 0),
        "kind": kind,
    }


def _pick_asset(assets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    zips = [a for a in assets if str(a.get("name") or "").lower().endswith(".zip")]
    prefer = [a for a in zips if "attendance" in str(a.get("name") or "").lower()]
    if prefer:
        return prefer[0]
    if zips:
        return zips[0]
    exes = [a for a in assets if str(a.get("name") or "").lower().endswith(".exe")]
    return exes[0] if exes else None


def check_for_update() -> Optional[dict[str, Any]]:
    """None when already up to date; otherwise the GitHub release dict."""
    info = fetch_latest_release()
    if not is_newer(info["version"], CURRENT_VERSION):
        return None
    return info


def download_file(
    url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    requests = _requests()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(
        url,
        headers=_http_headers(),
        stream=True,
        timeout=(20, 120),
        allow_redirects=True,
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    tmp.replace(dest)
    if on_progress:
        on_progress(dest.stat().st_size, total or dest.stat().st_size)
    return dest


def _safe_extract_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                raise RuntimeError(f"Zip không hợp lệ: {info.filename}")
        zf.extractall(dest)
    return _find_payload(dest)


def _find_payload(staging: Path) -> Path:
    if (staging / EXE_NAME).is_file():
        return staging
    nested = staging / "AttendanceApp"
    if (nested / EXE_NAME).is_file():
        return nested
    matches = [p for p in staging.rglob(EXE_NAME) if p.is_file()]
    if matches:
        return matches[0].parent
    raise RuntimeError("Gói cập nhật không chứa AttendanceApp.exe. Hãy zip cả thư mục dist\\AttendanceApp.")


def _write_update_bat(*, app_dir: Path, mode: str, payload: Path | None, new_exe: Path | None) -> Path:
    bat = app_dir / UPDATE_BAT
    app_dir_q = str(app_dir)
    exe_q = EXE_NAME
    lines = [
        "@echo off",
        "setlocal",
        "timeout /t 2 /nobreak >nul",
        f'cd /d "{app_dir_q}"',
    ]
    if mode == "exe":
        src = new_exe.name if new_exe is not None else STAGING_EXE
        lines += [
            f'if exist "{exe_q}" del /f /q "{exe_q}"',
            f'ren "{src}" "{exe_q}"',
        ]
    else:
        rel = os.path.relpath(payload or app_dir / STAGING_DIR, app_dir)
        src_exe = f"{rel}\\{exe_q}"
        src_int = f"{rel}\\_internal"
        lines += [
            f'if exist "{src_int}" robocopy "{src_int}" "_internal" /E /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS',
            f'if exist "{src_exe}" copy /y "{src_exe}" "{exe_q}"',
            'if not exist "_internal\\models\\craft_mlt_25k.pth" echo UPDATE_MISSING_OCR_MODELS> update_error.txt',
            f'if exist "{STAGING_DIR}" rmdir /s /q "{STAGING_DIR}"',
            f'if exist "{STAGING_ZIP}" del /f /q "{STAGING_ZIP}"',
        ]
    lines += [
        f'if exist "{exe_q}" start "" "{exe_q}"',
        'del "%~f0"',
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def _launch_bat(bat: Path) -> None:
    creation = 0
    if sys.platform == "win32":
        creation = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(bat.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
        return
    except OSError:
        pass
    os.startfile(str(bat))  # type: ignore[attr-defined]


def _backup_db_quietly() -> None:
    try:
        from processing.database import backup_database

        backup_database()
    except Exception:
        pass


def apply_package_and_restart(info: dict[str, Any], downloaded: Path) -> None:
    """Write update.bat, start it, then exit this process."""
    app_dir = _app_dir()
    _backup_db_quietly()
    kind = info.get("kind") or ("zip" if downloaded.suffix.lower() == ".zip" else "exe")
    payload: Path | None = None
    new_exe: Path | None = None
    if kind == "zip":
        staging = app_dir / STAGING_DIR
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        payload = _safe_extract_zip(downloaded, staging)
        bat = _write_update_bat(app_dir=app_dir, mode="zip", payload=payload, new_exe=None)
    else:
        new_exe = app_dir / STAGING_EXE
        if downloaded.resolve() != new_exe.resolve():
            shutil.copy2(downloaded, new_exe)
        bat = _write_update_bat(app_dir=app_dir, mode="exe", payload=None, new_exe=new_exe)
    _launch_bat(bat)
    sys.exit(0)


def _fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class UpdateWindow:
    """CTkToplevel: progress + MB label, download on a background thread."""

    def __init__(self, master, info: dict[str, Any], auto_start: bool = False) -> None:
        import customtkinter as ctk

        self.master = master
        self.info = info
        self._busy = False
        self.win = ctk.CTkToplevel(master)
        self.win.title("Cập nhật phần mềm")
        self.win.geometry("460x240")
        self.win.resizable(False, False)
        self.win.transient(master)
        self.win.after(50, self.win.lift)

        remote = info.get("version") or "?"
        ctk.CTkLabel(
            self.win,
            text="Cập nhật phần mềm",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            self.win,
            text=f"Bản mới: {remote}   ·   Đang dùng: {CURRENT_VERSION}",
            text_color=("#4A5568", "#A0AEC0"),
        ).pack(anchor="w", padx=20)
        if info.get("kind") == "exe":
            ctk.CTkLabel(
                self.win,
                text="Release chỉ có .exe — OCR/_internal có thể không đổi. Nên đính kèm AttendanceApp.zip.",
                text_color=("#C53030", "#FC8181"),
                wraplength=410,
                justify="left",
            ).pack(anchor="w", padx=20, pady=(6, 0))

        self.status = ctk.CTkLabel(self.win, text="Sẵn sàng tải gói cập nhật.", anchor="w")
        self.status.pack(fill="x", padx=20, pady=(14, 4))
        self.bar = ctk.CTkProgressBar(self.win, height=16)
        self.bar.pack(fill="x", padx=20)
        self.bar.set(0)
        self.mb = ctk.CTkLabel(self.win, text="0.0 MB / -- MB", anchor="w")
        self.mb.pack(fill="x", padx=20, pady=(4, 8))

        btns = ctk.CTkFrame(self.win, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 16))
        self.go = ctk.CTkButton(btns, text="Cập nhật ngay", width=140, command=self._start)
        self.go.pack(side="left")
        ctk.CTkButton(btns, text="Để sau", width=100, fg_color="#718096", command=self.win.destroy).pack(
            side="left", padx=8
        )
        if auto_start:
            self.win.after(200, self._start)

    def _ui(self, fn: Callable[[], None]) -> None:
        try:
            self.win.after(0, fn)
        except Exception:
            pass

    def _start(self) -> None:
        if self._busy:
            return
        if not getattr(sys, "frozen", False):
            self.status.configure(text="Chỉ cập nhật khi chạy bản AttendanceApp.exe đã đóng gói.")
            return
        self._busy = True
        self.go.configure(state="disabled")
        self.status.configure(text="Đang tải...")
        import threading

        threading.Thread(target=self._download_job, daemon=True).start()

    def _download_job(self) -> None:
        url = str(self.info.get("download_url") or "")
        kind = self.info.get("kind") or "zip"
        dest = _app_dir() / (STAGING_ZIP if kind == "zip" else STAGING_EXE)

        def progress(done: int, total: int) -> None:
            frac = (done / total) if total else 0.0
            label = f"{_fmt_mb(done)} / {_fmt_mb(total)}" if total else f"{_fmt_mb(done)} / -- MB"

            def paint() -> None:
                self.bar.set(min(1.0, max(0.0, frac)))
                self.mb.configure(text=label)
                if total:
                    pct = int(frac * 100)
                    self.status.configure(text=f"Đang tải... {pct}%")

            self._ui(paint)

        try:
            download_file(url, dest, on_progress=progress)
        except Exception as exc:
            err = str(exc)

            def fail() -> None:
                self._busy = False
                self.go.configure(state="normal")
                self.status.configure(text=f"Lỗi tải: {err}")

            self._ui(fail)
            return

        def finish() -> None:
            self.bar.set(1)
            self.status.configure(text="Tải xong. Đang cài và khởi động lại...")
            try:
                apply_package_and_restart(self.info, dest)
            except SystemExit:
                os._exit(0)
            except Exception as exc:
                self._busy = False
                self.go.configure(state="normal")
                self.status.configure(text=f"Lỗi cài đặt: {exc}")

        self._ui(finish)


def schedule_update_check(app, delay_ms: int = 2500) -> None:
    """Silent GitHub check after the main window is up (frozen exe only)."""
    if not getattr(sys, "frozen", False):
        return

    def later() -> None:
        import threading

        def job() -> None:
            try:
                info = check_for_update()
            except Exception:
                return
            if info:
                app.after(0, lambda: UpdateWindow(app, info))

        threading.Thread(target=job, daemon=True).start()

    app.after(delay_ms, later)


def prompt_update_check(app) -> None:
    """Manual button: show a dialog even when already current or when offline."""
    from tkinter import messagebox

    def job() -> None:
        try:
            info = check_for_update()
        except Exception as exc:
            app.after(0, lambda e=exc: messagebox.showwarning(app.title(), str(e)))
            return
        if info is None:
            app.after(
                0,
                lambda: messagebox.showinfo(app.title(), f"Bạn đang dùng bản mới nhất ({CURRENT_VERSION})."),
            )
            return
        app.after(0, lambda: UpdateWindow(app, info))

    import threading

    threading.Thread(target=job, daemon=True).start()
