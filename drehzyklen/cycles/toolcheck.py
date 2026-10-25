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
"""Does a tool fit the cycle?

Every tool is described by the angular range its insert occupies around the cutting tip (the
"sector", see ``tools.py``). A tool can cut along a direction if the insert stays completely
on the free side of the path:

* roughing in the work frame (towards -Z, material above): sector inside 0...90 degrees
* finishing a contour segment with direction theta: sector inside (theta - 180)...theta
* facing: sector inside the free quadrant behind the cut (see ``facing.py``)
"""
from __future__ import annotations

import math
from typing import List

from ..geometry import Element
from ..i18n import _
from ..tools import normalize_angle, sector_inside


def tool_name(tool) -> str:
    return f"T{tool.number} ({tool.kind_text()} {tool.hand_text()} {tool.position_text()})"


def turning_tool_problems(tool, inside: bool, z_mirrored: bool, work_elements: List[Element],
                          roughing: bool) -> List[str]:
    """Can the turning tool machine the contour (given in the work frame)?"""
    name = tool_name(tool)
    if tool.kind == "thread":
        return [_("{tool}: a threading tool is not suitable for turning").format(tool=name)]
    if tool.kind == "drill":
        return [_("{tool}: a drill is not suitable for turning").format(tool=name)]
    sector = tool.sector_for(inside, z_mirrored)
    if sector is None:
        return []
    if not sector_inside(sector, 0.0, 180.0):
        if inside:
            return [_("{tool}: wrong tool position - inside machining needs an inside "
                      "tool").format(tool=name)]
        return [_("{tool}: wrong tool position - outside machining needs an outside "
                  "tool").format(tool=name)]
    problems = []
    if roughing and tool.kind != "R" and not sector_inside(sector, 0.0, 90.0):
        if z_mirrored:
            needed = _("left-hand (cuts towards +Z)")
        else:
            needed = _("right-hand (cuts towards -Z)")
        problems.append(_("{tool}: wrong cutting direction - a {needed} tool is needed "
                          "here").format(tool=name, needed=needed))
    if tool.kind == "R":
        return problems
    worst = None
    for element in work_elements:
        points = element.sample(10.0)
        for (z_start, r_start), (z_end, r_end) in zip(points, points[1:]):
            if math.hypot(z_end - z_start, r_end - r_start) < 1e-6:
                continue
            direction = math.degrees(math.atan2(r_end - r_start, z_end - z_start))
            if not sector_inside(sector, direction - 180.0, direction):
                falling = abs(normalize_angle(direction + 180.0))
                if worst is None or falling > worst:
                    worst = falling
    if worst is not None:
        problems.append(_("{tool}: clearance angle too small for the contour (a section falls "
                          "by {angle:.0f}°) - choose a tool with a sharper insert (e.g. ISO V) "
                          "or the matching hand").format(tool=name, angle=worst))
    return problems
