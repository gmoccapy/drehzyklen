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
"""Base class of all cycles.

Common naming in all cycles:
    D  = diameter at the start      d  = diameter at the end
    Z1 = Z start                    Z2 = Z end
The values decide the machining direction: Z1 > Z2 machines towards -Z (right to left),
Z1 < Z2 towards +Z (left to right, e.g. back faces with a left-hand tool).

A cycle object only holds its values (``self.v``). What it does is split into small methods
that the program generator (``program.py``) calls in this order:

    check()          plausibility of the values, returns hints, raises ContourError
    outline()        finished contour for the preview
    tool_problems()  does the selected tool fit? (see ``toolcheck.py``)
    body()           writes the moves of the cycle into the emitter
    material_cuts()  what the cycle removes (material simulation, see ``material.py``)
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ..gcode import Emitter
from ..geometry import Element
from ..i18n import _
from ..machine import Machine
from ..material import Material
from .params import (CUTTING_DATA_KEYS, INSIDE, SIDE, SPEED_FEED_MODES, TECH, P, feed_mode_of,
                     migrate_values, spindle_mode_of)


class Cycle:
    KEY = ""
    TITLE = ""                          # English, translated with title()
    SHORT = ""                          # short name for the tab bar, translated with short()
    PARAMS: List[P] = []
    TECH_PARAMS = TECH
    HAS_SIDE = True
    HAS_TOOL = True
    FORCE_G97 = False
    RENAMES: Dict[str, str] = {}        # keys of older projects -> current keys

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self.v: Dict[str, Any] = {}
        params = self.all_params()
        for param in params:
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
            values = migrate_values(params, values)
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

    @classmethod
    def title(cls) -> str:
        return _(cls.TITLE)

    @classmethod
    def short(cls) -> str:
        return _(cls.SHORT)

    def __getattr__(self, item):
        values = self.__dict__.get("v")
        if values is not None and item in values:
            return values[item]
        raise AttributeError(item)

    @classmethod
    def cutting_keys(cls) -> List[str]:
        """Cutting data kept between cycles and sessions (no geometry)."""
        keys = []
        for param in cls.all_params():
            if param.key in CUTTING_DATA_KEYS:
                keys.append(param.key)
            elif param.unit_by is not None:      # feed rates
                keys.append(param.key)
        return keys

    # ---------------------------------------------------------------- side and direction
    @property
    def inside(self) -> bool:
        return self.v.get("side") == INSIDE

    @property
    def radius_sign(self) -> int:
        """-1 for inside machining: the work frame is mirrored at the spindle axis."""
        if self.inside:
            return -1
        return 1

    def z_sign(self) -> int:
        """+1: machining towards -Z (default), -1: towards +Z."""
        if self.v.get("z_end", 0.0) > self.v.get("z_start", 0.0):
            return -1
        return 1

    @property
    def side_text(self) -> str:
        if self.inside:
            return _("inside")
        return _("outside")

    def direction_text(self) -> str:
        if self.z_sign() < 0:
            return " →+Z"
        return ""

    def to_dict(self):
        return {"cycle": self.KEY, "values": dict(self.v)}

    def help_values(self) -> Dict[str, Any]:
        """Values for the parameter picture (including derived values)."""
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
        """A field changed: return dependent values that must be updated in the form.

        Here: switching between G96/G97 and G94/G95 converts speed and feed rates.
        """
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

    # ---------------------------------------------------------------- technology
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

    # ---------------------------------------------------------------- implemented by cycles
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
