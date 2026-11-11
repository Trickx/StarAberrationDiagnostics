"""Images: loading (FITS, XISF), FITS keywords, SuperPixel debayering and
the automatic display stretch.

Pixel values are float32 in [0, 1]; a mono image has the shape (h, w), a
color image (h, w, 3). Row 0 is the top of the image (FITS files written
bottom-up, ROWORDER = 'BOTTOM-UP', are flipped).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SUPPORTED_EXTENSIONS = (".fits", ".fit", ".fts", ".xisf")


@dataclass
class ImageData:
    pixels: np.ndarray
    keywords: list[tuple[str, str]] = field(default_factory=list)
    path: str = ""
    name: str = ""

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def is_color(self) -> bool:
        return self.pixels.ndim == 3 and self.pixels.shape[2] >= 3

    def channel(self, k: int = 0) -> np.ndarray:
        """One channel as a 2D array (channel 0 of a color image: red, the
        channel the PSF fits use, as DynamicPSF)."""
        return self.pixels if self.pixels.ndim == 2 else self.pixels[:, :, k]

    def keyword_values(self) -> dict[str, str]:
        """The keywords by name (upper case, the first of each name wins),
        values as text without quotes."""
        out: dict[str, str] = {}
        for name, value in self.keywords:
            key = name.strip().upper()
            if key and key not in out:
                out[key] = unquote(value)
        return out


def unquote(value) -> str:
    v = str(value).strip()
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        v = v[1:-1].replace("''", "'").strip()
    return v


def _normalize(data: np.ndarray) -> np.ndarray:
    """Pixel data as float32 in [0, 1]: integers by their type's range,
    floats as they are (rescaled by their maximum when above 1)."""
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        out = (data.astype(np.float64) - info.min) / float(info.max - info.min)
        return out.astype(np.float32)
    out = np.nan_to_num(data.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    hi = float(out.max()) if out.size else 1.0
    lo = float(out.min()) if out.size else 0.0
    if lo < 0 or hi > 1:
        span = hi - min(lo, 0.0)
        if span > 0:
            out = (out - min(lo, 0.0)) / span
    return out


def load_image(path: str | os.PathLike) -> ImageData:
    """Loads a FITS or XISF image. Raises ValueError with a message for the
    user when the file cannot be read."""
    path = str(path)
    ext = Path(path).suffix.lower()
    if ext in (".fits", ".fit", ".fts"):
        img = _load_fits(path)
    elif ext == ".xisf":
        img = _load_xisf(path)
    else:
        raise ValueError(f"Unsupported file type: {ext} (FITS or XISF)")
    img.path = path
    img.name = Path(path).stem
    return img


def read_keywords(path: str) -> list[tuple[str, str]]:
    """The FITS keywords of a file without reading its pixels."""
    ext = Path(path).suffix.lower()
    if ext in (".fits", ".fit", ".fts"):
        from astropy.io import fits
        with fits.open(path, memmap=True) as hdul:
            for hdu in hdul:
                if hdu.header.get("NAXIS", 0) >= 2:
                    return _fits_header_keywords(hdu.header)
            return _fits_header_keywords(hdul[0].header)
    if ext == ".xisf":
        from xisf import XISF
        meta = XISF(path).get_images_metadata()
        return _xisf_keywords(meta[0]) if meta else []
    raise ValueError(f"Unsupported file type: {ext}")


def _fits_header_keywords(header) -> list[tuple[str, str]]:
    out = []
    for card in header.cards:
        if card.keyword in ("", "COMMENT", "HISTORY"):
            continue
        v = card.value
        if isinstance(v, bool):
            text = "T" if v else "F"
        elif isinstance(v, str):
            text = "'" + v.replace("'", "''") + "'"
        else:
            text = str(v)
        out.append((card.keyword, text))
    return out


def _load_fits(path: str) -> ImageData:
    from astropy.io import fits
    try:
        with fits.open(path, memmap=False) as hdul:
            hdu = next((h for h in hdul if h.data is not None and h.data.ndim >= 2), None)
            if hdu is None:
                raise ValueError("The FITS file holds no image.")
            data = np.asarray(hdu.data)
            header = hdu.header
            keywords = _fits_header_keywords(header)
            row_order = str(header.get("ROWORDER", "")).strip().upper()
    except OSError as e:
        raise ValueError(f"Cannot read the FITS file: {e}") from e
    if data.ndim == 3:
        # FITS cubes are (channels, rows, columns).
        data = np.moveaxis(data[:3], 0, -1) if data.shape[0] in (1, 3, 4) else data[..., 0]
        if data.ndim == 3 and data.shape[2] == 1:
            data = data[:, :, 0]
    pixels = _normalize(data)
    if row_order == "BOTTOM-UP":
        pixels = pixels[::-1].copy()
    return ImageData(pixels=np.ascontiguousarray(pixels), keywords=keywords)


def _xisf_keywords(meta: dict) -> list[tuple[str, str]]:
    out = []
    for name, entries in (meta.get("FITSKeywords") or {}).items():
        for e in entries:
            out.append((name, str(e.get("value", ""))))
    return out


def _load_xisf(path: str) -> ImageData:
    from xisf import XISF
    try:
        x = XISF(path)
        meta = x.get_images_metadata()
        data = x.read_image(0)
    except Exception as e:  # the xisf package raises various errors
        raise ValueError(f"Cannot read the XISF file: {e}") from e
    data = np.asarray(data)
    if data.ndim == 3:
        data = data[:, :, 0] if data.shape[2] == 1 else data[:, :, :3]
    return ImageData(pixels=np.ascontiguousarray(_normalize(data)), keywords=_xisf_keywords(meta[0]) if meta else [])


# ---------------------------------------------------------------------------
# SuperPixel debayering

def bayer_pattern_from_keywords(img: ImageData) -> str | None:
    """The CFA pattern from BAYERPAT (with XBAYROFF/YBAYROFF), or None."""
    kv = img.keyword_values()
    pat = kv.get("BAYERPAT", "").upper().replace(" ", "")
    if pat not in ("RGGB", "BGGR", "GBRG", "GRBG"):
        return None
    try:
        xoff = int(float(kv.get("XBAYROFF", "0"))) % 2
        yoff = int(float(kv.get("YBAYROFF", "0"))) % 2
    except ValueError:
        xoff = yoff = 0
    grid = [[pat[0], pat[1]], [pat[2], pat[3]]]
    if yoff:
        grid = grid[::-1]
    if xoff:
        grid = [row[::-1] for row in grid]
    return grid[0][0] + grid[0][1] + grid[1][0] + grid[1][1]


def debayer_superpixel(img: ImageData, pattern: str = "Auto") -> ImageData:
    """Groups each 2 x 2 Bayer block into one RGB pixel without interpolation
    (half the resolution on each axis), so that the star profiles are not
    broadened. Raises ValueError when the pattern is unknown."""
    if img.is_color:
        raise ValueError("The image is already in color; debayering needs a raw CFA image.")
    if pattern == "Auto":
        found = bayer_pattern_from_keywords(img)
        if found is None:
            raise ValueError("Unable to acquire CFA pattern information (no BAYERPAT keyword): "
                             "choose the Bayer pattern explicitly.")
        pattern = found
    pattern = pattern.upper()
    if pattern not in ("RGGB", "BGGR", "GBRG", "GRBG"):
        raise ValueError(f"Unknown Bayer pattern: {pattern}")
    p = img.pixels
    h2, w2 = p.shape[0] // 2, p.shape[1] // 2
    p = p[:2 * h2, :2 * w2]
    sub = {(0, 0): p[0::2, 0::2], (0, 1): p[0::2, 1::2], (1, 0): p[1::2, 0::2], (1, 1): p[1::2, 1::2]}
    pos = {(0, 0): pattern[0], (0, 1): pattern[1], (1, 0): pattern[2], (1, 1): pattern[3]}
    r = next(sub[k] for k, c in pos.items() if c == "R")
    b = next(sub[k] for k, c in pos.items() if c == "B")
    gs = [sub[k] for k, c in pos.items() if c == "G"]
    g = 0.5 * (gs[0] + gs[1])
    rgb = np.stack([r, g, b], axis=-1).astype(np.float32)
    return ImageData(pixels=np.ascontiguousarray(rgb), keywords=list(img.keywords), path=img.path,
                     name=img.name + "_debayered")


# ---------------------------------------------------------------------------
# Display stretch (PixInsight's automatic screen transfer function)

def mtf(m: float, x: np.ndarray) -> np.ndarray:
    """The midtones transfer function."""
    x = np.clip(x, 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = ((m - 1) * x) / ((2 * m - 1) * x - m)
    y = np.where(x <= 0, 0.0, np.where(x >= 1, 1.0, y))
    return y


def auto_stretch_params(channel: np.ndarray, shadows_clip: float = -2.8, target_bkg: float = 0.25) -> tuple[float, float]:
    """(shadows c0, midtones m) of the automatic stretch of one channel."""
    sample = channel[::max(1, channel.shape[0] // 1024), ::max(1, channel.shape[1] // 1024)].ravel()
    med = float(np.median(sample))
    madn = 1.4826 * float(np.median(np.abs(sample - med)))
    c0 = min(max(0.0, med + shadows_clip * madn), 1.0) if madn > 0 else 0.0
    x = med - c0
    if x <= 0:
        return c0, 0.5
    m = float(mtf(target_bkg, np.array([x]))[0]) if x < 1 else 0.5
    return c0, m


def stretched_rgb8(img: ImageData) -> np.ndarray:
    """The image stretched for display (linked channels) as uint8 RGB
    (h, w, 3) - the background of the maps."""
    p = img.pixels
    chans = [p] if p.ndim == 2 else [p[:, :, k] for k in range(3)]
    params = [auto_stretch_params(c) for c in chans]
    c0 = float(np.mean([q[0] for q in params]))
    m = float(np.mean([q[1] for q in params]))
    out = []
    for c in chans:
        x = np.clip((c - c0) / max(1e-12, 1 - c0), 0, 1)
        out.append((mtf(m, x) * 255 + 0.5).astype(np.uint8))
    if len(out) == 1:
        out = out * 3
    return np.ascontiguousarray(np.stack(out, axis=-1))
