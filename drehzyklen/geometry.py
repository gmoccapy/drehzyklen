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
"""Geometrie für Drehteile: Konturbeschreibung, Kreisbögen, Eckenradien/-fasen, Schruppebenen.

Koordinaten:
    x  = Durchmesser (wie im G-Code mit G7)
    r  = Radius (intern für die Geometrie)
    z  = Längsachse
Die Drehrichtung von Bögen bezieht sich auf die Z/X-Ebene wie im G-Code (G18):
G3 = gegen den Uhrzeigersinn, wenn Z nach rechts und X nach oben zeigt.

Konturtext – eine Zeile pro Element:
    X.. Z..                 Gerade zu diesem Punkt (U/W inkremental möglich)
    G2|G3 X.. Z.. R..       Bogen mit Radius (kurzer Bogen, < 180°)
    G2|G3 X.. Z.. R.. LA    Bogen mit Radius, langer Bogen (> 180°)
    G2|G3 X.. Z.. I.. K..   Bogen über Mittelpunkt (I radial, K axial, relativ zum Anfangspunkt)
    G3 X.. Z.. TS           Bogen, tangential an das vorherige Element (Radius ergibt sich)
    G3 X.. Z.. TE           Bogen, tangential in die folgende Gerade (Radius ergibt sich)
Ecke am Endpunkt einer Zeile:
    R5 (bei Geraden) bzw. RND5      Eckenradius
    C2 bzw. CHR2                    Fase 45° (Schenkellänge 2)
    C2 A30                          Fase: 2 mm auf der ankommenden Kante, 30° zur ankommenden Kante
    C2 B30                          Fase: 2 mm auf der abgehenden Kante, 30° zur abgehenden Kante
    R0 / RND0                       diese Ecke ohne Radius/Fase (überschreibt "Alle Ecken")
Kommentare mit # oder ( ), Dezimalkomma erlaubt, mehrere Elemente pro Zeile mit ';'.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

EPS = 1e-7
TWO_PI = 2 * math.pi


class ContourError(ValueError):
    """Fehler in der Konturbeschreibung (Meldung ist für den Bediener gedacht)."""


# =========================================================================== Vektorhilfen

def unit_vector(delta_z, delta_r):
    length = math.hypot(delta_z, delta_r)
    if length < 1e-12:
        raise ContourError("Zwei aufeinanderfolgende Punkte sind identisch")
    return delta_z / length, delta_r / length


def left_normal(vector):
    return -vector[1], vector[0]


def cross_product(first, second):
    return first[0] * second[1] - first[1] * second[0]


def dot_product(first, second):
    return first[0] * second[0] + first[1] * second[1]


def rotate(vector, angle):
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return vector[0] * cosine - vector[1] * sine, vector[0] * sine + vector[1] * cosine


def distance(first, second):
    return math.hypot(first[0] - second[0], first[1] - second[1])


def sweep_angle(start_angle: float, end_angle: float, counter_clockwise: bool) -> float:
    difference = end_angle - start_angle
    if counter_clockwise:
        while difference <= 1e-12:
            difference += TWO_PI
    else:
        while difference >= -1e-12:
            difference -= TWO_PI
    return difference


def format_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text


# =========================================================================== Elemente

@dataclass
class Element:
    kind: str                   # "line" | "arc"
    z0: float
    r0: float
    z1: float
    r1: float
    radius: float = 0.0
    ccw: bool = False           # G3 (True) / G2 (False) in G18
    cz: float = 0.0             # Mittelpunkt Z
    cr: float = 0.0             # Mittelpunkt Radius

    @property
    def start(self):
        return self.z0, self.r0

    @property
    def end(self):
        return self.z1, self.r1

    def sweep(self) -> float:
        if distance(self.start, self.end) < 1e-9:
            if self.ccw:
                return TWO_PI
            return -TWO_PI
        start_angle = math.atan2(self.r0 - self.cr, self.z0 - self.cz)
        end_angle = math.atan2(self.r1 - self.cr, self.z1 - self.cz)
        return sweep_angle(start_angle, end_angle, self.ccw)

    def tangent_at(self, point):
        if self.kind == "line":
            return unit_vector(self.z1 - self.z0, self.r1 - self.r0)
        radial = unit_vector(point[0] - self.cz, point[1] - self.cr)
        if self.ccw:
            return left_normal(radial)
        return radial[1], -radial[0]

    def tangent_start(self):
        return self.tangent_at(self.start)

    def tangent_end(self):
        return self.tangent_at(self.end)

    def length(self) -> float:
        if self.kind == "line":
            return distance(self.start, self.end)
        return abs(self.sweep()) * self.radius

    def sample(self, step_deg: float = 2.0) -> List[Tuple[float, float]]:
        """Punkte (z, r) entlang des Elements, inkl. Anfang und Ende."""
        if self.kind == "line":
            return [self.start, self.end]
        start_angle = math.atan2(self.r0 - self.cr, self.z0 - self.cz)
        sweep = self.sweep()
        steps = max(2, int(abs(math.degrees(sweep)) / step_deg) + 1)
        points = []
        for step in range(steps + 1):
            angle = start_angle + sweep * step / steps
            points.append((self.cz + self.radius * math.cos(angle),
                           self.cr + self.radius * math.sin(angle)))
        points[0] = self.start
        points[-1] = self.end
        return points

    def transformed(self, radius_sign: int, z_sign: int) -> "Element":
        """Gespiegelte Kopie: radius_sign=-1 spiegelt an der Drehachse, z_sign=-1 an Z=0."""
        counter_clockwise = self.ccw
        if radius_sign * z_sign < 0:
            counter_clockwise = not counter_clockwise
        return Element(self.kind, z_sign * self.z0, radius_sign * self.r0, z_sign * self.z1,
                       radius_sign * self.r1, self.radius, counter_clockwise,
                       z_sign * self.cz, radius_sign * self.cr)

    def with_ends(self, new_start, new_end) -> "Element":
        return Element(self.kind, new_start[0], new_start[1], new_end[0], new_end[1],
                       self.radius, self.ccw, self.cz, self.cr)

    def param(self, point) -> float:
        """Lage eines Punktes auf dem Element: 0 = Anfang, 1 = Ende."""
        if self.kind == "line":
            delta_z = self.z1 - self.z0
            delta_r = self.r1 - self.r0
            length_squared = delta_z * delta_z + delta_r * delta_r
            return ((point[0] - self.z0) * delta_z + (point[1] - self.r0) * delta_r) / length_squared
        start_angle = math.atan2(self.r0 - self.cr, self.z0 - self.cz)
        angle = math.atan2(point[1] - self.cr, point[0] - self.cz)
        total = abs(self.sweep())
        if self.ccw:
            travelled = (angle - start_angle) % TWO_PI
        else:
            travelled = (start_angle - angle) % TWO_PI
        if travelled > total + 1e-9 and TWO_PI - travelled < 1e-5:
            travelled -= TWO_PI
        return travelled / total

    def carrier(self):
        """Trägerkurve: ("line", Punkt, Richtung) oder ("circle", Mittelpunkt, Radius)."""
        if self.kind == "line":
            return ("line", self.start, unit_vector(self.z1 - self.z0, self.r1 - self.r0))
        return ("circle", (self.cz, self.cr), self.radius)

    def foot(self, point):
        """Nächster Punkt der (unendlichen) Trägerkurve zu point."""
        if self.kind == "line":
            direction = unit_vector(self.z1 - self.z0, self.r1 - self.r0)
            along = (point[0] - self.z0) * direction[0] + (point[1] - self.r0) * direction[1]
            return self.z0 + along * direction[0], self.r0 + along * direction[1]
        radial = unit_vector(point[0] - self.cz, point[1] - self.cr)
        return self.cz + self.radius * radial[0], self.cr + self.radius * radial[1]


def offset_carrier(element: Element, offset: float):
    """Trägerkurve um offset nach links (in Fahrtrichtung) versetzt."""
    kind, first, second = element.carrier()
    if kind == "line":
        normal = left_normal(second)
        return ("line", (first[0] + offset * normal[0], first[1] + offset * normal[1]), second)
    if element.ccw:
        new_radius = second - offset
    else:
        new_radius = second + offset
    if new_radius <= 1e-9:
        return None
    return ("circle", first, new_radius)


def intersect(first_curve, second_curve):
    """Schnittpunkte zweier Trägerkurven (Gerade/Kreis)."""
    if first_curve is None or second_curve is None:
        return []
    if first_curve[0] == "circle" and second_curve[0] == "line":
        first_curve, second_curve = second_curve, first_curve
    if first_curve[0] == "line" and second_curve[0] == "line":
        point, direction = first_curve[1], first_curve[2]
        other_point, other_direction = second_curve[1], second_curve[2]
        denominator = cross_product(direction, other_direction)
        if abs(denominator) < 1e-12:
            return []
        offset = (other_point[0] - point[0], other_point[1] - point[1])
        along = cross_product(offset, other_direction) / denominator
        return [(point[0] + along * direction[0], point[1] + along * direction[1])]
    if first_curve[0] == "line":
        point, direction = first_curve[1], first_curve[2]
        center, radius = second_curve[1], second_curve[2]
        relative = (point[0] - center[0], point[1] - center[1])
        half_b = dot_product(relative, direction)
        discriminant = half_b * half_b - (dot_product(relative, relative) - radius * radius)
        if discriminant < -1e-9:
            return []
        root = math.sqrt(max(discriminant, 0.0))
        results = []
        for along in (-half_b - root, -half_b + root):
            results.append((point[0] + along * direction[0], point[1] + along * direction[1]))
        return results
    center_one, radius_one = first_curve[1], first_curve[2]
    center_two, radius_two = second_curve[1], second_curve[2]
    center_distance = distance(center_one, center_two)
    if center_distance < 1e-12:
        return []
    if center_distance > radius_one + radius_two + 1e-9:
        return []
    if center_distance < abs(radius_one - radius_two) - 1e-9:
        return []
    along = (radius_one ** 2 - radius_two ** 2 + center_distance ** 2) / (2 * center_distance)
    height = math.sqrt(max(radius_one ** 2 - along ** 2, 0.0))
    axis = ((center_two[0] - center_one[0]) / center_distance,
            (center_two[1] - center_one[1]) / center_distance)
    middle = (center_one[0] + along * axis[0], center_one[1] + along * axis[1])
    normal = left_normal(axis)
    return [(middle[0] + height * normal[0], middle[1] + height * normal[1]),
            (middle[0] - height * normal[0], middle[1] - height * normal[1])]


def lies_on(element: Element, point, tolerance=1e-6) -> bool:
    position = element.param(point)
    return -tolerance <= position <= 1 + tolerance


# =========================================================================== Konturzeilen

@dataclass
class Row:
    kind: str = "L"                 # "L" Gerade, "G2", "G3"
    x: float = 0.0                  # Durchmesser, absolut
    z: float = 0.0
    corner: Optional[str] = None    # None = Vorgabe, "N" = keine, "R" Radius, "C" Fase
    csize: float = 0.0
    cangle: Optional[float] = None  # Fasenwinkel in Grad (None = 45°/gleiche Schenkel)
    cref: str = "A"                 # "A" ankommende Kante, "B" abgehende Kante
    arc: str = "R"                  # "R", "RL" (>180°), "TS", "TE", "IK"
    ar: float = 0.0                 # Bogenradius
    ai: float = 0.0                 # Mittelpunkt relativ, radial
    ak: float = 0.0                 # Mittelpunkt relativ, axial
    line_no: int = 0

    @property
    def is_arc(self):
        return self.kind in ("G2", "G3")


WORD_PATTERN = re.compile(r"([A-Z]+)\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))?")
NUMBER_WORDS = {"X", "Z", "U", "W", "R", "C", "A", "B", "I", "K", "RND", "CHR", "G"}
FLAG_WORDS = {"LA", "TS", "TE"}


def _corner_from_words(values: dict, is_arc: bool, line_no: int):
    corner = None
    size = 0.0
    if "RND" in values:
        corner, size = "R", values["RND"]
    elif "CHR" in values:
        corner, size = "C", values["CHR"]
    elif "C" in values:
        corner, size = "C", values["C"]
    elif not is_arc and "R" in values:
        corner, size = "R", values["R"]
    if corner and size < 0:
        raise ContourError(f"Zeile {line_no}: Eckengröße darf nicht negativ sein")
    if corner and size <= EPS:
        corner = "N"
    angle = None
    reference = "A"
    if "A" in values or "B" in values:
        if corner != "C":
            raise ContourError(f"Zeile {line_no}: Winkel A/B nur zusammen mit einer Fase C")
        if "A" in values:
            reference = "A"
        else:
            reference = "B"
        angle = values[reference]
        if not 0 < angle < 180:
            raise ContourError(f"Zeile {line_no}: Fasenwinkel muss zwischen 0 und 180° liegen")
    return corner, size, angle, reference


def _copy_corner(target: Row, corner, size, angle, reference):
    target.corner = corner
    target.csize = size
    target.cangle = angle
    target.cref = reference


def parse_rows(text: str) -> List[Row]:
    rows: List[Row] = []
    current_x = None
    current_z = None
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        line = re.sub(r"\(.*?\)", " ", raw_line.split("#")[0]).upper()
        line = re.sub(r"(\d),(\d)", r"\1.\2", line)
        for chunk in line.split(";"):
            chunk = chunk.replace(",", " ").strip()
            if not chunk:
                continue
            rest = WORD_PATTERN.sub("", chunk).strip()
            if rest:
                raise ContourError(f"Zeile {line_no}: unbekannter Text '{rest}'")
            values = {}
            flags = set()
            for word, number in WORD_PATTERN.findall(chunk):
                if word in FLAG_WORDS:
                    if number:
                        raise ContourError(f"Zeile {line_no}: {word} hat keinen Zahlenwert")
                    flags.add(word)
                elif word in NUMBER_WORDS:
                    if not number:
                        raise ContourError(f"Zeile {line_no}: {word} ohne Zahlenwert")
                    values[word] = float(number)
                else:
                    raise ContourError(f"Zeile {line_no}: unbekanntes Wort '{word}'")
            kind = "L"
            if "G" in values:
                g_number = int(round(values["G"]))
                if g_number in (0, 1):
                    kind = "L"
                elif g_number in (2, 3):
                    kind = f"G{g_number}"
                else:
                    raise ContourError(f"Zeile {line_no}: nur G1, G2 und G3 sind erlaubt")
            is_arc = kind != "L"

            new_x = current_x
            new_z = current_z
            if "X" in values:
                new_x = values["X"]
            if "U" in values:
                if current_x is None:
                    raise ContourError(f"Zeile {line_no}: U ohne vorherigen X-Wert")
                new_x = current_x + values["U"]
            if "Z" in values:
                new_z = values["Z"]
            if "W" in values:
                if current_z is None:
                    raise ContourError(f"Zeile {line_no}: W ohne vorherigen Z-Wert")
                new_z = current_z + values["W"]
            has_coordinate = any(key in values for key in "XZUW")
            corner, size, angle, reference = _corner_from_words(values, is_arc, line_no)

            if not has_coordinate and not is_arc:
                if not rows:
                    raise ContourError(f"Zeile {line_no}: Der erste Punkt braucht X und Z")
                if corner:
                    _copy_corner(rows[-1], corner, size, angle, reference)
                continue
            if new_x is None or new_z is None:
                raise ContourError(f"Zeile {line_no}: Der erste Punkt braucht X und Z")
            if new_x < -EPS:
                raise ContourError(f"Zeile {line_no}: negativer Durchmesser X{format_number(new_x)}")
            row = Row(kind, new_x, new_z, line_no=line_no)
            _copy_corner(row, corner, size, angle, reference)
            if is_arc:
                if not rows:
                    raise ContourError(f"Zeile {line_no}: Die Kontur muss mit einem Punkt beginnen")
                if "TS" in flags:
                    row.arc = "TS"
                elif "TE" in flags:
                    row.arc = "TE"
                elif "I" in values or "K" in values:
                    row.arc = "IK"
                    row.ai = values.get("I", 0.0)
                    row.ak = values.get("K", 0.0)
                elif "R" in values:
                    if values["R"] <= 0:
                        raise ContourError(f"Zeile {line_no}: Bogenradius muss positiv sein")
                    if "LA" in flags:
                        row.arc = "RL"
                    else:
                        row.arc = "R"
                    row.ar = values["R"]
                else:
                    raise ContourError(f"Zeile {line_no}: Bogen braucht R, I/K, TS oder TE")
            elif flags:
                raise ContourError(f"Zeile {line_no}: {', '.join(sorted(flags))} nur bei G2/G3")
            same_point = (rows and abs(rows[-1].x - new_x) < EPS and abs(rows[-1].z - new_z) < EPS)
            if same_point and not is_arc:
                if corner:
                    _copy_corner(rows[-1], corner, size, angle, reference)
                continue
            current_x = new_x
            current_z = new_z
            rows.append(row)
    if len(rows) < 2:
        raise ContourError("Die Kontur braucht mindestens zwei Punkte")
    return rows


def row_to_text(row: Row, first: bool = False) -> str:
    parts = []
    write_arc = row.is_arc and not first
    if write_arc:
        parts.append(row.kind)
    parts.append("X" + format_number(row.x))
    parts.append("Z" + format_number(row.z))
    if write_arc:
        if row.arc in ("R", "RL"):
            parts.append("R" + format_number(row.ar))
            if row.arc == "RL":
                parts.append("LA")
        elif row.arc == "IK":
            parts.append("I" + format_number(row.ai))
            parts.append("K" + format_number(row.ak))
        else:
            parts.append(row.arc)
    if write_arc:
        radius_word, chamfer_word = "RND", "CHR"
    else:
        radius_word, chamfer_word = "R", "C"
    if row.corner == "N":
        parts.append(radius_word + "0")
    elif row.corner == "R":
        parts.append(radius_word + format_number(row.csize))
    elif row.corner == "C":
        parts.append(chamfer_word + format_number(row.csize))
        if row.cangle is not None:
            parts.append(row.cref + format_number(row.cangle))
    return " ".join(parts)


def rows_to_text(rows: List[Row]) -> str:
    lines = []
    for index, row in enumerate(rows):
        lines.append(row_to_text(row, index == 0))
    return "\n".join(lines)


# =========================================================================== Elemente bauen

def _arc_element(start, end, row: Row, previous: Optional[Element], next_point,
                 point_number: int) -> Element:
    counter_clockwise = row.kind == "G3"
    if distance(start, end) < 1e-9:
        raise ContourError(f"Punkt {point_number}: Bogen hat gleichen Anfangs- und Endpunkt")
    if row.arc in ("R", "RL"):
        radius = row.ar
        chord = (end[0] - start[0], end[1] - start[1])
        chord_length = math.hypot(*chord)
        if radius < chord_length / 2 - 1e-6:
            raise ContourError(f"Punkt {point_number}: Radius R{format_number(radius)} zu klein – "
                               f"mindestens R{format_number(chord_length / 2 + 0.0005)}")
        height = math.sqrt(max(radius * radius - (chord_length / 2) ** 2, 0.0))
        normal = left_normal((chord[0] / chord_length, chord[1] / chord_length))
        if counter_clockwise:
            side = 1.0
        else:
            side = -1.0
        if row.arc == "RL":
            side = -side
        middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        center = (middle[0] + side * height * normal[0], middle[1] + side * height * normal[1])
        return Element("arc", start[0], start[1], end[0], end[1], radius, counter_clockwise,
                       center[0], center[1])
    if row.arc == "IK":
        center = (start[0] + row.ak, start[1] + row.ai)
        radius = distance(start, center)
        if radius < 1e-6:
            raise ContourError(f"Punkt {point_number}: Mittelpunkt liegt auf dem Anfangspunkt")
        deviation = abs(distance(end, center) - radius)
        if deviation > 0.01:
            raise ContourError(f"Punkt {point_number}: Endpunkt liegt nicht auf dem Kreis um den "
                               f"Mittelpunkt (Abweichung {deviation:.3f} mm)")
        return Element("arc", start[0], start[1], end[0], end[1], radius, counter_clockwise,
                       center[0], center[1])
    if row.arc == "TS":
        if previous is None:
            raise ContourError(f"Punkt {point_number}: 'tangential am Anfang' braucht ein "
                               f"vorheriges Element")
        tangent = previous.tangent_end()
        base_point, other_point = start, end
    else:
        if next_point is None:
            raise ContourError(f"Punkt {point_number}: 'tangential am Ende' braucht eine "
                               f"folgende Gerade")
        tangent = unit_vector(next_point[0] - end[0], next_point[1] - end[1])
        base_point, other_point = end, start
    normal = left_normal(tangent)
    offset = (base_point[0] - other_point[0], base_point[1] - other_point[1])
    denominator = 2 * dot_product(offset, normal)
    if abs(denominator) < 1e-9:
        raise ContourError(f"Punkt {point_number}: Endpunkt liegt auf der Tangente – hier passt "
                           f"eine Gerade")
    signed_radius = -dot_product(offset, offset) / denominator
    center = (base_point[0] + signed_radius * normal[0], base_point[1] + signed_radius * normal[1])
    return Element("arc", start[0], start[1], end[0], end[1], abs(signed_radius),
                   signed_radius > 0, center[0], center[1])


def _point_at(element: Element, length: float, from_end: bool, point_number: int):
    if from_end:
        vertex = element.end
    else:
        vertex = element.start
    if element.kind == "line":
        direction = unit_vector(element.z1 - element.z0, element.r1 - element.r0)
        if from_end:
            sign = -1
        else:
            sign = 1
        candidates = [(vertex[0] + sign * length * direction[0],
                       vertex[1] + sign * length * direction[1])]
    else:
        candidates = intersect(("circle", vertex, length),
                               ("circle", (element.cz, element.cr), element.radius))
    valid = [point for point in candidates if lies_on(element, point)]
    if not valid:
        raise ContourError(f"Punkt {point_number}: Fase {format_number(length)} ist länger als "
                           f"das Element")
    if from_end:
        return max(valid, key=element.param)
    return min(valid, key=element.param)


def _ray_hit(origin, direction, element: Element, point_number: int):
    best_distance = None
    best_point = None
    for point in intersect(("line", origin, direction), element.carrier()):
        along = (point[0] - origin[0]) * direction[0] + (point[1] - origin[1]) * direction[1]
        if along > 1e-9 and lies_on(element, point):
            if best_distance is None or along < best_distance:
                best_distance = along
                best_point = point
    if best_point is None:
        raise ContourError(f"Punkt {point_number}: Fase mit diesem Winkel trifft die Nachbarkante "
                           f"nicht")
    return best_point


def _corner(first: Element, second: Element, kind: str, size: float, angle, reference,
            point_number: int):
    tangent_in = first.tangent_end()
    tangent_out = second.tangent_start()
    turn = cross_product(tangent_in, tangent_out)
    if abs(turn) < 1e-9:
        if dot_product(tangent_in, tangent_out) > 0:
            return None                       # tangentialer Übergang, keine Ecke
        raise ContourError(f"Punkt {point_number}: Kontur kehrt um, Ecke nicht möglich")
    if turn > 0:
        turn_sign = 1
    else:
        turn_sign = -1
    vertex = first.end
    if kind == "R":
        candidates = intersect(offset_carrier(first, turn_sign * size),
                               offset_carrier(second, turn_sign * size))
        best = None
        for center in candidates:
            touch_first = first.foot(center)
            touch_second = second.foot(center)
            if lies_on(first, touch_first) and lies_on(second, touch_second):
                center_distance = distance(center, vertex)
                if best is None or center_distance < best[0]:
                    best = (center_distance, center, touch_first, touch_second)
        if best is None:
            raise ContourError(f"Punkt {point_number}: Radius R{format_number(size)} passt nicht "
                               f"an diese Ecke (zu wenig Platz)")
        _distance, center, touch_first, touch_second = best
        fillet = Element("arc", touch_first[0], touch_first[1], touch_second[0], touch_second[1],
                         size, turn_sign > 0, center[0], center[1])
        return touch_first, touch_second, [fillet]
    # Fase
    if angle is None:
        point_first = _point_at(first, size, True, point_number)
        point_second = _point_at(second, size, False, point_number)
    elif reference == "A":
        point_first = _point_at(first, size, True, point_number)
        direction = rotate(tangent_in, turn_sign * math.radians(angle))
        point_second = _ray_hit(point_first, direction, second, point_number)
    else:
        point_second = _point_at(second, size, False, point_number)
        direction = rotate((-tangent_out[0], -tangent_out[1]), -turn_sign * math.radians(angle))
        point_first = _ray_hit(point_second, direction, first, point_number)
    chamfer = Element("line", point_first[0], point_first[1], point_second[0], point_second[1])
    return point_first, point_second, [chamfer]


def resolve_rows(rows: List[Row], default_corner: Optional[str] = None,
                 default_size: float = 0.0) -> List[Element]:
    """Wandelt Konturzeilen in Linien und Kreisbögen um (inkl. Eckenradien/-fasen)."""
    points = [(row.z, row.x / 2.0) for row in rows]
    count = len(rows)
    base: List[Element] = []
    for index in range(1, count):
        start, end = points[index - 1], points[index]
        if distance(start, end) < 1e-9:
            raise ContourError(f"Punkt {index + 1}: gleicher Punkt wie davor")
        row = rows[index]
        if row.is_arc:
            if index + 1 < count and not rows[index + 1].is_arc:
                next_point = points[index + 1]
            else:
                next_point = None
            if base:
                previous = base[-1]
            else:
                previous = None
            base.append(_arc_element(start, end, row, previous, next_point, index + 1))
        else:
            base.append(Element("line", start[0], start[1], end[0], end[1]))

    starts = [element.start for element in base]
    ends = [element.end for element in base]
    inserted = {}
    for vertex in range(1, count - 1):          # Ecke am Punkt vertex zwischen Element vertex-1 und vertex
        row = rows[vertex]
        kind, size, angle, reference = row.corner, row.csize, row.cangle, row.cref
        if kind is None:
            kind, size, angle, reference = default_corner, default_size, None, "A"
        if kind not in ("R", "C") or size <= EPS:
            continue
        result = _corner(base[vertex - 1], base[vertex], kind, size, angle, reference, vertex + 1)
        if result is None:
            continue
        ends[vertex - 1], starts[vertex], inserted[vertex] = result

    elements: List[Element] = []
    for index, element in enumerate(base):
        if index in inserted:
            elements.extend(inserted[index])
        new_start, new_end = starts[index], ends[index]
        if element.param(new_start) > element.param(new_end) + 1e-7:
            raise ContourError(f"Zwischen Punkt {index + 1} und {index + 2} ist zu wenig Platz für "
                               f"die Radien/Fasen")
        if distance(new_start, new_end) > 1e-6:
            elements.append(element.with_ends(new_start, new_end))
    return elements


def clip_at_radius(elements: List[Element], limit_radius: float,
                   radius_sign: int = 1) -> List[Element]:
    """Kontur am Rohteilradius abschneiden.

    Läuft die Kontur (radius_sign=+1 außen: nach außen, -1 innen: zur Mitte) über limit_radius
    hinaus, endet sie am ersten Schnittpunkt. So darf z.B. ein Radius am Ende größer sein als der
    Absatz zum Rohteil – der Bogen wird dann am Rohteil-Ø abgeschnitten.
    """
    clipped: List[Element] = []
    for element in elements:
        hits = []
        if element.kind == "line":
            start_value = radius_sign * element.r0
            end_value = radius_sign * element.r1
            changes = abs(element.r1 - element.r0) > 1e-12
            if changes and max(start_value, end_value) >= radius_sign * limit_radius - 1e-9:
                position = (limit_radius - element.r0) / (element.r1 - element.r0)
                if -1e-9 <= position <= 1 + 1e-9:
                    hit_z = element.z0 + position * (element.z1 - element.z0)
                    hits.append((position, (hit_z, limit_radius)))
            elif not changes and abs(element.r0 - limit_radius) < 1e-9:
                hits.append((0.0, element.start))
        else:
            radial_offset = limit_radius - element.cr
            if abs(radial_offset) <= element.radius + 1e-12:
                axial_offset = math.sqrt(max(element.radius ** 2 - radial_offset ** 2, 0.0))
                for hit_z in (element.cz - axial_offset, element.cz + axial_offset):
                    point = (hit_z, limit_radius)
                    if lies_on(element, point, 1e-9):
                        hits.append((element.param(point), point))
        already_outside = radius_sign * element.r0 >= radius_sign * limit_radius - 1e-9
        if already_outside and clipped:
            return clipped
        if hits:
            position, point = min(hits)
            if position > 1e-9:
                clipped.append(element.with_ends(element.start, point))
            return clipped
        clipped.append(element)
    return clipped


def polyline(elements: List[Element], step_deg: float = 2.0) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for element in elements:
        samples = element.sample(step_deg)
        if points:
            samples = samples[1:]
        points.extend(samples)
    return points


def contour_warnings(elements: List[Element]) -> List[str]:
    """Hinweise zur (bereits ins Arbeitssystem transformierten) Kontur."""
    warnings = []
    points = polyline(elements)
    for index in range(len(points) - 1):
        if points[index + 1][0] > points[index][0] + 1e-6:
            warnings.append("Die Kontur läuft stellenweise gegen die Bearbeitungsrichtung zurück. "
                            "Dort ist Schruppen nicht möglich.")
            break
    for index in range(len(points) - 1):
        falling = points[index + 1][1] < points[index][1] - 1e-6
        advancing = points[index + 1][0] < points[index][0] - 1e-6
        if falling and advancing:
            warnings.append("Die Kontur enthält einen Hinterschnitt – es wird schräg eingefahren.")
            break
    return warnings


# =========================================================================== Schruppen
#
# Die folgenden Funktionen arbeiten im Arbeitssystem eines Zyklus: Bearbeitung in −Z, Material
# oberhalb der (Aufmaß-)Kontur. Innen- und Rechtsbearbeitung werden vorher dorthin gespiegelt.

@dataclass
class Crossing:
    segment: int
    position: float
    z: float
    r: float


def _crossing(points, index, level) -> Crossing:
    (z_start, r_start), (z_end, r_end) = points[index], points[index + 1]
    if abs(r_end - r_start) < 1e-12:
        position = 0.0
    else:
        position = (level - r_start) / (r_end - r_start)
    position = max(0.0, min(1.0, position))
    return Crossing(index, position, z_start + position * (z_end - z_start), level)


def intervals_at(points, level):
    """Abschnitte, in denen die Kontur <= level liegt: Liste (Anfang, Ende), None = offen."""
    intervals = []
    below = points[0][1] <= level + EPS
    start: Optional[Crossing] = None
    for index in range(len(points) - 1):
        this_below = points[index][1] <= level + EPS
        next_below = points[index + 1][1] <= level + EPS
        if this_below and not next_below:
            intervals.append((start, _crossing(points, index, level)))
            below = False
        elif next_below and not this_below:
            start = _crossing(points, index, level)
            below = True
    if below:
        intervals.append((start, None))
    return intervals


def walk_up(points, crossing: Crossing, limit: float):
    """Der Kontur ab crossing vorwärts folgen, solange r <= limit."""
    path = []
    for index in range(crossing.segment + 1, len(points)):
        z, r = points[index]
        if r > limit + EPS:
            limit_crossing = _crossing(points, index - 1, limit)
            path.append((limit_crossing.z, limit_crossing.r))
            return path
        path.append((z, r))
    return path


def walk_back(points, crossing: Crossing, limit: float, floor: float):
    """Der Kontur ab crossing rückwärts folgen, bis r > limit (schräger Einstieg)."""
    path = [(crossing.z, crossing.r)]
    for index in range(crossing.segment, -1, -1):
        z, r = points[index]
        if r > limit + EPS:
            limit_crossing = _crossing(points, index, limit)
            path.append((limit_crossing.z, limit_crossing.r))
            path.reverse()
            return path, True
        if r < floor - EPS:
            path.reverse()
            return path, False
        path.append((z, r))
    path.reverse()
    return path, True


def segment_between(points, first: Crossing, second: Crossing):
    path = [(first.z, first.r)]
    for index in range(first.segment + 1, second.segment + 1):
        path.append(points[index])
    path.append((second.z, second.r))
    return path
