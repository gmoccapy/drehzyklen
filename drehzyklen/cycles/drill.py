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
"""Drilling on the centre line with a drill from the tool table.

Tool path
---------
1. Rapid to X0 and to Z1 + safety distance.
2. Depending on the mode
     through     one feed move to the tip depth,
     chip break  feed by Q, retract by Rb, next step,
     peck        feed by Q, rapid out to the start, rapid back just above the last depth.
3. Dwell P at the bottom, rapid out to Z1 + safety distance.

The depth refers to the tip or, with "full Ø", to the end of the cylindrical part (the tip then
goes deeper by (D/2) / tan(σ/2)).
"""
from __future__ import annotations

import math

from ..gcode import fmt
from ..geometry import ContourError, Element, polyline
from ..i18n import N_, _
from .base import Cycle
from .params import P, feed_param
from .toolcheck import tool_name

DEPTH_TIP = "tip"
DEPTH_FULL = "full"
MODE_THROUGH = "through"
MODE_CHIP_BREAK = "chip_break"
MODE_PECK = "peck"
MODE_LABELS = {MODE_THROUGH: N_("Straight"), MODE_CHIP_BREAK: N_("Chip breaking"),
               MODE_PECK: N_("Peck drilling")}


class Drill(Cycle):
    KEY = "drill"
    TITLE = N_("Drilling")
    SHORT = N_("Drill")
    HAS_SIDE = False
    RENAMES = {"drill_d": "diameter"}
    PARAMS = [
        P("diameter", N_("Drill Ø"), 10.0, unit="mm", min=0.1, sym="D"),
        P("tip_angle", N_("Point angle"), 118.0, unit="°", min=60, max=180, sym="σ"),
        P("z_start", N_("Z start (surface)"), 0.0, unit="mm", sym="Z1"),
        P("z_end", N_("Z end (drilling depth)"), -20.0, unit="mm", sym="Z2"),
        P("depth_ref", N_("Depth refers to"), DEPTH_TIP, "choice", (DEPTH_TIP, DEPTH_FULL),
          labels={DEPTH_TIP: N_("Tip"), DEPTH_FULL: N_("Full Ø")},
          help=N_("Full Ø: Z2 is the end of the cylindrical bore, the tip goes deeper")),
        P("mode", N_("Drilling mode"), MODE_CHIP_BREAK, "choice",
          (MODE_THROUGH, MODE_CHIP_BREAK, MODE_PECK), labels=MODE_LABELS,
          help=N_("Chip breaking: short retract after every step. Peck drilling: all the way "
                  "out")),
        P("peck", N_("Step depth"), 5.0, unit="mm", min=0.1, sym="Q",
          when=("mode", (MODE_CHIP_BREAK, MODE_PECK))),
        P("break_back", N_("Chip breaking retract"), 0.5, unit="mm", min=0.05, sym="Rb",
          when=("mode", (MODE_CHIP_BREAK,))),
        P("dwell", N_("Dwell at the bottom"), 0.0, unit="s", min=0, sym="P"),
        feed_param("f", N_("Feed"), 0.1, "F"),
    ]

    def __init__(self, values=None):
        if values is None:
            values = {}
        super().__init__(values)
        if "speed" not in values:
            self.v["speed"] = 25.0          # cutting speed for drills

    @property
    def inside(self):
        return True

    def z_sign(self):
        return 1

    def summary(self):
        return (f"Ø{fmt(self.diameter)} Z{fmt(self.z_start)}→{fmt(self.z_end)} "
                f"({_(MODE_LABELS.get(self.mode, self.mode))})")

    def max_d(self):
        return self.diameter

    def inner_x(self, machine):
        return 0.0

    def tip_length(self):
        return (self.diameter / 2) / math.tan(math.radians(self.tip_angle / 2))

    def tip_z(self):
        if self.depth_ref == DEPTH_TIP:
            return self.z_end
        return self.z_end - self.tip_length()

    def shoulder_z(self):
        return self.tip_z() + self.tip_length()

    def z_range(self):
        return self.tip_z(), self.z_start

    def check(self, machine):
        if self.z_end >= self.z_start:
            raise ContourError(_("Drilling only works towards -Z: Z end must be smaller than "
                                 "Z start"))
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
            return [_("{tool}: drilling needs a drill").format(tool=tool_name(tool))]
        if tool.diameter and abs(tool.diameter - self.diameter) > 0.01:
            return [_("T{number}: drill Ø {tool_diameter:g} does not fit D {diameter:g}").format(
                number=tool.number, tool_diameter=tool.diameter, diameter=self.diameter)]
        return []

    def body(self, emitter, machine, material):
        clearance = machine.clearance
        top = self.z_start + clearance
        tip = self.tip_z()
        emitter.rapid(x=0.0)
        emitter.rapid(z=top)
        if self.mode == MODE_THROUGH:
            emitter.feed(z=tip, f=self.f)
        else:
            depth = self.z_start
            while depth > tip + 1e-6:
                next_depth = max(depth - self.peck, tip)
                if self.mode == MODE_CHIP_BREAK:
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
