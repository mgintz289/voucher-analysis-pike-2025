from pathlib import Path
from pdf2image import convert_from_path
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageDraw
from multiprocessing import Pool
from paddleocr import PaddleOCR
import argparse, sys, shutil, pymupdf, csv, cv2, img2pdf, io, glob, os, re

import pytesseract
from PIL import Image, ImageOps, ImageSequence

pdf_path = "auditor-stubs-email.pdf"
original_jpg = Path("00 original-jpgs")
deskewed = Path("01 deskewed")
cropped = Path("02 cropped")
hsled = Path("03 hsled")
pralined = Path("04 pralined")
deaudited = Path("05 deaudited")

def folder_reset (myfolder):
    if myfolder.exists():
        shutil.rmtree(myfolder)
    myfolder.mkdir()

# step 00 create jpg files
# region
# if __name__ == "__main__":
#     folder_reset(original_jpg)

#     doc = pymupdf.open(pdf_path)
#     for i, page in enumerate(doc, start=1):
#         pix = page.get_pixmap(dpi=300)
#         pix.save(original_jpg / f"page_{i:04d}.jpg")
# endregion

#steps 01 + 02 base deskew and crop
# region
# if __name__ == "__main__":
#     folder_reset(deskewed)

#     def find_skew(gray, max_angle=5, step=0.1):
#         """Try small rotations and pick the one that makes text rows sharpest."""
#         small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
#         bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
#         h, w = bw.shape
#         best_angle, best_score = 0, -1
#         for angle in np.arange(-max_angle, max_angle + step, step):
#             M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
#             rotated = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST)
#             score = np.var(rotated.sum(axis=1))  # sharp text lines -> high variance
#             if score > best_score:
#                 best_angle, best_score = angle, score
#         return best_angle

#     def content_bbox(gray, margin_frac=0.02):
#         """Bounding box of the dark content, ignoring specks and the page edge."""
#         bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
#         h, w = bw.shape
#         m_y, m_x = int(h * margin_frac), int(w * margin_frac)
#         bw[:m_y, :] = 0; bw[-m_y:, :] = 0; bw[:, :m_x] = 0; bw[:, -m_x:] = 0  # kill scan borders
#         bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))   # remove specks
#         bw = cv2.dilate(bw, np.ones((15, 15), np.uint8))                       # join text
#         pts = cv2.findNonZero(bw)
#         if pts is None:
#             return None
#         return cv2.boundingRect(pts)  # x, y, w, h

#     rows = []
#     for path in sorted(original_jpg.glob("*.jpg")):
#         img = cv2.imread(str(path))
#         gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

#         angle = find_skew(gray)
#         h, w = gray.shape
#         M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
#         img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
#                             borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
#         gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

#         box = content_bbox(gray)
#         cv2.imwrite(str(deskewed / path.name), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
#         rows.append([path.name, round(angle, 2), *(box or (0, 0, 0, 0))])

#     with open("deskew-info.csv", "w", newline="") as f:
#         csv.writer(f).writerows([["file", "angle", "x", "y", "w", "h"], *rows])

#     folder_reset(cropped)
#     PAD = 40  # white margin around the content, in pixels

#     rows = list(csv.DictReader(open("deskew-info.csv")))
#     ws = np.array([int(r["w"]) for r in rows])
#     hs = np.array([int(r["h"]) for r in rows])

#     # Target size: a high percentile, so most pages fit without being clipped
#     target_w = int(np.percentile(ws, 95)) + 2 * PAD
#     target_h = int(np.percentile(hs, 95)) + 2 * PAD
#     print(f"Canvas: {target_w} x {target_h}")

#     # Flag pages whose content is far from the median size
#     med_w, med_h = np.median(ws), np.median(hs)
#     flagged = []

#     for r in rows:
#         img = cv2.imread(str(deskewed / r["file"]))
#         x, y, w, h = (int(r[k]) for k in ("x", "y", "w", "h"))
#         crop = img[y:y + h, x:x + w]

#         if abs(w - med_w) > 0.15 * med_w or abs(h - med_h) > 0.15 * med_h:
#             flagged.append(r["file"])

#         canvas = np.full((target_h, target_w, 3), 255, np.uint8)
#         # Clip if the content is bigger than the canvas
#         crop = crop[:target_h - 2 * PAD, :target_w - 2 * PAD]
#         ch, cw = crop.shape[:2]
#         ox, oy = (target_w - cw) // 2, PAD   # center horizontally, align to top
#         canvas[oy:oy + ch, ox:ox + cw] = crop
#         cv2.imwrite(str(cropped / r["file"]), canvas, [cv2.IMWRITE_JPEG_QUALITY, 90])

#     print(f"{len(flagged)} pages to check manually:")
#     print("\n".join(flagged))
# endregion

#step 03 hsl
# region

# SAT_CUT = 0.10      # saturation mask cutoff (your -threshold 10%)
# GRAY_CUT = 0.20     # final threshold (your -threshold 20%)
# TARGET_DPI = 300
# SOURCE_DPI = None
# CHROMA_CUT = 0.08    # max-min channel difference that counts as "colored"

# def hsl_saturation(rgb):
#     x = rgb.astype(np.float32) / 255
#     mx, mn = x.max(axis=2), x.min(axis=2)
#     l = (mx + mn) / 2
#     d = mx - mn
#     denom = 1 - np.abs(2 * l - 1)
#     return np.divide(d, denom, out=np.zeros_like(d), where=denom > 1e-6)

# def process(path):
#     img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
#     rgb = np.array(img)

#     # 1. mask: absolute chroma, then grow by 1px to catch bled color fringes
#     chroma = (rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2)) / 255
#     mask = Image.fromarray(((chroma > CHROMA_CUT) * 255).astype(np.uint8))
#     mask = np.array(mask.filter(ImageFilter.MaxFilter(3))) > 0
#     rgb[mask] = 255

#     # 2. grayscale, optional resample
#     gray = Image.fromarray(rgb).convert("L")
#     if SOURCE_DPI and SOURCE_DPI != TARGET_DPI:
#         s = TARGET_DPI / SOURCE_DPI
#         gray = gray.resize((round(gray.width * s), round(gray.height * s)),
#                            Image.Resampling.LANCZOS)

#     # 3. hard threshold -> true 1-bit
#     bw = np.array(gray) > GRAY_CUT * 255
#     out = Image.fromarray(bw.astype(np.uint8) * 255).convert("1", dither=Image.Dither.NONE)
#     out.save(hsled / (path.stem + ".tif"), compression="group4", dpi=(TARGET_DPI, TARGET_DPI))
#     return path.name


# if __name__ == "__main__":
#     folder_reset(hsled)
#     files = sorted(p for p in cropped.iterdir() if p.suffix.lower() in (".jpg"))
#     with Pool() as pool:
#         for i, name in enumerate(pool.imap_unordered(process, files), 1):
#             print(f"{i}/{len(files)} {name}")
# endregion

# step 04 orient around PR
# region
# if __name__ == "__main__":

#     # img = cv2.imread("03 hsled/page_0002.tif", cv2.IMREAD_GRAYSCALE)
#     # x, y, w, h = cv2.selectROI("drag a box around (PR), press Enter", img, showCrosshair=False)
#     # cv2.imwrite("template.png", img[y:y+h, x:x+w])

#     SRC_DIR = hsled        # use the ORIGINAL scans, not the already-aligned ones
#     OUT_DIR = pralined

#     # Where to look for "(PR)", as page fractions (x0, y0, x1, y1).
#     # Make this generous but not the whole page.
#     SEARCH = (0.15, 0.0, 0.60, 0.35)
#     MIN_SCORE = 0.6          # below this, the match is flagged as unreliable

#     folder_reset(pralined)
#     tmpl = cv2.imread("template.png", cv2.IMREAD_GRAYSCALE)

#     def natural_key(s):
#         return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

#     def to_gray(img):
#         g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
#         if g.dtype != np.uint8:
#             g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
#         return g

#     def find_pr(gray):
#         h, w = gray.shape
#         x0, y0, x1, y1 = [int(v) for v in (SEARCH[0]*w, SEARCH[1]*h, SEARCH[2]*w, SEARCH[3]*h)]
#         roi = gray[y0:y1, x0:x1]
#         res = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
#         _, score, _, loc = cv2.minMaxLoc(res)
#         return (x0 + loc[0], y0 + loc[1]), score

#     files = sorted(glob.glob(os.path.join(SRC_DIR, "*.tif")) +
#                 glob.glob(os.path.join(SRC_DIR, "*.tiff")), key=natural_key)

#     ref = None
#     for f in files:
#         img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
#         if img is None:
#             print(f"{f}: could not read, skipped"); continue
#         pos, score = find_pr(to_gray(img))
#         out_path = os.path.join(OUT_DIR, os.path.basename(f))

#         if score < MIN_SCORE:
#             print(f"{os.path.basename(f)}: LOW MATCH ({score:.2f}), left unchanged")
#             cv2.imwrite(out_path, img); continue

#         if ref is None:
#             ref = pos
#         dx, dy = ref[0] - pos[0], ref[1] - pos[1]
#         M = np.float32([[1, 0, dx], [0, 1, dy]])
#         white = 255 if img.dtype == np.uint8 else int(np.iinfo(img.dtype).max)
#         shifted = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
#                                 borderValue=(white,) * (img.shape[2] if img.ndim == 3 else 1))
#         cv2.imwrite(out_path, shifted)
#         print(f"{os.path.basename(f)}: score={score:.2f} dx={dx} dy={dy}")
# endregion

# step 05 remove box
# region
# if __name__ == "__main__":
    # folder_reset(deaudited)

    # ERASE = (0.0, 0.0, 0.30, 0.12)
    # ERASETWO = (0.85, 0.0, 1, 0.25)

    # def natural_key(s):
    #     return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    # files = sorted(glob.glob(os.path.join(pralined, "*.tif")), key=natural_key)

    # def box(img):
    #     h, w = img.shape[:2]
    #     return (int(ERASE[0]*w), int(ERASE[1]*h), int(ERASE[2]*w), int(ERASE[3]*h))
    # def boxtwo(img):
    #     h, w = img.shape[:2]
    #     return (int(ERASETWO[0]*w), int(ERASETWO[1]*h), int(ERASETWO[2]*w), int(ERASETWO[3]*h))

    # for f in files:
    #     img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
    #     if img is None:
    #         print(f"{f}: could not read, skipped"); continue
    #     x0, y0, x1, y1 = box(img)
    #     white = 255 if img.dtype == np.uint8 else int(np.iinfo(img.dtype).max)
    #     img[y0:y1, x0:x1] = white
    #     x0, y0, x1, y1 = boxtwo(img)
    #     white = 255 if img.dtype == np.uint8 else int(np.iinfo(img.dtype).max)
    #     img[y0:y1, x0:x1] = white
    #     cv2.imwrite(os.path.join(deaudited, os.path.basename(f)), img)
    # print(f"Done: {len(files)} files written to {deaudited}/")
# endregion

# step 06: get text
# region

# ---- Layout configuration -------------------------------------------------
TOP_END = 0.571             # top region:    0%  -> 59.2%
MID_END = 0.733     # middle region: 59.2% -> 76.6%
BOT_START = 0.733     # middle region: 59.2% -> 76.6%
                            # bottom region: 76.6% -> 100%

N_TOP, N_MID, N_BOT = 12, 4, 4
HEADERS = (
    [f"top_{i}" for i in range(1, N_TOP + 1)]
    + [f"mid_{i}" for i in range(1, N_MID + 1)]
    + [f"bot_{i}" for i in range(1, N_BOT + 1)]
)

# Tesseract: LSTM engine (--oem 1), "single uniform block of text" (--psm 6).
# Try --psm 4 (columns of variable size) or --psm 3 (fully automatic) via
# the --psm flag if your pages have a different structure.
DEFAULT_PSM = 6


def preprocess(img: Image.Image, min_width: int = 2000) -> Image.Image:
    """Grayscale, normalize contrast, and upscale small scans for better OCR."""
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    if img.width < min_width:
        factor = min_width / img.width
        img = img.resize(
            (round(img.width * factor), round(img.height * factor)),
            Image.LANCZOS,
        )
    return img

def ocr_area(img: Image.Image, box, n_lines: int, psm: int, lang: str):
    area = img.crop(box)
    text = pytesseract.image_to_string(
        area, lang=lang, config=f"--oem 1 --psm {psm}"
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = lines[:n_lines]
    return lines + [""] * (n_lines - len(lines))

def process_image(path: Path, psm: int, lang: str, all_frames: bool):
    """Return a list of 19-cell rows (one per image, or per frame if requested)."""
    rows = []
    with Image.open(path) as im:
        frames = ImageSequence.Iterator(im) if all_frames else [im]
        for frame in frames:
            img = preprocess(frame.copy())
            w, h = img.size
            y1 = round(h * TOP_END)
            y2 = round(h * MID_END)
            y3 = round(h * BOT_START)

            top = ocr_area(img, (0, 0, w, y1), N_TOP, psm, lang)
            mid = ocr_area(img, (0, y1, w, y2), N_MID, psm, lang)
            bot = ocr_area(img, (0, y3, w, h), N_BOT, psm, lang)
            rows.append(top + mid + bot)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # ap.add_argument("folder", type=Path, help="Folder containing the .tif files")
    folder = deaudited
    ap.add_argument("-o", "--output", type=Path, default=Path("auditor-stubs.csv"))
    ap.add_argument("--psm", type=int, default=DEFAULT_PSM,
                    help=f"Tesseract page segmentation mode (default {DEFAULT_PSM})")
    ap.add_argument("--lang", default="eng", help="Tesseract language (default eng)")
    ap.add_argument("--no-header", action="store_true", help="Omit the header row")
    ap.add_argument("--include-filename", action="store_true",
                    help="Add a leading 'filename' column (makes 20 columns)")
    ap.add_argument("--all-frames", action="store_true",
                    help="For multi-page TIFFs, OCR every page (one row each) "
                         "instead of just the first")
    ap.add_argument("--tesseract-cmd", default=None,
                    help="Full path to tesseract executable if not on PATH")
    args = ap.parse_args()

    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd

    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    files = sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}),
        key=lambda p: p.name.lower(),
    )
    if not files:
        sys.exit(f"No .tif/.tiff files found in {folder}")

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not args.no_header:
            writer.writerow((["filename"] if args.include_filename else []) + HEADERS)

        for i, path in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {path.name}", file=sys.stderr)
            try:
                rows = process_image(path, args.psm, args.lang, args.all_frames)
            except Exception as e:  # keep going on a bad file
                print(f"  ! failed: {e}", file=sys.stderr)
                rows = [[""] * len(HEADERS)]
            for row in rows:
                writer.writerow(([path.name] if args.include_filename else []) + row)

    print(f"Done. Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

# endregion

# step 98: find voids
# region

# ???

# endregion

# step 99: make pdf
# region
MAKE_PDF = False
if __name__ == "__main__":

    def make_pdf(folder):
        out_pdf = "auditor-stubs-handled.pdf"

        exts = {".jpg", ".jpeg", ".tif", ".tiff"}

        
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)


        with open(out_pdf, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in files]))


        # pages = []
        # for f in files:
        #     img = Image.open(f)
        #     # PDF needs RGB (or L); convert anything else (CMYK, RGBA, palette, 16-bit, etc.)
        #     if img.mode not in ("RGB", "L"):
        #         img = img.convert("RGB")
        #     pages.append(img)

        # pages[0].save(out_pdf, save_all=True, append_images=pages[1:], resolution=300)
        # print(f"Wrote {len(pages)} pages to {out_pdf}")

    if not(MAKE_PDF==False): make_pdf(MAKE_PDF)
# endregion