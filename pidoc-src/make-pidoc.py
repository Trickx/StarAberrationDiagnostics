"""Generates the PixInsight reference documentation (PIDoc) of
StarAberrationDiagnostics from the English dialog reference
(_sad_dialog/en/*.md), so that "Browse Documentation" in the dialog and the
Process Explorer show the same text as the website.

Usage, from pi-scripts/StarAberrationDiagnostics:
   python3 pidoc-src/make-pidoc.py

It writes, in pidoc-src/build/ (not in git):
   StarAberrationDiagnostics.pidoc   the PIDoc source
   images/*.jpg                      the screenshots, half size, as JPEG
   system/                           an empty PIDoc system the document is
                                     compiled into (created once, from the
                                     PixInsight installation)

Then compile it in PixInsight; the command for the Process Console is printed
at the end. The result, system/scripts/StarAberrationDiagnostics/, goes into
the release package (make-release.sh).

The compiler writes into the target system selected the last time in its
dialog (Script > Development > DocumentationCompiler, Target System), also when run
from the Process Console; a \\pragma[basedir:...] in the document does not
change it (the compiler reads the directory before the document). So select
pidoc-src/build/system there once; else it tries to write into the
PixInsight installation, which fails without administrator rights.

Converted: paragraphs, lists, **bold**, *italic*, `code`, <sub>, links (to a
page of the reference: a local link; to the website: an external link), "> "
notes. Left out: the "## Simulator" groups (they need the website; a link
points there) and the kramdown attributes ({: ...}).
"""
import glob
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAD = os.path.normpath(os.path.join(HERE, ".."))
SITE = os.path.normpath(os.path.join(SAD, "..", ".."))
BUILD = os.path.join(HERE, "build")
SYSTEM = os.path.join(BUILD, "system")
PI_DOC = "/Applications/PixInsight/doc"
COMPILER = "/Applications/PixInsight/src/scripts/PIDocCompiler/pidoc.js"

SCRIPT = "StarAberrationDiagnostics"
URL = "https://tricx.de/pi-scripts/StarAberrationDiagnostics/"
BRIEF = ("Diagnostics of the optical system from the shapes of the stars: sensor tilt, "
         "corrector spacing, field curvature, coma, collimation and tracking errors, "
         "with maps and an assessment with suggested corrections.")
KEYWORDS = ("star shape, aberration, FWHM, elongation, eccentricity, sensor tilt, "
            "field curvature, corrector spacing, coma, collimation, tracking, meridian flip")
AUTHOR = "Sven Kopetzki"
COPYRIGHT = "2026 Sven Kopetzki"
# Screenshots are 2524 px wide (Retina); half of that is sharp enough.
SHOT_WIDTH = 1262


# ----------------------------------------------------------------------------
# Markdown sources
# ----------------------------------------------------------------------------

def front_matter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            v = v.strip('"')
        meta[k.strip()] = v
    return meta, text[m.end():]


def pages():
    out = []
    for f in glob.glob(os.path.join(SITE, "_sad_dialog", "en", "*.md")):
        meta, body = front_matter(open(f, encoding="utf-8").read())
        meta["id"] = os.path.splitext(os.path.basename(f))[0]
        meta["body"] = body
        out.append(meta)
    return sorted(out, key=lambda p: int(p["order"]))


def chapters():
    out = {}
    for f in glob.glob(os.path.join(SITE, "_sad_handbook", "en", "*.md")):
        meta, _ = front_matter(open(f, encoding="utf-8").read())
        out[os.path.splitext(os.path.basename(f))[0]] = meta["title"]
    return out


def slug(text):
    """The anchor kramdown gives a heading (as on the website)."""
    s = re.sub(r"<[^>]+>|&[a-zA-Z]+;", "", text).lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s", "-", s.strip())


# ----------------------------------------------------------------------------
# Inline markup
# ----------------------------------------------------------------------------

LABELS = set()
UNRESOLVED = []

INLINE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)"     # link
                    r"|\*\*(.+?)\*\*"               # bold
                    r"|\*(.+?)\*"                   # italic
                    r"|`([^`]+)`"                   # code
                    r"|<sub>(.*?)</sub>"
                    r"|<sup>(.*?)</sup>"
                    r"|\\([\\'\"_*\[\]#|`])")       # Markdown escape


def esc(text):
    """Plain text for PIDoc: its reserved characters escaped, < > & as entities."""
    text = re.sub(r"([\\{}\[\]%])", r"\\\1", text)
    text = re.sub(r"&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def link(text, url):
    if url.startswith("#"):
        if url[1:] in LABELS:
            return "\\lref %s {%s}" % (url[1:], inl(text))
        UNRESOLVED.append(url)
        return inl(text)
    if url.startswith("/"):
        url = "https://tricx.de" + url
    return "\\xref %s {%s}" % (url, inl(text))


def inl(s):
    out, pos = [], 0
    for m in INLINE.finditer(s):
        out.append(esc(s[pos:m.start()]))
        pos = m.end()
        g = m.groups()
        if g[0] is not None:
            out.append(link(g[0], g[1]))
        elif g[2] is not None:
            out.append("\\s {%s}" % inl(g[2]))
        elif g[3] is not None:
            out.append("\\e {%s}" % inl(g[3]))
        elif g[4] is not None:
            out.append("\\c {%s}" % esc(g[4]))
        elif g[5] is not None:
            out.append("\\sub {%s}" % inl(g[5]))
        elif g[6] is not None:
            out.append("\\sup {%s}" % inl(g[6]))
        else:
            out.append(esc(g[7]))
    out.append(esc(s[pos:]))
    return "".join(out)


# ----------------------------------------------------------------------------
# Blocks
# ----------------------------------------------------------------------------

def blocks(lines):
    """Paragraphs, lists and notes of a run of Markdown lines, as PIDoc."""
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("{:"):
            i += 1
        elif re.match(r"([-*]|\d+\.) ", line):
            ordered = line[0].isdigit()
            items = []
            while i < len(lines) and (re.match(r"([-*]|\d+\.) ", lines[i])
                                      or (lines[i].startswith("   ") and items)):
                if lines[i].startswith("   "):
                    items[-1] += " " + lines[i].strip()
                else:
                    items.append(re.sub(r"^([-*]|\d+\.) ", "", lines[i]))
                i += 1
            out.append("\\list%s {\n%s\n}" % ("[ordered]" if ordered else "",
                                               "\n".join("   { %s }" % inl(t) for t in items)))
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].strip())
                i += 1
            out.append("\\note {\n%s\n}" % blocks(quote))
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("{:", ">")) \
                    and not re.match(r"([-*]|\d+\.) ", lines[i]):
                para.append(inl(lines[i]))
                i += 1
            # One line: the PIDoc compiler joins the lines of a paragraph
            # without a space (one sentence per line in the sources).
            out.append(" ".join(para))
    return "\n\n".join(out)


def split(lines, marker):
    """(lines before the first heading, [(heading, attribute, lines)])."""
    head, parts = [], []
    for line in lines:
        if line.startswith(marker + " "):
            parts.append([line[len(marker) + 1:].strip(), "", []])
        elif parts:
            if line.startswith("{:") and not parts[-1][2] and not parts[-1][1]:
                parts[-1][1] = line
            else:
                parts[-1][2].append(line)
        else:
            head.append(line)
    return head, parts


def settings(lines):
    """A group: its text, then the settings as a definition list."""
    intro, items = split(lines, "###")
    out = [blocks(intro)] if blocks(intro) else []
    if items:
        defs = "\n".join("   { %s }\n   {\n%s\n   }" % (inl(name), blocks(body))
                         for name, _, body in items)
        out.append("\\definition {\n%s\n}" % defs)
    return "\n\n".join(out)


# ----------------------------------------------------------------------------
# Document
# ----------------------------------------------------------------------------

def shot(name):
    os.makedirs(os.path.join(BUILD, "images"), exist_ok=True)
    src = os.path.join(SAD, "screenshots", name)
    dst = os.path.join(BUILD, "images", os.path.splitext(name)[0] + ".jpg")
    if not os.path.exists(src):
        return ""
    if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
        try:
            subprocess.run(["sips", "-Z", str(SHOT_WIDTH), "-s", "format", "jpeg",
                            "-s", "formatOptions", "85", src, "--out", dst],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            dst = os.path.join(BUILD, "images", name)
            shutil.copyfile(src, dst)
    return "\\image[width:100\\%%] images/%s" % os.path.basename(dst)


def handbook_links(ids, titles):
    links = ["\\xref %s#%s {%s}" % (URL, c, esc(titles[c])) for c in ids if c in titles]
    if not links:
        return ""
    return "\\s {Handbook:} " + ", ".join(links) + "."


def document():
    pp = pages()
    titles = chapters()
    for p in pp:
        LABELS.add(p["id"])
        for heading, _, _ in split(p["body"].splitlines(), "##")[1]:
            LABELS.add("%s-%s" % (p["id"], slug(heading)))
    # Only the targets of local links get a label: the compiler warns of
    # unreferenced ones.
    referenced = set(re.findall(r"\]\(#([^)]+)\)", "".join(p["body"] for p in pp)))

    def label(id):
        return "\\label %s " % id if id in referenced else ""

    description, usage = "", []
    for p in pp:
        lines = p["body"].splitlines()
        intro, groups = split(lines, "##")
        simulator = any(h == "Simulator" for h, _, _ in groups)
        groups = [g for g in groups if g[0] != "Simulator"]

        if p["id"] == "overview":
            # The introduction of the reference describes the whole dialog.
            text, subs = split(intro, "###")
            description = "\n\n".join([blocks(text)] + [
                "\\subsection { %s } {\n%s\n}" % (inl(h), blocks(b)) for h, _, b in subs])
            title = "Preview and buttons"
            intro = []
        else:
            title = p["title"]

        body = []
        if blocks(intro):
            body.append(blocks(intro))
        for name in p.get("shot", []):
            body.append(shot(name))
        for heading, _, glines in groups:
            body.append("\\subsection { %s%s } {\n%s\n}"
                        % (label("%s-%s" % (p["id"], slug(heading))), inl(heading), settings(glines)))
        if simulator:
            body.append("The online reference has an interactive simulator of this map: "
                        "\\xref %sdialog/#%s-simulator {%s, Simulator}."
                        % (URL, p["id"], esc(p["title"])))
        links = handbook_links(p.get("handbook", []), titles)
        if links:
            body.append(links)
        usage.append("\\subsection { %s%s } {\n%s\n}"
                     % (label(p["id"]), esc(title), "\n\n".join(b for b in body if b)))

    description += ("\n\nHow the computations behind the dialog work - star detection, PSF fits, "
                    "the statistics of the maps and the assessment - is explained in the "
                    "\\xref %s#handbook {handbook} on the website, which also has this reference "
                    "with interactive simulators of the maps." % URL)

    return "\n".join([
        "% Generated by pidoc-src/make-pidoc.py from _sad_dialog/en/*.md - do not edit.",
        "\\documentclass PIScriptDoc",
        "\\script %s" % SCRIPT,
        "\\keywords { %s }" % KEYWORDS,
        "\\author { %s }" % AUTHOR,
        "\\copyright { %s }" % COPYRIGHT,
        "\\brief { %s }" % esc(BRIEF),
        "",
        "\\description {\n%s\n}" % description,
        "",
        "\\usage {\n%s\n}" % "\n\n".join(usage),
        "",
        "\\related %s { StarAberrationDiagnostics: handbook, examples and installation }" % URL,
        "\\related %sdialog/ { Dialog reference with simulators of the maps }" % URL,
        "",
        "\\make",
        ""])


def make_system():
    """An empty PIDoc system, as the compiler's 'Generate new PIDoc system'."""
    for d in ["docs", "pjsr/objects", "scripts", "tools"]:
        os.makedirs(os.path.join(SYSTEM, d), exist_ok=True)
    for d in ["css", "graphics", "icons", "images", "scripts", "titles"]:
        src = os.path.join(PI_DOC, "pidoc", d)
        dst = os.path.join(SYSTEM, "pidoc", d)
        os.makedirs(dst, exist_ok=True)
        if os.path.isdir(src):
            for f in os.listdir(src):
                if os.path.isfile(os.path.join(src, f)):
                    shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))


def main():
    os.makedirs(BUILD, exist_ok=True)
    if not os.path.isdir(os.path.join(SYSTEM, "pidoc", "css")):
        make_system()
    path = os.path.join(BUILD, SCRIPT + ".pidoc")
    text = document()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    for u in sorted(set(UNRESOLVED)):
        print("Unresolved local link (written as plain text): " + u, file=sys.stderr)
    print("Written: " + os.path.relpath(path, SAD))
    print("\nCompile it in PixInsight, in the Process Console:\n")
    print('   run -a="--no-backup" -a="%s" -x=auto "%s"' % (path, COMPILER))
    print("\nThe target system must be set once in the dialog of the compiler")
    print("(run it without arguments, Target System), to:\n")
    print("   " + SYSTEM)
    print("\nResult: " + os.path.relpath(os.path.join(SYSTEM, "scripts", SCRIPT), SAD) + "/")


if __name__ == "__main__":
    main()
