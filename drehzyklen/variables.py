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
"""Variables for part variants.

Every number field (and the contour text) may contain a calculation with variables instead of
a number, e.g. ``$StockDiameter - 2`` or ``$Length / 2 + 5``. In the contour text a calculation
is written in brackets: ``X[$Diameter] Z[-$Length]``; a single variable also works without
brackets: ``X$Diameter``.

Variables
---------
* own variables of the program (``Name = calculation``, in the order given; a variable may use
  the ones above it and the stock variables),
* stock variables, taken from the stock tab: ``$StockDiameter`` (D), ``$StockInner`` (d),
  ``$StockStickout`` (L), ``$StockFace`` (facing allowance a).

The fields keep the calculation (it is saved in the project); the cycles calculate with the
values. Changing a variable changes every cycle that uses it - a variant of the part.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .i18n import N_, _

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
VARIABLE_PATTERN = re.compile(r"\$(" + NAME + r")")
NAME_PATTERN = re.compile(r"^" + NAME + r"$")
BRACKET_PATTERN = re.compile(r"\[([^\[\]]*)\]")
ALLOWED_CHARACTERS = set("0123456789.+-*/() eE")

# stock variable -> key of the stock values
STOCK_VARIABLES = {"StockDiameter": "D", "StockInner": "d", "StockStickout": "L",
                   "StockFace": "face"}
STOCK_VARIABLE_HELP = {"StockDiameter": N_("outer Ø of the stock (D)"),
                       "StockInner": N_("inner Ø of the stock (d)"),
                       "StockStickout": N_("length out of the chuck (L)"),
                       "StockFace": N_("facing allowance in front of Z0 (a)")}


class VariableError(ValueError):
    """A calculation cannot be evaluated (unknown variable, loop, syntax)."""


def is_expression(value: Any) -> bool:
    """A field value that has to be calculated (text with a variable)."""
    return isinstance(value, str) and "$" in value


def calculate(text: str, lookup: Callable[[str], float]) -> float:
    """Calculate '40 - 2*1.5' or '$StockDiameter - 2' (lookup gives the variable values)."""
    def replace(match):
        value = lookup(match.group(1))
        return f"({value!r})"
    expression = VARIABLE_PATTERN.sub(replace, text.replace(",", "."))
    if not expression.strip():
        raise VariableError(_("empty calculation"))
    if not set(expression) <= ALLOWED_CHARACTERS:
        raise VariableError(_("»{text}« is not a calculation").format(text=text))
    try:
        result = eval(expression, {"__builtins__": {}}, {})   # digits and operators only
    except (SyntaxError, ZeroDivisionError, TypeError, NameError) as error:
        raise VariableError(_("»{text}« cannot be calculated ({error})").format(
            text=text, error=error)) from error
    return float(result)


def parse_definitions(text: str) -> List[Tuple[str, str, str]]:
    """Lines 'Name = calculation ; comment' -> [(name, calculation, comment)]."""
    definitions = []
    names = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        content, _separator, comment = line.partition(";")
        if not content.strip():
            continue
        name, equals, expression = content.partition("=")
        name = name.strip().lstrip("$")
        if not equals or not NAME_PATTERN.match(name) or not expression.strip():
            raise VariableError(_("Line {line}: write »Name = value« (letters, digits, _)")
                                .format(line=line_number))
        if name in STOCK_VARIABLES:
            raise VariableError(_("Line {line}: ${name} is a stock variable - set it in the "
                                  "stock tab").format(line=line_number, name=name))
        if name in names:
            raise VariableError(_("Line {line}: ${name} is defined twice").format(
                line=line_number, name=name))
        names.add(name)
        definitions.append((name, expression.strip(), comment.strip()))
    return definitions


def definitions_text(definitions: List[Tuple[str, str, str]]) -> str:
    lines = []
    for name, expression, comment in definitions:
        line = f"{name} = {expression}"
        if comment:
            line += f"  ; {comment}"
        lines.append(line)
    return "\n".join(lines)


class Variables:
    """Own variables of a program plus the stock variables (from the raw stock values)."""

    def __init__(self, definitions: Optional[List[Tuple[str, str, str]]] = None,
                 stock_values: Optional[Dict[str, Any]] = None):
        self.expressions: Dict[str, Any] = {}
        for name, expression, _comment in definitions or []:
            self.expressions[name] = expression
        for name, key in STOCK_VARIABLES.items():
            if stock_values is not None and key in stock_values:
                self.expressions[name] = stock_values[key]
        self.cache: Dict[str, float] = {}
        self.active: List[str] = []

    def names(self) -> List[str]:
        return list(self.expressions)

    def value(self, name: str) -> float:
        if name in self.cache:
            return self.cache[name]
        if name not in self.expressions:
            raise VariableError(_("unknown variable ${name}").format(name=name))
        if name in self.active:
            loop = " → ".join("$" + item for item in self.active + [name])
            raise VariableError(_("the variables refer to each other: {loop}").format(loop=loop))
        self.active.append(name)
        try:
            expression = self.expressions[name]
            if isinstance(expression, str):
                result = calculate(expression, self.value)
            else:
                result = float(expression)
        finally:
            self.active.pop()
        self.cache[name] = result
        return result

    def evaluate(self, value: Any) -> Any:
        """Value of a field: calculations are evaluated, everything else stays."""
        if is_expression(value):
            return calculate(value, self.value)
        return value

    def substitute(self, text: str) -> str:
        """Contour text: '[calculation]' and '$Name' are replaced by their values."""
        def bracket(match):
            return _number_text(calculate(match.group(1), self.value))
        text = BRACKET_PATTERN.sub(bracket, text)

        def single(match):
            return _number_text(self.value(match.group(1)))
        return VARIABLE_PATTERN.sub(single, text)

    def values(self) -> Dict[str, Optional[float]]:
        """All variables with their value (None where it cannot be calculated)."""
        result = {}
        for name in self.expressions:
            try:
                result[name] = self.value(name)
            except VariableError:
                result[name] = None
        return result


NUMBER_KINDS = ("float", "int", "optfloat")


def resolve_values(params, raw: Dict[str, Any], variables: Optional[Variables]):
    """Field values with every calculation replaced by its value (raises VariableError)."""
    if variables is None:
        variables = Variables()
    resolved = dict(raw)
    kinds = {param.key: param.kind for param in params}
    for key, value in raw.items():
        kind = kinds.get(key)
        if kind in NUMBER_KINDS and is_expression(value):
            number = variables.evaluate(value)
            if kind == "int":
                number = int(round(number))
            resolved[key] = number
        elif kind == "contour" and isinstance(value, str) and ("$" in value or "[" in value):
            resolved[key] = variables.substitute(value)
    return resolved


def _number_text(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text
