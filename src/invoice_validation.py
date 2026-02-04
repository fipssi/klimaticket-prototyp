"""
invoice_validation.py â€” Validierung von KlimaTicket-Rechnungsdokumenten
=======================================================================

ÃœBERBLICK
---------
Dieses Modul validiert die INHALTLICHEN Daten von Rechnungsdokumenten
gegen die Antragsdaten. Es ist der dritte Schritt in der Pipeline:

    PDF â†’ document_loader â†’ Text
    Text â†’ document_classifier â†’ ("jahresrechnung", 0.92)
    Text + Antragsdaten â†’ invoice_validation â†’ {name_ok, period_ok, ...}  â† HIER
    Alle Ergebnisse â†’ decision_engine â†’ Gesamtentscheidung


DREI DOKUMENTTYPEN WERDEN VALIDIERT
------------------------------------
Jeder Typ hat eine eigene Validierungsfunktion:

    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â”‚ Dokumenttyp                  â”‚ Validierungsfunktion           â”‚
    â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
    â”‚ Jahresrechnung               â”‚ validate_rechnung()            â”‚
    â”‚ Monatsrechnung               â”‚ validate_monatsrechnung()      â”‚
    â”‚ ZahlungsbestÃ¤tigung          â”‚ validate_zahlungsbestaetigung()â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

    Alle drei prÃ¼fen:
        1. NAME:    Stimmt der Karteninhaber mit dem Antrag Ã¼berein?
        2. ZEITRAUM: Stimmt der GÃ¼ltigkeitszeitraum mit dem Antrag Ã¼berein?

    ZusÃ¤tzlich bei Jahres-/Monatsrechnungen:
        3. LEISTUNGSZEITRAUM: Liegt er innerhalb der GÃ¼ltigkeit?


NAMENS-MATCHING â€” WARUM SO KOMPLEX?
-------------------------------------
Das Matching von Namen ist das schwierigste Problem in diesem Modul,
weil OCR-Text und Antragsdaten auf viele Weisen voneinander abweichen:

    Problem                 â”‚ Beispiel
    â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    Umlaute / Transliterationâ”‚ "JÃ¼rgen" vs. "Juergen" vs. "Jurgen"
    ÃŸ â†’ ss                  â”‚ "GrÃ¶ÃŸer" vs. "Groesser"
    OCR ohne Leerzeichen    â”‚ "MaxMichael" statt "Max Michael"
    Bindestrich-Varianten   â”‚ "Muster-Beispiel" vs. "Muster Beispiel"
    Mehrfach-Vornamen       â”‚ Antrag "Max", PDF "Max Michael"
    Diakritika              â”‚ "AndrÃ©" vs. "Andre"
    â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    Strategie: Wir "erraten" den Namen NICHT aus dem PDF (Gefahr: Firmenname
    wie "One Mobility Ticketing GmbH" wird fÃ¤lschlich als Name erkannt).
    Stattdessen prÃ¼fen wir, ob der Name aus dem ANTRAG im Umfeld eines
    klaren Markers vorkommt:
        - Rechnungen:           "Karteninhaber"  (12 Zeilen Fenster)
        - ZahlungsbestÃ¤tigungen: "fÃ¼r"           (4 Zeilen Fenster)


ZEITRAUM-EXTRAKTION â€” OCR-ROBUSTHEIT
--------------------------------------
OCR-Text enthÃ¤lt typische Fehler bei Datumsangaben:

    "01 .04.2023"   â†’ Leerzeichen vor dem Punkt
    "31.O3.2024"    â†’ Buchstabe O statt Ziffer 0
    "1O.12.2024"    â†’ O statt 0 mitten in einer Zahl

    Alle diese FÃ¤lle werden von clean_date_dot() repariert.


ABHÃ„NGIGKEITEN
--------------
    utils.py  â€” normalize_for_matching(), _compact(), _variants_for_umlaut_translit()
                (Shared Hilfsfunktionen, auch von registration_validation.py genutzt)
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Tuple, Optional

try:
    from src.utils import normalize_for_matching, _compact, _variants_for_umlaut_translit
except ImportError:
    from utils import normalize_for_matching, _compact, _variants_for_umlaut_translit


# =============================================================================
# 0) LOKALE HILFSFUNKTIONEN
# =============================================================================
#
# Kleine Helfer, die nur innerhalb dieses Moduls verwendet werden.
# Sie sind mit _ prefixed, um zu signalisieren, dass sie nicht fÃ¼r
# den Import durch andere Module gedacht sind.

def _contains_marker(line_norm: str, marker: str) -> bool:
    """
    PrÃ¼ft robust, ob ein Marker-Wort in einer normalisierten Textzeile vorkommt.

    Warum nicht einfach `marker in line`?
        OCR kann Buchstaben mit Leerzeichen trennen:
            "fÃ¼r"  â†’ "f Ã¼ r"  â†’ nach Normalisierung: "f u r"
            "gilt" â†’ "g i l t"

        Deshalb prÃ¼fen wir zusÃ¤tzlich per _compact() (entfernt alle Leerzeichen):
            _compact("f u r") = "fur"  â†’ "fur" in "fur" = True âœ“

    Parameter:
        line_norm: Bereits normalisierte Zeile (via normalize_for_matching)
        marker:    Gesuchtes Wort, ebenfalls normalisiert (z.B. "fur", "gilt")

    RÃ¼ckgabe:
        True wenn der Marker gefunden wurde (normal oder compact)
    """
    return (marker in line_norm) or (marker in _compact(line_norm))


def _fmt_iso(dt: datetime | None) -> str | None:
    """
    Formatiert ein datetime als ISO-String (YYYY-MM-DD) fÃ¼r die Decision Engine.

    Beispiel: datetime(2024, 9, 15) â†’ "2024-09-15"
    None â†’ None (kein Datum gefunden)
    """
    return dt.date().isoformat() if dt else None


def _fmt_dot(dt: datetime | None) -> str | None:
    """
    Formatiert ein datetime als deutsches Datum (DD.MM.YYYY) fÃ¼r Debug-Ausgaben.

    Beispiel: datetime(2024, 9, 15) â†’ "15.09.2024"
    None â†’ None
    """
    return dt.strftime("%d.%m.%Y") if dt else None


# =============================================================================
# 1) DATUM-PARSING: ANTRAGSDATEN
# =============================================================================
#
# Antragsdaten kommen aus dem Formular (Streamlit UI oder Excel/JSON).
# Das Datumsformat ist NICHT garantiert â€” verschiedene Quellen liefern:
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
        "2024-09-15"              â†’ ISO-Datum
        "2024-09-15T00:00:00"     â†’ ISO mit Uhrzeit
        "2024-09-15 00:00:00"     â†’ ISO mit Uhrzeit (Leerzeichen)
        "15.09.2024"              â†’ Deutsches Format (TT.MM.JJJJ)
        ""                        â†’ None (leer)
        None                      â†’ None

    RÃ¼ckgabe:
        datetime bei Erfolg, None bei leerem oder unbekanntem Format.

    Wichtig:
        Bei fehlenden Antragsdaten (None) bricht die Validierung nicht ab,
        sondern gibt {all_ok: False, reason: "gilt_von/gilt_bis fehlen"} zurÃ¼ck.
    """
    value = (value or "").strip()
    if not value:
        return None

    # Versuch 1: Python-natives ISO-Parsing (deckt "2024-09-15" und "2024-09-15T00:00:00" ab)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    # Versuch 2: Explizite Format-Strings fÃ¼r SonderfÃ¤lle
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Kein Format passt â†’ None (wird upstream als "fehlend" behandelt)
    return None


# =============================================================================
# 1b) DATUM-PARSING: ZAHLUNGSBESTÃ„TIGUNG (Textformat)
# =============================================================================
#
# ZahlungsbestÃ¤tigungen verwenden ein anderes Datumsformat als Rechnungen:
#   "gilt 21. Dez 2024 - 20. Dez 2025"
#
# Das ist ein deutsches Textformat mit abgekÃ¼rztem Monatsnamen.
# Python's strptime braucht englische Monatsnamen â†’ wir mappen erst.

# Regex fÃ¼r Textformat-Datumsangaben: "21. Dez 2024"
# Aufbau:
#   \d{1,2}       â†’ Tag (1-2 Ziffern)
#   \.             â†’ Punkt nach dem Tag
#   \s*            â†’ Optionale Leerzeichen
#   [A-Za-z...]{3} â†’ MonatsabkÃ¼rzung (3 Buchstaben, inkl. Umlaute fÃ¼r "JÃ¤n", "MÃ¤r")
#   \s*            â†’ Optionale Leerzeichen
#   \d{4}          â†’ Jahr (4 Ziffern)
DATE_PATTERN_TEXT = r"\d{1,2}\.\s*[A-Za-zÃ„Ã–ÃœÃ¤Ã¶Ã¼]{3}\s*\d{4}"

# Mapping: Deutsche MonatsabkÃ¼rzungen â†’ Englische (fÃ¼r strptime)
# Hinweis: "Mai" = "May" (zufÃ¤llig gleich lang, aber unterschiedliche Schreibweise)
MONTH_MAP = {
    "JÃ¤n": "Jan",    # JÃ¤nner (Ã¶sterreichisch fÃ¼r Januar)
    "Feb": "Feb",    # Februar
    "MÃ¤r": "Mar",    # MÃ¤rz
    "Apr": "Apr",    # April
    "Mai": "May",    # Mai
    "Jun": "Jun",    # Juni
    "Jul": "Jul",    # Juli
    "Aug": "Aug",    # August
    "Sep": "Sep",    # September
    "Okt": "Oct",    # Oktober
    "Nov": "Nov",    # November
    "Dez": "Dec",    # Dezember
}


def parse_pdf_date_text(value: str | None) -> datetime | None:
    """
    Parst Datumsangaben im Textformat aus ZahlungsbestÃ¤tigungen.

    Eingabe: "21. Dez 2024"
    Ausgabe: datetime(2024, 12, 21)

    Ablauf:
        1. Deutsche MonatsabkÃ¼rzung â†’ Englisch ("Dez" â†’ "Dec")
        2. strptime mit Format "%d. %b %Y"

    RÃ¼ckgabe:
        datetime bei Erfolg, None bei ungÃ¼ltigem Format.
    """
    if not value:
        return None

    v = value.strip()

    # Deutsche Monate ersetzen (z.B. "Dez" â†’ "Dec")
    for de, en in MONTH_MAP.items():
        if de in v:
            v = v.replace(de, en)
            break   # Nur eine Ersetzung nÃ¶tig (es gibt nur einen Monat pro Datum)

    try:
        return datetime.strptime(v, "%d. %b %Y")
    except ValueError:
        return None


# =============================================================================
# 1c) DATUM-PARSING: RECHNUNGEN (Punkt-Format mit OCR-Fehlern)
# =============================================================================
#
# Rechnungen (Jahres- und Monatsrechnungen) verwenden das Format "DD.MM.YYYY":
#   "GÃ¼ltigkeitszeitraum: 15.09.2024 - 14.09.2025"
#   "Leistungszeitraum:   15.09.2024 - 14.10.2024"
#
# OCR erzeugt aber typische Fehler:
#   "01 .04.2023"  â†’ Leerzeichen vor dem Punkt
#   "31.O3.2024"   â†’ GroÃŸbuchstabe O statt Ziffer 0
#   "1O.12.2024"   â†’ O mitten in einer Zahl

# Regex fÃ¼r Punkt-Format-Datumsangaben (tolerant fÃ¼r OCR-Leerzeichen und O/0-Verwechslung)
# Aufbau:
#   \b             â†’ Wortgrenze (damit "12345.01.2024" nicht matcht)
#   \d{1,2}        â†’ Tag
#   \s*\.\s*       â†’ Punkt mit optionalen Leerzeichen davor/danach
#   [\dOo]{1,2}    â†’ Monat (1-2 Zeichen, kann O statt 0 sein)
#   \s*\.\s*       â†’ Punkt
#   \d{4}          â†’ Jahr
#   \b             â†’ Wortgrenze
DATE_PATTERN_DOT = r"\b\d{1,2}\s*\.\s*[\dOo]{1,2}\s*\.\s*\d{4}\b"


def clean_date_dot(value: str) -> str:
    """
    Repariert typische OCR-Fehler in Punkt-Format-Datumsangaben.

    Reparaturen:
        1. Alle Leerzeichen entfernen: "01 .04.2023" â†’ "01.04.2023"
        2. O nach Punkt â†’ 0:           "31.O3.2024"  â†’ "31.03.2024"
        3. O zwischen Ziffern â†’ 0:     "1O.12.2024"  â†’ "10.12.2024"

    Parameter:
        value: Roher Datums-String aus OCR

    RÃ¼ckgabe:
        Bereinigter String, der von strptime("%d.%m.%Y") geparst werden kann.
    """
    # Alle Leerzeichen entfernen (OCR fÃ¼gt oft welche ein: "01 .04")
    v = re.sub(r"\s+", "", (value or "").strip())

    # O (Buchstabe) â†’ 0 (Ziffer) nach einem Punkt: ".O3" â†’ ".03"
    v = re.sub(r"(?<=\.)[Oo](?=\d)", "0", v)

    # O (Buchstabe) â†’ 0 (Ziffer) zwischen Ziffern: "1O" â†’ "10"
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)

    return v


def parse_pdf_date_dot(value: str | None) -> datetime | None:
    """
    Parst Punkt-Format-Datumsangaben aus Rechnungen, mit OCR-Fehler-Korrektur.

    Eingabe: "01 .O4.2023"  (OCR-fehlerhaft)
    Ablauf:  â†’ clean_date_dot â†’ "01.04.2023" â†’ strptime â†’ datetime(2023, 4, 1)

    RÃ¼ckgabe:
        datetime bei Erfolg, None bei ungÃ¼ltigem Format.
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
# Zwei separate Matcher fÃ¼r Vor- und Nachnamen, weil die Logik
# unterschiedlich ist:
#
#   VORNAME:
#     - Es reicht, wenn der ERSTE Vorname aus dem Antrag matcht.
#       (Antrag: "Max", PDF: "Max Michael" â†’ OK)
#     - Mehrfach-Vornamen im PDF werden toleriert.
#
#   NACHNAME:
#     - Bei Doppelnamen mÃ¼ssen ALLE Teile vorkommen.
#       (Antrag: "Muster Beispiel", PDF: "Muster Beispiel" â†’ OK)
#       (Antrag: "Muster Beispiel", PDF: "Beispiel" â†’ NICHT OK)
#
# Beide Matcher verwenden drei Ebenen der Toleranz:
#   1. Token-Match (exakte Wort-Ãœbereinstimmung nach Normalisierung)
#   2. Compact-Match (Leerzeichen entfernt, fÃ¼r OCR-Fehler)
#   3. Umlaut-Varianten (Ã¶â†’oeâ†’o, Ã¼â†’ueâ†’u, etc.)

def first_name_matches_flexible(form_vorname: str, chunk_text: str) -> bool:
    """
    PrÃ¼ft, ob der Vorname aus dem Antrag im gegebenen Textausschnitt vorkommt.

    Matching-Strategie (in Reihenfolge):
        1. Token-Match: Erster Vorname als exaktes Wort im Text?
           "max" in ["max", "michael", "mustermann"] â†’ True âœ“

        2. Compact-Match: OCR hat Leerzeichen verschluckt?
           _compact("max") in _compact("maxmichael") â†’ True âœ“

        3. Umlaut-Varianten: Transliteration berÃ¼cksichtigen?
           "jurgen" ~ "juergen" ~ "jÃ¼rgen" â†’ generiert Varianten und prÃ¼ft alle

    Parameter:
        form_vorname: Vorname aus dem Antrag (z.B. "Max")
        chunk_text:   Textausschnitt aus dem PDF (z.B. "Karteninhaber: Max Michael Mustermann")

    RÃ¼ckgabe:
        True wenn der Vorname gefunden wurde.

    Warum nur der ERSTE Vorname?
        Im Antrag steht typischerweise nur der Rufname ("Max"),
        aber auf der Rechnung der vollstÃ¤ndige Name ("Max Michael").
        Wir splitten den Antrags-Vornamen und nehmen nur das erste Wort.
    """
    form_norm = normalize_for_matching(form_vorname)
    if not form_norm:
        return False

    # Nur den ersten Vornamen verwenden (Rufname)
    # "Max Michael" â†’ "max" (nach Normalisierung)
    first = form_norm.split()[0]

    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    # â”€â”€ Ebene 1: Token-Match (exaktes Wort) â”€â”€
    # "max" in {"max", "michael", "mustermann"}
    if first in chunk_norm.split():
        return True

    # â”€â”€ Ebene 2: Compact-Match (OCR ohne Leerzeichen) â”€â”€
    # Wenn OCR "MaxMichael" als ein Wort erkennt:
    # _compact("max") = "max"
    # _compact("maxmichael") = "maxmichael"
    # "max" in "maxmichael" â†’ True
    if _compact(first) in _compact(chunk_norm):
        return True

    # â”€â”€ Ebene 3: Umlaut-Varianten â”€â”€
    # "jÃ¼rgen" â†’ Varianten: ["jurgen", "juergen"]
    # PrÃ¼fe jede Variante als Token und Compact
    for v in _variants_for_umlaut_translit(first):
        if v in chunk_norm.split() or _compact(v) in _compact(chunk_norm):
            return True

    return False


def last_name_matches_flexible(form_nachname: str, chunk_text: str) -> bool:
    """
    PrÃ¼ft, ob der Nachname aus dem Antrag im gegebenen Textausschnitt vorkommt.

    Matching-Strategie:
        1. Alle Tokens aus dem Antrags-Nachnamen mÃ¼ssen im Text vorkommen.
           Antrag: "Muster Beispiel" â†’ beide Tokens mÃ¼ssen da sein.

        2. Compact-Match fÃ¼r OCR-Fehler ("MusterBeispiel" als ein Wort).

        3. Umlaut-Varianten wie beim Vornamen.

    Parameter:
        form_nachname: Nachname aus dem Antrag (z.B. "Muster-Beispiel")
        chunk_text:    Textausschnitt aus dem PDF

    RÃ¼ckgabe:
        True wenn der Nachname gefunden wurde.

    Unterschied zum Vornamen:
        Beim Vornamen reicht der ERSTE Token (Rufname).
        Beim Nachnamen mÃ¼ssen ALLE Tokens matchen (Doppelname = alle Teile).
    """
    form_norm = normalize_for_matching(form_nachname)
    if not form_norm:
        return False

    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    # Nachname in Tokens splitten (Bindestriche werden bei Normalisierung
    # zu Leerzeichen, daher: "Muster-Beispiel" â†’ ["muster", "beispiel"])
    form_tokens = form_norm.split()
    chunk_tokens = set(chunk_norm.split())

    # â”€â”€ Ebene 1: Alle Tokens vorhanden? â”€â”€
    # Antrag: ["muster", "beispiel"]
    # Chunk:  {"karteninhaber", "muster", "beispiel", "kund:innen", ...}
    # all(["muster" in chunk, "beispiel" in chunk]) â†’ True âœ“
    if all(t in chunk_tokens for t in form_tokens):
        return True

    # â”€â”€ Ebene 2: Compact-Match (OCR-Fehler) â”€â”€
    # "musterbeispiel" in "karteninhabermusterbeispielkund" â†’ True
    if _compact(form_norm) in _compact(chunk_norm):
        return True

    # â”€â”€ Ebene 3: Umlaut-Varianten â”€â”€
    for v in _variants_for_umlaut_translit(form_norm):
        if _compact(v) in _compact(chunk_norm):
            return True

    return False


# =============================================================================
# 2b) MARKER-BASIERTES NAMENS-MATCHING
# =============================================================================
#
# Das HerzstÃ¼ck der Namens-Validierung. Statt den ganzen PDF-Text nach
# dem Namen abzusuchen (Gefahr: Firmenname matcht), suchen wir NUR
# im Umfeld eines bestimmten Markers:
#
#   Rechnungen:           "Karteninhaber" â†’ 12 Zeilen Fenster
#   ZahlungsbestÃ¤tigungen: "fÃ¼r"          â†’ 4 Zeilen Fenster
#
# Das Fenster (window_lines) definiert, wie viele Zeilen ab dem Marker
# in den "Chunk" genommen werden. Der Name muss innerhalb dieses
# Chunks vorkommen.
#
# Beispiel:
#   Zeile 15: "Karteninhaber:in:"          â† Marker gefunden
#   Zeile 16: "Erika Musterfrau"           â† Name im Chunk (15-27)
#   Zeile 17: "Kund:innennr: 12345"
#   ...

def name_match_near_markers(
    text: str,
    form_vorname: str,
    form_nachname: str,
    markers: list[tuple[list[str], int]],
) -> tuple[bool, str | None]:
    """
    PrÃ¼ft, ob Vor- UND Nachname im Umfeld eines Markers vorkommen.

    Parameter:
        text:           Gesamter PDF-Text
        form_vorname:   Vorname aus dem Antrag (z.B. "Erika")
        form_nachname:  Nachname aus dem Antrag (z.B. "Musterfrau")
        markers:        Liste von (marker_list, window_lines)-Tupeln.

            marker_list:   MÃ¶gliche Marker-WÃ¶rter (normalisiert).
                           Mehrere Varianten fÃ¼r OCR-Robustheit.
            window_lines:  Wie viele Zeilen ab dem Marker in den Chunk nehmen.

            Beispiele:
                [(["karteninhaber"], 12)]  â†’ Rechnungen
                [(["fur", "fuer"], 4)]     â†’ ZahlungsbestÃ¤tigungen

    RÃ¼ckgabe:
        (match_ok, context_chunk)

        match_ok:      True wenn Vor- UND Nachname im Marker-Fenster gefunden
        context_chunk: Der Textausschnitt um den ersten Marker (fÃ¼r Debug/UI).
                       Auch bei Nicht-Match wird der erste gefundene Marker-
                       Chunk zurÃ¼ckgegeben, damit die UI zeigen kann, was
                       das Programm "gesehen" hat.

    Ablauf:
        1. Text in Zeilen splitten
        2. FÃ¼r jede Zeile prÃ¼fen: EnthÃ¤lt sie einen Marker?
        3. Falls ja: Chunk = diese Zeile + die nÃ¤chsten N Zeilen
        4. Vor- und Nachname im Chunk suchen (flexible Matcher)
        5. Wenn beide gefunden â†’ (True, chunk)
        6. Kein Match bei keinem Marker â†’ (False, erster Chunk fÃ¼r Debug)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Speichert den Chunk um den ERSTEN gefundenen Marker (fÃ¼r Debug)
    first_context: str | None = None

    for i, raw in enumerate(lines):
        raw_norm = normalize_for_matching(raw)

        # PrÃ¼fe jeden Marker-Typ
        for marker_list, window_lines in markers:
            # Ist einer der Marker in dieser Zeile?
            if any(_contains_marker(raw_norm, m) for m in marker_list):
                # Chunk: Zeilen i bis i+window_lines zusammenfÃ¼gen
                chunk = " ".join(lines[i : i + window_lines])

                # Ersten Kontext merken (fÃ¼r Debug, auch wenn kein Match)
                if first_context is None:
                    first_context = chunk

                # Vor- und Nachname im Chunk prÃ¼fen
                fn_ok = first_name_matches_flexible(form_vorname, chunk)
                ln_ok = last_name_matches_flexible(form_nachname, chunk)

                if fn_ok and ln_ok:
                    return True, chunk

    # Kein Match gefunden â†’ False + ersten Kontext (falls Marker gefunden wurde)
    return False, first_context


# =============================================================================
# 3) ZEITRAUM-EXTRAKTION AUS PDF-TEXT
# =============================================================================
#
# Drei verschiedene Funktionen fÃ¼r unterschiedliche Dokumenttypen und
# Zeitraum-Arten:
#
#   extract_period_from_zahlungsbestaetigung()
#       â†’ "gilt 21. Dez 2024 - 20. Dez 2025" (Textformat)
#
#   extract_period_from_rechnung()
#       â†’ "GÃ¼ltigkeitszeitraum: 15.09.2024 - 14.09.2025" (Punkt-Format)
#       â†’ Fallback: "Leistungszeitraum: ..."
#
#   _extract_leistungszeitraum()
#       â†’ Nur der Leistungszeitraum (fÃ¼r Monats-PrÃ¼fung)
#
# Alle Funktionen geben ein Tupel (von_string, bis_string) zurÃ¼ck,
# das dann separat geparst wird.

def extract_period_from_zahlungsbestaetigung(text: str) -> tuple[str | None, str | None]:
    """
    Extrahiert den GÃ¼ltigkeitszeitraum aus einer ZahlungsbestÃ¤tigung.

    Sucht nach dem Marker "gilt" und extrahiert daraus zwei Datumsangaben
    im Textformat: "gilt 21. Dez 2024 - 20. Dez 2025"

    OCR-Robustheit:
        - "gilt" wird per _contains_marker() gesucht (findet auch "g i l t")
        - Datumswerte kÃ¶nnen auf der nÃ¤chsten Zeile stehen (OCR-Zeilenumbruch),
          daher werden 3 Zeilen als Chunk zusammengefasst.

    RÃ¼ckgabe:
        (von_str, bis_str) â€” Roh-Strings der beiden Datumsangaben
        (None, None) â€” wenn kein passender Zeitraum gefunden wurde
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
    Extrahiert den GÃ¼ltigkeitszeitraum aus einer Jahres-/Monatsrechnung.

    Sucht nach Markern in dieser PrioritÃ¤t:
        1. "GÃ¼ltigkeitszeitraum" / "GÃ¼ltigkeit"  (primÃ¤r)
        2. "Leistungszeitraum"                    (Fallback fÃ¼r Alt-Layouts)

    Das Ergebnis sind zwei Punkt-Format-Datumsangaben:
        "15.09.2024", "14.09.2025"

    OCR-Robustheit:
        - Marker werden normalisiert (Umlaute entfernt): "gÃ¼ltigkeitszeitraum" â†’ "gultigkeitszeitraum"
        - Datumsangaben werden per clean_date_dot() repariert
        - Chunk Ã¼ber 3 Zeilen (OCR-ZeilenumbrÃ¼che)
        - Suche geht bis zu 80 Zeilen nach dem Marker weiter
          (bei manchen Layouts steht der Zeitraum weit unten)

    RÃ¼ckgabe:
        (von_str, bis_str) â€” Bereinigte Datums-Strings ("DD.MM.YYYY")
        (None, None) â€” wenn nichts gefunden
    """
    lines = text.splitlines()

    # Marker-Varianten (normalisiert, ohne Umlaute)
    # "gÃ¼ltigkeitszeitraum" â†’ "gultigkeitszeitraum" nach normalize_for_matching()
    markers = ["gultigkeitszeitraum", "gultigkeit"]

    # â”€â”€ PrimÃ¤r: Nach "GÃ¼ltigkeitszeitraum" / "GÃ¼ltigkeit" suchen â”€â”€
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

    # â”€â”€ Fallback: "Leistungszeitraum" (Alt-Layouts) â”€â”€
    for i, line in enumerate(lines):
        if normalize_for_matching(line).startswith("leistungszeitraum"):
            chunk = " ".join(lines[i:i + 3])
            matches = re.findall(DATE_PATTERN_DOT, chunk)
            if len(matches) >= 2:
                return clean_date_dot(matches[0]), clean_date_dot(matches[1])

    return None, None


def _extract_leistungszeitraum(text: str) -> tuple[datetime | None, datetime | None]:
    """
    Extrahiert NUR den Leistungszeitraum (NICHT den GÃ¼ltigkeitszeitraum).

    Hintergrund:
        Eine Rechnung hat zwei ZeitrÃ¤ume:
        - GÃ¼ltigkeitszeitraum: Die gesamte Laufzeit des Tickets
          (z.B. 15.09.2024 - 14.09.2025 = 12 Monate)
        - Leistungszeitraum: Der Abrechnungszeitraum dieser Rechnung
          (z.B. 15.09.2024 - 14.10.2024 = 1 Monat bei Monatsrechnung)
          (z.B. 15.09.2024 - 14.09.2025 = 12 Monate bei Jahresrechnung)

    Wird verwendet von:
        - validate_rechnung():        Dauer in Monaten berechnen (leist_months)
        - validate_monatsrechnung():  PrÃ¼fen, ob Leistung innerhalb GÃ¼ltigkeit liegt
        - decision_engine:            Reklassifizierung (< 10 Monate â†’ Monatsrechnung)

    Suche:
        Gezielt nach Zeilen, die mit "Leistungszeitraum" beginnen.
        Im Chunk (5 Zeilen) nach dem Muster "DD.MM.YYYY - DD.MM.YYYY" suchen.

    RÃ¼ckgabe:
        (start_datetime, end_datetime) â€” Beide als datetime
        (None, None) â€” wenn kein Leistungszeitraum gefunden

    Hinweis:
        Findet nur den ERSTEN Leistungszeitraum im Text. Bei Multi-Page-PDFs
        (mehrere Rechnungen in einer PDF) muss der Text VORHER seitenweise
        gesplittet werden (decision_engine macht das Ã¼ber text.split('\\f')).
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

            # Zeile enthielt "Leistungszeitraum" aber kein Datum gefunden â†’ aufhÃ¶ren
            break

    return None, None


def _months_between(start: datetime, end: datetime) -> int:
    """
    Berechnet die Anzahl Monate zwischen zwei Datumswerten.

    Formel: (Jahres-Differenz Ã— 12) + Monats-Differenz

    Beispiele:
        01.09.2024 â†’ 01.09.2025 = 12 Monate (Jahresrechnung)
        01.09.2024 â†’ 01.10.2024 = 1 Monat   (Monatsrechnung)
        01.09.2024 â†’ 01.12.2024 = 3 Monate  (Quartalsrechnung)

    Verwendet in:
        - validate_rechnung() â†’ leist_months
        - decision_engine â†’ reclassify_short_jahresrechnungen()
          (wenn leist_months < 10 â†’ Reklassifizierung zu Monatsrechnung)
    """
    return (end.year - start.year) * 12 + (end.month - start.month)


# =============================================================================
# 4) DEBUG: NAMEN-EXTRAKTION AUS RECHNUNG
# =============================================================================
#
# Diese Funktion wird NICHT fÃ¼r die Validierung verwendet.
# Sie dient nur der Debug-Ausgabe, um zu zeigen, welchen Namen
# das System aus der Rechnung extrahiert hat.
#
# Warnung: Kann Firmenname statt Personenname liefern, z.B.:
#   "Karteninhaber:in: One Mobility Ticketing GmbH"
#   â†’ extrahiert "One Mobility Ticketing GmbH" â† FALSCH
#
# Deshalb verwenden wir fÃ¼r die VALIDIERUNG stattdessen
# name_match_near_markers(), das den Antrags-Namen im Umfeld
# des Markers sucht, statt den Namen aus dem Text zu "erraten".

def extract_name_from_rechnung(text: str) -> str | None:
    """
    OPTIONAL / DEBUG: Versucht den Karteninhaber-Namen aus dem Text zu extrahieren.

    Regex sucht nach:
        "Karteninhaber:in: <Name>"
        "Karteninhaber: <Name>"

    Der extrahierte Name wird am ersten StÃ¶rwort abgeschnitten:
        "Musterfrau Erika Kund:innennr" â†’ "Musterfrau Erika"

    StÃ¶rwÃ¶rter: "kund", "kunden", "kund:inn", "nr", "rechnungsdatum",
    "fallig", "menge", "beschreibung"

    RÃ¼ckgabe:
        Normalisierter Name oder None.

    NICHT fÃ¼r Validierung verwenden â€” nur fÃ¼r Debug-Ausgaben.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full = " ".join(lines)

    # Regex: "Karteninhaber" (optional ":in") + ":" + Name
    m = re.search(
        r"karteninhaber(?:\:in)?\s*:\s*([A-Za-zÃ„Ã–ÃœÃ¤Ã¶Ã¼ÃŸ\- ]{2,})",
        full,
        flags=re.IGNORECASE,
    )
    if m:
        name_raw = m.group(1).strip()

        # Am ersten StÃ¶rwort abschneiden (z.B. "Kund:innen", "Nr", "Rechnungsdatum")
        # Das sind WÃ¶rter, die NACH dem Namen in der gleichen Zeile stehen kÃ¶nnen.
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
# Drei Funktionen â€” eine pro Dokumenttyp. Jede gibt ein dict zurÃ¼ck
# mit allen PrÃ¼fergebnissen. Die Decision Engine (build_invoice_decision)
# ruft je nach doc_type die passende Funktion auf:
#
#   doc_type == "jahresrechnung"        â†’ validate_rechnung()
#   doc_type == "monatsrechnung"        â†’ validate_monatsrechnung()
#   doc_type == "zahlungsbestaetigung"  â†’ validate_zahlungsbestaetigung()
#
# Gemeinsames Muster aller drei Funktionen:
#   1. Name prÃ¼fen (name_match_near_markers)
#   2. Zeitraum aus PDF extrahieren
#   3. Zeitraum aus Antrag laden
#   4. Vergleichen
#   5. Ergebnis-dict aufbauen
#   6. Optional: Debug-Ausgaben (verbose=True)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 5a) ZAHLUNGSBESTÃ„TIGUNG
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def validate_zahlungsbestaetigung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine ZahlungsbestÃ¤tigung gegen Antragsdaten.

    PrÃ¼fungen:
        1. NAME:    Kommt "Vorname Nachname" im Umfeld von "fÃ¼r" vor?
                    Marker: "fÃ¼r" (normalisiert: "fur")
                    Fenster: 4 Zeilen ab dem Marker

        2. ZEITRAUM: Stimmt "gilt <von> - <bis>" mit dem Antrag Ã¼berein?
                     Format: "gilt 21. Dez 2024 - 20. Dez 2025"

    RÃ¼ckgabe (dict):
        name_ok:         bool       â€” Name gefunden?
        name_context:    str | None â€” Textausschnitt um "fÃ¼r" (Debug)
        period_ok:       bool       â€” Zeitraum stimmt Ã¼berein?
        period_pdf_raw:  dict       â€” Rohe Datums-Strings aus PDF
        period_pdf_iso:  dict       â€” Geparste Daten als ISO-Strings
        period_form_iso: dict       â€” Antrags-Daten als ISO-Strings
        all_ok:          bool       â€” Gesamt: name_ok AND period_ok
        reason:          str        â€” (nur bei Fehler) Grund fÃ¼r Ablehnung
    """

    # â”€â”€ Name prÃ¼fen â”€â”€
    # Marker: "fÃ¼r" (auf ZahlungsbestÃ¤tigungen steht "fÃ¼r Erika Musterfrau")
    # "fÃ¼r" wird zu "fur" nach normalize_for_matching (Umlaute werden entfernt)
    # "fuer" als Alternative, falls OCR "Ã¼" als "ue" liest
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["fur", "fuer"], 4) ],   # 4 Zeilen Fenster (Zahlung ist kompakt)
    )

    # â”€â”€ Zeitraum aus PDF extrahieren â”€â”€
    von_str, bis_str = extract_period_from_zahlungsbestaetigung(text)
    von_pdf = parse_pdf_date_text(von_str)     # "21. Dez 2024" â†’ datetime
    bis_pdf = parse_pdf_date_text(bis_str)

    # â”€â”€ Zeitraum aus Antrag laden â”€â”€
    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    # â”€â”€ Fehlende Antragsdaten? â”€â”€
    # Wenn gilt_von oder gilt_bis im Antrag fehlen, kÃ¶nnen wir den
    # Zeitraum nicht prÃ¼fen â†’ all_ok = False mit BegrÃ¼ndung
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
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag â†’ Zeitraum-PrÃ¼fung nicht mÃ¶glich")
        return result

    # â”€â”€ Zeitraum vergleichen â”€â”€
    # Beide Seiten (PDF und Antrag) mÃ¼ssen gesetzt sein UND exakt Ã¼bereinstimmen.
    # .date() wird verwendet, damit Uhrzeiten ignoriert werden.
    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    # â”€â”€ Ergebnis aufbauen â”€â”€
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

    # â”€â”€ Debug-Ausgaben â”€â”€
    if verbose:
        print("Name im Antrag:", form_data.get("vorname"), form_data.get("familienname"))
        print("Name-Match ZahlungsbestÃ¤tigung (near 'fÃ¼r'):", result["name_ok"])
        print("Zeitraum im Antrag:", von_json.date(), "-", bis_json.date())
        print("Zeitraum auf ZahlungsbestÃ¤tigung:",
              von_pdf.date() if von_pdf else None, "-",
              bis_pdf.date() if bis_pdf else None)
        print("Zeitraum-Match ZahlungsbestÃ¤tigung:", result["period_ok"])
        print("DEBUG Zeitraum-Roh:", von_str, bis_str)

    return result


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 5b) JAHRESRECHNUNG
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def validate_rechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine Jahresrechnung gegen Antragsdaten.

    PrÃ¼fungen:
        1. NAME:              Kommt "Vorname Nachname" im Umfeld von "Karteninhaber" vor?
        2. GÃœLTIGKEITSZEITRAUM: Stimmt er mit dem Antrag Ã¼berein?
        3. LEISTUNGSZEITRAUM:  Wird extrahiert und Dauer in Monaten berechnet.

    Besonderheit â€” Leistungszeitraum vs. GÃ¼ltigkeitszeitraum:
        Eine Rechnung hat ZWEI ZeitrÃ¤ume:

        GÃ¼ltigkeitszeitraum = Laufzeit des gesamten Tickets
            z.B. 15.09.2024 - 14.09.2025 (12 Monate)
            â†’ Muss mit dem Antrag Ã¼bereinstimmen (period_ok)

        Leistungszeitraum = Abrechnungszeitraum DIESER Rechnung
            z.B. 15.09.2024 - 14.09.2025 (12 Monate bei Jahresrechnung)
            z.B. 15.09.2024 - 14.10.2024 (1 Monat bei Kurzrechnung)
            â†’ Wird in leist_months umgerechnet
            â†’ Decision Engine nutzt leist_months fÃ¼r Reklassifizierung:
              Wenn < 10 Monate â†’ wird als Monatsrechnung behandelt

    RÃ¼ckgabe (dict):
        name_ok:          bool       â€” Name (Karteninhaber) matcht?
        name_context:     str | None â€” Textausschnitt (Debug)
        period_ok:        bool       â€” GÃ¼ltigkeitszeitraum == Antrag?
        leist_months:     int | None â€” Dauer des Leistungszeitraums in Monaten
        leist_month_key:  str | None â€” z.B. "2024-09" (fÃ¼r Monatsgruppierung)
        leist_in_guelt:   bool       â€” Leistungszeitraum âŠ† GÃ¼ltigkeitszeitraum?
        all_ok:           bool       â€” name_ok AND period_ok
        dbg_extracted_name: str | None â€” Debug: extrahierter Name (kann Firma sein!)
    """

    # â”€â”€ 1) Name prÃ¼fen â”€â”€
    # Marker: "Karteninhaber" (auf Rechnungen steht "Karteninhaber:in: Erika Musterfrau")
    # 12 Zeilen Fenster, weil zwischen Marker und Name manchmal
    # noch andere Felder stehen (Kund:innennr, Rechnungsnr, etc.)
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],
    )

    # Debug-Name extrahieren (NUR fÃ¼r Ausgabe, NICHT fÃ¼r Validierung)
    dbg_name = extract_name_from_rechnung(text)

    # â”€â”€ 2) GÃ¼ltigkeitszeitraum aus PDF extrahieren â”€â”€
    von_str, bis_str = extract_period_from_rechnung(text)
    von_pdf = parse_pdf_date_dot(von_str)
    bis_pdf = parse_pdf_date_dot(bis_str)

    # â”€â”€ 3) GÃ¼ltigkeitszeitraum aus Antrag laden â”€â”€
    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    # â”€â”€ Fehlende Antragsdaten? â”€â”€
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
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag â†’ Zeitraum-PrÃ¼fung nicht mÃ¶glich")
        return result

    # â”€â”€ 4) GÃ¼ltigkeitszeitraum vergleichen â”€â”€
    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    # â”€â”€ 5) Leistungszeitraum extrahieren und analysieren â”€â”€
    l_von, l_bis = _extract_leistungszeitraum(text)

    # Dauer in Monaten berechnen
    # Beispiele:
    #   12 Monate â†’ echte Jahresrechnung
    #   1 Monat  â†’ wird von decision_engine als Monatsrechnung reklassifiziert
    leist_months = _months_between(l_von, l_bis) if (l_von and l_bis) else None

    # MonatsschlÃ¼ssel: Jahr-Monat des Leistungsbeginns
    # Wird fÃ¼r Gruppierung verwendet, wenn die Rechnung als
    # Monatsrechnung reklassifiziert wird
    leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}" if l_von else None

    # PrÃ¼fen, ob der Leistungszeitraum innerhalb der Ticket-GÃ¼ltigkeit liegt
    leist_in_guelt = (
        l_von is not None and l_bis is not None
        and von_json.date() <= l_von.date() <= l_bis.date() <= bis_json.date()
    )

    # â”€â”€ 6) Ergebnis aufbauen â”€â”€
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

    # â”€â”€ 7) Debug-Ausgaben â”€â”€
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
        print("Leistungszeitraum innerhalb GÃ¼ltigkeit:", result["leist_in_guelt"])

    return result


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 5c) MONATSRECHNUNG
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def validate_monatsrechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Validiert eine Monatsrechnung gegen Antragsdaten.

    Eine Monatsrechnung ist eine Einzelrechnung fÃ¼r einen Abrechnungsmonat.
    Drei Monatsrechnungen fÃ¼r VERSCHIEDENE Monate = Rechnungsnachweis OK.

    PrÃ¼fungen:
        1. NAME:    Kommt "Vorname Nachname" im Umfeld von "Karteninhaber" vor?
        2. GÃœLTIGKEIT: Stimmt der GÃ¼ltigkeitszeitraum mit dem Antrag Ã¼berein?
                       (= Gesamtlaufzeit des Tickets)
        3. LEISTUNG:   Liegt der Leistungszeitraum INNERHALB der GÃ¼ltigkeit?
                       (= der eine Abrechnungsmonat dieser Rechnung)

    Unterschied zur Jahresrechnung:
        - Jahresrechnung: leist_months wird berechnet, aber all_ok hÃ¤ngt
          nur von name_ok AND period_ok ab.
        - Monatsrechnung: all_ok = name_ok AND guelt_ok AND leist_ok
          (alle drei mÃ¼ssen stimmen!)

    leist_month_key:
        Der MonatsschlÃ¼ssel (z.B. "2024-09") wird von der Decision Engine
        verwendet, um zu zÃ¤hlen, wie viele VERSCHIEDENE Monate abgedeckt sind.
        Beispiel: 3 Rechnungen mit keys "2024-09", "2024-10", "2024-11"
        â†’ 3 verschiedene Monate â†’ monatsrechnungen_ok = True

    Multi-Page-PDFs:
        Wenn mehrere Monatsrechnungen in EINER PDF stehen, wird der Text
        von der Decision Engine seitenweise gesplittet (text.split('\\f')).
        Diese Funktion bekommt dann nur den Text EINER Seite.

    RÃ¼ckgabe (dict):
        name_ok:          bool       â€” Karteninhaber matcht?
        name_context:     str | None â€” Textausschnitt (Debug)
        guelt_ok:         bool       â€” GÃ¼ltigkeitszeitraum == Antrag?
        leist_ok:         bool       â€” Leistungszeitraum âŠ† GÃ¼ltigkeit?
        guelt_pdf_raw/iso: dict      â€” GÃ¼ltigkeitsdaten (roh/ISO)
        form_iso:         dict       â€” Antrags-Daten (ISO)
        leist_pdf_iso:    dict       â€” Leistungszeitraum (ISO)
        leist_month_key:  str | None â€” z.B. "2024-09"
        all_ok:           bool       â€” Gesamt: name_ok AND guelt_ok AND leist_ok
    """

    # â”€â”€ 1) Name prÃ¼fen â”€â”€
    # Gleicher Marker wie bei Jahresrechnung: "Karteninhaber"
    # 12 Zeilen Fenster (gleiche Rechnungslayouts)
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],
    )

    # â”€â”€ 2) GÃ¼ltigkeit (Gesamtlaufzeit des Tickets) prÃ¼fen â”€â”€
    # Aus der Rechnung: "GÃ¼ltigkeitszeitraum: 15.09.2024 - 14.09.2025"
    g_von_s, g_bis_s = extract_period_from_rechnung(text)
    g_von = parse_pdf_date_dot(g_von_s)
    g_bis = parse_pdf_date_dot(g_bis_s)

    # Aus dem Antrag: gilt_von und gilt_bis
    a_von = parse_form_datetime(form_data.get("gilt_von", ""))
    a_bis = parse_form_datetime(form_data.get("gilt_bis", ""))

    # â”€â”€ Fehlende Antragsdaten? â”€â”€
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
            print("WARNUNG: gilt_von/gilt_bis fehlen im Antrag â†’ Zeitraum-PrÃ¼fung nicht mÃ¶glich")
        return result

    # â”€â”€ GÃ¼ltigkeit vergleichen â”€â”€
    # Beide Seiten mÃ¼ssen exakt Ã¼bereinstimmen (Datum ohne Uhrzeit)
    guelt_ok = (
        g_von is not None and g_bis is not None
        and g_von.date() == a_von.date()
        and g_bis.date() == a_bis.date()
    )

    # â”€â”€ 3) Leistungszeitraum (ein einzelner Abrechnungsmonat) prÃ¼fen â”€â”€
    l_von, l_bis = _extract_leistungszeitraum(text)

    # Leistungszeitraum muss KOMPLETT innerhalb der Ticket-GÃ¼ltigkeit liegen:
    #   a_von â‰¤ l_von â‰¤ l_bis â‰¤ a_bis
    #
    # Beispiel (OK):
    #   GÃ¼ltigkeit:    15.09.2024 - 14.09.2025
    #   Leistung:      15.10.2024 - 14.11.2024   â†’ liegt komplett drin âœ“
    #
    # Beispiel (NICHT OK):
    #   GÃ¼ltigkeit:    15.09.2024 - 14.09.2025
    #   Leistung:      15.09.2023 - 14.10.2023   â†’ liegt VOR der GÃ¼ltigkeit âœ—
    leist_ok = (
        l_von is not None and l_bis is not None
        and a_von.date() <= l_von.date() <= l_bis.date() <= a_bis.date()
    )

    # â”€â”€ 4) MonatsschlÃ¼ssel bauen â”€â”€
    # Jahr-Monat des Leistungszeitraum-BEGINNS.
    # Wird von der Decision Engine verwendet, um einzigartige Monate zu zÃ¤hlen.
    #
    # Beispiel:
    #   Leistung 15.09.2024 - 14.10.2024 â†’ leist_month_key = "2024-09"
    #   Leistung 15.10.2024 - 14.11.2024 â†’ leist_month_key = "2024-10"
    #
    # In der Decision Engine:
    #   valid_months = {"2024-09", "2024-10", "2024-11"}  â†’ 3 verschiedene â†’ OK
    leist_month_key = None
    if l_von is not None:
        leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}"

    # â”€â”€ 5) Ergebnis aufbauen â”€â”€
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
        # Gesamt: ALLE drei Checks mÃ¼ssen bestehen
        # (Unterschied zur Jahresrechnung, wo leist_months nur informativ ist)
        "all_ok": bool(name_ok and guelt_ok and leist_ok),
    }

    # â”€â”€ 6) Debug-Ausgaben â”€â”€
    if verbose:
        print("Name-Match Monatsrechnung (near Karteninhaber):", result["name_ok"])
        print("GÃ¼ltigkeit Monatsrechnung:",
              _fmt_dot(g_von), "-", _fmt_dot(g_bis), "->", result["guelt_ok"])
        print("Leistungszeitraum Monatsrechnung:",
              _fmt_dot(l_von), "-", _fmt_dot(l_bis), "->", result["leist_ok"])
        print("Leistungs-MonatsschlÃ¼ssel:", leist_month_key)

    return result
