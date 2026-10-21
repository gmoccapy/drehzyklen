#!/usr/bin/env python3
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
"""Drehzyklen – Startprogramm.

Eigenständig:              python3 start_drehzyklen.py [--ini meine_maschine.ini]
Eingebettet in gmoccapy:   EMBED_TAB_COMMAND = python3 /pfad/start_drehzyklen.py --xid {XID}
Ohne Oberfläche (Test):    python3 start_drehzyklen.py --gcode projekt.dzp > teil.ngc
Maschinen-Reiter zeigen:   --machine-tab

Einstellungen (machine.json, settings.json, werkzeuge.json) liegen in ~/.config/drehzyklen.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from drehzyklen.cycles import Program  # noqa: E402
from drehzyklen.machine import Machine  # noqa: E402
from drehzyklen.tools import ToolTable  # noqa: E402

CONFIG_DIR = os.path.expanduser("~/.config/drehzyklen")


def default_machine_json():
    user_file = os.path.join(CONFIG_DIR, "machine.json")
    local_file = os.path.join(HERE, "machine.json")
    if os.path.isfile(user_file):
        return user_file
    if os.path.isfile(local_file):
        return local_file
    return user_file


def window_id(text):
    return int(text, 0)


def main():
    parser = argparse.ArgumentParser(description="Drehzyklen für LinuxCNC / gmoccapy")
    parser.add_argument("-x", "--xid", type=window_id,
                        help="X-Window-ID zum Einbetten (gmoccapy setzt {XID})")
    parser.add_argument("--ini", help="LinuxCNC-INI (Standard: $INI_FILE_NAME)")
    parser.add_argument("--machine", default=default_machine_json(), help="Pfad zu machine.json")
    parser.add_argument("--project", help="Projekt (.dzp) beim Start laden")
    parser.add_argument("--machine-tab", action="store_true",
                        help="Reiter Maschine auch bei laufendem LinuxCNC einblenden")
    parser.add_argument("--gcode", metavar="PROJEKT",
                        help="Projekt ohne Oberfläche in G-Code umwandeln (Ausgabe auf stdout)")
    arguments = parser.parse_args()

    machine = Machine.load(arguments.machine, arguments.ini)

    if arguments.gcode:
        config_dir = os.path.dirname(os.path.abspath(arguments.machine))
        tools = ToolTable(machine, os.path.join(config_dir, "werkzeuge.json"))
        with open(arguments.gcode, encoding="utf-8") as file:
            program = Program.from_json(file.read(), machine, tools)
        result = program.generate()
        for warning in result.warnings:
            print("Hinweis:", warning, file=sys.stderr)
        for error in result.errors:
            print("FEHLER:", error, file=sys.stderr)
        sys.stdout.write(result.gcode)
        if result.errors:
            sys.exit(1)
        sys.exit(0)

    from drehzyklen.gui import run
    ini_path = arguments.ini or os.environ.get("INI_FILE_NAME")
    run(machine, arguments.machine, ini_path, arguments.xid, arguments.project,
        arguments.machine_tab)


if __name__ == "__main__":
    main()
