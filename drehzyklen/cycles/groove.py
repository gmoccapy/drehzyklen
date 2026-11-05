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
"""Radial groove (outside or inside) with a grooving tool.

Tool path
---------
The groove lies between the flanks Z1 and Z2, from the surface Ø D down to the groove bottom
Ø d (outside d < D, inside d > D).

1. From the retract plane rapid along Z to the first plunge position (the flank towards the
   front, +Z), then down to the safety distance above the surface. Inside: first to the
   safety distance in the bore (in front of the part), then along Z into the bore.
2. Plunge radially to the bottom - in one move, or with the step depth Q and a short retract Rb
   after every step (chip breaking). Dwell P at the bottom, rapid out.
3. Next plunge, shifted by 80 % of the tool width towards -Z; the last plunge lies exactly at
   the other flank. A groove as wide as the tool needs one plunge.

The programmed Z is the reference point of the tool (tools tab: left corner, centre or right
corner - where the tool was touched off). The tool width b comes from the tool table.
"""
from __future__ import annotations

from typing import List

from ..gcode import fmt
from ..geometry import ContourError, Element
from ..i18n import N_, _
from .base import Cycle
from .params import P, feed_param
from .toolcheck import tool_name

STEP_SHARE = 0.8              # shift between two plunges as share of the tool width


class Groove(Cycle):
    KEY = "groove"
    START_KEYS = ("d_start", "z_start")
    TITLE = N_("Grooving")
    SHORT = N_("Groove")
    PARAMS = [
        P("d_start", N_("Ø surface"), 50.0, unit="mm", min=0, sym="D", skey=True),
        P("d_end", N_("Ø groove bottom"), 44.0, unit="mm", min=0, sym="d",
          help=N_("Outside d < D, inside d > D")),
        P("z_start", N_("Z flank 1"), -10.0, unit="mm", sym="Z1"),
        P("z_end", N_("Z flank 2"), -16.0, unit="mm", sym="Z2",
          help=N_("The groove lies between Z1 and Z2; wider than the tool: several plunges")),
        P("peck", N_("Step depth (0 = one plunge)"), 0.0, unit="mm", min=0, sym="Q"),
        P("break_back", N_("Chip breaking retract"), 0.3, unit="mm", min=0.05, sym="Rb"),
        P("dwell", N_("Dwell at the bottom"), 0.2, unit="s", min=0, sym="P"),
        feed_param("f", N_("Feed"), 0.05, "F"),
    ]

    def __init__(self, values=None, variables=None):
        values = dict(values or {})
        if "speed" not in values:
            values["speed"] = 80.0              # cutting speed for grooving
        super().__init__(values, variables)
        self.tool_width = 0.0
        self.tool_reference = "left"

    def z_sign(self):
        return 1                                # the flanks are given, no direction

    # ---------------------------------------------------------------- geometry
    def flanks(self):
        return min(self.z_start, self.z_end), max(self.z_start, self.z_end)

    def depth(self) -> float:
        return abs(self.d_start - self.d_end) / 2

    def summary(self):
        low, high = self.flanks()
        return (f"{self.side_text} Ø{fmt(self.d_start)}→Ø{fmt(self.d_end)} "
                f"Z{fmt(high)}…{fmt(low)}")

    def max_d(self):
        return max(self.d_start, self.d_end)

    def z_range(self):
        return self.flanks()

    def inner_x(self, machine):
        return max(0.0, min(self.d_start, self.d_end) - 2 * machine.clearance)

    def check(self, machine):
        low, high = self.flanks()
        if high - low < 1e-6:
            raise ContourError(_("Z1 and Z2 are equal - the groove has no width"))
        if abs(self.d_start - self.d_end) < 1e-6:
            raise ContourError(_("Ø surface and Ø groove bottom are equal"))
        if not self.inside and self.d_end > self.d_start:
            raise ContourError(_("Outside the groove bottom d must be smaller than D"))
        if self.inside and self.d_end < self.d_start:
            raise ContourError(_("Inside the groove bottom d must be larger than D"))
        return []

    def outline(self):
        low, high = self.flanks()
        top = self.d_start / 2
        bottom = self.d_end / 2
        return [Element("line", high, top, high, bottom),
                Element("line", high, bottom, low, bottom),
                Element("line", low, bottom, low, top)]

    def material_cuts(self, material=None):
        low, high = self.flanks()
        radius_low = min(self.d_start, self.d_end) / 2
        radius_high = max(self.d_start, self.d_end) / 2
        return [("band", low, high, radius_low, radius_high)]

    # ---------------------------------------------------------------- tool
    def tool_problems(self, tool) -> List[str]:
        name = tool_name(tool)
        if tool.kind != "groove":
            return [_("{tool}: grooving needs a grooving tool").format(tool=name)]
        problems = []
        if self.inside and tool.position != "inside":
            problems.append(_("{tool}: an inside groove needs an inside grooving tool").format(
                tool=name))
        if not self.inside and tool.position == "inside":
            problems.append(_("{tool}: an outside groove needs an outside grooving tool").format(
                tool=name))
        low, high = self.flanks()
        if tool.width > 0 and tool.width > high - low + 1e-6:
            problems.append(_("{tool}: the tool (b{width:g}) is wider than the groove "
                              "({groove:g} mm)").format(tool=name, width=tool.width,
                                                        groove=round(high - low, 3)))
        if tool.groove_depth > 0 and self.depth() > tool.groove_depth + 1e-6:
            problems.append(_("{tool}: groove depth {depth:g} mm is larger than the grooving "
                              "depth of the tool ({limit:g} mm)").format(
                tool=name, depth=round(self.depth(), 3), limit=tool.groove_depth))
        return problems

    def prepare(self, tool):
        """Width and reference point of the tool for the plunge positions."""
        if tool is not None and tool.kind == "groove":
            self.tool_width = tool.width
            self.tool_reference = tool.reference

    def plunge_positions(self) -> List[float]:
        """Programmed Z of every plunge (reference point of the tool), front flank first."""
        low, high = self.flanks()
        width = self.tool_width
        if width <= 0 or width > high - low:
            width = high - low                  # without tool data: one plunge
        if self.tool_reference == "centre":
            offset = width / 2
        elif self.tool_reference == "right":
            offset = width
        else:
            offset = 0.0
        left_edges = []
        edge = high - width                     # left edge of the tool at the front flank
        step = STEP_SHARE * width
        while edge > low + 1e-6:
            left_edges.append(edge)
            edge -= step
        left_edges.append(low)
        return [round(left_edge + offset, 6) for left_edge in left_edges]

    # ---------------------------------------------------------------- tool path
    def body(self, emitter, machine, material):
        clearance = machine.clearance
        if self.inside:
            direction = 1.0                     # towards a larger Ø
        else:
            direction = -1.0
        surface = self.d_start
        bottom = self.d_end
        safe = surface - direction * 2 * clearance
        positions = self.plunge_positions()
        if self.inside:
            # in front of the part into the bore axis first, then along Z into the bore
            emitter.rapid(x=safe)
            emitter.rapid(z=positions[0])
        else:
            # from the retract plane (above all material) first along Z, then down
            emitter.rapid(z=positions[0])
            emitter.rapid(x=safe)
        for z in positions:
            emitter.rapid(z=z)
            if self.peck > 0:
                current = surface
                while (bottom - current) * direction > 1e-6:
                    next_x = current + direction * 2 * self.peck
                    if (bottom - next_x) * direction < 0:
                        next_x = bottom
                    emitter.feed(x=next_x, f=self.f)
                    if abs(next_x - bottom) > 1e-6:
                        emitter.rapid(x=next_x - direction * 2 * self.break_back)
                    current = next_x
            else:
                emitter.feed(x=bottom, f=self.f)
            emitter.dwell(self.dwell)
            emitter.rapid(x=safe)
