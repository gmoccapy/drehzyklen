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
"""Free G-code between two cycles (e.g. M5 / M0 to remove chips).

The code is inserted unchanged while the tool is at the retract plane. What happens with the
spindle and the coolant afterwards is decided by the program generator (``program.py``):
with "switch on again" the previous state is restored right after the block (or by the next
cycle after a tool change); M5/M9 inside the block are recognised in any case.
"""
from __future__ import annotations

import re

from ..i18n import N_, _
from .base import Cycle
from .params import P

DEFAULT_CODE = "M5\nM9\nM0 (Remove chips - continue with cycle start)"


class GCodeOp(Cycle):
    KEY = "gcode"
    TITLE = N_("G-code")
    SHORT = N_("G-code")
    TECH_PARAMS = []
    HAS_SIDE = False
    HAS_TOOL = False
    PARAMS = [
        P("code", N_("G-code"), DEFAULT_CODE, "text",
          help=N_("Inserted unchanged at this point. The tool is at the retract plane; own "
                  "moves must return there.")),
        P("restart", N_("Spindle/coolant on again"), True, "bool",
          help=N_("Right after the block the speed with M3/M4 and M8 of the previous cycle are "
                  "output again")),
    ]

    @property
    def tool(self):
        return None

    @property
    def coolant(self):
        return False

    def m_codes(self) -> set:
        """M numbers in the block (without comments), e.g. {5, 9, 0}."""
        text = re.sub(r"\([^)]*\)", " ", str(self.code))
        lines = [line.split(";")[0] for line in text.splitlines()]
        numbers = set()
        for match in re.finditer(r"[Mm]\s*(\d+)", " ".join(lines)):
            numbers.add(int(match.group(1)))
        return numbers

    def summary(self):
        lines = [line.strip() for line in str(self.code).splitlines() if line.strip()]
        text = " · ".join(lines[:3])
        if len(lines) > 3:
            text += " …"
        return text

    def z_range(self):
        return 0.0, 0.0

    def body(self, emitter, machine, material):
        emitter.comment(_("--- own G-code ---"))
        for line in str(self.code).splitlines():
            if line.strip():
                emitter.emit(line.rstrip())
        emitter.comment(_("--- end of own G-code ---"))
