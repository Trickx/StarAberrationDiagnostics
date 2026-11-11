#!/usr/bin/env python3
"""Writes the handbook of StarAberrationDiagnostics as pages of the GitHub
wiki (https://github.com/Trickx/StarAberrationDiagnostics/wiki).

The handbook's sources stay where they are: the chapters in
_sad_handbook/<lang>/<id>.md of the website repository (astroblogy), the
figures in pi-scripts/StarAberrationDiagnostics/fig/ and de/fig/. This
script converts them; the wiki is generated, never edited by hand.

    python3 wiki-src/make-wiki.py --site ../astroblogy --out ../StarAberrationDiagnostics.wiki

then commit and push the wiki repository. Per chapter and language one
page (English "07-Focus-sensor-tilt-and-field-curvature", German
"DE-07-Fokus-..."), plus Home, the German start page, the sidebar and the
footer. Chapters are numbered from 0 as on the website, so that the text's
"chapter 7" stays right.

What the conversion does:
  - front matter -> heading "n. Title"; ### subheadings -> ##
  - kramdown attribute lines ({: .steps}) and the escaped quotes (\\' \\")
  - <div class="note"> -> blockquote; <div class="eq"> -> centered block
    (GitHub drops class attributes)
  - figures -> the SVG copied into the wiki (images/<lang>/), linked by
    its raw URL
  - links to chapters (#id) -> wiki pages; other relative links -> the
    website
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

REPO = "Trickx/StarAberrationDiagnostics"
SITE_URL = "https://tricx.de"
SCRIPT_URL = SITE_URL + "/pi-scripts/StarAberrationDiagnostics/"
RAW = f"https://raw.githubusercontent.com/wiki/{REPO}/"
LANGS = ("en", "de")

TEXT = {
    "en": {
        "home": "Home", "chapter": "Chapter", "prev": "Previous", "next": "Next", "contents": "Contents",
        "other": ("Deutsch", "DE-Startseite"), "dialog": "In the dialog reference:",
        "handbook": "Handbook: how the diagnostics work",
        "generated": "Generated from the handbook sources by wiki-src/make-wiki.py - edit those, not this page.",
    },
    "de": {
        "home": "DE-Startseite", "chapter": "Kapitel", "prev": "Zurück", "next": "Weiter", "contents": "Inhalt",
        "other": ("English", "Home"), "dialog": "In der Dialogreferenz:",
        "handbook": "Handbuch: So funktioniert die Diagnose",
        "generated": "Aus den Quellen des Handbuchs erzeugt von wiki-src/make-wiki.py - dort ändern, nicht hier.",
    },
}

HOME = {
    "en": """# StarAberrationDiagnostics

Diagnostics of the optical system from the shapes of the stars across the field - a script for
[PixInsight](https://pixinsight.com/). **[Deutsche Version](DE-Startseite)**

![StarAberrationDiagnostics dialog with the vector map preview]({raw}images/StarAberrationDiagnostics.png)

## What it does

- Vector map of every star's elongation, with streamlines and an orientation heatmap
- Sensor tilt from the FWHM across the field (Siril-style quadrants, 11×11 grid, tilt axis, Δz and tilt angle)
- Corrector spacing and field curvature from the radial/tangential pattern at the field edge
- Coma field and coma-free point from the asymmetry of each star, for collimation
- The tracking error shared by all stars, measured and optionally subtracted
- An assessment of the results with suggested corrections, in the order in which to work through them
- A series analysis of frames before and after a meridian flip, including the plate-solve distortion

## Installation in PixInsight

1. Resources > Updates > Manage Repositories
2. Add repository: `{script}`
3. Run Check for Updates
4. Run via Script > Tricx > StarAberrationDiagnostics

## {handbook}

The chapters explain every computation of the script: what each error does to the stars, and how the
script captures it mathematically. They build on each other, but each can be read on its own.

{toc}

In depth (in German): [Koma - wie sie entsteht]({script}de/koma.html), how coma arises, with computed ray
traces and a simulator, and [Typische Fehler eines Newton-Teleskops]({script}de/newton.html), the typical
errors of a Newtonian, with computed star images.
The dialog reference with interactive simulators of the maps: [{script}dialog/]({script}dialog/).
""",
    "de": """# StarAberrationDiagnostics

Diagnose des optischen Systems anhand der Sternformen über das ganze Bildfeld - ein Script für
[PixInsight](https://pixinsight.com/). **[English version](Home)**

![Dialog von StarAberrationDiagnostics mit der Vorschau der Vektorkarte]({raw}images/StarAberrationDiagnostics.png)

## Was das Script macht

- Vektorkarte der Elongation jedes Sterns, mit Stromlinien und einer Orientierungs-Heatmap
- Sensorverkippung aus der FWHM über das Bildfeld (Quadranten wie in Siril, 11×11-Raster, Kippachse, Δz und Kippwinkel)
- Korrektorabstand und Bildfeldwölbung aus dem radialen/tangentialen Muster am Bildrand
- Komafeld und komafreier Punkt aus der Asymmetrie jedes Sterns, für die Kollimation
- Der allen Sternen gemeinsame Nachführfehler, gemessen und optional abgezogen
- Eine Bewertung der Ergebnisse mit Korrekturvorschlägen, in der Reihenfolge, in der man sie abarbeiten sollte
- Eine Serienanalyse von Aufnahmen vor und nach dem Meridian-Flip, einschließlich der Plate-Solve-Verzeichnung

## Installation in PixInsight

1. Resources > Updates > Manage Repositories
2. Repository hinzufügen: `{script}`
3. Check for Updates ausführen
4. Starten über Script > Tricx > StarAberrationDiagnostics

## {handbook}

Die Kapitel erklären jede Berechnung des Scripts: was jeder Fehler mit den Sternen macht und wie das
Script ihn mathematisch erfasst. Sie bauen aufeinander auf, lassen sich aber auch einzeln lesen.

{toc}

Vertiefung: [Koma - wie sie entsteht]({script}de/koma.html), mit berechneten Strahlengängen und einem
Simulator, und [Typische Fehler eines Newton-Teleskops]({script}de/newton.html), mit berechneten Sternbildern.
Die Dialogreferenz mit interaktiven Simulatoren der Karten: [{script}de/dialog/]({script}de/dialog/).
""",
}


def front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k.strip()] = v.strip('"')
    return meta, text[m.end():]


def page_slug(title: str) -> str:
    s = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-")
    return s


def page_name(lang: str, number: int, title: str) -> str:
    return ("DE-" if lang == "de" else "") + f"{number:02d}-{page_slug(title)}"


def link(name: str) -> str:
    return quote(name)


def chapters(site: Path, lang: str) -> list[dict]:
    out = []
    for f in (site / "_sad_handbook" / lang).glob("*.md"):
        meta, body = front_matter(f.read_text(encoding="utf-8"))
        out.append({"id": f.stem, "title": meta["title"], "order": float(meta["order"]), "body": body})
    out.sort(key=lambda c: c["order"])
    for i, c in enumerate(out):
        c["number"] = i
        c["page"] = page_name(lang, i, c["title"])
    return out


def dialog_pages(site: Path, lang: str) -> list[dict]:
    out = []
    for f in (site / "_sad_dialog" / lang).glob("*.md"):
        meta, _ = front_matter(f.read_text(encoding="utf-8"))
        out.append({"id": f.stem, "title": meta.get("title", f.stem), "order": float(meta.get("order", 0)),
                    "handbook": meta.get("handbook", [])})
    out.sort(key=lambda d: d["order"])
    return out


def convert(body: str, lang: str, pages: dict[str, str], missing: list[str]) -> str:
    text = body
    text = re.sub(r"^\{:.*\}\s*$\n?", "", text, flags=re.M)
    text = text.replace("\\'", "'").replace('\\"', '"')
    text = re.sub(r"^### ", "## ", text, flags=re.M)
    text = re.sub(r'<div class="note">(.*?)</div>', r"<blockquote>\1</blockquote>", text, flags=re.S)
    text = text.replace('<div class="eq">', '<div align="center">')
    text = re.sub(r'<div class="[^"]*">', "<div>", text)

    figs = "images/" + lang + "/"
    text = re.sub(r'src="fig/([^"]+)"', lambda m: f'src="{RAW}{figs}{m.group(1)}"', text)

    def target(href: str) -> str:
        if href.startswith("#"):
            anchor = href[1:]
            if anchor in pages:
                return link(pages[anchor])
            for cid, page in pages.items():
                if anchor.startswith(cid + "-"):
                    return link(page) + "#" + anchor[len(cid) + 1:]
            missing.append(href)
            return href
        if re.match(r"^[a-z]+:", href):
            return href
        if href.startswith("/"):
            return SITE_URL + href
        return SCRIPT_URL + ("de/" if lang == "de" else "") + href

    text = re.sub(r"\]\(([^)\s]+)\)", lambda m: "](" + target(m.group(1)) + ")", text)
    text = re.sub(r'href="([^"]+)"', lambda m: 'href="' + target(m.group(1)) + '"', text)
    return text.strip() + "\n"


def nav(lang: str, chs: list[dict], i: int, other: dict) -> str:
    t = TEXT[lang]
    parts = []
    if i > 0:
        parts.append(f"← [{t['prev']}: {chs[i - 1]['number']}. {chs[i - 1]['title']}]({link(chs[i - 1]['page'])})")
    parts.append(f"[{t['contents']}]({link(t['home'])})")
    if i + 1 < len(chs):
        parts.append(f"[{t['next']}: {chs[i + 1]['number']}. {chs[i + 1]['title']}]({link(chs[i + 1]['page'])}) →")
    return " · ".join(parts)


def toc(chs: list[dict]) -> str:
    return "\n".join(f"{c['number']}. [{c['title']}]({link(c['page'])})" for c in chs)


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", default=str(here.parent / "astroblogy"), help="the website repository (astroblogy)")
    ap.add_argument("--out", default=str(here.parent / "StarAberrationDiagnostics.wiki"),
                    help="the clone of the wiki repository")
    args = ap.parse_args()
    site, out = Path(args.site), Path(args.out)
    sad = site / "pi-scripts" / "StarAberrationDiagnostics"
    if not (site / "_sad_handbook").is_dir():
        print(f"No handbook sources in {site}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    # Generated pages and images are replaced as a whole.
    for f in out.glob("*.md"):
        f.unlink()
    shutil.rmtree(out / "images", ignore_errors=True)
    (out / "images").mkdir()
    shutil.copytree(sad / "fig", out / "images" / "en")
    shutil.copytree(sad / "de" / "fig", out / "images" / "de")
    shutil.copy(sad / "StarAberrationDiagnostics.png", out / "images" / "StarAberrationDiagnostics.png")

    all_chs = {lang: chapters(site, lang) for lang in LANGS}
    missing: list[str] = []
    for lang in LANGS:
        t = TEXT[lang]
        chs = all_chs[lang]
        other_lang = "de" if lang == "en" else "en"
        other = {c["id"]: c for c in all_chs[other_lang]}
        pages = {c["id"]: c["page"] for c in chs}
        dialog = dialog_pages(site, lang)
        dialog_url = SCRIPT_URL + ("de/" if lang == "de" else "") + "dialog/"
        for i, c in enumerate(chs):
            body = convert(c["body"], lang, pages, missing)
            o = other.get(c["id"])
            switch = f"[{t['other'][0]}]({link(o['page'])})" if o else ""
            refs = [d for d in dialog if c["id"] in d["handbook"]]
            lines = [f"# {c['number']}. {c['title']}", "", nav(lang, chs, i, other) + (" · " + switch if switch else ""),
                     "", body]
            if refs:
                lines += ["", t["dialog"] + " " + ", ".join(f"[{d['title']}]({dialog_url}#{d['id']})" for d in refs)]
            lines += ["", "---", "", nav(lang, chs, i, other), ""]
            (out / (c["page"] + ".md")).write_text("\n".join(lines), encoding="utf-8")
        home = HOME[lang].format(raw=RAW, script=SCRIPT_URL, handbook=t["handbook"], toc=toc(chs))
        (out / (t["home"] + ".md")).write_text(home, encoding="utf-8")

    sidebar = ["**[StarAberrationDiagnostics](Home)**", "", "**Handbook**", ""]
    sidebar += [f"{c['number']}. [{c['title']}]({link(c['page'])})" for c in all_chs["en"]]
    sidebar += ["", "**[Handbuch](DE-Startseite)**", ""]
    sidebar += [f"{c['number']}. [{c['title']}]({link(c['page'])})" for c in all_chs["de"]]
    (out / "_Sidebar.md").write_text("\n".join(sidebar) + "\n", encoding="utf-8")
    (out / "_Footer.md").write_text(
        f"[{SCRIPT_URL}]({SCRIPT_URL}) · GNU GPL v3 or later · {TEXT['en']['generated']}\n", encoding="utf-8")

    if missing:
        print("Links to unknown chapters: " + ", ".join(sorted(set(missing))), file=sys.stderr)
    n = sum(len(v) for v in all_chs.values())
    print(f"Written: {n} chapters, 2 start pages, sidebar and footer to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
