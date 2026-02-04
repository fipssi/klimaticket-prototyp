"""
utils.py

Gemeinsame Hilfsfunktionen für Text-Normalisierung und -Matching.

Wird verwendet von:
- invoice_validation.py  (Rechnungs-Validierung)
- registration_validation.py  (Meldezettel-Validierung)
"""

from __future__ import annotations

import re
import unicodedata


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
      "Max-Michael" -> "max michael"
      "Johannes-Filzer-Straße" -> "johannes filzer strasse"
    """
    value = (value or "").strip().lower()
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
    Nützlich für OCR-Fälle ohne Leerzeichen: "MarcoWurst", "MaxMichael".
    """
    return (s or "").replace(" ", "")


def _variants_for_umlaut_translit(s: str) -> set[str]:
    """
    Erzeugt zusätzliche Varianten für deutsche Transliterationen:
    - normalize_for_matching macht "ö" -> "o"
    - man sieht aber manchmal "oe" statt "ö"
      -> daher: "juergen" soll auch "jurgen" matchen

    Hinweis:
    - Nur die sichere Richtung (ae/oe/ue -> a/o/u),
      NICHT umgekehrt (würde False-Positives erhöhen).
    """
    v = normalize_for_matching(s)
    variants = {v}
    variants.add(v.replace("ae", "a").replace("oe", "o").replace("ue", "u"))
    return {x for x in variants if x}