"""
registration_validation.py — Validierung von Meldezetteln / Meldebestätigungen
================================================================================

ÜBERBLICK
---------
Dieses Modul validiert Meldezettel-PDFs gegen Antragsdaten. Es ist der
zweite Validierungsbaustein neben invoice_validation.py:

    Pipeline-Position:
        PDF → document_loader → Text
        Text → document_classifier → ("meldezettel", 0.87)
        Text + Antragsdaten → registration_validation → {checks: ...}  ← HIER
        Alle Ergebnisse → decision_engine → Gesamtentscheidung

    Parallel dazu:
        Rechnungs-PDFs → invoice_validation → {name_ok, period_ok, ...}


WAS WIRD GEPRÜFT?
------------------
Ein Meldezettel muss vier Dinge bestätigen:

    ┌──────────────────┬─────────────────────────────────────────────────┐
    │ Prüfung          │ Was wird verglichen?                            │
    ├──────────────────┼─────────────────────────────────────────────────┤
    │ 1. Vorname       │ Antrag-Vorname ≈ Meldezettel-Vorname            │
    │ 2. Nachname      │ Antrag-Nachname ≈ Meldezettel-Nachname          │
    │ 3. Geburtsdatum  │ Antrag-Datum == Meldezettel-Datum (ISO)         │
    │ 4. PLZ           │ Meldezettel-PLZ ∈ Salzburger PLZ               │
    │                  │ UND Meldezettel-PLZ == Antrag-PLZ               │
    └──────────────────┴─────────────────────────────────────────────────┘

    all_ok = vorname_ok AND nachname_ok AND geburtsdatum_ok AND plz_ok


MELDEZETTEL-LAYOUTS: DAS HAUPTPROBLEM
--------------------------------------
Es gibt KEIN einheitliches Meldezettel-Layout in Österreich. Jede Gemeinde
kann ihr eigenes Format verwenden. Dieses Modul muss mit mindestens drei
bekannten Layouts umgehen:

    Layout A — "Inline mit Doppelpunkt" (häufigste Variante):
        Vorname: Max Michael
        Familienname: Mustermann
        Geburtsdatum: 01.01.1990

    Layout B — "Label auf einer Zeile, Wert auf der nächsten":
        Vorname:
        Max Michael
        Familienname:
        Mustermann

    Layout C — "Block-Layout" (z.B. Salzburg):
        Familienname oder Nachname:
        Vorname:
        Geschlecht:
        Geburtsdatum:
        Mustermann
        Max Michael
        männlich
        01.01.1990

        → Alle Labels stehen als Block, alle Werte als Block darunter.
          Die Zuordnung erfolgt über die Reihenfolge (1. Label → 1. Wert).

    Layout D — "Inline OHNE Doppelpunkt" (z.B. Linz):
        Vorname                Max
        Familienname           Mustermann
        Geburtsdatum           01.06.1985

        → Kein ":" nach dem Label, nur Leerzeichen-Trennung.
          Erkannt durch Splitting an 2+ aufeinanderfolgenden Leerzeichen.

    extract_value_after_label() handhabt alle vier Layouts automatisch.


ABHÄNGIGKEITEN
--------------
    utils.py → normalize_for_matching(), _compact(), _variants_for_umlaut_translit()
    (Geteilte Hilfsfunktionen, auch von invoice_validation.py verwendet)
"""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Optional, Dict, Any

# Shared Hilfsfunktionen aus utils.py:
#   normalize_for_matching()  → Lowercase, Umlaute entfernen, Whitespace normalisieren
#   _compact()                → Alle Leerzeichen entfernen ("a b c" → "abc")
#   _variants_for_umlaut_translit() → Varianten: "jürgen" → ["juergen", "jurgen"]
from utils import normalize_for_matching, _compact, _variants_for_umlaut_translit


# =============================================================================
# 1) LABEL/VALUE-EXTRAKTION AUS MELDEZETTEL
# =============================================================================
#
# Das schwierigste Problem in diesem Modul: Aus dem OCR-Text eines
# Meldezettels die richtigen Werte zu den richtigen Labels zuordnen.
#
# Die Extraktion muss vier verschiedene Layouts erkennen (siehe Modul-Docstring)
# und dabei robust gegen OCR-Fehler sein.

# ─────────────────────────────────────────────────────────────────────────────
# 1a) BEKANNTE LABELS
# ─────────────────────────────────────────────────────────────────────────────
#
# Diese Menge enthält alle Labels, die auf österreichischen Meldezetteln
# vorkommen können. Sie wird verwendet, um zu erkennen, ob eine Zeile
# ein Label ist (→ Wert auf der nächsten Zeile) oder bereits ein Wert.
#
# Wichtig: Die Labels sind KLEINGESCHRIEBEN und OHNE Umlaute, weil sie
# mit normalize_for_matching() verglichen werden.
#
# "vomame" ist ein bekannter OCR-Fehler: Wenn der Textlayer kaputt ist,
# wird "Vorname" oft als "Vomame" gelesen (r/n → m).
# → document_loader.py erkennt diesen Fall und macht OCR stattdessen,
#   aber als Sicherheitsnetz akzeptieren wir "vomame" trotzdem als Label.

_LABELS = {
    "familienname",                   # Standard-Label
    "familienname oder nachname",     # Manche Gemeinden verwenden dieses Label
    "nachname",                       # Kurzform
    "vorname",                        # Standard
    "vomame",                         # OCR-Fehler für "Vorname" (r/n → m)
    "geschlecht",                     # Wird nicht validiert, aber als Label erkannt
    "geburtsdatum",                   # Standard
    "geburtsort",                     # Wird nicht validiert, aber als Label erkannt
    "staatsangehorigkeit",            # Normalisiert (ö → o)
    "zmr zahl",                       # Variante 1 (mit Leerzeichen)
    "zmr-zahl",                       # Variante 2 (mit Bindestrich)
    "zmrzahl",                        # Variante 3 (zusammengeschrieben)
}

# Normalisierte Versionen der Labels (via normalize_for_matching):
# "familienname oder nachname" → "familienname oder nachname" (bereits lowercase)
# Werden für den Vergleich mit normalisierten Zeilen verwendet.
_LABELS_NORM = {normalize_for_matching(x) for x in _LABELS}

# Compact-Versionen (alle Leerzeichen entfernt):
# "familienname oder nachname" → "familiennameodernachname"
# Werden gebraucht, weil OCR Labels manchmal mit Leerzeichen durchsetzt:
# "Staatsa ngehörig keit" → normalisiert: "staatsa ngehorig keit"
# → compact: "staatsangehorigkkeit" → matcht gegen "staatsangehorigkeit"
_LABELS_COMPACT = {_compact(x) for x in _LABELS_NORM}


# ─────────────────────────────────────────────────────────────────────────────
# 1b) LABEL-ERKENNUNG
# ─────────────────────────────────────────────────────────────────────────────
#
# Drei Hilfsfunktionen, die zusammenarbeiten:
#
#   _label_key_of(line)       → Extrahiert den Label-Teil einer Zeile
#   _is_label_only_line(line) → Ist diese Zeile NUR ein Label (ohne Wert)?
#   _matches_label_line(line) → Matcht diese Zeile ein bestimmtes Label?

def _label_key_of(line: str) -> str:
    """
    Extrahiert den normalisierten Label-Teil einer Zeile.

    Regeln:
        "Vorname: Max"   → "" (hat bereits einen Wert → kein Label-only)
        "Vorname:"          → "vorname" (Label ohne Wert)
        "Vorname"           → "vorname" (Label ohne Doppelpunkt, z.B. Linz-Layout)
        ""                  → "" (leere Zeile)

    Warum gibt "Vorname: Max" einen leeren String zurück?
        Im Block-Layout (Layout C) muss die Funktion erkennen, ob eine Zeile
        ein "reines Label" ist. "Vorname: Max" ist KEIN reines Label,
        weil der Wert bereits in der gleichen Zeile steht.
        Wenn wir das als Label behandeln würden, würde die Block-Logik
        die nächste Zeile als Wert nehmen → falsche Zuordnung.

    Parameter:
        line: Eine Zeile aus dem Meldezettel-Text

    Rückgabe:
        Normalisierter Label-String, oder "" wenn kein reines Label
    """
    s = (line or "").strip()
    if not s:
        return ""

    if ":" in s:
        left, right = s.split(":", 1)
        if right.strip():
            return ""  # Hat bereits Wert rechts vom ":" → kein Label-only
        return normalize_for_matching(left)

    # Kein Doppelpunkt → die ganze Zeile könnte ein Label sein (Linz-Layout)
    return normalize_for_matching(s)


def _is_label_only_line(line: str) -> bool:
    """
    Prüft, ob eine Zeile NUR ein Personendaten-Label ist (ohne Wert).

    Gibt True zurück GENAU DANN, wenn der Label-Text einem der bekannten
    Labels aus _LABELS entspricht.

    Warum so streng?
        Früher gab es eine generische Heuristik ("sieht aus wie Label").
        Das führte zu Fehlern bei Salzburg-Meldezetteln, wo Sätze wie
        "Im lokalen Melderegister..." oder "Wohnsitzqualität..." fälschlich
        als Labels erkannt wurden und die Zuordnung verschoben.

        Jetzt: NUR echte Personendaten-Labels werden akzeptiert.

    OCR-Robustheit:
        OCR kann Labels intern trennen: "Staatsa ngehörig keit"
        → normalisiert: "staatsa ngehorig keit"
        → compact: "staatsangehorigkkeit"
        → wird mit _LABELS_COMPACT verglichen → Treffer!

    Parameter:
        line: Eine Zeile aus dem Meldezettel-Text

    Rückgabe:
        True wenn die Zeile ein bekanntes Label ist, sonst False

    Beispiele:
        "Vorname:"                    → True (bekanntes Label)
        "Vorname: Max"             → False (hat bereits Wert)
        "Im lokalen Melderegister..." → False (kein bekanntes Label)
        "Max Michael"                → False (ein Wert, kein Label)
    """
    key = _label_key_of(line)
    if not key:
        return False
    return (key in _LABELS_NORM) or (_compact(key) in _LABELS_COMPACT)


def _matches_label_line(line: str, label_key_norm: str) -> bool:
    """
    Prüft, ob eine Zeile das gesuchte Label enthält.

    Tolerant gegen:
        - Groß/Kleinschreibung (via normalize_for_matching)
        - Teilstring-Match ("familienname oder nachname" enthält "familienname")
        - OCR-Leerzeichen ("Familien name" → compact "familienname")

    Parameter:
        line:           Eine Zeile aus dem Meldezettel-Text
        label_key_norm: Das gesuchte Label (bereits normalisiert)

    Rückgabe:
        True wenn die Zeile das gesuchte Label enthält

    Beispiele:
        _matches_label_line("Familienname oder Nachname:", "familienname") → True
        _matches_label_line("Vorname: Max", "vorname")                 → True
        _matches_label_line("Geburtsdatum:", "vorname")                   → False
    """
    s = (line or "").strip()
    if not s:
        return False

    # Nur den Teil VOR dem Doppelpunkt normalisieren (falls vorhanden)
    if ":" in s:
        left = s.split(":", 1)[0]
        ln = normalize_for_matching(left)
    else:
        ln = normalize_for_matching(s)

    if not ln:
        return False

    # Vier Vergleichsstrategien (von genau zu tolerant):
    return (
        ln == label_key_norm                                 # Exakt: "vorname" == "vorname"
        or label_key_norm in ln                              # Teilstring: "vorname" in "vorname und zweitname"
        or _compact(ln) == _compact(label_key_norm)          # Compact exakt: "vomame" == "vorname"? Nein, aber OCR: "vor name" == "vorname"
        or _compact(label_key_norm) in _compact(ln)          # Compact Teilstring
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1c) HAUPTEXTRAKTION: LABEL → WERT
# ─────────────────────────────────────────────────────────────────────────────
#
# Dies ist die ZENTRALE Extraktionsfunktion. Sie behandelt alle vier
# Meldezettel-Layouts (A, A2, B, C) automatisch:
#
#   Layout A:  "Vorname: Max Michael"       → Wert rechts vom ":"
#   Layout A2: "Vorname                Max" → Wert nach 2+ Leerzeichen
#   Layout B:  "Vorname:" + nächste Zeile     → Wert auf Folgezeile
#   Layout C:  Label-Block + Werte-Block      → Wert an gleicher Position
#
# Die Funktion probiert die Layouts in Reihenfolge A → A2 → B → C durch.
# Sobald ein Wert gefunden wird, wird er zurückgegeben.

def extract_value_after_label(lines: list[str], label: str) -> Optional[str]:
    """
    Extrahiert den Wert zu einem Label aus einem Meldezettel.

    Handhabt vier verschiedene Layouts:

    Layout A — Inline mit Doppelpunkt:
        "Vorname: Max Michael"
        → Rückgabe: "Max Michael"

    Layout A2 — Inline ohne Doppelpunkt (z.B. Linz):
        "Vorname                Max"
        → Erkennung: kein ":" in der Zeile + 2+ Leerzeichen trennen Label und Wert
        → Rückgabe: "Max"
        → Schutz: Der rechte Teil darf kein weiteres Label sein

    Layout B — Label und Wert auf getrennten Zeilen:
        "Vorname:"
        "Max Michael"
        → Rückgabe: "Max Michael"

    Layout C — Block-Layout (z.B. Salzburg):
        "Familienname oder Nachname:"
        "Vorname:"
        "Geschlecht:"
        "Geburtsdatum:"
        "Mustermann"
        "Max Michael"
        "männlich"
        "01.01.1990"
        → Labels und Werte stehen in getrennten Blöcken
        → Zuordnung über Position: 2. Label → 2. Wert
        → Rückgabe für "Vorname": "Max Michael" (Position 2)

    Parameter:
        lines: Liste von Textzeilen (text.splitlines())
        label: Das gesuchte Label (z.B. "Vorname", "Geburtsdatum")

    Rückgabe:
        Der extrahierte Wert als String, oder None wenn nicht gefunden.
    """
    label_key = normalize_for_matching(label)

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        # Ist diese Zeile unser gesuchtes Label?
        if _matches_label_line(line, label_key):

            # ── Layout A: Wert rechts vom ":" ──
            # "Vorname: Max Michael"  → right = "Max Michael"
            if ":" in line:
                right = line.split(":", 1)[1].strip()
                if right:
                    return right

            # ── Layout A2: Inline ohne Doppelpunkt (Linz) ──
            # "Vorname                Max"
            # Kein ":" vorhanden → Splitten an 2+ Leerzeichen.
            # Das erste Teil ist das Label, das zweite der Wert.
            #
            # Wichtig: Der Wert darf KEIN weiteres Label sein!
            # Sonst würde "Familienname                Vorname" den
            # Wert "Vorname" zurückgeben (was falsch wäre).
            if ":" not in line:
                parts = re.split(r'\s{2,}', line.strip(), maxsplit=1)
                if len(parts) == 2:
                    potential_value = parts[1].strip()
                    if potential_value and not _is_label_only_line(potential_value):
                        return potential_value

            # ── Nächste nicht-leere Zeile suchen ──
            # Für Layout B und C brauchen wir die Folgezeile(n).
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines):
                return None

            # ── Layout B: Wert auf der nächsten Zeile ──
            # "Vorname:"      ← Label
            # "Max Michael"  ← Wert (kein Label-only)
            if not _is_label_only_line(lines[k]):
                return lines[k].strip()

            # ── Layout C: Block-Layout ──
            # Die nächste Zeile ist AUCH ein Label → wir sind in einem Label-Block.
            # Strategie:
            #   1) Label-Block nach oben erweitern (alle zusammenhängenden Labels)
            #   2) Index unseres Labels im Block bestimmen
            #   3) Werte-Block (nach den Labels) lesen
            #   4) Wert an der gleichen Position zurückgeben

            # Schritt 1: Start des Label-Blocks nach oben suchen
            # Wir gehen von der aktuellen Zeile rückwärts, solange Labels kommen.
            start = i
            while start - 1 >= 0 and _is_label_only_line(lines[start - 1]):
                start -= 1

            # Schritt 2: Alle Labels im Block sammeln (vorwärts ab start)
            labels: list[str] = []
            end = start
            while end < len(lines):
                cur = lines[end].strip()
                if not cur:
                    end += 1        # Leere Zeilen überspringen
                    continue
                if _is_label_only_line(cur):
                    labels.append(_label_key_of(cur))
                    end += 1
                    continue
                break  # Erste Nicht-Label-Zeile → Werteblock beginnt hier

            if not labels:
                return None

            # Schritt 3: Index unseres Labels im Label-Block finden
            # (auch mit Compact-Matching für OCR-Robustheit)
            idx: Optional[int] = None
            for pos, lab in enumerate(labels):
                if (
                    lab == label_key
                    or label_key in lab
                    or _compact(lab) == _compact(label_key)
                    or _compact(label_key) in _compact(lab)
                ):
                    idx = pos
                    break

            if idx is None:
                return None

            # Schritt 4: Werte sammeln (ab end) und idx-ten Wert zurückgeben
            # Der Werteblock beginnt bei 'end' (erste Nicht-Label-Zeile).
            # Wir sammeln Werte, bis wir genug haben (idx + 1 Stück).
            values: list[str] = []
            p = end
            while p < len(lines) and len(values) <= idx:
                v = lines[p].strip()
                if v:
                    # Sicherheitsnetz: Falls im Werteblock doch ein Label auftaucht
                    # (unerwartetes Layout), überspringen wir es.
                    if _is_label_only_line(v):
                        p += 1
                        continue
                    values.append(v)
                p += 1

            return values[idx] if len(values) > idx else None

    # Label wurde im gesamten Text nicht gefunden
    return None


# =============================================================================
# 2) FELD-EXTRAKTION
# =============================================================================
#
# Vier Convenience-Funktionen, die extract_value_after_label() für
# die spezifischen Felder aufrufen. Sie kapseln die Label-Varianten
# und Nachbearbeitung (z.B. Geburtsdatum-Normalisierung).

def extract_first_name_from_melde(text: str) -> Optional[str]:
    """
    Extrahiert den Vornamen aus einem Meldezettel-Text.

    Sucht nach dem Label "Vorname" (oder OCR-Variante "Vomame").

    Rückgabe:
        Der Vorname als String (z.B. "Max Michael"), oder None.
    """
    lines = text.splitlines()
    return extract_value_after_label(lines, "Vorname")


def extract_last_name_from_melde(text: str) -> Optional[str]:
    """
    Extrahiert den Nachnamen aus einem Meldezettel-Text.

    Problem: Verschiedene Gemeinden verwenden verschiedene Labels:
        - "Familienname oder Nachname" (z.B. Salzburg, Wien)
        - "Familienname" (häufigste Variante)
        - "Nachname" (selten, aber vorkommend)

    Strategie: Alle drei Varianten durchprobieren, erste Treffer zurückgeben.
    Reihenfolge: Spezifischstes Label zuerst (vermeidet Fehlzuordnungen).

    Rückgabe:
        Der Nachname als String (z.B. "Mustermann"), oder None.
    """
    lines = text.splitlines()
    for lbl in ("Familienname oder Nachname", "Familienname", "Nachname"):
        v = extract_value_after_label(lines, lbl)
        if v:
            return v
    return None


def normalize_birthdate(value: str) -> Optional[str]:
    """
    Normalisiert ein Geburtsdatum auf ISO-Format (YYYY-MM-DD).

    Akzeptierte Eingabeformate:
        "1985-06-15"              → "1985-06-15"   (ISO)
        "1985.06.15"              → "1985-06-15"   (ISO mit Punkten)
        "01.06.1985"              → "1985-06-15"   (deutsches Format)
        "01,01,1990"              → "1990-01-01"   (OCR mit Kommas)
        "01,01.1990"              → "1990-01-01"   (OCR gemischt)
        "1990-01-01 00:00:00"     → "1990-01-01"   (ISO mit Uhrzeit)
        "1990-01-01T00:00:00"     → "1990-01-01"   (ISO mit T-Separator)

    OCR-Korrekturen:
        - O/o zwischen Ziffern → 0: "O1.O6.1985" → "01.06.1985"
        - l/I zwischen Ziffern → 1: "l5.06.1985" → "15.06.1985"
        - Kommas → Punkte: "01,01,1990" → "01.01.1990"
        - Leerzeichen entfernen: "01. 06. 1985" → "01.06.1985"

    Rückgabe:
        ISO-formatiertes Datum "YYYY-MM-DD", oder None bei ungültigem Format.

    Warum ISO?
        Beide Seiten (Antrag und Meldezettel) werden auf ISO normalisiert,
        dann ist der Vergleich ein einfacher String-Vergleich:
            "1990-01-01" == "1990-01-01" → True
    """
    if not value:
        return None

    v = (value or "").strip()

    # OCR-Bereinigung: Leerzeichen entfernen, Kommas zu Punkten
    v = v.replace(" ", "").replace(",", ".")

    # OCR-Fehler: O/o → 0 (zwischen Ziffern)
    # "O1.O6.1985" → "01.06.1985"
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)

    # OCR-Fehler: l/I → 1 (zwischen Ziffern)
    # "l5.06.1985" → "15.06.1985"
    v = re.sub(r"(?<=\d)[lI](?=\d)", "1", v)

    # Versuch 1: ISO-Parsing (inkl. Uhrzeit)
    # fromisoformat versteht: "1990-01-01", "1990-01-01T00:00:00"
    try:
        dt = datetime.fromisoformat(v)
        return dt.date().isoformat()
    except ValueError:
        pass

    # Versuch 2: Explizite Formate
    # Reihenfolge wichtig: YYYY-MM-DD vor DD.MM.YYYY
    # (sonst wird "1990-01-15" falsch geparst)
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            dt2 = datetime.strptime(v, fmt).date()
            return dt2.isoformat()
        except ValueError:
            continue

    return None


def extract_birthdate_from_melde(text: str) -> Optional[str]:
    """
    Extrahiert und normalisiert das Geburtsdatum aus einem Meldezettel.

    Ablauf:
        1. Label "Geburtsdatum" suchen → Rohwert extrahieren
        2. Rohwert normalisieren → ISO-Format "YYYY-MM-DD"

    Rückgabe:
        ISO-formatiertes Geburtsdatum, oder None.

    Beispiel:
        Text enthält "Geburtsdatum: 01.01.1990"
        → extract_value_after_label → "01.01.1990"
        → normalize_birthdate → "1990-01-01"
    """
    lines = text.splitlines()
    raw = extract_value_after_label(lines, "Geburtsdatum")
    return normalize_birthdate(raw) if raw else None


def extract_current_main_residence_postal_code(text: str) -> Optional[str]:
    """
    Extrahiert die PLZ des Hauptwohnsitzes aus einem Meldezettel.

    Strategie:
        1. Zeile mit "Hauptwohnsitz" finden (case-insensitive, normalisiert)
        2. Ab dieser Zeile nach der ersten 4-stelligen Zahl suchen

    Warum ab "Hauptwohnsitz" und nicht einfach die erste PLZ im Text?
        Meldezettels enthalten oft mehrere Adressen:
            - Hauptwohnsitz: 5020 Salzburg
            - Nebenwohnsitz: 4020 Linz
        Wir brauchen die PLZ des HAUPTwohnsitzes, nicht irgendeinen.

    Warum 4-stellig?
        Österreichische PLZ sind immer genau 4 Ziffern (1010 bis 9992).
        Das Regex \\b\\d{4}\\b findet exakt solche Zahlen.

    OCR-Robustheit:
        - "Hauptwohnsitz" wird normalisiert gesucht (Umlaute, Groß/Klein)
        - \\b (Wortgrenze) verhindert, dass Teile von längeren Zahlen
          als PLZ erkannt werden (z.B. "12345" → nicht "1234")

    Parameter:
        text: Extrahierter Meldezettel-Text

    Rückgabe:
        4-stellige PLZ als String (z.B. "5020"), oder None.
    """
    lines = text.splitlines()

    # Schritt 1: Zeile mit "Hauptwohnsitz" finden
    start_idx = None
    for i, line in enumerate(lines):
        # Normalisiert suchen: "Hauptwohnsitz" → "hauptwohnsitz"
        # Auch: "HAUPTWOHNSITZ", "Hauptwohn sitz" (OCR) werden gefunden
        if "hauptwohnsitz" in normalize_for_matching(line):
            start_idx = i
            break

    if start_idx is None:
        return None

    # Schritt 2: Ab der Hauptwohnsitz-Zeile die erste 4-stellige Zahl suchen
    # Die PLZ steht typischerweise in der gleichen oder nächsten Zeile:
    #   "Hauptwohnsitz Musterstraße 1, 5020 Salzburg"
    #   oder
    #   "Hauptwohnsitz
    #    5020 Salzburg"
    for line in lines[start_idx:]:
        match = re.search(r"\b\d{4}\b", line)
        if match:
            return match.group(0)

    return None


# =============================================================================
# 3) MATCHING-REGELN
# =============================================================================
#
# Vier Vergleichsfunktionen, eine pro Feld.
# Jede vergleicht den Antragswert mit dem extrahierten Meldezettel-Wert.
#
# Namen-Matching ist FLEXIBEL (Umlaute, OCR-Fehler, Doppelnamen).
# Datums-Matching ist EXAKT (beide Seiten auf ISO normalisiert).
# PLZ-Matching ist EXAKT (4-stelliger String-Vergleich).

def first_name_matches(form_vorname: str, melde_vorname: Optional[str]) -> bool:
    """
    Prüft, ob der Vorname aus dem Antrag zum Meldezettel-Vornamen passt.

    Matching-Strategie:
        1. Token-Match: Erster Vorname aus Antrag als Wort im Meldezettel?
           Antrag: "Max", Meldezettel: "Max Michael" → True ✓

        2. Compact-Match: OCR hat Leerzeichen verschluckt?
           Antrag: "Max", Meldezettel: "MaxMichael" → True ✓

        3. Umlaut-Varianten: Transliteration berücksichtigen?
           Antrag: "Jürgen", Meldezettel: "Juergen" → True ✓

    Warum nur der ERSTE Vorname?
        Im Antrag steht typischerweise nur der Rufname ("Max"),
        aber auf dem Meldezettel stehen alle Vornamen ("Max Michael").
        Der erste Vorname muss reichen.

    Parameter:
        form_vorname:  Vorname aus dem Antrag
        melde_vorname: Vorname aus dem Meldezettel (kann None sein)

    Rückgabe:
        True wenn der Vorname matcht.
    """
    if not melde_vorname:
        return False

    f_norm = normalize_for_matching(form_vorname)
    m_norm = normalize_for_matching(melde_vorname)

    if not f_norm or not m_norm:
        return False

    # Nur den ERSTEN Vornamen verwenden (Rufname)
    f_first = f_norm.split()[0]

    # ── Ebene 1: Token-Match ──
    # "max" in {"max", "michael"} → True
    if f_first in set(m_norm.split()):
        return True

    # ── Ebene 2: Compact-Match (OCR) ──
    # "max" in "maxmichael" → True
    if _compact(f_first) in _compact(m_norm):
        return True

    # ── Ebene 3: Umlaut-Varianten ──
    # "jurgen" → Varianten: ["juergen", "jurgen"] → prüfe alle
    for v in _variants_for_umlaut_translit(f_first):
        if v in set(m_norm.split()) or _compact(v) in _compact(m_norm):
            return True

    return False


def last_name_matches(form_nachname: str, melde_nachname: Optional[str]) -> bool:
    """
    Prüft, ob der Nachname aus dem Antrag zum Meldezettel-Nachnamen passt.

    Matching-Strategie (toleranter als Vorname):
        1. Exakt oder Teilstring nach Normalisierung:
           "mustermann" == "mustermann" → True ✓
           "mustermann" in "mustermann beispiel" → True ✓ (Doppelname)

        2. Alle Tokens müssen vorkommen (Doppelname):
           Antrag: "Muster Beispiel" → ["muster", "beispiel"]
           Meldezettel: "Beispiel Muster" → alle da → True ✓

        3. Compact-Match (OCR):
           "musterbeispiel" in "musterbeispieleva" → True ✓

        4. Umlaut-Varianten:
           "muster" → "muster" → prüfe Compact

    Parameter:
        form_nachname:  Nachname aus dem Antrag
        melde_nachname: Nachname aus dem Meldezettel (kann None sein)

    Rückgabe:
        True wenn der Nachname matcht.

    Unterschied zum Vornamen:
        Beim Nachnamen gibt es einen Teilstring-Check (f_norm in m_norm),
        der beim Vornamen fehlt. Das hilft bei OCR-Zusätzen:
        Meldezettel: "MUSTERMANN geb. BEISPIEL" → "mustermann" in "mustermann geb beispiel" → True
    """
    if not melde_nachname:
        return False

    f_norm = normalize_for_matching(form_nachname)
    m_norm = normalize_for_matching(melde_nachname)

    if not f_norm or not m_norm:
        return False

    # ── Ebene 1: Exakt oder Teilstring ──
    # Hilfreich bei Doppelnamen und OCR-Zusätzen (z.B. "geb.")
    if f_norm == m_norm or f_norm in m_norm or m_norm in f_norm:
        return True

    # ── Ebene 2: Doppelname-Logik ──
    # Antrag: "Muster Beispiel" → Tokens: ["muster", "beispiel"]
    # Beide müssen im Meldezettel vorkommen (Reihenfolge egal)
    f_tokens = f_norm.split()
    m_tokens = set(m_norm.split())
    if f_tokens and all(t in m_tokens for t in f_tokens):
        return True

    # ── Ebene 3: Compact-Match (OCR) ──
    if _compact(f_norm) in _compact(m_norm):
        return True

    # ── Ebene 4: Umlaut-Varianten ──
    for v in _variants_for_umlaut_translit(f_norm):
        if _compact(v) in _compact(m_norm):
            return True

    return False


def birthdate_matches(form_date: str, melde_date_iso: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Prüft, ob das Geburtsdatum aus dem Antrag mit dem Meldezettel übereinstimmt.

    Ablauf:
        1. Antragsdatum auf ISO normalisieren (normalize_birthdate)
        2. String-Vergleich: "1990-01-01" == "1990-01-01"

    Warum String-Vergleich und nicht datetime?
        Beide Seiten sind bereits auf ISO normalisiert. Ein String-Vergleich
        ist schneller und vermeidet Probleme mit Uhrzeiten/Zeitzonen.

    Parameter:
        form_date:      Geburtsdatum aus dem Antrag (beliebiges Format)
        melde_date_iso: Geburtsdatum aus dem Meldezettel (bereits ISO!)

    Rückgabe:
        Tupel (match_ok, form_date_iso):
            match_ok:      True wenn die Daten übereinstimmen
            form_date_iso: Das normalisierte Antragsdatum (für Debug/UI)

    Beispiel:
        birthdate_matches("01.01.1990", "1990-01-01")
        → (True, "1990-01-01")
    """
    if not melde_date_iso:
        return False, None
    form_iso = normalize_birthdate(form_date)
    return (form_iso is not None and form_iso == melde_date_iso), form_iso


# =============================================================================
# 4) PLZ-REGEL (FÖRDERBERECHTIGUNG)
# =============================================================================
#
# Die Förderung gilt NUR für Personen mit Hauptwohnsitz in der Stadt Salzburg.
# Die Stadt Salzburg hat mehrere PLZ (nicht nur 5020), weil verschiedene
# Stadtteile eigene PLZ haben.
#
# Diese Menge muss aktualisiert werden, wenn sich die Förderbedingungen ändern
# oder neue PLZ hinzukommen.

SALZBURG_PLZ = {
    "5010",    # Salzburg (Altstadt, Zentrum)
    "5014",    # Salzburg (Leopoldskron-Moos)
    "5017",    # Salzburg (Mülln, Maxglan)
    "5018",    # Salzburg (Maxglan-West)
    "5020",    # Salzburg (Hauptpostleitzahl, Nonntal, Parsch, Aigen, Gnigl)
    "5023",    # Salzburg (Gaisberg)
    "5025",    # Salzburg (Josefiau, Herrnau)
    "5026",    # Salzburg (Salzburg-Süd)
    "5027",    # Salzburg (Berchtesgadner Straße)
    "5033",    # Salzburg (Langwied, Kasern)
}


def is_postcode_foerderberechtigt(plz: str) -> bool:
    """
    Prüft, ob eine PLZ zur Stadt Salzburg gehört und damit förderberechtigt ist.

    Parameter:
        plz: 4-stellige PLZ als String (z.B. "5020")

    Rückgabe:
        True wenn die PLZ in SALZBURG_PLZ enthalten ist.

    Beispiele:
        is_postcode_foerderberechtigt("5020") → True  (Salzburg)
        is_postcode_foerderberechtigt("4020") → False (Linz)
        is_postcode_foerderberechtigt("")     → False (leer)
    """
    return (plz or "").strip() in SALZBURG_PLZ


# =============================================================================
# 5) HAUPTFUNKTION: MELDEZETTEL VALIDIEREN
# =============================================================================
#
# Diese Funktion wird von der Decision Engine aufgerufen:
#   decision_engine.build_overall_decision()
#       → validate_meldezettel(form_data, melde_text)
#       → {all_ok: True/False, checks: {...}, extracted: {...}}
#
# Sie führt alle vier Prüfungen durch und gibt ein strukturiertes
# Ergebnis-Dict zurück.

def validate_meldezettel(form_data: dict, melde_text: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Validiert einen Meldezettel gegen Antragsdaten.

    Ablauf:
        1. Vier Felder aus dem Meldezettel extrahieren
        2. Jedes Feld gegen den Antrag prüfen
        3. Strukturiertes Ergebnis-Dict zurückgeben

    Prüfungen:
        vorname_ok:      Erster Vorname aus Antrag ≈ Meldezettel-Vorname
        nachname_ok:     Nachname aus Antrag ≈ Meldezettel-Nachname
        geburtsdatum_ok: Geburtsdatum Antrag == Meldezettel (nach ISO-Normalisierung)
        plz_ok:          PLZ förderberechtigt (Salzburg) UND PLZ Antrag == Meldezettel

    Parameter:
        form_data:  Dict mit Antragsdaten. Erwartete Keys:
                    "vorname", "familienname", "geburtsdatum", "plz"
        melde_text: Extrahierter Text aus dem Meldezettel-PDF
        verbose:    Debug-Ausgaben auf Konsole? (Default: False)

    Rückgabe (dict):
        doc_type:    "meldezettel"
        extracted:   {vorname_full, nachname, geburtsdatum_iso, plz}
                     → Was aus dem Meldezettel extrahiert wurde
        form_norm:   {geburtsdatum_iso}
                     → Normalisiertes Antragsdatum (für Vergleich/Debug)
        checks:      {vorname_ok, nachname_ok, geburtsdatum_ok, plz_ok,
                      plz_ok_melde, plz_ok_form}
                     → Einzelergebnisse jeder Prüfung
        all_ok:      bool
                     → Gesamtergebnis (AND aller Checks)

    PLZ-Prüfung im Detail:
        Die PLZ wird in ZWEI Schritten geprüft:

        1. plz_ok_melde: Ist die Meldezettel-PLZ förderberechtigt?
           → PLZ ∈ SALZBURG_PLZ
           Prüft: Hat die Person ihren Hauptwohnsitz in der Stadt Salzburg?

        2. plz_ok_form: Stimmt die Antrag-PLZ mit der Meldezettel-PLZ überein?
           → form_plz == melde_plz
           Prüft: Hat der Antragsteller die richtige PLZ angegeben?

        3. plz_ok = plz_ok_melde AND plz_ok_form
           → Beide müssen stimmen.

    Beispiel:
        >>> result = validate_meldezettel(
        ...     {"vorname": "Max", "familienname": "Mustermann",
        ...      "geburtsdatum": "01.01.1990", "plz": "5020"},
        ...     meldezettel_text
        ... )
        >>> result["all_ok"]
        True
        >>> result["checks"]["vorname_ok"]
        True
    """

    # ── 1) Felder aus Meldezettel extrahieren ──
    # Jede Funktion sucht das entsprechende Label und gibt den Wert zurück.
    melde_vorname_full = extract_first_name_from_melde(melde_text)     # z.B. "Max Michael"
    melde_nachname = extract_last_name_from_melde(melde_text)          # z.B. "Mustermann"
    melde_geburtsdatum_iso = extract_birthdate_from_melde(melde_text)  # z.B. "1990-01-01"
    current_plz = extract_current_main_residence_postal_code(melde_text)  # z.B. "5020"

    # ── 2) Vorname prüfen ──
    vorname_ok = first_name_matches(
        form_data.get("vorname", ""),
        melde_vorname_full,
    )

    # ── 3) Nachname prüfen ──
    nachname_ok = last_name_matches(
        form_data.get("familienname", ""),
        melde_nachname,
    )

    # ── 4) Geburtsdatum prüfen ──
    # birthdate_matches() normalisiert beide Seiten auf ISO und vergleicht.
    geburtsdatum_ok, form_geburtsdatum_iso = birthdate_matches(
        form_data.get("geburtsdatum", ""),
        melde_geburtsdatum_iso,
    )

    # ── 5) PLZ prüfen (zwei Teilprüfungen) ──
    current_plz = extract_current_main_residence_postal_code(melde_text)
    form_plz = (form_data.get("plz") or "").strip()

    # Teilprüfung 1: Ist die Meldezettel-PLZ eine Salzburger PLZ?
    # → Nur Personen mit Hauptwohnsitz in der Stadt Salzburg sind förderberechtigt.
    plz_ok_melde = (current_plz is not None) and is_postcode_foerderberechtigt(current_plz)

    # Teilprüfung 2: Stimmt die Antrag-PLZ mit der Meldezettel-PLZ überein?
    # → Verhindert, dass jemand eine falsche PLZ im Antrag angibt.
    plz_ok_form = bool(current_plz and form_plz and current_plz == form_plz)

    # Gesamt-PLZ: Beide Teilprüfungen müssen bestehen.
    plz_ok = plz_ok_melde and plz_ok_form

    # ── 6) Ergebnis-Dict aufbauen ──
    result: Dict[str, Any] = {
        "doc_type": "meldezettel",

        # Was aus dem Meldezettel extrahiert wurde (für Debug/UI)
        "extracted": {
            "vorname_full": melde_vorname_full,        # z.B. "Max Michael"
            "nachname": melde_nachname,                # z.B. "Mustermann"
            "geburtsdatum_iso": melde_geburtsdatum_iso,  # z.B. "1990-01-01"
            "plz": current_plz,                        # z.B. "5020"
        },

        # Normalisiertes Antragsdatum (für Vergleich in UI)
        "form_norm": {
            "geburtsdatum_iso": form_geburtsdatum_iso,   # z.B. "1990-01-01"
        },

        # Einzelergebnisse jeder Prüfung
        "checks": {
            "vorname_ok": bool(vorname_ok),
            "nachname_ok": bool(nachname_ok),
            "geburtsdatum_ok": bool(geburtsdatum_ok),
            "plz_ok": bool(plz_ok),              # Gesamt-PLZ (beide Teile)
            "plz_ok_melde": bool(plz_ok_melde),  # PLZ förderberechtigt?
            "plz_ok_form": bool(plz_ok_form),    # PLZ Antrag == Meldezettel?
        },

        # Gesamtergebnis: Alle vier Hauptprüfungen müssen bestehen
        "all_ok": bool(vorname_ok and nachname_ok and geburtsdatum_ok and plz_ok),
    }

    # ── 7) Debug-Ausgaben ──
    if verbose:
        print("Vorname-Match:", result["checks"]["vorname_ok"])
        print("Nachname-Match:", result["checks"]["nachname_ok"])
        print("Geburtsdatum-Match:", result["checks"]["geburtsdatum_ok"])
        print("PLZ förderberechtigt:", result["checks"]["plz_ok"])

        print("DEBUG melde_nachname:", melde_nachname)
        print("DEBUG melde_vorname_full:", melde_vorname_full)
        print("DEBUG melde_geburtsdatum_iso:", melde_geburtsdatum_iso)
        print("DEBUG form_geburtsdatum_iso:", form_geburtsdatum_iso)
        print("PLZ (Meldezettel) förderberechtigt:", plz_ok_melde)
        print("PLZ Formular = PLZ Meldezettel:", plz_ok_form)

    return result


# =============================================================================
# 6) ABWÄRTSKOMPATIBILITÄT
# =============================================================================
#
# Die alte Funktion process_meldezettel() wurde in früheren Versionen von
# main.py aufgerufen. Sie gab nur print()-Ausgaben aus und hatte kein
# strukturiertes Return.
#
# Diese Wrapper-Funktion stellt sicher, dass alter Code weiterhin funktioniert,
# bis er auf validate_meldezettel() umgestellt wird.

def process_meldezettel(form_data: dict, melde_text: str) -> Dict[str, Any]:
    """
    DEPRECATED: Alte Entry-Funktion für Abwärtskompatibilität.

    Ruft validate_meldezettel() mit verbose=True auf (wie die alten Prints).

    Migration:
        Alt:  result = process_meldezettel(form_data, text)
        Neu:  result = validate_meldezettel(form_data, text, verbose=False)
    """
    return validate_meldezettel(form_data, melde_text, verbose=True)