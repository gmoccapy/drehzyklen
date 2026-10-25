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
"""GTK3 user interface - stand-alone or as a user tab in gmoccapy (Gtk.Plug via XID).

Layout (three columns, widths adjustable with the handles):
  top     tabs: Stock | Facing | Turning | Taper | Thread | Chamfer | Contour | Drilling |
          G-code | Tools | (Machine)
  left    program (list of steps)
  middle  parameters of the selected tab
  right   view: Cycle | Picture | G-code | Program
  bottom  large buttons: New, Open, Save, G-code, language, Machine, Load

Switching the language rebuilds the panel (``App.rebuild``); the program, the stock, the tool
table and the values in all forms are carried over.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import i18n, threads, widgets  # noqa: E402
from .cycles import (CYCLES, INSIDE, OUTSIDE, STOCK_PARAMS, Cycle, P, Program, Stock,  # noqa: E402
                     cycle_from_dict)
from .cycles.program import NO_TOOL_CHANGE_WARNING  # noqa: E402
from .i18n import N_, _  # noqa: E402
from .machine import MACHINE_FIELDS, MACHINE_HELP, Machine  # noqa: E402
from .material import simulate  # noqa: E402
from .settings import Settings  # noqa: E402
from .tools import (HAND_LABELS, HANDS, KIND_LABELS, NEW_SOURCE, POSITION_LABELS,  # noqa: E402
                    NEUTRAL, POSITIONS, RIGHT, Tool, ToolTable, table_angles)
from .widgets import (NumEntry, ParamForm, RadioBar, ViewArea, image_button,  # noqa: E402
                      message_area, message_markup, short_combo)

CSS = b"""
.dz button { min-height: 30px; padding: 2px 8px; }
.dz entry { min-height: 28px; }
.dz .bottombar button, .dz .buttonbar button { min-height: 50px; min-width: 70px;
                                                padding: 2px 8px; }
.dz .buttonbar button:checked { box-shadow: inset 0 -3px #e0a422; }
.dz .sidesw button, .dz .viewbar button { min-width: 56px; padding: 2px 4px; }
.dz .selbar button { min-width: 64px; padding: 2px 6px; }
.dz .viewswitch button { min-height: 28px; padding: 0 8px; }
.dz .hint { opacity: 0.7; font-size: 90%; }
.dz frame.column > border { border-radius: 6px; }
.dz frame.column { margin: 2px; }
.dz .msg { font-size: 92%; }
.dz .numpad button { min-width: 54px; min-height: 46px; font-size: 16px; }
.dz.compact button { min-height: 26px; padding: 1px 5px; }
.dz.compact entry { min-height: 24px; }
.dz.compact .bottombar button, .dz.compact .buttonbar button { min-height: 40px;
                                                              min-width: 56px; }
.dz.compact { font-size: 92%; }
"""

BOTTOM_SPACING = 8
COLUMN_SPACING = 4          # button bars inside the columns (outside/inside, views)
DEFAULT_NAME = "Part"


def linuxcnc_running() -> bool:
    try:
        import linuxcnc
        linuxcnc.stat().poll()
        return True
    except Exception:  # noqa: BLE001 - module missing or LinuxCNC not running
        return False


def title_label(text):
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
    return label


def column_frame(child):
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.IN)
    frame.get_style_context().add_class("column")
    frame.add(child)
    return frame


def scrolled(child):
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(child)
    return scroller


# =========================================================================== cycle page (middle)

class CyclePage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel", cycle_class):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.cycle_class = cycle_class
        self.timer = None
        self.side = OUTSIDE
        self.scene = None
        self.warnings = []
        self.errors = []
        self.help = (cycle_class.KEY, cycle_class().help_values(), False)
        self.edit_index: Optional[int] = None
        if cycle_class.HAS_SIDE:
            self.side_bar = RadioBar([(OUTSIDE, _("Outside"), "side_out", _("Outside machining")),
                                      (INSIDE, _("Inside"), "side_in", _("Inside machining"))],
                                     "sidesw", self.set_side, icon_size=24, buttons=True,
                                     spacing=COLUMN_SPACING)
            self.pack_start(self.side_bar, False, False, 0)
        self.title = title_label(cycle_class.title())
        self.pack_start(self.title, False, False, 0)
        if cycle_class.KEY == "gcode":
            self.pack_start(self._snippet_bar(), False, False, 0)
        self.form = ParamForm(cycle_class.all_params(), panel.machine, self.on_param,
                              panel.tool_choices)
        self.pack_start(scrolled(self.form), True, True, 0)
        self.restore_cutting()
        button_box = Gtk.Box(spacing=4)
        self.add_button = Gtk.Button(label=_("＋ Add to program"))
        self.add_button.set_tooltip_text(_("Append this cycle at the end of the program"))
        self.add_button.connect("clicked", self.on_add)
        self.apply_button = Gtk.Button(label=_("✔ Apply"))
        self.apply_button.set_tooltip_text(_("Take the change over into the edited program step"))
        self.apply_button.connect("clicked", self.on_apply)
        self.apply_button.set_sensitive(False)
        button_box.pack_start(self.add_button, True, True, 0)
        button_box.pack_start(self.apply_button, True, True, 0)
        self.pack_start(button_box, False, False, 0)

    # ---- snippets (G-code tab only)
    def _snippet_bar(self):
        box = Gtk.Grid(row_spacing=4, column_spacing=4)
        self.snippet_combo = short_combo()
        self.snippet_combo.set_tooltip_text(_("Choose a snippet - replaces the content of the "
                                              "code field"))
        self.snippet_filling = False
        self.fill_snippets()
        self.snippet_combo.connect("changed", self.on_snippet_chosen)
        insert_button = Gtk.Button(label=_("Insert"))
        insert_button.set_tooltip_text(_("Insert the snippet additionally at the cursor"))
        insert_button.connect("clicked", self.on_insert_snippet)
        clear_button = Gtk.Button(label=_("Clear"))
        clear_button.set_tooltip_text(_("Empty the code field to write a new snippet"))
        clear_button.connect("clicked", self.on_clear_code)
        save_button = Gtk.Button(label=_("Save …"))
        save_button.set_tooltip_text(_("Save the current code as a snippet under a name"))
        save_button.connect("clicked", self.on_save_snippet)
        delete_button = Gtk.Button(label=_("Delete"))
        delete_button.set_tooltip_text(_("Delete the chosen snippet"))
        delete_button.connect("clicked", self.on_delete_snippet)
        buttons = Gtk.Grid(row_spacing=4, column_spacing=4, column_homogeneous=True)
        for index, button in enumerate((insert_button, clear_button, save_button,
                                        delete_button)):
            button.set_hexpand(True)
            buttons.attach(button, index % 2, index // 2, 1, 1)
        box.attach(Gtk.Label(label=_("Snippet"), xalign=0), 0, 0, 1, 1)
        box.attach(self.snippet_combo, 1, 0, 1, 1)
        box.attach(buttons, 0, 1, 2, 1)
        return box

    def fill_snippets(self, active_name=None):
        self.snippet_filling = True
        try:
            self.snippet_combo.remove_all()
            for snippet in self.panel.settings.snippets:
                self.snippet_combo.append(snippet["name"], _(snippet["name"]))
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
        name = self.panel.ask_text(_("Save snippet"), _("Name of the snippet:"))
        if not name:
            return
        buffer = self._code_buffer()
        code = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        self.panel.settings.add_snippet(name, code)
        self.fill_snippets(name)

    def on_delete_snippet(self, _button):
        name = self.snippet_combo.get_active_id()
        if name and self.panel.confirm(_("Delete the snippet »{name}«?").format(name=name)):
            self.panel.settings.remove_snippet(name)
            self.fill_snippets()

    # ---- last used cutting data (per cycle and outside/inside)
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

    # ---- operation
    def set_side(self, side):
        self.side = side
        self.form.set_side(side)
        self.restore_cutting()
        self.on_param("side", None)

    def on_param(self, key=None, old_value=None):
        if key:
            values, _errors = self.form.get_values(lenient=True)
            if self.cycle_class.HAS_SIDE:
                values["side"] = self.side
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
            prefix = Program.tag(1, op)
            self.warnings = []
            for warning in result.warnings:
                if warning != _(NO_TOOL_CHANGE_WARNING):
                    self.warnings.append(warning.replace(prefix, ""))
            self.errors = [error.replace(prefix, "") for error in result.errors]
        self.panel.page_updated(self)
        return False

    def set_title(self):
        title = f"<b>{GLib.markup_escape_text(self.cycle_class.title())}</b>"
        if self.edit_index is not None:
            title += "  <small>" + GLib.markup_escape_text(
                _("(step {number})").format(number=self.edit_index + 1)) + "</small>"
        self.title.set_markup(title)

    def load(self, op: Cycle, index: Optional[int]):
        self.edit_index = index
        if self.cycle_class.HAS_SIDE:
            side = op.v.get("side", OUTSIDE)
            self.side_bar.buttons[side].set_active(True)
            self.side = side
            self.form.side = side
        self.form.set_values(op.v)
        self.apply_button.set_sensitive(False)
        self.add_button.set_sensitive(True)
        self.set_title()
        self.update()

    def clear_edit(self):
        self.edit_index = None
        self.apply_button.set_sensitive(False)
        self.set_title()

    def on_add(self, _button):
        op = self.build()
        if op is None:
            self.panel.page_updated(self)
            return
        self.panel.add_op(op)
        self.add_button.set_sensitive(False)          # active again after the next change

    def on_apply(self, _button):
        op = self.build()
        if op is not None and self.edit_index is not None:
            self.panel.replace_op(self.edit_index, op)
            self.apply_button.set_sensitive(False)

    # ---- state for rebuilding the panel (language switch)
    def export_state(self):
        values, _errors = self.form.get_values(lenient=True)
        return {"values": values, "side": self.side, "edit_index": self.edit_index}

    def import_state(self, state):
        if self.cycle_class.HAS_SIDE:
            side = state.get("side", OUTSIDE)
            self.side_bar.buttons[side].set_active(True)
            self.side = side
            self.form.set_side(side)
        self.form.set_values(state.get("values", {}))
        self.edit_index = state.get("edit_index")
        self.apply_button.set_sensitive(self.edit_index is not None)
        self.set_title()


# =========================================================================== stock page (middle)

class StockPage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.timer = None
        self.errors = []
        self.pack_start(title_label(_("Stock and clamping")), False, False, 0)
        self.form = ParamForm(STOCK_PARAMS, None, self.on_param)
        self.pack_start(scrolled(self.form), True, True, 0)
        info = Gtk.Label(xalign=0, label=_(
            "The stock is set only here. The stock Ø prefills Ø start in facing and turning; "
            "taper, chamfer and contour rough from the existing material. Moves behind the "
            "chuck face are errors, drilling/inside machining there only a warning."))
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


# =========================================================================== tool page (middle)

KIND_KEYS = ["C", "D", "T", "V", "W", "R", "groove", "thread", "drill", "special"]


class ToolPage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.loading = False
        self.pack_start(title_label(_("Tools")), False, False, 0)
        self.source_label = Gtk.Label(xalign=0)
        self.source_label.set_line_wrap(True)
        self.source_label.get_style_context().add_class("hint")
        self.pack_start(self.source_label, False, False, 0)

        self.store = Gtk.ListStore(int, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate(("T", _("Type"), _("Hand"), _("Position"), "r/b/Ø", "")):
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
        list_scroller.set_min_content_height(120)
        list_scroller.add(self.tree)
        self.pack_start(list_scroller, True, True, 0)

        grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        self.kind_combo = short_combo()
        for key in KIND_KEYS:
            self.kind_combo.append(key, _(KIND_LABELS[key]))
        self.kind_combo.connect("changed", self.on_kind_changed)
        self.hand_combo = short_combo()
        for hand in HANDS:
            self.hand_combo.append(hand, _(HAND_LABELS[hand]))
        self.position_combo = short_combo()
        for position in POSITIONS:
            self.position_combo.append(position, _(POSITION_LABELS[position]))
        self.lead_entry = NumEntry(width=6)
        self.radius_entry = NumEntry(width=6)
        self.width_entry = NumEntry(width=6)
        self.diameter_entry = NumEntry(width=6)
        self.tip_entry = NumEntry(width=6)
        self.tip_entry.set_tooltip_text(_("Point angle of the threading insert, e.g. 60° "
                                          "metric/UNF, 55° Whitworth"))
        self.front_entry = NumEntry(width=6)
        self.back_entry = NumEntry(width=6)
        self.axis_entry = NumEntry(allow_empty=True, width=6)
        angle_help = _("Special shape: angles as in the tool.tbl (I, J), clockwise from Z+. "
                       "Example I120 J70 gives a point angle of 50°")
        self.front_entry.set_tooltip_text(angle_help)
        self.back_entry.set_tooltip_text(angle_help)
        self.axis_entry.set_tooltip_text(_("Direction of the tool axis (shank), same convention "
                                           "as I/J. Empty = bisector of the insert. The insert "
                                           "may sit asymmetrically, e.g. I120 J70 mounted at "
                                           "80°"))
        self.comment_entry = Gtk.Entry()
        rows = [(_("Type"), self.kind_combo), (_("Hand"), self.hand_combo),
                (_("Position"), self.position_combo), (_("Lead angle κ [°]"), self.lead_entry),
                (_("Nose radius r"), self.radius_entry), (_("Grooving width b"), self.width_entry),
                (_("Drill Ø"), self.diameter_entry),
                (_("Thread point angle ε [°]"), self.tip_entry),
                (_("Front angle I [°]"), self.front_entry),
                (_("Back angle J [°]"), self.back_entry),
                (_("Tool axis [°]"), self.axis_entry),
                (_("Description"), self.comment_entry)]
        for row, (text, widget) in enumerate(rows):
            label = Gtk.Label(label=text, xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(16)
            widget.set_hexpand(True)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        self.pack_start(grid, False, False, 0)

        self.apply_button = Gtk.Button(label=_("✔ Apply"))
        self.apply_button.connect("clicked", self.on_apply)
        self.new_button = Gtk.Button(label=_("＋ New"))
        self.new_button.connect("clicked", self.on_new)
        self.delete_button = Gtk.Button(label=_("Delete"))
        self.delete_button.connect("clicked", self.on_delete)
        self.reload_button = Gtk.Button(label=_("⟳ Reload"))
        self.reload_button.set_tooltip_text(_("Read the tool table again (automatically: switch "
                                              "in the machine tab)"))
        self.reload_button.connect("clicked", self.on_reload)
        buttons = Gtk.Grid(row_spacing=4, column_spacing=4, column_homogeneous=True)
        for index, button in enumerate((self.apply_button, self.new_button, self.delete_button,
                                        self.reload_button)):
            button.set_hexpand(True)
            buttons.attach(button, index % 2, index // 2, 1, 1)
        self.pack_start(buttons, False, False, 0)
        self.write_button = Gtk.Button(label=_("→ Write to tool.tbl"))
        self.write_button.set_tooltip_text(_("Enter the selected tool with D, I, J, Q into the "
                                             "tool.tbl (X, Z and all other values stay); "
                                             "LinuxCNC reloads the table"))
        self.write_button.connect("clicked", self.on_write_table)
        self.pack_start(self.write_button, False, False, 0)
        self.mirror_check = Gtk.CheckButton(label=_("Mirror the angles I/J of the tool.tbl in Z"))
        self.mirror_check.set_tooltip_text(_("If the picture shows a right-hand tool as a "
                                             "left-hand one: switch once and check with a known "
                                             "tool"))
        self.mirror_check.get_child().set_line_wrap(True)
        self.mirror_check.connect("toggled", self.on_mirror_toggled)
        self.pack_start(self.mirror_check, False, False, 0)
        self.refresh()

    # ---- list
    def refresh(self, select_number=None):
        table = self.panel.tools
        self.loading = True
        self.mirror_check.set_active(table.mirror_table_angles)
        self.loading = False
        self.mirror_check.set_visible(table.from_linuxcnc)
        self.write_button.set_visible(table.from_linuxcnc)
        self.write_button.set_no_show_all(not table.from_linuxcnc)
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
            self.store.append([tool.number, tool.kind_text(), tool.hand_text(),
                               tool.position_text(), size_text, marker])
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
        """Mark the tool in the list and make it visible."""
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
        self.tip_entry.set_value(round(tool.insert_angle, 1))
        self.show_angles(tool)
        self.comment_entry.set_text(tool.comment)
        # values from the tool.tbl (radius, comment) are maintained in LinuxCNC; newly created
        # tools are free until they are written to the tool.tbl
        editable = not self.panel.tools.from_linuxcnc or tool.source == NEW_SOURCE
        self.radius_entry.set_sensitive(editable)
        self.comment_entry.set_sensitive(editable)
        self.delete_button.set_sensitive(editable)
        self.loading = False
        self.on_kind_changed(self.kind_combo)

    def show_angles(self, tool: Tool):
        """Front/back angle and tool axis in tool.tbl convention (for information or input)."""
        table = self.panel.tools
        back_tool = self.panel.machine.back_tool
        if tool.angles_from_table:
            self.front_entry.set_value(tool.front_angle)
            self.back_entry.set_value(tool.back_angle)
        else:
            angles = table_angles(tool, back_tool, table.mirror_table_angles)
            if angles is None:
                self.front_entry.set_text("")
                self.back_entry.set_text("")
            else:
                # the values »Write to tool.tbl« would enter
                self.front_entry.set_value(angles[0])
                self.back_entry.set_value(angles[1])
        axis = table.table_axis(tool)
        if axis is None:
            self.axis_entry.set_text("")
        else:
            self.axis_entry.set_value(axis)

    def special_editable(self, kind, tool) -> bool:
        """Can the insert of a special shape be entered with I/J (not fixed by the tool.tbl)?"""
        if kind != "special":
            return False
        return tool is None or not tool.angles_from_table or tool.entered_angles

    def on_kind_changed(self, _combo):
        kind = self.kind_combo.get_active_id()
        tool = self.selected_tool()
        # position of the insert from I/J/Q of the tool.tbl: hand, position and angles are fixed,
        # only the type (the classification) can be changed
        geometry_free = tool is None or not tool.angles_from_table
        special = self.special_editable(kind, tool)
        if special:
            # hand, position, lead and point angle follow from I/J
            geometry_free = False
        self.hand_combo.set_sensitive(geometry_free)
        self.position_combo.set_sensitive(geometry_free)
        self.width_entry.set_sensitive(kind == "groove")
        self.diameter_entry.set_sensitive(kind == "drill")
        self.lead_entry.set_sensitive(geometry_free and kind in ("C", "D", "T", "V", "W"))
        self.tip_entry.set_sensitive(geometry_free and kind == "thread")
        self.front_entry.set_sensitive(special)
        self.back_entry.set_sensitive(special)
        self.axis_entry.set_sensitive(special)

    def on_apply(self, _button):
        tool = self.selected_tool()
        if tool is None:
            return
        try:
            lead = self.lead_entry.get_value()
            radius = self.radius_entry.get_value()
            width = self.width_entry.get_value()
            diameter = self.diameter_entry.get_value()
            tip_angle = self.tip_entry.get_value()
        except (ValueError, SyntaxError, ZeroDivisionError):
            self.panel.message(_("Please enter valid numbers."), Gtk.MessageType.ERROR)
            return
        kind = self.kind_combo.get_active_id()
        hand = self.hand_combo.get_active_id()
        position = self.position_combo.get_active_id()
        special_angles = None
        if self.special_editable(kind, tool):
            try:
                front = self.front_entry.get_value()
                back = self.back_entry.get_value()
                axis = self.axis_entry.get_value()
            except (ValueError, SyntaxError, ZeroDivisionError):
                self.panel.message(_("Special shape: enter the front angle I and the back angle "
                                     "J."), Gtk.MessageType.ERROR)
                return
            point_angle = abs(front - back) % 360.0
            if point_angle > 180.0:
                point_angle = 360.0 - point_angle
            if point_angle < 1.0 or point_angle > 179.0:
                self.panel.message(_("Special shape: I and J must enclose a point angle between "
                                     "1° and 179°."), Gtk.MessageType.ERROR)
                return
            special_angles = (front, back, axis)
            hand = hand or NEUTRAL
            position = position or OUTSIDE
        if not kind or not hand or not position:
            self.panel.message(_("Choose type, hand and position."), Gtk.MessageType.ERROR)
            return
        if kind == "thread" and not tool.angles_from_table and not 20.0 <= tip_angle <= 90.0:
            self.panel.message(_("Point angle of the threading tool: 20° … 90°."),
                               Gtk.MessageType.ERROR)
            return
        self.panel.tools.assign(tool, kind, hand, position, lead, radius, width, diameter,
                                self.comment_entry.get_text(), tip_angle, special_angles)
        self.panel.tools_changed(tool.number)

    def on_new(self, _button):
        table = self.panel.tools
        number = table.next_number()
        tool = Tool(number, number, "C", RIGHT, OUTSIDE, 0.4, 95.0, comment=_("new"))
        if table.from_linuxcnc:
            tool.source = NEW_SOURCE        # kept only after »Write to tool.tbl«
        table.add(tool)
        self.panel.tools_changed(number)

    def on_write_table(self, _button):
        tool = self.selected_tool()
        if tool is None:
            return
        if tool.needs_input:
            self.panel.message(_("Enter and apply type, hand and position first."),
                               Gtk.MessageType.WARNING)
            return
        if not self.panel.confirm(_("Write T{number} to the tool.tbl?\nD, I, J, Q and the comment "
                                    "are set, X/Z stay.").format(number=tool.number)):
            return
        notice = self.panel.tools.write_to_table(tool)
        if notice:
            self.panel.message(notice, Gtk.MessageType.WARNING)
        self.panel.tools_changed(tool.number)

    def on_reload(self, _button):
        self.panel.reload_tools()

    def on_delete(self, _button):
        number = self.selected_number()
        if number is not None and self.panel.confirm(_("Delete tool T{number}?").format(
                number=number)):
            self.panel.tools.remove(number)
            self.panel.tools_changed(None)

    def on_mirror_toggled(self, check):
        if self.loading:
            return
        self.panel.tools.set_mirror(check.get_active())
        self.panel.tools_changed(self.selected_number())


# =========================================================================== machine page (middle)

class MachinePage(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        machine = panel.machine
        self.pack_start(title_label(_("Machine")), False, False, 0)
        params = []
        for key, label in MACHINE_FIELDS:
            value = getattr(machine, key)
            if isinstance(value, bool):
                kind = "bool"
            elif isinstance(value, float):
                kind = "float"
            else:
                kind = "str"
            params.append(P(key, label, value, kind, help=MACHINE_HELP.get(key, "")))
        self.form = ParamForm(params)
        self.pack_start(scrolled(self.form), True, True, 0)
        self.info = Gtk.Label(xalign=0)
        self.info.set_line_wrap(True)
        self.info.get_style_context().add_class("hint")
        self.pack_start(self.info, False, False, 0)
        buttons = Gtk.Grid(row_spacing=4, column_spacing=4)
        actions = ((_("Apply"), panel.on_machine_apply), (_("Save"), panel.on_machine_save),
                   (_("Reload from INI"), panel.on_machine_reload))
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
            source_text = f"{_(machine.source)} ({machine.ini_used})"
        else:
            source_text = _(machine.source)
        text = _("Source: {source}. Spindle and axis limits, tool table and tool change "
                 "position (G53) come from the LinuxCNC INI and override machine.json. The tool "
                 "change is always done with M6.").format(source=source_text)
        if machine.tc_enabled:
            text += " " + _("G53 change position from: {source}.").format(
                source=_(machine.tc_source or N_("machine")))
        self.info.set_text(text)


# =========================================================================== left column

class ProgramColumn(Gtk.Box):
    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.pack_start(title_label(_("Program")), False, False, 0)
        self.name_entry = Gtk.Entry(text=panel.program.name)
        self.name_entry.set_width_chars(8)
        self.name_entry.set_placeholder_text(_("Program name"))
        self.name_entry.connect("changed", self.on_name_changed)
        self.pack_start(self.name_entry, False, False, 0)
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate(("#", _("Cycle"), "T", _("Data"))):
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
        actions = (("▲", panel.on_move_up, _("Move up")),
                   ("▼", panel.on_move_down, _("Move down")),
                   ("✎", panel.on_edit, _("Edit (also double click)")),
                   ("⧉", panel.on_duplicate, _("Duplicate")),
                   ("✖", panel.on_delete, _("Delete")))
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

    def unselect(self):
        self.tree.get_selection().unselect_all()


# =========================================================================== right column

class ViewColumn(Gtk.Box):
    MODES = [("cycle", N_("Cycle"), "theme:video-display",
              N_("Preview of the current tab with stock and removal; the mouse wheel zooms, the "
                 "left mouse button pans, a double click resets")),
             ("help", N_("Picture"), "theme:image-x-generic",
              N_("Parameter picture of the current tab")),
             ("gcode", N_("G-code"), "theme:text-x-generic",
              N_("Generated G-code, jumps to the marked program step")),
             ("program", N_("Program"), "theme:x-office-document",
              N_("The whole program, the marked step highlighted"))]

    def __init__(self, panel: "DrehzyklenPanel"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        self.panel = panel
        self.mode = "cycle"
        top = Gtk.Box(spacing=6)
        items = [(key, _(text), icon, _(tooltip)) for key, text, icon, tooltip in self.MODES]
        self.mode_bar = RadioBar(items, "viewbar", self.on_mode, buttons=True,
                                 spacing=COLUMN_SPACING)
        top.pack_start(self.mode_bar, False, False, 0)
        self.pack_start(top, False, False, 0)
        # below the picture, so that the button bar keeps the column narrow
        self.rapid_check = Gtk.CheckButton(label=_("Rapids"), active=True)
        self.rapid_check.connect("toggled", self.on_rapids_toggled)
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
        self.pack_start(self.rapid_check, False, False, 0)
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


# =========================================================================== main panel

class DrehzyklenPanel(Gtk.Box):
    def __init__(self, machine: Machine, machine_json: str, ini_path: Optional[str] = None,
                 embedded=False, settings: Optional[Settings] = None,
                 tools: Optional[ToolTable] = None, program: Optional[Program] = None,
                 on_language=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.get_style_context().add_class("dz")
        self.machine = machine
        self.machine_json = machine_json
        self.ini_path = ini_path
        self.embedded = embedded
        self.on_language = on_language
        config_dir = os.path.dirname(os.path.abspath(machine_json))
        if settings is None:
            settings = Settings(os.path.join(config_dir, "settings.json"))
            threads.load(os.path.join(config_dir, "threads.json"))      # own threads
        self.settings = settings
        if tools is None:
            tools = ToolTable(machine, os.path.join(config_dir, "werkzeuge.json"))
        self.tools = tools
        widgets.ICON_STATE["flipped"] = self.icons_flipped
        if program is None:
            program = Program(machine, name=DEFAULT_NAME, tools=self.tools)
        self.program = program
        self.current = "stock"
        self.keep_selection = False
        self.refresh_timer = None
        self.watch_timer = None
        self.layout_state = None
        self.positioned = False
        self.starting = True
        self.program_result = None
        self.active_dialog = None
        self.set_size_request(300, 200)

        # ---- tab bar - the order of the tabs is defined here
        items = [("stock", _("Stock"), "stock", _("Stock and clamping"))]
        for cycle_class in CYCLES:
            items.append((cycle_class.KEY, cycle_class.short(), cycle_class.KEY,
                          cycle_class.title()))
        items.append(("tools", _("Tools"), "tools", _("Tool table")))
        items.append(("machine", _("Machine"), "machine", _("Machine data")))
        self.selector = RadioBar(items, "selbar", self.show_page, icon_size=24, buttons=True,
                                 spacing=6)
        selector_scroller = Gtk.ScrolledWindow()
        selector_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        selector_scroller.set_propagate_natural_height(True)
        selector_scroller.add(self.selector)
        self.pack_start(selector_scroller, False, False, 0)

        # ---- three columns
        self.program_column = ProgramColumn(self)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stock_page = StockPage(self)
        self.stack.add_named(self.stock_page, "stock")
        self.tool_page = ToolPage(self)
        # the tools tab has many fields - it scrolls so that the panel stays low (1024×768)
        self.stack.add_named(scrolled(self.tool_page), "tools")
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
        # frames around the parameter column and the view column: their button bars
        # (outside/inside, the four views) visibly belong to them
        self.inner_paned.pack1(column_frame(self.stack), False, False)
        self.inner_paned.pack2(column_frame(self.view_column), True, False)
        self.pack_start(self.outer_paned, True, True, 0)

        # ---- bottom button bar
        bar = Gtk.Box(spacing=BOTTOM_SPACING)
        bar.get_style_context().add_class("bottombar")
        self.bottom_buttons = []
        self._bottom_button(bar, _("New"), "document-new", self.on_new,
                            _("New program (the stock stays)"))
        self._bottom_button(bar, _("Open"), "document-open", self.on_open,
                            _("Open a project (.dzp)"))
        self._bottom_button(bar, _("Save"), "document-save", self.on_save_project,
                            _("Save the project (.dzp)"))
        self._bottom_button(bar, _("G-code"), "accessories-text-editor", self.on_gcode_editor,
                            _("Show, complete and save the G-code"))
        self.send_button = self._bottom_button(bar, _("Load"), "media-playback-start",
                                               self.on_send,
                                               _("Generate the G-code, save it and open it in "
                                                 "LinuxCNC"), at_end=True)
        self.machine_button = self._bottom_button(bar, _("Machine"), "preferences-system",
                                                  self.on_toggle_machine,
                                                  _("Show/hide the machine tab"), toggle=True,
                                                  at_end=True)
        bar.pack_end(self._language_box(), False, False, 0)
        self.pack_start(bar, False, False, 0)

        running = linuxcnc_running()
        self.machine_button.set_active(not running)
        self.selector.buttons["machine"].set_visible(not running)
        self.stock_page.load(self.program.stock)
        for page in self.pages.values():
            page.form.set_stock(self.program.stock, force=True)
        self.starting = False
        self.watch_timer = GLib.timeout_add_seconds(2, self.watch_tool_table)
        self.connect("size-allocate", self.on_size_allocate)
        self.connect("destroy", self.on_destroy)
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

    def _language_box(self):
        """Language selection; switching rebuilds the panel at once."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_valign(Gtk.Align.CENTER)
        self.language_combo = Gtk.ComboBoxText()
        for code in i18n.available_languages():
            self.language_combo.append(code, i18n.language_name(code))
        self.language_combo.set_active_id(i18n.language())
        self.language_combo.set_tooltip_text(_("Language of the user interface and of the "
                                               "G-code comments"))
        self.language_combo.connect("changed", self.on_language_changed)
        box.pack_start(self.language_combo, False, False, 0)
        return box

    def on_language_changed(self, combo):
        code = combo.get_active_id()
        if not code or code == i18n.language():
            return
        self.settings.set_language(code)
        if self.on_language is not None:
            GLib.idle_add(self.on_language, code)

    def on_destroy(self, _widget):
        if self.watch_timer:
            GLib.source_remove(self.watch_timer)
            self.watch_timer = None

    def icons_flipped(self) -> bool:
        return not self.machine.back_tool

    def tool_choices(self):
        choices = []
        for tool in self.tools.sorted_tools():
            choices.append((tool.number, tool.label()))
        return choices

    # ---------------------------------------------------------------- state (language switch)
    def export_state(self):
        return {"current": self.current, "view_mode": self.view_column.mode,
                "selected": self.program_column.selected(),
                "name": self.program_column.name_entry.get_text(),
                "machine_tab": self.machine_button.get_active(),
                "positions": (self.outer_paned.get_position(), self.inner_paned.get_position()),
                "pages": {key: page.export_state() for key, page in self.pages.items()}}

    def import_state(self, state):
        for key, page_state in state.get("pages", {}).items():
            if key in self.pages:
                self.pages[key].import_state(page_state)
        self.program_column.name_entry.set_text(state.get("name", self.program.name))
        self.machine_button.set_active(state.get("machine_tab", False))
        left, middle = state.get("positions", (0, 0))
        if left > 50 and middle > 50:
            self.positioned = True
            GLib.idle_add(self.set_positions, left, middle)
        self.refresh_list(state.get("selected"))
        current = state.get("current", "stock")
        if current in self.selector.buttons:
            self.keep_selection = True
            try:
                self.selector.buttons[current].set_active(True)
                self.show_page(current)
            finally:
                self.keep_selection = False
        self.view_column.set_mode(state.get("view_mode", "cycle"))

    # ---------------------------------------------------------------- layout
    def on_size_allocate(self, _widget, allocation):
        # narrow or very low: button texts are hidden (icons only, the text stays as tooltip)
        narrow = allocation.width < 760 or allocation.height < 480
        layout = (narrow, allocation.width < 920 or allocation.height < 600)
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
        bars = (self.selector, self.view_column.mode_bar)
        buttons = [button for bar in bars for button in bar.buttons.values()]
        for page in self.pages.values():
            if page.cycle_class.HAS_SIDE:
                buttons.extend(page.side_bar.buttons.values())
        for button in buttons + self.bottom_buttons:
            button.text_label.set_visible(not narrow)
        return False

    def show_startup_hints(self):
        """Tools without data: open the tools tab and point it out."""
        pending = self.tools.pending()
        if pending:
            self.selector.buttons["tools"].set_active(True)
            numbers = ", ".join(f"T{tool.number}" for tool in pending)
            self.view_column.message_label.set_markup(message_markup(
                [_("For {numbers} type and cutting direction cannot be derived from the tool.tbl "
                   "- please enter and apply type, hand and position.").format(numbers=numbers)],
                []))
        return False

    # ---------------------------------------------------------------- pages / view
    def show_page(self, key):
        previous_page = self.pages.get(self.current)
        if key == "tools" and previous_page is not None and previous_page.cycle_class.HAS_TOOL:
            # show the tool of the cycle edited last in the tools tab
            values, _errors = previous_page.form.get_values(lenient=True)
            tool_number = values.get("tool")
            if tool_number:
                self.tool_page.select_number(int(tool_number))
        self.current = key
        self.stack.set_visible_child_name(key)
        self.view_column.view.reset_view()
        page = self.pages.get(key)
        selected = self.program_column.selected()
        if (page is not None and selected is not None and page.edit_index != selected
                and not self.keep_selection):
            # a new cycle is chosen - the marked program step is not edited here, so the
            # marking would only suggest that »Apply« changes it
            self.clear_edit()
            self.program_column.unselect()
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
                                                               "✔ " + _("No messages")))
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
            column.message_label.set_markup(message_markup(page.warnings, page.errors,
                                                           "✔ " + _("OK")))
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

    # ---------------------------------------------------------------- stock / tools
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
        """Every 2 s check whether LinuxCNC changed the tool.tbl and read it again
        (only if switched on in the machine tab)."""
        if not self.machine.auto_reload_tools:
            return True
        if self.tools.from_linuxcnc or self.tools.table_path():
            if self.tools.changed_on_disk():
                self.reload_tools()
                self.program_column.status.set_text(_("The tool table was changed - read "
                                                      "again"))
        return True

    def tools_changed(self, select_number):
        self.tool_page.refresh(select_number)
        for page in self.pages.values():
            page.form.refresh_tools()
            page.schedule_update()
        self.show_tool(self.tool_page.selected_tool())
        self.refresh_gcode()

    # ---------------------------------------------------------------- program list
    def refresh_list(self, select: Optional[int] = None):
        column = self.program_column
        column.store.clear()
        for index, op in enumerate(self.program.ops, 1):
            if op.HAS_TOOL:
                tool_text = str(int(op.tool))
            else:
                tool_text = "-"
            title = op.title()
            if op.HAS_SIDE and op.inside:
                title += " " + _("inside")
            column.store.append([str(index), title, tool_text, op.summary(),
                                 GLib.markup_escape_text(f"{op.title()}: {op.summary()}")])
        if select is not None and 0 <= select < len(self.program.ops):
            column.tree.get_selection().select_path(Gtk.TreePath.new_from_indices([select]))
        self.refresh_gcode()

    def on_select(self):
        if self.view_column.mode in ("program", "gcode"):
            self.update_view()

    def add_op(self, op: Cycle):
        self.program.ops.append(op)
        self.refresh_list(len(self.program.ops) - 1)
        self.program_column.status.set_text(_("{title} added as step {number}").format(
            title=op.title(), number=len(self.program.ops)))

    def replace_op(self, index: int, op: Cycle):
        if index >= len(self.program.ops):
            self.add_op(op)
            return
        self.program.ops[index] = op
        self.refresh_list(index)
        self.program_column.status.set_text(_("Step {number} changed").format(number=index + 1))

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
        self.keep_selection = True
        try:
            self.selector.buttons[op.KEY].set_active(True)
        finally:
            self.keep_selection = False
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

    # ---------------------------------------------------------------- G-code
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
            name = DEFAULT_NAME
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
        text = _("{steps} step(s), {tools} tool(s)").format(steps=count, tools=len(tool_numbers))
        if result.errors:
            text += " · " + _("{count} error(s)").format(count=len(result.errors))
        self.program_column.status.set_text(text)
        self.update_view()
        return result

    def gcode_ok(self, result) -> bool:
        if not self.program.ops:
            self.message(_("The program does not contain any cycles yet."))
            return False
        if result.errors:
            self.message(_("The program contains errors:") + "\n\n" + "\n".join(result.errors),
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
            name = "part"
        return name + ".ngc"

    def program_folder(self):
        return self.settings.program_dir(self.machine.program_dir)

    def on_gcode_editor(self, _button):
        """Show the generated G-code, complete it if needed, then save or load it."""
        result = self.refresh_gcode()
        if not self.gcode_ok(result):
            return
        dialog = Gtk.Dialog(title=_("G-code - show and complete"),
                            transient_for=self.get_toplevel(), modal=True)
        dialog.add_buttons(_("Close"), Gtk.ResponseType.CANCEL, _("Save as …"), 1,
                           _("Load into LinuxCNC"), 2)
        dialog.set_default_size(720, 520)
        running = linuxcnc_running()
        dialog.set_response_sensitive(2, running)
        if not running:
            load_button = dialog.get_widget_for_response(2)
            if load_button is not None:
                load_button.set_tooltip_text(_("LinuxCNC is not running"))
        content = dialog.get_content_area()
        info = Gtk.Label(xalign=0, label=_("Additions here apply to this file only. Code that "
                                           "should stay between two cycles belongs in a "
                                           "»G-code« step (with snippets)."))
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
                path = self.file_dialog(_("Save G-code"), Gtk.FileChooserAction.SAVE,
                                        self.file_name(), (_("G-code"), "*.ngc"))
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
            self.message(_("LinuxCNC is not running - the program cannot be loaded.\n"
                           "To save it, use »G-code« → »Save as …«."), Gtk.MessageType.WARNING)
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
            self.message(_("Saved as\n{path}\n\nLinuxCNC Python module not found - please open "
                           "the file by hand.").format(path=path))
            return True
        try:
            status = linuxcnc.stat()
            command = linuxcnc.command()
            status.poll()
            if status.interp_state != linuxcnc.INTERP_IDLE:
                self.message(_("LinuxCNC is running a program - loading is not possible."),
                             Gtk.MessageType.WARNING)
                return False
            if status.task_mode != linuxcnc.MODE_AUTO:
                command.mode(linuxcnc.MODE_AUTO)
                command.wait_complete()
            command.program_open(path)
            self.program_column.status.set_text(_("Loaded into LinuxCNC: {path}").format(
                path=path))
            if warnings:
                self.message(_("Program loaded. Please note the hints in the »Program« view."),
                             Gtk.MessageType.WARNING)
            return True
        except linuxcnc.error as error:
            self.message(_("LinuxCNC error: {error}\nThe file is at {path}").format(
                error=error, path=path), Gtk.MessageType.ERROR)
            return False

    def write_file(self, path, text) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
        except OSError as error:
            self.message(_("Saving failed: {error}").format(error=error), Gtk.MessageType.ERROR)
            return False
        self.settings.remember_dir(path)
        self.program_column.status.set_text(_("Saved: {path}").format(path=path))
        return True

    # ---------------------------------------------------------------- project
    def on_new(self, _button):
        if self.program.ops and not self.confirm(_("Discard the current program? (the stock "
                                                   "stays)")):
            return
        self.program.ops.clear()
        self.clear_edit()
        self.refresh_list()
        # the stock stays - show the stock tab for checking
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
        path = self.file_dialog(_("Open project"), Gtk.FileChooserAction.OPEN, None,
                                (_("Drehzyklen project"), "*.dzp"))
        if not path:
            return
        try:
            self.load_project(path)
            self.settings.remember_dir(path)
        except (OSError, ValueError, KeyError) as error:
            self.message(_("The project cannot be read: {error}").format(error=error),
                         Gtk.MessageType.ERROR)

    def on_save_project(self, _button):
        name = self.program_column.name_entry.get_text().strip()
        if not name:
            name = DEFAULT_NAME
        self.program.name = name
        path = self.file_dialog(_("Save project"), Gtk.FileChooserAction.SAVE,
                                self.file_name().replace(".ngc", ".dzp"),
                                (_("Drehzyklen project"), "*.dzp"))
        if path:
            if not path.endswith(".dzp"):
                path += ".dzp"
            self.write_file(path, self.program.to_json())

    # ---------------------------------------------------------------- machine
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
            self.message(_("Please enter a program folder."), Gtk.MessageType.ERROR)
            return False
        if not os.path.isdir(folder):
            if not self.confirm(_("The program folder\n{folder}\ndoes not exist. Create it "
                                  "now?").format(folder=folder)):
                self.message(_("Program folder not applied - please enter an existing "
                               "folder."), Gtk.MessageType.WARNING)
                return False
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as error:
                self.message(_("The folder cannot be created: {error}").format(error=error),
                             Gtk.MessageType.ERROR)
                return False
        ini_text = str(values.get("ini_file", "")).strip()
        ini_path = os.path.expanduser(ini_text)
        if ini_text and not os.path.isfile(ini_path):
            self.message(_("The INI file\n{path}\ndoes not exist.").format(path=ini_path),
                         Gtk.MessageType.ERROR)
            return False
        old_values = dict(self.machine.__dict__)
        self.machine.update_from_dict(values)
        if ini_text != old_values.get("ini_file", ""):
            self.use_ini_file(ini_path, old_values)
        if self.machine.tc_enabled and not self.machine.tc_source:
            self.machine.tc_source = N_("machine tab")
        self.settings.last_dir = ""
        self.settings.save()
        self.machine_updated(old_values)
        return True

    def use_ini_file(self, ini_path, old_values):
        """Read the INI entered in the machine tab right away (tool table, limits, G53)."""
        if self.ini_path or os.environ.get("INI_FILE_NAME"):
            self.message(_("LinuxCNC already passed an INI at the start - the entered INI is "
                           "only used without a running LinuxCNC."), Gtk.MessageType.INFO)
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
                self.program_column.status.set_text(_("Machine data saved: {path}").format(
                    path=self.machine_json))
            except OSError as error:
                self.message(_("Saving failed: {error}").format(error=error),
                             Gtk.MessageType.ERROR)

    def on_machine_reload(self, _button):
        old_values = dict(self.machine.__dict__)
        reloaded = Machine.load(self.machine_json, self.ini_path)
        self.machine.__dict__.update(reloaded.__dict__)
        self.machine_page.refresh()
        self.machine_updated(old_values)

    # ---------------------------------------------------------------- dialogs
    def dialog_parent(self):
        """Window above which messages appear: an open dialog, otherwise the main window."""
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
        dialog.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("OK"), Gtk.ResponseType.OK)
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
            confirm_text = _("Save")
        else:
            confirm_text = _("Open")
        dialog.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, confirm_text,
                           Gtk.ResponseType.OK)
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


ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def set_default_icon():
    """Program icon (small lathe) for the main window and the dialogs."""
    for name in ("drehzyklen.svg", "drehzyklen.png"):
        try:
            Gtk.Window.set_default_icon_from_file(os.path.join(ICON_DIR, name))
            return
        except GLib.Error:
            continue


class App:
    """Window (or gmoccapy plug) holding the panel; rebuilds the panel on a language change."""

    def __init__(self, machine: Machine, machine_json: str, ini_path: Optional[str],
                 xid: Optional[int]):
        self.machine = machine
        self.machine_json = machine_json
        self.ini_path = ini_path
        self.xid = xid
        config_dir = os.path.dirname(os.path.abspath(machine_json))
        self.settings = Settings(os.path.join(config_dir, "settings.json"))
        i18n.set_language(self.settings.language)
        threads.load(os.path.join(config_dir, "threads.json"))      # own threads
        self.tools = ToolTable(machine, os.path.join(config_dir, "werkzeuge.json"))
        if xid:
            self.window = Gtk.Plug.new(xid)
        else:
            self.window = Gtk.Window()
            self.window.set_default_size(1100, 680)
        self.window.connect("destroy", Gtk.main_quit)
        self.panel = None

    def create_panel(self, program: Optional[Program] = None):
        self.window.set_title(_("Drehzyklen - LinuxCNC"))
        panel = DrehzyklenPanel(self.machine, self.machine_json, self.ini_path,
                                embedded=self.xid is not None, settings=self.settings,
                                tools=self.tools, program=program, on_language=self.rebuild)
        self.panel = panel
        self.window.add(panel)
        return panel

    def finish_start(self, machine_tab=False):
        panel = self.panel
        self.window.show_all()
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

    def rebuild(self, code):
        """Switch the language: build a new panel and carry the state over."""
        i18n.set_language(code)
        old_panel = self.panel
        state = old_panel.export_state()
        program = old_panel.program
        self.window.remove(old_panel)
        old_panel.destroy()
        panel = self.create_panel(program)
        self.finish_start(state.get("machine_tab", False))
        panel.import_state(state)
        return False


def run(machine: Machine, machine_json: str, ini_path: Optional[str], xid: Optional[int],
        project: Optional[str] = None, machine_tab: bool = False):
    install_css()
    set_default_icon()
    app = App(machine, machine_json, ini_path, xid)
    panel = app.create_panel()
    if project:
        try:
            panel.load_project(project)
        except (OSError, ValueError, KeyError) as error:
            print(_("Project {path} cannot be read: {error}").format(path=project, error=error))
    app.finish_start(machine_tab)
    GLib.idle_add(panel.show_startup_hints)
    Gtk.main()
