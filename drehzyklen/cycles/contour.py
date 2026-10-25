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
"""Free contour: points P1…Pn with lines, arcs and individual corner radii/chamfers.

The contour text is parsed by ``geometry.parse_rows`` and resolved into lines and arcs by
``geometry.resolve_rows``. The direction follows the points: last Z > first Z machines towards
+Z. The path is the common contour turning (``turning.ContourTurner``).
"""
from __future__ import annotations

from ..gcode import fmt
from ..geometry import ContourError, parse_rows, resolve_rows
from ..i18n import N_, _
from .params import CORNER_CHAMFER, CORNER_LABELS, CORNER_NONE, CORNER_RADIUS, ROUGH, P
from .turning import TurningCycle

DEFAULT_CONTOUR = "X0 Z0\nX50 Z0 C2 A30\nX50 Z-20\nX60 Z-20 R5\nX60 Z-50\nX64 Z-50"


class Contour(TurningCycle):
    KEY = "contour"
    TITLE = N_("Contour")
    SHORT = N_("Contour")
    PARAMS = [
        P("contour", N_("Contour"), DEFAULT_CONTOUR, "contour", sym="P1…Pn"),
        P("corner_mode", N_("All other corners"), CORNER_NONE, "choice",
          (CORNER_NONE, CORNER_RADIUS, CORNER_CHAMFER), labels=CORNER_LABELS,
          help=N_("Applies to all corners without their own entry")),
        P("corner_size", N_("Size for all other corners"), 1.0, unit="mm", min=0),
    ] + ROUGH

    def rows(self):
        return parse_rows(self.contour)

    def z_sign(self):
        rows = self.rows()
        if rows[-1].z > rows[0].z:
            return -1
        return 1

    def default_corner(self):
        if self.corner_mode == CORNER_RADIUS:
            return "R"
        if self.corner_mode == CORNER_CHAMFER:
            return "C"
        return None

    def real_elements(self):
        return resolve_rows(self.rows(), self.default_corner(), self.corner_size)

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        return [element.transformed(sign_r, sign_z) for element in self.real_elements()]

    def summary(self):
        try:
            rows = self.rows()
            return _("{side} {count} points Z{first}…{last}").format(
                side=self.side_text, count=len(rows), first=fmt(rows[0].z),
                last=fmt(rows[-1].z)) + self.direction_text()
        except ContourError as error:
            return _("Error: {message}").format(message=error)

    def check(self, machine):
        return self.contour_check()
