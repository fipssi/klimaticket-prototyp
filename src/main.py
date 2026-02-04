"""
main.py — Batch-Auswertung aller KlimaTicket-Förderanträge + Excel-Report
==========================================================================

ÜBERBLICK
---------
Dieses Skript ist der EINSTIEGSPUNKT für die automatisierte Verarbeitung
aller Förderanträge. Es liest alle Cases (Antragsdaten + PDF-Dokumente),
validiert sie vollständig und schreibt das Ergebnis als Excel-Report.

    Aufruf:   python main.py
    Eingabe:  data/cases/<monat>/<case_id>/antrag.json + *.pdf
    Ausgabe:  case_report.xlsx (eine Zeile pro Case)


PIPELINE PRO CASE
-----------------
Jeder Case-Ordner durchläuft diese Schritte:

    ┌─────────────────────────────────────────────────────────────────┐
    │ 1. antrag.json laden                                           │
    │    → form_data: {vorname, familienname, geburtsdatum, plz,     │
    │                  gilt_von, gilt_bis, ...}                      │
    ├─────────────────────────────────────────────────────────────────┤
    │ 2. classify_case_pdfs(case_dir)                                │
    │    → Alle PDFs klassifizieren: [(pfad, typ, text, konfidenz)]  │
    │    → Intern: document_loader → document_classifier             │
    ├─────────────────────────────────────────────────────────────────┤
    │ 3. reclassify_short_jahresrechnungen(classified_pdfs)          │
    │    → Jahresrechnungen mit < 10 Monaten Leistungszeitraum       │
    │      werden als Monatsrechnungen reklassifiziert                │
    ├─────────────────────────────────────────────────────────────────┤
    │ 4. build_overall_decision(form_data, final_pdfs)               │
    │    → Gesamtentscheidung: {all_ok, meldezettel_ok, rechnungen_ok}│
    │    → Intern: registration_validation + invoice_validation       │
    ├─────────────────────────────────────────────────────────────────┤
    │ 5. build_error_reason(overall_decision)                        │
    │    → Detaillierte, benutzerfreundliche Fehlertexte              │
    ├─────────────────────────────────────────────────────────────────┤
    │ 6. Excel-Zeile aufbauen und an rows[] anhängen                 │
    └─────────────────────────────────────────────────────────────────┘

    Am Ende: pandas DataFrame → case_report.xlsx


ORDNERSTRUKTUR
--------------
    projekt/
    ├── src/                     ← Python-Module
    │   ├── main.py              ← DIESES SKRIPT
    │   ├── document_loader.py   ← PDF → Text (OCR + Textlayer)
    │   ├── document_classifier.py ← Text → Dokumenttyp
    │   ├── invoice_validation.py  ← Rechnungs-Validierung
    │   ├── registration_validation.py ← Meldezettel-Validierung
    │   ├── decision_engine.py   ← Gesamtentscheidung
    │   └── utils.py             ← Gemeinsame Hilfsfunktionen
    ├── data/
    │   └── cases/
    │       ├── 2024-09/         ← Monats-Ordner
    │       │   ├── 12345/       ← Case-Ordner (case_id)
    │       │   │   ├── antrag.json
    │       │   │   ├── 11612.pdf   (z.B. Jahresrechnung)
    │       │   │   ├── 11413.pdf   (z.B. Zahlungsbestätigung)
    │       │   │   └── 11987.pdf   (z.B. Meldezettel)
    │       │   └── 12346/
    │       │       └── ...
    │       └── 2024-10/
    │           └── ...
    └── case_report.xlsx         ← AUSGABE (neben data/)


EXCEL-REPORT: SPALTEN-ÜBERSICHT
--------------------------------
Der Report enthält pro Case eine Zeile mit diesen Spalten:

    ┌───────────────────────────┬────────────────────────────────────────────┐
    │ Spalte                    │ Inhalt / Herkunft                          │
    ├───────────────────────────┼────────────────────────────────────────────┤
    │ run_id                    │ Laufende Nummer (1, 2, 3, ...)             │
    │ laufende_nr               │ Interne Antragsnummer (aus antrag.json)    │
    │ intern_id                 │ Interne ID (aus antrag.json)               │
    │ familienname / vorname    │ Antragsdaten                               │
    │ geschlecht / geburtsdatum │ Antragsdaten                               │
    │ strasse / plz             │ Antragsdaten                               │
    │ gilt_von / gilt_bis       │ Gültigkeitszeitraum KlimaTicket            │
    ├───────────────────────────┼────────────────────────────────────────────┤
    │ meldezettel_ok            │ True/False: Meldezettel-Validierung OK?     │
    │ meldezettel_konfidenz     │ ML-Konfidenz der Klassifizierung (0-1)     │
    │ meldezettel_datei         │ Dateiname des erkannten Meldezettels       │
    │ fehler_meldezettel        │ Detaillierter Fehlertext (oder "")         │
    ├───────────────────────────┼────────────────────────────────────────────┤
    │ rechnungen_ok             │ True/False: Rechnungsnachweis gesamt OK?    │
    │ jahresrechnung_ok         │ True/False: Jahresrechnung validiert?       │
    │ zahlungsbestätigung_ok    │ True/False: Zahlungsbestätigung validiert?  │
    │ monatsrechnungen_gültig   │ Anzahl gültiger Monatsrechnungs-Monate    │
    │ fehler_rechnung           │ Detaillierter Fehlertext (oder "")         │
    ├───────────────────────────┼────────────────────────────────────────────┤
    │ fehler_antrag             │ Fehlende Pflichtfelder im Antrag           │
    │ all_ok                    │ True/False: GESAMTERGEBNIS                 │
    │ fehlergrund               │ Alle Fehler zusammengefasst                │
    │ dok_klassifizierung       │ PDF-Klassifizierung je Datei               │
    │ case_id / monat_ordner    │ Identifikation des Cases                   │
    └───────────────────────────┴────────────────────────────────────────────┘


FEHLERBEHANDLUNG
----------------
    1. Einzelne Cases:
       Wenn ein Case fehlschlägt (korrupte PDF, etc.), wird eine
       Fehlerzeile ins Excel geschrieben. Der Batch läuft WEITER.

    2. Excel-Schreibfehler:
       Wenn case_report.xlsx in Excel geöffnet ist (PermissionError),
       wird ein Fallback mit Zeitstempel geschrieben.

    3. Zusammenfassung:
       Am Ende zeigt die Konsole: X OK, Y abgelehnt, Z Fehler.


ABHÄNGIGKEITEN
--------------
    document_classifier.py  → classify_case_pdfs()
    decision_engine.py      → build_overall_decision(), reclassify_short_jahresrechnungen()
    pandas                  → DataFrame, to_excel()
"""

import json
from pathlib import Path

import pandas as pd

# ── Importe aus dem Projekt ──

# classify_case_pdfs(case_dir) liest ALLE PDFs in einem Case-Ordner ein,
# extrahiert den Text (via document_loader.py) und klassifiziert den
# Dokumenttyp (via document_classifier.py).
#
# Rückgabe: Liste von Tupeln:
#   [(pdf_path, doc_type, text, confidence), ...]
#
# doc_type ist einer von:
#   "jahresrechnung"        → Jahresrechnung KlimaTicket
#   "monatsrechnung"        → Monatsrechnung KlimaTicket
#   "zahlungsbestaetigung"  → Zahlungsbestätigung KlimaTicket
#   "meldezettel"           → Meldebestätigung / Meldezettel
#   "unbekannt"             → Nicht zuordenbar
from document_classifier import classify_case_pdfs

# build_overall_decision(form_data, classified_pdfs)
#   Orchestriert ALLE Validierungen (Meldezettel + Rechnungen) und
#   gibt die Gesamtentscheidung als Dict zurück.
#
# reclassify_short_jahresrechnungen(classified_pdfs)
#   Prüft Jahresrechnungen: Wenn der Leistungszeitraum < 10 Monate
#   beträgt, wird der doc_type von "jahresrechnung" auf
#   "monatsrechnung" geändert. Der ML-Classifier erkennt das Layout
#   korrekt als Jahresrechnung, aber inhaltlich ist es eine monatliche
#   Abrechnung. Dieses Skript korrigiert die Klassifizierung.
from decision_engine import build_overall_decision, reclassify_short_jahresrechnungen


# =============================================================================
# PFAD-KONFIGURATION
# =============================================================================
#
# Pfadberechnung relativ zum Skriptstandort:
#
#   __file__             = /projekt/src/main.py
#   .resolve()           = Absoluter Pfad (Symlinks aufgelöst)
#   .parent              = /projekt/src/
#   .parent.parent       = /projekt/           ← BASE_DIR
#
#   CASES_ROOT           = /projekt/data/cases/
#
# Die Ordnerstruktur ist fest vorgegeben:
#   data/cases/<monat>/<case_id>/
#   Beispiel: data/cases/2024-09/12345/antrag.json

BASE_DIR = Path(__file__).resolve().parent.parent
CASES_ROOT = BASE_DIR / "data" / "cases"    # Erwartet: cases/<monat>/<case_id>


# =============================================================================
# 1) CASE-DATEN LADEN
# =============================================================================

def load_case_json(case_dir: Path) -> dict | None:
    """
    Lädt die Antragsdaten (antrag.json) aus einem Case-Ordner.

    Die antrag.json enthält alle vom Antragsteller eingegebenen Daten:

        {
            "vorname":       "Max",
            "familienname":  "Mustermann",
            "geburtsdatum":  "01.01.1990",
            "plz":           "5020",
            "gilt_von":      "2024-09-15",
            "gilt_bis":      "2025-09-14",
            "laufende_nr":   "KT-2024-001",
            "intern_id":     "12345",
            "geschlecht":    "männlich",
            "strasse":       "Musterstraße 1",
            ...
        }

    Diese Daten werden anschließend mit den Inhalten der PDF-Dokumente
    (Meldezettel, Rechnungen) abgeglichen.

    Parameter:
        case_dir: Pfad zum Case-Ordner (z.B. data/cases/2024-09/12345/)

    Rückgabe:
        dict mit Antragsdaten, oder None wenn antrag.json fehlt.

    Hinweis:
        Cases ohne antrag.json werden ÜBERSPRUNGEN (mit Konsolenwarnung).
        Das kann passieren wenn:
        - Der Ordner manuell erstellt wurde
        - Die JSON-Datei noch nicht aus dem Fördersystem exportiert wurde
        - Der Ordner nur PDFs enthält (z.B. Testdaten)
    """
    antrag_path = case_dir / "antrag.json"
    if not antrag_path.exists():
        print(f"Überspringe {case_dir}: antrag.json fehlt")
        return None
    with open(antrag_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# 2) FEHLERTEXT-GENERIERUNG
# =============================================================================
#
# Die Fehlertexte sind das "Gesicht" des Systems für den Sachbearbeiter.
# Sie erscheinen in drei Excel-Spalten und müssen so formuliert sein,
# dass der Sachbearbeiter sofort versteht, WAS das Problem ist und
# WO es liegt, OHNE die PDF-Datei öffnen zu müssen.
#
# Drei Fehler-Spalten im Excel:
#   fehler_meldezettel  → Nur Meldezettel-Probleme
#   fehler_rechnung     → Nur Rechnungs-Probleme
#   fehlergrund         → ALLES zusammen (Melde + Rechnung + Antrag)
#
# Design-Prinzip: Fehler werden so KONKRET wie möglich formuliert.
# Statt: "Vorname stimmt nicht"
# Besser: "Vorname stimmt nicht (Meldezettel: 'Max Michael')"
# → Der Sachbearbeiter sieht sofort, was das System aus dem PDF gelesen hat.


def _build_melde_errors(melde_dec: dict) -> str:
    """
    Erzeugt eine detaillierte Fehlerbeschreibung für den Meldezettel-Teil.

    Analysiert die Ergebnisstruktur aus der Decision Engine:

        melde_dec = {
            "meldezettel_found": True/False,
            "meldezettel_ok":    True/False,
            "meldezettel_file":  "11987.pdf",
            "meldezettel_confidence": 0.87,
            "details": {                           ← von validate_meldezettel()
                "checks": {
                    "vorname_ok": True/False,
                    "nachname_ok": True/False,
                    "geburtsdatum_ok": True/False,
                    "plz_ok": True/False,
                    "plz_ok_melde": True/False,    ← PLZ förderberechtigt?
                    "plz_ok_form": True/False,     ← PLZ Antrag == Meldezettel?
                },
                "extracted": {
                    "vorname_full": "Max Michael",
                    "nachname": "Mustermann",
                    "geburtsdatum_iso": "1990-01-01",
                    "plz": "5020",
                },
            }
        }

    Drei Fälle:
        1. Kein Meldezettel gefunden        → "Meldezettel fehlt"
        2. Meldezettel vorhanden und OK     → "" (leerer String)
        3. Meldezettel vorhanden, aber Fehler → Detaillierte Problembeschreibung

    PLZ-Fehler werden DIFFERENZIERT:
        - "PLZ nicht förderberechtigt (Meldezettel: 4020)"
          → Person wohnt nicht in Salzburg (z.B. Linz)
        - "PLZ Antrag ≠ Meldezettel (Meldezettel: 5020)"
          → Person wohnt in Salzburg, hat aber falsche PLZ im Antrag eingetragen

    Parameter:
        melde_dec: Dict aus overall_decision["melde_decision"]

    Rückgabe:
        Fehlertext-String, oder "" wenn alles OK.
    """
    # ── Fall 1: Kein Meldezettel unter den PDFs gefunden ──
    # Keines der hochgeladenen PDFs wurde als Meldezettel klassifiziert.
    if not melde_dec.get("meldezettel_found", False):
        return "Meldezettel fehlt"

    # ── Fall 2: Meldezettel vorhanden und alle Checks bestanden ──
    if melde_dec.get("meldezettel_ok", False):
        return ""

    # ── Fall 3: Meldezettel vorhanden, aber mindestens ein Check fehlgeschlagen ──

    # Details aus der Validierungsfunktion extrahieren
    details = melde_dec.get("details", {})
    checks = details.get("checks", {})        # Boolean-Ergebnisse je Feld
    extracted = details.get("extracted", {})   # Aus dem Meldezettel extrahierte Werte

    problems = []

    # ── Vorname ──
    # Zeigt den Meldezettel-Vornamen in der Fehlermeldung an,
    # damit der Sachbearbeiter sofort sieht, was das System gelesen hat.
    if not checks.get("vorname_ok", False):
        melde_vorname = extracted.get("vorname_full") or "nicht erkannt"
        problems.append(f"Vorname stimmt nicht (Meldezettel: '{melde_vorname}')")

    # ── Nachname ──
    if not checks.get("nachname_ok", False):
        melde_nachname = extracted.get("nachname") or "nicht erkannt"
        problems.append(f"Nachname stimmt nicht (Meldezettel: '{melde_nachname}')")

    # ── Geburtsdatum ──
    if not checks.get("geburtsdatum_ok", False):
        melde_geb = extracted.get("geburtsdatum_iso") or "nicht erkannt"
        problems.append(f"Geburtsdatum stimmt nicht (Meldezettel: {melde_geb})")

    # ── PLZ (differenziert) ──
    # Zwei verschiedene Fehlerursachen möglich:
    #   a) PLZ gehört nicht zur Stadt Salzburg → nicht förderberechtigt
    #   b) PLZ im Antrag stimmt nicht mit Meldezettel überein
    # Der Sachbearbeiter braucht unterschiedliche Hinweise je nach Fall.
    if not checks.get("plz_ok", False):
        melde_plz = extracted.get("plz") or "nicht erkannt"
        if not checks.get("plz_ok_melde", False):
            # PLZ ist NICHT förderberechtigt (z.B. 4020 = Linz)
            # → Person wohnt außerhalb des Salzburger Stadtgebiets
            problems.append(f"PLZ nicht förderberechtigt (Meldezettel: {melde_plz})")
        elif not checks.get("plz_ok_form", False):
            # PLZ IST förderberechtigt, aber Antrag hat eine ANDERE PLZ
            # → Antragsteller hat sich bei der PLZ vertippt
            problems.append(f"PLZ Antrag ≠ Meldezettel (Meldezettel: {melde_plz})")

    # Alle Probleme mit Semikolon verbinden
    # Beispiel: "Vorname stimmt nicht (...); PLZ nicht förderberechtigt (...)"
    return "; ".join(problems) if problems else "Meldezettel ungültig (unbekannt)"


def _build_invoice_errors(inv_dec: dict) -> str:
    """
    Erzeugt eine detaillierte Fehlerbeschreibung für die Rechnungsseite.

    Die Rechnungsvalidierung ist KOMPLEX, weil drei verschiedene
    Dokumenttypen zusammenspielen:

        1. JAHRESRECHNUNG + ZAHLUNGSBESTÄTIGUNG (Hauptweg)
           Beide müssen vorhanden sein UND validieren:
           - Jahresrechnung:  Name + Gültigkeitszeitraum + Leistungszeitraum
           - Zahlungsbestätigung: Name + Gültigkeitszeitraum

        2. MONATSRECHNUNGEN (Alternativweg)
           Mindestens 3 gültige Monatsrechnungen für VERSCHIEDENE Monate.
           Jede einzelne muss: Name + Gültigkeit + Leistung innerhalb Gültigkeit.

        Die Decision Engine akzeptiert EINEN der beiden Wege.

    Fehlertext-Struktur:
        Für JEDEN gefundenen (aber fehlgeschlagenen) Typ wird eine
        separate Zeile erzeugt:
            "Jahresrechnung: Name stimmt nicht, Gültigkeitszeitraum stimmt nicht"
            "Zahlungsbestätigung: Zeitraum stimmt nicht"

        Für NICHT gefundene Typen:
            "Keine Jahresrechnung vorhanden"
            "Keine Zahlungsbestätigung vorhanden"

        Monatsrechnungen mit Statistik:
            "Monatsrechnungen: 5 gefunden, 2 gültige Monate (mind. 3 nötig)"

    Parameter:
        inv_dec: Dict aus overall_decision["invoice_decision"]

    Rückgabe:
        Fehlertext-String, oder "" wenn Rechnungen OK.
    """
    # Alles OK → kein Fehlertext nötig
    if inv_dec.get("rechnungen_ok", False):
        return ""

    problems = []

    # ══════════════════════════════════════════════════
    # JAHRESRECHNUNG
    # ══════════════════════════════════════════════════
    if inv_dec.get("jahresrechnung_found", False):
        # Jahresrechnung wurde gefunden, aber Validierung fehlgeschlagen
        j = inv_dec.get("jahresrechnung_details") or {}
        j_issues = []

        if j.get("reason"):
            # Direkter Fehlergrund von der Validierungsfunktion
            # z.B. "gilt_von/gilt_bis fehlen im Antrag"
            j_issues.append(j["reason"])
        else:
            # Einzelne Checks analysieren
            if not j.get("name_ok", False):
                j_issues.append("Name stimmt nicht")
            if not j.get("period_ok", False):
                j_issues.append("Gültigkeitszeitraum stimmt nicht")

            # Leistungszeitraum analysieren
            leist = j.get("leist_months")
            if leist is not None and leist < 10:
                # Rechnung deckt weniger als 10 Monate ab → eigentlich eine
                # Monatsrechnung. Wird normalerweise von reclassify_short_
                # jahresrechnungen() abgefangen, aber falls es doch hier
                # ankommt, zeigen wir es an.
                j_issues.append(f"Leistungszeitraum nur {leist} Monate (< 10)")
            elif leist is None:
                # Leistungszeitraum konnte nicht aus der PDF extrahiert werden
                j_issues.append("Leistungszeitraum nicht erkannt")

        if j_issues:
            problems.append("Jahresrechnung: " + ", ".join(j_issues))
    else:
        # Keine PDF wurde als Jahresrechnung klassifiziert
        problems.append("Keine Jahresrechnung vorhanden")

    # ══════════════════════════════════════════════════
    # ZAHLUNGSBESTÄTIGUNG
    # ══════════════════════════════════════════════════
    if inv_dec.get("zahlungsbestaetigung_found", False):
        # Zahlungsbestätigung gefunden, aber Validierung fehlgeschlagen
        z = inv_dec.get("zahlungsbestaetigung_details") or {}
        z_issues = []

        if z.get("reason"):
            z_issues.append(z["reason"])
        else:
            if not z.get("name_ok", False):
                z_issues.append("Name stimmt nicht")
            if not z.get("period_ok", False):
                z_issues.append("Zeitraum stimmt nicht")

        if z_issues:
            problems.append("Zahlungsbestätigung: " + ", ".join(z_issues))
    else:
        problems.append("Keine Zahlungsbestätigung vorhanden")

    # ══════════════════════════════════════════════════
    # MONATSRECHNUNGEN
    # ══════════════════════════════════════════════════
    # Monatsrechnungen sind eine ALTERNATIVE zum Hauptweg
    # (Jahresrechnung + Zahlungsbestätigung).
    # Mindestens 3 verschiedene Monate müssen abgedeckt sein.
    #
    # monats_found: Anzahl der als "monatsrechnung" klassifizierten PDFs
    #               (inkl. reklassifizierter Jahresrechnungen)
    # monats_valid: Anzahl VERSCHIEDENER gültiger Monate
    #               (z.B. 3 PDFs für Sept/Okt/Nov → monats_valid = 3)
    monats_found = inv_dec.get("monatsrechnungen_found", 0)
    monats_valid = inv_dec.get("monatsrechnungen_valid", 0)
    if monats_found > 0 or monats_valid > 0:
        problems.append(
            f"Monatsrechnungen: {monats_found} gefunden, "
            f"{monats_valid} gültige Monate (mind. 3 nötig)"
        )
    else:
        problems.append("Keine Monatsrechnungen vorhanden")

    return "; ".join(problems) if problems else "Rechnungen ungültig (unbekannt)"


def build_error_reason(overall_decision: dict) -> tuple[str, str, str]:
    """
    Erzeugt die drei Fehlertext-Spalten für den Excel-Report.

    Diese Funktion ist der EINZIGE Aufrufer von _build_melde_errors()
    und _build_invoice_errors(). Sie kombiniert beide Ergebnisse.

    Parameter:
        overall_decision: Ergebnis-Dict von build_overall_decision()

    Rückgabe:
        Tupel (fehler_meldezettel, fehler_rechnung, fehlergrund):

        fehler_meldezettel:
            Nur Meldezettel-Probleme.
            Beispiel: "Vorname stimmt nicht (Meldezettel: 'Max')"

        fehler_rechnung:
            Nur Rechnungs-Probleme.
            Beispiel: "Keine Jahresrechnung vorhanden; Zahlungsbestätigung: ..."

        fehlergrund:
            ALLES zusammen, getrennt durch " | ".
            Beispiel: "Vorname stimmt nicht (...) | Keine Jahresrechnung ..."

    Bei all_ok = True sind alle drei Strings leer ("").
    """
    melde_dec = overall_decision.get("melde_decision", {})
    inv_dec = overall_decision.get("invoice_decision", {})

    # Einzelne Fehlertexte erzeugen
    fehler_melde = _build_melde_errors(melde_dec)
    fehler_rechnung = _build_invoice_errors(inv_dec)

    # Gesamtfehlergrund: beide Teile mit " | " verbinden
    # (nur nicht-leere Teile aufnehmen)
    parts = []
    if fehler_melde:
        parts.append(fehler_melde)
    if fehler_rechnung:
        parts.append(fehler_rechnung)

    fehlergrund = " | ".join(parts) if parts else ""

    return fehler_melde, fehler_rechnung, fehlergrund


# =============================================================================
# 3) KLASSIFIZIERUNGS-ZUSAMMENFASSUNG FÜR EXCEL
# =============================================================================
#
# Die Spalte "dok_klassifizierung" im Excel zeigt dem Sachbearbeiter,
# wie jede PDF-Datei klassifiziert wurde. Das ist wichtig für die
# Nachvollziehbarkeit: Wenn ein Case abgelehnt wird, kann der
# Sachbearbeiter sehen, ob die Klassifizierung korrekt war.
#
# Beispiel:
#   "11612.pdf → jahresrechnung (95%); 11413.pdf → monatsrechnung (umkl. von jahresrechnung, 88%)"
#
# "umkl." = umklassifiziert: Eine als "jahresrechnung" erkannte PDF wurde
# von reclassify_short_jahresrechnungen() zur "monatsrechnung" geändert,
# weil ihr Leistungszeitraum < 10 Monate beträgt.

def _build_klassifizierung_summary(
    original_pdfs: list,
    final_pdfs: list,
) -> str:
    """
    Baut eine kompakte Zusammenfassung der Dokumentklassifizierung.

    Vergleicht die ORIGINALE Klassifizierung (vom ML-Classifier) mit der
    FINALEN (nach Reklassifizierung durch die Decision Engine) und markiert
    Änderungen explizit.

    Parameter:
        original_pdfs: Liste von (path, doc_type, text, confidence)
                       VOR Reklassifizierung
        final_pdfs:    Liste von (path, doc_type, text, confidence)
                       NACH Reklassifizierung

    Rückgabe:
        Zusammenfassungs-String für die Excel-Spalte.

    Beispiele:
        Ohne Reklassifizierung:
            "11612.pdf → jahresrechnung (95%); 11987.pdf → meldezettel (87%)"

        Mit Reklassifizierung:
            "11413.pdf → monatsrechnung (umkl. von jahresrechnung, 88%)"
            ↑ PDF wurde vom Classifier als Jahresrechnung erkannt (88%),
              aber wegen kurzem Leistungszeitraum als Monatsrechnung behandelt.
    """
    # Lookup-Dict: Dateiname → originaler doc_type
    # Damit wir für jede finale PDF prüfen können, ob sich der Typ geändert hat.
    orig_types = {path.name: doc_type for path, doc_type, _text, _conf in original_pdfs}

    parts = []
    for pdf_path, doc_type, _text, confidence in final_pdfs:
        name = pdf_path.name
        orig_type = orig_types.get(name, doc_type)

        if orig_type != doc_type:
            # ── Reklassifizierung stattgefunden ──
            # Zeige BEIDE Typen: neuen Typ + "umkl. von" + alten Typ
            # Beispiel: "11413.pdf → monatsrechnung (umkl. von jahresrechnung, 88%)"
            parts.append(f"{name} → {doc_type} (umkl. von {orig_type}, {confidence:.0%})")
        else:
            # ── Keine Änderung ──
            # Nur Typ + Konfidenz
            # Beispiel: "11612.pdf → jahresrechnung (95%)"
            parts.append(f"{name} → {doc_type} ({confidence:.0%})")

    return "; ".join(parts)


# =============================================================================
# 4) HAUPTFUNKTION: BATCH-VERARBEITUNG
# =============================================================================
#
# Die main()-Funktion ist der Batch-Prozessor. Sie:
#   1. Iteriert über ALLE Case-Ordner
#   2. Verarbeitet jeden einzeln (Laden → Klassifizieren → Validieren)
#   3. Sammelt die Ergebnisse als Zeilen
#   4. Schreibt am Ende EINE Excel-Datei
#
# FEHLERTOLERANZ:
#   Einzelne Cases können fehlschlagen (korrupte PDF, fehlende Abhängigkeit,
#   etc.) — der Batch wird NICHT abgebrochen. Stattdessen wird eine
#   Fehlerzeile ins Excel geschrieben (all_ok = False, fehlergrund = Fehlermeldung).
#   So gehen die Ergebnisse der anderen Cases nicht verloren.

def main():
    """
    Verarbeitet alle Cases in CASES_ROOT und schreibt den Excel-Report.

    Ablauf:
        1. Alle Monats-Ordner durchlaufen (sortiert: 2024-09, 2024-10, ...)
        2. Innerhalb jedes Monats alle Case-Ordner durchlaufen (sortiert)
        3. Pro Case: antrag.json → PDFs klassifizieren → validieren → Zeile bauen
        4. Am Ende: pandas DataFrame → case_report.xlsx

    Konsolenausgabe:
        - Pro Case: DEBUG-Zeile mit Case-ID und Gültigkeitszeitraum
        - Bei Fehler: ⚠ VERARBEITUNGSFEHLER: <Exception>
        - Am Ende: Zusammenfassung (X OK, Y abgelehnt, Z Fehler)

    Ausgabe-Dateien:
        - case_report.xlsx (Standardpfad: direkt im Projektverzeichnis)
        - case_report_<YYYYMMDD_HHMMSS>.xlsx (Fallback bei gesperrter Datei)
    """

    rows = []           # Gesammelte Zeilen für das Excel (eine pro Case)
    run_id = 1          # Laufende Nummer (wird für jede verarbeitete Case hochgezählt)
    fehler_count = 0    # Zähler für Cases mit Verarbeitungsfehlern (Exception)

    # ══════════════════════════════════════════════════════════════════
    # ITERATION ÜBER ALLE CASES
    # ══════════════════════════════════════════════════════════════════
    #
    # Doppelt verschachtelte Schleife:
    #   Ebene 1: Monats-Ordner ("2024-09", "2024-10", ...)
    #   Ebene 2: Case-Ordner ("12345", "12346", ...)
    #
    # sorted() stellt sicher, dass die Reihenfolge im Excel KONSISTENT ist
    # (alphabetisch/numerisch), unabhängig von der Dateisystem-Reihenfolge.

    for month_dir in sorted(CASES_ROOT.iterdir()):
        if not month_dir.is_dir():
            continue    # Dateien auf Monats-Ebene ignorieren (z.B. .DS_Store)

        for case_dir in sorted(month_dir.iterdir()):
            if not case_dir.is_dir():
                continue    # Dateien auf Case-Ebene ignorieren

            case_id = case_dir.name     # z.B. "12345"

            # ── Schritt 1: Antragsdaten laden ──
            form_data = load_case_json(case_dir)
            if form_data is None:
                continue    # Case ohne antrag.json überspringen

            # Debug-Ausgabe: Welcher Case wird gerade verarbeitet?
            # Zeigt insbesondere, ob gilt_von/gilt_bis vorhanden sind
            # (fehlende Datumsfelder sind eine häufige Fehlerquelle).
            print(
                "DEBUG case:",
                month_dir.name,
                case_id,
                "gilt_von:", repr(form_data.get("gilt_von")),
                "gilt_bis:", repr(form_data.get("gilt_bis")),
            )

            try:
                # ══════════════════════════════════════════════
                # SCHRITT 2: PDFs KLASSIFIZIEREN
                # ══════════════════════════════════════════════
                # classify_case_pdfs() macht Folgendes:
                #   a) Alle *.pdf im Case-Ordner finden
                #   b) Für jede PDF: Text extrahieren (document_loader)
                #   c) Text klassifizieren (document_classifier)
                #
                # Rückgabe: [(Path, doc_type, text, confidence), ...]
                # Beispiel: [(Path(".../11612.pdf"), "jahresrechnung", "...", 0.95)]
                classified_pdfs = classify_case_pdfs(case_dir)

                # ══════════════════════════════════════════════
                # SCHRITT 3: REKLASSIFIZIERUNG
                # ══════════════════════════════════════════════
                # Problem: Der ML-Classifier erkennt das LAYOUT einer PDF.
                # Eine Jahresrechnung und eine Monatsrechnung haben exakt
                # das gleiche Layout (Karteninhaber, Gültigkeitszeitraum,
                # Leistungszeitraum, Betrag). Der Unterschied ist nur die
                # DAUER des Leistungszeitraums:
                #   Jahresrechnung:    12 Monate
                #   Monatsrechnung:    1 Monat
                #
                # Der Classifier kann das nicht unterscheiden → er
                # klassifiziert BEIDE als "jahresrechnung".
                #
                # reclassify_short_jahresrechnungen() korrigiert das:
                #   1. Für jede "jahresrechnung": Leistungszeitraum extrahieren
                #   2. Wenn < 10 Monate → doc_type ändern: "monatsrechnung"
                #   3. Sonst: doc_type bleibt "jahresrechnung"
                #
                # Warum 10 Monate als Schwelle?
                #   Normale Jahresrechnungen haben 12 Monate. Teiljahres-
                #   rechnungen (z.B. 10 Monate) sollen noch als Jahres-
                #   rechnung gelten. Nur kurze Rechnungen (< 10 Monate)
                #   werden als Monatsrechnungen behandelt.
                final_pdfs = reclassify_short_jahresrechnungen(classified_pdfs)

                # ══════════════════════════════════════════════
                # SCHRITT 4: KLASSIFIZIERUNGS-ZUSAMMENFASSUNG
                # ══════════════════════════════════════════════
                # Kompakter Text für die Excel-Spalte "dok_klassifizierung".
                # Zeigt pro PDF: Dateiname → Typ (Konfidenz)
                # Bei Reklassifizierung: Typ (umkl. von <alter Typ>, Konfidenz)
                dok_klassifizierung = _build_klassifizierung_summary(classified_pdfs, final_pdfs)

                # ══════════════════════════════════════════════
                # SCHRITT 5: GESAMTENTSCHEIDUNG
                # ══════════════════════════════════════════════
                # build_overall_decision() ist der ZENTRALE Aufruf.
                # Er orchestriert:
                #   a) Meldezettel-Validierung (registration_validation.py)
                #      → validate_meldezettel(form_data, melde_text)
                #   b) Rechnungs-Validierung (invoice_validation.py)
                #      → validate_rechnung(), validate_zahlungsbestaetigung(),
                #        validate_monatsrechnung()
                #   c) Gesamtentscheidung zusammenbauen
                #
                # Rückgabe:
                #   {
                #       "all_ok":         True/False,    ← Gesamtergebnis
                #       "meldezettel_ok": True/False,
                #       "rechnungen_ok":  True/False,
                #       "melde_decision":   {...},       ← Meldezettel-Details
                #       "invoice_decision": {...},       ← Rechnungs-Details
                #   }
                overall_decision = build_overall_decision(form_data, final_pdfs)

                # ══════════════════════════════════════════════
                # SCHRITT 6: FEHLERTEXTE
                # ══════════════════════════════════════════════
                # Bei all_ok = False: detaillierte Fehlertexte erzeugen.
                # Bei all_ok = True:  alle drei Strings sind leer ("").
                fehler_melde, fehler_rechnung, fehlergrund = build_error_reason(overall_decision)

                # ══════════════════════════════════════════════
                # SCHRITT 7: ANTRAGSDATEN-VOLLSTÄNDIGKEIT
                # ══════════════════════════════════════════════
                # Unabhängig von der PDF-Validierung prüfen wir, ob im
                # Antrag selbst wichtige Felder fehlen. Fehlende Felder
                # machen eine Validierung unmöglich (z.B. ohne gilt_von
                # können wir den Gültigkeitszeitraum nicht prüfen).
                #
                # Das hilft dem Sachbearbeiter: Er sieht sofort, dass das
                # Problem beim ANTRAG liegt, nicht bei den Dokumenten.
                fehler_antrag_parts = []
                if not (form_data.get("gilt_von") or "").strip():
                    fehler_antrag_parts.append("gilt_von fehlt")
                if not (form_data.get("gilt_bis") or "").strip():
                    fehler_antrag_parts.append("gilt_bis fehlt")
                if not (form_data.get("vorname") or "").strip():
                    fehler_antrag_parts.append("Vorname fehlt")
                if not (form_data.get("familienname") or "").strip():
                    fehler_antrag_parts.append("Familienname fehlt")
                if not (form_data.get("geburtsdatum") or "").strip():
                    fehler_antrag_parts.append("Geburtsdatum fehlt")
                if not (form_data.get("plz") or "").strip():
                    fehler_antrag_parts.append("PLZ fehlt")

                fehler_antrag = "; ".join(fehler_antrag_parts)

                # Antragsfehler in den Gesamtfehlergrund EINBAUEN
                # → Wird vorne angefügt: "Antragsdaten: gilt_von fehlt | ..."
                # So sieht der Sachbearbeiter zuerst den grundlegendsten Fehler.
                if fehler_antrag:
                    fehlergrund = (
                        f"Antragsdaten: {fehler_antrag} | {fehlergrund}"
                        if fehlergrund
                        else f"Antragsdaten: {fehler_antrag}"
                    )

                # ══════════════════════════════════════════════
                # SCHRITT 8: DETAIL-DICTS FÜR EXCEL-SPALTEN
                # ══════════════════════════════════════════════
                # Laufende Nummer aus antrag.json (die interne Antragsnummer
                # des Fördersystems, z.B. "KT-2024-001")
                laufende_nr = form_data.get("laufende_nr")

                # Sub-Dicts für die einzelnen Excel-Spalten
                melde_dec = overall_decision.get("melde_decision", {})
                inv_dec = overall_decision.get("invoice_decision", {})

                # ══════════════════════════════════════════════
                # SCHRITT 9: EXCEL-ZEILE AUFBAUEN
                # ══════════════════════════════════════════════
                # Ein flaches Dict (keine verschachtelten Strukturen),
                # damit pandas es direkt als DataFrame-Zeile verwenden kann.
                # Jeder Key wird zu einer Excel-Spalte.
                row = {
                    # ── Identifikation ──
                    "run_id": run_id,                                  # 1, 2, 3, ...
                    "laufende_nr": laufende_nr,                        # "KT-2024-001"
                    "intern_id": form_data.get("intern_id"),           # "12345"

                    # ── Antragsdaten (direkt aus antrag.json) ──
                    "familienname": form_data.get("familienname"),     # "Mustermann"
                    "vorname": form_data.get("vorname"),               # "Max"
                    "geschlecht": form_data.get("geschlecht"),         # "männlich"
                    "geburtsdatum": form_data.get("geburtsdatum"),     # "01.01.1990"
                    "strasse": form_data.get("strasse"),               # "Musterstraße 1"
                    "plz": form_data.get("plz"),                       # "5020"
                    "gilt_von": form_data.get("gilt_von"),             # "2024-09-15"
                    "gilt_bis": form_data.get("gilt_bis"),             # "2025-09-14"

                    # ── Meldezettel-Ergebnis ──
                    "meldezettel_ok": overall_decision.get("meldezettel_ok"),        # True/False
                    "meldezettel_konfidenz": melde_dec.get("meldezettel_confidence"),  # 0.87
                    "meldezettel_datei": melde_dec.get("meldezettel_file"),           # "11987.pdf"
                    "fehler_meldezettel": fehler_melde,                               # Fehlertext

                    # ── Rechnungs-Ergebnis ──
                    "rechnungen_ok": overall_decision.get("rechnungen_ok"),           # True/False
                    "jahresrechnung_ok": inv_dec.get("jahresrechnung_ok"),            # True/False
                    "zahlungsbestätigung_ok": inv_dec.get("zahlungsbestaetigung_ok"), # True/False
                    "monatsrechnungen_gültig": inv_dec.get("monatsrechnungen_valid"), # 0, 1, 2, 3...
                    "fehler_rechnung": fehler_rechnung,                               # Fehlertext

                    # ── Antragsdaten-Fehler ──
                    "fehler_antrag": fehler_antrag,                    # "gilt_von fehlt; ..."

                    # ── Gesamtergebnis ──
                    "all_ok": overall_decision.get("all_ok"),          # True/False
                    "fehlergrund": fehlergrund,                        # Alles zusammen

                    # ── Klassifizierung (Zusammenfassung) ──
                    "dok_klassifizierung": dok_klassifizierung,         # "11612.pdf → ..."

                    # ── Case-Identifikation ──
                    "case_id": case_id,                                # "12345"
                    "monat_ordner": month_dir.name,                    # "2024-09"
                }

            except Exception as exc:
                # ══════════════════════════════════════════════════
                # FEHLERBEHANDLUNG: Case-Verarbeitung fehlgeschlagen
                # ══════════════════════════════════════════════════
                #
                # Mögliche Ursachen:
                #   - Korrupte PDF (konnte nicht gelesen werden)
                #   - Fehlende Systemabhängigkeiten (Tesseract, Poppler)
                #   - Unerwartetes JSON-Format in antrag.json
                #   - Speicherprobleme bei sehr großen PDFs
                #   - Timeout bei OCR
                #
                # Strategie:
                #   1. Fehler auf der Konsole ausgeben (für Debugging)
                #   2. Fehlerzeile ins Excel schreiben (all_ok = False)
                #   3. Batch WEITER laufen lassen (kein Abbruch!)
                #
                # So gehen die Ergebnisse der anderen Cases nicht verloren.
                # Der Sachbearbeiter sieht im Excel den Fehler und kann
                # diesen Case manuell bearbeiten.
                fehler_count += 1
                error_msg = f"VERARBEITUNGSFEHLER: {type(exc).__name__}: {exc}"
                print(f"  ⚠ {error_msg}")

                # Fehlerzeile: Alle Prüfergebnisse auf False/None,
                # aber Antragsdaten trotzdem eintragen (für Identifikation)
                row = {
                    "run_id": run_id,
                    "laufende_nr": form_data.get("laufende_nr"),
                    "intern_id": form_data.get("intern_id"),
                    "familienname": form_data.get("familienname"),
                    "vorname": form_data.get("vorname"),
                    "geschlecht": form_data.get("geschlecht"),
                    "geburtsdatum": form_data.get("geburtsdatum"),
                    "strasse": form_data.get("strasse"),
                    "plz": form_data.get("plz"),
                    "gilt_von": form_data.get("gilt_von"),
                    "gilt_bis": form_data.get("gilt_bis"),
                    "meldezettel_ok": False,
                    "meldezettel_konfidenz": None,
                    "meldezettel_datei": None,
                    "fehler_meldezettel": None,
                    "rechnungen_ok": False,
                    "jahresrechnung_ok": None,
                    "zahlungsbestätigung_ok": None,
                    "monatsrechnungen_gültig": None,
                    "fehler_rechnung": None,
                    "fehler_antrag": None,
                    "all_ok": False,
                    "fehlergrund": error_msg,       # ← Exception-Text hier!
                    "dok_klassifizierung": None,
                    "case_id": case_id,
                    "monat_ordner": month_dir.name,
                }

            # Zeile anhängen — sowohl bei Erfolg ALS AUCH bei Fehler
            rows.append(row)
            run_id += 1

    # ══════════════════════════════════════════════════════════════════
    # EXCEL-REPORT SCHREIBEN
    # ══════════════════════════════════════════════════════════════════
    #
    # Alle Zeilen werden zu einem pandas DataFrame zusammengefügt
    # und als .xlsx geschrieben. Die Spaltenreihenfolge entspricht
    # der Reihenfolge der Keys im row-Dict (Python 3.7+: Insertion Order).

    df = pd.DataFrame(rows)
    output_path = BASE_DIR / "case_report.xlsx"

    try:
        df.to_excel(output_path, index=False)
        print(f"\nReport geschrieben nach: {output_path}")
    except PermissionError:
        # ── Fallback bei gesperrter Datei ──
        # Windows sperrt Excel-Dateien exklusiv, wenn sie geöffnet sind.
        # Statt den Batch abzubrechen, schreiben wir eine Datei mit
        # Zeitstempel im Namen. Der Sachbearbeiter kann die alte Datei
        # schließen und den Batch erneut starten, oder die neue Datei
        # direkt verwenden.
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fallback_path = BASE_DIR / f"case_report_{ts}.xlsx"
        df.to_excel(fallback_path, index=False)
        print(f"\nWARNUNG: {output_path.name} ist gesperrt (in Excel geöffnet?).")
        print(f"Report stattdessen geschrieben nach: {fallback_path}")

    # ══════════════════════════════════════════════════════════════════
    # ZUSAMMENFASSUNG AUF DER KONSOLE
    # ══════════════════════════════════════════════════════════════════
    #
    # Schnelle Übersicht, OHNE das Excel öffnen zu müssen.
    #
    # Beispielausgabe:
    #   ==================================================
    #   Batch abgeschlossen: 47 Cases verarbeitet
    #     ✓ 38 OK
    #     ✗ 7 abgelehnt
    #     ⚠ 2 Verarbeitungsfehler (siehe fehlergrund)
    #   ==================================================
    #
    # Rechnung:
    #   total        = alle verarbeiteten Cases (OK + abgelehnt + Fehler)
    #   ok_count     = Cases mit all_ok = True
    #   abgelehnt    = total - ok_count - fehler_count
    #   fehler_count = Cases, die eine Exception ausgelöst haben

    total = len(rows)
    ok_count = sum(1 for r in rows if r.get("all_ok"))

    print(f"\n{'='*50}")
    print(f"Batch abgeschlossen: {total} Cases verarbeitet")
    print(f"  ✓ {ok_count} OK")
    print(f"  ✗ {total - ok_count - fehler_count} abgelehnt")
    if fehler_count:
        print(f"  ⚠ {fehler_count} Verarbeitungsfehler (siehe fehlergrund)")
    print(f"{'='*50}")


# =============================================================================
# ENTRY POINT
# =============================================================================
#
# if __name__ == "__main__":
#   → Nur ausführen, wenn das Skript DIREKT aufgerufen wird:
#     python main.py           → main() wird ausgeführt ✓
#     import main              → main() wird NICHT ausgeführt ✗
#     from main import main    → main() wird NICHT ausgeführt ✗
#
# Das ermöglicht es, einzelne Funktionen aus main.py zu importieren
# (z.B. für Tests), ohne dass der gesamte Batch-Lauf startet.

if __name__ == "__main__":
    main()