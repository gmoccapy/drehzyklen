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

import re
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
from .variables import VariableError, is_expression  # noqa: E402

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


class NumberField:
    """Number input on top of a Gtk.Entry: on-screen keypad (touch), decimal comma and simple
    calculations. Used for entries created in code (NumEntry) and for plain GtkEntry widgets
    from the Glade file (NumberField(builder.get_object(...))).

    Unknown attributes are passed on to the entry (set_sensitive, set_text, ...).
    """

    def __init__(self, entry: Gtk.Entry, allow_empty=False, width=None):
        self.entry = entry
        self.allow_empty = allow_empty
        self.variables: Optional[Callable] = None     # () -> Variables: $ in the keypad
        # calculation with $variables: the field shows its result and the formula again while
        # it is edited (blue bar in front, red if it cannot be calculated)
        self.formula: Optional[str] = None
        self.switching = False                  # text replaced by formula/result, no change
        self.result_shown = False               # the field shows the result, not the formula
        self.numpad_open = False
        if width is not None:
            entry.set_width_chars(width)
        entry.set_alignment(1.0)
        entry.set_hexpand(True)
        entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "input-dialpad-symbolic")
        entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Keypad"))
        entry.connect("icon-press", self.on_numpad)
        entry.connect("focus-in-event", self.on_focus_in)
        entry.connect("focus-out-event", self.on_focus_out)

    def __getattr__(self, name):
        return getattr(self.entry, name)

    def get_value(self) -> Optional[float]:
        text = self.entry.get_text().strip().replace(",", ".")
        if not text:
            if self.allow_empty:
                return None
            raise ValueError("empty")
        return eval_simple(text)

    def set_value(self, value):
        self.formula = None
        self.result_shown = False
        self._mark(None)
        self.entry.set_text(number_text(value))

    # ---- calculated fields
    def editing(self) -> bool:
        return self.entry.has_focus() or self.numpad_open

    def text(self) -> str:
        """What the field holds: the formula of a calculated field, otherwise its text."""
        if self.formula is not None and not self.editing():
            return self.formula
        return self.entry.get_text()

    def set_formula(self, formula: str):
        self.formula = formula
        if self.editing():
            self._show(formula)
            self.result_shown = False
            self._mark("calculated")
        else:
            self.show_result()

    def show_result(self):
        """Show the result of the formula (or the formula itself if it cannot be calculated)."""
        if self.formula is None or self.editing():
            return
        try:
            value = self.variables().evaluate(self.formula) if self.variables else None
        except (VariableError, ValueError, TypeError):
            value = None
        if value is None:
            self._show(self.formula)
            self.result_shown = True
            self._mark("calculated-error")
            self.entry.set_tooltip_text(_("Cannot be calculated: {formula}").format(
                formula=self.formula))
        else:
            self._show(number_text(value))
            self.result_shown = True
            self._mark("calculated")
            self.entry.set_tooltip_text(_("Calculated: {formula}").format(formula=self.formula))

    def _show(self, text):
        if self.entry.get_text() == text:
            return
        self.switching = True
        try:
            self.entry.set_text(text)
        finally:
            self.switching = False

    def _mark(self, css_class):
        context = self.entry.get_style_context()
        for name in ("calculated", "calculated-error"):
            if name == css_class:
                context.add_class(name)
            else:
                context.remove_class(name)
        if css_class is None and self.variables is not None:
            self.entry.set_tooltip_text(getattr(self.entry, "help_text", None))

    def on_focus_in(self, *_args):
        if self.formula is not None:
            self._show(self.formula)
            self.result_shown = False
            self._mark("calculated")
            GLib.idle_add(self._cursor_to_end)
        return False

    def _cursor_to_end(self):
        self.entry.set_position(-1)
        return False

    def on_focus_out(self, *_args):
        if not self.numpad_open:
            GLib.idle_add(self.finish_editing)
        return False

    def finish_editing(self):
        """After editing: a text with $ becomes the formula and shows its result."""
        if self.editing() or self.variables is None or self.result_shown:
            return False                        # still edited, or already finished
        text = self.entry.get_text().strip()
        if "$" in text:
            self.formula = text
            self.show_result()
        elif self.formula is not None:
            self.formula = None
            self._mark(None)
        return False

    # the extended keys (calculation, $variables) stay open for the next keypad once shown
    extended = False

    def on_numpad(self, *_args):
        popover = Gtk.Popover.new(self.entry)
        self.numpad_open = True
        if self.formula is not None:
            self._show(self.formula)
            self.result_shown = False
            self._mark("calculated")
        popover.connect("closed", self.on_numpad_closed)
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_homogeneous(False)
        stack.set_vhomogeneous(False)
        stack.set_interpolate_size(True)
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

        # extended keys: calculation and $variables, shown to the right
        extra = Gtk.Grid(row_spacing=4, column_spacing=4, margin_start=4)
        extra.get_style_context().add_class("numpad")
        extra_keys = [("+", "+"), ("(", "("), ("×", "*"), (")", ")"), ("÷", "/")]
        for index, (label, key) in enumerate(extra_keys):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.on_numpad_key, key)
            extra.attach(button, index % 2, index // 2, 1, 1)
        variable_button = Gtk.Button(label="$")
        if self.variables is None:
            variable_button.set_sensitive(False)
            variable_button.set_tooltip_text(_("No variables in this field"))
        else:
            variable_button.set_tooltip_text(_("Choose a variable"))
            variable_button.connect("clicked", self.on_numpad_variables, stack)
        extra.attach(variable_button, 1, 2, 1, 1)
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        revealer.add(extra)
        revealer.set_reveal_child(NumberField.extended)
        grid.attach(revealer, 4, 0, 1, 4)
        more = Gtk.ToggleButton(label=self.more_label())
        more.set_tooltip_text(_("Calculation and $variables"))
        more.set_active(NumberField.extended)
        more.connect("toggled", self.on_numpad_more, revealer)
        grid.attach(more, 0, 4, 5, 1)

        stack.add_named(grid, "keys")
        stack.add_named(Gtk.Box(), "variables")
        popover.add(stack)
        stack.show_all()
        popover.popup()

    @staticmethod
    def more_label():
        return "◂" if NumberField.extended else "+ × ÷ ( ) $  ▸"

    def on_numpad_more(self, button, revealer):
        NumberField.extended = button.get_active()
        revealer.set_reveal_child(NumberField.extended)
        button.set_label(self.more_label())

    def on_numpad_variables(self, _button, stack):
        """Page with one large button per variable (value and meaning)."""
        old_page = stack.get_child_by_name("variables")
        if old_page is not None:
            stack.remove(old_page)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=6)
        page.set_size_request(300, -1)
        back = Gtk.Button(label="◂ " + _("Back"))
        back.connect("clicked", lambda _b: stack.set_visible_child_name("keys"))
        page.pack_start(back, False, False, 0)
        try:
            variables = self.variables()
        except Exception:                       # noqa: BLE001 - empty list, but never a crash
            variables = None
        names = variables.names() if variables is not None else []
        if not names:
            page.pack_start(Gtk.Label(label=_("No variables defined")), False, False, 0)
        else:
            values = variables.values()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for name in names:
                button = Gtk.Button()
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                row = Gtk.Box(spacing=12)
                name_label = Gtk.Label(xalign=0)
                name_label.set_markup("<b>$" + GLib.markup_escape_text(name) + "</b>")
                row.pack_start(name_label, False, False, 0)
                value = values.get(name)
                row.pack_end(Gtk.Label(label="?" if value is None else "= " + number_text(value)),
                             False, False, 0)
                content.pack_start(row, False, False, 0)
                description = variables.description(name)
                if description:
                    # the meaning in a second, smaller line (wraps instead of being cut)
                    hint = Gtk.Label(label=description, xalign=0)
                    hint.set_line_wrap(True)
                    hint.set_max_width_chars(34)
                    hint.get_style_context().add_class("hint")
                    content.pack_start(hint, False, False, 0)
                button.add(content)
                button.get_style_context().add_class("varbutton")
                button.connect("clicked", self.on_numpad_variable, name, stack)
                box.pack_start(button, False, False, 0)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_propagate_natural_height(True)
            scroller.set_max_content_height(360)
            scroller.add(box)
            page.pack_start(scroller, True, True, 0)
        page.show_all()
        stack.add_named(page, "variables")
        stack.set_visible_child_name("variables")

    def on_numpad_variable(self, _button, name, stack):
        text = "$" + name
        position = self.entry.get_position()
        self.entry.insert_text(text, position)
        self.entry.set_position(position + len(text))
        stack.set_visible_child_name("keys")

    def on_numpad_key(self, _button, key):
        position = self.entry.get_position()
        self.entry.insert_text(key, position)
        self.entry.set_position(position + 1)

    def on_numpad_backspace(self, _button):
        position = self.entry.get_position()
        if position > 0:
            self.entry.delete_text(position - 1, position)

    def on_numpad_clear(self, _button):
        self.entry.set_text("")

    def on_numpad_closed(self, _popover):
        self.numpad_open = False
        GLib.idle_add(self.finish_editing)

    def on_numpad_ok(self, _button, popover):
        popover.popdown()
        self.entry.activate()


TYPED_VARIABLE = re.compile(r"\$([A-Za-z0-9_]*)$")


class VariableCompleter:
    """Autocompletion for $variables in a Gtk.Entry or Gtk.TextView.

    Typing $ opens a list of the variables under the field; every further letter narrows it
    (names starting with the letters first, then names containing them). Up/Down choose,
    Enter or Tab take the variable, Escape closes the list; a click takes it as well.
    variables: () -> Variables (own variables of the program and the stock variables).
    """

    MAX_ROWS = 12

    def __init__(self, widget: Gtk.Widget, variables: Callable):
        self.widget = widget
        self.variables = variables
        self.names: List[str] = []
        self.inserting = False
        self.popover = Gtk.Popover.new(widget)
        self.popover.set_modal(False)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.get_style_context().add_class("varlist")
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list_box.set_can_focus(False)
        self.list_box.connect("row-activated", self.on_row_activated)
        self.popover.add(self.list_box)
        if isinstance(widget, Gtk.TextView):
            widget.get_buffer().connect("changed", self.on_changed)
            widget.get_buffer().connect("notify::cursor-position", self.on_cursor_moved)
        else:
            widget.connect("changed", self.on_changed)
            widget.connect("notify::cursor-position", self.on_cursor_moved)
        widget.connect("key-press-event", self.on_key_press)
        widget.connect("focus-out-event", self.on_focus_out)

    # ---------------------------------------------------------------- text access
    def text_before_cursor(self) -> str:
        if isinstance(self.widget, Gtk.TextView):
            buffer = self.widget.get_buffer()
            cursor = buffer.get_iter_at_mark(buffer.get_insert())
            line_start = cursor.copy()
            line_start.set_line_offset(0)
            return buffer.get_text(line_start, cursor, True)
        return self.widget.get_text()[:self.widget.get_position()]

    def replace_typed(self, typed_length: int, text: str):
        """Replace the typed '$abc' in front of the cursor by text."""
        if isinstance(self.widget, Gtk.TextView):
            buffer = self.widget.get_buffer()
            end = buffer.get_iter_at_mark(buffer.get_insert())
            start = end.copy()
            start.backward_chars(typed_length)
            buffer.begin_user_action()
            buffer.delete(start, end)
            buffer.insert(start, text)
            buffer.end_user_action()
            buffer.place_cursor(start)
        else:
            position = self.widget.get_position()
            self.widget.delete_text(position - typed_length, position)
            new_position = self.widget.insert_text(text, position - typed_length)
            self.widget.set_position(new_position)

    def cursor_rectangle(self) -> Gdk.Rectangle:
        rectangle = Gdk.Rectangle()
        if isinstance(self.widget, Gtk.TextView):
            buffer = self.widget.get_buffer()
            location = self.widget.get_iter_location(buffer.get_iter_at_mark(buffer.get_insert()))
            x, y = self.widget.buffer_to_window_coords(Gtk.TextWindowType.WIDGET,
                                                       location.x, location.y)
            rectangle.x, rectangle.y = x, y
            rectangle.width, rectangle.height = 1, location.height
        else:
            allocation = self.widget.get_allocation()
            rectangle.x, rectangle.y = 0, 0
            rectangle.width, rectangle.height = allocation.width, allocation.height
        return rectangle

    # ---------------------------------------------------------------- list
    def on_changed(self, *_args):
        if self.inserting:
            return
        GLib.idle_add(self.update)

    def on_cursor_moved(self, *_args):
        if self.popover.get_visible() and not self.inserting:
            GLib.idle_add(self.update)

    def update(self):
        if not self.widget.has_focus():
            self.popover.popdown()
            return False
        match = TYPED_VARIABLE.search(self.text_before_cursor())
        if match is None:
            self.popover.popdown()
            return False
        typed = match.group(1).lower()
        try:
            variables = self.variables()
        except Exception:                       # noqa: BLE001 - no list, but never a crash
            variables = None
        if variables is None:
            self.popover.popdown()
            return False
        names = variables.names()
        starting = [name for name in names if name.lower().startswith(typed)]
        containing = [name for name in names
                      if typed in name.lower() and name not in starting]
        self.names = (starting + containing)[:self.MAX_ROWS]
        if not self.names or (len(self.names) == 1 and self.names[0].lower() == typed):
            self.popover.popdown()
            return False
        values = variables.values()
        for row in self.list_box.get_children():
            self.list_box.remove(row)
        for name in self.names:
            self.list_box.add(self._row(name, values.get(name), variables.description(name)))
        self.list_box.show_all()
        self.list_box.select_row(self.list_box.get_row_at_index(0))
        self.popover.set_pointing_to(self.cursor_rectangle())
        if not self.popover.get_visible():
            self.popover.popup()
        return False

    @staticmethod
    def _row(name, value, description):
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=3, margin_bottom=3)
        name_label = Gtk.Label(xalign=0)
        name_label.set_markup("<b>$" + GLib.markup_escape_text(name) + "</b>")
        box.pack_start(name_label, False, False, 0)
        value_text = "?" if value is None else "= " + number_text(value)
        value_label = Gtk.Label(label=value_text, xalign=1)
        box.pack_end(value_label, False, False, 0)
        if description:
            hint = Gtk.Label(label=description, xalign=0)
            hint.set_ellipsize(Pango.EllipsizeMode.END)
            hint.set_max_width_chars(30)
            hint.get_style_context().add_class("hint")
            box.pack_start(hint, True, True, 0)
        row = Gtk.ListBoxRow()
        row.set_can_focus(False)
        row.add(box)
        row.variable_name = name
        return row

    def take(self, name):
        match = TYPED_VARIABLE.search(self.text_before_cursor())
        if match is None:
            return
        self.inserting = True
        try:
            self.replace_typed(len(match.group(0)), "$" + name)
        finally:
            self.inserting = False
        self.popover.popdown()
        self.widget.grab_focus()
        if isinstance(self.widget, Gtk.Entry):
            # grab_focus selects the whole text of an entry - put the cursor back
            position = self.widget.get_position()
            self.widget.select_region(position, position)

    def on_row_activated(self, _list_box, row):
        self.take(row.variable_name)

    def on_key_press(self, _widget, event):
        if not self.popover.get_visible():
            return False
        key = event.keyval
        if key in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_KP_Down, Gdk.KEY_KP_Up):
            row = self.list_box.get_selected_row()
            index = row.get_index() if row is not None else -1
            step = 1 if key in (Gdk.KEY_Down, Gdk.KEY_KP_Down) else -1
            index = max(0, min(len(self.names) - 1, index + step))
            self.list_box.select_row(self.list_box.get_row_at_index(index))
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Enter):
            row = self.list_box.get_selected_row()
            if row is not None:
                self.take(row.variable_name)
                return True
            return False
        if key == Gdk.KEY_Escape:
            self.popover.popdown()
            return True
        return False

    def on_focus_out(self, *_args):
        self.popover.popdown()
        return False


class NumEntry(Gtk.Entry):
    """Gtk.Entry with the behaviour of NumberField (for fields created in code)."""

    def __init__(self, allow_empty=False, width=8):
        super().__init__()
        self.field = NumberField(self, allow_empty, width)

    @property
    def allow_empty(self):
        return self.field.allow_empty

    def get_value(self) -> Optional[float]:
        return self.field.get_value()

    def set_value(self, value):
        self.field.set_value(value)

    def field_text(self) -> str:
        return self.field.text()

    def set_formula(self, formula: str):
        self.field.set_formula(formula)


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


def ellipsize_combo(combo):
    """Shorten the texts of a choice box with … so that long entries do not widen the column
    (the full text is shown in the opened list)."""
    for cell in combo.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
    combo.set_hexpand(True)
    return combo


SUITABLE_COLOR = "#cdebc4"         # light green: the tool fits this operation
UNSUITABLE_COLOR = "#fbe7a0"       # light yellow: only with »Release tool despite warning«
STATUS_TEXT_COLOR = "#1e1e1e"


class ToolCombo(Gtk.ComboBox):
    """Tool selection whose entries are coloured: green = fits the operation, yellow = not
    suitable (can only be used with »Release tool despite warning«)."""

    def __init__(self):
        self.store = Gtk.ListStore(str, str, str, str)   # id, text, background, foreground
        super().__init__(model=self.store)
        self.set_id_column(0)
        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        self.pack_start(renderer, True)
        self.add_attribute(renderer, "text", 1)
        self.add_attribute(renderer, "cell-background", 2)
        self.add_attribute(renderer, "foreground", 3)
        self.set_hexpand(True)

    def append(self, key, text):
        self.store.append([key, text, None, None])

    def remove_all(self):
        self.store.clear()

    def set_status(self, status):
        """status: {tool number: True (fits) / False (not suitable)}; missing = no colour."""
        for row in self.store:
            try:
                fits = status.get(int(row[0]))
            except (TypeError, ValueError):
                fits = None
            if fits is None:
                row[2] = None
                row[3] = None
            elif fits:
                row[2] = SUITABLE_COLOR
                row[3] = STATUS_TEXT_COLOR
            else:
                row[2] = UNSUITABLE_COLOR
                row[3] = STATUS_TEXT_COLOR


class ToolPicture(Gtk.DrawingArea):
    """Small picture of the chosen tool (in the cycles, next to the tool selection)."""

    def __init__(self, lookup: Callable, width=84, height=64):
        super().__init__()
        self.lookup = lookup
        self.number = None
        self.set_size_request(width, height)
        self.set_halign(Gtk.Align.START)
        self.connect("draw", self.on_draw)

    def show_tool(self, number):
        self.number = number
        self.queue_draw()

    def on_draw(self, widget, cr):
        tool = None
        if self.number is not None:
            tool = self.lookup(self.number)
        tooldraw.draw_tool(cr, widget.get_allocated_width(), widget.get_allocated_height(), tool,
                           flip=ICON_STATE["flipped"](), compact=True)
        return False


def short_combo():
    return ellipsize_combo(Gtk.ComboBoxText())


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

    def __init__(self, on_change: Callable, variables: Optional[Callable] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.on_change = on_change
        self.variables = variables              # () -> Variables, for $ in the contour
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
        if self.variables is not None:
            self.text_view.completer = VariableCompleter(self.text_view, self.variables)
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
        if self.has_variables(text):
            GLib.idle_add(self.mode_bar.buttons["text"].set_active, True)

    def has_variables(self, text) -> bool:
        return "$" in text or "[" in text

    def parse(self, text) -> bool:
        """Check the text; with $variables the calculated contour is checked and shown, it can
        then only be edited as text (the list would replace the calculations by numbers)."""
        calculated = text
        if self.has_variables(text):
            if self.variables is None:
                return False
            try:
                calculated = self.variables().substitute(text)
            except VariableError as error:
                self.error_label.set_markup(error_markup(str(error)))
                return False
        try:
            self.rows = parse_rows(calculated)
        except ContourError as error:
            self.error_label.set_markup(error_markup(str(error)))
            return False
        if self.has_variables(text):
            self.error_label.set_text(_("Contour with $variables - the list shows the calculated "
                                        "values, change it as text"))
        else:
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
        text = self.get_text()
        if key == "list" and (not self.parse(text) or self.has_variables(text)):
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
                 tool_choices: Optional[Callable] = None, tool_lookup: Optional[Callable] = None,
                 variables: Optional[Callable] = None):
        super().__init__(row_spacing=5, column_spacing=8, margin=6)
        self.params = [param for param in params if param.kind != "side"]
        self.machine = machine
        self.widgets: Dict[str, Gtk.Widget] = {}
        self.labels: Dict[str, Gtk.Label] = {}
        self.on_change = on_change
        self.tool_choices = tool_choices
        self.tool_lookup = tool_lookup
        self.variables = variables              # () -> Variables, for calculations with $
        self.tool_picture: Optional[ToolPicture] = None
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
            elif param.kind == "tool" and tool_lookup is not None:
                # pictogram of the chosen tool under the label
                self.tool_picture = ToolPicture(tool_lookup)
                label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                label_box.pack_start(label, False, False, 0)
                label_box.pack_start(self.tool_picture, False, False, 0)
                self.attach(label_box, 0, row, 1, 1)
                widget.set_valign(Gtk.Align.START)
                self.attach(widget, 1, row, 1, 1)
                row += 1
            else:
                self.attach(label, 0, row, 1, 1)
                self.attach(widget, 1, row, 1, 1)
                row += 1
            if param.help and param.kind not in ("contour", "text"):
                widget.set_tooltip_text(_(param.help))
                widget.help_text = _(param.help)
            if isinstance(widget, Gtk.Entry):
                # Enter moves on to the next field
                widget.connect("activate", self.on_entry_activate, param.key)
            self.widgets[param.key] = widget
        self.update_labels()

    def on_entry_activate(self, _entry, key):
        """Enter in a field: focus the next visible and usable field of the form."""
        keys = [param.key for param in self.params]
        start = keys.index(key) + 1
        for next_key in keys[start:]:
            widget = self.widgets.get(next_key)
            if widget is None or isinstance(widget, ContourEditor):
                continue
            if widget.get_visible() and widget.get_sensitive() and widget.get_can_focus():
                widget.grab_focus()
                return

    def _create_widget(self, param: P):
        if param.kind == "contour":
            editor = ContourEditor(self.on_contour_changed, self.variables)
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
            if self.variables is not None:
                entry.completer = VariableCompleter(entry, self.variables)
                entry.field.variables = self.variables
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
            combo = ToolCombo()
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
        if self.tool_picture is not None:
            self.tool_picture.show_tool(selected_number)

    def set_tool_status(self, status):
        """Colour the tool selection: {tool number: fits}."""
        for param in self.params:
            if param.kind == "tool":
                self.widgets[param.key].set_status(status)

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

    def _has_variable(self, key):
        """The field holds a calculation with $variables (it is never overwritten)."""
        widget = self.widgets.get(key)
        return isinstance(widget, NumEntry) and "$" in widget.field_text()

    def refresh_calculated(self):
        """Show the results of the calculated fields again (variables or stock changed)."""
        for widget in self.widgets.values():
            if isinstance(widget, NumEntry):
                widget.field.show_result()

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
            if not param.skey or self._has_variable(param.key):
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
                if param.skey and new_value is not None and not self._has_variable(param.key):
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

    def on_widget_changed(self, widget, key):
        if isinstance(widget, NumEntry) and widget.field.switching:
            return                              # only formula <-> result, the value stays
        if isinstance(widget, ToolCombo) and self.tool_picture is not None:
            try:
                self.tool_picture.show_tool(int(widget.get_active_id()))
            except (TypeError, ValueError):
                self.tool_picture.show_tool(None)
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
                elif param.kind in ("float", "int", "optfloat") and "$" in widget.field_text():
                    # calculation with $variables: kept as entered, checked with the values
                    text = widget.field_text().strip()
                    values[param.key] = text
                    if self.variables is not None:
                        try:
                            number = self.variables().evaluate(text)
                        except VariableError as error:
                            errors.append(f"{param.sym or _(param.label)}: {error}")
                            continue
                        self._check_limits(param, number, errors)
                    continue
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
                self._check_limits(param, value, errors)
        return values, errors

    @staticmethod
    def _check_limits(param, value, errors):
        if param.min is not None and value < param.min:
            errors.append(_("{field}: at least {limit}").format(
                field=param.sym or _(param.label), limit=number_text(param.min)))
        if param.max is not None and value > param.max:
            errors.append(_("{field}: at most {limit}").format(
                field=param.sym or _(param.label), limit=number_text(param.max)))

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
                    if is_expression(value):
                        widget.set_formula(value)       # calculation with $variables
                    else:
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
