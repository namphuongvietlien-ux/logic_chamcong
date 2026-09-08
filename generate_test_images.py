"""Generate mock Timemark photos for OCR stress-testing.

Creates Images_Test/NV_Test_01 .. NV_Test_70 with 30 days x 2 punches
(August 2026) = 4200 JPEGs.

Run:
    .venv\\Scripts\\python.exe generate_test_images.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover

    def tqdm(iterable, **kwargs):
        total = kwargs.get("total") or getattr(iterable, "__len__", lambda: None)()
        desc = kwargs.get("desc") or ""
        for i, item in enumerate(iterable, 1):
            if total:
                if i == 1 or i == total or i % 50 == 0:
                    print(f"{desc} {i}/{total}", flush=True)
            yield item


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "Images_Test"

WIDTH, HEIGHT = 600, 800
N_EMPLOYEES = 70
YEAR, MONTH = 2026, 8
DAYS_IN_MONTH = 30

WEEKDAYS_VN = {
    0: "Hai",
    1: "Ba",
    2: "Tư",
    3: "Năm",
    4: "Sáu",
    5: "Bảy",
    6: "Chủ Nhật",
}

YELLOW = (255, 214, 0)
YELLOW_TEXT = (20, 20, 20)


def _font_candidates() -> list[Path]:
    windir = Path(r"C:\Windows\Fonts")
    names = (
        "arial.ttf",
        "arialuni.ttf",
        "tahoma.ttf",
        "segoeui.ttf",
        "calibri.ttf",
        "times.ttf",
        "verdana.ttf",
    )
    found = [windir / n for n in names if (windir / n).exists()]
    return found


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer a Windows TTF so Vietnamese diacritics actually render.

    Pillow's built-in bitmap font cannot draw 'Điểm' / 'Tháng'.
    """
    for path in _font_candidates():
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


FONT_TIME = load_font(36)
FONT_DATE = load_font(26)


def weekday_label(day: date) -> str:
    name = WEEKDAYS_VN[day.weekday()]
    if day.weekday() == 6:
        return name
    return f"Thứ {name}"


def random_time(hour: int, minute_jitter: int) -> tuple[int, int]:
    minute = hour * 60 + random.randint(-minute_jitter, minute_jitter)
    minute = max(0, min(23 * 60 + 59, minute))
    return divmod(minute, 60)


def noisy_background() -> Image.Image:
    """Solid color or grain so every frame is not identical."""
    if random.random() < 0.45:
        color = (
            random.randint(30, 180),
            random.randint(30, 180),
            random.randint(30, 180),
        )
        img = Image.new("RGB", (WIDTH, HEIGHT), color)
    else:
        pixels = bytearray()
        base = [random.randint(20, 160) for _ in range(3)]
        for _ in range(WIDTH * HEIGHT):
            pixels.extend(
                max(0, min(255, c + random.randint(-40, 40))) for c in base
            )
        img = Image.frombytes("RGB", (WIDTH, HEIGHT), bytes(pixels))
        img = img.filter(ImageFilter.BoxBlur(1))
    draw = ImageDraw.Draw(img)
    for _ in range(random.randint(4, 12)):
        x0, y0 = random.randint(0, WIDTH), random.randint(0, int(HEIGHT * 0.7))
        x1, y1 = x0 + random.randint(20, 160), y0 + random.randint(20, 120)
        fill = tuple(random.randint(0, 255) for _ in range(3))
        draw.rectangle([x0, y0, x1, y1], outline=fill, width=2)
    return img


def draw_timemark(img: Image.Image, punch_at: datetime) -> None:
    draw = ImageDraw.Draw(img)
    bar_h = 150
    top = HEIGHT - bar_h - 16
    margin = 18
    yellow = (
        255,
        random.randint(200, 230),
        random.randint(0, 30),
    )
    draw.rounded_rectangle(
        [margin, top, WIDTH - margin, HEIGHT - 16],
        radius=10,
        fill=yellow,
        outline=(210, 170, 0),
        width=2,
    )
    line1 = f"Điểm danh {punch_at.strftime('%H:%M')}"
    day = punch_at.date()
    line2 = f"{weekday_label(day)}, {day.day:02d} Tháng 8,2026"
    draw.text((margin + 18, top + 22), line1, font=FONT_TIME, fill=YELLOW_TEXT)
    draw.text((margin + 18, top + 78), line2, font=FONT_DATE, fill=YELLOW_TEXT)


def make_image(punch_at: datetime) -> Image.Image:
    img = noisy_background()
    draw_timemark(img, punch_at)
    return img


def punches_for_day(day: date) -> list[tuple[str, datetime]]:
    in_h, in_m = random_time(8, 12)  # 07:48 .. 08:12
    out_h, out_m = random_time(17, 20)  # 16:40 .. 17:20
    check_in = datetime(day.year, day.month, day.day, in_h, in_m)
    check_out = datetime(day.year, day.month, day.day, out_h, out_m)
    if check_out <= check_in:
        check_out = check_in + timedelta(hours=8, minutes=random.randint(0, 40))
    return [("in", check_in), ("out", check_out)]


def employee_folder(index: int) -> Path:
    return OUT_DIR / f"NV_Test_{index:02d}"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    days = [date(YEAR, MONTH, d) for d in range(1, DAYS_IN_MONTH + 1)]
    total = N_EMPLOYEES * DAYS_IN_MONTH * 2
    jobs: list[tuple[Path, datetime]] = []
    for emp in range(1, N_EMPLOYEES + 1):
        folder = employee_folder(emp)
        folder.mkdir(parents=True, exist_ok=True)
        for day in days:
            for kind, punch_at in punches_for_day(day):
                name = f"{day.isoformat()}_{kind}_{punch_at.strftime('%H%M')}.jpg"
                jobs.append((folder / name, punch_at))

    print(f"Writing {total} images under {OUT_DIR}")
    for path, punch_at in tqdm(jobs, total=total, desc="Timemark images", unit="img"):
        img = make_image(punch_at)
        img.save(path, format="JPEG", quality=85, optimize=False)

    print(f"Done. {total} files in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
