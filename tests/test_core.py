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
"""Tests für den Zyklenkern (ohne GTK):  python3 tests/test_core.py   (oder pytest)"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from drehzyklen.cycles import (SPEED_FEED_MODES, Contour, Drill, EdgeBreak, Facing,  # noqa: E402
                               GCodeOp, LongTurning, Program, Stock, Taper, Thread)
from drehzyklen.geometry import (ContourError, parse_rows, polyline, resolve_rows,  # noqa: E402
                                 rows_to_text)
from drehzyklen.machine import Machine  # noqa: E402
from drehzyklen.material import simulate  # noqa: E402
from drehzyklen.settings import Settings  # noqa: E402
from drehzyklen.tools import ToolTable, parse_tool_line, preset_tools, tool_from_table  # noqa: E402


def elements_of(text, default_corner=None, default_size=0):
    return resolve_rows(parse_rows(text), default_corner, default_size)


def lies_on_circle(element):
    start_ok = math.isclose(math.hypot(element.z0 - element.cz, element.r0 - element.cr),
                            element.radius, abs_tol=1e-7)
    end_ok = math.isclose(math.hypot(element.z1 - element.cz, element.r1 - element.cr),
                          element.radius, abs_tol=1e-7)
    return start_ok and end_ok


def tangent(first, second):
    tangent_in = first.tangent_end()
    tangent_out = second.tangent_start()
    cross = tangent_in[0] * tangent_out[1] - tangent_in[1] * tangent_out[0]
    dot = tangent_in[0] * tangent_out[0] + tangent_in[1] * tangent_out[1]
    return abs(cross) < 1e-6 and dot > 0


def tool_table():
    folder = tempfile.mkdtemp()
    return ToolTable(Machine(), os.path.join(folder, "werkzeuge.json"))


# ------------------------------------------------------------------ Kontur

def test_parser_increment_comma_roundtrip():
    rows = parse_rows("X0 Z0\nU10 # Plan\nW-20,5 R2\n(Kommentar) X20 Z-40; X30\nG3 X40 Z-45 R5 RND1")
    assert [(row.x, row.z) for row in rows] == [(0, 0), (10, 0), (10, -20.5), (20, -40), (30, -40),
                                                (40, -45)]
    assert rows[2].corner == "R" and rows[2].csize == 2
    assert rows[5].is_arc and rows[5].ar == 5 and rows[5].corner == "R"
    assert rows_to_text(parse_rows(rows_to_text(rows))) == rows_to_text(rows)


def test_individual_corners_user_example():
    elements = elements_of("X0 Z0\nX50 C2 A30\nZ-20\nX60 R5\nZ-50")
    chamfer = elements[1]
    assert math.isclose(2 * chamfer.r0, 46.0, abs_tol=1e-6)
    assert math.isclose(chamfer.z1, -2 * math.tan(math.radians(30)), abs_tol=1e-6)
    other = elements_of("X0 Z0\nX50 C2 B30\nZ-20\nX60 R5\nZ-50")
    assert math.isclose(other[1].z1, -2.0, abs_tol=1e-6)
    arc = [element for element in elements if element.kind == "arc"][0]
    assert arc.radius == 5 and lies_on_circle(arc) and arc.ccw
    assert tangent(arc, elements[4])


def test_arcs_radius_center_tangential():
    short = elements_of("X10 Z-10\nG2 X20 Z-20 R10")[0]
    long = elements_of("X10 Z-10\nG2 X20 Z-20 R10 LA")[0]
    assert abs(math.degrees(short.sweep())) < 180 < abs(math.degrees(long.sweep()))
    centered = elements_of("X0 Z0\nG3 X30 Z-15 I0 K-15")[0]
    assert math.isclose(centered.radius, 15) and centered.ccw
    elements = elements_of("X0 Z0\nX20\nZ-10\nG3 X30 Z-15 TS\nZ-30")
    assert tangent(elements[1], elements[2])
    ball = elements_of("X0 Z0\nG3 X20 Z-26.1803 R15\nZ-40\nX32")
    assert math.isclose(ball[0].cz, -15, abs_tol=1e-3)
    try:
        elements_of("X10 Z-10\nG2 X20 Z-20 R3")
    except ContourError as error:
        assert "zu klein" in str(error)
    else:
        raise AssertionError


def test_fillet_between_line_and_arc():
    elements = elements_of("X0 Z0\nX20 R3\nG2 X30 Z-15 R12 RND2\nZ-30\nX40")
    assert len([element for element in elements if element.kind == "arc"]) == 3
    for index in range(len(elements) - 1):
        if elements[index].kind == "arc" or elements[index + 1].kind == "arc":
            assert tangent(elements[index], elements[index + 1]), index


# ------------------------------------------------------------------ Programm

def tube_stock():
    return Stock({"D": 64, "d": 16, "L": 120})


def all_ops():
    """Jeder Zyklus einmal, passend zu einem Rohr Ø64/Ø16 (jeweils einzeln erzeugt)."""
    return [Facing({"d_start": 64}),
            LongTurning({"d_start": 64, "d_end": 50, "start_kind": "Fase", "start_size": 1,
                         "end_kind": "Radius", "end_size": 2}),
            Taper({"mode": "Winkel", "d_start": 50, "d_end": 64, "angle": 15}),
            Thread({"diameter": 64, "pitch": 2}),
            EdgeBreak({"kind": "Radius", "diameter": 64}),
            Contour({"comp": "G42"}),
            Drill({"diameter": 10}),
            Facing({"side": "Innen", "d_start": 16, "d_end": 30, "z_start": 1, "z_end": -2}),
            LongTurning({"side": "Innen", "d_start": 16, "d_end": 20, "z_end": -8,
                         "start_kind": "Fase", "start_size": 1}),
            Taper({"side": "Innen", "d_start": 24, "d_end": 18, "z_end": -6}),
            Thread({"side": "Innen", "diameter": 20, "pitch": 1.5, "z_end": -5}),
            EdgeBreak({"side": "Innen", "diameter": 16, "size": 1, "angle": 30}),
            Contour({"side": "Innen", "contour": "X40 Z0 C1\nX30 Z-5 R1\nZ-8\nX16"})]


def test_program_generates_all_cycles_inside_and_outside():
    machine = Machine(tc_enabled=True, tc_x=100, tc_z=250)
    collected = []
    for op in all_ops():
        result = Program(machine, [op], stock=tube_stock()).generate()
        assert not result.errors, (op.KEY, op.side_text, result.errors)
        collected.append(result.gcode)
    gcode = "\n".join(collected)
    assert "G76 P2" in gcode and "G42" in gcode and "G40" in gcode and "G53 G0 X100" in gcode
    for text in collected:
        mode = "G7"
        for line in text.splitlines():
            if line in ("G7", "G8"):
                mode = line
            if line.startswith(("G2 ", "G3 ", "G76", "G53")):
                assert mode == "G8", line


def test_same_tool_no_tool_change_and_m6():
    machine = Machine(tc_enabled=True, tc_x=100, tc_z=250)
    ops = [Facing({"tool": 1}), LongTurning({"tool": 1}), Thread({"tool": 3}), Contour({"tool": 3})]
    gcode = Program(machine, ops, stock=Stock({"L": 100})).generate().gcode
    assert gcode.count(" M6 G43") == 2
    assert gcode.count("G53 G0 Z250") == 3
    assert "kein Werkzeugwechsel" in gcode and "Werkzeugwechselposition G53" in gcode


def test_machine_from_ini():
    folder = tempfile.mkdtemp()
    ini = os.path.join(folder, "t.ini")
    with open(ini, "w") as file:
        file.write("[SPINDLE_0]\nMAX_FORWARD_VELOCITY = 1800\n[AXIS_X]\nMAX_VELOCITY = 20\n"
                   "[AXIS_Z]\nMAX_VELOCITY = 40\n[DISPLAY]\nBACK_TOOL_LATHE = 1\n"
                   "[EMCIO]\nTOOL_TABLE = tool.tbl\nTOOL_CHANGE_POSITION = 1 2 3\n"
                   "[CHANGE_POSITION]\nX = 60\nY = 0\nZ = 180\n")
    machine = Machine.load(None, ini)
    assert machine.max_rpm == 1800 and machine.max_vel_x == 1200 and machine.max_vel_z == 2400
    assert machine.tc_enabled and (machine.tc_x, machine.tc_z) == (60, 180)
    assert machine.tool_table == os.path.join(folder, "tool.tbl")
    assert machine.default_spindle_dir == "M4"


def test_speed_feed_limits_and_spindle_direction():
    machine = Machine(max_rpm=4000, max_vel_x=600, max_vel_z=600)
    gcode = Program(machine, [LongTurning({"f_rough": 0.3, "f_finish": 0.1})]).generate().gcode
    assert "G96 D2000 " in gcode and " M3" in gcode
    machine = Machine(max_vel_z=1200, thread_speed_factor=0.8)
    assert "G97 S480 " in Program(machine, [Thread({"pitch": 2, "speed": 1000})]).generate().gcode
    machine = Machine(max_vel_x=500, max_vel_z=800, back_tool=True)
    op = LongTurning({"speed_feed": SPEED_FEED_MODES[3], "speed": 800, "f_rough": 900,
                      "f_finish": 100})
    result = Program(machine, [op]).generate()
    assert "G94" in result.gcode and "F500" in result.gcode and " M4" in result.gcode
    assert any("auf 500 mm/min begrenzt" in warning for warning in result.warnings)


def test_speed_feed_switch_converts_values():
    values = {"speed_feed": SPEED_FEED_MODES[1], "speed": 180.0, "d_start": 50.0,
              "f_rough": 0.25, "f_finish": 0.1}
    updates = LongTurning.switch("speed_feed", values, SPEED_FEED_MODES[0])
    assert updates["speed"] == round(180 * 1000 / (math.pi * 50))
    values = {"speed_feed": SPEED_FEED_MODES[3], "speed": 1000.0, "d_start": 50.0,
              "f_rough": 0.2, "f_finish": 0.1}
    updates = LongTurning.switch("speed_feed", values, SPEED_FEED_MODES[1])
    assert updates["f_rough"] == 200 and updates["f_finish"] == 100


def test_cycles_left_to_right():
    stock = Stock({"D": 50, "L": 60, "zero": "Futter"})
    groove = LongTurning({"d_start": 50, "d_end": 30, "z_start": 50, "z_end": 5})
    facing = Facing({"d_start": 30, "d_end": 50, "z_start": 10, "z_end": 14, "ap": 1})
    assert facing.z_sign() == -1
    material = simulate(stock, [groove], None, extra=facing)
    result = Program(Machine(), [facing], stock=stock).generate(start_material=material)
    assert not result.errors, result.errors
    feeds = [segment for segment in result.path if segment.kind == "feed"]
    assert max(segment.z1 for segment in feeds) > 13.9


def test_falling_taper_enters_obliquely():
    stock = Stock({"D": 44, "L": 90, "face": 0})
    taper = Taper({"d_start": 44, "d_end": 30, "z_start": 0, "z_end": -25, "ap": 1.5})
    result = Program(Machine(), [taper], stock=stock).generate()
    assert not result.errors, result.errors
    oblique = [segment for segment in result.path if segment.kind == "feed"
               and abs(segment.z1 - segment.z0) > 1e-3 and abs(segment.x1 - segment.x0) > 1e-3]
    assert len(oblique) >= 4
    # Kein Vorschub fährt senkrecht unter die Kegelfläche
    for segment in result.path:
        if segment.kind == "feed" and abs(segment.z1 - segment.z0) < 1e-6:
            cone_radius = 22 - (0 - segment.z0) * 7 / 25
            assert min(segment.x0, segment.x1) / 2 >= cone_radius - 0.05 or segment.z0 < -24.9


def test_back_edge_chamfer_with_negative_angle():
    stock = Stock({"D": 50, "L": 90, "face": 0})
    neck = LongTurning({"d_start": 50, "d_end": 30, "z_start": -20, "z_end": -40})
    edge = EdgeBreak({"diameter": 50, "angle": -30, "size": 3, "z_start": -20, "ap": 0.7})
    assert edge.is_back_edge()
    material = simulate(stock, [neck], None, extra=edge)
    result = Program(Machine(), [edge], stock=stock).generate(start_material=material)
    assert not result.errors, result.errors
    outline = edge.outline()
    assert outline[-1].z1 == -20 and outline[-1].r1 < 25


def _surface(points, z):
    best = None
    for (z_start, r_start), (z_end, r_end) in zip(points, points[1:]):
        if min(z_start, z_end) - 1e-9 <= z <= max(z_start, z_end) + 1e-9:
            if abs(z_end - z_start) < 1e-12:
                radius = r_start
            else:
                radius = r_start + (z - z_start) / (z_end - z_start) * (r_end - r_start)
            if best is None or radius > best:
                best = radius
    return best


def _check_no_gouge(op, stock, inside):
    result = Program(Machine(), [op], stock=stock).generate()
    assert not result.errors, result.errors
    if inside:
        sign = -1
    else:
        sign = 1
    points = [(z, sign * r) for z, r in polyline(op.real_elements(), 1.0)]
    for segment in result.path:
        if segment.kind == "arc":
            continue
        for step in range(11):
            z = segment.z0 + (segment.z1 - segment.z0) * step / 10
            radius = sign * (segment.x0 + (segment.x1 - segment.x0) * step / 10) / 2
            surface = _surface(points, z)
            if surface is not None:
                assert radius >= surface - 1e-3, (segment, z, radius, surface)


def test_roughing_stays_outside_part():
    _check_no_gouge(Contour({"corner_mode": "keine", "ap": 1,
                             "contour": "X20 Z0\nZ-10\nX30 Z-15 R2\nX24 Z-20\nZ-25 R1\nX36 Z-30\n"
                                        "Z-40\nX40"}), Stock({"D": 40, "L": 90, "face": 0}), False)


def test_inside_roughing_stays_in_bore():
    _check_no_gouge(Contour({"side": "Innen", "ap": 1,
                             "contour": "X50 Z0 C2\nX40 Z-15 R3\nX30 Z-20 R2\nZ-35\nG2 X24 Z-38 R3\n"
                                        "X18"}), Stock({"D": 70, "d": 18, "L": 90, "face": 0}), True)


def test_rapid_collision_detected():
    stock = Stock({"D": 50, "L": 90, "face": 0})
    op = LongTurning({"d_start": 50, "d_end": 40, "z_start": -40, "z_end": -10})
    result = Program(Machine(), [op], stock=stock).generate()
    assert any("Eilgang durch Material" in error for error in result.errors)


def test_stock_and_chuck_check():
    stock = Stock({"D": 50, "L": 40, "zero": "Vorderkante", "face": 1})
    assert stock.z_front == 1 and stock.z_chuck == -39
    result = Program(Machine(), [LongTurning({"z_end": -45})], stock=stock).generate()
    assert any("Kollision" in error for error in result.errors)
    result = Program(Machine(), [Drill({"z_end": -60})], stock=stock).generate()
    assert not result.errors and any("Futterfläche" in warning for warning in result.warnings)


def test_gcode_step_between_cycles():
    ops = [Facing({"tool": 1}), GCodeOp({"code": "M5\nM0 (Späne)"}), LongTurning({"tool": 1})]
    gcode = Program(Machine(), ops, stock=Stock({"L": 100})).generate().gcode
    after = gcode[gcode.index("M0 (Späne)"):]
    assert "M6" not in after and "G96" in after and "M8" in after


def test_material_simulation():
    stock = Stock({"D": 50, "d": 0, "L": 60})
    ops = [Facing({"d_start": 50, "z_start": 1, "z_end": 0}),
           LongTurning({"d_start": 50, "d_end": 40, "z_end": -20}), Drill({"z_end": -10})]
    material = simulate(stock, ops)

    def nearest(z):
        return min(range(material.count), key=lambda index: abs(material.z[index] - z))

    assert math.isclose(material.outer[nearest(-10)], 20, abs_tol=0.05)
    assert material.outer[nearest(0.5)] <= material.inner[nearest(0.5)] + 1e-6
    assert material.inner[nearest(-5)] > 4.9


# ------------------------------------------------------------------ Werkzeuge

def test_tool_checks():
    table = tool_table()
    # T1 C rechts außen, T2 C links, T7 V rechts, T10 Gewinde, T11 Bohrer
    thread = Thread({"tool": 1})
    result = Program(Machine(), [thread], tools=table).generate()
    assert any("kann kein Gewinde" in error for error in result.errors)
    thread.v["tool_ok"] = True
    result = Program(Machine(), [thread], tools=table).generate()
    assert not result.errors and any("freigegeben" in warning for warning in result.warnings)
    left_tool = Program(Machine(), [LongTurning({"tool": 2})], tools=table).generate()
    assert any("Schnittrichtung passt nicht" in error for error in left_tool.errors)
    falling = Taper({"d_start": 44, "d_end": 30, "z_start": 0, "z_end": -10, "tool": 1})
    result = Program(Machine(), [falling], stock=Stock({"D": 44}), tools=table).generate()
    assert any("Freiwinkel" in error for error in result.errors)
    falling.v["tool"] = 7
    result = Program(Machine(), [falling], stock=Stock({"D": 44}), tools=table).generate()
    assert not result.errors, result.errors
    inside = LongTurning({"side": "Innen", "d_start": 16, "d_end": 20, "z_end": -10, "tool": 1})
    result = Program(Machine(), [inside], stock=Stock({"d": 16}), tools=table).generate()
    assert any("Innenwerkzeug" in error for error in result.errors)


def test_tool_table_from_linuxcnc():
    entry = parse_tool_line("T3 P3 X+0.0 Z+0.0 D+0.8 I+0.0 J+0.0 Q0 ; unbekannt")
    tool = tool_from_table(entry, back_tool=False)
    assert tool.needs_input
    folder = tempfile.mkdtemp()
    table_path = os.path.join(folder, "tool.tbl")
    with open(table_path, "w") as file:
        file.write("T1 P1 D0.8 I95 J155 Q2 ;a\nT3 P3 D0.4 I0 J0 Q0 ;b\n")
    machine = Machine(tool_table=table_path)
    table = ToolTable(machine, os.path.join(folder, "werkzeuge.json"))
    assert table.from_linuxcnc and not table.get(1).needs_input
    assert [tool.number for tool in table.pending()] == [3]
    table.assign(table.get(3), "D", "rechts", "außen", 93.0, 0.4, 0.0, 0.0, "")
    reloaded = ToolTable(machine, os.path.join(folder, "werkzeuge.json"))
    assert not reloaded.get(3).needs_input and reloaded.get(3).kind == "D"
    with open(table_path, "w") as file:
        file.write("T1 P1 D0.8 I95 J155 Q2 ;a\nT3 P3 D0.8 I0 J0 Q0 ;b geändert\n")
    changed = ToolTable(machine, os.path.join(folder, "werkzeuge.json"))
    assert changed.get(3).needs_input            # Werkzeug geändert -> neu abfragen


def test_presets_and_settings():
    kinds = [(tool.kind, tool.hand) for tool in preset_tools()]
    for kind in ("C", "D", "T", "V"):
        assert (kind, "rechts") in kinds and (kind, "links") in kinds
    assert ("groove", "neutral") in kinds and ("thread", "neutral") in kinds
    folder = tempfile.mkdtemp()
    settings = Settings(os.path.join(folder, "settings.json"))
    names = [snippet["name"] for snippet in settings.snippets]
    assert "Späne entfernen" in names and "Nachmessen" in names
    settings.add_snippet("Test", "M0")
    settings.remember_dir(os.path.join(folder, "teil.ngc"))
    again = Settings(os.path.join(folder, "settings.json"))
    assert again.last_dir == folder and any(snippet["name"] == "Test" for snippet in again.snippets)


def test_old_project_keys():
    old = LongTurning({"stock_d": 45, "target_d": 30, "chamfer": 1, "end_radius": 2,
                       "speed_mode": "G97"})
    assert old.d_start == 45 and old.d_end == 30 and old.start_kind == "Fase"
    assert old.spindle_mode() == "G97"
    assert EdgeBreak({"edge_d": 33}).diameter == 33
    assert Drill({"drill_d": 8}).diameter == 8


def test_facing_no_false_rapid_collision():
    """Eilgang direkt auf der eben gedrehten Planfläche ist kein Fehler (Rasterauflösung)."""
    cases = [(Facing({"d_start": 51, "d_end": -0.4, "z_start": 1, "z_end": 0}), {"D": 50}),
             (Facing({"side": "Innen", "d_start": 8, "d_end": 15, "z_start": 1, "z_end": -15}),
              {"D": 50, "d": 10})]
    for op, stock_values in cases:
        for length in range(20, 200, 7):
            stock = Stock(dict(stock_values, L=length))
            before = simulate(stock, [], 0, extra=op)
            result = Program(Machine(), [op], stock=stock).generate(start_material=before)
            assert not result.errors, (length, result.errors)


def test_inside_facing_outward_order():
    """Innen nach außen: in der Bohrung auf Tiefe, nach außen schneiden, abheben, zurück."""
    op = Facing({"side": "Innen", "d_start": 8, "d_end": 15, "z_start": 1, "z_end": -15})
    gcode = Program(Machine(), [op], stock=Stock({"D": 50, "d": 10})).generate().gcode
    finish = gcode[gcode.index("(Schlichten)"):].splitlines()
    assert finish[1:5] == ["G0 Z-15", "G1 X15 F0.08", "G0 Z-14", "G0 X6"], finish


def test_thread_exit_taper_in_preview():
    op = Thread({"diameter": 20, "pitch": 2, "z_end": -25, "chamfer_l": 2, "chamfer_e": 5})
    result = Program(Machine(), [op], stock=Stock({"D": 20})).generate()
    threads = [segment for segment in result.path if segment.kind == "thread"]
    tapered = [segment for segment in threads
               if abs(segment.x1 - segment.x0) > 1e-6 and abs(segment.z1 - segment.z0) > 1e-6]
    assert tapered and all(math.isclose(segment.z0, -20) and math.isclose(segment.z1, -25)
                           and math.isclose(segment.x1, 20) for segment in tapered)
    assert "E5 L2" in result.gcode


def test_gcode_block_restarts_spindle_and_coolant():
    ops = [Facing({"d_start": 51}), GCodeOp({"code": "M5 M9 M0"}),
           Facing({"d_start": 51, "z_start": 0, "z_end": -1})]
    gcode = Program(Machine(), ops, stock=Stock({"D": 50})).generate().gcode
    after_block = gcode.split("(--- Ende eigener G-Code ---)")[1].splitlines()
    assert after_block[2].endswith(" M3") and after_block[3] == "M8", after_block[:5]


def test_cutting_data_is_remembered():
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "settings.json")
    settings = Settings(path)
    keys = LongTurning.cutting_keys()
    assert "f_rough" in keys and "tool" in keys
    assert "d_start" not in keys and "tool_ok" not in keys
    settings.remember_cutting("long/Innen", {"tool": 12, "f_rough": 0.15})
    assert Settings(path).cutting["long/Innen"] == {"tool": 12, "f_rough": 0.15}


def test_ini_from_machine_tab_and_insert_kind():
    """Ohne LinuxCNC: INI aus machine.json (Reiter Maschine), tool.tbl relativ zur INI."""
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, "lathe.ini"), "w", encoding="utf-8") as file:
        file.write("[EMCIO]\n# tool table file\nTOOL_TABLE = lathe.tbl\n")
    with open(os.path.join(folder, "lathe.tbl"), "w", encoding="utf-8") as file:
        file.write("T1 P1 X-12.0 Z-25.0 D0.4 I26.0 J86.0 Q2.0 ;60 Grad vorn\n")
    machine_json = os.path.join(folder, "machine.json")
    Machine(ini_file=os.path.join(folder, "lathe.ini")).save(machine_json)
    saved_environment = os.environ.pop("INI_FILE_NAME", None)
    try:
        machine = Machine.load(machine_json)
    finally:
        if saved_environment is not None:
            os.environ["INI_FILE_NAME"] = saved_environment
    assert machine.tool_table == os.path.join(folder, "lathe.tbl")
    table = ToolTable(machine, os.path.join(folder, "werkzeuge.json"))
    assert table.from_linuxcnc and table.get(1).kind == "T"     # 60° ist T, nicht D (55°)
    assert LongTurning().comp == "auto"


def test_tool_table_change_is_detected():
    folder = tempfile.mkdtemp()
    table_path = os.path.join(folder, "lathe.tbl")
    with open(table_path, "w", encoding="utf-8") as file:
        file.write("T1 P1 D0.4 I26.0 J86.0 Q2.0 ;60 Grad vorn\n")
    table = ToolTable(Machine(tool_table=table_path), os.path.join(folder, "werkzeuge.json"))
    assert not table.changed_on_disk() and sorted(table.tools) == [1]
    with open(table_path, "a", encoding="utf-8") as file:
        file.write("T2 P2 D0.1 I50.0 J85.0 Q2.0 ;35 Grad vorn\n")
    assert table.changed_on_disk()
    table.load()
    assert not table.changed_on_disk() and sorted(table.tools) == [1, 2]


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_"):
            function()
            print("ok ", name)
