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
"""Maschinendaten und -grenzen.

Quellen (spätere überschreiben frühere):
  1. Standardwerte
  2. machine.json (eigene Einstellungen, Reiter "Maschine")
  3. LinuxCNC-INI: Drehzahl- und Achsgrenzen, Werkzeugwechselposition, Programmordner,
     Werkzeugtabelle

Welche INI? Der Reihe nach: Aufrufparameter --ini, Umgebungsvariable INI_FILE_NAME (gesetzt,
wenn LinuxCNC das Programm startet), INI des gerade laufenden LinuxCNC, zuletzt die im Reiter
Maschine eingetragene INI (für den Betrieb ohne LinuxCNC).
"""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import asdict, dataclass, fields
from typing import List, Optional


def running_linuxcnc_ini() -> Optional[str]:
    """INI des laufenden LinuxCNC (wenn das Programm z.B. im Terminal gestartet wurde)."""
    try:
        import linuxcnc
        status = linuxcnc.stat()
        status.poll()
        path = status.ini_filename
    except Exception:  # noqa: BLE001 – Modul fehlt oder LinuxCNC läuft nicht
        return None
    if path and os.path.isfile(path):
        return path
    return None


@dataclass
class Machine:
    max_rpm: float = 3000.0          # 1/min
    min_rpm: float = 50.0            # 1/min
    max_vel_x: float = 3000.0        # mm/min
    max_vel_z: float = 4000.0        # mm/min
    max_acc_x: float = 500.0         # mm/s²
    max_acc_z: float = 500.0         # mm/s²
    max_diameter: float = 200.0      # mm (Umlaufdurchmesser / Futter)
    tc_enabled: bool = False         # Werkzeugwechselposition mit G53 anfahren
    tc_x: float = 0.0                # G53-Position X (Radiuswert wie in der INI)
    tc_z: float = 0.0                # G53-Position Z
    tc_move_z: bool = True           # aus: nur X anfahren, Z bleibt stehen
    clearance: float = 1.0           # Sicherheitsabstand mm
    retract: float = 5.0             # Rückzugsebene: Abstand vor der vordersten Werkstückkante
    thread_speed_factor: float = 0.8  # Anteil der Z-Geschwindigkeit beim Gewinden
    back_tool: bool = False          # Werkzeug hinter der Drehmitte
    program_dir: str = "~/linuxcnc/nc_files"
    ini_file: str = ""               # LinuxCNC-INI für den Betrieb ohne laufendes LinuxCNC
    auto_reload_tools: bool = False  # tool.tbl bei Änderung automatisch neu einlesen

    source: str = "Standardwerte"
    tc_source: str = ""
    ini_loaded: bool = False
    ini_used: str = ""               # tatsächlich gelesene INI
    tool_table: str = ""             # Pfad der tool.tbl aus der INI

    _INTERNAL = ("source", "tc_source", "ini_loaded", "ini_used", "tool_table")

    # ---------------------------------------------------------------- Laden/Speichern
    @classmethod
    def load(cls, json_path: Optional[str] = None, ini_path: Optional[str] = None) -> "Machine":
        machine = cls()
        sources: List[str] = []
        if json_path and os.path.isfile(json_path):
            try:
                with open(json_path, encoding="utf-8") as file:
                    machine.update_from_dict(json.load(file))
                sources.append(os.path.basename(json_path))
                if machine.tc_enabled:
                    machine.tc_source = "machine.json"
            except (OSError, ValueError):
                pass
        if not ini_path:
            ini_path = os.environ.get("INI_FILE_NAME")
        if not ini_path:
            ini_path = running_linuxcnc_ini()
        if not ini_path and machine.ini_file:
            ini_path = os.path.expanduser(machine.ini_file)
        if ini_path and os.path.isfile(ini_path):
            if machine.update_from_ini(ini_path):
                sources.append(os.path.basename(ini_path))
                machine.ini_loaded = True
                machine.ini_used = os.path.abspath(ini_path)
        if sources:
            machine.source = " + ".join(sources)
        else:
            machine.source = "Standardwerte"
        return machine

    def update_from_dict(self, values: dict):
        for field in fields(self):
            name = field.name
            if name not in values or name in self._INTERNAL:
                continue
            current = getattr(self, name)
            new_value = values[name]
            try:
                if isinstance(current, bool):
                    if not isinstance(new_value, bool):
                        new_value = str(new_value).lower() in ("1", "true", "ja", "yes")
                elif isinstance(current, float):
                    new_value = float(new_value)
                elif isinstance(current, str):
                    new_value = str(new_value)
                setattr(self, name, new_value)
            except (TypeError, ValueError):
                pass

    def save(self, json_path: str):
        values = asdict(self)
        for name in self._INTERNAL:
            values.pop(name, None)
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(values, file, indent=2, ensure_ascii=False)

    def update_from_ini(self, path: str) -> bool:
        parser = configparser.ConfigParser(strict=False, interpolation=None,
                                           comment_prefixes=("#", ";"),
                                           inline_comment_prefixes=("#",))
        parser.optionxform = str.upper
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            return False

        def get_text(section, key):
            try:
                return parser.get(section, key).strip()
            except configparser.Error:
                return None

        def get_number(section, key):
            text = get_text(section, key)
            if not text:
                return None
            try:
                return float(text.split()[0])
            except ValueError:
                return None

        ini_dir = os.path.dirname(os.path.abspath(path))
        units = (get_text("TRAJ", "LINEAR_UNITS") or "mm").lower()
        if units in ("inch", "in", "imperial"):
            factor = 25.4
        else:
            factor = 1.0

        value = get_number("SPINDLE_0", "MAX_FORWARD_VELOCITY")
        if not value:
            value = get_number("DISPLAY", "MAX_SPINDLE_SPEED")
        if value:
            self.max_rpm = value
        value = get_number("SPINDLE_0", "MIN_FORWARD_VELOCITY")
        if value is not None:
            self.min_rpm = value
        for axis in ("X", "Z"):
            velocity = get_number(f"AXIS_{axis}", "MAX_VELOCITY")
            acceleration = get_number(f"AXIS_{axis}", "MAX_ACCELERATION")
            if velocity:
                setattr(self, f"max_vel_{axis.lower()}", velocity * factor * 60.0)
            if acceleration:
                setattr(self, f"max_acc_{axis.lower()}", acceleration * factor)
        back_tool_text = (get_text("DISPLAY", "BACK_TOOL_LATHE") or "").lower()
        if back_tool_text in ("1", "true", "yes"):
            self.back_tool = True
        prefix = get_text("DISPLAY", "PROGRAM_PREFIX")
        if prefix:
            prefix = os.path.expanduser(prefix)
            if os.path.isabs(prefix):
                self.program_dir = prefix
            else:
                self.program_dir = os.path.join(ini_dir, prefix)
        table = get_text("EMCIO", "TOOL_TABLE")
        if table:
            table = os.path.expanduser(table.split()[0])
            if not os.path.isabs(table):
                table = os.path.join(ini_dir, table)
            self.tool_table = table

        # Werkzeugwechselposition: gmoccapy [CHANGE_POSITION], sonst [EMCIO] TOOL_CHANGE_POSITION
        change_x = get_number("CHANGE_POSITION", "X")
        change_z = get_number("CHANGE_POSITION", "Z")
        if change_x is not None:
            # [CHANGE_POSITION] nur mit X: Z bleibt beim Wechsel stehen
            self.tc_enabled = True
            self.tc_x = change_x * factor
            if change_z is not None:
                self.tc_z = change_z * factor
                self.tc_move_z = True
            else:
                self.tc_move_z = False
            self.tc_source = "INI [CHANGE_POSITION]"
        else:
            text = get_text("EMCIO", "TOOL_CHANGE_POSITION")
            if text:
                try:
                    values = [float(token) for token in text.split()]
                except ValueError:
                    values = []
                if len(values) >= 3:
                    self.tc_enabled = True
                    self.tc_x = values[0] * factor
                    self.tc_z = values[2] * factor
                    self.tc_source = "INI [EMCIO] TOOL_CHANGE_POSITION"
        return True

    # ---------------------------------------------------------------- Hilfen
    @property
    def max_feed_vel(self) -> float:
        """Langsamste Achse in mm/min (konservativ für Vorschubprüfung)."""
        return min(self.max_vel_x, self.max_vel_z)

    @property
    def default_spindle_dir(self) -> str:
        """Werkzeug vor der Drehmitte: M3, hinter der Drehmitte: M4."""
        if self.back_tool:
            return "M4"
        return "M3"

    def summary(self) -> str:
        if self.tc_enabled and self.tc_move_z:
            tool_change = f"WZW G53 X{self.tc_x:g} Z{self.tc_z:g}"
        elif self.tc_enabled:
            tool_change = f"WZW G53 X{self.tc_x:g} (Z bleibt)"
        else:
            tool_change = "keine WZW-Position"
        return (f"n {self.min_rpm:.0f}–{self.max_rpm:.0f} 1/min · "
                f"vX {self.max_vel_x:.0f} / vZ {self.max_vel_z:.0f} mm/min · "
                f"Ø max {self.max_diameter:.0f} · {tool_change}  ({self.source})")


MACHINE_FIELDS = [
    ("max_rpm", "Max. Spindeldrehzahl [1/min]"),
    ("min_rpm", "Min. Spindeldrehzahl [1/min]"),
    ("max_vel_x", "Max. Geschwindigkeit X [mm/min]"),
    ("max_vel_z", "Max. Geschwindigkeit Z [mm/min]"),
    ("max_acc_x", "Max. Beschleunigung X [mm/s²]"),
    ("max_acc_z", "Max. Beschleunigung Z [mm/s²]"),
    ("max_diameter", "Max. Drehdurchmesser [mm]"),
    ("tc_enabled", "Werkzeugwechselposition G53 anfahren"),
    ("tc_x", "Werkzeugwechsel G53 X (Radius wie INI)"),
    ("tc_move_z", "Werkzeugwechsel: auch Z anfahren (aus = nur X)"),
    ("tc_z", "Werkzeugwechsel G53 Z"),
    ("clearance", "Sicherheitsabstand [mm]"),
    ("retract", "Rückzugsebene vor Werkstück [mm]"),
    ("thread_speed_factor", "Gewinde: Anteil v_Z max"),
    ("back_tool", "Werkzeug hinter der Drehmitte"),
    ("program_dir", "Programmordner"),
    ("ini_file", "LinuxCNC-INI (ohne laufendes LinuxCNC)"),
    ("auto_reload_tools", "Werkzeugtabelle bei Änderung automatisch einlesen"),
]
