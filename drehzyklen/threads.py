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
"""Gewindetabellen und daraus berechnete Werte für den Gewindezyklus.

Normen: metrisches ISO-Gewinde (Regel- und Feingewinde, 60°), UNC und UNF (60°),
Whitworth BSW und Rohrgewinde G/BSP (55°).

Berechnung (Grundprofil, ohne Toleranzfeld):
  60°: Flankentiefe außen h3 = 0,6134·P, innen H1 = 0,5413·P, Kern innen D1 = D − 1,0825·P
  55°: Flankentiefe außen und innen h = 0,6403·P, Kern D1 = D − 1,2806·P
Vordrehen außen (Faustformel): D − 0,1·P. Das entspricht etwa der Mitte des Toleranzfelds 6g.
Vordrehen innen: D1 + 0,1·P (etwa Mitte des Toleranzfelds 6H für den Kerndurchmesser).
Zustellwinkel Q: halber Flankenwinkel − 0,5° (29,5° bzw. 27°), damit die zweite Flanke
leicht nachschabt.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

INCH = 25.4


@dataclass(frozen=True)
class ThreadSpec:
    name: str            # z.B. "M12", "M12x1", "1/4-28 UNF", "G1/2"
    norm: str            # Schlüssel aus NORMS
    diameter: float      # Nenn-Ø (Außendurchmesser) in mm
    pitch: float         # Steigung in mm
    angle: float         # Flankenwinkel in Grad

    # ---------------------------------------------------------------- Profil
    def depth(self, inside: bool) -> float:
        if self.angle < 57.5:
            return 0.6403 * self.pitch
        if inside:
            return 0.5413 * self.pitch
        return 0.6134 * self.pitch

    def minor_diameter(self) -> float:
        """Kerndurchmesser der Mutter D1 (Grundprofil)."""
        if self.angle < 57.5:
            return self.diameter - 1.2806 * self.pitch
        return self.diameter - 1.0825 * self.pitch

    def infeed(self) -> float:
        return self.angle / 2 - 0.5

    def pre_turn_diameter(self, inside: bool) -> float:
        """Vordreh-Ø: außen D − 0,1·P, innen D1 + 0,1·P."""
        if inside:
            return self.minor_diameter() + 0.1 * self.pitch
        return self.diameter - 0.1 * self.pitch

    def cycle_diameter(self, inside: bool) -> float:
        """Wert für das Feld D im Gewindezyklus: außen Nenn-Ø, innen Kernloch-Ø."""
        if inside:
            return self.minor_diameter()
        return self.diameter

    def pitch_text(self) -> str:
        if self.norm in ("UNC", "UNF", "BSW", "G"):
            threads_per_inch = INCH / self.pitch
            return f"{threads_per_inch:g} Gg/Zoll = P{self.pitch:.4g}"
        return f"P{self.pitch:g}"


# ------------------------------------------------------------------ Tabellen

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "threads.json")
FREE = "frei"

# Normen: Schlüssel -> {"label": Beschriftung, "angle": Flankenwinkel}
NORMS: Dict[str, Dict] = {}
THREADS: List[ThreadSpec] = []
SIZE_KEYS: Dict[str, str] = {}
LOAD_MESSAGES: List[str] = []

USER_TEMPLATE = {
    "_info": "Eigene Gewinde für Drehzyklen. Beispiel siehe 'threads'. Felder: norm (vorhandene "
             "Norm wie M, MF, UNC, UNF, BSW, G oder eine eigene aus 'norms'), name, diameter (mm) "
             "oder diameter_inch, pitch (mm) oder tpi, optional angle. Gleicher Name in gleicher "
             "Norm ersetzt den Tabelleneintrag.",
    "norms": {"Eigene": {"label": "Eigene Gewinde", "angle": 60}},
    "threads": [{"norm": "Eigene", "name": "M12x0,75 (Beispiel)", "diameter": 12.0,
                 "pitch": 0.75}],
}


def _spec_from_entry(entry: dict) -> ThreadSpec:
    norm = str(entry["norm"])
    if "diameter" in entry:
        diameter = float(entry["diameter"])
    else:
        diameter = float(entry["diameter_inch"]) * INCH
    if "pitch" in entry:
        pitch = float(entry["pitch"])
    else:
        pitch = INCH / float(entry["tpi"])
    if "angle" in entry:
        angle = float(entry["angle"])
    else:
        angle = float(NORMS[norm].get("angle", 60.0))
    if diameter <= 0 or pitch <= 0:
        raise ValueError("Ø und Steigung müssen größer 0 sein")
    return ThreadSpec(str(entry["name"]), norm, diameter, pitch, angle)


def _read(path: str, user_file: bool):
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    for key, values in data.get("norms", {}).items():
        NORMS[str(key)] = {"label": str(values.get("label", key)),
                           "angle": float(values.get("angle", 60.0))}
    for entry in data.get("threads", []):
        try:
            spec = _spec_from_entry(entry)
        except (KeyError, TypeError, ValueError) as error:
            LOAD_MESSAGES.append(f"{os.path.basename(path)}: Eintrag {entry!r} übersprungen "
                                 f"({error})")
            continue
        for index, existing in enumerate(THREADS):
            if existing.norm == spec.norm and existing.name == spec.name:
                THREADS[index] = spec
                break
        else:
            THREADS.append(spec)
    if user_file:
        LOAD_MESSAGES.append(f"Eigene Gewinde aus {path}")


def load(user_path: Optional[str] = None):
    """Tabellen laden: mitgelieferte threads.json, danach die eigene Datei des Benutzers.

    Gibt es die eigene Datei noch nicht, wird sie mit einem Beispiel angelegt.
    """
    NORMS.clear()
    NORMS[FREE] = {"label": "frei (eigene Werte)", "angle": 60.0}
    THREADS.clear()
    LOAD_MESSAGES.clear()
    _read(DATA_FILE, False)
    if user_path:
        if not os.path.isfile(user_path):
            try:
                os.makedirs(os.path.dirname(user_path) or ".", exist_ok=True)
                with open(user_path, "w", encoding="utf-8") as file:
                    json.dump(USER_TEMPLATE, file, indent=2, ensure_ascii=False)
            except OSError:
                pass
        if os.path.isfile(user_path):
            try:
                _read(user_path, True)
            except (OSError, ValueError) as error:
                LOAD_MESSAGES.append(f"{user_path} nicht lesbar: {error}")
    SIZE_KEYS.clear()
    for norm in NORMS:
        if norm != FREE and names_for(norm):
            SIZE_KEYS[norm] = "size_" + re.sub(r"[^a-z0-9]", "_", norm.lower())


def norm_labels() -> Tuple[str, ...]:
    return tuple(NORMS[norm]["label"] for norm in [FREE] + list(SIZE_KEYS))


def names_for(norm: str) -> Tuple[str, ...]:
    return tuple(spec.name for spec in THREADS if spec.norm == norm)


def find(norm: str, name: str) -> Optional[ThreadSpec]:
    for spec in THREADS:
        if spec.norm == norm and spec.name == name:
            return spec
    return None


def norm_of_label(label: str) -> str:
    """Normschlüssel aus der Beschriftung der Auswahl (oder dem Schlüssel selbst)."""
    for key, values in NORMS.items():
        if label in (key, values["label"]):
            return key
    return FREE


load()
