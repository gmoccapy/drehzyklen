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
"""G-code output that records the tool path at the same time (for the preview and checks)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .geometry import format_number


def fmt(value: float, digits: int = 4) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    if text in ("-0", "", "-"):
        return "0"
    return text


@dataclass
class Segment:
    kind: str           # "rapid" | "feed" | "arc" | "thread"
    x0: float           # diameter
    z0: float
    x1: float
    z1: float
    radius: float = 0.0
    ccw: bool = False
    op: int = 0         # number of the operation
    cz: float = 0.0     # arc centre Z
    cr: float = 0.0     # arc centre as radius


class Emitter:
    """Writes G-code lines and keeps track of position, feed and path."""

    def __init__(self):
        self.lines: List[str] = []
        self.path: List[Segment] = []
        self.x: Optional[float] = None      # diameter
        self.z: Optional[float] = None
        self.feed_value: Optional[float] = None
        self.radius_mode = False            # G8 active?
        self.feed_mode = "G95"
        self.op = 0

    # ---------------------------------------------------------------- basics
    def emit(self, text: str):
        self.lines.append(text)

    def comment(self, text: str):
        text = text.replace("(", "[").replace(")", "]")
        self.lines.append(f"({text})")

    def blank(self):
        self.lines.append("")

    def set_radius_mode(self, active: bool):
        if active != self.radius_mode:
            if active:
                self.lines.append("G8")
            else:
                self.lines.append("G7")
            self.radius_mode = active

    def set_feed_mode(self, mode: str):
        if mode != self.feed_mode:
            self.lines.append(mode)
            self.feed_mode = mode
            self.feed_value = None

    def _x_word(self, x: float) -> str:
        if self.radius_mode:
            return format_number(x / 2.0)
        return format_number(x)

    def _move(self, code: str, kind: str, x, z, extra: str = ""):
        if x is None:
            new_x = self.x
        else:
            new_x = x
        if z is None:
            new_z = self.z
        else:
            new_z = z
        words = [code]
        if x is not None and (self.x is None or abs(x - self.x) > 1e-6):
            words.append("X" + self._x_word(x))
        if z is not None and (self.z is None or abs(z - self.z) > 1e-6):
            words.append("Z" + fmt(z))
        if len(words) == 1:
            return
        if extra:
            words.append(extra)
        known = (self.x is not None and self.z is not None and new_x is not None
                 and new_z is not None)
        if known:
            self.path.append(Segment(kind, self.x, self.z, new_x, new_z, op=self.op))
        self.x = new_x
        self.z = new_z
        self.lines.append(" ".join(words))

    def _feed_word(self, feed: Optional[float]) -> str:
        if feed is not None and (self.feed_value is None or abs(feed - self.feed_value) > 1e-9):
            self.feed_value = feed
            return "F" + fmt(feed)
        return ""

    # ---------------------------------------------------------------- moves
    def rapid(self, x: Optional[float] = None, z: Optional[float] = None):
        self._move("G0", "rapid", x, z)

    def feed(self, x: Optional[float] = None, z: Optional[float] = None,
             f: Optional[float] = None):
        if x is None:
            new_x = self.x
        else:
            new_x = x
        if z is None:
            new_z = self.z
        else:
            new_z = z
        if (self.x is not None and self.z is not None and abs(new_x - self.x) < 1e-6
                and abs(new_z - self.z) < 1e-6):
            return
        self._move("G1", "feed", x, z, self._feed_word(f))

    def arc(self, x: float, z: float, center_z: float, center_r: float, counter_clockwise: bool,
            f: Optional[float] = None):
        """Arc in centre format. x = diameter, centre given with center_r as radius.

        I/K are written relative to the start point (G91.1) and as radius values - the arc is
        therefore output in radius mode G8 (the contour turner takes care of that).
        """
        feed_word = self._feed_word(f)
        start_radius = self.x / 2.0
        offset_i = center_r - start_radius
        offset_k = center_z - self.z
        radius = (offset_i * offset_i + offset_k * offset_k) ** 0.5
        if counter_clockwise:
            code = "G3"
        else:
            code = "G2"
        words = [code, "X" + self._x_word(x), "Z" + fmt(z), "I" + fmt(offset_i),
                 "K" + fmt(offset_k)]
        if feed_word:
            words.append(feed_word)
        self.lines.append(" ".join(words))
        self.path.append(Segment("arc", self.x, self.z, x, z, radius, counter_clockwise, self.op,
                                 center_z, center_r))
        self.x = x
        self.z = z

    def follow(self, points, f: Optional[float] = None):
        """G1 along a list of points (z, r)."""
        for z, r in points:
            self.feed(x=2 * r, z=z, f=f)

    def dwell(self, seconds: float):
        if seconds > 0:
            self.lines.append(f"G4 P{fmt(seconds, 2)}")

    def record_thread(self, x0, z0, x1, z1):
        self.path.append(Segment("thread", x0, z0, x1, z1, op=self.op))

    def set_pos(self, x: Optional[float], z: Optional[float]):
        self.x = x
        self.z = z

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


class TransformedEmitter:
    """Takes moves in the work frame of a cycle and writes them mirrored back.

    Work frame: machining towards -Z, material above the contour.
      radius_sign = -1: inside machining (mirrored at the spindle axis)
      z_sign      = -1: machining from left to right (mirrored at Z = 0)
    """

    def __init__(self, emitter: Emitter, radius_sign: int = 1, z_sign: int = 1):
        self.emitter = emitter
        self.radius_sign = radius_sign
        self.z_sign = z_sign

    def _real_x(self, x):
        if x is None:
            return None
        return self.radius_sign * x

    def _real_z(self, z):
        if z is None:
            return None
        return self.z_sign * z

    def comment(self, text):
        self.emitter.comment(text)

    def emit(self, text):
        self.emitter.emit(text)

    def set_radius_mode(self, active):
        self.emitter.set_radius_mode(active)

    def rapid(self, x=None, z=None):
        self.emitter.rapid(self._real_x(x), self._real_z(z))

    def feed(self, x=None, z=None, f=None):
        self.emitter.feed(self._real_x(x), self._real_z(z), f)

    def follow(self, points, f=None):
        for z, r in points:
            self.feed(x=2 * r, z=z, f=f)

    def arc(self, x, z, center_z, center_r, counter_clockwise, f=None):
        real_direction = counter_clockwise
        if self.radius_sign * self.z_sign < 0:
            real_direction = not counter_clockwise
        self.emitter.arc(self._real_x(x), self._real_z(z), self._real_z(center_z),
                         self.radius_sign * center_r, real_direction, f)
