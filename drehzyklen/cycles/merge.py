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
"""Several turning steps as one contour.

Every cycle turns its own piece of the contour; at the transitions the corner radius of the
tool leaves small noses. Turned as one contour the tool runs through in one go. Two ways use
the same contour:

* merge (``merge_steps``): the steps are replaced by one contour cycle,
* finishing step (``FinishContour``): the steps stay, a finishing pass over all of them follows.

The finished contour of each step comes from ``outline()`` (real frame: Z, radius). Run-in and
run-out faces of long turning (radial lines at the start and end) are left out between two
steps; the pieces are joined with a line where they do not meet. Chamfers/radii (edge steps)
become a corner of the contour point they belong to.

A facing step is a face of the contour (a radial line at Z2). As the first piece it runs from
the axis side (outside: from the centre, inside: from the outer edge) to where the next piece
starts; later it starts where the piece before ends. Faces that reach further are cut there.
"""
from __future__ import annotations

from typing import List, Tuple

from ..geometry import ContourError, Element, Row, distance, rows_to_text
from ..i18n import _
from .params import CORNER_CHAMFER

FACE_KEYS = ("facing",)                       # steps whose piece is one face (radial line)
PIECE_KEYS = ("long", "taper", "contour") + FACE_KEYS   # steps with a piece of the contour
CORNER_KEYS = ("edge",)                        # steps that only round/chamfer one edge
MERGE_KEYS = PIECE_KEYS + CORNER_KEYS
TECH_KEYS = ("tool", "tool_ok", "speed_feed", "speed", "spindle_dir", "coolant")
TOLERANCE = 1e-4


def _radial(element: Element) -> bool:
    return element.kind == "line" and abs(element.z1 - element.z0) < TOLERANCE


def _direction(elements: List[Element]) -> int:
    """-1: the contour runs towards -Z (usual), +1 towards +Z, 0 unknown."""
    delta = elements[-1].z1 - elements[0].z0
    if abs(delta) < TOLERANCE:
        return 0
    return 1 if delta > 0 else -1


def source_step(steps):
    """Step whose cutting data the contour takes: the first turning piece, a facing step only
    if there is no other piece."""
    pieces = [op for op in steps if op.KEY in PIECE_KEYS]
    for op in pieces:
        if op.KEY not in FACE_KEYS:
            return op
    return pieces[0]


def _face_piece(op, before, after) -> List[Element]:
    """The face of a facing step as a piece of the contour (see module docstring)."""
    face = op.outline()[0]
    z, low, high = face.z0, min(face.r0, face.r1), max(face.r0, face.r1)

    def within(point):
        return (point is not None and abs(point[0] - z) < TOLERANCE
                and low - TOLERANCE <= point[1] <= high + TOLERANCE)
    if before is None:
        start = high if op.inside else low
        end = low if op.inside else high
        if within(after):
            end = after[1]
    else:
        previous = before[1]
        start = previous if within(before) else min((low, high), key=lambda r: abs(r - previous))
        end = high if abs(high - start) >= abs(low - start) else low
    if abs(end - start) < TOLERANCE:
        return []
    return [Element("line", z, start, z, end)]


def check_steps(ops, numbers: List[int], minimum: int = 2) -> List[str]:
    """Problems that prevent turning the steps (1-based numbers) as one contour (minimum: number
    of steps needed - merging needs two, a finishing step one)."""
    problems = []
    if not numbers or numbers[0] < 1 or numbers[-1] > len(ops):
        return [_("Step {number} does not exist").format(
            number=numbers[-1] if numbers else 0)]
    steps = [(number, ops[number - 1]) for number in numbers]
    pieces = [(number, op) for number, op in steps if op.KEY in PIECE_KEYS]
    if len(pieces) < 1 or len(steps) < minimum:
        problems.append(_("Choose at least two steps, one of them long turning, taper, "
                          "contour or facing"))
        return problems
    for number, op in steps:
        if op.KEY not in MERGE_KEYS:
            problems.append(_("Step {number} ({title}) cannot be part of a contour").format(
                number=number, title=op.title()))
    first = pieces[0][1]
    for number, op in steps:
        if op.KEY not in MERGE_KEYS:
            continue
        if op.inside != first.inside:
            problems.append(_("Step {number}: all steps must be outside or all inside").format(
                number=number))
        if op.KEY in PIECE_KEYS and int(op.tool) != int(first.tool):
            problems.append(_("Step {number}: other tool (T{tool}) - one contour is turned "
                              "with one tool").format(number=number, tool=int(op.tool)))
        if op.resolve_error:
            problems.append(_("Step {number}: {error}").format(number=number,
                                                               error=op.resolve_error))
    return problems


def contour_elements(ops, numbers: List[int]) -> Tuple[List[Element], List]:
    """The finished contour of the steps (in program order) and their edge steps."""
    steps = [ops[number - 1] for number in numbers]
    pieces = [op for op in steps if op.KEY in PIECE_KEYS]
    edges = [op for op in steps if op.KEY in CORNER_KEYS]
    outlines = []
    for index, op in enumerate(pieces):
        try:
            elements = list(op.outline())
        except (ContourError, ValueError, TypeError, ZeroDivisionError) as error:
            raise ContourError(_("{title}: {error}").format(title=op.title(), error=error))
        if op.KEY not in ("contour",) + FACE_KEYS:
            # run-in/run-out faces of long turning only at the start and the end
            if index > 0:
                while elements and _radial(elements[0]):
                    elements.pop(0)
            if index < len(pieces) - 1:
                while elements and _radial(elements[-1]):
                    elements.pop()
        outlines.append(elements)
    result: List[Element] = []
    direction = 0
    for index, op in enumerate(pieces):
        elements = outlines[index]
        if op.KEY in FACE_KEYS:
            after = next((outline[0].start for outline in outlines[index + 1:] if outline),
                         None)
            elements = _face_piece(op, result[-1].end if result else None, after)
        if not elements:
            continue
        piece_direction = _direction(elements)
        if direction and piece_direction and piece_direction != direction:
            raise ContourError(_("{title}: runs in the other Z direction than the steps "
                                 "before").format(title=op.title()))
        direction = direction or piece_direction
        if result and distance(result[-1].end, elements[0].start) > TOLERANCE:
            start, end = result[-1].end, elements[0].start
            if (end[0] - start[0]) * (direction or -1) < -TOLERANCE:
                raise ContourError(_("{title}: starts before the end of the step before - the "
                                     "steps overlap").format(title=op.title()))
            result.append(Element("line", start[0], start[1], end[0], end[1]))
        result.extend(elements)
    if not result:
        raise ContourError(_("The steps have no contour"))
    return result, edges


def _edge_at(point, edges):
    for edge in edges:
        if abs(edge.diameter / 2 - abs(point[1])) < 1e-3 and abs(edge.z_start - point[0]) < 1e-3:
            return edge
    return None


def _corner_for(point, edges):
    """Corner (kind, size, angle, reference edge) of an edge step on this contour point."""
    edge = _edge_at(point, edges)
    if edge is None:
        return None
    reference = "A" if edge.is_back_edge() else "B"
    if edge.kind == CORNER_CHAMFER:
        angle = abs(edge.angle)
        return "C", edge.size, (None if abs(angle - 45) < 1e-6 else angle), reference
    return "R", edge.size, None, reference


def _faces_for_edges(elements: List[Element], edges, inside: bool) -> List[Element]:
    """An edge on the first or last point of the contour needs a face there to become a
    corner: a short radial line is added (like the run-in of long turning)."""
    towards_axis = 1.0 if inside else -1.0
    first = _edge_at(elements[0].start, edges)
    if first is not None and not _radial(elements[0]):
        z, r = elements[0].start
        length = first.radial_leg() + 0.5
        elements = [Element("line", z, r + towards_axis * length, z, r)] + elements
    last = _edge_at(elements[-1].end, edges)
    if last is not None and not _radial(elements[-1]):
        z, r = elements[-1].end
        length = last.radial_leg() + 0.5
        elements = elements + [Element("line", z, r, z, r - towards_axis * length)]
    return elements


def elements_to_rows(elements: List[Element], edges=()) -> List[Row]:
    """Lines and arcs (real frame) as contour rows; edge steps become corners."""
    rows = [Row("L", 2 * abs(elements[0].r0), elements[0].z0)]
    for element in elements:
        if element.kind == "arc":
            row = Row("G3" if element.ccw else "G2", 2 * abs(element.r1), element.z1)
            row.arc = "IK"
            row.ai = element.cr - element.r0
            row.ak = element.cz - element.z0
        else:
            row = Row("L", 2 * abs(element.r1), element.z1)
        rows.append(row)
    for row in rows[1:-1]:
        corner = _corner_for((row.z, row.x / 2), edges)
        if corner is not None:
            row.corner, row.csize, row.cangle, row.cref = corner
    return rows


def unused_edges(rows: List[Row], edges) -> List:
    """Edge steps without a matching corner point in the contour."""
    missing = []
    for edge in edges:
        if not any(abs(edge.diameter / 2 - row.x / 2) < 1e-3 and abs(edge.z_start - row.z) < 1e-3
                   for row in rows[1:-1]):
            missing.append(edge)
    return missing


def contour_text(ops, numbers: List[int], minimum: int = 2) -> str:
    """Contour text of the steps (raises ContourError)."""
    problems = check_steps(ops, numbers, minimum)
    if problems:
        raise ContourError(problems[0])
    elements, edges = contour_elements(ops, numbers)
    if edges:
        inside = ops[numbers[0] - 1].inside
        elements = _faces_for_edges(elements, edges, inside)
    rows = elements_to_rows(elements, edges)
    missing = unused_edges(rows, edges)
    if missing:
        raise ContourError(_("{title}: the edge Ø{diameter:g} Z{z:g} is not a corner of the "
                             "contour").format(title=missing[0].title(),
                                               diameter=missing[0].diameter,
                                               z=missing[0].z_start))
    return rows_to_text(rows)


def merged_values(ops, numbers: List[int]) -> dict:
    """Values of the contour cycle that replaces the steps (cutting data of the first one)."""
    first = source_step([ops[number - 1] for number in numbers])
    values = {key: first.raw[key] for key in TECH_KEYS if key in first.raw}
    for key in ("ap", "f_rough", "f_finish", "allow_x", "allow_z", "do_rough", "do_finish",
                "comp"):
        if key in first.v:
            values[key] = first.v[key]
    values["side"] = first.v.get("side")
    values["contour"] = contour_text(ops, numbers)
    values["corner_mode"] = "none"
    return values
