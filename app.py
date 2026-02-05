import os
from pathlib import Path
import tempfile

import streamlit as st


# Module aus deinem Projekt
from src.document_classifier import classify_document
from src.document_loader import extract_text_from_pdf
from src.decision_engine import (
    build_overall_decision,
    reclassify_short_jahresrechnungen,   # NEU: für kurze Jahresrechnungen
)
from src.registration_validation import is_postcode_foerderberechtigt

# Session-State initialisieren:
# - decision: speichert das Ergebnis der Decision Engine
# - classified_pdfs: speichert (Pfad, Dokumenttyp, Text, Konfidenz)-Tupel
if "decision" not in st.session_state:
    st.session_state["decision"] = None
if "classified_pdfs" not in st.session_state:
    st.session_state["classified_pdfs"] = []

# ---------------------------------------------------------
# Titel der Seite
# ---------------------------------------------------------
st.title("Prototyp KI-basierte Dokumentenprüfung: KlimaTicket")
st.markdown("Dieser Prototyp wurde im Rahmen meiner Bachelorarbeit erstellt und soll eine KI-basierte Dokumentenprüfung durchführen. Er dient der automatischen Prüfung der erforderlichen Beilagen: Rechnungen und Meldezettel.")

# ---------------------------------------------------------
# Abschnitt: Antragsdaten (Eingabeformular)
# ---------------------------------------------------------
st.header("Antragsdaten")

vorname = st.text_input("Vorname")
nachname = st.text_input("Nachname")
plz = st.text_input("PLZ")
geburtsdatum = st.text_input("Geburtsdatum (TT.MM.JJJJ)", placeholder="z.B. 01.01.1990")
ticket_typ = st.text_input("Ticket-Typ (z.B. Classic)")
gilt_von = st.text_input("Gültig von (TT.MM.JJJJ)", placeholder="z.B. 01.01.2025")
gilt_bis = st.text_input("Gültig bis (TT.MM.JJJJ)", placeholder="z.B. 31.12.2025")

# ---------------------------------------------------------
# Abschnitt: Datei-Upload (mehrere PDFs in einem Feld)
# ---------------------------------------------------------
st.header("Dokumente hochladen")

uploaded_files = st.file_uploader(
    "Meldezettel, Rechnung(en) und ggf. Zahlungsbestätigung (PDF) hochladen",
    type=["pdf"],
    accept_multiple_files=True,
)

# Hinweis direkt beim Ausfüllen anzeigen
if plz and not is_postcode_foerderberechtigt(plz):
    st.warning(
        "Nur Bürger:innen, die ihren Hauptwohnsitz in der Stadt Salzburg haben, "
        "sind für diese Förderung berechtigt."
    )

# ---------------------------------------------------------
# Button löst „Prüfen"-Logik aus
# ---------------------------------------------------------
if st.button("Prüfen"):
    with st.spinner("Dokumente werden geprüft ..."):
        # 1) Formulardaten aus den Eingabefeldern in ein dict packen
        form_data = {
            "antrags_id": "TEST-UI",
            "intern_id": "UI-0001",
            "familienname": nachname,
            "vorname": vorname,
            "strasse": "",
            "plz": plz,
            "geburtsdatum": geburtsdatum,
            "ticket_typ": ticket_typ,
            "gilt_von": gilt_von,
            "gilt_bis": gilt_bis,
        }

        st.subheader("Formulardaten (form_data)")
        st.json(form_data)

        # 2) Hochgeladene Dateien prüfen
        st.subheader("Hochgeladene Dateien")
        st.write("Anzahl:", len(uploaded_files))

        if not uploaded_files:
            st.warning(
                "Es wurden keine Dokumente hochgeladen. "
                "Bitte Meldebestätigung und Klimaticket-Rechnung(en) als PDF hochladen."
            )
            st.stop()

        # 3) Temporäres Verzeichnis anlegen
        temp_dir = tempfile.mkdtemp()
        st.write("TEMP-Verzeichnis:", temp_dir)

        paths: list[Path] = []

        for f in uploaded_files:
            save_path = Path(temp_dir) / f.name
            with open(save_path, "wb") as out:
                out.write(f.read())
            st.write("- gespeichert als:", str(save_path))
            paths.append(save_path)

        # 4) PDFs einlesen und klassifizieren
        #    FIX: classify_document() gibt (doc_type, confidence) zurück
        #    FIX: classified_pdfs muss 4-Tupel sein: (Pfad, Typ, Text, Konfidenz)
        classified_pdfs: list[tuple[Path, str, str, float]] = []

        for pdf_path in paths:
            text = extract_text_from_pdf(pdf_path)
            doc_type, confidence = classify_document(text)      # Tupel entpacken!
            classified_pdfs.append((pdf_path, doc_type, text, confidence))
            st.write(
                f"Dokument **{pdf_path.name}** klassifiziert als: "
                f"**{doc_type}** (Konfidenz: {confidence:.0%})"
            )

        # 5) Reklassifizierung: kurze Jahresrechnungen → Monatsrechnung
        classified_pdfs = reclassify_short_jahresrechnungen(classified_pdfs)

        # 6) Decision Engine aufrufen
        overall_decision = build_overall_decision(form_data, classified_pdfs)

        # 7) Ergebnis im Session-State ablegen
        st.session_state["decision"] = overall_decision
        st.session_state["classified_pdfs"] = classified_pdfs

    st.success("Prüfung abgeschlossen.")


# ---------------------------------------------------------
# Ergebnis-Anzeige + „Antrag absenden"-Button
# ---------------------------------------------------------
decision = st.session_state["decision"]
classified_pdfs = st.session_state["classified_pdfs"]

if decision is not None:
    melde_ok = decision["meldezettel_ok"]
    rechnungen_ok = decision["rechnungen_ok"]
    all_ok = decision["all_ok"]

    melde_decision = decision["melde_decision"]
    invoice_decision = decision["invoice_decision"]

    # ── Gesamtergebnis ──
    st.subheader("Ergebnis")
    if all_ok:
        st.success("✅ Alle Prüfungen bestanden — Antrag kann abgesendet werden.")
    else:
        st.error("❌ Antrag kann noch nicht abgesendet werden.")

    # ── Meldezettel-Status ──
    st.markdown("---")
    st.markdown("#### Meldezettel")

    if not melde_decision.get("meldezettel_found"):
        # ── Kein Meldezettel erkannt ──
        st.warning(
            "📄 **Kein Meldezettel erkannt.** "
            "Bitte eine gültige Meldebestätigung (Bestätigung der Meldung "
            "aus dem Zentralen Melderegister) als PDF hochladen."
        )
    elif melde_ok:
        # ── Alles OK ──
        st.success(
            f"✅ Meldezettel OK "
            f"({melde_decision.get('meldezettel_file', '')})"
        )
    else:
        # ── Meldezettel vorhanden aber Fehler ──
        details = melde_decision.get("details", {})
        checks = details.get("checks", {})
        extracted = details.get("extracted", {})

        fehler = []

        if not checks.get("vorname_ok"):
            m_vn = extracted.get("vorname_full") or "—"
            fehler.append(
                f"**Vorname** stimmt nicht überein "
                f"(Antrag: *{vorname}*, Meldezettel: *{m_vn}*)"
            )

        if not checks.get("nachname_ok"):
            m_nn = extracted.get("nachname") or "—"
            fehler.append(
                f"**Nachname** stimmt nicht überein "
                f"(Antrag: *{nachname}*, Meldezettel: *{m_nn}*)"
            )

        if not checks.get("geburtsdatum_ok"):
            m_gd = extracted.get("geburtsdatum_iso") or "—"
            fehler.append(
                f"**Geburtsdatum** stimmt nicht überein "
                f"(Antrag: *{geburtsdatum}*, Meldezettel: *{m_gd}*)"
            )

        if not checks.get("plz_ok"):
            m_plz = extracted.get("plz") or "—"
            if not checks.get("plz_ok_melde"):
                fehler.append(
                    f"**PLZ {m_plz}** aus dem Meldezettel ist nicht förderberechtigt. "
                    f"Nur Hauptwohnsitz in der Stadt Salzburg berechtigt zur Förderung."
                )
            elif not checks.get("plz_ok_form"):
                fehler.append(
                    f"**PLZ im Antrag** ({plz}) stimmt nicht mit dem "
                    f"Meldezettel überein (PLZ: {m_plz})."
                )

        # Fehlermeldungen anzeigen
        file_hint = melde_decision.get("meldezettel_file", "")
        st.error(f"❌ Meldezettel nicht gültig ({file_hint}):")
        for f in fehler:
            st.markdown(f"- {f}")

    # ── Rechnungs-Status ──
    st.markdown("---")
    st.markdown("#### Rechnungsnachweis")

    has_invoice_like = any(
        doc_type in ("jahresrechnung", "monatsrechnung", "zahlungsbestaetigung")
        for _, doc_type, _, _ in classified_pdfs
    )

    if not has_invoice_like:
        # ── Gar keine Rechnungsdokumente erkannt ──
        st.warning(
            "📄 **Kein Rechnungsdokument erkannt.** "
            "Bitte eine KlimaTicket-Jahresrechnung, Monatsrechnungen "
            "oder eine Zahlungsbestätigung als PDF hochladen."
        )
    elif rechnungen_ok:
        # ── Rechnungsnachweis OK — zeige welcher Weg erfolgreich war ──
        if invoice_decision.get("jahresrechnung_ok"):
            j_det = invoice_decision.get("jahresrechnung_details", {})
            j_file = j_det.get("_source_file", "")
            j_months = j_det.get("leist_months", "?")
            st.success(
                f"✅ Jahresrechnung OK ({j_file}, {j_months} Monate Leistungszeitraum)"
            )
        elif invoice_decision.get("zahlungsbestaetigung_ok"):
            z_det = invoice_decision.get("zahlungsbestaetigung_details", {})
            z_file = z_det.get("_source_file", "")
            st.success(f"✅ Zahlungsbestätigung OK ({z_file})")
        elif invoice_decision.get("monatsrechnungen_ok"):
            m_valid = invoice_decision.get("monatsrechnungen_valid", 0)
            st.success(f"✅ Monatsrechnungen OK ({m_valid} gültige Monate)")
    else:
        # ── Rechnungsdokumente vorhanden, aber nicht ausreichend ──

        # Jahresrechnung-Fehler
        if invoice_decision.get("jahresrechnung_found"):
            j_det = invoice_decision.get("jahresrechnung_details", {})
            j_file = j_det.get("_source_file", "")
            j_fehler = []

            if not j_det.get("name_ok"):
                j_fehler.append("Name (Karteninhaber:in) stimmt nicht mit dem Antrag überein")
            if not j_det.get("period_ok"):
                j_fehler.append("Gültigkeitszeitraum stimmt nicht mit dem Antrag überein")

            if j_fehler:
                st.error(f"❌ Jahresrechnung ({j_file}):")
                for f in j_fehler:
                    st.markdown(f"- {f}")

        # Zahlungsbestätigung-Fehler
        if invoice_decision.get("zahlungsbestaetigung_found"):
            z_det = invoice_decision.get("zahlungsbestaetigung_details", {})
            z_file = z_det.get("_source_file", "")
            z_fehler = []

            if not z_det.get("name_ok"):
                z_fehler.append("Name stimmt nicht mit dem Antrag überein")
            if not z_det.get("period_ok"):
                z_fehler.append("Zeitraum stimmt nicht mit dem Antrag überein")

            if z_fehler:
                st.error(f"❌ Zahlungsbestätigung ({z_file}):")
                for f in z_fehler:
                    st.markdown(f"- {f}")

        # Monatsrechnungen-Status
        monats_found = invoice_decision.get("monatsrechnungen_found", 0)
        monats_valid = invoice_decision.get("monatsrechnungen_valid", 0)

        if monats_found > 0:
            if monats_valid == 0:
                st.error(
                    f"❌ {monats_found} Monatsrechnung(en) erkannt, "
                    f"aber keine davon ist gültig. "
                    f"Bitte Karteninhaber:in und Gültigkeitszeitraum prüfen."
                )
            elif monats_valid < 3:
                st.warning(
                    f"⚠️ {monats_valid} von mindestens 3 benötigten "
                    f"Monatsrechnungen gültig. "
                    f"Bitte weitere Monatsrechnungen für unterschiedliche "
                    f"Monate hochladen."
                )

    # ── Antrag absenden ──
    st.markdown("---")
    if all_ok:
        st.subheader("Antrag abschließen")
        send_clicked = st.button("Antrag absenden")
        if send_clicked:
            st.success("🎉 Antrag wurde (im Prototyp) erfolgreich abgesendet.")
    else:
        st.info(
            "Antrag kann derzeit nicht abgesendet werden. "
            "Bitte die Hinweise oben beachten."
        )