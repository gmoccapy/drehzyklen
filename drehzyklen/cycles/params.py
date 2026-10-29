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
"""Parameter descriptions shared by all cycles.

A cycle lists its input fields as ``P`` objects. The form in the GUI is built from them, the
values are stored in the project file under ``key``.

Choice fields store a language independent key (e.g. ``"chamfer"``); ``labels`` maps the key to
the English text shown in the form, which is translated when displayed. Projects written by
older versions stored the German text; ``LEGACY_VALUES`` maps those to the keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..i18n import N_


@dataclass
class P:
    key: str
    label: str
    default: Any
    # kind: float | optfloat | int | choice | bool | text | contour | side | tool
    kind: str = "float"
    choices: Tuple[str, ...] = ()
    unit: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""
    sym: str = ""                 # short symbol used in the parameter picture
    label_in: Optional[str] = None  # different label for inside machining
    show: Optional[str] = None    # "out"/"in": only shown for outside or inside machining
    skey: bool = False            # prefilled with the stock diameter (outside D, inside d)
    when: Optional[Tuple[str, Tuple[str, ...]]] = None  # only shown if a choice has one of the keys
    label_by: Optional[Tuple[str, Dict[str, str]]] = None  # label depends on a choice field
    unit_by: Optional[Tuple[str, Dict[str, str]]] = None   # unit depends on a choice field
    labels: Dict[str, str] = field(default_factory=dict)   # choice key -> English display text

    def choice_label(self, key: str) -> str:
        """English display text of a choice key (translate with _() before showing it)."""
        return self.labels.get(key, key)


# ------------------------------------------------------------------ choice keys

OUTSIDE = "outside"
INSIDE = "inside"
SIDE_LABELS = {OUTSIDE: N_("Outside"), INSIDE: N_("Inside")}
SIDE = P("side", N_("Machining"), OUTSIDE, "side", (OUTSIDE, INSIDE), labels=SIDE_LABELS)

SPEED_FEED_MODES = ("G96_G95", "G97_G95", "G96_G94", "G97_G94")
SPEED_FEED_LABELS = {"G96_G95": N_("G96 Vc · G95 mm/rev"), "G97_G95": N_("G97 n · G95 mm/rev"),
                     "G96_G94": N_("G96 Vc · G94 mm/min"), "G97_G94": N_("G97 n · G94 mm/min")}

CORNER_NONE = "none"
CORNER_CHAMFER = "chamfer"
CORNER_RADIUS = "radius"
CORNER_KINDS = (CORNER_NONE, CORNER_CHAMFER, CORNER_RADIUS)
CORNER_LABELS = {CORNER_NONE: N_("none"), CORNER_CHAMFER: N_("Chamfer"),
                 CORNER_RADIUS: N_("Radius")}

COMPENSATION = ("off", "auto", "G41", "G42")
COMPENSATION_LABELS = {"off": N_("off"), "auto": N_("auto")}

# German texts stored by versions up to 0.4 -> keys
LEGACY_VALUES = {
    "Außen": OUTSIDE, "Innen": INSIDE,
    "G96 Vc · G95 mm/U": "G96_G95", "G97 n · G95 mm/U": "G97_G95",
    "G96 Vc · G94 mm/min": "G96_G94", "G97 n · G94 mm/min": "G97_G94",
    "keine": CORNER_NONE, "Fase": CORNER_CHAMFER, "Radius": CORNER_RADIUS,
    "aus": "off",
    "Enddurchmesser": "diameter", "Winkel": "angle",
    "Vorderkante": "front", "Rückkante": "back", "Futter": "chuck",
    "Spitze": "tip", "Voller Ø": "full",
    "Durchgehend": "through", "Spanbruch": "chip_break", "Entspanen": "peck",
    "frei (eigene Werte)": "free", "Metrisch Regelgewinde": "M", "Metrisch Feingewinde": "MF",
    "UNC (Zoll, grob)": "UNC", "UNF (Zoll, fein)": "UNF", "Whitworth BSW (55°)": "BSW",
    "Rohrgewinde G / BSP (55°)": "G",
}


def spindle_mode_of(mode: str) -> str:
    """G96 (constant surface speed) or G97 (fixed rpm) from a speed/feed mode key."""
    if str(mode).startswith("G97"):
        return "G97"
    return "G96"


def feed_mode_of(mode: str) -> str:
    """G94 (mm/min) or G95 (mm/rev) from a speed/feed mode key."""
    if "G94" in str(mode):
        return "G94"
    return "G95"


SPEED_LABELS = {}
FEED_UNITS = {}
for _mode in SPEED_FEED_MODES:
    if spindle_mode_of(_mode) == "G96":
        SPEED_LABELS[_mode] = N_("Cutting speed Vc [m/min]")
    else:
        SPEED_LABELS[_mode] = N_("Spindle speed n [1/min]")
    if feed_mode_of(_mode) == "G94":
        FEED_UNITS[_mode] = "mm/min"
    else:
        FEED_UNITS[_mode] = N_("mm/rev")


# ------------------------------------------------------------------ shared parameter groups

TOOL_PARAMS = [
    P("tool", N_("Tool"), 1, "tool", min=0, max=9999, sym="T"),
    P("tool_ok", N_("Release tool despite warning"), False, "bool",
      help=N_("Lifts the block of the tool check (e.g. form or special threads)")),
]

SPINDLE_DIR = P("spindle_dir", N_("Spindle direction"), "auto", "choice", ("auto", "M3", "M4"),
                labels={"auto": N_("auto")},
                help=N_("auto: tool in front of the centre line M3, behind it M4 (settings)"))
COOLANT = P("coolant", N_("Coolant M8"), True, "bool")

TECH = TOOL_PARAMS + [
    P("speed_feed", N_("Speed/feed"), SPEED_FEED_MODES[0], "choice", SPEED_FEED_MODES,
      labels=SPEED_FEED_LABELS,
      help=N_("G96 constant cutting speed, G97 fixed spindle speed; G95 feed per revolution, "
              "G94 feed per minute")),
    P("speed", N_("Speed"), 180.0, min=1, sym="S", label_by=("speed_feed", SPEED_LABELS),
      help=N_("Compared with the machine limits and reduced if necessary")),
    SPINDLE_DIR,
    COOLANT,
]

TECH_THREAD = TOOL_PARAMS + [
    P("speed", N_("Spindle speed n"), 600.0, unit="1/min", min=1, sym="S",
      help=N_("Limited by the machine limits and the Z velocity")),
    SPINDLE_DIR,
    COOLANT,
]


def feed_param(key, label, default, sym):
    """Feed rate field whose unit follows the speed/feed mode (mm/rev or mm/min)."""
    return P(key, label, default, min=0.001, sym=sym, unit_by=("speed_feed", FEED_UNITS))


ROUGH = [
    P("ap", N_("Depth of cut (radial)"), 1.5, unit="mm", min=0.01, sym="ap"),
    feed_param("f_rough", N_("Feed roughing"), 0.25, "F"),
    feed_param("f_finish", N_("Feed finishing"), 0.1, "Fs"),
    P("allow_x", N_("Finishing allowance X (Ø)"), 0.4, unit="mm", min=0, sym="aX"),
    P("allow_z", N_("Finishing allowance Z"), 0.1, unit="mm", min=0, sym="aZ"),
    P("do_rough", N_("Roughing"), True, "bool"),
    P("do_finish", N_("Finishing"), True, "bool"),
    P("comp", N_("Tool nose radius compensation"), "auto", "choice", COMPENSATION,
      labels=COMPENSATION_LABELS,
      help=N_("Finishing pass only; auto chooses the side from the machining direction. Needs "
              "a tool table with radius and orientation.")),
]

# Values suggested again for the next cycle of the same kind. Not included: geometry and
# "release tool despite warning" (must be set deliberately every time).
CUTTING_DATA_KEYS = {"tool", "speed_feed", "speed", "spindle_dir", "coolant",
                     "ap", "allow_x", "allow_z", "do_rough", "do_finish", "comp",
                     "peck", "break_back", "dwell",
                     "first_cut", "degression", "infeed", "spring", "thread_clear"}


def migrate_values(params, values: Dict[str, Any]) -> Dict[str, Any]:
    """Map choice values of older projects (German texts) to the current keys."""
    result = dict(values)
    for param in params:
        if param.kind not in ("choice", "side") or param.key not in result:
            continue
        value = result[param.key]
        if value in param.choices:
            continue
        legacy = LEGACY_VALUES.get(value)
        if legacy is not None and (legacy in param.choices or not param.choices):
            result[param.key] = legacy
    return result
