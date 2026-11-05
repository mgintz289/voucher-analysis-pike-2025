#!/usr/bin/env python3
"""
Build a PDF containing only the pages flagged YES in a flag CSV.

    python extract_flagged.py auditor-stubs-handled.pdf sus_pages.csv sus_pages.pdf

Same alignment as validate_stubs.py: PDF page 1 <-> CSV row 2 (the row after
the header), flag = column 2 equals YES.
"""
import csv
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is required:  pip install pymupdf")


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else "auditor-stubs-handled.pdf"
    flags = sys.argv[2] if len(sys.argv) > 2 else "sus_pages.csv"
    out = sys.argv[3] if len(sys.argv) > 3 else "sus_pages.pdf"

    with open(flags, newline="", encoding="utf-8-sig") as f:
        first = f.readline()
        f.seek(0)
        delim = max([",", "\t", ";", "|"], key=first.count)
        rows = list(csv.reader(f, delimiter=delim))

    src = fitz.open(pdf)
    pages = [i for i, row in enumerate(rows[1:])
             if i < src.page_count and len(row) > 1 and row[1].strip().upper() == "YES"]
    if not pages:
        sys.exit("No flagged pages found.")

    dst = fitz.open()
    for i in pages:
        dst.insert_pdf(src, from_page=i, to_page=i)
    dst.save(out)
    print(f"Wrote {len(pages)} pages to {out}")
    print("Original page numbers:", ", ".join(str(i + 1) for i in pages))


if __name__ == "__main__":
    main()
