"""Shared ttk.Treeview look: zebra rows, hover, selection, centered text."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

TREE_STYLE = "Attendance.Treeview"
TREE_ODD_BG = "#FFFFFF"
TREE_EVEN_BG = "#F4F6F7"
TREE_HOVER_BG = "#EAF2F8"
TREE_SELECT_BG = "#D4E6F1"
TREE_SELECT_FG = "#1B4F72"
TREE_HEAD_BG = "#2C3E50"
TREE_HEAD_FG = "#FFFFFF"
SEARCH_PLACEHOLDER = "🔍 Tìm kiếm theo Tên hoặc Mã NV..."


def employee_search_match(query: str, *fields: object) -> bool:
    """Case-insensitive substring match on employee id / name (in-memory)."""
    needle = str(query or "").strip().lower()
    if not needle:
        return True
    return any(needle in str(field or "").lower() for field in fields)


def replace_tree_rows(tree: ttk.Treeview, rows: list[tuple[str, tuple, tuple | list]]) -> None:
    """Clear once, then insert all rows without per-row UI updates."""
    existing = tree.get_children()
    if existing:
        tree.delete(*existing)
    insert = tree.insert
    for iid, values, tags in rows:
        insert("", "end", iid=iid, values=values, tags=tags)


def apply_tree_style(tree: ttk.Treeview) -> None:
    style = ttk.Style(tree.winfo_toplevel())
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        TREE_STYLE,
        background=TREE_ODD_BG,
        fieldbackground=TREE_ODD_BG,
        foreground="#1A202C",
        rowheight=28,
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.configure(
        f"{TREE_STYLE}.Heading",
        background=TREE_HEAD_BG,
        foreground=TREE_HEAD_FG,
        font=("Segoe UI", 10, "bold"),
        relief="flat",
        padding=6,
    )
    style.map(
        TREE_STYLE,
        background=[("selected", TREE_SELECT_BG)],
        foreground=[("selected", TREE_SELECT_FG)],
    )
    style.map(f"{TREE_STYLE}.Heading", background=[("active", "#34495E")])
    tree.configure(style=TREE_STYLE)
    tree.tag_configure("odd", background=TREE_ODD_BG)
    tree.tag_configure("even", background=TREE_EVEN_BG)
    tree.tag_configure("hover", background=TREE_HOVER_BG)


def stripe_tags(index: int, extra: str = "") -> tuple:
    stripe = "even" if index % 2 == 0 else "odd"
    return (stripe, extra) if extra else (stripe,)


def bind_hover(tree: ttk.Treeview) -> None:
    state = {"iid": ""}

    def clear() -> None:
        iid = state["iid"]
        state["iid"] = ""
        if iid and iid in tree.get_children():
            tags = [t for t in tree.item(iid, "tags") if t != "hover"]
            tree.item(iid, tags=tags)

    def on_motion(event) -> None:
        iid = tree.identify_row(event.y)
        if iid == state["iid"]:
            return
        clear()
        if not iid or iid in tree.selection():
            return
        state["iid"] = iid
        tags = [t for t in tree.item(iid, "tags") if t != "hover"]
        tags.append("hover")
        tree.item(iid, tags=tags)

    tree.bind("<Motion>", on_motion)
    tree.bind("<Leave>", lambda _e: clear())
