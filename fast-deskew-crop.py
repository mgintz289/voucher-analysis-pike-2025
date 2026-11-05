# keeping this version since it seems to be faster!

import csv, math
import numpy as np
import pikepdf

SRC, DST = "auditor-stubs-email.pdf", "deskewed-cropped.pdf"
S = 72 / 300            # 300-dpi pixels -> PDF points
PAD = 40 * S

rows = list(csv.DictReader(open("deskew-info.csv")))
ws = np.array([int(r["w"]) for r in rows])
hs = np.array([int(r["h"]) for r in rows])
Wt = float(np.percentile(ws, 95) * S + 2 * PAD)
Ht = float(np.percentile(hs, 95) * S + 2 * PAD)

pdf = pikepdf.open(SRC)
for r in rows:
    idx = int(r["file"].split("_")[1].split(".")[0]) - 1     # page_0001.jpg -> 0
    page = pdf.pages[idx]

    x0, y0, x1, y1 = (float(v) for v in page.MediaBox)
    W, H = x1 - x0, y1 - y0
    cx, cy = W / 2, H / 2

    # rotation about page center (cv2 positive angle = counter-clockwise, same as PDF y-up)
    th = math.radians(float(r["angle"]))
    c, s = math.cos(th), math.sin(th)
    e = cx - c * cx + s * cy
    f = cy - s * cx - c * cy
    page.contents_add(pikepdf.Stream(pdf, f"q {c:.6f} {s:.6f} {-s:.6f} {c:.6f} {e:.4f} {f:.4f} cm\n".encode()), prepend=True)
    page.contents_add(pikepdf.Stream(pdf, b"\nQ"), prepend=False)

    # crop box: centered horizontally on content, aligned to top (as in your canvas code)
    x, y, w, h = (int(r[k]) for k in ("x", "y", "w", "h"))
    top = H - y * S + PAD
    left = x * S + (w * S) / 2 - Wt / 2
    box = [left, top - Ht, left + Wt, top]
    page.MediaBox = box
    page.CropBox = box

pdf.save(DST, object_stream_mode=pikepdf.ObjectStreamMode.generate, compress_streams=True)