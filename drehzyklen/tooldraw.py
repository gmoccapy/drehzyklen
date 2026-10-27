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
"""Drawing of a turning tool (insert, holder, angles) - plain Cairo."""
from __future__ import annotations

import math

from .drawtext import show_text
from .helpdraw import (BACKGROUND, DIMENSION, EDGE, HINT, MATERIAL, PATH, TEXT, Sketch,
                       value_text)
from .i18n import _
from .tools import HAND_TEXT, INSIDE, LEFT, RIGHT, table_angles

INSERT_COLOR = (0.95, 0.78, 0.30)
HOLDER_COLOR = (0.48, 0.50, 0.56)


def _direction(angle_deg):
    radians = math.radians(angle_deg)
    return math.cos(radians), math.sin(radians)


def _add(point, vector, length=1.0):
    return point[0] + vector[0] * length, point[1] + vector[1] * length


def draw_tool(cr, width, height, tool, flip=False, font=None):
    """Draws the tool with its cutting tip in the centre of the picture."""
    if font is None:
        font = max(9.0, min(12.5, min(width / 46, height / 24)))
    cr.set_source_rgb(*BACKGROUND)
    cr.paint()
    if tool is None:
        cr.set_source_rgb(*HINT)
        cr.set_font_size(font)
        cr.move_to(10, 10 + font)
        show_text(cr, _("No tool selected"))
        return
    sketch = Sketch(cr, 0, font * 4, width, height - font * 4, (-26, 26, -16, 26), flip, font)
    length = 10.0
    tip = (0.0, 0.0)

    if tool.kind == "drill":
        radius = 4.0
        body = [(0, 0), (4, radius), (24, radius), (24, -radius), (4, -radius)]
        sketch.polygon([(-26, -12), (-2, -12), (-2, 12), (-26, 12)], fill=MATERIAL)
        sketch.polygon(body, fill=INSERT_COLOR, stroke=EDGE)
        sketch.axis(-26, 26)
        sketch.arrow((6, -6), (-6, -6), PATH, 1.6)
        sketch.label((10, radius + 3), "Ø", value_text(tool.diameter))
        _header(cr, font, tool, _("Drill on the centre line, drills towards -Z"))
        return

    sector = tool.sector()
    if sector is None:
        _header(cr, font, tool, _("Enter type, hand and position (fields below) - only then "
                                  "can the tool be checked"))
        return
    low, high = sector
    edge_low = _direction(low)
    edge_high = _direction(high)
    bisector = _direction((low + high) / 2)

    # workpiece: outside below, inside above the cutting tip
    if tool.position == INSIDE:
        sketch.polygon([(-26, 0.0), (26, 0.0), (26, 16), (-26, 16)], fill=MATERIAL)
        sketch.polygon([(-26, -16), (26, -16), (26, -14), (-26, -14)], fill=MATERIAL)
    else:
        sketch.polygon([(-26, 0.0), (26, 0.0), (26, -14), (-26, -14)], fill=MATERIAL)

    if tool.kind == "groove":
        flanks = _groove_flanks(tool, sector)
        blade_length = length * 1.6
        (left_corner, left_edge), (right_corner, right_edge) = flanks
        left_end = _add(left_corner, _direction(left_edge), blade_length)
        right_end = _add(right_corner, _direction(right_edge), blade_length)
        sketch.polygon([left_corner, right_corner, right_end, left_end], fill=INSERT_COLOR,
                       stroke=EDGE)
        holder_start = ((left_end[0] + right_end[0]) / 2, (left_end[1] + right_end[1]) / 2)
    elif tool.kind == "R":
        center = _add(tip, bisector, 4.0)
        cr.set_source_rgb(*INSERT_COLOR)
        center_x, center_y = sketch.to_screen(*center)
        cr.arc(center_x, center_y, 4.0 * sketch.scale, 0, 2 * math.pi)
        cr.fill()
        holder_start = _add(tip, bisector, 8.0)
    else:
        corner_low = _add(tip, edge_low, length)
        corner_high = _add(tip, edge_high, length)
        if tool.kind in ("T", "thread"):
            insert = [tip, corner_low, corner_high]
            holder_start = ((corner_low[0] + corner_high[0]) / 2, (corner_low[1] + corner_high[1]) / 2)
        else:
            far_corner = (corner_low[0] + corner_high[0] - tip[0],
                          corner_low[1] + corner_high[1] - tip[1])
            insert = [tip, corner_low, far_corner, corner_high]
            holder_start = ((tip[0] + far_corner[0]) / 2, (tip[1] + far_corner[1]) / 2)
        sketch.polygon(insert, fill=INSERT_COLOR, stroke=EDGE)
        sketch.angle(tip, math.radians(low), math.radians(high), 34, "ε",
                     value_text(high - low, 1) + "°", label_distance=8)

    # holder: direction from the orientation Q of the tool.tbl, otherwise along the bisector
    shank = _direction(tool.shank_direction())
    shank_end = _add(holder_start, shank, 12.0)
    normal = (-shank[1], shank[0])
    holder = [_add(holder_start, normal, 3.0), _add(holder_start, normal, -3.0),
              _add(shank_end, normal, -3.0), _add(shank_end, normal, 3.0)]
    sketch.polygon(holder, fill=HOLDER_COLOR, stroke=HINT, line_width=0.8)
    sketch.dot(tip, (1, 0.3, 0.3), 3.5)
    angles_text = _draw_table_angles(sketch, tool, tip, flip)

    # main cutting direction
    if tool.hand == RIGHT:
        sketch.arrow((-3, -6), (-15, -6), PATH, 1.8)
        sketch.label((-15, -9), _("Cut"), "-Z", anchor="l")
    elif tool.hand == LEFT:
        sketch.arrow((3, -6), (15, -6), PATH, 1.8)
        sketch.label((15, -9), _("Cut"), "+Z", anchor="r")
    if tool.kind not in ("groove", "thread", "R"):
        sketch.label((-22, 22), "κ", value_text(tool.lead_angle, 1) + "°", anchor="l")
    if tool.kind == "groove":
        sketch.label((-22, 22), "b", value_text(tool.width), anchor="l")
    sketch.label((-22, 18), "r", value_text(tool.nose_radius), anchor="l")
    _header(cr, font, tool, _("{hand}, {position} · {angles} · source: {source}").format(
        hand=_(HAND_TEXT.get(tool.hand, tool.hand)), position=tool.position_text(),
        angles=angles_text, source=tool.source_text()))


def _screen_point(sketch, center, model_angle, radius_px):
    """Screen point at radius_px from the model point center in the direction model_angle."""
    center_x, center_y = sketch.to_screen(*center)
    if sketch.flip:
        screen_angle = model_angle
    else:
        screen_angle = -model_angle
    return (center_x + radius_px * math.cos(screen_angle),
            center_y + radius_px * math.sin(screen_angle))


def _groove_flanks(tool, sector):
    """Corners of the cutting edge with the direction of their side flank (model degrees).

    I/J of a grooving tool are the directions of the two side flanks. Each flank starts at its
    own corner and leans inwards (side clearance): the left flank towards +Z, the right one
    towards -Z - the blade gets narrower towards the back and does not jam in the groove.
    """
    half = max(tool.width, 1.0)
    edge_one, edge_two = sector
    if math.cos(math.radians(edge_one)) > math.cos(math.radians(edge_two)):
        left_edge, right_edge = edge_one, edge_two
    else:
        left_edge, right_edge = edge_two, edge_one
    return [((-half, 0.0), left_edge), ((half, 0.0), right_edge)]


def _table_value(model_degrees, back_tool):
    """Model angle -> tool.tbl angle (0…360°) for comparing with I/J."""
    if back_tool:
        return (-model_degrees) % 360.0
    return model_degrees % 360.0


def _arc(sketch, center, from_degrees, to_degrees, radius_px, color):
    """Arc around a model point between two model angles (as Sketch.angle, without label)."""
    cr = sketch.cr
    center_x, center_y = sketch.to_screen(*center)
    if sketch.flip:
        screen_from, screen_to = math.radians(from_degrees), math.radians(to_degrees)
    else:
        screen_from, screen_to = -math.radians(from_degrees), -math.radians(to_degrees)
    cr.set_source_rgb(*color)
    cr.set_line_width(1.2)
    cr.new_sub_path()
    cr.arc(center_x, center_y, radius_px, min(screen_from, screen_to),
           max(screen_from, screen_to))
    cr.stroke()


def _draw_groove_angles(sketch, tool, front, back, back_tool):
    """Grooving tool: I and J at the corner of their flank, measured from Z+; the small arc
    between the dashed vertical and the flank is the side clearance."""
    radius_px = 70
    for (corner, edge), side in zip(_groove_flanks(tool, tool.sector()), (-1, 1)):
        value = _table_value(edge, back_tool)
        if abs(((value - front) + 180.0) % 360.0 - 180.0) < 0.05:
            symbol, shown = "I", front
        else:
            symbol, shown = "J", back
        if math.sin(math.radians(edge)) >= 0:
            vertical = 90.0
        else:
            vertical = -90.0
        clearance = abs(edge - vertical)
        reference_end = sketch.to_model(*_screen_point(sketch, corner, math.radians(vertical),
                                                       radius_px + 14))
        sketch.line(corner, reference_end, PATH, 0.8, [4, 3])
        _arc(sketch, corner, vertical, edge, radius_px, PATH)
        label_point = sketch.to_model(*_screen_point(sketch, corner, math.radians(vertical),
                                                     radius_px))
        if side < 0:
            anchor = "r"
        else:
            anchor = "l"
        sketch.label(label_point, symbol,
                     _("{angle}° (clearance {clearance}°)").format(
                         angle=value_text(shown, 1), clearance=value_text(clearance, 1)),
                     anchor=anchor, dx=side * 8, color=PATH)


def _draw_table_angles(sketch, tool, tip, flip):
    """Front and back angle as in the tool.tbl: clockwise from Z+ (LinuxCNC view)."""
    back_tool = not flip
    if tool.angles_from_table:
        front, back, orientation = tool.front_angle, tool.back_angle, tool.orientation
    else:
        angles = table_angles(tool, back_tool)
        if angles is None:
            return ""
        front, back, orientation = angles
    # blue like the cutting direction, so that the arcs stand out against the yellow insert
    sketch.line(tip, (24.0, 0.0), PATH, 0.8, [4, 3])
    sketch.label((24.0, 0.0), "Z+", None, anchor="r", dy=-sketch.font * 0.9 * sketch.sign,
                 color=PATH)
    if tool.kind == "groove":
        _draw_groove_angles(sketch, tool, front, back, back_tool)
        return f"I {value_text(front, 1)}°  J {value_text(back, 1)}°  Q{orientation}"
    for value, symbol, radius in ((front, "I", 58), (back, "J", 76)):
        if back_tool:
            model = -value
        else:
            model = value
        sketch.angle(tip, 0.0, math.radians(model), radius, symbol, value_text(value, 1) + "°",
                     label_distance=10, color=PATH)
        # thin line along the edge up to the arc: shows which edge the angle belongs to
        edge_end = sketch.to_model(*_screen_point(sketch, tip, math.radians(model), radius))
        sketch.line(tip, edge_end, PATH, 1.6)
    return f"I {value_text(front, 1)}°  J {value_text(back, 1)}°  Q{orientation}"


def _header(cr, font, tool, detail):
    cr.set_font_size(font * 1.1)
    cr.select_font_face("Sans", 0, 1)
    cr.set_source_rgb(*DIMENSION)
    cr.move_to(10, font * 1.5)
    show_text(cr, f"T{tool.number}  {tool.kind_text()}")
    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(font * 0.9)
    cr.set_source_rgb(*TEXT)
    cr.move_to(10, font * 2.9)
    show_text(cr, detail)
    if tool.comment:
        cr.set_source_rgb(*HINT)
        cr.move_to(10, font * 4.1)
        show_text(cr, tool.comment)
