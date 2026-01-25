import os
import re
import shutil
import math
from pathlib import Path

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract


# ----------------------------
# Plattform-Setup
# ----------------------------
if os.name == "nt":
    POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    POPPLER_PATH = None


# ----------------------------
# OCR / Rendering-Settings
# ----------------------------
# Max. Pixelanzahl des gerenderten Bildes (Sicherheits-/Stabilitätsgrenze)
# 60 Mio. Pixel sind i.d.R. gut für Streamlit Cloud, ohne Bomb-Fehler.
MAX_RENDER_PIXELS = 60_000_000

# DPI-Grenzen
DPI_CAP = 300     # nie höher als 300
DPI_FLOOR = 140   # nie niedriger als 140 (sonst wird OCR bei kleiner Schrift schnell schlecht)

# OCR config (du kannst psm je nach Dokumenttyp anpassen)
TESS_CONFIG = "--oem 1 --psm 6 -c preserve_interword_spaces=1"


# ----------------------------
# Heuristik: Textlayer kaputt?
# ----------------------------
def looks_like_bad_textlayer(text: str) -> bool:
    """
    Heuristik: Textlayer ist vorhanden, aber offenbar "kaputt".
    Kriterien sind bewusst konservativ.
    """
    if not text:
        return True

    s = text.strip()
    if len(s) < 30:
        return True

    low = s.lower()

    expected_markers = ["personendaten", "meldedaten"]
    marker_missing = not any(m in low for m in expected_markers)

    bad_markers = [
        "vomame",
        "hauptwohnsitr",
        "staatsangehdr",
        "postleieahl",
        "wohnsitrqual",
    ]
    if any(m in low for m in bad_markers):
        return True

    tokens = re.findall(r"\w+", low)
    if tokens:
        short = sum(1 for t in tokens if len(t) <= 2)
        if short / len(tokens) > 0.35:
            return True

    non_print = sum(1 for ch in s if ord(ch) < 9 or ord(ch) == 0x0b or ord(ch) == 0x0c)
    if non_print > 0:
        return True

    if marker_missing and len(tokens) < 10:
        return True

    return False


def _ensure_tesseract_available() -> None:
    """Setzt tesseract_cmd auf Linux/Cloud automatisch, falls möglich."""
    cmd = shutil.which("tesseract")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _page_size_points(reader: PdfReader, page_index_1based: int) -> tuple[float, float]:
    """
    Liefert Seitenbreite/-höhe in PDF-Points (1 pt = 1/72 inch).
    """
    page = reader.pages[page_index_1based - 1]
    w_pt = float(page.mediabox.width)
    h_pt = float(page.mediabox.height)
    return w_pt, h_pt


def _safe_dpi_for_page(w_pt: float, h_pt: float,
                       max_pixels: int = MAX_RENDER_PIXELS,
                       dpi_cap: int = DPI_CAP,
                       dpi_floor: int = DPI_FLOOR) -> int:
    """
    Wählt DPI so, dass (w/72*dpi) * (h/72*dpi) <= max_pixels
    => dpi <= 72 * sqrt(max_pixels / (w*h))
    """
    if w_pt <= 0 or h_pt <= 0:
        return dpi_floor

    dpi = int(math.floor(72.0 * math.sqrt(max_pixels / (w_pt * h_pt))))
    dpi = max(dpi_floor, min(dpi, dpi_cap))
    return dpi


def _convert_single_page(path: Path, page_index: int, dpi: int):
    """
    Rendert genau eine Seite als Bild.
    """
    kwargs = dict(
        dpi=dpi,
        first_page=page_index,
        last_page=page_index,
    )
    if POPPLER_PATH:
        kwargs["poppler_path"] = POPPLER_PATH

    return convert_from_path(str(path), **kwargs)


def ocr_page(path: Path, page_index: int, reader: PdfReader) -> str:
    """
    OCR für eine einzelne Seite – robust gegen riesige Seiten / DecompressionBomb.
    """
    _ensure_tesseract_available()

    w_pt, h_pt = _page_size_points(reader, page_index)
    dpi = _safe_dpi_for_page(w_pt, h_pt)

    # Fallback-Strategie: wenn etwas schiefgeht, DPI stufenweise reduzieren.
    # (Wichtig online, falls trotzdem ein Bomb-/Memory-Problem auftritt.)
    dpi_candidates = [dpi]
    for step in (240, 200, 170, 150, 140):
        if step < dpi_candidates[-1]:
            dpi_candidates.append(step)

    last_err = None
    images = None

    # Pillow Bomb Error Klasse (import erst hier, damit es überall läuft)
    try:
        from PIL import Image
        DecompressionBombError = Image.DecompressionBombError
    except Exception:
        DecompressionBombError = Exception  # fallback

    for d in dpi_candidates:
        try:
            images = _convert_single_page(path, page_index, dpi=d)
            last_err = None
            break
        except DecompressionBombError as e:
            last_err = e
            continue
        except MemoryError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    if images is None:
        raise RuntimeError(f"OCR render failed on page {page_index}: {last_err}")

    out_parts = []
    for img in images:
        out_parts.append(
            pytesseract.image_to_string(img, lang="deu", config=TESS_CONFIG)
        )

    return "\n".join(out_parts).strip()


def extract_text_from_pdf(path: Path) -> str:
    """
    - Textlayer verwenden, wenn brauchbar
    - sonst OCR (mit dynamischem DPI, um DecompressionBombError zu vermeiden)
    """
    reader = PdfReader(path)
    parts: list[str] = []

    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()

        if text and not looks_like_bad_textlayer(text):
            parts.append(text)
        else:
            parts.append(ocr_page(path, idx, reader))

    return "\n\n".join(p for p in parts if p)
