# Drehzyklen – Zyklenprogrammierung für LinuxCNC-Drehmaschinen (gmoccapy)
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
"""Grafische Darstellung eines Drehwerkzeugs (Schneidplatte, Halter, Winkel) – reines Cairo."""
from __future__ import annotations

import math

from .helpdraw import (BACKGROUND, DIMENSION, EDGE, HINT, MATERIAL, PATH, TEXT, Sketch,
                       value_text)
from .tools import HAND_TEXT, KIND_LABELS

INSERT_COLOR = (0.95, 0.78, 0.30)
HOLDER_COLOR = (0.48, 0.50, 0.56)


def _direction(angle_deg):
    radians = math.radians(angle_deg)
    return math.cos(radians), math.sin(radians)


def _add(point, vector, length=1.0):
    return point[0] + vector[0] * length, point[1] + vector[1] * length


def draw_tool(cr, width, height, tool, flip=False, font=None):
    """Zeichnet das Werkzeug mit der Schneide im Bildmittelpunkt."""
    if font is None:
        font = max(9.0, min(12.5, min(width / 46, height / 24)))
    cr.set_source_rgb(*BACKGROUND)
    cr.paint()
    if tool is None:
        cr.set_source_rgb(*HINT)
        cr.set_font_size(font)
        cr.move_to(10, 10 + font)
        cr.show_text("Kein Werkzeug gewählt")
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
        _header(cr, font, tool, "Bohrer auf Drehmitte, bohrt nach −Z")
        return

    sector = tool.sector()
    if sector is None:
        _header(cr, font, tool, "Typ, Hand und Lage angeben (Felder unten) – erst dann ist eine "
                                "Prüfung möglich")
        return
    low, high = sector
    edge_low = _direction(low)
    edge_high = _direction(high)
    bisector = _direction((low + high) / 2)

    # Werkstück: außen unterhalb, innen oberhalb der Schneide
    if tool.position == "innen":
        sketch.polygon([(-26, 0.0), (26, 0.0), (26, 16), (-26, 16)], fill=MATERIAL)
        sketch.polygon([(-26, -16), (26, -16), (26, -14), (-26, -14)], fill=MATERIAL)
    else:
        sketch.polygon([(-26, 0.0), (26, 0.0), (26, -14), (-26, -14)], fill=MATERIAL)

    if tool.kind == "groove":
        half = max(tool.width, 1.0)
        blade = [(-half, 0), (half, 0), (half, length * 1.6), (-half, length * 1.6)]
        if tool.position == "innen":
            blade = [(z, -r) for z, r in blade]
        sketch.polygon(blade, fill=INSERT_COLOR, stroke=EDGE)
        holder_start = _add(tip, bisector, length * 1.6)
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

    # Halter entlang der Winkelhalbierenden
    shank_end = _add(holder_start, bisector, 12.0)
    normal = (-bisector[1], bisector[0])
    shank = [_add(holder_start, normal, 3.0), _add(holder_start, normal, -3.0),
             _add(shank_end, normal, -3.0), _add(shank_end, normal, 3.0)]
    sketch.polygon(shank, fill=HOLDER_COLOR, stroke=HINT, line_width=0.8)
    sketch.dot(tip, (1, 0.3, 0.3), 3.5)

    # Hauptschnittrichtung
    if tool.hand == "rechts":
        sketch.arrow((-3, -6), (-15, -6), PATH, 1.8)
        sketch.label((-15, -9), "Schnitt", "−Z", anchor="l")
    elif tool.hand == "links":
        sketch.arrow((3, -6), (15, -6), PATH, 1.8)
        sketch.label((15, -9), "Schnitt", "+Z", anchor="r")
    if tool.kind not in ("groove", "thread", "R"):
        sketch.label((-22, 22), "κ", value_text(tool.lead_angle, 1) + "°", anchor="l")
    if tool.kind == "groove":
        sketch.label((-22, 22), "b", value_text(tool.width), anchor="l")
    sketch.label((-22, 18), "r", value_text(tool.nose_radius), anchor="l")
    _header(cr, font, tool, f"{HAND_TEXT.get(tool.hand, tool.hand)}, {tool.position} · "
                            f"Plattensektor {low:.0f}°…{high:.0f}° · Quelle: {tool.source}")


def _header(cr, font, tool, detail):
    cr.set_font_size(font * 1.1)
    cr.select_font_face("Sans", 0, 1)
    cr.set_source_rgb(*DIMENSION)
    cr.move_to(10, font * 1.5)
    cr.show_text(f"T{tool.number}  {KIND_LABELS.get(tool.kind, tool.kind)}")
    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(font * 0.9)
    cr.set_source_rgb(*TEXT)
    cr.move_to(10, font * 2.9)
    cr.show_text(detail)
    if tool.comment:
        cr.set_source_rgb(*HINT)
        cr.move_to(10, font * 4.1)
        cr.show_text(tool.comment)
