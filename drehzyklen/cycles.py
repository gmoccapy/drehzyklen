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
"""Drehzyklen und Programmerzeugung für LinuxCNC (Drehmaschine, G18, Durchmesserprogrammierung G7).

Einheitliche Bezeichnungen in allen Zyklen:
    D  = Durchmesser Start      d  = Durchmesser Ende
    Z1 = Z Start                Z2 = Z Ende
Die Werte bestimmen die Bearbeitungsrichtung: Z1 > Z2 arbeitet nach −Z (von rechts nach links),
Z1 < Z2 nach +Z (von links nach rechts, z.B. Rückseiten mit linkem Werkzeug).

Jeder Drehzyklus wird intern in ein "Arbeitssystem" gespiegelt: Bearbeitung in −Z, Material
oberhalb der Kontur. Innenbearbeitung spiegelt an der Drehachse, Bearbeitung nach +Z an Z = 0.
Die Werkzeugbahn wird dort berechnet und beim Ausgeben zurückgespiegelt (TransformedEmitter).

Rohteildurchmesser werden nicht mehr in den Zyklen abgefragt: Konus, Fase und Kontur schruppen
ab dem tatsächlich vorhandenen Material (Abtragssimulation, Reiter Rohteil).
"""
from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .gcode import Emitter, Segment, TransformedEmitter, fmt
from .geometry import (EPS, ContourError, Element, Row, clip_at_radius, contour_warnings,
                       intervals_at, parse_rows, polyline, resolve_rows, segment_between,
                       walk_back, walk_up)
from .machine import Machine
from .material import Material, bounds
from .threads import NORMS, SIZE_KEYS, ThreadSpec, find, names_for, norm_of_label
from .tools import KIND_LABELS, normalize_angle, sector_inside


# =========================================================================== Parameter

@dataclass
class P:
    key: str
    label: str
    default: Any
    kind: str = "float"           # float | optfloat | int | choice | bool | text | contour | side | tool
    choices: Tuple[str, ...] = ()
    unit: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""
    sym: str = ""                 # Kurzzeichen im Parameterbild
    label_in: Optional[str] = None  # abweichende Beschriftung bei Innenbearbeitung
    show: Optional[str] = None    # "out"/"in": nur bei Außen- bzw. Innenbearbeitung zeigen
    skey: bool = False            # Vorbelegung mit dem Rohteil-Ø (außen D, innen d)
    when: Optional[Tuple[str, Tuple[str, ...]]] = None  # nur zeigen, wenn Feld = einer der Werte
    label_by: Optional[Tuple[str, Dict[str, str]]] = None  # Beschriftung abhängig von einem Feld
    unit_by: Optional[Tuple[str, Dict[str, str]]] = None   # Einheit abhängig von einem Feld


SIDE = P("side", "Bearbeitung", "Außen", "side", ("Außen", "Innen"))

SPEED_FEED_MODES = ("G96 Vc · G95 mm/U", "G97 n · G95 mm/U",
                    "G96 Vc · G94 mm/min", "G97 n · G94 mm/min")


def spindle_mode_of(mode_text: str) -> str:
    if str(mode_text).startswith("G97"):
        return "G97"
    return "G96"


def feed_mode_of(mode_text: str) -> str:
    if "G94" in str(mode_text):
        return "G94"
    return "G95"


SPEED_LABELS = {}
FEED_UNITS = {}
for _mode in SPEED_FEED_MODES:
    if spindle_mode_of(_mode) == "G96":
        SPEED_LABELS[_mode] = "Schnittgeschwindigkeit Vc [m/min]"
    else:
        SPEED_LABELS[_mode] = "Drehzahl n [1/min]"
    if feed_mode_of(_mode) == "G94":
        FEED_UNITS[_mode] = "mm/min"
    else:
        FEED_UNITS[_mode] = "mm/U"

TOOL_PARAMS = [
    P("tool", "Werkzeug", 1, "tool", min=0, max=9999, sym="T"),
    P("tool_ok", "Werkzeug trotz Warnung freigeben", False, "bool",
      help="Hebt die Sperre der Werkzeugprüfung auf (z.B. Rund- oder Sondergewinde)"),
]

SPINDLE_DIR = P("spindle_dir", "Drehrichtung", "auto", "choice", ("auto", "M3", "M4"),
                help="auto: Werkzeug vor der Drehmitte M3, hinter der Drehmitte M4 (Reiter Maschine)")
COOLANT = P("coolant", "Kühlmittel M8", True, "bool")

TECH = TOOL_PARAMS + [
    P("speed_feed", "Drehzahl/Vorschub", SPEED_FEED_MODES[0], "choice", SPEED_FEED_MODES,
      help="G96 konstante Schnittgeschwindigkeit, G97 feste Drehzahl; "
           "G95 Vorschub pro Umdrehung, G94 Vorschub pro Minute"),
    P("speed", "Drehzahl", 180.0, min=1, sym="S", label_by=("speed_feed", SPEED_LABELS),
      help="Wird mit den Maschinengrenzen verglichen und ggf. begrenzt"),
    SPINDLE_DIR,
    COOLANT,
]

TECH_THREAD = TOOL_PARAMS + [
    P("speed", "Drehzahl n", 600.0, unit="1/min", min=1, sym="S",
      help="Wird auf Maschinengrenzen und Z-Geschwindigkeit begrenzt"),
    SPINDLE_DIR,
    COOLANT,
]


def feed_param(key, label, default, sym):
    return P(key, label, default, min=0.001, sym=sym, unit_by=("speed_feed", FEED_UNITS))


ROUGH = [
    P("ap", "Zustellung (radial)", 1.5, unit="mm", min=0.01, sym="ap"),
    feed_param("f_rough", "Vorschub Schruppen", 0.25, "F"),
    feed_param("f_finish", "Vorschub Schlichten", 0.1, "Fs"),
    P("allow_x", "Schlichtaufmaß X (Ø)", 0.4, unit="mm", min=0, sym="aX"),
    P("allow_z", "Schlichtaufmaß Z", 0.1, unit="mm", min=0, sym="aZ"),
    P("do_rough", "Schruppen", True, "bool"),
    P("do_finish", "Schlichten", True, "bool"),
    P("comp", "Schneidenradiuskorrektur", "auto", "choice", ("aus", "auto", "G41", "G42"),
      help="Nur beim Schlichten; auto wählt die Seite nach Bearbeitungsrichtung. "
           "Werkzeugtabelle mit Radius und Orientierung nötig."),
]

# Werte, die beim nächsten Zyklus gleicher Art wieder vorgeschlagen werden. Nicht dabei:
# Geometrie und "Werkzeug trotz Warnung freigeben" (muss jedes Mal bewusst gesetzt werden).
CUTTING_DATA_KEYS = {"tool", "speed_feed", "speed", "spindle_dir", "coolant",
                     "ap", "allow_x", "allow_z", "do_rough", "do_finish", "comp",
                     "peck", "break_back", "dwell",
                     "first_cut", "degression", "infeed", "spring", "thread_clear"}

STOCK_PARAMS = [
    P("D", "Außen-Ø", 50.0, unit="mm", min=0.5, sym="D"),
    P("d", "Innen-Ø (0 = Vollmaterial)", 0.0, unit="mm", min=0, sym="d"),
    P("L", "Ausspannlänge (Futter bis Vorderkante)", 80.0, unit="mm", min=1, sym="L"),
    P("zero", "Werkstücknullpunkt Z0", "Vorderkante", "choice", ("Vorderkante", "Futter"),
      help="Vorderkante: Z0 an der fertigen Stirnfläche, Futter: Z0 an der Futterfläche"),
    P("face", "Planaufmaß vor Z0", 1.0, unit="mm", min=0, sym="a",
      when=("zero", ("Vorderkante",)),
      help="Rohteil steht um dieses Maß vor Z0 (wird mit Plandrehen abgetragen)"),
    P("chuck_clear", "Sicherheitsabstand zum Futter", 2.0, unit="mm", min=0, sym="s"),
]


class Stock:
    """Rohteil und Einspannung."""

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self.v = {}
        for param in STOCK_PARAMS:
            self.v[param.key] = param.default
        if values:
            for key, value in values.items():
                if key in self.v:
                    self.v[key] = value

    def __getattr__(self, item):
        values = self.__dict__.get("v")
        if values is not None and item in values:
            return values[item]
        raise AttributeError(item)

    @property
    def z_front(self) -> float:
        """Z der Rohteil-Vorderkante."""
        if self.zero == "Vorderkante":
            return self.face
        return self.L

    @property
    def z_chuck(self) -> float:
        """Z der Futterfläche (Backen)."""
        if self.zero == "Vorderkante":
            return self.face - self.L
        return 0.0

    def to_dict(self):
        return dict(self.v)

    def check(self) -> List[str]:
        if self.d >= self.D:
            raise ContourError("Rohteil: Innen-Ø muss kleiner als Außen-Ø sein")
        return []

    def summary(self) -> str:
        text = f"Rohteil Ø{fmt(self.D)}"
        if self.d > 0:
            text += f"/Ø{fmt(self.d)}"
        return text + f", Z{fmt(self.z_front)}…Futter Z{fmt(self.z_chuck)}"


# =========================================================================== Basisklasse

class Cycle:
    KEY = ""
    TITLE = ""
    SHORT = ""
    PARAMS: List[P] = []
    TECH_PARAMS = TECH
    HAS_SIDE = True
    HAS_TOOL = True
    FORCE_G97 = False
    RENAMES: Dict[str, str] = {}        # alte Schlüssel aus früheren Projekten -> neue

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self.v: Dict[str, Any] = {}
        for param in self.all_params():
            self.v[param.key] = param.default
        if values:
            values = dict(values)
            for old_key, new_key in self.RENAMES.items():
                if old_key in values and new_key not in values:
                    values[new_key] = values[old_key]
            if "speed_mode" in values and "speed_feed" not in values:
                if values["speed_mode"] == "G97":
                    values["speed_feed"] = SPEED_FEED_MODES[1]
                else:
                    values["speed_feed"] = SPEED_FEED_MODES[0]
            for key, value in values.items():
                if key in self.v:
                    self.v[key] = value

    @classmethod
    def all_params(cls) -> List[P]:
        params = []
        if cls.HAS_SIDE:
            params.append(SIDE)
        params.extend(cls.TECH_PARAMS)
        params.extend(cls.PARAMS)
        return params

    def __getattr__(self, item):
        values = self.__dict__.get("v")
        if values is not None and item in values:
            return values[item]
        raise AttributeError(item)

    @classmethod
    def cutting_keys(cls) -> List[str]:
        """Schnittdaten, die zwischen Zyklen/Sitzungen behalten werden (keine Geometrie)."""
        keys = []
        for param in cls.all_params():
            if param.key in CUTTING_DATA_KEYS:
                keys.append(param.key)
            elif param.unit_by is not None:      # Vorschübe
                keys.append(param.key)
        return keys

    # ---------------------------------------------------------------- Lage und Richtung
    @property
    def inside(self) -> bool:
        return self.v.get("side") == "Innen"

    @property
    def radius_sign(self) -> int:
        if self.inside:
            return -1
        return 1

    def z_sign(self) -> int:
        """+1: Bearbeitung nach −Z (Standard), −1: Bearbeitung nach +Z."""
        if self.v.get("z_end", 0.0) > self.v.get("z_start", 0.0):
            return -1
        return 1

    @property
    def side_text(self) -> str:
        if self.inside:
            return "innen"
        return "außen"

    def direction_text(self) -> str:
        if self.z_sign() < 0:
            return " →+Z"
        return ""

    def to_dict(self):
        return {"cycle": self.KEY, "values": dict(self.v)}

    def help_values(self) -> Dict[str, Any]:
        """Werte für das Parameterbild (inkl. berechneter Größen)."""
        return dict(self.v)

    @classmethod
    def reference_diameter(cls, values: Dict[str, Any]) -> float:
        for key in ("d_start", "diameter", "d_end"):
            value = values.get(key)
            if isinstance(value, (int, float)) and abs(value) > 1:
                return abs(value)
        return 50.0

    @classmethod
    def switch(cls, key: str, values: Dict[str, Any], old_value=None) -> Dict[str, Any]:
        """Bei Änderung eines Auswahlfeldes: abhängige Werte umrechnen."""
        updates = {}
        if key != "speed_feed" or old_value is None:
            return updates
        new_value = values.get("speed_feed")
        diameter = cls.reference_diameter(values)
        speed = values.get("speed")
        if not isinstance(speed, (int, float)) or speed <= 0:
            return updates
        old_spindle = spindle_mode_of(old_value)
        new_spindle = spindle_mode_of(new_value)
        if old_spindle == "G96":
            rpm = speed * 1000.0 / (math.pi * diameter)
        else:
            rpm = speed
        if old_spindle != new_spindle:
            if new_spindle == "G97":
                updates["speed"] = float(round(rpm))
            else:
                updates["speed"] = float(round(speed * math.pi * diameter / 1000.0))
        old_feed = feed_mode_of(old_value)
        new_feed = feed_mode_of(new_value)
        if old_feed != new_feed:
            for feed_key in ("f_rough", "f_finish", "f"):
                feed = values.get(feed_key)
                if not isinstance(feed, (int, float)) or feed <= 0:
                    continue
                if new_feed == "G94":
                    updates[feed_key] = float(round(feed * rpm))
                else:
                    updates[feed_key] = round(feed / max(rpm, 1.0), 3)
        return updates

    # ---------------------------------------------------------------- Technologie
    def spindle_mode(self) -> str:
        if self.FORCE_G97:
            return "G97"
        return spindle_mode_of(self.v.get("speed_feed", SPEED_FEED_MODES[0]))

    def feed_mode(self) -> str:
        return feed_mode_of(self.v.get("speed_feed", SPEED_FEED_MODES[0]))

    def spindle_direction(self, machine: Machine) -> str:
        direction = self.v.get("spindle_dir", "auto")
        if direction in ("M3", "M4"):
            return direction
        return machine.default_spindle_dir

    def feed_keys(self) -> List[str]:
        return [key for key in ("f_rough", "f_finish", "f") if key in self.v]

    def feeds(self) -> List[float]:
        return [self.v[key] for key in self.feed_keys()]

    def copy_with(self, updates: Dict[str, Any]) -> "Cycle":
        values = dict(self.v)
        values.update(updates)
        return type(self)(values)

    # ---------------------------------------------------------------- von Unterklassen
    def summary(self) -> str:
        return ""

    def max_d(self) -> float:
        return 0.0

    def z_range(self) -> Tuple[float, float]:
        z_start = self.v.get("z_start", 0.0)
        z_end = self.v.get("z_end", 0.0)
        return min(z_start, z_end), max(z_start, z_end)

    def outer_x(self, machine: Machine) -> float:
        return self.max_d() + 2 * machine.clearance

    def inner_x(self, machine: Machine) -> float:
        return 0.0

    def check(self, machine: Machine) -> List[str]:
        return []

    def outline(self) -> List[Element]:
        return []

    def material_cuts(self, material=None) -> list:
        return []

    def tool_problems(self, tool) -> List[str]:
        return []

    def body(self, emitter: Emitter, machine: Machine, material: Optional[Material]):
        raise NotImplementedError


def _tool_name(tool) -> str:
    return f"T{tool.number} ({tool.kind_text()} {tool.hand} {tool.position})"


def turning_tool_problems(tool, inside: bool, z_mirrored: bool, work_elements: List[Element],
                          roughing: bool) -> List[str]:
    """Prüft, ob ein Drehwerkzeug die Kontur im Arbeitssystem bearbeiten kann."""
    name = _tool_name(tool)
    if tool.kind == "thread":
        return [f"{name}: Gewindewerkzeug ist für Drehbearbeitung nicht geeignet"]
    if tool.kind == "drill":
        return [f"{name}: Bohrer ist für Drehbearbeitung nicht geeignet"]
    sector = tool.sector_for(inside, z_mirrored)
    if sector is None:
        return []
    if not sector_inside(sector, 0.0, 180.0):
        if inside:
            return [f"{name}: Werkzeuglage passt nicht – für Innenbearbeitung wird ein "
                    f"Innenwerkzeug gebraucht"]
        return [f"{name}: Werkzeuglage passt nicht – für Außenbearbeitung wird ein "
                f"Außenwerkzeug gebraucht"]
    problems = []
    if roughing and tool.kind != "R" and not sector_inside(sector, 0.0, 90.0):
        if z_mirrored:
            needed = "links (schneidet nach +Z)"
        else:
            needed = "rechts (schneidet nach −Z)"
        problems.append(f"{name}: Schnittrichtung passt nicht – hier wird ein Werkzeug "
                        f"{needed} gebraucht")
    if tool.kind == "R":
        return problems
    worst = None
    for element in work_elements:
        points = element.sample(10.0)
        for (z_start, r_start), (z_end, r_end) in zip(points, points[1:]):
            if math.hypot(z_end - z_start, r_end - r_start) < 1e-6:
                continue
            direction = math.degrees(math.atan2(r_end - r_start, z_end - z_start))
            if not sector_inside(sector, direction - 180.0, direction):
                falling = abs(normalize_angle(direction + 180.0))
                if worst is None or falling > worst:
                    worst = falling
    if worst is not None:
        problems.append(f"{name}: Freiwinkel reicht nicht für die Kontur (Abschnitt fällt um "
                        f"{worst:.0f}°) – Werkzeug mit spitzerer Platte (z.B. ISO V) oder "
                        f"passender Hand wählen")
    return problems


# =========================================================================== Konturdrehen (Kern)

def turn_contour(emitter: Emitter, work_elements: List[Element], radius_sign: int, z_sign: int,
                 start_radius: float, z_front: float, ap: float, allow_x: float, allow_z: float,
                 feed_rough: float, feed_finish: float, clearance: float, do_rough=True,
                 do_finish=True, compensation="aus"):
    """Längsschruppen in Ebenen mit Konturnachzug + Schlichtschnitt im Arbeitssystem.

    work_elements, start_radius und z_front sind bereits ins Arbeitssystem gespiegelt.
    """
    work = TransformedEmitter(emitter, radius_sign, z_sign)
    safe_radius = start_radius + clearance
    if radius_sign < 0 and safe_radius > 0:
        raise ContourError("Bohrung ist zu klein für den Sicherheitsabstand")
    work.rapid(x=2 * safe_radius)
    work.rapid(z=z_front + clearance)
    if do_rough:
        offset_points = []
        for z, r in polyline(work_elements):
            offset_points.append((z + allow_z, r + allow_x / 2.0))
        rough_levels(work, offset_points, start_radius, z_front, ap, feed_rough, clearance,
                     safe_radius)
    if do_finish:
        first = work_elements[0]
        work.comment("Schlichten")
        work.rapid(x=2 * safe_radius)
        work.rapid(z=max(first.z0, z_front) + clearance)
        work.rapid(x=2 * first.r0)
        has_arcs = any(element.kind == "arc" for element in work_elements)
        if has_arcs:
            work.comment("Kontur mit Bögen im Radiusmodus G8: X = Radius, I/K = Mittelpunkt relativ")
            work.set_radius_mode(True)
        compensation_code = None
        if compensation == "auto":
            if radius_sign * z_sign > 0:
                compensation_code = "G42"
            else:
                compensation_code = "G41"
        elif compensation in ("G41", "G42"):
            compensation_code = compensation
        if compensation_code:
            work.emit(compensation_code)
        work.feed(z=first.z0, f=feed_finish)
        for element in work_elements:
            if element.kind == "line":
                work.feed(x=2 * element.r1, z=element.z1, f=feed_finish)
            else:
                work.arc(2 * element.r1, element.z1, element.cz, element.cr, element.ccw,
                         feed_finish)
        work.feed(x=2 * safe_radius, f=feed_finish)
        if compensation_code:
            work.emit("G40")
        if has_arcs:
            work.set_radius_mode(False)
    work.rapid(x=2 * safe_radius)
    work.rapid(z=z_front + clearance)


def rough_levels(work: TransformedEmitter, points, start_radius, z_front, ap, feed, clearance,
                 safe_radius):
    """Schruppebenen im Arbeitssystem; fallende Konturen werden schräg angefahren."""
    lowest = min(r for _z, r in points)
    if start_radius <= lowest + 1e-6:
        return
    levels = []
    level = start_radius - ap
    while level > lowest + 1e-6:
        levels.append(level)
        level -= ap
    if levels and levels[-1] - lowest < 0.05:
        levels[-1] = lowest
    else:
        levels.append(lowest)

    work.comment(f"Schruppen: {len(levels)} Ebenen")
    previous_level = start_radius
    for level in levels:
        intervals = []
        for start, end in intervals_at(points, level):
            if end is not None:
                end_z = end.z
            else:
                end_z = points[-1][0]
            if end_z >= z_front - 1e-6:
                continue
            if start is not None and start.z >= z_front - 1e-6:
                start = None
            intervals.append((start, end))
        groups: List[list] = []
        for start, end in intervals:
            joinable = groups and groups[-1][-1][1] is not None and start is not None
            if joinable:
                gap = segment_between(points, groups[-1][-1][1], start)
                highest_in_gap = max(r for _z, r in gap)
                if highest_in_gap <= previous_level + EPS:
                    groups[-1].append((start, end))
                    continue
            groups.append([(start, end)])

        for group in groups:
            first_start = group[0][0]
            if first_start is None:
                work.rapid(z=z_front + clearance)
                work.rapid(x=2 * level)
            else:
                entry_path, _found = walk_back(points, first_start, previous_level, level)
                work.rapid(z=entry_path[0][0])
                work.rapid(x=2 * max(previous_level, entry_path[0][1]))
                work.follow(entry_path, feed)
            for index, (start, end) in enumerate(group):
                if index > 0:
                    work.follow(segment_between(points, group[index - 1][1], start)[1:], feed)
                if end is not None:
                    work.feed(z=end.z, f=feed)
                else:
                    work.feed(z=points[-1][0], f=feed)
            last_end = group[-1][1]
            if last_end is not None:
                work.follow(walk_up(points, last_end, previous_level), feed)
            work.rapid(x=2 * safe_radius)
        previous_level = level


class TurningCycle(Cycle):
    """Gemeinsamer Ablauf für Längs, Konus, Fase und Kontur."""

    def work_elements(self, material: Optional[Material] = None) -> List[Element]:
        raise NotImplementedError

    def outline(self) -> List[Element]:
        elements = []
        for element in self.work_elements():
            elements.append(element.transformed(self.radius_sign, self.z_sign()))
        return elements

    def material_cuts(self, material=None) -> list:
        points = polyline(self.outline(), 2.0)
        if self.inside:
            kind = "inside"
        else:
            kind = "outside"
        cuts = [(kind, points)]
        if material is not None:
            # Planaufmaß vor dem Konturanfang, das die Schruppschnitte mit abtragen
            elements = self.work_elements(material)
            start = self.start_radius_work(material, elements)
            front = self.front_work(elements)
            approach = self.approach_front(material, elements, start)
            if approach > front + 1e-6:
                lowest = None
                for element in elements:
                    for _z, radius in element.sample(10.0):
                        if lowest is None or radius < lowest:
                            lowest = radius
                sign = self.z_sign()
                real_radius = self.radius_sign * lowest
                cuts.append((kind, [(sign * front, real_radius), (sign * approach, real_radius)]))
        return cuts

    def z_range(self):
        z_values = []
        for element in self.outline():
            z_values.append(element.z0)
            z_values.append(element.z1)
        return min(z_values), max(z_values)

    def max_d(self):
        radii = []
        for element in self.outline():
            for _z, r in element.sample(10.0):
                radii.append(abs(r))
        return 2 * max(radii)

    def inner_x(self, machine):
        radii = []
        for element in self.outline():
            for _z, r in element.sample(10.0):
                radii.append(abs(r))
        return max(0.0, 2 * min(radii) - 2 * machine.clearance)

    def start_radius_work(self, material: Optional[Material], elements: List[Element]) -> float:
        """Startradius im Arbeitssystem: vorhandenes Material über dem Konturbereich."""
        real_low, real_high = self.z_range()
        work_radii = []
        for element in elements:
            for _z, radius in element.sample(10.0):
                work_radii.append(radius)
        highest_contour = max(work_radii)
        if material is None:
            return highest_contour
        if self.inside:
            bore = material.min_inner(real_low, real_high)
            if bore is None:
                return highest_contour
            if bore < 1.0:
                raise ContourError(f"Im Bereich Z{real_low:g}…{real_high:g} fehlt eine Vorbohrung – "
                                   f"zuerst bohren (Reiter Bohren) oder Innen-Ø im Rohteil angeben")
            return max(-bore, highest_contour)
        outer = material.max_outer(real_low, real_high)
        if outer is None:
            return highest_contour
        return max(outer, highest_contour)

    def front_work(self, elements: List[Element]) -> float:
        return max(max(element.z0, element.z1) for element in elements)

    def contour_check(self) -> List[str]:
        return contour_warnings(self.work_elements())

    def tool_problems(self, tool) -> List[str]:
        roughing = bool(self.v.get("do_rough", True))
        return turning_tool_problems(tool, self.inside, self.z_sign() < 0,
                                     self.work_elements(), roughing)

    def approach_front(self, material, elements, start_radius) -> float:
        """Anfahr-Z im Arbeitssystem: Konturanfang, bei ungeplantem Rohteil davor dessen Kante.

        Steht vor dem Konturanfang noch Material, das nicht größer ist als der Startradius
        (z.B. Planaufmaß), beginnen die Schnitte vor diesem Material.
        """
        front = self.front_work(elements)
        if material is None or self.z_sign() < 0:
            return front
        sign = self.z_sign()
        real_front = sign * front
        material_front = material.material_end(real_front, sign)
        stub_length = abs(material_front - real_front)
        if stub_length < 1e-6 or stub_length > 10.0:
            return front
        if self.inside:
            inner = material.min_inner(real_front, material_front)
            passable = inner is not None and inner >= -start_radius - 1e-6
        else:
            outer = material.max_outer(real_front, material_front)
            passable = outer is not None and outer <= start_radius + 1e-6
        if passable:
            return sign * material_front
        return front

    def body(self, emitter, machine, material):
        elements = self.work_elements(material)
        start = self.start_radius_work(material, elements)
        turn_contour(emitter, elements, self.radius_sign, self.z_sign(), start,
                     self.approach_front(material, elements, start), self.ap, self.allow_x,
                     self.allow_z,
                     self.f_rough, self.f_finish, machine.clearance,
                     self.v.get("do_rough", True), self.v.get("do_finish", True),
                     self.v.get("comp", "aus"))


def _work_row(x_work: float, z_work: float, corner=None, size=0.0, angle=None, reference="A"):
    row = Row("L", x_work, z_work)
    if corner and size > EPS:
        row.corner = corner
        row.csize = size
        row.cangle = angle
        row.cref = reference
    return row


CORNER_KINDS = ("keine", "Fase", "Radius")


def _corner_code(kind: str) -> Optional[str]:
    if kind == "Fase":
        return "C"
    if kind == "Radius":
        return "R"
    return None


# =========================================================================== Zyklen

class Facing(Cycle):
    KEY = "facing"
    TITLE = "Plandrehen"
    SHORT = "Plan"
    RENAMES = {"stock_d": "d_start", "inner_d": "d_end"}
    PARAMS = [
        P("d_start", "Ø Start", 50.0, unit="mm", min=-5, sym="D", skey=True),
        P("d_end", "Ø Ende", -0.4, unit="mm", min=-5, sym="d",
          help="Außen: 0 = bis Mitte, leicht negativ damit kein Zapfen bleibt. "
               "D > d: Plandrehen zur Mitte, D < d: nach außen"),
        P("z_start", "Z Start", 1.0, unit="mm", sym="Z1",
          help="Z1 > Z2: Abtrag nach −Z (Stirnseite), Z1 < Z2: Abtrag nach +Z (Rückseite)"),
        P("z_end", "Z Ende", 0.0, unit="mm", sym="Z2"),
        P("ap", "Zustellung (Z)", 0.5, unit="mm", min=0.01, sym="ap"),
        feed_param("f_rough", "Vorschub Schruppen", 0.2, "F"),
        feed_param("f_finish", "Vorschub Schlichten", 0.08, "Fs"),
        P("allow_z", "Schlichtaufmaß Z", 0.1, unit="mm", min=0, sym="aZ"),
    ]

    def summary(self):
        return (f"{self.side_text} Ø{fmt(self.d_start)}→Ø{fmt(self.d_end)} "
                f"Z{fmt(self.z_start)}→{fmt(self.z_end)}{self.direction_text()}")

    def max_d(self):
        return max(self.d_start, self.d_end)

    def inner_x(self, machine):
        return max(0.0, min(self.d_start, self.d_end) - 2 * machine.clearance)

    def check(self, machine):
        if abs(self.z_end - self.z_start) < 1e-6:
            raise ContourError("Z Start und Z Ende sind gleich")
        if abs(self.d_end - self.d_start) < 1e-6:
            raise ContourError("Ø Start und Ø Ende sind gleich")
        return []

    def outline(self):
        radius_low = max(min(self.d_start, self.d_end), 0) / 2
        radius_high = max(self.d_start, self.d_end) / 2
        return [Element("line", self.z_end, radius_low, self.z_end, radius_high)]

    def material_cuts(self, material=None):
        radius_low = max(min(self.d_start, self.d_end), 0) / 2
        radius_high = max(self.d_start, self.d_end) / 2
        return [("band", self.z_end, self.z_start, radius_low, radius_high)]

    def radial_direction(self) -> float:
        """Vorschubrichtung des Planschnitts im Modellsystem (Grad)."""
        if self.d_end < self.d_start:
            return 270.0
        return 90.0

    def tool_problems(self, tool):
        name = _tool_name(tool)
        if tool.kind in ("thread", "drill"):
            return [f"{name}: für Plandrehen nicht geeignet"]
        sector = tool.sector()
        if sector is None:
            return []
        moving_inward = self.radial_direction() == 270.0
        if self.z_sign() > 0:
            if moving_inward:
                free_low, free_high = 0.0, 90.0
            else:
                free_low, free_high = 270.0, 360.0
        else:
            if moving_inward:
                free_low, free_high = 90.0, 180.0
            else:
                free_low, free_high = 180.0, 270.0
        if sector_inside(sector, free_low, free_high):
            return []
        if moving_inward:
            radial_text = "zur Mitte"
        else:
            radial_text = "nach außen"
        if self.z_sign() > 0:
            axial_text = "−Z"
        else:
            axial_text = "+Z"
        return [f"{name}: Werkzeuglage/Hand passt nicht zu dieser Planbearbeitung "
                f"(Richtung {radial_text}, Abtrag nach {axial_text})"]

    def body(self, emitter, machine, material):
        clearance = machine.clearance
        sign = self.z_sign()
        work = TransformedEmitter(emitter, 1, sign)
        z_first = sign * self.z_start
        z_last = sign * self.z_end
        rough_end = z_last + self.allow_z
        levels = []
        level = z_first - self.ap
        while level > rough_end + 1e-6:
            levels.append(level)
            level -= self.ap
        if rough_end < z_first - 1e-6:
            levels.append(rough_end)
        passes = []
        for level in levels:
            passes.append((level, self.f_rough))
        if self.allow_z > 0:
            passes.append((z_last, self.f_finish))
        start_d = self.d_start
        end_d = self.d_end
        if self.inside:
            approach_x = max(min(start_d, end_d) - 2 * clearance, 0.0)
        else:
            approach_x = max(start_d, end_d) + 2 * clearance
        # Schnitt beginnt auf der Seite, von der das Werkzeug kommt (außen zur Mitte,
        # innen nach außen)? Dann direkt auf Tiefe fahren und durchschneiden.
        if self.inside:
            cut_from_approach_side = end_d > start_d
        else:
            cut_from_approach_side = end_d < start_d
        work.rapid(x=approach_x)
        work.rapid(z=z_first + clearance)
        previous = z_first
        for index, (level, feed) in enumerate(passes):
            if index == len(passes) - 1 and self.allow_z > 0:
                work.comment("Schlichten")
            if cut_from_approach_side:
                work.rapid(z=level)
                work.feed(x=end_d, f=feed)
            else:
                work.rapid(z=previous + clearance)
                work.rapid(x=start_d)
                work.feed(z=level, f=feed)
                work.feed(x=end_d, f=feed)
            work.rapid(z=level + clearance)
            work.rapid(x=approach_x)
            previous = level
        work.rapid(z=z_first + clearance)


class LongTurning(TurningCycle):
    KEY = "long"
    TITLE = "Längsdrehen"
    SHORT = "Längs"
    RENAMES = {"stock_d": "d_start", "target_d": "d_end"}
    PARAMS = [
        P("d_start", "Ø Start", 50.0, unit="mm", min=0.1, sym="D", skey=True,
          help="Außen muss D ≥ d sein, innen D ≤ d"),
        P("d_end", "Ø Ende", 40.0, unit="mm", min=0, sym="d"),
        P("z_start", "Z Start", 0.0, unit="mm", sym="Z1",
          help="Z1 > Z2: nach −Z, Z1 < Z2: nach +Z (linkes Werkzeug)"),
        P("z_end", "Z Ende", -30.0, unit="mm", sym="Z2"),
        P("start_kind", "Kante am Anfang", "keine", "choice", CORNER_KINDS),
        P("start_size", "Größe Anfang (Fase 45° / Radius)", 1.0, unit="mm", min=0.01, sym="Ka",
          when=("start_kind", ("Fase", "Radius"))),
        P("end_kind", "Kante am Ende (Schulter/Grund)", "keine", "choice", CORNER_KINDS),
        P("end_size", "Größe Ende (Fase 45° / Radius)", 1.0, unit="mm", min=0.01, sym="Ke",
          when=("end_kind", ("Fase", "Radius")),
          help="Darf größer sein als der Absatz zu D – die Kante endet dann bei D"),
    ] + ROUGH

    def __init__(self, values=None):
        if values is None:
            values = {}
        values = dict(values)
        if values.get("chamfer", 0) and "start_kind" not in values:
            values["start_kind"] = "Fase"
            values["start_size"] = values["chamfer"]
        if values.get("end_radius", 0) and "end_kind" not in values:
            values["end_kind"] = "Radius"
            values["end_size"] = values["end_radius"]
        super().__init__(values)

    def summary(self):
        text = (f"{self.side_text} Ø{fmt(self.d_start)}→Ø{fmt(self.d_end)} "
                f"Z{fmt(self.z_start)}…{fmt(self.z_end)}{self.direction_text()}")
        for kind, size in ((self.start_kind, self.start_size), (self.end_kind, self.end_size)):
            if kind == "Fase":
                text += f" C{fmt(size)}"
            elif kind == "Radius":
                text += f" R{fmt(size)}"
        return text

    def max_d(self):
        return max(self.d_start, self.d_end)

    def check(self, machine):
        if not self.inside and self.d_start < self.d_end:
            raise ContourError("Außen muss Ø Start D größer oder gleich Ø Ende d sein")
        if self.inside and self.d_start > self.d_end:
            raise ContourError("Innen muss Ø Start D kleiner oder gleich Ø Ende d sein")
        if abs(self.d_start - self.d_end) < 1e-6:
            raise ContourError("Ø Start und Ø Ende sind gleich – nichts abzutragen")
        if abs(self.z_end - self.z_start) < 1e-6:
            raise ContourError("Z Start und Z Ende sind gleich")
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        target = sign_r * self.d_end
        start_diameter = sign_r * self.d_start
        z_first = sign_z * self.z_start
        z_last = sign_z * self.z_end
        rows = []
        start_corner = _corner_code(self.start_kind)
        if start_corner:
            face_x = target - 2 * (self.start_size + 0.5)
            if not self.inside and face_x < 0:
                face_x = 0.0
            rows.append(_work_row(face_x, z_first))
            rows.append(_work_row(target, z_first, start_corner, self.start_size))
        else:
            rows.append(_work_row(target, z_first))
        end_corner = _corner_code(self.end_kind)
        if end_corner:
            rows.append(_work_row(target, z_last, end_corner, self.end_size))
            extension = 2 * (2 * self.end_size + 1)
        else:
            rows.append(_work_row(target, z_last))
            extension = 2.0
        rows.append(_work_row(start_diameter + extension, z_last))
        try:
            elements = resolve_rows(rows)
        except ContourError:
            raise ContourError("Die Kanten passen nicht auf die Länge Z1…Z2 bzw. den Durchmesser "
                               "– Kanten verkleinern") from None
        return clip_at_radius(elements, start_diameter / 2, 1)

    def start_radius_work(self, material, elements):
        return self.radius_sign * self.d_start / 2


class Taper(TurningCycle):
    KEY = "taper"
    TITLE = "Konus"
    SHORT = "Konus"
    PARAMS = [
        P("mode", "Angabe über", "Enddurchmesser", "choice", ("Enddurchmesser", "Winkel"),
          help="Beim Umschalten wird der jeweils andere Wert berechnet"),
        P("d_start", "Ø Start (bei Z1)", 30.0, unit="mm", min=0, sym="D"),
        P("d_end", "Ø Ende (bei Z2)", 44.0, unit="mm", min=0, sym="d"),
        P("z_start", "Z Start", 0.0, unit="mm", sym="Z1"),
        P("z_end", "Z Ende", -25.0, unit="mm", sym="Z2", when=("mode", ("Enddurchmesser",))),
        P("angle", "Halber Kegelwinkel", 15.0, unit="°", min=-89, max=89, sym="α/2",
          when=("mode", ("Winkel",)),
          help="Positiv = Ø wird in Bearbeitungsrichtung größer, negativ = kleiner "
               "(fallender Konus, wird schräg angefahren). Z Ende wird berechnet"),
    ] + ROUGH

    @staticmethod
    def angle_from(d_start, d_end, z_start, z_end):
        return math.degrees(math.atan2((d_end - d_start) / 2, abs(z_start - z_end)))

    @staticmethod
    def z_end_from(d_start, d_end, z_start, angle, direction=1):
        tangent = math.tan(math.radians(angle))
        if abs(tangent) < 1e-9 or (d_end - d_start) * tangent < 0:
            raise ContourError("Winkel passt nicht zu den Durchmessern (Vorzeichen oder 0°)")
        return z_start - direction * (d_end - d_start) / (2 * tangent)

    @classmethod
    def switch(cls, key, values, old_value=None):
        updates = super().switch(key, values, old_value)
        if key != "mode":
            return updates
        try:
            if values.get("mode") == "Winkel":
                angle = cls.angle_from(values["d_start"], values["d_end"], values["z_start"],
                                       values["z_end"])
                updates["angle"] = round(angle, 4)
            else:
                z_end = cls.z_end_from(values["d_start"], values["d_end"], values["z_start"],
                                       values["angle"])
                updates["z_end"] = round(z_end, 4)
        except (ContourError, TypeError, KeyError, ZeroDivisionError):
            pass
        return updates

    def z_end_effective(self):
        if self.mode == "Winkel":
            return self.z_end_from(self.d_start, self.d_end, self.z_start, self.angle)
        return self.z_end

    def angle_effective(self):
        if self.mode == "Winkel":
            return self.angle
        return self.angle_from(self.d_start, self.d_end, self.z_start, self.z_end)

    def z_sign(self):
        if self.z_end_effective() > self.z_start:
            return -1
        return 1

    def help_values(self):
        values = dict(self.v)
        try:
            values["z_end"] = round(self.z_end_effective(), 3)
            values["angle"] = round(self.angle_effective(), 3)
        except (ContourError, ZeroDivisionError):
            pass
        return values

    def summary(self):
        try:
            return (f"{self.side_text} Ø{fmt(self.d_start)}→Ø{fmt(self.d_end)} "
                    f"Z{fmt(self.z_start)}…{fmt(self.z_end_effective(), 3)} "
                    f"α/2={fmt(self.angle_effective(), 3)}°{self.direction_text()}")
        except ContourError as error:
            return str(error)

    def check(self, machine):
        if abs(self.z_end_effective() - self.z_start) < 1e-6:
            raise ContourError("Z Start und Z Ende sind gleich")
        if abs(self.d_end - self.d_start) < 1e-6:
            raise ContourError("Ø Start und Ø Ende sind gleich – das ist ein Zylinder")
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        z_first = sign_z * self.z_start
        z_last = sign_z * self.z_end_effective()
        rows = [_work_row(sign_r * self.d_start, z_first), _work_row(sign_r * self.d_end, z_last)]
        elements = resolve_rows(rows)
        if material is not None:
            real_low = min(self.z_start, self.z_end_effective())
            real_high = max(self.z_start, self.z_end_effective())
            if self.inside:
                bore = material.min_inner(real_low, real_high)
                if bore is not None:
                    shoulder = -bore
                else:
                    shoulder = None
            else:
                shoulder = material.max_outer(real_low, real_high)
            end_radius = sign_r * self.d_end / 2
            if shoulder is not None and shoulder > end_radius + 1e-6:
                elements.append(Element("line", z_last, end_radius, z_last, shoulder))
        return elements


class EdgeBreak(TurningCycle):
    KEY = "edge"
    TITLE = "Fase / Kantenradius"
    SHORT = "Fase/R"
    RENAMES = {"edge_d": "diameter", "edge_z": "z_start"}
    PARAMS = [
        P("kind", "Art", "Fase", "choice", ("Fase", "Radius")),
        P("edge", "Kante", "Vorderkante", "choice", ("Vorderkante", "Rückkante"),
          help="Vorderkante: Planfläche zeigt nach +Z. Rückkante: Planfläche zeigt nach −Z "
               "(Hinterschnitt, wird schräg angefahren)"),
        P("size", "Länge in Z (Fase) bzw. Radius", 1.0, unit="mm", min=0.01, sym="L/R"),
        P("angle", "Fasenwinkel zur Z-Achse", 45.0, unit="°", min=-89, max=89, sym="α",
          when=("kind", ("Fase",)),
          help="45° = gleichschenklig. Negativer Winkel = Fase an der Rückkante"),
        P("diameter", "Ø der Kante", 40.0, unit="mm", min=0.1, sym="D"),
        P("z_start", "Z der Planfläche", 0.0, unit="mm", sym="Z1"),
        P("ap", "Zustellung (radial)", 0.5, unit="mm", min=0.01, sym="ap"),
        feed_param("f_rough", "Vorschub Schruppen", 0.15, "F"),
        feed_param("f_finish", "Vorschub Schlichten", 0.08, "Fs"),
        P("allow_x", "Schlichtaufmaß X (Ø)", 0.2, unit="mm", min=0, sym="aX"),
        P("allow_z", "Schlichtaufmaß Z", 0.1, unit="mm", min=0, sym="aZ"),
        P("comp", "Schneidenradiuskorrektur", "auto", "choice", ("aus", "auto", "G41", "G42")),
    ]

    def z_sign(self):
        return 1

    def is_back_edge(self) -> bool:
        back = self.edge == "Rückkante"
        if self.kind == "Fase" and self.angle < 0:
            back = not back
        return back

    def radial_leg(self):
        if self.kind == "Fase":
            return self.size * math.tan(math.radians(abs(self.angle)))
        return self.size

    def summary(self):
        if self.kind == "Fase":
            text = f"Fase {fmt(self.size)}×{fmt(abs(self.angle))}°"
        else:
            text = f"R{fmt(self.size)}"
        if self.is_back_edge():
            text += " Rückkante"
        return f"{self.side_text} {text} an Ø{fmt(self.diameter)} Z{fmt(self.z_start)}"

    def check(self, machine):
        if abs(self.angle) < 1e-6 and self.kind == "Fase":
            raise ContourError("Fasenwinkel darf nicht 0° sein")
        if not self.inside and self.radial_leg() + 0.5 >= self.diameter / 2:
            raise ContourError("Fase/Radius ist zu groß für diesen Durchmesser")
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        edge_x = sign_r * self.diameter
        z_face = self.z_start
        leg = self.radial_leg()
        if self.kind == "Fase":
            corner = ("C", self.size, abs(self.angle))
        else:
            corner = ("R", self.size, None)
        if not self.is_back_edge():
            rows = [_work_row(edge_x - 2 * (leg + 0.5), z_face),
                    _work_row(edge_x, z_face, corner[0], corner[1], corner[2], "B"),
                    _work_row(edge_x, z_face - self.size - 0.5)]
        else:
            # Rückkante: Kontur endet am Fasen-/Radiusende, die Planfläche dahinter bleibt
            # unberührt – so genügt ein rechtes Werkzeug mit ausreichendem Freiwinkel.
            rows = [_work_row(edge_x, z_face + self.size + 0.5),
                    _work_row(edge_x, z_face, corner[0], corner[1], corner[2], "A"),
                    _work_row(edge_x - 2 * (leg + 0.5), z_face)]
            elements = resolve_rows(rows)
            return elements[:-1]
        return resolve_rows(rows)

    def start_radius_work(self, material, elements):
        return self.radius_sign * self.diameter / 2


class Contour(TurningCycle):
    KEY = "contour"
    TITLE = "Kontur"
    SHORT = "Kontur"
    PARAMS = [
        P("contour", "Kontur", "X0 Z0\nX50 Z0 C2 A30\nX50 Z-20\nX60 Z-20 R5\nX60 Z-50\nX64 Z-50",
          "contour", sym="P1…Pn"),
        P("corner_mode", "Alle übrigen Ecken", "keine", "choice", ("keine", "Radius", "Fase"),
          help="Gilt für alle Ecken ohne eigene Angabe"),
        P("corner_size", "Größe für alle übrigen Ecken", 1.0, unit="mm", min=0),
    ] + ROUGH

    def rows(self):
        return parse_rows(self.contour)

    def z_sign(self):
        rows = self.rows()
        if rows[-1].z > rows[0].z:
            return -1
        return 1

    def real_elements(self):
        if self.corner_mode == "Radius":
            default = "R"
        elif self.corner_mode == "Fase":
            default = "C"
        else:
            default = None
        return resolve_rows(self.rows(), default, self.corner_size)

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        return [element.transformed(sign_r, sign_z) for element in self.real_elements()]

    def summary(self):
        try:
            rows = self.rows()
            return (f"{self.side_text} {len(rows)} Punkte "
                    f"Z{fmt(rows[0].z)}…{fmt(rows[-1].z)}{self.direction_text()}")
        except ContourError as error:
            return f"Fehler: {error}"

    def check(self, machine):
        return self.contour_check()


class Thread(Cycle):
    KEY = "thread"
    TITLE = "Gewinde (G76)"
    SHORT = "Gewinde"
    TECH_PARAMS = TECH_THREAD
    FORCE_G97 = True
    PARAMS = [
        P("norm", "Gewinde aus Tabelle", NORMS["frei"], "choice", tuple(NORMS.values()),
          help="Norm und Größe wählen: Ø, Steigung, Tiefe und Zustellwinkel werden eingetragen "
               "(danach von Hand änderbar)"),
    ]
    for _norm, _key in SIZE_KEYS.items():
        PARAMS.append(P(_key, "Größe", names_for(_norm)[0], "choice", names_for(_norm),
                        when=("norm", (NORMS[_norm],))))
    PARAMS += [
        P("diameter", "Nenn-Ø", 20.0, unit="mm", min=0.5, sym="D", label_in="Kernloch-Ø"),
        P("pitch", "Steigung", 2.5, unit="mm", min=0.05, sym="P"),
        P("z_begin", "Z Gewindeanfang", 0.0, unit="mm", sym="Z1"),
        P("z_end", "Z Gewindeende", -30.0, unit="mm", sym="Z2",
          help="Z2 < Z1: Gewinde nach −Z, Z2 > Z1: Gewinde nach +Z"),
        P("z_start", "Z Startpunkt (Anlauf)", 5.0, unit="mm", sym="Zs",
          help="Liegt vor dem Gewindeanfang, entgegen der Gewinderichtung"),
        P("depth", "Gewindetiefe (0 = automatisch)", 0.0, unit="mm", min=0, sym="K"),
        P("first_cut", "Erste Zustellung", 0.3, unit="mm", min=0.01, sym="J"),
        P("degression", "Degression (1–3)", 1.5, min=1.0, max=3.0, sym="R"),
        P("infeed", "Zustellwinkel", 29.5, unit="°", min=0, max=60, sym="Q"),
        P("spring", "Leerschnitte", 1, "int", min=0, max=10, sym="H"),
        P("chamfer_l", "Kegel an (0 kein, 1 Ein, 2 Aus, 3 beide)", 0, "int", min=0, max=3, sym="L"),
        P("chamfer_e", "Kegellänge Ein-/Auslauf", 0.0, unit="mm", min=0, sym="E"),
        P("thread_clear", "Abstand Zustelllinie", 1.0, unit="mm", min=0.1, sym="I"),
    ]

    def thread_direction(self) -> int:
        """−1: Gewinde läuft nach −Z, +1: nach +Z."""
        if self.z_end > self.z_begin:
            return 1
        return -1

    @staticmethod
    def spec_from(values: Dict[str, Any]) -> Optional[ThreadSpec]:
        """Gewinde aus der Tabelle (None bei „frei“)."""
        norm = norm_of_label(str(values.get("norm", "frei")))
        if norm == "frei":
            return None
        return find(norm, str(values.get(SIZE_KEYS[norm], "")))

    def spec(self) -> Optional[ThreadSpec]:
        return self.spec_from(self.v)

    @classmethod
    def switch(cls, key, values, old_value=None):
        updates = super().switch(key, values, old_value)
        if key not in ("norm", "side") and key not in SIZE_KEYS.values():
            return updates
        spec = cls.spec_from(values)
        if spec is None:
            return updates
        inside = values.get("side") == "Innen"
        updates["diameter"] = round(spec.cycle_diameter(inside), 3)
        updates["pitch"] = round(spec.pitch, 6)
        updates["depth"] = 0.0
        updates["infeed"] = spec.infeed()
        return updates

    def pre_turn_hint(self) -> str:
        """Hinweis auf den Vordreh-Ø (außen D − 0,1·P, innen D1 + 0,1·P)."""
        spec = self.spec()
        if self.inside:
            pre_turn = self.diameter + 0.1 * self.pitch
            text = (f"Bohrung vordrehen auf Ø{pre_turn:.2f} (Kern-Ø D1 {self.diameter:.2f} "
                    f"+ 0,1·P)")
        else:
            pre_turn = self.diameter - 0.1 * self.pitch
            text = f"Vordrehen auf Ø{pre_turn:.2f} (Nenn-Ø − 0,1·P)"
        if spec is not None:
            text = f"{spec.name} {spec.pitch_text()}: " + text
        return text

    def eff_depth(self):
        if self.depth > 0:
            return self.depth
        spec = self.spec()
        if spec is not None:
            return spec.depth(self.inside)
        if self.inside:
            return 0.5413 * self.pitch
        return 0.6134 * self.pitch

    def summary(self):
        spec = self.spec()
        if spec is not None:
            text = f"{self.side_text} {spec.name}"
        elif self.inside:
            text = f"innen Kernloch Ø{fmt(self.diameter)}×P{fmt(self.pitch)}"
        else:
            text = f"außen M{fmt(self.diameter)}×{fmt(self.pitch)}"
        return text + f" Z{fmt(self.z_begin)}…{fmt(self.z_end)}"

    def feeds(self):
        return []

    def z_range(self):
        values = (self.z_begin, self.z_end, self.z_start)
        return min(values), max(values)

    def max_d(self):
        if self.inside:
            return self.diameter + 2 * self.eff_depth()
        return self.diameter + 2 * self.thread_clear

    def outer_x(self, machine):
        return self.diameter + 2 * (self.thread_clear + machine.clearance)

    def inner_x(self, machine):
        return max(0.0, self.diameter - 2 * (self.thread_clear + machine.clearance))

    def passes(self):
        depth, first, degression = self.eff_depth(), self.first_cut, self.degression
        depths = []
        number = 1
        while True:
            cut = first * number ** (1.0 / degression)
            if cut >= depth - 1e-6 or number > 200:
                depths.append(depth)
                break
            depths.append(cut)
            number += 1
        return depths

    def rpm_limit(self, machine: Machine) -> float:
        return min(machine.max_rpm, machine.thread_speed_factor * machine.max_vel_z / self.pitch)

    def check(self, machine):
        warnings = []
        if abs(self.z_end - self.z_begin) < 1e-6:
            raise ContourError("Gewindeanfang und -ende sind gleich")
        run_in = (self.z_start - self.z_begin) * -self.thread_direction()
        if run_in < 0:
            raise ContourError("Der Startpunkt Zs muss vor dem Gewindeanfang liegen "
                               "(entgegen der Gewinderichtung)")
        if self.first_cut > self.eff_depth():
            raise ContourError("Erste Zustellung ist größer als die Gewindetiefe")
        if self.inside and self.diameter / 2 - self.thread_clear <= 0:
            raise ContourError("Abstand der Zustelllinie ist größer als der Kernlochradius")
        rpm = min(self.speed, self.rpm_limit(machine))
        velocity = rpm * self.pitch / 60.0
        acceleration_way = velocity * velocity / (2 * max(machine.max_acc_z, 1e-3))
        if run_in < 1.5 * acceleration_way:
            warnings.append(f"Anlaufweg {run_in:.2f} mm ist knapp (Beschleunigungsweg ≈ "
                            f"{acceleration_way:.2f} mm) – Startpunkt weiter weg legen oder "
                            f"Drehzahl senken")
        warnings.append(self.pre_turn_hint())
        warnings.append(f"{len(self.passes())} Gewindeschnitte + {self.spring} Leerschnitt(e); "
                        f"Spindel-Encoder erforderlich")
        return warnings

    def outline(self):
        radius = self.diameter / 2
        root = radius - self.radius_sign * self.eff_depth()
        if self.chamfer_e > 0 and self.chamfer_l in (2, 3):
            taper_length = min(self.chamfer_e, abs(self.z_end - self.z_begin))
            taper_z = self.z_end - self.thread_direction() * taper_length
            return [Element("line", self.z_begin, root, taper_z, root),
                    Element("line", taper_z, root, self.z_end, radius)]
        return [Element("line", self.z_begin, root, self.z_end, root)]

    def tool_problems(self, tool):
        name = _tool_name(tool)
        if tool.kind != "thread":
            return [f"{name}: Mit {KIND_LABELS.get(tool.kind, tool.kind)} kann kein Gewinde "
                    f"geschnitten werden – Gewindewerkzeug wählen"]
        if self.inside and tool.position != "innen":
            return [f"{name}: Innengewinde braucht ein Innen-Gewindewerkzeug"]
        if not self.inside and tool.position == "innen":
            return [f"{name}: Außengewinde braucht ein Außen-Gewindewerkzeug"]
        spec = self.spec()
        if spec is not None and abs(tool.insert_angle - spec.angle) > 2.0:
            return [f"{name}: Spitzenwinkel {tool.insert_angle:g}° passt nicht zu {spec.name} "
                    f"({spec.angle:g}°)"]
        return []

    def body(self, emitter, machine, material):
        radius = self.diameter / 2
        clear = self.thread_clear
        sign_r = self.radius_sign
        drive_radius = radius + sign_r * clear
        depth = self.eff_depth()
        safe_x = 2 * (radius + sign_r * (clear + machine.clearance))
        if self.inside:
            safe_x = max(safe_x, 0.0)
        emitter.rapid(x=safe_x)
        emitter.rapid(z=self.z_start)
        emitter.rapid(x=2 * drive_radius)
        emitter.comment("G76 im Radiusmodus G8 (I, J, K als Radiuswerte), danach zurück zu G7")
        emitter.set_radius_mode(True)
        g76 = (f"G76 P{fmt(self.pitch, 6)} Z{fmt(self.z_end)} I{fmt(-sign_r * clear)} "
               f"J{fmt(self.first_cut)} R{fmt(self.degression)} K{fmt(depth)} "
               f"Q{fmt(self.infeed)} H{int(self.spring)}")
        if self.chamfer_e > 0 and self.chamfer_l > 0:
            g76 += f" E{fmt(self.chamfer_e)} L{int(self.chamfer_l)}"
        emitter.lines.append(g76)
        emitter.set_radius_mode(False)
        # Bahn für die Vorschau: Kegel am Ein-/Auslauf (L, E) laufen über die Länge E
        # von der Gewindespitze (Nenn-Ø) auf Schnitttiefe bzw. zurück.
        direction = self.thread_direction()
        taper_length = 0.0
        if self.chamfer_e > 0 and self.chamfer_l > 0:
            taper_length = min(self.chamfer_e, abs(self.z_end - self.z_start))
        entry_taper = taper_length > 0 and self.chamfer_l in (1, 3)
        exit_taper = taper_length > 0 and self.chamfer_l in (2, 3)
        for cut in self.passes():
            pass_radius = radius - sign_r * cut
            if entry_taper:
                emitter.record_thread(2 * drive_radius, self.z_start, 2 * radius, self.z_start)
                cut_start_z = self.z_start + direction * taper_length
                emitter.record_thread(2 * radius, self.z_start, 2 * pass_radius, cut_start_z)
            else:
                cut_start_z = self.z_start
                emitter.record_thread(2 * drive_radius, self.z_start, 2 * pass_radius,
                                      self.z_start)
            if exit_taper:
                cut_end_z = self.z_end - direction * taper_length
                emitter.record_thread(2 * pass_radius, cut_start_z, 2 * pass_radius, cut_end_z)
                emitter.record_thread(2 * pass_radius, cut_end_z, 2 * radius, self.z_end)
                emitter.record_thread(2 * radius, self.z_end, 2 * drive_radius, self.z_end)
            else:
                emitter.record_thread(2 * pass_radius, cut_start_z, 2 * pass_radius, self.z_end)
                emitter.record_thread(2 * pass_radius, self.z_end, 2 * drive_radius, self.z_end)
        emitter.set_pos(2 * drive_radius, self.z_start)
        emitter.rapid(x=safe_x)


class Drill(Cycle):
    KEY = "drill"
    TITLE = "Bohren"
    SHORT = "Bohren"
    HAS_SIDE = False
    RENAMES = {"drill_d": "diameter"}
    PARAMS = [
        P("diameter", "Bohrer-Ø", 10.0, unit="mm", min=0.1, sym="D"),
        P("tip_angle", "Spitzenwinkel", 118.0, unit="°", min=60, max=180, sym="σ"),
        P("z_start", "Z Start (Oberfläche)", 0.0, unit="mm", sym="Z1"),
        P("z_end", "Z Ende (Bohrtiefe)", -20.0, unit="mm", sym="Z2"),
        P("depth_ref", "Tiefe bezogen auf", "Spitze", "choice", ("Spitze", "Voller Ø"),
          help="Voller Ø: Z2 ist das Ende der zylindrischen Bohrung, die Spitze geht tiefer"),
        P("mode", "Bohrart", "Spanbruch", "choice", ("Durchgehend", "Spanbruch", "Entspanen"),
          help="Spanbruch: kurz zurück nach jeder Zustellung. Entspanen: ganz heraus"),
        P("peck", "Zustelltiefe", 5.0, unit="mm", min=0.1, sym="Q",
          when=("mode", ("Spanbruch", "Entspanen"))),
        P("break_back", "Rückzug Spanbruch", 0.5, unit="mm", min=0.05, sym="Rb",
          when=("mode", ("Spanbruch",))),
        P("dwell", "Verweilzeit am Grund", 0.0, unit="s", min=0, sym="P"),
        feed_param("f", "Vorschub", 0.1, "F"),
    ]

    def __init__(self, values=None):
        if values is None:
            values = {}
        super().__init__(values)
        if "speed" not in values:
            self.v["speed"] = 25.0          # Schnittgeschwindigkeit für Bohrer

    @property
    def inside(self):
        return True

    def z_sign(self):
        return 1

    def summary(self):
        return f"Ø{fmt(self.diameter)} Z{fmt(self.z_start)}→{fmt(self.z_end)} ({self.mode})"

    def max_d(self):
        return self.diameter

    def inner_x(self, machine):
        return 0.0

    def tip_z(self):
        if self.depth_ref == "Spitze":
            return self.z_end
        return self.z_end - (self.diameter / 2) / math.tan(math.radians(self.tip_angle / 2))

    def shoulder_z(self):
        return self.tip_z() + (self.diameter / 2) / math.tan(math.radians(self.tip_angle / 2))

    def z_range(self):
        return self.tip_z(), self.z_start

    def check(self, machine):
        if self.z_end >= self.z_start:
            raise ContourError("Bohren geht nur nach −Z: Z Ende muss kleiner als Z Start sein")
        return []

    def outline(self):
        radius = self.diameter / 2
        shoulder = self.shoulder_z()
        tip = self.tip_z()
        if shoulder < self.z_start:
            return [Element("line", self.z_start, radius, shoulder, radius),
                    Element("line", shoulder, radius, tip, 0.0)]
        entry_radius = radius * (self.z_start - tip) / (shoulder - tip)
        return [Element("line", self.z_start, entry_radius, tip, 0.0)]

    def material_cuts(self, material=None):
        return [("inside", polyline(self.outline()))]

    def tool_problems(self, tool):
        if tool.kind != "drill":
            return [f"{_tool_name(tool)}: Bohren braucht einen Bohrer"]
        if tool.diameter and abs(tool.diameter - self.diameter) > 0.01:
            return [f"T{tool.number}: Bohrer-Ø {tool.diameter:g} passt nicht zu D {self.diameter:g}"]
        return []

    def body(self, emitter, machine, material):
        clearance = machine.clearance
        top = self.z_start + clearance
        tip = self.tip_z()
        emitter.rapid(x=0.0)
        emitter.rapid(z=top)
        if self.mode == "Durchgehend":
            emitter.feed(z=tip, f=self.f)
        else:
            depth = self.z_start
            while depth > tip + 1e-6:
                next_depth = max(depth - self.peck, tip)
                if self.mode == "Spanbruch":
                    emitter.feed(z=next_depth, f=self.f)
                    if next_depth > tip + 1e-6:
                        emitter.rapid(z=next_depth + self.break_back)
                else:
                    if depth < self.z_start:
                        emitter.rapid(z=depth + min(clearance, 0.5))
                    emitter.feed(z=next_depth, f=self.f)
                    if next_depth > tip + 1e-6:
                        emitter.rapid(z=top)
                depth = next_depth
        emitter.dwell(self.dwell)
        emitter.rapid(z=top)


class GCodeOp(Cycle):
    """Freier G-Code zwischen zwei Zyklen (z.B. M5 / M0 zum Späne entfernen)."""
    KEY = "gcode"
    TITLE = "G-Code"
    SHORT = "G-Code"
    TECH_PARAMS = []
    HAS_SIDE = False
    HAS_TOOL = False
    PARAMS = [
        P("code", "G-Code", "M5\nM9\nM0 (Späne entfernen – weiter mit Zyklusstart)", "text",
          help="Wird unverändert an dieser Stelle eingefügt. Das Werkzeug steht dabei auf der "
               "Rückzugsebene. Eigene Fahrbewegungen müssen dorthin zurückkehren."),
        P("restart", "Spindel/Kühlung danach wieder einschalten", True, "bool",
          help="Direkt nach dem Baustein werden Drehzahl mit M3/M4 und M8 des vorherigen "
               "Zyklus wieder ausgegeben"),
    ]

    @property
    def tool(self):
        return None

    @property
    def coolant(self):
        return False

    def summary(self):
        lines = [line.strip() for line in str(self.code).splitlines() if line.strip()]
        text = " · ".join(lines[:3])
        if len(lines) > 3:
            text += " …"
        return text

    def z_range(self):
        return 0.0, 0.0

    def body(self, emitter, machine, material):
        emitter.comment("--- eigener G-Code ---")
        for line in str(self.code).splitlines():
            if line.strip():
                emitter.emit(line.rstrip())
        emitter.comment("--- Ende eigener G-Code ---")


CYCLES = [Facing, LongTurning, Taper, Thread, EdgeBreak, Contour, Drill, GCodeOp]
CYCLE_BY_KEY = {cycle.KEY: cycle for cycle in CYCLES}


def cycle_from_dict(data) -> Cycle:
    return CYCLE_BY_KEY[data["cycle"]](data.get("values", {}))


# =========================================================================== Programm

@dataclass
class Result:
    gcode: str
    path: List[Segment]
    outlines: List[Tuple[int, List[Element], bool]]
    warnings: List[str]
    errors: List[str]


class Program:
    def __init__(self, machine: Machine, ops: Optional[List[Cycle]] = None, name="Drehteil",
                 stock: Optional[Stock] = None, tools=None):
        self.machine = machine
        if ops is None:
            ops = []
        self.ops: List[Cycle] = ops
        self.name = name
        if stock is None:
            stock = Stock()
        self.stock = stock
        self.tools = tools

    # ---------------------------------------------------------------- Projektdatei
    def to_json(self) -> str:
        return json.dumps({"name": self.name, "stock": self.stock.to_dict(),
                           "ops": [op.to_dict() for op in self.ops]}, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str, machine: Machine, tools=None) -> "Program":
        data = json.loads(text)
        ops = [cycle_from_dict(entry) for entry in data.get("ops", [])]
        return cls(machine, ops, data.get("name", ""), Stock(data.get("stock")), tools)

    # ---------------------------------------------------------------- Technologie
    def limited_op(self, op: Cycle, tag: str, warnings: List[str]) -> Cycle:
        """Vorschübe bei G94 auf die Maschinengrenze begrenzen."""
        if op.feed_mode() != "G94":
            return op
        limit = self.machine.max_feed_vel
        updates = {}
        for key in op.feed_keys():
            if op.v[key] > limit:
                updates[key] = limit
                warnings.append(tag + f"Vorschub {op.v[key]:g} mm/min auf {limit:.0f} mm/min "
                                      f"begrenzt (Maschinengrenze)")
        if updates:
            return op.copy_with(updates)
        return op

    def spindle_word(self, op: Cycle, tag: str, warnings: List[str]) -> str:
        machine = self.machine
        limit = machine.max_rpm
        feeds = [feed for feed in op.feeds() if feed and feed > 0]
        if feeds and op.feed_mode() == "G95":
            rpm_by_velocity = machine.max_feed_vel / max(feeds)
            if rpm_by_velocity < limit:
                limit = rpm_by_velocity
                warnings.append(tag + f"Drehzahl auf {limit:.0f} 1/min begrenzt, da Vorschub "
                                      f"{max(feeds):g} mm/U sonst die Achsgeschwindigkeit "
                                      f"überschreitet")
        if op.KEY == "thread":
            limit = min(limit, op.rpm_limit(machine))
        direction = op.spindle_direction(machine)
        rpm = None
        if op.KEY == "drill":
            if op.spindle_mode() == "G96":
                rpm = op.speed * 1000 / (math.pi * op.diameter)
            else:
                rpm = op.speed
        elif op.spindle_mode() == "G97":
            rpm = op.speed
        if rpm is not None:
            if rpm > limit + 0.5:
                warnings.append(tag + f"Drehzahl {rpm:.0f} → {limit:.0f} 1/min begrenzt")
                rpm = limit
            if rpm < machine.min_rpm:
                warnings.append(tag + f"Drehzahl {rpm:.0f} unter Minimum, auf "
                                      f"{machine.min_rpm:.0f} angehoben")
                rpm = machine.min_rpm
            return f"G97 S{rpm:.0f} {direction}"
        cutting_speed = op.speed
        diameter = max(op.max_d(), 1e-3)
        rpm_at_diameter = cutting_speed * 1000 / (math.pi * diameter)
        if rpm_at_diameter < machine.min_rpm:
            warnings.append(tag + f"Bei Ø{diameter:.1f} ergäbe Vc {cutting_speed:g} nur "
                                  f"{rpm_at_diameter:.0f} 1/min (Minimum {machine.min_rpm:.0f})")
        return f"G96 D{limit:.0f} S{fmt(cutting_speed)} {direction}"

    # ---------------------------------------------------------------- Werkzeugwechsel
    def _goto_tool_change(self, emitter: Emitter, x_out: float, z_safe: float):
        machine = self.machine
        if machine.tc_enabled:
            source = machine.tc_source or "Maschine"
            emitter.comment(f"Werkzeugwechselposition G53 ({source})")
            emitter.set_radius_mode(True)
            emitter.emit(f"G53 G0 X{fmt(machine.tc_x)}")
            emitter.emit(f"G53 G0 Z{fmt(machine.tc_z)}")
            emitter.set_radius_mode(False)
            emitter.set_pos(None, None)
        else:
            emitter.rapid(x=x_out)
            emitter.rapid(z=z_safe)

    # ---------------------------------------------------------------- Prüfungen
    def _tool_check(self, op: Cycle, tag: str, warnings: List[str], errors: List[str]):
        if self.tools is None or not op.HAS_TOOL:
            return
        tool = self.tools.get(op.tool)
        if tool is None:
            errors.append(tag + f"Werkzeug T{op.tool} steht nicht in der Werkzeugtabelle")
            return
        if tool.needs_input:
            errors.append(tag + f"T{tool.number}: Typ, Hand und Lage im Reiter Werkzeuge angeben")
            return
        problems = op.tool_problems(tool)
        for problem in problems:
            if op.v.get("tool_ok"):
                warnings.append(tag + "freigegeben: " + problem)
            else:
                errors.append(tag + problem + " (Freigabe: »Werkzeug trotz Warnung freigeben«)")

    def _chuck_check(self, path, ops_by_number, warnings, errors):
        stock = self.stock
        chuck_z = stock.z_chuck
        clearance = stock.chuck_clear
        lowest = {}
        for segment in path:
            if segment.op in ops_by_number:
                lowest[segment.op] = min(lowest.get(segment.op, 1e9), segment.z0, segment.z1)
        for number in sorted(lowest):
            z = lowest[number]
            op = ops_by_number[number]
            tag = f"Op {number} ({op.TITLE}): "
            if z < chuck_z - 1e-6:
                if op.inside:
                    warnings.append(tag + f"fährt bis Z{z:.2f} – tiefer als die Futterfläche "
                                          f"Z{chuck_z:g} (Bohrung/Werkzeug im Futterbereich prüfen)")
                else:
                    errors.append(tag + f"Kollision: fährt bis Z{z:.2f}, die Futterfläche liegt "
                                        f"bei Z{chuck_z:g}")
            elif z < chuck_z + clearance - 1e-6 and not op.inside:
                warnings.append(tag + f"fährt bis Z{z:.2f}, nur {z - chuck_z:.2f} mm vor dem "
                                      f"Futter (Sicherheitsabstand {clearance:g})")

    @staticmethod
    def _rapid_check(path, start_index, material: Material, tag, errors):
        """Eilgänge dürfen nicht durch das nach dem Zyklus verbleibende Material führen."""
        for segment in path[start_index:]:
            if segment.kind != "rapid":
                continue
            length = math.hypot(segment.z1 - segment.z0, (segment.x1 - segment.x0) / 2)
            steps = max(1, int(length / 0.5))
            for step in range(steps + 1):
                fraction = step / steps
                z = segment.z0 + fraction * (segment.z1 - segment.z0)
                r = (segment.x0 + fraction * (segment.x1 - segment.x0)) / 2
                if material.contains(z, r, 0.1):
                    errors.append(tag + f"Eilgang durch Material bei Z{z:.2f} X{2 * r:.2f} – "
                                        f"Bearbeitungsreihenfolge, Z1/Z2 oder Ø prüfen")
                    return

    # ---------------------------------------------------------------- Erzeugen
    def generate(self, only: Optional[int] = None,
                 start_material: Optional[Material] = None) -> Result:
        machine = self.machine
        stock = self.stock
        emitter = Emitter()
        warnings: List[str] = []
        errors: List[str] = []
        outlines = []
        numbered_ops = list(enumerate(self.ops, 1))
        if only is not None:
            numbered_ops = [numbered_ops[only]]

        try:
            stock.check()
        except ContourError as error:
            errors.append(str(error))
        valid = []
        for number, op in numbered_ops:
            tag = f"Op {number} ({op.TITLE}): "
            try:
                for warning in op.check(machine):
                    warnings.append(tag + warning)
                outline = op.outline()
                if op.max_d() > machine.max_diameter + 1e-6:
                    errors.append(tag + f"Ø{op.max_d():.1f} größer als max. Drehdurchmesser "
                                        f"{machine.max_diameter:.0f}")
                    continue
                if op.KEY == "facing" and op.z_sign() > 0 and not op.inside:
                    if stock.z_front > op.z_start + 1e-6:
                        warnings.append(tag + f"Rohteil-Vorderkante Z{stock.z_front:g} liegt vor "
                                              f"Z1 {op.z_start:g} – erster Schnitt wäre zu tief")
                self._tool_check(op, tag, warnings, errors)
                valid.append((number, op))
                if outline:
                    outlines.append((number, outline, op.inside))
            except (ContourError, ValueError, TypeError, ZeroDivisionError) as error:
                errors.append(tag + str(error))

        emitter.emit("%")
        emitter.comment(f"{self.name} - erzeugt mit Drehzyklen {datetime.date.today().isoformat()}")
        emitter.comment(f"Maschine: {machine.summary()}")
        emitter.comment(stock.summary())
        emitter.emit("G18 G7 G21 G90 G91.1 G40 G49 G80 G54")
        emitter.emit("G95 G64 P0.01")
        machining = [(number, op) for number, op in valid if op.HAS_TOOL]
        if not machining:
            for _number, op in valid:
                op.body(emitter, machine, None)
            emitter.emit("M2")
            emitter.emit("%")
            return Result(emitter.text(), emitter.path, outlines, warnings, errors)

        if start_material is not None:
            material = start_material.copy()
        else:
            z_low, z_high = bounds(stock, [op for _number, op in valid])
            material = Material(stock, z_low, z_high)
        z_safe = max([op.z_range()[1] for _number, op in machining] + [stock.z_front])
        z_safe += machine.retract
        outer_candidates = [op.outer_x(machine) for _number, op in machining if not op.inside]
        outer_candidates.append(stock.D + 2 * machine.clearance)
        x_out = max(outer_candidates)
        inner_candidates = [op.inner_x(machine) for _number, op in machining if op.inside]
        if inner_candidates:
            x_in = min(inner_candidates)
        else:
            x_in = 0.0
        emitter.comment(f"Rückzugsebene Z{fmt(z_safe)}, außen X{fmt(x_out)}, innen X{fmt(x_in)}")
        if not machine.tc_enabled:
            warnings.append("Keine Werkzeugwechselposition definiert (INI [CHANGE_POSITION] oder "
                            "Reiter Maschine) – Wechsel erfolgt an der Rückzugsposition")

        last_tool = None
        spindle = None
        coolant = False
        for position, (number, op) in enumerate(valid):
            tag = f"Op {number} ({op.TITLE}): "
            emitter.op = number
            if not op.HAS_TOOL:
                emitter.blank()
                emitter.comment(f"--- {number}: {op.TITLE} ---")
                op.body(emitter, machine, material)
                if op.restart:
                    next_tool = None
                    for _next_number, next_op in valid[position + 1:]:
                        if next_op.HAS_TOOL:
                            next_tool = next_op.tool
                            break
                    if next_tool != last_tool:
                        # Werkzeugwechsel folgt: der nächste Zyklus schaltet Spindel/Kühlung ein
                        spindle = None
                        coolant = False
                        continue
                    # gleiches Werkzeug: Zustand vor dem Baustein gleich wiederherstellen
                    if spindle or coolant:
                        emitter.comment("Spindel/Kühlung wieder einschalten")
                    if spindle:
                        emitter.emit(spindle)
                    if coolant:
                        emitter.emit("M8")
                continue
            mark = (len(emitter.lines), len(emitter.path), len(warnings), emitter.x, emitter.z,
                    emitter.feed_value, emitter.radius_mode, emitter.feed_mode, spindle, coolant)
            try:
                op_limited = self.limited_op(op, tag, warnings)
                emitter.blank()
                emitter.comment(f"--- {number}: {op.TITLE}: {op.summary()} ---")
                if op.tool != last_tool:
                    if coolant:
                        emitter.emit("M9")
                        coolant = False
                    if spindle:
                        emitter.emit("M5")
                        spindle = None
                    self._goto_tool_change(emitter, x_out, z_safe)
                    emitter.emit(f"T{int(op.tool)} M6 G43")
                    emitter.rapid(z=z_safe)
                else:
                    emitter.comment(f"gleiches Werkzeug T{int(op.tool)} – kein Werkzeugwechsel")
                emitter.set_feed_mode(op.feed_mode())
                spindle_text = self.spindle_word(op_limited, tag, warnings)
                if spindle_text != spindle:
                    emitter.emit(spindle_text)
                    spindle = spindle_text
                if op.coolant and not coolant:
                    emitter.emit("M8")
                    coolant = True
                elif not op.coolant and coolant:
                    emitter.emit("M9")
                    coolant = False
                path_start = len(emitter.path)
                op_limited.body(emitter, machine, material)
                if op.inside:
                    emitter.rapid(x=x_in)
                else:
                    current_x = emitter.x
                    if current_x is None:
                        current_x = 0.0
                    emitter.rapid(x=max(x_out, current_x))
                emitter.rapid(z=z_safe)
                material.apply(op)
                self._rapid_check(emitter.path, path_start, material, tag, errors)
            except (ContourError, ValueError, TypeError, ZeroDivisionError) as error:
                del emitter.lines[mark[0]:]
                del emitter.path[mark[1]:]
                del warnings[mark[2]:]
                (emitter.x, emitter.z, emitter.feed_value, emitter.radius_mode, emitter.feed_mode,
                 spindle, coolant) = mark[3:]
                errors.append(tag + str(error))
                continue
            last_tool = op.tool
        emitter.blank()
        if coolant:
            emitter.emit("M9")
        emitter.emit("M5")
        self._goto_tool_change(emitter, x_out, z_safe)
        emitter.emit("M2")
        emitter.emit("%")
        self._chuck_check(emitter.path, dict(valid), warnings, errors)
        return Result(emitter.text(), emitter.path, outlines, warnings, errors)
