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
"""User settings: language, last used folder, G-code snippets and the last used cutting data
of every cycle.

Stored in ~/.config/drehzyklen/settings.json (next to machine.json).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .i18n import N_

# names are translated when shown; the code stays as written
DEFAULT_SNIPPETS = [
    {"name": N_("Remove chips"), "code": "M5\nM9\nM0 (Remove chips - continue with cycle start)"},
    {"name": N_("Measure"), "code": "G0 X200\n(MSG, Please measure the diameter)\nM0"},
]
LEGACY_SIDES = {"Außen": "outside", "Innen": "inside"}


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.last_dir = ""
        self.language = "en"
        self.snippets: List[Dict[str, str]] = [dict(snippet) for snippet in DEFAULT_SNIPPETS]
        self.cutting: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError):
            return
        self.last_dir = str(data.get("last_dir", ""))
        self.language = str(data.get("language", "en"))
        snippets = data.get("snippets")
        if isinstance(snippets, list):
            cleaned = []
            for snippet in snippets:
                if isinstance(snippet, dict) and "name" in snippet and "code" in snippet:
                    cleaned.append({"name": str(snippet["name"]), "code": str(snippet["code"])})
            self.snippets = cleaned
        cutting = data.get("cutting")
        if isinstance(cutting, dict):
            for cycle_key, values in cutting.items():
                if isinstance(values, dict):
                    self.cutting[self._migrated_key(str(cycle_key))] = self._migrated(values)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump({"language": self.language, "last_dir": self.last_dir,
                           "snippets": self.snippets, "cutting": self.cutting}, file, indent=2,
                          ensure_ascii=False)
        except OSError:
            pass

    def program_dir(self, machine_dir: str) -> str:
        """Folder for file dialogs: the last used folder, otherwise the program folder."""
        if self.last_dir and os.path.isdir(self.last_dir):
            return self.last_dir
        return os.path.expanduser(machine_dir)

    def remember_dir(self, file_path: str):
        folder = os.path.dirname(os.path.abspath(file_path))
        if folder and folder != self.last_dir:
            self.last_dir = folder
            self.save()

    def add_snippet(self, name: str, code: str):
        for snippet in self.snippets:
            if snippet["name"] == name:
                snippet["code"] = code
                self.save()
                return
        self.snippets.append({"name": name, "code": code})
        self.save()

    def remove_snippet(self, name: str):
        self.snippets = [snippet for snippet in self.snippets if snippet["name"] != name]
        self.save()

    def remember_cutting(self, cycle_key: str, values: Dict[str, Any]):
        """Remember the cutting data of a cycle (saved only if something changed)."""
        if self.cutting.get(cycle_key) == values:
            return
        self.cutting[cycle_key] = dict(values)
        self.save()

    def set_language(self, code: str):
        if code != self.language:
            self.language = code
            self.save()

    @staticmethod
    def _migrated_key(key: str) -> str:
        """Cutting data keys of version 0.4 ("facing/Außen") -> "facing/outside"."""
        cycle, separator, side = key.partition("/")
        if separator and side in LEGACY_SIDES:
            return f"{cycle}/{LEGACY_SIDES[side]}"
        return key

    @staticmethod
    def _migrated(values: Dict[str, Any]) -> Dict[str, Any]:
        from .cycles.params import LEGACY_VALUES
        result = {}
        for key, value in values.items():
            if isinstance(value, str) and value in LEGACY_VALUES:
                result[key] = LEGACY_VALUES[value]
            else:
                result[key] = value
        return result
