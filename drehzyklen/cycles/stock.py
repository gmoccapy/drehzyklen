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
"""Stock (raw part) and how it is clamped."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..gcode import fmt
from ..geometry import ContourError
from ..i18n import N_, _
from .params import P, migrate_values

ZERO_FRONT = "front"
ZERO_CHUCK = "chuck"

STOCK_PARAMS = [
    P("D", N_("Outer Ø"), 50.0, unit="mm", min=0.5, sym="D"),
    P("d", N_("Inner Ø (0 = solid)"), 0.0, unit="mm", min=0, sym="d"),
    P("L", N_("Length out of the chuck"), 80.0, unit="mm", min=1, sym="L",
      help=N_("From the chuck face to the stock front")),
    P("zero", N_("Workpiece zero Z0"), ZERO_FRONT, "choice", (ZERO_FRONT, ZERO_CHUCK),
      labels={ZERO_FRONT: N_("Front face"), ZERO_CHUCK: N_("Chuck")},
      help=N_("Front face: Z0 at the finished front face, chuck: Z0 at the chuck face")),
    P("face", N_("Facing allowance"), 1.0, unit="mm", min=0, sym="a",
      when=("zero", (ZERO_FRONT,)),
      help=N_("The stock protrudes this much in front of Z0 (removed by facing)")),
    P("chuck_clear", N_("Distance to the chuck"), 2.0, unit="mm", min=0, sym="s",
      help=N_("Safety distance - moves closer to the chuck are errors")),
]


class Stock:
    """Stock and clamping: outer/inner diameter, length out of the chuck, zero point."""

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self.v = {}
        for param in STOCK_PARAMS:
            self.v[param.key] = param.default
        if values:
            values = migrate_values(STOCK_PARAMS, values)
            for key, value in values.items():
                if key in self.v:
                    self.v[key] = value

    def __getattr__(self, item):
        values = self.__dict__.get("v")
        if values is not None and item in values:
            return values[item]
        raise AttributeError(item)

    @property
    def z_front(self) -> float:
        """Z of the stock front face."""
        if self.zero == ZERO_FRONT:
            return self.face
        return self.L

    @property
    def z_chuck(self) -> float:
        """Z of the chuck face (jaws)."""
        if self.zero == ZERO_FRONT:
            return self.face - self.L
        return 0.0

    def to_dict(self):
        return dict(self.v)

    def check(self) -> List[str]:
        if self.d >= self.D:
            raise ContourError(_("Stock: the inner Ø must be smaller than the outer Ø"))
        return []

    def summary(self) -> str:
        text = _("Stock Ø{outer}").format(outer=fmt(self.D))
        if self.d > 0:
            text += f"/Ø{fmt(self.d)}"
        return text + _(", Z{front}…chuck Z{chuck}").format(front=fmt(self.z_front),
                                                            chuck=fmt(self.z_chuck))
