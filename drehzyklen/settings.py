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
"""Benutzereinstellungen: zuletzt benutzter Ordner und G-Code-Textbausteine.

Gespeichert in ~/.config/drehzyklen/settings.json (bzw. neben machine.json).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

DEFAULT_SNIPPETS = [
    {"name": "Späne entfernen", "code": "M5\nM9\nM0 (Späne entfernen – weiter mit Zyklusstart)"},
    {"name": "Nachmessen", "code": "G0 X200\n(MSG, Bitte Durchmesser nachmessen)\nM0"},
]


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.last_dir = ""
        self.snippets: List[Dict[str, str]] = [dict(snippet) for snippet in DEFAULT_SNIPPETS]
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
        snippets = data.get("snippets")
        if isinstance(snippets, list):
            cleaned = []
            for snippet in snippets:
                if isinstance(snippet, dict) and "name" in snippet and "code" in snippet:
                    cleaned.append({"name": str(snippet["name"]), "code": str(snippet["code"])})
            self.snippets = cleaned

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump({"last_dir": self.last_dir, "snippets": self.snippets}, file,
                          indent=2, ensure_ascii=False)
        except OSError:
            pass

    def program_dir(self, machine_dir: str) -> str:
        """Ordner für Dateidialoge: zuletzt benutzter Ordner, sonst der Programmordner."""
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
