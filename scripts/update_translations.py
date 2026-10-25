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

1. Every _("...") and N_("...") in drehzyklen/ is collected into drehzyklen/locale/drehzyklen.pot.
2. Every drehzyklen/locale/<language>.po is merged with it: existing translations are kept,
   new texts are added with an empty translation, texts no longer used are dropped.

Then translate the empty entries (Poedit or a text editor). A new language is added by copying
drehzyklen.pot to <code>.po; it appears in the language selection automatically.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from drehzyklen.i18n import read_po  # noqa: E402

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
    return found


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


def header(language):
    return (f'# Drehzyklen translation ({language})\n'
            '# SPDX-License-Identifier: GPL-2.0-or-later\n'
            'msgid ""\n'
            'msgstr ""\n'
            '"Project-Id-Version: Drehzyklen\\n"\n'
            f'"Language: {language}\\n"\n'
            '"MIME-Version: 1.0\\n"\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n'
            '"Content-Transfer-Encoding: 8bit\\n"\n')


def write(path, language, messages, translations):
    parts = [header(language)]
    for msgid, places in messages.items():
        parts.append(entry(msgid, translations.get(msgid, ""), places))
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n\n".join(parts) + "\n")


def main():
    messages = collect()
    os.makedirs(LOCALE, exist_ok=True)
    write(os.path.join(LOCALE, "drehzyklen.pot"), "", messages, {})
    for name in sorted(os.listdir(LOCALE)):
        if not name.endswith(".po"):
            continue
        path = os.path.join(LOCALE, name)
        translations = read_po(path)
        write(path, name[:-3], messages, translations)
        missing = [msgid for msgid in messages if not translations.get(msgid)]
        print(f"{name}: {len(messages) - len(missing)} of {len(messages)} translated")
    print(f"{len(messages)} texts in drehzyklen.pot")


if __name__ == "__main__":
    main()
