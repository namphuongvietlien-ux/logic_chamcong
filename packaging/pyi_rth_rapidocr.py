"""Runtime hook for RapidOCR in frozen exe (PyInstaller).

Unlike torch/EasyOCR, RapidOCR uses ONNX Runtime which is much more PyInstaller-friendly.
Set environment variables to prevent multi-threading issues.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    meipass = str(sys._MEIPASS)
    os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")

try:
    import onnxruntime
except Exception:
    pass
