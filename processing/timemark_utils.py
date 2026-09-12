"""Helper utilities for Timemark Photo Code verification."""

from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

TIMEMARK_VERIFY_URL = "https://verify.timemark.com"


def open_timemark_verify() -> bool:
    """Open the official Timemark verification portal in the default browser.
    
    Returns:
        True if browser opened successfully, False otherwise
    """
    try:
        webbrowser.open(TIMEMARK_VERIFY_URL)
        return True
    except Exception:
        return False


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard (Windows).
    
    Args:
        text: Text to copy
        
    Returns:
        True if successful, False otherwise
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(str(text))
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


def format_photo_code_note(photo_code: Optional[str], original_note: str = "") -> str:
    """Format a note string that includes the Photo Code for HR review.
    
    Args:
        photo_code: Extracted Timemark Photo Code (or None)
        original_note: Existing OCR note
        
    Returns:
        Formatted note string
    """
    if not photo_code:
        return original_note
    
    prefix = f"Photo Code: {photo_code}"
    if original_note:
        return f"{prefix} | {original_note}"
    return prefix


def extract_photo_code_from_note(note: str) -> Optional[str]:
    """Extract Photo Code from a note string.
    
    Args:
        note: Note string potentially containing "Photo Code: XXXXXX"
        
    Returns:
        Extracted Photo Code or None
    """
    import re
    
    pattern = r'Photo Code:\s*([A-Z0-9]{12,16})'
    match = re.search(pattern, note, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def show_photo_code_help() -> str:
    """Return help text for Photo Code verification workflow.
    
    Returns:
        Multi-line help text
    """
    return """
Timemark Photo Code Verification
==================================

When OCR cannot read the date/time from a Timemark photo, the Photo Code
can be used to verify the timestamp via Timemark's official tools:

1. Copy the Photo Code from the OCR log or exception report
2. Open https://verify.timemark.com in your browser
3. Paste the Photo Code OR upload the original photo
4. Timemark will return the verified timestamp
5. Use the Corrections tab to manually add the verified punch

For more details, see TIMEMARK_VERIFY.md
"""
