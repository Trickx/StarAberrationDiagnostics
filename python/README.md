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
.venv/bin/pip install -e .            # or: -e ".[dev]" for the tests
.venv/bin/star-aberration             # or: .venv/bin/python -m star_aberration
```

Python 3.11 or later. Images: FITS (`.fits`, `.fit`, `.fts`) and XISF.

## Packages (model - view - controller)

| Package | What |
|---|---|
| `star_aberration.model` | everything that is measured and judged, without Qt: settings, image loading, star detection, PSF fit, FWHM grid, tilt, star shapes, streamlines, tracking, coma, FWHM surface, optics and mount from the header, analysis, assessment, series, results files |
| `star_aberration.render` | the maps (Size, Shape, Coma, Tilt), the 3D tilt plot and the mosaic, drawn with QPainter into QImages |
| `star_aberration.ai` | the AI review: system prompt, data, request (Anthropic SDK), answer schema, formatting, log file |
| `star_aberration.view` | the main window and its widgets; texts and tooltips as in the PixInsight dialog (`ui_text.json`) |
| `star_aberration.controller` | the state, the reactions to the view, the work in background threads |

## Differences from the PixInsight script

- **Star detection and PSF fit** are implemented here (numpy/scipy) instead of
  PixInsight's StarDetector and DynamicPSF. The conventions are the same
  (Gaussian or Moffat, sx >= sy, rotation counterclockwise on the screen, the
  MAD outlier filter, the automatic lowering of the threshold); the detector
  parameters keep their meaning but not their exact effect, so the star
  counts differ somewhat from PixInsight's.
- **Save** writes the four maps, the 3D plot and the CSV next to the image
  instead of opening windows.
- The **target image** is opened with *Open image...* instead of being chosen
  among PixInsight's windows.
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
