"""The request: the system prompt, the data of a single frame and of a series
as JSON, and the user message (with the maps as PNG images in front when
they are attached).

The JSON uses the key names of the original script (camelCase), which the
system prompt describes; numbers are rounded to 4 significant digits.
"""

from __future__ import annotations

import base64
import json
import math
import re
from importlib import resources
from typing import Any

from ..model.assessment import LEVEL_LABELS, OPTICS_TYPES
from ..model.fwhm_grid import compute_fwhm_grid
from ..model.series import (SeriesFrame, series_metric, sip_corner_shifts, sip_max_shift, sip_tilt_equivalent_deg)
from . import AI_DETAIL_LEVELS

_INSTR = json.loads(resources.files(__package__).joinpath("instructions.json").read_text(encoding="utf-8"))
SYSTEM_PROMPT: str = resources.files(__package__).joinpath("system_prompt.txt").read_text(encoding="utf-8").rstrip("\n")
DETAIL_INSTRUCTIONS: list[str] = _INSTR["detail"]
CONTINUE_PROMPT: str = _INSTR["continue"]

assert len(DETAIL_INSTRUCTIONS) == len(AI_DETAIL_LEVELS)


def _camel(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _prepare(value: Any) -> Any:
    """Keys in camelCase, numbers rounded to 4 significant digits, NaN and
    infinities as null."""
    if isinstance(value, dict):
        return {_camel(str(k)): _prepare(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_prepare(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        if isinstance(value, int):
            return value
        return float(f"{value:.4g}")
    try:  # numpy scalars
        return _prepare(value.item())
    except AttributeError:
        return str(value)


def ai_json(obj: Any) -> str:
    return json.dumps(_prepare(obj), ensure_ascii=True, separators=(",", ":"))


def single_frame_text(single: dict, s) -> str:
    """single: {analysis, stars, metrics, optics, assessment}."""
    m, optics = single["metrics"], single["optics"]
    data = {
        "image": {"w": m["w"], "h": m["h"], "stars": m["n"], "debayered": single["analysis"].debayer},
        "optics": {"pixel_pitch_um": optics.pixel_pitch_um, "focal_length_mm": optics.focal_length_mm,
                   "aperture_mm": optics.aperture_mm, "f_ratio": optics.f_ratio or 0,
                   "optics_type": OPTICS_TYPES[s.optics_type]},
        "tracking": m["tracking"],
        "tracking_subtracted": m["tracking_subtracted"],
        "rings": m["rings"],
        "surface": m["surface"],
        "siril": m["siril"],
        "fwhm_grid": compute_fwhm_grid(single["stars"], m["w"], m["h"], 5),
        "fwhm_grid_assessment": m["grid"],
        "shape_assessment": m["shape"],
        "mount": m["mount"],
        "guiding": m["guiding"],
        "coma": m["coma"] if "coma" in m else "not measured",
    }
    rules = [f"[{LEVEL_LABELS[f['level']]}] {f['topic']}: {f['text']}" + (f" -> {f['advice']}" if f["advice"] else "")
             for f in single["assessment"]["findings"]]
    return ("Single frame - measured values (JSON):\n" + ai_json(data) + "\n\nRule-based assessment of that frame:\n"
            + "\n".join(rules))


COLUMNS = ["file", "side", "sideFrom", "haH", "altDeg", "focusPos", "tempC", "stars", "fwhmCenter", "fwhmEdge",
           "tiltRel", "tiltDirDeg", "epsRad", "trackingEllipticity", "trackingAngleDeg", "comaK", "comaPointX",
           "comaPointY", "sipMaxShiftPx", "sipTiltEquivDeg", "sipCornerShiftsPx"]


def series_text(frames: list[SeriesFrame], result: dict, s) -> str:
    rows = []
    for f in frames:
        m = f.metrics
        sf = m.get("surface") if m else None
        tilt = series_metric("tilt").get(m) if m else None
        point = series_metric("comaPoint").get(m) if m else None
        rows.append([f.name, f.side, f.side_source, f.ha_h, f.alt_deg, f.focus_pos, f.temp_c, m["n"] if m else None,
                     sf["f_center"] if sf else None, sf["f_edge"] if sf else None,
                     math.hypot(*tilt) if tilt else None, sf["tilt_dir_deg"] if sf else None,
                     m["rings"]["e_rad"] if m and m.get("rings") else None,
                     m["tracking"]["ellipticity"] if m and m.get("tracking") else None,
                     m["tracking"]["angle_deg"] if m and m.get("tracking") else None,
                     m["coma"]["k"] if m and m.get("coma") else None, point[0] if point else None,
                     point[1] if point else None, sip_max_shift(sip_corner_shifts(f)) if f.sip else None,
                     sip_tilt_equivalent_deg(f), sip_corner_shifts(f)])
    comparison = []
    for row in result["rows"]:
        sides = {side: row["stats"][k] for k, side in enumerate(result["sides"]) if row["stats"][k]}
        comparison.append({"value": row["metric"].label, "vector": row["metric"].vector, "sides": sides,
                           "difference": row["diff"], "verdict": row["verdict"], "drift": row["drift"]})
    data = {"optics_type": OPTICS_TYPES[s.optics_type], "tracking_subtracted": s.subtract_tracking,
            "columns": COLUMNS, "frames": rows,
            "failed": [f"{f.name}: {f.error}" for f in frames if f.error],
            "comparison": comparison, "notes": result["notes"]}
    # The column names are already camelCase; _prepare leaves them as they are.
    return "Series before and after a meridian flip (JSON, key series):\n" + ai_json({"series": data})


def png_base64(png: bytes) -> str:
    return base64.standard_b64encode(png).decode("ascii")


def build_message(s, single: dict | None, series: dict | None, maps: list[dict] | None) -> dict:
    """The user message. maps: [{label, png (bytes)}] or None."""
    parts = []
    if single:
        parts.append(single_frame_text(single, s))
    if series:
        parts.append(series_text(series["frames"], series["result"], s))
    detail = DETAIL_INSTRUCTIONS[min(max(s.ai_detail, 0), len(DETAIL_INSTRUCTIONS) - 1)]
    language = s.ai_language.strip() or "English"
    parts.append(("The maps of the single frame are attached above (" + ", ".join(m["label"] for m in maps) + ")."
                  if maps else "No maps attached.") + "\n" + detail + f"\nWrite all texts, the labels included, in {language}.")
    content: list[dict] = []
    for m in maps or []:
        content.append({"type": "text", "text": "Map: " + m["label"]})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": png_base64(m["png"])}})
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return {"role": "user", "content": content}
