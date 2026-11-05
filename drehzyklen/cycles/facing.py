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
"""Facing: removes material between Z1 and Z2 in passes of ``ap``, radially from D to d.

Tool path
---------
The passes are computed in a frame where the removal goes towards -Z (z_sign mirrors the
back-side case Z1 < Z2). Levels: Z1 - ap, Z1 - 2 ap, ... down to Z2 + finishing allowance,
then one finishing pass at Z2 with the finishing feed.

The switches »Roughing« and »Finishing« work as in the other turning cycles: roughing only
leaves the finishing allowance, finishing only makes the one finishing pass at Z2 (the
material in front of it must already be removed down to the allowance).

"Approach side" is the side the tool comes from: outside it is beyond the largest diameter,
inside it is in the bore below the smallest diameter.

* The cut starts at the approach side (outside towards the centre, inside outwards):
  rapid to the level, cut from D to d, lift by the safety distance in Z, rapid back to the
  approach side. The tool never drags over the face it has just cut.
* The cut starts at the far side (outside outwards, inside towards the centre): rapid above the
  previous level to D, feed to the level, cut to d, lift in Z, back to the approach side.

After the last pass the tool rapids in front of Z1.
"""
from __future__ import annotations

from typing import List, Tuple

from ..gcode import TransformedEmitter, fmt
from ..geometry import ContourError, Element
from ..i18n import N_, _
from ..tools import sector_inside
from .base import Cycle
from .params import P, feed_param
from .toolcheck import tool_name


class FacingPlan:
    """Levels and feeds of the facing passes (in the frame with removal towards -Z)."""

    def __init__(self, z_first: float, z_last: float, ap: float, allowance: float,
                 feed_rough: float, feed_finish: float, rough: bool = True,
                 finish: bool = True):
        rough_end = z_last + allowance
        levels = []
        if rough:
            level = z_first - ap
            while level > rough_end + 1e-6:
                levels.append(level)
                level -= ap
            if rough_end < z_first - 1e-6:
                levels.append(rough_end)
        self.passes: List[Tuple[float, float]] = [(level, feed_rough) for level in levels]
        # with roughing and no allowance the last roughing pass is already at Z2
        self.has_finish = finish and (allowance > 0 or not rough)
        if self.has_finish:
            self.passes.append((z_last, feed_finish))


class Facing(Cycle):
    KEY = "facing"
    ROUNDS_CORNERS = True
    TITLE = N_("Facing")
    SHORT = N_("Face")
    RENAMES = {"stock_d": "d_start", "inner_d": "d_end"}
    PARAMS = [
        P("d_start", N_("Ø start"), 50.0, unit="mm", min=-5, sym="D", skey=True),
        P("d_end", N_("Ø end"), -0.4, unit="mm", min=-5, sym="d",
          help=N_("Outside: 0 = to the centre, slightly negative so that no pip remains. "
                  "D > d: facing towards the centre, D < d: outwards")),
        P("z_start", N_("Z start"), 1.0, unit="mm", sym="Z1",
          help=N_("Z1 > Z2: removal towards -Z (front face), Z1 < Z2: removal towards +Z "
                  "(back face)")),
        P("z_end", N_("Z end"), 0.0, unit="mm", sym="Z2"),
        P("ap", N_("Depth of cut (Z)"), 0.5, unit="mm", min=0.01, sym="ap"),
        feed_param("f_rough", N_("Feed roughing"), 0.2, "F"),
        feed_param("f_finish", N_("Feed finishing"), 0.08, "Fs"),
        P("allow_z", N_("Finishing allowance Z"), 0.1, unit="mm", min=0, sym="aZ"),
        P("do_rough", N_("Roughing"), True, "bool"),
        P("do_finish", N_("Finishing"), True, "bool"),
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
            raise ContourError(_("Z start and Z end are equal"))
        if abs(self.d_end - self.d_start) < 1e-6:
            raise ContourError(_("Ø start and Ø end are equal"))
        if not self.do_rough and not self.do_finish:
            return [_("Roughing and finishing are switched off - the step does not cut")]
        return []

    def rough_only_allowance(self) -> float:
        """Finishing allowance that stays on the face (roughing only), else 0."""
        if self.do_rough and not self.do_finish:
            return self.allow_z
        return 0.0

    def outline(self):
        radius_low = max(min(self.d_start, self.d_end), 0) / 2
        radius_high = max(self.d_start, self.d_end) / 2
        return [Element("line", self.z_end, radius_low, self.z_end, radius_high)]

    def material_cuts(self, material=None):
        if not self.do_rough and not self.do_finish:
            return []
        radius_low = max(min(self.d_start, self.d_end), 0) / 2
        radius_high = max(self.d_start, self.d_end) / 2
        z_end = self.z_end + self.z_sign() * self.rough_only_allowance()
        return [("band", z_end, self.z_start, radius_low, radius_high)]

    def moving_inward(self) -> bool:
        """Does the facing cut run towards the centre (D > d)?"""
        return self.d_end < self.d_start

    def tool_problems(self, tool):
        name = tool_name(tool)
        if tool.kind in ("thread", "drill"):
            return [_("{tool}: not suitable for facing").format(tool=name)]
        if self.inside and tool.position == "outside":
            return [_("{tool}: outside tool - inside facing needs an inside tool (boring "
                      "bar)").format(tool=name)]
        if not self.inside and tool.position == "inside":
            return [_("{tool}: inside tool - outside facing needs an outside "
                      "tool").format(tool=name)]
        sector = tool.sector()
        if sector is None:
            return []
        # free quadrant behind the cut: away from the remaining face and from the uncut chip
        moving_inward = self.moving_inward()
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
            radial_text = _("towards the centre")
        else:
            radial_text = _("outwards")
        if self.z_sign() > 0:
            axial_text = "-Z"
        else:
            axial_text = "+Z"
        return [_("{tool}: tool position/hand does not fit this facing operation (direction "
                  "{radial}, removal towards {axial})").format(tool=name, radial=radial_text,
                                                               axial=axial_text)]

    def body(self, emitter, machine, material):
        clearance = machine.clearance
        sign = self.z_sign()
        work = TransformedEmitter(emitter, 1, sign)
        z_first = sign * self.z_start
        z_last = sign * self.z_end
        plan = FacingPlan(z_first, z_last, self.ap, self.allow_z, self.f_rough, self.f_finish,
                          rough=bool(self.do_rough), finish=bool(self.do_finish))
        start_d = self.d_start
        end_d = self.d_end
        if self.inside:
            approach_x = max(min(start_d, end_d) - 2 * clearance, 0.0)
            cut_from_approach_side = end_d > start_d
        else:
            approach_x = max(start_d, end_d) + 2 * clearance
            cut_from_approach_side = end_d < start_d
        work.rapid(x=approach_x)
        work.rapid(z=z_first + clearance)
        previous = z_first
        for index, (level, feed) in enumerate(plan.passes):
            if index == len(plan.passes) - 1 and plan.has_finish:
                work.comment(_("Finishing"))
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
