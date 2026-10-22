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
"""GTK3-Oberfläche – eigenständig oder als Benutzer-Tab in gmoccapy (Gtk.Plug über XID).

Aufbau (dreispaltig, Spaltenbreiten mit den Griffen verstellbar):
  oben    Auswahl: Rohteil | Werkzeuge | Plan | Längs | Konus | Gewinde | Fase/R | Kontur |
          Bohren | G-Code | (Maschine)
  links   Programm (Liste der Schritte)
  Mitte   Parameter des gewählten Reiters
  rechts  Ansicht: Zyklus | Bild | G-Code | Programm
  unten   große Tasten: Neu, Öffnen, Speichern, G-Code, Maschine, Laden
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import widgets  # noqa: E402
from .cycles import CYCLES, STOCK_PARAMS, Cycle, P, Program, Stock, cycle_from_dict  # noqa: E402
from .machine import MACHINE_FIELDS, Machine  # noqa: E402
from .material import simulate  # noqa: E402
from .settings import Settings  # noqa: E402
from .tools import HANDS, KIND_LABELS, POSITIONS, Tool, ToolTable  # noqa: E402
from .widgets import (NumEntry, ParamForm, RadioBar, ViewArea, image_button,  # noqa: E402
                      message_area, message_markup)

CSS = b"""
.dz button { min-height: 30px; padding: 2px 8px; }
.dz entry { min-height: 28px; }
.dz .selbar button { padding: 2px 6px; min-height: 40px; }
.dz .selbar button:checked { box-shadow: inset 0 -3px #e0a422; }
.dz .bottombar button { min-height: 50px; min-width: 70px; padding: 2px 8px; }
.dz .viewswitch button { min-height: 28px; padding: 0 8px; }
.dz .sidesw button { min-height: 34px; }
.dz .hint { opacity: 0.7; font-size: 90%; }
.dz .msg { font-size: 92%; }
.dz .numpad button { min-width: 54px; min-height: 46px; font-size: 16px; }
.dz.compact button { min-height: 26px; padding: 1px 5px; }
.dz.compact entry { min-height: 24px; }
.dz.compact .selbar button { min-height: 34px; }
.dz.compact .bottombar button { min-height: 40px; min-width: 56px; }
.dz.compact { font-size: 92%; }
"""

BOTTOM_SPACING = 8


def linuxcnc_running() -> bool:
    try:
        import linuxcnc
        linuxcnc.stat().poll()
        return True
    except Exception:  # noqa: BLE001 – Modul fehlt oder LinuxCNC läuft nicht
        return False


def title_label(text):
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
    return label


def scrolled(child):
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(child)
    return scroller


# =========================================================================== Zyklus-Seite (Mitte)

class CyclePage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel", cycle_class):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.cycle_class = cycle_class
        self.timer = None
        self.side = "Außen"
        self.scene = None
        self.warnings = []
        self.errors = []
        self.help = (cycle_class.KEY, cycle_class().help_values(), False)
        self.edit_index: Optional[int] = None
        if cycle_class.HAS_SIDE:
            self.side_bar = RadioBar([("Außen", "Außen", "side_out", "Außenbearbeitung"),
                                      ("Innen", "Innen", "side_in", "Innenbearbeitung")],
                                     "sidesw", self.set_side, icon_size=18)
            self.pack_start(self.side_bar, False, False, 0)
        self.title = title_label(cycle_class.TITLE)
        self.pack_start(self.title, False, False, 0)
        if cycle_class.KEY == "gcode":
            self.pack_start(self._snippet_bar(), False, False, 0)
        self.form = ParamForm(cycle_class.all_params(), panel.machine, self.on_param,
                              panel.tool_choices)
        self.pack_start(scrolled(self.form), True, True, 0)
        self.restore_cutting()
        button_box = Gtk.Box(spacing=4)
        self.add_button = Gtk.Button(label="＋ Zum Programm")
        self.add_button.set_tooltip_text("Diesen Zyklus am Ende des Programms anfügen")
        self.add_button.connect("clicked", self.on_add)
        self.apply_button = Gtk.Button(label="✔ Übernehmen")
        self.apply_button.set_tooltip_text("Änderung in den bearbeiteten Programmschritt übernehmen")
        self.apply_button.connect("clicked", self.on_apply)
        self.apply_button.set_sensitive(False)
        button_box.pack_start(self.add_button, True, True, 0)
        button_box.pack_start(self.apply_button, True, True, 0)
        self.pack_start(button_box, False, False, 0)

    # ---- Textbausteine (nur Reiter G-Code)
    def _snippet_bar(self):
        box = Gtk.Grid(row_spacing=4, column_spacing=4)
        self.snippet_combo = Gtk.ComboBoxText()
        self.snippet_combo.set_hexpand(True)
        self.snippet_combo.set_tooltip_text("Baustein wählen – ersetzt den Inhalt des Codefelds")
        self.snippet_filling = False
        self.fill_snippets()
        self.snippet_combo.connect("changed", self.on_snippet_chosen)
        insert_button = Gtk.Button(label="Einfügen")
        insert_button.set_tooltip_text("Textbaustein zusätzlich an der Schreibmarke einfügen")
        insert_button.connect("clicked", self.on_insert_snippet)
        clear_button = Gtk.Button(label="Leeren")
        clear_button.set_tooltip_text("Codefeld leeren, um einen neuen Baustein zu schreiben")
        clear_button.connect("clicked", self.on_clear_code)
        save_button = Gtk.Button(label="Speichern …")
        save_button.set_tooltip_text("Aktuellen Code als Textbaustein unter einem Namen speichern")
        save_button.connect("clicked", self.on_save_snippet)
        delete_button = Gtk.Button(label="Löschen")
        delete_button.set_tooltip_text("Gewählten Textbaustein löschen")
        delete_button.connect("clicked", self.on_delete_snippet)
        buttons = Gtk.Box(spacing=4, homogeneous=True)
        for button in (insert_button, clear_button, save_button, delete_button):
            buttons.pack_start(button, True, True, 0)
        box.attach(Gtk.Label(label="Textbaustein", xalign=0), 0, 0, 1, 1)
        box.attach(self.snippet_combo, 1, 0, 1, 1)
        box.attach(buttons, 0, 1, 2, 1)
        return box

    def fill_snippets(self, active_name=None):
        self.snippet_filling = True
        try:
            self.snippet_combo.remove_all()
            for snippet in self.panel.settings.snippets:
                self.snippet_combo.append(snippet["name"], snippet["name"])
            if active_name is not None:
                self.snippet_combo.set_active_id(active_name)
            elif self.panel.settings.snippets:
                self.snippet_combo.set_active(0)
        finally:
            self.snippet_filling = False

    def _code_buffer(self):
        return self.form.widgets["code"].get_buffer()

    def _snippet_code(self, name):
        for snippet in self.panel.settings.snippets:
            if snippet["name"] == name:
                return snippet["code"]
        return None

    def on_snippet_chosen(self, _combo):
        if self.snippet_filling:
            return
        code = self._snippet_code(self.snippet_combo.get_active_id())
        if code is not None:
            self._code_buffer().set_text(code + "\n")

    def on_clear_code(self, _button):
        self._code_buffer().set_text("")
        self.form.widgets["code"].grab_focus()

    def on_insert_snippet(self, _button):
        text = self._snippet_code(self.snippet_combo.get_active_id())
        if text is None:
            return
        buffer = self._code_buffer()
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        if cursor.get_line_offset() > 0:
            text = "\n" + text
        buffer.insert(cursor, text + "\n")

    def on_save_snippet(self, _button):
        name = self.panel.ask_text("Textbaustein speichern", "Name des Bausteins:")
        if not name:
            return
        buffer = self._code_buffer()
        code = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        self.panel.settings.add_snippet(name, code)
        self.fill_snippets(name)

    def on_delete_snippet(self, _button):
        name = self.snippet_combo.get_active_id()
        if name and self.panel.confirm(f"Textbaustein »{name}« löschen?"):
            self.panel.settings.remove_snippet(name)
            self.fill_snippets()

    # ---- zuletzt benutzte Schnittdaten (je Zyklus und Außen/Innen)
    def cutting_key(self):
        if self.cycle_class.HAS_SIDE:
            return f"{self.cycle_class.KEY}/{self.side}"
        return self.cycle_class.KEY

    def restore_cutting(self):
        stored = self.panel.settings.cutting.get(self.cutting_key())
        if not stored:
            return
        values = {}
        for key in self.cycle_class.cutting_keys():
            if key in stored:
                values[key] = stored[key]
        if values:
            self.form.set_values(values)

    def remember_cutting(self, values):
        cutting = {}
        for key in self.cycle_class.cutting_keys():
            if key in values:
                cutting[key] = values[key]
        if cutting:
            self.panel.settings.remember_cutting(self.cutting_key(), cutting)

    # ---- Bedienung
    def set_side(self, side):
        self.side = side
        self.form.set_side(side)
        self.restore_cutting()
        self.on_param("side", None)

    def on_param(self, key=None, old_value=None):
        if key and key != "side":
            values, _errors = self.form.get_values(lenient=True)
            updates = self.cycle_class.switch(key, values, old_value)
            if updates:
                self.form.set_values(updates)
        self.add_button.set_sensitive(True)
        if self.edit_index is not None:
            self.apply_button.set_sensitive(True)
        self.schedule_update()

    def schedule_update(self):
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(250, self.update)

    def build(self) -> Optional[Cycle]:
        values, errors = self.form.get_values()
        if errors:
            self.errors = errors
            self.warnings = []
            return None
        if self.cycle_class.HAS_SIDE:
            values["side"] = self.side
        return self.cycle_class(values)

    def update(self):
        self.timer = None
        values, _errors = self.form.get_values(lenient=True)
        if self.cycle_class.HAS_SIDE:
            values["side"] = self.side
        lenient_op = self.cycle_class(values)
        self.help = (self.cycle_class.KEY, lenient_op.help_values(), lenient_op.inside)
        op = self.build()
        if op is not None:
            self.remember_cutting(op.v)
        if op is None:
            self.scene = {}
        elif not op.HAS_TOOL:
            self.scene = None
            self.warnings = []
            self.errors = []
        else:
            program = self.panel.program
            if self.edit_index is not None:
                upto = self.edit_index
            else:
                upto = len(program.ops)
            before = simulate(program.stock, program.ops, upto, extra=op)
            after = before.copy().apply(op)
            single = Program(self.panel.machine, [op], stock=program.stock,
                             tools=self.panel.tools)
            result = single.generate(start_material=before)
            self.scene = dict(path=result.path, outlines=result.outlines, stock=program.stock,
                              before=before, after=after)
            self.warnings = []
            for warning in result.warnings:
                if not warning.startswith("Keine Werkzeugwechselposition"):
                    self.warnings.append(warning.replace("Op 1 ", ""))
            self.errors = [error.replace("Op 1 ", "") for error in result.errors]
        self.panel.page_updated(self)
        return False

    def load(self, op: Cycle, index: Optional[int]):
        self.edit_index = index
        if self.cycle_class.HAS_SIDE:
            side = op.v.get("side", "Außen")
            self.side_bar.buttons[side].set_active(True)
            self.side = side
            self.form.side = side
        self.form.set_values(op.v)
        self.apply_button.set_sensitive(False)
        self.add_button.set_sensitive(True)
        title = f"<b>{GLib.markup_escape_text(self.cycle_class.TITLE)}</b>"
        if index is not None:
            title += f"  <small>(Schritt {index + 1})</small>"
        self.title.set_markup(title)
        self.update()

    def clear_edit(self):
        self.edit_index = None
        self.apply_button.set_sensitive(False)
        self.title.set_markup(f"<b>{GLib.markup_escape_text(self.cycle_class.TITLE)}</b>")

    def on_add(self, _button):
        op = self.build()
        if op is None:
            self.panel.page_updated(self)
            return
        self.panel.add_op(op)
        self.add_button.set_sensitive(False)          # erst nach einer Änderung wieder aktiv

    def on_apply(self, _button):
        op = self.build()
        if op is not None and self.edit_index is not None:
            self.panel.replace_op(self.edit_index, op)
            self.apply_button.set_sensitive(False)


# =========================================================================== Rohteil-Seite (Mitte)

class StockPage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.timer = None
        self.errors = []
        self.pack_start(title_label("Rohteil und Einspannung"), False, False, 0)
        self.form = ParamForm(STOCK_PARAMS, None, self.on_param)
        self.pack_start(scrolled(self.form), True, True, 0)
        info = Gtk.Label(xalign=0, label="Nur hier wird das Rohteil eingestellt. Der Rohteil-Ø "
                                          "belegt Ø Start in Plan- und Längsdrehen vor; Konus, Fase "
                                          "und Kontur schruppen ab dem vorhandenen Material. "
                                          "Fahrten hinter die Futterfläche sind Fehler, "
                                          "Bohren/Innenbearbeitung dort nur Warnung.")
        info.set_line_wrap(True)
        info.get_style_context().add_class("hint")
        self.pack_start(info, False, False, 0)

    def on_param(self, key=None, old_value=None):
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(250, self.apply_values)

    def apply_values(self):
        self.timer = None
        values, errors = self.form.get_values()
        self.errors = errors
        if not errors:
            self.panel.stock_changed(Stock(values))
        else:
            self.panel.page_updated(self)
        return False

    def load(self, stock: Stock):
        self.form.set_values(stock.v)


# =========================================================================== Werkzeug-Seite (Mitte)

KIND_KEYS = ["C", "D", "T", "V", "W", "R", "groove", "thread", "drill", "special"]


class ToolPage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.loading = False
        self.pack_start(title_label("Werkzeuge"), False, False, 0)
        self.source_label = Gtk.Label(xalign=0)
        self.source_label.set_line_wrap(True)
        self.source_label.get_style_context().add_class("hint")
        self.pack_start(self.source_label, False, False, 0)

        self.store = Gtk.ListStore(int, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate(("T", "Typ", "Hand", "Lage", "r/b/Ø", "")):
            renderer = Gtk.CellRendererText()
            if index == 1:
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(index == 1)
            self.tree.append_column(column)
        self.tree.get_selection().connect("changed", self.on_selection_changed)
        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_shadow_type(Gtk.ShadowType.IN)
        list_scroller.set_min_content_height(150)
        list_scroller.add(self.tree)
        self.pack_start(list_scroller, True, True, 0)

        grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        self.kind_combo = Gtk.ComboBoxText()
        for key in KIND_KEYS:
            self.kind_combo.append(key, KIND_LABELS[key])
        self.kind_combo.connect("changed", self.on_kind_changed)
        self.hand_combo = Gtk.ComboBoxText()
        for hand in HANDS:
            self.hand_combo.append(hand, hand)
        self.position_combo = Gtk.ComboBoxText()
        for position in POSITIONS:
            self.position_combo.append(position, position)
        self.lead_entry = NumEntry(width=6)
        self.radius_entry = NumEntry(width=6)
        self.width_entry = NumEntry(width=6)
        self.diameter_entry = NumEntry(width=6)
        self.comment_entry = Gtk.Entry()
        rows = [("Typ", self.kind_combo), ("Hand", self.hand_combo), ("Lage", self.position_combo),
                ("Einstellwinkel κ [°]", self.lead_entry), ("Schneidenradius r", self.radius_entry),
                ("Einstechbreite b", self.width_entry), ("Bohrer-Ø", self.diameter_entry),
                ("Bezeichnung", self.comment_entry)]
        self.editor_labels = {}
        for row, (text, widget) in enumerate(rows):
            label = Gtk.Label(label=text, xalign=0)
            self.editor_labels[text] = label
            widget.set_hexpand(True)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        self.pack_start(grid, False, False, 0)

        buttons = Gtk.Box(spacing=4)
        self.apply_button = Gtk.Button(label="✔ Übernehmen")
        self.apply_button.connect("clicked", self.on_apply)
        self.new_button = Gtk.Button(label="＋ Neu")
        self.new_button.connect("clicked", self.on_new)
        self.delete_button = Gtk.Button(label="Löschen")
        self.delete_button.connect("clicked", self.on_delete)
        self.reload_button = Gtk.Button(label="⟳ Einlesen")
        self.reload_button.set_tooltip_text("Werkzeugtabelle neu einlesen (Änderungen aus LinuxCNC "
                                            "werden auch automatisch erkannt)")
        self.reload_button.connect("clicked", self.on_reload)
        for button in (self.apply_button, self.new_button, self.delete_button,
                       self.reload_button):
            buttons.pack_start(button, True, True, 0)
        self.pack_start(buttons, False, False, 0)
        self.mirror_check = Gtk.CheckButton(label="Winkel I/J der tool.tbl in Z spiegeln")
        self.mirror_check.set_tooltip_text("Falls die Grafik ein rechtes Werkzeug als linkes "
                                           "zeigt: einmal umschalten und mit einem bekannten "
                                           "Werkzeug prüfen")
        self.mirror_check.connect("toggled", self.on_mirror_toggled)
        self.pack_start(self.mirror_check, False, False, 0)
        self.refresh()

    # ---- Liste
    def refresh(self, select_number=None):
        table = self.panel.tools
        self.loading = True
        self.mirror_check.set_active(table.mirror_table_angles)
        self.loading = False
        self.mirror_check.set_visible(table.from_linuxcnc)
        self.new_button.set_sensitive(not table.from_linuxcnc)
        self.delete_button.set_sensitive(not table.from_linuxcnc)
        self.source_label.set_text(table.message)
        if select_number is None:
            select_number = self.selected_number()
        self.store.clear()
        for tool in table.sorted_tools():
            if tool.kind == "drill":
                size_text = f"Ø{tool.diameter:g}"
            elif tool.kind == "groove":
                size_text = f"b{tool.width:g}"
            else:
                size_text = f"r{tool.nose_radius:g}"
            if tool.needs_input:
                marker = "⚠"
            else:
                marker = ""
            self.store.append([tool.number, KIND_LABELS.get(tool.kind, tool.kind), tool.hand,
                               tool.position, size_text, marker])
        if select_number is not None:
            for index, row in enumerate(self.store):
                if row[0] == select_number:
                    self.tree.get_selection().select_path(Gtk.TreePath.new_from_indices([index]))

    def selected_number(self):
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        return model[tree_iter][0]

    def select_number(self, number):
        """Werkzeug in der Liste markieren und sichtbar machen."""
        for index, row in enumerate(self.store):
            if row[0] == number:
                path = Gtk.TreePath.new_from_indices([index])
                self.tree.get_selection().select_path(path)
                self.tree.scroll_to_cell(path, None, False, 0.0, 0.0)
                return True
        return False

    def selected_tool(self) -> Optional[Tool]:
        number = self.selected_number()
        if number is None:
            return None
        return self.panel.tools.get(number)

    def on_selection_changed(self, _selection):
        tool = self.selected_tool()
        self.panel.show_tool(tool)
        if tool is None:
            return
        self.loading = True
        if tool.kind in KIND_KEYS:
            self.kind_combo.set_active_id(tool.kind)
        else:
            self.kind_combo.set_active(-1)
        self.hand_combo.set_active_id(tool.hand)
        self.position_combo.set_active_id(tool.position)
        self.lead_entry.set_value(tool.lead_angle)
        self.radius_entry.set_value(tool.nose_radius)
        self.width_entry.set_value(tool.width)
        self.diameter_entry.set_value(tool.diameter)
        self.comment_entry.set_text(tool.comment)
        from_table = self.panel.tools.from_linuxcnc
        self.radius_entry.set_sensitive(not from_table)
        self.comment_entry.set_sensitive(not from_table)
        self.loading = False
        self.on_kind_changed(self.kind_combo)

    def on_kind_changed(self, _combo):
        kind = self.kind_combo.get_active_id()
        self.width_entry.set_sensitive(kind == "groove")
        self.diameter_entry.set_sensitive(kind == "drill")
        self.lead_entry.set_sensitive(kind in ("C", "D", "T", "V", "W", "special"))

    def on_apply(self, _button):
        tool = self.selected_tool()
        if tool is None:
            return
        try:
            lead = self.lead_entry.get_value()
            radius = self.radius_entry.get_value()
            width = self.width_entry.get_value()
            diameter = self.diameter_entry.get_value()
        except (ValueError, SyntaxError, ZeroDivisionError):
            self.panel.message("Bitte gültige Zahlen eingeben.", Gtk.MessageType.ERROR)
            return
        kind = self.kind_combo.get_active_id()
        hand = self.hand_combo.get_active_id()
        position = self.position_combo.get_active_id()
        if not kind or not hand or not position:
            self.panel.message("Typ, Hand und Lage auswählen.", Gtk.MessageType.ERROR)
            return
        self.panel.tools.assign(tool, kind, hand, position, lead, radius, width, diameter,
                                self.comment_entry.get_text())
        self.panel.tools_changed(tool.number)

    def on_new(self, _button):
        table = self.panel.tools
        number = table.next_number()
        table.add(Tool(number, number, "C", "rechts", "außen", 0.4, 95.0, comment="neu"))
        self.panel.tools_changed(number)

    def on_reload(self, _button):
        self.panel.reload_tools()

    def on_delete(self, _button):
        number = self.selected_number()
        if number is not None and self.panel.confirm(f"Werkzeug T{number} löschen?"):
            self.panel.tools.remove(number)
            self.panel.tools_changed(None)

    def on_mirror_toggled(self, check):
        if self.loading:
            return
        self.panel.tools.set_mirror(check.get_active())
        self.panel.tools_changed(self.selected_number())


# =========================================================================== Maschinen-Seite (Mitte)

class MachinePage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        machine = panel.machine
        self.pack_start(title_label("Maschine"), False, False, 0)
        params = []
        for key, label in MACHINE_FIELDS:
            value = getattr(machine, key)
            if isinstance(value, bool):
                kind = "bool"
            elif isinstance(value, float):
                kind = "float"
            else:
                kind = "str"
            params.append(P(key, label, value, kind))
        self.form = ParamForm(params)
        self.pack_start(scrolled(self.form), True, True, 0)
        self.info = Gtk.Label(xalign=0)
        self.info.set_line_wrap(True)
        self.info.get_style_context().add_class("hint")
        self.pack_start(self.info, False, False, 0)
        buttons = Gtk.Grid(row_spacing=4, column_spacing=4)
        actions = (("Übernehmen", panel.on_machine_apply), ("Speichern", panel.on_machine_save),
                   ("Aus INI neu laden", panel.on_machine_reload))
        for index, (label, handler) in enumerate(actions):
            button = Gtk.Button(label=label)
            button.set_hexpand(True)
            button.connect("clicked", handler)
            buttons.attach(button, index % 2, index // 2, 1, 1)
        self.pack_start(buttons, False, False, 0)
        self.refresh()

    def refresh(self):
        machine = self.panel.machine
        values = {}
        for key, _label in MACHINE_FIELDS:
            values[key] = getattr(machine, key)
        self.form.set_values(values)
        if machine.ini_used:
            source_text = f"{machine.source} ({machine.ini_used})"
        else:
            source_text = machine.source
        text = (f"Quelle: {source_text}. Drehzahl-, Achsgrenzen, Werkzeugtabelle und "
                f"Werkzeugwechselposition (G53) kommen aus der LinuxCNC-INI und überschreiben "
                f"machine.json. Werkzeugwechsel immer mit M6.")
        if machine.tc_enabled:
            text += f" G53-Wechselposition aus: {machine.tc_source or 'Maschine'}."
        self.info.set_text(text)


# =========================================================================== linke Spalte

class ProgramColumn(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.pack_start(title_label("Programm"), False, False, 0)
        self.name_entry = Gtk.Entry(text=panel.program.name)
        self.name_entry.set_width_chars(8)
        self.name_entry.set_placeholder_text("Programmname")
        self.name_entry.connect("changed", self.on_name_changed)
        self.pack_start(self.name_entry, False, False, 0)
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate(("#", "Zyklus", "T", "Daten")):
            renderer = Gtk.CellRendererText()
            if index in (1, 3):
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(index == 3)
            if index == 1:
                column.set_min_width(60)
            self.tree.append_column(column)
        self.tree.set_tooltip_column(4)
        self.tree.connect("row-activated", self.on_row_activated)
        self.tree.get_selection().connect("changed", self.on_selection_changed)
        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_shadow_type(Gtk.ShadowType.IN)
        list_scroller.add(self.tree)
        self.pack_start(list_scroller, True, True, 0)
        tools = Gtk.Grid(row_spacing=4, column_spacing=4)
        actions = (("▲", panel.on_move_up, "Nach oben"),
                   ("▼", panel.on_move_down, "Nach unten"),
                   ("✎", panel.on_edit, "Bearbeiten (auch Doppelklick)"),
                   ("⧉", panel.on_duplicate, "Kopieren"),
                   ("✖", panel.on_delete, "Löschen"))
        for index, (label, handler, tooltip) in enumerate(actions):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tooltip)
            button.connect("clicked", handler)
            button.set_hexpand(True)
            tools.attach(button, index, 0, 1, 1)
        self.pack_start(tools, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(28)
        self.status.get_style_context().add_class("hint")
        self.pack_start(self.status, False, False, 0)

    def on_name_changed(self, _entry):
        self.panel.schedule_refresh()

    def on_row_activated(self, *_args):
        self.panel.on_edit(None)

    def on_selection_changed(self, _selection):
        self.panel.on_select()

    def selected(self) -> Optional[int]:
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        return model.get_path(tree_iter).get_indices()[0]


# =========================================================================== rechte Spalte

class ViewColumn(Gtk.Box):
    MODES = [("cycle", "Zyklus", "Vorschau des aktuellen Reiters mit Rohteil und Abtrag; "
                                 "Mausrad zoomt, linke Maustaste verschiebt, Doppelklick setzt zurück"),
             ("help", "Bild", "Parameterbild des aktuellen Reiters"),
             ("gcode", "G-Code", "Erzeugter G-Code, springt zum markierten Programmschritt"),
             ("program", "Programm", "Ganzes Programm, markierter Schritt hervorgehoben")]

    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.mode = "cycle"
        top = Gtk.Box(spacing=6)
        items = [(key, text, None, tooltip) for key, text, tooltip in self.MODES]
        self.mode_bar = RadioBar(items, "viewswitch", self.on_mode)
        top.pack_start(self.mode_bar, False, False, 0)
        self.rapid_check = Gtk.CheckButton(label="Eilgänge", active=True)
        self.rapid_check.connect("toggled", self.on_rapids_toggled)
        top.pack_end(self.rapid_check, False, False, 0)
        self.pack_start(top, False, False, 0)
        self.stack = Gtk.Stack()
        self.view = ViewArea(panel.machine)
        self.stack.add_named(self.view, "view")
        self.gcode_view = Gtk.TextView(editable=False, monospace=True)
        self.gcode_view.get_buffer().create_tag("current", background="#5a4a10",
                                                foreground="#ffffff")
        self.gcode_scroller = Gtk.ScrolledWindow()
        self.gcode_scroller.add(self.gcode_view)
        self.stack.add_named(self.gcode_scroller, "gcode")
        self.pack_start(self.stack, True, True, 0)
        message_scroller, self.message_label = message_area()
        self.pack_start(message_scroller, False, True, 0)

    def set_mode(self, mode):
        self.mode_bar.buttons[mode].set_active(True)

    def on_mode(self, mode):
        self.mode = mode
        if mode == "gcode":
            self.stack.set_visible_child_name("gcode")
        else:
            self.stack.set_visible_child_name("view")
        self.panel.update_view()

    def on_rapids_toggled(self, check):
        self.view.show_rapids = check.get_active()
        self.view.queue_draw()

    def show_gcode(self, text, op_number=None):
        buffer = self.gcode_view.get_buffer()
        buffer.set_text(text)
        if op_number is None:
            return
        marker = f"(--- {op_number}: "
        for line_number, line in enumerate(text.splitlines()):
            if line.startswith(marker):
                start = buffer.get_iter_at_line(line_number)
                end = buffer.get_iter_at_line(line_number + 1)
                buffer.apply_tag_by_name("current", start, end)
                GLib.idle_add(self.scroll_to_line, line_number)
                break

    def scroll_to_line(self, line_number):
        buffer = self.gcode_view.get_buffer()
        target = buffer.get_iter_at_line(line_number)
        self.gcode_view.scroll_to_iter(target, 0.0, True, 0.0, 0.1)
        return False


# =========================================================================== Hauptpanel

class DrehzyklenPanel(Gtk.Box):
    def __init__(self, machine: Machine, machine_json: str, ini_path: Optional[str] = None,
                 embedded=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.get_style_context().add_class("dz")
        self.machine = machine
        self.machine_json = machine_json
        self.ini_path = ini_path
        self.embedded = embedded
        config_dir = os.path.dirname(os.path.abspath(machine_json))
        self.settings = Settings(os.path.join(config_dir, "settings.json"))
        self.tools = ToolTable(machine, os.path.join(config_dir, "werkzeuge.json"))
        widgets.ICON_STATE["flipped"] = self.icons_flipped
        self.program = Program(machine, tools=self.tools)
        self.current = "stock"
        self.refresh_timer = None
        self.layout_state = None
        self.positioned = False
        self.starting = True
        self.program_result = None
        self.active_dialog = None
        self.set_size_request(300, 200)

        # ---- Auswahlleiste - Hier werden die Reiter in der Anordnung festgelegt
        items = [("stock", "Rohteil", "stock", "Rohteil und Einspannung")]
        for cycle_class in CYCLES:
            items.append((cycle_class.KEY, cycle_class.SHORT, cycle_class.KEY, cycle_class.TITLE))
        items.append(("tools", "Werkzeuge", "tools", "Werkzeugtabelle"))
        items.append(("machine", "Maschine", "machine", "Maschinendaten"))
        self.selector = RadioBar(items, "selbar", self.show_page, icon_size=22)
        selector_scroller = Gtk.ScrolledWindow()
        selector_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        selector_scroller.set_propagate_natural_height(True)
        selector_scroller.add(self.selector)
        self.pack_start(selector_scroller, False, False, 0)

        # ---- drei Spalten
        self.program_column = ProgramColumn(self)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stock_page = StockPage(self)
        self.stack.add_named(self.stock_page, "stock")
        self.tool_page = ToolPage(self)
        self.stack.add_named(self.tool_page, "tools")
        self.pages: Dict[str, CyclePage] = {}
        for cycle_class in CYCLES:
            page = CyclePage(self, cycle_class)
            self.pages[cycle_class.KEY] = page
            self.stack.add_named(page, cycle_class.KEY)
        self.machine_page = MachinePage(self)
        self.stack.add_named(self.machine_page, "machine")
        self.view_column = ViewColumn(self)

        self.outer_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.inner_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.outer_paned.set_wide_handle(True)
        self.inner_paned.set_wide_handle(True)
        self.outer_paned.pack1(self.program_column, False, False)
        self.outer_paned.pack2(self.inner_paned, True, False)
        self.inner_paned.pack1(self.stack, False, False)
        self.inner_paned.pack2(self.view_column, True, False)
        self.pack_start(self.outer_paned, True, True, 0)

        # ---- untere Tastenleiste
        bar = Gtk.Box(spacing=BOTTOM_SPACING)
        bar.get_style_context().add_class("bottombar")
        self.bottom_buttons = []
        self._bottom_button(bar, "Neu", "document-new", self.on_new,
                            "Neues Programm (Rohteil bleibt)")
        self._bottom_button(bar, "Öffnen", "document-open", self.on_open, "Projekt (.dzp) öffnen")
        self._bottom_button(bar, "Speichern", "document-save", self.on_save_project,
                            "Projekt (.dzp) speichern")
        self._bottom_button(bar, "G-Code", "accessories-text-editor", self.on_gcode_editor,
                            "G-Code anzeigen, ergänzen und speichern")
        self.send_button = self._bottom_button(bar, "Laden", "media-playback-start", self.on_send,
                                               "G-Code erzeugen, speichern und in LinuxCNC öffnen",
                                               at_end=True)
        self.machine_button = self._bottom_button(bar, "Maschine", "preferences-system",
                                                  self.on_toggle_machine,
                                                  "Reiter Maschine ein-/ausblenden", toggle=True,
                                                  at_end=True)
        self.pack_start(bar, False, False, 0)

        running = linuxcnc_running()
        self.machine_button.set_active(not running)
        self.selector.buttons["machine"].set_visible(not running)
        self.stock_page.load(self.program.stock)
        for page in self.pages.values():
            page.form.set_stock(self.program.stock, force=True)
        self.starting = False
        GLib.timeout_add_seconds(2, self.watch_tool_table)
        self.connect("size-allocate", self.on_size_allocate)
        self.refresh_list()

    def _bottom_button(self, bar, text, icon, handler, tooltip, toggle=False, at_end=False):
        button = image_button(text, icon, tooltip, toggle)
        if toggle:
            button.connect("toggled", handler)
        else:
            button.connect("clicked", handler)
        if at_end:
            bar.pack_end(button, False, False, 0)
        else:
            bar.pack_start(button, False, False, 0)
        self.bottom_buttons.append(button)
        return button

    def icons_flipped(self) -> bool:
        return not self.machine.back_tool

    def tool_choices(self):
        choices = []
        for tool in self.tools.sorted_tools():
            choices.append((tool.number, tool.label()))
        return choices

    # ---------------------------------------------------------------- Layout
    def on_size_allocate(self, _widget, allocation):
        layout = (allocation.width < 760, allocation.width < 920 or allocation.height < 560)
        if not self.positioned and allocation.width > 50:
            self.positioned = True
            left = int(min(300, max(170, 0.22 * allocation.width)))
            middle = int(min(380, max(250, 0.32 * allocation.width)))
            GLib.idle_add(self.set_positions, left, middle)
        if layout != self.layout_state:
            self.layout_state = layout
            GLib.idle_add(self.apply_layout, layout)

    def set_positions(self, left, middle):
        self.outer_paned.set_position(left)
        self.inner_paned.set_position(middle)
        return False

    def apply_layout(self, layout):
        narrow, compact = layout
        context = self.get_style_context()
        if compact:
            context.add_class("compact")
        else:
            context.remove_class("compact")
        for button in list(self.selector.buttons.values()) + self.bottom_buttons:
            button.text_label.set_visible(not narrow)
        return False

    def show_startup_hints(self):
        """Werkzeuge ohne Angaben: Reiter Werkzeuge öffnen und darauf hinweisen."""
        pending = self.tools.pending()
        if pending:
            self.selector.buttons["tools"].set_active(True)
            numbers = ", ".join(f"T{tool.number}" for tool in pending)
            self.view_column.message_label.set_markup(message_markup(
                [f"Bei {numbers} lassen sich Typ und Schnittrichtung nicht aus der tool.tbl "
                 f"ableiten – bitte Typ, Hand und Lage angeben und übernehmen."], []))
        return False

    # ---------------------------------------------------------------- Seiten / Ansicht
    def show_page(self, key):
        previous_page = self.pages.get(self.current)
        if key == "tools" and previous_page is not None and previous_page.cycle_class.HAS_TOOL:
            # Werkzeug des zuletzt bearbeiteten Zyklus im Werkzeugreiter zeigen
            values, _errors = previous_page.form.get_values(lenient=True)
            tool_number = values.get("tool")
            if tool_number:
                self.tool_page.select_number(int(tool_number))
        self.current = key
        self.stack.set_visible_child_name(key)
        self.view_column.view.reset_view()
        page = self.pages.get(key)
        if page is not None:
            page.update()
        else:
            self.update_view()

    def page_updated(self, page):
        if self.stack.get_visible_child() is page:
            self.update_view()

    def show_tool(self, tool):
        self.view_column.view.set_tool(tool)
        if self.current == "tools":
            self.update_view()

    def update_view(self):
        column = self.view_column
        mode = column.mode
        view = column.view
        page = self.pages.get(self.current)
        result = self.program_result
        selected = self.program_column.selected()
        if mode == "gcode":
            if result is not None:
                if selected is not None:
                    column.show_gcode(result.gcode, selected + 1)
                else:
                    column.show_gcode(result.gcode)
        if mode in ("program", "gcode") or (mode == "cycle" and self.current == "machine"):
            view.set_mode("preview")
            self.show_program_scene()
            if result is not None:
                column.message_label.set_markup(message_markup(result.warnings, result.errors,
                                                               "✔ Keine Meldungen"))
            return
        if self.current == "tools":
            view.set_mode("tool")
            column.message_label.set_text(self.tools.message)
            return
        if mode == "help":
            view.set_mode("help")
        else:
            view.set_mode("preview")
        if self.current == "stock":
            stock = self.program.stock
            view.set_help("stock", stock.to_dict(), False)
            view.set_scene(dict(stock=stock, after=simulate(stock, [], None)))
            column.message_label.set_markup(message_markup([], self.stock_page.errors,
                                                           "✔ " + stock.summary()))
        elif page is not None:
            view.set_help(*page.help)
            if page.scene is None:
                self.show_program_scene()
            else:
                view.set_scene(page.scene)
            column.message_label.set_markup(message_markup(page.warnings, page.errors))
        else:
            view.set_help(None, {}, False)
            view.set_scene({})
            column.message_label.set_text("")

    def show_program_scene(self):
        result = self.program_result
        if result is None:
            return
        stock = self.program.stock
        selected = self.program_column.selected()
        if selected is not None:
            highlight = selected + 1
        else:
            highlight = None
        self.view_column.view.set_scene(dict(
            path=result.path, outlines=result.outlines, stock=stock,
            before=simulate(stock, self.program.ops, 0), after=simulate(stock, self.program.ops),
            highlight=highlight, removed_color=(0.50, 0.53, 0.58, 0.16)))

    def on_toggle_machine(self, button):
        visible = button.get_active()
        self.selector.buttons["machine"].set_visible(visible)
        if self.starting:
            return
        if visible:
            self.selector.buttons["machine"].set_active(True)
        elif self.current == "machine":
            self.selector.buttons["stock"].set_active(True)

    # ---------------------------------------------------------------- Rohteil / Werkzeuge
    def stock_changed(self, stock: Stock):
        old_stock = self.program.stock
        self.program.stock = stock
        for page in self.pages.values():
            page.form.set_stock(stock, old_stock)
            page.schedule_update()
        self.refresh_gcode()

    def reload_tools(self):
        selected = self.tool_page.selected_number()
        self.tools.load()
        self.tools_changed(selected)

    def watch_tool_table(self):
        """Alle 2 s prüfen, ob LinuxCNC die tool.tbl geändert hat, und dann neu einlesen."""
        if self.tools.from_linuxcnc or self.tools.table_path():
            if self.tools.changed_on_disk():
                self.reload_tools()
                self.program_column.status.set_text("Werkzeugtabelle wurde geändert – neu "
                                                    "eingelesen")
        return True

    def tools_changed(self, select_number):
        self.tool_page.refresh(select_number)
        for page in self.pages.values():
            page.form.refresh_tools()
            page.schedule_update()
        self.show_tool(self.tool_page.selected_tool())
        self.refresh_gcode()

    # ---------------------------------------------------------------- Programmliste
    def refresh_list(self, select: Optional[int] = None):
        column = self.program_column
        column.store.clear()
        for index, op in enumerate(self.program.ops, 1):
            if op.HAS_TOOL:
                tool_text = str(int(op.tool))
            else:
                tool_text = "–"
            title = op.TITLE
            if op.HAS_SIDE and op.inside:
                title += " innen"
            column.store.append([str(index), title, tool_text, op.summary(),
                                 GLib.markup_escape_text(f"{op.TITLE}: {op.summary()}")])
        if select is not None and 0 <= select < len(self.program.ops):
            column.tree.get_selection().select_path(Gtk.TreePath.new_from_indices([select]))
        self.refresh_gcode()

    def on_select(self):
        if self.view_column.mode in ("program", "gcode"):
            self.update_view()

    def add_op(self, op: Cycle):
        self.program.ops.append(op)
        self.refresh_list(len(self.program.ops) - 1)
        self.program_column.status.set_text(f"{op.TITLE} als Schritt {len(self.program.ops)} "
                                            f"angefügt")

    def replace_op(self, index: int, op: Cycle):
        if index >= len(self.program.ops):
            self.add_op(op)
            return
        self.program.ops[index] = op
        self.refresh_list(index)
        self.program_column.status.set_text(f"Schritt {index + 1} geändert")

    def _move(self, offset):
        index = self.program_column.selected()
        if index is None:
            return
        target = index + offset
        if 0 <= target < len(self.program.ops):
            ops = self.program.ops
            ops[index], ops[target] = ops[target], ops[index]
            self.clear_edit()
            self.refresh_list(target)

    def on_move_up(self, _button):
        self._move(-1)

    def on_move_down(self, _button):
        self._move(1)

    def clear_edit(self):
        for page in self.pages.values():
            if page.edit_index is not None:
                page.clear_edit()

    def on_edit(self, _button):
        index = self.program_column.selected()
        if index is None:
            return
        op = self.program.ops[index]
        self.clear_edit()
        page = self.pages[op.KEY]
        self.selector.buttons[op.KEY].set_active(True)
        page.load(op, index)
        if self.view_column.mode in ("program", "gcode"):
            self.view_column.set_mode("cycle")

    def on_duplicate(self, _button):
        index = self.program_column.selected()
        if index is not None:
            self.program.ops.insert(index + 1, cycle_from_dict(self.program.ops[index].to_dict()))
            self.clear_edit()
            self.refresh_list(index + 1)

    def on_delete(self, _button):
        index = self.program_column.selected()
        if index is not None:
            del self.program.ops[index]
            self.clear_edit()
            self.refresh_list(min(index, len(self.program.ops) - 1))

    # ---------------------------------------------------------------- G-Code
    def schedule_refresh(self):
        if self.refresh_timer:
            GLib.source_remove(self.refresh_timer)
        self.refresh_timer = GLib.timeout_add(400, self.timed_refresh)

    def timed_refresh(self):
        self.refresh_timer = None
        self.refresh_gcode()
        return False

    def generate(self):
        name = self.program_column.name_entry.get_text().strip()
        if not name:
            name = "Drehteil"
        self.program.name = name
        self.program.tools = self.tools
        return self.program.generate()

    def refresh_gcode(self):
        result = self.generate()
        self.program_result = result
        count = len(self.program.ops)
        tool_numbers = set()
        for op in self.program.ops:
            if op.HAS_TOOL:
                tool_numbers.add(int(op.tool))
        text = f"{count} Schritt(e), {len(tool_numbers)} Werkzeug(e)"
        if result.errors:
            text += f" · {len(result.errors)} Fehler"
        self.program_column.status.set_text(text)
        self.update_view()
        return result

    def gcode_ok(self, result) -> bool:
        if not self.program.ops:
            self.message("Das Programm enthält noch keine Zyklen.")
            return False
        if result.errors:
            self.message("Das Programm enthält Fehler:\n\n" + "\n".join(result.errors),
                         Gtk.MessageType.ERROR)
            return False
        return True

    def file_name(self):
        characters = []
        for character in self.program.name:
            if character.isalnum() or character in "-_":
                characters.append(character)
            else:
                characters.append("_")
        name = "".join(characters)
        if not name:
            name = "drehteil"
        return name + ".ngc"

    def program_folder(self):
        return self.settings.program_dir(self.machine.program_dir)

    def on_gcode_editor(self, _button):
        """Erzeugten G-Code anzeigen, bei Bedarf ergänzen, dann speichern oder laden."""
        result = self.refresh_gcode()
        if not self.gcode_ok(result):
            return
        dialog = Gtk.Dialog(title="G-Code – anzeigen und ergänzen",
                            transient_for=self.get_toplevel(), modal=True)
        dialog.add_buttons("Schließen", Gtk.ResponseType.CANCEL, "Speichern unter …", 1,
                           "In LinuxCNC laden", 2)
        dialog.set_default_size(720, 520)
        running = linuxcnc_running()
        dialog.set_response_sensitive(2, running)
        if not running:
            load_button = dialog.get_widget_for_response(2)
            if load_button is not None:
                load_button.set_tooltip_text("LinuxCNC läuft nicht")
        content = dialog.get_content_area()
        info = Gtk.Label(xalign=0, label="Ergänzungen hier gelten nur für diese Datei. Code, der "
                                         "dauerhaft zwischen zwei Zyklen stehen soll, gehört in "
                                         "einen Schritt »G-Code« (mit Textbausteinen).")
        info.set_line_wrap(True)
        content.pack_start(info, False, False, 4)
        text_view = Gtk.TextView(monospace=True)
        text_view.get_buffer().set_text(result.gcode)
        scroller = Gtk.ScrolledWindow()
        scroller.add(text_view)
        content.pack_start(scroller, True, True, 0)
        dialog.show_all()
        self.active_dialog = dialog
        while True:
            response = dialog.run()
            buffer = text_view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            if response == 1:
                path = self.file_dialog("G-Code speichern", Gtk.FileChooserAction.SAVE,
                                        self.file_name(), ("G-Code", "*.ngc"))
                if path and self.write_file(path, text):
                    break
            elif response == 2:
                if self.send_text(text, result.warnings):
                    break
            else:
                break
        self.active_dialog = None
        dialog.destroy()

    def on_send(self, _button):
        if not linuxcnc_running():
            self.message("LinuxCNC läuft nicht – das Programm kann nicht geladen werden.\n"
                         "Zum Speichern bitte »G-Code« → »Speichern unter …« verwenden.",
                         Gtk.MessageType.WARNING)
            return
        result = self.refresh_gcode()
        if self.gcode_ok(result):
            self.send_text(result.gcode, result.warnings)

    def send_text(self, text, warnings) -> bool:
        path = os.path.join(self.program_folder(), self.file_name())
        if not self.write_file(path, text):
            return False
        try:
            import linuxcnc
        except ImportError:
            self.message(f"Gespeichert unter\n{path}\n\nLinuxCNC-Python-Modul nicht gefunden – "
                         f"Datei bitte manuell öffnen.")
            return True
        try:
            status = linuxcnc.stat()
            command = linuxcnc.command()
            status.poll()
            if status.interp_state != linuxcnc.INTERP_IDLE:
                self.message("LinuxCNC arbeitet gerade ein Programm ab – Laden nicht möglich.",
                             Gtk.MessageType.WARNING)
                return False
            if status.task_mode != linuxcnc.MODE_AUTO:
                command.mode(linuxcnc.MODE_AUTO)
                command.wait_complete()
            command.program_open(path)
            self.program_column.status.set_text(f"In LinuxCNC geladen: {path}")
            if warnings:
                self.message("Programm geladen. Bitte die Hinweise in der Ansicht »Programm« "
                             "beachten.", Gtk.MessageType.WARNING)
            return True
        except linuxcnc.error as error:
            self.message(f"LinuxCNC-Fehler: {error}\nDatei liegt unter {path}",
                         Gtk.MessageType.ERROR)
            return False

    def write_file(self, path, text) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
        except OSError as error:
            self.message(f"Speichern fehlgeschlagen: {error}", Gtk.MessageType.ERROR)
            return False
        self.settings.remember_dir(path)
        self.program_column.status.set_text(f"Gespeichert: {path}")
        return True

    # ---------------------------------------------------------------- Projekt
    def on_new(self, _button):
        if self.program.ops and not self.confirm("Aktuelles Programm verwerfen? (Rohteil bleibt)"):
            return
        self.program.ops.clear()
        self.clear_edit()
        self.refresh_list()
        # Rohteil bleibt – zur Kontrolle gleich den Reiter Rohteil zeigen
        self.selector.buttons["stock"].set_active(True)

    def load_project(self, path):
        with open(path, encoding="utf-8") as file:
            program = Program.from_json(file.read(), self.machine, self.tools)
        old_stock = self.program.stock
        self.program = program
        self.program_column.name_entry.set_text(program.name)
        self.stock_page.load(program.stock)
        for page in self.pages.values():
            page.form.set_stock(program.stock, old_stock)
        self.clear_edit()
        self.refresh_list(0)

    def on_open(self, _button):
        path = self.file_dialog("Projekt öffnen", Gtk.FileChooserAction.OPEN, None,
                                ("Drehzyklen-Projekt", "*.dzp"))
        if not path:
            return
        try:
            self.load_project(path)
            self.settings.remember_dir(path)
        except (OSError, ValueError, KeyError) as error:
            self.message(f"Projekt kann nicht gelesen werden: {error}", Gtk.MessageType.ERROR)

    def on_save_project(self, _button):
        name = self.program_column.name_entry.get_text().strip()
        if not name:
            name = "Drehteil"
        self.program.name = name
        path = self.file_dialog("Projekt speichern", Gtk.FileChooserAction.SAVE,
                                self.file_name().replace(".ngc", ".dzp"),
                                ("Drehzyklen-Projekt", "*.dzp"))
        if path:
            if not path.endswith(".dzp"):
                path += ".dzp"
            self.write_file(path, self.program.to_json())

    # ---------------------------------------------------------------- Maschine
    def machine_updated(self, old_values):
        for page in self.pages.values():
            page.schedule_update()
        self.tools.load()
        self.tool_page.refresh()
        self.selector.queue_draw()
        self.refresh_gcode()

    def on_machine_apply(self, _button):
        values, errors = self.machine_page.form.get_values()
        if errors:
            self.message("\n".join(errors), Gtk.MessageType.ERROR)
            return False
        folder = os.path.expanduser(str(values.get("program_dir", "")).strip())
        if not folder:
            self.message("Bitte einen Programmordner angeben.", Gtk.MessageType.ERROR)
            return False
        if not os.path.isdir(folder):
            if not self.confirm(f"Der Programmordner\n{folder}\nexistiert nicht. Jetzt anlegen?"):
                self.message("Programmordner nicht übernommen – bitte einen vorhandenen Ordner "
                             "angeben.", Gtk.MessageType.WARNING)
                return False
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as error:
                self.message(f"Ordner kann nicht angelegt werden: {error}",
                             Gtk.MessageType.ERROR)
                return False
        ini_text = str(values.get("ini_file", "")).strip()
        ini_path = os.path.expanduser(ini_text)
        if ini_text and not os.path.isfile(ini_path):
            self.message(f"Die INI-Datei\n{ini_path}\nexistiert nicht.", Gtk.MessageType.ERROR)
            return False
        old_values = dict(self.machine.__dict__)
        self.machine.update_from_dict(values)
        if ini_text != old_values.get("ini_file", ""):
            self.use_ini_file(ini_path, old_values)
        if self.machine.tc_enabled and not self.machine.tc_source:
            self.machine.tc_source = "Reiter Maschine"
        self.settings.last_dir = ""
        self.settings.save()
        self.machine_updated(old_values)
        return True

    def use_ini_file(self, ini_path, old_values):
        """Im Reiter Maschine eingetragene INI sofort lesen (Werkzeugtabelle, Grenzen, G53)."""
        if self.ini_path or os.environ.get("INI_FILE_NAME"):
            self.message("LinuxCNC hat beim Start bereits eine INI übergeben – die eingetragene "
                         "INI wird nur ohne laufendes LinuxCNC benutzt.", Gtk.MessageType.INFO)
            return
        if ini_path and self.machine.update_from_ini(ini_path):
            self.machine.ini_loaded = True
            self.machine.ini_used = os.path.abspath(ini_path)
            self.machine.source = (f"{os.path.basename(self.machine_json)} + "
                                   f"{os.path.basename(ini_path)}")
        elif not ini_path and old_values.get("ini_used"):
            self.machine.ini_loaded = False
            self.machine.ini_used = ""
            self.machine.tool_table = ""
            self.machine.source = os.path.basename(self.machine_json)
        self.machine_page.refresh()

    def on_machine_save(self, button):
        if self.on_machine_apply(button):
            try:
                os.makedirs(os.path.dirname(self.machine_json) or ".", exist_ok=True)
                self.machine.save(self.machine_json)
                self.program_column.status.set_text(f"Maschinendaten gespeichert: "
                                                    f"{self.machine_json}")
            except OSError as error:
                self.message(f"Speichern fehlgeschlagen: {error}", Gtk.MessageType.ERROR)

    def on_machine_reload(self, _button):
        old_values = dict(self.machine.__dict__)
        reloaded = Machine.load(self.machine_json, self.ini_path)
        self.machine.__dict__.update(reloaded.__dict__)
        self.machine_page.refresh()
        self.machine_updated(old_values)

    # ---------------------------------------------------------------- Dialoge
    def dialog_parent(self):
        """Fenster, über dem Meldungen erscheinen: ein offener Dialog, sonst das Hauptfenster."""
        if self.active_dialog is not None:
            return self.active_dialog
        return self.get_toplevel()

    def message(self, text, kind=Gtk.MessageType.INFO):
        dialog = Gtk.MessageDialog(transient_for=self.dialog_parent(), flags=0, message_type=kind,
                                   buttons=Gtk.ButtonsType.OK, text=text)
        dialog.run()
        dialog.destroy()

    def confirm(self, text) -> bool:
        dialog = Gtk.MessageDialog(transient_for=self.dialog_parent(), flags=0,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.YES_NO, text=text)
        answer = dialog.run() == Gtk.ResponseType.YES
        dialog.destroy()
        return answer

    def ask_text(self, title, prompt) -> Optional[str]:
        dialog = Gtk.Dialog(title=title, transient_for=self.dialog_parent(), modal=True)
        dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.pack_start(Gtk.Label(label=prompt, xalign=0), False, False, 4)
        entry = Gtk.Entry()
        entry.set_activates_default(True)
        content.pack_start(entry, False, False, 4)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        answer = None
        if dialog.run() == Gtk.ResponseType.OK:
            answer = entry.get_text().strip()
        dialog.destroy()
        return answer

    def file_dialog(self, title, action, name, file_filter):
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self.dialog_parent(),
                                       action=action)
        if action == Gtk.FileChooserAction.SAVE:
            confirm_text = "Speichern"
        else:
            confirm_text = "Öffnen"
        dialog.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, confirm_text, Gtk.ResponseType.OK)
        dialog.set_default_size(700, 450)
        if action == Gtk.FileChooserAction.SAVE:
            dialog.set_do_overwrite_confirmation(True)
            if name:
                dialog.set_current_name(name)
        folder = self.program_folder()
        if os.path.isdir(folder):
            dialog.set_current_folder(folder)
        filter_object = Gtk.FileFilter()
        filter_object.set_name(file_filter[0])
        filter_object.add_pattern(file_filter[1])
        dialog.add_filter(filter_object)
        path = None
        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
        dialog.destroy()
        return path


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(screen, provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def run(machine: Machine, machine_json: str, ini_path: Optional[str], xid: Optional[int],
        project: Optional[str] = None, machine_tab: bool = False):
    install_css()
    panel = DrehzyklenPanel(machine, machine_json, ini_path, embedded=xid is not None)
    if project:
        try:
            panel.load_project(project)
        except (OSError, ValueError, KeyError) as error:
            print(f"Projekt {project} nicht lesbar: {error}")
    if xid:
        window = Gtk.Plug.new(xid)
    else:
        window = Gtk.Window(title="Drehzyklen – LinuxCNC")
        window.set_default_size(1100, 680)
    window.add(panel)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    if machine_tab:
        panel.starting = True
        panel.machine_button.set_active(True)
        panel.starting = False
    panel.selector.buttons["machine"].set_visible(panel.machine_button.get_active())
    for page in panel.pages.values():
        page.form.update_labels()
    panel.stock_page.form.update_labels()
    panel.tool_page.on_kind_changed(None)
    panel.refresh_gcode()
    panel.update_view()
    GLib.idle_add(panel.show_startup_hints)
    Gtk.main()
