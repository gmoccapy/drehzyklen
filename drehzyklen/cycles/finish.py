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
"""Finishing pass over the contour of several steps (from step … to step).

The steps stay as they are (roughing, own finishing); this step turns their finished contour
once more in one go, so the small noses the corner radius leaves at the transitions between the
steps are removed. The contour is taken from the steps every time the program is calculated
(``attach``): a changed step changes the finishing pass too.

Tool path: approach in front of the contour, one finishing pass along the whole contour
(optionally with tool nose radius compensation), retract - like the finishing pass of the
contour cycle, without roughing and without allowance.
"""
from __future__ import annotations

from ..geometry import ContourError
from ..i18n import N_, _
from .contour import Contour
from .merge import contour_text
from .params import COMPENSATION, COMPENSATION_LABELS, P, feed_param
from .turning import ContourTurner


class FinishContour(Contour):
    KEY = "finish"
    TITLE = N_("Finish contour")
    SHORT = N_("Finish")
    PARAMS = [
        P("first", N_("From step"), 1, "int", min=1, sym="1",
          help=N_("First step of the contour (long turning, taper, contour, chamfer)")),
        P("last", N_("To step"), 2, "int", min=1, sym="2",
          help=N_("Last step of the contour; all steps in between belong to it")),
        feed_param("f_finish", N_("Feed finishing"), 0.1, "Fs"),
        P("comp", N_("Tool nose radius compensation"), "auto", "choice", COMPENSATION,
          labels=COMPENSATION_LABELS,
          help=N_("auto chooses the side from the machining direction. Needs a tool table with "
                  "radius and orientation.")),
    ]
    END_KEYS = None
    START_KEYS = None

    def __init__(self, values=None, variables=None):
        self.generated = ""                     # contour text taken from the steps
        self.source_error = _("not linked to the program yet")
        super().__init__(values, variables)

    def resolve(self, variables):
        super().resolve(variables)
        self._set_contour()

    def _set_contour(self):
        """The contour fields are no input fields: taken from the steps (see attach)."""
        self.v["contour"] = self.generated
        self.v["corner_mode"] = "none"
        self.v["corner_size"] = 0.0

    def attach(self, ops, position: int):
        """Take the contour from the steps first…last (position: index of this step)."""
        first, last = int(self.v.get("first", 1)), int(self.v.get("last", 1))
        try:
            if first > last:
                raise ContourError(_("»From step« {first} is after »to step« {last}").format(
                    first=first, last=last))
            if last > position:
                raise ContourError(_("Step {number} does not lie before this finishing step")
                                   .format(number=last))
            numbers = list(range(first, last + 1))
            text = contour_text(ops, numbers, minimum=1)
            source = next(ops[number - 1] for number in numbers
                          if ops[number - 1].KEY in ("long", "taper", "contour"))
            if source.inside != self.inside:
                raise ContourError(_("The steps are machined on the other side (outside/inside)"))
        except ContourError as error:
            self.generated = ""
            self.source_error = str(error)
            self._set_contour()
            return
        self.generated = text
        self.source_error = ""
        self._set_contour()

    def rows(self):
        if self.source_error:
            raise ContourError(self.source_error)
        return super().rows()

    def end_point(self):
        return None

    def copy_with(self, updates):
        copy = super().copy_with(updates)
        copy.generated, copy.source_error = self.generated, self.source_error
        copy._set_contour()
        return copy

    def summary(self):
        text = _("{side} finish steps {first}–{last}").format(
            side=self.side_text, first=int(self.v.get("first", 1)),
            last=int(self.v.get("last", 1)))
        if self.source_error:
            text += " – " + self.source_error
        return text

    def check(self, machine):
        if self.source_error:
            raise ContourError(self.source_error)
        return super().check(machine)

    def help_values(self):
        values = dict(self.v)
        values["source_error"] = self.source_error
        return values

    def body(self, emitter, machine, material):
        elements = self.work_elements(material)
        start = self.start_radius_work(material, elements)
        turner = ContourTurner(emitter, self.radius_sign, self.z_sign(), machine.clearance)
        turner.run(elements, start, self.approach_front(material, elements, start), 1.0,
                   0.0, 0.0, self.f_finish, self.f_finish, False, True,
                   self.v.get("comp", "auto"))
