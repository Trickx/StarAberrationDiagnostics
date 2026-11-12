# Star Aberration Diagnostics (Python)

A port of the PixInsight script StarAberrationDiagnostics 0.10 to Python with
a Qt GUI: diagnostics of the optical system from the shapes of the stars -
sensor tilt, corrector spacing, field curvature, coma, collimation and
tracking errors - with maps, an assessment with suggested corrections, a
series analysis before and after a meridian flip, and an AI review by Claude.

How the measurements work is explained in the handbook:
<https://github.com/Trickx/StarAberrationDiagnostics/wiki>.

## Installation and start

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # the dependencies only
.venv/bin/pip install -e .            # the package (or -e ".[dev]" with pytest)
.venv/bin/star-aberration             # or: .venv/bin/python -m star_aberration
```

Python 3.11 or later. Images: FITS (`.fits`, `.fit`, `.fts`) and XISF.

## Packages (model - view - controller)

| Package | What |
|---|---|
| `star_aberration.model` | everything that is measured and judged, without Qt: settings, image loading, star detection, PSF fit, FWHM grid, tilt, star shapes, streamlines, tracking, coma, FWHM surface, optics and mount from the header, analysis, assessment, series, results files |
| `star_aberration.render` | the maps (Size, Shape, Coma, Tilt), the 3D tilt plot and the mosaic, drawn with QPainter into QImages |
| `star_aberration.ai` | the AI review: system prompt, data, request (Anthropic SDK), answer schema, formatting, log file |
| `star_aberration.view` | the main window (menus, toolbar, tabs, docks), the map viewer and the diagrams (pyqtgraph), the inspector, the preferences dialog, the theme (dark/light, qtawesome icons), the binding of widgets to the settings; tooltips as in the PixInsight dialog (`ui_text.json`) |
| `star_aberration.controller` | the settings and the state, the reactions to the actions of the window, the work in background threads |

## Using it

- **File › Open Image…** (Ctrl+O), *Open Recent*, or drop a FITS/XISF file on
  the window; **Analysis › Calculate** (Ctrl+R) measures the stars.
- The toolbar (or **View**, Ctrl+1 … Ctrl+4) switches between the maps Star
  Size, Star Shape, Coma and Tilt. The map zooms with the wheel and pans by
  dragging (double click: fit); under the mouse the position and the values of
  the nearest star are shown. The **Inspector** beside the map shows the
  image's optics, mount and guiding, the layers of the map shown and its
  evaluation. Layer changes redraw the map at once.
- **Profiles** tab: FWHM, elongation (eps_rad) and coma flare of every star
  against the distance from the center, with the ring medians, and the FWHM
  distribution.
- **Assessment** tab: the rule-based assessment and the AI review
  (**Analysis › Ask Claude**).
- **Series** tab: **Series › Add Frames…** (or drop several files),
  **Series › Analyze Series**; the comparison of the sides of the meridian
  flip, each frame's map, the values of each frame over the night, and the
  pooled static optics (toolbar *Data*).
- **File › Export Maps…** writes the four maps, the 3D tilt plot and the star
  table; **Save/Load Results** keep a measurement.
- **Settings › Preferences…** (Ctrl+,): measurement, optics, output and the
  AI review (API key); the optics type and the most used switches also
  directly in the **Settings** menu.

## Differences from the PixInsight script

- **Star detection and PSF fit** are implemented here (numpy/scipy) instead of
  PixInsight's StarDetector and DynamicPSF. The conventions are the same
  (Gaussian or Moffat, sx >= sy, rotation counterclockwise on the screen, the
  MAD outlier filter, the automatic lowering of the threshold); the detector
  parameters keep their meaning but not their exact effect, so the star
  counts differ somewhat from PixInsight's.
- The window is an app of its own: menus, toolbar, preferences dialog,
  inspector, drag and drop; *Export Maps* writes files instead of opening
  PixInsight windows.
- The **AI review** streams the answer (no time limit on long answers).

## Settings and API key

The settings are kept in `settings.json` in the user's configuration
directory (macOS: `~/Library/Application Support/StarAberrationDiagnostics`,
Linux: `~/.config/StarAberrationDiagnostics`, Windows: `%APPDATA%`). The
Anthropic API key is stored separately in `api_key` there, readable by the
user only; the environment variable `ANTHROPIC_API_KEY` takes precedence.

## What the AI review sends

Ask Claude sends to the Anthropic API (api.anthropic.com): the measured
values and the rule-based assessment as text, for a series the values per
frame with the file names, and - with *Attach the maps*, on by default - the
maps Star size, Star shape and Coma as PNG images of at most 1092 px, which
show a stretched rendering of the analyzed image. With *Save requests and
answers* (on by default), each request and answer is saved as
`StarAberrationAI_<date>_<time>.txt` next to the series frames, else next to
the target image, else in the temporary directory.

## Tests

```
.venv/bin/python -m pytest
```

Released under the GNU General Public License v3 or later.
