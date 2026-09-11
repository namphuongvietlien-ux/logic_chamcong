"""Module 2: OCR timestamped photos in employee subfolders.

Uses RapidOCR (ONNX Runtime) for frozen builds to avoid torch/EasyOCR PyInstaller crashes.
Dev mode can still use EasyOCR if available, or falls back to RapidOCR.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Optional

CACHE_FILENAME = "ocr_cache.json"
CACHE_SAVE_EVERY = 25
_PROCESS_READER = None

import numpy as np
import pandas as pd
from PIL import Image, ImageOps, UnidentifiedImageError

from processing.resources import resource_path
from processing.utils import parse_time_value

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int, str], None]]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".jfif"}

DATETIME_PATTERNS = [
    re.compile(
        r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})[ T_-]+"
        r"(?P<H>\d{1,2})[:.](?P<M>\d{2})(?:[:.](?P<S>\d{2}))?"
    ),
    re.compile(
        r"(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>\d{4})[ T_-]+"
        r"(?P<H>\d{1,2})[:.](?P<M>\d{2})(?:[:.](?P<S>\d{2}))?"
    ),
    re.compile(
        r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[ T_-]+"
        r"(?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2})"
    ),
]

TIME_ONLY = re.compile(
    r"(?P<H>\d{1,2})\s*[:;.,|]\s*(?P<M>\d{2})(?:\s*[:;.,|]\s*(?P<S>\d{2}))?"
)
# Timemark overlay: "Điểm danh 16:53" / OCR "16 : 53" / "16.53" / "16;53" / "16|53"
TIMEMARK_TIME_RE = re.compile(r"(\d{1,2})\s*[:;.,|]\s*(\d{2})")
TIMEMARK_DATE_RE = re.compile(
    r"(\d{1,2})\s*[Tt]h[aáàảãạ]\s*ng\s*(\d{1,2})\s*[,.]?\s*(\d{4})"
)
# "Điểm" dùng ể — regex cũ [eêế] không khớp nên bỏ qua "Điểm danh 18:49"
DIEM_DANH_TIME_RE = re.compile(
    r"[ĐđDd]\s*i\s*[eêếểẽẹ]\s*m\s*danh\s+(\d{1,2})\s*[:;.,|\s]\s*(\d{2})",
    re.IGNORECASE,
)


def _log(callback: LogFn, message: str) -> None:
    if callback:
        callback(message)


REQUIRED_EASYOCR_MODELS = ("craft_mlt_25k.pth", "latin_g2.pth")
USE_RAPIDOCR = getattr(sys, "frozen", False)


def _model_dir() -> Path:
    """Bundled EasyOCR weights: project/models, or _MEIPASS/models inside the exe."""
    return Path(resource_path("models"))


def _models_ready(model_dir: Path) -> bool:
    if USE_RAPIDOCR:
        return True
    return all((model_dir / name).is_file() for name in REQUIRED_EASYOCR_MODELS)


def _ensure_torchvision_ops() -> None:
    """Register torchvision::nms — missing in frozen worker processes unless imported first."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = str(sys._MEIPASS)
        os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")
    try:
        import torchvision  # noqa: F401
        import torchvision.ops  # noqa: F401
        from torchvision.ops import nms  # noqa: F401
    except Exception:
        pass


def _frozen_onefile() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    return not (Path(sys.executable).resolve().parent / "_internal").is_dir()


def _use_process_pool(job_count: int) -> bool:
    """ProcessPool from a Tk background thread (or a frozen exe) fails on Windows."""
    if job_count <= 1:
        return False
    if getattr(sys, "frozen", False):
        return False
    if threading.current_thread() is not threading.main_thread():
        return False
    return True


def _patch_torch_cpu() -> None:
    """EasyOCR on CPU: skip pin_memory, mute quantize warnings, cap threads so the GUI stays responsive."""
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    _ensure_torchvision_ops()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    warnings.filterwarnings("ignore", message=".*pin_memory.*")
    warnings.filterwarnings("ignore", message=".*quantize_per_tensor.*")
    warnings.filterwarnings("ignore", message=".*Quantizer.cpp.*")
    warnings.filterwarnings("ignore", category=UserWarning, module=r"torch.*")
    try:
        import torch
        from torch.utils.data import DataLoader

        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 4) // 2)))
        if getattr(DataLoader, "_chamcong_cpu", False):
            return

        class _CpuDataLoader(DataLoader):
            _chamcong_cpu = True

            def __init__(self, *args, **kwargs):
                kwargs["pin_memory"] = False
                kwargs["num_workers"] = 0
                super().__init__(*args, **kwargs)

        torch.utils.data.DataLoader = _CpuDataLoader
    except Exception:
        pass


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _cache_path() -> Path:
    return _project_root() / CACHE_FILENAME


def _cache_key(images_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(images_root.resolve()).as_posix()
    except ValueError:
        return f"{path.parent.name}/{path.name}"


def _load_ocr_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_ocr_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(path)


def _datetime_from_cache(entry: Any) -> tuple[Optional[datetime], Optional[str]]:
    """Returns (datetime, photo_code)."""
    if entry is None or not isinstance(entry, dict):
        return None, None
    day = entry.get("date")
    clock = entry.get("time")
    photo_code = entry.get("photo_code")
    if not day or not clock:
        return None, photo_code
    blob = f"{day} {clock}"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(blob, fmt), photo_code
        except ValueError:
            continue
    return None, photo_code


def _cache_payload(dt: Optional[datetime], photo_code: Optional[str] = None) -> Optional[dict[str, str]]:
    if dt is None and photo_code is None:
        return None
    payload = {}
    if dt is not None:
        payload["date"] = dt.strftime("%Y-%m-%d")
        payload["time"] = dt.strftime("%H:%M")
    if photo_code:
        payload["photo_code"] = photo_code
    return payload if payload else None


def _ocr_worker_count() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def _ensure_process_reader():
    """One OCR Reader per process — Reader is not pickle-safe."""
    global _PROCESS_READER
    if _PROCESS_READER is None:
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        if not USE_RAPIDOCR:
            _ensure_torchvision_ops()
            try:
                import torch
                torch.set_num_threads(1)
            except Exception:
                pass
        _PROCESS_READER = _init_reader(log=None)
    return _PROCESS_READER


def ocr_image_job(item: tuple[str, str, str]) -> dict[str, Any]:
    """Picklable worker for ProcessPoolExecutor: (employee, path, cache_key)."""
    employee, path_str, cache_key = item
    path = Path(path_str)
    try:
        reader = _ensure_process_reader()
        dt, raw_text, note, photo_code = _ocr_image(reader, path, log=None)
    except Exception as exc:  # noqa: BLE001
        return {
            "key": cache_key,
            "employee": employee,
            "path": path_str,
            "date": None,
            "time": None,
            "datetime": None,
            "note": f"OCR lỗi: {exc}",
            "raw_text": "",
            "photo_code": None,
            "ok": False,
        }
    return {
        "key": cache_key,
        "employee": employee,
        "path": path_str,
        "date": dt.strftime("%Y-%m-%d") if dt else None,
        "time": dt.strftime("%H:%M") if dt else None,
        "datetime": dt,
        "note": note or "",
        "raw_text": (raw_text or "")[:500],
        "photo_code": photo_code,
        "ok": dt is not None,
    }


def _init_reader(log: LogFn = None):
    """Initialize OCR reader: RapidOCR for frozen, EasyOCR for dev (with fallback)."""
    if USE_RAPIDOCR:
        _log(log, "Khởi tạo RapidOCR (ONNX Runtime) cho bản frozen...")
        try:
            from rapidocr import RapidOCR
            reader = RapidOCR()
            _log(log, "RapidOCR sẵn sàng (ONNX, không dùng torch).")
            return reader
        except ImportError as exc:
            raise RuntimeError(
                "Thiếu RapidOCR. Cài: py -m pip install rapidocr onnxruntime"
            ) from exc
    
    _patch_torch_cpu()
    model_storage = _model_dir()
    offline = _models_ready(model_storage)
    
    try:
        if offline:
            _log(log, f"Nạp mô hình EasyOCR offline từ {model_storage}")
        else:
            _log(log, f"Tải mô hình EasyOCR vào {model_storage} (chỉ lần đầu, cần mạng)...")
            model_storage.mkdir(parents=True, exist_ok=True)
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import easyocr
            reader = easyocr.Reader(
                ["vi", "en"],
                gpu=False,
                verbose=False,
                model_storage_directory=str(model_storage),
                download_enabled=not offline,
            )
        _log(log, "Mô hình EasyOCR sẵn sàng.")
        return reader
    except ImportError:
        _log(log, "EasyOCR không có, thử RapidOCR...")
        try:
            from rapidocr import RapidOCR
            reader = RapidOCR()
            _log(log, "RapidOCR sẵn sàng (fallback từ EasyOCR).")
            return reader
        except ImportError as exc:
            raise RuntimeError(
                "Không có EasyOCR hoặc RapidOCR. Cài một trong hai."
            ) from exc


def _load_cv_image(path: Path):
    """Load BGR image. Never use cv2.imread on Windows — it fails on Unicode paths."""
    import cv2

    image = None
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size:
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        image = None
    if image is None:
        try:
            with Image.open(path) as pil:
                pil = ImageOps.exif_transpose(pil).convert("RGB")
                image = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except (OSError, UnidentifiedImageError, ValueError):
            return None
    if image is None:
        return None
    return np.ascontiguousarray(image)


def preprocess_image(img_path: str | Path):
    """Grayscale + 2x upscale + contrast for small Timemark digits on yellow overlay."""
    try:
        img = _load_cv_image(Path(img_path))
        if img is None:
            return None
        return preprocess_roi(img)
    except Exception:
        return None


def preprocess_roi(img):
    """Enhance a (usually cropped) overlay so small digits are readable."""
    import cv2

    try:
        if img is None or getattr(img, "size", 0) == 0:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        height, width = gray.shape[:2]
        scale = 3.0 if max(height, width) < 900 else 2.0
        resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        enhanced = cv2.convertScaleAbs(resized, alpha=1.6, beta=0)
        if float(np.mean(enhanced)) < 90:
            enhanced = cv2.bitwise_not(enhanced)
            enhanced = cv2.convertScaleAbs(enhanced, alpha=1.3, beta=12)
        return enhanced
    except Exception:
        return None


def preprocess_for_white_on_bright(img):
    """Multiple strategies for white text on bright backgrounds (tiles, receipts, blue panels)."""
    import cv2

    variants = []
    try:
        if img is None or getattr(img, "size", 0) == 0:
            return variants
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        height, width = gray.shape[:2]
        scale = 3.0 if max(height, width) < 900 else 2.0
        
        resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        variants.append(resized)
        
        inverted = cv2.bitwise_not(resized)
        variants.append(inverted)
        
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        contrast = clahe.apply(resized)
        variants.append(contrast)
        
        _, binary = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(binary)
        
        _, adaptive = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(adaptive)
        
        return variants
    except Exception:
        return []


def _overlay_rois(bgr) -> list:
    """Crop Timemark overlay (yellow 'Điểm danh' + time + date), not the whole scene."""
    import cv2

    if bgr is None or getattr(bgr, "size", 0) == 0:
        return []
    height, width = bgr.shape[:2]
    rois = []
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, np.array([12, 55, 90]), np.array([45, 255, 255]))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 5))
    yellow = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for contour in contours:
        x, y, box_w, box_h = cv2.boundingRect(contour)
        area = box_w * box_h
        if y < height * 0.28 or area < 80:
            continue
        if area > best_area:
            best_area = area
            best = (x, y, box_w, box_h)
    if best:
        x, y, box_w, box_h = best
        x0 = max(0, x - int(0.04 * width))
        y0 = max(0, y - int(0.03 * height))
        x1 = min(width, x + max(int(box_w * 5), int(0.62 * width)))
        y1 = min(height, y + max(int(box_h * 9), int(0.32 * height)))
        crop = bgr[y0:y1, x0:x1]
        if crop.size:
            rois.append(crop)
    bottom_left = bgr[int(height * 0.58) :, : int(width * 0.88)]
    if bottom_left.size:
        rois.append(bottom_left)
    bottom_half = bgr[int(height * 0.5) :, :]
    if bottom_half.size:
        rois.append(bottom_half)
    bottom = bgr[int(height * 0.75) :, :]
    if bottom.size:
        rois.append(bottom)
    return rois


def extract_photo_code(bgr) -> tuple[Optional[str], str]:
    """Extract Timemark Photo Code from vertical right-edge strip.
    
    Returns: (photo_code, note)
    - photo_code: ~14 alphanumeric chars, or None if not found
    - note: extraction method or error description
    """
    import cv2
    import re
    
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return None, "No image"
    
    try:
        height, width = bgr.shape[:2]
        right_strip_width = int(width * 0.15)
        right_strip = bgr[:, width - right_strip_width:]
        
        if right_strip.size == 0:
            return None, "Empty right strip"
        
        gray = cv2.cvtColor(right_strip, cv2.COLOR_BGR2GRAY)
        rotated = cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
        
        scale = 3.0
        scaled = cv2.resize(rotated, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        
        variants = []
        variants.append(scaled)
        
        inverted = cv2.bitwise_not(scaled)
        variants.append(inverted)
        
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        contrast = clahe.apply(scaled)
        variants.append(contrast)
        
        _, binary = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(binary)
        
        code_pattern = re.compile(r'\b([A-Z0-9]{12,16})\b', re.IGNORECASE)
        
        reader = _ensure_process_reader()
        
        best_code = None
        best_note = "Not found"
        
        for idx, variant in enumerate(variants):
            try:
                texts = _readtext(reader, variant)
                combined = " ".join(texts).upper()
                
                combined = re.sub(r'[©®™]', '', combined)
                combined = re.sub(r'TIMEMARK\s*VERIFIED', '', combined, flags=re.IGNORECASE)
                combined = re.sub(r'100%\s*CHÂN\s*THỰC', '', combined, flags=re.IGNORECASE)
                combined = re.sub(r'\s+', '', combined)
                
                matches = code_pattern.findall(combined)
                if matches:
                    for match in matches:
                        cleaned = re.sub(r'[^A-Z0-9]', '', match.upper())
                        if 12 <= len(cleaned) <= 16:
                            best_code = cleaned
                            best_note = f"variant_{idx}"
                            return best_code, best_note
                
                alphanumeric = re.sub(r'[^A-Z0-9]', '', combined)
                if 12 <= len(alphanumeric) <= 20:
                    best_code = alphanumeric[:16]
                    best_note = f"variant_{idx}_partial"
                    
            except Exception:
                continue
        
        return best_code, best_note if best_code else "Not found"
        
    except Exception as exc:
        return None, f"Error: {exc}"


def _readtext(reader, source) -> list[str]:
    """Read text from image using either RapidOCR or EasyOCR."""
    import cv2

    if source is None or getattr(source, "size", 0) == 0:
        return []
    image = np.ascontiguousarray(source)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    
    if hasattr(reader, '__class__') and 'RapidOCR' in reader.__class__.__name__:
        result = reader(image, use_det=True, use_cls=True, use_rec=True)
        if result is None:
            return []
        txts = getattr(result, 'txts', None)
        if txts:
            return [str(x) for x in txts if x]
        texts = []
        try:
            for line in result:
                if isinstance(line, (list, tuple)) and len(line) >= 2:
                    text = str(line[1])
                    if text:
                        texts.append(text)
        except TypeError:
            return []
        return texts
    else:
        result = reader.readtext(image, detail=0, paragraph=True)
        return [str(t) for t in result if t]


def _resize_max(image, max_width: int = 1600):
    import cv2

    height, width = image.shape[:2]
    if width <= max_width:
        return image
    scale = max_width / float(width)
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def _timestamp_crops(image):
    """Prefer typical overlay zones: bottom strip, top strip, then full frame."""
    import cv2

    height, width = image.shape[:2]
    crops = [
        image[int(height * 0.78) :, :],
        image[: int(height * 0.18), :],
        image[int(height * 0.78) :, int(width * 0.45) :],
        image[: int(height * 0.18), int(width * 0.45) :],
    ]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    crops.append(cv2.cvtColor(enhanced[int(height * 0.78) :, :], cv2.COLOR_GRAY2BGR))
    return crops


def _parse_dt_groups(match: re.Match) -> Optional[datetime]:
    parts = match.groupdict()
    try:
        year = int(parts["y"])
        month = int(parts["m"])
        day = int(parts["d"])
        hour = int(parts["H"])
        minute = int(parts["M"])
        second = int(parts.get("S") or 0)
        if year < 100:
            year += 2000
        return datetime(year, month, day, hour, minute, second)
    except (ValueError, KeyError, TypeError):
        return None


def extract_datetimes(text: str) -> list[datetime]:
    found: list[datetime] = []
    compact = re.sub(r"\s+", " ", text)
    for pattern in DATETIME_PATTERNS:
        for match in pattern.finditer(compact):
            parsed = _parse_dt_groups(match)
            if parsed:
                found.append(parsed)
    return found


def extract_times(text: str) -> list[time]:
    found: list[time] = []
    for match in TIME_ONLY.finditer(text or ""):
        parsed = _reconstruct_time(match.group("H"), match.group("M"))
        if parsed and not _is_date_like_time_match(text or "", match):
            found.append(parsed)
    return found


def _reconstruct_time(hour_raw, minute_raw) -> Optional[time]:
    """Build HH:MM from OCR groups; hours 0-23, minutes 0-59."""
    try:
        hour = int(str(hour_raw).strip())
        minute = int(str(minute_raw).strip())
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    clean = f"{hour:02d}:{minute:02d}"
    return parse_time_value(clean)


def _is_date_like_time_match(blob: str, match: re.Match) -> bool:
    """Ignore fragments of dates like 07.08.2026."""
    tail = blob[match.end() : match.end() + 8]
    if re.match(r"\s*[./-]\s*\d{2}", tail):
        return True
    return False


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_timemark_time(text: str) -> Optional[time]:
    """Time from Timemark overlay; skip invalid clocks like 10:90 and keep searching."""
    blob = text or ""
    marked = DIEM_DANH_TIME_RE.search(blob)
    if marked:
        parsed = _reconstruct_time(marked.group(1), marked.group(2))
        if parsed:
            return parsed
    date_match = TIMEMARK_DATE_RE.search(blob)
    if date_match:
        window = blob[max(0, date_match.start() - 120) : date_match.start()]
        near: Optional[time] = None
        for match in TIMEMARK_TIME_RE.finditer(window):
            candidate = _reconstruct_time(match.group(1), match.group(2))
            if candidate:
                near = candidate
        if near:
            return near
    for match in TIMEMARK_TIME_RE.finditer(blob):
        if _is_date_like_time_match(blob, match):
            continue
        candidate = _reconstruct_time(match.group(1), match.group(2))
        if candidate:
            return candidate
    return None


def extract_timemark_date(text: str) -> Optional[date]:
    """Vietnamese Timemark date, e.g. '07 Tháng 8,2026' -> 2026-08-07."""
    blob = re.sub(r"\s+", " ", text or "")
    match = TIMEMARK_DATE_RE.search(blob)
    if not match:
        return None
    try:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
    except (TypeError, ValueError):
        return None
    parsed = _safe_date(year, month, day)
    return parsed


def _try_dateutil(text: str) -> tuple[Optional[date], Optional[time]]:
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return None, None
    try:
        from dateutil import parser as dateutil_parser
    except ImportError:
        return None, None
    kwargs = {"fuzzy": True, "dayfirst": True}
    if re.search(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", blob):
        kwargs["dayfirst"] = False
        kwargs["yearfirst"] = True
    try:
        parsed = dateutil_parser.parse(blob, **kwargs)
    except (ValueError, OverflowError, TypeError, OSError):
        return None, None
    except Exception:
        return None, None
    if parsed is None:
        return None, None
    parsed_date = parsed.date() if hasattr(parsed, "date") else None
    parsed_time = parsed.time() if hasattr(parsed, "time") else None
    if parsed_time and parsed_time.hour == 0 and parsed_time.minute == 0 and parsed_time.second == 0:
        if not TIMEMARK_TIME_RE.search(blob):
            parsed_time = None
    return parsed_date, parsed_time


def parse_ocr_date_and_time(text: str) -> tuple[Optional[date], Optional[time]]:
    """Prefer Timemark overlay regex; never let fuzzy dateutil override 18:49 with 08:20."""
    raw = text or ""
    blob = re.sub(r"\s+", " ", raw)
    parsed_date = extract_timemark_date(blob) or extract_timemark_date(raw)
    parsed_time = extract_timemark_time(raw) or extract_timemark_time(blob)

    if parsed_date is None or parsed_time is None:
        for dt in extract_datetimes(blob) or extract_datetimes(raw):
            if parsed_date is None:
                parsed_date = dt.date()
            if parsed_time is None:
                parsed_time = dt.time()
            if parsed_date and parsed_time:
                break

    if parsed_date is None or parsed_time is None:
        try:
            du_date, du_time = _try_dateutil(blob)
        except Exception:
            du_date, du_time = None, None
        if parsed_date is None:
            parsed_date = du_date
        if parsed_time is None:
            parsed_time = du_time

    if parsed_time is None:
        times = extract_times(raw) or extract_times(blob)
        if times:
            parsed_time = times[0]
    return parsed_date, parsed_time


def _warn_missing_ocr_parts(
    blob: str,
    parsed_date: Optional[date],
    parsed_time: Optional[time],
    log: LogFn = None,
    source: str = "",
) -> None:
    """Log the full EasyOCR text when date or time cannot be parsed."""
    if parsed_date is not None and parsed_time is not None:
        return
    if parsed_time is None and parsed_date is not None:
        kind = "time"
    elif parsed_date is None and parsed_time is not None:
        kind = "date"
    else:
        kind = "date or time"
    prefix = f"{source} " if source else ""
    msg = f"WARNING: Cannot find {kind}. {prefix}Raw OCR text: {blob}"
    print(msg)
    _log(log, msg)


def combine_date_time(parsed_date: Optional[date], parsed_time: Optional[time]) -> Optional[datetime]:
    if parsed_date is None or parsed_time is None:
        return None
    try:
        return datetime.combine(parsed_date, parsed_time)
    except (TypeError, ValueError, OverflowError):
        return None


def _exif_datetime(path: Path) -> Optional[datetime]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            for tag in (36867, 36868, 306):  # DateTimeOriginal, Digitized, DateTime
                value = exif.get(tag)
                if not value:
                    continue
                text = str(value).replace(":", "-", 2)
                try:
                    return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    try:
                        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    return None


def _timemark_complete(blob: str) -> bool:
    return extract_timemark_date(blob) is not None and extract_timemark_time(blob) is not None


def _parse_blob(blob: str) -> tuple[Optional[date], Optional[time], str]:
    try:
        parsed_date, parsed_time = parse_ocr_date_and_time(blob)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Lỗi parse timestamp: {exc}"
    return parsed_date, parsed_time, ""


def _collect_texts(reader, sources: list) -> list[str]:
    texts: list[str] = []
    for source in sources:
        if source is None:
            continue
        try:
            texts.extend(_readtext(reader, source))
        except Exception:
            continue
    return texts


def _ocr_image(reader, path: Path, log: LogFn = None) -> tuple[Optional[datetime], str, str, Optional[str]]:
    """Three-pass OCR: overlay crop, white-on-bright preprocessing, then extract photo code.
    
    Returns: (datetime, raw_text, note, photo_code)
    """
    try:
        original = _load_cv_image(path)
    except Exception as exc:  # noqa: BLE001 — skip unreadable / corrupted files
        return None, "", f"Không mở được ảnh: {exc}", None
    if original is None:
        return None, "", "OpenCV không đọc được ảnh", None

    photo_code, code_note = extract_photo_code(original)

    rois = _overlay_rois(original) or [original]
    texts = _collect_texts(reader, rois)
    blob = " ".join(texts)
    parsed_date, parsed_time, note_parse = _parse_blob(blob)
    pass2_ok = False
    pass3_ok = False
    overlay_ok = _timemark_complete(blob) or bool(extract_datetimes(blob))
    pass1_complete = parsed_date is not None and parsed_time is not None and overlay_ok

    if not pass1_complete:
        try:
            enhanced_rois = [preprocess_roi(roi) for roi in rois]
            texts2 = _collect_texts(reader, [item for item in enhanced_rois if item is not None])
            blob2 = " ".join(texts2)
            if blob2:
                blob = f"{blob} {blob2}".strip()
            d2, t2, note2 = _parse_blob(blob2)
            if note2 and not note_parse:
                note_parse = note2
            tm_d = extract_timemark_date(blob2)
            tm_t = extract_timemark_time(blob2)
            if tm_d is not None:
                parsed_date = tm_d
            elif d2 is not None:
                parsed_date = d2
            if tm_t is not None:
                parsed_time = tm_t
            elif t2 is not None:
                parsed_time = t2
            if parsed_date is None or parsed_time is None:
                d3, t3, _ = _parse_blob(blob)
                parsed_date = parsed_date or d3
                parsed_time = parsed_time or t3
            filled_by_pass2 = tm_d is not None or tm_t is not None or d2 is not None or t2 is not None
            if parsed_date is not None and parsed_time is not None and filled_by_pass2:
                pass2_ok = True
                msg = "[OCR] Đọc thành công sau khi xử lý ảnh bằng OpenCV."
                print(msg)
                _log(log, msg)
        except Exception as exc:  # noqa: BLE001
            _log(log, f"OCR Pass 2 (OpenCV) lỗi, bỏ qua: {exc}")

    if parsed_date is None or parsed_time is None:
        try:
            all_variants = []
            for roi in rois:
                all_variants.extend(preprocess_for_white_on_bright(roi))
            
            texts3 = _collect_texts(reader, all_variants)
            blob3 = " ".join(texts3)
            if blob3:
                blob = f"{blob} {blob3}".strip()
            
            d3, t3, note3 = _parse_blob(blob3)
            if note3 and not note_parse:
                note_parse = note3
            tm_d3 = extract_timemark_date(blob3)
            tm_t3 = extract_timemark_time(blob3)
            
            if tm_d3 is not None:
                parsed_date = tm_d3
                pass3_ok = True
            elif d3 is not None:
                parsed_date = d3
                pass3_ok = True
            
            if tm_t3 is not None:
                parsed_time = tm_t3
                pass3_ok = True
            elif t3 is not None:
                parsed_time = t3
                pass3_ok = True
            
            if pass3_ok and parsed_date is not None and parsed_time is not None:
                msg = "[OCR] Đọc thành công với white-on-bright preprocessing."
                print(msg)
                _log(log, msg)
                note_parse = "white-on-bright preprocess"
        except Exception as exc:  # noqa: BLE001
            _log(log, f"OCR Pass 3 (white-on-bright) lỗi, bỏ qua: {exc}")

    if parsed_date is None or parsed_time is None:
        _warn_missing_ocr_parts(blob, parsed_date, parsed_time, log=log, source=path.name)
    
    dt = combine_date_time(parsed_date, parsed_time)
    
    final_note = note_parse or ""
    if pass2_ok:
        final_note = "OpenCV preprocess" + (f" + {final_note}" if final_note else "")
    elif pass3_ok:
        final_note = "white-on-bright preprocess" + (f" + {final_note}" if final_note else "")
    
    if photo_code:
        final_note = f"Photo Code: {photo_code}" + (f" ({code_note})" if code_note else "") + (f" | {final_note}" if final_note else "")
    
    if dt:
        return dt, blob, final_note, photo_code
    
    exif_dt = _exif_datetime(path)
    if parsed_time and exif_dt:
        combined = datetime(
            exif_dt.year, exif_dt.month, exif_dt.day,
            parsed_time.hour, parsed_time.minute, parsed_time.second,
        )
        return combined, blob, f"Date from EXIF, time from OCR | {final_note}", photo_code
    if parsed_date and not parsed_time:
        msg = f"OCR có ngày {parsed_date.isoformat()}, không thấy giờ"
        if photo_code:
            msg += f" — Cần verify Timemark với Photo Code: {photo_code}"
        return None, blob, msg, photo_code
    if parsed_time and not parsed_date:
        if exif_dt:
            return datetime(
                exif_dt.year, exif_dt.month, exif_dt.day,
                parsed_time.hour, parsed_time.minute, parsed_time.second,
            ), blob, f"Date from EXIF | {final_note}", photo_code
        msg = f"OCR có giờ {parsed_time.strftime('%H:%M')}, không thấy ngày"
        if photo_code:
            msg += f" — Cần verify Timemark với Photo Code: {photo_code}"
        return None, blob, msg, photo_code
    if exif_dt:
        return exif_dt, blob, f"Used EXIF fallback | {final_note}", photo_code
    
    fail_msg = note_parse or "Không đọc được timestamp"
    if photo_code:
        fail_msg += f" — Cần verify Timemark với Photo Code: {photo_code}"
    return None, blob, fail_msg, photo_code


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def _iter_employee_images(images_root: Path) -> list[tuple[str, Path]]:
    """Map each photo to its parent subfolder name — that is the employee name.

    Vietnamese names on Timemark overlays are not OCR'd (too error-prone).
    Also accepts a single employee folder (images placed directly inside).
    """
    items: list[tuple[str, Path]] = []
    for child in sorted(images_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        employee = child.name.strip()
        for file in sorted(child.rglob("*")):
            if _is_image_file(file):
                items.append((employee, file))
    if items:
        return items
    # User selected Images/<Tên NV>/ instead of Images/
    direct = [file for file in sorted(images_root.iterdir()) if _is_image_file(file)]
    if direct:
        employee = images_root.name.strip() or "NV"
        return [(employee, file) for file in direct]
    return []


def _apply_ocr_result(
    employee: str,
    path: Path,
    dt: Optional[datetime],
    raw_text: str,
    note: str,
    punches: list[dict],
    ocr_rows: list[dict],
    log: LogFn,
    from_cache: bool = False,
    photo_code: Optional[str] = None,
) -> None:
    status = "ok" if dt else "skipped"
    if dt is None:
        if not from_cache:
            log_msg = f"[BỎ QUA] {employee}/{path.name}: {note or 'OCR thất bại'}"
            if photo_code:
                log_msg += f" [Photo Code: {photo_code}]"
            _log(log, log_msg)
    else:
        punches.append(
            {
                "employee_name": employee,
                "date": dt.date(),
                "photo_time": dt.time(),
                "photo_datetime": dt,
                "photo_code": photo_code,
            }
        )
        if note and not from_cache:
            _log(log, f"[OK-fallback] {employee}/{path.name}: {dt} ({note})")
    ocr_rows.append(
        {
            "employee_name": employee,
            "file": str(path),
            "status": status,
            "timestamp": dt,
            "ocr_text": raw_text,
            "note": note,
            "photo_code": photo_code or "",
        }
    )


def _ocr_uncached_parallel(
    jobs: list[tuple[str, str, str]],
    cache: dict[str, Any],
    cache_path: Path,
    punches: list[dict],
    ocr_rows: list[dict],
    done: int,
    total: int,
    log: LogFn,
    progress: ProgressFn,
) -> int:
    workers = min(_ocr_worker_count(), len(jobs))
    _log(log, f"OCR song song {len(jobs)} ảnh, {workers} process (giữ 1 nhân cho giao diện).")
    processed = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=_ensure_torchvision_ops) as pool:
        futures = {pool.submit(ocr_image_job, job): job for job in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            employee, path_str, key = job
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001
                result = {
                    "key": key,
                    "employee": employee,
                    "path": path_str,
                    "datetime": None,
                    "note": f"OCR lỗi process: {exc}",
                    "raw_text": "",
                    "photo_code": None,
                    "ok": False,
                }
            dt = result.get("datetime")
            if not isinstance(dt, datetime):
                dt_tuple = _datetime_from_cache(
                    {"date": result.get("date"), "time": result.get("time")}
                    if result.get("date") and result.get("time")
                    else None
                )
                dt = dt_tuple[0] if dt_tuple else None
            photo_code = result.get("photo_code")
            payload = _cache_payload(dt, photo_code)
            if payload is not None:
                cache[key] = payload
            else:
                cache.pop(key, None)
            _apply_ocr_result(
                result.get("employee") or employee,
                Path(result.get("path") or path_str),
                dt,
                str(result.get("raw_text") or ""),
                str(result.get("note") or ""),
                punches,
                ocr_rows,
                log,
                photo_code=photo_code,
            )
            processed += 1
            done += 1
            if progress:
                progress(done, total, f"{employee} / {Path(path_str).name}")
            if processed % CACHE_SAVE_EVERY == 0:
                _save_ocr_cache(cache_path, cache)
                _log(log, f"Đã ghi cache ({len(cache)} ảnh).")
    return done


def _ocr_uncached_sequential(
    jobs: list[tuple[str, str, str]],
    cache: dict[str, Any],
    cache_path: Path,
    punches: list[dict],
    ocr_rows: list[dict],
    done: int,
    total: int,
    log: LogFn,
    progress: ProgressFn,
) -> int:
    reader = _init_reader(log=log)
    processed = 0
    for employee, path_str, key in jobs:
        path = Path(path_str)
        dt, raw_text, note, photo_code = _ocr_image(reader, path, log=log)
        payload = _cache_payload(dt, photo_code)
        if payload is not None:
            cache[key] = payload
        else:
            cache.pop(key, None)
        _apply_ocr_result(employee, path, dt, raw_text, note, punches, ocr_rows, log, photo_code=photo_code)
        processed += 1
        done += 1
        if progress:
            progress(done, total, f"{employee} / {path.name}")
        if processed % CACHE_SAVE_EVERY == 0:
            _save_ocr_cache(cache_path, cache)
    return done


def load_photo_attendance(
    images_folder: str | Path,
    log: LogFn = None,
    progress: ProgressFn = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scan /Images/<employee>/*.jpg, OCR timestamps, aggregate min/max per day.

    Cached filenames skip EasyOCR. Uncached files run in a process pool.
    Call this from a background thread so CustomTkinter stays responsive.
    """
    root = Path(images_folder)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục ảnh: {root}")
    _log(log, "Ảnh Timemark: tên nhân viên = tên thư mục con (không OCR tên trên ảnh).")
    files = _iter_employee_images(root)
    if not files:
        _log(
            log,
            f"Không thấy file ảnh trong {root}. "
            "Chọn thư mục Images (mỗi nhân viên một thư mục con), "
            "hoặc chọn thẳng thư mục của một nhân viên có file .jpg/.png.",
        )
        empty = pd.DataFrame(columns=["employee_name", "date", "photo_in", "photo_out"])
        return empty, pd.DataFrame()

    cache_path = _cache_path()
    cache = _load_ocr_cache(cache_path)
    punches: list[dict] = []
    ocr_rows: list[dict] = []
    total = len(files)
    cached_jobs: list[tuple[str, Path, str, Any]] = []
    fresh_jobs: list[tuple[str, str, str]] = []
    for employee, path in files:
        key = _cache_key(root, path)
        entry = cache.get(key)
        if entry is not None:
            cached_jobs.append((employee, path, key, entry))
        else:
            fresh_jobs.append((employee, str(path), key))

    _log(
        log,
        f"OCR {total} ảnh từ {root} — cache {len(cached_jobs)}, cần đọc {len(fresh_jobs)} "
        f"(file {cache_path.name}).",
    )

    done = 0
    for employee, path, key, entry in cached_jobs:
        dt, photo_code = _datetime_from_cache(entry)
        note = "cache" if dt else "cache (không có giờ)"
        _apply_ocr_result(employee, path, dt, "", note, punches, ocr_rows, log, from_cache=True, photo_code=photo_code)
        done += 1
    if cached_jobs and progress:
        progress(done, total, f"cache {done}/{total}")

    if fresh_jobs:
        try:
            if _use_process_pool(len(fresh_jobs)):
                done = _ocr_uncached_parallel(
                    fresh_jobs, cache, cache_path, punches, ocr_rows, done, total, log, progress
                )
            else:
                if getattr(sys, "frozen", False):
                    _log(log, "OCR tuần tự trong bản .exe (tránh lỗi process + torchvision::nms).")
                elif threading.current_thread() is not threading.main_thread():
                    _log(log, "OCR tuần tự trên luồng nền (Windows không chạy ProcessPool từ GUI).")
                done = _ocr_uncached_sequential(
                    fresh_jobs, cache, cache_path, punches, ocr_rows, done, total, log, progress
                )
        except Exception as exc:  # noqa: BLE001
            _log(log, f"OCR đa tiến trình lỗi ({exc}). Chuyển sang đọc tuần tự trên luồng nền.")
            done_paths = {row["file"] for row in ocr_rows}
            leftover = [job for job in fresh_jobs if job[1] not in done_paths]
            if leftover:
                done = _ocr_uncached_sequential(
                    leftover, cache, cache_path, punches, ocr_rows, done, total, log, progress
                )

    try:
        _save_ocr_cache(cache_path, cache)
        _log(log, f"Đã lưu cache OCR: {cache_path} ({len(cache)} ảnh).")
    except OSError as exc:
        _log(log, f"Không ghi được cache OCR: {exc}")

    if not punches:
        _log(log, "OCR không trích được mốc thời gian nào.")
        return pd.DataFrame(columns=["employee_name", "date", "photo_in", "photo_out"]), pd.DataFrame(ocr_rows)

    punch_df = pd.DataFrame(punches)
    grouped = (
        punch_df.groupby(["employee_name", "date"], as_index=False)
        .agg(
            photo_in=("photo_time", "min"),
            photo_out=("photo_time", "max"),
            photo_datetimes=("photo_datetime", lambda s: tuple(sorted(x for x in s if x is not None))),
        )
    )
    same = grouped["photo_in"] == grouped["photo_out"]
    noon = time(12, 0)
    only_morning = same & grouped["photo_in"].map(lambda t: t < noon if t else False)
    only_afternoon = same & grouped["photo_in"].map(lambda t: t >= noon if t else False)
    grouped.loc[only_morning, "photo_out"] = None
    grouped.loc[only_afternoon, "photo_in"] = None
    _log(log, f"Ảnh: {grouped['employee_name'].nunique()} nhân viên, {len(grouped)} ngày.")
    return grouped, pd.DataFrame(ocr_rows)
