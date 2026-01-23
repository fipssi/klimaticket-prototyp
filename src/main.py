# Steuert den Ablauf für einen Förderantrag (Case laden, Meldezettel prüfen) in main.py
import json
from pathlib import Path


from document_loader import extract_text_from_pdf
from document_classifier import classify_document
from document_classifier import classify_case_pdfs

from registration_validation import (
    extract_value_after_label,
    extract_first_name_from_melde,
    first_name_matches,
    extract_last_name_from_melde,
    last_name_matches,
    extract_birthdate_from_melde,
    birthdate_matches,
    extract_current_main_residence_postal_code,
    is_postcode_foerderberechtigt,
    process_meldezettel
)

from invoice_validation import (
    validate_zahlungsbestaetigung,
    extract_period_from_zahlungsbestaetigung,
    validate_rechnung,
    extract_name_from_rechnung,
    validate_monatsrechnung
)

from decision_engine import build_melde_decision, build_invoice_decision, build_overall_decision




BASE_DIR = Path(__file__).resolve().parent.parent
CASES_DIR = BASE_DIR / "data" / "cases"

def load_case(case_id: str) -> dict:
    case_dir = CASES_DIR / case_id
    with open(case_dir / "antrag.json", "r", encoding="utf-8") as f:
        form_data = json.load(f)
    return form_data

def main():
    case_id = "20001"
    case_dir = CASES_DIR / case_id
    form_data = load_case(case_id)

    classified_pdfs = classify_case_pdfs(case_dir)

    # Alle PDFs mit erkannter Klassifikation anzeigen
    print(f"Klassifikation für Case {case_id}:")
    for pdf_path, doc_type in classified_pdfs:
        print(f" - {pdf_path.name}: {doc_type}")


    # Meldezettel verarbeiten
    melde_result = None
    for pdf_path, doc_type in classified_pdfs:
        if doc_type == "meldezettel":
            melde_text = extract_text_from_pdf(pdf_path)
            melde_result = process_meldezettel(form_data, melde_text)

    print("Meldezettel-Result NEU:", melde_result)

    # Zahlungsbestätigung prüfen
    invoice_text = None
    valid_monatsrechnungen = 0

    # Zahlungsbestätigung und Jahresrechnung prüfen
    for pdf_path, doc_type in classified_pdfs:
        if doc_type == "zahlungsbestaetigung":
            invoice_text = extract_text_from_pdf(pdf_path)
            validate_zahlungsbestaetigung(form_data, invoice_text)

        elif doc_type == "jahresrechnung":
            invoice_text = extract_text_from_pdf(pdf_path)  # <--- neu
            validate_rechnung(form_data, invoice_text)

        elif doc_type == "monatsrechnung":
            invoice_text = extract_text_from_pdf(pdf_path)
            if validate_monatsrechnung(form_data, invoice_text):
                valid_monatsrechnungen += 1

    print("Anzahl gültiger Monatsrechnungen:", valid_monatsrechnungen)

    # Nur noch zum Debuggen der letzten geladenen Rechnung, falls gewünscht
    if invoice_text is not None:
        print(invoice_text)

    for line in invoice_text.splitlines():
        if "Klimaticket" in line and "Classic" in line:
            print("DEBUG Produktzeile:", repr(line))
            print("DEBUG Matches:", re.findall(DATE_PATTERN_DOT, line))

    print("----- MELDEZETTEL TEXT -----")
    print(melde_text)
    print("----- ENDE MELDEZETTEL -----")

    melde_decision = build_melde_decision(form_data, classified_pdfs)
    print("Meldezettel vorhanden:", melde_decision["meldezettel_found"])
    print("Meldezettel gültig:", melde_decision["meldezettel_ok"])

    invoice_decision = build_invoice_decision(form_data, classified_pdfs)
    # Nur das Hauptflag ausgeben
    print("Rechnung gültig:", invoice_decision.get("rechnungen_ok"))

    overall_decision = build_overall_decision(form_data, classified_pdfs)

    print("Meldezettel ok:", overall_decision["meldezettel_ok"])
    print("Rechnungen ok:", overall_decision["rechnungen_ok"])
    print("Gesamtentscheidung all_ok:", overall_decision["all_ok"])

    # FÜR DEBUGGEN AUSGABE:
    print("Melde-Details:", overall_decision["melde_decision"])
    print("Invoice-Details:", overall_decision["invoice_decision"])

if __name__ == "__main__":
    main()