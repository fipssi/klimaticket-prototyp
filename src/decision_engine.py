"""
decision_engine.py — Entscheidungslogik für KlimaTicket-Förderanträge
=====================================================================

ÜBERBLICK
---------
Diese Datei ist das "Gehirn" der automatisierten Antragsprüfung.

Sie bekommt als Input:
  1. form_data       → die Antragsdaten aus dem JSON (Name, Geb.-Datum, PLZ, Gültigkeit, …)
  2. classified_pdfs → eine Liste von PDFs, die der ML-Classifier bereits klassifiziert hat.
                       Jedes Element ist ein Tupel:
                         (Dateipfad, Dokumenttyp, extrahierter Text, Konfidenz)
                       Beispiel:
                         (Path("rechnung.pdf"), "jahresrechnung", "KlimaTicket Ö ...", 0.92)

Die Decision Engine entscheidet dann:
  • Ist ein gültiger Meldezettel vorhanden?
  • Sind genügend gültige Rechnungsnachweise vorhanden?
  • Gesamtentscheidung: Antrag OK oder abgelehnt?


PIPELINE (Ablaufreihenfolge in main.py)
---------------------------------------
  1. ML-Classifier klassifiziert alle PDFs im Case-Ordner
     → Mögliche Typen: "meldezettel", "jahresrechnung", "zahlungsbestaetigung", "monatsrechnung"

  2. reclassify_short_jahresrechnungen()          ← diese Datei
     → Korrigiert Jahresrechnungen mit kurzem Leistungszeitraum zu "monatsrechnung"

  3. build_overall_decision()                      ← diese Datei
     → Ruft intern auf:
        a) build_melde_decision()     → prüft den Meldezettel
        b) build_invoice_decision()   → prüft alle Rechnungsdokumente
     → Kombiniert beides zur Gesamtentscheidung

  4. main.py schreibt das Ergebnis in den Excel-Report


ENTSCHEIDUNGSREGELN
-------------------
Ein Antrag ist OK (all_ok = True), wenn BEIDE Bedingungen erfüllt sind:

  A) Meldezettel OK (UND-Verknüpfung aller Checks):
     ✓ Mindestens ein PDF als "meldezettel" klassifiziert (über Konfidenz-Schwelle)
     ✓ Vorname stimmt mit Antrag überein
     ✓ Nachname stimmt mit Antrag überein
     ✓ Geburtsdatum stimmt mit Antrag überein
     ✓ PLZ ist förderberechtigt

  B) Rechnungsnachweis OK (ODER-Verknüpfung — EIN Weg reicht):
     ┌──────────────────────────────────────────────────────────────┐
     │ Variante 1: 1× gültige Jahresrechnung                      │
     │             (Name + Zeitraum OK, Leistungszeitraum ≥ 10 Mo) │
     ├──────────────────────────────────────────────────────────────┤
     │ Variante 2: 1× gültige Zahlungsbestätigung                 │
     │             (Name + Zeitraum OK)                             │
     ├──────────────────────────────────────────────────────────────┤
     │ Variante 3: 3× gültige Monatsrechnungen                    │
     │             (jeweils für unterschiedliche Monate)            │
     └──────────────────────────────────────────────────────────────┘

  Finale Formel:
    all_ok = meldezettel_ok AND (jahresrechnung_ok OR zahlung_ok OR monatsrechnungen_ok)
"""

from pathlib import Path
from typing import Tuple

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS AUS ANDEREN MODULEN
# ─────────────────────────────────────────────────────────────────────────────

# Meldezettel-Validierung:
#   process_meldezettel(form_data, text) → dict mit vorname_ok, nachname_ok,
#   geburtsdatum_ok, plz_ok, all_ok
from src.registration_validation import process_meldezettel

# Rechnungs-Validierung: Drei spezialisierte Funktionen, je eine pro Dokumenttyp.
# Jede gibt ein dict zurück mit mindestens: all_ok, name_ok, period_ok
from src.invoice_validation import (
    validate_rechnung,               # Für Jahresrechnungen
    validate_zahlungsbestaetigung,   # Für Zahlungsbestätigungen
    validate_monatsrechnung,         # Für Monatsrechnungen
    _extract_leistungszeitraum,      # Hilfsfunktion: liest Datum-Paar aus dem PDF-Text
    _months_between,                 # Hilfsfunktion: berechnet Monats-Differenz
)


# =============================================================================
# KONFIGURATION
# =============================================================================
# Diese Werte steuern das Verhalten der Decision Engine.
# Bei Bedarf können sie angepasst werden, ohne den Code zu ändern.
# =============================================================================

# ── Meldezettel-Konfidenz-Schwellenwert ──
#
# Der ML-Classifier gibt für jedes PDF eine Konfidenz zurück (0.0 bis 1.0).
# Nur PDFs mit Konfidenz >= diesem Wert werden als Meldezettel akzeptiert.
#
# Warum nötig?
#   Manchmal klassifiziert das ML-Modell eine Rechnung fälschlich als
#   Meldezettel — aber mit niedriger Konfidenz (z.B. 45%). Dieser
#   Schwellenwert filtert solche Fehlklassifikationen heraus.
#
# Beobachtete Werte aus Testdaten:
#   • Echte Meldezettels:   77-89% (niedrigster: 76.8%)
#   • Fehlklassifizierungen: 40-55%
#
# Empfohlene Werte:
#   0.50 → liberal (akzeptiert mehr, aber auch mehr Fehler)
#   0.70 → guter Kompromiss
#   0.75 → konservativ (sicher, könnte aber schlechte Scans ablehnen)
MELDE_MIN_CONFIDENCE = 0.70

# ── Mindest-Leistungszeitraum für Jahresrechnungen ──
#
# Eine Rechnung, die als "jahresrechnung" klassifiziert wurde, kann
# trotzdem einen kurzen Leistungszeitraum haben (z.B. nur 1 Monat).
# Das passiert z.B. bei der ersten Teilrechnung eines neuen KlimaTickets.
#
# Wenn der Leistungszeitraum kürzer als dieser Wert ist, wird das Dokument
# zu "monatsrechnung" reklassifiziert → durchläuft die Monats-Pipeline.
#
# Beispiel:
#   Rechnung mit Leistungszeitraum 15.12.2024 – 14.01.2025 = 1 Monat
#   → ML sagt "jahresrechnung" (weil das Layout gleich aussieht)
#   → Reklassifizierung zu "monatsrechnung"
#   → Zählt als 1 von 3 benötigten Monatsnachweisen
MIN_MONTHS_JAHRESRECHNUNG = 10


# =============================================================================
# SCHRITT 1: REKLASSIFIZIERUNG
# =============================================================================
#
# WARUM?
#   Der ML-Classifier entscheidet anhand des LAYOUTS, ob ein PDF eine
#   Jahresrechnung ist. Er kann aber nicht prüfen, ob der Leistungszeitraum
#   tatsächlich 12 Monate umfasst — das ist eine fachliche Prüfung.
#
#   Ohne Reklassifizierung würde eine 1-Monats-Rechnung als "Jahresrechnung"
#   validiert werden und durchfallen (weil zu kurz). Mit Reklassifizierung
#   wird sie stattdessen als Monatsrechnung gezählt — was fachlich korrekt ist.
#
# WANN?
#   Wird von main.py NACH dem Classifier aber VOR build_overall_decision()
#   aufgerufen. So hat main.py Zugriff auf BEIDE Listen:
#   • Original-Klassifizierung (für die dok_klassifizierung-Spalte im Excel)
#   • Korrigierte Klassifizierung (für die eigentliche Validierung)
#
# =============================================================================

def reclassify_short_jahresrechnungen(
    classified_pdfs: list[Tuple[Path, str, str, float]],
) -> list[Tuple[Path, str, str, float]]:
    """
    Korrigiert den Dokumenttyp von Jahresrechnungen mit kurzem Leistungszeitraum.

    Eingang:
        classified_pdfs: Liste von Tupeln aus dem ML-Classifier.
            Jedes Tupel: (Dateipfad, Dokumenttyp, extrahierter_Text, Konfidenz)

    Ausgang:
        Gleiche Liste, aber doc_type ggf. geändert:
            "jahresrechnung" → "monatsrechnung"  (wenn Leistungszeitraum < 10 Monate)
            Alle anderen Typen bleiben unverändert.
    """
    result = []

    for pdf_path, doc_type, text, confidence in classified_pdfs:

        # ── Nur Jahresrechnungen prüfen ──
        # Monatsrechnungen, Zahlungsbestätigungen, Meldezettels: einfach durchreichen
        if doc_type == "jahresrechnung":

            # Leistungszeitraum aus dem PDF-Text extrahieren.
            # Sucht Zeilen wie "Leistungszeitraum: 15.12.2024 - 14.01.2025"
            # und gibt zwei datetime-Objekte zurück (oder None, None).
            l_von, l_bis = _extract_leistungszeitraum(text)

            # Dauer in Monaten berechnen.
            # Beispiel: Dez 2024 bis Jan 2025 = 1 Monat
            # Beispiel: Dez 2024 bis Dez 2025 = 12 Monate
            # Falls Start oder Ende nicht erkannt → None (keine Reklassifizierung)
            leist_months = _months_between(l_von, l_bis) if (l_von and l_bis) else None

            # Reklassifizieren, wenn Zeitraum erkannt UND zu kurz
            if leist_months is not None and leist_months < MIN_MONTHS_JAHRESRECHNUNG:
                print(f"  Reklassifizierung: {pdf_path.name} "
                      f"jahresrechnung → monatsrechnung "
                      f"(Leistungszeitraum: {leist_months} Monate)")
                doc_type = "monatsrechnung"

            # WICHTIG: Wenn kein Leistungszeitraum erkannt wurde (leist_months = None),
            # bleibt das Dokument als "jahresrechnung". Wir wollen KEINE
            # False-Positive-Reklassifizierung bei unlesbaren/korrupten PDFs.

        # Tupel (ggf. mit geändertem doc_type) in die Ergebnisliste
        result.append((pdf_path, doc_type, text, confidence))

    return result


# =============================================================================
# SCHRITT 2a: MELDEZETTEL-ENTSCHEIDUNG
# =============================================================================
#
# AUFGABE:
#   Prüft, ob unter den hochgeladenen Dokumenten ein gültiger Meldezettel ist.
#   "Gültig" heißt: Name, Geburtsdatum und PLZ stimmen mit dem Antrag überein.
#
# AUSWAHL BEI MEHREREN:
#   Falls der Antragsteller mehrere PDFs hochgeladen hat, die als "meldezettel"
#   klassifiziert wurden (z.B. Vorder- und Rückseite), wird nur dasjenige
#   mit der höchsten ML-Konfidenz verwendet.
#
# =============================================================================

def build_melde_decision(form_data: dict,
                         classified_pdfs: list[Tuple[Path, str, str, float]]) -> dict:
    """
    Prüft den Meldezettel gegen die Antragsdaten.

    Rückgabe (dict):
        meldezettel_found:      bool   — Wurde überhaupt ein Meldezettel erkannt?
        meldezettel_ok:         bool   — Sind Name, Geburtsdatum, PLZ korrekt?
        meldezettel_confidence: float  — ML-Konfidenz des gewählten Meldezettels
        meldezettel_file:       str    — Dateiname des gewählten PDFs
        details:                dict   — Einzelergebnisse (vorname_ok, nachname_ok, …)
        reason:                 str    — Nur gesetzt, wenn kein Meldezettel gefunden
    """

    # ── 1. Kandidaten sammeln ──
    # Filtert aus allen klassifizierten PDFs diejenigen heraus, die:
    #   a) als "meldezettel" klassifiziert wurden  UND
    #   b) eine Konfidenz >= MELDE_MIN_CONFIDENCE haben
    #
    # Dokumente unter der Schwelle werden ignoriert — das sind typischerweise
    # Fehlklassifikationen (z.B. eine Rechnung, die versehentlich als
    # Meldezettel erkannt wurde, aber nur mit 45% Konfidenz).
    candidates = [
        (pdf_path, text, confidence)
        for pdf_path, doc_type, text, confidence in classified_pdfs
        if doc_type == "meldezettel" and confidence >= MELDE_MIN_CONFIDENCE
    ]

    # ── 2. Kein Kandidat? → Abbruch ──
    if not candidates:
        return {
            "meldezettel_found": False,
            "meldezettel_ok": False,
            "reason": "Kein Meldezettel gefunden.",
        }

    # ── 3. Besten Kandidaten wählen ──
    # max(..., key=lambda c: c[2]) wählt das Tupel mit der höchsten Konfidenz.
    # Bei nur einem Kandidaten wird natürlich dieser genommen.
    best_path, best_text, best_confidence = max(candidates, key=lambda c: c[2])

    # Debug-Ausgabe (erscheint in der Konsole beim Batch-Lauf)
    print(f"Meldezettel gewählt: {best_path.name} (Konfidenz: {best_confidence:.1%})")
    if len(candidates) > 1:
        print(f"  ({len(candidates)} Kandidaten, {len(candidates)-1} verworfen)")

    # ── 4. Inhaltliche Validierung ──
    # process_meldezettel() ist in registration_validation.py definiert.
    # Sie extrahiert aus dem PDF-Text die Felder (Vorname, Nachname, Geb.-Datum, PLZ)
    # und vergleicht sie mit den Antragsdaten.
    #
    # Rückgabe-Dict enthält u.a.:
    #   vorname_ok:       bool  — Vorname stimmt überein?
    #   nachname_ok:      bool  — Nachname stimmt überein?
    #   geburtsdatum_ok:  bool  — Geburtsdatum stimmt überein?
    #   plz_ok:           bool  — PLZ förderberechtigt?
    #   all_ok:           bool  — Alles zusammen OK?
    melde_result = process_meldezettel(form_data, best_text)

    return {
        "meldezettel_found": True,
        "meldezettel_ok": melde_result.get("all_ok", False),
        "meldezettel_confidence": best_confidence,
        "meldezettel_file": best_path.name,
        "details": melde_result,
    }


# =============================================================================
# SCHRITT 2b: RECHNUNGS-ENTSCHEIDUNG — Hilfsfunktionen für Beste-Auswahl
# =============================================================================
#
# PROBLEM:
#   Ein Antragsteller kann mehrere Jahresrechnungen oder Zahlungsbestätigungen
#   hochladen. Vorher wurde einfach die LETZTE in der Dateiliste genommen —
#   das war ein Bug, weil die Reihenfolge zufällig ist und eine gute Rechnung
#   von einer schlechten überschrieben werden konnte.
#
# LÖSUNG:
#   Alle Kandidaten in eine Liste sammeln, dann den BESTEN auswählen.
#   "Bester" wird über ein Ranking-Tupel bestimmt, das Python elementweise
#   vergleicht:
#
#   Beispiel Jahresrechnung:
#     (True,  12, True)  > (True,  10, True)   → 12 Monate schlägt 10
#     (True,   1, True)  > (False, 12, True)   → all_ok=True schlägt immer
#     (False,  0, True)  > (False,  0, False)  → name_ok als Tiebreaker
#
# =============================================================================

def _pick_best_jahresrechnung(candidates: list[dict]) -> dict:
    """
    Wählt die beste Jahresrechnung aus einer Liste von Validierungsergebnissen.

    Ranking-Kriterien (erstes ist wichtigstes):
      1. all_ok = True         → Eine komplett gültige Rechnung gewinnt immer
      2. leist_months (höher)  → Längerer Leistungszeitraum ist besser
      3. name_ok = True        → Name-Match als letzter Tiebreaker

    Eingang:
        candidates: Liste von Dicts, jedes ein Ergebnis von validate_rechnung().
                    Enthält u.a.: all_ok, name_ok, period_ok, leist_months, _source_file

    Ausgang:
        Das Dict des besten Kandidaten.
    """
    def sort_key(res: dict) -> tuple:
        return (
            res.get('all_ok', False),        # Prio 1: Komplett gültig?
            res.get('leist_months') or 0,    # Prio 2: Längster Leistungszeitraum
            res.get('name_ok', False),       # Prio 3: Name erkannt?
        )
    # max() mit diesem Schlüssel gibt den Kandidaten mit dem höchsten Tupel zurück
    return max(candidates, key=sort_key)


def _pick_best_zahlungsbestaetigung(candidates: list[dict]) -> dict:
    """
    Wählt die beste Zahlungsbestätigung aus einer Liste von Validierungsergebnissen.

    Ranking-Kriterien:
      1. all_ok = True       → Komplett gültig gewinnt
      2. name_ok = True      → Name-Match wichtiger als Zeitraum
      3. period_ok = True    → Zeitraum-Match als letzter Tiebreaker

    (Kein leist_months-Kriterium, weil Zahlungsbestätigungen keinen
     Leistungszeitraum im gleichen Sinne wie Jahresrechnungen haben.)
    """
    def sort_key(res: dict) -> tuple:
        return (
            res.get('all_ok', False),        # Prio 1: Komplett gültig?
            res.get('name_ok', False),       # Prio 2: Name erkannt?
            res.get('period_ok', False),     # Prio 3: Zeitraum passt?
        )
    return max(candidates, key=sort_key)


# =============================================================================
# SCHRITT 2b: RECHNUNGS-ENTSCHEIDUNG — Hauptfunktion
# =============================================================================
#
# AUFGABE:
#   Geht alle klassifizierten PDFs (außer Meldezettels) durch und validiert
#   jedes Rechnungsdokument gegen die Antragsdaten.
#
# DREI WEGE ZUM ERFOLG (ODER-Verknüpfung):
#
#   ┌─────────────────────────────────┐
#   │ 1× gültige Jahresrechnung      │ → rechnungen_ok = True
#   ├─────────────────────────────────┤
#   │ 1× gültige Zahlungsbestätigung │ → rechnungen_ok = True
#   ├─────────────────────────────────┤
#   │ 3× gültige Monatsrechnungen    │ → rechnungen_ok = True
#   │ (verschiedene Monate)           │
#   └─────────────────────────────────┘
#
# BEI MEHREREN KANDIDATEN:
#   Jahresrechnungen & Zahlungsbestätigungen: Beste wird gewählt (siehe oben)
#   Monatsrechnungen: Alle gültigen zählen, aber pro Monat nur einmal
#
# =============================================================================

def build_invoice_decision(form_data: dict,
                           classified_pdfs: list[Tuple[Path, str, str, float]]) -> dict:
    """
    Prüft alle Rechnungsdokumente und entscheidet, ob der Rechnungsnachweis reicht.

    Rückgabe (dict):
        jahresrechnung_found:   bool  — Mindestens eine Jahresrechnung erkannt?
        jahresrechnung_ok:      bool  — Ist die BESTE davon gültig?
        jahresrechnung_details: dict  — Validierungsergebnis der besten
        jahresrechnung_count:   int   — Wie viele Jahresrechnungen insgesamt?

        zahlungsbestaetigung_found/ok/details/count — Analog

        monatsrechnungen_found: int   — Gesamtzahl erkannter Monatsrechnungen
        monatsrechnungen_valid: int   — Davon: wie viele VERSCHIEDENE gültige Monate?
        monatsrechnungen_ok:    bool  — Mindestens 3 verschiedene Monate abgedeckt?

        rechnungen_ok:          bool  — GESAMT: Rechnungsnachweis ausreichend?
    """

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1: Alle Dokumente validieren und nach Typ sammeln
    # ══════════════════════════════════════════════════════════════════════════
    #
    # Wir iterieren EINMAL über alle PDFs. Jedes PDF wird anhand seines
    # doc_type an die richtige Validierungsfunktion übergeben.
    # Die Ergebnisse werden in Listen/Sets gesammelt.

    # Liste aller Jahresrechnungs-Validierungsergebnisse.
    # Jedes Element ist ein dict von validate_rechnung(), erweitert um _source_file.
    jahresrechnung_candidates: list[dict] = []

    # Liste aller Zahlungsbestätigungs-Validierungsergebnisse.
    zahlung_candidates: list[dict] = []

    # Zähler: Wie viele PDFs wurden als Monatsrechnung klassifiziert?
    monats_found = 0

    # Set von Monatsschlüsseln (z.B. "2024-07", "2024-08", "2024-09").
    # Warum ein Set und keine Liste?
    #   → Wenn derselbe Monat mehrfach vorkommt (z.B. zwei Rechnungen für Sept.),
    #     zählt er trotzdem nur einmal. Sets ignorieren Duplikate automatisch.
    valid_months: set[str] = set()

    for pdf_path, doc_type, text, _confidence in classified_pdfs:

        # ── Jahresrechnung ──
        if doc_type == 'jahresrechnung':
            # validate_rechnung() prüft:
            #   • name_ok:      Ist "Vorname Nachname" im Text nahe "Karteninhaber"?
            #   • period_ok:    Stimmt der Gültigkeitszeitraum mit dem Antrag überein?
            #   • leist_months: Wie viele Monate umfasst der Leistungszeitraum?
            #   • leist_in_guelt: Liegt der Leistungszeitraum innerhalb der Gültigkeit?
            #   • all_ok:       Alle Checks bestanden?
            j_res = validate_rechnung(form_data, text)

            # Dateiname merken — wird im Excel angezeigt ("welche PDF wurde gewählt?")
            j_res['_source_file'] = pdf_path.name

            # In die Kandidatenliste aufnehmen (wird NICHT sofort ausgewertet)
            jahresrechnung_candidates.append(j_res)

        # ── Zahlungsbestätigung ──
        elif doc_type == 'zahlungsbestaetigung':
            # validate_zahlungsbestaetigung() prüft:
            #   • name_ok:   Ist "Vorname Nachname" im Text nahe "für"?
            #   • period_ok: Stimmt der Zeitraum mit dem Antrag überein?
            #   • all_ok:    Alle Checks bestanden?
            z_res = validate_zahlungsbestaetigung(form_data, text)
            z_res['_source_file'] = pdf_path.name
            zahlung_candidates.append(z_res)

        # ── Monatsrechnung ──
        elif doc_type == 'monatsrechnung':
            # Multi-Page-PDFs: Manchmal enthält eine einzige PDF mehrere
            # Monatsrechnungen (z.B. Seite 1 = September, Seite 2 = Oktober).
            # Der document_loader trennt Seiten mit \f (Form Feed).
            # Wir splitten und validieren jede Seite einzeln.
            pages = [p for p in text.split('\f') if p.strip()]
            if not pages:
                pages = [text]

            for page_text in pages:
                monats_found += 1
                # validate_monatsrechnung() prüft:
                #   • name_ok:         Name nahe "Karteninhaber"?
                #   • guelt_ok:        Gültigkeitszeitraum == Antrag?
                #   • leist_ok:        Leistungszeitraum liegt innerhalb Gültigkeit?
                #   • leist_month_key: Monatsschlüssel, z.B. "2024-09"
                #   • all_ok:          Alle Checks bestanden?
                m_res = validate_monatsrechnung(form_data, page_text)

                # Nur gültige Monatsrechnungen zählen
                if m_res.get('all_ok'):
                    month_key = m_res.get('leist_month_key')   # z.B. "2024-09"
                    if month_key:
                        valid_months.add(month_key)

            if len(pages) > 1:
                print(f'  Monatsrechnung {pdf_path.name}: {len(pages)} Seiten, '
                      f'{len(valid_months)} gültige Monate bisher')

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2: Beste Jahresrechnung auswählen
    # ══════════════════════════════════════════════════════════════════════════
    #
    # Wenn nur eine Jahresrechnung vorliegt → die wird genommen.
    # Wenn mehrere vorliegen → _pick_best_jahresrechnung() wählt die beste.
    # Wenn keine vorliegt → jahresrechnung_ok = False.

    jahresrechnung_found = len(jahresrechnung_candidates) > 0

    if jahresrechnung_candidates:
        jahresrechnung_details = _pick_best_jahresrechnung(jahresrechnung_candidates)
        jahresrechnung_ok = bool(jahresrechnung_details.get('all_ok'))

        # Log bei mehreren Kandidaten, damit man im Batch-Lauf sieht, was passiert
        if len(jahresrechnung_candidates) > 1:
            print(f'  Jahresrechnung: {len(jahresrechnung_candidates)} Kandidaten, '
                  f'gewählt: {jahresrechnung_details.get("_source_file")} '
                  f'(all_ok={jahresrechnung_ok})')
    else:
        jahresrechnung_details = None
        jahresrechnung_ok = False

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 3: Beste Zahlungsbestätigung auswählen
    # ══════════════════════════════════════════════════════════════════════════
    # Identische Logik wie Phase 2, nur mit anderem Ranking.

    zahlung_found = len(zahlung_candidates) > 0

    if zahlung_candidates:
        zahlung_details = _pick_best_zahlungsbestaetigung(zahlung_candidates)
        zahlung_ok = bool(zahlung_details.get('all_ok'))

        if len(zahlung_candidates) > 1:
            print(f'  Zahlungsbestätigung: {len(zahlung_candidates)} Kandidaten, '
                  f'gewählt: {zahlung_details.get("_source_file")} '
                  f'(all_ok={zahlung_ok})')
    else:
        zahlung_details = None
        zahlung_ok = False

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 4: Monatsrechnungen auswerten
    # ══════════════════════════════════════════════════════════════════════════
    #
    # Hier gibt es keine "Beste-Auswahl" — stattdessen zählen wir, wie viele
    # UNTERSCHIEDLICHE Monate abgedeckt sind.
    #
    # Beispiele:
    #   3 Rechnungen für Jul, Aug, Sep  → monats_valid = 3 → OK ✓
    #   3 Rechnungen für Jul, Jul, Aug  → monats_valid = 2 → NICHT OK ✗
    #   5 Rechnungen für Jul, Aug, Sep, Okt, Nov → monats_valid = 5 → OK ✓

    monats_valid = len(valid_months)    # Anzahl verschiedener Monate im Set
    monats_ok = monats_valid >= 3       # Mindestens 3 verschiedene Monate nötig

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 5: Gesamtentscheidung für Rechnungsnachweis
    # ══════════════════════════════════════════════════════════════════════════
    #
    # ODER-Verknüpfung: Es reicht, wenn EINE der drei Varianten erfüllt ist.
    # Das entspricht der fachlichen Regel: Der Antragsteller muss IRGENDEINEN
    # gültigen Nachweis über ein KlimaTicket vorlegen.

    rechnungen_ok = (
        (jahresrechnung_found and jahresrechnung_ok)    # Variante 1
        or (zahlung_found and zahlung_ok)               # Variante 2
        or monats_ok                                    # Variante 3
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ERGEBNIS-DICT
    # ══════════════════════════════════════════════════════════════════════════
    #
    # Dieses Dict wird von main.py weiterverwendet für:
    #   • Excel-Spalten:    jahresrechnung_ok, zahlungsbestätigung_ok, monatsrechnungen_gültig
    #   • Excel-Dateien:    jahresrechnung_datei, zahlungsbestätigung_datei (aus _source_file)
    #   • Fehler-Report:    _build_invoice_errors() liest die details-Dicts
    #   • Gesamtentscheid:  rechnungen_ok wird mit meldezettel_ok kombiniert

    return {
        'jahresrechnung_found': jahresrechnung_found,       # bool
        'jahresrechnung_ok': jahresrechnung_ok,             # bool
        'jahresrechnung_details': jahresrechnung_details,   # dict oder None
        'jahresrechnung_count': len(jahresrechnung_candidates),  # int

        'zahlungsbestaetigung_found': zahlung_found,        # bool
        'zahlungsbestaetigung_ok': zahlung_ok,              # bool
        'zahlungsbestaetigung_details': zahlung_details,    # dict oder None
        'zahlungsbestaetigung_count': len(zahlung_candidates),  # int

        'monatsrechnungen_found': monats_found,             # int (Gesamtzahl)
        'monatsrechnungen_valid': monats_valid,             # int (verschiedene gültige Monate)
        'monatsrechnungen_ok': monats_ok,                   # bool

        'rechnungen_ok': rechnungen_ok,                     # bool (Gesamt-Flag)
    }


# =============================================================================
# SCHRITT 3: GESAMTENTSCHEIDUNG
# =============================================================================
#
# Kombiniert die Ergebnisse von Meldezettel (Schritt 2a) und Rechnungen
# (Schritt 2b) zu einer finalen Entscheidung.
#
# Logik:  all_ok = meldezettel_ok  AND  rechnungen_ok
#
# WICHTIG:
#   Diese Funktion erwartet, dass classified_pdfs BEREITS reklassifiziert
#   wurde (Schritt 1). Die Reklassifizierung passiert in main.py, weil
#   main.py sowohl die Original- als auch die korrigierte Liste braucht
#   (für die dok_klassifizierung-Spalte im Excel).
#
# Aufruf-Reihenfolge in main.py:
#   1. classified_pdfs = classify_case_pdfs(...)           # ML-Classifier
#   2. classified_pdfs = reclassify_short_jahresrechnungen(classified_pdfs)
#   3. overall = build_overall_decision(form_data, classified_pdfs)
#
# =============================================================================

def build_overall_decision(form_data: dict,
                           classified_pdfs: list[Tuple[Path, str, str, float]]) -> dict:
    """
    Finale Entscheidung: Antrag genehmigt oder abgelehnt?

    Rückgabe (dict):
        melde_decision:    dict  — Komplettes Ergebnis von build_melde_decision()
        invoice_decision:  dict  — Komplettes Ergebnis von build_invoice_decision()
        meldezettel_ok:    bool  — Kurzform: Meldezettel gültig?
        rechnungen_ok:     bool  — Kurzform: Rechnungsnachweis ausreichend?
        all_ok:            bool  — ★ FINALE ENTSCHEIDUNG ★
    """

    # ── Beide Teil-Entscheidungen berechnen ──
    melde_decision = build_melde_decision(form_data, classified_pdfs)
    invoice_decision = build_invoice_decision(form_data, classified_pdfs)

    # ── Kurzform-Flags extrahieren ──
    melde_ok = melde_decision.get("meldezettel_ok", False)
    rechnungen_ok = invoice_decision.get("rechnungen_ok", False)

    # ── Gesamtentscheidung ──
    # UND-Verknüpfung: BEIDE müssen erfüllt sein.
    # (Im Gegensatz zur ODER-Verknüpfung bei den drei Rechnungsvarianten.)
    #
    # Mögliche Ergebnisse:
    #   meldezettel_ok=True  + rechnungen_ok=True  → all_ok=True  ✓ Antrag OK
    #   meldezettel_ok=True  + rechnungen_ok=False → all_ok=False ✗ Rechnung fehlt
    #   meldezettel_ok=False + rechnungen_ok=True  → all_ok=False ✗ Meldezettel fehlt
    #   meldezettel_ok=False + rechnungen_ok=False → all_ok=False ✗ Beides fehlt
    all_ok = melde_ok and rechnungen_ok

    return {
        "melde_decision": melde_decision,      # Komplette Melde-Details (für Fehler-Report)
        "invoice_decision": invoice_decision,  # Komplette Rechnungs-Details (für Fehler-Report)
        "meldezettel_ok": melde_ok,            # Kurzform für Excel-Spalte
        "rechnungen_ok": rechnungen_ok,        # Kurzform für Excel-Spalte
        "all_ok": all_ok,                      # ★ Finale Entscheidung für Excel-Spalte
    }