"""One measured star."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

SIGMA_TO_FWHM = 2.0 * math.sqrt(2.0 * math.log(2.0))  # Gaussian sigma -> FWHM, about 2.35482


def moffat_alpha_to_fwhm(beta: float) -> float:
    """Moffat core width alpha -> FWHM factor for a given beta."""
    return 2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0)


@dataclass
class Star:
    """A star as fitted: position (px, image coordinates, y down), FWHM along
    the two axes of the fit, eccentricity e = sqrt(1 - b^2/a^2) and the
    rotation of the fit's x axis (degrees, counterclockwise on the screen,
    i.e. from +x toward -y).

    eccentricity_raw/rotation_raw keep the values as fitted when the
    tracking component has been subtracted. asym_x/asym_y (px) and
    m3_x/m3_y (dimensionless) are the PSF asymmetry (see coma.py), None
    when not measured.
    """

    x: float
    y: float
    fwhm_x: float
    fwhm_y: float
    eccentricity: float
    rotation: float
    eccentricity_raw: float | None = None
    rotation_raw: float | None = None
    asym_x: float | None = None
    asym_y: float | None = None
    m3_x: float | None = None
    m3_y: float | None = None
    m3_se: float | None = None
    flux: float = 0.0

    @property
    def fwhm(self) -> float:
        """The mean FWHM (px)."""
        return 0.5 * (self.fwhm_x + self.fwhm_y)

    def copy(self) -> "Star":
        return replace(self)
