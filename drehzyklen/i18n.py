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
compiled ``.mo`` files are needed. Own translations can also be put into
``~/.config/drehzyklen/locale/<code>.po``; every PO file found there appears in the language
selection automatically (a file there wins over one with the same name in the program folder).
The name shown in the selection comes from the header field ``X-Language-Name`` of the PO
file, otherwise from a list of common languages, otherwise the code itself is shown.

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
USER_LOCALE_DIR = os.path.expanduser("~/.config/drehzyklen/locale")
LANGUAGE_NAMES = {"en": "English", "de": "Deutsch", "es": "Español", "fr": "Français",
                  "it": "Italiano", "nl": "Nederlands", "pt": "Português", "pl": "Polski",
                  "cs": "Čeština", "sk": "Slovenčina", "sl": "Slovenščina", "hr": "Hrvatski",
                  "hu": "Magyar", "ro": "Română", "da": "Dansk", "sv": "Svenska",
                  "nb": "Norsk bokmål", "fi": "Suomi", "tr": "Türkçe", "ru": "Русский",
                  "uk": "Українська", "el": "Ελληνικά", "ja": "日本語", "zh": "中文",
                  "ko": "한국어"}

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


def locale_dirs() -> List[str]:
    """Folders with PO files; later folders win (the user's own translations)."""
    return [LOCALE_DIR, USER_LOCALE_DIR]


def po_files() -> Dict[str, str]:
    """Language code -> path of its PO file."""
    files: Dict[str, str] = {}
    for folder in locale_dirs():
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".po"):
                files[name[:-3]] = os.path.join(folder, name)
    return files


def available_languages() -> List[str]:
    """English first, then all languages with a PO file, sorted by their shown name."""
    files = po_files()
    codes = [code for code in files if code != DEFAULT_LANGUAGE]
    codes.sort(key=lambda code: language_name(code).lower())
    return [DEFAULT_LANGUAGE] + codes


def language_name(code: str) -> str:
    """Name for the language selection."""
    path = po_files().get(code)
    if path is not None:
        name = po_header(path).get("X-Language-Name", "").strip()
        if name:
            return name
    if code in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[code]
    base = code.split("_")[0].split("-")[0]
    if base in LANGUAGE_NAMES:
        return f"{LANGUAGE_NAMES[base]} ({code})"
    return code


def set_language(code: str) -> bool:
    """Activate a language; returns False (and keeps the old language) if no catalog exists."""
    global _language
    code = (code or DEFAULT_LANGUAGE).split(".")[0]
    files = po_files()
    if code not in files:
        # e.g. "de_DE" from the system locale -> "de"
        code = code.split("_")[0].split("-")[0].lower()
    if code == DEFAULT_LANGUAGE:
        _catalog.clear()
        _language = DEFAULT_LANGUAGE
        _notify()
        return True
    path = files.get(code)
    if path is None:
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


def po_header(path: str) -> Dict[str, str]:
    """Fields of the PO header (the entry with the empty msgid)."""
    header_text = []
    target = None
    try:
        with open(path, encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if line.startswith("msgid "):
                    if target is not None or _unquote(line[6:]):
                        break               # the header is the first entry
                    target = "msgid"
                elif line.startswith("msgstr ") and target == "msgid":
                    header_text.append(_unquote(line[7:]))
                    target = "msgstr"
                elif line.startswith('"') and target == "msgstr":
                    header_text.append(_unquote(line))
                elif line and not line.startswith("#") and target == "msgstr":
                    break
    except (OSError, ValueError, SyntaxError):
        return {}
    fields: Dict[str, str] = {}
    for header_line in "".join(header_text).splitlines():
        key, separator, value = header_line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields
