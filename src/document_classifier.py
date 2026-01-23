from pathlib import Path
import joblib
from typing import Literal
from src.document_loader import extract_text_from_pdf

# Definiert alle möglichen Dokumenttypen für die Klassifikation
DocumentType = Literal[
    "meldezettel",
    "jahresrechnung",
    "monatsrechnung",
    "zahlungsbestaetigung",
    "unbekannt",
]

# Projektwurzel und models-Ordner bestimmen
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"

# Vektorisierer und Modell einmalig beim Import laden
VECTORIZER = joblib.load(MODELS_DIR / "document_vectorizer.joblib")
CLASSIFIER = joblib.load(MODELS_DIR / "document_classifier.joblib")

def classify_document(text: str) -> DocumentType:
    """
    Klassifiziert ein Text-Dokument in einen Dokumenttyp.
    """
    # Text in TF-IDF-Features umwandeln (gleicher Vektorisierer wie beim Training)
    X = VECTORIZER.transform([text])

    # Modell sagt die Klasse voraus
    predicted_label: str = CLASSIFIER.predict(X)[0]

    # Fallback, falls etwas Unerwartetes zurückkommt
    valid_labels = {
        "meldezettel",
        "jahresrechnung",
        "monatsrechnung",
        "zahlungsbestaetigung",
    }
    if predicted_label not in valid_labels:
        return "unbekannt"

    return predicted_label  # type: ignore[return-value]

# UM EINEN GANZEN ORDNER ZU KLASSIFIZIEREN:

def classify_case_pdfs(case_dir: Path) -> list[tuple[Path, str]]:
    """
    Nimmt einen Case-Ordner (z.B. data/cases/20001),
    klassifiziert alle PDFs darin und gibt (pfad, typ) zurück.
    """
    results: list[tuple[Path, str]] = []

    for pdf_path in case_dir.glob("*.pdf"):
        if pdf_path.suffix.lower() != ".pdf":
            continue
        text = extract_text_from_pdf(pdf_path)
        doc_type = classify_document(text)
        results.append((pdf_path, doc_type))

    return results