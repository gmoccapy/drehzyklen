#!/usr/bin/env python3
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
"""Drehzyklen - start script.

Stand-alone:               python3 start_drehzyklen.py [--ini my_machine.ini]
Embedded in gmoccapy:      EMBED_TAB_COMMAND = python3 /path/start_drehzyklen.py --xid {XID}
Without GUI (test):        python3 start_drehzyklen.py --gcode project.dzp > part.ngc
Show the machine tab:      --machine-tab
Language:                  --language de   (otherwise the language chosen in the program)

Settings (machine.json, settings.json, werkzeuge.json, threads.json) are kept in
~/.config/drehzyklen.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from drehzyklen import i18n, threads  # noqa: E402
from drehzyklen.cycles import Program  # noqa: E402
from drehzyklen.machine import Machine  # noqa: E402
from drehzyklen.settings import Settings  # noqa: E402
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
    parser = argparse.ArgumentParser(description="Drehzyklen for LinuxCNC / gmoccapy")
    parser.add_argument("-x", "--xid", type=window_id,
                        help="X window id for embedding (gmoccapy sets {XID})")
    parser.add_argument("--ini", help="LinuxCNC INI (default: $INI_FILE_NAME)")
    parser.add_argument("--machine", default=default_machine_json(), help="path of machine.json")
    parser.add_argument("--project", help="load a project (.dzp) at the start")
    parser.add_argument("--machine-tab", action="store_true",
                        help="show the machine tab even while LinuxCNC is running")
    parser.add_argument("--gcode", metavar="PROJECT",
                        help="convert a project to G-code without GUI (output to stdout)")
    parser.add_argument("--language", help="en, de or es (saved for the next start)")
    arguments = parser.parse_args()
    config_dir = os.path.dirname(os.path.abspath(arguments.machine))
    if arguments.language:
        settings = Settings(os.path.join(config_dir, "settings.json"))
        settings.set_language(arguments.language)

    machine = Machine.load(arguments.machine, arguments.ini)

    if arguments.gcode:
        i18n.set_language(Settings(os.path.join(config_dir, "settings.json")).language)
        threads.load(os.path.join(config_dir, "threads.json"))
        tools = ToolTable(machine, os.path.join(config_dir, "werkzeuge.json"))
        with open(arguments.gcode, encoding="utf-8") as file:
            program = Program.from_json(file.read(), machine, tools)
        result = program.generate()
        for warning in result.warnings:
            print(i18n._("Hint:"), warning, file=sys.stderr)
        for error in result.errors:
            print(i18n._("ERROR:"), error, file=sys.stderr)
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
