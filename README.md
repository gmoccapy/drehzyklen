# Drehzyklen für LinuxCNC / gmoccapy

Zyklenprogrammierung für Drehmaschinen. Du stellst Zyklen in einer Liste zusammen, prüfst Vorschau,
Abtrag und Parameterbild und erzeugst daraus das fertige CNC-Programm (`.ngc`). Das Programm läuft
als **Benutzer-Tab in gmoccapy** (ab 1024×768) oder als **eigenes Fenster**.

## Oberfläche

```
┌ Rohteil │ Werkzeuge │ Plan │ Längs │ Konus │ Gewinde │ Fase/R │ Kontur │ Bohren │ G-Code │ (Maschine) ┐
├─────────────┬╫┬──────────────────┬╫┬───────────────────────────────────────────────────────────────┤
│ Programm    │ │ Parameter        │ │ [Zyklus | Bild | G-Code | Programm]                           │
│ 1 Plandrehen│ │ Außen | Innen    │ │                                                               │
│ 2 Kontur    │ │ D  Ø Start   64  │ │   Vorschau mit Rohteil, Futter und Abtrag (orange)            │
│ 3 G-Code    │ │ …                │ │   Mausrad: Zoom · linke Maustaste: verschieben ·              │
│ ▲ ▼ ✎ ⧉ ✖  │ │ ＋ Zum Programm  │ │   Doppelklick: zurücksetzen                                   │
├─────────────┴─┴──────────────────┴─┴───────────────────────────────────────────────────────────────┤
│ Neu   Öffnen   Speichern   G-Code                                                Maschine   Laden  │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
- **Spalten:** Programm- und Parameterspalte sind schmal, ihre Breite stellst du mit den Griffen (╫)
  ein. Den Rest bekommt die Vorschau.
- **Ansicht G-Code:** springt zum Code des links markierten Programmschritts.
- **Symbole** der Auswahlleiste richten sich nach der Werkzeuglage (vor oder hinter der Drehmitte).
- **„＋ Zum Programm“** ist nach dem Anfügen gesperrt, bis du einen Parameter änderst.

## Einheitliche Bezeichnungen

| Zeichen | Bedeutung |
|---|---|
| **D** | Durchmesser Start |
| **d** | Durchmesser Ende |
| **Z1** | Z Start |
| **Z2** | Z Ende |

Die eingegebenen Werte bestimmen die Richtung:
- **Z1 > Z2:** Bearbeitung nach −Z (von rechts nach links)
- **Z1 < Z2:** Bearbeitung nach +Z (von links nach rechts), z.B. Plandrehen einer Rückseite von
  Z10 nach Z50 bei Nullpunkt am Futter, mit linkem Werkzeug
- Längsdrehen außen braucht D ≥ d, innen D ≤ d.

## Zyklen

Alle Zyklen außer Bohren und G-Code haben den Schalter **Außen | Innen**.

| Reiter | Inhalt |
|---|---|
| Plan | D → d radial, Z1 → Z2 in Zustellungen ap. D > d: zur Mitte, D < d: nach außen |
| Längs | D → d zwischen Z1 und Z2. Kante am Anfang und Ende einzeln: keine, Fase oder Radius. Die Endkante darf größer sein als der Absatz und endet dann bei D |
| Konus | „Angabe über Enddurchmesser“ (D, d, Z1, Z2) oder „Winkel“ (D, d, Z1, α/2). Umschalten berechnet den fehlenden Wert. Ein fallender Konus (z.B. D44 → d30) wird je Ebene schräg am Kegel angefahren |
| Gewinde | G76 außen/innen, Z1 Gewindeanfang, Z2 Gewindeende, Zs Startpunkt. Abfrage von L vor E |
| Fase/R | Fase L × α (Winkel zur Z-Achse) oder Radius an Vorder- oder Rückkante. **Negativer Winkel = Fase an der Rückkante**; sie wird schräg angefahren |
| Kontur | freie Kontur, Ecken je Punkt, Bögen; Richtung = erster → letzter Punkt |
| Bohren | durchgehend, Spanbruch oder Entspanen; Tiefe auf Spitze oder vollen Ø |
| G-Code | eigener Code zwischen zwei Zyklen, mit **Textbausteinen** |

Konus, Fase und Kontur schruppen ab dem **tatsächlich vorhandenen Material** (Abtragssimulation),
eine Rohteilangabe im Zyklus gibt es nicht mehr. Steht vor dem Konturanfang noch Planaufmaß, wird es
mitgeschruppt.

### Drehzahl / Vorschub
Die Auswahl „Drehzahl/Vorschub“ legt fest:
- G96 (Schnittgeschwindigkeit Vc) oder G97 (Drehzahl n)
- G95 (mm/U) oder G94 (mm/min)

Je nach Auswahl ändern sich Beschriftung und Einheit von S und F. Beim Umschalten werden die Werte
umgerechnet. Bei der Erzeugung gilt:
- G97-Drehzahlen werden auf die Maschinengrenzen begrenzt.
- G94-Vorschübe werden auf die langsamste Achse begrenzt.
- Bei G95 wird die Drehzahl so begrenzt, dass F·n die Achsgeschwindigkeit nicht überschreitet.
- G96 erhält D = Maximaldrehzahl der Maschine.

Die **Drehrichtung „auto“** ist M3 bei Werkzeug vor der Drehmitte und M4 bei Werkzeug dahinter.

### Kontur
| Eingabe | Bedeutung |
|---|---|
| `X Z` / `U W` | Gerade, absolut / inkremental (X = Durchmesser) |
| `R5`, `C2` | Eckenradius, Fase 45° an diesem Punkt |
| `C2 A30` / `C2 B30` | Fase 2 mm auf der ankommenden / abgehenden Kante, 30° zu ihr |
| `R0` | Ecke scharf lassen |
| `G2`/`G3 X Z R10` (`LA`) | Bogen mit Radius (langer Bogen > 180°) |
| `G2`/`G3 X Z I K` | Bogen über Mittelpunkt (relativ) |
| `G3 X Z TS` / `TE` | Bogen tangential am Anfang / Ende |
| `RND5` / `CHR2` | Radius/Fase an einem Bogen-Endpunkt |

## Rohteil

Im Reiter Rohteil – und nur dort – stellst du ein:
- Außen-Ø D und Innen-Ø d
- Ausspannlänge L ab Futter
- Nullpunkt an der Vorderkante (mit Planaufmaß a) oder am Futter
- Sicherheitsabstand s

Daraus folgt:
- D belegt „Ø Start“ in Plan- und Längsdrehen vor (innen: d).
- **Fahrten hinter die Futterfläche** sind bei Außenbearbeitung ein Fehler, bei Bohren und
  Innenbearbeitung eine Warnung.
- **Eilgänge durch Material**, das nach dem Zyklus noch steht, werden als Fehler gemeldet, z.B. bei
  falscher Reihenfolge oder vertauschten Z1/Z2.
- Innenzyklen ohne Vorbohrung melden „zuerst bohren“.

## Werkzeuge

**Mit LinuxCNC-Konfiguration** wird die `tool.tbl` aus `[EMCIO] TOOL_TABLE` gelesen:
- Aus Orientierung Q und den Winkeln I/J werden Plattenform, Hand (links/rechts) und Lage
  (außen/innen) abgeleitet.
- Geht das nicht (I = J = 0, Q = 0 oder 9), öffnet sich beim Start der Reiter Werkzeuge. Dort gibst
  du Typ, Hand und Lage an. Die Angabe wird gespeichert und **neu abgefragt, sobald sich die Zeile
  in der tool.tbl ändert**.
- Die Grafik zeigt jedes Werkzeug mit Schneidplatte, Halter, κ, ε und Schnittrichtung.
- Wenn ein bekanntes rechtes Werkzeug als linkes erscheint, schalte „Winkel I/J der tool.tbl in Z
  spiegeln“ um. Die Winkelkonvention der LinuxCNC-Doku („im Uhrzeigersinn ab Z+“) ist nicht
  eindeutig genug beschrieben, um das sicher festzulegen.

**Stand Alone** sind vorbelegt (`werkzeuge.json`, editierbar):
- ISO C, D, T und V, jeweils links und rechts
- Einstechwerkzeug, Gewindewerkzeug
- zusätzlich ein Bohrer und eine Bohrstange

**Plausibilitätsprüfung** in jedem Zyklus. Gemeldet werden unter anderem:
- Gewinde ohne Gewindewerkzeug (z.B. mit Form C)
- falsche Hand für die Schnittrichtung
- Außenwerkzeug für Innenbearbeitung
- zu wenig Freiwinkel für fallende Konturen

Das ist ein Fehler, den du mit **„Werkzeug trotz Warnung freigeben“** übersteuern kannst, z.B. für
Rund- oder Sondergewinde. Dann bleibt eine deutliche Warnung.

## Programmablauf

- **Werkzeugwechsel** immer mit `T… M6 G43`, nur bei neuer T-Nummer.
- **Wechselposition `G53`** aus `[CHANGE_POSITION]`, sonst `[EMCIO] TOOL_CHANGE_POSITION`, sonst
  Reiter Maschine. Angefahren wird im Radiusmodus G8, damit X dem INI-Wert entspricht; erst X,
  dann Z.
- **Zwischen den Zyklen** auf eine Rückzugsebene vor dem Werkstück:
  - außen: erst X nach außen
  - innen: erst X zur Mitte, dann Z heraus
- Programm in G7, G18, mm. G76 und Konturbögen stehen in G8, Bogenmittelpunkte relativ (G91.1).

## G-Code bearbeiten

- Die Taste **G-Code** zeigt den erzeugten Code in einem Editor. Dort ergänzen, speichern oder direkt
  in LinuxCNC laden (nur möglich, wenn LinuxCNC läuft).
- Dauerhafter Code zwischen zwei Zyklen gehört in einen Schritt **G-Code**. Einen Textbaustein
  auswählen ersetzt den Inhalt des Codefelds, **Einfügen** fügt ihn zusätzlich an der Schreibmarke
  ein, **Leeren** macht das Feld frei für einen neuen Baustein. Die Bausteine („Späne entfernen“,
  „Nachmessen“, eigene über **Speichern …**) stehen in `settings.json`.
- „Spindel/Kühlung danach wieder einschalten“: Folgt ein Zyklus mit demselben Werkzeug, werden
  Drehzahl mit M3/M4 und M8 direkt nach dem Baustein wieder ausgegeben; bei einem Werkzeugwechsel
  schaltet der nächste Zyklus sie ein.
- Der **Programmordner** steht im Reiter Maschine (Standard aus `PROGRAM_PREFIX`). Ein Ordner, den
  es nicht gibt, wird nicht übernommen (auf Wunsch wird er angelegt). Danach merkt sich das
  Programm den zuletzt benutzten Ordner.
- Die **Schnittdaten** (Werkzeug, Drehzahl, Vorschübe, Zustellung, Aufmaße …) merkt sich jeder
  Zyklus getrennt für außen und innen, auch über einen Neustart hinweg (`settings.json`).
  „Werkzeug trotz Warnung freigeben“ wird bewusst nicht gemerkt.

## Installation

Kopiere den Ordner z.B. nach `~/linuxcnc/drehzyklen`. Du brauchst Python 3 und `python3-gi`
(GTK 3). Einstellungen liegen in `~/.config/drehzyklen`.

### In gmoccapy einbetten
Ergänze in der INI im Abschnitt `[DISPLAY]`:
```
EMBED_TAB_NAME = Zyklen
EMBED_TAB_LOCATION = ntb_user_tabs
EMBED_TAB_COMMAND = python3 /home/BENUTZER/linuxcnc/drehzyklen/start_drehzyklen.py --xid {XID}
```
Optional die Wechselposition für gmoccapy und die Zyklen:
```
[CHANGE_POSITION]
X = 60
Y = 0
Z = 250
```

### Eigenständig / ohne Oberfläche
```
python3 start_drehzyklen.py --ini ~/linuxcnc/configs/meine_drehe/meine_drehe.ini
python3 start_drehzyklen.py --gcode beispiel.dzp > teil.ngc
```

## Wichtig vor dem ersten Span

- Prüfe neue Programme in der Simulation (`configs/sim/gmoccapy/lathe_configs`) oder im Luftschnitt.
- G76 braucht einen Spindel-Encoder.
- Prüfe die Werkzeugzuordnung an einem bekannten Werkzeug (Grafik im Reiter Werkzeuge).

## Aufbau

```
start_drehzyklen.py      Start (Fenster, gmoccapy-Plug, Kommandozeile)
drehzyklen/geometry.py   Konturtext, Bögen, Ecken, Schruppebenen
drehzyklen/cycles.py     Zyklen, Grenzwerte, Werkzeugprüfung, Programmerzeugung
drehzyklen/gcode.py      G-Code-Ausgabe, Spiegelung ins Arbeitssystem
drehzyklen/material.py   Abtragssimulation
drehzyklen/tools.py      Werkzeugtabelle (tool.tbl / Vorgaben)
drehzyklen/machine.py    Maschinendaten (machine.json + INI)
drehzyklen/settings.py   letzter Ordner, Textbausteine, Schnittdaten
drehzyklen/render.py     Vorschau mit Zoom
drehzyklen/helpdraw.py   Parameterbilder und Symbole
drehzyklen/tooldraw.py   Werkzeuggrafik
drehzyklen/widgets.py    GTK-Bausteine
drehzyklen/gui.py        Oberfläche
tests/test_core.py       Tests (python3 tests/test_core.py)
```

## Lizenz

Copyright (C) 2026 nieson@web.de

Dieses Programm ist freie Software: Du kannst es unter den Bedingungen der GNU General Public
License, Version 2 oder (nach deiner Wahl) jeder späteren Version, weitergeben und/oder verändern
– wie LinuxCNC und gmoccapy. Es wird OHNE JEDE GEWÄHRLEISTUNG bereitgestellt. Der vollständige Text
steht in der Datei `LICENSE`.
