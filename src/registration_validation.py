"""
registration_validation.py â€” Validierung von Meldezetteln / MeldebestÃ¤tigungen
================================================================================

ÃœBERBLICK
---------
Dieses Modul validiert Meldezettel-PDFs gegen Antragsdaten. Es ist der
zweite Validierungsbaustein neben invoice_validation.py:

    Pipeline-Position:
        PDF â†’ document_loader â†’ Text
        Text â†’ document_classifier â†’ ("meldezettel", 0.87)
        Text + Antragsdaten â†’ registration_validation â†’ {checks: ...}  â† HIER
        Alle Ergebnisse â†’ decision_engine â†’ Gesamtentscheidung

    Parallel dazu:
        Rechnungs-PDFs â†’ invoice_validation â†’ {name_ok, period_ok, ...}


WAS WIRD GEPRÃœFT?
------------------
Ein Meldezettel muss vier Dinge bestÃ¤tigen:

    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â”‚ PrÃ¼fung          â”‚ Was wird verglichen?                            â”‚
    â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
    â”‚ 1. Vorname       â”‚ Antrag-Vorname â‰ˆ Meldezettel-Vorname            â”‚
    â”‚ 2. Nachname      â”‚ Antrag-Nachname â‰ˆ Meldezettel-Nachname          â”‚
    â”‚ 3. Geburtsdatum  â”‚ Antrag-Datum == Meldezettel-Datum (ISO)         â”‚
    â”‚ 4. PLZ           â”‚ Meldezettel-PLZ âˆˆ Salzburger PLZ               â”‚
    â”‚                  â”‚ UND Meldezettel-PLZ == Antrag-PLZ               â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

    all_ok = vorname_ok AND nachname_ok AND geburtsdatum_ok AND plz_ok


MELDEZETTEL-LAYOUTS: DAS HAUPTPROBLEM
--------------------------------------
Es gibt KEIN einheitliches Meldezettel-Layout in Ã–sterreich. Jede Gemeinde
kann ihr eigenes Format verwenden. Dieses Modul muss mit mindestens drei
bekannten Layouts umgehen:

    Layout A â€” "Inline mit Doppelpunkt" (hÃ¤ufigste Variante):
        Vorname: Max Michael
        Familienname: Mustermann
        Geburtsdatum: 01.01.1990

    Layout B â€” "Label auf einer Zeile, Wert auf der nÃ¤chsten":
        Vorname:
        Max Michael
        Familienname:
        Mustermann

    Layout C â€” "Block-Layout" (z.B. Salzburg):
        Familienname oder Nachname:
        Vorname:
        Geschlecht:
        Geburtsdatum:
        Mustermann
        Max Michael
        mÃ¤nnlich
        01.01.1990

        â†’ Alle Labels stehen als Block, alle Werte als Block darunter.
          Die Zuordnung erfolgt Ã¼ber die Reihenfolge (1. Label â†’ 1. Wert).

    Layout D â€” "Inline OHNE Doppelpunkt" (z.B. Linz):
        Vorname                Max
        Familienname           Mustermann
        Geburtsdatum           01.06.1985

        â†’ Kein ":" nach dem Label, nur Leerzeichen-Trennung.
          Erkannt durch Splitting an 2+ aufeinanderfolgenden Leerzeichen.

    extract_value_after_label() handhabt alle vier Layouts automatisch.


ABHÃ„NGIGKEITEN
--------------
    utils.py â†’ normalize_for_matching(), _compact(), _variants_for_umlaut_translit()
    (Geteilte Hilfsfunktionen, auch von invoice_validation.py verwendet)
"""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Optional, Dict, Any

# Shared Hilfsfunktionen aus utils.py:
#   normalize_for_matching()  â†’ Lowercase, Umlaute entfernen, Whitespace normalisieren
#   _compact()                â†’ Alle Leerzeichen entfernen ("a b c" â†’ "abc")
#   _variants_for_umlaut_translit() â†’ Varianten: "jÃ¼rgen" â†’ ["juergen", "jurgen"]
try:
    from src.utils import normalize_for_matching, _compact, _variants_for_umlaut_translit
except ImportError:
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

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 1a) BEKANNTE LABELS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Diese Menge enthÃ¤lt alle Labels, die auf Ã¶sterreichischen Meldezetteln
# vorkommen kÃ¶nnen. Sie wird verwendet, um zu erkennen, ob eine Zeile
# ein Label ist (â†’ Wert auf der nÃ¤chsten Zeile) oder bereits ein Wert.
#
# Wichtig: Die Labels sind KLEINGESCHRIEBEN und OHNE Umlaute, weil sie
# mit normalize_for_matching() verglichen werden.
#
# "vomame" ist ein bekannter OCR-Fehler: Wenn der Textlayer kaputt ist,
# wird "Vorname" oft als "Vomame" gelesen (r/n â†’ m).
# â†’ document_loader.py erkennt diesen Fall und macht OCR stattdessen,
#   aber als Sicherheitsnetz akzeptieren wir "vomame" trotzdem als Label.

_LABELS = {
    "familienname",                   # Standard-Label
    "familienname oder nachname",     # Manche Gemeinden verwenden dieses Label
    "nachname",                       # Kurzform
    "vorname",                        # Standard
    "vomame",                         # OCR-Fehler fÃ¼r "Vorname" (r/n â†’ m)
    "geschlecht",                     # Wird nicht validiert, aber als Label erkannt
    "geburtsdatum",                   # Standard
    "geburtsort",                     # Wird nicht validiert, aber als Label erkannt
    "staatsangehorigkeit",            # Normalisiert (Ã¶ â†’ o)
    "zmr zahl",                       # Variante 1 (mit Leerzeichen)
    "zmr-zahl",                       # Variante 2 (mit Bindestrich)
    "zmrzahl",                        # Variante 3 (zusammengeschrieben)
}

# Normalisierte Versionen der Labels (via normalize_for_matching):
# "familienname oder nachname" â†’ "familienname oder nachname" (bereits lowercase)
# Werden fÃ¼r den Vergleich mit normalisierten Zeilen verwendet.
_LABELS_NORM = {normalize_for_matching(x) for x in _LABELS}

# Compact-Versionen (alle Leerzeichen entfernt):
# "familienname oder nachname" â†’ "familiennameodernachname"
# Werden gebraucht, weil OCR Labels manchmal mit Leerzeichen durchsetzt:
# "Staatsa ngehÃ¶rig keit" â†’ normalisiert: "staatsa ngehorig keit"
# â†’ compact: "staatsangehorigkkeit" â†’ matcht gegen "staatsangehorigkeit"
_LABELS_COMPACT = {_compact(x) for x in _LABELS_NORM}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 1b) LABEL-ERKENNUNG
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Drei Hilfsfunktionen, die zusammenarbeiten:
#
#   _label_key_of(line)       â†’ Extrahiert den Label-Teil einer Zeile
#   _is_label_only_line(line) â†’ Ist diese Zeile NUR ein Label (ohne Wert)?
#   _matches_label_line(line) â†’ Matcht diese Zeile ein bestimmtes Label?

def _label_key_of(line: str) -> str:
    """
    Extrahiert den normalisierten Label-Teil einer Zeile.

    Regeln:
        "Vorname: Max"   â†’ "" (hat bereits einen Wert â†’ kein Label-only)
        "Vorname:"          â†’ "vorname" (Label ohne Wert)
        "Vorname"           â†’ "vorname" (Label ohne Doppelpunkt, z.B. Linz-Layout)
        ""                  â†’ "" (leere Zeile)

    Warum gibt "Vorname: Max" einen leeren String zurÃ¼ck?
        Im Block-Layout (Layout C) muss die Funktion erkennen, ob eine Zeile
        ein "reines Label" ist. "Vorname: Max" ist KEIN reines Label,
        weil der Wert bereits in der gleichen Zeile steht.
        Wenn wir das als Label behandeln wÃ¼rden, wÃ¼rde die Block-Logik
        die nÃ¤chste Zeile als Wert nehmen â†’ falsche Zuordnung.

    Parameter:
        line: Eine Zeile aus dem Meldezettel-Text

    RÃ¼ckgabe:
        Normalisierter Label-String, oder "" wenn kein reines Label
    """
    s = (line or "").strip()
    if not s:
        return ""

    if ":" in s:
        left, right = s.split(":", 1)
        if right.strip():
            return ""  # Hat bereits Wert rechts vom ":" â†’ kein Label-only
        return normalize_for_matching(left)

    # Kein Doppelpunkt â†’ die ganze Zeile kÃ¶nnte ein Label sein (Linz-Layout)
    return normalize_for_matching(s)


def _is_label_only_line(line: str) -> bool:
    """
    PrÃ¼ft, ob eine Zeile NUR ein Personendaten-Label ist (ohne Wert).

    Gibt True zurÃ¼ck GENAU DANN, wenn der Label-Text einem der bekannten
    Labels aus _LABELS entspricht.

    Warum so streng?
        FrÃ¼her gab es eine generische Heuristik ("sieht aus wie Label").
        Das fÃ¼hrte zu Fehlern bei Salzburg-Meldezetteln, wo SÃ¤tze wie
        "Im lokalen Melderegister..." oder "WohnsitzqualitÃ¤t..." fÃ¤lschlich
        als Labels erkannt wurden und die Zuordnung verschoben.

        Jetzt: NUR echte Personendaten-Labels werden akzeptiert.

    OCR-Robustheit:
        OCR kann Labels intern trennen: "Staatsa ngehÃ¶rig keit"
        â†’ normalisiert: "staatsa ngehorig keit"
        â†’ compact: "staatsangehorigkkeit"
        â†’ wird mit _LABELS_COMPACT verglichen â†’ Treffer!

    Parameter:
        line: Eine Zeile aus dem Meldezettel-Text

    RÃ¼ckgabe:
        True wenn die Zeile ein bekanntes Label ist, sonst False

    Beispiele:
        "Vorname:"                    â†’ True (bekanntes Label)
        "Vorname: Max"             â†’ False (hat bereits Wert)
        "Im lokalen Melderegister..." â†’ False (kein bekanntes Label)
        "Max Michael"                â†’ False (ein Wert, kein Label)
    """
    key = _label_key_of(line)
    if not key:
        return False
    return (key in _LABELS_NORM) or (_compact(key) in _LABELS_COMPACT)


def _matches_label_line(line: str, label_key_norm: str) -> bool:
    """
    PrÃ¼ft, ob eine Zeile das gesuchte Label enthÃ¤lt.

    Tolerant gegen:
        - GroÃŸ/Kleinschreibung (via normalize_for_matching)
        - Teilstring-Match ("familienname oder nachname" enthÃ¤lt "familienname")
        - OCR-Leerzeichen ("Familien name" â†’ compact "familienname")

    Parameter:
        line:           Eine Zeile aus dem Meldezettel-Text
        label_key_norm: Das gesuchte Label (bereits normalisiert)

    RÃ¼ckgabe:
        True wenn die Zeile das gesuchte Label enthÃ¤lt

    Beispiele:
        _matches_label_line("Familienname oder Nachname:", "familienname") â†’ True
        _matches_label_line("Vorname: Max", "vorname")                 â†’ True
        _matches_label_line("Geburtsdatum:", "vorname")                   â†’ False
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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 1c) HAUPTEXTRAKTION: LABEL â†’ WERT
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Dies ist die ZENTRALE Extraktionsfunktion. Sie behandelt alle vier
# Meldezettel-Layouts (A, A2, B, C) automatisch:
#
#   Layout A:  "Vorname: Max Michael"       â†’ Wert rechts vom ":"
#   Layout A2: "Vorname                Max" â†’ Wert nach 2+ Leerzeichen
#   Layout B:  "Vorname:" + nÃ¤chste Zeile     â†’ Wert auf Folgezeile
#   Layout C:  Label-Block + Werte-Block      â†’ Wert an gleicher Position
#
# Die Funktion probiert die Layouts in Reihenfolge A â†’ A2 â†’ B â†’ C durch.
# Sobald ein Wert gefunden wird, wird er zurÃ¼ckgegeben.

def extract_value_after_label(lines: list[str], label: str) -> Optional[str]:
    """
    Extrahiert den Wert zu einem Label aus einem Meldezettel.

    Handhabt vier verschiedene Layouts:

    Layout A â€” Inline mit Doppelpunkt:
        "Vorname: Max Michael"
        â†’ RÃ¼ckgabe: "Max Michael"

    Layout A2 â€” Inline ohne Doppelpunkt (z.B. Linz):
        "Vorname                Max"
        â†’ Erkennung: kein ":" in der Zeile + 2+ Leerzeichen trennen Label und Wert
        â†’ RÃ¼ckgabe: "Max"
        â†’ Schutz: Der rechte Teil darf kein weiteres Label sein

    Layout B â€” Label und Wert auf getrennten Zeilen:
        "Vorname:"
        "Max Michael"
        â†’ RÃ¼ckgabe: "Max Michael"

    Layout C â€” Block-Layout (z.B. Salzburg):
        "Familienname oder Nachname:"
        "Vorname:"
        "Geschlecht:"
        "Geburtsdatum:"
        "Mustermann"
        "Max Michael"
        "mÃ¤nnlich"
        "01.01.1990"
        â†’ Labels und Werte stehen in getrennten BlÃ¶cken
        â†’ Zuordnung Ã¼ber Position: 2. Label â†’ 2. Wert
        â†’ RÃ¼ckgabe fÃ¼r "Vorname": "Max Michael" (Position 2)

    Parameter:
        lines: Liste von Textzeilen (text.splitlines())
        label: Das gesuchte Label (z.B. "Vorname", "Geburtsdatum")

    RÃ¼ckgabe:
        Der extrahierte Wert als String, oder None wenn nicht gefunden.
    """
    label_key = normalize_for_matching(label)

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        # Ist diese Zeile unser gesuchtes Label?
        if _matches_label_line(line, label_key):

            # â”€â”€ Layout A: Wert rechts vom ":" â”€â”€
            # "Vorname: Max Michael"  â†’ right = "Max Michael"
            if ":" in line:
                right = line.split(":", 1)[1].strip()
                if right:
                    return right

            # â”€â”€ Layout A2: Inline ohne Doppelpunkt (Linz) â”€â”€
            # "Vorname                Max"
            # Kein ":" vorhanden â†’ Splitten an 2+ Leerzeichen.
            # Das erste Teil ist das Label, das zweite der Wert.
            #
            # Wichtig: Der Wert darf KEIN weiteres Label sein!
            # Sonst wÃ¼rde "Familienname                Vorname" den
            # Wert "Vorname" zurÃ¼ckgeben (was falsch wÃ¤re).
            if ":" not in line:
                parts = re.split(r'\s{2,}', line.strip(), maxsplit=1)
                if len(parts) == 2:
                    potential_value = parts[1].strip()
                    if potential_value and not _is_label_only_line(potential_value):
                        return potential_value

            # â”€â”€ NÃ¤chste nicht-leere Zeile suchen â”€â”€
            # FÃ¼r Layout B und C brauchen wir die Folgezeile(n).
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines):
                return None

            # â”€â”€ Layout B: Wert auf der nÃ¤chsten Zeile â”€â”€
            # "Vorname:"      â† Label
            # "Max Michael"  â† Wert (kein Label-only)
            if not _is_label_only_line(lines[k]):
                return lines[k].strip()

            # â”€â”€ Layout C: Block-Layout â”€â”€
            # Die nÃ¤chste Zeile ist AUCH ein Label â†’ wir sind in einem Label-Block.
            # Strategie:
            #   1) Label-Block nach oben erweitern (alle zusammenhÃ¤ngenden Labels)
            #   2) Index unseres Labels im Block bestimmen
            #   3) Werte-Block (nach den Labels) lesen
            #   4) Wert an der gleichen Position zurÃ¼ckgeben

            # Schritt 1: Start des Label-Blocks nach oben suchen
            # Wir gehen von der aktuellen Zeile rÃ¼ckwÃ¤rts, solange Labels kommen.
            start = i
            while start - 1 >= 0 and _is_label_only_line(lines[start - 1]):
                start -= 1

            # Schritt 2: Alle Labels im Block sammeln (vorwÃ¤rts ab start)
            labels: list[str] = []
            end = start
            while end < len(lines):
                cur = lines[end].strip()
                if not cur:
                    end += 1        # Leere Zeilen Ã¼berspringen
                    continue
                if _is_label_only_line(cur):
                    labels.append(_label_key_of(cur))
                    end += 1
                    continue
                break  # Erste Nicht-Label-Zeile â†’ Werteblock beginnt hier

            if not labels:
                return None

            # Schritt 3: Index unseres Labels im Label-Block finden
            # (auch mit Compact-Matching fÃ¼r OCR-Robustheit)
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

            # Schritt 4: Werte sammeln (ab end) und idx-ten Wert zurÃ¼ckgeben
            # Der Werteblock beginnt bei 'end' (erste Nicht-Label-Zeile).
            # Wir sammeln Werte, bis wir genug haben (idx + 1 StÃ¼ck).
            values: list[str] = []
            p = end
            while p < len(lines) and len(values) <= idx:
                v = lines[p].strip()
                if v:
                    # Sicherheitsnetz: Falls im Werteblock doch ein Label auftaucht
                    # (unerwartetes Layout), Ã¼berspringen wir es.
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
# Vier Convenience-Funktionen, die extract_value_after_label() fÃ¼r
# die spezifischen Felder aufrufen. Sie kapseln die Label-Varianten
# und Nachbearbeitung (z.B. Geburtsdatum-Normalisierung).

def extract_first_name_from_melde(text: str) -> Optional[str]:
    """
    Extrahiert den Vornamen aus einem Meldezettel-Text.

    Sucht nach dem Label "Vorname" (oder OCR-Variante "Vomame").

    RÃ¼ckgabe:
        Der Vorname als String (z.B. "Max Michael"), oder None.
    """
    lines = text.splitlines()
    return extract_value_after_label(lines, "Vorname")


def extract_last_name_from_melde(text: str) -> Optional[str]:
    """
    Extrahiert den Nachnamen aus einem Meldezettel-Text.

    Problem: Verschiedene Gemeinden verwenden verschiedene Labels:
        - "Familienname oder Nachname" (z.B. Salzburg, Wien)
        - "Familienname" (hÃ¤ufigste Variante)
        - "Nachname" (selten, aber vorkommend)

    Strategie: Alle drei Varianten durchprobieren, erste Treffer zurÃ¼ckgeben.
    Reihenfolge: Spezifischstes Label zuerst (vermeidet Fehlzuordnungen).

    RÃ¼ckgabe:
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
        "1985-06-15"              â†’ "1985-06-15"   (ISO)
        "1985.06.15"              â†’ "1985-06-15"   (ISO mit Punkten)
        "01.06.1985"              â†’ "1985-06-15"   (deutsches Format)
        "01,01,1990"              â†’ "1990-01-01"   (OCR mit Kommas)
        "01,01.1990"              â†’ "1990-01-01"   (OCR gemischt)
        "1990-01-01 00:00:00"     â†’ "1990-01-01"   (ISO mit Uhrzeit)
        "1990-01-01T00:00:00"     â†’ "1990-01-01"   (ISO mit T-Separator)

    OCR-Korrekturen:
        - O/o zwischen Ziffern â†’ 0: "O1.O6.1985" â†’ "01.06.1985"
        - l/I zwischen Ziffern â†’ 1: "l5.06.1985" â†’ "15.06.1985"
        - Kommas â†’ Punkte: "01,01,1990" â†’ "01.01.1990"
        - Leerzeichen entfernen: "01. 06. 1985" â†’ "01.06.1985"

    RÃ¼ckgabe:
        ISO-formatiertes Datum "YYYY-MM-DD", oder None bei ungÃ¼ltigem Format.

    Warum ISO?
        Beide Seiten (Antrag und Meldezettel) werden auf ISO normalisiert,
        dann ist der Vergleich ein einfacher String-Vergleich:
            "1990-01-01" == "1990-01-01" â†’ True
    """
    if not value:
        return None

    v = (value or "").strip()

    # OCR-Bereinigung: Leerzeichen entfernen, Kommas zu Punkten
    v = v.replace(" ", "").replace(",", ".")

    # OCR-Fehler: O/o â†’ 0 (zwischen Ziffern)
    # "O1.O6.1985" â†’ "01.06.1985"
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)

    # OCR-Fehler: l/I â†’ 1 (zwischen Ziffern)
    # "l5.06.1985" â†’ "15.06.1985"
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
        1. Label "Geburtsdatum" suchen â†’ Rohwert extrahieren
        2. Rohwert normalisieren â†’ ISO-Format "YYYY-MM-DD"

    RÃ¼ckgabe:
        ISO-formatiertes Geburtsdatum, oder None.

    Beispiel:
        Text enthÃ¤lt "Geburtsdatum: 01.01.1990"
        â†’ extract_value_after_label â†’ "01.01.1990"
        â†’ normalize_birthdate â†’ "1990-01-01"
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
        Ã–sterreichische PLZ sind immer genau 4 Ziffern (1010 bis 9992).
        Das Regex \\b\\d{4}\\b findet exakt solche Zahlen.

    OCR-Robustheit:
        - "Hauptwohnsitz" wird normalisiert gesucht (Umlaute, GroÃŸ/Klein)
        - \\b (Wortgrenze) verhindert, dass Teile von lÃ¤ngeren Zahlen
          als PLZ erkannt werden (z.B. "12345" â†’ nicht "1234")

    Parameter:
        text: Extrahierter Meldezettel-Text

    RÃ¼ckgabe:
        4-stellige PLZ als String (z.B. "5020"), oder None.
    """
    lines = text.splitlines()

    # Schritt 1: Zeile mit "Hauptwohnsitz" finden
    start_idx = None
    for i, line in enumerate(lines):
        # Normalisiert suchen: "Hauptwohnsitz" â†’ "hauptwohnsitz"
        # Auch: "HAUPTWOHNSITZ", "Hauptwohn sitz" (OCR) werden gefunden
        if "hauptwohnsitz" in normalize_for_matching(line):
            start_idx = i
            break

    if start_idx is None:
        return None

    # Schritt 2: Ab der Hauptwohnsitz-Zeile die erste 4-stellige Zahl suchen
    # Die PLZ steht typischerweise in der gleichen oder nÃ¤chsten Zeile:
    #   "Hauptwohnsitz MusterstraÃŸe 1, 5020 Salzburg"
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
    PrÃ¼ft, ob der Vorname aus dem Antrag zum Meldezettel-Vornamen passt.

    Matching-Strategie:
        1. Token-Match: Erster Vorname aus Antrag als Wort im Meldezettel?
           Antrag: "Max", Meldezettel: "Max Michael" â†’ True âœ“

        2. Compact-Match: OCR hat Leerzeichen verschluckt?
           Antrag: "Max", Meldezettel: "MaxMichael" â†’ True âœ“

        3. Umlaut-Varianten: Transliteration berÃ¼cksichtigen?
           Antrag: "JÃ¼rgen", Meldezettel: "Juergen" â†’ True âœ“

    Warum nur der ERSTE Vorname?
        Im Antrag steht typischerweise nur der Rufname ("Max"),
        aber auf dem Meldezettel stehen alle Vornamen ("Max Michael").
        Der erste Vorname muss reichen.

    Parameter:
        form_vorname:  Vorname aus dem Antrag
        melde_vorname: Vorname aus dem Meldezettel (kann None sein)

    RÃ¼ckgabe:
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

    # â”€â”€ Ebene 1: Token-Match â”€â”€
    # "max" in {"max", "michael"} â†’ True
    if f_first in set(m_norm.split()):
        return True

    # â”€â”€ Ebene 2: Compact-Match (OCR) â”€â”€
    # "max" in "maxmichael" â†’ True
    if _compact(f_first) in _compact(m_norm):
        return True

    # â”€â”€ Ebene 3: Umlaut-Varianten â”€â”€
    # "jurgen" â†’ Varianten: ["juergen", "jurgen"] â†’ prÃ¼fe alle
    for v in _variants_for_umlaut_translit(f_first):
        if v in set(m_norm.split()) or _compact(v) in _compact(m_norm):
            return True

    return False


def last_name_matches(form_nachname: str, melde_nachname: Optional[str]) -> bool:
    """
    PrÃ¼ft, ob der Nachname aus dem Antrag zum Meldezettel-Nachnamen passt.

    Matching-Strategie (toleranter als Vorname):
        1. Exakt oder Teilstring nach Normalisierung:
           "mustermann" == "mustermann" â†’ True âœ“
           "mustermann" in "mustermann beispiel" â†’ True âœ“ (Doppelname)

        2. Alle Tokens mÃ¼ssen vorkommen (Doppelname):
           Antrag: "Muster Beispiel" â†’ ["muster", "beispiel"]
           Meldezettel: "Beispiel Muster" â†’ alle da â†’ True âœ“

        3. Compact-Match (OCR):
           "musterbeispiel" in "musterbeispieleva" â†’ True âœ“

        4. Umlaut-Varianten:
           "muster" â†’ "muster" â†’ prÃ¼fe Compact

    Parameter:
        form_nachname:  Nachname aus dem Antrag
        melde_nachname: Nachname aus dem Meldezettel (kann None sein)

    RÃ¼ckgabe:
        True wenn der Nachname matcht.

    Unterschied zum Vornamen:
        Beim Nachnamen gibt es einen Teilstring-Check (f_norm in m_norm),
        der beim Vornamen fehlt. Das hilft bei OCR-ZusÃ¤tzen:
        Meldezettel: "MUSTERMANN geb. BEISPIEL" â†’ "mustermann" in "mustermann geb beispiel" â†’ True
    """
    if not melde_nachname:
        return False

    f_norm = normalize_for_matching(form_nachname)
    m_norm = normalize_for_matching(melde_nachname)

    if not f_norm or not m_norm:
        return False

    # â”€â”€ Ebene 1: Exakt oder Teilstring â”€â”€
    # Hilfreich bei Doppelnamen und OCR-ZusÃ¤tzen (z.B. "geb.")
    if f_norm == m_norm or f_norm in m_norm or m_norm in f_norm:
        return True

    # â”€â”€ Ebene 2: Doppelname-Logik â”€â”€
    # Antrag: "Muster Beispiel" â†’ Tokens: ["muster", "beispiel"]
    # Beide mÃ¼ssen im Meldezettel vorkommen (Reihenfolge egal)
    f_tokens = f_norm.split()
    m_tokens = set(m_norm.split())
    if f_tokens and all(t in m_tokens for t in f_tokens):
        return True

    # â”€â”€ Ebene 3: Compact-Match (OCR) â”€â”€
    if _compact(f_norm) in _compact(m_norm):
        return True

    # â”€â”€ Ebene 4: Umlaut-Varianten â”€â”€
    for v in _variants_for_umlaut_translit(f_norm):
        if _compact(v) in _compact(m_norm):
            return True

    return False


def birthdate_matches(form_date: str, melde_date_iso: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    PrÃ¼ft, ob das Geburtsdatum aus dem Antrag mit dem Meldezettel Ã¼bereinstimmt.

    Ablauf:
        1. Antragsdatum auf ISO normalisieren (normalize_birthdate)
        2. String-Vergleich: "1990-01-01" == "1990-01-01"

    Warum String-Vergleich und nicht datetime?
        Beide Seiten sind bereits auf ISO normalisiert. Ein String-Vergleich
        ist schneller und vermeidet Probleme mit Uhrzeiten/Zeitzonen.

    Parameter:
        form_date:      Geburtsdatum aus dem Antrag (beliebiges Format)
        melde_date_iso: Geburtsdatum aus dem Meldezettel (bereits ISO!)

    RÃ¼ckgabe:
        Tupel (match_ok, form_date_iso):
            match_ok:      True wenn die Daten Ã¼bereinstimmen
            form_date_iso: Das normalisierte Antragsdatum (fÃ¼r Debug/UI)

    Beispiel:
        birthdate_matches("01.01.1990", "1990-01-01")
        â†’ (True, "1990-01-01")
    """
    if not melde_date_iso:
        return False, None
    form_iso = normalize_birthdate(form_date)
    return (form_iso is not None and form_iso == melde_date_iso), form_iso


# =============================================================================
# 4) PLZ-REGEL (FÃ–RDERBERECHTIGUNG)
# =============================================================================
#
# Die FÃ¶rderung gilt NUR fÃ¼r Personen mit Hauptwohnsitz in der Stadt Salzburg.
# Die Stadt Salzburg hat mehrere PLZ (nicht nur 5020), weil verschiedene
# Stadtteile eigene PLZ haben.
#
# Diese Menge muss aktualisiert werden, wenn sich die FÃ¶rderbedingungen Ã¤ndern
# oder neue PLZ hinzukommen.

SALZBURG_PLZ = {
    "5010",    # Salzburg (Altstadt, Zentrum)
    "5014",    # Salzburg (Leopoldskron-Moos)
    "5017",    # Salzburg (MÃ¼lln, Maxglan)
    "5018",    # Salzburg (Maxglan-West)
    "5020",    # Salzburg (Hauptpostleitzahl, Nonntal, Parsch, Aigen, Gnigl)
    "5023",    # Salzburg (Gaisberg)
    "5025",    # Salzburg (Josefiau, Herrnau)
    "5026",    # Salzburg (Salzburg-SÃ¼d)
    "5027",    # Salzburg (Berchtesgadner StraÃŸe)
    "5033",    # Salzburg (Langwied, Kasern)
}


def is_postcode_foerderberechtigt(plz: str) -> bool:
    """
    PrÃ¼ft, ob eine PLZ zur Stadt Salzburg gehÃ¶rt und damit fÃ¶rderberechtigt ist.

    Parameter:
        plz: 4-stellige PLZ als String (z.B. "5020")

    RÃ¼ckgabe:
        True wenn die PLZ in SALZBURG_PLZ enthalten ist.

    Beispiele:
        is_postcode_foerderberechtigt("5020") â†’ True  (Salzburg)
        is_postcode_foerderberechtigt("4020") â†’ False (Linz)
        is_postcode_foerderberechtigt("")     â†’ False (leer)
    """
    return (plz or "").strip() in SALZBURG_PLZ


# =============================================================================
# 5) HAUPTFUNKTION: MELDEZETTEL VALIDIEREN
# =============================================================================
#
# Diese Funktion wird von der Decision Engine aufgerufen:
#   decision_engine.build_overall_decision()
#       â†’ validate_meldezettel(form_data, melde_text)
#       â†’ {all_ok: True/False, checks: {...}, extracted: {...}}
#
# Sie fÃ¼hrt alle vier PrÃ¼fungen durch und gibt ein strukturiertes
# Ergebnis-Dict zurÃ¼ck.

def validate_meldezettel(form_data: dict, melde_text: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Validiert einen Meldezettel gegen Antragsdaten.

    Ablauf:
        1. Vier Felder aus dem Meldezettel extrahieren
        2. Jedes Feld gegen den Antrag prÃ¼fen
        3. Strukturiertes Ergebnis-Dict zurÃ¼ckgeben

    PrÃ¼fungen:
        vorname_ok:      Erster Vorname aus Antrag â‰ˆ Meldezettel-Vorname
        nachname_ok:     Nachname aus Antrag â‰ˆ Meldezettel-Nachname
        geburtsdatum_ok: Geburtsdatum Antrag == Meldezettel (nach ISO-Normalisierung)
        plz_ok:          PLZ fÃ¶rderberechtigt (Salzburg) UND PLZ Antrag == Meldezettel

    Parameter:
        form_data:  Dict mit Antragsdaten. Erwartete Keys:
                    "vorname", "familienname", "geburtsdatum", "plz"
        melde_text: Extrahierter Text aus dem Meldezettel-PDF
        verbose:    Debug-Ausgaben auf Konsole? (Default: False)

    RÃ¼ckgabe (dict):
        doc_type:    "meldezettel"
        extracted:   {vorname_full, nachname, geburtsdatum_iso, plz}
                     â†’ Was aus dem Meldezettel extrahiert wurde
        form_norm:   {geburtsdatum_iso}
                     â†’ Normalisiertes Antragsdatum (fÃ¼r Vergleich/Debug)
        checks:      {vorname_ok, nachname_ok, geburtsdatum_ok, plz_ok,
                      plz_ok_melde, plz_ok_form}
                     â†’ Einzelergebnisse jeder PrÃ¼fung
        all_ok:      bool
                     â†’ Gesamtergebnis (AND aller Checks)

    PLZ-PrÃ¼fung im Detail:
        Die PLZ wird in ZWEI Schritten geprÃ¼ft:

        1. plz_ok_melde: Ist die Meldezettel-PLZ fÃ¶rderberechtigt?
           â†’ PLZ âˆˆ SALZBURG_PLZ
           PrÃ¼ft: Hat die Person ihren Hauptwohnsitz in der Stadt Salzburg?

        2. plz_ok_form: Stimmt die Antrag-PLZ mit der Meldezettel-PLZ Ã¼berein?
           â†’ form_plz == melde_plz
           PrÃ¼ft: Hat der Antragsteller die richtige PLZ angegeben?

        3. plz_ok = plz_ok_melde AND plz_ok_form
           â†’ Beide mÃ¼ssen stimmen.

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

    # â”€â”€ 1) Felder aus Meldezettel extrahieren â”€â”€
    # Jede Funktion sucht das entsprechende Label und gibt den Wert zurÃ¼ck.
    melde_vorname_full = extract_first_name_from_melde(melde_text)     # z.B. "Max Michael"
    melde_nachname = extract_last_name_from_melde(melde_text)          # z.B. "Mustermann"
    melde_geburtsdatum_iso = extract_birthdate_from_melde(melde_text)  # z.B. "1990-01-01"
    current_plz = extract_current_main_residence_postal_code(melde_text)  # z.B. "5020"

    # â”€â”€ 2) Vorname prÃ¼fen â”€â”€
    vorname_ok = first_name_matches(
        form_data.get("vorname", ""),
        melde_vorname_full,
    )

    # â”€â”€ 3) Nachname prÃ¼fen â”€â”€
    nachname_ok = last_name_matches(
        form_data.get("familienname", ""),
        melde_nachname,
    )

    # â”€â”€ 4) Geburtsdatum prÃ¼fen â”€â”€
    # birthdate_matches() normalisiert beide Seiten auf ISO und vergleicht.
    geburtsdatum_ok, form_geburtsdatum_iso = birthdate_matches(
        form_data.get("geburtsdatum", ""),
        melde_geburtsdatum_iso,
    )

    # â”€â”€ 5) PLZ prÃ¼fen (zwei TeilprÃ¼fungen) â”€â”€
    current_plz = extract_current_main_residence_postal_code(melde_text)
    form_plz = (form_data.get("plz") or "").strip()

    # TeilprÃ¼fung 1: Ist die Meldezettel-PLZ eine Salzburger PLZ?
    # â†’ Nur Personen mit Hauptwohnsitz in der Stadt Salzburg sind fÃ¶rderberechtigt.
    plz_ok_melde = (current_plz is not None) and is_postcode_foerderberechtigt(current_plz)

    # TeilprÃ¼fung 2: Stimmt die Antrag-PLZ mit der Meldezettel-PLZ Ã¼berein?
    # â†’ Verhindert, dass jemand eine falsche PLZ im Antrag angibt.
    plz_ok_form = bool(current_plz and form_plz and current_plz == form_plz)

    # Gesamt-PLZ: Beide TeilprÃ¼fungen mÃ¼ssen bestehen.
    plz_ok = plz_ok_melde and plz_ok_form

    # â”€â”€ 6) Ergebnis-Dict aufbauen â”€â”€
    result: Dict[str, Any] = {
        "doc_type": "meldezettel",

        # Was aus dem Meldezettel extrahiert wurde (fÃ¼r Debug/UI)
        "extracted": {
            "vorname_full": melde_vorname_full,        # z.B. "Max Michael"
            "nachname": melde_nachname,                # z.B. "Mustermann"
            "geburtsdatum_iso": melde_geburtsdatum_iso,  # z.B. "1990-01-01"
            "plz": current_plz,                        # z.B. "5020"
        },

        # Normalisiertes Antragsdatum (fÃ¼r Vergleich in UI)
        "form_norm": {
            "geburtsdatum_iso": form_geburtsdatum_iso,   # z.B. "1990-01-01"
        },

        # Einzelergebnisse jeder PrÃ¼fung
        "checks": {
            "vorname_ok": bool(vorname_ok),
            "nachname_ok": bool(nachname_ok),
            "geburtsdatum_ok": bool(geburtsdatum_ok),
            "plz_ok": bool(plz_ok),              # Gesamt-PLZ (beide Teile)
            "plz_ok_melde": bool(plz_ok_melde),  # PLZ fÃ¶rderberechtigt?
            "plz_ok_form": bool(plz_ok_form),    # PLZ Antrag == Meldezettel?
        },

        # Gesamtergebnis: Alle vier HauptprÃ¼fungen mÃ¼ssen bestehen
        "all_ok": bool(vorname_ok and nachname_ok and geburtsdatum_ok and plz_ok),
    }

    # â”€â”€ 7) Debug-Ausgaben â”€â”€
    if verbose:
        print("Vorname-Match:", result["checks"]["vorname_ok"])
        print("Nachname-Match:", result["checks"]["nachname_ok"])
        print("Geburtsdatum-Match:", result["checks"]["geburtsdatum_ok"])
        print("PLZ fÃ¶rderberechtigt:", result["checks"]["plz_ok"])

        print("DEBUG melde_nachname:", melde_nachname)
        print("DEBUG melde_vorname_full:", melde_vorname_full)
        print("DEBUG melde_geburtsdatum_iso:", melde_geburtsdatum_iso)
        print("DEBUG form_geburtsdatum_iso:", form_geburtsdatum_iso)
        print("PLZ (Meldezettel) fÃ¶rderberechtigt:", plz_ok_melde)
        print("PLZ Formular = PLZ Meldezettel:", plz_ok_form)

    return result


# =============================================================================
# 6) ABWÃ„RTSKOMPATIBILITÃ„T
# =============================================================================
#
# Die alte Funktion process_meldezettel() wurde in frÃ¼heren Versionen von
# main.py aufgerufen. Sie gab nur print()-Ausgaben aus und hatte kein
# strukturiertes Return.
#
# Diese Wrapper-Funktion stellt sicher, dass alter Code weiterhin funktioniert,
# bis er auf validate_meldezettel() umgestellt wird.

def process_meldezettel(form_data: dict, melde_text: str) -> Dict[str, Any]:
    """
    DEPRECATED: Alte Entry-Funktion fÃ¼r AbwÃ¤rtskompatibilitÃ¤t.

    Ruft validate_meldezettel() mit verbose=True auf (wie die alten Prints).

    Migration:
        Alt:  result = process_meldezettel(form_data, text)
        Neu:  result = validate_meldezettel(form_data, text, verbose=False)
    """
    return validate_meldezettel(form_data, melde_text, verbose=True)
