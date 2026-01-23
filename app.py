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
# Wenn der Benutzer auf den Button klickt, startet die gesamte Prüf-Logik
if st.button("Prüfen"):
    # Spinner zeigt im UI: „Dokumente werden geprüft ...“ solange der Block läuft
    with st.spinner("Dokumente werden geprüft ..."):
        # 1) Formulardaten aus den Eingabefeldern in ein dict packen
        form_data = {
            "antrags_id": "TEST-UI",   # Platzhalter-ID für den Demo-Antrag
            "intern_id": "UI-0001",    # interne Referenz, hier nur Demo
            "familienname": nachname,  # Nachname aus Textfeld
            "vorname": vorname,        # Vorname aus Textfeld
            "strasse": "",             # aktuell noch nicht im UI abgefragt
            "plz": plz,                # PLZ aus Textfeld
            "geburtsdatum": geburtsdatum,  # Geburtsdatum im ISO-Format
            "ticket_typ": ticket_typ,      # z.B. „Classic“ aus Textfeld
            "gilt_von": gilt_von,          # Beginn des Ticket-Zeitraums
            "gilt_bis": gilt_bis,          # Ende des Ticket-Zeitraums
        }

        # Die gesammelten Formulardaten zur Kontrolle im UI anzeigen
        st.subheader("Formulardaten (form_data)")
        st.json(form_data)

        # 2) Hochgeladene Dateien anzeigen und sicherstellen, dass überhaupt welche da sind
        st.subheader("Hochgeladene Dateien")
        st.write("Anzahl:", len(uploaded_files))

        # Wenn keine Dateien hochgeladen wurden, Hinweis anzeigen und Verarbeitung abbrechen
        if not uploaded_files:
            st.warning(
                "Es wurden keine Dokumente hochgeladen. "
                "Bitte Meldebestätigung und Klimaticket-Rechnung(en) als PDF hochladen."
            )
            # bricht nur den Button-Handler ab, nicht die ganze App
            st.stop()

        # 3) Temporäres Verzeichnis anlegen, in dem alle Uploads für diese Sitzung gespeichert werden
        temp_dir = tempfile.mkdtemp()
        st.write("TEMP-Verzeichnis:", temp_dir)

        # Liste, in der die Pfade zu allen gespeicherten PDFs gesammelt werden
        paths: list[Path] = []

        # Jede hochgeladene Datei aus dem Streamlit-Upload-Objekt auf die Festplatte schreiben
        for f in uploaded_files:
            save_path = Path(temp_dir) / f.name

            # Binären Inhalt des Uploads in die Datei schreiben
            with open(save_path, "wb") as out:
                out.write(f.read())

            st.write("- gespeichert als:", str(save_path))
            paths.append(save_path)

        # 4) Alle gespeicherten PDFs einlesen und klassifizieren
        classified_pdfs: list[tuple[Path, str]] = []
        for pdf_path in paths:
            # Text aus dem PDF holen (inkl. OCR, falls nötig)
            text = extract_text_from_pdf(pdf_path)
            # Dokumenttyp (meldezettel, jahresrechnung, ...) per ML-Modell bestimmen
            doc_type = classify_document(text)
            # Pfad + erkannter Typ speichern
            classified_pdfs.append((pdf_path, doc_type))
            st.write(f"Dokument {pdf_path.name} klassifiziert als: {doc_type}")

        # 5) Decision Engine aufrufen, die aus form_data + klassifizierten PDFs
        #    die Gesamtförderentscheidung berechnet
        overall_decision = build_overall_decision(form_data, classified_pdfs)

        # 6) Ergebnis im Session-State ablegen, damit es auch nach einem Re-Render
        #    noch zur Verfügung steht
        st.session_state["decision"] = overall_decision
        st.session_state["classified_pdfs"] = classified_pdfs

    # Dieser Code läuft NACH dem Spinner-Block, wenn alles fertig verarbeitet ist
    st.success("Prüfung abgeschlossen.")


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
