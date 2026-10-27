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
"""Parameter pictures (dimensioned sketches) and icons of the cycles - plain Cairo, no GTK.

Every sketch is schematic (fixed proportions); the labels show the symbol and the value that is
currently set, e.g. "Z2 = -30". Drawing happens in the model frame (Z to the right, X up); with
the tool in front of the centre line the picture is mirrored (X+ down).
"""
from __future__ import annotations

import math
from typing import Dict, List

from .drawtext import show_text, text_width
from .geometry import ContourError, Row, parse_rows, polyline, resolve_rows, row_to_text
from .i18n import N_, _

BACKGROUND = (0.11, 0.12, 0.14)
MATERIAL = (0.44, 0.47, 0.52)
MATERIAL_REMOVED = (0.44, 0.47, 0.52, 0.28)
EDGE = (0.93, 0.93, 0.93)
DIMENSION = (1.0, 0.78, 0.25)
PATH = (0.35, 0.7, 1.0)
AXIS = (0.75, 0.75, 0.75)
TEXT = (0.95, 0.95, 0.95)
HINT = (0.72, 0.74, 0.78)
CHUCK = (0.30, 0.31, 0.34)
CHUCK_BODY = (0.25, 0.26, 0.29)
DANGER = (1.0, 0.35, 0.3, 0.35)

DIRECTION_LINES = [N_("Z1 > Z2: machining towards -Z"), N_("Z1 < Z2: towards +Z (left-hand tool)")]


def value_text(value, digits=3) -> str:
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        if value:
            return _("yes")
        return _("no")
    if isinstance(value, (int, float)):
        text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
        if text in ("-0", ""):
            return "0"
        return text
    return str(value)


def set_color(cr, color):
    if len(color) == 4:
        cr.set_source_rgba(*color)
    else:
        cr.set_source_rgb(*color)


class Sketch:
    """Mapping model (z, r) -> screen, with dimensioning helpers."""

    def __init__(self, cr, left, top, width, height, bounds, flip=False, font=11.0, padding=6):
        self.cr = cr
        z_min, z_max, r_min, r_max = bounds
        self.flip = flip
        self.font = font
        scale = min((width - 2 * padding) / (z_max - z_min),
                    (height - 2 * padding) / (r_max - r_min))
        self.scale = scale
        self.origin_x = (left + padding + ((width - 2 * padding) - (z_max - z_min) * scale) / 2
                         - z_min * scale)
        upper = top + padding + ((height - 2 * padding) - (r_max - r_min) * scale) / 2
        if flip:
            self.origin_y = upper - r_min * scale
            self.sign = -1
        else:
            self.origin_y = upper + r_max * scale
            self.sign = 1

    def to_screen(self, z, r):
        return self.origin_x + z * self.scale, self.origin_y - self.sign * r * self.scale

    def to_model(self, x, y):
        return (x - self.origin_x) / self.scale, (self.origin_y - y) / (self.sign * self.scale)

    # ---------------------------------------------------------------- basic shapes
    def _path(self, points, close=False):
        self.cr.move_to(*self.to_screen(*points[0]))
        for point in points[1:]:
            self.cr.line_to(*self.to_screen(*point))
        if close:
            self.cr.close_path()

    def polygon(self, points, fill=None, stroke=None, line_width=1.4, dash=None):
        cr = self.cr
        if fill:
            self._path(points, True)
            set_color(cr, fill)
            cr.fill()
        if stroke:
            self._path(points, bool(fill))
            set_color(cr, stroke)
            cr.set_line_width(line_width)
            if dash:
                cr.set_dash(dash)
            else:
                cr.set_dash([])
            cr.stroke()
            cr.set_dash([])

    def line(self, start, end, color=EDGE, line_width=1.0, dash=None):
        self.polygon([start, end], stroke=color, line_width=line_width, dash=dash)

    def axis(self, z_from, z_to):
        cr = self.cr
        cr.set_source_rgba(*AXIS, 0.7)
        cr.set_line_width(1)
        cr.set_dash([9, 3, 2, 3])
        cr.move_to(*self.to_screen(z_from, 0))
        cr.line_to(*self.to_screen(z_to, 0))
        cr.stroke()
        cr.set_dash([])

    def _arrow_head(self, tip, origin, color, size=7.0):
        cr = self.cr
        angle = math.atan2(tip[1] - origin[1], tip[0] - origin[0])
        set_color(cr, color)
        cr.move_to(*tip)
        cr.line_to(tip[0] - size * math.cos(angle - 0.38), tip[1] - size * math.sin(angle - 0.38))
        cr.line_to(tip[0] - size * math.cos(angle + 0.38), tip[1] - size * math.sin(angle + 0.38))
        cr.close_path()
        cr.fill()

    def arrow(self, start, end, color=PATH, line_width=1.4, dash=None, both=False):
        start_screen = self.to_screen(*start)
        end_screen = self.to_screen(*end)
        self.line(start, end, color, line_width, dash)
        self._arrow_head(end_screen, start_screen, color)
        if both:
            self._arrow_head(start_screen, end_screen, color)

    def origin_cross(self, point, length=6.0, color=(1.0, 0.85, 0.2)):
        """Coordinate cross at the workpiece zero: Z+ to the right, X+ outwards."""
        z, r = point
        self.arrow((z, r), (z + length, r), color, 1.6)
        self.arrow((z, r), (z, r + length), color, 1.6)
        self.dot((z, r), color, 4)
        self.label((z + length, r), "Z", None, anchor="l", dx=4, color=color)
        self.label((z, r + length), "X", None, anchor="c", dy=-self.font * 0.9 * self.sign,
                   color=color)

    def dot(self, point, color=DIMENSION, radius=3.5):
        set_color(self.cr, color)
        x, y = self.to_screen(*point)
        self.cr.arc(x, y, radius, 0, 2 * math.pi)
        self.cr.fill()

    # ---------------------------------------------------------------- Text
    def label(self, point, symbol, value=None, anchor="c", dx=0.0, dy=0.0, color=DIMENSION):
        """Label 'symbol = value' with background at the model point (dx/dy in pixels)."""
        cr = self.cr
        cr.set_font_size(self.font)
        cr.select_font_face("Sans", 0, 1)
        symbol_width = text_width(cr, symbol)
        if value is None:
            value_part = ""
        else:
            value_part = f" = {value}"
        cr.select_font_face("Sans", 0, 0)
        if value_part:
            value_width = text_width(cr, value_part)
        else:
            value_width = 0
        total_width = symbol_width + value_width
        height = self.font
        x, y = self.to_screen(*point)
        x += dx
        y += dy
        if anchor == "l":
            left = x
        elif anchor == "r":
            left = x - total_width
        else:
            left = x - total_width / 2
        cr.set_source_rgba(*BACKGROUND, 0.82)
        cr.rectangle(left - 3, y - height / 2 - 2, total_width + 6, height + 4)
        cr.fill()
        cr.select_font_face("Sans", 0, 1)
        set_color(cr, color)
        cr.move_to(left, y + height * 0.36)
        show_text(cr, symbol)
        if value_part:
            cr.select_font_face("Sans", 0, 0)
            cr.set_source_rgb(*TEXT)
            cr.move_to(left + symbol_width, y + height * 0.36)
            show_text(cr, value_part)
        cr.select_font_face("Sans", 0, 0)

    def legend(self, x, y, lines: List[str], size=None):
        """Text block; "key: text" is drawn with a bold key. Lines are translated here."""
        lines = [_(text) for text in lines]
        cr = self.cr
        if size is None:
            size = self.font * 0.92
        cr.set_font_size(size)
        widest = 0
        for text in lines:
            widest = max(widest, text_width(cr, text))
        cr.set_source_rgba(*BACKGROUND, 0.85)
        cr.rectangle(x - 4, y - size, widest + 8, len(lines) * (size + 3) + 4)
        cr.fill()
        for index, text in enumerate(lines):
            baseline = y + index * (size + 3)
            if ":" in text:
                key, rest = text.split(":", 1)
                cr.select_font_face("Sans", 0, 1)
                cr.set_source_rgb(*DIMENSION)
                cr.move_to(x, baseline)
                show_text(cr, key + ":")
                key_width = text_width(cr, key + ":")
                cr.select_font_face("Sans", 0, 0)
                cr.set_source_rgb(*HINT)
                cr.move_to(x + key_width, baseline)
                show_text(cr, rest)
            else:
                cr.set_source_rgb(*HINT)
                cr.move_to(x, baseline)
                show_text(cr, text)

    # ---------------------------------------------------------------- dimensions
    def dim_z(self, z_from, z_to, r, symbol, value=None, extension_from=None, label_position=0.5,
              anchor="c", label_dy=None):
        """Horizontal dimension (in Z) at height r, extension lines from extension_from=(r0, r1)."""
        if extension_from:
            for z, start_radius in ((z_from, extension_from[0]), (z_to, extension_from[1])):
                if start_radius is not None:
                    self.line((z, start_radius), (z, r), DIMENSION, 0.8)
        self.arrow((z_from, r), (z_to, r), DIMENSION, 1.0, both=True)
        if label_dy is None:
            label_dy = -self.font * 0.8
        label_z = z_from + (z_to - z_from) * label_position
        self.label((label_z, r), symbol, value, anchor=anchor, dy=label_dy)

    def dim_r(self, z, r_from, r_to, symbol, value=None, extension_from=None, anchor="l",
              label_dx=5, label_position=0.5):
        """Vertical (radial) dimension at z, extension lines from extension_from=(z0, z1)."""
        if extension_from:
            for start_z, radius in ((extension_from[0], r_from), (extension_from[1], r_to)):
                if start_z is not None:
                    self.line((start_z, radius), (z, radius), DIMENSION, 0.8)
        self.arrow((z, r_from), (z, r_to), DIMENSION, 1.0, both=True)
        label_r = r_from + (r_to - r_from) * label_position
        self.label((z, label_r), symbol, value, anchor=anchor, dx=label_dx)

    def z_mark(self, z, r_from, r_to, symbol, value, anchor="c"):
        """Vertical helper line at a Z position with a label at its end."""
        self.line((z, r_from), (z, r_to), DIMENSION, 0.9, [4, 3])
        upward_on_screen = (r_to > r_from) != self.flip
        if upward_on_screen:
            label_dy = -self.font * 0.7
        else:
            label_dy = self.font * 0.7
        self.label((z, r_to), symbol, value, anchor=anchor, dy=label_dy)

    def angle(self, center, angle_from, angle_to, radius_px, symbol, value=None, label_distance=16,
              color=DIMENSION):
        """Angle arc around the model point center, angles in the model (z right, r up)."""
        cr = self.cr
        center_x, center_y = self.to_screen(*center)
        if self.flip:
            screen_from, screen_to = angle_from, angle_to
        else:
            screen_from, screen_to = -angle_from, -angle_to
        low = min(screen_from, screen_to)
        high = max(screen_from, screen_to)
        cr.set_source_rgb(*color)
        cr.set_line_width(1.0)
        cr.new_sub_path()
        cr.arc(center_x, center_y, radius_px, low, high)
        cr.stroke()
        middle = (low + high) / 2
        label_x = center_x + (radius_px + label_distance) * math.cos(middle)
        label_y = center_y + (radius_px + label_distance) * math.sin(middle)
        self.label(self.to_model(label_x, label_y), symbol, value, color=color)

    def leader(self, point, label_point, symbol, value=None, anchor="c"):
        """Leader line from the label to the point."""
        self.line(label_point, point, DIMENSION, 0.9)
        self.dot(point, DIMENSION, 2.5)
        self.label(label_point, symbol, value, anchor=anchor)


def contour_from_diameters(*points):
    """Contour from (X diameter, Z[, corner, size[, angle, reference]]) - as in the contour text."""
    rows = []
    for point in points:
        row = Row("L", point[0], point[1])
        if len(point) > 2:
            row.corner = point[2]
            row.csize = point[3]
            if len(point) > 4:
                row.cangle = point[4]
                row.cref = point[5]
        rows.append(row)
    return polyline(resolve_rows(rows), 4.0)


def contour_from_model(*points):
    """Contour from model points (z, r[, corner, size[, angle, reference]])."""
    rows = []
    for point in points:
        row = Row("L", 2 * point[1], point[0])
        if len(point) > 2:
            row.corner = point[2]
            row.csize = point[3]
            if len(point) > 4:
                row.cangle = point[4]
                row.cref = point[5]
        rows.append(row)
    return polyline(resolve_rows(rows), 4.0)


def feeds_text(values) -> str:
    return f"{value_text(values.get('f_rough'))} / {value_text(values.get('f_finish'))}"


# =========================================================================== sketches

def draw_facing(sketch: Sketch, values, inside):
    # direction from the values: D < d = facing outwards, otherwise towards the centre
    try:
        outward = float(values["d_end"]) > float(values["d_start"])
    except (TypeError, ValueError):
        outward = False
    if not inside:
        sketch.polygon([(0, 0), (6, 0), (6, 20), (0, 20)], fill=MATERIAL_REMOVED, stroke=HINT,
                       line_width=0.8, dash=[3, 3])
        sketch.polygon([(-30, 0), (0, 0), (0, 20), (-30, 20)], fill=MATERIAL, stroke=EDGE)
        sketch.axis(-33, 11)
        if outward:
            radius_from, radius_to = -0.6, 23
        else:
            radius_from, radius_to = 23, -0.6
        for z in (4, 2):
            sketch.arrow((z, radius_from), (z, radius_to), PATH, 1.3)
        sketch.arrow((0.25, radius_from), (0.25, radius_to), PATH, 1.0, dash=[4, 3])
        sketch.z_mark(6, 20, 28.5, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(0, 20, 26, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_z(6, 4, -3, "ap", value_text(values["ap"]), anchor="l", label_position=0.0,
                     label_dy=sketch.font)
        if outward:
            sketch.dim_r(-23, 0, 20, "Ød", value_text(values["d_end"]))
            sketch.leader((0, 0), (-9, -4), "ØD", value_text(values["d_start"]))
        else:
            sketch.dim_r(-23, 0, 20, "ØD", value_text(values["d_start"]))
            sketch.leader((0, 0), (-9, -4), "Ød", value_text(values["d_end"]))
        sketch.label((-30, -5), "aZ", value_text(values["allow_z"]), anchor="l")
        sketch.label((-30, -8.5), "F / Fs", feeds_text(values), anchor="l")
    else:
        sketch.polygon([(-14, 4), (-10, 4), (-10, 15), (-14, 15)], fill=MATERIAL_REMOVED,
                       stroke=HINT, line_width=0.8, dash=[3, 3])
        sketch.polygon([(0, 15), (0, 25), (-30, 25), (-30, 4), (-14, 4), (-14, 15)],
                       fill=MATERIAL, stroke=EDGE)
        sketch.axis(-33, 8)
        if outward:
            radius_from, radius_to = 4.6, 14.6
        else:
            radius_from, radius_to = 14.6, 4.6
        for z in (-11, -12.6):
            sketch.arrow((z, radius_from), (z, radius_to), PATH, 1.3)
        sketch.arrow((-13.85, radius_from), (-13.85, radius_to), PATH, 1.0, dash=[4, 3])
        sketch.z_mark(-10, 4, -4.5, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(-14, 4, -8.5, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_z(-10, -11, 17.5, "ap", value_text(values["ap"]), anchor="l",
                     label_position=0.0, label_dy=0)
        if outward:
            sketch.dim_r(-5, 0, 15, "Ød", value_text(values["d_end"]))
            sketch.dim_r(-22, 0, 4, "ØD", value_text(values["d_start"]), label_position=1.0)
        else:
            sketch.dim_r(-5, 0, 15, "ØD", value_text(values["d_start"]))
            sketch.dim_r(-22, 0, 4, "Ød", value_text(values["d_end"]), label_position=1.0)
        sketch.label((-33, -11), "aZ", value_text(values["allow_z"]), anchor="l")
        sketch.label((-24, -11), "F / Fs", feeds_text(values), anchor="l")


def corner_spec(kind, size):
    if kind == "chamfer":
        return ("C", size)
    if kind == "radius":
        return ("R", size)
    return ()


CORNER_TEXT = {"chamfer": N_("Chamfer"), "radius": N_("Radius")}


def corner_text(kind, size):
    if kind not in CORNER_TEXT:
        return _("none")
    return f"{_(CORNER_TEXT[kind])} {value_text(size)}"


def draw_long(sketch: Sketch, values, inside):
    start_corner = corner_spec(values.get("start_kind", "none"), 2.0)
    end_corner = corner_spec(values.get("end_kind", "none"), 3.0)
    start_text = corner_text(values.get("start_kind"), values.get("start_size"))
    end_text = corner_text(values.get("end_kind"), values.get("end_size"))
    allowance = f"{value_text(values['allow_x'])} / {value_text(values['allow_z'])}"
    if not inside:
        finished, start_radius, z_end = 14.0, 20.0, -26.0
        sketch.polygon([(0, 0), (0, start_radius), (-40, start_radius), (-40, 0)],
                       fill=MATERIAL_REMOVED)
        rows = []
        if start_corner:
            rows.append((0, finished - 3))
        rows.append((0, finished) + start_corner)
        rows.append((z_end, finished) + end_corner)
        rows.append((z_end, start_radius))
        points = contour_from_model(*rows)
        sketch.polygon([(0, 0)] + points + [(-40, start_radius), (-40, 0)], fill=MATERIAL,
                       stroke=EDGE)
        sketch.axis(-44, 8)
        for level in (18.5, 17.0, 15.5):
            if level < 16:
                end_z = z_end + 2.5
            else:
                end_z = z_end + 0.4
            sketch.arrow((3, level), (end_z, level), PATH, 1.1, dash=[5, 3])
        sketch.dim_r(4.5, start_radius, 18.5, "ap", value_text(values["ap"]),
                     extension_from=(0, None), label_position=0.0)
        sketch.z_mark(0, start_radius, 26, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(z_end, start_radius, 26, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_r(-35, 0, start_radius, "ØD", value_text(values["d_start"]))
        sketch.dim_r(-12, 0, finished, "Ød", value_text(values["d_end"]))
        sketch.leader((-0.8, finished - 0.8), (5, 9), "Ka", start_text, "l")
        sketch.leader((z_end + 0.9, finished + 0.9), (-18, 6), "Ke", end_text)
        sketch.label((-43, -2.5), "aX / aZ", allowance, anchor="l")
    else:
        finished, start_radius, z_end = 12.0, 6.0, -25.0
        sketch.polygon([(0, start_radius), (0, finished), (z_end, finished), (z_end, start_radius)],
                       fill=MATERIAL_REMOVED)
        rows = []
        if start_corner:
            rows.append((0, finished + 3))
        rows.append((0, finished) + start_corner)
        rows.append((z_end, finished) + end_corner)
        rows.append((z_end, start_radius))
        points = contour_from_model(*rows)
        sketch.polygon([(0, 25)] + points + [(-40, start_radius), (-40, 25)], fill=MATERIAL,
                       stroke=EDGE)
        sketch.axis(-44, 8)
        for level in (7.5, 9.0, 10.5):
            sketch.arrow((3, level), (z_end + 1.4, level), PATH, 1.1, dash=[5, 3])
        sketch.dim_r(4.5, start_radius, 7.5, "ap", value_text(values["ap"]),
                     extension_from=(0, None), label_position=1.0)
        sketch.z_mark(0, 25, 30, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(z_end, 25, 30, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_r(-34, 0, start_radius, "ØD", value_text(values["d_start"]), label_position=0.4)
        sketch.dim_r(-12, 0, finished, "Ød", value_text(values["d_end"]), label_position=0.3)
        sketch.leader((-0.8, finished + 0.8), (5, 19), "Ka", start_text, "l")
        sketch.leader((z_end + 0.9, finished - 0.9), (-17, 19), "Ke", end_text)
        sketch.label((-43, -2.5), "aX / aZ", allowance, anchor="l")


def draw_taper(sketch: Sketch, values, inside):
    falling = values.get("d_end", 0) < values.get("d_start", 0)
    if inside:
        falling = not falling
    if not inside:
        if falling:
            start_radius, end_radius = 16.0, 10.0
        else:
            start_radius, end_radius = 10.0, 16.0
        stock_radius = 20.0
        sketch.polygon([(0, 0), (0, stock_radius), (-40, stock_radius), (-40, 0)],
                       fill=MATERIAL_REMOVED)
        sketch.polygon([(0, 0), (0, start_radius), (-25, end_radius), (-25, stock_radius),
                        (-40, stock_radius), (-40, 0)], fill=MATERIAL, stroke=EDGE)
        sketch.axis(-44, 8)
        sketch.line((0, start_radius), (-12, start_radius), DIMENSION, 0.8, [4, 3])
        slope = math.atan2(end_radius - start_radius, -25)
        if slope < 0:
            slope += 2 * math.pi
        sketch.angle((0, start_radius), min(slope, math.pi), max(slope, math.pi), 46, "α/2",
                     value_text(values["angle"]))
        sketch.z_mark(0, stock_radius, 26, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(-25, stock_radius, 26, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_r(3, 0, start_radius, "ØD1", value_text(values["d_start"]), extension_from=(0, 0))
        sketch.dim_r(-25, 0, end_radius, "ØD2", value_text(values["d_end"]), label_position=0.4,
                     anchor="r", label_dx=-5)
        if falling:
            levels = (18.5, 17.0, 15.0, 13.5)
        else:
            levels = (18.5, 17.0)
        for level in levels:
            if level >= start_radius:
                start_z = 3.0
            else:
                start_z = -25 * (start_radius - level) / (start_radius - end_radius)
                sketch.arrow((start_z + 25 * 1.5 / 6, level + 1.5), (start_z, level), PATH, 1.1)
            sketch.arrow((start_z, level), (-24.6, level), PATH, 1.1, dash=[5, 3])
        sketch.dim_r(6, stock_radius, 18.5, "ap", value_text(values["ap"]),
                     extension_from=(0, None), label_position=0.0)
    else:
        if falling:
            start_radius, end_radius = 10.0, 16.0
        else:
            start_radius, end_radius = 16.0, 10.0
        bore_radius = 6.0
        sketch.polygon([(0, bore_radius), (0, start_radius), (-25, end_radius), (-25, bore_radius)],
                       fill=MATERIAL_REMOVED)
        sketch.polygon([(0, 25), (0, start_radius), (-25, end_radius), (-25, bore_radius),
                        (-40, bore_radius), (-40, 25)], fill=MATERIAL, stroke=EDGE)
        sketch.axis(-44, 8)
        sketch.line((0, start_radius), (-12, start_radius), DIMENSION, 0.8, [4, 3])
        slope = math.atan2(end_radius - start_radius, -25)
        if slope < 0:
            slope += 2 * math.pi
        sketch.angle((0, start_radius), min(math.pi, slope), max(math.pi, slope), 46, "α/2",
                     value_text(values["angle"]))
        sketch.z_mark(0, 25, 30, "Z1", value_text(values["z_start"]), "l")
        sketch.z_mark(-25, 25, 30, "Z2", value_text(values["z_end"]), "r")
        sketch.dim_r(3, 0, start_radius, "ØD1", value_text(values["d_start"]),
                     extension_from=(0, 0), label_position=0.6)
        sketch.dim_r(-20, 0, end_radius + (start_radius - end_radius) * 0.2, "ØD2",
                     value_text(values["d_end"]), label_position=0.4)
        for level in (7.5, 9.0):
            sketch.arrow((3, level), (-24.6, level), PATH, 1.1, dash=[5, 3])
        sketch.dim_r(6, bore_radius, 7.5, "ap", value_text(values["ap"]),
                     extension_from=(0, None), label_position=1.0)
    if values.get("mode") == "angle":
        mode_text = _("Angle")
    else:
        mode_text = _("End diameter")
    sketch.label((-10, -4), _("Given by"), mode_text)


def draw_thread(sketch: Sketch, values, inside):
    pitch, depth, clearance, crest = 5.0, 3.0, 3.0, 10.0
    if inside:
        sign = -1
    else:
        sign = 1
    z_end = -25.0
    profile = [(0.0, crest - sign * depth * 0.25)]
    z = 0.0
    while z - pitch > z_end - 1e-6:
        profile.append((z - pitch * 0.5, crest - sign * depth))
        profile.append((z - pitch, crest))
        z -= pitch
    profile.append((-31.0, crest))
    if inside:
        outer = 22
    else:
        outer = 0
    sketch.polygon([(0, outer)] + profile + [(-34, crest), (-34, outer)], fill=MATERIAL,
                   stroke=EDGE)
    sketch.axis(-37, 15)
    drive_radius = crest + sign * clearance
    z_start_point = 7.0
    sketch.arrow((z_start_point, drive_radius), (z_end + 5, drive_radius), PATH, 1.3, dash=[6, 3])
    sketch.arrow((z_end + 5, drive_radius), (z_end, drive_radius + sign * 2.0), PATH, 1.3,
                 dash=[6, 3])
    sketch.dot((z_start_point, drive_radius), PATH, 4)
    if inside:
        top = 27.5
    else:
        top = crest + 11
    sketch.z_mark(0, crest, top, "Z1", value_text(values["z_begin"]), "c")
    sketch.z_mark(z_start_point, drive_radius, top, "Zs", value_text(values["z_start"]), "l")
    sketch.z_mark(z_end, crest, top, "Z2", value_text(values["z_end"]), "c")
    sketch.label((z_start_point, drive_radius), _("Start"), None, anchor="l", dx=6,
                 dy=sketch.font * 1.1 * sketch.sign, color=PATH)
    sketch.dim_z(z_end + 5, z_end, drive_radius + sign * 3.5, "E", value_text(values["chamfer_e"]),
                 extension_from=(drive_radius, drive_radius + sign * 2.0))
    if inside:
        pitch_radius = crest - 6.5
    else:
        pitch_radius = crest + 6.2
    sketch.dim_z(-5, -10, pitch_radius, "P", value_text(values["pitch"]),
                 extension_from=(crest, crest))
    sketch.dim_r(3.2, crest, drive_radius, "I", value_text(-sign * values["thread_clear"]),
                 extension_from=(0, None), anchor="r", label_dx=-5)
    sketch.dim_r(-30, 0, crest, "ØD", value_text(values["diameter"]), label_position=0.45)


def draw_thread_detail(cr, left, top, width, height, values, font):
    """Enlarged thread groove: infeed angle Q, first depth J, degression R, depth K."""
    sketch = Sketch(cr, left, top, width, height, (-6, 6, -1.0, 7.5), False, font, padding=4)
    cr.set_source_rgba(*BACKGROUND, 1.0)
    cr.rectangle(left, top, width, height)
    cr.fill()
    cr.set_source_rgb(*HINT)
    cr.set_line_width(0.8)
    cr.rectangle(left + 0.5, top + 0.5, width - 1, height - 1)
    cr.stroke()
    flank = math.tan(math.radians(30))
    groove_depth = 5.0
    sketch.polygon([(-6, 6), (-groove_depth * flank - 0.5, 6), (0, 6 - groove_depth),
                    (groove_depth * flank + 0.5, 6), (6, 6), (6, -1), (-6, -1)],
                   fill=MATERIAL, stroke=EDGE)
    full_depth = groove_depth * 0.9
    first_cut = 1.2
    degression = max(1.0, float(values.get("degression") or 1.5))
    depths = []
    number = 1
    while True:
        cut = first_cut * number ** (1 / degression)
        if cut >= full_depth or number > 30:
            depths.append(full_depth)
            break
        depths.append(cut)
        number += 1
    for cut in depths:
        sketch.line((-4.6, 6 - cut), (4.6, 6 - cut), PATH, 0.7, [3, 2])
    sketch.line((0, 7.3), (0, 6 - full_depth - 0.3), AXIS, 0.8, [5, 3])
    infeed = math.radians(float(values.get("infeed") or 29.5))
    length = full_depth + 0.8
    sketch.arrow((-math.sin(infeed) * 0.2, 7.3),
                 (-math.sin(infeed) * length * 0.8, 7.3 - math.cos(infeed) * length),
                 DIMENSION, 1.2)
    sketch.angle((0, 7.3), -math.pi / 2 - infeed, -math.pi / 2, 34, "Q",
                 value_text(values.get("infeed")) + "°", label_distance=10)
    sketch.dim_r(4.8, 6, 6 - first_cut, "J", None, label_position=0.0, label_dx=4)
    sketch.dim_r(-5.2, 6, 6 - full_depth, "K", None, anchor="r", label_dx=-4)
    sketch.label((3.4, 0.0), "R", value_text(values.get("degression")))


def draw_edge(sketch: Sketch, values, inside):
    length = 4.0
    is_chamfer = values["kind"] == "chamfer"
    angle = abs(float(values.get("angle") or 45))
    back_edge = values.get("edge") == "back"
    if is_chamfer and float(values.get("angle") or 45) < 0:
        back_edge = not back_edge
    if is_chamfer:
        leg = length * math.tan(math.radians(angle))
    else:
        leg = length
    if inside:
        radius = 12.0
        sign = -1
        far = 25.0
    else:
        radius = 15.0
        sign = 1
        far = 0.0
    if back_edge:
        # collar (z > Z1) with the edge at its back, a smaller Ø behind it
        neck = radius - sign * (leg + 3)
        sketch.polygon([(0, radius), (-length - 1, radius), (0, radius - sign * (leg + 1))],
                       fill=MATERIAL_REMOVED)
        if is_chamfer:
            outline = contour_from_model((14, far), (14, radius), (length, radius), (0, radius - sign * leg),
                                         (0, neck), (-26, neck), (-26, far))
        else:
            outline = contour_from_model((14, far), (14, radius), (0, radius, "R", length),
                                         (0, neck), (-26, neck), (-26, far))
        sketch.polygon(outline, fill=MATERIAL, stroke=EDGE)
        sketch.axis(-30, 18)
        sketch.z_mark(0, neck, radius + sign * 9, "Z1", value_text(values["z_start"]), "r")
        sketch.dim_r(10, 0, radius, "ØD", value_text(values["diameter"]))
        if is_chamfer:
            sketch.dim_z(0, length, radius + sign * 4, "L", value_text(values["size"]),
                         extension_from=(None, radius), anchor="l", label_position=1.0)
            sketch.line((length, radius), (length + 9, radius), DIMENSION, 0.8, [4, 3])
            slope = math.atan2(-sign * leg, -length)
            if slope < 0:
                slope += 2 * math.pi
            sketch.angle((length, radius), min(math.pi, slope), max(math.pi, slope), 40, "α",
                         value_text(values["angle"]) + "°")
        else:
            sketch.leader((length * (1 - math.cos(math.radians(45))),
                           radius - sign * length * (1 - math.sin(math.radians(45)))),
                          (8, radius + sign * 5), "R", value_text(values["size"]))
        if inside:
            note_radius = 27.0
        else:
            note_radius = -3.0
        sketch.label((-26, note_radius), _("Back edge"), _("entered obliquely"), anchor="l")
        return
    if not inside:
        sketch.polygon([(0, radius - leg - 1), (0, radius), (-length - 1, radius)],
                       fill=MATERIAL_REMOVED)
        if is_chamfer:
            outline = contour_from_model((0, 0), (0, radius - leg), (-length, radius),
                                         (-30, radius), (-30, 0))
        else:
            outline = contour_from_model((0, 0), (0, radius, "R", length), (-30, radius), (-30, 0))
        sketch.polygon(outline, fill=MATERIAL, stroke=EDGE)
        sketch.axis(-34, 8)
        sketch.z_mark(0, radius - leg, radius + 9, "Z1", value_text(values["z_start"]), "l")
        sketch.dim_r(-24, 0, radius, "ØD", value_text(values["diameter"]))
        sketch.label((-24, -3), "ap", value_text(values["ap"]) + " (radial)")
        if is_chamfer:
            sketch.dim_z(0, -length, radius + 4, "L", value_text(values["size"]),
                         extension_from=(None, radius), anchor="r", label_position=1.0)
            sketch.line((-length, radius), (-length - 9, radius), DIMENSION, 0.8, [4, 3])
            sketch.angle((-length, radius), math.atan2(-leg, length), 0.0, 40, "α",
                         value_text(values["angle"]) + "°")
        else:
            sketch.leader((-length + length * math.cos(math.radians(45)),
                           radius - length + length * math.sin(math.radians(45))),
                          (-12, radius + 4), "R", value_text(values["size"]))
    else:
        sketch.polygon([(0, radius + leg + 1), (0, radius), (-length - 1, radius)],
                       fill=MATERIAL_REMOVED)
        if is_chamfer:
            outline = contour_from_model((0, 25), (0, radius + leg), (-length, radius),
                                         (-30, radius), (-30, 25))
        else:
            outline = contour_from_model((0, 25), (0, radius, "R", length), (-30, radius),
                                         (-30, 25))
        sketch.polygon(outline, fill=MATERIAL, stroke=EDGE)
        sketch.axis(-34, 8)
        sketch.z_mark(0, radius + leg, -4, "Z1", value_text(values["z_start"]), "l")
        sketch.dim_r(-22, 0, radius, "ØD", value_text(values["diameter"]))
        sketch.label((-22, -5), "ap", value_text(values["ap"]) + " (radial)")
        if is_chamfer:
            sketch.dim_z(0, -length, radius - 4, "L", value_text(values["size"]),
                         extension_from=(None, radius), anchor="r", label_position=1.0,
                         label_dy=sketch.font)
            sketch.line((-length, radius), (-length - 9, radius), DIMENSION, 0.8, [4, 3])
            sketch.angle((-length, radius), 0.0, math.atan2(leg, length), 40, "α",
                         value_text(values["angle"]) + "°")
        else:
            sketch.leader((-length + length * math.cos(math.radians(45)),
                           radius + length - length * math.sin(math.radians(45))),
                          (-12, radius - 5), "R", value_text(values["size"]))


def draw_drill(sketch: Sketch, values):
    radius = 5.0
    tip_angle = float(values.get("tip_angle") or 118)
    tip_length = radius / math.tan(math.radians(tip_angle / 2))
    z_shoulder = -20.0
    z_tip = z_shoulder - tip_length
    for side in (1, -1):
        sketch.polygon([(0, side * radius), (z_shoulder, side * radius), (z_tip, 0), (-40, 0),
                        (-40, side * 18), (0, side * 18)], fill=MATERIAL)
    sketch.polygon([(0, radius), (z_shoulder, radius), (z_tip, 0), (z_shoulder, -radius),
                    (0, -radius)], stroke=EDGE)
    sketch.line((0, 18), (0, radius), EDGE, 1.4)
    sketch.line((0, -18), (0, -radius), EDGE, 1.4)
    sketch.axis(-44, 10)
    for start_z, end_z in ((4, -6), (-6, -12), (-12, -18), (-18, z_tip)):
        if start_z < 4:
            start_z += 0.6
        sketch.arrow((start_z, 1.2), (end_z, 1.2), PATH, 1.2)
    sketch.z_mark(0, 18, 22.5, "Z1", value_text(values["z_start"]), "l")
    if values.get("depth_ref") == "tip":
        sketch.z_mark(z_tip, 0, -23, _("Z2 (tip)"), value_text(values["z_end"]), "c")
    else:
        sketch.z_mark(z_shoulder, radius, -23, _("Z2 (full Ø)"), value_text(values["z_end"]),
                      "c")
    sketch.dim_r(-8, 0, radius, "ØD", value_text(values["diameter"]), label_position=1.6,
                 label_dx=4)
    sketch.dim_z(0, -6, -2.6, "Q", value_text(values["peck"]), label_dy=0)
    half_angle = math.atan2(radius, z_shoulder - z_tip)
    sketch.angle((z_tip, 0), -half_angle, half_angle, 34, "σ",
                 value_text(values["tip_angle"]) + "°", label_distance=4)
    sketch.label((-30, 13), "F", value_text(values["f"]))
    sketch.label((-30, 9), "S", value_text(values["speed"]))


def draw_contour_example(sketch: Sketch, values, inside):
    if not inside:
        rows = [(0, 0), (50, 0, "C", 2, 30, "A"), (50, -20), (60, -20, "R", 5), (60, -50),
                (64, -50)]
        points = contour_from_diameters(*rows)
        sketch.polygon(points + [(-50, 0)], fill=MATERIAL, stroke=EDGE)
        sketch.axis(-56, 8)
        corners = [(0, 0), (0, 25), (-20, 25), (-20, 30), (-50, 30), (-50, 32)]
        for point in corners:
            sketch.dot(point, DIMENSION, 3.2)
        font = sketch.font
        sign = sketch.sign
        sketch.label((0, 0), "P1", "X0 Z0", "r", dx=-4, dy=-font * 0.9 * sign)
        sketch.label((0, 25), "P2", "X50 Z0 C2 A30", "l", dx=6)
        sketch.label((-20, 25), "P3", "X50 Z-20", "l", dx=6, dy=font * 1.1 * sign)
        sketch.label((-20, 30), "P4", "X60 Z-20 R5", "l", dx=4, dy=-font * 1.3 * sign)
        sketch.label((-50, 30), "P5", "X60 Z-50", "l", dx=6, dy=font * 1.1 * sign)
        sketch.label((-50, 32), "P6", "X64 Z-50", "l", dx=6, dy=-font * 1.1 * sign)
    else:
        rows = [(70, 0), (50, 0, "C", 2), (50, -20, "R", 3), (30, -20), (30, -50), (20, -50)]
        points = contour_from_diameters(*rows)
        sketch.polygon(points + [(-50, 40), (0, 40)], fill=MATERIAL, stroke=EDGE)
        sketch.axis(-56, 8)
        corners = [(0, 35), (0, 25), (-20, 25), (-20, 15), (-50, 15), (-50, 10)]
        for point in corners:
            sketch.dot(point, DIMENSION, 3.2)
        font = sketch.font
        sign = sketch.sign
        sketch.label((0, 35), "P1", "X70 Z0", "l", dx=6)
        sketch.label((0, 25), "P2", "X50 Z0 C2", "l", dx=6)
        sketch.label((-20, 25), "P3", "X50 Z-20 R3", "l", dx=6, dy=-font * 1.1 * sign)
        sketch.label((-20, 15), "P4", "X30 Z-20", "l", dx=6, dy=font * 0.9 * sign)
        sketch.label((-50, 15), "P5", "X30 Z-50", "l", dx=6, dy=-font * 1.0 * sign)
        sketch.label((-50, 10), "P6", "X20 Z-50", "l", dx=6, dy=font * 1.0 * sign)
    sketch.label((-62, -2.6), "ap", value_text(values["ap"]) + " " + _("(radial depth of cut)"),
                 anchor="l")


def contour_geometry(values, inside):
    """The own contour for the parameter picture: (rows, points, bounds) or None on errors."""
    try:
        rows = parse_rows(str(values.get("contour", "")))
        if len(rows) < 2:
            return None
        corner_mode = values.get("corner_mode")
        if corner_mode == "radius":
            default = "R"
        elif corner_mode == "chamfer":
            default = "C"
        else:
            default = None
        points = polyline(resolve_rows(rows, default, float(values.get("corner_size") or 0)))
    except (ContourError, ValueError, TypeError, ZeroDivisionError):
        return None
    z_values = [z for z, _r in points]
    r_values = [r for _z, r in points]
    z_min, z_max = min(z_values), max(z_values)
    r_min, r_max = min(0.0, min(r_values)), max(r_values)
    span = max(z_max - z_min, r_max - r_min, 1.0)
    if inside:
        r_max += 0.15 * span
    bounds = (z_min - 0.2 * span, z_max + 0.45 * span, r_min - 0.08 * span,
              r_max + 0.12 * span)
    return rows, points, bounds


def draw_contour(sketch: Sketch, values, inside, rows, points):
    """The entered contour with the points P1…Pn and their values."""
    z_first = points[0][0]
    z_end, r_end = points[-1]
    span = max(abs(z_end - z_first), 1.0)
    if z_end < z_first:
        z_last = z_end - 0.08 * span
    else:
        z_last = z_end + 0.08 * span
    outline = points + [(z_last, r_end)]          # material continues behind the last point
    if inside:
        top = max(r for _z, r in points) * 1.12 + 1.0
        sketch.polygon(outline + [(z_last, top), (z_first, top)], fill=MATERIAL, stroke=EDGE)
    else:
        sketch.polygon(outline + [(z_last, 0.0), (z_first, 0.0)], fill=MATERIAL, stroke=EDGE)
    z_low = min(z for z, _r in points)
    z_high = max(z for z, _r in points)
    sketch.axis(min(z_low, z_last) - 3, max(z_high, z_last) + 3)
    font = sketch.font
    sign = sketch.sign
    for index, row in enumerate(rows):
        point = (row.z, row.x / 2.0)
        sketch.dot(point, DIMENSION, 3.2)
        if index % 2:
            offset = font * 1.0 * sign
        else:
            offset = -font * 1.0 * sign
        sketch.label(point, f"P{index + 1}", row_to_text(row, index == 0), "l", dx=6, dy=offset)
    sketch.label((z_low, 0.0), "ap", value_text(values["ap"]) + " " + _("(radial depth of cut)"),
                 anchor="l", dy=font * 1.4 * sign)


def draw_stock(sketch: Sketch, values):
    radius = 14.0
    if (values.get("d") or 0) > 0:
        bore = 5.0
    else:
        bore = 0.0
    z_chuck, z_finished, allowance = -40.0, 0.0, 3.0
    front_zero = values.get("zero", "front") == "front"
    sketch.polygon([(z_chuck - 14, radius), (z_chuck, radius), (z_chuck, radius + 7),
                    (z_chuck - 14, radius + 7)], fill=CHUCK, stroke=HINT, line_width=0.8)
    sketch.polygon([(z_chuck - 18, 0), (z_chuck - 14, 0), (z_chuck - 14, radius + 11),
                    (z_chuck - 18, radius + 11)], fill=CHUCK_BODY, stroke=HINT, line_width=0.8)
    if front_zero:
        sketch.polygon([(z_finished, bore), (z_finished + allowance, bore),
                        (z_finished + allowance, radius), (z_finished, radius)],
                       fill=MATERIAL_REMOVED, stroke=HINT, line_width=0.8, dash=[3, 3])
        z_front = z_finished + allowance
    else:
        z_front = z_finished
    sketch.polygon([(z_chuck - 14, bore), (z_finished, bore), (z_finished, radius),
                    (z_chuck - 14, radius)], fill=MATERIAL, stroke=EDGE)
    if bore > 0:
        sketch.line((z_chuck - 14, bore), (z_front, bore), EDGE, 1.0, [5, 3])
    sketch.axis(z_chuck - 20, 9)
    sketch.dim_z(z_chuck, z_front, radius + 4.5, "L", value_text(values.get("L")),
                 extension_from=(radius + 7, radius))
    sketch.dim_r(-20, 0, radius, "ØD", value_text(values.get("D")))
    if bore > 0:
        sketch.dim_r(-8, 0, bore, "Ød", value_text(values.get("d")), label_position=1.3)
    else:
        sketch.label((-30, 3.0), "Ød", _("0 (solid)"))
    safety = 3.0
    sketch.polygon([(z_chuck, radius), (z_chuck + safety, radius), (z_chuck + safety, radius + 3),
                    (z_chuck, radius + 3)], fill=DANGER)
    sketch.dim_z(z_chuck, z_chuck + safety, -3.0, "s", value_text(values.get("chuck_clear")),
                 extension_from=(0, 0), anchor="l", label_position=1.0, label_dy=0)
    if front_zero:
        zero_z = z_finished
        zero_text = _("Front face")
    else:
        zero_z = z_chuck
        zero_text = _("Chuck")
    sketch.origin_cross((zero_z, 0))
    sketch.label((zero_z, -4.5), "Z0", zero_text, anchor="r", dx=-8, color=(1, 0.85, 0.2))
    if front_zero:
        sketch.dim_z(z_finished, z_finished + allowance, radius + 1.5, "a",
                     value_text(values.get("face")), extension_from=(radius, radius), anchor="l",
                     label_position=1.0, label_dy=0)
    sketch.legend(8, sketch.font + 6, [N_("L: length out of the chuck to the stock front"),
                                       N_("s: safety distance - moves behind it are errors,"),
                                       N_("    drilling/inside behind it only a warning")],
                  sketch.font * 0.85)


def draw_gcode_help(cr, width, height, font):
    lines = [N_("Own G-code is inserted unchanged between two cycles."),
             "",
             N_("Snippets: choosing one replaces the code field, »Insert« adds it at the"),
             N_("cursor, »Clear« empties the field, »Save …« stores own snippets."),
             "",
             N_("Example remove chips:"),
             N_("   M5        spindle off"),
             N_("   M9        coolant off"),
             N_("   M0        stop (M1 only with optional stop switched on)"),
             "",
             N_("The tool is at the retract plane in front of the part."),
             N_("Own moves must return there."),
             N_("With »Spindle/coolant on again« the speed,"),
             N_("M3/M4 and M8 are output again after the block.")]
    sketch = Sketch(cr, 0, 0, width, height, (0, 1, 0, 1), False, font)
    sketch.legend(12, font + 12, lines, font)


CONTOUR_LEGEND = [
    N_("X Z: line, U W incremental"),
    N_("R5: corner radius"),
    N_("C2: chamfer 45°"),
    N_("C2 A30: 2 mm on the incoming"),
    N_("      edge, 30° to it"),
    N_("C2 B30: same, outgoing edge"),
    N_("G2/G3 X Z R: arc"),
    N_("LA: arc > 180°"),
    N_("I K: centre (relative)"),
    N_("TS / TE: tangential start/end"),
    N_("RND / CHR: corner after an arc"),
    N_("R0: keep the corner sharp"),
    N_("Direction: first -> last point"),
]


# =========================================================================== entry point

def draw_help(cr, width, height, key: str, values: Dict, inside=False, flip=False, font=None):
    """Draws the parameter picture of a cycle into an area width × height."""
    if font is None:
        font = max(9.0, min(12.5, min(width / 46, height / 24)))
    cr.set_source_rgb(*BACKGROUND)
    cr.paint()
    values = dict(values)
    if key == "facing":
        if inside:
            bounds = (-33, 10, -13, 28)
        else:
            bounds = (-33, 13, -11, 31)
        sketch = Sketch(cr, 0, 0, width, height, bounds, flip, font)
        draw_facing(sketch, values, inside)
        sketch.legend(width - 230, font + 6, DIRECTION_LINES, font * 0.8)
    elif key == "long":
        if inside:
            bounds = (-43, 14, -4, 33)
        else:
            bounds = (-43, 14, -4, 29)
        sketch = Sketch(cr, 0, 0, width, height, bounds, flip, font)
        draw_long(sketch, values, inside)
        sketch.legend(8, height - 2 * (font + 3) + 2, DIRECTION_LINES, font * 0.8)
    elif key == "taper":
        if inside:
            bounds = (-43, 14, -7, 33)
        else:
            bounds = (-43, 14, -7, 29)
        sketch = Sketch(cr, 0, 0, width, height, bounds, flip, font)
        draw_taper(sketch, values, inside)
    elif key == "thread":
        wide = width > 2.0 * height
        if wide:
            main_width, main_height = width * 0.63, height
        else:
            main_width, main_height = width, height * 0.62
        if inside:
            bounds = (-34, 15, -1, 29)
        else:
            bounds = (-34, 15, -2, 22)
        sketch = Sketch(cr, 0, 0, main_width, main_height, bounds, flip, font)
        draw_thread(sketch, values, inside)
        lines = [_("H: {count} spring pass(es)").format(count=value_text(values['spring'])),
                 _("L: {value} (0 none, 1 entry, 2 exit, 3 both)").format(
                     value=value_text(values['chamfer_l'])),
                 _("S: {speed} 1/min (G97)").format(speed=value_text(values['speed']))]
        if wide:
            detail = (main_width + 2, 6, width - main_width - 8, height * 0.58)
            draw_thread_detail(cr, *detail, values, font * 0.95)
            sketch.legend(detail[0] + 4, detail[1] + detail[3] + font + 10, lines, font * 0.85)
        else:
            detail = (6, main_height + 2, width * 0.42, height - main_height - 8)
            draw_thread_detail(cr, *detail, values, font * 0.95)
            sketch.legend(detail[0] + detail[2] + 12, detail[1] + font + 6, lines, font * 0.85)
    elif key == "edge":
        if inside:
            bounds = (-36, 20, -8, 30)
        else:
            bounds = (-36, 20, -5, 27)
        sketch = Sketch(cr, 0, 0, width, height, bounds, flip, font)
        draw_edge(sketch, values, inside)
    elif key == "drill":
        sketch = Sketch(cr, 0, 0, width, height, (-46, 12, -27, 27), flip, font)
        draw_drill(sketch, values)
        mode_texts = {"through": N_("Straight"), "chip_break": N_("Chip breaking"),
                      "peck": N_("Peck drilling")}
        mode_text = _(mode_texts.get(values.get("mode"), str(values.get("mode"))))
        sketch.legend(8, height - 4 * (font * 0.85 + 3) + 2,
                      [_("Mode: {mode}").format(mode=mode_text),
                       N_("Chip breaking: back by Rb after every step Q"),
                       N_("Peck drilling: all the way out after every step"),
                       _("P: dwell {seconds} s").format(seconds=value_text(values.get('dwell')))],
                      font * 0.85)
    elif key == "contour":
        wide = width > 1.45 * height
        if wide:
            drawing_width, drawing_height = width * 0.6, height
        else:
            drawing_width, drawing_height = width, height * 0.58
        geometry = contour_geometry(values, inside)
        if geometry is not None:
            rows, points, bounds = geometry
            sketch = Sketch(cr, 0, 0, drawing_width, drawing_height, bounds, flip, font * 0.9)
            draw_contour(sketch, values, inside, rows, points)
        else:
            if inside:
                bounds = (-62, 30, -4, 42)
            else:
                bounds = (-64, 34, -4, 38)
            sketch = Sketch(cr, 0, 0, drawing_width, drawing_height, bounds, flip, font * 0.9)
            draw_contour_example(sketch, values, inside)
        count = len(CONTOUR_LEGEND)
        if wide:
            size = max(7.5, min(font * 0.9, (width - drawing_width) / 22,
                                (height - 12) / (count * 1.3)))
            sketch.legend(drawing_width + 4, size + 8, CONTOUR_LEGEND, size)
        else:
            half = (count + 1) // 2
            size = max(7.5, min(font * 0.85, width / 46,
                                (height - drawing_height - 10) / (half * 1.3)))
            sketch.legend(8, drawing_height + size + 4, CONTOUR_LEGEND[:half], size)
            sketch.legend(width / 2 + 4, drawing_height + size + 4, CONTOUR_LEGEND[half:], size)
    elif key == "stock":
        sketch = Sketch(cr, 0, 0, width, height, (-60, 11, -7, 26), flip, font)
        draw_stock(sketch, values)
    elif key == "gcode":
        draw_gcode_help(cr, width, height, font)
    else:
        cr.set_source_rgb(*HINT)
        cr.set_font_size(font)
        cr.move_to(10, 10 + font)
        show_text(cr, _("No parameter picture"))


# =========================================================================== icons

def draw_icon(cr, key: str, size: float, color=(0.9, 0.9, 0.9), accent=(1.0, 0.65, 0.2),
              flip=False):
    """Small icon (size × size) for the tab bar.

    Drawn for a tool behind the centre line (tool at the top); flip=True mirrors for a tool in
    front of the centre line (tool at the bottom), matching the preview.
    """
    scale = size / 24.0
    cr.set_line_width(1.6 * scale)
    if key in ("menu", "send", "gcode_view"):
        flip = False                    # symbols without a tool position

    def point(x, y):
        if flip:
            y = 24 - y
        return x * scale, y * scale

    def shape(points, fill=False, shape_color=color):
        cr.move_to(*point(*points[0]))
        for corner in points[1:]:
            cr.line_to(*point(*corner))
        cr.set_source_rgb(*shape_color)
        if fill:
            cr.close_path()
            cr.fill()
        else:
            cr.stroke()

    def arrow(x_from, y_from, x_to, y_to, arrow_color=accent):
        shape([(x_from, y_from), (x_to, y_to)], shape_color=arrow_color)
        angle = math.atan2(y_to - y_from, x_to - x_from)
        shape([(x_to, y_to),
               (x_to - 4 * math.cos(angle - 0.5), y_to - 4 * math.sin(angle - 0.5)),
               (x_to - 4 * math.cos(angle + 0.5), y_to - 4 * math.sin(angle + 0.5))],
              True, arrow_color)

    def disc(x, y, radius, disc_color):
        cr.set_source_rgb(*disc_color)
        center_x, center_y = point(x, y)
        cr.arc(center_x, center_y, radius * scale, 0, 2 * math.pi)
        cr.fill()

    if key == "program":
        for y in (6, 12, 18):
            shape([(3, y - 1.5), (6, y - 1.5), (6, y + 1.5), (3, y + 1.5)], True, accent)
            shape([(9, y), (21, y)])
    elif key == "facing":
        shape([(2, 4), (14, 4), (14, 20), (2, 20)], True)
        arrow(18, 3, 18, 18)
    elif key == "long":
        # turned section at the front (right), machining towards the chuck (left)
        shape([(22, 7), (11, 7), (11, 4), (2, 4), (2, 20), (22, 20)], True)
        arrow(23, 2.5, 12, 2.5)
    elif key == "taper":
        shape([(22, 20), (22, 12), (2, 5), (2, 20)], True)
        arrow(20, 8, 5, 2.5)
    elif key == "thread":
        teeth = [(2, 20), (2, 10)]
        for index in range(5):
            if index % 2 == 0:
                teeth.append((4 + 4 * index, 6))
            else:
                teeth.append((4 + 4 * index, 10))
        teeth += [(22, 10), (22, 20)]
        shape(teeth, True)
        shape([(2, 3), (22, 3)], shape_color=accent)
    elif key == "edge":
        shape([(2, 4), (14, 4), (22, 12), (22, 20), (2, 20)], True)
        shape([(15, 2), (24, 11)], shape_color=accent)
    elif key == "contour":
        shape([(22, 20), (22, 16), (18, 14), (16, 9), (13, 8), (8, 8), (2, 3), (2, 20)], True)
        for x, y in ((22, 16), (13, 8), (8, 8), (2, 3)):
            disc(x, y, 1.8, accent)
    elif key == "drill":
        shape([(2, 9), (14, 9), (19, 12), (14, 15), (2, 15)], True)
        shape([(0, 12), (24, 12)], shape_color=accent)
    elif key == "tools":
        shape([(3, 4), (9, 4), (9, 14), (3, 14)], True)
        shape([(9, 14), (15, 20), (9, 20), (3, 14)], True, accent)
        shape([(13, 6), (21, 6)])
        shape([(13, 10), (21, 10)])
    elif key == "machine":
        cr.set_source_rgb(*color)
        for spoke in range(8):
            angle = spoke * math.pi / 4
            cr.move_to(*point(12 + 6 * math.cos(angle), 12 + 6 * math.sin(angle)))
            cr.line_to(*point(12 + 10 * math.cos(angle), 12 + 10 * math.sin(angle)))
        cr.set_line_width(3.2 * scale)
        cr.stroke()
        cr.set_line_width(1.6 * scale)
        disc(12, 12, 7, color)
        disc(12, 12, 2.5, accent)
    elif key == "stock":
        shape([(1, 4), (6, 4), (6, 20), (1, 20)], True, accent)
        shape([(6, 7), (22, 7), (22, 17), (6, 17)], True)
    elif key == "gcode":
        cr.set_source_rgb(*color)
        cr.set_font_size(11 * scale)
        cr.select_font_face("Sans", 0, 1)
        cr.move_to(1 * scale, 16 * scale)
        show_text(cr, "G")
        cr.set_source_rgb(*accent)
        cr.move_to(11 * scale, 16 * scale)
        show_text(cr, "M")
        cr.select_font_face("Sans", 0, 0)
    elif key == "menu":
        cr.set_line_width(2.6 * scale)
        for y in (6, 12, 18):
            shape([(3, y), (21, y)])
        cr.set_line_width(1.6 * scale)
    elif key == "gcode_view":
        shape([(4, 2), (16, 2), (16, 21), (4, 21), (4, 2)])
        for y in (6, 9, 12):
            shape([(7, y), (13, y)], shape_color=accent)
        cr.set_source_rgb(*color)
        center_x, center_y = point(15, 15)
        cr.new_sub_path()
        cr.arc(center_x, center_y, 4.5 * scale, 0, 2 * math.pi)
        cr.stroke()
        cr.set_line_width(2.8 * scale)
        shape([(18.3, 18.3), (22.5, 22.5)])
        cr.set_line_width(1.6 * scale)
    elif key == "send":
        shape([(2, 3), (12, 3), (12, 21), (2, 21), (2, 3)])
        for y in (7, 10, 13):
            shape([(4.5, y), (9.5, y)])
        cr.set_line_width(2.4 * scale)
        arrow(11, 15, 22.5, 15)
        cr.set_line_width(1.6 * scale)
    elif key == "side_out":
        shape([(2, 8), (22, 8), (22, 16), (2, 16)], True)
        arrow(12, 1, 12, 7)
    elif key == "side_in":
        shape([(2, 3), (22, 3), (22, 8), (2, 8)], True)
        shape([(2, 16), (22, 16), (22, 21), (2, 21)], True)
        arrow(1, 12, 14, 12)
