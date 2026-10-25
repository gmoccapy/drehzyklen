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
"""Translations that can be switched while the program runs.

The source text is English. Translations live in ``drehzyklen/locale/<code>.po`` (standard
gettext PO files, editable with Poedit or any text editor). They are read directly, so no
compiled ``.mo`` files are needed.

Usage::

    from .i18n import _, N_
    LABEL = N_("Feed rate")            # marked for extraction, translated when displayed
    message = _("Tool T{number} not found").format(number=4)

``_()`` looks the text up in the active language at call time; switching the language with
``set_language()`` therefore affects everything that is drawn or generated afterwards.
Placeholders use ``str.format`` names so that translations may reorder them.
"""
from __future__ import annotations

import ast
import os
from typing import Callable, Dict, List

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")
DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "de": "Deutsch", "es": "Español"}

_catalog: Dict[str, str] = {}
_language = DEFAULT_LANGUAGE
_listeners: List[Callable[[str], None]] = []


def _(text: str) -> str:
    """Translate text into the active language (unknown text stays English)."""
    if not text:
        return text
    translated = _catalog.get(text)
    if translated:
        return translated
    return text


def N_(text: str) -> str:
    """Mark text for translation without translating it now (module level constants)."""
    return text


def language() -> str:
    return _language


def available_languages() -> List[str]:
    codes = [DEFAULT_LANGUAGE]
    if os.path.isdir(LOCALE_DIR):
        for name in sorted(os.listdir(LOCALE_DIR)):
            if name.endswith(".po"):
                codes.append(name[:-3])
    return codes


def set_language(code: str) -> bool:
    """Activate a language; returns False (and keeps English) if no catalog exists."""
    global _language
    code = (code or DEFAULT_LANGUAGE).split("_")[0].split(".")[0].lower()
    if code == DEFAULT_LANGUAGE:
        _catalog.clear()
        _language = DEFAULT_LANGUAGE
        _notify()
        return True
    path = os.path.join(LOCALE_DIR, f"{code}.po")
    if not os.path.isfile(path):
        return False
    catalog = read_po(path)
    _catalog.clear()
    _catalog.update(catalog)
    _language = code
    _notify()
    return True


def on_language_change(callback: Callable[[str], None]):
    _listeners.append(callback)


def _notify():
    for callback in list(_listeners):
        callback(_language)


# ------------------------------------------------------------------ PO files

def _unquote(text: str) -> str:
    return ast.literal_eval(text)


def read_po(path: str) -> Dict[str, str]:
    """Read msgid/msgstr pairs from a PO file (fuzzy and empty entries are skipped)."""
    catalog: Dict[str, str] = {}
    msgid: List[str] = []
    msgstr: List[str] = []
    target = None
    entry_fuzzy = False          # the entry being read is marked "#, fuzzy"
    next_fuzzy = False           # a "#, fuzzy" line was seen for the next entry

    def store():
        source = "".join(msgid)
        translation = "".join(msgstr)
        if source and translation and not entry_fuzzy:
            catalog[source] = translation

    with open(path, encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#,"):
                if "fuzzy" in line:
                    next_fuzzy = True
                continue
            if line.startswith("#"):
                continue
            if line.startswith("msgid "):
                if target == "msgstr":
                    store()
                entry_fuzzy = next_fuzzy
                next_fuzzy = False
                msgid = [_unquote(line[6:])]
                msgstr = []
                target = "msgid"
            elif line.startswith("msgstr "):
                msgstr = [_unquote(line[7:])]
                target = "msgstr"
            elif line.startswith('"'):
                if target == "msgid":
                    msgid.append(_unquote(line))
                elif target == "msgstr":
                    msgstr.append(_unquote(line))
        if target == "msgstr":
            store()
    return catalog
