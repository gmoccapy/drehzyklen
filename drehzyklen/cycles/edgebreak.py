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
"""Chamfer or radius at an existing edge (Ø D, face at Z1).

Contour (work frame)
--------------------
Front edge (face points to +Z):
    face at Z1 from below the edge up to D, chamfer/radius at D/Z1, a short piece of D.
Back edge (face points to -Z, e.g. behind a collar):
    a short piece of D in front of the edge, chamfer/radius, and the contour ends there - the
    face behind it stays untouched, so a right-hand tool with enough clearance angle suffices.

A negative chamfer angle swaps front and back edge. Path: common contour turning.
"""
from __future__ import annotations

import math

from ..gcode import fmt
from ..geometry import ContourError, resolve_rows
from ..i18n import N_, _
from .params import (COMPENSATION, COMPENSATION_LABELS, CORNER_CHAMFER, CORNER_LABELS,
                     CORNER_RADIUS, P, feed_param)
from .turning import TurningCycle, work_row

EDGE_FRONT = "front"
EDGE_BACK = "back"


class EdgeBreak(TurningCycle):
    KEY = "edge"
    TITLE = N_("Chamfer / edge radius")
    SHORT = N_("Chamfer")
    RENAMES = {"edge_d": "diameter", "edge_z": "z_start"}
    PARAMS = [
        P("kind", N_("Kind"), CORNER_CHAMFER, "choice", (CORNER_CHAMFER, CORNER_RADIUS),
          labels=CORNER_LABELS),
        P("edge", N_("Edge"), EDGE_FRONT, "choice", (EDGE_FRONT, EDGE_BACK),
          labels={EDGE_FRONT: N_("Front edge"), EDGE_BACK: N_("Back edge")},
          help=N_("Front edge: the face points to +Z. Back edge: the face points to -Z "
                  "(undercut, entered obliquely)")),
        P("size", N_("Length in Z (chamfer) or radius"), 1.0, unit="mm", min=0.01, sym="L/R"),
        P("angle", N_("Chamfer angle to the Z axis"), 45.0, unit="°", min=-89, max=89, sym="α",
          when=("kind", (CORNER_CHAMFER,)),
          help=N_("45° = equal legs. A negative angle puts the chamfer on the back edge")),
        P("diameter", N_("Ø of the edge"), 40.0, unit="mm", min=0.1, sym="D"),
        P("z_start", N_("Z of the face"), 0.0, unit="mm", sym="Z1"),
        P("ap", N_("Depth of cut (radial)"), 0.5, unit="mm", min=0.01, sym="ap"),
        feed_param("f_rough", N_("Feed roughing"), 0.15, "F"),
        feed_param("f_finish", N_("Feed finishing"), 0.08, "Fs"),
        P("allow_x", N_("Finishing allowance X (Ø)"), 0.2, unit="mm", min=0, sym="aX"),
        P("allow_z", N_("Finishing allowance Z"), 0.1, unit="mm", min=0, sym="aZ"),
        P("comp", N_("Tool nose radius compensation"), "auto", "choice", COMPENSATION,
          labels=COMPENSATION_LABELS),
    ]

    def z_sign(self):
        return 1

    def is_back_edge(self) -> bool:
        back = self.edge == EDGE_BACK
        if self.kind == CORNER_CHAMFER and self.angle < 0:
            back = not back
        return back

    def radial_leg(self):
        if self.kind == CORNER_CHAMFER:
            return self.size * math.tan(math.radians(abs(self.angle)))
        return self.size

    def summary(self):
        if self.kind == CORNER_CHAMFER:
            text = _("Chamfer {size}×{angle}°").format(size=fmt(self.size),
                                                       angle=fmt(abs(self.angle)))
        else:
            text = f"R{fmt(self.size)}"
        if self.is_back_edge():
            text += " " + _("back edge")
        return _("{side} {edge} at Ø{diameter} Z{z}").format(
            side=self.side_text, edge=text, diameter=fmt(self.diameter), z=fmt(self.z_start))

    def check(self, machine):
        if abs(self.angle) < 1e-6 and self.kind == CORNER_CHAMFER:
            raise ContourError(_("The chamfer angle must not be 0°"))
        if not self.inside and self.radial_leg() + 0.5 >= self.diameter / 2:
            raise ContourError(_("Chamfer/radius is too large for this diameter"))
        return self.contour_check()

    def work_elements(self, material=None):
        sign_r = self.radius_sign
        edge_x = sign_r * self.diameter
        z_face = self.z_start
        leg = self.radial_leg()
        if self.kind == CORNER_CHAMFER:
            corner = ("C", self.size, abs(self.angle))
        else:
            corner = ("R", self.size, None)
        if not self.is_back_edge():
            rows = [work_row(edge_x - 2 * (leg + 0.5), z_face),
                    work_row(edge_x, z_face, corner[0], corner[1], corner[2], "B"),
                    work_row(edge_x, z_face - self.size - 0.5)]
            return resolve_rows(rows)
        # back edge: the contour ends at the end of the chamfer/radius, the face behind it
        # stays untouched
        rows = [work_row(edge_x, z_face + self.size + 0.5),
                work_row(edge_x, z_face, corner[0], corner[1], corner[2], "A"),
                work_row(edge_x - 2 * (leg + 0.5), z_face)]
        elements = resolve_rows(rows)
        return elements[:-1]

    def start_radius_work(self, material, elements):
        return self.radius_sign * self.diameter / 2
