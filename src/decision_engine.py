from pathlib import Path
from typing import Tuple

# Import: Meldezettel-Verarbeitung (macht das detaillierte Matching etc.)
from src.registration_validation import process_meldezettel

# Import: PDF-Text-Extraktion (liefert Rohtext aus den PDFs)
from src.document_loader import extract_text_from_pdf

# Import: spezialisierte Prüf-Funktionen für Rechnungen/Zahlungsbestätigung
from src.invoice_validation import (
    validate_zahlungsbestaetigung,
    validate_rechnung,
    validate_monatsrechnung,
)


def build_melde_decision(form_data: dict,
                         classified_pdfs: list[Tuple[Path, str]]) -> dict:
    """
    Baut eine Entscheidung NUR basierend auf dem Meldezettel.

    form_data:
        Struktur aus dem Web-Formular (Name, Adresse, PLZ, Geburtsdatum, etc.).
    classified_pdfs:
        Liste von (Pfad, Dokumenttyp)-Tupeln, z.B. (".../file.pdf", "meldezettel").
    """
    # Hier speichern wir das Detail-Resultat der Meldezettel-Validierung
    melde_result = None

    # Über alle klassifizierten PDFs iterieren
    for pdf_path, doc_type in classified_pdfs:
        # Nur dann aktiv werden, wenn der Dokumenttyp "meldezettel" ist
        if doc_type == "meldezettel":
            # Text aus dem PDF extrahieren
            text = extract_text_from_pdf(pdf_path)
            # Detailprüfung: Name, Adresse, PLZ, etc. werden hier geprüft
            melde_result = process_meldezettel(form_data, text)

    # Wenn wir keinen Meldezettel gefunden haben (melde_result blieb None)
    if melde_result is None:
        return {
            "meldezettel_found": False,          # kein Meldezettel im Upload
            "meldezettel_ok": False,             # kann also auch nicht ok sein
            "reason": "Kein Meldezettel gefunden.",
        }

    # Wenn ein Meldezettel gefunden wurde, Ergebnis aus dem Detail-Dict ableiten
    return {
        "meldezettel_found": True,                           # es gab einen Meldezettel
        "meldezettel_ok": melde_result.get("all_ok", False), # Hauptflag aus Detailprüfung
        "details": melde_result,                             # vollständige Detailstruktur
    }


def build_invoice_decision(form_data: dict,
                           classified_pdfs: list[Tuple[Path, str]]) -> dict:
    """
    Entscheidet NUR anhand der Rechnungen (ohne Meldezettel).

    Ziel:
    - Prüfen, ob die hochgeladenen Rechnungs-Dokumente fachlich ausreichen.
    - Nur zusammenfassende Flags zurückgeben, aber auf Wunsch auch Detail-Infos.
    """
    # Flags für Jahresrechnung
    jahresrechnung_found = False    # Wurde eine Jahresrechnung gefunden?
    jahresrechnung_ok = False       # Ist diese Jahresrechnung inhaltlich korrekt?
    jahresrechnung_details = None   # Detail-Dict für Debug/Sachbearbeitung

    # Flags für Zahlungsbestätigung
    zahlung_found = False           # Wurde eine Zahlungsbestätigung gefunden?
    zahlung_ok = False              # Ist diese Zahlungsbestätigung inhaltlich korrekt?
    zahlung_details = None          # Detail-Dict für Debug/Sachbearbeitung

    # Zähler für Monatsrechnungen
    monats_found = 0                # Wie viele Monatsrechnungen wurden gefunden?
    monats_valid = 0                # Wie viele davon waren gültig?

    # Über alle klassifizierten PDFs iterieren
    for pdf_path, doc_type in classified_pdfs:
        # Text aus PDF holen (Basis für alle Prüf-Funktionen)
        text = extract_text_from_pdf(pdf_path)

        if doc_type == "jahresrechnung":
            # Mindestens eine Jahresrechnung gefunden
            jahresrechnung_found = True
            # Detail-Resultat holen (Dict mit name_ok, period_ok, all_ok, etc.)
            j_res = validate_rechnung(form_data, text)
            # Dieses Dict speichern wir für Debugging / UI
            jahresrechnung_details = j_res
            # Für die eigentliche Entscheidung reicht das Gesamtflag all_ok
            jahresrechnung_ok = bool(j_res.get("all_ok"))

        elif doc_type == "zahlungsbestaetigung":
            # Zahlungsbestätigung wurde gefunden
            zahlung_found = True
            # Detail-Resultat holen (Dict mit name_ok, period_ok, all_ok, etc.)
            z_res = validate_zahlungsbestaetigung(form_data, text)
            # Dieses Dict speichern wir für Debugging / UI
            zahlung_details = z_res
            # Für die eigentliche Entscheidung reicht das Gesamtflag all_ok
            zahlung_ok = bool(z_res.get("all_ok"))

        elif doc_type == "monatsrechnung":
            # Jede gefundene Monatsrechnung zählen
            monats_found += 1
            m_res = validate_monatsrechnung(form_data, text)
            # Monatsrechnung zählt nur, wenn alles ok ist (inkl. Name)
            if m_res.get("all_ok"):
                monats_valid += 1

    # Monatsrechnungen gelten als ausreichend, wenn mindestens 3 gültig sind
    monats_ok = monats_valid >= 3

    # Aktuelle Entscheidungsregel:
    # - Entweder: Jahresrechnung vorhanden UND ok
    # - ODER: Zahlungsbestätigung vorhanden UND ok
    # - ODER: (mindestens 3 gültige Monatsrechnungen)
    rechnungen_ok = (
        (jahresrechnung_found and jahresrechnung_ok)
        or (zahlung_found and zahlung_ok)
        or monats_ok
    )

    # Zusammenfassendes Ergebnis, das die Decision Engine weiterverwenden kann
    return {
        # Infos zur Jahresrechnung
        "jahresrechnung_found": jahresrechnung_found,
        "jahresrechnung_ok": jahresrechnung_ok,               # nur True/False
        "jahresrechnung_details": jahresrechnung_details,     # komplettes Dict

        # Infos zur Zahlungsbestätigung
        "zahlungsbestaetigung_found": zahlung_found,
        "zahlungsbestaetigung_ok": zahlung_ok,                # nur True/False
        # Detail-Daten nur für Debug/Sachbearbeiter, nicht zwingend für die Entscheidung nötig
        "zahlungsbestaetigung_details": zahlung_details,

        # Infos zu Monatsrechnungen
        "monatsrechnungen_found": monats_found,
        "monatsrechnungen_valid": monats_valid,
        "monatsrechnungen_ok": monats_ok,

        # Gesamt-Flag für „Rechnungsseite passt“
        "rechnungen_ok": rechnungen_ok,
    }


def build_overall_decision(form_data: dict,
                           classified_pdfs: list[Tuple[Path, str]]) -> dict:
    """
    Kombiniert Meldezettel- und Rechnungs-Decision zu einer Gesamtentscheidung.
    """
    melde_decision = build_melde_decision(form_data, classified_pdfs)
    invoice_decision = build_invoice_decision(form_data, classified_pdfs)

    # Nur die zusammengesetzten Bool-Flags verwenden
    melde_ok = melde_decision.get("meldezettel_ok", False)
    rechnungen_ok = invoice_decision.get("rechnungen_ok", False)

    # Gesamtentscheidung: nur ok, wenn beides erfüllt ist
    all_ok = melde_ok and rechnungen_ok

    return {
        "melde_decision": melde_decision,      # komplette Melde-Details
        "invoice_decision": invoice_decision,  # komplette Rechnungs-Details
        "meldezettel_ok": melde_ok,
        "rechnungen_ok": rechnungen_ok,
        "all_ok": all_ok,
    }
