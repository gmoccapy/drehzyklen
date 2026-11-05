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
"""Collect all translatable texts and update the translation files.

    python3 scripts/update_translations.py

1. Every _("...") and N_("...") in drehzyklen/ and every text marked translatable in the Glade
   file drehzyklen/ui/drehzyklen.ui is collected into drehzyklen/locale/drehzyklen.pot.
2. Every drehzyklen/locale/<language>.po is merged with it: existing translations are kept,
   new texts are added with an empty translation, texts no longer used are dropped.

Then translate the empty entries (Poedit or a text editor). A new language is added by copying
drehzyklen.pot to <code>.po (in drehzyklen/locale/ or in ~/.config/drehzyklen/locale/); it
appears in the language selection automatically. The shown name can be set in the header with
"X-Language-Name: Français\n".

Further PO files (e.g. an own translation in ~/.config/drehzyklen/locale) can be given as
arguments; they are merged as well and created from the template if they do not exist:

    python3 scripts/update_translations.py ~/.config/drehzyklen/locale/fr.po
"""
import ast
import os
import sys
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from drehzyklen.i18n import LANGUAGE_NAMES, po_header, read_po  # noqa: E402

PACKAGE = os.path.join(ROOT, "drehzyklen")
LOCALE = os.path.join(PACKAGE, "locale")


def is_translation_call(node) -> bool:
    """_("..."), N_("...") or i18n._("...") with a constant text."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    function = node.func
    if isinstance(function, ast.Name):
        name = function.id
    elif isinstance(function, ast.Attribute):
        name = function.attr
    else:
        return False
    argument = node.args[0]
    return (name in ("_", "N_") and isinstance(argument, ast.Constant)
            and isinstance(argument.value, str) and bool(argument.value))


def collect():
    """{msgid: [file:line, ...]} in the order of first appearance."""
    paths = [os.path.join(ROOT, "start_drehzyklen.py")]
    for folder, _dirs, files in sorted(os.walk(PACKAGE)):
        for name in sorted(files):
            if name.endswith(".py"):
                paths.append(os.path.join(folder, name))
    found = {}
    for path in paths:
        relative = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as file:
            tree = ast.parse(file.read(), path)
        calls = [node for node in ast.walk(tree) if is_translation_call(node)]
        calls.sort(key=lambda node: (node.lineno, node.col_offset))
        for node in calls:
            found.setdefault(node.args[0].value, []).append(f"{relative}:{node.lineno}")
    for path in ui_paths():
        for text, line in ui_texts(path):
            found.setdefault(text, []).append(f"{os.path.relpath(path, ROOT)}:{line}")
    return found


def ui_paths():
    folder = os.path.join(PACKAGE, "ui")
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
            if name.endswith(".ui")]


def ui_texts(path):
    """Texts marked translatable="yes" in a Glade file, with their line numbers."""
    parser = ElementTree.XMLPullParser(events=("end",))
    texts = []
    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            parser.feed(line)
            for _event, element in parser.read_events():
                if element.get("translatable") == "yes" and element.text:
                    texts.append((element.text, line_number))
    return texts


def quote(text):
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
               .replace("\t", "\\t"))
    return f'"{escaped}"'


def entry(msgid, msgstr, places):
    lines = []
    for index in range(0, len(places), 4):
        lines.append("#: " + " ".join(places[index:index + 4]))
    if "{" in msgid:
        lines.append("#, python-brace-format")
    lines.append("msgid " + quote(msgid))
    lines.append("msgstr " + quote(msgstr))
    return "\n".join(lines)


def header(language, language_name=""):
    name_line = ""
    if language_name:
        name_line = f'"X-Language-Name: {language_name}\\n"\n'
    return (f'# Drehzyklen translation ({language})\n'
            '# SPDX-License-Identifier: GPL-2.0-or-later\n'
            'msgid ""\n'
            'msgstr ""\n'
            '"Project-Id-Version: Drehzyklen\\n"\n'
            f'"Language: {language}\\n"\n' + name_line +
            '"MIME-Version: 1.0\\n"\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n'
            '"Content-Transfer-Encoding: 8bit\\n"\n')


def write(path, language, messages, translations, language_name=""):
    parts = [header(language, language_name)]
    for msgid, places in messages.items():
        parts.append(entry(msgid, translations.get(msgid, ""), places))
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n\n".join(parts) + "\n")


def merge(path, messages):
    """Bring one PO file up to date with the collected texts (created if missing)."""
    code = os.path.basename(path)[:-3]
    translations = {}
    language_name = LANGUAGE_NAMES.get(code, "")
    if os.path.isfile(path):
        translations = read_po(path)
        language_name = po_header(path).get("X-Language-Name", "") or language_name
    write(path, code, messages, translations, language_name)
    missing = [msgid for msgid in messages if not translations.get(msgid)]
    print(f"{path}: {len(messages) - len(missing)} of {len(messages)} translated")


def main():
    messages = collect()
    os.makedirs(LOCALE, exist_ok=True)
    write(os.path.join(LOCALE, "drehzyklen.pot"), "", messages, {})
    paths = [os.path.join(LOCALE, name) for name in sorted(os.listdir(LOCALE))
             if name.endswith(".po")]
    for argument in sys.argv[1:]:
        path = os.path.abspath(os.path.expanduser(argument))
        if not path.endswith(".po"):
            sys.exit(f"not a .po file: {argument}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        paths.append(path)
    for path in paths:
        merge(path, messages)
    print(f"{len(messages)} texts in drehzyklen.pot")


if __name__ == "__main__":
    main()
