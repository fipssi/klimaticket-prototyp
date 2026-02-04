"""
document_loader.py — PDF-Text-Extraktion für KlimaTicket-Förderanträge
======================================================================

ÜBERBLICK
---------
Dieses Modul extrahiert lesbaren Text aus PDF-Dateien. Es ist der
allererste Schritt in der Pipeline — noch VOR der Klassifikation:

    PDF-Datei  →  document_loader  →  Text-String  →  document_classifier  →  ...

Das Modul muss mit zwei grundsätzlich verschiedenen PDF-Typen umgehen:

    1. PDFs MIT Textlayer (z.B. digital erstellte Rechnungen von ÖBB)
       → Text wird direkt aus der PDF-Struktur gelesen (schnell, genau)

    2. PDFs OHNE Textlayer (z.B. eingescannte Meldezettels)
       → Die Seite wird als Bild gerendert und per OCR (Tesseract) gelesen


ENTSCHEIDUNGSLOGIK PRO SEITE
-----------------------------
    ┌─────────────────────────────────────┐
    │ Textlayer vorhanden?                │
    │  ├─ JA → Textlayer brauchbar?       │
    │  │   ├─ JA  → Text verwenden        │  (~1 ms pro Seite)
    │  │   └─ NEIN → OCR                  │  (~2–5 s pro Seite)
    │  └─ NEIN → OCR                      │
    └─────────────────────────────────────┘


SEITENTRENNUNG MIT \\f (FORM FEED)
----------------------------------
Mehrseitige PDFs werden Seite für Seite verarbeitet. Die Texte der
einzelnen Seiten werden mit \\f (Form Feed / ASCII 12) verbunden.
Das ist wichtig für Multi-Page-Monatsrechnungen, wo z.B. 3 Monate
in einer einzigen PDF stehen:

    Seite 1: "Leistungszeitraum: 01.09.2024 - 30.09.2024"
    \\f
    Seite 2: "Leistungszeitraum: 01.10.2024 - 31.10.2024"
    \\f
    Seite 3: "Leistungszeitraum: 01.11.2024 - 30.11.2024"

Die Decision Engine splittet dann an \\f und validiert jede Seite einzeln.


ABHÄNGIGKEITEN
--------------
    Python-Pakete:
        pypdf       — PDF-Parsing, Textlayer auslesen
        pdf2image   — PDF-Seiten als Bilder rendern (nutzt Poppler)
        pytesseract — Python-Wrapper für Tesseract OCR
        Pillow      — Bildverarbeitung (von pdf2image/pytesseract genutzt)

    System-Tools (müssen installiert sein):
        Poppler     — PDF-Rendering-Engine (pdftoppm)
                      Linux:   apt install poppler-utils
                      Windows: manuell installieren (siehe POPPLER_PATH)
        Tesseract   — OCR-Engine
                      Linux:   apt install tesseract-ocr tesseract-ocr-deu
                      Windows: manuell installieren (siehe pytesseract.tesseract_cmd)
"""

import os
import re
import shutil
from pathlib import Path

from pypdf import PdfReader             # PDF-Parsing: Textlayer auslesen
from pdf2image import convert_from_path  # PDF-Seite → Bild (nutzt Poppler/pdftoppm)
import pytesseract                       # Bild → Text (nutzt Tesseract OCR)


# =============================================================================
# PLATTFORM-KONFIGURATION (Windows vs. Linux/Cloud)
# =============================================================================
#
# Poppler und Tesseract sind EXTERNE Programme (nicht Python-Pakete).
# Auf Windows muss man sie manuell installieren und die Pfade setzen.
# Auf Linux/Cloud sind sie über apt installiert und im System-PATH.

if os.name == "nt":
    # ── Windows ──
    # Poppler: Enthält pdftoppm.exe, das PDF-Seiten als Bilder rendert.
    # Download: https://github.com/oschwartz10612/poppler-windows/releases
    # Muss entpackt und der Pfad zum bin/-Ordner hier eingetragen werden.
    POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"

    # Tesseract: Die OCR-Engine, die Bilder in Text umwandelt.
    # Download: https://github.com/UB-Mannheim/tesseract/wiki
    # Beim Installieren "Additional language data: German" mitnehmen.
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    # ── Linux / macOS / Streamlit Cloud ──
    # Poppler und Tesseract liegen im Standard-PATH (/usr/bin/).
    # pdf2image findet pdftoppm automatisch, wenn POPPLER_PATH = None.
    POPPLER_PATH = None


# =============================================================================
# OCR-KONFIGURATION
# =============================================================================

# Maximale Bildbreite für den OCR-Fallback (in Pixel).
#
# Normalerweise rendern wir bei 300 DPI — das ergibt bei A4:
#   Breite: 8.27 Zoll × 300 DPI = 2481 ≈ 2480 px
#   Höhe:   11.69 Zoll × 300 DPI = 3507 px
#   → ca. 8.7 Megapixel pro Seite → kein Problem
#
# ABER: Manche PDFs haben extreme Seitenmaße (z.B. A0, Poster, Pläne).
# Bei 300 DPI können das >200 Megapixel werden → Pillow wirft einen
# DecompressionBombError als Schutz gegen RAM-Überlauf.
#
# Fallback-Strategie: Statt DPI eine fixe Maximalbreite vorgeben.
# 2480 px ≈ A4 bei 300 DPI → gute OCR-Qualität ohne RAM-Explosion.
MAX_WIDTH_PX = 2480

# Tesseract-Konfiguration:
#
#   --oem 1
#       OCR Engine Mode 1 = LSTM-basierte neuronale Netzwerk-Engine.
#       Genauer als die alte Tesseract-Engine (OEM 0), besonders bei
#       deutschen Sonderzeichen (ä, ö, ü, ß).
#
#   --psm 6
#       Page Segmentation Mode 6 = "Assume a single uniform block of text".
#       Gut für strukturierte Dokumente wie Rechnungen und Meldezettels,
#       die aus Textblöcken bestehen (im Gegensatz zu z.B. Zeitungslayouts
#       mit mehreren Spalten, wofür PSM 3 besser wäre).
#
#   preserve_interword_spaces=1
#       Leerzeichen zwischen Wörtern erhalten, statt sie zu normalisieren.
#       Kritisch für tabellarische Layouts wie:
#           "Vorname                Max"
#           "Familienname:          Mustermann"
#       Ohne diese Option würde Tesseract die Leerzeichen schlucken:
#           "Vorname Max" → nicht unterscheidbar von Fließtext.
TESS_CONFIG = "--oem 1 --psm 6 -c preserve_interword_spaces=1"


# =============================================================================
# TEXTLAYER-QUALITÄTSPRÜFUNG
# =============================================================================
#
# Nicht jeder PDF-Textlayer ist brauchbar. Manche PDFs haben zwar einen
# eingebetteten Textlayer, aber der ist kaputt — z.B. weil:
#
#   a) Ein schlechter Scanner beim Erstellen OCR lief und Fehler einbaute
#   b) Die Font-Encoding-Tabelle defekt ist (Zeichen werden falsch gemappt)
#   c) Text als Grafik gerendert wurde und pypdf nur Artefakte liest
#
# In solchen Fällen ist frisches OCR auf dem gerenderten Bild BESSER
# als der kaputte Textlayer.
#
# Wichtig: Diese Prüfung ist DOKUMENTTYP-AGNOSTISCH. Sie sucht NICHT
# nach "Meldezettel" oder "Rechnung", sondern nach allgemeinen Mustern
# kaputter Textlayer. Dadurch funktioniert sie für alle Dokumenttypen gleich.

def looks_like_bad_textlayer(text: str) -> bool:
    """
    Prüft, ob ein extrahierter Textlayer "kaputt" ist und OCR stattdessen
    verwendet werden sollte.

    Rückgabe:
        True  → Textlayer ist kaputt → OCR verwenden
        False → Textlayer ist brauchbar → direkt verwenden

    Prüfungen (in Reihenfolge):
        1. Text leer oder zu kurz (< 25 Zeichen)
        2. Enthält bekannte Misread-Muster (z.B. "Vomame" statt "Vorname")
        3. Zu viele kurze Tokens (> 40% haben ≤ 2 Zeichen)
        4. Nicht-druckbare Steuerzeichen vorhanden
    """

    # ── Check 1: Leer oder zu kurz ──
    # Ein brauchbarer Textlayer hat mindestens ~25 Zeichen.
    # Weniger deutet auf eine leere Seite oder rein grafischen Inhalt hin.
    if not text:
        return True

    s = text.strip()
    if len(s) < 25:
        return True

    low = s.lower()

    # ── Check 2: Bekannte Misread-Marker ──
    # Diese Strings tauchen auf, wenn ein kaputter Textlayer typische
    # Wörter aus österreichischen Behördendokumenten falsch kodiert.
    # Das passiert oft, wenn die Font-Encoding-Tabelle in der PDF defekt ist:
    # Die richtigen Glyphen werden angezeigt, aber beim Kopieren/Extrahieren
    # kommen falsche Unicode-Codepoints heraus.
    #
    # Hinweis: Diese Marker wurden empirisch aus echten fehlerhaften PDFs
    # gesammelt — sie decken die häufigsten Fälle ab, nicht alle.
    bad_markers = [
        "vomame",            # "Vorname"           → "Vomame"
        "hauptwohnsitr",     # "Hauptwohnsitz"     → "Hauptwohnsitr"
        "staatsangehdr",     # "Staatsangehörigkeit"→ "Staatsangehdr..."
        "postleieahl",       # "Postleitzahl"      → "PostleiEahl"
        "wohnsitrqual",      # "Wohnsitzqualität"  → "Wohnsitrqual..."
    ]
    if any(m in low for m in bad_markers):
        return True

    # ── Check 3: Zu viele kurze Tokens ──
    # Kaputter Textlayer extrahiert oft einzelne Buchstaben statt Wörter:
    #   Sauber:  "Familienname Mustermann"    → Tokens: ["Familienname", "Mustermann"]
    #   Kaputt:  "F a m i l i e n n a m e" → Tokens: ["F","a","m","i",...]
    #
    # Schwellwert 40%: In normalem Text sind ca. 5-15% kurze Tokens
    # (Artikel wie "am", "in", Zahlen wie "1", "37"). Über 40% ist
    # ein starker Indikator für einen kaputten Textlayer.
    tokens = re.findall(r"\w+", low)
    if tokens:
        short = sum(1 for t in tokens if len(t) <= 2)
        if short / len(tokens) > 0.40:
            return True

    # ── Check 4: Nicht-druckbare Steuerzeichen ──
    # Normale PDF-Texte enthalten keine Steuerzeichen (außer \n und \r).
    # Wenn welche da sind, ist die Kodierung kaputt.
    #
    # Geprüft werden:
    #   ASCII 0-8 (NUL, SOH, STX, ...) → kommen in keinem normalen Text vor
    #   0x0B (Vertikal-Tab) → in PDFs ein Zeichen für kaputtes Encoding
    #   0x0C (Form Feed) → in PDFs oft ein Artefakt
    #
    # NICHT geprüft: \n (0x0A) und \r (0x0D) → sind normale Zeilenumbrüche
    non_print = sum(1 for ch in s if ord(ch) < 9 or ord(ch) in (0x0b, 0x0c))
    if non_print > 0:
        return True

    # Alle Checks bestanden → Textlayer ist brauchbar
    return False


# =============================================================================
# TESSERACT-VERFÜGBARKEIT SICHERSTELLEN
# =============================================================================

def _ensure_tesseract_available() -> None:
    """
    Findet Tesseract im System-PATH und setzt den Pfad für pytesseract.

    Hintergrund:
        Auf Linux/Cloud ist Tesseract über apt installiert und liegt unter
        /usr/bin/tesseract. pytesseract braucht den vollständigen Pfad,
        um das Programm aufrufen zu können.

        shutil.which("tesseract") durchsucht den PATH und gibt den
        vollständigen Pfad zurück (oder None, wenn nicht gefunden).

    Idempotent: Kann mehrfach aufgerufen werden ohne Seiteneffekte.
    Wird vor jedem OCR-Aufruf in ocr_page() aufgerufen.
    """
    cmd = shutil.which("tesseract")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


# =============================================================================
# PDF-SEITE → BILD RENDERN (interne Hilfsfunktion)
# =============================================================================

def _convert_single_page(path: Path, page_index: int, *, dpi: int | None = None, size=None):
    """
    Rendert eine einzelne PDF-Seite als Bild (PIL Image).

    Wird intern von ocr_page() aufgerufen — nicht direkt verwenden.

    Parameter:
        path:       Pfad zur PDF-Datei
        page_index: 1-basierter Seitenindex (1 = erste Seite).
                    pdf2image/Poppler verwenden 1-basierte Indizes.
        dpi:        Auflösung in DPI (z.B. 300).
                    Höhere DPI = bessere OCR, aber mehr RAM.
        size:       Alternative zur DPI: Tuple (Breite, Höhe) in Pixeln.
                    Höhe=None → wird proportional berechnet.
                    Vorteil: Kontrollierte Bildgröße unabhängig von Seitenmaßen.

    Rückgabe:
        Liste von PIL Images (genau 1 Bild, da wir nur 1 Seite rendern).

    Technik:
        pdf2image ruft Poppler (pdftoppm) auf, das die PDF-Seite in ein
        Pixelbild rendert. Das Bild wird als PIL.Image zurückgegeben,
        das Tesseract direkt verarbeiten kann.
    """
    kwargs = dict(
        first_page=page_index,     # Nur diese eine Seite rendern
        last_page=page_index,      # (Start = Ende = genau 1 Seite)
    )

    if dpi is not None:
        kwargs["dpi"] = dpi        # z.B. 300 → A4 wird ~2480×3507 px
    if size is not None:
        kwargs["size"] = size      # z.B. (2480, None) → Breite fix, Höhe proportional
    if POPPLER_PATH:
        kwargs["poppler_path"] = POPPLER_PATH  # Nur auf Windows gesetzt

    return convert_from_path(str(path), **kwargs)


# =============================================================================
# OCR FÜR EINE EINZELNE SEITE
# =============================================================================
#
# Warum Seite für Seite statt die ganze PDF auf einmal?
#
#   1. SPEICHER: Eine 10-seitige PDF bei 300 DPI erzeugt ~500 MB Bilddaten.
#      Pro Seite sind es nur ~50 MB → viel stabiler auf Cloud-Servern
#      (Streamlit Cloud hat begrenzt RAM).
#
#   2. FEHLERBEHANDLUNG: Wenn eine Seite zu groß ist (DecompressionBomb),
#      können wir sie einzeln mit Fallback behandeln, ohne andere Seiten
#      zu verlieren.
#
#   3. MISCHUNG: Manche PDFs haben gemischte Seiten — Seite 1 mit Textlayer,
#      Seite 2 als Bild. extract_text_from_pdf() entscheidet PRO SEITE,
#      ob OCR nötig ist oder der Textlayer reicht.

def ocr_page(path: Path, page_index: int) -> str:
    """
    Führt OCR auf einer einzelnen PDF-Seite durch.

    Ablauf:
        1. Tesseract-Verfügbarkeit prüfen (_ensure_tesseract_available)
        2. PDF-Seite als Bild rendern bei 300 DPI (_convert_single_page)
        3. Bei DecompressionBombError: Fallback mit fixer Breite (2480 px)
        4. Tesseract-OCR auf dem Bild ausführen (Sprache: Deutsch)

    Parameter:
        path:       Pfad zur PDF-Datei
        page_index: 1-basierter Seitenindex

    Rückgabe:
        Erkannter Text als String. Kann leer sein, wenn die Seite
        komplett unleserlich ist (z.B. nur ein Bild ohne Text).

    Performance:
        ~2–5 Sekunden pro Seite (abhängig von Komplexität und Hardware).
        Deshalb wird OCR nur als Fallback verwendet, wenn der Textlayer
        fehlt oder kaputt ist.
    """

    # Tesseract-Pfad sicherstellen (besonders auf Linux/Cloud)
    _ensure_tesseract_available()

    # ── DecompressionBombError vorbereiten ──
    # Pillow (PIL) hat eine Sicherheitsbegrenzung: Bilder mit mehr als
    # ~178 Megapixeln lösen einen DecompressionBombError aus, um
    # RAM-Überläufe zu verhindern.
    #
    # Wir importieren den spezifischen Error-Typ hier, damit wir ihn
    # gezielt abfangen können. Falls Pillow nicht importierbar ist
    # (extrem unwahrscheinlich, da pdf2image es braucht), fangen wir
    # generische Exceptions ab.
    try:
        from PIL import Image
        BombError = Image.DecompressionBombError
    except Exception:
        BombError = Exception

    # ── Versuch 1: Rendern bei 300 DPI (beste OCR-Qualität) ──
    try:
        images = _convert_single_page(path, page_index, dpi=300)

    except (BombError, MemoryError):
        # ── Versuch 2: Fallback mit fixer Breite ──
        # Das Bild wird auf MAX_WIDTH_PX (2480) Breite skaliert,
        # unabhängig von den tatsächlichen Seitenmaßen.
        # Ergebnis: Kontrollierte Bildgröße, etwas weniger scharf bei
        # sehr großen Seiten, aber stabil und für OCR ausreichend.
        images = _convert_single_page(path, page_index, size=(MAX_WIDTH_PX, None))

    # ── OCR auf den gerenderten Bildern ausführen ──
    # images ist eine Liste (normalerweise genau 1 Bild für 1 Seite).
    # Tesseract verarbeitet jedes Bild einzeln:
    #   - lang="deu": Deutsche Sprachdaten (Umlaute, ß, deutsche Wörter)
    #   - config: LSTM-Engine, Block-Modus, Leerzeichen erhalten
    out_parts = []
    for img in images:
        out_parts.append(
            pytesseract.image_to_string(
                img,
                lang="deu",          # Deutsch: erkennt ä, ö, ü, ß korrekt
                config=TESS_CONFIG,  # --oem 1 --psm 6 preserve_interword_spaces
            )
        )
    return "\n".join(out_parts).strip()


# =============================================================================
# HAUPTFUNKTION: TEXT AUS PDF EXTRAHIEREN
# =============================================================================
#
# Dies ist die EINZIGE Funktion, die von außen aufgerufen wird:
#
#   document_classifier.py:
#       text = extract_text_from_pdf(pdf_path)
#       doc_type, confidence = classify_document(text)
#
#   app.py (Streamlit):
#       text = extract_text_from_pdf(pdf_path)
#       doc_type, confidence = classify_document(text)
#       classified_pdfs.append((pdf_path, doc_type, text, confidence))
#
# Die Funktion abstrahiert komplett weg, ob ein PDF digital erstellt
# oder gescannt wurde. Der Aufrufer bekommt immer einen String zurück
# und muss sich nicht um Textlayer vs. OCR kümmern.

def extract_text_from_pdf(path: Path) -> str:
    """
    Extrahiert den gesamten Text aus einer PDF-Datei.

    Strategie pro Seite:
        1. Textlayer mit pypdf auslesen (page.extract_text())
        2. Qualitätsprüfung mit looks_like_bad_textlayer()
        3. Falls brauchbar → direkt verwenden (~1 ms pro Seite)
        4. Falls kaputt/leer → OCR-Fallback (~2–5 s pro Seite)

    Parameter:
        path: Pfad zur PDF-Datei

    Rückgabe:
        Gesamttext aller Seiten, getrennt durch \\f (Form Feed).
        Leere Seiten werden herausgefiltert.

        Warum \\f als Trenner?
            Form Feed (ASCII 12) ist der Standard-Seitentrenner.
            Die Decision Engine nutzt ihn, um Multi-Page-Monatsrechnungen
            seitenweise aufzuteilen:
                pages = text.split('\\f')  → je eine Rechnung pro Seite

    Beispiel:
        3-seitige Monatsrechnungs-PDF:
        >>> text = extract_text_from_pdf(Path("rechnungen.pdf"))
        >>> pages = text.split('\\f')
        >>> len(pages)
        3
        >>> "Leistungszeitraum: 01.09" in pages[0]
        True
    """

    # pypdf liest die interne PDF-Struktur (Seiten, Fonts, Textlayer)
    reader = PdfReader(path)

    # Gesammelter Text pro Seite
    parts: list[str] = []

    # ── Jede Seite einzeln verarbeiten ──
    # enumerate startet bei 1, weil pdf2image 1-basierte Seitenindizes
    # erwartet (Seite 1 = first_page=1, nicht 0).
    for idx, page in enumerate(reader.pages, start=1):

        # Textlayer direkt auslesen (pypdf).
        # Gibt den eingebetteten Text zurück, oder "" bei reinen Bild-PDFs.
        text = (page.extract_text() or "").strip()

        # Qualitätsprüfung: Textlayer vorhanden UND brauchbar?
        if text and not looks_like_bad_textlayer(text):
            # ✓ Guter Textlayer → direkt verwenden (schneller Pfad)
            parts.append(text)
            continue

        # ✗ Kein Textlayer oder kaputt → OCR als Fallback
        parts.append(ocr_page(path, idx))

    # ── Seiten mit Form Feed verbinden ──
    # \f trennt die Seiten, damit nachgelagerte Logik (z.B. Decision Engine)
    # Multi-Page-PDFs seitenweise verarbeiten kann.
    # Leere Seiten werden herausgefiltert (if p → nur nicht-leere Strings).
    return "\f".join(p for p in parts if p)