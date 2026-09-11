"""Register torchvision::nms before EasyOCR loads in a frozen exe."""

from __future__ import annotations

import os
import sys

# Two OpenMP runtimes (NumPy + Torch) abort the process with no window.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    meipass = str(sys._MEIPASS)
    os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")

try:
    import torchvision  # noqa: F401
    import torchvision.ops  # noqa: F401
    from torchvision.ops import nms  # noqa: F401
except Exception:
    pass
