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
"""Lathe cycles and program generation for LinuxCNC (G18, diameter programming G7).

One module per cycle; each module explains the tool path of its cycle at the top:

    facing.py       Facing          longturning.py  LongTurning
    taper.py        Taper           edgebreak.py    EdgeBreak (chamfer / edge radius)
    contour.py      Contour         thread.py       Thread (G76)
    drill.py        Drill           gcodeblock.py   GCodeOp (free G-code)

Shared parts:

    params.py       input field descriptions (P), choice keys, shared parameter groups
    base.py         Cycle base class: values, direction, speed/feed technology
    turning.py      TurningCycle and ContourTurner (roughing levels + finishing pass)
    toolcheck.py    does a tool fit a contour?
    stock.py        Stock (raw part and clamping)
    program.py      Program.generate(): checks, header, tool changes, material simulation
"""
from .base import Cycle
from .contour import Contour
from .drill import Drill
from .edgebreak import EdgeBreak
from .facing import Facing
from .gcodeblock import GCodeOp
from .longturning import LongTurning
from .params import (CORNER_KINDS, INSIDE, OUTSIDE, SIDE, SPEED_FEED_MODES, P, feed_mode_of,
                     spindle_mode_of)
from .program import CYCLE_BY_KEY, CYCLES, Program, Result, SpindleState, cycle_from_dict
from .stock import STOCK_PARAMS, Stock
from .taper import Taper
from .thread import Thread
from .turning import ContourTurner, TurningCycle

__all__ = ["CORNER_KINDS", "CYCLES", "CYCLE_BY_KEY", "INSIDE", "OUTSIDE", "SIDE",
           "SPEED_FEED_MODES", "STOCK_PARAMS", "Contour", "ContourTurner", "Cycle", "Drill",
           "EdgeBreak", "Facing", "GCodeOp", "LongTurning", "P", "Program", "Result",
           "SpindleState", "Stock", "Taper", "Thread", "TurningCycle", "cycle_from_dict",
           "feed_mode_of", "spindle_mode_of"]
