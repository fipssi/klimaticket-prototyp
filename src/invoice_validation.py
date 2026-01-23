"""
invoice_validation.py

Zweck
-----
Validierung von KlimaTicket-Dokumenten (Rechnungen / Monatsrechnungen / Zahlungsbestätigungen)
gegen Antragsdaten (JSON). Schwerpunkt:
- Robustes Matching von Namen (Umlaute/Diakritika, ß->ss, Bindestriche, Mehrfachnamen, OCR ohne Leerzeichen)
- Robustes Extrahieren von Zeiträumen aus OCR-Texten (z.B. "01 .04.2023", "31.O3.2024")
- Robuste Datumsverarbeitung aus Antrag (ISO Datum mit/ohne Uhrzeit)

Wichtiges Design-Prinzip
------------------------
Wir "erraten" den Namen nicht mehr über generische Header-Zeilen (Gefahr: Firmenname),
sondern prüfen, ob der Name aus dem Antrag im Umfeld eines klaren Markers vorkommt:
- Rechnungen/Monatsrechnungen: "Karteninhaber"
- Zahlungsbestätigung: "für <NAME>"

So vermeidest du Fälle wie:
"Name auf Rechnung: One Mobility Ticketing GmbH"
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Tuple, Optional


# =============================================================================
# 0) Text-Normalisierung & Hilfsfunktionen
# =============================================================================

def normalize_for_matching(value: str) -> str:
    """
    Normalisiert Text für robuste Vergleiche:
    - lowercase
    - ß -> ss
    - Diakritika entfernen (ä->a, ö->o, ü->u, é->e, ...)
    - Bindestrich/Slash/Unterstrich als Trennzeichen => Leerzeichen
    - Sonderzeichen entfernen
    - Whitespace normalisieren

    Beispiel:
      "Phillip-Andréas" -> "phillip andreas"
      "Karteninhaber:in" -> "karteninhaber in"
    """
    value = (value or "").lower().strip()
    value = value.replace("ß", "ss")

    # Diakritika entfernen
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))

    # Trennzeichen als Worttrenner behandeln
    value = re.sub(r"[-_/]+", " ", value)

    # Alles außer Buchstaben/Zahlen/Whitespace entfernen
    value = re.sub(r"[^a-z0-9\s]", " ", value)

    # Whitespace normalisieren
    return " ".join(value.split())


def _compact(s: str) -> str:
    """
    Entfernt alle Leerzeichen.
    Nützlich für OCR-Fälle ohne Leerzeichen: "MarcoWurst".
    """
    return (s or "").replace(" ", "")


def _variants_for_umlaut_translit(s: str) -> set[str]:
    """
    Erzeugt einige zusätzliche Varianten für deutsche Transliterationen:
    - normalize_for_matching macht "ö" -> "o"
    - man sieht aber manchmal "oe" statt "ö"
      -> daher: "joerg" soll auch "jorg" matchen

    Hinweis:
    - Wir machen nur "ae/oe/ue -> a/o/u" als sichere Richtung,
      NICHT die umgekehrte Richtung (würde False-Positives erhöhen).
    """
    v = normalize_for_matching(s)
    variants = {v}
    variants.add(v.replace("ae", "a").replace("oe", "o").replace("ue", "u"))
    return {x for x in variants if x}


def _contains_marker(line_norm: str, marker: str) -> bool:
    """
    Marker robust finden:
    - normal: "fur" in "fur elisabeth baier"
    - OCR spaced: "fur" in "f u r elisabeth ..." -> via _compact

    Erwartet bereits normalisierten Text (normalize_for_matching).
    """
    return (marker in line_norm) or (marker in _compact(line_norm))


def _fmt_iso(dt: datetime | None) -> str | None:
    """ISO-Format für Decision-Engine/Weiterverarbeitung."""
    return dt.date().isoformat() if dt else None


def _fmt_dot(dt: datetime | None) -> str | None:
    """DD.MM.YYYY-Format (z.B. für Debug-Ausgaben)."""
    return dt.strftime("%d.%m.%Y") if dt else None


# =============================================================================
# 1) Datum Parsing (Antrag) / Datum Parsing (PDF)
# =============================================================================

def parse_form_datetime(value: str) -> datetime:
    """
    Akzeptiert Antragsfelder in ISO-Form:
    - 'YYYY-MM-DD'
    - 'YYYY-MM-DD HH:MM:SS'
    - 'YYYY-MM-DDTHH:MM:SS'
    - inkl. optionaler Zeitzone (wenn vorhanden)

    Hintergrund:
    Manche JSONs liefern '2025-02-01 00:00:00' statt '2025-02-01'.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("Leeres Datumsfeld im Antrag")

    # fromisoformat kann "YYYY-MM-DD" und "YYYY-MM-DD HH:MM:SS" etc.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    # Fallback-Formate
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unbekanntes Datumsformat im Antrag: {value!r}")


# Zahlungsbestätigung: "21. Dez 2024"
DATE_PATTERN_TEXT = r"\d{1,2}\.\s*[A-Za-zÄÖÜäöü]{3}\s*\d{4}"

MONTH_MAP = {
    "Jän": "Jan",
    "Feb": "Feb",
    "Mär": "Mar",
    "Apr": "Apr",
    "Mai": "May",
    "Jun": "Jun",
    "Jul": "Jul",
    "Aug": "Aug",
    "Sep": "Sep",
    "Okt": "Oct",
    "Nov": "Nov",
    "Dez": "Dec",
}


def parse_pdf_date_text(value: str | None) -> datetime | None:
    """
    Parse für Zahlungsbestätigungen: "21. Dez 2024"
    """
    if not value:
        return None

    v = value.strip()
    for de, en in MONTH_MAP.items():
        if de in v:
            v = v.replace(de, en)
            break

    try:
        return datetime.strptime(v, "%d. %b %Y")
    except ValueError:
        return None


# Rechnungen: "01.04.2023" (mit OCR-Fehlern)
DATE_PATTERN_DOT = r"\b\d{1,2}\s*\.\s*[\dOo]{1,2}\s*\.\s*\d{4}\b"


def clean_date_dot(value: str) -> str:
    """
    Repariert typische OCR-Probleme:
    - "01 .04.2023" -> "01.04.2023"
    - "31.O3.2024"  -> "31.03.2024" (O statt 0)
    """
    v = re.sub(r"\s+", "", (value or "").strip())  # spaces entfernen
    v = re.sub(r"(?<=\.)[Oo](?=\d)", "0", v)       # ".O3." -> ".03."
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)       # "1O" -> "10"
    return v


def parse_pdf_date_dot(value: str | None) -> datetime | None:
    """
    Parse für Dot-Formate: "DD.MM.YYYY", tolerant bzgl OCR.
    """
    if not value:
        return None
    try:
        v = clean_date_dot(value)
        return datetime.strptime(v, "%d.%m.%Y")
    except ValueError:
        return None


# =============================================================================
# 2) Flexible Namens-Matcher
# =============================================================================

def first_name_matches_flexible(form_vorname: str, chunk_text: str) -> bool:
    """
    Flexibler Vorname-Match:
    - Es reicht der erste Vorname aus dem Antrag (typisch: Antrag enthält nur 1. Vorname)
    - chunk_text kann Mehrfachnamen enthalten: "Phillip Andreas" oder "Phillip-Andreas"
    - tolerant für OCR ohne Leerzeichen: "PhillipAndreas"
    - tolerant für Umlaute/Translit: "Jörg" ~ "Joerg"/"Jorg"
    """
    form_norm = normalize_for_matching(form_vorname)
    if not form_norm:
        return False

    first = form_norm.split()[0]
    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    # 1) Token match
    if first in chunk_norm.split():
        return True

    # 2) OCR ohne Leerzeichen
    if _compact(first) in _compact(chunk_norm):
        return True

    # 3) Umlaute/Translit-Varianten
    for v in _variants_for_umlaut_translit(first):
        if v in chunk_norm.split() or _compact(v) in _compact(chunk_norm):
            return True

    return False


def last_name_matches_flexible(form_nachname: str, chunk_text: str) -> bool:
    """
    Flexibler Nachname-Match:
    - Bei Doppel-/Mehrfachnachnamen müssen alle Tokens aus Antrag im chunk vorkommen
      (z.B. "Mayer Schmidt")
    - tolerant für Bindestrich
    - tolerant für OCR ohne Leerzeichen
    """
    form_norm = normalize_for_matching(form_nachname)
    if not form_norm:
        return False

    chunk_norm = normalize_for_matching(chunk_text)
    if not chunk_norm:
        return False

    form_tokens = form_norm.split()
    chunk_tokens = set(chunk_norm.split())

    # 1) Alle Nachname-Bestandteile müssen im chunk vorkommen
    if all(t in chunk_tokens for t in form_tokens):
        return True

    # 2) OCR ohne Leerzeichen
    if _compact(form_norm) in _compact(chunk_norm):
        return True

    # 3) Varianten
    for v in _variants_for_umlaut_translit(form_norm):
        if _compact(v) in _compact(chunk_norm):
            return True

    return False


def name_match_near_markers(
    text: str,
    form_vorname: str,
    form_nachname: str,
    markers: list[tuple[list[str], int]],
) -> tuple[bool, str | None]:
    """
    Prüft, ob Vorname+Nachname im Umfeld eines Markers auftauchen.

    markers: Liste aus (marker_list, window_lines)
      Beispiel:
        [ (["karteninhaber"], 8) ]   -> Rechnungen
        [ (["fur", "fuer"], 3) ]     -> Zahlungsbestätigungen ("für" normalisiert -> "fur")

    window_lines: wie viele Zeilen ab Marker in den "Chunk" genommen werden.

    Rückgabe:
      (match_ok, context_chunk)

    - match_ok: True, wenn Vorname+Nachname im Marker-Fenster matchen
    - context_chunk: der Chunk um den ersten gefundenen Marker (oder der Chunk des Matches),
                     nützlich für Decision Engine / Debug
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first_context: str | None = None

    for i, raw in enumerate(lines):
        raw_norm = normalize_for_matching(raw)

        for marker_list, window_lines in markers:
            if any(_contains_marker(raw_norm, m) for m in marker_list):
                chunk = " ".join(lines[i : i + window_lines])

                if first_context is None:
                    first_context = chunk

                fn_ok = first_name_matches_flexible(form_vorname, chunk)
                ln_ok = last_name_matches_flexible(form_nachname, chunk)

                if fn_ok and ln_ok:
                    return True, chunk

    return False, first_context


# =============================================================================
# 3) Zeitraum-Extraktion
# =============================================================================

def extract_period_from_zahlungsbestaetigung(text: str) -> tuple[str | None, str | None]:
    """
    Zahlungsbestätigung: sucht 'gilt 27. Dez 2024 - 26. Dez 2025' o.ä.
    OCR kann Zeilenumbrüche haben, daher suchen wir in der "gilt"-Zeile zwei Datumswerte.

    Robustheit:
    - "gilt" kann OCR-spaced sein ("g i l t"), daher normalisieren + _contains_marker.
    """
    lines = text.splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        ln = normalize_for_matching(line)
        if _contains_marker(ln, "gilt"):
            # OCR kann die Datumswerte auf die nächste Zeile umbrechen.
            # Daher nehmen wir ein kleines Chunk (aktuelle + nächste 2 Zeilen) und suchen darin 2 Datumswerte.
            chunk_raw = " ".join(lines[i : i + 3])
            matches = re.findall(DATE_PATTERN_TEXT, chunk_raw)
            if len(matches) >= 2:
                return matches[0].strip(), matches[1].strip()

    return None, None


def extract_period_from_rechnung(text: str) -> tuple[str | None, str | None]:
    """
    Jahres-/Monatsrechnung:
    Primär: Bereich nach "Gültigkeitszeitraum" / "Gültigkeit"
    Fallback: "Leistungszeitraum"

    OCR-Probleme:
    - Datumswerte können Leerzeichen enthalten: "01 .04.2023"
    - Monat kann "O3" statt "03" sein
    """

    lines = text.splitlines()

    # Marker ohne Umlaut (normalize_for_matching macht "ü"->"u")
    markers = ["gultigkeitszeitraum", "gultigkeit"]

    # 1) Primär: im Umfeld des Markers die erste Zeilen-Gruppe finden, die 2 Datumswerte enthält
    for i, line in enumerate(lines):
        ln = normalize_for_matching(line)
        if any(m in ln for m in markers):
            # In den nächsten Zeilen nach zwei Daten suchen (Chunk über 2-3 Zeilen)
            for j in range(i, min(i + 80, len(lines))):
                chunk = " ".join(lines[j:j + 3])
                matches = re.findall(DATE_PATTERN_DOT, chunk)
                if len(matches) >= 2:
                    return clean_date_dot(matches[0]), clean_date_dot(matches[1])

    # 2) Fallback: Leistungszeitraum (Alt-Layout)
    for i, line in enumerate(lines):
        if normalize_for_matching(line).startswith("leistungszeitraum"):
            chunk = " ".join(lines[i:i + 3])
            matches = re.findall(DATE_PATTERN_DOT, chunk)
            if len(matches) >= 2:
                return clean_date_dot(matches[0]), clean_date_dot(matches[1])

    return None, None


# =============================================================================
# 4) (Optional) Debug-Extraktion von Namen
# =============================================================================

def extract_name_from_rechnung(text: str) -> str | None:
    """
    OPTIONAL / DEBUG:
    Versucht den Karteninhaber-Namen aus dem Text zu extrahieren.

    Achtung:
    - Kann in manchen Layouts fälschlicherweise Firmenname liefern.
    - Für die Validierung verwenden wir NICHT mehr diesen Wert, sondern
      name_match_near_markers(...) im Umfeld von "Karteninhaber".
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full = " ".join(lines)

    m = re.search(
        r"karteninhaber(?:\:in)?\s*:\s*([A-Za-zÄÖÜäöüß\- ]{2,})",
        full,
        flags=re.IGNORECASE,
    )
    if m:
        name_raw = m.group(1).strip()
        name_raw = re.split(
            r"\s+(kund|kunden|kund:inn|kundinnen|kund:innen|nr|rechnungsdatum|fallig|menge|beschreibung)\b",
            name_raw,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        return normalize_for_matching(name_raw) or None

    return None


# =============================================================================
# 5) Validierungen
# =============================================================================

def validate_zahlungsbestaetigung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Zahlungsbestätigung:
    - Name prüfen (Marker: "für <NAME>")
    - Zeitraum prüfen (Marker: "gilt <von> - <bis>")

    Rückgabe (dict):
    - name_ok: bool
    - name_context: Chunk um den Marker (für Decision Engine / Debug)
    - period_ok: bool
    - period_pdf_raw / period_pdf_iso / period_form_iso
    - all_ok: bool
    """

    # --- Name (Zahlungsbestätigung: "für ...") ---
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["fur", "fuer"], 4) ],
    )

    # --- Zeitraum ---
    von_str, bis_str = extract_period_from_zahlungsbestaetigung(text)
    von_pdf = parse_pdf_date_text(von_str)
    bis_pdf = parse_pdf_date_text(bis_str)

    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    result = {
        "doc_type": "zahlungsbestaetigung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "period_ok": bool(period_ok),
        "period_pdf_raw": {"von": von_str, "bis": bis_str},
        "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},
        "period_form_iso": {"von": von_json.date().isoformat(), "bis": bis_json.date().isoformat()},
        "all_ok": bool(name_ok and period_ok),
    }

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


def validate_rechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Jahresrechnung:
    - Name prüfen im Umfeld von "Karteninhaber"
    - Zeitraum prüfen (Gültigkeitszeitraum / Leistungszeitraum)

    Rückgabe (dict):
    - name_ok: bool
    - name_context: Chunk um den Marker (für Decision Engine / Debug)
    - period_ok: bool
    - period_pdf_raw / period_pdf_iso / period_form_iso
    - all_ok: bool
    """

    # --- Name robust: im Umfeld von "Karteninhaber" ---
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],
    )

    # Optional Debug
    dbg_name = extract_name_from_rechnung(text)

    # --- Zeitraum ---
    von_str, bis_str = extract_period_from_rechnung(text)
    von_pdf = parse_pdf_date_dot(von_str)
    bis_pdf = parse_pdf_date_dot(bis_str)

    von_json = parse_form_datetime(form_data.get("gilt_von", ""))
    bis_json = parse_form_datetime(form_data.get("gilt_bis", ""))

    period_ok = (
        von_pdf is not None
        and bis_pdf is not None
        and von_pdf.date() == von_json.date()
        and bis_pdf.date() == bis_json.date()
    )

    result = {
        "doc_type": "jahresrechnung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "period_ok": bool(period_ok),
        "period_pdf_raw": {"von": von_str, "bis": bis_str},
        "period_pdf_iso": {"von": _fmt_iso(von_pdf), "bis": _fmt_iso(bis_pdf)},
        "period_form_iso": {"von": von_json.date().isoformat(), "bis": bis_json.date().isoformat()},
        "dbg_extracted_name": dbg_name,
        "all_ok": bool(name_ok and period_ok),
    }

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

    return result


def validate_monatsrechnung(form_data: dict, text: str, verbose: bool = True) -> dict:
    """
    Monatsrechnung:
    - Name prüfen im Umfeld von "Karteninhaber"
    - Gültigkeit prüfen (Gültigkeitszeitraum/Gültigkeit)
    - Leistungszeitraum prüfen (liegt innerhalb der Gültigkeit)

    Rückgabe (dict):
    - name_ok: bool                 -> Karteninhaber passt zum Antrag
    - name_context: str | None      -> Textausschnitt um "Karteninhaber" (für Debug/UI)
    - guelt_ok: bool                -> Gültigkeit der Rechnung = Gültigkeit im Antrag
    - leist_ok: bool                -> Leistungszeitraum liegt innerhalb der Gültigkeit
    - guelt_pdf_raw / guelt_pdf_iso -> Gültigkeitsdaten aus der Rechnung (roh/ISO)
    - form_iso                      -> Gültigkeitsdaten aus dem Antrag (ISO)
    - leistungszeitraum_iso         -> Leistungszeitraum der Monatsrechnung (ISO)
    - leist_month_key: str | None   -> Jahr-Monat des Leistungsbeginns (z.B. "2024-09")
    - all_ok: bool                  -> Gesamtflag für diese Monatsrechnung
    """

    # --- 1) Name prüfen: kommt Vor- und Nachname im Umfeld von "Karteninhaber" vor? ---
    name_ok, name_context = name_match_near_markers(
        text,
        form_data.get("vorname", ""),
        form_data.get("familienname", ""),
        markers=[ (["karteninhaber"], 12) ],  # 12 Zeilen nach dem Marker in den Chunk nehmen
    )

    # --- 2) Gültigkeit (Jahreszeitraum der Karte) prüfen ---
    # Aus der Rechnung den Gültigkeitszeitraum (oder "Gültigkeit") herausziehen
    g_von_s, g_bis_s = extract_period_from_rechnung(text)
    g_von = parse_pdf_date_dot(g_von_s)   # Beginn als datetime
    g_bis = parse_pdf_date_dot(g_bis_s)   # Ende als datetime

    # Aus dem Antrag den erwarteten Gültigkeitszeitraum laden
    a_von = parse_form_datetime(form_data.get("gilt_von", ""))
    a_bis = parse_form_datetime(form_data.get("gilt_bis", ""))

    # Gültigkeit ist ok, wenn beide Seiten gesetzt sind und exakt übereinstimmen
    guelt_ok = (
        g_von is not None and g_bis is not None
        and g_von.date() == a_von.date()
        and g_bis.date() == a_bis.date()
    )

    # --- 3) Leistungszeitraum (ein einzelner Abrechnungsmonat) prüfen ---
    # Viele Monatsrechnungen haben "Leistungszeitraum: DD.MM.YYYY - DD.MM.YYYY"
    lines = text.splitlines()
    l_von = l_bis = None  # Start/Ende des Leistungszeitraums

    for i, line in enumerate(lines):
        # Zeile suchen, die mit "Leistungszeitraum" beginnt (nach Normalisierung)
        if normalize_for_matching(line).startswith("leistungszeitraum"):
            # Ein paar Zeilen um diese Zeile herum zusammenfassen
            chunk = " ".join(lines[i:i + 5])
            # Zwei Datumswerte im Format DD.MM.YYYY (mit OCR-Toleranz) suchen
            m = re.search(rf"({DATE_PATTERN_DOT})\s*-\s*({DATE_PATTERN_DOT})", chunk)
            if m:
                l_von = parse_pdf_date_dot(m.group(1))
                l_bis = parse_pdf_date_dot(m.group(2))
            break  # nach dem ersten Treffer abbrechen

    # Leistungszeitraum ist ok, wenn:
    # - Start und Ende gefunden wurden
    # - und komplett innerhalb der Gültigkeit des Tickets liegen
    leist_ok = (
        l_von is not None and l_bis is not None
        and a_von.date() <= l_von.date() <= l_bis.date() <= a_bis.date()
    )

    # --- 4) Monats-Schlüssel für „einzigartige Monatsrechnung“ bauen ---
    # Idee: Jahr-Monat des Leistungsbeginns, z.B. 2024-09 für 15.09.2024–14.10.2024
    leist_month_key = None
    if l_von is not None:
        leist_month_key = f"{l_von.year:04d}-{l_von.month:02d}"

    # --- 5) Ergebnis-Dict aufbauen ---
    result = {
        "doc_type": "monatsrechnung",
        "name_ok": bool(name_ok),
        "name_context": name_context,
        "guelt_ok": bool(guelt_ok),
        "leist_ok": bool(leist_ok),
        "guelt_pdf_raw": {"von": g_von_s, "bis": g_bis_s},           # Rohtexte aus PDF
        "guelt_pdf_iso": {"von": _fmt_iso(g_von), "bis": _fmt_iso(g_bis)},
        "form_iso": {"von": a_von.date().isoformat(), "bis": a_bis.date().isoformat()},
        "leist_pdf_iso": {"von": _fmt_iso(l_von), "bis": _fmt_iso(l_bis)},
        "leist_month_key": leist_month_key,                          # NEU: Monatsschlüssel
        "all_ok": bool(name_ok and guelt_ok and leist_ok),           # Gesamtflag
    }

    # --- 6) Optionale Debug-Ausgaben für Konsole ---
    if verbose:
        print("Name-Match Monatsrechnung (near Karteninhaber):", result["name_ok"])
        # Wunschformat wie bei dir: "15.09.2024 - 14.09.2025 -> True"
        print("Gültigkeit Monatsrechnung:",
              _fmt_dot(g_von), "-", _fmt_dot(g_bis), "->", result["guelt_ok"])
        print("Leistungszeitraum Monatsrechnung:",
              _fmt_dot(l_von), "-", _fmt_dot(l_bis), "->", result["leist_ok"])
        print("Leistungs-Monatsschlüssel:", leist_month_key)

    return result