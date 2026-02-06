"""
invoice_validation.py — Validierung von KlimaTicket-Rechnungsdokumenten
=======================================================================

ÜBERBLICK
---------
Dieses Modul validiert die INHALTLICHEN Daten von Rechnungsdokumenten
gegen die Antragsdaten. Es ist der dritte Schritt in der Pipeline:

    PDF → document_loader → Text
    Text → document_classifier → ("jahresrechnung", 0.92)
    Text + Antragsdaten → invoice_validation → {name_ok, period_ok, ...}  ← HIER
    Alle Ergebnisse → decision_engine → Gesamtentscheidung


DREI DOKUMENTTYPEN WERDEN VALIDIERT
------------------------------------
Jeder Typ hat eine eigene Validierungsfunktion:

    ┌──────────────────────────────┬────────────────────────────────┐
    │ Dokumenttyp                  │ Validierungsfunktion           │
    ├──────────────────────────────┼────────────────────────────────┤
    │ Jahresrechnung               │ validate_rechnung()            │
    │ Monatsrechnung               │ validate_monatsrechnung()      │
    │ Zahlungsbestätigung          │ validate_zahlungsbestaetigung()│
    └──────────────────────────────┴────────────────────────────────┘

    Alle drei prüfen:
        1. NAME:    Stimmt der Karteninhaber mit dem Antrag überein?
        2. ZEITRAUM: Stimmt der Gültigkeitszeitraum mit dem Antrag überein?

    Zusätzlich bei Jahres-/Monatsrechnungen:
        3. LEISTUNGSZEITRAUM: Liegt er innerhalb der Gültigkeit?


NAMENS-MATCHING — WARUM SO KOMPLEX?
-------------------------------------
Das Matching von Namen ist das schwierigste Problem in diesem Modul,
weil OCR-Text und Antragsdaten auf viele Weisen voneinander abweichen:

    Problem                 │ Beispiel
    ────────────────────────┼──────────────────────────────────
    Umlaute / Transliteration│ "Jürgen" vs. "Juergen" vs. "Jurgen"
    ß → ss                  │ "Größer" vs. "Groesser"
    OCR ohne Leerzeichen    │ "MaxMichael" statt "Max Michael"
    Bindestrich-Varianten   │ "Muster-Beispiel" vs. "Muster Beispiel"
    Mehrfach-Vornamen       │ Antrag "Max", PDF "Max Michael"
    Diakritika              │ "André" vs. "Andre"
    ────────────────────────┼──────────────────────────────────

    Strategie: Wir "erraten" den Namen NICHT aus dem PDF (Gefahr: Firmenname
    wie "One Mobility Ticketing GmbH" wird fälschlich als Name erkannt).
    Stattdessen prüfen wir, ob der Name aus dem ANTRAG im Umfeld eines
    klaren Markers vorkommt:
        - Rechnungen:           "Karteninhaber"  (12 Zeilen Fenster)
        - Zahlungsbestätigungen: "für"           (4 Zeilen Fenster)


ZEITRAUM-EXTRAKTION — OCR-ROBUSTHEIT
--------------------------------------
OCR-Text enthält typische Fehler bei Datumsangaben:

    "01 .04.2023"   → Leerzeichen vor dem Punkt
    "31.O3.2024"    → Buchstabe O statt Ziffer 0
    "1O.12.2024"    → O statt 0 mitten in einer Zahl

    Alle diese Fälle werden von clean_date_dot() repariert.


ABHÄNGIGKEITEN
--------------
    utils.py  — normalize_for_matching(), _compact(), _variants_for_umlaut_translit()
                (Shared Hilfsfunktionen, auch von registration_validation.py genutzt)
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Tuple, Optional

from utils import normalize_for_matching, _compact, _variants_for_umlaut_translit


# =============================================================================
# 0) LOKALE HILFSFUNKTIONEN
# =============================================================================
#
# Kleine Helfer, die nur innerhalb dieses Moduls verwendet werden.
# Sie sind mit _ prefixed, um zu signalisieren, dass sie nicht für
# den Import durch andere Module gedacht sind.

def _contains_marker(line_norm: str, marker: str) -> bool:
    """
    Prüft robust, ob ein Marker-Wort in einer normalisierten Textzeile vorkommt.

    Warum nicht einfach `marker in line`?
        OCR kann Buchstaben mit Leerzeichen trennen:
            "für"  → "f ü r"  → nach Normalisierung: "f u r"
            "gilt" → "g i l t"

        Deshalb prüfen wir zusätzlich per _compact() (entfernt alle Leerzeichen):
            _compact("f u r") = "fur"  → "fur" in "fur" = True ✓

    Parameter:
        line_norm: Bereits normalisierte Zeile (via normalize_for_matching)
        marker:    Gesuchtes Wort, ebenfalls normalisiert (z.B. "fur", "gilt")

    Rückgabe:
        True wenn der Marker gefunden wurde (normal oder compact)
    """
    return (marker in line_norm) or (marker in _compact(line_norm))


def _fmt_iso(dt: datetime | None) -> str | None:
    """
    Formatiert ein datetime als ISO-String (YYYY-MM-DD) für die Decision Engine.

    Beispiel: datetime(2024, 9, 15) → "2024-09-15"
    None → None (kein Datum gefunden)
    """
    return dt.date().isoformat() if dt else None


def _fmt_dot(dt: datetime | None) -> str | None:
    """
    Formatiert ein datetime als deutsches Datum (DD.MM.YYYY) für Debug-Ausgaben.

    Beispiel: datetime(2024, 9, 15) → "15.09.2024"
    None → None
    """
    return dt.strftime("%d.%m.%Y") if dt else None


# =============================================================================
# 1) DATUM-PARSING: ANTRAGSDATEN
# =============================================================================
#
# Antragsdaten kommen aus dem Formular (Streamlit UI oder Excel/JSON).
# Das Datumsformat ist NICHT garantiert — verschiedene Quellen liefern:
#   - ISO: "2024-09-15"
#   - ISO mit Uhrzeit: "2024-09-15 00:00:00"
#   - Deutsches Format: "15.09.2024"
#   - Leer: "" oder None
#
# parse_form_datetime() akzeptiert alle diese Formate.

def parse_form_datetime(value: str) -> Optional[datetime]:
    """
    Parst ein Datumsfeld aus dem Antrag (Formular/Excel/JSON).

    Akzeptierte Formate:
        "2024-09-15"              → ISO-Datum
        "2024-09-15T00:00:00"     → ISO mit Uhrzeit
        "2024-09-15 00:00:00"     → ISO mit Uhrzeit (Leerzeichen)
        "15.09.2024"              → Deutsches Format (TT.MM.JJJJ)
        ""                        → None (leer)
        None                      → None

    Rückgabe:
        datetime bei Erfolg, None bei leerem oder unbekanntem Format.

    Wichtig:
        Bei fehlenden Antragsdaten (None) bricht die Validierung nicht ab,
        sondern gibt {all_ok: False, reason: "gilt_von/gilt_bis fehlen"} zurück.
    """
    value = (value or "").strip()
    if not value:
        return None

    # Versuch 1: Python-natives ISO-Parsing (deckt "2024-09-15" und "2024-09-15T00:00:00" ab)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    # Versuch 2: Explizite Format-Strings für Sonderfälle
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Kein Format passt → None (wird upstream als "fehlend" behandelt)
    return None


# =============================================================================
# 1b) DATUM-PARSING: ZAHLUNGSBESTÄTIGUNG (Textformat)
# =============================================================================
#
# Zahlungsbestätigungen verwenden ein anderes Datumsformat als Rechnungen:
#   "gilt 21. Dez 2024 - 20. Dez 2025"
#
# Das ist ein deutsches Textformat mit abgekürztem Monatsnamen.
# Python's strptime braucht englische Monatsnamen → wir mappen erst.

# Regex für Textformat-Datumsangaben: "21. Dez 2024"
# Aufbau:
#   \d{1,2}       → Tag (1-2 Ziffern)
#   \.             → Punkt nach dem Tag
#   \s*            → Optionale Leerzeichen
#   [A-Za-z...]{3} → Monatsabkürzung (3 Buchstaben, inkl. Umlaute für "Jän", "Mär")
#   \s*            → Optionale Leerzeichen
#   \d{4}          → Jahr (4 Ziffern)
# Regex für Textformat-Datumsangaben: "21. Dez 2024", "01. Mär 2026", etc.
# Erweitert auf 3-4 Buchstaben um auch "März" (ausgeschrieben) zu matchen.
# \w matcht auch Unicode-Buchstaben wie ä, ö, ü
# [.,] akzeptiert sowohl Punkt als auch Komma (OCR-Fehler: "02, Okt" statt "02. Okt")
DATE_PATTERN_TEXT = r"\d{1,2}[.,]\s*\w{3,4}\s*\d{4}"

# Mapping: Deutsche Monatsabkürzungen → Englische (für strptime)
# Enthält alle Varianten: mit/ohne Umlaute, verschiedene Schreibweisen
# Die Ersetzung erfolgt case-insensitive, daher nur Kleinschreibung nötig
# ZUSÄTZLICH: Typische OCR-Fehler (O↔0, l↔1, etc.)
MONTH_MAP = {
    # Jänner (österreichisch für Januar)
    "jän": "Jan",
    "jan": "Jan",
    "jänner": "Jan",
    "januar": "Jan",
    # Februar
    "feb": "Feb",
    "februar": "Feb",
    # März
    "mär": "Mar",
    "mar": "Mar",      # falls OCR Umlaut verliert
    "märz": "Mar",
    "maerz": "Mar",    # Transliteration
    # April
    "apr": "Apr",
    "april": "Apr",
    # Mai
    "mai": "May",
    # Juni
    "jun": "Jun",
    "juni": "Jun",
    # Juli
    "jul": "Jul",
    "juli": "Jul",
    # August
    "aug": "Aug",
    "august": "Aug",
    # September
    "sep": "Sep",
    "sept": "Sep",
    "september": "Sep",
    # Oktober — inkl. OCR-Fehler O→0
    "okt": "Oct",
    "oct": "Oct",
    "0kt": "Oct",      # OCR-Fehler: O als 0 gelesen
    "oktober": "Oct",
    "0ktober": "Oct",  # OCR-Fehler
    # November
    "nov": "Nov",
    "november": "Nov",
    # Dezember
    "dez": "Dec",
    "dec": "Dec",
    "dezember": "Dec",
}


def parse_pdf_date_text(value: str | None) -> datetime | None:
    """
    Parst Datumsangaben im Textformat aus Zahlungsbestätigungen.

    Eingabe: "21. Dez 2024", "01. Mär 2026", "31. Jän 2026"
    Ausgabe: datetime(2024, 12, 21), etc.

    OCR-Robustheit:
        - Unicode-Normalisierung (verschiedene ä-Kodierungen → einheitlich)
        - Case-insensitive Monatsersetzung
        - Unterstützt Kurzformen (Mär) und Langformen (März)
        - Unterstützt österreichische Varianten (Jän = Januar)

    Ablauf:
        1. Unicode normalisieren (NFC)
        2. Deutsche Monatsabkürzung → Englisch ("Dez" → "Dec")
        3. strptime mit Format "%d. %b %Y"

    Rückgabe:
        datetime bei Erfolg, None bei ungültigem Format.
    """
    if not value:
        return None

    # Unicode normalisieren — verschiedene Kodierungen von ä, ö, ü vereinheitlichen
    v = unicodedata.normalize("NFC", value.strip())

    # OCR-Fehler: Komma statt Punkt nach dem Tag ("02, Okt" → "02. Okt")
    v = re.sub(r"(\d{1,2}),\s*", r"\1. ", v)

    # Monat extrahieren: zwischen Punkt und Jahr steht der Monatsname
    # "21. Dez 2024" → wir suchen "Dez"
    # Wir ersetzen den Monat case-insensitive
    for de, en in MONTH_MAP.items():
        # Case-insensitive Suche
        pattern = re.compile(re.escape(de), re.IGNORECASE)
        if pattern.search(v):
            v = pattern.sub(en, v)
            break   # Nur eine Ersetzung nötig (es gibt nur einen Monat pro Datum)

    try:
        return datetime.strptime(v, "%d. %b %Y")
    except ValueError:
        # Fallback: vielleicht ist das Format leicht anders
        # z.B. "21.Dez 2024" (ohne Leerzeichen nach Punkt)
        try:
            # Leerzeichen nach Punkt einfügen falls fehlend
            v_fixed = re.sub(r"(\d{1,2})\.(\w)", r"\1. \2", v)
            return datetime.strptime(v_fixed, "%d. %b %Y")
        except ValueError:
            return None


# =============================================================================
# 1c) DATUM-PARSING: RECHNUNGEN (Punkt-Format mit OCR-Fehlern)
# =============================================================================
#
# Rechnungen (Jahres- und Monatsrechnungen) verwenden das Format "DD.MM.YYYY":
#   "Gültigkeitszeitraum: 15.09.2024 - 14.09.2025"
#   "Leistungszeitraum:   15.09.2024 - 14.10.2024"
#
# OCR erzeugt aber typische Fehler:
#   "01 .04.2023"  → Leerzeichen vor dem Punkt
#   "31.O3.2024"   → Großbuchstabe O statt Ziffer 0
#   "1O.12.2024"   → O mitten in einer Zahl

# Regex für Punkt-Format-Datumsangaben (tolerant für OCR-Leerzeichen und O/0-Verwechslung)
# Aufbau:
#   \b             → Wortgrenze (damit "12345.01.2024" nicht matcht)
#   \d{1,2}        → Tag
#   \s*\.\s*       → Punkt mit optionalen Leerzeichen davor/danach
#   [\dOo]{1,2}    → Monat (1-2 Zeichen, kann O statt 0 sein)
#   \s*\.\s*       → Punkt
#   \d{4}          → Jahr
#   \b             → Wortgrenze
DATE_PATTERN_DOT = r"\b\d{1,2}\s*\.\s*[\dOo]{1,2}\s*\.\s*\d{4}\b"


def clean_date_dot(value: str) -> str:
    """
    Repariert typische OCR-Fehler in Punkt-Format-Datumsangaben.

    Reparaturen:
        1. Alle Leerzeichen entfernen: "01 .04.2023" → "01.04.2023"
        2. O nach Punkt → 0:           "31.O3.2024"  → "31.03.2024"
        3. O zwischen Ziffern → 0:     "1O.12.2024"  → "10.12.2024"

    Parameter:
        value: Roher Datums-String aus OCR

    Rückgabe:
        Bereinigter String, der von strptime("%d.%m.%Y") geparst werden kann.
    """
    # Alle Leerzeichen entfernen (OCR fügt oft welche ein: "01 .04")
    v = re.sub(r"\s+", "", (value or "").strip())

    # O (Buchstabe) → 0 (Ziffer) nach einem Punkt: ".O3" → ".03"
    v = re.sub(r"(?<=\.)[Oo](?=\d)", "0", v)

    # O (Buchstabe) → 0 (Ziffer) zwischen Ziffern: "1O" → "10"
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)

    return v


def parse_pdf_date_dot(value: str | None) -> datetime | None:
    """
    Parst Punkt-Format-Datumsangaben aus Rechnungen, mit OCR-Fehler-Korrektur.

    Eingabe: "01 .O4.2023"  (OCR-fehlerhaft)
    Ablauf:  → clean_date_dot → "01.04.2023" → strptime → datetime(2023, 4, 1)

    Rückgabe:
        datetime bei Erfolg, None bei ungültigem Format.
    """
    if not value:
        return None
    try:
        v = clean_date_dot(value)
        return datetime.strptime(v, "%d.%m.%Y")
    except ValueError:
        return None


# =============================================================================
# 2) NAMENS-MATCHING
# =============================================================================
#
# Zwei separate Matcher für Vor- und Nachnamen, weil die Logik
# unterschiedlich ist:
#
#   VORNAME:
#     - Es reicht, wenn der ERSTE Vorname aus dem Antrag matcht.
#       (Antrag: "Max", PDF: "Max Michael" → OK)
#     - Mehrfach-Vornamen im PDF werden toleriert.
#
#   NACHNAME:
#     - Bei Doppelnamen müssen ALLE Teile vorkommen.
#       (Antrag: "Muster Beispiel", PDF: "Muster Beispiel" → OK)
#       (Antrag: "Muster Beispiel", PDF: "Beispiel" → NICHT OK)
#
# Beide Matcher verwenden drei Ebenen der Toleranz:
#   1. Token-Match (exakte Wort-Übereinstimmung nach Normalisierung)
#   2. Compact-Match (Leerzeichen entfernt, für OCR-Fehler)
#   3. Umlaut-Varianten (ö→oe→o, ü→ue→u, etc.)

def first_name_matches_flexible(form_vorname: str, chunk_text: str) -> bool:
    """
    Prüft, ob der Vorname aus dem Antrag im gegebenen Textausschnitt vorkommt.

    Matching-Strategie (in Reihenfolge):
        1. Token-Match: Erster Vorname als exaktes Wort im Text?
           "max" in ["max", "michael", "mustermann"] → True ✓

        2. Compact-Match: OCR hat Leerzeichen verschluckt?
           _compact("max") in _compact("maxmichael") → True ✓

        3. Umlaut-Varianten: Transliteration berücksichtigen?
           "jurgen" ~ "juergen" ~ "jürgen" → generiert Varianten und prüft alle

    Parameter:
        form_vorname: Vorname aus dem Antrag (z.B. "Max")
        chunk_text:   Textausschnitt aus dem PDF (z.B. "Karteninhaber: Max Michael Mustermann")

    Rückgabe:
        True wenn der Vorname gefunden wurde.

    Warum nur der ERSTE Vorname?
        Im Antrag steht typischerweise nur der Rufname ("Max"),
        aber auf der Rechnung der vollständige Name ("Max Michael").
        Wir splitten den Antrags-Vornamen und nehmen nur das erste Wort.
    """
    form_norm = normalize_for_matching(form_vorname)
    if not form_norm:
        return False

    # Nur den ersten Vornamen verwenden (Rufname)
    # "Max Michael" → "max" (nach Normalisierung)
    first = form_norm.split()[0]

    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    # ── Ebene 1: Token-Match (exaktes Wort) ──
    # "max" in {"max", "michael", "mustermann"}
    if first in chunk_norm.split():
        return True

    # ── Ebene 2: Compact-Match (OCR ohne Leerzeichen) ──
    # Wenn OCR "MaxMichael" als ein Wort erkennt:
    # _compact("max") = "max"
    # _compact("maxmichael") = "maxmichael"
    # "max" in "maxmichael" → True
    if _compact(first) in _compact(chunk_norm):
        return True

    # ── Ebene 3: Umlaut-Varianten ──
    # "jürgen" → Varianten: ["jurgen", "juergen"]
    # Prüfe jede Variante als Token und Compact
    for v in _variants_for_umlaut_translit(first):
        if v in chunk_norm.split() or _compact(v) in _compact(chunk_norm):
            return True

    return False


def last_name_matches_flexible(form_nachname: str, chunk_text: str) -> bool:
    """
    Prüft, ob der Nachname aus dem Antrag im gegebenen Textausschnitt vorkommt.

    Matching-Strategie:
        1. Alle Tokens aus dem Antrags-Nachnamen müssen im Text vorkommen.
           Antrag: "Muster Beispiel" → beide Tokens müssen da sein.

        2. Compact-Match für OCR-Fehler ("MusterBeispiel" als ein Wort).

        3. Umlaut-Varianten wie beim Vornamen.

    Parameter:
        form_nachname: Nachname aus dem Antrag (z.B. "Muster-Beispiel")
        chunk_text:    Textausschnitt aus dem PDF

    Rückgabe:
        True wenn der Nachname gefunden wurde.

    Unterschied zum Vornamen:
        Beim Vornamen reicht der ERSTE Token (Rufname).
        Beim Nachnamen müssen ALLE Tokens matchen (Doppelname = alle Teile).
    """
    form_norm = normalize_for_matching(form_nachname)
    if not form_norm:
        return False

    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    # Nachname in Tokens splitten (Bindestriche werden bei Normalisierung
    # zu Leerzeichen, daher: "Muster-Beispiel" → ["muster", "beispiel"])
    form_tokens = form_norm.split()
    chunk_tokens = set(chunk_norm.split())

    # ── Ebene 1: Alle Tokens vorhanden? ──
    # Antrag: ["muster", "beispiel"]
    # Chunk:  {"karteninhaber", "muster", "beispiel", "kund:innen", ...}
    # all(["muster" in chunk, "beispiel" in chunk]) → True ✓
    if all(t in chunk_tokens for t in form_tokens):
        return True

    # ── Ebene 2: Compact-Match (OCR-Fehler) ──
    # "musterbeispiel" in "karteninhabermusterbeispielkund" → True
    if _compact(form_norm) in _compact(chunk_norm):
        return True

    # ── Ebene 3: Umlaut-Varianten ──
    for v in _variants_for_umlaut_translit(form_norm):
        if _compact(v) in _compact(chunk_norm):
            return True

    return False


# =============================================================================
# 2b) MARKER-BASIERTES NAMENS-MATCHING
# =============================================================================
#
# Das Herzstück der Namens-Validierung. Statt den ganzen PDF-Text nach
# dem Namen abzusuchen (Gefahr: Firmenname matcht), suchen wir NUR
# im Umfeld eines bestimmten Markers:
#
#   Rechnungen:           "Karteninhaber" → 12 Zeilen Fenster
#   Zahlungsbestätigungen: "für"          → 4 Zeilen Fenster
#
# Das Fenster (window_lines) definiert, wie viele Zeilen ab dem Marker
# in den "Chunk" genommen werden. Der Name muss innerhalb dieses
# Chunks vorkommen.
#
# Beispiel:
#   Zeile 15: "Karteninhaber:in:"          ← Marker gefunden
#   Zeile 16: "Erika Musterfrau"           ← Name im Chunk (15-27)
#   Zeile 17: "Kund:innennr: 12345"
#   ...

def name_match_near_markers(
    text: str,
    form_vorname: str,
    form_nachname: str,
    markers: list[tuple[list[str], int]],
) -> tuple[bool, str | None]:
    """
    Prüft, ob Vor- UND Nachname im Umfeld eines Markers vorkommen.

    Parameter:
        text:           Gesamter PDF-Text
        form_vorname:   Vorname aus dem Antrag (z.B. "Erika")
        form_nachname:  Nachname aus dem Antrag (z.B. "Musterfrau")
        markers:        Liste von (marker_list, window_lines)-Tupeln.

            marker_list:   Mögliche Marker-Wörter (normalisiert).
                           Mehrere Varianten für OCR-Robustheit.
            window_lines:  Wie viele Zeilen ab dem Marker in den Chunk nehmen.

            Beispiele:
                [(["karteninhaber"], 12)]  → Rechnungen
                [(["fur", "fuer"], 4)]     → Zahlungsbestätigungen

    Rückgabe:
        (match_ok, context_chunk)

        match_ok:      True wenn Vor- UND Nachname im Marker-Fenster gefunden
        context_chunk: Der Textausschnitt um den ersten Marker (für Debug/UI).
                       Auch bei Nicht-Match wird der erste gefundene Marker-
                       Chunk zurückgegeben, damit die UI zeigen kann, was
                       das Programm "gesehen" hat.

    Ablauf:
        1. Text in Zeilen splitten
        2. Für jede Zeile prüfen: Enthält sie einen Marker?
        3. Falls ja: Chunk = diese Zeile + die nächsten N Zeilen
        4. Vor- und Nachname im Chunk suchen (flexible Matcher)
        5. Wenn beide gefunden → (True, chunk)
        6. Kein Match bei keinem Marker → (False, erster Chunk für Debug)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Speichert den Chunk um den ERSTEN gefundenen Marker (für Debug)
    first_context: str | None = None

    for i, raw in enumerate(lines):
        raw_norm = normalize_for_matching(raw)

        # Prüfe jeden Marker-Typ
        for marker_list, window_lines in markers:
            # Ist einer der Marker in dieser Zeile?
            if any(_contains_marker(raw_norm, m) for m in marker_list):
                # Chunk: Zeilen i bis i+window_lines zusammenfügen
                chunk = " ".join(lines[i : i + window_lines])

                # Ersten Kontext merken (für Debug, auch wenn kein Match)
                if first_context is None:
                    first_context = chunk

                # Vor- und Nachname im Chunk prüfen
                fn_ok = first_name_matches_flexible(form_vorname, chunk)
                ln_ok = last_name_matches_flexible(form_nachname, chunk)

                if fn_ok and ln_ok:
                    return True, chunk

    # Kein Match gefunden → False + ersten Kontext (falls Marker gefunden wurde)
    return False, first_context


# =============================================================================
# 3) ZEITRAUM-EXTRAKTION AUS PDF-TEXT
# =============================================================================
#
# Drei verschiedene Funktionen für unterschiedliche Dokumenttypen und
# Zeitraum-Arten:
#
#   extract_period_from_zahlungsbestaetigung()
#       → "gilt 21. Dez 2024 - 20. Dez 2025" (Textformat)
#
#   extract_period_from_rechnung()
#       → "Gültigkeitszeitraum: 15.09.2024 - 14.09.2025" (Punkt-Format)
#       → Fallback: "Leistungszeitraum: ..."
#
#   _extract_leistungszeitraum()
#       → Nur der Leistungszeitraum (für Monats-Prüfung)
#
# Alle Funktionen geben ein Tupel (von_string, bis_string) zurück,
# das dann separat geparst wird.

def extract_period_from_zahlungsbestaetigung(text: str) -> tuple[str | None, str | None]:
    """
    Extrahiert den Gültigkeitszeitraum aus einer Zahlungsbestätigung.

    Sucht nach dem Marker "gilt" und extrahiert daraus zwei Datumsangaben
    im Textformat: "gilt 21. Dez 2024 - 20. Dez 2025"

    OCR-Robustheit:
        - "gilt" wird per _contains_marker() gesucht (findet auch "g i l t")
        - Datumswerte können auf der nächsten Zeile stehen (OCR-Zeilenumbruch),
          daher werden 3 Zeilen als Chunk zusammengefasst.

    Rückgabe:
        (von_str, bis_str) — Roh-Strings der beiden Datumsangaben
        (None, None) — wenn kein passender Zeitraum gefunden wurde
    """
    lines = text.splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        ln = normalize_for_matching(line)

        # Marker "gilt" gefunden?
        if _contains_marker(ln, "gilt"):
            # Chunk: aktuelle Zeile + 2 Folgezeilen (OCR kann umbrechen)
            chunk_raw = " ".join(lines[i : i + 3])

            # Zwei Textformat-Datumsangaben suchen
            matches = re.findall(DATE_PATTERN_TEXT, chunk_raw)
            if len(matches) >= 2:
                return matches[0].strip(), matches[1].strip()

    return None, None


def extract_period_from_rechnung(text: str) -> tuple[str | None, str | None]:
    """
    Extrahiert den Gültigkeitszeitraum aus einer Jahres-/Monatsrechnung.

    Sucht nach Markern in dieser Priorität:
        1. "Gültigkeitszeitraum" / "Gültigkeit"  (primär)
        2. "Leistungszeitraum"                    (Fallback für Alt-Layouts)

    Das Ergebnis sind zwei Punkt-Format-Datumsangaben:
        "15.09.2024", "14.09.2025"

    OCR-Robustheit:
        - Marker werden normalisiert (Umlaute entfernt): "gültigkeitszeitraum" → "gultigkeitszeitraum"
        - Datumsangaben werden per clean_date_dot() repariert
        - Chunk über 3 Zeilen (OCR-Zeilenumbrüche)
        - Suche geht bis zu 80 Zeilen nach dem Marker weiter
          (bei manchen Layouts steht der Zeitraum weit unten)

    Rückgabe:
        (von_str, bis_str) — Bereinigte Datums-Strings ("DD.MM.YYYY")
        (None, None) — wenn nichts gefunden
    """
    lines = text.splitlines()

    # Marker-Varianten (normalisiert, ohne Umlaute)
    # "gültigkeitszeitraum" → "gultigkeitszeitraum" nach normalize_for_matching()
    markers = ["gultigkeitszeitraum", "gultigkeit"]

    # ── Primär: Nach "Gültigkeitszeitraum" / "Gültigkeit" suchen ──
    for i, line in enumerate(lines):
        ln = normalize_for_matching(line)

        if any(m in ln for m in markers):
            # Ab dem Marker bis zu 80 Zeilen weiter suchen
            # (bei manchen Layouts steht der Zeitraum in einer Tabelle weiter unten)
            for j in range(i, min(i + 80, len(lines))):
                chunk = " ".join(lines[j:j + 3])
                matches = re.findall(DATE_PATTERN_DOT, chunk)
                if len(matches) >= 2:
                    return clean_date_dot(matches[0]), clean_date_dot(matches[1])

    # ── Fallback: "Leistungszeitraum" (Alt-Layouts) ──
    for i, line in enumerate(lines):
        if normalize_for_matching(line).startswith("leistungszeitraum"):
            chunk = " ".join(lines[i:i + 3])
            matches = re.findall(DATE_PATTERN_DOT, chunk)
            if len(matches) >= 2:
                return clean_date_dot(matches[0]), clean_date_dot(matches[1])

    return None, None


def _extract_leistungszeitraum(text: str) -> tuple[datetime | None, datetime | None]:
    """
    Extrahiert NUR den Leistungszeitraum (NICHT den Gültigkeitszeitraum).

    Hintergrund:
        Eine Rechnung hat zwei Zeiträume:
        - Gültigkeitszeitraum: Die gesamte Laufzeit des Tickets
          (z.B. 15.09.2024 - 14.09.2025 = 12 Monate)
        - Leistungszeitraum: Der Abrechnungszeitraum dieser Rechnung
          (z.B. 15.09.2024 - 14.10.2024 = 1 Monat bei Monatsrechnung)
          (z.B. 15.09.2024 - 14.09.2025 = 12 Monate bei Jahresrechnung)

    Wird verwendet von:
        - validate_rechnung():        Dauer in Monaten berechnen (leist_months)
        - validate_monatsrechnung():  Prüfen, ob Leistung innerhalb Gültigkeit liegt
        - decision_engine:            Reklassifizierung (< 10 Monate → Monatsrechnung)

    Suche:
        Gezielt nach Zeilen, die mit "Leistungszeitraum" beginnen.
        Im Chunk (5 Zeilen) nach dem Muster "DD.MM.YYYY - DD.MM.YYYY" suchen.

    Rückgabe:
        (start_datetime, end_datetime) — Beide als datetime
        (None, None) — wenn kein Leistungszeitraum gefunden

    Hinweis:
        Findet nur den ERSTEN Leistungszeitraum im Text. Bei Multi-Page-PDFs
        (mehrere Rechnungen in einer PDF) muss der Text VORHER seitenweise
        gesplittet werden (decision_engine macht das über text.split('\\f')).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if normalize_for_matching(line).startswith("leistungszeitraum"):
            # Chunk: 5 Zeilen ab dem Marker (manchmal steht das Datum auf der Folgezeile)
            chunk = " ".join(lines[i:i + 5])

            # Muster: "DD.MM.YYYY - DD.MM.YYYY" (mit optionalen Leerzeichen)
            m = re.search(rf"({DATE_PATTERN_DOT})\s*-\s*({DATE_PATTERN_DOT})", chunk)
            if m:
                return parse_pdf_date_dot(m.group(1)), parse_pdf_date_dot(m.group(2))

            # Zeile enthielt "Leistungszeitraum" aber kein Datum gefunden → aufhören
            break

    return None, None


def _months_between(start: datetime, end: datetime) -> int:
    """
    Berechnet die Anzahl Monate zwischen zwei Datumswerten.

    Formel: (Jahres-Differenz × 12) + Monats-Differenz

    Beispiele:
        01.09.2024 → 01.09.2025 = 12 Monate (Jahresrechnung)
        01.09.2024 → 01.10.2024 = 1 Monat   (Monatsrechnung)
        01.09.2024 → 01.12.2024 = 3 Monate  (Quartalsrechnung)

    Verwendet in:
        - validate_rechnung() → leist_months
        - decision_engine → reclassify_short_jahresrechnungen()
          (wenn leist_months < 10 → Reklassifizierung zu Monatsrechnung)
    """
    return (end.year - start.year) * 12 + (end.month - start.month)


# =============================================================================
# 4) DEBUG: NAMEN-EXTRAKTION AUS RECHNUNG
# =============================================================================
#
# Diese Funktion wird NICHT für die Validierung verwendet.
# Sie dient nur der Debug-Ausgabe, um zu zeigen, welchen Namen
# das System aus der Rechnung extrahiert hat.
#
# Warnung: Kann Firmenname statt Personenname liefern, z.B.:
#   "Karteninhaber:in: One Mobility Ticketing GmbH"
#   → extrahiert "One Mobility Ticketing GmbH" ← FALSCH
#
# Deshalb verwenden wir für die VALIDIERUNG stattdessen
# name_match_near_markers(), das den Antrags-Namen im Umfeld
# des Markers sucht, statt den Namen aus dem Text zu "erraten".

def extract_name_from_rechnung(text: str) -> str | None:
    """
    OPTIONAL / DEBUG: Versucht den Karteninhaber-Namen aus dem Text zu extrahieren.

    Regex sucht nach:
        "Karteninhaber:in: <Name>"
        "Karteninhaber: <Name>"

    Der extrahierte Name wird am ersten Störwort abgeschnitten:
        "Musterfrau Erika Kund:innennr" → "Musterfrau Erika"

    Störwörter: "kund", "kunden", "kund:inn", "nr", "rechnungsdatum",
    "fallig", "menge", "beschreibung"

    Rückgabe:
        Normalisierter Name oder None.

    NICHT für Validierung verwenden — nur für Debug-Ausgaben.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full = " ".join(lines)

    # Regex: "Karteninhaber" (optional ":in") + ":" + Name
    m = re.search(
        r"karteninhaber(?:\:in)?\s*:\s*([A-Za-zÄÖÜäöüß\- ]{2,})",
        full,
        flags=re.IGNORECASE,
    )
    if m:
        name_raw = m.group(1).strip()

        # Am ersten Störwort abschneiden (z.B. "Kund:innen", "Nr", "Rechnungsdatum")
        # Das sind Wörter, die NACH dem Namen in der gleichen Zeile stehen können.
        name_raw = re.split(
            r"\s+(kund|kunden|kund:inn|kundinnen|kund:innen|nr|rechnungsdatum|fallig|menge|beschreibung)\b",
            name_raw,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

        return normalize_for_matching(name_raw) or None

    return None


# =============================================================================
# 5) VALIDIERUNGSFUNKTIONEN
# =============================================================================
#
# Drei Funktionen — eine pro Dokumenttyp. Jede gibt ein dict zurück
# mit allen Prüfergebnissen. Die Decision Engine (build_invoice_decision)
# ruft je nach doc_type die passende Funktion auf:
#
#   doc_type == "jahresrechnung"        → validate_rechnung()
#   doc_type == "monatsrechnung"        → validate_monatsrechnung()
#   doc_type == "zahlungsbestaetigung"  → validate_zahlungsbestaetigung()
#
# Gemeinsames Muster aller drei Funktionen:
#   1. Name prüfen (name_match_near_markers)
#   2. Zeitraum aus PDF extrahieren
#   3. Zeitraum aus Antrag laden
#   4. Vergleichen
#   5. Ergebnis-dict aufbauen
#   6. Optional: Debug-Ausgaben (verbose=True)


# ─────────────────────────────────────────────────────────────────────────────
# 5a) ZAHLUNGSBESTÄTIGUNG
# ─────────────────────────────────────────────────────────────────────────────

def validate_zahlungsbestaetigung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine Zahlungsbestätigung gegen Antragsdaten.

    Prüfungen:
        1. NAME:    Kommt "Vorname Nachname" im Umfeld von "für" vor?
                    Marker: "für" (normalisiert: "fur")
                    Fenster: 4 Zeilen ab dem Marker

        2. ZEITRAUM: Stimmt "gilt <von> - <bis>" mit dem Antrag überein?
                     Format: "gilt 21. Dez 2024 - 20. Dez 2025"

    Rückgabe (dict):
        name_ok:         bool       — Name gefunden?
        name_context:    str | None — Textausschnitt um "für" (Debug)
        period_ok:       bool       — Zeitraum stimmt überein?
        period_pdf_raw:  dict       — Rohe Datums-Strings aus PDF
        period_pdf_iso:  dict       — Geparste Daten als ISO-Strings
        period_form_iso: dict       — Antrags-Daten als ISO-Strings
        all_ok:          bool       — Gesamt: name_ok AND period_ok
        reason:          str        — (nur bei Fehler) Grund für Ablehnung
    """

    # ── Name prüfen ──
    # Marker: "für" (auf Zahlungsbestätigungen steht "für Erika Musterfrau")
    # "für" wird zu "fur" nach normalize_for_matching (Umlaute werden entfernt)
    # "fuer" als Alternative, falls OCR "ü" als "ue" liest
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["fur", "fuer"], 4) ],   # 4 Zeilen Fenster (Zahlung ist kompakt)
    )

    # ── Zeitraum aus PDF extrahieren ──
    von_str, bis_str = extract_period_from_zahlungsbestaetigung(text)
    von_pdf = parse_pdf_date_text(von_str)     # "21. Dez 2024" → datetime
    bis_pdf = parse_pdf_date_text(bis_str)

    # ── Zeitraum aus Antrag laden ──
    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    # ── Fehlende Antragsdaten? ──
    # Wenn gilt_von oder gilt_bis im Antrag fehlen, können wir den
    # Zeitraum nicht prüfen → all_ok = False mit Begründung
    if von_json is None or bis_json is None:
        result = {
            "doc_type": "zahlungsbestaetigung",
            "name_ok": bool(name_ok),
            "name_context": name_context,
            "period_ok": False,
            "period_pdf_raw": {"von": von_str, "bis": bis_str},
            "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},
            "period_form_iso": {"von": _fmt_iso(von_json), "bis": _fmt_iso(bis_json)},
            "all_ok": False,
            "reason": "gilt_von/gilt_bis fehlen im Antrag",
        }
        if verbose:
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag → Zeitraum-Prüfung nicht möglich")
        return result

    # ── Zeitraum vergleichen ──
    # Beide Seiten (PDF und Antrag) müssen gesetzt sein UND exakt übereinstimmen.
    # .date() wird verwendet, damit Uhrzeiten ignoriert werden.
    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    # ── Ergebnis aufbauen ──
    result = {
        "doc_type": "zahlungsbestaetigung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "period_ok": bool(period_ok),
        "period_pdf_raw": {"von": von_str, "bis": bis_str},                       # Rohdaten (Debug)
        "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},   # Geparst (Debug)
        "period_form_iso": {"von": von_json.date().isoformat(), "bis": bis_json.date().isoformat()},
        "all_ok": bool(name_ok and period_ok),
    }

    # ── Debug-Ausgaben ──
    if verbose:
        print("Name im Antrag:", form_data.get("vorname"), form_data.get("familienname"))
        print("Name-Match Zahlungsbestätigung (near 'für'):", result["name_ok"])
        print("Zeitraum im Antrag:", von_json.date(), "-", bis_json.date())
        print("Zeitraum auf Zahlungsbestätigung:",
              von_pdf.date() if von_pdf else None, "-",
              bis_pdf.date() if bis_pdf else None)
        print("Zeitraum-Match Zahlungsbestätigung:", result["period_ok"])
        print("DEBUG Zeitraum-Roh:", von_str, bis_str)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5b) JAHRESRECHNUNG
# ─────────────────────────────────────────────────────────────────────────────

def validate_rechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine Jahresrechnung gegen Antragsdaten.

    Prüfungen:
        1. NAME:              Kommt "Vorname Nachname" im Umfeld von "Karteninhaber" vor?
        2. GÜLTIGKEITSZEITRAUM: Stimmt er mit dem Antrag überein?
        3. LEISTUNGSZEITRAUM:  Wird extrahiert und Dauer in Monaten berechnet.

    Besonderheit — Leistungszeitraum vs. Gültigkeitszeitraum:
        Eine Rechnung hat ZWEI Zeiträume:

        Gültigkeitszeitraum = Laufzeit des gesamten Tickets
            z.B. 15.09.2024 - 14.09.2025 (12 Monate)
            → Muss mit dem Antrag übereinstimmen (period_ok)

        Leistungszeitraum = Abrechnungszeitraum DIESER Rechnung
            z.B. 15.09.2024 - 14.09.2025 (12 Monate bei Jahresrechnung)
            z.B. 15.09.2024 - 14.10.2024 (1 Monat bei Kurzrechnung)
            → Wird in leist_months umgerechnet
            → Decision Engine nutzt leist_months für Reklassifizierung:
              Wenn < 10 Monate → wird als Monatsrechnung behandelt

    Rückgabe (dict):
        name_ok:          bool       — Name (Karteninhaber) matcht?
        name_context:     str | None — Textausschnitt (Debug)
        period_ok:        bool       — Gültigkeitszeitraum == Antrag?
        leist_months:     int | None — Dauer des Leistungszeitraums in Monaten
        leist_month_key:  str | None — z.B. "2024-09" (für Monatsgruppierung)
        leist_in_guelt:   bool       — Leistungszeitraum ⊆ Gültigkeitszeitraum?
        all_ok:           bool       — name_ok AND period_ok
        dbg_extracted_name: str | None — Debug: extrahierter Name (kann Firma sein!)
    """

    # ── 1) Name prüfen ──
    # Marker: "Karteninhaber" (auf Rechnungen steht "Karteninhaber:in: Erika Musterfrau")
    # 12 Zeilen Fenster, weil zwischen Marker und Name manchmal
    # noch andere Felder stehen (Kund:innennr, Rechnungsnr, etc.)
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],
    )

    # Debug-Name extrahieren (NUR für Ausgabe, NICHT für Validierung)
    dbg_name = extract_name_from_rechnung(text)

    # ── 2) Gültigkeitszeitraum aus PDF extrahieren ──
    von_str, bis_str = extract_period_from_rechnung(text)
    von_pdf = parse_pdf_date_dot(von_str)
    bis_pdf = parse_pdf_date_dot(bis_str)

    # ── 3) Gültigkeitszeitraum aus Antrag laden ──
    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    # ── Fehlende Antragsdaten? ──
    if von_json is None or bis_json is None:
        l_von, l_bis = _extract_leistungszeitraum(text)
        result = {
            "doc_type": "jahresrechnung",
            "name_ok": bool(name_ok),
            "name_context": name_context,
            "period_ok": False,
            "period_pdf_raw": {"von": von_str, "bis": bis_str},
            "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},
            "period_form_iso": {"von": _fmt_iso(von_json), "bis": _fmt_iso(bis_json)},
            "leist_pdf_iso": {"von": _fmt_iso(l_von), "bis": _fmt_iso(l_bis)},
            "leist_months": None,
            "leist_month_key": None,
            "leist_in_guelt": False,
            "dbg_extracted_name": dbg_name,
            "all_ok": False,
            "reason": "gilt_von/gilt_bis fehlen im Antrag",
        }
        if verbose:
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag → Zeitraum-Prüfung nicht möglich")
        return result

    # ── 4) Gültigkeitszeitraum vergleichen ──
    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    # ── 5) Leistungszeitraum extrahieren und analysieren ──
    l_von, l_bis = _extract_leistungszeitraum(text)

    # Dauer in Monaten berechnen
    # Beispiele:
    #   12 Monate → echte Jahresrechnung
    #   1 Monat  → wird von decision_engine als Monatsrechnung reklassifiziert
    leist_months = _months_between(l_von, l_bis) if (l_von and l_bis) else None

    # Monatsschlüssel: Jahr-Monat des Leistungsbeginns
    # Wird für Gruppierung verwendet, wenn die Rechnung als
    # Monatsrechnung reklassifiziert wird
    leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}" if l_von else None

    # Prüfen, ob der Leistungszeitraum innerhalb der Ticket-Gültigkeit liegt
    leist_in_guelt = (
        l_von is not None and l_bis is not None
        and von_json.date() <= l_von.date() <= l_bis.date() <= bis_json.date()
    )

    # ── 6) Ergebnis aufbauen ──
    result = {
        "doc_type": "jahresrechnung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "period_ok": bool(period_ok),
        "period_pdf_raw": {"von": von_str, "bis": bis_str},
        "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},
        "period_form_iso": {"von": von_json.date().isoformat(), "bis": bis_json.date().isoformat()},
        "leist_pdf_iso": {"von": _fmt_iso(l_von), "bis": _fmt_iso(l_bis)},
        "leist_months": leist_months,
        "leist_month_key": leist_month_key,
        "leist_in_guelt": bool(leist_in_guelt),
        "dbg_extracted_name": dbg_name,
        "all_ok": bool(name_ok and period_ok),
    }

    # ── 7) Debug-Ausgaben ──
    if verbose:
        print("Name im Antrag:", form_data.get("vorname"), form_data.get("familienname"))
        print("Name-Match Rechnung (near Karteninhaber):", result["name_ok"])
        if dbg_name:
            print("DEBUG extrahierter Name (kann Firma sein):", dbg_name)

        print("Zeitraum im Antrag:", von_json.date(), "-", bis_json.date())
        print("Zeitraum auf Rechnung:",
              von_pdf.date() if von_pdf else None, "-",
              bis_pdf.date() if bis_pdf else None)
        print("Zeitraum-Match Rechnung:", result["period_ok"])

        print("Leistungszeitraum:",
              _fmt_dot(l_von), "-", _fmt_dot(l_bis),
              f"({leist_months} Monate)" if leist_months is not None else "(nicht erkannt)")
        print("Leistungszeitraum innerhalb Gültigkeit:", result["leist_in_guelt"])

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5c) MONATSRECHNUNG
# ─────────────────────────────────────────────────────────────────────────────

def validate_monatsrechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine Monatsrechnung gegen Antragsdaten.

    Eine Monatsrechnung ist eine Einzelrechnung für einen Abrechnungsmonat.
    Drei Monatsrechnungen für VERSCHIEDENE Monate = Rechnungsnachweis OK.

    Prüfungen:
        1. NAME:    Kommt "Vorname Nachname" im Umfeld von "Karteninhaber" vor?
        2. GÜLTIGKEIT: Stimmt der Gültigkeitszeitraum mit dem Antrag überein?
                       (= Gesamtlaufzeit des Tickets)
        3. LEISTUNG:   Liegt der Leistungszeitraum INNERHALB der Gültigkeit?
                       (= der eine Abrechnungsmonat dieser Rechnung)

    Unterschied zur Jahresrechnung:
        - Jahresrechnung: leist_months wird berechnet, aber all_ok hängt
          nur von name_ok AND period_ok ab.
        - Monatsrechnung: all_ok = name_ok AND guelt_ok AND leist_ok
          (alle drei müssen stimmen!)

    leist_month_key:
        Der Monatsschlüssel (z.B. "2024-09") wird von der Decision Engine
        verwendet, um zu zählen, wie viele VERSCHIEDENE Monate abgedeckt sind.
        Beispiel: 3 Rechnungen mit keys "2024-09", "2024-10", "2024-11"
        → 3 verschiedene Monate → monatsrechnungen_ok = True

    Multi-Page-PDFs:
        Wenn mehrere Monatsrechnungen in EINER PDF stehen, wird der Text
        von der Decision Engine seitenweise gesplittet (text.split('\\f')).
        Diese Funktion bekommt dann nur den Text EINER Seite.

    Rückgabe (dict):
        name_ok:          bool       — Karteninhaber matcht?
        name_context:     str | None — Textausschnitt (Debug)
        guelt_ok:         bool       — Gültigkeitszeitraum == Antrag?
        leist_ok:         bool       — Leistungszeitraum ⊆ Gültigkeit?
        guelt_pdf_raw/iso: dict      — Gültigkeitsdaten (roh/ISO)
        form_iso:         dict       — Antrags-Daten (ISO)
        leist_pdf_iso:    dict       — Leistungszeitraum (ISO)
        leist_month_key:  str | None — z.B. "2024-09"
        all_ok:           bool       — Gesamt: name_ok AND guelt_ok AND leist_ok
    """

    # ── 1) Name prüfen ──
    # Gleicher Marker wie bei Jahresrechnung: "Karteninhaber"
    # 12 Zeilen Fenster (gleiche Rechnungslayouts)
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],
    )

    # ── 2) Gültigkeit (Gesamtlaufzeit des Tickets) prüfen ──
    # Aus der Rechnung: "Gültigkeitszeitraum: 15.09.2024 - 14.09.2025"
    g_von_s, g_bis_s = extract_period_from_rechnung(text)
    g_von = parse_pdf_date_dot(g_von_s)
    g_bis = parse_pdf_date_dot(g_bis_s)

    # Aus dem Antrag: gilt_von und gilt_bis
    a_von = parse_form_datetime(form_data.get("gilt_von", ""))
    a_bis = parse_form_datetime(form_data.get("gilt_bis", ""))

    # ── Fehlende Antragsdaten? ──
    if a_von is None or a_bis is None:
        l_von, l_bis = _extract_leistungszeitraum(text)
        leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}" if l_von else None
        result = {
            "doc_type": "monatsrechnung",
            "name_ok": bool(name_ok),
            "name_context": name_context,
            "guelt_ok": False,
            "leist_ok": False,
            "guelt_pdf_raw": {"von": g_von_s, "bis": g_bis_s},
            "guelt_pdf_iso": {"von": _fmt_iso(g_von), "bis": _fmt_iso(g_bis)},
            "form_iso": {"von": _fmt_iso(a_von), "bis": _fmt_iso(a_bis)},
            "leist_pdf_iso": {"von": _fmt_iso(l_von), "bis": _fmt_iso(l_bis)},
            "leist_month_key": leist_month_key,
            "all_ok": False,
            "reason": "gilt_von/gilt_bis fehlen im Antrag",
        }
        if verbose:
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag → Zeitraum-Prüfung nicht möglich")
        return result

    # ── Gültigkeit vergleichen ──
    # Beide Seiten müssen exakt übereinstimmen (Datum ohne Uhrzeit)
    guelt_ok = (
        g_von is not None and g_bis is not None
        and g_von.date() == a_von.date()
        and g_bis.date() == a_bis.date()
    )

    # ── 3) Leistungszeitraum (ein einzelner Abrechnungsmonat) prüfen ──
    l_von, l_bis = _extract_leistungszeitraum(text)

    # Leistungszeitraum muss KOMPLETT innerhalb der Ticket-Gültigkeit liegen:
    #   a_von ≤ l_von ≤ l_bis ≤ a_bis
    #
    # Beispiel (OK):
    #   Gültigkeit:    15.09.2024 - 14.09.2025
    #   Leistung:      15.10.2024 - 14.11.2024   → liegt komplett drin ✓
    #
    # Beispiel (NICHT OK):
    #   Gültigkeit:    15.09.2024 - 14.09.2025
    #   Leistung:      15.09.2023 - 14.10.2023   → liegt VOR der Gültigkeit ✗
    leist_ok = (
        l_von is not None and l_bis is not None
        and a_von.date() <= l_von.date() <= l_bis.date() <= a_bis.date()
    )

    # ── 4) Monatsschlüssel bauen ──
    # Jahr-Monat des Leistungszeitraum-BEGINNS.
    # Wird von der Decision Engine verwendet, um einzigartige Monate zu zählen.
    #
    # Beispiel:
    #   Leistung 15.09.2024 - 14.10.2024 → leist_month_key = "2024-09"
    #   Leistung 15.10.2024 - 14.11.2024 → leist_month_key = "2024-10"
    #
    # In der Decision Engine:
    #   valid_months = {"2024-09", "2024-10", "2024-11"}  → 3 verschiedene → OK
    leist_month_key = None
    if l_von is not None:
        leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}"

    # ── 5) Ergebnis aufbauen ──
    result = {
        "doc_type": "monatsrechnung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "guelt_ok": bool(guelt_ok),
        "leist_ok": bool(leist_ok),
        "guelt_pdf_raw": {"von": g_von_s, "bis": g_bis_s},
        "guelt_pdf_iso": {"von": _fmt_iso(g_von), "bis": _fmt_iso(g_bis)},
        "form_iso": {"von": a_von.date().isoformat(), "bis": a_bis.date().isoformat()},
        "leist_pdf_iso": {"von": _fmt_iso(l_von), "bis": _fmt_iso(l_bis)},
        "leist_month_key": leist_month_key,
        # Gesamt: ALLE drei Checks müssen bestehen
        # (Unterschied zur Jahresrechnung, wo leist_months nur informativ ist)
        "all_ok": bool(name_ok and guelt_ok and leist_ok),
    }

    # ── 6) Debug-Ausgaben ──
    if verbose:
        print("Name-Match Monatsrechnung (near Karteninhaber):", result["name_ok"])
        print("Gültigkeit Monatsrechnung:",
              _fmt_dot(g_von), "-", _fmt_dot(g_bis), "->", result["guelt_ok"])
        print("Leistungszeitraum Monatsrechnung:",
              _fmt_dot(l_von), "-", _fmt_dot(l_bis), "->", result["leist_ok"])
        print("Leistungs-Monatsschlüssel:", leist_month_key)

    return result