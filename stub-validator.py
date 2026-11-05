#!/usr/bin/env python3
"""
Stub validator: PDF page on the left, the matching CSV row (as it will be
processed) on the right.

    pip install pymupdf
    python validate_stubs.py auditor-stubs-handled.pdf auditor-stubs.csv

Optional:
    --void void_pages.csv  --sus sus_pages.csv
    --delimiter tab        (default: auto-detected)
    --start 50             (1-based page to open first)
    --offset 0             (shift the CSV row for a page; 0 = page 1 -> CSV line 2)

Keys (when the cursor is NOT inside a text field):
    Right / Left      next / previous page       Home / End   first / last
    v                 toggle void                s            toggle sus
    f                 set top_1 total = sum      w            fit page / fit width
    Up / Down         scroll the PDF             Ctrl+S       save now
Inside a field:  Enter / Esc leaves the field (so arrows navigate again),
                 Ctrl+Right / Ctrl+Left navigate even from inside a field.
"""
import argparse
import codecs
import csv
import io
import os
import re
import shutil
import sys
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import ttk, font as tkfont

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is required:  pip install pymupdf")

# --------------------------------------------------------------------------
# Validation rules (edit these to taste)
# --------------------------------------------------------------------------
DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](?:\d{4}|\d{2})")
NUM_RE = re.compile(r"-?\$?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)")
ID_RE = re.compile(r"\d{4}(?:\.\d+)?")          # top lines, section 1
ACCT_RE = re.compile(r"\d[A-Z]+")               # top lines, section 2
SPLIT_RE = re.compile(r"^(\S+)\s+(\S+)\s+(.*?)\s+(\S+)$")


def is_date(s):
    m = DATE_RE.fullmatch(s)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return 1 <= a <= 31 and 1 <= b <= 31 and (a <= 12 or b <= 12)


def is_number(s):
    return bool(NUM_RE.fullmatch(s))


def to_dec(s):
    s = s.strip()
    if not is_number(s):
        return None
    try:
        return Decimal(s.replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None


CHECKS_FIRST = [None, is_date, None, is_number]
CHECKS_OTHER = [lambda s: bool(ID_RE.fullmatch(s)),
                lambda s: bool(ACCT_RE.fullmatch(s)),
                None, is_number]


def split_line(s):
    """before 1st space | 1st-2nd space | 2nd-last space | after last space"""
    s = s.strip()
    if not s:
        return ["", "", "", ""]
    m = SPLIT_RE.match(s)
    if m:
        return list(m.groups())
    t = s.split()
    if len(t) == 3:
        return [t[0], t[1], "", t[2]]
    if len(t) == 2:
        return [t[0], t[1], "", ""]
    return [t[0], "", "", ""]


def join_line(parts):
    return " ".join(p.strip() for p in parts if p.strip())


# --------------------------------------------------------------------------
# CSV helpers (preserve delimiter / encoding / BOM / line endings)
# --------------------------------------------------------------------------
class CsvFile:
    def __init__(self, path, delimiter=None):
        self.path = path
        self.enc, self.lt, self.delim = "utf-8", "\r\n", delimiter or ","
        self.rows = []
        if os.path.exists(path):
            with open(path, "rb") as f:
                raw = f.read()
            try:
                text = raw.decode("utf-8-sig")
                self.enc = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                self.enc = "latin-1"
            head = raw[:1 << 16]
            self.lt = "\n" if (b"\n" in head and b"\r\n" not in head) else "\r\n"
            if not delimiter:
                first = text.split("\n", 1)[0]
                self.delim = max([",", "\t", ";", "|"], key=first.count)
            self.rows = list(csv.reader(io.StringIO(text, newline=""),
                                        delimiter=self.delim))

    def save(self, backup=False):
        if backup and os.path.exists(self.path) and not os.path.exists(self.path + ".bak"):
            shutil.copy2(self.path, self.path + ".bak")
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding=self.enc, newline="") as f:
            csv.writer(f, delimiter=self.delim, lineterminator=self.lt).writerows(self.rows)
        os.replace(tmp, self.path)


class FlagFile(CsvFile):
    """void_pages.csv / sus_pages.csv: page i (0-based) lives in rows[i+1],
    column 2 is 'YES' when flagged."""

    def __init__(self, path, label):
        super().__init__(path)
        if not self.rows:
            self.rows = [["page", label]]

    def get(self, i):
        idx = i + 1
        return (idx < len(self.rows) and len(self.rows[idx]) > 1
                and self.rows[idx][1].strip().upper() == "YES")

    def toggle(self, i):
        idx = i + 1
        while len(self.rows) <= idx:
            self.rows.append([str(len(self.rows)), ""])
        row = self.rows[idx]
        while len(row) < 2:
            row.append("")
        old = row[1]
        row[1] = "" if self.get(i) else "YES"
        try:
            self.save()
        except Exception:
            row[1] = old
            raise


def find_cols(header, prefix):
    found = []
    for i, h in enumerate(header):
        m = re.fullmatch(prefix + r"_(\d+)", h.strip())
        if m:
            found.append((int(m.group(1)), i))
    return [i for _, i in sorted(found)]


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class App:
    CACHE_MAX = 16
    WIDTHS = (12, 12, 44, 12)
    BAD, WARN, OK = "#ff7b7b", "#fff04d", "white"

    def __init__(self, root, args):
        self.root = root
        self.doc = fitz.open(args.pdf)
        self.n = self.doc.page_count
        self.data = CsvFile(args.csv, args.delimiter)
        if not self.data.rows:
            sys.exit(f"{args.csv} is empty or missing")
        self.rows = self.data.rows
        self.header = self.rows[0]
        self.width = len(self.header)
        for r in self.rows:
            if len(r) < self.width:
                r.extend([""] * (self.width - len(r)))
        self.offset = args.offset
        self.top_cols = find_cols(self.header, "top")
        self.mid_cols = find_cols(self.header, "mid")
        self.bot_cols = find_cols(self.header, "bot")
        if not self.top_cols:
            sys.exit("No top_N columns found in the CSV header.")
        self.ntop = len(self.top_cols)
        self.void = FlagFile(args.void, "void")
        self.sus = FlagFile(args.sus, "sus")

        self.cur = max(0, min(self.n - 1, args.start - 1))
        self.fit = "page"
        self.loading = False
        self.dirty = False
        self.cache = OrderedDict()
        self.img = None
        self._show_pending = False
        self._pf_id = None
        self._cfg_id = None

        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=11)
        self.bold = tkfont.nametofont("TkDefaultFont").copy()
        self.bold.configure(weight="bold", size=11)

        self._build(args)
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(80, self.show)
        root.after(1500, self._autosave)
        root.focus_set()

    # ---------------- UI construction ----------------
    def _cmd(self, fn):
        def run():
            fn()
            self.root.focus_set()
        return run

    def _build(self, args):
        r = self.root
        r.title(f"Stub validator - {os.path.basename(args.pdf)}")
        r.geometry("1500x900")

        bar = ttk.Frame(r, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="\u25c0 Prev", command=self._cmd(lambda: self.step(-1))).pack(side="left")
        self.page_var = tk.StringVar(value=str(self.cur + 1))
        pe = ttk.Entry(bar, width=6, textvariable=self.page_var, justify="center")
        pe.pack(side="left", padx=4)
        pe.bind("<Return>", self._goto_entry)
        pe.bind("<Escape>", self._defocus)
        ttk.Label(bar, text=f"/ {self.n}").pack(side="left")
        ttk.Button(bar, text="Next \u25b6", command=self._cmd(lambda: self.step(1))).pack(side="left", padx=4)
        self.fit_btn = ttk.Button(bar, text="Fit: page (w)", command=self._cmd(self.toggle_fit))
        self.fit_btn.pack(side="left", padx=12)
        self.void_btn = tk.Button(bar, text="Void (v)", width=12,
                                  command=self._cmd(lambda: self.toggle_flag("void")))
        self.void_btn.pack(side="left", padx=2)
        self.sus_btn = tk.Button(bar, text="Sus (s)", width=12,
                                 command=self._cmd(lambda: self.toggle_flag("sus")))
        self.sus_btn.pack(side="left", padx=2)
        self.btn_bg = self.void_btn.cget("bg")
        ttk.Button(bar, text="Save (Ctrl+S)", command=self._cmd(self.save)).pack(side="left", padx=12)

        self.status_var = tk.StringVar()
        self.status_lbl = tk.Label(r, textvariable=self.status_var, anchor="w", padx=6)
        self.status_lbl.pack(side="bottom", fill="x")
        self.status_bg = self.status_lbl.cget("bg")

        pw = ttk.PanedWindow(r, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # left: PDF
        left = ttk.Frame(pw)
        self.canvas = tk.Canvas(left, bg="#555555", highlightthickness=0, bd=0)
        vs = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_cfg)
        self.canvas.bind("<Button-1>", lambda e: self.root.focus_set())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        pw.add(left, weight=1)

        # right: data
        rp = ttk.Frame(pw, padding=6)
        pw.add(rp, weight=1)
        rp.columnconfigure(0, weight=1)
        rp.rowconfigure(3, weight=1)
        rp.rowconfigure(4, weight=1)

        self.banner = tk.Label(rp, text="", font=self.bold, anchor="w", padx=6, pady=4)
        self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.banner_bg = self.banner.cget("bg")

        tf = ttk.LabelFrame(rp, text="Top", padding=4)
        tf.grid(row=1, column=0, sticky="ew")
        tf.columnconfigure(3, weight=1)
        self.top_vars, self.top_entries = [], []
        self.colors = [[self.OK] * 4 for _ in range(self.ntop)]
        for k in range(self.ntop):
            ttk.Label(tf, text=self.header[self.top_cols[k]].strip()).grid(row=k, column=0, sticky="e", padx=(0, 4))
            vars_, ents = [], []
            for j in range(4):
                v = tk.StringVar()
                e = tk.Entry(tf, textvariable=v, width=self.WIDTHS[j], font=self.mono,
                             relief="solid", bd=1, bg=self.OK, fg="black",
                             insertbackground="black")
                e.grid(row=k, column=j + 1, sticky="ew", padx=1, pady=1)
                e.bind("<Return>", self._defocus)
                e.bind("<Escape>", self._defocus)
                v.trace_add("write", lambda *_a, k=k: self._on_top_edit(k))
                vars_.append(v)
                ents.append(e)
            self.top_vars.append(vars_)
            self.top_entries.append(ents)

        sb = ttk.Frame(rp)
        sb.grid(row=2, column=0, sticky="ew", pady=6)
        sb.columnconfigure(0, weight=1)
        self.sum_lbl = tk.Label(sb, text="", anchor="w", font=self.mono, padx=6, pady=3)
        self.sum_lbl.grid(row=0, column=0, sticky="ew")
        self.sum_bg = self.sum_lbl.cget("bg")
        ttk.Button(sb, text="Set total = sum (f)", command=self._cmd(self.fix_sum)).grid(row=0, column=1, padx=(6, 0))

        self.texts = {}
        for idx, (name, cols) in enumerate((("mid", self.mid_cols), ("bot", self.bot_cols))):
            lf = ttk.LabelFrame(rp, text=f"{name}  (one line per column, newline-separated)", padding=4)
            lf.grid(row=3 + idx, column=0, sticky="nsew", pady=(0, 6))
            lf.columnconfigure(0, weight=1)
            lf.rowconfigure(0, weight=1)
            t = tk.Text(lf, height=max(4, len(cols)), font=self.mono, wrap="word", undo=True,
                        relief="solid", bd=1, bg=self.OK, fg="black", insertbackground="black")
            t.grid(row=0, column=0, sticky="nsew")
            t.bind("<<Modified>>", lambda e, name=name: self._on_text_modified(name))
            t.bind("<Escape>", self._defocus)
            t.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
            self.texts[name] = (t, cols)

        self._status("Right/Left = next/prev   v = void   s = sus   f = fix sum   w = fit   "
                     "Esc/Enter = leave field   Ctrl+Right/Left = navigate from a field")
        r.update_idletasks()
        r.after(60, lambda: pw.sashpos(0, int(r.winfo_width() * 0.42)))

    def _bind_keys(self):
        r = self.root
        r.bind_all("<Right>", lambda e: self._plain(lambda: self.step(1)))
        r.bind_all("<Left>", lambda e: self._plain(lambda: self.step(-1)))
        r.bind_all("<Next>", lambda e: self._plain(lambda: self.step(1)))
        r.bind_all("<Prior>", lambda e: self._plain(lambda: self.step(-1)))
        r.bind_all("<Home>", lambda e: self._plain(lambda: self.goto(0)))
        r.bind_all("<End>", lambda e: self._plain(lambda: self.goto(self.n - 1)))
        r.bind_all("<Down>", lambda e: self._plain(lambda: self.canvas.yview_scroll(3, "units")))
        r.bind_all("<Up>", lambda e: self._plain(lambda: self.canvas.yview_scroll(-3, "units")))
        r.bind_all("<Control-Right>", lambda e: self.step(1))
        r.bind_all("<Control-Left>", lambda e: self.step(-1))
        r.bind_all("<Control-s>", lambda e: self.save())
        r.bind_all("<Key>", self._on_key)

    # ---------------- keyboard helpers ----------------
    def _in_input(self):
        try:
            w = self.root.focus_get()
        except Exception:
            return False
        return w is not None and w.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox")

    def _plain(self, fn):
        if not self._in_input():
            fn()

    def _on_key(self, e):
        if self._in_input() or not e.char:
            return
        c = e.char.lower()
        if c == "v":
            self.toggle_flag("void")
        elif c == "s":
            self.toggle_flag("sus")
        elif c == "f":
            self.fix_sum()
        elif c == "w":
            self.toggle_fit()

    def _defocus(self, _e=None):
        self.root.focus_set()
        return "break"

    def _goto_entry(self, _e):
        try:
            self.goto(int(self.page_var.get()) - 1)
        except ValueError:
            pass
        self.page_var.set(str(self.cur + 1))
        self.root.focus_set()
        return "break"

    # ---------------- navigation ----------------
    def step(self, d):
        self.goto(self.cur + d)
        self.root.focus_set()

    def goto(self, i):
        i = max(0, min(self.n - 1, i))
        if i == self.cur:
            return
        self.cur = i
        self.page_var.set(str(i + 1))
        if self._pf_id:
            self.root.after_cancel(self._pf_id)
            self._pf_id = None
        if not self._show_pending:
            self._show_pending = True
            self.root.after_idle(self.show)

    def show(self):
        self._show_pending = False
        self._load_row()
        self._show_image()
        self._update_flags()
        if self._pf_id:
            self.root.after_cancel(self._pf_id)
        self._pf_id = self.root.after(60, self._prefetch_step)

    # ---------------- PDF rendering ----------------
    def _canvas_size(self):
        return self.canvas.winfo_width() - 2, self.canvas.winfo_height() - 2

    def _get_image(self, i, cw, ch):
        key = (i, self.fit, cw, ch)
        img = self.cache.get(key)
        if img is not None:
            self.cache.move_to_end(key)
            return img
        page = self.doc[i]
        rect = page.rect
        if self.fit == "width":
            s = cw / rect.width
        else:
            s = min(cw / rect.width, ch / rect.height)
        s = max(0.2, min(s, 6.0))
        pix = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
        img = tk.PhotoImage(data=pix.tobytes("ppm"))
        self.cache[key] = img
        while len(self.cache) > self.CACHE_MAX:
            self.cache.popitem(last=False)
        return img

    def _show_image(self):
        cw, ch = self._canvas_size()
        if cw < 50 or ch < 50:
            return
        img = self._get_image(self.cur, cw, ch)
        self.img = img
        c = self.canvas
        c.delete("all")
        iw, ih = img.width(), img.height()
        c.create_image(max(0, (cw - iw) // 2), 0, anchor="nw", image=img)
        c.configure(scrollregion=(0, 0, max(iw, cw), max(ih, ch)))
        c.yview_moveto(0)

    def _prefetch_step(self):
        self._pf_id = None
        cw, ch = self._canvas_size()
        if cw < 50 or ch < 50:
            return
        for j in (self.cur + 1, self.cur - 1, self.cur + 2):
            if 0 <= j < self.n and (j, self.fit, cw, ch) not in self.cache:
                self._get_image(j, cw, ch)
                self._pf_id = self.root.after(10, self._prefetch_step)
                return

    def _on_canvas_cfg(self, _e):
        if self._cfg_id:
            self.root.after_cancel(self._cfg_id)
        self._cfg_id = self.root.after(120, self._show_image)

    def toggle_fit(self):
        self.fit = "width" if self.fit == "page" else "page"
        self.fit_btn.configure(text=f"Fit: {self.fit} (w)")
        self._show_image()

    # ---------------- row <-> widgets ----------------
    def _row_index(self, i=None):
        return (self.cur if i is None else i) + 1 + self.offset

    def _ensure_row(self):
        ridx = self._row_index()
        while len(self.rows) <= ridx:
            self.rows.append([""] * self.width)
        return self.rows[ridx]

    def _load_row(self):
        ridx = self._row_index()
        row = self.rows[ridx] if 0 <= ridx < len(self.rows) else None
        self.loading = True
        try:
            for k, col in enumerate(self.top_cols):
                parts = split_line(row[col]) if row else ["", "", "", ""]
                for j in range(4):
                    self.top_vars[k][j].set(parts[j])
            for name, (t, cols) in self.texts.items():
                lines = [row[c] for c in cols if row[c].strip()] if row else []
                t.delete("1.0", "end")
                t.insert("1.0", "\n".join(lines))
                t.configure(bg=self.OK)
                t.edit_modified(False)
                t.edit_reset()
        finally:
            self.loading = False
        self._revalidate()
        note = "" if row else "   (no CSV row for this page)"
        self._banner_text = f"PDF page {self.cur + 1} / {self.n}    \u00b7    CSV line {ridx + 1}{note}"

    def _on_top_edit(self, k):
        if self.loading:
            return
        line = join_line([v.get() for v in self.top_vars[k]])
        row = self._ensure_row()
        col = self.top_cols[k]
        if row[col] != line:
            row[col] = line
            self._mark_dirty()
        self._revalidate()

    def _on_text_modified(self, name):
        t, cols = self.texts[name]
        if not t.edit_modified():
            return
        t.edit_modified(False)
        if self.loading:
            return
        lines = t.get("1.0", "end-1c").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        overflow = len(lines) > len(cols)
        row = self._ensure_row()
        for idx, c in enumerate(cols):
            if idx == len(cols) - 1 and overflow:
                row[c] = " ".join(x.strip() for x in lines[idx:] if x.strip())
            else:
                row[c] = lines[idx] if idx < len(lines) else ""
        t.configure(bg=self.WARN if overflow else self.OK)
        self._mark_dirty()
        if overflow:
            self._status(f"{name}: more lines than columns ({len(cols)}); extra lines are "
                         f"merged into {self.header[cols[-1]].strip()}", warn=True)

    # ---------------- validation ----------------
    def _paint(self, k, j, color):
        if self.colors[k][j] != color:
            self.colors[k][j] = color
            self.top_entries[k][j].configure(bg=color)

    def _revalidate(self):
        total_sum = Decimal(0)
        unreadable = False
        for k in range(self.ntop):
            vals = [v.get().strip() for v in self.top_vars[k]]
            used = any(vals)
            checks = CHECKS_FIRST if k == 0 else CHECKS_OTHER
            for j in range(4):
                chk = checks[j]
                bad = chk is not None and (k == 0 or used) and not chk(vals[j])
                self._paint(k, j, self.BAD if bad else self.OK)
            if k > 0 and used:
                d = to_dec(vals[3])
                if d is None:
                    unreadable = True
                else:
                    total_sum += d
        total = to_dec(self.top_vars[0][3].get())
        mismatch = total is not None and total != total_sum
        if mismatch:
            self._paint(0, 3, self.WARN)
        shown = f"{total_sum:.2f}"
        tstr = "?" if total is None else f"{total}"
        if mismatch:
            msg = f"\u03a3 lines = {shown}   total = {tstr}   \u2718 MISMATCH"
        elif total is None:
            msg = f"\u03a3 lines = {shown}   total unreadable"
        else:
            msg = f"\u03a3 lines = {shown}   total = {tstr}   \u2714"
        if unreadable:
            msg += "   (some line amounts unreadable)"
        self.sum_lbl.configure(text=msg, bg=self.WARN if mismatch else self.sum_bg)
        self._sum_value = total_sum

    def fix_sum(self):
        val = format(self._sum_value.quantize(Decimal("0.01")), "f")
        self.top_vars[0][3].set(val)

    # ---------------- void / sus ----------------
    def toggle_flag(self, which):
        ff = self.void if which == "void" else self.sus
        try:
            ff.toggle(self.cur)
        except Exception as ex:
            self._status(f"Could not write {ff.path}: {ex}", warn=True)
        self._update_flags()

    def _update_flags(self):
        v, s = self.void.get(self.cur), self.sus.get(self.cur)
        self.void_btn.configure(text="VOID \u2714 (v)" if v else "Void (v)",
                                bg="#e57373" if v else self.btn_bg)
        self.sus_btn.configure(text="SUS \u2714 (s)" if s else "Sus (s)",
                               bg="#ffb74d" if s else self.btn_bg)
        text = self._banner_text
        if v:
            text += "    \u2014 VOID"
        if s:
            text += "    \u2014 SUS"
        bg = "#ef9a9a" if v else ("#ffcc80" if s else self.banner_bg)
        self.banner.configure(text=text, bg=bg)

    # ---------------- saving ----------------
    def _mark_dirty(self):
        self.dirty = True

    def save(self):
        try:
            self.data.save(backup=True)
            self.dirty = False
            self._status("Saved.")
        except Exception as ex:
            self._status(f"SAVE FAILED: {ex}", warn=True)

    def _autosave(self):
        if self.dirty:
            self.save()
        self.root.after(1500, self._autosave)

    def _close(self):
        if self.dirty:
            self.save()
        self.root.destroy()

    def _status(self, msg, warn=False):
        self.status_var.set(msg)
        self.status_lbl.configure(bg=self.BAD if warn else self.status_bg)


def main():
    ap = argparse.ArgumentParser(description="Validate extracted stub data against the PDF.")
    ap.add_argument("pdf", nargs="?", default="auditor-stubs-handled.pdf")
    ap.add_argument("csv", nargs="?", default="auditor-stubs.csv")
    ap.add_argument("--void", default="void_pages.csv")
    ap.add_argument("--sus", default="sus_pages.csv")
    ap.add_argument("--delimiter", default=None, help="',' ';' 'tab' ... (default: auto-detect)")
    ap.add_argument("--start", type=int, default=1, help="1-based page to open first")
    ap.add_argument("--offset", type=int, default=0,
                    help="shift CSV row relative to page (default 0: page 1 -> CSV line 2)")
    args = ap.parse_args()
    if args.delimiter and args.delimiter.lower() in ("tab", "\\t"):
        args.delimiter = "\t"
    root = tk.Tk()
    App(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()