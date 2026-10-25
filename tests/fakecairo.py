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
"""Minimaler Cairo-Ersatz auf Basis von matplotlib – nur zum Testen der Zeichenroutinen ohne GTK."""
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path  # noqa: E402
from matplotlib.textpath import TextPath  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402


class FakeContext:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, w)
        self.ax.set_ylim(h, 0)
        self.ax.axis("off")
        self.col = (0, 0, 0, 1)
        self.lw = 2.0
        self.dash = None
        self.fs = 10
        self.bold = False
        self.sub = []
        self.cur = None
        self.stack = []
        self.z = 0

    # --- Zustand
    def save(self):
        self.stack.append((self.col, self.lw, self.dash, self.fs, self.bold))

    def restore(self):
        self.col, self.lw, self.dash, self.fs, self.bold = self.stack.pop()

    def set_source_rgb(self, r, g, b):
        self.col = (r, g, b, 1)

    def set_source_rgba(self, r, g, b, a):
        self.col = (r, g, b, a)

    def set_line_width(self, w):
        self.lw = w

    def set_dash(self, d, off=0):
        self.dash = list(d) if d else None

    def set_line_cap(self, *_):
        pass

    def set_line_join(self, *_):
        pass

    def set_font_size(self, s):
        self.fs = s

    def select_font_face(self, name, slant=0, weight=0):
        self.bold = bool(weight)

    # --- Pfade
    def new_path(self):
        self.sub, self.cur = [], None

    def new_sub_path(self):
        self.cur = None

    def move_to(self, x, y):
        self.sub.append([[(x, y)], False])
        self.cur = (x, y)

    def line_to(self, x, y):
        if self.cur is None or not self.sub:
            self.move_to(x, y)
            return
        self.sub[-1][0].append((x, y))
        self.cur = (x, y)

    def rel_line_to(self, dx, dy):
        self.line_to(self.cur[0] + dx, self.cur[1] + dy)

    def close_path(self):
        if self.sub:
            self.sub[-1][1] = True

    def rectangle(self, x, y, w, h):
        self.move_to(x, y)
        self.line_to(x + w, y)
        self.line_to(x + w, y + h)
        self.line_to(x, y + h)
        self.close_path()

    def _arc(self, xc, yc, r, a1, a2, neg):
        if neg:
            while a2 > a1:
                a2 -= 2 * math.pi
        else:
            while a2 < a1:
                a2 += 2 * math.pi
        n = max(4, int(abs(a2 - a1) / 0.05))
        for k in range(n + 1):
            a = a1 + (a2 - a1) * k / n
            self.line_to(xc + r * math.cos(a), yc + r * math.sin(a))

    def arc(self, xc, yc, r, a1, a2):
        self._arc(xc, yc, r, a1, a2, False)

    def arc_negative(self, xc, yc, r, a1, a2):
        self._arc(xc, yc, r, a1, a2, True)

    # --- Zeichnen
    def _mpl_path(self):
        verts, codes = [], []
        for pts, closed in self.sub:
            if not pts:
                continue
            verts.append(pts[0])
            codes.append(Path.MOVETO)
            for p in pts[1:]:
                verts.append(p)
                codes.append(Path.LINETO)
            if closed:
                verts.append(pts[0])
                codes.append(Path.CLOSEPOLY)
        return Path(verts, codes) if verts else None

    def stroke_preserve(self):
        p = self._mpl_path()
        if p is None:
            return
        self.z += 1
        ls = (0, [d * 0.72 / max(self.lw * 0.72, 0.1) for d in self.dash]) if self.dash else "solid"
        self.ax.add_patch(PathPatch(p, fill=False, edgecolor=self.col, lw=self.lw * 0.72,
                                    linestyle=ls, zorder=self.z))

    def stroke(self):
        self.stroke_preserve()
        self.new_path()

    def fill_preserve(self):
        p = self._mpl_path()
        if p is None:
            return
        self.z += 1
        self.ax.add_patch(PathPatch(p, facecolor=self.col, edgecolor="none", zorder=self.z))

    def fill(self):
        self.fill_preserve()
        self.new_path()

    def paint(self):
        self.z += 1
        self.ax.add_patch(plt.Rectangle((0, 0), self.w, self.h, color=self.col, zorder=self.z))

    def _fp(self):
        return FontProperties(family="DejaVu Sans", weight="bold" if self.bold else "normal")

    def text_extents(self, s):
        if not s:
            return (0, 0, 0, 0, 0, 0)
        tp = TextPath((0, 0), s, size=self.fs, prop=self._fp())
        ext = tp.get_extents()
        return (ext.x0, -ext.y1, ext.width, ext.height, ext.x1 + self.fs * 0.1, 0)

    def show_text(self, s):
        self.z += 1
        x, y = self.cur or (0, 0)
        fp = self._fp()
        fp.set_size(self.fs * 0.72)
        self.ax.text(x, y, s, color=self.col, va="baseline", ha="left", fontproperties=fp,
                     zorder=self.z)
        self.cur = (x + self.text_extents(s)[4], y)

    def write_png(self, path):
        self.fig.savefig(path, dpi=100)
        plt.close(self.fig)
