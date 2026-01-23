#Funktion zum auslesen von PDFS
from pathlib import Path
from pypdf import PdfReader
from pdf2image import convert_from_path  # wandelt PDF-Seiten in Bilder um
import pytesseract  # Python-Wrapper für die Tesseract-OCR-Engine

POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"  # für pdf2image wichtig
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def extract_text_from_pdf(path: Path) -> str:
    """
    Liest Text aus einer PDF-Datei.
    - Zuerst wird versucht, normalen (digitalen) Text aus den Seiten zu extrahieren.
    - Wenn eine Seite keinen Text enthält (z.B. Scan/Bild-PDF),
      wird ein OCR-Fallback verwendet: Seite -> Bild -> Tesseract-OCR -> Text.
    """
    reader = PdfReader(path)
    parts: list[str] = []

    for page in reader.pages:
        # 1. Versuch: eingebetteten Text direkt aus der Seite holen
        text = page.extract_text()

        if text:
            # Falls normaler Text gefunden wurde, anhängen
            parts.append(text)
        else:
            # Kein Text → vermutlich Bild-PDF → OCR verwenden
            # convert_from_path rendert alle Seiten des PDFs als Bild.
            # (In einfachen Fällen: ganze Datei noch einmal als Bilder laden.)
            images = convert_from_path(str(path), poppler_path=POPPLER_PATH)

            ocr_text_parts: list[str] = []
            for img in images:
                # pytesseract liest Text aus dem Bild heraus
                ocr_text = pytesseract.image_to_string(img)
                ocr_text_parts.append(ocr_text)

            # Text aller OCR-Seiten zusammenfügen und als Fallback anhängen
            parts.append("\n".join(ocr_text_parts))

    # Alle Seiten-Texte (direkt oder OCR) zu einem String zusammenfügen
    return "\n".join(parts)