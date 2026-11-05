"""
Fast keyboard-driven viewer for marking pages with a label of your choice
(VOID, TEST, whatever).

Works directly on a folder of .tif/.tiff files (alphabetical order), or on a
PDF (needs: pip install pymupdf).

Install:
    pip install pillow
    (optional, only for PDFs)  pip install pymupdf

Set SOURCE below, then run with a required label argument:
    python void_marker.py void
    python void_marker.py test

The label is shown on screen in capitals (VOID, TEST, ...) and decides the
output file names, so each label keeps its own separate marks.

KEYS
    1                toggle the label on the current page
    Right / Space / D    next page          Left / Backspace / A   previous page
    Down / PageDown      forward 10 pages   Up / PageUp            back 10 pages
    Home / End           first / last page
    ]  /  [              jump to next / previous marked page
    G                    go to a page number
    Q / Esc              quit (progress is saved automatically)

Your marks are saved after every toggle to (shown for label "void"):
    void_pages.csv     (filename, void)  <- the result you want
    void_state.json    (marks + where you left off, so you can resume)
"""

import csv
import json
import re
import sys
import threading
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import simpledialog

from PIL import Image, ImageTk

# ============================== SETTINGS ====================================
SOURCE = Path("auditor-stubs-handled.pdf")
TOGGLE_KEY = "1"          # key that toggles the label
AUTO_ADVANCE = False      # True = jump to the next page after toggling
PREFETCH_AHEAD = 4        # pages decoded in the background ahead of you
PREFETCH_BEHIND = 1
CACHE_SIZE = 16           # decoded pages kept in memory
PDF_ZOOM = 1.5            # PDF render scale (higher = sharper but slower)
# ============================================================================


# ------------------------------------------------------- label (argument)
def parse_label():
    if len(sys.argv) != 2:
        sys.exit("Usage: python void_marker.py <label>\n"
                 "  e.g. python void_marker.py void\n"
                 "       python void_marker.py test")
    slug = re.sub(r"[^a-z0-9_-]", "", sys.argv[1].lower())
    if not slug:
        sys.exit("Label must contain letters or numbers.")
    return slug


SLUG = parse_label()
LABEL = SLUG.upper()                      # what is shown on screen
CSV_FILE = Path(f"{SLUG}_pages.csv")      # e.g. test_pages.csv
STATE_FILE = Path(f"{SLUG}_state.json")   # e.g. test_state.json


# ---------------------------------------------------------------- sources
class FolderSource:
    def __init__(self, folder: Path):
        self.files = sorted(
            (p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}),
            key=lambda p: p.name.lower())
        self.names = [p.name for p in self.files]

    def load(self, i: int) -> Image.Image:
        with Image.open(self.files[i]) as im:
            im.seek(0)
            return im.convert("L")


class PdfSource:
    def __init__(self, path: Path):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            sys.exit("PDF support needs PyMuPDF:  pip install pymupdf")
        self.fitz = fitz
        self.doc = fitz.open(path)
        self.lock = threading.Lock()  # PyMuPDF is not thread-safe
        self.names = [f"page_{i + 1:04d}" for i in range(len(self.doc))]

    def load(self, i: int) -> Image.Image:
        with self.lock:
            pix = self.doc[i].get_pixmap(
                matrix=self.fitz.Matrix(PDF_ZOOM, PDF_ZOOM),
                colorspace=self.fitz.csGRAY)
            return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def open_source(path: Path):
    if path.is_dir():
        return FolderSource(path)
    if path.suffix.lower() == ".pdf":
        return PdfSource(path)
    sys.exit(f"SOURCE must be a folder of TIFFs or a .pdf file: {path}")


# ----------------------------------------------------------------- viewer
class Viewer:
    def __init__(self, root: tk.Tk, source):
        self.root = root
        self.src = source
        self.n = len(source.names)
        if self.n == 0:
            sys.exit("No pages found.")

        self.voided = set()
        self.idx = 0
        self._load_state()

        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()
        self.pending = set()
        self.pool = ThreadPoolExecutor(max_workers=2)

        root.title(f"{LABEL} marker")
        self.status = tk.Label(root, anchor="w", font=("Arial", 13), padx=8, pady=4)
        self.status.pack(side="top", fill="x")
        self.canvas = tk.Canvas(root, bg="#333333", highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.help = tk.Label(
            root, anchor="w", padx=8, pady=3, fg="#555555",
            text=f"{TOGGLE_KEY}=toggle {LABEL}   \u2190/\u2192=prev/next   \u2191/\u2193=\u00b110   "
                 f"[ / ]=prev/next {LABEL}   G=go to   Q=quit")
        self.help.pack(side="bottom", fill="x")

        root.bind("<Key>", self.on_key)
        self.canvas.bind("<Configure>", lambda e: self.root.after_idle(self.show))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.focus_force()
        self.root.after(50, self.show)

    # ------------------------------------------------------------- state
    def _load_state(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                names = set(self.src.names)
                self.voided = {n for n in data.get("voided", []) if n in names}
                self.idx = min(max(int(data.get("last", 0)), 0), self.n - 1)
            except Exception:
                pass

    def save(self):
        STATE_FILE.write_text(json.dumps(
            {"voided": sorted(self.voided), "last": self.idx}))
        with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["filename", SLUG])
            for name in self.src.names:
                w.writerow([name, "YES" if name in self.voided else ""])

    # ------------------------------------------------------------- cache
    def _decode(self, i):
        try:
            img = self.src.load(i)
        except Exception:
            img = Image.new("L", (800, 1000), 128)
        with self.cache_lock:
            self.cache[i] = img
            self.cache.move_to_end(i)
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
            self.pending.discard(i)

    def get_image(self, i):
        with self.cache_lock:
            img = self.cache.get(i)
            if img is not None:
                self.cache.move_to_end(i)
                return img
        self._decode(i)
        with self.cache_lock:
            return self.cache[i]

    def prefetch(self):
        lo = max(0, self.idx - PREFETCH_BEHIND)
        hi = min(self.n - 1, self.idx + PREFETCH_AHEAD)
        for j in range(lo, hi + 1):
            with self.cache_lock:
                if j in self.cache or j in self.pending:
                    continue
                self.pending.add(j)
            self.pool.submit(self._decode, j)

    # ----------------------------------------------------------- drawing
    def show(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        img = self.get_image(self.idx)
        scale = min(cw / img.width, ch / img.height)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        resized = img.resize(size, Image.BILINEAR, reducing_gap=2.0)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self.photo)
        self.draw_overlay()
        self.prefetch()

    def draw_overlay(self):
        self.canvas.delete("overlay")
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        name = self.src.names[self.idx]
        is_void = name in self.voided
        if is_void:
            self.canvas.create_rectangle(5, 5, cw - 5, ch - 5, outline="red",
                                         width=10, tags="overlay")
            self.canvas.create_text(cw // 2, 45, text=LABEL, fill="red",
                                    font=("Arial", 56, "bold"), tags="overlay")
        self.status.config(
            text=f"Page {self.idx + 1} / {self.n}    {name}    "
                 f"{'*** ' + LABEL + ' ***' if is_void else ''}    "
                 f"({len(self.voided)} marked)",
            fg="red" if is_void else "black")

    # -------------------------------------------------------- navigation
    def goto(self, i):
        i = min(max(i, 0), self.n - 1)
        if i != self.idx:
            self.idx = i
            self.show()
            self.save_position()

    def save_position(self):
        # cheap: only the state file, not the CSV
        STATE_FILE.write_text(json.dumps(
            {"voided": sorted(self.voided), "last": self.idx}))

    def jump_void(self, step):
        i = self.idx + step
        while 0 <= i < self.n:
            if self.src.names[i] in self.voided:
                self.goto(i)
                return
            i += step

    def toggle(self):
        name = self.src.names[self.idx]
        if name in self.voided:
            self.voided.discard(name)
        else:
            self.voided.add(name)
        self.save()
        self.draw_overlay()
        if AUTO_ADVANCE:
            self.goto(self.idx + 1)

    def on_key(self, e):
        k, c = e.keysym, e.char
        if c == TOGGLE_KEY:
            self.toggle()
        elif k in ("Right", "space", "d", "D"):
            self.goto(self.idx + 1)
        elif k in ("Left", "BackSpace", "a", "A"):
            self.goto(self.idx - 1)
        elif k in ("Down", "Next"):
            self.goto(self.idx + 10)
        elif k in ("Up", "Prior"):
            self.goto(self.idx - 10)
        elif k == "Home":
            self.goto(0)
        elif k == "End":
            self.goto(self.n - 1)
        elif c == "]":
            self.jump_void(1)
        elif c == "[":
            self.jump_void(-1)
        elif k in ("g", "G"):
            n = simpledialog.askinteger("Go to page", f"Page number (1-{self.n}):",
                                        parent=self.root, minvalue=1, maxvalue=self.n)
            if n:
                self.goto(n - 1)
        elif k in ("q", "Q", "Escape"):
            self.quit()

    def quit(self):
        self.save()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    if not SOURCE.exists():
        sys.exit(f"SOURCE not found: {SOURCE}  (edit SOURCE at the top of the script)")
    source = open_source(SOURCE)
    root = tk.Tk()
    try:
        root.state("zoomed")            # Windows
    except tk.TclError:
        try:
            root.attributes("-zoomed", True)   # Linux
        except tk.TclError:
            root.geometry("1200x900")          # macOS fallback
    Viewer(root, source)
    root.mainloop()


if __name__ == "__main__":
    main()