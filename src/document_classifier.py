"""
document_classifier.py — ML-basierte Dokumentklassifikation für KlimaTicket-Förderanträge
=========================================================================================

ÜBERBLICK
---------
Dieses Modul ist der erste Schritt in der Verarbeitungspipeline.
Es nimmt den extrahierten Text eines PDFs und bestimmt per Machine-Learning-Modell,
um welchen Dokumenttyp es sich handelt:

    PDF-Text  →  ML-Classifier  →  ("jahresrechnung", 0.92)

Die Klassifikation basiert auf einem vortrainierten Modell (scikit-learn),
das als .joblib-Datei im models/-Ordner gespeichert ist.


ARCHITEKTUR
-----------
Das Modell besteht aus zwei Komponenten:

    1. VECTORIZER (TF-IDF oder CountVectorizer)
       Wandelt den Rohtext in einen numerischen Feature-Vektor um.
       Beispiel: "KlimaTicket Rechnung Karteninhaber" → [0.0, 0.3, 0.0, 0.8, ...]
       Gespeichert in: models/document_vectorizer.joblib

    2. CLASSIFIER (z.B. LogisticRegression, SVM, RandomForest)
       Nimmt den Feature-Vektor und gibt eine Klasse + Konfidenz zurück.
       Gespeichert in: models/document_classifier.joblib

    Ablauf:
        Text → VECTORIZER.transform() → Feature-Vektor → CLASSIFIER.predict() → Label
                                                        → CLASSIFIER.predict_proba() → Konfidenz


MÖGLICHE DOKUMENTTYPEN
----------------------
    "meldezettel"           — Bestätigung der Meldung (Meldezettel / Meldebestätigung)
    "jahresrechnung"        — KlimaTicket-Jahresrechnung (Leistungszeitraum ≥ 10 Monate)
    "monatsrechnung"        — KlimaTicket-Monatsrechnung (Leistungszeitraum < 10 Monate)
    "zahlungsbestaetigung"  — Zahlungsbestätigung für das KlimaTicket
    "unbekannt"             — Keinem bekannten Typ zuordenbar


WO WIRD DIESES MODUL VERWENDET?
--------------------------------
    1. main.py → classify_case_pdfs()
       Batch-Verarbeitung: Klassifiziert alle PDFs in einem Case-Ordner

    2. app.py (Streamlit) → classify_document()
       Einzelne PDFs, die der User im Web-UI hochlädt

    In beiden Fällen wird das Ergebnis an die Decision Engine weitergegeben:
        classify_document(text) → (doc_type, confidence)
        → decision_engine.build_overall_decision(form_data, classified_pdfs)
"""

from pathlib import Path
import joblib
from typing import Literal, Tuple
from src.document_loader import extract_text_from_pdf


# =============================================================================
# DOKUMENTTYPEN
# =============================================================================
#
# Literal-Type: Definiert alle erlaubten Werte für den Dokumenttyp.
# Das hilft bei der Code-Vervollständigung und Fehlerprüfung in der IDE.
# Wenn jemand einen falschen Typ verwendet (z.B. "rechnung"), markiert
# die IDE das als Fehler.

DocumentType = Literal[
    "meldezettel",              # Meldebestätigung aus dem ZMR
    "jahresrechnung",           # Jahresrechnung für KlimaTicket (≥ 10 Monate Leistungszeitraum)
    "monatsrechnung",           # Monatsrechnung für KlimaTicket (< 10 Monate Leistungszeitraum)
    "zahlungsbestaetigung",     # Zahlungsbestätigung
    "unbekannt",                # Nicht erkannt / nicht zuordenbar
]


# =============================================================================
# MODELL LADEN (einmalig beim Import)
# =============================================================================
#
# Die Modelle werden beim ersten `import document_classifier` geladen
# und bleiben dann im Speicher. Das ist effizient, weil:
#   - Das Laden von .joblib-Dateien ~100ms dauert
#   - Danach ist jede Klassifikation nur noch ~1ms
#   - Wir wollen nicht bei jedem PDF neu laden
#
# Pfad-Auflösung:
#   __file__           = z.B. /app/src/document_classifier.py
#   .parents[1]        = /app/                (2 Ebenen hoch: src → Projekt-Root)
#   MODELS_DIR         = /app/models/
#
# Wenn die .joblib-Dateien fehlen, stürzt das Programm beim Import ab
# mit einem FileNotFoundError — das ist gewollt, weil ohne Modell
# keine Klassifikation möglich ist.

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"

# Vectorizer: Wandelt Text → numerischen Feature-Vektor
# Wurde beim Training auf den gleichen Dokumenttypen trainiert.
# WICHTIG: Muss exakt derselbe Vectorizer sein, der beim Training verwendet wurde!
# Ein anderer Vectorizer erzeugt andere Feature-Vektoren → Modell gibt Müll aus.
VECTORIZER = joblib.load(MODELS_DIR / "document_vectorizer.joblib")

# Classifier: Nimmt Feature-Vektor → gibt Label + Wahrscheinlichkeit zurück
CLASSIFIER = joblib.load(MODELS_DIR / "document_classifier.joblib")

# Prüfe einmalig, ob das Modell predict_proba unterstützt.
# Manche Modelle (z.B. SVM mit bestimmten Kernels) haben kein predict_proba.
# In dem Fall verwenden wir confidence=1.0 als Fallback.
# Die meisten sklearn-Modelle (LogisticRegression, RandomForest, etc.)
# unterstützen predict_proba.
_HAS_PROBA = hasattr(CLASSIFIER, "predict_proba") and callable(CLASSIFIER.predict_proba)


# =============================================================================
# GÜLTIGE LABELS
# =============================================================================
#
# Set aller Labels, die wir als "erkannt" akzeptieren.
# Falls das ML-Modell ein Label zurückgibt, das NICHT in diesem Set ist
# (z.B. weil das Modell auf andere Daten trainiert wurde), wird der
# Dokumenttyp auf "unbekannt" gesetzt.
#
# Hinweis: "unbekannt" ist bewusst NICHT in diesem Set, damit es als
# Fallback dient und nie direkt vom Modell kommt.

_VALID_LABELS = {
    "meldezettel",
    "jahresrechnung",
    "monatsrechnung",
    "zahlungsbestaetigung",
}


# =============================================================================
# EINZELDOKUMENT KLASSIFIZIEREN
# =============================================================================

def classify_document(text: str) -> Tuple[DocumentType, float]:
    """
    Klassifiziert einen Text in einen Dokumenttyp.

    Ablauf:
        1. Text → Feature-Vektor (via VECTORIZER)
        2. Feature-Vektor → Label (via CLASSIFIER.predict)
        3. Konfidenz bestimmen (via CLASSIFIER.predict_proba, falls verfügbar)
        4. Prüfen, ob das Label gültig ist → sonst "unbekannt"

    Parameter:
        text: Der extrahierte Text aus einem PDF.
              Kann aus pdftotext oder OCR stammen (document_loader.py).

    Rückgabe:
        Tupel (doc_type, confidence):
            doc_type:   str   — Einer der Werte aus DocumentType
            confidence: float — Wahrscheinlichkeit des Modells (0.0 bis 1.0)
                                Beispiel: 0.92 = 92% sicher, dass es dieser Typ ist

    Beispiel:
        >>> classify_document("Karteninhaber:in Musterfrau Erika ...")
        ("jahresrechnung", 0.94)

        >>> classify_document("Bestätigung der Meldung ...")
        ("meldezettel", 0.87)
    """

    # ── Schritt 1: Text → Feature-Vektor ──
    # transform() erwartet eine Liste von Texten (auch bei nur einem Text).
    # Ergebnis: Sparse-Matrix mit einer Zeile und N Spalten (eine pro Feature/Wort).
    X = VECTORIZER.transform([text])

    # ── Schritt 2: Feature-Vektor → Label ──
    # predict() gibt ein Array zurück → [0] für das erste (und einzige) Element.
    # Beispiel: "jahresrechnung"
    predicted_label: str = CLASSIFIER.predict(X)[0]

    # ── Schritt 3: Konfidenz bestimmen ──
    if _HAS_PROBA:
        # predict_proba() gibt eine Matrix zurück:
        # Jede Zeile = ein Dokument, jede Spalte = eine Klasse.
        #
        # Beispiel für 4 Klassen:
        #   [0.02, 0.94, 0.01, 0.03]
        #    ↑      ↑     ↑     ↑
        #    melde  jahres monats zahlung
        #
        # Wir brauchen den Wert an der Position des vorhergesagten Labels.
        proba = CLASSIFIER.predict_proba(X)[0]

        # Index des vorhergesagten Labels in der Klassen-Liste finden
        # CLASSIFIER.classes_ = z.B. ["jahresrechnung", "meldezettel", "monatsrechnung", ...]
        class_index = list(CLASSIFIER.classes_).index(predicted_label)
        confidence = float(proba[class_index])
    else:
        # Fallback: Wenn das Modell keine Wahrscheinlichkeiten liefert,
        # setzen wir die Konfidenz auf 1.0.
        # Die Decision Engine filtert dann nicht nach Konfidenz.
        confidence = 1.0

    # ── Schritt 4: Label validieren ──
    # Schutz gegen unerwartete Labels (z.B. nach Modell-Update mit neuen Klassen)
    if predicted_label not in _VALID_LABELS:
        return "unbekannt", confidence

    return predicted_label, confidence  # type: ignore[return-value]


# =============================================================================
# GANZEN ORDNER KLASSIFIZIEREN (Batch-Modus)
# =============================================================================
#
# Wird von main.py für die Batch-Verarbeitung verwendet:
#   for case_dir in data/cases/*:
#       classified_pdfs = classify_case_pdfs(case_dir)
#       decision = build_overall_decision(form_data, classified_pdfs)
#
# Jeder Case-Ordner enthält typischerweise 2-5 PDFs:
#   data/cases/20001/
#       meldezettel.pdf
#       jahresrechnung.pdf
#       (optional: zahlungsbestaetigung.pdf, monatsrechnung_1.pdf, ...)

def classify_case_pdfs(case_dir: Path) -> list[tuple[Path, str, str, float]]:
    """
    Klassifiziert alle PDFs in einem Case-Ordner.

    Ablauf für jede PDF:
        1. Text extrahieren (extract_text_from_pdf → inkl. OCR falls nötig)
        2. Text klassifizieren (classify_document)
        3. Ergebnis als 4-Tupel speichern

    Parameter:
        case_dir: Pfad zum Case-Ordner, z.B. Path("data/cases/20001")

    Rückgabe:
        Liste von 4-Tupeln, jedes Tupel:
            (pdf_path, doc_type, text, confidence)

            pdf_path:   Path  — Pfad zur PDF-Datei
            doc_type:   str   — Erkannter Dokumenttyp
            text:       str   — Extrahierter Text (wird von der Decision Engine wiederverwendet)
            confidence: float — Modell-Konfidenz (0.0–1.0)

    Warum wird der Text mitgegeben?
        Die Decision Engine braucht den Text für die inhaltliche Validierung
        (Name, Geburtsdatum, Gültigkeitszeitraum, etc.). Ohne den Text hier
        mitzugeben, müsste die PDF nochmal geladen und geparst werden —
        das wäre doppelte Arbeit und bei OCR-PDFs besonders langsam.

    Beispiel:
        >>> results = classify_case_pdfs(Path("data/cases/20001"))
        >>> for path, typ, text, conf in results:
        ...     print(f"{path.name}: {typ} ({conf:.0%})")
        meldezettel.pdf: meldezettel (87%)
        rechnung.pdf: jahresrechnung (94%)
    """
    results: list[tuple[Path, str, str, float]] = []

    # Alle .pdf-Dateien im Ordner durchgehen
    for pdf_path in case_dir.glob("*.pdf"):
        # Doppelter Check: glob("*.pdf") könnte theoretisch auch .PDF liefern,
        # aber zur Sicherheit nochmal prüfen
        if pdf_path.suffix.lower() != ".pdf":
            continue

        # Text aus dem PDF extrahieren (inkl. OCR bei Bild-PDFs)
        # Der document_loader trennt Seiten mit \f (Form Feed),
        # was bei Multi-Page-Monatsrechnungen wichtig ist.
        text = extract_text_from_pdf(pdf_path)

        # ML-Klassifikation durchführen
        doc_type, confidence = classify_document(text)

        # 4-Tupel speichern — dieses Format wird von der gesamten
        # Pipeline erwartet (decision_engine, main.py, app.py)
        results.append((pdf_path, doc_type, text, confidence))

    return results