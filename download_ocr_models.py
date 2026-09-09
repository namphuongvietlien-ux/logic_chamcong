"""Download EasyOCR detector + Latin/Vietnamese recognizer into models/."""

from __future__ import annotations

import ssl
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"

JOBS = (
    (
        "https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/craft_mlt_25k.zip",
        "craft_mlt_25k.pth",
    ),
    (
        "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/latin_g2.zip",
        "latin_g2.pth",
    ),
)


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    ssl._create_default_https_context = ssl._create_unverified_context
    for url, filename in JOBS:
        dest = MODELS / filename
        if dest.is_file() and dest.stat().st_size > 1_000_000:
            print(f"OK {filename} ({dest.stat().st_size} bytes)")
            continue
        zip_path = MODELS / "temp.zip"
        print(f"Downloading {filename} ...")
        urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract(filename, MODELS)
        zip_path.unlink(missing_ok=True)
        print(f"OK {filename} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
