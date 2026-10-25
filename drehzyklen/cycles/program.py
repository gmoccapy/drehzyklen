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
"""A program: stock + list of cycles -> G-code, tool path and messages.

``Program.generate()`` runs in these steps:

1. check every cycle (values, maximum diameter, tool) -> warnings and errors
2. program header (G18 G7 G21 ..., machine and stock as comments)
3. retract plane: highest Z of all cycles + retract distance, X outside/inside
4. for every cycle
     - G-code block: insert the text, then restore spindle/coolant (``SpindleState``)
     - machining cycle: tool change (only if the tool number changes), speed/feed mode,
       spindle and coolant, the moves of the cycle, back to the retract plane
     - the material simulation removes what the cycle cut; rapid moves through the remaining
       material are errors
5. program end (M9 M5, tool change position, M2) and the check against the chuck
"""
from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..gcode import Emitter, Segment, fmt
from ..geometry import ContourError, Element
from ..i18n import N_, _
from ..machine import Machine
from ..material import Material, bounds
from .base import Cycle
from .contour import Contour
from .drill import Drill
from .edgebreak import EdgeBreak
from .facing import Facing
from .gcodeblock import GCodeOp
from .longturning import LongTurning
from .stock import Stock
from .taper import Taper
from .thread import Thread

CYCLES = [Facing, LongTurning, Taper, Thread, EdgeBreak, Contour, Drill, GCodeOp]
CYCLE_BY_KEY = {cycle.KEY: cycle for cycle in CYCLES}
CHECKED_ERRORS = (ContourError, ValueError, TypeError, ZeroDivisionError)
NO_TOOL_CHANGE_WARNING = N_("No tool change position defined (INI [CHANGE_POSITION] or machine "
                            "tab) - the change happens at the retract position")


def cycle_from_dict(data) -> Cycle:
    return CYCLE_BY_KEY[data["cycle"]](data.get("values", {}))


@dataclass
class Result:
    gcode: str
    path: List[Segment]
    outlines: List[Tuple[int, List[Element], bool]]
    warnings: List[str]
    errors: List[str]


@dataclass
class SpindleState:
    """What is switched on while the program runs."""
    spindle: Optional[str] = None      # last spindle word, e.g. "G96 D3000 S180 M3"
    coolant: bool = False
    tool: Optional[int] = None         # tool in the spindle

    def switch_off(self, emitter: Emitter):
        if self.coolant:
            emitter.emit("M9")
            self.coolant = False
        if self.spindle:
            emitter.emit("M5")
            self.spindle = None


class Program:
    def __init__(self, machine: Machine, ops: Optional[List[Cycle]] = None, name="Part",
                 stock: Optional[Stock] = None, tools=None):
        self.machine = machine
        if ops is None:
            ops = []
        self.ops: List[Cycle] = ops
        self.name = name
        if stock is None:
            stock = Stock()
        self.stock = stock
        self.tools = tools

    # ---------------------------------------------------------------- project file
    def to_json(self) -> str:
        return json.dumps({"name": self.name, "stock": self.stock.to_dict(),
                           "ops": [op.to_dict() for op in self.ops]}, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str, machine: Machine, tools=None) -> "Program":
        data = json.loads(text)
        ops = [cycle_from_dict(entry) for entry in data.get("ops", [])]
        return cls(machine, ops, data.get("name", ""), Stock(data.get("stock")), tools)

    # ---------------------------------------------------------------- technology
    def limited_op(self, op: Cycle, tag: str, warnings: List[str]) -> Cycle:
        """With G94 limit the feed rates to the machine limit."""
        if op.feed_mode() != "G94":
            return op
        limit = self.machine.max_feed_vel
        updates = {}
        for key in op.feed_keys():
            if op.v[key] > limit:
                updates[key] = limit
                warnings.append(tag + _("feed {feed:g} mm/min limited to {limit:.0f} mm/min "
                                        "(machine limit)").format(feed=op.v[key], limit=limit))
        if updates:
            return op.copy_with(updates)
        return op

    def spindle_word(self, op: Cycle, tag: str, warnings: List[str]) -> str:
        machine = self.machine
        limit = machine.max_rpm
        feeds = [feed for feed in op.feeds() if feed and feed > 0]
        if feeds and op.feed_mode() == "G95":
            rpm_by_velocity = machine.max_feed_vel / max(feeds)
            if rpm_by_velocity < limit:
                limit = rpm_by_velocity
                warnings.append(tag + _("speed limited to {limit:.0f} 1/min, otherwise the feed "
                                        "{feed:g} mm/rev would exceed the axis velocity").format(
                    limit=limit, feed=max(feeds)))
        if op.KEY == "thread":
            limit = min(limit, op.rpm_limit(machine))
        direction = op.spindle_direction(machine)
        rpm = None
        if op.KEY == "drill":
            if op.spindle_mode() == "G96":
                rpm = op.speed * 1000 / (math.pi * op.diameter)
            else:
                rpm = op.speed
        elif op.spindle_mode() == "G97":
            rpm = op.speed
        if rpm is not None:
            if rpm > limit + 0.5:
                warnings.append(tag + _("speed {rpm:.0f} → {limit:.0f} 1/min limited").format(
                    rpm=rpm, limit=limit))
                rpm = limit
            if rpm < machine.min_rpm:
                warnings.append(tag + _("speed {rpm:.0f} below the minimum, raised to "
                                        "{minimum:.0f}").format(rpm=rpm, minimum=machine.min_rpm))
                rpm = machine.min_rpm
            return f"G97 S{rpm:.0f} {direction}"
        cutting_speed = op.speed
        diameter = max(op.max_d(), 1e-3)
        rpm_at_diameter = cutting_speed * 1000 / (math.pi * diameter)
        if rpm_at_diameter < machine.min_rpm:
            warnings.append(tag + _("at Ø{diameter:.1f} Vc {speed:g} would give only {rpm:.0f} "
                                    "1/min (minimum {minimum:.0f})").format(
                diameter=diameter, speed=cutting_speed, rpm=rpm_at_diameter,
                minimum=machine.min_rpm))
        return f"G96 D{limit:.0f} S{fmt(cutting_speed)} {direction}"

    # ---------------------------------------------------------------- tool change
    def _goto_tool_change(self, emitter: Emitter, x_out: float, z_safe: float):
        machine = self.machine
        if machine.tc_enabled:
            source = machine.tc_source or _("machine")
            emitter.comment(_("Tool change position G53 ({source})").format(source=source))
            emitter.set_radius_mode(True)
            emitter.emit(f"G53 G0 X{fmt(machine.tc_x)}")
            if machine.tc_move_z:
                emitter.emit(f"G53 G0 Z{fmt(machine.tc_z)}")
                emitter.set_radius_mode(False)
                emitter.set_pos(None, None)
            else:
                # only X, Z stays where it is
                emitter.set_radius_mode(False)
                emitter.set_pos(None, emitter.z)
        else:
            emitter.rapid(x=x_out)
            emitter.rapid(z=z_safe)

    # ---------------------------------------------------------------- checks
    def _tool_check(self, op: Cycle, tag: str, warnings: List[str], errors: List[str]):
        if self.tools is None or not op.HAS_TOOL:
            return
        tool = self.tools.get(op.tool)
        if tool is None:
            errors.append(tag + _("tool T{number} is not in the tool table").format(
                number=op.tool))
            return
        if tool.needs_input:
            errors.append(tag + _("T{number}: enter type, hand and position in the tools "
                                  "tab").format(number=tool.number))
            return
        for problem in op.tool_problems(tool):
            if op.v.get("tool_ok"):
                warnings.append(tag + _("released: ") + problem)
            else:
                errors.append(tag + problem + _(" (release: »Release tool despite warning«)"))

    def _chuck_check(self, path, ops_by_number, warnings, errors):
        stock = self.stock
        chuck_z = stock.z_chuck
        clearance = stock.chuck_clear
        lowest = {}
        for segment in path:
            if segment.op in ops_by_number:
                lowest[segment.op] = min(lowest.get(segment.op, 1e9), segment.z0, segment.z1)
        for number in sorted(lowest):
            z = lowest[number]
            op = ops_by_number[number]
            tag = self.tag(number, op)
            if z < chuck_z - 1e-6:
                if op.inside:
                    warnings.append(tag + _("moves to Z{z:.2f} - deeper than the chuck face "
                                            "Z{chuck:g} (check the bore/tool near the "
                                            "chuck)").format(z=z, chuck=chuck_z))
                else:
                    errors.append(tag + _("collision: moves to Z{z:.2f}, the chuck face is at "
                                          "Z{chuck:g}").format(z=z, chuck=chuck_z))
            elif z < chuck_z + clearance - 1e-6 and not op.inside:
                warnings.append(tag + _("moves to Z{z:.2f}, only {gap:.2f} mm in front of the "
                                        "chuck (safety distance {clearance:g})").format(
                    z=z, gap=z - chuck_z, clearance=clearance))

    @staticmethod
    def _rapid_check(path, start_index, material: Material, tag, errors):
        """Rapid moves must not pass through the material that remains after the cycle."""
        for segment in path[start_index:]:
            if segment.kind != "rapid":
                continue
            length = math.hypot(segment.z1 - segment.z0, (segment.x1 - segment.x0) / 2)
            steps = max(1, int(length / 0.5))
            for step in range(steps + 1):
                fraction = step / steps
                z = segment.z0 + fraction * (segment.z1 - segment.z0)
                r = (segment.x0 + fraction * (segment.x1 - segment.x0)) / 2
                if material.contains(z, r, 0.1):
                    errors.append(tag + _("rapid move through material at Z{z:.2f} X{x:.2f} - "
                                          "check the order of operations, Z1/Z2 or Ø").format(
                        z=z, x=2 * r))
                    return

    @staticmethod
    def tag(number: int, op: Cycle) -> str:
        return _("Op {number} ({title}): ").format(number=number, title=op.title())

    def _check_ops(self, numbered_ops, warnings, errors):
        """Step 1: check all cycles; returns the valid ones and their finished outlines."""
        machine = self.machine
        stock = self.stock
        valid = []
        outlines = []
        try:
            stock.check()
        except ContourError as error:
            errors.append(str(error))
        for number, op in numbered_ops:
            tag = self.tag(number, op)
            try:
                for warning in op.check(machine):
                    warnings.append(tag + warning)
                outline = op.outline()
                if op.max_d() > machine.max_diameter + 1e-6:
                    errors.append(tag + _("Ø{diameter:.1f} larger than the maximum turning "
                                          "diameter {limit:.0f}").format(
                        diameter=op.max_d(), limit=machine.max_diameter))
                    continue
                if op.KEY == "facing" and op.z_sign() > 0 and not op.inside:
                    if stock.z_front > op.z_start + 1e-6:
                        warnings.append(tag + _("the stock front Z{front:g} lies in front of "
                                                "Z1 {z1:g} - the first cut would be too "
                                                "deep").format(front=stock.z_front,
                                                               z1=op.z_start))
                self._tool_check(op, tag, warnings, errors)
                valid.append((number, op))
                if outline:
                    outlines.append((number, outline, op.inside))
            except CHECKED_ERRORS as error:
                errors.append(tag + str(error))
        return valid, outlines

    def _header(self, emitter: Emitter):
        emitter.emit("%")
        emitter.comment(_("{name} - generated with Drehzyklen {date}").format(
            name=self.name, date=datetime.date.today().isoformat()))
        emitter.comment(_("Machine: {summary}").format(summary=self.machine.summary()))
        emitter.comment(self.stock.summary())
        emitter.emit("G18 G7 G21 G90 G91.1 G40 G49 G80 G54")
        emitter.emit("G95 G64 P0.01")

    def _retract_plane(self, machining):
        """Step 3: Z in front of everything, X outside of everything, X inside all bores."""
        machine = self.machine
        z_safe = max([op.z_range()[1] for _number, op in machining] + [self.stock.z_front])
        z_safe += machine.retract
        outer_candidates = [op.outer_x(machine) for _number, op in machining if not op.inside]
        outer_candidates.append(self.stock.D + 2 * machine.clearance)
        x_out = max(outer_candidates)
        inner_candidates = [op.inner_x(machine) for _number, op in machining if op.inside]
        if inner_candidates:
            x_in = min(inner_candidates)
        else:
            x_in = 0.0
        return z_safe, x_out, x_in

    def _gcode_block(self, emitter, op: GCodeOp, position, valid, state: SpindleState,
                     material):
        """A G-code block and what happens with spindle and coolant afterwards."""
        op.body(emitter, self.machine, material)
        if not op.restart:
            # without switching on again: what the block switches off is switched on by the
            # next cycle (otherwise it would cut with a stopped spindle)
            m_codes = op.m_codes()
            if m_codes & {5, 2, 30}:
                state.spindle = None
            if m_codes & {9, 2, 30}:
                state.coolant = False
            return
        next_tool = None
        for _next_number, next_op in valid[position + 1:]:
            if next_op.HAS_TOOL:
                next_tool = next_op.tool
                break
        if next_tool != state.tool:
            # a tool change follows: the next cycle switches spindle and coolant on
            if next_tool is not None and (state.spindle or state.coolant):
                emitter.comment(_("Spindle/coolant are switched on by the next cycle after the "
                                  "tool change"))
            state.spindle = None
            state.coolant = False
            return
        # same tool: restore the state from before the block right away
        if state.spindle or state.coolant:
            emitter.comment(_("Switch spindle/coolant on again"))
        if state.spindle:
            emitter.emit(state.spindle)
        if state.coolant:
            emitter.emit("M8")

    def _machining(self, emitter, number, op, state: SpindleState, material, x_out, x_in,
                   z_safe, warnings, errors):
        """One machining cycle: tool, technology, moves, retract, material and rapid check."""
        tag = self.tag(number, op)
        machine = self.machine
        op_limited = self.limited_op(op, tag, warnings)
        emitter.blank()
        emitter.comment(f"--- {number}: {op.title()}: {op.summary()} ---")
        if op.tool != state.tool:
            state.switch_off(emitter)
            self._goto_tool_change(emitter, x_out, z_safe)
            emitter.emit(f"T{int(op.tool)} M6 G43")
            emitter.rapid(z=z_safe)
        else:
            emitter.comment(_("same tool T{number} - no tool change").format(
                number=int(op.tool)))
        emitter.set_feed_mode(op.feed_mode())
        spindle_text = self.spindle_word(op_limited, tag, warnings)
        if spindle_text != state.spindle:
            emitter.emit(spindle_text)
            state.spindle = spindle_text
        if op.coolant and not state.coolant:
            emitter.emit("M8")
            state.coolant = True
        elif not op.coolant and state.coolant:
            emitter.emit("M9")
            state.coolant = False
        path_start = len(emitter.path)
        op_limited.body(emitter, machine, material)
        if op.inside:
            emitter.rapid(x=x_in)
        else:
            current_x = emitter.x
            if current_x is None:
                current_x = 0.0
            emitter.rapid(x=max(x_out, current_x))
        emitter.rapid(z=z_safe)
        material.apply(op)
        self._rapid_check(emitter.path, path_start, material, tag, errors)
        state.tool = op.tool

    # ---------------------------------------------------------------- generate
    def generate(self, only: Optional[int] = None,
                 start_material: Optional[Material] = None) -> Result:
        machine = self.machine
        emitter = Emitter()
        warnings: List[str] = []
        errors: List[str] = []
        numbered_ops = list(enumerate(self.ops, 1))
        if only is not None:
            numbered_ops = [numbered_ops[only]]

        valid, outlines = self._check_ops(numbered_ops, warnings, errors)
        self._header(emitter)
        machining = [(number, op) for number, op in valid if op.HAS_TOOL]
        if not machining:
            for _number, op in valid:
                op.body(emitter, machine, None)
            emitter.emit("M2")
            emitter.emit("%")
            return Result(emitter.text(), emitter.path, outlines, warnings, errors)

        if start_material is not None:
            material = start_material.copy()
        else:
            z_low, z_high = bounds(self.stock, [op for _number, op in valid])
            material = Material(self.stock, z_low, z_high)
        z_safe, x_out, x_in = self._retract_plane(machining)
        emitter.comment(_("Retract plane Z{z}, outside X{x_out}, inside X{x_in}").format(
            z=fmt(z_safe), x_out=fmt(x_out), x_in=fmt(x_in)))
        if not machine.tc_enabled:
            warnings.append(_(NO_TOOL_CHANGE_WARNING))

        state = SpindleState()
        for position, (number, op) in enumerate(valid):
            emitter.op = number
            if not op.HAS_TOOL:
                emitter.blank()
                emitter.comment(f"--- {number}: {op.title()} ---")
                self._gcode_block(emitter, op, position, valid, state, material)
                continue
            mark = (len(emitter.lines), len(emitter.path), len(warnings), emitter.x, emitter.z,
                    emitter.feed_value, emitter.radius_mode, emitter.feed_mode, state.spindle,
                    state.coolant, state.tool)
            try:
                self._machining(emitter, number, op, state, material, x_out, x_in, z_safe,
                                warnings, errors)
            except CHECKED_ERRORS as error:
                # take back everything this cycle wrote and report the error
                del emitter.lines[mark[0]:]
                del emitter.path[mark[1]:]
                del warnings[mark[2]:]
                (emitter.x, emitter.z, emitter.feed_value, emitter.radius_mode, emitter.feed_mode,
                 state.spindle, state.coolant, state.tool) = mark[3:]
                errors.append(self.tag(number, op) + str(error))
        emitter.blank()
        if state.coolant:
            emitter.emit("M9")
        emitter.emit("M5")
        self._goto_tool_change(emitter, x_out, z_safe)
        emitter.emit("M2")
        emitter.emit("%")
        self._chuck_check(emitter.path, dict(valid), warnings, errors)
        return Result(emitter.text(), emitter.path, outlines, warnings, errors)
