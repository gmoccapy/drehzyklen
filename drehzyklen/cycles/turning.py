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
"""Contour turning: the common core of longitudinal turning, taper, chamfer/radius and contour.

The work frame
--------------
Every turning cycle describes its finished contour in a *work frame*: machining towards -Z and
material above the contour. Inside machining is mirrored at the spindle axis (radius_sign -1),
machining towards +Z is mirrored at Z = 0 (z_sign -1). The tool path is computed once in this
frame and written back mirrored by ``TransformedEmitter``. Therefore one roughing and one
finishing routine serve all cases.

Tool path of ``ContourTurner``
------------------------------
1. Rapid to the safe radius (start radius + clearance), then in Z in front of the part.
2. Roughing (if switched on): the contour is offset by the finishing allowance. Starting at the
   existing material, levels are cut every ``ap`` down to the lowest point of the contour.
   On every level the tool
     a. enters - on a falling contour obliquely along the contour, otherwise at the front,
     b. cuts towards -Z up to where the contour rises above the level,
     c. follows the contour upwards to the previous level (no step remains),
     d. retracts to the safe radius.
   Sections of one level separated by a rise that stays below the previous level are cut in
   one go.
3. Finishing (if switched on): to the start of the contour, optional G41/G42, the whole contour
   in one pass (arcs in radius mode G8), out to the safe radius, G40.
4. Back to the safe radius and in front of the part.
"""
from __future__ import annotations

from typing import List, Optional

from ..gcode import Emitter, TransformedEmitter
from ..geometry import (EPS, ContourError, Element, Row, contour_warnings, intervals_at,
                        polyline, segment_between, walk_back, walk_up)
from ..i18n import _
from ..material import Material
from .base import Cycle
from .params import CORNER_CHAMFER, CORNER_RADIUS
from .toolcheck import turning_tool_problems


class ContourTurner:
    """Roughing and finishing of a contour that is given in the work frame."""

    def __init__(self, emitter: Emitter, radius_sign: int, z_sign: int, clearance: float):
        self.work = TransformedEmitter(emitter, radius_sign, z_sign)
        self.radius_sign = radius_sign
        self.z_sign = z_sign
        self.clearance = clearance

    def run(self, elements: List[Element], start_radius: float, z_front: float, ap: float,
            allow_x: float, allow_z: float, feed_rough: float, feed_finish: float,
            do_rough=True, do_finish=True, compensation="off"):
        work = self.work
        safe_radius = start_radius + self.clearance
        if self.radius_sign < 0 and safe_radius > 0:
            raise ContourError(_("The bore is too small for the safety distance"))
        work.rapid(x=2 * safe_radius)
        work.rapid(z=z_front + self.clearance)
        if do_rough:
            offset_points = []
            for z, r in polyline(elements):
                offset_points.append((z + allow_z, r + allow_x / 2.0))
            self.rough(offset_points, start_radius, z_front, ap, feed_rough, safe_radius)
        if do_finish:
            self.finish(elements, z_front, feed_finish, safe_radius, compensation)
        work.rapid(x=2 * safe_radius)
        work.rapid(z=z_front + self.clearance)

    # ---------------------------------------------------------------- roughing
    @staticmethod
    def levels(points, start_radius: float, ap: float) -> List[float]:
        """Roughing levels from the start radius down to the lowest point of the contour."""
        lowest = min(r for _z, r in points)
        if start_radius <= lowest + 1e-6:
            return []
        levels = []
        level = start_radius - ap
        while level > lowest + 1e-6:
            levels.append(level)
            level -= ap
        if levels and levels[-1] - lowest < 0.05:
            levels[-1] = lowest
        else:
            levels.append(lowest)
        return levels

    def rough(self, points, start_radius, z_front, ap, feed, safe_radius):
        levels = self.levels(points, start_radius, ap)
        if not levels:
            return
        self.work.comment(_("Roughing: {count} levels").format(count=len(levels)))
        previous_level = start_radius
        for level in levels:
            for group in self._level_groups(points, level, z_front, previous_level):
                self._cut_group(points, group, level, previous_level, z_front, feed, safe_radius)
            previous_level = level

    @staticmethod
    def _level_groups(points, level, z_front, previous_level):
        """Sections of the contour below the level; neighbours that can be cut together form a
        group (the rise between them stays below the previous level)."""
        intervals = []
        for start, end in intervals_at(points, level):
            if end is not None:
                end_z = end.z
            else:
                end_z = points[-1][0]
            if end_z >= z_front - 1e-6:
                continue
            if start is not None and start.z >= z_front - 1e-6:
                start = None
            intervals.append((start, end))
        groups: List[list] = []
        for start, end in intervals:
            joinable = groups and groups[-1][-1][1] is not None and start is not None
            if joinable:
                gap = segment_between(points, groups[-1][-1][1], start)
                highest_in_gap = max(r for _z, r in gap)
                if highest_in_gap <= previous_level + EPS:
                    groups[-1].append((start, end))
                    continue
            groups.append([(start, end)])
        return groups

    def _cut_group(self, points, group, level, previous_level, z_front, feed, safe_radius):
        work = self.work
        first_start = group[0][0]
        if first_start is None:
            # the level starts in front of the part: enter from the front
            work.rapid(z=z_front + self.clearance)
            work.rapid(x=2 * level)
        else:
            # falling contour: enter obliquely along the contour from the previous level
            entry_path, _found = walk_back(points, first_start, previous_level, level)
            work.rapid(z=entry_path[0][0])
            work.rapid(x=2 * max(previous_level, entry_path[0][1]))
            work.follow(entry_path, feed)
        for index, (start, end) in enumerate(group):
            if index > 0:
                work.follow(segment_between(points, group[index - 1][1], start)[1:], feed)
            if end is not None:
                work.feed(z=end.z, f=feed)
            else:
                work.feed(z=points[-1][0], f=feed)
        last_end = group[-1][1]
        if last_end is not None:
            work.follow(walk_up(points, last_end, previous_level), feed)
        work.rapid(x=2 * safe_radius)

    # ---------------------------------------------------------------- finishing
    def compensation_code(self, compensation: str) -> Optional[str]:
        if compensation == "auto":
            if self.radius_sign * self.z_sign > 0:
                return "G42"
            return "G41"
        if compensation in ("G41", "G42"):
            return compensation
        return None

    def finish(self, elements, z_front, feed, safe_radius, compensation):
        work = self.work
        first = elements[0]
        work.comment(_("Finishing"))
        work.rapid(x=2 * safe_radius)
        work.rapid(z=max(first.z0, z_front) + self.clearance)
        work.rapid(x=2 * first.r0)
        has_arcs = any(element.kind == "arc" for element in elements)
        if has_arcs:
            work.comment(_("Contour with arcs in radius mode G8: X = radius, I/K = centre "
                           "relative"))
            work.set_radius_mode(True)
        code = self.compensation_code(compensation)
        if code:
            work.emit(code)
        work.feed(z=first.z0, f=feed)
        for element in elements:
            if element.kind == "line":
                work.feed(x=2 * element.r1, z=element.z1, f=feed)
            else:
                work.arc(2 * element.r1, element.z1, element.cz, element.cr, element.ccw, feed)
        work.feed(x=2 * safe_radius, f=feed)
        if code:
            work.emit("G40")
        if has_arcs:
            work.set_radius_mode(False)


def work_row(x_work: float, z_work: float, corner=None, size=0.0, angle=None, reference="A"):
    """Contour row in the work frame, optionally with a corner (R/C)."""
    row = Row("L", x_work, z_work)
    if corner and size > EPS:
        row.corner = corner
        row.csize = size
        row.cangle = angle
        row.cref = reference
    return row


def corner_code(kind: str) -> Optional[str]:
    """Contour corner word for a corner kind key: chamfer -> C, radius -> R."""
    if kind == CORNER_CHAMFER:
        return "C"
    if kind == CORNER_RADIUS:
        return "R"
    return None


class TurningCycle(Cycle):
    """Common behaviour of longitudinal turning, taper, chamfer/radius and contour.

    Subclasses only build their finished contour in the work frame (``work_elements``); the
    path is produced by ``ContourTurner``.
    """

    def work_elements(self, material: Optional[Material] = None) -> List[Element]:
        raise NotImplementedError

    def outline(self) -> List[Element]:
        elements = []
        for element in self.work_elements():
            elements.append(element.transformed(self.radius_sign, self.z_sign()))
        return elements

    def material_cuts(self, material=None) -> list:
        points = polyline(self.outline(), 2.0)
        if self.inside:
            kind = "inside"
        else:
            kind = "outside"
        cuts = [(kind, points)]
        if material is not None:
            # facing allowance in front of the contour that the roughing passes remove too
            elements = self.work_elements(material)
            start = self.start_radius_work(material, elements)
            front = self.front_work(elements)
            approach = self.approach_front(material, elements, start)
            if approach > front + 1e-6:
                lowest = None
                for element in elements:
                    for _z, radius in element.sample(10.0):
                        if lowest is None or radius < lowest:
                            lowest = radius
                sign = self.z_sign()
                real_radius = self.radius_sign * lowest
                cuts.append((kind, [(sign * front, real_radius), (sign * approach, real_radius)]))
        return cuts

    def z_range(self):
        z_values = []
        for element in self.outline():
            z_values.append(element.z0)
            z_values.append(element.z1)
        return min(z_values), max(z_values)

    def _outline_radii(self) -> List[float]:
        radii = []
        for element in self.outline():
            for _z, r in element.sample(10.0):
                radii.append(abs(r))
        return radii

    def max_d(self):
        return 2 * max(self._outline_radii())

    def inner_x(self, machine):
        return max(0.0, 2 * min(self._outline_radii()) - 2 * machine.clearance)

    def start_radius_work(self, material: Optional[Material], elements: List[Element]) -> float:
        """Start radius in the work frame: existing material above the contour range."""
        real_low, real_high = self.z_range()
        work_radii = []
        for element in elements:
            for _z, radius in element.sample(10.0):
                work_radii.append(radius)
        highest_contour = max(work_radii)
        if material is None:
            return highest_contour
        if self.inside:
            bore = material.min_inner(real_low, real_high)
            if bore is None:
                return highest_contour
            if bore < 1.0:
                raise ContourError(_("There is no pre-drilled hole between Z{low:g} and "
                                     "Z{high:g} - drill first (drilling tab) or enter an inner Ø "
                                     "for the stock").format(low=real_low, high=real_high))
            return max(-bore, highest_contour)
        outer = material.max_outer(real_low, real_high)
        if outer is None:
            return highest_contour
        return max(outer, highest_contour)

    def front_work(self, elements: List[Element]) -> float:
        return max(max(element.z0, element.z1) for element in elements)

    def contour_check(self) -> List[str]:
        return contour_warnings(self.work_elements())

    def tool_problems(self, tool) -> List[str]:
        roughing = bool(self.v.get("do_rough", True))
        return turning_tool_problems(tool, self.inside, self.z_sign() < 0,
                                     self.work_elements(), roughing)

    def approach_front(self, material, elements, start_radius) -> float:
        """Approach Z in the work frame: start of the contour, or the stock edge in front of it.

        If there is still material in front of the contour that is not larger than the start
        radius (e.g. the facing allowance), the passes start in front of that material.
        """
        front = self.front_work(elements)
        if material is None or self.z_sign() < 0:
            return front
        sign = self.z_sign()
        real_front = sign * front
        material_front = material.material_end(real_front, sign)
        stub_length = abs(material_front - real_front)
        if stub_length < 1e-6 or stub_length > 10.0:
            return front
        if self.inside:
            inner = material.min_inner(real_front, material_front)
            passable = inner is not None and inner >= -start_radius - 1e-6
        else:
            outer = material.max_outer(real_front, material_front)
            passable = outer is not None and outer <= start_radius + 1e-6
        if passable:
            return sign * material_front
        return front

    def body(self, emitter, machine, material):
        elements = self.work_elements(material)
        start = self.start_radius_work(material, elements)
        turner = ContourTurner(emitter, self.radius_sign, self.z_sign(), machine.clearance)
        turner.run(elements, start, self.approach_front(material, elements, start), self.ap,
                   self.allow_x, self.allow_z, self.f_rough, self.f_finish,
                   self.v.get("do_rough", True), self.v.get("do_finish", True),
                   self.v.get("comp", "off"))
