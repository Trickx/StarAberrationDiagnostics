"""Tests of the model on synthetic star fields (no GUI, no network)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from star_aberration.model import analysis, assessment, image, results_io, stats
from star_aberration.model.log import SILENT
from star_aberration.model.optics import resolve_optics
from star_aberration.model.settings import Settings, SettingsStore


def star_field(radial: float = 0.6, n: int = 450, seed: int = 1, w: int = 900, h: int = 600) -> image.ImageData:
    """Gaussian stars whose major axis points radially, growing with the
    distance from the center by the factor (1 + radial r)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), 0.05, np.float32)
    for _ in range(n):
        x, y = rng.uniform(15, w - 15), rng.uniform(15, h - 15)
        phi = math.atan2(y - h / 2, x - w / 2)
        r = math.hypot(x - w / 2, y - h / 2) / math.hypot(w / 2, h / 2)
        sa, sb = 1.5 * (1 + radial * r), 1.5
        x0, x1, y0, y1 = int(x - 12), int(x + 13), int(y - 12), int(y + 13)
        dx, dy = xx[y0:y1, x0:x1] - x, yy[y0:y1, x0:x1] - y
        u = dx * math.cos(phi) + dy * math.sin(phi)
        v = -dx * math.sin(phi) + dy * math.cos(phi)
        img[y0:y1, x0:x1] += rng.uniform(0.05, 0.5) * np.exp(-0.5 * (u * u / sa / sa + v * v / sb / sb))
    img += rng.normal(0, 0.004, img.shape).astype(np.float32)
    return image.ImageData(np.clip(img, 0, 1), [("XPIXSZ", "3.76"), ("FOCALLEN", "600"), ("APTDIA", "120")],
                           "", "synthetic")


@pytest.fixture(scope="module")
def measured():
    img = star_field()
    s = Settings()
    a = analysis.analyze_image(img, s, True, SILENT)
    assert a is not None
    return img, s, a


def test_stats_match_the_script():
    assert stats.median_of([1, 2, 3, 4]) == 3  # the upper middle value, as the script
    assert stats.poly_exponents(1) == [(0, 0), (0, 1), (1, 0)]
    assert math.isnan(stats.robust_spread([1.0]))
    assert stats.trimmed_mean25([1, 2, 3, 4, 100, 5, 6, 7]) == pytest.approx(4.5)


def test_orientation_convention(measured):
    """The major axis of the fit points radially: psi = -rotation."""
    _, _, a = measured
    errs = []
    for s in a.stars:
        if s.eccentricity > 0.5:
            phi = math.degrees(math.atan2(s.y - a.h / 2, s.x - a.w / 2))
            errs.append(abs(((-s.rotation - phi) + 90) % 180 - 90))
    assert len(errs) > 50
    assert np.median(errs) < 5


def test_radial_pattern_is_assessed_as_spacing(measured):
    img, s, a = measured
    optics = resolve_optics(img.keywords, s, False)
    assert optics.physical and optics.pixel_pitch_um == 3.76
    rep = analysis.report_analysis(a, analysis.stars_for(a, True), s, optics, SILENT)
    m = rep["metrics"]
    assert m["rings"]["e_rad"] > 0.1
    assert m["tracking"]["ellipticity"] < 0.03
    result = assessment.assess_image(m, assessment.OPTICS_CORRECTED)
    assert [f["topic"] for f in result["actions"]][0] == "Spacing"


def test_results_round_trip(measured, tmp_path):
    _, s, a = measured
    p = tmp_path / "r_aberration.json"
    results_io.save_results(a, s, str(p))
    b = results_io.load_results(str(p))
    assert (b.w, b.h, len(b.stars)) == (a.w, a.h, len(a.stars))
    assert b.stars[0].x == pytest.approx(a.stars[0].x, rel=1e-6)
    assert b.coma_fit["k"] == pytest.approx(a.coma_fit["k"], rel=1e-6)


def test_settings_store(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = SettingsStore(tmp_path)
    s = Settings(threshold=2.5, ai_language="Deutsch")
    st.save(s)
    assert st.load() == s
    st.store_api_key("sk-test")
    assert st.api_key() == ("sk-test", "settings")
    assert (tmp_path / "api_key").stat().st_mode & 0o077 == 0  # readable by the user only
    st.store_api_key("")
    assert st.api_key() is None


def test_ai_message(measured):
    from star_aberration.ai.prompt import SYSTEM_PROMPT, build_message
    img, s, a = measured
    optics = resolve_optics(img.keywords, s, False)
    stars = analysis.stars_for(a, True)
    rep = analysis.report_analysis(a, stars, s, optics, SILENT)
    single = {"analysis": a, "stars": stars, "metrics": rep["metrics"], "optics": optics,
              "assessment": assessment.assess_image(rep["metrics"], 0)}
    msg = build_message(s, single, None, [{"label": "Star size", "png": b"\x89PNG"}])
    assert [b["type"] for b in msg["content"]] == ["text", "image", "text"]
    text = msg["content"][-1]["text"]
    assert '"eRad"' in text and '"fwhmGridAssessment"' in text and "Write all texts" in text
    assert "Star Aberration Diagnostics" in SYSTEM_PROMPT
