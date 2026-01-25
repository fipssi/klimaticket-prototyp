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


def looks_like_bad_textlayer(text: str) -> bool:
    """
    Heuristik: Textlayer ist vorhanden, aber offenbar "kaputt".
    Kriterien sind bewusst konservativ.
    """
    if not text:
        return True

    s = text.strip()
    if len(s) < 30:
        # sehr wenig Text -> häufig "leerer" oder defekter Layer
        return True

    low = s.lower()

    # 1) Wenn wichtige Schlüsselwörter fehlen, obwohl es ein Meldezettel sein sollte
    # (du kannst diese Liste je Dokumenttyp anpassen)
    expected_markers = ["personendaten", "meldedaten"]
    if not any(m in low for m in expected_markers):
        # Nicht hart als "bad" markieren, aber es ist ein starkes Indiz
        marker_missing = True
    else:
        marker_missing = False

    # 2) Typische OCR/Textlayer-Missreads, die du bereits gesehen hast
    bad_markers = [
        "vomame",            # Vorname -> Vomame
        "hauptwohnsitr",     # Hauptwohnsitz -> Hauptwohnsitr
        "staatsangehdr",     # Staatsangehörigkeit -> Staatsangehdr...
        "postleieahl",       # Postleitzahl -> PostleiEahl
        "wohnsitrqual",      # Wohnsitzqualität -> Wohnsitrqual...
    ]
    if any(m in low for m in bad_markers):
        return True

    # 3) Viele "kaputte" Tokens (zu viele sehr kurze Wörter)
    tokens = re.findall(r"\w+", low)
    if tokens:
        short = sum(1 for t in tokens if len(t) <= 2)
        if short / len(tokens) > 0.35:
            return True

    # 4) Zu viele nicht-ASCII/sonderbare Zeichen kann ein Encoding-Mapping-Problem sein
    non_print = sum(1 for ch in s if ord(ch) < 9 or ord(ch) == 0x0b or ord(ch) == 0x0c)
    if non_print > 0:
        return True

    # Wenn Marker fehlen UND die Seite insgesamt "komisch" wirkt (wenige Tokens)
    if marker_missing and len(tokens) < 10:
        return True

    return False


def _ensure_tesseract_available() -> None:
    """Setzt tesseract_cmd auf Linux/Cloud automatisch, falls möglich."""
    cmd = shutil.which("tesseract")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def ocr_page(path: Path, page_index: int) -> str:
    """
    OCR nur für eine einzelne Seite (wichtig für Performance).
    """
    _ensure_tesseract_available()

    if POPPLER_PATH:
        images = convert_from_path(
            str(path),
            dpi=300,
            poppler_path=POPPLER_PATH,
            first_page=page_index,
            last_page=page_index,
        )
    else:
        images = convert_from_path(
            str(path),
            dpi=300,
            first_page=page_index,
            last_page=page_index,
        )

    # i.d.R. genau ein Bild
    out_parts = []
    for img in images:
        out_parts.append(
            pytesseract.image_to_string(img, lang="deu", config="--oem 1 --psm 6")
        )
    return "\n".join(out_parts).strip()


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(path)
    parts: list[str] = []

    for idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()

        if text and not looks_like_bad_textlayer(text):
            parts.append(text)
        else:
            parts.append(ocr_page(path, idx))

    return "\n\n".join(p for p in parts if p)
