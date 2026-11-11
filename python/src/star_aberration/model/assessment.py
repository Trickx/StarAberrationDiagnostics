"""The rule-based assessment: judges the measured values of an analysis and
suggests corrections, as one would read the console report.

Each finding has a level (OK, Note, Action), a topic, what was found and
what to do; the actions come back in the order in which they are best
worked through (focus before tracking before tilt ...). The thresholds are
starting points, not calibrated limits. Where the direction of a correction
depends on the optical system, the optics type decides the wording.

A refractor differs from a Newtonian in what is normal: hardly any coma by
design, field curvature as the main off-axis error without a flattener, a
lens collimated at the factory, and lateral color that elongates the stars
of a color frame radially at the edge. Catadioptric systems focus by moving
the primary mirror (mirror flop). Slow systems (f/8 and above) have a deep
critical focus zone and are seeing-limited.
"""

from __future__ import annotations

import html
import math

from .optics import GUIDING_UNKNOWN, MOUNT_ALTAZ, MOUNT_NONE, MOUNT_TYPES, MOUNT_UNKNOWN, guiding_causes

# The optical systems; the indices are stored with the settings (new types
# are appended). kind: newtonian | refractor | rasa | sct | cat | astrograph |
# camera-lens | unknown.
OPTICS_PROFILES = [
    {"name": "Unknown", "kind": "unknown"},
    {"name": "Reflector without corrector (e.g. bare Newtonian)", "kind": "newtonian"},
    {"name": "Reflector with coma corrector / reducer", "kind": "newtonian", "corrected": True,
     "corrector": "corrector", "distance": "corrector-sensor distance"},
    {"name": "Refractor without flattener", "kind": "refractor", "lateral_color": True},
    {"name": "Refractor with flattener / reducer", "kind": "refractor", "corrected": True, "lateral_color": True,
     "corrector": "flattener", "distance": "flattener-sensor distance"},
    {"name": "RASA / Hyperstar (f/2)", "kind": "rasa", "corrected": True, "flat_by_design": True,
     "mirror_focus": True, "corrector": "corrector lens", "distance": "camera distance to the corrector lens",
     "back_focus": "the back focus Celestron or Starizona specify for your model and camera adapter"},
    {"name": "SCT with reducer (e.g. f/6.3)", "kind": "sct", "corrected": True, "mirror_focus": True,
     "corrector": "reducer", "distance": "reducer-sensor distance",
     "back_focus": "the reducer's specified distance (usually 105 mm)"},
    {"name": "Aplanatic SCT (EdgeHD and similar)", "kind": "sct", "corrected": True, "flat_by_design": True,
     "mirror_focus": True, "corrector": "built-in corrector", "distance": "back focus",
     "back_focus": "the specified back focus (EdgeHD: 133.35 mm for the 8\", 146.05 mm for 9.25\"-14\", "
                   "105 mm with the 0.7x reducer)"},
    {"name": "Classic SCT or Maksutov (no corrector)", "kind": "cat", "mirror_focus": True},
    {"name": "Astrograph with built-in flattener (Petzval, quadruplet)", "kind": "astrograph", "corrected": True,
     "flat_by_design": True, "lateral_color": True, "corrector": "built-in flattener",
     "distance": "camera back focus", "back_focus": "the back focus the manufacturer specifies"},
    {"name": "Photographic lens", "kind": "camera-lens", "lateral_color": True},
]
OPTICS_TYPES = [o["name"] for o in OPTICS_PROFILES]
(OPTICS_UNKNOWN, OPTICS_UNCORRECTED, OPTICS_CORRECTED, OPTICS_REFRACTOR, OPTICS_REFRACTOR_FLAT, OPTICS_RASA,
 OPTICS_SCT_REDUCER, OPTICS_SCT_APLANATIC, OPTICS_CLASSIC_CAT, OPTICS_ASTROGRAPH, OPTICS_CAMERA_LENS) = range(11)

ASSESS_OK, ASSESS_NOTE, ASSESS_ACTION = 0, 1, 2
LEVEL_LABELS = ["OK", "Note", "Action"]
ORDER = ["Mount", "Focus", "Tracking", "Tilt", "Collimation", "Spacing", "Coma", "Field curvature", "Data"]


def critical_focus_half_um(f_ratio: float) -> float:
    """Half the critical focus zone (um) at f-ratio N: 2.44 lambda N^2, lambda = 0.55 um."""
    return 2.44 * 0.55 * f_ratio * f_ratio


def _side(deg: float) -> str:
    names = ["right", "lower right", "bottom", "lower left", "left", "upper left", "top", "upper right"]
    return names[int(math.floor(((deg % 360) + 360) % 360 / 45 + 0.5)) % 8]


def _pct(v: float) -> str:
    return f"{100 * v:.0f}%"


def assess_image(m: dict, optics_type: int) -> dict:
    """{findings: [{level, topic, text, advice}], actions (the Action
    findings in the order to work through them)}. m: the metrics of
    report_analysis(); "coma" is absent when the asymmetry was not
    measured, None with too few stars."""
    P = OPTICS_PROFILES[optics_type] if 0 <= optics_type < len(OPTICS_PROFILES) else OPTICS_PROFILES[0]
    refractor = P["kind"] == "refractor"
    camera_lens = P["kind"] == "camera-lens"
    classic_cat = P["kind"] == "cat"
    bare = P["kind"] != "unknown" and not P.get("corrected")
    corrected = P.get("corrected") is True
    corrector = P.get("corrector") or "corrector or flattener"
    distance = P.get("distance") or "corrector-sensor distance"
    from_back_focus = f" Start from {P['back_focus']}." if P.get("back_focus") else ""
    lateral_color = (" With lenses, lateral color produces the same radial pattern in a color or broadband frame: "
                     "compare with a narrowband frame or a single color channel." if P.get("lateral_color") else "")
    if_corrector = "Rule of thumb: " if corrected else "If a corrector or flattener is used: rule of thumb, "
    findings: list[dict] = []

    def add(level, topic, text, advice=""):
        findings.append({"level": level, "topic": topic, "text": text, "advice": advice or ""})

    f_ratio = m.get("f_ratio")
    cfz = critical_focus_half_um(f_ratio) if f_ratio and f_ratio > 0 else None

    if m["n"] < 150:
        add(ASSESS_NOTE, "Data", f"Only {m['n']} stars measured - the verdicts below are uncertain.",
            "Lower the detection threshold, raise the maximum number of stars, or expose longer.")
    if cfz is not None and f_ratio >= 8:
        add(ASSESS_NOTE, "Data", f"At f/{f_ratio:.1f} the critical focus zone is about ±{cfz:.0f} µm: tilt and field "
            "curvature smaller than that hardly enlarge the stars, and the seeing dominates their size - small "
            "differences across the field are uncertain.",
            "Judge tilt and curvature only when they are clear, and compare several frames.")

    s = m.get("surface")
    rings = m.get("rings")
    focus_bad = bool(rings and rings.get("defocus_suspect")) or (s is not None and s["c"][3] < 0)
    if focus_bad:
        add(ASSESS_ACTION, "Focus", "The image center is softer than the field edge: the frame is probably out of "
            "focus, or focused on the edge.", "Refocus on the center. Tilt and spacing verdicts are unreliable until then.")
    caveat = " Uncertain: the frame seems out of focus." if focus_bad else ""

    mt = m.get("mount")
    if mt and mt["type"] == MOUNT_ALTAZ:
        smear = mt.get("field_rotation_corner_px")
        src = f" ({mt['source']})" if mt.get("source") and mt["source"] != "dialog" else ""
        text = ("Alt-azimuth mount" + src + ": without a derotator the field turns during the exposure, the stars "
                "become arcs around the rotation center - a tangential pattern growing with the distance, like "
                "field curvature or too short a spacing.")
        if smear is not None:
            text += f" Here about {smear:.1f} px at the corners in {mt['exposure_s']:.0f} s" + (
                f" (FWHM in the center {s['f_center']:.2f} px)." if s is not None and s["f_center"] > 0 else ".")
        add(ASSESS_ACTION if smear is not None and s is not None and smear > 0.5 * s["f_center"] else ASSESS_NOTE,
            "Mount", text, "Judge a tangential edge pattern only with short exposures or a derotator; shorter "
            "exposures, an altitude away from the zenith and an azimuth near east or west reduce the rotation.")
    elif mt and mt["type"] == MOUNT_NONE:
        add(ASSESS_NOTE, "Mount", "Not tracked: all stars trail along the right ascension.",
            "The shape results show the trail, not the optics; use the FWHM results only across the trail, or "
            "short exposures.")
    elif mt and mt["type"] != MOUNT_UNKNOWN:
        src = f" ({mt['source']})" if mt.get("source") and mt["source"] != "dialog" else ""
        add(ASSESS_OK, "Mount", MOUNT_TYPES[mt["type"]] + src + ": no field rotation.")

    t = m.get("tracking")
    if t:
        if t["ellipticity"] < 0.03:
            add(ASSESS_OK, "Tracking", f"No significant common elongation ({t['ellipticity']:.3f}).")
        else:
            gd = m.get("guiding")
            advice = guiding_causes(gd["type"]) if gd and gd["type"] != GUIDING_UNKNOWN else (
                "Check guiding (RMS, calibration), balance, cable drag, wind and flexure. An axis that stays the "
                "same over several frames points to the mount, a changing one to wind.")
            if not m.get("tracking_subtracted"):
                advice += " Enable \"Subtract it from all stars\" so that it does not mask the optical patterns."
            add(ASSESS_ACTION if t["ellipticity"] > 0.07 else ASSESS_NOTE, "Tracking",
                f"All stars share an elongation of {t['ellipticity']:.3f} along {t['angle_deg']:.0f}° "
                "(image coordinates).", advice)

    if s is not None:
        rel = (s["f_bad"] - s["f_good"]) / max(1e-6, 0.5 * (s["f_bad"] + s["f_good"]))
        if rel < 0.05:
            add(ASSESS_OK, "Tilt", f"No significant one-sided blur ({_pct(rel)} between opposite edges).")
        else:
            text = (f"The {_side(s['tilt_dir_deg'])} side is softer: FWHM {s['f_bad']:.2f} px against "
                    f"{s['f_good']:.2f} px on the opposite side ({_pct(rel)}).")
            if s["dz_um"] is not None:
                text += f" Δz ≈ ±{s['dz_um']:.0f} µm at the edge, tilt ≈ {s['tilt_deg']:.3f}°."
                if cfz is not None:
                    text += f" Critical focus zone ±{cfz:.0f} µm."
            if m.get("siril"):
                q = [m["siril"][k] for k in ("m1", "m2", "m3", "m4")]
                text += f" Quadrants differ by {_pct((max(q) - min(q)) / (sum(q) / 4))}."
            advice = (f"Adjust the tilt of the camera on the axis toward the {_side(s['tilt_dir_deg'])} "
                      f"({s['tilt_dir_deg']:.0f}° in image coordinates). Whether that side has to move toward or away "
                      "from the optics cannot be told from one frame: change it slightly and compare.")
            if P["kind"] == "rasa":
                advice += (" At f/2 even a few micrometers count: use a tilt adapter between the camera and the "
                           "corrector, and check that the camera sits square on the adapter.")
            if P.get("mirror_focus"):
                advice += (" Focusing moves the primary mirror: a mirror that shifted can tilt the image as well - "
                           "lock it if possible, and compare frames before adjusting.")
            add(ASSESS_ACTION if rel > 0.10 else ASSESS_NOTE, "Tilt", text + caveat, advice)

    if not rings or rings.get("e_rad") is None:
        add(ASSESS_NOTE, "Spacing", "Too few stars in the outer field for a radial/tangential verdict.")
    elif abs(rings["e_rad"]) <= 0.04:
        add(ASSESS_OK, "Spacing", f"No radial or tangential pattern at the field edge (eps_rad {rings['e_rad']:.3f}).")
    else:
        e = rings["e_rad"]
        level = ASSESS_ACTION if abs(e) > 0.08 else ASSESS_NOTE
        if e > 0:
            text = f"Stars at the field edge are elongated radially (eps_rad {e:.3f})."
            if camera_lens:
                add(ASSESS_NOTE, "Spacing", text + " A camera lens has no adjustable corrector spacing; this can be a "
                    "lens aberration or lateral color in a color frame." + caveat,
                    "There is no back focus to adjust. Compare a single color channel, check focus and camera "
                    "alignment, and compare several frames.")
            elif optics_type == OPTICS_UNCORRECTED:
                add(ASSESS_NOTE, "Spacing", text + " This is the normal coma of an uncorrected system.",
                    "A coma corrector would remove it; there is no spacing to adjust.")
            elif classic_cat:
                add(ASSESS_NOTE, "Spacing", text + " A classic SCT has coma at the field edge by design; a Maksutov "
                    "has little - there it is rather astigmatism or a tilted camera." + caveat,
                    "There is no spacing to adjust. A reducer-corrector or an aplanatic design would reduce it.")
            elif optics_type == OPTICS_REFRACTOR:
                add(ASSESS_NOTE, "Spacing", text + " A refractor has hardly any coma: without a flattener this is "
                    "astigmatism of the curved field, or lateral color in a color or broadband frame." + caveat,
                    "There is no spacing to adjust. A flattener matching the refractor would reduce it; compare with "
                    "a narrowband frame or a single color channel to tell lateral color apart.")
            else:
                add(level, "Spacing", text + f" With a {corrector} this usually means under-correction." +
                    lateral_color + caveat,
                    if_corrector + f"increase the {distance} in small steps (e.g. 0.5 mm) and measure again." +
                    from_back_focus)
        else:
            text = f"Stars at the field edge are elongated tangentially, concentric around the center (eps_rad {e:.3f})."
            if camera_lens:
                add(ASSESS_NOTE, "Spacing", text + " This can be field curvature or astigmatism of the camera lens." +
                    caveat, "There is no spacing to adjust. Check focus, especially at the center, and compare "
                    "several frames.")
            elif optics_type == OPTICS_REFRACTOR:
                add(ASSESS_NOTE, "Spacing", text + " Without a flattener this is the field curvature or astigmatism "
                    "of the refractor." + caveat,
                    "Expected; there is no spacing to adjust. Check the focus on the center; a flattener matching "
                    "the refractor would reduce it.")
            elif classic_cat:
                add(ASSESS_NOTE, "Spacing", text + " A classic SCT or Maksutov has a curved field: this is its field "
                    "curvature or astigmatism." + caveat,
                    "Expected; there is no spacing to adjust. Check the focus on the center; a reducer-corrector "
                    "would flatten the field.")
            elif optics_type == OPTICS_UNCORRECTED:
                add(level, "Spacing", text + " In an uncorrected system this is field curvature or astigmatism." +
                    caveat, "Check the focus; a field flattener or corrector would reduce it.")
            else:
                add(level, "Spacing", text + caveat,
                    if_corrector + f"decrease the {distance} in small steps. Defocus combined with field curvature "
                    "looks the same, so check the focus first." + from_back_focus)

    if s is not None and s["f_center"] > 0:
        rel = (s["f_edge"] - s["f_center"]) / s["f_center"]
        if rel > 0.15:
            text = (f"Stars at the field edge are {_pct(rel)} larger than in the center (FWHM {s['f_edge']:.2f} "
                    f"against {s['f_center']:.2f} px).")
            if s["sag_um"] is not None:
                text += f" That is about {s['sag_um']:.0f} µm of focus difference"
                text += f" (critical focus zone ±{cfz:.0f} µm)." if cfz is not None else "."
            if camera_lens:
                advice = "Camera lenses vary in their edge correction by design; check focus and compare several frames."
            elif optics_type == OPTICS_REFRACTOR:
                advice = "Expected for a refractor without a flattener; a flattener matching it would remove it."
            elif classic_cat:
                advice = "Expected for a classic SCT or Maksutov; a reducer-corrector would flatten the field."
            elif bare:
                advice = "Expected for an uncorrected system; a flattener or corrector would reduce it."
            elif P.get("flat_by_design"):
                advice = (f"The field of this system is meant to be flat: the {distance} is probably off (see Spacing)."
                          + from_back_focus)
            elif corrected:
                advice = (f"The {corrector} does not flatten the field fully: check its spacing (see Spacing)."
                          + (from_back_focus or " Start from the back focus the manufacturer specifies."))
            else:
                advice = "With a corrector or flattener, check its spacing (see Spacing); without one this is expected."
            level = ASSESS_NOTE if (camera_lens or optics_type == OPTICS_REFRACTOR or classic_cat) else \
                ASSESS_ACTION if rel > 0.30 else ASSESS_NOTE
            add(level, "Field curvature", text + caveat, advice)
        elif rel >= 0:
            add(ASSESS_OK, "Field curvature", f"Field edge and center are about equally sharp ({_pct(rel)}).")

    if "coma" not in m:
        add(ASSESS_NOTE, "Coma", "Not measured.", "Enable \"Measure PSF asymmetry\" on Maps > Coma.")
    elif m["coma"] is None:
        add(ASSESS_NOTE, "Coma", "Too few stars with a valid asymmetry measurement.")
    elif not m["coma"]["significant"]:
        c = m["coma"]
        add(ASSESS_OK, "Coma", f"No significant field-dependent coma (k = {c['k']:.3f} ± {c['k_sd']:.3f}).")
    else:
        c = m["coma"]
        if camera_lens:
            add(ASSESS_NOTE, "Coma", f"Field-dependent asymmetry in the camera lens (k = {c['k']:.3f} ± {c['k_sd']:.3f}).",
                "Possible causes include lens aberrations, lateral color in a color or broadband frame, or a tilted "
                "or decentered lens. Compare a single color channel and several frames; check that the camera and "
                "lens sit straight without play.")
        elif optics_type == OPTICS_REFRACTOR:
            add(ASSESS_NOTE, "Coma", f"Field-dependent asymmetry, the flares pointing "
                f"{'away from' if c['k'] > 0 else 'toward'} the coma-free point (k = {c['k']:.3f} ± {c['k_sd']:.3f}). "
                "A refractor has hardly any coma by design.",
                "Likely causes: lateral color in a color or broadband frame, astigmatism of the curved field, or a "
                "tilted or decentered lens. Compare with a narrowband frame or a single color channel; if it stays, "
                "check the focuser and the lens cell.")
        elif c["k"] > 0:
            text = (f"Under-corrected coma: the flares point away from the coma-free point (k = {c['k']:.3f} ± "
                    f"{c['k_sd']:.3f}).")
            if optics_type == OPTICS_UNCORRECTED:
                add(ASSESS_NOTE, "Coma", text + " Normal for an uncorrected Newtonian.", "A coma corrector would remove it.")
            elif classic_cat:
                add(ASSESS_NOTE, "Coma", text + " Normal for a classic SCT; a Maksutov has little coma, there it points "
                    "to collimation.", "A reducer-corrector or an aplanatic design would remove it.")
            else:
                add(ASSESS_ACTION, "Coma", text + (" This system is meant to be coma-free." if P.get("flat_by_design")
                                                   else "") + lateral_color,
                    if_corrector + f"increase the {distance} in small steps." + from_back_focus)
        else:
            add(ASSESS_ACTION, "Coma", f"Over-corrected coma: the flares point toward the coma-free point "
                f"(k = {c['k']:.3f} ± {c['k_sd']:.3f}).",
                if_corrector + f"decrease the {distance} in small steps." + from_back_focus)

        dx, dy = c["x0"] - m["w"] / 2, c["y0"] - m["h"] / 2
        dist = math.hypot(dx, dy)
        off = dist / c["r_max"]
        sd = math.hypot(c["x0_sd"], c["y0_sd"])
        if off > 0.15 and math.isfinite(sd) and dist > 2 * sd:
            dir_deg = (math.degrees(math.atan2(dy, dx)) + 360) % 360
            lens_optics = refractor or camera_lens or P["kind"] == "astrograph"
            if lens_optics:
                advice = ("The lenses are collimated at the factory: check first that the focuser, " +
                          ("the flattener, " if optics_type == OPTICS_REFRACTOR_FLAT else "") +
                          "the adapters and the camera sit straight and without play; only then the lens cell (only "
                          "some can be adjusted). With the little coma of a lens system, the coma-free point is "
                          "uncertain - compare several frames.")
            elif P["kind"] == "sct":
                advice = ("Collimate with the secondary mirror's screws (defocused star in the center, rings "
                          "concentric). Focusing moves the primary: approach focus from the same direction, lock the "
                          "mirror if possible.")
            elif classic_cat:
                advice = ("A classic SCT is collimated with the secondary mirror's screws; most Maksutovs are "
                          "collimated at the factory - check the visual back, the focuser and the camera for tilt first.")
            elif P["kind"] == "rasa":
                advice = ("Check that the camera sits centered and square on the corrector (adapter, tilt plate); "
                          "then the collimation of the primary mirror, where the model allows it.")
            elif optics_type == OPTICS_UNCORRECTED:
                advice = "Collimate the primary and secondary mirror."
            elif optics_type == OPTICS_CORRECTED:
                advice = "Collimate the optics, or check the corrector for tilt or decentering."
            else:
                advice = "Collimate the optics; with a corrector, also check it for tilt or decentering."
            if t and t["ellipticity"] >= 0.03:
                d = abs(((dir_deg - t["angle_deg"]) % 180 + 180) % 180)
                if min(d, 180 - d) < 20:
                    advice += (" Caution: the offset lies along the tracking elongation and may come from uneven "
                               "tracking - compare several frames first.")
            add(ASSESS_NOTE if lens_optics else ASSESS_ACTION, "Collimation",
                f"The coma-free point lies {_pct(off)} of the half diagonal off the center, toward the "
                f"{_side(dir_deg)} ({dir_deg:.0f}°).", advice)
        else:
            add(ASSESS_OK, "Collimation", f"The coma-free point is near the center ({_pct(off)} of the half diagonal).")

    actions = sorted((f for f in findings if f["level"] == ASSESS_ACTION), key=lambda f: ORDER.index(f["topic"]))
    return {"findings": findings, "actions": actions}


def format_assessment_html(assessment: dict) -> str:
    """The assessment as HTML for the Assessment page."""
    lines = []
    if not assessment["actions"]:
        lines.append("<b>No corrections needed.</b>")
    else:
        lines.append("<b>Suggested order of corrections:</b>")
        for i, a in enumerate(assessment["actions"]):
            lines.append(f"&nbsp;&nbsp;&nbsp;{i + 1}. {html.escape(a['topic'])}")
    for f in assessment["findings"]:
        lines.append("")
        lines.append(f"<b>[{LEVEL_LABELS[f['level']]}] {html.escape(f['topic'])}:</b> {html.escape(f['text'])}")
        if f["advice"]:
            lines.append(f"&nbsp;&nbsp;&nbsp;→ {html.escape(f['advice'])}")
    return "<br>".join(lines)


def format_assessment_text(assessment: dict) -> str:
    """The assessment as plain text (console, logs)."""
    lines = []
    if not assessment["actions"]:
        lines.append("No corrections needed.")
    else:
        lines.append("Suggested order of corrections:")
        lines += [f"   {i + 1}. {a['topic']}" for i, a in enumerate(assessment["actions"])]
    for f in assessment["findings"]:
        lines.append("")
        lines.append(f"[{LEVEL_LABELS[f['level']]}] {f['topic']}: {f['text']}")
        if f["advice"]:
            lines.append(f"   → {f['advice']}")
    return "\n".join(lines)
