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
"""Text output for cairo drawings.

Cairo's simple text API has no font fallback: a character missing from the selected font is
drawn as an empty box. Typographic characters are therefore replaced by plain ones.
"""

REPLACEMENTS = {"−": "-", "…": "...", "→": "->", "≈": "~", "✔": "", "–": "-"}


def plain(text: str) -> str:
    for character, replacement in REPLACEMENTS.items():
        text = text.replace(character, replacement)
    return text


def show_text(cr, text: str):
    cr.show_text(plain(text))


def text_width(cr, text: str) -> float:
    return cr.text_extents(plain(text))[4]
