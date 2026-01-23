import os
from pathlib import Path
from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract

# Nur lokal unter Windows spezielle Pfade setzen
if os.name == "nt":
    POPPLER_PATH = r"C:\Program Files\poppler-24.02.0\Library\bin"
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    POPPLER_PATH = None  # in der Cloud: Poppler kommt aus packages.txt, Tesseract aus dem System


def extract_text_from_pdf(path: Path) -> str:
    """
    Liest Text aus einer PDF-Datei.

    Strategie:
    1. Erst versuchen, normalen (eingebetteten) Text aus den PDF-Seiten zu lesen.
    2. Wenn auf einer Seite kein Text vorhanden ist (typisch bei Scan-/Bild-PDFs),
       wird als Fallback OCR mit Tesseract verwendet.
    """
    # PdfReader öffnet das PDF und erlaubt Zugriff auf einzelne Seiten
    reader = PdfReader(path)
    parts: list[str] = []

    # Jede Seite nacheinander verarbeiten
    for page in reader.pages:
        # 1. Versuch: eingebetteten Text direkt aus der PDF-Seite holen
        text = page.extract_text()

        if text:
            # Fall „digitales PDF“: Text wurde gefunden → einfach anhängen
            parts.append(text)
        else:
            # Fall „Scan-/Bild-PDF“: kein eingebetteter Text vorhanden
            # → komplette Datei (oder alle Seiten) als Bilder rendern

            if POPPLER_PATH:
                # Lokal unter Windows: Poppler muss über POPPLER_PATH gefunden werden
                images = convert_from_path(str(path), poppler_path=POPPLER_PATH)
            else:
                # In der Cloud / auf Linux: Poppler kommt aus dem System-PATH,
                # daher ohne poppler_path-Parameter aufrufen
                images = convert_from_path(str(path))

            ocr_text_parts: list[str] = []

            # Über alle gerenderten Seitenbilder iterieren
            for img in images:
                # Tesseract-OCR liest Text aus dem Bild heraus
                ocr_text = pytesseract.image_to_string(img)
                ocr_text_parts.append(ocr_text)

            # Alle OCR-Texte der Seiten zu einem String zusammenfassen
            parts.append("\n".join(ocr_text_parts))

    # Am Ende: alle Seiten-Texte (direkt + OCR) zu einem einzigen großen Text verbinden
    return "\n".join(parts)
