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
"""Longitudinal turning: a cylinder from Ø D to Ø d between Z1 and Z2, optional edges.

Contour (work frame)
--------------------
    [start corner]  face at Z1 down to d, chamfer or radius at the corner d/Z1
    cylinder        d from Z1 to Z2
    [end corner]    chamfer or radius at the shoulder d/Z2
    shoulder        from d up to D (cut off at D, so a radius may be larger than the step)

The tool path is the common contour turning (``turning.ContourTurner``): roughing levels from
D down to d, then the finishing pass along the contour.
"""
from __future__ import annotations

from ..gcode import fmt
from ..geometry import ContourError, clip_at_radius, resolve_rows
from ..i18n import N_, _
from .params import (CORNER_CHAMFER, CORNER_KINDS, CORNER_LABELS, CORNER_NONE, CORNER_RADIUS,
                     ROUGH, P)
from .turning import TurningCycle, corner_code, work_row


class LongTurning(TurningCycle):
    KEY = "long"
    TITLE = N_("Longitudinal turning")
    SHORT = N_("Turn")
    RENAMES = {"stock_d": "d_start", "target_d": "d_end"}
    PARAMS = [
        P("d_start", N_("Ø start"), 50.0, unit="mm", min=0.1, sym="D", skey=True,
          help=N_("Outside D ≥ d, inside D ≤ d")),
        P("d_end", N_("Ø end"), 40.0, unit="mm", min=0, sym="d"),
        P("z_start", N_("Z start"), 0.0, unit="mm", sym="Z1",
          help=N_("Z1 > Z2: towards -Z, Z1 < Z2: towards +Z (left-hand tool)")),
        P("z_end", N_("Z end"), -30.0, unit="mm", sym="Z2"),
        P("start_kind", N_("Edge at the start"), CORNER_NONE, "choice", CORNER_KINDS,
          labels=CORNER_LABELS),
        P("start_size", N_("Size at the start"), 1.0, unit="mm",
          min=0.01, sym="Ka", when=("start_kind", (CORNER_CHAMFER, CORNER_RADIUS)),
          help=N_("Chamfer 45° or radius")),
        P("end_kind", N_("Edge at the end (shoulder/bottom)"), CORNER_NONE, "choice",
          CORNER_KINDS, labels=CORNER_LABELS),
        P("end_size", N_("Size at the end"), 1.0, unit="mm", min=0.01,
          sym="Ke", when=("end_kind", (CORNER_CHAMFER, CORNER_RADIUS)),
          help=N_("May be larger than the step to D - the edge then ends at D")),
    ] + ROUGH

    def __init__(self, values=None):
        if values is None:
            values = {}
        values = dict(values)
        # projects of version 0.1: chamfer/end_radius
        if values.get("chamfer", 0) and "start_kind" not in values:
            values["start_kind"] = CORNER_CHAMFER
            values["start_size"] = values["chamfer"]
        if values.get("end_radius", 0) and "end_kind" not in values:
            values["end_kind"] = CORNER_RADIUS
            values["end_size"] = values["end_radius"]
        super().__init__(values)

    def summary(self):
        text = (f"{self.side_text} Ø{fmt(self.d_start)}→Ø{fmt(self.d_end)} "
                f"Z{fmt(self.z_start)}…{fmt(self.z_end)}{self.direction_text()}")
        for kind, size in ((self.start_kind, self.start_size), (self.end_kind, self.end_size)):
            if kind == CORNER_CHAMFER:
                text += f" C{fmt(size)}"
            elif kind == CORNER_RADIUS:
                text += f" R{fmt(size)}"
        return text

    def max_d(self):
        return max(self.d_start, self.d_end)

    def check(self, machine):
        if not self.inside and self.d_start < self.d_end:
            raise ContourError(_("Outside the start Ø D must be greater than or equal to the end "
                                 "Ø d"))
        if self.inside and self.d_start > self.d_end:
            raise ContourError(_("Inside the start Ø D must be smaller than or equal to the end "
                                 "Ø d"))
        if abs(self.d_start - self.d_end) < 1e-6:
            raise ContourError(_("Ø start and Ø end are equal - nothing to remove"))
        if abs(self.z_end - self.z_start) < 1e-6:
            raise ContourError(_("Z start and Z end are equal"))
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        sign_z = self.z_sign()
        target = sign_r * self.d_end
        start_diameter = sign_r * self.d_start
        z_first = sign_z * self.z_start
        z_last = sign_z * self.z_end
        rows = []
        start_corner = corner_code(self.start_kind)
        if start_corner:
            face_x = target - 2 * (self.start_size + 0.5)
            if not self.inside and face_x < 0:
                face_x = 0.0
            rows.append(work_row(face_x, z_first))
            rows.append(work_row(target, z_first, start_corner, self.start_size))
        else:
            rows.append(work_row(target, z_first))
        end_corner = corner_code(self.end_kind)
        if end_corner:
            rows.append(work_row(target, z_last, end_corner, self.end_size))
            extension = 2 * (2 * self.end_size + 1)
        else:
            rows.append(work_row(target, z_last))
            extension = 2.0
        rows.append(work_row(start_diameter + extension, z_last))
        try:
            elements = resolve_rows(rows)
        except ContourError:
            raise ContourError(_("The edges do not fit the length Z1…Z2 or the diameter - make "
                                 "the edges smaller")) from None
        return clip_at_radius(elements, start_diameter / 2, 1)

    def start_radius_work(self, material, elements):
        return self.radius_sign * self.d_start / 2
