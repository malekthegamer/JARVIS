"""IconPad — a deterministic 'only vision can find it' test surface.

A Tk window titled 'IconPad' whose two controls are drawn as PIXELS on a
single Canvas: a trash-can icon (left) and a bold 'B' (right). Because they're
canvas drawings, they expose NO per-icon UIA elements — the fast text path
cannot name or locate them, so slice-5 vision must. Every click is appended
(region + canvas coords) to a record file so a driver can assert what got hit.

    python tests/harness_iconpad.py <record_file>

Left half  (x < mid) → 'trash'   Right half (x >= mid) → 'bold'.
"""
from __future__ import annotations

import sys
import tkinter as tk

W, H = 440, 240
MID = W // 2


def main() -> None:
    record = sys.argv[1] if len(sys.argv) > 1 else "iconpad_record.txt"
    open(record, "w").close()  # truncate

    root = tk.Tk()
    root.title("IconPad")
    root.geometry(f"{W}x{H}+200+200")
    root.attributes("-topmost", True)

    cv = tk.Canvas(root, width=W, height=H, bg="#eef0f3", highlightthickness=0)
    cv.pack(fill="both", expand=True)

    # --- left: a trash-can icon, framed like a toolbar button ---
    cv.create_rectangle(40, 50, 200, 200, fill="#ffffff", outline="#7a7e86", width=2)
    cv.create_rectangle(88, 78, 152, 90, fill="#3c3f46", outline="")          # lid
    cv.create_rectangle(108, 68, 132, 78, fill="#3c3f46", outline="")         # handle
    cv.create_polygon(96, 92, 144, 92, 138, 176, 102, 176, fill="#50545c")    # body
    for x in (112, 120, 128):
        cv.create_line(x, 104, x, 166, fill="#eef0f3", width=3)               # stripes

    # --- right: a bold 'B', framed like a toolbar button ---
    cv.create_rectangle(240, 50, 400, 200, fill="#ffffff", outline="#7a7e86", width=2)
    cv.create_text(320, 125, text="B", font=("Arial", 90, "bold"), fill="#16181c")

    def on_click(event):
        region = "trash" if event.x < MID else "bold"
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(f"{region},{event.x},{event.y}\n")
        print(f"[iconpad] click -> {region} ({event.x},{event.y})", flush=True)

    cv.bind("<Button-1>", on_click)
    root.mainloop()


if __name__ == "__main__":
    main()
