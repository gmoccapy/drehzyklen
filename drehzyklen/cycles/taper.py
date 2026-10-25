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
"""Taper: a cone from Ø D at Z1 to Ø d at Z2, given by end diameter or by half angle.

Contour (work frame)
--------------------
    cone            D at Z1 to d at Z2
    [shoulder]      at Z2 from d up to the existing material (only if material is higher)

Roughing starts at the existing material (material simulation). A falling cone (d < D in
machining direction) is entered obliquely along the cone - no level plunges into the part.
"""
from __future__ import annotations

import math

from ..gcode import fmt
from ..geometry import ContourError, Element, resolve_rows
from ..i18n import N_, _
from .params import ROUGH, P
from .turning import TurningCycle, work_row

MODE_DIAMETER = "diameter"
MODE_ANGLE = "angle"


class Taper(TurningCycle):
    KEY = "taper"
    TITLE = N_("Taper")
    SHORT = N_("Taper")
    PARAMS = [
        P("mode", N_("Given by"), MODE_DIAMETER, "choice", (MODE_DIAMETER, MODE_ANGLE),
          labels={MODE_DIAMETER: N_("End diameter"), MODE_ANGLE: N_("Angle")},
          help=N_("When switching, the other value is calculated")),
        P("d_start", N_("Ø start (at Z1)"), 30.0, unit="mm", min=0, sym="D"),
        P("d_end", N_("Ø end (at Z2)"), 44.0, unit="mm", min=0, sym="d"),
        P("z_start", N_("Z start"), 0.0, unit="mm", sym="Z1"),
        P("z_end", N_("Z end"), -25.0, unit="mm", sym="Z2", when=("mode", (MODE_DIAMETER,))),
        P("angle", N_("Half taper angle"), 15.0, unit="°", min=-89, max=89, sym="α/2",
          when=("mode", (MODE_ANGLE,)),
          help=N_("Positive = the Ø grows in machining direction, negative = it gets smaller "
                  "(falling taper, entered obliquely). Z end is calculated")),
    ] + ROUGH

    @staticmethod
    def angle_from(d_start, d_end, z_start, z_end):
        return math.degrees(math.atan2((d_end - d_start) / 2, abs(z_start - z_end)))

    @staticmethod
    def z_end_from(d_start, d_end, z_start, angle, direction=1):
        tangent = math.tan(math.radians(angle))
        if abs(tangent) < 1e-9 or (d_end - d_start) * tangent < 0:
            raise ContourError(_("The angle does not fit the diameters (sign or 0°)"))
        return z_start - direction * (d_end - d_start) / (2 * tangent)

    @classmethod
    def switch(cls, key, values, old_value=None):
        updates = super().switch(key, values, old_value)
        if key != "mode":
            return updates
        try:
            if values.get("mode") == MODE_ANGLE:
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
        if self.mode == MODE_ANGLE:
            return self.z_end_from(self.d_start, self.d_end, self.z_start, self.angle)
        return self.z_end

    def angle_effective(self):
        if self.mode == MODE_ANGLE:
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
            raise ContourError(_("Z start and Z end are equal"))
        if abs(self.d_end - self.d_start) < 1e-6:
            raise ContourError(_("Ø start and Ø end are equal - that is a cylinder"))
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        z_first = sign_z * self.z_start
        z_last = sign_z * self.z_end_effective()
        rows = [work_row(sign_r * self.d_start, z_first), work_row(sign_r * self.d_end, z_last)]
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
