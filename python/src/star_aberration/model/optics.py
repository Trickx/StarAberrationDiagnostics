"""Optics, camera, mount and guiding from the FITS header or the settings.

No FITS keyword names the mount type, so it is estimated:
    PIERSIDE East/West   only a German equatorial mount has a pier side
    TELESCOP             the mount driver's name (ASCOM/INDI call the mount
                         "telescope"): EQMod, iOptron, AM5 ... equatorial;
                         AZ-GTi, Seestar, Dwarf, NexStar ... alt-azimuth
An alt-azimuth mount without a derotator turns the field during the
exposure: the stars become arcs around the rotation center, a tangential
pattern growing with the distance.

Guiding: no keyword says whether it ran; the ASIAIR writes the guide camera
(GUIDECAM). The user chooses it; with "Unknown" and a guide camera in the
header it counts as guided, method unknown.
"""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass, field

from .log import Log

MOUNT_TYPES = ["Unknown", "German equatorial (GEM)", "Equatorial fork", "Alt-azimuth, tracked", "Not tracked"]
MOUNT_UNKNOWN, MOUNT_GEM, MOUNT_FORK, MOUNT_ALTAZ, MOUNT_NONE = range(5)

GUIDING_TYPES = ["Unknown", "Not guided", "Guide scope", "Off-axis guider (OAG)", "Guided, method unknown"]
GUIDING_UNKNOWN, GUIDING_OFF, GUIDING_SCOPE, GUIDING_OAG, GUIDING_ON = range(5)

_FLOAT_PREFIX = re.compile(r"^\s*[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|Infinity)")


def js_parse_float(s) -> float:
    """parseFloat of JavaScript: the leading number of a string, else NaN."""
    m = _FLOAT_PREFIX.match(str(s))
    if not m:
        return float("nan")
    try:
        return float(m.group(0))
    except ValueError:
        return float("nan")


def js_parse_int(s) -> int | None:
    m = re.match(r"^\s*[+-]?\d+", str(s))
    return int(m.group(0)) if m else None


def keyword_values(keywords: list[tuple[str, str]]) -> dict[str, str]:
    """{NAME: value} (first occurrence; strings without their quotes)."""
    values: dict[str, str] = {}
    for name, value in keywords:
        key = str(name).strip().upper()
        if key and key not in values:
            values[key] = re.sub(r"^'+|'+$", "", str(value).strip()).strip()
    return values


@dataclass
class Optics:
    pixel_pitch_um: float = 0.0
    focal_length_mm: float = 0.0
    aperture_mm: float = 0.0
    mount: dict = field(default_factory=dict)  # {type, source, obs}
    guiding: dict = field(default_factory=dict)  # {type, source}

    @property
    def physical(self) -> bool:
        return self.pixel_pitch_um > 0 and self.focal_length_mm > 0 and self.aperture_mm > 0

    @property
    def f_ratio(self) -> float | None:
        return self.focal_length_mm / self.aperture_mm if self.focal_length_mm > 0 and self.aperture_mm > 0 else None


def read_optics_from_header(keywords: list[tuple[str, str]]) -> dict:
    """{pixel_pitch_um, focal_length_mm, aperture_mm, sources} from XPIXSZ,
    FOCALLEN, APTDIA (or FOCALLEN/FOCRATIO); 0 where absent or implausible."""
    result = {"pixel_pitch_um": 0.0, "focal_length_mm": 0.0, "aperture_mm": 0.0, "sources": {}}
    values: dict[str, float] = {}
    for name, value in keywords:
        key = str(name).strip().upper()
        raw = re.sub(r"^'+|'+$", "", str(value).strip()).strip()
        v = js_parse_float(raw)
        if math.isfinite(v) and key not in values:
            values[key] = v

    def take(name, lo, hi):
        v = values.get(name)
        return v if v is not None and lo <= v <= hi else 0.0

    pitch = take("XPIXSZ", 0.5, 50)
    if pitch > 0:
        result["pixel_pitch_um"] = pitch
        result["sources"]["pixel_pitch_um"] = "XPIXSZ"
    focal = take("FOCALLEN", 10, 20000)
    if focal > 0:
        result["focal_length_mm"] = focal
        result["sources"]["focal_length_mm"] = "FOCALLEN"
    aperture = take("APTDIA", 10, 3000)
    if aperture > 0:
        result["aperture_mm"] = aperture
        result["sources"]["aperture_mm"] = "APTDIA"
    else:
        f_ratio = take("FOCRATIO", 1, 50)
        if f_ratio > 0 and focal > 0:
            result["aperture_mm"] = focal / f_ratio
            result["sources"]["aperture_mm"] = "FOCALLEN/FOCRATIO"
    return result


def read_camera_from_header(keywords: list[tuple[str, str]]) -> str:
    """INSTRUME, with the binning when above 1, or ""."""
    values = keyword_values(keywords)
    camera = values.get("INSTRUME", "")
    if not camera:
        return ""
    bx = js_parse_int(values.get("XBINNING", "")) or 0
    by = js_parse_int(values.get("YBINNING", "")) or 0
    if bx > 1 or by > 1:
        camera += f" (bin {bx if bx > 0 else 1}x{by if by > 0 else bx})"
    return camera


def estimate_mount(values: dict[str, str]) -> dict:
    pier = values.get("PIERSIDE", "").upper()
    if "EAST" in pier or "WEST" in pier:
        return {"type": MOUNT_GEM, "source": "PIERSIDE " + values["PIERSIDE"]}
    tel = values.get("TELESCOP", "")
    if re.search(r"alt.?az|\baz\b|az-?gti|seestar|dwarf|vespera|stellina|nexstar|evolution|\bslt\b", tel, re.I):
        return {"type": MOUNT_ALTAZ, "source": f"TELESCOP '{tel}'"}
    if re.search(r"eqmod|gsserver|gs server|green ?swamp|\beq\d|heq|neq|\bcem|\bgem|ioptron|10micron|mach ?\d|"
                 r"astro-?physics|losmandy|avalon|\bam[35]\b|onstep|cgem|\bcgx|\bcge\b", tel, re.I):
        return {"type": MOUNT_GEM, "source": f"TELESCOP '{tel}'"}
    return {"type": MOUNT_UNKNOWN, "source": ""}


def effective_guiding(guiding_type: int, values: dict[str, str]) -> dict:
    t = min(max(guiding_type or 0, 0), len(GUIDING_TYPES) - 1)
    if t != GUIDING_UNKNOWN:
        return {"type": t, "source": "dialog"}
    cam = values.get("GUIDECAM", "")
    if cam:
        return {"type": GUIDING_ON, "source": f"GUIDECAM '{cam}'"}
    return {"type": GUIDING_UNKNOWN, "source": ""}


def guiding_causes(t: int) -> str:
    """What a common elongation means with this guiding."""
    if t == GUIDING_OFF:
        return ("Not guided: the mount's tracking - the periodic error of the worm (on an EQ6-R one period takes "
                "about 479 s, so it differs from frame to frame) and the drift of the polar alignment. Guiding "
                "removes both.")
    if t == GUIDING_SCOPE:
        return ("Guided with a guide scope: when it is the same in every frame, differential flexure between guide "
                "scope and main optics (often changing with altitude), mirror flop or a moving focuser - or "
                "astigmatism on the axis; when it changes from frame to frame, the guiding (RMS, oscillation, DEC "
                "backlash), balance and wind.")
    if t == GUIDING_OAG:
        return ("Guided with an off-axis guider (no differential flexure): when it is the same in every frame, "
                "rather astigmatism on the axis (optics) or a moving focuser; when it changes from frame to frame, "
                "the guiding (RMS, oscillation, DEC backlash), balance and wind.")
    if t == GUIDING_ON:
        return ("Guided, method unknown: with a guide scope differential flexure or mirror flop, with an off-axis "
                "guider rather astigmatism on the axis when it is the same in every frame; changing from frame to "
                "frame, the guiding, balance and wind. Choose the guiding method (Setup > General) for a clearer "
                "verdict.")
    return ""


# ---------------------------------------------------------------------------
# Header values

def parse_sexagesimal(s: str) -> float:
    """"22 47 43", "+58:03:01" -> 22.795..., 58.050..."""
    parts = re.split(r"[ :]+", s.strip())
    v = 0.0
    for p in reversed(parts):
        v = abs(js_parse_float(p)) + v / 60
    return -v if parts[0].startswith("-") else v


def keyword_number(values: dict[str, str], names: list[str]) -> dict | None:
    """The first of the keywords that holds a number: {value, name,
    sexagesimal}."""
    for name in names:
        raw = values.get(name)
        if not raw:
            continue
        sexa = re.match(r"^[+-]?\d+([ :]+\d+(\.\d*)?){1,2}$", raw) is not None
        v = parse_sexagesimal(raw) if sexa else js_parse_float(raw)
        if math.isfinite(v):
            return {"value": v, "name": name, "sexagesimal": sexa}
    return None


def parse_fits_date(s: str | None) -> float | None:
    """A FITS date (UTC) in ms since 1970, or None."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", s or "")
    if not m:
        return None
    base = calendar.timegm((int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), 0, 0, 0, 0))
    return base * 1000.0 + float(m[6]) * 1000.0


def gmst_deg(time_ms: float) -> float:
    """Greenwich mean sidereal time in degrees."""
    j2000 = calendar.timegm((2000, 1, 1, 12, 0, 0, 0, 0, 0)) * 1000.0
    d = (time_ms - j2000) / 86400000.0
    return ((280.46061837 + 360.98564736629 * d) % 360 + 360) % 360


def mount_observation(values: dict[str, str]) -> dict:
    """Exposure (s), altitude, azimuth and site latitude (deg); None where
    missing."""
    def take(names):
        k = keyword_number(values, names)
        return k["value"] if k else None
    return {"exposure_s": take(["EXPTIME", "EXPOSURE"]), "alt_deg": take(["CENTALT", "OBJCTALT"]),
            "az_deg": take(["CENTAZ", "OBJCTAZ"]), "lat_deg": take(["SITELAT", "LAT-OBS", "OBSGEO-B"])}


def field_rotation_smear_px(obs: dict | None, w: float, h: float) -> float | None:
    """The arc a corner star describes on an alt-azimuth mount during the
    exposure (px): rate = Omega cos(lat) cos(az) / cos(alt), times the
    exposure, times the half diagonal."""
    if not obs or None in (obs.get("exposure_s"), obs.get("alt_deg"), obs.get("az_deg"), obs.get("lat_deg")):
        return None
    r = math.pi / 180
    cos_alt = math.cos(obs["alt_deg"] * r)
    if cos_alt < 0.05:
        return None
    rate = 7.2921e-5 * math.cos(obs["lat_deg"] * r) * math.cos(obs["az_deg"] * r) / cos_alt
    return abs(rate) * obs["exposure_s"] * math.hypot(w, h) / 2


def resolve_optics(keywords: list[tuple[str, str]] | None, s, debayer: bool, log: Log | None = None) -> Optics:
    """The optics for the physical conversions: the settings' values, or with
    optics_from_header only those of the header (a value it lacks stays 0).
    SuperPixel debayering doubles the effective pixel pitch."""
    if s.optics_from_header:
        optics = Optics()
    else:
        optics = Optics(s.pixel_pitch_um, s.focal_length_mm, s.aperture_mm)
    if s.optics_from_header and keywords is not None:
        hdr = read_optics_from_header(keywords)
        used = []
        if hdr["pixel_pitch_um"] > 0:
            optics.pixel_pitch_um = hdr["pixel_pitch_um"]
            used.append(f"pixel pitch {optics.pixel_pitch_um:.2f} µm ({hdr['sources']['pixel_pitch_um']})")
        if hdr["focal_length_mm"] > 0:
            optics.focal_length_mm = hdr["focal_length_mm"]
            used.append(f"focal length {optics.focal_length_mm:.1f} mm ({hdr['sources']['focal_length_mm']})")
        if hdr["aperture_mm"] > 0:
            optics.aperture_mm = hdr["aperture_mm"]
            used.append(f"aperture {optics.aperture_mm:.1f} mm ({hdr['sources']['aperture_mm']})")
        if log is not None:
            if used:
                log.write("Optics from FITS header: " + ", ".join(used) + (
                    ". The others are not set (uncheck \"Read the values from the FITS header\" to enter them)."
                    if len(used) < 3 else "."))
            else:
                log.write("No optics keywords (XPIXSZ, FOCALLEN, APTDIA/FOCRATIO) in the FITS header - not set "
                          "(uncheck \"Read the values from the FITS header\" to enter them).")
    if debayer:
        if log is not None and optics.pixel_pitch_um > 0:
            log.write(f"SuperPixel debayer: effective pixel pitch {2 * optics.pixel_pitch_um:.2f} µm "
                      f"(2 x {optics.pixel_pitch_um:.2f} µm) used for all physical conversions.")
        optics.pixel_pitch_um *= 2
    values = keyword_values(keywords or [])
    mount = estimate_mount(values) if s.optics_from_header else \
        {"type": min(max(s.mount_type or 0, 0), len(MOUNT_TYPES) - 1), "source": "dialog"}
    optics.mount = {"type": mount["type"], "source": mount["source"], "obs": mount_observation(values)}
    optics.guiding = effective_guiding(s.guiding_type, values)
    return optics
