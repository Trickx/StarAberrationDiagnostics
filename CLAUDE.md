# StarAberrationDiagnostics pages

Rules for editing the handbook and the dialog reference of this script, by hand
or with an AI. This file is also imported by `_sad_handbook/CLAUDE.md` and
`_sad_dialog/CLAUDE.md` and is not published.

## Where what is

| What | Edit here | Never edit |
|---|---|---|
| The script (developed here since 2026-10-04) | `StarAberrationDiagnostics.js`, icon `StarAberrationDiagnostics.svg` | |
| Release | `python3 pidoc-src/make-pidoc.py` and compile the documentation in PixInsight (it prints the command), sign the script in PixInsight (`.xsgn`), then `./make-release.sh`, then sign `updates.xri` | `.package/`, the `.tar.gz` by hand |
| Chapter text | `_sad_handbook/en/<id>.md`, `_sad_handbook/de/<id>.md` (repository root) | `_site/` |
| Chapter title and order | front matter of the chapter: `title`, `order` | |
| Header cards (install, screenshot, table of contents) | `index.html`, `de/index.html` | |
| Figures | `handbook-src/figures.py` | `fig/*.svg`, `de/fig/*.svg` (generated) |
| Dialog reference: one file per dialog page, all shown on one page | `_sad_dialog/en/<page>.md`, `_sad_dialog/de/<page>.md` (repository root) | |
| Simulators of the star size, star shape and coma maps (Maps › Size, Shape, Coma): model, evaluation (copies of `evaluateFwhmGrid()`, `evaluateShapeField()`, `fitComaField()` and the drawing of their layers in the script; update them when the script changes) and the texts that change with the values, both languages | `dialog/size-sim.js`, `dialog/shape-sim.js`, `dialog/coma-sim.js`; their controls are in `_sad_dialog/<lang>/maps-size.md`, `maps-shape.md` and `maps-coma.md`, their style in `_layouts/pi_dialog.html` | |
| PixInsight documentation (Browse Documentation, installed in `doc/scripts/StarAberrationDiagnostics/`): the English dialog reference without the simulators | the dialog reference; frame (brief, keywords, links) in `pidoc-src/make-pidoc.py` | `pidoc-src/build/` (generated, not in git) |
| Dialog screenshots | `screenshots/<name>.png` (shared by both languages) | |
| Dialog reference and examples: menu, page frame, interface texts | `_layouts/pi_dialog.html`, `_includes/pi-dialog-*.html`, `_includes/pi-sidenav.html`, `_data/pi_dialog.yml`; the pages themselves are `dialog/index.html`, `examples/index.html` and their `de/` copies (front matter only) | |
| Redirects from the former addresses of single files (`…/dialog/<page>/`) | `_layouts/pi_redirect.html` | |
| Examples: one file per example, all shown on one page (same layout as the dialog reference) | `_sad_examples/en/<id>.md`, `_sad_examples/de/<id>.md` (repository root) | |
| Example images (maps scaled down from the script's PNGs, shared by both languages) | `examples/<id>/` | |
| Page frame, chapter cards, style | `_layouts/pi_handbook.html`, `_includes/pi-handbook-*.html` (repository root) | |
| Coma page, Newtonian page (DE) | `koma-src/`, `newton-src/` (template + `gen.py`) | `de/koma.html`, `de/newton.html` |

## Chapters

- One file per chapter; the file name is the anchor (`stars.md` → `#stars`) and the same in
  both languages. Chapters are sorted by `order` (steps of 10 leave room to insert);
  the numbers and the table of contents follow from it.
- A new chapter: create `_sad_handbook/en/<id>.md` and `_sad_handbook/de/<id>.md` with
  `title` and `order`.
- The text refers to chapters by number ("chapter 7", "Kapitel 7", "Kap. 7"), also from the
  coma and Newtonian pages (`koma-src/`, `newton-src/`; there, "Kapitel n" without
  `./#…` link can also mean a section of that page itself). A new or moved chapter
  shifts these numbers: update them and regenerate both pages.
- One sentence per line, so that a diff shows exactly what changed.
- Subheadings (`### …`) get the anchor `<id>-<heading>`, e.g. `#tracking-the-error`;
  renaming a heading changes its anchor.
- Paragraphs, lists, `**bold**`, `*italic*` and links are Markdown. Everything without a
  Markdown form stays HTML: indices `<sub>`, variables `<i class="v">`, formula boxes
  `<div class="eq">`, notes `<div class="note">`, tables `<div class="tbl">`, figures.
  Markdown inside these HTML blocks is not processed.
- English text: write straight quotes as `\'` and `\"`, otherwise kramdown turns them into
  typographic quotes. German text uses „…“ and needs no escaping.

## Figures

- A figure in a chapter:
  `<figure><img src="fig/<name>.svg" alt="<label>" />`
  `<figcaption>Figure n - …</figcaption></figure>` (German: `Abbildung n - …`).
  Figures are numbered by hand, through the whole handbook.
- Each figure is a function in `handbook-src/figures.py`, registered with
  `@figure(name, width, height, labels)`. Its labels are given for `en` and `de`;
  the geometry is computed once for both. Numbers in labels via `t.num()` (decimal comma in German).
- After a change, from this folder: `python3 handbook-src/figures.py`.
  `--check` compares the generated figures with the files on disk without writing.
- The SVG files are shown with `<img>`, so they must be valid XML on their own
  (`text()` escapes `<` and `&`).

## Dialog reference

- One Markdown file per page of the dialog; all of them are shown, sorted by `order`, on one
  page: `/pi-scripts/StarAberrationDiagnostics/dialog/` (German: `…/de/dialog/`). The file name
  is the anchor of its part (`maps-size.md` → `#maps-size`); link between parts with
  `[Size](#maps-size)`. The first file (`overview.md`) opens the page under the title
  "Dialog reference". The former address `…/dialog/<page>/` redirects to the anchor.
  The examples work the same way (`_sad_examples/`, `…/examples/`). Front matter: `title`, `menu` (entry in the menu tree),
  `section` (the dialog tab: Setup, Maps; none for a page of its own), `order`,
  `shot` (list of screenshot file names, e.g. `[SAD_Maps_Size.png]`; a missing one is shown as a
  placeholder) and `handbook`
  (chapter ids, e.g. `[tilt, spacing]`).
- `handbook` links both ways: the dialog page lists those chapters, and below each of them
  the handbook lists the dialog pages that name it. Prefer it to hand-written links.
- The text before the first `##` is the introduction; each `##` group becomes a card.
  On the page a group is an `h3` with the anchor `<page>-<heading>` (e.g. `#series-frames`), a
  setting an `h4`. Headings without a control (`{: .display}`) also get the page as prefix;
  control ids stay as they are and must be unique across all pages (a setting shown on several
  tabs, like **Show** above all map tabs, is documented once, in `maps.md`).
- A setting is a `###` heading with the name shown in the dialog, and below it the member
  name of its control in the script and its kind:

      ### Debayer (SuperPixel) before star detection
      {: #debayerCheck .checkbox}

  Kinds: `checkbox`, `option`, `number`, `list`, `button`, `text`, `display` (an element
  without a control of its own: only `{: .display}`). The id is the anchor of the setting
  and the key for `dialog-src/check.py`; never change it unless the script renames the control.
- Below the heading: the list entries (for a list), then the text of the tooltip in the
  script, then notes of the reference as a quote (`> …`), shown as a box.
- The names of the dialog itself - captions, group boxes, list entries, tab names - stay
  English in the German pages too, as on the screenshots.
- Comparing with the script, from this folder:
  `python3 dialog-src/check.py` lists changed tooltips (old and new wording), controls
  that no page documents, ids the script no longer has, ids missing in one language and
  group boxes without a `##` group. It does not see changes of the layout itself (a pane
  moved to the left side, a new tab): after those, compare the pages with the screenshots
  and the layout code of the dialog.
  After updating both languages: `python3 dialog-src/check.py --stamp`.
  `--draft <id>` prints the Markdown for a new setting, `--script PATH` compares with
  another script (default: `../Koma/src/StarAberrationDiagnostics.js`, the development
  version next to this repository; else the released copy in this folder).

## Language and terms

- English is the original; German is the translation. A change in one language belongs
  in the other too, in the same commit.
- Fixed terms (EN / DE): FWHM; elongation / Elongation; ellipticity ε / Elliptizität;
  eccentricity e / Exzentrizität; tilt / Verkippung; field curvature / Bildfeldwölbung;
  corrector spacing / Korrektorabstand; coma-free point P₀ / komafreier Punkt;
  flare / Ausläufer; tracking / Nachführung; collimation, decollimated / Kollimation, dejustiert.

## Check

From the repository root, with Ruby 3.2 (as in the GitHub workflow):

    bundle _2.4.19_ exec jekyll build

then look at `_site/pi-scripts/StarAberrationDiagnostics/` and `…/de/`.
