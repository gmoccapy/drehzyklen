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
"""Thread cutting with the LinuxCNC cycle G76.

Tool path
---------
1. Rapid to the safe X (outside the thread), to the start point Zs, to the drive line
   (thread Ø + I).
2. ``G76 P Z I J R K Q H [E L]`` in radius mode G8, so that I, J and K are radius values as in
   the LinuxCNC documentation. LinuxCNC cuts all passes itself: first depth J, the following
   ones with degression R, compound infeed angle Q, H spring passes, tapers at the entry/exit
   (L) over the length E.
3. Back to G7 and out to the safe X.

For the preview the passes are recorded as "thread" segments (G76 itself has no path in the
emitter); depths follow the same degression formula as LinuxCNC: J·n^(1/R) up to K.

Threads can be taken from tables (``threads.py``): diameter, pitch, depth and infeed angle are
filled in, the pre-turning diameter is shown as a hint.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..gcode import fmt
from ..geometry import ContourError, Element
from ..i18n import N_, _
from ..machine import Machine
from ..threads import FREE, NORMS, SIZE_KEYS, ThreadSpec, find, names_for, norm_of_label
from .base import Cycle
from .params import INSIDE, SIDE, TECH_THREAD, P
from .toolcheck import tool_name


class Thread(Cycle):
    KEY = "thread"
    TITLE = N_("Thread (G76)")
    SHORT = N_("Thread")
    TECH_PARAMS = TECH_THREAD
    FORCE_G97 = True
    PARAMS = [
        P("diameter", N_("Nominal Ø"), 20.0, unit="mm", min=0.5, sym="D",
          label_in=N_("Core hole Ø")),
        P("pitch", N_("Pitch"), 2.5, unit="mm", min=0.05, sym="P"),
        P("z_begin", N_("Z start of thread"), 0.0, unit="mm", sym="Z1"),
        P("z_end", N_("Z end of thread"), -30.0, unit="mm", sym="Z2",
          help=N_("Z2 < Z1: thread towards -Z, Z2 > Z1: thread towards +Z")),
        P("z_start", N_("Start point (run-in)"), 5.0, unit="mm", sym="Zs",
          help=N_("Lies before the start of the thread, against the thread direction")),
        P("depth", N_("Thread depth (0 = auto)"), 0.0, unit="mm", min=0, sym="K",
          help=N_("0 = calculated from the basic profile")),
        P("first_cut", N_("First depth of cut"), 0.3, unit="mm", min=0.01, sym="J"),
        P("degression", N_("Degression (1-3)"), 1.5, min=1.0, max=3.0, sym="R"),
        P("infeed", N_("Infeed angle"), 29.5, unit="°", min=0, max=60, sym="Q"),
        P("spring", N_("Spring passes"), 1, "int", min=0, max=10, sym="H"),
        P("chamfer_l", N_("Taper at (0…3)"), 0, "int", min=0, max=3, sym="L",
          help=N_("0 none, 1 entry, 2 exit, 3 both")),
        P("chamfer_e", N_("Taper length entry/exit"), 0.0, unit="mm", min=0, sym="E"),
        P("thread_clear", N_("Drive line distance"), 1.0, unit="mm", min=0.1, sym="I",
          help=N_("Distance of the drive line from the thread (G76 I)")),
    ]

    # ---------------------------------------------------------------- thread tables
    @staticmethod
    def table_params() -> List[P]:
        """Norm/size selection - built from the loaded thread tables (including own threads)."""
        labels = {}
        for norm, values in NORMS.items():
            labels[norm] = values["label"]
        params = [P("norm", N_("Thread from table"), FREE, "choice", (FREE,) + tuple(SIZE_KEYS),
                    labels=labels,
                    help=N_("Choose norm and size: Ø, pitch, depth and infeed angle are filled "
                            "in (can be changed by hand afterwards)"))]
        for norm, key in SIZE_KEYS.items():
            names = names_for(norm)
            params.append(P(key, N_("Size"), names[0], "choice", names, when=("norm", (norm,))))
        return params

    @classmethod
    def all_params(cls) -> List[P]:
        return [SIDE] + list(cls.TECH_PARAMS) + cls.table_params() + list(cls.PARAMS)

    @staticmethod
    def spec_from(values: Dict[str, Any]) -> Optional[ThreadSpec]:
        """Thread from the table (None for "free")."""
        norm = norm_of_label(str(values.get("norm", FREE)))
        if norm == FREE or norm not in SIZE_KEYS:
            return None
        return find(norm, str(values.get(SIZE_KEYS[norm], "")))

    def spec(self) -> Optional[ThreadSpec]:
        return self.spec_from(self.v)

    @classmethod
    def switch(cls, key, values, old_value=None):
        updates = super().switch(key, values, old_value)
        if key not in ("norm", "side") and key not in SIZE_KEYS.values():
            return updates
        spec = cls.spec_from(values)
        if spec is None:
            return updates
        inside = values.get("side") == INSIDE
        updates["diameter"] = round(spec.cycle_diameter(inside), 3)
        updates["pitch"] = round(spec.pitch, 6)
        updates["depth"] = 0.0
        updates["infeed"] = spec.infeed()
        return updates

    def pre_turn_hint(self) -> str:
        """Hint for the pre-turning Ø (outside D - 0.1·P, inside D1 + 0.1·P)."""
        spec = self.spec()
        if self.inside:
            pre_turn = self.diameter + 0.1 * self.pitch
            text = _("Pre-turn the bore to Ø{pre:.2f} (core Ø D1 {core:.2f} + 0.1·P)").format(
                pre=pre_turn, core=self.diameter)
        else:
            pre_turn = self.diameter - 0.1 * self.pitch
            text = _("Pre-turn to Ø{pre:.2f} (nominal Ø - 0.1·P)").format(pre=pre_turn)
        if spec is not None:
            text = f"{spec.name} {spec.pitch_text()}: " + text
        return text

    # ---------------------------------------------------------------- geometry
    def thread_direction(self) -> int:
        """-1: the thread runs towards -Z, +1: towards +Z."""
        if self.z_end > self.z_begin:
            return 1
        return -1

    def eff_depth(self):
        if self.depth > 0:
            return self.depth
        spec = self.spec()
        if spec is not None:
            return spec.depth(self.inside)
        if self.inside:
            return 0.5413 * self.pitch
        return 0.6134 * self.pitch

    def summary(self):
        spec = self.spec()
        if spec is not None:
            text = f"{self.side_text} {spec.name}"
        elif self.inside:
            text = _("inside core hole Ø{diameter}×P{pitch}").format(diameter=fmt(self.diameter),
                                                                     pitch=fmt(self.pitch))
        else:
            text = _("outside M{diameter}×{pitch}").format(diameter=fmt(self.diameter),
                                                           pitch=fmt(self.pitch))
        return text + f" Z{fmt(self.z_begin)}…{fmt(self.z_end)}"

    def feeds(self):
        return []

    def z_range(self):
        values = (self.z_begin, self.z_end, self.z_start)
        return min(values), max(values)

    def max_d(self):
        if self.inside:
            return self.diameter + 2 * self.eff_depth()
        return self.diameter + 2 * self.thread_clear

    def outer_x(self, machine):
        return self.diameter + 2 * (self.thread_clear + machine.clearance)

    def inner_x(self, machine):
        return max(0.0, self.diameter - 2 * (self.thread_clear + machine.clearance))

    def passes(self):
        """Cutting depths as LinuxCNC calculates them: J·n^(1/R), the last one = K."""
        depth, first, degression = self.eff_depth(), self.first_cut, self.degression
        depths = []
        number = 1
        while True:
            cut = first * number ** (1.0 / degression)
            if cut >= depth - 1e-6 or number > 200:
                depths.append(depth)
                break
            depths.append(cut)
            number += 1
        return depths

    def rpm_limit(self, machine: Machine) -> float:
        return min(machine.max_rpm, machine.thread_speed_factor * machine.max_vel_z / self.pitch)

    def taper_lengths(self):
        """(entry taper, exit taper) lengths in mm according to L and E."""
        taper_length = 0.0
        if self.chamfer_e > 0 and self.chamfer_l > 0:
            taper_length = min(self.chamfer_e, abs(self.z_end - self.z_start))
        entry = 0.0
        exit_length = 0.0
        if taper_length > 0 and self.chamfer_l in (1, 3):
            entry = taper_length
        if taper_length > 0 and self.chamfer_l in (2, 3):
            exit_length = taper_length
        return entry, exit_length

    def check(self, machine):
        warnings = []
        if abs(self.z_end - self.z_begin) < 1e-6:
            raise ContourError(_("Start and end of the thread are equal"))
        run_in = (self.z_start - self.z_begin) * -self.thread_direction()
        if run_in < 0:
            raise ContourError(_("The start point Zs must lie before the start of the thread "
                                 "(against the thread direction)"))
        if self.first_cut > self.eff_depth():
            raise ContourError(_("The first depth of cut is larger than the thread depth"))
        if self.inside and self.diameter / 2 - self.thread_clear <= 0:
            raise ContourError(_("The distance of the drive line is larger than the core hole "
                                 "radius"))
        rpm = min(self.speed, self.rpm_limit(machine))
        velocity = rpm * self.pitch / 60.0
        acceleration_way = velocity * velocity / (2 * max(machine.max_acc_z, 1e-3))
        if run_in < 1.5 * acceleration_way:
            warnings.append(_("Run-in {run_in:.2f} mm is short (acceleration distance ≈ "
                              "{needed:.2f} mm) - move the start point further away or reduce "
                              "the speed").format(run_in=run_in, needed=acceleration_way))
        warnings.append(self.pre_turn_hint())
        warnings.append(_("{passes} thread passes + {spring} spring pass(es); spindle encoder "
                          "required").format(passes=len(self.passes()), spring=self.spring))
        return warnings

    def outline(self):
        radius = self.diameter / 2
        root = radius - self.radius_sign * self.eff_depth()
        _entry, exit_length = self.taper_lengths()
        if exit_length > 0:
            exit_length = min(exit_length, abs(self.z_end - self.z_begin))
            taper_z = self.z_end - self.thread_direction() * exit_length
            return [Element("line", self.z_begin, root, taper_z, root),
                    Element("line", taper_z, root, self.z_end, radius)]
        return [Element("line", self.z_begin, root, self.z_end, root)]

    def tool_problems(self, tool):
        name = tool_name(tool)
        if tool.kind != "thread":
            return [_("{tool}: a {kind} cannot cut a thread - choose a threading "
                      "tool").format(tool=name, kind=tool.kind_text())]
        if self.inside and tool.position != INSIDE:
            return [_("{tool}: an inside thread needs an inside threading tool").format(
                tool=name)]
        if not self.inside and tool.position == INSIDE:
            return [_("{tool}: an outside thread needs an outside threading tool").format(
                tool=name)]
        spec = self.spec()
        if spec is not None and abs(tool.insert_angle - spec.angle) > 2.0:
            return [_("{tool}: tip angle {angle:g}° does not fit {thread} ({needed:g}°)").format(
                tool=name, angle=tool.insert_angle, thread=spec.name, needed=spec.angle)]
        return []

    # ---------------------------------------------------------------- path
    def body(self, emitter, machine, material):
        radius = self.diameter / 2
        clear = self.thread_clear
        sign_r = self.radius_sign
        drive_radius = radius + sign_r * clear
        depth = self.eff_depth()
        safe_x = 2 * (radius + sign_r * (clear + machine.clearance))
        if self.inside:
            safe_x = max(safe_x, 0.0)
        emitter.rapid(x=safe_x)
        emitter.rapid(z=self.z_start)
        emitter.rapid(x=2 * drive_radius)
        emitter.comment(_("G76 in radius mode G8 (I, J, K as radius values), then back to G7"))
        emitter.set_radius_mode(True)
        g76 = (f"G76 P{fmt(self.pitch, 6)} Z{fmt(self.z_end)} I{fmt(-sign_r * clear)} "
               f"J{fmt(self.first_cut)} R{fmt(self.degression)} K{fmt(depth)} "
               f"Q{fmt(self.infeed)} H{int(self.spring)}")
        if self.chamfer_e > 0 and self.chamfer_l > 0:
            g76 += f" E{fmt(self.chamfer_e)} L{int(self.chamfer_l)}"
        emitter.lines.append(g76)
        emitter.set_radius_mode(False)
        self.record_passes(emitter, radius, drive_radius)
        emitter.set_pos(2 * drive_radius, self.z_start)
        emitter.rapid(x=safe_x)

    def record_passes(self, emitter, radius, drive_radius):
        """Path for the preview: tapers at the entry/exit run over the length E from the thread
        crest (nominal Ø) to the cutting depth and back."""
        sign_r = self.radius_sign
        direction = self.thread_direction()
        entry_length, exit_length = self.taper_lengths()
        for cut in self.passes():
            pass_radius = radius - sign_r * cut
            if entry_length > 0:
                emitter.record_thread(2 * drive_radius, self.z_start, 2 * radius, self.z_start)
                cut_start_z = self.z_start + direction * entry_length
                emitter.record_thread(2 * radius, self.z_start, 2 * pass_radius, cut_start_z)
            else:
                cut_start_z = self.z_start
                emitter.record_thread(2 * drive_radius, self.z_start, 2 * pass_radius,
                                      self.z_start)
            if exit_length > 0:
                cut_end_z = self.z_end - direction * exit_length
                emitter.record_thread(2 * pass_radius, cut_start_z, 2 * pass_radius, cut_end_z)
                emitter.record_thread(2 * pass_radius, cut_end_z, 2 * radius, self.z_end)
                emitter.record_thread(2 * radius, self.z_end, 2 * drive_radius, self.z_end)
            else:
                emitter.record_thread(2 * pass_radius, cut_start_z, 2 * pass_radius, self.z_end)
                emitter.record_thread(2 * pass_radius, self.z_end, 2 * drive_radius, self.z_end)
