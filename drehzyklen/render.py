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
"""Drawing the preview (plain Cairo, independent of GTK).

Scene (dictionary):
  stock       Stock object (stock + chuck) or None
  before      material before the operation - the removal is highlighted in colour
  after       material after the operation or after the whole program
  path        tool path (segments)
  outlines    finished contours [(op, elements, inside)]
  highlight   number of the highlighted operation (other paths dimmed)

View: view = (zoom, shift_x, shift_y) in pixels around the centre of the picture.
"""
from __future__ import annotations

import math

from .drawtext import show_text, text_width
from .i18n import _

BACKGROUND = (0.11, 0.12, 0.14)
COLORS = {"rapid": (0.95, 0.35, 0.3), "feed": (0.35, 0.7, 1.0),
          "arc": (0.4, 0.95, 0.5), "thread": (0.95, 0.45, 0.95)}
MATERIAL = (0.50, 0.53, 0.58)
REMOVE = (1.0, 0.62, 0.18, 0.55)
REMOVED_EARLIER = (0.50, 0.53, 0.58, 0.16)
JAW = (0.30, 0.31, 0.34)


def segment_points(segment):
    if segment.kind != "arc":
        return [(segment.z0, segment.x0 / 2), (segment.z1, segment.x1 / 2)]
    center_z, center_r = segment.cz, segment.cr
    start_angle = math.atan2(segment.x0 / 2 - center_r, segment.z0 - center_z)
    end_angle = math.atan2(segment.x1 / 2 - center_r, segment.z1 - center_z)
    sweep = end_angle - start_angle
    if segment.ccw:
        while sweep <= 1e-12:
            sweep += 2 * math.pi
    else:
        while sweep >= -1e-12:
            sweep -= 2 * math.pi
    radius = math.hypot(segment.x0 / 2 - center_r, segment.z0 - center_z)
    steps = max(4, int(abs(sweep) / 0.05))
    points = []
    for step in range(steps + 1):
        angle = start_angle + sweep * step / steps
        points.append((center_z + radius * math.cos(angle), center_r + radius * math.sin(angle)))
    return points


# old name, used by tests and tools
seg_points = segment_points


def outline_points(elements):
    points = []
    for element in elements:
        samples = element.sample()
        if points:
            samples = samples[1:]
        points.extend(samples)
    return points


def chuck_jaws(stock):
    """Chuck jaws as rectangles (z_from, z_to, r_from, r_to)."""
    if stock is None:
        return []
    chuck_z = stock.z_chuck
    height = max(6.0, 0.22 * stock.D)
    length = max(8.0, min(25.0, 0.35 * stock.L))
    radius = stock.D / 2
    return [(chuck_z - length, chuck_z, radius, radius + height)]


class ViewTransform:
    """Model (z, r) -> screen, fitted plus zoom/shift."""

    def __init__(self, width, height, bounds, back_tool, legend_height, view):
        z_min, z_max, r_min, r_max = bounds
        padding = 14
        usable_height = height - 2 * padding - legend_height
        self.scale = min((width - 2 * padding) / max(z_max - z_min, 1e-3),
                         usable_height / max(r_max - r_min, 1e-3))
        if back_tool:
            self.flip = 1                     # X+ up
        else:
            self.flip = -1                    # tool in front of the centre line: X+ down
        self.origin_x = (padding + ((width - 2 * padding) - (z_max - z_min) * self.scale) / 2
                         - z_min * self.scale)
        top = padding + (usable_height - (r_max - r_min) * self.scale) / 2
        if self.flip == 1:
            self.origin_y = top + r_max * self.scale
        else:
            self.origin_y = top - r_min * self.scale
        self.center_x = width / 2
        self.center_y = height / 2
        if view is None:
            view = (1.0, 0.0, 0.0)
        self.zoom, self.pan_x, self.pan_y = view

    def __call__(self, z, r):
        x = self.origin_x + z * self.scale
        y = self.origin_y - self.flip * r * self.scale
        x = self.center_x + (x - self.center_x) * self.zoom + self.pan_x
        y = self.center_y + (y - self.center_y) * self.zoom + self.pan_y
        return x, y


def draw_preview(cr, width, height, path=(), outlines=(), back_tool=False, show_rapids=True,
                 font=11, text_color=(0.75, 0.75, 0.78), stock=None, before=None, after=None,
                 highlight=None, removed_color=REMOVE, view=None):
    cr.set_source_rgb(*BACKGROUND)
    cr.paint()
    cr.set_font_size(font)
    segments = []
    for segment in path:
        if show_rapids or segment.kind != "rapid":
            segments.append(segment)
    points = []
    for segment in path:
        points.extend(segment_points(segment))
    for _number, elements, _inside in outlines:
        points.extend(outline_points(elements))
    polygons_before = []
    polygons_after = []
    if before is not None:
        polygons_before = before.polygons()
    if after is not None:
        polygons_after = after.polygons()
    for polygon in polygons_before + polygons_after:
        points.extend(polygon)
    jaws = chuck_jaws(stock)
    for z_from, z_to, _r_from, r_to in jaws:
        points.append((z_from, r_to))
        points.append((z_to, r_to))
    if not points:
        cr.set_source_rgb(*text_color)
        cr.move_to(12, 12 + font)
        show_text(cr, _("No preview"))
        return
    z_min = min(point[0] for point in points)
    z_max = max(point[0] for point in points)
    r_max = max(max(point[1] for point in points), 1.0)
    r_min = min(0.0, min(point[1] for point in points))
    to_screen = ViewTransform(width, height, (z_min, z_max, r_min, r_max), back_tool, font + 4,
                              view)

    def fill_polygon(polygon, color):
        cr.move_to(*to_screen(*polygon[0]))
        for point in polygon[1:]:
            cr.line_to(*to_screen(*point))
        cr.close_path()
        if len(color) == 4:
            cr.set_source_rgba(*color)
        else:
            cr.set_source_rgb(*color)
        cr.fill()

    # chuck jaws
    for z_from, z_to, r_from, r_to in jaws:
        fill_polygon([(z_from, r_from), (z_to, r_from), (z_to, r_to), (z_from, r_to)], JAW)
        cr.set_source_rgb(0.55, 0.56, 0.6)
        cr.set_line_width(1)
        x_from, y_from = to_screen(z_from, r_from)
        x_to, y_to = to_screen(z_to, r_to)
        cr.rectangle(min(x_from, x_to), min(y_from, y_to), abs(x_to - x_from), abs(y_to - y_from))
        cr.stroke()
    # material: before (removal in colour) and after
    if before is not None and after is not None:
        for polygon in polygons_before:
            fill_polygon(polygon, removed_color)
    elif before is not None:
        for polygon in polygons_before:
            fill_polygon(polygon, MATERIAL)
    for polygon in polygons_after:
        fill_polygon(polygon, MATERIAL)
    # stock outline
    if stock is not None:
        z_inside = stock.z_chuck - min(20.0, max(5.0, 0.25 * stock.L))
        z_front = stock.z_front
        cr.set_source_rgba(0.85, 0.85, 0.85, 0.7)
        cr.set_line_width(1)
        cr.set_dash([5, 4])
        for radius in (stock.D / 2, stock.d / 2):
            if radius > 0:
                cr.move_to(*to_screen(z_inside, radius))
                cr.line_to(*to_screen(z_front, radius))
        cr.move_to(*to_screen(z_front, stock.d / 2))
        cr.line_to(*to_screen(z_front, stock.D / 2))
        cr.stroke()
        cr.set_dash([])
    # centre line
    cr.set_source_rgba(0.8, 0.8, 0.8, 0.5)
    cr.set_dash([8, 4, 2, 4])
    cr.set_line_width(1)
    cr.move_to(*to_screen(z_min - 2, 0))
    cr.line_to(*to_screen(z_max + 2, 0))
    cr.stroke()
    cr.set_dash([])
    # finished contours
    for number, elements, _inside in outlines:
        polygon = outline_points(elements)
        if len(polygon) < 2:
            continue
        cr.move_to(*to_screen(*polygon[0]))
        for point in polygon[1:]:
            cr.line_to(*to_screen(*point))
        if highlight is None or highlight == number:
            alpha = 0.95
        else:
            alpha = 0.35
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.set_line_width(1.6)
        cr.stroke()
    # tool path
    for segment in segments:
        dimmed = highlight is not None and segment.op != highlight
        color = COLORS[segment.kind]
        if dimmed:
            cr.set_source_rgba(*color, 0.22)
        else:
            cr.set_source_rgba(*color, 1.0)
        if segment.kind == "rapid":
            cr.set_line_width(0.9)
            cr.set_dash([4, 3])
        else:
            cr.set_line_width(1.4)
            cr.set_dash([])
        segment_line = segment_points(segment)
        cr.move_to(*to_screen(*segment_line[0]))
        for point in segment_line[1:]:
            cr.line_to(*to_screen(*point))
        cr.stroke()
    cr.set_dash([])
    # zero point
    zero_x, zero_y = to_screen(0, 0)
    cr.set_source_rgb(1, 0.85, 0.2)
    cr.arc(zero_x, zero_y, 4, 0, 2 * math.pi)
    cr.fill()
    # legend
    cr.set_source_rgba(*BACKGROUND, 0.85)
    cr.rectangle(0, height - font - 10, width, font + 10)
    cr.fill()
    cr.set_source_rgb(*text_color)
    cr.move_to(8, height - 6)
    zoom_text = ""
    if view is not None and abs(view[0] - 1.0) > 1e-3:
        zoom_text = f"   Zoom {view[0]:.1f}×"
    show_text(cr, f"Z {z_min:.1f} … {z_max:.1f}   Ø max {2 * r_max:.1f}{zoom_text}")
    x = width - 8
    items = [("thread", _("Thread")), ("arc", _("Arc")), ("feed", _("Feed")),
             ("rapid", _("Rapid"))]
    if before is not None and after is not None:
        items.append(("remove", _("Removal")))
    for name, label in items:
        label_width = text_width(cr, label)
        x -= label_width
        if name == "remove":
            cr.set_source_rgb(*removed_color[:3])
        else:
            cr.set_source_rgb(*COLORS[name])
        cr.move_to(x, height - 6)
        show_text(cr, label)
        x -= 12
