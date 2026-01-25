import os
import re
import shutil
from pathlib import Path

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract


if os.name == "nt":
    POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    POPPLER_PATH = None


# OCR-Rendering: A4-Breite bei 300 DPI ≈ 2480 px
# Fallback nutzt diese Breite, um riesige PDF-Seiten sicher zu rendern.
MAX_WIDTH_PX = 2480

TESS_CONFIG = "--oem 1 --psm 6 -c preserve_interword_spaces=1"


def looks_like_bad_textlayer(text: str) -> bool:
    """
    Dokument-agnostisch: erkennt "kaputte" Textlayer ohne
    auf einen speziellen Dokumenttyp (Meldezettel) zu biasen.

    WICHTIG: Entfernt die erwarteten Marker ["personendaten","meldedaten"],
    weil das Rechnungen fälschlich ins OCR zwingt.
    """
    if not text:
        return True

    s = text.strip()
    if len(s) < 25:
        return True

    low = s.lower()

    # Typische Missreads / kaputte Layer
    bad_markers = [
        "vomame",            # Vorname -> Vomame
        "hauptwohnsitr",     # Hauptwohnsitz -> Hauptwohnsitr
        "staatsangehdr",     # Staatsangehörigkeit -> Staatsangehdr...
        "postleieahl",       # Postleitzahl -> PostleiEahl
        "wohnsitrqual",      # Wohnsitzqualität -> Wohnsitrqual...
    ]
    if any(m in low for m in bad_markers):
        return True

    # Viele sehr kurze Tokens -> oft "kaputter" Layer
    tokens = re.findall(r"\w+", low)
    if tokens:
        short = sum(1 for t in tokens if len(t) <= 2)
        if short / len(tokens) > 0.40:
            return True

    # Control chars / non-printables -> kaputt
    non_print = sum(1 for ch in s if ord(ch) < 9 or ord(ch) in (0x0b, 0x0c))
    if non_print > 0:
        return True

    return False


def _ensure_tesseract_available() -> None:
    """Setzt tesseract_cmd auf Linux/Cloud automatisch, falls möglich."""
    cmd = shutil.which("tesseract")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _convert_single_page(path: Path, page_index: int, *, dpi: int | None = None, size=None):
    kwargs = dict(
        first_page=page_index,
        last_page=page_index,
    )
    if dpi is not None:
        kwargs["dpi"] = dpi
    if size is not None:
        kwargs["size"] = size
    if POPPLER_PATH:
        kwargs["poppler_path"] = POPPLER_PATH

    return convert_from_path(str(path), **kwargs)


def ocr_page(path: Path, page_index: int) -> str:
    """
    OCR nur für eine einzelne Seite (wichtig für Performance).

    Saubere Lösung gegen DecompressionBombError:
    - Versuch 1: 300 DPI (beste OCR)
    - Falls DecompressionBombError/MemoryError: Fallback mit fixer Max-Breite (2480px),
      entspricht A4@300dpi und bleibt stabil auch bei riesigen Seiten.
    """
    _ensure_tesseract_available()

    # Pillow-Bomb-Exception sauber abfangen
    try:
        from PIL import Image
        BombError = Image.DecompressionBombError
    except Exception:
        BombError = Exception

    try:
        images = _convert_single_page(path, page_index, dpi=300)
    except (BombError, MemoryError):
        # Fallback: statt DPI -> fixe Breite (verhindert riesige Pixelanzahl)
        images = _convert_single_page(path, page_index, size=(MAX_WIDTH_PX, None))

    out_parts = []
    for img in images:
        out_parts.append(
            pytesseract.image_to_string(img, lang="deu", config=TESS_CONFIG)
        )
    return "\n".join(out_parts).strip()


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(path)
    parts: list[str] = []

    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()

        # Wenn brauchbarer Textlayer da ist: verwenden (auch für Rechnungen!)
        if text and not looks_like_bad_textlayer(text):
            parts.append(text)
            continue

        # sonst OCR
        parts.append(ocr_page(path, idx))

    return "\n\n".join(p for p in parts if p)
