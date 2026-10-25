# Drehzyklen – Zyklenprogrammierung für LinuxCNC-Drehmaschinen (gmoccapy)
# Copyright (C) 2026  nieson@web.de
#
# This program is free software; you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program;
# if not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Werkzeugtabelle für Drehwerkzeuge.

Quellen:
  - bei LinuxCNC-Konfiguration: tool.tbl aus [EMCIO] TOOL_TABLE der INI
  - sonst (Stand Alone): werkzeuge.json, beim ersten Start mit ISO-Platten C, D, T, V
    (jeweils links und rechts), Einstech-, Gewindewerkzeug und Bohrer vorbelegt

Geometrisches Modell
--------------------
Jedes Werkzeug wird im Modellsystem beschrieben: Z nach rechts, X (Radius) nach oben.
Ein Außenwerkzeug sitzt oberhalb der Schneide, seine Schneidplatte füllt einen Winkelbereich
(Sektor) zwischen Haupt- und Nebenschneide, gemessen ab +Z gegen den Uhrzeigersinn.
Beispiel rechter Drehmeißel ISO C 95°: Hauptschneide 85°, Nebenschneide 5° -> Sektor 5°…85°.
Ein Innenwerkzeug liegt gespiegelt unterhalb der Schneide.

LinuxCNC beschreibt Frontwinkel I und Rückwinkel J "im Uhrzeigersinn ab einer Parallelen zu Z+"
in der LinuxCNC-Ansicht. Bei Werkzeug vor der Drehmitte zeigt X+ dort nach unten, daher
entspricht der Winkel dem Modellwinkel; bei Werkzeug hinter der Drehmitte wird gespiegelt.
Ist die Ableitung nicht möglich (I = J = 0, Q = 0 oder 9), muss der Bediener Typ, Hand und Lage
im Reiter Werkzeuge angeben. Die Angabe wird mit einem Fingerabdruck der Tabellenzeile
gespeichert und neu abgefragt, sobald sich das Werkzeug in der tool.tbl ändert.
"""
from __future__ import annotations

import json
import math
import os
from operator import attrgetter
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional, Tuple

KIND_LABELS = {
    "C": "ISO C 80°",
    "D": "ISO D 55°",
    "T": "ISO T 60°",
    "V": "ISO V 35°",
    "W": "ISO W 80°",
    "R": "Rundplatte",
    "groove": "Einstechwerkzeug",
    "thread": "Gewindewerkzeug",
    "drill": "Bohrer",
    "special": "Sonderform",
    "unknown": "unbekannt",
}
INSERT_ANGLES = {"C": 80.0, "D": 55.0, "T": 60.0, "V": 35.0, "W": 80.0, "thread": 60.0,
                 "groove": 6.0, "R": 90.0}
HANDS = ("rechts", "links", "neutral")
POSITIONS = ("außen", "innen", "Mitte")

HAND_TEXT = {"rechts": "rechts (schneidet nach −Z)", "links": "links (schneidet nach +Z)",
             "neutral": "neutral"}

ANGLE_TOLERANCE = 0.5
NEW_SOURCE = "neu"                    # im Reiter Werkzeuge angelegt, noch nicht in der tool.tbl

# LinuxCNC-Orientierung Q: Richtung der Werkzeugmittellinie (Schaft), im Uhrzeigersinn ab Z+
# wie in der LinuxCNC-Doku „Lathe Tool Orientation“ (Q9 = Mitte, kein Schaft)
ORIENTATION_ANGLES = {7: 0.0, 2: 45.0, 6: 90.0, 1: 135.0, 5: 180.0, 4: 225.0, 8: 270.0,
                      3: 315.0}
STANDARD_ORIENTATION = {("rechts", "außen"): 2, ("links", "außen"): 1, ("rechts", "innen"): 3,
                        ("links", "innen"): 4, ("neutral", "außen"): 6, ("neutral", "innen"): 8}


def normalize_angle(angle: float) -> float:
    """Winkel auf den Bereich (−180°, 180°] bringen."""
    while angle <= -180.0:
        angle += 360.0
    while angle > 180.0:
        angle -= 360.0
    return angle


def sector_contains(sector: Tuple[float, float], angle: float, tolerance: float = 0.0) -> bool:
    low, high = sector
    width = high - low
    offset = (angle - low + tolerance) % 360.0
    return offset < width + 2 * tolerance


def sector_inside(sector: Tuple[float, float], low: float, high: float,
                  tolerance: float = ANGLE_TOLERANCE) -> bool:
    """Liegt der Werkzeugsektor vollständig im Freiraum low…high (Grad, gegen Uhrzeigersinn)?"""
    space = (high - low) % 360.0
    if space == 0:
        space = 360.0
    start = (sector[0] - low) % 360.0
    end = start + (sector[1] - sector[0])
    return start >= tolerance and end <= space - tolerance


@dataclass
class Tool:
    number: int
    pocket: int = 0
    kind: str = "C"
    hand: str = "rechts"
    position: str = "außen"
    nose_radius: float = 0.4
    lead_angle: float = 95.0          # Einstellwinkel κ
    width: float = 0.0                # Einstechbreite
    diameter: float = 0.0             # Bohrer-Ø
    comment: str = ""
    source: str = "Vorgabe"           # Vorgabe | tool.tbl | Benutzer
    front_angle: float = 0.0          # I aus tool.tbl
    back_angle: float = 0.0           # J aus tool.tbl
    orientation: int = 0              # Q aus tool.tbl
    table_sector: Optional[Tuple[float, float]] = None
    needs_input: bool = False
    fingerprint: str = ""
    tip_angle: float = 0.0            # Spitzenwinkel Gewindewerkzeug (0 = 60°)
    shank_angle: Optional[float] = None  # Schaftrichtung im Modell aus Q der tool.tbl

    @property
    def angles_from_table(self) -> bool:
        """Lage der Schneide kommt aus I/J/Q der tool.tbl (nicht vom Bediener)."""
        return self.table_sector is not None

    # ---------------------------------------------------------------- Beschreibung
    @property
    def insert_angle(self) -> float:
        if self.table_sector is not None:
            return self.table_sector[1] - self.table_sector[0]
        if self.kind == "thread" and self.tip_angle > 0:
            return self.tip_angle
        return INSERT_ANGLES.get(self.kind, 80.0)

    def kind_text(self) -> str:
        if self.kind == "thread":
            return f"Gewindewerkzeug {self.insert_angle:g}°"
        return KIND_LABELS.get(self.kind, self.kind)

    def label(self) -> str:
        text = f"T{self.number}  {self.kind_text()}"
        if self.kind not in ("drill",):
            text += f" {self.hand} {self.position}"
        if self.kind == "drill":
            text += f" Ø{self.diameter:g}"
        elif self.kind == "groove":
            text += f" b{self.width:g}"
        else:
            text += f" r{self.nose_radius:g}"
        if self.needs_input:
            text += "  ⚠ Angabe fehlt"
        if self.source == NEW_SOURCE:
            text += "  (noch nicht in tool.tbl)"
        return text

    # ---------------------------------------------------------------- Geometrie
    def sector(self) -> Optional[Tuple[float, float]]:
        """Winkelbereich der Schneidplatte im Modellsystem (Grad), None bei Bohrern."""
        if self.kind == "drill" or self.needs_input:
            return None
        if self.table_sector is not None:
            return self.table_sector
        epsilon = self.insert_angle
        if self.kind == "thread":
            low, high = 90.0 - epsilon / 2, 90.0 + epsilon / 2
        elif self.kind == "groove":
            low, high = 87.0, 93.0
        elif self.kind == "R":
            low, high = 45.0, 135.0
        else:
            main_edge = 180.0 - self.lead_angle
            low, high = main_edge - epsilon, main_edge
            if self.hand == "links":
                low, high = 180.0 - high, 180.0 - low
        if self.position == "innen":
            low, high = -high, -low
        return low, high

    def shank_direction(self) -> Optional[float]:
        """Richtung des Halters im Modellsystem (Grad): aus Q, sonst Winkelhalbierende."""
        if self.shank_angle is not None:
            return self.shank_angle
        sector = self.sector()
        if sector is None:
            return None
        return (sector[0] + sector[1]) / 2

    def sector_for(self, inside: bool, z_mirrored: bool) -> Optional[Tuple[float, float]]:
        base = self.sector()
        if base is None:
            return None
        low, high = base
        if inside:
            low, high = -high, -low
        if z_mirrored:
            low, high = 180.0 - high, 180.0 - low
        shift = normalize_angle(low) - low
        return low + shift, high + shift

    # ---------------------------------------------------------------- Speichern
    def to_dict(self) -> dict:
        values = asdict(self)
        if self.table_sector:
            values["table_sector"] = list(self.table_sector)
        else:
            values["table_sector"] = None
        return values

    @classmethod
    def from_dict(cls, values: dict) -> "Tool":
        names = {field.name for field in fields(cls)}
        clean = {key: value for key, value in values.items() if key in names}
        if clean.get("table_sector"):
            clean["table_sector"] = tuple(clean["table_sector"])
        return cls(**clean)


def preset_tools() -> List[Tool]:
    """Vorbelegung für den Stand-Alone-Betrieb."""
    tools = []
    number = 1
    for kind, lead_angle, radius, name in (("C", 95.0, 0.8, "CNMG 120408"),
                                           ("D", 93.0, 0.4, "DNMG 150604"),
                                           ("T", 91.0, 0.4, "TNMG 160404"),
                                           ("V", 93.0, 0.4, "VBMT 160404")):
        for hand in ("rechts", "links"):
            tools.append(Tool(number, number, kind, hand, "außen", radius, lead_angle,
                              comment=f"{name} {hand}"))
            number += 1
    tools.append(Tool(number, number, "groove", "neutral", "außen", 0.2, 90.0, width=3.0,
                      comment="Einstechwerkzeug 3 mm"))
    number += 1
    tools.append(Tool(number, number, "thread", "neutral", "außen", 0.1, 90.0,
                      comment="Gewindewerkzeug 60° außen"))
    number += 1
    tools.append(Tool(number, number, "drill", "neutral", "Mitte", 0.0, 118.0, diameter=10.0,
                      comment="Bohrer Ø10"))
    number += 1
    tools.append(Tool(number, number, "D", "rechts", "innen", 0.4, 93.0,
                      comment="Bohrstange DNMG rechts"))
    return tools


# =========================================================================== tool.tbl

def parse_tool_line(line: str) -> Optional[dict]:
    text, _separator, comment = line.partition(";")
    words = {}
    for token in text.split():
        letter = token[0].upper()
        try:
            words[letter] = float(token[1:])
        except ValueError:
            continue
    if "T" not in words:
        return None
    return {"number": int(words["T"]), "pocket": int(words.get("P", 0)),
            "diameter": abs(words.get("D", 0.0)), "front": words.get("I", 0.0),
            "back": words.get("J", 0.0), "orientation": int(words.get("Q", 0)),
            "comment": comment.strip()}


def model_angle(table_angle: float, back_tool: bool, mirror_z: bool = False) -> float:
    """Winkel der tool.tbl (im Uhrzeigersinn ab Z+, LinuxCNC-Ansicht) -> Modellwinkel."""
    if back_tool:
        angle = -table_angle
    else:
        angle = table_angle
    if mirror_z:
        angle = 180.0 - angle
    return angle


def table_angles(tool: Tool, back_tool: bool, mirror_z: bool = False):
    """Frontwinkel I, Rückwinkel J und Orientierung Q für die tool.tbl.

    Die Winkel werden möglichst positiv (0…360°) geschrieben. Überstreicht die Platte die
    Richtung Z+ (0°), wird der kleinere Winkel negativ (wie Q7 in der LinuxCNC-Doku), denn
    LinuxCNC zeichnet die Platte vom kleineren zum größeren Winkel.
    """
    sector = tool.sector()
    if sector is None:
        return None
    # Modell -> Tabelle ist dieselbe (selbstinverse) Abbildung wie Tabelle -> Modell
    first = model_angle(sector[0], back_tool, mirror_z)
    second = model_angle(sector[1], back_tool, mirror_z)
    low, high = min(first, second), max(first, second)
    while low < 0:
        low += 360.0
        high += 360.0
    while low >= 360.0:
        low -= 360.0
        high -= 360.0
    if high > 360.0:
        low -= 360.0
        high -= 360.0
    if tool.orientation in ORIENTATION_ANGLES and tool.angles_from_table:
        orientation = tool.orientation
    else:
        # Lage des Bezugspunkts wie in der LinuxCNC-Doku: rechts außen 2, links außen 1,
        # rechts innen 3, links innen 4, neutral außen 6, neutral innen 8
        orientation = STANDARD_ORIENTATION.get((tool.hand, tool.position), 6)
    return round(low, 3), round(high, 3), orientation


def write_table_line(path: str, tool: Tool, front: float, back: float, orientation: int):
    """Werkzeug in der tool.tbl eintragen oder die vorhandene Zeile ändern.

    Geändert werden nur D, I, J, Q und der Kommentar – X, Z und alle übrigen Werte bleiben.
    """
    with open(path, encoding="utf-8", errors="replace") as file:
        lines = file.read().splitlines()
    words = {"D": format_table_number(2 * tool.nose_radius), "I": format_table_number(front),
             "J": format_table_number(back), "Q": str(int(orientation))}
    comment = tool.comment.strip() or tool.kind_text()
    for index, line in enumerate(lines):
        entry = parse_tool_line(line)
        if entry is None or entry["number"] != tool.number:
            continue
        text = line.partition(";")[0]
        tokens = text.split()
        present = set()
        for token_index, token in enumerate(tokens):
            letter = token[0].upper()
            if letter in words:
                tokens[token_index] = letter + words[letter]
                present.add(letter)
        for letter in ("D", "I", "J", "Q"):
            if letter not in present:
                tokens.append(letter + words[letter])
        lines[index] = " ".join(tokens) + " ;" + comment
        break
    else:
        pocket = tool.pocket or tool.number
        lines.append(f"T{tool.number} P{pocket} X0 Z0 D{words['D']} I{words['I']} "
                     f"J{words['J']} Q{words['Q']} ;{comment}")
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def reload_linuxcnc_tool_table() -> str:
    """Laufendes LinuxCNC die geänderte tool.tbl neu laden lassen."""
    try:
        import linuxcnc
    except ImportError:
        return ""
    try:
        status = linuxcnc.stat()
        status.poll()
        linuxcnc.command().load_tool_table()
    except Exception as error:  # noqa: BLE001 – LinuxCNC läuft nicht oder lehnt ab
        return f"tool.tbl geschrieben, LinuxCNC hat sie nicht neu geladen ({error})"
    return ""


def format_table_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text


def tool_from_table(entry: dict, back_tool: bool, mirror_z: bool = False) -> Tool:
    """Werkzeug aus einer tool.tbl-Zeile ableiten."""
    tool = Tool(entry["number"], entry["pocket"], "unknown", "neutral", "außen",
                entry["diameter"] / 2, 90.0, comment=entry["comment"], source="tool.tbl",
                front_angle=entry["front"], back_angle=entry["back"],
                orientation=entry["orientation"])
    orientation = entry["orientation"]
    if orientation in ORIENTATION_ANGLES:
        shank = model_angle(ORIENTATION_ANGLES[orientation], back_tool, mirror_z)
        tool.shank_angle = normalize_angle(shank)
    tool.fingerprint = (f"T{entry['number']} D{entry['diameter']:g} I{entry['front']:g} "
                        f"J{entry['back']:g} Q{entry['orientation']} {entry['comment']}")
    front, back = entry["front"], entry["back"]
    if entry["orientation"] in (0, 9) or (front == 0 and back == 0) or front == back:
        tool.needs_input = True
        return tool
    if back_tool:
        edge_one, edge_two = -front, -back
    else:
        edge_one, edge_two = front, back
    if mirror_z:
        # Bedieneroption, falls die Winkel der eigenen tool.tbl andersherum gemeint sind
        edge_one, edge_two = 180.0 - edge_one, 180.0 - edge_two
    difference = (edge_two - edge_one) % 360.0
    if difference <= 180.0:
        low = edge_one
        width = difference
    else:
        low = edge_two
        width = 360.0 - difference
    low = normalize_angle(low)
    tool.table_sector = (low, low + width)
    bisector = math.radians(low + width / 2)
    if math.sin(bisector) >= 0:
        tool.position = "außen"
        bisector_outside = bisector
    else:
        tool.position = "innen"
        bisector_outside = -bisector
    if math.cos(bisector_outside) > 0.17:
        tool.hand = "rechts"
    elif math.cos(bisector_outside) < -0.17:
        tool.hand = "links"
    else:
        tool.hand = "neutral"
    tool.kind = "special"
    if tool.hand == "neutral" and (abs(width - 60.0) < 3.0 or abs(width - 55.0) < 3.0):
        # symmetrische Spitze mit 55° (Whitworth) oder 60° (metrisch, UNC/UNF)
        tool.kind = "thread"
        tool.tip_angle = round(width, 1)
    elif tool.hand == "neutral" and width < 12.0:
        tool.kind = "groove"
    else:
        # nächstliegender Plattenwinkel (60° ist T, nicht D mit 55°)
        best_difference = 6.0
        for kind in ("C", "D", "T", "V"):
            difference = abs(width - INSERT_ANGLES[kind])
            if difference < best_difference:
                best_difference = difference
                tool.kind = kind
    if tool.position == "außen":
        outside_low, outside_high = tool.table_sector
    else:
        outside_low, outside_high = -tool.table_sector[1], -tool.table_sector[0]
    if tool.hand == "links":
        tool.lead_angle = round(outside_low, 1)
    else:
        tool.lead_angle = round(180.0 - outside_high, 1)
    return tool


class ToolTable:
    """Alle Werkzeuge, mit Speicherung je nach Herkunft."""

    def __init__(self, machine, data_path: str):
        self.machine = machine
        self.data_path = data_path
        self.tools: Dict[int, Tool] = {}
        self.from_linuxcnc = False
        self.mirror_table_angles = bool(self._stored().get("mirror_table_angles", False))
        self.message = ""
        self.loaded_stamp = None
        self.load()

    # ---------------------------------------------------------------- Laden
    def table_path(self) -> str:
        return getattr(self.machine, "tool_table", "")

    def table_stamp(self):
        """Änderungszeit und Größe der tool.tbl (None ohne LinuxCNC-Tabelle)."""
        table_path = self.table_path()
        if not table_path:
            return None
        try:
            info = os.stat(table_path)
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def changed_on_disk(self) -> bool:
        """Wurde die tool.tbl seit dem Einlesen verändert (z.B. Werkzeug in LinuxCNC angelegt)?"""
        return self.table_stamp() != self.loaded_stamp

    def load(self):
        self.tools = {}
        table_path = self.table_path()
        self.loaded_stamp = self.table_stamp()
        if table_path and os.path.isfile(table_path):
            self.from_linuxcnc = True
            self._load_tbl(table_path)
        else:
            self.from_linuxcnc = False
            self._load_json()

    def _stored(self) -> dict:
        if not os.path.isfile(self.data_path):
            return {}
        try:
            with open(self.data_path, encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    def _load_json(self):
        data = self._stored()
        stored_tools = data.get("tools")
        if stored_tools:
            for values in stored_tools:
                try:
                    tool = Tool.from_dict(values)
                except TypeError:
                    continue
                self.tools[tool.number] = tool
            self.message = f"Werkzeuge aus {os.path.basename(self.data_path)}"
        else:
            for tool in preset_tools():
                self.tools[tool.number] = tool
            self.message = ("Vorbelegte Werkzeuge (ISO C, D, T, V links/rechts, Einstich, Gewinde, "
                            "Bohrer, Bohrstange)")

    def _load_tbl(self, path: str):
        assignments = self._stored().get("assignments", {})
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
        except OSError as error:
            self.message = f"tool.tbl nicht lesbar: {error}"
            return
        for line in lines:
            entry = parse_tool_line(line)
            if entry is None:
                continue
            tool = tool_from_table(entry, self.machine.back_tool, self.mirror_table_angles)
            stored = assignments.get(str(tool.number))
            if stored and stored.get("fingerprint") == tool.fingerprint:
                self._apply_assignment(tool, stored)
            self.tools[tool.number] = tool
        pending = [tool for tool in self.tools.values() if tool.needs_input]
        self.message = f"{len(self.tools)} Werkzeuge aus {path}"
        if pending:
            numbers = ", ".join(f"T{tool.number}" for tool in pending)
            self.message += f" – Angabe fehlt bei {numbers}"

    @staticmethod
    def _apply_assignment(tool: Tool, stored: dict):
        tool.kind = stored.get("kind", tool.kind)
        tool.width = float(stored.get("width", tool.width))
        tool.diameter = float(stored.get("diameter", tool.diameter))
        tool.tip_angle = float(stored.get("tip_angle", tool.tip_angle))
        if not tool.angles_from_table:
            tool.hand = stored.get("hand", tool.hand)
            tool.position = stored.get("position", tool.position)
            tool.lead_angle = float(stored.get("lead_angle", tool.lead_angle))
        tool.needs_input = False
        tool.source = "tool.tbl + Angabe"

    # ---------------------------------------------------------------- Speichern
    def save(self):
        data = self._stored()
        data["mirror_table_angles"] = self.mirror_table_angles
        if self.from_linuxcnc:
            assignments = data.get("assignments", {})
            for tool in self.tools.values():
                if tool.source == "tool.tbl + Angabe":
                    assignments[str(tool.number)] = {
                        "fingerprint": tool.fingerprint, "kind": tool.kind, "hand": tool.hand,
                        "position": tool.position, "lead_angle": tool.lead_angle,
                        "width": tool.width, "diameter": tool.diameter,
                        "tip_angle": tool.tip_angle}
            data["assignments"] = assignments
        else:
            data["tools"] = [tool.to_dict() for tool in sorted(self.tools.values(),
                                                                key=attrgetter("number"))]
        try:
            os.makedirs(os.path.dirname(self.data_path) or ".", exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
        except OSError as error:
            self.message = f"Werkzeuge nicht gespeichert: {error}"

    # ---------------------------------------------------------------- Zugriff
    def get(self, number) -> Optional[Tool]:
        try:
            return self.tools.get(int(number))
        except (TypeError, ValueError):
            return None

    def sorted_tools(self) -> List[Tool]:
        return sorted(self.tools.values(), key=attrgetter("number"))

    def pending(self) -> List[Tool]:
        return [tool for tool in self.sorted_tools() if tool.needs_input]

    def assign(self, tool: Tool, kind: str, hand: str, position: str, lead_angle: float,
               nose_radius: float, width: float, diameter: float, comment: str,
               tip_angle: float = 0.0):
        """Angaben des Bedieners übernehmen.

        Bei tool.tbl nur Typ, Hand, Lage und Winkel. Liefert die Tabelle die Schneidenlage
        (I/J/Q), bleibt diese erhalten – geändert wird dann nur die Einordnung (Typ), z.B.
        eine 55°-Spitze als Gewindewerkzeug statt als ISO D.
        """
        tool.kind = kind
        if not tool.angles_from_table:
            tool.hand = hand
            tool.position = position
            tool.lead_angle = lead_angle
            tool.tip_angle = tip_angle
        tool.width = width
        tool.diameter = diameter
        tool.needs_input = False
        if self.from_linuxcnc and tool.source != NEW_SOURCE:
            tool.source = "tool.tbl + Angabe"
        else:
            tool.nose_radius = nose_radius
            tool.comment = comment
            if not self.from_linuxcnc:
                tool.source = "Benutzer"
        self.save()

    def write_to_table(self, tool: Tool) -> str:
        """Werkzeug in die tool.tbl schreiben, LinuxCNC benachrichtigen, Tabelle neu lesen.

        Gibt eine Meldung zurück (leer = alles in Ordnung).
        """
        table_path = self.table_path()
        if not table_path or not os.path.isfile(table_path):
            return "Keine tool.tbl aus der LinuxCNC-INI bekannt"
        angles = table_angles(tool, self.machine.back_tool, self.mirror_table_angles)
        if angles is None:
            return f"T{tool.number}: Typ, Hand und Lage fehlen – nichts geschrieben"
        front, back, orientation = angles
        kind, width, diameter, tip_angle = tool.kind, tool.width, tool.diameter, tool.tip_angle
        try:
            write_table_line(table_path, tool, front, back, orientation)
        except OSError as error:
            return f"tool.tbl nicht schreibbar: {error}"
        notice = reload_linuxcnc_tool_table()
        self.load()
        written = self.tools.get(tool.number)
        if written is not None and (written.kind != kind or kind in ("groove", "thread")):
            # Einordnung (z.B. Einstechbreite) behalten, die Winkel stehen jetzt in der Tabelle
            written.kind = kind
            written.width = width
            written.diameter = diameter
            written.tip_angle = tip_angle
            written.needs_input = False
            written.source = "tool.tbl + Angabe"
            self.save()
        return notice

    def add(self, tool: Tool):
        self.tools[tool.number] = tool
        self.save()

    def remove(self, number: int):
        if number in self.tools:
            del self.tools[number]
            self.save()

    def set_mirror(self, mirror: bool):
        """Winkel der tool.tbl in Z spiegeln und Tabelle neu einlesen."""
        self.mirror_table_angles = mirror
        self.save()
        self.load()

    def next_number(self) -> int:
        if not self.tools:
            return 1
        return max(self.tools) + 1
