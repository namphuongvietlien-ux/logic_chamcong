"""Register torchvision::nms before EasyOCR loads in a frozen exe."""

from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    meipass = str(sys._MEIPASS)
    os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")

try:
    import torchvision  # noqa: F401
    import torchvision.ops  # noqa: F401
    from torchvision.ops import nms  # noqa: F401
except Exception:
    pass
