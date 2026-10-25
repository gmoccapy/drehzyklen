# Drehzyklen – wie das Programm arbeitet

Kurzer Überblick für alle, die den Code lesen oder ändern wollen. Bedienung: siehe `README.md`.

## Grundidee

Ein **Programm** ist eine Liste von **Zyklen** plus **Rohteil**. Jeder Zyklus ist ein Objekt mit
seinen Eingabewerten (ein `dict`). Erst beim Erzeugen wird daraus G-Code; dabei wird das Material
mitsimuliert, damit spätere Zyklen wissen, was noch steht, und Kollisionen erkannt werden.

```
 GUI (gui.py, widgets.py)
   │  Eingabefelder ← Feldbeschreibungen P (cycles/params.py, Cycle.PARAMS)
   │  Werte-dict  →  Cycle-Objekt (cycles/facing.py, … )
   ▼
 Program (cycles/program.py)  ── Rohteil (cycles/stock.py), Maschine (machine.py),
   │                              Werkzeuge (tools.py), Gewinde (threads.py)
   │  generate()
   │   1. _check_ops     Werte prüfen, Werkzeug prüfen (cycles/toolcheck.py), Fertigkontur
   │   2. _header        G18 G7 G21 …, Rückzugsebene
   │   3. je Zyklus      _machining: Werkzeugwechsel, S/M3/M4/M8, cycle.body(), Rückzug
   │                     _gcode_block: eigener Code, Spindel/Kühlung nachführen
   │   4. Material       material.apply(cycle), Eilgang-durch-Material-Prüfung
   │   5. Ende           M9 M5, Wechselposition, M2, Futterprüfung
   ▼
 Result: G-Code-Text, Bahn (für Vorschau), Fertigkonturen, Hinweise, Fehler
   │
   ▼
 Vorschau (render.py), Parameterbild (helpdraw.py), Werkzeugbild (tooldraw.py)
```

## Die Zyklen (`drehzyklen/cycles/`)

Jeder Zyklus ist eine Klasse in einem eigenen Modul. Oben im Modul steht in Worten, welche
Werkzeugbahn er fährt.

| Modul | Klasse | Kern |
|---|---|---|
| `facing.py` | `Facing` | `FacingPlan`: Zustellungen in Z, radial zur Mitte oder nach außen |
| `longturning.py` | `LongTurning` | baut eine Kontur (Zylinder + Kanten) → `ContourTurner` |
| `taper.py` | `Taper` | Konus als Kontur → `ContourTurner` |
| `edgebreak.py` | `EdgeBreak` | Fase/Radius als Kontur → `ContourTurner` |
| `contour.py` | `Contour` | freie Kontur (Text aus `geometry.py`) → `ContourTurner` |
| `thread.py` | `Thread` | ein G76-Satz im Radiusmodus G8, Gewindetabellen |
| `drill.py` | `Drill` | Bohren durchgehend, Spanbruch, Entspanen |
| `gcodeblock.py` | `GCodeOp` | eigener G-Code, wird unverändert eingefügt |

Ein Zyklus liefert (Basisklasse `Cycle` in `base.py`):

- `PARAMS` – die Eingabefelder (Name, Einheit, Grenzen, Auswahlwerte). Die GUI baut daraus das
  Formular, `check()` die Grenzwertprüfung.
- `check(machine)` – Plausibilität, wirft `ContourError` bei Fehlern, gibt Hinweise zurück.
- `outline()` – die Fertigkontur (für die Vorschau und die Abtragssimulation).
- `tool_problems(tool)` – passt das Werkzeug? (Hand, Lage, Freiwinkel, Gewindewinkel …)
- `body(emitter, machine, material)` – schreibt die Bewegungen.
- `switch(key, values)` – rechnet beim Umschalten um (z. B. G96 ↔ G97, Konus Winkel ↔ Ø).

Gemeinsame Teile:

- `turning.py`: `TurningCycle` (Basis für Längs, Konus, Fase, Kontur) und `ContourTurner`, der aus
  einer Kontur Schruppebenen und den Schlichtschnitt (mit G41/G42) erzeugt.
- `params.py`: Feldbeschreibung `P`, gemeinsame Gruppen (Werkzeug, Drehzahl/Vorschub, Kühlung,
  Schruppen) und die Auswahlschlüssel.
- `toolcheck.py`: Werkzeugprüfung für Drehzyklen.

## Arbeitssystem und Spiegelung

Jeder Zyklus rechnet in einem einheitlichen **Arbeitssystem**: Bearbeitung nach −Z, Material
oberhalb der Kontur (Außenbearbeitung von rechts). Andere Fälle werden gespiegelt:

- innen → `radius_sign = -1` (Spiegelung an der Drehachse)
- von links nach rechts (Z1 < Z2) → `z_sign = -1` (Spiegelung an Z = 0)

`TransformedEmitter` (`gcode.py`) nimmt die Bewegungen im Arbeitssystem an und schreibt sie
zurückgespiegelt in den echten `Emitter`. So gibt es die Schrupplogik nur einmal.

## Material und Prüfungen

- `material.py`: Das Werkstück ist ein Raster über Z mit Außen- und Innenradius je Spalte.
  `apply(cycle)` schneidet die Fertigkontur des Zyklus heraus.
- Konus, Fase und Kontur schruppen ab dem **tatsächlich vorhandenen** Material.
- Nach jedem Zyklus wird geprüft, ob ein Eilgang durch noch stehendes Material fährt
  (`_rapid_check`), am Ende, ob eine Bahn hinter die Futterfläche geht (`_chuck_check`).
- Fehler in einem Zyklus nehmen dessen Ausgabe vollständig zurück; die anderen Zyklen werden
  trotzdem erzeugt, damit die Vorschau aussagekräftig bleibt.

## Spindel und Kühlung

`SpindleState` merkt sich, was gerade eingeschaltet ist (S-Wort, M8, Werkzeug). Ein Zyklus gibt S,
M3/M4 und M8/M9 nur aus, wenn sich etwas ändert. Vor einem Werkzeugwechsel wird M9/M5 ausgegeben.
Ein G-Code-Schritt wird nach M5/M9 durchsucht. Mit „Spindel/Kühlung danach wieder einschalten“
werden sie direkt nach dem Schritt wieder eingeschaltet, sonst schaltet der nächste Zyklus sie ein.

## Werkzeuge

`tools.py` liest die `tool.tbl` (aus der INI) und leitet aus Q, I, J die Plattenform, Hand und Lage
ab. Wenn das nicht geht, gibt der Benutzer Typ/Hand/Lage ein (`werkzeuge.json`). Mit
„→ In tool.tbl schreiben“ werden D, I, J, Q in die tool.tbl zurückgeschrieben.

## Daten und Sprache

- Gespeichert wird **sprachunabhängig**: Auswahlfelder speichern Schlüssel (`"outside"`,
  `"G96_G95"`, `"chamfer"` …). Die Anzeige holt den Text über `P.labels` und übersetzt ihn.
  Alte deutsche Werte werden beim Laden über `LEGACY_VALUES` umgesetzt.
- `i18n.py`: `_("…")` übersetzt beim Aufruf, `N_("…")` markiert Texte, die erst später übersetzt
  werden (z. B. Feldnamen). Die `.po`-Dateien in `locale/` werden direkt gelesen.
- Sprachwechsel: `App.rebuild()` (`gui.py`) merkt sich Programm und Eingaben, baut die Oberfläche
  in der neuen Sprache neu auf und stellt alles wieder her.

## Wo ändere ich was?

| Aufgabe | Datei |
|---|---|
| neuen Zyklus anlegen | neues Modul in `cycles/`, Klasse von `Cycle`/`TurningCycle`, in `CYCLES` (`program.py`) eintragen, Bild in `helpdraw.py` |
| Eingabefeld ergänzen | `PARAMS` der Zyklusklasse |
| Werkzeugbahn ändern | `body()` des Zyklus bzw. `ContourTurner` |
| Ablauf zwischen Zyklen | `Program._machining` / `_gcode_block` |
| neuer Text | `_("…")` im Code, dann `python3 scripts/update_translations.py`, `de.po`/`es.po` ergänzen |
| Gewinde ergänzen | `data/threads.json` oder `~/.config/drehzyklen/threads.json` |
| Tests | `python3 tests/test_core.py` |
