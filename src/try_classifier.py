from pathlib import Path

# Holt Text aus einem PDF
from src.document_loader import extract_text_from_pdf
# Nutzt dein trainiertes Modell
from src.document_classifier import classify_document

# Projektwurzel: Ordner, der src und data enthält
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def try_single_pdf(pdf_path: Path) -> None:
    text = extract_text_from_pdf(pdf_path)
    doc_type, confidence = classify_document(text)
    print(f"Datei: {pdf_path.name} -> erkannter Typ: {doc_type} (Konfidenz: {confidence:.1%})")

if __name__ == "__main__":
    # Basis-Ordner absolut ausgehend von der Projektwurzel
    base = PROJECT_ROOT / "data" / "cases" / "20002"

    # 1) Meldebestätigung
    try_single_pdf(base / "meldebestätigung.pdf")

    # 2) Rechnung
    try_single_pdf(base / "rechnung.pdf")