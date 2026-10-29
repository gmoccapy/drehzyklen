# Drehzyklen - lathe cycle programming for LinuxCNC (gmoccapy)
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
"""Tool table for turning tools.

Sources:
  - with a LinuxCNC configuration: tool.tbl from [EMCIO] TOOL_TABLE of the INI
  - otherwise (stand-alone): werkzeuge.json, preset on first start with ISO inserts C, D, T, V
    (left and right each), grooving tool, threading tool, drill and boring bar

Geometric model
---------------
Every tool is described in the model frame: Z to the right, X (radius) up. An outside tool sits
above its cutting tip; the insert fills an angular range (sector) between the main and the
minor cutting edge, measured from +Z counter-clockwise. Example right-hand ISO C 95° tool: main
edge 85°, minor edge 5° -> sector 5°…85°. An inside tool lies mirrored below the tip.

LinuxCNC gives the front angle I and back angle J "clockwise from a line parallel to Z+" in the
LinuxCNC view. With the tool in front of the centre line X+ points down there, so the angle
equals the model angle; with the tool behind the centre line it is mirrored. If nothing can be
derived (I = J = 0, Q = 0 or 9), the operator enters type, hand and position in the tools tab.
The entry is stored with a fingerprint of the table line and asked for again as soon as the
tool changes in the tool.tbl.

The orientation Q decides the direction of the holder (shank) as in the LinuxCNC documentation
"Lathe Tool Orientation"; tools can be written back to the tool.tbl (``write_to_table``).
"""
from __future__ import annotations

import json
import math
import os
from operator import attrgetter
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional, Tuple

from .i18n import N_, _

KIND_LABELS = {
    "C": N_("ISO C 80°"),
    "D": N_("ISO D 55°"),
    "T": N_("ISO T 60°"),
    "V": N_("ISO V 35°"),
    "W": N_("ISO W 80°"),
    "R": N_("Round insert"),
    "groove": N_("Grooving tool"),
    "thread": N_("Threading tool"),
    "drill": N_("Drill"),
    "special": N_("Special shape"),
    "unknown": N_("unknown"),
}
INSERT_ANGLES = {"C": 80.0, "D": 55.0, "T": 60.0, "V": 35.0, "W": 80.0, "thread": 60.0,
                 "groove": 6.0, "R": 90.0}
RIGHT = "right"
LEFT = "left"
NEUTRAL = "neutral"
OUTSIDE = "outside"
INSIDE = "inside"
CENTER = "center"
HANDS = (RIGHT, LEFT, NEUTRAL)
POSITIONS = (OUTSIDE, INSIDE, CENTER)
HAND_LABELS = {RIGHT: N_("right"), LEFT: N_("left"), NEUTRAL: N_("neutral")}
POSITION_LABELS = {OUTSIDE: N_("outside"), INSIDE: N_("inside"), CENTER: N_("centre")}
HAND_TEXT = {RIGHT: N_("right (cuts towards -Z)"), LEFT: N_("left (cuts towards +Z)"),
             NEUTRAL: N_("neutral")}
# values written by version 0.4 (German) -> keys
LEGACY_TOOL_VALUES = {"rechts": RIGHT, "links": LEFT, "außen": OUTSIDE, "innen": INSIDE,
                      "Mitte": CENTER, "Vorgabe": "preset", "Benutzer": "user",
                      "tool.tbl + Angabe": "tool.tbl + entry", "neu": "new"}

SOURCE_PRESET = "preset"
SOURCE_TABLE = "tool.tbl"
SOURCE_USER = "user"
SOURCE_ENTRY = "tool.tbl + entry"     # tool.tbl line plus operator entry (type, hand, ...)
NEW_SOURCE = "new"                    # created in the tools tab, not yet in the tool.tbl
SOURCE_LABELS = {SOURCE_PRESET: N_("preset"), SOURCE_TABLE: "tool.tbl",
                 SOURCE_USER: N_("user"), SOURCE_ENTRY: N_("tool.tbl + entry"),
                 NEW_SOURCE: N_("new")}

ANGLE_TOLERANCE = 0.5

# LinuxCNC orientation Q: direction of the tool centre line (shank), clockwise from Z+ as in
# the LinuxCNC documentation "Lathe Tool Orientation" (Q9 = centre, no shank)
ORIENTATION_ANGLES = {7: 0.0, 2: 45.0, 6: 90.0, 1: 135.0, 5: 180.0, 4: 225.0, 8: 270.0,
                      3: 315.0}
STANDARD_ORIENTATION = {(RIGHT, OUTSIDE): 2, (LEFT, OUTSIDE): 1, (RIGHT, INSIDE): 3,
                        (LEFT, INSIDE): 4, (NEUTRAL, OUTSIDE): 6, (NEUTRAL, INSIDE): 8}


def normalize_angle(angle: float) -> float:
    """Bring an angle into the range (-180°, 180°]."""
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
    """Is the tool sector completely inside the free range low…high (degrees, counter-clockwise)?"""
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
    hand: str = RIGHT
    position: str = OUTSIDE
    nose_radius: float = 0.4
    lead_angle: float = 95.0          # lead angle κ
    width: float = 0.0                # grooving width
    diameter: float = 0.0             # drill Ø
    comment: str = ""
    source: str = SOURCE_PRESET       # preset | tool.tbl | user | tool.tbl + entry | new
    front_angle: float = 0.0          # I from tool.tbl
    back_angle: float = 0.0           # J from tool.tbl
    orientation: int = 0              # Q from tool.tbl
    table_sector: Optional[Tuple[float, float]] = None
    needs_input: bool = False
    fingerprint: str = ""
    tip_angle: float = 0.0            # point angle of a threading tool (0 = 60°)
    shank_angle: Optional[float] = None  # shank direction in the model from Q of the tool.tbl
    entered_angles: bool = False      # special shape: I/J entered in the tools tab
    entered_axis: bool = False        # holder direction entered in the tools tab

    @property
    def angles_from_table(self) -> bool:
        """The position of the insert comes from I/J/Q of the tool.tbl (not from the operator)."""
        return self.table_sector is not None

    # ---------------------------------------------------------------- description
    @property
    def insert_angle(self) -> float:
        if self.table_sector is not None:
            return self.table_sector[1] - self.table_sector[0]
        if self.kind == "thread" and self.tip_angle > 0:
            return self.tip_angle
        return INSERT_ANGLES.get(self.kind, 80.0)

    def kind_text(self) -> str:
        if self.kind == "thread":
            return _("Threading tool {angle:g}°").format(angle=self.insert_angle)
        return _(KIND_LABELS.get(self.kind, self.kind))

    def hand_text(self) -> str:
        return _(HAND_LABELS.get(self.hand, self.hand))

    def position_text(self) -> str:
        return _(POSITION_LABELS.get(self.position, self.position))

    def source_text(self) -> str:
        return _(SOURCE_LABELS.get(self.source, self.source))

    def label(self) -> str:
        text = f"T{self.number}  {self.kind_text()}"
        if self.kind not in ("drill",):
            text += f" {self.hand_text()} {self.position_text()}"
        if self.kind == "drill":
            text += f" Ø{self.diameter:g}"
        elif self.kind == "groove":
            text += f" b{self.width:g}"
        else:
            text += f" r{self.nose_radius:g}"
        if self.needs_input:
            text += "  ⚠ " + _("input missing")
        if self.source == NEW_SOURCE:
            text += "  " + _("(not yet in tool.tbl)")
        return text

    # ---------------------------------------------------------------- geometry
    def sector(self) -> Optional[Tuple[float, float]]:
        """Angular range of the insert in the model frame (degrees), None for drills."""
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
            if self.hand == LEFT:
                low, high = 180.0 - high, 180.0 - low
        if self.position == INSIDE:
            low, high = -high, -low
        return low, high

    def shank_direction(self) -> Optional[float]:
        """Direction of the holder in the model frame (degrees): from Q, else the bisector."""
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

    # ---------------------------------------------------------------- storing
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
        for key in ("hand", "position", "source"):
            if key in clean:
                clean[key] = LEGACY_TOOL_VALUES.get(clean[key], clean[key])
        return cls(**clean)


def preset_tools() -> List[Tool]:
    """Preset tools for stand-alone use."""
    tools = []
    number = 1
    for kind, lead_angle, radius, name in (("C", 95.0, 0.8, "CNMG 120408"),
                                           ("D", 93.0, 0.4, "DNMG 150604"),
                                           ("T", 91.0, 0.4, "TNMG 160404"),
                                           ("V", 93.0, 0.4, "VBMT 160404")):
        for hand in (RIGHT, LEFT):
            tools.append(Tool(number, number, kind, hand, OUTSIDE, radius, lead_angle,
                              comment=f"{name} {hand}"))
            number += 1
    tools.append(Tool(number, number, "groove", NEUTRAL, OUTSIDE, 0.2, 90.0, width=3.0,
                      comment="Grooving tool 3 mm"))
    number += 1
    tools.append(Tool(number, number, "thread", NEUTRAL, OUTSIDE, 0.1, 90.0,
                      comment="Threading tool 60° outside"))
    number += 1
    tools.append(Tool(number, number, "drill", NEUTRAL, CENTER, 0.0, 118.0, diameter=10.0,
                      comment="Drill Ø10"))
    number += 1
    tools.append(Tool(number, number, "D", RIGHT, INSIDE, 0.4, 93.0,
                      comment="Boring bar DNMG right"))
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
    """Angle of the tool.tbl (clockwise from Z+, LinuxCNC view) -> model angle."""
    if back_tool:
        angle = -table_angle
    else:
        angle = table_angle
    if mirror_z:
        angle = 180.0 - angle
    return angle


def table_angles(tool: Tool, back_tool: bool, mirror_z: bool = False):
    """Front angle I, back angle J and orientation Q for the tool.tbl.

    The front angle belongs to the main cutting edge: for a right-hand tool the edge towards -Z,
    for a left-hand tool the edge towards +Z. For a neutral tool (threading, grooving) the
    larger angle is the front angle (e.g. I117.5 J62.5).

    The angles are written as positive values (0…360°) where possible. If the insert covers
    the direction Z+ (0°), the smaller angle becomes negative (like Q7 in the LinuxCNC
    documentation). A tool read from the tool.tbl keeps its I and J unchanged.
    """
    sector = tool.sector()
    if sector is None:
        return None
    if tool.orientation in ORIENTATION_ANGLES and (tool.angles_from_table or tool.entered_axis):
        orientation = tool.orientation
    else:
        # control point as in the LinuxCNC documentation: right outside 2, left outside 1,
        # right inside 3, left inside 4, neutral outside 6, neutral inside 8
        orientation = STANDARD_ORIENTATION.get((tool.hand, tool.position), 6)
    if tool.angles_from_table and not tool.entered_angles:
        return tool.front_angle, tool.back_angle, orientation
    if tool.entered_angles:
        return tool.front_angle, tool.back_angle, orientation
    # main cutting edge in the model: the edge pointing most towards the cutting direction
    edge_one, edge_two = sector
    if tool.hand == RIGHT:
        if math.cos(math.radians(edge_one)) < math.cos(math.radians(edge_two)):
            main_edge, other_edge = edge_one, edge_two
        else:
            main_edge, other_edge = edge_two, edge_one
    elif tool.hand == LEFT:
        if math.cos(math.radians(edge_one)) > math.cos(math.radians(edge_two)):
            main_edge, other_edge = edge_one, edge_two
        else:
            main_edge, other_edge = edge_two, edge_one
    else:
        main_edge, other_edge = edge_one, edge_two
    # model -> table is the same (self-inverse) mapping as table -> model
    front = model_angle(main_edge, back_tool, mirror_z)
    back = model_angle(other_edge, back_tool, mirror_z)
    shift = 0.0
    while min(front, back) + shift < 0:
        shift += 360.0
    while min(front, back) + shift >= 360.0:
        shift -= 360.0
    if max(front, back) + shift > 360.0:
        shift -= 360.0
    front += shift
    back += shift
    if tool.hand not in (RIGHT, LEFT) and front < back:
        front, back = back, front
    return round(front, 3), round(back, 3), orientation


def write_table_line(path: str, tool: Tool, front: float, back: float, orientation: int):
    """Add the tool to the tool.tbl or change its existing line.

    Only D, I, J, Q and the comment are changed - X, Z and all other values stay.
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
    """Let a running LinuxCNC reload the changed tool.tbl."""
    try:
        import linuxcnc
    except ImportError:
        return ""
    try:
        status = linuxcnc.stat()
        status.poll()
        linuxcnc.command().load_tool_table()
    except Exception as error:  # noqa: BLE001 - LinuxCNC is not running or refuses
        return _("tool.tbl written, LinuxCNC did not reload it ({error})").format(error=error)
    return ""


def format_table_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text


def tool_from_table(entry: dict, back_tool: bool, mirror_z: bool = False) -> Tool:
    """Derive a tool from a tool.tbl line."""
    tool = Tool(entry["number"], entry["pocket"], "unknown", NEUTRAL, OUTSIDE,
                entry["diameter"] / 2, 90.0, comment=entry["comment"], source=SOURCE_TABLE,
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
    tool.table_sector = sector_from_table_angles(front, back, back_tool, mirror_z)
    derive_hand_position(tool)
    width = tool.table_sector[1] - tool.table_sector[0]
    tool.kind = "special"
    if tool.hand == NEUTRAL and (abs(width - 60.0) < 3.0 or abs(width - 55.0) < 3.0):
        # symmetric tip with 55° (Whitworth) or 60° (metric, UNC/UNF)
        tool.kind = "thread"
        tool.tip_angle = round(width, 1)
    elif tool.hand == NEUTRAL and width < 12.0:
        tool.kind = "groove"
    else:
        # nearest insert angle (60° is T, not D with 55°)
        best_difference = 6.0
        for kind in ("C", "D", "T", "V"):
            difference = abs(width - INSERT_ANGLES[kind])
            if difference < best_difference:
                best_difference = difference
                tool.kind = kind
    derive_lead_angle(tool)
    return tool


def sector_from_table_angles(front: float, back: float, back_tool: bool,
                             mirror_z: bool = False) -> Tuple[float, float]:
    """Insert sector in the model frame from front angle I and back angle J of the tool.tbl."""
    if back_tool:
        edge_one, edge_two = -front, -back
    else:
        edge_one, edge_two = front, back
    if mirror_z:
        # operator option in case the angles of the own tool.tbl are meant the other way round
        edge_one, edge_two = 180.0 - edge_one, 180.0 - edge_two
    difference = (edge_two - edge_one) % 360.0
    if difference <= 180.0:
        low = edge_one
        width = difference
    else:
        low = edge_two
        width = 360.0 - difference
    low = normalize_angle(low)
    return low, low + width


def derive_hand_position(tool: Tool):
    """Hand and position (outside/inside) from the insert sector of the tool."""
    low, high = tool.table_sector
    bisector = math.radians((low + high) / 2)
    if math.sin(bisector) >= 0:
        tool.position = OUTSIDE
        bisector_outside = bisector
    else:
        tool.position = INSIDE
        bisector_outside = -bisector
    if math.cos(bisector_outside) > 0.17:
        tool.hand = RIGHT
    elif math.cos(bisector_outside) < -0.17:
        tool.hand = LEFT
    else:
        tool.hand = NEUTRAL


def derive_lead_angle(tool: Tool):
    """Lead angle κ from the insert sector (main edge = edge on the cutting side)."""
    if tool.position == OUTSIDE:
        outside_low, outside_high = tool.table_sector
    else:
        outside_low, outside_high = -tool.table_sector[1], -tool.table_sector[0]
    if tool.hand == LEFT:
        tool.lead_angle = round(outside_low, 1)
    else:
        tool.lead_angle = round(180.0 - outside_high, 1)


def nearest_orientation(model_shank: float, back_tool: bool, mirror_z: bool = False) -> int:
    """Orientation Q of the tool.tbl whose direction comes closest to the tool axis."""
    table_shank = model_angle(model_shank, back_tool, mirror_z)
    best_orientation = 6
    best_difference = None
    for orientation, angle in ORIENTATION_ANGLES.items():
        difference = abs(normalize_angle(table_shank - angle))
        if best_difference is None or difference < best_difference:
            best_difference = difference
            best_orientation = orientation
    return best_orientation


# inserts whose position is given by the lead angle κ and the insert angle ε
LEAD_ANGLE_KINDS = ("C", "D", "T", "V", "W")


def lead_angle_problem(kind: str, lead_angle: float) -> str:
    """Check the lead angle κ of an insert: the minor cutting edge lies at 180° - κ - ε from the
    surface; below 0° it would rub on the part (e.g. ISO C 80° with κ 105° gives -5°).
    Returns an error text or "" if the value is possible."""
    if kind not in LEAD_ANGLE_KINDS:
        return ""
    insert_angle = INSERT_ANGLES[kind]
    if lead_angle <= 0.0 or lead_angle >= 180.0:
        return _("Lead angle κ must be between 0° and 180°.")
    clearance = 180.0 - lead_angle - insert_angle
    if clearance < 0.0:
        return _("κ {lead:g}° + ε {insert:g}° is more than 180°: the minor cutting edge would rub "
                 "on the part ({clearance:g}°). With this insert κ may be at most "
                 "{maximum:g}°.").format(lead=lead_angle, insert=insert_angle,
                                         clearance=clearance, maximum=180.0 - insert_angle)
    return ""


def set_tool_axis(tool: Tool, axis: Optional[float], back_tool: bool, mirror_z: bool = False):
    """Direction of the holder (tool.tbl convention, clockwise from Z+); None = bisector of the
    insert. The holder may stand at any angle to the insert, e.g. an ISO C insert with κ 95° in
    a holder at 90°."""
    if axis is None:
        tool.shank_angle = None
        tool.entered_axis = False
        return
    tool.shank_angle = normalize_angle(model_angle(axis, back_tool, mirror_z))
    tool.entered_axis = True
    tool.orientation = nearest_orientation(tool.shank_angle, back_tool, mirror_z)


def set_special_angles(tool: Tool, front: float, back: float, axis: Optional[float],
                       back_tool: bool, mirror_z: bool = False):
    """Special shape: insert from front angle I and back angle J (tool.tbl convention).

    axis is the direction of the tool axis (shank) in the same convention; None = bisector.
    The insert may sit asymmetrically to the axis, e.g. I120 J70 (ε 50°) mounted at 80°.
    """
    tool.table_sector = sector_from_table_angles(front, back, back_tool, mirror_z)
    tool.entered_angles = True
    tool.front_angle = front
    tool.back_angle = back
    derive_hand_position(tool)
    derive_lead_angle(tool)
    if axis is None:
        tool.shank_angle = None
        model_shank = (tool.table_sector[0] + tool.table_sector[1]) / 2
    else:
        tool.shank_angle = normalize_angle(model_angle(axis, back_tool, mirror_z))
        model_shank = tool.shank_angle
    tool.orientation = nearest_orientation(model_shank, back_tool, mirror_z)


def clear_special_angles(tool: Tool):
    if tool.entered_angles:
        tool.table_sector = None
        tool.entered_angles = False
        tool.shank_angle = None


class ToolTable:
    """All tools, stored according to their source."""

    def __init__(self, machine, data_path: str):
        self.machine = machine
        self.data_path = data_path
        self.tools: Dict[int, Tool] = {}
        self.from_linuxcnc = False
        self.mirror_table_angles = bool(self._stored().get("mirror_table_angles", False))
        # status text as (English text, values): translated when shown, so that it follows a
        # language switch
        self.message_parts: List[Tuple[str, dict]] = []
        self.loaded_stamp = None
        self.load()

    # ---------------------------------------------------------------- status text
    def set_message(self, text: str, **values):
        self.message_parts = [(text, values)]

    def add_message(self, text: str, **values):
        self.message_parts.append((text, values))

    @property
    def message(self) -> str:
        """Status text in the active language."""
        return " - ".join(_(text).format(**values) for text, values in self.message_parts)

    # ---------------------------------------------------------------- loading
    def table_path(self) -> str:
        return getattr(self.machine, "tool_table", "")

    def table_stamp(self):
        """Modification time and size of the tool.tbl (None without LinuxCNC table)."""
        table_path = self.table_path()
        if not table_path:
            return None
        try:
            info = os.stat(table_path)
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def changed_on_disk(self) -> bool:
        """Was the tool.tbl changed since it was read (e.g. a tool added in LinuxCNC)?"""
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
            self.set_message(N_("Tools from {file}"), file=os.path.basename(self.data_path))
        else:
            for tool in preset_tools():
                self.tools[tool.number] = tool
            self.set_message(N_("Preset tools (ISO C, D, T, V left/right, grooving, threading, "
                                "drill, boring bar)"))

    def _load_tbl(self, path: str):
        assignments = self._stored().get("assignments", {})
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
        except OSError as error:
            self.set_message(N_("tool.tbl cannot be read: {error}"), error=error)
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
        self.set_message(N_("{count} tools from {path}"), count=len(self.tools), path=path)
        if pending:
            numbers = ", ".join(f"T{tool.number}" for tool in pending)
            self.add_message(N_("input missing for {numbers}"), numbers=numbers)

    def table_axis(self, tool: Tool) -> Optional[float]:
        """Tool axis of an entered special shape in tool.tbl convention (None = bisector)."""
        if tool.shank_angle is None:
            return None
        return round(normalize_angle(model_angle(tool.shank_angle, self.machine.back_tool,
                                                 self.mirror_table_angles)), 3)

    def _apply_assignment(self, tool: Tool, stored: dict):
        migrated = {}
        for key, value in stored.items():
            if isinstance(value, str):
                migrated[key] = LEGACY_TOOL_VALUES.get(value, value)
            else:
                migrated[key] = value
        stored = migrated
        tool.kind = stored.get("kind", tool.kind)
        tool.width = float(stored.get("width", tool.width))
        tool.diameter = float(stored.get("diameter", tool.diameter))
        tool.tip_angle = float(stored.get("tip_angle", tool.tip_angle))
        special_angles = stored.get("special_angles")
        if tool.kind == "special" and special_angles and not tool.angles_from_table:
            front, back, axis = special_angles
            set_special_angles(tool, front, back, axis, self.machine.back_tool,
                               self.mirror_table_angles)
        elif not tool.angles_from_table:
            tool.hand = stored.get("hand", tool.hand)
            tool.position = stored.get("position", tool.position)
            tool.lead_angle = float(stored.get("lead_angle", tool.lead_angle))
        if stored.get("shank_angle") is not None:
            tool.shank_angle = float(stored["shank_angle"])
            if tool.kind != "special":
                tool.entered_axis = True
        tool.needs_input = False
        tool.source = SOURCE_ENTRY

    # ---------------------------------------------------------------- storing
    def save(self):
        data = self._stored()
        data["mirror_table_angles"] = self.mirror_table_angles
        if self.from_linuxcnc:
            assignments = data.get("assignments", {})
            for tool in self.tools.values():
                if tool.source == SOURCE_ENTRY:
                    assignments[str(tool.number)] = {
                        "fingerprint": tool.fingerprint, "kind": tool.kind, "hand": tool.hand,
                        "position": tool.position, "lead_angle": tool.lead_angle,
                        "width": tool.width, "diameter": tool.diameter,
                        "tip_angle": tool.tip_angle}
                    if tool.entered_angles:
                        assignments[str(tool.number)]["special_angles"] = [
                            tool.front_angle, tool.back_angle, self.table_axis(tool)]
                    elif tool.entered_axis or (tool.kind == "special"
                                               and tool.shank_angle is not None):
                        assignments[str(tool.number)]["shank_angle"] = tool.shank_angle
            data["assignments"] = assignments
        else:
            data["tools"] = [tool.to_dict() for tool in sorted(self.tools.values(),
                                                                key=attrgetter("number"))]
        try:
            os.makedirs(os.path.dirname(self.data_path) or ".", exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
        except OSError as error:
            self.set_message(N_("Tools not saved: {error}"), error=error)

    # ---------------------------------------------------------------- access
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
               tip_angle: float = 0.0, special_angles=None, axis=None):
        """Take over the operator's entry.

        With a tool.tbl only type, hand, position and angles. If the table gives the position of
        the insert (I/J/Q), it is kept - only the classification (type) changes, e.g. a 55° tip
        as threading tool instead of ISO D.
        """
        tool.kind = kind
        if kind != "special":
            clear_special_angles(tool)
        geometry_free = not tool.angles_from_table or tool.entered_angles
        if kind == "special" and special_angles is not None and geometry_free:
            front, back, axis = special_angles
            set_special_angles(tool, front, back, axis, self.machine.back_tool,
                               self.mirror_table_angles)
        elif not tool.angles_from_table:
            tool.hand = hand
            tool.position = position
            tool.lead_angle = lead_angle
            tool.tip_angle = tip_angle
        if kind != "special" and (not tool.angles_from_table or tool.entered_axis):
            set_tool_axis(tool, axis, self.machine.back_tool, self.mirror_table_angles)
        tool.width = width
        tool.diameter = diameter
        tool.needs_input = False
        if self.from_linuxcnc and tool.source != NEW_SOURCE:
            tool.source = SOURCE_ENTRY
        else:
            tool.nose_radius = nose_radius
            tool.comment = comment
            if not self.from_linuxcnc:
                tool.source = SOURCE_USER
        self.save()

    def write_to_table(self, tool: Tool) -> str:
        """Write the tool into the tool.tbl, notify LinuxCNC and read the table again.

        Returns a message (empty = everything fine).
        """
        table_path = self.table_path()
        if not table_path or not os.path.isfile(table_path):
            return _("No tool.tbl known from the LinuxCNC INI")
        angles = table_angles(tool, self.machine.back_tool, self.mirror_table_angles)
        if angles is None:
            return _("T{number}: type, hand and position are missing - nothing written").format(
                number=tool.number)
        front, back, orientation = angles
        if tool.entered_angles:
            # special shape: write I and J exactly as entered
            front, back = tool.front_angle, tool.back_angle
        kind, width, diameter, tip_angle = tool.kind, tool.width, tool.diameter, tool.tip_angle
        shank_angle = tool.shank_angle
        try:
            write_table_line(table_path, tool, front, back, orientation)
        except OSError as error:
            return _("tool.tbl cannot be written: {error}").format(error=error)
        notice = reload_linuxcnc_tool_table()
        self.load()
        written = self.tools.get(tool.number)
        if written is not None and (written.kind != kind or kind in ("groove", "thread",
                                                                     "special")
                                    or tool.entered_axis):
            # keep the classification (e.g. grooving width), the angles are now in the table
            written.kind = kind
            written.width = width
            written.diameter = diameter
            written.tip_angle = tip_angle
            if shank_angle is not None and (kind == "special" or tool.entered_axis):
                # LinuxCNC only knows Q in steps of 45° - keep the exact tool axis
                written.shank_angle = shank_angle
            written.needs_input = False
            written.source = SOURCE_ENTRY
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
        """Mirror the angles of the tool.tbl in Z and read the table again."""
        self.mirror_table_angles = mirror
        self.save()
        self.load()

    def next_number(self) -> int:
        if not self.tools:
            return 1
        return max(self.tools) + 1
