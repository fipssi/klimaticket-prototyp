"""
registration_validation.py

Zweck
-----
Validierung eines Meldezettels / einer Meldebestätigung gegen Antragsdaten (JSON).
Geprüft werden typischerweise:
- Vorname (flexibel: Umlaute/Diakritika, Bindestrich, Mehrfachnamen, OCR ohne Leerzeichen)
- Nachname (flexibel: Doppelname, OCR-Trennzeichen)
- Geburtsdatum (auf EIN Format bringen: ISO YYYY-MM-DD)
- PLZ (Hauptwohnsitz) -> Förderberechtigung Stadt Salzburg

Wichtiges Design-Prinzip
------------------------
Wir trennen:
1) Extraktion (aus OCR-Text)
2) Normalisierung (für robuste Vergleiche)
3) Matching-Regeln (boolean Checks)
4) Ergebnisobjekt (dict) als Return, statt nur print()

Damit kannst du die Resultate später sauber in eine decision_engine.py übernehmen.
"""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Optional, Dict, Any


# =============================================================================
# 0) Normalisierung & Hilfsfunktionen
# =============================================================================

def normalize_for_matching(value: str) -> str:
    """
    Robust für Namen/Labels:
    - lowercase
    - ß -> ss
    - Diakritika entfernen (ä->a, ö->o, ü->u; é->e; ...)
    - Trennzeichen (Bindestrich, Slash, Unterstrich) -> Leerzeichen
    - sonstige Sonderzeichen raus
    - Whitespaces normalisieren

    Beispiel:
      "Bianca-Maria" -> "bianca maria"
      "Johannes-Filzer-Straße" -> "johannes filzer strasse"  (ß->ss)
    """
    value = (value or "").strip().lower()
    value = value.replace("ß", "ss")

    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))

    value = re.sub(r"[-_/]+", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)

    return " ".join(value.split())


def _compact(s: str) -> str:
    """
    Entfernt Leerzeichen komplett.
    Nützlich für OCR-Fälle ohne Leerzeichen: "PhillipAndreas", "MarcoWurst".
    """
    return (s or "").replace(" ", "")


def _variants_for_umlaut_translit(s: str) -> set[str]:
    """
    Zusätzliche Varianten für deutsche Transliterationen:
    - normalize_for_matching macht "ö" -> "o"
    - in der Praxis gibt es aber auch "oe" statt "ö"
      -> daher erzeugen wir zusätzlich "ae/oe/ue -> a/o/u"

    Hinweis:
    - Wir machen nur die "sichere" Richtung (ae/oe/ue -> a/o/u).
    """
    v = normalize_for_matching(s)
    variants = {v}
    variants.add(v.replace("ae", "a").replace("oe", "o").replace("ue", "u"))
    return {x for x in variants if x}


# =============================================================================
# 1) Label/Value-Extraktion aus Meldezettel
# =============================================================================

# Mögliche Label-Zeilen in unterschiedlichen Meldezettel-Layouts
_LABELS = {
    "familienname",
    "familienname oder nachname",
    "nachname",
    "vorname",
    "geschlecht",
    "geburtsdatum",
    "geburtsort",
    "staatsangehorigkeit",
    "zmr zahl",
    "zmr-zahl",
    "zmrzahl",
}

# Normalisierte und "compact" Varianten der Labels:
# -> wichtig für OCR, das Wörter mitten trennt ("Staatsa ngehörig keit")
_LABELS_NORM = {normalize_for_matching(x) for x in _LABELS}
_LABELS_COMPACT = {_compact(x) for x in _LABELS_NORM}


def _label_key_of(line: str) -> str:
    """
    Extrahiert (normalisiert) den Label-Teil einer Zeile.

    Akzeptiert Label-Formen:
    - "Vorname:" (ohne Wert rechts)
    - "Vorname"  (ohne ":") in manchen Layouts/Parsern

    Wenn rechts vom ":" bereits ein Wert steht ("Vorname: Bianca"),
    ist das NICHT "label-only" und wird hier als "" zurückgegeben,
    weil es im Block-Parser sonst die Label-Block-Logik stören würde.
    """
    s = (line or "").strip()
    if not s:
        return ""

    if ":" in s:
        left, right = s.split(":", 1)
        if right.strip():
            return ""  # hat bereits Wert -> nicht label-only
        return normalize_for_matching(left)

    return normalize_for_matching(s)


def _is_label_only_line(line: str) -> bool:
    """
    True nur für echte Personendaten-Labels.

    Wichtig:
    - Wir akzeptieren NICHT mehr generische Heuristiken wie "sieht aus wie Label",
      weil das bei deinem Salzburg-Meldezettel Sätze wie
      "Im lokalen Melderegister..." oder "Wohnsitzqualität..." fälschlich
      als Label interpretiert und die Zuordnung verschiebt.

    Robustheit:
    - OCR kann Labels intern trennen: "Staatsa ngehörig keit"
      -> daher Vergleich auch über _compact(...)
    """
    key = _label_key_of(line)
    if not key:
        return False
    return (key in _LABELS_NORM) or (_compact(key) in _LABELS_COMPACT)


def _matches_label_line(line: str, label_key_norm: str) -> bool:
    """
    Prüft robust, ob eine Zeile das gesuchte Label ist.
    - vergleicht normalisiert
    - zusätzlich compact-Vergleich (OCR trennt Wörter / fügt Leerzeichen ein)
    """
    s = (line or "").strip()
    if not s:
        return False

    if ":" in s:
        left = s.split(":", 1)[0]
        ln = normalize_for_matching(left)
    else:
        ln = normalize_for_matching(s)

    if not ln:
        return False

    # Gleichheit / Substring / compact-Variante
    return (
        ln == label_key_norm
        or label_key_norm in ln
        or _compact(ln) == _compact(label_key_norm)
        or _compact(label_key_norm) in _compact(ln)
    )


def extract_value_after_label(lines: list[str], label: str) -> Optional[str]:
    """
    Robust für:
    A) 'Vorname: Bianca Maria'
    B) 'Vorname:' + nächste Zeile 'Bianca Maria'
    C) Label-Block -> Werte-Block (gleiche Reihenfolge)

    Beispiel C (Salzburg-Layout):
      Familienname oder Nachname:
      Vorname:
      Geschlecht:
      Geburtsdatum:
      ...
      Bogner
      Bianca Maria
      weiblich
      15.10.1990
    """
    label_key = normalize_for_matching(label)

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        if _matches_label_line(line, label_key):
            # Fall A: Wert steht rechts vom ":" in derselben Zeile
            if ":" in line:
                right = line.split(":", 1)[1].strip()
                if right:
                    return right

            # Nächsten nicht-leeren Eintrag suchen
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines):
                return None

            # Fall B: nächste nicht-leere Zeile ist bereits ein Wert (kein Label-only)
            if not _is_label_only_line(lines[k]):
                return lines[k].strip()

            # Fall C: nächste Zeile ist wieder Label-only => Label-Block
            # 1) Start des Label-Blocks nach oben suchen (nur echte Labels!)
            start = i
            while start - 1 >= 0 and _is_label_only_line(lines[start - 1]):
                start -= 1

            # 2) Labels sammeln (ab start)
            labels: list[str] = []
            end = start
            while end < len(lines):
                cur = lines[end].strip()
                if not cur:
                    end += 1
                    continue
                if _is_label_only_line(cur):
                    labels.append(_label_key_of(cur))
                    end += 1
                    continue
                break  # erster Nicht-Label-only => Werteblock beginnt

            if not labels:
                return None

            # 3) Index unseres Labels im Label-Block finden (auch compact matchen)
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

            # 4) Werte sammeln (ab end) und den idx-ten Wert zurückgeben
            values: list[str] = []
            p = end
            while p < len(lines) and len(values) <= idx:
                v = lines[p].strip()
                if v:
                    # Sicherheitsnetz: falls doch wieder Label-only im Wertebereich auftaucht
                    if _is_label_only_line(v):
                        p += 1
                        continue
                    values.append(v)
                p += 1

            return values[idx] if len(values) > idx else None

    return None


# =============================================================================
# 2) Feld-Extraktion (Vorname/Nachname/Geburtsdatum/PLZ)
# =============================================================================

def extract_first_name_from_melde(text: str) -> Optional[str]:
    lines = text.splitlines()
    return extract_value_after_label(lines, "Vorname")


def extract_last_name_from_melde(text: str) -> Optional[str]:
    lines = text.splitlines()
    # unterschiedliche Varianten in den PDFs: "Familienname:", "Familienname oder Nachname:", "Nachname:"
    for lbl in ("Familienname oder Nachname", "Familienname", "Nachname"):
        v = extract_value_after_label(lines, lbl)
        if v:
            return v
    return None


def normalize_birthdate(value: str) -> Optional[str]:
    """
    Gibt IMMER ISO zurück: YYYY-MM-DD

    Akzeptiert u.a.:
    - 1995-07-05
    - 1995.07.05
    - 05.07.1995
    - 15,10,1990  / 15,10.1990  (OCR)
    - ISO mit Uhrzeit: 1990-01-01 00:00:00 oder 1990-01-01T00:00:00
    """
    if not value:
        return None

    v = (value or "").strip()
    v = v.replace(" ", "").replace(",", ".")

    # OCR-Fehler abfangen: O statt 0, l/I statt 1 (zwischen Ziffern)
    v = re.sub(r"(?<=\d)[Oo](?=\d)", "0", v)
    v = re.sub(r"(?<=\d)[lI](?=\d)", "1", v)

    # 1) ISO-Parsing (kann auch Zeit enthalten)
    try:
        dt = datetime.fromisoformat(v)
        return dt.date().isoformat()
    except ValueError:
        pass

    # 2) Bekannte Formate
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            dt2 = datetime.strptime(v, fmt).date()
            return dt2.isoformat()
        except ValueError:
            continue

    return None


def extract_birthdate_from_melde(text: str) -> Optional[str]:
    lines = text.splitlines()
    raw = extract_value_after_label(lines, "Geburtsdatum")
    return normalize_birthdate(raw) if raw else None


def extract_current_main_residence_postal_code(text: str) -> Optional[str]:
    """
    Extrahiert die PLZ des Hauptwohnsitzes, indem ab der ersten
    "Hauptwohnsitz"-Zeile nach der ersten 4-stelligen Zahl gesucht wird.

    Robustheit:
    - Case-insensitive
    - tolerant für OCR/Diakritika, da wir normalisieren
    """
    lines = text.splitlines()

    # 1) Index der ersten Zeile mit 'Hauptwohnsitz' finden (normalisiert)
    start_idx = None
    for i, line in enumerate(lines):
        if "hauptwohnsitz" in normalize_for_matching(line):
            start_idx = i
            break

    if start_idx is None:
        return None

    # 2) Ab dieser Zeile nach der ersten 4-stelligen Zahl suchen
    for line in lines[start_idx:]:
        match = re.search(r"\b\d{4}\b", line)
        if match:
            return match.group(0)

    return None


# =============================================================================
# 3) Matching-Regeln (Vorname/Nachname/Geburtsdatum/PLZ)
# =============================================================================

def first_name_matches(form_vorname: str, melde_vorname: Optional[str]) -> bool:
    """
    Prüft, ob der erste Vorname aus dem Antrag irgendwo im Melde-Vornamen vorkommt.

    Robust:
    - Mehrfachnamen am Meldezettel: "Phillip Andreas" / "Phillip-Andreas"
    - OCR ohne Leerzeichen: "PhillipAndreas"
    - Umlaute/Translit: "Jörg" ~ "Joerg"/"Jorg"
    """
    if not melde_vorname:
        return False

    f_norm = normalize_for_matching(form_vorname)
    m_norm = normalize_for_matching(melde_vorname)

    if not f_norm or not m_norm:
        return False

    # Antrag: typischerweise reicht der erste Vorname
    f_first = f_norm.split()[0]

    # 1) Token match
    if f_first in set(m_norm.split()):
        return True

    # 2) OCR ohne Leerzeichen
    if _compact(f_first) in _compact(m_norm):
        return True

    # 3) Varianten (ae/oe/ue -> a/o/u)
    for v in _variants_for_umlaut_translit(f_first):
        if v in set(m_norm.split()) or _compact(v) in _compact(m_norm):
            return True

    return False


def last_name_matches(form_nachname: str, melde_nachname: Optional[str]) -> bool:
    """
    Nachname-Match (flexibel):
    - exakter Match nach Normalisierung
    - Teilstring-Match (hilft bei OCR-Zusätzen)
    - Doppelname: alle Tokens aus Antrag müssen im Melde-Namen vorkommen
    - OCR ohne Leerzeichen: "MayerSchmidt"
    """
    if not melde_nachname:
        return False

    f_norm = normalize_for_matching(form_nachname)
    m_norm = normalize_for_matching(melde_nachname)

    if not f_norm or not m_norm:
        return False

    # 1) exakt oder Teilstring
    if f_norm == m_norm or f_norm in m_norm or m_norm in f_norm:
        return True

    # 2) Doppelname-Logik: alle Tokens aus Antrag müssen im Melde-Namen vorkommen
    f_tokens = f_norm.split()
    m_tokens = set(m_norm.split())
    if f_tokens and all(t in m_tokens for t in f_tokens):
        return True

    # 3) OCR ohne Leerzeichen
    if _compact(f_norm) in _compact(m_norm):
        return True

    # 4) Varianten (ae/oe/ue -> a/o/u)
    for v in _variants_for_umlaut_translit(f_norm):
        if _compact(v) in _compact(m_norm):
            return True

    return False


def birthdate_matches(form_date: str, melde_date_iso: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Geburtsdatum-Match:
    - Beide Seiten auf ISO normalisieren und dann vergleichen.

    Rückgabe:
      (ok, form_date_iso)
    """
    if not melde_date_iso:
        return False, None
    form_iso = normalize_birthdate(form_date)
    return (form_iso is not None and form_iso == melde_date_iso), form_iso


# =============================================================================
# 4) PLZ-Regel (Förderberechtigung)
# =============================================================================

SALZBURG_PLZ = {
    "5010", "5014", "5017", "5018", "5020", "5023", "5025", "5026", "5027", "5033",
}


def is_postcode_foerderberechtigt(plz: str) -> bool:
    """
    True, wenn die PLZ in der Menge der förderberechtigten Salzburger PLZ liegt.
    """
    return (plz or "").strip() in SALZBURG_PLZ


# =============================================================================
# 5) Hauptfunktion: Validierung mit Return-Result (statt nur print)
# =============================================================================

def validate_meldezettel(form_data: dict, melde_text: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Validiert Meldezettel gegen Antragsdaten und gibt ein strukturiertes Ergebnis zurück.

    Rückgabe (dict):
    - extracted: {vorname_full, nachname, geburtsdatum_iso, plz}
    - checks: {vorname_ok, nachname_ok, geburtsdatum_ok, plz_ok}
    - form_norm: {geburtsdatum_iso}
    - all_ok: bool
    """
    # --- Extraktion aus Meldezettel ---
    melde_vorname_full = extract_first_name_from_melde(melde_text)
    melde_nachname = extract_last_name_from_melde(melde_text)
    melde_geburtsdatum_iso = extract_birthdate_from_melde(melde_text)
    current_plz = extract_current_main_residence_postal_code(melde_text)

    # --- Checks gegen Antrag ---
    vorname_ok = first_name_matches(form_data.get("vorname", ""), melde_vorname_full)
    nachname_ok = last_name_matches(form_data.get("familienname", ""), melde_nachname)

    geburtsdatum_ok, form_geburtsdatum_iso = birthdate_matches(
        form_data.get("geburtsdatum", ""),
        melde_geburtsdatum_iso
    )

    plz_ok = (current_plz is not None) and is_postcode_foerderberechtigt(current_plz)

    result: Dict[str, Any] = {
        "doc_type": "meldezettel",
        "extracted": {
            "vorname_full": melde_vorname_full,
            "nachname": melde_nachname,
            "geburtsdatum_iso": melde_geburtsdatum_iso,
            "plz": current_plz,
        },
        "form_norm": {
            "geburtsdatum_iso": form_geburtsdatum_iso,
        },
        "checks": {
            "vorname_ok": bool(vorname_ok),
            "nachname_ok": bool(nachname_ok),
            "geburtsdatum_ok": bool(geburtsdatum_ok),
            "plz_ok": bool(plz_ok),
        },
        "all_ok": bool(vorname_ok and nachname_ok and geburtsdatum_ok and plz_ok),
    }

    if verbose:
        print("Vorname-Match:", result["checks"]["vorname_ok"])
        print("Nachname-Match:", result["checks"]["nachname_ok"])
        print("Geburtsdatum-Match:", result["checks"]["geburtsdatum_ok"])
        print("PLZ förderberechtigt:", result["checks"]["plz_ok"])

        print("DEBUG melde_nachname:", melde_nachname)
        print("DEBUG melde_vorname_full:", melde_vorname_full)
        print("DEBUG melde_geburtsdatum_iso:", melde_geburtsdatum_iso)
        print("DEBUG form_geburtsdatum_iso:", form_geburtsdatum_iso)

    return result


# -----------------------------------------------------------------------------
# Backwards Compatibility:
# Falls dein main.py noch process_meldezettel(...) aufruft, brichst du nichts.
# Du kannst später in main.py sauber auf validate_meldezettel(...) umstellen.
# -----------------------------------------------------------------------------

def process_meldezettel(form_data: dict, melde_text: str) -> Dict[str, Any]:
    """
    Alte Entry-Funktion (früher: print-only).
    Jetzt: return dict (und verbose=True Standard wie bisherige Prints).
    """
    return validate_meldezettel(form_data, melde_text, verbose=True)
