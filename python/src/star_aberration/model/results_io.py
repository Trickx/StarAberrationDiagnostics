"""The results of a Calculate as a file that can be loaded again (JSON),
compatible with the files of the PixInsight script:

    format, version, script     identification
    image                       viewId, filePath, width, height (half size
                                after SuperPixel debayering), debayer,
                                hasAsymmetry
    measurement                 the detection settings of the run
    keywords                    the FITS keywords [[name, value], ...]
    comaFit, comaFitCentroid    as measured (their bootstrap is random)
    columns, stars              the stars as measured (without the tracking
                                correction), one row of RESULTS_COLUMNS each

Everything else (tracking, layers, evaluations) is computed from these.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
from pathlib import Path

import numpy as np

from .. import TITLE, VERSION
from .analysis import Analysis, MEASUREMENT_KEYS
from .coma import fit_coma_field
from .star import Star
from .tracking import compute_tracking_component

RESULTS_FORMAT = "StarAberrationDiagnostics results"
RESULTS_VERSION = 1
RESULTS_COLUMNS = ["x", "y", "fwhmX", "fwhmY", "eccentricity", "rotation", "asymX", "asymY", "m3X", "m3Y"]
RESULTS_EXTENSION = "_aberration.json"
_STAR_ATTR = {"x": "x", "y": "y", "fwhmX": "fwhm_x", "fwhmY": "fwhm_y", "eccentricity": "eccentricity",
              "rotation": "rotation", "asymX": "asym_x", "asymY": "asym_y", "m3X": "m3_x", "m3Y": "m3_y"}
# The settings names in the file are those of the PixInsight script.
_MEASUREMENT_JS = {"threshold": "threshold", "radius": "radius", "use_moffat": "useMoffat",
                   "mad_outlier_factor": "madOutlierFactor", "max_candidates": "maxCandidates", "debayer": "debayer",
                   "bayer_pattern": "bayerPattern", "sd_custom": "sdCustom",
                   "sd_structure_layers": "sdStructureLayers", "sd_sensitivity": "sdSensitivity",
                   "sd_peak_response": "sdPeakResponse", "sd_max_distortion": "sdMaxDistortion",
                   "sd_allow_clustered": "sdAllowClustered"}
_COMA_JS = {"k": "k", "ox": "ox", "oy": "oy", "x0": "x0", "y0": "y0", "n": "n", "r_max": "rMax", "k_sd": "kSd",
            "x0_sd": "x0Sd", "y0_sd": "y0Sd", "significant": "significant"}


def results_default_path(header: dict) -> str:
    """Next to the original file, named after it, or in the temporary
    directory for an image never saved."""
    path = header.get("path") or ""
    if path:
        p = Path(path)
        return str(p.with_name(p.stem + RESULTS_EXTENSION))
    import tempfile
    return os.path.join(tempfile.gettempdir(), (header.get("name") or "image") + RESULTS_EXTENSION)


def _num(v):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(f"{v:.7g}")


def _coma_to_js(fit):
    if fit is None:
        return None
    return {_COMA_JS[k]: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in fit.items() if k in _COMA_JS}


def _coma_from_js(fit):
    if not fit:
        return None
    inv = {v: k for k, v in _COMA_JS.items()}
    out = {inv[k]: v for k, v in fit.items() if k in inv}
    for k in ("k_sd", "x0_sd", "y0_sd"):
        if out.get(k) is None:
            out[k] = float("nan")
    return out


def save_results(analysis: Analysis, settings, path: str) -> None:
    measurement = analysis.measurement or {k: getattr(settings, k) for k in MEASUREMENT_KEYS}
    data = {
        "format": RESULTS_FORMAT, "version": RESULTS_VERSION, "script": VERSION,
        "created": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "image": {"viewId": analysis.header.get("name", ""), "filePath": analysis.header.get("path", ""),
                  "width": analysis.w, "height": analysis.h, "debayer": analysis.debayer,
                  "hasAsymmetry": analysis.has_asymmetry},
        "measurement": {_MEASUREMENT_JS[k]: v for k, v in measurement.items() if k in _MEASUREMENT_JS},
        "keywords": [list(k) for k in analysis.header.get("keywords", [])],
        "comaFit": _coma_to_js(analysis.coma_fit),
        "comaFitCentroid": _coma_to_js(analysis.coma_fit_centroid),
        "columns": RESULTS_COLUMNS,
        "stars": "@STARS@",
    }
    rows = [[_num(getattr(s, _STAR_ATTR[c])) for c in RESULTS_COLUMNS] for s in analysis.stars]
    text = json.dumps(data, indent=1, ensure_ascii=True)
    text = text.replace('"@STARS@"', "[\n" + ",\n".join("  " + json.dumps(r) for r in rows) + "\n ]")
    with open(path, "w", encoding="ascii") as f:
        f.write(text + "\n")


def load_results(path: str, background_for=None) -> Analysis:
    """Loads a results file. background_for(header, w, h, debayer) may return
    the background (uint8 RGB); else a dark field. Raises ValueError with a
    message for the user."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"The file is not a readable results file: {e}") from e
    if not isinstance(data, dict) or data.get("format") != RESULTS_FORMAT:
        raise ValueError(f"The file is not a {TITLE} results file.")
    if (data.get("version") or 0) > RESULTS_VERSION:
        raise ValueError(f"The file has format version {data['version']}; this program reads up to version "
                         f"{RESULTS_VERSION} - update it.")
    img = data.get("image") or {}
    w, h = img.get("width"), img.get("height")
    if not (isinstance(w, int) and w > 0 and isinstance(h, int) and h > 0) or not isinstance(data.get("stars"), list) \
            or not isinstance(data.get("columns"), list):
        raise ValueError("The results file is incomplete (image size or stars missing).")
    col = {c: i for i, c in enumerate(data["columns"])}
    for c in ("x", "y", "fwhmX", "fwhmY", "eccentricity", "rotation"):
        if c not in col:
            raise ValueError(f"The results file lacks the column {c}.")
    has_asym = img.get("hasAsymmetry") is True

    def get(row, c):
        i = col.get(c)
        return None if i is None or i >= len(row) else row[i]

    stars = []
    for row in data["stars"]:
        s = Star(row[col["x"]], row[col["y"]], row[col["fwhmX"]], row[col["fwhmY"]], row[col["eccentricity"]],
                 row[col["rotation"]])
        s.eccentricity_raw, s.rotation_raw = s.eccentricity, s.rotation
        if has_asym:
            s.asym_x, s.asym_y, s.m3_x, s.m3_y = get(row, "asymX"), get(row, "asymY"), get(row, "m3X"), get(row, "m3Y")
        stars.append(s)
    if not stars:
        raise ValueError("The results file contains no stars.")
    header = {"name": img.get("viewId") or Path(path).stem, "path": img.get("filePath") or "",
              "keywords": [tuple(k) for k in data.get("keywords", []) if isinstance(k, list) and len(k) >= 2]}
    debayer = img.get("debayer") is True
    background = background_for(header, w, h, debayer) if background_for else None
    if background is None:
        background = np.full((h, w, 3), 16, dtype=np.uint8)
    inv = {v: k for k, v in _MEASUREMENT_JS.items()}
    return Analysis(
        key="file:" + path, target_name=header["name"], debayer=debayer, w=w, h=h, stars=stars,
        tracking=compute_tracking_component(stars, w, h), has_asymmetry=has_asym,
        coma_fit=(_coma_from_js(data["comaFit"]) if "comaFit" in data else fit_coma_field(stars, w, h, False))
        if has_asym else None,
        coma_fit_centroid=(_coma_from_js(data["comaFitCentroid"]) if "comaFitCentroid" in data
                           else fit_coma_field(stars, w, h, True)) if has_asym else None,
        background=background, header=header,
        measurement={inv[k]: v for k, v in (data.get("measurement") or {}).items() if k in inv},
        file=path, created=data.get("created", ""))
