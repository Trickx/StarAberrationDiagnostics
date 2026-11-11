"""Builds the figures of the StarAberrationDiagnostics handbook: fig/<name>.svg
(English) and de/fig/<name>.svg (German). Each figure is computed once; only
its labels differ between the languages.

Usage, from pi-scripts/StarAberrationDiagnostics:
   python3 handbook-src/figures.py            writes all figures
   python3 handbook-src/figures.py --check    compares with the files on disk

Edit the figures here, never in fig/ or de/fig/."""
import math, os, random, re, sys

BG, MUTED, TEXT, ACCENT, OK, WARN, GRID = "#0c141d", "#a8b6c8", "#e7edf5", "#64c7ff", "#82e0aa", "#ffab5e", "#2a3b4f"
PANEL = "#101c28"
FONT = 'font-family="Segoe UI, Helvetica Neue, Arial, sans-serif"'


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------
def fmt(v):
    """Floats with one decimal, everything else as given."""
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def el(tag, **attrs):
    """An empty element; attribute names with _ become -, values via fmt()."""
    a = " ".join(f'{k.rstrip("_").replace("_", "-")}="{fmt(v)}"' for k, v in attrs.items() if v is not None)
    return f"<{tag} {a}/>"


def xml_text(s):
    """Text content: & and < must be escaped, or the SVG file is not valid XML."""
    return s.replace("&", "&amp;").replace("<", "&lt;")


def text(x, y, s, size=13, fill=MUTED, anchor="start", weight="normal", italic=False):
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{style}>{xml_text(s)}</text>')


def line(x1, y1, x2, y2, stroke=MUTED, w=1.2, **extra):
    return el("line", x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2), stroke=stroke, stroke_width=w, **extra)


def path(pts, stroke, w, **extra):
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return f'<path d="{d}" ' + el("x", stroke=stroke, stroke_width=w, fill="none", **extra)[3:]


class Figure:
    """Collects the elements of one figure. The id prefix of the markers and the
    star gradient comes from the English label, so both languages share it."""

    def __init__(self, name, w, h, label_en):
        self.name, self.w, self.h = name, w, h
        self.p = re.sub(r"[^A-Za-z0-9]", "", label_en)[:12]
        self.body = []

    def arrow(self, color=""):
        return f"url(#{self.p}ARR{color})"

    def star(self, cx, cy, rx, ry, angle=0.0, **extra):
        """A star image: an ellipse filled with the star gradient."""
        return el("ellipse", cx=float(cx), cy=float(cy), rx=float(rx), ry=float(ry), fill=f"url(#{self.p}STAR)",
                  transform=f"rotate({angle:.1f} {cx:.1f} {cy:.1f})", **extra)

    def add(self, *items):
        self.body += items

    def svg(self, label):
        p = self.p
        markers = "".join(f'''
  <marker id="{p}ARR{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>''' for k, c in (("", MUTED), ("B", ACCENT), ("O", WARN), ("G", OK)))
        defs = f'''<defs>
  <radialGradient id="{p}STAR" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#ffffff"/>
    <stop offset="35%" stop-color="#fff1d6" stop-opacity="0.95"/>
    <stop offset="100%" stop-color="#ffd9a0" stop-opacity="0"/>
  </radialGradient>{markers}
</defs>'''
        return (f'<svg viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg" {FONT}>'
                f'{defs}<rect width="{self.w}" height="{self.h}" rx="10" fill="{BG}"/>{"".join(self.body)}</svg>')


FIGURES = []


def figure(name, w, h, labels):
    """Registers a figure function f(fig, t); t(key) gives the label in the
    language being built. labels = {"en": {...}, "de": {...}}, each with the
    key "label" for the aria-label."""
    def register(fn):
        FIGURES.append((name, w, h, labels, fn))
        return fn
    return register


# ---------------------------------------------------------------------------
# Chapter 1: star profile
# ---------------------------------------------------------------------------
@figure("star-profile", 640, 290, {
    "en": {"label": "Star profile", "x": "position across the star (px)", "y": "brightness", "bg": "background B",
           "peak": "peak: B + A", "amp": "amplitude A", "fwhm": "FWHM = 2.3548 σ", "half": "half of A"},
    "de": {"label": "Sternprofil", "x": "Position quer durch den Stern (px)", "y": "Helligkeit", "bg": "Hintergrund B",
           "peak": "Maximum: B + A", "amp": "Amplitude A", "fwhm": "FWHM = 2,3548 σ", "half": "halbes A"}})
def star_profile(f, t):
    x0, x1, yb = 70.0, 590.0, 240.0               # axes
    xc, sx = 330.0, 520 / 12.8                    # center, px of the plot per image pixel
    sigma, bg, amp = 2.0, 24.0, 176.0             # Gaussian in image pixels / plot px
    def y(u): return yb - bg - amp * math.exp(-u * u / (2 * sigma * sigma))
    f.add(line(x0, yb, x1, yb, marker_end=f.arrow()), line(x0, yb, x0, 25, marker_end=f.arrow()),
          text(585, 262, t("x"), anchor="end"), text(62, 18, t("y")))
    for i in range(-6, 7):                        # pixel values
        top = y(i)
        f.add(el("rect", x=xc + i * sx - (sx - 2) / 2, y=top, width=sx - 2, height=yb - top, fill=GRID, opacity="0.8"))
    f.add(path([(x0 + k * 2.6, y((x0 + k * 2.6 - xc) / sx)) for k in range(201)], ACCENT, "2.5"))
    hw = 1.1774 * sigma * sx                      # half width at half maximum
    f.add(line(x0, yb - bg, x1, yb - bg, OK, stroke_dasharray="5 4"), text(590, 234, t("bg"), fill=OK, anchor="end"),
          line(xc, yb - bg, xc, yb - bg - amp, w=1, stroke_dasharray="3 3"),
          text(364, 42, t("peak"), fill=TEXT), text(338, 168, t("amp")),
          line(xc - hw, 128, xc + hw, 128, WARN, 2), line(xc - hw, 120, xc - hw, 136, WARN, 2), line(xc + hw, 120, xc + hw, 136, WARN, 2),
          text(xc, 118, t("fwhm"), size=15, fill=WARN, anchor="middle", weight="bold"), text(xc + hw + 12, 133, t("half"), fill=WARN))


# ---------------------------------------------------------------------------
# Chapter 2: SuperPixel debayering
# ---------------------------------------------------------------------------
@figure("superpixel-debayer", 640, 250, {
    "en": {"label": "SuperPixel debayer", "none": "no interpolation", "l1": "one RGB pixel per 2×2 block:",
           "l2": "half resolution, effective pitch 2p"},
    "de": {"label": "SuperPixel-Debayering", "none": "ohne Interpolation", "l1": "ein RGB-Pixel pro 2×2-Block:",
           "l2": "halbe Auflösung, eff. Pixelabstand 2p"}})
def superpixel_debayer(f, t):
    color = {"R": "#d9534f", "G": "#4cae4c", "B": "#428bca"}
    for r in range(4):
        for c in range(4):
            ch = ("RG", "GB")[r % 2][c % 2]
            x, y = 60 + 38 * c, 30 + 38 * r
            f.add(el("rect", x=x, y=y, width=36, height=36, fill=color[ch], opacity="0.85"),
                  text(x + 18, y + 24, ch, size=14, fill="#fff", anchor="middle"))
    f.add(el("rect", x=58, y=28, width=78, height=78, fill="none", stroke=TEXT, stroke_width="2.5"),
          line(250, 105, 360, 105, w=2, marker_end=f.arrow()),
          text(305, 92, "SuperPixel", size=14, fill=TEXT, anchor="middle"), text(305, 128, t("none"), size=12, anchor="middle"))
    for r in range(2):
        for c in range(2):
            f.add(el("rect", x=390 + 76 * c, y=30 + 76 * r, width=73, height=73, fill="#8a7f6a", opacity="0.9"))
    f.add(el("rect", x=388, y=28, width=77, height=77, fill="none", stroke=TEXT, stroke_width="2.5"),
          text(466, 208, t("l1"), anchor="middle"), text(466, 226, t("l2"), anchor="middle"))


# ---------------------------------------------------------------------------
# Chapter 3: shape of a star
# ---------------------------------------------------------------------------
@figure("shape-measures", 640, 270, {
    "en": {"label": "Shape measures", "legend": "eccentricity e (map colors)  ·  ellipticity ε (verdicts)  ·  distortion χ (averaging)"},
    "de": {"label": "Formmaße", "legend": "Exzentrizität e (Kartenfarben)  ·  Elliptizität ε (Urteile)  ·  Verzerrung χ (Mittelung)"}})
def shape_measures(f, t):
    for k, q in enumerate((1.0, 0.9, 0.7, 0.5)):          # axis ratio b/a
        x = 95 + 150 * k
        f.add(f.star(x, 90, 42, 42 * q),
              el("ellipse", cx=x, cy=90, rx=29.4, ry=29.4 * q, fill="none", stroke=ACCENT, stroke_width="1.5"),
              text(x, 160, "b/a = " + t.num(q), size=15, fill=TEXT, anchor="middle", weight="bold"),
              text(x, 188, "e = " + t.num(math.sqrt(1 - q * q), 2), size=14, fill=WARN, anchor="middle"),
              text(x, 210, "ε = " + t.num(1 - q, 2), size=14, fill=OK, anchor="middle"),
              text(x, 232, "χ = " + t.num((1 - q * q) / (1 + q * q), 2), size=14, fill=ACCENT, anchor="middle"))
    f.add(text(320, 260, t("legend"), anchor="middle"))


@figure("psf-ellipse", 640, 300, {
    "en": {"label": "PSF ellipse", "img1": "image", "img2": "coordinates", "a": "a  (major half axis)", "b": "b  (minor)",
           "n1": "0° = right, 90° = down (image coordinates)", "n2": "orientation is defined only modulo 180°"},
    "de": {"label": "PSF-Ellipse", "img1": "Bild-", "img2": "koordinaten", "a": "a  (große Halbachse)", "b": "b  (kleine)",
           "n1": "0° = rechts, 90° = unten (Bildkoordinaten)", "n2": "Orientierung nur modulo 180° definiert"}})
def psf_ellipse(f, t):
    cx, cy, a, b, psi = 330, 150, 150, 70, 28            # ellipse, psi in degrees (y down)
    ca, sa = math.cos(math.radians(psi)), math.sin(math.radians(psi))
    cb, sb = math.cos(math.radians(psi + 90)), math.sin(math.radians(psi + 90))
    f.add(line(40, 40, 120, 40, w=1.4, marker_end=f.arrow()), text(126, 45, "x", size=15),
          line(40, 40, 40, 110, w=1.4, marker_end=f.arrow()), text(34, 128, "y", size=15),
          text(40, 150, t("img1"), size=12), text(40, 165, t("img2"), size=12),
          f.star(cx, cy, 0.9 * a, 0.9 * b, float(psi)),
          el("ellipse", cx=cx, cy=cy, rx=a, ry=b, fill="none", stroke=ACCENT, stroke_width=2, transform=f"rotate({psi} {cx} {cy})"),
          line(cx, cy, cx + a * ca, cy + a * sa, WARN, 2, marker_end=f.arrow("O")),
          text(cx + a * ca + 6, cy + a * sa - 4, t("a"), size=14, fill=WARN),
          line(cx, cy, cx + b * cb, cy + b * sb, OK, 2, marker_end=f.arrow("G")),
          text(cx + b * cb - 8, cy + b * sb + 20, t("b"), size=14, fill=OK, anchor="end"),
          line(cx, cy, 520, cy, w=1, stroke_dasharray="4 4"),
          f'<path d="M{cx + b},{cy} A{b},{b} 0 0 0 {cx + b * ca:.1f},{cy + b * sa:.1f}" stroke="{TEXT}" fill="none" stroke-width="1.4"/>',
          text(408, 136, "ψ", size=17, fill=TEXT, italic=True),
          text(620, 270, t("n1"), anchor="end"), text(620, 288, t("n2"), anchor="end"))


@figure("double-angle", 640, 250, {
    "en": {"label": "Double angle", "naive": "naive mean of the angles", "double": "double-angle mean",
           "r1": "20° and 340° → vector mean 0° → half: 0°  ✓", "r2": "(cos 2ψ, sin 2ψ) are averaged, then halved"},
    "de": {"label": "Doppelter Winkel", "naive": "naives Mittel der Winkel", "double": "Mittel über den doppelten Winkel",
           "r1": "20° und 340° → Mittel 0° → halbiert: 0°  ✓", "r2": "(cos 2ψ, sin 2ψ) mitteln, dann halbieren"}})
def double_angle(f, t):
    def stars(cx):                                         # two stars at 10° and 170°
        for deg in (10, 170):
            c, s = 70 * math.cos(math.radians(deg)), 70 * math.sin(math.radians(deg))
            f.add(line(cx - c, 125 + s, cx + c, 125 - s, ACCENT, 3))
    f.add(text(160, 30, t("naive"), size=15, fill=TEXT, anchor="middle", weight="bold"))
    stars(160)
    f.add(line(160, 195, 160, 55, "#ff7b7b", 3, stroke_dasharray="6 5"),
          text(160, 215, "(10° + 170°) / 2 = 90°  ✗", size=15, fill="#ff7b7b", anchor="middle"),
          text(480, 30, t("double"), size=15, fill=TEXT, anchor="middle", weight="bold"))
    stars(480)
    f.add(line(395, 125, 565, 125, OK, 3, stroke_dasharray="6 5"),
          text(480, 205, t("r1"), size=14, fill=OK, anchor="middle"), text(480, 228, t("r2"), anchor="middle"),
          line(320, 20, 320, 230, GRID, 1))


# ---------------------------------------------------------------------------
# Chapter 4: tracking
# ---------------------------------------------------------------------------
@figure("tracking-component", 640, 300, {
    "en": {"label": "Tracking component", "left": "every star shares one elongation", "right": "subtraction in the (χ₁, χ₂) plane",
           "star": "star", "trk": "tracking (median)", "r1": "star − tracking =", "r2": "what the optics did"},
    "de": {"label": "Nachführanteil", "left": "alle Sterne teilen eine Elongation", "right": "Subtraktion in der (χ₁, χ₂)-Ebene",
           "star": "Stern", "trk": "Nachführung (Median)", "r1": "Stern − Nachführung =", "r2": "Anteil der Optik"}})
def tracking_component(f, t):
    f.add(text(160, 28, t("left"), size=14, fill=TEXT, anchor="middle", weight="bold"))
    rng = random.Random(3)                                 # a fixed random star field
    for _ in range(26):
        x, y, size = 30 + 260 * rng.random(), 50 + 220 * rng.random(), 0.6 + 0.6 * rng.random()
        f.add(f.star(x, y, 15.5 * size, 9 * size, -25.0))
    f.add(text(470, 28, t("right"), size=14, fill=TEXT, anchor="middle", weight="bold"),
          line(330, 170, 620, 170, marker_end=f.arrow()), text(622, 188, "χ₁", size=14, anchor="end"),
          line(470, 280, 470, 50, marker_end=f.arrow()), text(478, 60, "χ₂", size=14),
          line(470, 170, 580, 80, ACCENT, "2.5", marker_end=f.arrow("B")), text(586, 74, t("star"), size=14, fill=ACCENT),
          line(470, 170, 540, 130, WARN, "2.5", marker_end=f.arrow("O")), text(550, 156, t("trk"), fill=WARN, anchor="middle"),
          line(540, 130, 580, 80, OK, "2.5", stroke_dasharray="6 4", marker_end=f.arrow("G")),
          text(460, 230, t("r1"), fill=OK, anchor="end"), text(460, 248, t("r2"), fill=OK, anchor="end"))


# ---------------------------------------------------------------------------
# Chapter 6: focus, tilt and field curvature
# ---------------------------------------------------------------------------
@figure("defocus-blur", 640, 300, {
    "en": {"label": "Defocus blur", "optics": "optics, aperture D", "focus": "focus", "sensor": "sensor", "blur": "blur d",
           "ratio": "focal ratio N = f / D"},
    "de": {"label": "Unschärfe durch Defokus", "optics": "Optik, Apertur D", "focus": "Fokus", "sensor": "Sensor", "blur": "Unschärfe d",
           "ratio": "Öffnungsverhältnis N = f / D"}})
def defocus_blur(f, t):
    xo, xf, xs, xe, yc = 80.0, 430.0, 520.0, 560.0, 150.0  # optics, focus, sensor, end of the rays, axis
    slope = 105 / (xf - xo)
    half = slope * (xs - xf)                               # half the blur on the sensor
    f.add(line(20, yc, 620, yc, GRID, 1, stroke_dasharray="4 4"),
          el("ellipse", cx=80, cy=150, rx=12, ry=110, fill="#1d3a55", stroke=ACCENT, stroke_width="1.5"),
          text(xo, 285, t("optics"), anchor="middle"))
    for sgn in (-1, 1):
        f.add(path([(xo, yc + sgn * 105), (xf, yc), (xe, yc - sgn * slope * (xe - xf))], ACCENT, "1.6"))
    f.add(line(xf, 40, xf, 260, OK, 2, stroke_dasharray="6 4"), text(xf, 32, t("focus"), fill=OK, anchor="middle"),
          line(xs, 40, xs, 260, WARN, 3), text(xs, 32, t("sensor"), fill=WARN, anchor="middle"),
          line(542, yc - half, 542, yc + half, TEXT, 2), line(536, yc - half, 548, yc - half, TEXT, 2),
          line(536, yc + half, 548, yc + half, TEXT, 2), text(554, 155, t("blur"), size=14, fill=TEXT),
          line(xf, 250, xs, 250, TEXT, "1.5", marker_end=f.arrow()), line(xs, 250, xf, 250, TEXT, "1.5", marker_end=f.arrow()),
          text((xf + xs) / 2, 272, "Δz", size=15, fill=TEXT, anchor="middle", italic=True),
          text(230, 60, t("ratio"), size=14, anchor="middle"),
          text(230, 80, "d = Δz / N   ⇔   Δz = N · d", size=15, fill=WARN, anchor="middle", weight="bold"))


@figure("tilt-and-curvature", 640, 320, {
    "en": {"label": "Tilt and curvature", "tilt": "sensor tilt", "curv": "field curvature", "plane": "focal plane", "sensor": "sensor",
           "behind": "behind focus", "sharp": "sharp", "front": "in front", "surface": "focal surface", "flat": "flat sensor",
           "c1": "sharp center,", "c2": "soft edge"},
    "de": {"label": "Verkippung und Bildfeldwölbung", "tilt": "Sensorverkippung", "curv": "Bildfeldwölbung", "plane": "Brennebene",
           "sensor": "Sensor", "behind": "hinter Fokus", "sharp": "scharf", "front": "davor", "surface": "Bildschale",
           "flat": "ebener Sensor", "c1": "Mitte scharf,", "c2": "Rand weich"}})
def tilt_and_curvature(f, t):
    def blur(d):                                           # star radius at a distance d from the focus
        return 6.6 + 0.77 * abs(d)
    f.add(text(160, 26, t("tilt"), size=15, fill=TEXT, anchor="middle", weight="bold"),
          text(480, 26, t("curv"), size=15, fill=TEXT, anchor="middle", weight="bold"),
          line(160, 50, 160, 280, OK, 2, stroke_dasharray="6 4"), text(154, 298, t("plane"), size=12, fill=OK, anchor="middle"),
          line(125, 60, 195, 270, WARN, 3), text(220, 215, t("sensor"), size=12, fill=WARN))
    for y, key in ((70, "behind"), (165, "sharp"), (260, "front")):
        x = 125 + (y - 60) / 3                             # on the tilted sensor
        f.add(el("circle", cx=x, cy=float(y), r=blur(x - 160), fill=f"url(#{f.p}STAR)"),
              text(100, y + 5, t(key), size=12, anchor="end"))
    f.add(line(320, 45, 320, 300, GRID, 1))
    def surface(y):                                        # the curved focal surface
        return 470 - 40 * ((y - 160) / 100) ** 2
    f.add(path([(surface(y), float(y)) for y in range(60, 261, 5)], OK, 2, stroke_dasharray="6 4"),
          text(425, 298, t("surface"), size=12, fill=OK, anchor="middle"),
          line(470, 50, 470, 280, WARN, 3), text(478, 280, t("flat"), size=12, fill=WARN))
    for y in (70, 115, 160, 205, 250):
        f.add(el("circle", cx=530.0, cy=float(y), r=blur(470 - surface(y)), fill=f"url(#{f.p}STAR)"))
    f.add(text(560, 165, t("c1"), size=12), text(560, 180, t("c2"), size=12))


@figure("siril-quadrants", 640, 300, {
    "en": {"label": "Siril quadrants", "q": ("TL", "TR", "BL", "BR"), "inner": "inner", "outer": "outer: r > {} R",
           "R": "R = half diagonal", "note": "each value: 25%-trimmed mean of the star FWHMs in that area"},
    "de": {"label": "Siril-Quadranten", "q": ("OL", "OR", "UL", "UR"), "inner": "innen", "outer": "außen: r > {} R",
           "R": "R = halbe Diagonale", "note": "jeder Wert: 25%-getrimmtes Mittel der Stern-FWHM im Bereich"}})
def siril_quadrants(f, t):
    x0, y0, w, h = 150, 30, 340, 226                       # the frame
    cx, cy = x0 + w / 2, y0 + h / 2
    R = math.hypot(w / 2, h / 2)
    f.add(el("rect", x=x0, y=y0, width=w, height=h, fill=PANEL, stroke=MUTED, stroke_width="1.5"),
          line(cx, y0, cx, y0 + h, w=1), line(x0, cy, x0 + w, cy, w=1),
          f'<clipPath id="{f.p}QC"><rect x="{x0}" y="{y0}" width="{w}" height="{h}"/></clipPath>',
          f'<g clip-path="url(#{f.p}QC)">',
          el("circle", cx=cx, cy=cy, r=0.75 * R, fill="none", stroke=WARN, stroke_width="1.5", stroke_dasharray="6 4"),
          "</g>",
          el("circle", cx=cx, cy=cy, r=0.25 * R, fill=OK, fill_opacity="0.12", stroke=OK, stroke_width="1.5"))
    for k, q in enumerate(t("q")):
        f.add(text(x0 + w / 4 * (1 + 2 * (k % 2)), y0 + h / 4 * (1 + 2 * (k // 2)) + 5, f"m{'₁₂₃₄'[k]} {q}",
                   size=15, fill=TEXT, anchor="middle", weight="bold"))
    f.add(text(cx, cy + 5, t("inner"), fill=OK, anchor="middle"),
          text(x0 + 6, y0 + h - 8, t("outer").format(t.num(0.75, 2)), size=12, fill=WARN),
          text(x0 + w + 12, cy, t("R"), size=12), text(320, 285, t("note"), anchor="middle"))


@figure("fwhm-squared-surface", 640, 280, {
    "en": {"label": "FWHM squared surface", "x": "X (−1 … +1 of the half diagonal)", "c0": "c₀ = FWHM² at the center",
           "c3": "c₃·(X²+Y²): the same on all sides → curvature", "c12": "+ c₁·X + c₂·Y: one side higher → tilt"},
    "de": {"label": "FWHM²-Fläche", "x": "X (−1 … +1 der halben Diagonale)", "c0": "c₀ = FWHM² in der Mitte",
           "c3": "c₃·(X²+Y²): auf allen Seiten gleich → Wölbung", "c12": "+ c₁·X + c₂·Y: eine Seite höher → Verkippung"}})
def fwhm_squared_surface(f, t):
    xc, sx, y0 = 330, 250, 164.0                           # X = 0, px per unit of X, c0
    c1, c3 = 35.2, 66.0                                    # tilt and curvature terms, in px
    xs = [80 + 5 * k for k in range(101)]
    f.add(line(60, 230, 600, 230, marker_end=f.arrow()), text(600, 252, t("x"), anchor="end"),
          line(xc, 235, xc, 30, w=1, marker_end=f.arrow()), text(338, 40, "FWHM²", size=14),
          path([(float(x), y0 - c3 * ((x - xc) / sx) ** 2) for x in xs], OK, "2.5", stroke_dasharray="6 4"),
          path([(float(x), y0 - c1 * (x - xc) / sx - c3 * ((x - xc) / sx) ** 2) for x in xs], ACCENT, "2.5"),
          line(80, y0, 580, y0, GRID, 1, stroke_dasharray="3 3"), text(86, 184, t("c0")),
          text(120, 70, t("c3"), fill=OK), text(120, 92, t("c12"), fill=ACCENT))


@figure("sign-ambiguity", 640, 240, {
    "en": {"label": "Sign ambiguity", "focus": "best focus", "v": "variant", "note": "the same blur at both edges — the sign of Δz is unknown from one frame"},
    "de": {"label": "Vorzeichen-Mehrdeutigkeit", "focus": "bester Fokus", "v": "Variante", "note": "gleiche Unschärfe an beiden Rändern — Vorzeichen von Δz unbekannt"}})
def sign_ambiguity(f, t):
    cx, cy, L, a = 320, 120, 190, math.radians(9)          # center, half length and angle of the sensor
    dx, dy = L * math.cos(a), L * math.sin(a)
    f.add(line(120, cy, 520, cy, OK, 2, stroke_dasharray="6 4"), text(526, 125, t("focus"), fill=OK),
          line(cx - dx, cy - dy, cx + dx, cy + dy, ACCENT, 3),
          line(cx - dx, cy + dy, cx + dx, cy - dy, WARN, 3, stroke_dasharray="8 5"),
          text(120, 86, t("v") + " 1", fill=ACCENT, anchor="end"), text(120, 162, t("v") + " 2", fill=WARN, anchor="end"))
    for x, r in ((150, 9), (490, 9), (320, 4)):
        f.add(el("circle", cx=float(x), cy=200.0, r=float(r), fill=f"url(#{f.p}STAR)"))
    f.add(text(320, 230, t("note"), anchor="middle"))


# ---------------------------------------------------------------------------
# Chapter 7: radial and tangential patterns
# ---------------------------------------------------------------------------
@figure("radial-and-tangential", 640, 300, {
    "en": {"label": "Radial and tangential", "rad": "radial (eps_rad > 0)", "under": "under-corrected",
           "tan": "tangential (eps_rad < 0)", "over": "over-corrected / curvature"},
    "de": {"label": "Radial und tangential", "rad": "radial (eps_rad > 0)", "under": "unterkorrigiert",
           "tan": "tangential (eps_rad < 0)", "over": "überkorrigiert / Wölbung"}})
def radial_and_tangential(f, t):
    rings = ((55, 8, 8.7, 4.1), (105, 14, 11.2, 3.2))      # radius, stars, half axes: longer farther out
    for cx, title, turn, note, color in ((160, "rad", 0, "under", ACCENT), (480, "tan", 90, "over", WARN)):
        f.add(text(cx, 26, t(title), size=15, fill=TEXT, anchor="middle", weight="bold"),
              el("circle", cx=float(cx), cy=160.0, r=6.0, fill=f"url(#{f.p}STAR)"))
        for r, n, a, b in rings:
            for k in range(n):
                phi = 360 * k / n
                f.add(f.star(cx + r * math.cos(math.radians(phi)), 160 + r * math.sin(math.radians(phi)), a, b, phi + turn))
        f.add(text(cx, 290, t(note), size=12, fill=color, anchor="middle"))
    f.add(line(320, 40, 320, 280, GRID, 1))


@figure("radial-ellipticity", 640, 250, {
    "en": {"label": "Radial ellipticity", "center": "image center", "phi": "radial direction φ", "psi": "major axis ψ",
           "note": "+ε: along the radius · −ε: across it · 0: at 45°"},
    "de": {"label": "Radiale Elliptizität", "center": "Bildmitte", "phi": "radiale Richtung φ", "psi": "große Achse ψ",
           "note": "+ε: entlang des Radius · −ε: quer dazu · 0: bei 45°"}})
def radial_ellipticity(f, t):
    x0, y0, x1, y1 = 130, 190, 420, 80                     # image center, star
    phi = math.atan2(y1 - y0, x1 - x0)                     # radial direction (y down)
    psi = phi - math.radians(35)                           # major axis of the star
    def at(a, r): return x1 + r * math.cos(a), y1 + r * math.sin(a)
    f.add(el("circle", cx=float(x0), cy=float(y0), r=7.0, fill=f"url(#{f.p}STAR)"),
          text(x0, y0 + 26, t("center"), size=12, anchor="middle"),
          line(x0, y0, x1, y1, w="1.3", stroke_dasharray="5 4"),
          line(x1, y1, *at(phi, 110), marker_end=f.arrow()))
    tx, ty = at(phi, 118)
    f.add(text(tx, ty + 16, t("phi"), size=12),
          f.star(x1, y1, 34, 16, math.degrees(psi)),
          el("ellipse", cx=float(x1), cy=float(y1), rx=0.62 * 34, ry=0.62 * 16, fill="none", stroke=ACCENT, stroke_width="1.4",
             transform=f"rotate({math.degrees(psi):.1f} {x1:.1f} {y1:.1f})"),
          line(x1, y1, *at(psi, 70), ACCENT, 2, marker_end=f.arrow("B")),
          text(*at(psi, 76), t("psi"), size=12, fill=ACCENT),
          text(40, 40, "eps_rad = ε · cos 2(ψ − φ)", size=17, fill=WARN, weight="bold"),
          text(40, 64, t("note")))


# ---------------------------------------------------------------------------
# Chapter 8: coma
# ---------------------------------------------------------------------------
@figure("coma-star", 640, 250, {
    "en": {"label": "Coma star", "fit": "fit center (symmetric model)", "centroid": "centroid offset",
           "m3": "third moment m₃ (weights the flare)",
           "t": ("Gaussian and Moffat are", "point-symmetric: they cannot", "tell on which side the flare is."),
           "pixels": "The pixels can:", "rho": "ρ = distance / σ"},
    "de": {"label": "Koma-Stern", "fit": "Fit-Zentrum (symm. Modell)", "centroid": "Schwerpunktversatz",
           "m3": "drittes Moment m₃ (gewichtet den Ausläufer)",
           "t": ("Gauß und Moffat sind", "punktsymmetrisch: Sie erkennen", "nicht, wo der Ausläufer liegt."),
           "pixels": "Die Pixel schon:", "rho": "ρ = Abstand / σ"}})
def coma_star(f, t):
    x0, y0 = 230, 125                                      # core of the star
    for i in range(7):                                     # the flare: ever larger, fainter, shifted
        f.add(el("ellipse", cx=x0 + i * 55 / 6, cy=y0, rx=22 + i * 26 / 6, ry=20.0 + 2 * i, fill=f"url(#{f.p}STAR)",
                 opacity=f"{0.55 - 0.06 * i:.2f}"))
    f.add(el("circle", cx=float(x0), cy=float(y0), r=26.0, fill=f"url(#{f.p}STAR)"),
          el("circle", cx=x0, cy=y0, r=4, fill=ACCENT), text(218, 87, t("fit"), size=12, fill=ACCENT, anchor="end"),
          el("circle", cx=246, cy=y0, r=4, fill=WARN),
          line(x0, 185, 246, 185, WARN, 2, marker_end=f.arrow("O")), text(252, 189, t("centroid"), size=12, fill=WARN),
          line(x0, 215, 290, 215, WARN, "2.5", marker_end=f.arrow("O")), text(296, 219, t("m3"), size=12, fill=WARN))
    for k, s in enumerate(t("t")):
        f.add(text(430, 60 + 18 * k, s))
    f.add(text(430, 124, t("pixels"), fill=TEXT), text(430, 146, "m₃ = Σ w·ρ²·ρ / Σ w", size=15, fill=WARN, weight="bold"),
          text(430, 166, t("rho")))


@figure("coma-field", 640, 300, {
    "en": {"label": "Coma field", "under": "under-corrected: k > 0", "over": "over-corrected: k < 0",
           "n1": "arrows: flare direction · green ring: coma-free point P₀ · cross: image center",
           "n2": "P₀ off center → decollimated (or decentered corrector)"},
    "de": {"label": "Komafeld", "under": "unterkorrigiert: k > 0", "over": "überkorrigiert: k < 0",
           "n1": "Pfeile: Ausläuferrichtung · grüner Ring: komafreier Punkt P₀ · Kreuz: Bildmitte",
           "n2": "P₀ außerhalb der Mitte → dejustiert (oder Korrektor dezentriert)"}})
def coma_field(f, t):
    for x0, title, k in ((20, "under", 0.22), (335, "over", -0.22)):   # panel, flare = k · (P − P0)
        cx, cy = x0 + 142.5, 140.0                         # image center
        px, py = cx + 38, cy - 22                          # coma-free point P0, off center
        f.add(el("rect", x=x0, y=40, width=285, height=200, fill=PANEL, stroke=GRID),
              text(cx, 28, t(title), size=14, fill=TEXT, anchor="middle", weight="bold"))
        for i in range(6):
            for j in range(4):
                x, y = x0 + 25 + 47 * i, 65 + 50 * j
                dx, dy = k * (x - px), k * (y - py)
                if math.hypot(dx, dy) >= 4:                # no arrow near P0
                    f.add(line(x, y, x + dx, y + dy, WARN, "1.8", marker_end=f.arrow("O")))
        f.add(el("circle", cx=px, cy=py, r=6, fill="none", stroke=OK, stroke_width=2),
              line(cx - 6, cy, cx + 6, cy, w=1.5), line(cx, cy - 6, cx, cy + 6, w=1.5))
    f.add(text(320, 262, t("n1"), anchor="middle"), text(320, 284, t("n2"), anchor="middle"))


# ---------------------------------------------------------------------------
# Chapter 10: series
# ---------------------------------------------------------------------------
@figure("meridian-flip", 640, 300, {
    "en": {"label": "Meridian flip", "before": "before the flip (West)", "after": "after the flip (East)", "finder": "finder",
           "camera": "camera", "gravity": "gravity",
           "note": "the tube is turned by 180° around its axis: gravity now pulls on the optics from the other side"},
    "de": {"label": "Meridian-Flip", "before": "vor dem Flip (West)", "after": "nach dem Flip (Ost)", "finder": "Sucher",
           "camera": "Kamera", "gravity": "Schwerkraft",
           "note": "Tubus um 180° um seine Achse gedreht: die Schwerkraft zieht von der anderen Seite an der Optik"}})
def meridian_flip(f, t):
    """A German equatorial mount; the right side is the left one mirrored."""
    a = math.radians(75)                                   # the tube against the horizontal
    ux, uy = math.cos(a), -math.sin(a)                     # along the tube, toward the sky
    nx, ny = math.sin(a), math.cos(a)                      # across the tube, toward the finder
    for side, title in ((1, "before"), (-1, "after")):
        def X(x): return x if side == 1 else 640 - x
        def rect(x, y, w, h, fill): return el("rect", x=X(x) if side == 1 else X(x + w), y=y, width=w, height=h, fill=fill)
        mx, my, px, py = 160, 195, 118.0, 150.0            # mount head, end of the declination axis
        tx0, ty0 = px - 28 * ux, py - 28 * uy              # camera end of the tube
        fx0, fy0 = px + 55 * ux + 16 * nx, py + 55 * uy + 16 * ny
        f.add(text(X(160), 26, t(title), size=15, fill=TEXT, anchor="middle", weight="bold"),
              rect(148, 205, 24, 65, "#233244"),
              line(X(mx), my, X(220), 235, w=4), rect(208, 227, 24, 18, MUTED),
              line(X(mx), my, X(px), py, w=4), el("circle", cx=X(mx), cy=my, r=14, fill="#2d4258"),
              line(X(tx0), ty0, X(px + 100 * ux), py + 100 * uy, "#cfd8e3", 22),
              rect(round(tx0 - 11), round(ty0 - 8), 22, 16, WARN),
              line(X(fx0), fy0, X(px + 85 * ux + 16 * nx), py + 85 * uy + 16 * ny, ACCENT, 7),
              text(X(fx0 + 14 * nx), fy0 + 14 * ny + 4, t("finder"), size=12, fill=ACCENT, anchor="start" if side == 1 else "end"),
              text(X(tx0 - 16), ty0 + 22, t("camera"), size=12, fill=WARN, anchor="middle"),
              line(X(45), 60, X(45), 110, OK, "2.5", marker_end=f.arrow("G")),
              text(X(45), 128, t("gravity"), size=12, fill=OK, anchor="middle"))
    f.add(line(320, 40, 320, 270, GRID, 1), text(320, 292, t("note"), anchor="middle"))


@figure("series-comparison", 640, 260, {
    "en": {"label": "Series comparison", "flip": "flip", "time": "time", "value": "measured value", "west": "median West ± spread",
           "east": "median East", "d1": "difference ≫ 3 standard errors", "d2": "→ changes at the flip"},
    "de": {"label": "Serienvergleich", "flip": "Flip", "time": "Zeit", "value": "Messwert", "west": "Median West ± Streuung",
           "east": "Median Ost", "d1": "Differenz ≫ 3 Standardfehler", "d2": "→ ändert sich beim Flip"}})
def series_comparison(f, t):
    west, east = 150, 95                                   # medians before and after the flip (y)
    rng = random.Random(7)                                 # a fixed random scatter
    f.add(line(50, 210, 600, 210, marker_end=f.arrow()), text(600, 232, t("time"), anchor="end"),
          line(50, 210, 50, 30, marker_end=f.arrow()), text(56, 40, t("value")),
          line(400, 30, 400, 210, GRID, 2, stroke_dasharray="6 4"), text(400, 26, t("flip"), fill=TEXT, anchor="middle"))
    for i in range(12):
        f.add(el("circle", cx=70 + 27 * i, cy=west + rng.gauss(0, 7), r=4.5, fill=ACCENT))
    for i in range(5):
        f.add(el("circle", cx=425 + 35 * i, cy=east + rng.gauss(0, 7), r=4.5, fill=WARN))
    f.add(line(65, west, 390, west, ACCENT, 2), text(70, 176, t("west"), size=12, fill=ACCENT),
          line(415, east, 580, east, WARN, 2), text(420, 77, t("east"), size=12, fill=WARN),
          line(405, west, 405, east, TEXT, "1.5", marker_end=f.arrow()),
          text(392, 125, t("d1"), size=12, fill=TEXT, anchor="end"), text(392, 141, t("d2"), size=12, fill=TEXT, anchor="end"))


# ---------------------------------------------------------------------------
# Chapter 11: plate-solve distortion
# ---------------------------------------------------------------------------
@figure("sip-distortion", 640, 270, {
    "en": {"label": "SIP distortion", "none": "no distortion", "quad": "quadratic terms (e.g. A₂,₀ > 0)",
           "note": "x′ = x + A₂,₀u² + A₁,₁uv + A₀,₂v²   (y likewise with B)"},
    "de": {"label": "SIP-Verzeichnung", "none": "keine Verzeichnung", "quad": "quadratische Terme (z. B. A₂,₀ > 0)",
           "note": "x′ = x + A₂,₀u² + A₁,₁uv + A₀,₂v²   (y ebenso mit B)"}})
def sip_distortion(f, t):
    """A grid of u, v = −1 … 1, without and with x′ = u + 0.22 u², y′ = v + 0.1 uv."""
    s = 85                                                 # px per unit
    steps = [k / 10 - 1 for k in range(21)]
    for cx, title, a20, b11 in ((160, "none", 0, 0), (480, "quad", 0.22, 0.1)):
        def P(u, v): return cx + s * (u + a20 * u * u), 150 + s * (v + b11 * u * v)
        f.add(text(cx, 30, t(title), size=14, fill=TEXT, anchor="middle", weight="bold"))
        for k in range(6):
            c = -1 + 0.4 * k
            f.add(path([P(u, c) for u in steps], ACCENT, "1.3"), path([P(c, v) for v in steps], ACCENT, "1.3"))
    f.add(text(320, 262, t("note"), anchor="middle"))


# ---------------------------------------------------------------------------
LANGS = {"en": "fig", "de": "de/fig"}


class Labels:
    """t(key) is the label in the language being built; t.num(v, d) a number
    with d decimals and the decimal separator of that language."""

    def __init__(self, labels, lang):
        self.labels, self.lang = labels, lang

    def __call__(self, key):
        return self.labels[self.lang][key]

    def num(self, v, d=1):
        s = f"{v:.{d}f}"
        return s.replace(".", ",") if self.lang == "de" else s


def build(name, w, h, labels, fn, lang):
    f = Figure(name, w, h, labels["en"]["label"])
    fn(f, Labels(labels, lang))
    return f.svg(labels[lang]["label"]) + "\n"


def first_difference(a, b):
    ea, eb = re.split(r"(?=<)", a), re.split(r"(?=<)", b)
    for i, (x, y) in enumerate(zip(ea, eb)):
        if x != y:
            return f"element {i}:\n   file: {x[:300]}\n   new:  {y[:300]}"
    return f"element count {len(ea)} (file) vs {len(eb)} (new)"


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    check = "--check" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    bad = 0
    for name, w, h, labels, fn in FIGURES:
        if only and name not in only:
            continue
        for lang, d in LANGS.items():
            out = os.path.join(d, name + ".svg")
            svg = build(name, w, h, labels, fn, lang)
            if check:
                old = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
                if old != svg:
                    bad += 1
                    print(f"DIFFERS {out}\n{first_difference(old, svg)}")
                else:
                    print(f"same    {out}")
            else:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(svg)
    sys.exit(1 if bad else 0)
