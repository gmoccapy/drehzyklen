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
"""Einfache Abtragssimulation für Vorschau, Schruppstart und Kollisionsprüfung.

Das Werkstück ist rotationssymmetrisch; für jede Z-Stelle eines feinen Rasters wird ein Ring
[inner, outer] (Innen-/Außenradius) gespeichert. Außenbearbeitung verkleinert den Außenradius,
Innenbearbeitung und Bohren vergrößern den Innenradius, Plandrehen entfernt ein radiales Band.

Jeder Zyklus beschreibt seinen Abtrag mit material_cuts():
    ("outside", punkte)               Material oberhalb der Kontur wird entfernt
    ("inside", punkte)                Material unterhalb der Kontur (Bohrung) wird entfernt
    ("band", z_a, z_b, r_von, r_bis)  radiales Band zwischen z_a und z_b wird entfernt
"""
from __future__ import annotations

import bisect
from typing import List, Optional, Tuple

from .geometry import ContourError

EPS = 1e-6


def inside_length(stock) -> float:
    """Wie weit das Rohteil in der Vorschau im Futter dargestellt wird."""
    return min(20.0, max(5.0, 0.25 * stock.L))


class Material:
    def __init__(self, stock, z_low: float, z_high: float, count: int = 1400):
        self.count = count
        self.z = [z_low + (z_high - z_low) * index / (count - 1) for index in range(count)]
        chuck_inside = stock.z_chuck - inside_length(stock)
        front = stock.z_front
        self.outer = []
        self.inner = []
        for z in self.z:
            if chuck_inside - EPS <= z <= front + EPS:
                self.outer.append(stock.D / 2)
                self.inner.append(stock.d / 2)
            else:
                self.outer.append(0.0)
                self.inner.append(0.0)

    def copy(self) -> "Material":
        clone = Material.__new__(Material)
        clone.count = self.count
        clone.z = self.z
        clone.outer = list(self.outer)
        clone.inner = list(self.inner)
        return clone

    # ---------------------------------------------------------------- Abfragen
    def _indices(self, z_first, z_second):
        low = min(z_first, z_second)
        high = max(z_first, z_second)
        return range(bisect.bisect_left(self.z, low - 1e-9),
                     bisect.bisect_right(self.z, high + 1e-9))

    def has_material(self, index) -> bool:
        return self.outer[index] > self.inner[index] + EPS

    def max_outer(self, z_first, z_second) -> Optional[float]:
        """Größter Außenradius zwischen z_first und z_second (None, wenn dort kein Material)."""
        result = None
        for index in self._indices(z_first, z_second):
            if self.has_material(index):
                if result is None or self.outer[index] > result:
                    result = self.outer[index]
        return result

    def min_inner(self, z_first, z_second) -> Optional[float]:
        """Kleinster Innenradius zwischen z_first und z_second (None, wenn dort kein Material)."""
        result = None
        for index in self._indices(z_first, z_second):
            if self.has_material(index):
                if result is None or self.inner[index] < result:
                    result = self.inner[index]
        return result

    def material_end(self, z_start: float, direction: int) -> float:
        """Vom Punkt z_start in Richtung direction (+1/−1) bis zum Ende des Materials gehen."""
        index = bisect.bisect_left(self.z, z_start)
        index = max(0, min(self.count - 1, index))
        end = z_start
        while 0 <= index < self.count and self.has_material(index):
            if (self.z[index] - z_start) * direction >= 0:
                end = self.z[index]
            index += direction
        return end

    def contains(self, z: float, r: float, margin: float = 0.05) -> bool:
        """Liegt der Punkt (z, Radius) deutlich im Material?

        Der Abstand margin gilt radial und axial: Ein Punkt direkt auf einer eben gedrehten
        Planfläche zählt nicht als „im Material“, auch wenn der nächste Rasterpunkt knapp
        dahinter liegt.
        """
        if z < self.z[0] or z > self.z[-1]:
            return False
        grid_step = (self.z[-1] - self.z[0]) / (self.count - 1)
        margin_z = max(margin, 1.5 * grid_step)
        indices = self._indices(z - margin_z, z + margin_z)
        if len(indices) == 0:
            nearest = min(self.count - 1, bisect.bisect_left(self.z, z))
            indices = [nearest]
        radius = abs(r)
        for index in indices:
            if not self.has_material(index):
                return False
            if not self.inner[index] + margin < radius < self.outer[index] - margin:
                return False
        return True

    # ---------------------------------------------------------------- Abtrag
    def _profile(self, points, choose):
        profile = {}
        for (z_start, r_start), (z_end, r_end) in zip(points, points[1:]):
            for index in self._indices(z_start, z_end):
                z = self.z[index]
                if abs(z_end - z_start) < 1e-12:
                    radius = choose(r_start, r_end)
                else:
                    radius = r_start + (z - z_start) / (z_end - z_start) * (r_end - r_start)
                if index in profile:
                    profile[index] = choose(profile[index], radius)
                else:
                    profile[index] = radius
        return profile

    def cut_outside(self, points):
        for index, radius in self._profile(points, max).items():
            self.outer[index] = min(self.outer[index], max(radius, 0.0))

    def cut_inside(self, points):
        for index, radius in self._profile(points, min).items():
            self.inner[index] = max(self.inner[index], radius)

    def cut_band(self, z_first, z_second, radius_low, radius_high):
        for index in self._indices(z_first, z_second):
            if radius_low <= self.inner[index] + EPS:
                self.inner[index] = max(self.inner[index], min(radius_high, self.outer[index]))
            elif radius_high >= self.outer[index] - EPS:
                self.outer[index] = min(self.outer[index], radius_low)

    def apply(self, op) -> "Material":
        try:
            cuts = op.material_cuts(self)
        except (ContourError, ValueError, TypeError, ZeroDivisionError):
            return self
        for cut in cuts:
            if cut[0] == "outside":
                self.cut_outside(cut[1])
            elif cut[0] == "inside":
                self.cut_inside(cut[1])
            elif cut[0] == "band":
                self.cut_band(cut[1], cut[2], cut[3], cut[4])
        return self

    # ---------------------------------------------------------------- Darstellung
    def polygons(self) -> List[List[Tuple[float, float]]]:
        polygons = []
        run = []
        for index in range(self.count):
            if self.has_material(index):
                run.append(index)
            elif run:
                polygons.append(self._polygon(run))
                run = []
        if run:
            polygons.append(self._polygon(run))
        return polygons

    def _polygon(self, run):
        upper = [(self.z[index], self.outer[index]) for index in run]
        lower = [(self.z[index], self.inner[index]) for index in reversed(run)]
        return upper + lower


def bounds(stock, ops) -> Tuple[float, float]:
    z_low = stock.z_chuck - inside_length(stock)
    z_high = stock.z_front
    for op in ops:
        try:
            if op.HAS_TOOL:
                op_low, op_high = op.z_range()
                z_low = min(z_low, op_low)
                z_high = max(z_high, op_high)
        except (ContourError, ValueError, TypeError, ZeroDivisionError):
            pass
    return z_low - 1.0, z_high + 1.0


def simulate(stock, ops, upto: Optional[int] = None, extra=None) -> Material:
    """Material nach ops[:upto]; extra = weitere Operation, die ins Raster passen soll."""
    all_ops = list(ops)
    if extra is not None:
        all_ops.append(extra)
    z_low, z_high = bounds(stock, all_ops)
    material = Material(stock, z_low, z_high)
    if upto is None:
        upto = len(ops)
    for op in ops[:upto]:
        material.apply(op)
    return material
