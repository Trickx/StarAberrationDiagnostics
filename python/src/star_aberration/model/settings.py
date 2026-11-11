"""The settings with their factory defaults, remembered between runs.

The settings are stored as JSON in the user's configuration directory
(``settings.json``). The Anthropic API key is never part of them: it is
kept in a file of its own (``api_key``, readable by the user only), so that
a settings file can be passed on without it.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

BAYER_PATTERNS = ["Auto", "RGGB", "BGGR", "GBRG", "GRBG"]


@dataclass
class Settings:
    # ---- Star detection and PSF fit
    threshold: float = 3.0
    radius: int = 8
    use_moffat: bool = False
    mad_outlier_factor: float = 3.0  # reject fits with MAD > factor x median(MAD); 0 = off
    max_candidates: int = 1500  # brightest candidates for the PSF fit; 0 = no limit
    # The detector parameters, applied only with sd_custom.
    sd_custom: bool = False
    sd_structure_layers: int = 5  # largest detectable structure about 2^n px
    sd_sensitivity: float = 0.5  # 0..1, higher = fainter stars
    sd_peak_response: float = 0.5  # 0..1, higher = more tolerant of flat profiles
    sd_max_distortion: float = 0.6  # 0..1, higher = more elongated stars accepted
    sd_allow_clustered: bool = False
    debayer: bool = False
    bayer_pattern: str = "Auto"
    # ---- General
    vector_scale: float = 1.0
    do_export: bool = True
    pixel_pitch_um: float = 0.0
    focal_length_mm: float = 0.0
    aperture_mm: float = 0.0
    optics_from_header: bool = True
    optics_type: int = 0  # index into OPTICS_TYPES
    mount_type: int = 0  # index into MOUNT_TYPES
    guiding_type: int = 0  # index into GUIDING_TYPES
    subtract_tracking: bool = True
    measure_asymmetry: bool = True
    # ---- Star shape map
    show_streamlines: bool = True
    shape_significant_only: bool = False
    shape_show_cells: bool = True
    shape_show_model: bool = False
    shape_model_significant_only: bool = False
    shape_show_match: bool = False
    shape_show_defects: bool = True
    streamline_radius_percent: float = 12.0
    show_orientation_heatmap: bool = False
    orientation_heatmap_degree: int = 2
    hide_ellipses: bool = False
    # ---- Star size map
    fwhm_grid_details: bool = False
    fwhm_cell_value: int = 0  # 0 = px, 1 = arcsec, 2 = ratio to the center
    # ---- Tilt map
    show_3d_tilt_plot: bool = True
    show_tilt_axis: bool = True
    # ---- Coma map
    show_star_asym_arrows: bool = False
    star_asym_min_sigma: float = 2.0
    star_asym_color_by_strength: bool = False
    show_coma_streamlines: bool = True
    coma_streamline_radius_percent: float = 24.0
    coma_streamline_min_percent: float = 20.0
    show_coma_arrows: bool = True
    # ---- AI review
    ai_send_map: bool = True
    ai_language: str = "English"
    ai_detail: int = 1  # index into AI_DETAIL_LEVELS
    ai_save_log: bool = True
    # ---- Series
    series_show_maps: bool = True
    series_save_maps: bool = True
    series_save_mosaic: bool = True

    def copy(self) -> "Settings":
        return dataclasses.replace(self)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """The settings from a dict; unknown keys are ignored, missing ones
        keep their defaults, and values of the wrong type are rejected."""
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            default = getattr(s, f.name)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    setattr(s, f.name, value)
            elif isinstance(default, int):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    setattr(s, f.name, int(value))
            elif isinstance(default, float):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    setattr(s, f.name, float(value))
            elif isinstance(default, str):
                if isinstance(value, str):
                    setattr(s, f.name, value)
        if s.bayer_pattern not in BAYER_PATTERNS:
            s.bayer_pattern = "Auto"
        return s


def config_dir() -> Path:
    """The directory of the settings: $XDG_CONFIG_HOME or the platform's
    usual place."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "StarAberrationDiagnostics"


class SettingsStore:
    """Loads and saves the settings and the API key."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else config_dir()

    @property
    def settings_path(self) -> Path:
        return self.directory / "settings.json"

    @property
    def key_path(self) -> Path:
        return self.directory / "api_key"

    def load(self) -> Settings:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings()
        return Settings.from_dict(data if isinstance(data, dict) else {})

    def save(self, settings: Settings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings.to_dict(), indent=1) + "\n", encoding="utf-8")

    # ---- Window state (splitter, size), opaque for the model
    def load_ui_state(self) -> dict:
        try:
            data = json.loads((self.directory / "ui_state.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_ui_state(self, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "ui_state.json").write_text(json.dumps(state), encoding="utf-8")

    # ---- API key
    def api_key(self) -> tuple[str, str] | None:
        """(key, source): the environment variable ANTHROPIC_API_KEY, else
        the stored key; None without either."""
        env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env:
            return env, "environment"
        try:
            stored = self.key_path.read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        return (stored, "settings") if stored else None

    def store_api_key(self, key: str) -> None:
        key = key.strip()
        if not key:
            try:
                self.key_path.unlink()
            except FileNotFoundError:
                pass
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
