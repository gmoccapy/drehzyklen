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
"""Reusable GTK building blocks: number field, toggle bars, contour editor, parameter form,
drawing area with zoom and pan."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import helpdraw, render, tooldraw  # noqa: E402
from .cycles import INSIDE, OUTSIDE, P  # noqa: E402
from .geometry import ContourError, Row, parse_rows, rows_to_text  # noqa: E402
from .i18n import N_, _  # noqa: E402
from .machine import Machine  # noqa: E402

SYM_COLOR = "#e0a422"


def is_icon_flipped() -> bool:
    """Replaced by the main panel: True if the tool is in front of the centre line."""
    return True


ICON_STATE = {"flipped": is_icon_flipped}


# =========================================================================== input elements

def eval_simple(text: str) -> float:
    """Number or a simple calculation like '40-2*1.5'."""
    try:
        return float(text)
    except ValueError:
        pass
    if not set(text) <= set("0123456789.+-*/() "):
        raise ValueError(text)
    return float(eval(text, {"__builtins__": {}}, {}))  # digits and operators only


def number_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        # up to 6 decimals so that e.g. inch pitches (25.4/28) stay exact
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        if text in ("-0", ""):
            return "0"
        return text
    return str(value)


class NumEntry(Gtk.Entry):
    """Number field with an on-screen keypad (touch), decimal comma and simple calculations."""

    def __init__(self, allow_empty=False, width=8):
        super().__init__()
        self.allow_empty = allow_empty
        self.set_width_chars(width)
        self.set_alignment(1.0)
        self.set_hexpand(True)
        self.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "input-dialpad-symbolic")
        self.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Keypad"))
        self.connect("icon-press", self.on_numpad)

    def get_value(self) -> Optional[float]:
        text = self.get_text().strip().replace(",", ".")
        if not text:
            if self.allow_empty:
                return None
            raise ValueError("empty")
        return eval_simple(text)

    def set_value(self, value):
        self.set_text(number_text(value))

    def on_numpad(self, *_args):
        popover = Gtk.Popover.new(self)
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, margin=6)
        grid.get_style_context().add_class("numpad")
        keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "-", "0", "."]
        for index, key in enumerate(keys):
            button = Gtk.Button(label=key)
            button.connect("clicked", self.on_numpad_key, key)
            grid.attach(button, index % 3, index // 3, 1, 1)
        backspace = Gtk.Button(label="⌫")
        backspace.connect("clicked", self.on_numpad_backspace)
        grid.attach(backspace, 3, 0, 1, 1)
        clear = Gtk.Button(label="C")
        clear.connect("clicked", self.on_numpad_clear)
        grid.attach(clear, 3, 1, 1, 1)
        ok_button = Gtk.Button(label="OK")
        ok_button.connect("clicked", self.on_numpad_ok, popover)
        grid.attach(ok_button, 3, 2, 1, 2)
        popover.add(grid)
        grid.show_all()
        popover.popup()

    def on_numpad_key(self, _button, key):
        position = self.get_position()
        self.insert_text(key, position)
        self.set_position(position + 1)

    def on_numpad_backspace(self, _button):
        position = self.get_position()
        if position > 0:
            self.delete_text(position - 1, position)

    def on_numpad_clear(self, _button):
        self.set_text("")

    def on_numpad_ok(self, _button, popover):
        popover.popdown()
        self.activate()


class RadioBar(Gtk.Box):
    """Row of toggle buttons of which one is active. items: [(key, text, icon, tooltip)].

    icon is the key of a drawn icon (helpdraw.draw_icon), "theme:<name>" for an icon of the
    icon theme or None. With buttons=True the row looks like the bottom button bar: separate
    buttons, icon above the text; otherwise a compact linked row.
    """

    def __init__(self, items, css_class, on_toggle, icon_size=0, buttons=False, spacing=8):
        if buttons:
            super().__init__(spacing=spacing)
            self.get_style_context().add_class("buttonbar")
        else:
            super().__init__(spacing=0)
            self.get_style_context().add_class("linked")
        self.get_style_context().add_class(css_class)
        self.on_toggle = on_toggle
        self.buttons: Dict[str, Gtk.RadioButton] = {}
        group = None
        for key, text, icon, tooltip in items:
            button = Gtk.RadioButton.new_from_widget(group)
            if group is None:
                group = button
            button.set_mode(False)
            if buttons:
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            else:
                inner = Gtk.Box(spacing=4)
            if icon and icon.startswith("theme:"):
                image = Gtk.Image.new_from_icon_name(icon[len("theme:"):],
                                                     Gtk.IconSize.LARGE_TOOLBAR)
                inner.pack_start(image, True, True, 0)
            elif icon and icon_size:
                area = Gtk.DrawingArea()
                area.set_size_request(icon_size, icon_size)
                area.set_halign(Gtk.Align.CENTER)
                area.connect("draw", self.on_draw_icon, icon, icon_size)
                inner.pack_start(area, True, False, 0)
                button.icon_area = area
            label = Gtk.Label(label=text)
            inner.pack_start(label, False, False, 0)
            button.add(inner)
            button.text_label = label
            if tooltip:
                button.set_tooltip_text(tooltip)
            button.connect("toggled", self.on_button_toggled, key)
            if buttons:
                self.pack_start(button, False, False, 0)
            else:
                self.pack_start(button, True, True, 0)
            self.buttons[key] = button

    def on_button_toggled(self, button, key):
        if button.get_active():
            self.on_toggle(key)

    @staticmethod
    def on_draw_icon(widget, cr, key, size):
        color = widget.get_style_context().get_color(widget.get_state_flags())
        helpdraw.draw_icon(cr, key, size, (color.red, color.green, color.blue),
                           flip=ICON_STATE["flipped"]())
        return False


def short_combo():
    """Choice box whose texts are shortened with … so that long entries do not widen the
    column (the full text is shown in the opened list)."""
    combo = Gtk.ComboBoxText()
    for cell in combo.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
    combo.set_hexpand(True)
    return combo


def image_button(text, icon_name, tooltip=None, toggle=False):
    if toggle:
        button = Gtk.ToggleButton()
    else:
        button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
    label = Gtk.Label(label=text)
    box.pack_start(image, True, True, 0)
    box.pack_start(label, False, False, 0)
    button.add(box)
    button.text_label = label
    if tooltip:
        button.set_tooltip_text(tooltip)
    else:
        button.set_tooltip_text(text)
    return button


def error_markup(text):
    return f"<span foreground='#e03030'>{GLib.markup_escape_text(text)}</span>"


# =========================================================================== contour editor

ARC_MODES = [("R", N_("Radius")), ("RL", N_("Radius, arc > 180°")),
             ("TS", N_("tangential at the start")), ("TE", N_("tangential at the end")),
             ("IK", N_("Centre I/K"))]
CORNER_MODES = [(None, N_("Default (all other corners)")), ("N", N_("none")),
                ("R", N_("Radius R")), ("C45", N_("Chamfer 45°")),
                ("CA", N_("Chamfer L × angle to the incoming edge")),
                ("CB", N_("Chamfer L × angle to the outgoing edge"))]
ARC_TEXT = {"R": "R{}", "RL": N_("R{} >180°"), "TS": N_("tang. start"), "TE": N_("tang. end")}


def describe_row(row: Row, first: bool):
    if first:
        kind_text = _("Start")
    elif row.kind == "L":
        kind_text = "G1"
    else:
        kind_text = row.kind
    arc_text = ""
    if row.is_arc and not first:
        if row.arc == "IK":
            arc_text = f"I{number_text(row.ai)} K{number_text(row.ak)}"
        else:
            arc_text = _(ARC_TEXT[row.arc]).format(number_text(row.ar))
    corner_text = ""
    if row.corner == "N":
        corner_text = _("sharp")
    elif row.corner == "R":
        corner_text = f"R{number_text(row.csize)}"
    elif row.corner == "C":
        corner_text = f"C{number_text(row.csize)}"
        if row.cangle is not None:
            corner_text += f" {row.cref}{number_text(row.cangle)}°"
    return kind_text, arc_text, corner_text


class ContourEditor(Gtk.Box):
    """Contour as a list (point by point, touch friendly) or as text."""

    def __init__(self, on_change: Callable):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.on_change = on_change
        self.rows: List[Row] = []
        self.locked = False

        self.mode_bar = RadioBar([("list", _("List"), None, _("Edit point by point")),
                                  ("text", _("Text"), None, _("Edit the contour text directly"))],
                                 "viewswitch", self.on_mode)
        self.pack_start(self.mode_bar, False, False, 0)
        self.stack = Gtk.Stack()
        self.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._build_list_view(), "list")
        self.stack.add_named(self._build_text_view(), "text")
        self.error_label = Gtk.Label(xalign=0)
        self.error_label.set_line_wrap(True)
        self.pack_start(self.error_label, False, False, 0)
        self.update_sensitivity()

    # ---------------------------------------------------------------- building
    def _build_list_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.store = Gtk.ListStore(str, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        for index, title in enumerate(("P", _("Kind"), "X (Ø)", "Z", _("Arc"), _("Corner"))):
            renderer = Gtk.CellRendererText()
            if index in (2, 3):
                renderer.set_property("xalign", 1.0)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            self.tree.append_column(column)
        self.tree.get_selection().connect("changed", self.on_selection_changed)
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_min_content_height(120)
        scroller.add(self.tree)
        box.pack_start(scroller, True, True, 0)

        grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        self.kind_combo = self._combo((_("Line G1"), _("Arc G2"), _("Arc G3")))
        self.x_entry = NumEntry(width=4)
        self.z_entry = NumEntry(width=4)
        self.arc_combo = self._combo([_(text) for _key, text in ARC_MODES])
        self.radius_entry = NumEntry(allow_empty=True, width=4)
        self.i_entry = NumEntry(allow_empty=True, width=4)
        self.k_entry = NumEntry(allow_empty=True, width=4)
        self.corner_combo = self._combo([_(text) for _key, text in CORNER_MODES])
        self.size_entry = NumEntry(allow_empty=True, width=4)
        self.angle_entry = NumEntry(allow_empty=True, width=4)
        self.radius_label = Gtk.Label(label="R", xalign=0)
        self.i_label = Gtk.Label(label="I", xalign=0)
        self.k_label = Gtk.Label(label="K", xalign=0)
        self.size_label = Gtk.Label(label=_("Size"), xalign=0)
        self.angle_label = Gtk.Label(label=_("Angle °"), xalign=0)

        grid.attach(Gtk.Label(label=_("Element"), xalign=0), 0, 0, 1, 1)
        grid.attach(self.kind_combo, 1, 0, 3, 1)
        grid.attach(Gtk.Label(label="X (Ø)", xalign=0), 0, 1, 1, 1)
        grid.attach(self.x_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Z", xalign=0), 2, 1, 1, 1)
        grid.attach(self.z_entry, 3, 1, 1, 1)
        grid.attach(Gtk.Label(label=_("Arc"), xalign=0), 0, 2, 1, 1)
        grid.attach(self.arc_combo, 1, 2, 3, 1)
        grid.attach(self.radius_label, 0, 3, 1, 1)
        grid.attach(self.radius_entry, 1, 3, 1, 1)
        center_box = Gtk.Box(spacing=4)
        center_box.pack_start(self.i_label, False, False, 0)
        center_box.pack_start(self.i_entry, True, True, 0)
        center_box.pack_start(self.k_label, False, False, 0)
        center_box.pack_start(self.k_entry, True, True, 0)
        grid.attach(center_box, 2, 3, 2, 1)
        grid.attach(Gtk.Label(label=_("Corner"), xalign=0), 0, 4, 1, 1)
        grid.attach(self.corner_combo, 1, 4, 3, 1)
        grid.attach(self.size_label, 0, 5, 1, 1)
        grid.attach(self.size_entry, 1, 5, 1, 1)
        grid.attach(self.angle_label, 2, 5, 1, 1)
        grid.attach(self.angle_entry, 3, 5, 1, 1)
        box.pack_start(grid, False, False, 0)

        buttons = Gtk.Grid(row_spacing=4, column_spacing=4)
        actions = [(_("＋ Append"), self.on_append, _("Insert a new element after the marked one")),
                   (_("✔ Change"), self.on_apply, _("Overwrite the marked element with the fields")),
                   (_("Delete"), self.on_delete, None),
                   ("▲", self.on_move_up, None),
                   ("▼", self.on_move_down, None)]
        for index, (text, handler, tooltip) in enumerate(actions):
            button = Gtk.Button(label=text)
            button.connect("clicked", handler)
            button.set_hexpand(True)
            if tooltip:
                button.set_tooltip_text(tooltip)
            if index < 3:
                buttons.attach(button, index, 0, 1, 1)
            else:
                buttons.attach(button, index - 3, 1, 1, 1)
        box.pack_start(buttons, False, False, 0)
        return box

    def _combo(self, texts):
        combo = short_combo()
        for text in texts:
            combo.append_text(text)
        combo.set_active(0)
        combo.set_hexpand(True)
        for cell in combo.get_cells():
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        combo.connect("changed", self.on_editor_combo_changed)
        return combo

    def _build_text_view(self):
        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_left_margin(6)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.get_buffer().connect("changed", self.on_text_changed)
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_min_content_height(180)
        scroller.add(self.text_view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.pack_start(scroller, True, True, 0)
        hint = Gtk.Label(xalign=0, label=_("X Z · R5 · C2 · C2 A30 / B30 · G2/G3 X Z R|LA|I K|TS|TE · "
                                           "RND/CHR · R0  (the »Picture« view shows the contour)"))
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("hint")
        box.pack_start(hint, False, False, 0)
        return box

    # ---------------------------------------------------------------- text <-> rows
    def get_text(self) -> str:
        buffer = self.text_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def set_text(self, text: str):
        self.locked = True
        self.text_view.get_buffer().set_text(text)
        self.locked = False
        self.parse(text)

    def parse(self, text) -> bool:
        try:
            self.rows = parse_rows(text)
        except ContourError as error:
            self.error_label.set_markup(error_markup(str(error)))
            return False
        self.error_label.set_text("")
        self.refresh_list()
        return True

    def on_text_changed(self, _buffer):
        if self.locked:
            return
        self.parse(self.get_text())
        self.on_change()

    def commit(self, select=None):
        self.locked = True
        self.text_view.get_buffer().set_text(rows_to_text(self.rows))
        self.locked = False
        self.refresh_list(select)
        self.on_change()

    def refresh_list(self, select=None):
        if select is None:
            select = self.selected_index()
        self.store.clear()
        for index, row in enumerate(self.rows):
            kind_text, arc_text, corner_text = describe_row(row, index == 0)
            self.store.append([str(index + 1), kind_text, number_text(row.x), number_text(row.z),
                               arc_text, corner_text])
        if select is not None and 0 <= select < len(self.rows):
            self.tree.get_selection().select_path(Gtk.TreePath.new_from_indices([select]))

    def on_mode(self, key):
        if key == "list" and not self.parse(self.get_text()):
            GLib.idle_add(self.mode_bar.buttons["text"].set_active, True)
            return
        self.stack.set_visible_child_name(key)

    # ---------------------------------------------------------------- editor
    def selected_index(self):
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        return model.get_path(tree_iter).get_indices()[0]

    def on_selection_changed(self, _selection):
        index = self.selected_index()
        if index is None or index >= len(self.rows):
            return
        row = self.rows[index]
        self.kind_combo.set_active({"L": 0, "G2": 1, "G3": 2}[row.kind])
        self.x_entry.set_value(row.x)
        self.z_entry.set_value(row.z)
        self.arc_combo.set_active([key for key, _text in ARC_MODES].index(row.arc))
        if row.arc in ("R", "RL"):
            self.radius_entry.set_value(row.ar)
        else:
            self.radius_entry.set_value(None)
        if row.arc == "IK":
            self.i_entry.set_value(row.ai)
            self.k_entry.set_value(row.ak)
        else:
            self.i_entry.set_value(None)
            self.k_entry.set_value(None)
        if row.corner == "C":
            if row.cangle is None:
                corner_key = "C45"
            elif row.cref == "A":
                corner_key = "CA"
            else:
                corner_key = "CB"
        else:
            corner_key = row.corner
        self.corner_combo.set_active([key for key, _text in CORNER_MODES].index(corner_key))
        if row.corner in ("R", "C"):
            self.size_entry.set_value(row.csize)
        else:
            self.size_entry.set_value(None)
        self.angle_entry.set_value(row.cangle)
        self.update_sensitivity()

    def on_editor_combo_changed(self, _combo):
        self.update_sensitivity()

    def update_sensitivity(self):
        is_arc = self.kind_combo.get_active() > 0
        arc_key = ARC_MODES[max(self.arc_combo.get_active(), 0)][0]
        self.arc_combo.set_sensitive(is_arc)
        for widget in (self.radius_entry, self.radius_label):
            widget.set_sensitive(is_arc and arc_key in ("R", "RL"))
        for widget in (self.i_entry, self.k_entry, self.i_label, self.k_label):
            widget.set_sensitive(is_arc and arc_key == "IK")
        corner_key = CORNER_MODES[max(self.corner_combo.get_active(), 0)][0]
        for widget in (self.size_entry, self.size_label):
            widget.set_sensitive(corner_key in ("R", "C45", "CA", "CB"))
        for widget in (self.angle_entry, self.angle_label):
            widget.set_sensitive(corner_key in ("CA", "CB"))

    def editor_row(self) -> Optional[Row]:
        try:
            kind = {0: "L", 1: "G2", 2: "G3"}[self.kind_combo.get_active()]
            row = Row(kind, self.x_entry.get_value(), self.z_entry.get_value())
            if row.is_arc:
                row.arc = ARC_MODES[self.arc_combo.get_active()][0]
                if row.arc in ("R", "RL"):
                    radius = self.radius_entry.get_value()
                    if radius is None or radius <= 0:
                        raise ValueError(_("Arc radius R is missing"))
                    row.ar = radius
                    if row.ar <= 0:
                        raise ValueError(_("Arc radius R is missing"))
                elif row.arc == "IK":
                    offset_i = self.i_entry.get_value()
                    offset_k = self.k_entry.get_value()
                    if offset_i is None:
                        offset_i = 0.0
                    if offset_k is None:
                        offset_k = 0.0
                    row.ai = offset_i
                    row.ak = offset_k
            corner_key = CORNER_MODES[self.corner_combo.get_active()][0]
            if corner_key == "N":
                row.corner = "N"
            elif corner_key in ("R", "C45", "CA", "CB"):
                size = self.size_entry.get_value()
                if not size or size <= 0:
                    raise ValueError(_("The size of the corner is missing"))
                if corner_key == "R":
                    row.corner = "R"
                else:
                    row.corner = "C"
                row.csize = size
                if corner_key in ("CA", "CB"):
                    angle = self.angle_entry.get_value()
                    if angle is None or not 0 < angle < 180:
                        raise ValueError(_("The chamfer angle is missing (0…180°)"))
                    row.cangle = angle
                    row.cref = corner_key[1]
            return row
        except (ValueError, SyntaxError, ZeroDivisionError) as error:
            message = str(error)
            if message == "empty":
                message = _("Enter X and Z")
            self.error_label.set_markup(error_markup(message))
            return None

    def on_append(self, _button):
        row = self.editor_row()
        if row is None:
            return
        index = self.selected_index()
        if index is None:
            position = len(self.rows)
        else:
            position = index + 1
        self.rows.insert(position, row)
        self.commit(position)

    def on_apply(self, _button):
        index = self.selected_index()
        row = self.editor_row()
        if row is None or index is None:
            return
        self.rows[index] = row
        self.commit(index)

    def on_delete(self, _button):
        index = self.selected_index()
        if index is not None:
            del self.rows[index]
            self.commit(min(index, len(self.rows) - 1))

    def _move(self, offset):
        index = self.selected_index()
        if index is None or not 0 <= index + offset < len(self.rows):
            return
        self.rows[index], self.rows[index + offset] = self.rows[index + offset], self.rows[index]
        self.commit(index + offset)

    def on_move_up(self, _button):
        self._move(-1)

    def on_move_down(self, _button):
        self._move(1)


# =========================================================================== parameter form

class ParamForm(Gtk.Grid):
    """Form built from a parameter list (cycles.P).

    - symbol (sym) in front of the label, matching the parameter picture
    - label/unit may depend on a choice field (label_by / unit_by)
    - fields with `when` are shown or hidden depending on a choice field
    - fields with `skey` are prefilled with the stock Ø (outside D, inside d)
    - fields of kind "tool" show the tool table as a choice
    - choice fields store keys; the shown texts are translated (P.labels)
    on_change(key, old_value) is called on every change.
    """

    def __init__(self, params: List[P], machine: Optional[Machine] = None, on_change=None,
                 tool_choices: Optional[Callable] = None):
        super().__init__(row_spacing=5, column_spacing=8, margin=6)
        self.params = [param for param in params if param.kind != "side"]
        self.machine = machine
        self.widgets: Dict[str, Gtk.Widget] = {}
        self.labels: Dict[str, Gtk.Label] = {}
        self.on_change = on_change
        self.tool_choices = tool_choices
        self.side = OUTSIDE
        self.stock = None
        self.locked = False
        self.choice_values: Dict[str, str] = {}
        row = 0
        for param in self.params:
            label = Gtk.Label(xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(22)
            self.labels[param.key] = label
            if param.help:
                label.set_tooltip_text(_(param.help))
            widget = self._create_widget(param)
            if param.kind == "contour":
                self.attach(widget, 0, row, 2, 1)
                row += 1
            elif param.kind == "text":
                self.attach(label, 0, row, 2, 1)
                self.attach(widget.scroller, 0, row + 1, 2, 1)
                row += 2
            else:
                self.attach(label, 0, row, 1, 1)
                self.attach(widget, 1, row, 1, 1)
                row += 1
            if param.help and param.kind not in ("contour", "text"):
                widget.set_tooltip_text(_(param.help))
            self.widgets[param.key] = widget
        self.update_labels()

    def _create_widget(self, param: P):
        if param.kind == "contour":
            editor = ContourEditor(self.on_contour_changed)
            editor.set_text(str(param.default))
            editor.param_key = param.key
            return editor
        if param.kind == "text":
            text_view = Gtk.TextView()
            text_view.set_monospace(True)
            text_view.set_left_margin(4)
            text_view.get_buffer().set_text(str(param.default))
            text_view.get_buffer().connect("changed", self.on_widget_changed, param.key)
            scroller = Gtk.ScrolledWindow()
            scroller.set_shadow_type(Gtk.ShadowType.IN)
            scroller.set_min_content_height(150)
            # the width follows the middle column (long lines scroll horizontally)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            scroller.set_min_content_width(120)
            scroller.add(text_view)
            text_view.scroller = scroller
            return text_view
        if param.kind in ("float", "int", "optfloat"):
            entry = NumEntry(allow_empty=param.kind == "optfloat")
            entry.set_value(param.default)
            entry.connect("changed", self.on_widget_changed, param.key)
            return entry
        if param.kind == "choice":
            combo = short_combo()
            for choice in param.choices:
                combo.append(choice, _(param.choice_label(choice)))
            combo.set_active_id(param.default)
            self.choice_values[param.key] = param.default
            combo.connect("changed", self.on_widget_changed, param.key)
            return combo
        if param.kind == "tool":
            combo = short_combo()
            self._fill_tools(combo, param.default)
            combo.connect("changed", self.on_widget_changed, param.key)
            return combo
        if param.kind == "bool":
            switch = Gtk.Switch(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
            switch.set_active(bool(param.default))
            switch.connect("notify::active", self.on_switch_changed, param.key)
            return switch
        entry = Gtk.Entry()
        entry.set_width_chars(8)            # long paths scroll inside the field
        entry.set_hexpand(True)
        entry.set_text(str(param.default))
        entry.connect("changed", self.on_widget_changed, param.key)
        return entry

    # ---- tool selection
    def _fill_tools(self, combo, selected):
        combo.remove_all()
        known = set()
        if self.tool_choices is not None:
            for number, text in self.tool_choices():
                combo.append(str(number), text)
                known.add(number)
        try:
            selected_number = int(selected)
        except (TypeError, ValueError):
            selected_number = 0
        if selected_number not in known:
            combo.append(str(selected_number),
                         _("T{number}  (not in the tool table)").format(number=selected_number))
        combo.set_active_id(str(selected_number))

    def refresh_tools(self):
        self.locked = True
        try:
            for param in self.params:
                if param.kind == "tool":
                    combo = self.widgets[param.key]
                    current = combo.get_active_id() or param.default
                    self._fill_tools(combo, current)
        finally:
            self.locked = False

    # ---- prefill with the stock
    def _stock_value(self, stock, side):
        if stock is None:
            return None
        if side == INSIDE:
            if stock.d > 0:
                return stock.d
            return None
        return stock.D

    def _number(self, key):
        try:
            return self.widgets[key].get_value()
        except (ValueError, SyntaxError, ZeroDivisionError, AttributeError):
            return None

    def _set(self, key, value):
        self.locked = True
        try:
            self.widgets[key].set_value(value)
        finally:
            self.locked = False

    def set_stock(self, stock, old=None, force=False):
        """Take over the stock Ø as long as the field was not set to something else deliberately."""
        self.stock = stock
        new_value = self._stock_value(stock, self.side)
        old_value = None
        if old is not None:
            old_value = self._stock_value(old, self.side)
        if new_value is None:
            return
        for param in self.params:
            if not param.skey:
                continue
            current = self._number(param.key)
            untouched = current is None or current == param.default
            was_stock = old_value is not None and current is not None and abs(current - old_value) < 1e-9
            if force or untouched or was_stock:
                self._set(param.key, new_value)

    def set_side(self, side):
        old_side = self.side
        self.side = side
        if self.stock is not None and old_side != side:
            old_value = self._stock_value(self.stock, old_side)
            new_value = self._stock_value(self.stock, side)
            for param in self.params:
                if param.skey and new_value is not None:
                    current = self._number(param.key)
                    was_stock = (old_value is not None and current is not None
                                 and abs(current - old_value) < 1e-9)
                    if current is None or was_stock or current == param.default:
                        self._set(param.key, new_value)
        self.update_labels()

    # ---- labels / visibility
    def _choice(self, key):
        widget = self.widgets.get(key)
        if isinstance(widget, Gtk.ComboBoxText):
            return widget.get_active_id()
        return None

    def update_labels(self):
        inside = self.side == INSIDE
        for param in self.params:
            if inside and param.label_in:
                text = param.label_in
            else:
                text = param.label
            if param.label_by:
                text = param.label_by[1].get(self._choice(param.label_by[0]), text)
            text = _(text)
            unit = param.unit
            if param.unit_by:
                unit = param.unit_by[1].get(self._choice(param.unit_by[0]), unit)
            if unit:
                text += f" [{_(unit)}]"
            if param.sym and param.kind != "contour":
                symbol = (f"<span foreground='{SYM_COLOR}'><b>"
                          f"{GLib.markup_escape_text(param.sym)}</b></span>  ")
            else:
                symbol = ""
            self.labels[param.key].set_markup(symbol + GLib.markup_escape_text(text))
        self.update_visibility()

    def update_visibility(self):
        inside = self.side == INSIDE
        for param in self.params:
            visible = param.show is None or (param.show == "in") == inside
            if param.when:
                visible = visible and self._choice(param.when[0]) in param.when[1]
            self.labels[param.key].set_visible(visible and param.kind != "contour")
            widget = self.widgets[param.key]
            scroller = getattr(widget, "scroller", None)
            if scroller is not None:
                scroller.set_visible(visible)
            else:
                widget.set_visible(visible)

    # ---- changes
    def on_contour_changed(self):
        self._changed("contour")

    def on_widget_changed(self, _widget, key):
        self._changed(key)

    def on_switch_changed(self, _switch, _property, key):
        self._changed(key)

    def _changed(self, key):
        if self.locked:
            return
        old_value = None
        param = None
        for candidate in self.params:
            if candidate.key == key:
                param = candidate
        if param is not None and param.kind == "choice":
            old_value = self.choice_values.get(key)
            self.choice_values[key] = self._choice(key)
            self.update_labels()
        if self.on_change:
            self.on_change(key, old_value)

    # ---- values
    def get_values(self, lenient=False):
        values, errors = {}, []
        for param in self.params:
            widget = self.widgets[param.key]
            try:
                if param.kind == "contour":
                    values[param.key] = widget.get_text()
                elif param.kind == "text":
                    buffer = widget.get_buffer()
                    values[param.key] = buffer.get_text(buffer.get_start_iter(),
                                                        buffer.get_end_iter(), True)
                elif param.kind in ("float", "optfloat"):
                    values[param.key] = widget.get_value()
                elif param.kind == "int":
                    values[param.key] = int(round(widget.get_value()))
                elif param.kind == "tool":
                    values[param.key] = int(widget.get_active_id() or 0)
                elif param.kind == "choice":
                    values[param.key] = widget.get_active_id()
                elif param.kind == "bool":
                    values[param.key] = widget.get_active()
                else:
                    values[param.key] = widget.get_text()
            except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
                errors.append(_("{field}: invalid number").format(field=param.sym or _(param.label)))
                if lenient:
                    values[param.key] = param.default
                continue
            value = values[param.key]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if param.min is not None and value < param.min:
                    errors.append(_("{field}: at least {limit}").format(
                        field=param.sym or _(param.label), limit=number_text(param.min)))
                if param.max is not None and value > param.max:
                    errors.append(_("{field}: at most {limit}").format(
                        field=param.sym or _(param.label), limit=number_text(param.max)))
        return values, errors

    def set_values(self, values):
        self.locked = True
        try:
            for param in self.params:
                if param.key not in values:
                    continue
                value = values[param.key]
                widget = self.widgets[param.key]
                if param.kind == "contour":
                    widget.set_text(str(value))
                elif param.kind == "text":
                    widget.get_buffer().set_text(str(value))
                elif param.kind in ("float", "int", "optfloat"):
                    widget.set_value(value)
                elif param.kind == "tool":
                    self._fill_tools(widget, value)
                elif param.kind == "choice":
                    if value in param.choices:
                        widget.set_active_id(value)
                        self.choice_values[param.key] = value
                elif param.kind == "bool":
                    widget.set_active(bool(value))
                else:
                    widget.set_text(str(value))
        finally:
            self.locked = False
        self.update_labels()


# =========================================================================== drawing area

class ViewArea(Gtk.DrawingArea):
    """Preview (zoom: mouse wheel, pan: left mouse button, double click: reset), parameter
    picture or tool drawing."""

    def __init__(self, machine: Machine):
        super().__init__()
        self.machine = machine
        self.mode = "preview"
        self.scene: Dict = {}
        self.help = (None, {}, False)
        self.tool = None
        self.show_rapids = True
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None
        self.set_size_request(160, 120)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK |
                        Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self.on_draw)
        self.connect("scroll-event", self.on_scroll)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)

    # ---- content
    def set_scene(self, scene):
        if scene:
            self.scene = scene
        else:
            self.scene = {}
        if self.mode == "preview":
            self.queue_draw()

    def set_help(self, key, values, inside):
        self.help = (key, values, inside)
        if self.mode == "help":
            self.queue_draw()

    def set_tool(self, tool):
        self.tool = tool
        if self.mode == "tool":
            self.queue_draw()

    def set_mode(self, mode):
        self.mode = mode
        self.queue_draw()

    def reset_view(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.queue_draw()

    # ---- zoom and pan
    def on_scroll(self, _widget, event):
        if self.mode != "preview":
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            factor = 1.25
        elif event.direction == Gdk.ScrollDirection.DOWN:
            factor = 1 / 1.25
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _delta_x, delta_y = event.get_scroll_deltas()
            if delta_y < 0:
                factor = 1.15
            elif delta_y > 0:
                factor = 1 / 1.15
            else:
                return False
        else:
            return False
        new_zoom = min(50.0, max(0.5, self.zoom * factor))
        center_x = self.get_allocated_width() / 2
        center_y = self.get_allocated_height() / 2
        ratio = new_zoom / self.zoom
        # the point under the mouse pointer stays where it is
        self.pan_x = event.x - center_x - (event.x - center_x - self.pan_x) * ratio
        self.pan_y = event.y - center_y - (event.y - center_y - self.pan_y) * ratio
        self.zoom = new_zoom
        self.queue_draw()
        return True

    def on_button_press(self, _widget, event):
        if self.mode != "preview" or event.button != 1:
            return False
        double_click = getattr(Gdk.EventType, "DOUBLE_BUTTON_PRESS", None)
        if double_click is None:
            double_click = getattr(Gdk.EventType, "_2BUTTON_PRESS")
        if event.type == double_click:
            self.reset_view()
            return True
        self.drag_start = (event.x, event.y, self.pan_x, self.pan_y)
        return True

    def on_button_release(self, _widget, event):
        if event.button == 1:
            self.drag_start = None
        return False

    def on_motion(self, _widget, event):
        if self.drag_start is None:
            return False
        start_x, start_y, start_pan_x, start_pan_y = self.drag_start
        self.pan_x = start_pan_x + event.x - start_x
        self.pan_y = start_pan_y + event.y - start_y
        self.queue_draw()
        return True

    # ---- drawing
    def on_draw(self, _widget, cr):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        front_tool = not self.machine.back_tool
        if self.mode == "help":
            key, values, inside = self.help
            helpdraw.draw_help(cr, width, height, key or "", values, inside, flip=front_tool)
        elif self.mode == "tool":
            tooldraw.draw_tool(cr, width, height, self.tool, flip=front_tool)
        else:
            scene = self.scene
            render.draw_preview(cr, width, height, scene.get("path", ()), scene.get("outlines", ()),
                                self.machine.back_tool, self.show_rapids,
                                font=max(9, min(12, height / 26)), stock=scene.get("stock"),
                                before=scene.get("before"), after=scene.get("after"),
                                highlight=scene.get("highlight"),
                                removed_color=scene.get("removed_color", render.REMOVE),
                                view=(self.zoom, self.pan_x, self.pan_y))
        return False


def message_markup(warnings, errors, ok_text="✔ OK"):
    parts = []
    for error in errors:
        parts.append(f"<span foreground='#e03030'><b>✖ {GLib.markup_escape_text(error)}</b></span>")
    for warning in warnings:
        parts.append(f"<span foreground='#d08000'>⚠ {GLib.markup_escape_text(warning)}</span>")
    if parts:
        return "\n".join(parts)
    return f"<span foreground='#40a040'>{GLib.markup_escape_text(ok_text)}</span>"


def message_area(max_height=90):
    label = Gtk.Label(xalign=0, yalign=0)
    label.set_line_wrap(True)
    label.set_selectable(True)
    label.get_style_context().add_class("msg")
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_min_content_height(22)
    scroller.set_max_content_height(max_height)
    scroller.set_propagate_natural_height(True)
    scroller.add(label)
    return scroller, label
