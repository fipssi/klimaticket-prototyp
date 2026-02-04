# KlimaTicket-Förderantragsprüfung

Automatisierte Validierung von KlimaTicket-Förderanträgen der Stadt Salzburg.

Das System prüft hochgeladene PDF-Dokumente (Meldezettels, Rechnungen, Zahlungsbestätigungen) automatisch gegen Antragsdaten und gibt eine Genehmigungsempfehlung ab.

---

## Inhaltsverzeichnis

- [Projektübersicht](#projektübersicht)
- [Architektur](#architektur)
- [Module im Detail](#module-im-detail)
- [Ordnerstruktur](#ordnerstruktur)
- [Installation](#installation)
- [Verwendung](#verwendung)
- [Validierungsregeln](#validierungsregeln)
- [OCR-Robustheit](#ocr-robustheit)
- [Konfiguration](#konfiguration)

---

## Projektübersicht

### Was macht das System?

Ein KlimaTicket-Förderantrag besteht aus:

1. **Antragsdaten** (JSON) — Name, Geburtsdatum, PLZ, Gültigkeitszeitraum
2. **Meldezettel** (PDF) — Nachweis des Hauptwohnsitzes in Salzburg
3. **Rechnungsnachweise** (PDFs) — Jahresrechnung + Zahlungsbestätigung ODER ≥ 3 Monatsrechnungen

Das System validiert automatisch:

| Prüfung | Was wird geprüft? |
|---|---|
| **Meldezettel** | Name, Geburtsdatum, PLZ (förderberechtigt?) |
| **Jahresrechnung** | Name, Gültigkeitszeitraum, Leistungszeitraum |
| **Zahlungsbestätigung** | Name, Gültigkeitszeitraum |
| **Monatsrechnungen** | Name, Gültigkeit, Leistung — ≥ 3 verschiedene Monate |

### Zwei Betriebsmodi

- **Batch-Modus** (`main.py`): Verarbeitet alle Cases auf einmal → Excel-Report
- **Web-Interface** (`app.py`): Einzelfallprüfung per Streamlit-UI

---

## Architektur

### Pipeline

```
PDF-Dateien
    │
    ▼
┌──────────────────┐
│ document_loader   │  PDF → Text (Textlayer oder OCR)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ document_classifier│  Text → Dokumenttyp + Konfidenz
│ (ML: TF-IDF+SVM)  │  ("jahresrechnung", 0.95)
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│ Validierung                                       │
│  ┌─────────────────────────┐ ┌──────────────────┐│
│  │ registration_validation │ │ invoice_validation││
│  │ (Meldezettel)           │ │ (Rechnungen)     ││
│  └────────────┬────────────┘ └────────┬─────────┘│
│               │                       │           │
│               ▼                       ▼           │
│         ┌──────────────────────────────┐          │
│         │     decision_engine          │          │
│         │  Gesamtentscheidung: OK/NOK  │          │
│         └──────────────────────────────┘          │
└──────────────────────────────────────────────────┘
         │
         ▼
    Excel-Report / Streamlit-UI
```

### Modulabhängigkeiten

```
utils.py                          ← Gemeinsame Hilfsfunktionen
    ↑                                (normalize_for_matching, _compact, ...)
    │
    ├── invoice_validation.py     ← Rechnungsvalidierung
    ├── registration_validation.py← Meldezettelvalidierung
    │       ↑
    │       │
    ├── decision_engine.py        ← Gesamtentscheidung
    │       ↑
    │       │
    ├── document_classifier.py    ← ML-Klassifizierung
    │   document_loader.py        ← PDF-Textextraktion
    │       ↑
    │       │
    ├── main.py                   ← Batch-Verarbeitung
    └── app.py                    ← Streamlit Web-UI
```

---

## Module im Detail

### `document_loader.py` — PDF → Text

Extrahiert Text aus PDF-Dateien mit einer Zwei-Stufen-Strategie:

1. **Textlayer lesen** (pypdf): Schnell, wenn vorhanden
2. **OCR-Fallback** (Tesseract): Wenn der Textlayer fehlt, beschädigt ist oder unlesbar

Enthält eine Qualitätsprüfung: Wenn der Textlayer zu wenig erkennbare Wörter enthält oder das OCR-Fehlermuster "Vomame" (statt "Vorname") auftaucht, wird automatisch OCR verwendet.

### `document_classifier.py` — Text → Dokumenttyp

ML-basierte Klassifizierung mit vortrainiertem TF-IDF + Classifier:

- **Eingabe**: Extrahierter Text einer PDF
- **Ausgabe**: Dokumenttyp + Konfidenz (z.B. `"jahresrechnung"`, `0.95`)
- **Typen**: `jahresrechnung`, `monatsrechnung`, `zahlungsbestaetigung`, `meldezettel`, `unbekannt`
- **Modelle**: `models/document_vectorizer.joblib`, `models/document_classifier.joblib`

### `registration_validation.py` — Meldezettel-Validierung

Extrahiert Personendaten aus dem Meldezettel-Text und vergleicht sie mit dem Antrag.

**Unterstützte Layouts:**
- Layout A: `Vorname: Max Mustermann` (inline mit Doppelpunkt)
- Layout B: Label + Wert auf getrennten Zeilen
- Layout C: Label-Block + Werte-Block (z.B. Salzburg)
- Layout D: Label + Wert durch Leerzeichen getrennt (z.B. Linz)

**Prüfungen:**
- Vorname (flexibel: erster Vorname reicht, Umlaute tolerant)
- Nachname (Doppelnamen, OCR-Fehler)
- Geburtsdatum (verschiedene Formate → ISO-Vergleich)
- PLZ (muss im Stadtgebiet Salzburg liegen UND mit Antrag übereinstimmen)

### `invoice_validation.py` — Rechnungs-Validierung

Validiert drei verschiedene Rechnungstypen:

| Funktion | Dokumenttyp | Prüfungen |
|---|---|---|
| `validate_rechnung()` | Jahresrechnung | Name bei "Karteninhaber", Gültigkeitszeitraum, Leistungszeitraum |
| `validate_zahlungsbestaetigung()` | Zahlungsbestätigung | Name bei "für", Gültigkeitszeitraum |
| `validate_monatsrechnung()` | Monatsrechnung | Name, Gültigkeit, Leistung innerhalb Gültigkeit |

**Marker-basiertes Name-Matching:**
- Sucht den Namen nicht im gesamten Text, sondern nur in der Nähe bekannter Marker ("Karteninhaber", "für")
- Verhindert False Positives (z.B. Firmenname "One Mobility GmbH")

### `decision_engine.py` — Gesamtentscheidung

Orchestriert alle Validierungen und trifft die Gesamtentscheidung:

```
all_ok = meldezettel_ok AND rechnungen_ok
```

**Rechnungsnachweis (zwei Wege):**
- **Weg 1**: Jahresrechnung OK + Zahlungsbestätigung OK
- **Weg 2**: ≥ 3 gültige Monatsrechnungen (verschiedene Monate)

**Reklassifizierung:** Jahresrechnungen mit Leistungszeitraum < 10 Monate werden automatisch als Monatsrechnungen behandelt.

### `utils.py` — Gemeinsame Hilfsfunktionen

- `normalize_for_matching()`: Lowercase, Umlaute entfernen, Whitespace normalisieren
- `_compact()`: Alle Leerzeichen entfernen (für OCR-Robustheit)
- `_variants_for_umlaut_translit()`: Umlaut-Varianten generieren (ö → oe/o)

### `main.py` — Batch-Verarbeitung

Iteriert über alle Cases in `data/cases/` und schreibt einen Excel-Report mit:
- Antragsdaten, Prüfergebnisse, Fehlertexte, Klassifizierung pro PDF
- Fehlertoleranz: Einzelne Cases können fehlschlagen, ohne den Batch abzubrechen
- Fallback: Schreibt Datei mit Zeitstempel, wenn Excel-Datei gesperrt ist

### `app.py` — Streamlit Web-Interface

Interaktive Einzelfallprüfung:
- Antragsdaten eingeben
- PDFs hochladen
- Ergebnis live anzeigen (Klassifizierung + Validierung + Entscheidung)

---

## Ordnerstruktur

```
klimaticket-foerderung/
├── src/                           Python-Module
│   ├── main.py                    Batch-Verarbeitung (Entry Point)
│   ├── document_loader.py         PDF → Text
│   ├── document_classifier.py     Text → Dokumenttyp (ML)
│   ├── invoice_validation.py      Rechnungs-Validierung
│   ├── registration_validation.py Meldezettel-Validierung
│   ├── decision_engine.py         Gesamtentscheidung
│   ├── utils.py                   Gemeinsame Hilfsfunktionen
│   └── try_classifier.py          Test-Skript für Klassifizierung
│
├── models/                        ML-Modelle (nicht im Git)
│   ├── document_vectorizer.joblib TF-IDF Vectorizer
│   └── document_classifier.joblib Trainierter Classifier
│
├── data/
│   └── cases/                     Eingabedaten
│       ├── 2024-09/               Monats-Ordner
│       │   ├── 12345/             Case-Ordner
│       │   │   ├── antrag.json    Antragsdaten
│       │   │   └── *.pdf          PDF-Dokumente
│       │   └── ...
│       └── ...
│
├── app.py                         Streamlit Web-Interface
├── packages.txt                   Systemabhängigkeiten (apt, für Streamlit Cloud)
├── requirements.txt               Python-Abhängigkeiten (pip)
├── install.txt                    Installationsanleitung
├── case_report.xlsx               Ausgabe (wird generiert)
└── README.md                      Diese Datei
```

---

## Installation

### Schnellstart (Linux)

```bash
# Repository klonen
git clone https://github.com/<username>/klimaticket-foerderung.git
cd klimaticket-foerderung

# Systemabhängigkeiten
sudo apt install -y tesseract-ocr tesseract-ocr-deu poppler-utils

# Python-Umgebung
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ML-Modelle in models/ kopieren

# Starten
cd src && python main.py          # Batch
streamlit run app.py              # Web-UI
```

### Detaillierte Anleitung

Siehe [install.txt](install.txt) für Windows, Linux und Streamlit Cloud.

### Systemvoraussetzungen

| Komponente | Version | Zweck |
|---|---|---|
| Python | ≥ 3.10 | Type Hints (`list[str]`, `dict \| None`) |
| Tesseract OCR | ≥ 5.0 | OCR-Engine für PDFs ohne Textlayer |
| Tesseract-deu | — | Deutsches Sprachpaket für Tesseract |
| Poppler | ≥ 22.0 | PDF-Rendering (pdftoppm) |

### Python-Pakete

| Paket | Zweck | Verwendet in |
|---|---|---|
| `pypdf` | PDF-Textlayer lesen | document_loader.py |
| `pdf2image` | PDF → Bild (für OCR) | document_loader.py |
| `pytesseract` | Bild → Text (OCR) | document_loader.py |
| `Pillow` | Bildverarbeitung | document_loader.py |
| `scikit-learn` | ML-Klassifizierung | document_classifier.py |
| `joblib` | Modell-Serialisierung | document_classifier.py |
| `pandas` | Excel-Export | main.py |
| `openpyxl` | .xlsx-Schreiber | main.py (via pandas) |
| `streamlit` | Web-Interface | app.py |

---

## Verwendung

### Batch-Verarbeitung

```bash
cd src
python main.py
```

Verarbeitet alle Cases in `data/cases/<monat>/<case_id>/` und schreibt `case_report.xlsx`.

**Eingabe pro Case:**
- `antrag.json` mit Pflichtfeldern: `vorname`, `familienname`, `geburtsdatum`, `plz`, `gilt_von`, `gilt_bis`
- Beliebig viele PDF-Dateien

**Ausgabe (Excel-Spalten):**
- Antragsdaten, Meldezettel-Ergebnis, Rechnungs-Ergebnis
- Detaillierte Fehlertexte pro Prüfung
- Gesamtergebnis (`all_ok`: True/False)
- Klassifizierungszusammenfassung pro PDF

### Streamlit Web-Interface

```bash
streamlit run app.py
```

Öffnet `http://localhost:8501` im Browser.

1. Antragsdaten eingeben (Vorname, Nachname, PLZ, Geburtsdatum, Gültigkeit)
2. PDF-Dokumente hochladen
3. "Prüfen" klicken → Ergebnis wird angezeigt

---

## Validierungsregeln

### Meldezettel

```
meldezettel_ok = vorname_ok AND nachname_ok AND geburtsdatum_ok AND plz_ok
```

| Check | Regel |
|---|---|
| `vorname_ok` | Erster Vorname aus Antrag muss im Melde-Vornamen vorkommen |
| `nachname_ok` | Alle Nachname-Tokens aus Antrag müssen im Melde-Nachnamen vorkommen |
| `geburtsdatum_ok` | Datum muss nach ISO-Normalisierung identisch sein |
| `plz_ok` | PLZ muss förderberechtigt sein (Salzburg) UND mit Antrag übereinstimmen |

**Förderberechtigte PLZ (Stadt Salzburg):**
5010, 5014, 5017, 5018, 5020, 5023, 5025, 5026, 5027, 5033

### Rechnungen

```
rechnungen_ok = (jahresrechnung_ok AND zahlungsbestaetigung_ok)
                OR (monatsrechnungen_valid >= 3)
```

**Jahresrechnung:**
- Name muss bei Marker "Karteninhaber" stehen (12-Zeilen-Fenster)
- Gültigkeitszeitraum muss mit Antrag übereinstimmen
- Leistungszeitraum wird extrahiert (< 10 Monate → Reklassifizierung als Monatsrechnung)

**Zahlungsbestätigung:**
- Name muss bei Marker "für" stehen (4-Zeilen-Fenster)
- Gültigkeitszeitraum muss mit Antrag übereinstimmen

**Monatsrechnungen:**
- Gleiche Prüfungen wie Jahresrechnung
- Zusätzlich: Leistungszeitraum muss innerhalb der Gültigkeit liegen
- Mindestens 3 verschiedene Monate müssen abgedeckt sein

---

## OCR-Robustheit

Das System ist auf fehlerhafte OCR-Ergebnisse vorbereitet:

| Problem | Beispiel | Lösung |
|---|---|---|
| Umlaute/Transliteration | "Jürgen" vs. "Juergen" vs. "Jurgen" | Varianten-Matching |
| ß → ss | "Größer" vs. "Groesser" | Normalisierung |
| OCR ohne Leerzeichen | "MaxMichael" statt "Max Michael" | Compact-Matching |
| O statt 0 in Daten | "O1.O1.1990" | Regex-Korrektur |
| l/I statt 1 | "l5.06.1985" | Regex-Korrektur |
| Komma statt Punkt | "01,01,1990" | Zeichenersetzung |
| Zerbrochene Labels | "Staatsa ngehörig keit" | Compact-Label-Matching |
| "Vomame" statt "Vorname" | Font-Encoding-Fehler | Bekannte OCR-Variante in Label-Liste |

---

## Konfiguration

### Windows-Pfade (document_loader.py)

```python
# Zeile ~88: Poppler-Pfad
POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"

# Zeile ~93: Tesseract-Pfad
pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### Förderberechtigte PLZ (registration_validation.py)

```python
SALZBURG_PLZ = {"5010", "5014", "5017", "5018", "5020", "5023", "5025", "5026", "5027", "5033"}
```

Bei Änderungen der Förderberechtigung muss diese Menge aktualisiert werden.

### Reklassifizierungs-Schwelle (decision_engine.py)

Jahresrechnungen mit Leistungszeitraum < 10 Monate werden als Monatsrechnungen behandelt. Dieser Schwellenwert ist in `reclassify_short_jahresrechnungen()` definiert.

### Marker-Fenstergrößen (invoice_validation.py)

| Marker | Fenster | Dokumenttyp |
|---|---|---|
| "Karteninhaber" | 12 Zeilen | Rechnungen |
| "für" | 4 Zeilen | Zahlungsbestätigungen |

---

## Lizenz

Internes Projekt der Stadt Salzburg. Nicht zur öffentlichen Verwendung bestimmt.