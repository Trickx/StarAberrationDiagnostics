"""Reads the texts of the dialog controls from StarAberrationDiagnostics.js:
the caption (.text), the field label (fieldLabel("...")) and the tooltip
(.toolTip) of a control, by its member name (this.<name>).

Only string literals are taken over; a tooltip that is built from other
expressions keeps its literal parts (see dynamic()). check.py uses this to
compare the dialog reference with the script. Taken over from the Koma
repository (docs/src/dialog_text.py).

Usage: python3 dialog_text.py [names]   (prints the texts, to check them)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "StarAberrationDiagnostics.js")   # check.py sets the script to read

_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _source():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def _unescape(s):
    # JS escapes in the literals: ±, \", \\ ...
    return json.loads('"' + s + '"')


def _expression(src, start):
    """The text from start up to the ';' that ends the statement (outside strings)."""
    i, n = start, len(src)
    depth = 0
    while i < n:
        ch = src[i]
        if ch == '"':
            m = _STRING.match(src, i)
            i = m.end()
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth <= 0:
            return src[start:i]
        i += 1
    return src[start:]


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([A-Z][A-Z0-9_]*)\b')


def _concat(expr, consts=None):
    """(text, complete): the joined string literals of a concatenation - with
    constants (UPPER_CASE, from consts) put in - and whether the expression
    consisted of nothing else."""
    consts = consts or {}
    parts, rest = [], []
    pos = 0
    for m in _TOKEN.finditer(expr):
        if m.group(2) is not None and m.group(2) not in consts:
            continue
        rest.append(expr[pos:m.start()])
        pos = m.end()
        parts.append(_unescape(m.group(1)) if m.group(1) is not None else consts[m.group(2)])
    rest.append(expr[pos:])
    complete = re.fullmatch(r"[\s+()]*", "".join(rest)) is not None
    return "".join(parts), complete


class DialogText:
    def __init__(self):
        self.src = _source()
        # Top-level constants with a literal value: const NAME = "..." or a number.
        self.consts = {}
        for m in re.finditer(r'^const ([A-Z][A-Z0-9_]*) = (?:"((?:[^"\\]|\\.)*)"|(-?[0-9.]+));', self.src, re.M):
            self.consts[m.group(1)] = _unescape(m.group(2)) if m.group(2) is not None else m.group(3)

    def _assignment(self, target):
        m = re.search(r"\b" + re.escape(target) + r"\s*=(?!=)", self.src)
        if m is None:
            return None
        return _expression(self.src, m.end())

    def text(self, name):
        expr = self._assignment("this." + name + ".text")
        return _concat(expr, self.consts)[0] if expr is not None else None

    def label(self, name):
        m = re.search(r"this\." + re.escape(name) + r'\s*=\s*fieldLabel\("((?:[^"\\]|\\.)*)"\)', self.src)
        return _unescape(m.group(1)) if m else None

    def tooltip(self, name):
        """The tooltip as HTML (the script's rich text: <p>, <b>, <i>, <br>)."""
        expr = self._assignment("this." + name + ".toolTip")
        if expr is None:
            return None
        text, complete = _concat(expr, self.consts)
        if not complete:
            # e.g. this.fwhmGridHelp - a member holding the text
            ref = re.fullmatch(r"\s*this\.(\w+)\s*", expr)
            if ref:
                return self.member(ref.group(1))
        return text

    def member(self, name):
        expr = self._assignment("this." + name)
        return _concat(expr, self.consts)[0] if expr is not None else None

    def items(self, name):
        """The entries of a combo box (addItem("...") calls on it)."""
        return [_unescape(m.group(1)) for m in
                re.finditer(r"this\." + re.escape(name) + r'\.addItem\("((?:[^"\\]|\\.)*)"\)', self.src)]

    def names(self, const):
        """The name: entries of a constant array of objects (e.g. OPTICS_PROFILES),
        or the strings of a constant array of strings (e.g. MOUNT_TYPES)."""
        m = re.search(r"^const " + re.escape(const) + r" = \[", self.src, re.M)
        body = _expression(self.src, m.end())
        named = re.findall(r'\{\s*name:\s*"((?:[^"\\]|\\.)*)"', body)
        return [_unescape(x) for x in (named or _STRING.findall(body))]

    def dynamic(self, name):
        """True when the tooltip contains more than string literals."""
        expr = self._assignment("this." + name + ".toolTip")
        return expr is not None and not _concat(expr, self.consts)[1]


if __name__ == "__main__":
    t = DialogText()
    for n in sys.argv[1:]:
        print(f"== {n}\n  text:  {t.text(n)}\n  label: {t.label(n)}\n  items: {t.items(n)}\n"
              f"  tip ({'dynamic' if t.dynamic(n) else 'static'}): {t.tooltip(n)}\n")
