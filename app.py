import os
from pathlib import Path
import tempfile

import streamlit as st

# Module aus deinem Projekt
from src.document_classifier import classify_document
from src.document_loader import extract_text_from_pdf
from src.decision_engine import build_overall_decision

# Session-State initialisieren:
# - decision: speichert das Ergebnis der Decision Engine
# - classified_pdfs: speichert (Pfad, Dokumenttyp)-Paare für Hinweise
if "decision" not in st.session_state:
    st.session_state["decision"] = None
if "classified_pdfs" not in st.session_state:
    st.session_state["classified_pdfs"] = []

# ---------------------------------------------------------
# Titel der Seite
# ---------------------------------------------------------
st.title("Klimaticket-Förderungsprototyp")

# ---------------------------------------------------------
# Abschnitt: Antragsdaten (Eingabeformular)
# ---------------------------------------------------------
st.header("Antragsdaten")

vorname = st.text_input("Vorname")
nachname = st.text_input("Nachname")
plz = st.text_input("PLZ")
geburtsdatum = st.text_input("Geburtsdatum (YYYY-MM-DD)")
ticket_typ = st.text_input("Ticket-Typ (z.B. Classic)")
gilt_von = st.text_input("Gültig von (YYYY-MM-DD)")
gilt_bis = st.text_input("Gültig bis (YYYY-MM-DD)")

# ---------------------------------------------------------
# Abschnitt: Datei-Upload (mehrere PDFs in einem Feld)
# ---------------------------------------------------------
st.header("Dokumente hochladen")

uploaded_files = st.file_uploader(
    "Meldezettel, Rechnung(en) und ggf. Zahlungsbestätigung (PDF) hochladen",
    type=["pdf"],
    accept_multiple_files=True,
)

# ---------------------------------------------------------
# Button löst „Prüfen“-Logik aus
# ---------------------------------------------------------
if st.button("Prüfen"):
    # 1) form_data wie antrag.json aufbauen
    form_data = {
        "antrags_id": "TEST-UI",   # Platzhalter
        "intern_id": "UI-0001",    # Platzhalter
        "familienname": nachname,
        "vorname": vorname,
        "strasse": "",             # optional: eigenes Feld hinzufügen
        "plz": plz,
        "geburtsdatum": geburtsdatum,
        "ticket_typ": ticket_typ,
        "gilt_von": gilt_von,
        "gilt_bis": gilt_bis,
    }

    # Formulardaten zur Kontrolle anzeigen
    st.subheader("Formulardaten (form_data)")
    st.json(form_data)

    # 2) Uploads in ein temporäres Verzeichnis speichern
    st.subheader("Hochgeladene Dateien")
    st.write("Anzahl:", len(uploaded_files))

    if not uploaded_files:
        st.warning(
            "Es wurden keine Dokumente hochgeladen. "
            "Bitte Meldebestätigung und Klimaticket-Rechnung(en) als PDF hochladen."
        )
        # Ohne Dokumente keine weitere Verarbeitung
        st.stop()

    # Temporären Ordner anlegen
    temp_dir = tempfile.mkdtemp()
    st.write("TEMP-Verzeichnis:", temp_dir)

    # Pfade der gespeicherten PDFs sammeln
    paths: list[Path] = []

    for f in uploaded_files:
        save_path = Path(temp_dir) / f.name

        # Dateiinhalt auf Platte schreiben
        with open(save_path, "wb") as out:
            out.write(f.read())

        st.write("- gespeichert als:", str(save_path))
        paths.append(save_path)

    # 3) PDFs klassifizieren -> list[tuple[Path, str]]
    classified_pdfs: list[tuple[Path, str]] = []
    for pdf_path in paths:
        text = extract_text_from_pdf(pdf_path)
        doc_type = classify_document(text)
        classified_pdfs.append((pdf_path, doc_type))
        st.write(f"Dokument {pdf_path.name} klassifiziert als: {doc_type}")

    # 4) Decision Engine aufrufen (nur EINMAL)
    overall_decision = build_overall_decision(form_data, classified_pdfs)

    # Ergebnis + Dokumente im Session-State speichern,
    # damit sie nach einem erneuten Rendern erhalten bleiben
    st.session_state["decision"] = overall_decision
    st.session_state["classified_pdfs"] = classified_pdfs

# ---------------------------------------------------------
# Ergebnis-Anzeige + „Antrag absenden“-Button
# wird immer ausgeführt, aber nur angezeigt,
# wenn bereits eine Entscheidung im Session-State liegt
# ---------------------------------------------------------
decision = st.session_state["decision"]
classified_pdfs = st.session_state["classified_pdfs"]

if decision is not None:
    melde_ok = decision["meldezettel_ok"]
    rechnungen_ok = decision["rechnungen_ok"]
    all_ok = decision["all_ok"]

    invoice_decision = decision["invoice_decision"]
    monats_valid = invoice_decision.get("monatsrechnungen_valid", 0)

    st.subheader("Ergebnis")
    st.write(f"Meldezettel: {'OK' if melde_ok else 'NICHT OK'}")
    st.write(f"Rechnungen: {'OK' if rechnungen_ok else 'NICHT OK'}")
    st.write(f"Gesamtentscheidung: {'OK' if all_ok else 'NICHT OK'}")

    # Prüfen, ob überhaupt passende Dokumenttypen erkannt wurden
    has_melde = any(doc_type == "meldezettel" for _, doc_type in classified_pdfs)
    has_invoice_like = any(
        doc_type in ("jahresrechnung", "monatsrechnung", "zahlungsbestaetigung")
        for _, doc_type in classified_pdfs
    )

    if not has_melde:
        st.warning(
            "Es wurde kein Meldezettel erkannt. "
            "Bitte eine gültige Meldebestätigung hochladen."
        )

    if not has_invoice_like:
        st.warning(
            "Es wurde keine Klimaticket-Rechnung, Monatsrechnung "
            "oder Zahlungsbestätigung erkannt. "
            "Bitte ein entsprechendes Dokument hochladen."
        )

    # Inhaltlich ungültiger Meldezettel
    if has_melde and not melde_ok:
        st.error(
            "Der hochgeladene Meldezettel ist für diesen Antrag nicht gültig. "
            "Bitte Name, Geburtsdatum und förderberechtigte Adresse (PLZ) prüfen."
        )

    # Inhaltlich ungültige Rechnungen
    if has_invoice_like and not rechnungen_ok:
        if 0 < monats_valid < 3:
            st.error(
                f"Es wurden nur {monats_valid} gültige Monatsrechnungen erkannt. "
                "Für die Förderung müssen mindestens 3 Monatsrechnungen hochgeladen werden."
            )
        else:
            st.error(
                "Die hochgeladenen Rechnungsdokumente erfüllen die Förderkriterien noch nicht. "
                "Bitte Zeitraum, Ticket-Typ und Karteninhaber:in prüfen."
            )

    # Nur wenn ALLES OK ist, darf der Antrag abgesendet werden
    if all_ok:
        st.subheader("Antrag abschließen")
        send_clicked = st.button("Antrag absenden")
        if send_clicked:
            st.success("Antrag wurde (im Prototyp) erfolgreich abgesendet.")
    else:
        st.info(
            "Antrag kann derzeit nicht abgesendet werden. "
            "Bitte Hinweise oben beachten und Dokumente/Angaben korrigieren."
        )
