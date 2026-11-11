"""Compares the dialog reference (_sad_dialog/en|de/*.md) with the dialog of
StarAberrationDiagnostics.js.

Every setting in the Markdown carries the member name of its control as id:
    ### Debayer (SuperPixel) before star detection
    {: #debayerCheck .checkbox}
(a group whose tooltip belongs to a label of its own: "## Side" + "{: #seriesSideLabel}").
script.lock.json holds the texts of the script (caption, field label, list
entries, tooltip) as they were when the reference was last brought up to date.

Usage, from pi-scripts/StarAberrationDiagnostics:
   python3 dialog-src/check.py            lists what needs attention
   python3 dialog-src/check.py --stamp    after updating the reference: takes
                                          over the current texts of the script
   python3 dialog-src/check.py --draft debayerCheck
                                          Markdown for a new setting, from the script
   --script PATH   the script to compare with; default: the Koma repository next
                   to this one (development version), else the released copy here

It reports:
 - texts of a control that changed in the script since the last --stamp, with
   old and new wording: update the English and the German page, then --stamp;
 - controls with a tooltip in the script that no page documents;
 - ids in the reference that the script does not have (renamed or removed);
 - ids documented in one language but not in the other;
 - group boxes of the script without a "## <title>" group in the English pages.
Changes of the layout itself (a pane moved, a new tab) are not detected: check
the screenshots and the layout code of the dialog after such changes.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAD = os.path.normpath(os.path.join(HERE, ".."))
SITE = os.path.normpath(os.path.join(SAD, "..", ".."))
LOCK = os.path.join(HERE, "script.lock.json")
SCRIPTS = [os.path.normpath(os.path.join(SITE, "..", "Koma", "src", "StarAberrationDiagnostics.js")),
           os.path.join(SAD, "StarAberrationDiagnostics.js")]

# Controls with a tooltip that are documented elsewhere on purpose or have no
# setting of their own (e.g. a help text that is shown as a tooltip).
NOT_DOCUMENTED = {"fwhmGridHelp", "tiltHelp"}

KINDS = [("Check", "checkbox"), ("Radio", "option"), ("Spin", "number"), ("Combo", "list"),
         ("Button", "button"), ("Edit", "text"), ("Tree", "list"), ("Label", "display"), ("Value", "display")]


def script_path(argv):
    if "--script" in argv:
        return argv[argv.index("--script") + 1]
    return next(p for p in SCRIPTS if os.path.exists(p))


def documented():
    """{lang: {id: (page, title)}} from the Markdown."""
    out = {}
    for f in sorted(glob.glob(os.path.join(SITE, "_sad_dialog", "*", "*.md"))):
        lang, page = f.split(os.sep)[-2], os.path.basename(f)[:-3]
        lines = open(f, encoding="utf-8").read().split("\n")
        for i, l in enumerate(lines):
            m = re.match(r"\{: #(\w+)(?: \.\w+)?\}$", l)
            if m and i and lines[i - 1].startswith("#"):
                out.setdefault(lang, {})[m.group(1)] = (page, lines[i - 1].lstrip("# "))
    return out


def texts(T, name):
    t = {"text": T.text(name), "label": None, "items": T.items(name), "tooltip": T.tooltip(name)}
    # the field label of a control is a member <stem>Label next to it
    stem = re.sub(r"(Check|Radio|Spin|Combo|Button|Edit|Tree|Value)$", "", name)
    t["label"] = T.label(stem + "Label")
    return {k: v for k, v in t.items() if v}


def kind(name):
    return next((k for suffix, k in KINDS if name.endswith(suffix)), "display")


def main(argv):
    path = script_path(argv)
    sys.path.insert(0, HERE)
    import dialog_text
    dialog_text.SRC = path
    T = dialog_text.DialogText()
    src = T.src
    in_script = set(re.findall(r"this\.(\w+)\s*=\s*new ", src))
    with_tooltip = set(re.findall(r"this\.(\w+)\.toolTip\s*=", src))
    doc = documented()
    ids = set().union(*[set(d) for d in doc.values()]) if doc else set()

    if "--draft" in argv:
        name = argv[argv.index("--draft") + 1]
        t = texts(T, name)
        title = (t.get("label") or t.get("text") or name).rstrip(":")
        print(f"### {title}\n{{: #{name} .{kind(name)}}}\n")
        if t.get("items"):
            print("\n".join("- " + i for i in t["items"]) + "\n")
        if t.get("tooltip"):
            print(re.sub(r"</?p>", "\n", t["tooltip"]).strip())
        return 0

    current = {name: texts(T, name) for name in sorted(ids | with_tooltip) if name in in_script}
    if "--stamp" in argv:
        with open(LOCK, "w", encoding="utf-8") as f:
            json.dump({"script": os.path.basename(path), "controls": current}, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print(f"stamped {len(current)} controls from {path}")
        return 0

    try:
        lock = json.load(open(LOCK, encoding="utf-8"))["controls"]
    except FileNotFoundError:
        lock = {}
    print(f"script: {path}")
    problems = 0

    def report(title, rows):
        nonlocal problems
        if rows:
            problems += len(rows)
            print(f"\n{title}:")
            for r in rows:
                print("  " + r)

    changed = []
    for name, t in current.items():
        old = lock.get(name)
        if old is None or old == t:
            continue
        where = ", ".join(f"{lang}/{d[name][0]}.md" for lang, d in sorted(doc.items()) if name in d)
        changed.append(f"{name} ({where or 'not documented'})")
        for k in sorted(set(t) | set(old)):
            if t.get(k) != old.get(k):
                changed.append(f"    {k} was: {old.get(k)!r}")
                changed.append(f"    {k} now: {t.get(k)!r}")
    report("Changed in the script since the last --stamp (update en and de, then --stamp)", changed)
    report("Tooltip in the script, but not documented (--draft <id> gives a start)",
           sorted(n for n in with_tooltip - ids - NOT_DOCUMENTED))
    report("Documented, but not in the script (renamed or removed?)",
           sorted(f"{n} ({', '.join(l + '/' + d[n][0] + '.md' for l, d in sorted(doc.items()) if n in d)})"
                  for n in ids - in_script))
    langs = sorted(doc)
    report("Documented in one language only",
           sorted(f"{n}: only {l}" for l in langs for n in doc[l] if any(n not in doc[o] for o in langs if o != l)))
    groups = set()
    for f in glob.glob(os.path.join(SITE, "_sad_dialog", "en", "*.md")):
        groups |= set(re.findall(r"^## (.+)$", open(f, encoding="utf-8").read(), re.M))
    report("Group box in the script without a ## group of that name (en)",
           sorted(set(re.findall(r'groupBox\("([^"]+)"', src)) - groups))
    report("New in the script since the last --stamp (documented, not stamped yet)",
           sorted(n for n in current if n not in lock and n in ids))
    print("\nall in step" if not problems else f"\n{problems} findings")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
