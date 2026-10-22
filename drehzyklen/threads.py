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
"""Gewindetabellen und daraus berechnete Werte für den Gewindezyklus.

Normen: metrisches ISO-Gewinde (Regel- und Feingewinde, 60°), UNC und UNF (60°),
Whitworth BSW und Rohrgewinde G/BSP (55°).

Berechnung (Grundprofil, ohne Toleranzfeld):
  60°: Flankentiefe außen h3 = 0,6134·P, innen H1 = 0,5413·P, Kern innen D1 = D − 1,0825·P
  55°: Flankentiefe außen und innen h = 0,6403·P, Kern D1 = D − 1,2806·P
Vordrehen außen (Faustformel): D − 0,1·P. Das entspricht etwa der Mitte des Toleranzfelds 6g.
Vordrehen innen: D1 + 0,1·P (etwa Mitte des Toleranzfelds 6H für den Kerndurchmesser).
Zustellwinkel Q: halber Flankenwinkel − 0,5° (29,5° bzw. 27°), damit die zweite Flanke
leicht nachschabt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

INCH = 25.4


@dataclass(frozen=True)
class ThreadSpec:
    name: str            # z.B. "M12", "M12x1", "1/4-28 UNF", "G1/2"
    norm: str            # Schlüssel aus NORMS
    diameter: float      # Nenn-Ø (Außendurchmesser) in mm
    pitch: float         # Steigung in mm
    angle: float         # Flankenwinkel in Grad

    # ---------------------------------------------------------------- Profil
    def depth(self, inside: bool) -> float:
        if self.angle < 57.5:
            return 0.6403 * self.pitch
        if inside:
            return 0.5413 * self.pitch
        return 0.6134 * self.pitch

    def minor_diameter(self) -> float:
        """Kerndurchmesser der Mutter D1 (Grundprofil)."""
        if self.angle < 57.5:
            return self.diameter - 1.2806 * self.pitch
        return self.diameter - 1.0825 * self.pitch

    def infeed(self) -> float:
        return self.angle / 2 - 0.5

    def pre_turn_diameter(self, inside: bool) -> float:
        """Vordreh-Ø: außen D − 0,1·P, innen D1 + 0,1·P."""
        if inside:
            return self.minor_diameter() + 0.1 * self.pitch
        return self.diameter - 0.1 * self.pitch

    def cycle_diameter(self, inside: bool) -> float:
        """Wert für das Feld D im Gewindezyklus: außen Nenn-Ø, innen Kernloch-Ø."""
        if inside:
            return self.minor_diameter()
        return self.diameter

    def pitch_text(self) -> str:
        if self.norm in ("UNC", "UNF", "BSW", "G"):
            threads_per_inch = INCH / self.pitch
            return f"{threads_per_inch:g} Gg/Zoll = P{self.pitch:.4g}"
        return f"P{self.pitch:g}"


# ------------------------------------------------------------------ Tabellen

NORMS: Dict[str, str] = {
    "frei": "frei (eigene Werte)",
    "M": "Metrisch Regelgewinde",
    "MF": "Metrisch Feingewinde",
    "UNC": "UNC (Zoll, grob)",
    "UNF": "UNF (Zoll, fein)",
    "BSW": "Whitworth BSW (55°)",
    "G": "Rohrgewinde G / BSP (55°)",
}

_METRIC_COARSE = [(3, 0.5), (4, 0.7), (5, 0.8), (6, 1.0), (8, 1.25), (10, 1.5), (12, 1.75),
                  (14, 2.0), (16, 2.0), (18, 2.5), (20, 2.5), (22, 2.5), (24, 3.0), (27, 3.0),
                  (30, 3.5), (33, 3.5), (36, 4.0), (39, 4.0), (42, 4.5), (45, 4.5), (48, 5.0),
                  (52, 5.0), (56, 5.5), (60, 5.5), (64, 6.0)]

_METRIC_FINE = [(6, 0.75), (8, 0.75), (8, 1.0), (10, 0.75), (10, 1.0), (10, 1.25), (12, 1.0),
                (12, 1.25), (12, 1.5), (14, 1.0), (14, 1.25), (14, 1.5), (16, 1.0), (16, 1.5),
                (18, 1.0), (18, 1.5), (18, 2.0), (20, 1.0), (20, 1.5), (20, 2.0), (22, 1.0),
                (22, 1.5), (22, 2.0), (24, 1.0), (24, 1.5), (24, 2.0), (27, 1.5), (27, 2.0),
                (30, 1.5), (30, 2.0), (33, 1.5), (33, 2.0), (36, 1.5), (36, 2.0), (36, 3.0),
                (39, 1.5), (39, 2.0), (39, 3.0), (42, 1.5), (42, 2.0), (42, 3.0), (45, 1.5),
                (45, 2.0), (45, 3.0), (48, 1.5), (48, 2.0), (48, 3.0), (52, 1.5), (52, 2.0),
                (52, 3.0), (56, 2.0), (56, 3.0), (56, 4.0), (60, 2.0), (60, 3.0), (60, 4.0),
                (64, 2.0), (64, 3.0), (64, 4.0)]

# Zollgewinde: (Name, Nenn-Ø in Zoll, Gänge je Zoll)
_UNC = [("#4-40", 0.112, 40), ("#5-40", 0.125, 40), ("#6-32", 0.138, 32), ("#8-32", 0.164, 32),
        ("#10-24", 0.190, 24), ("#12-24", 0.216, 24), ("1/4-20", 0.25, 20),
        ("5/16-18", 0.3125, 18), ("3/8-16", 0.375, 16), ("7/16-14", 0.4375, 14),
        ("1/2-13", 0.5, 13), ("9/16-12", 0.5625, 12), ("5/8-11", 0.625, 11), ("3/4-10", 0.75, 10),
        ("7/8-9", 0.875, 9), ("1-8", 1.0, 8), ("1 1/8-7", 1.125, 7), ("1 1/4-7", 1.25, 7),
        ("1 3/8-6", 1.375, 6), ("1 1/2-6", 1.5, 6), ("1 3/4-5", 1.75, 5),
        ("2-4 1/2", 2.0, 4.5)]

_UNF = [("#4-48", 0.112, 48), ("#5-44", 0.125, 44), ("#6-40", 0.138, 40), ("#8-36", 0.164, 36),
        ("#10-32", 0.190, 32), ("#12-28", 0.216, 28), ("1/4-28", 0.25, 28),
        ("5/16-24", 0.3125, 24), ("3/8-24", 0.375, 24), ("7/16-20", 0.4375, 20),
        ("1/2-20", 0.5, 20), ("9/16-18", 0.5625, 18), ("5/8-18", 0.625, 18), ("3/4-16", 0.75, 16),
        ("7/8-14", 0.875, 14), ("1-12", 1.0, 12), ("1 1/8-12", 1.125, 12),
        ("1 1/4-12", 1.25, 12), ("1 3/8-12", 1.375, 12), ("1 1/2-12", 1.5, 12)]

_BSW = [("1/8-40", 0.125, 40), ("3/16-24", 0.1875, 24), ("1/4-20", 0.25, 20),
        ("5/16-18", 0.3125, 18), ("3/8-16", 0.375, 16), ("7/16-14", 0.4375, 14),
        ("1/2-12", 0.5, 12), ("5/8-11", 0.625, 11), ("3/4-10", 0.75, 10), ("7/8-9", 0.875, 9),
        ("1-8", 1.0, 8), ("1 1/8-7", 1.125, 7), ("1 1/4-7", 1.25, 7), ("1 1/2-6", 1.5, 6),
        ("1 3/4-5", 1.75, 5), ("2-4 1/2", 2.0, 4.5)]

# Rohrgewinde G (ISO 228) / BSP: (Name, Außen-Ø in mm, Gänge je Zoll)
_PIPE = [("G1/16", 7.723, 28), ("G1/8", 9.728, 28), ("G1/4", 13.157, 19), ("G3/8", 16.662, 19),
         ("G1/2", 20.955, 14), ("G5/8", 22.911, 14), ("G3/4", 26.441, 14), ("G7/8", 30.201, 14),
         ("G1", 33.249, 11), ("G1 1/8", 37.897, 11), ("G1 1/4", 41.910, 11),
         ("G1 1/2", 47.803, 11), ("G1 3/4", 53.746, 11), ("G2", 59.614, 11),
         ("G2 1/2", 75.184, 11), ("G3", 87.884, 11)]


def _number_text(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _build() -> List[ThreadSpec]:
    specs = []
    for diameter, pitch in _METRIC_COARSE:
        specs.append(ThreadSpec(f"M{diameter}", "M", float(diameter), pitch, 60.0))
    for diameter, pitch in _METRIC_FINE:
        specs.append(ThreadSpec(f"M{diameter}x{_number_text(pitch)}", "MF", float(diameter),
                                pitch, 60.0))
    for name, inch, threads_per_inch in _UNC:
        specs.append(ThreadSpec(f"{name} UNC", "UNC", inch * INCH, INCH / threads_per_inch, 60.0))
    for name, inch, threads_per_inch in _UNF:
        specs.append(ThreadSpec(f"{name} UNF", "UNF", inch * INCH, INCH / threads_per_inch, 60.0))
    for name, inch, threads_per_inch in _BSW:
        specs.append(ThreadSpec(f"{name} BSW", "BSW", inch * INCH, INCH / threads_per_inch, 55.0))
    for name, diameter, threads_per_inch in _PIPE:
        specs.append(ThreadSpec(name, "G", diameter, INCH / threads_per_inch, 55.0))
    return specs


THREADS: List[ThreadSpec] = _build()

# Auswahl je Norm (für die Formularfelder)
SIZE_KEYS = {"M": "size_m", "MF": "size_mf", "UNC": "size_unc", "UNF": "size_unf",
             "BSW": "size_bsw", "G": "size_g"}


def names_for(norm: str) -> Tuple[str, ...]:
    return tuple(spec.name for spec in THREADS if spec.norm == norm)


def find(norm: str, name: str) -> Optional[ThreadSpec]:
    for spec in THREADS:
        if spec.norm == norm and spec.name == name:
            return spec
    return None


def norm_of_label(label: str) -> str:
    """Normschlüssel aus der Beschriftung der Auswahl (oder dem Schlüssel selbst)."""
    for key, text in NORMS.items():
        if label in (key, text):
            return key
    return "frei"
