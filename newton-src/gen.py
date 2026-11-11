"""Builds de/newton.html from newton.template.html: the layout of a Newtonian
with the places where errors arise, the view through a collimation eyepiece
and the table of the thermal focus drift. The page style is taken from the
handbook (_includes/pi-handbook-style.html); the star gallery is computed in the browser.

Usage, from pi-scripts/StarAberrationDiagnostics:
   python3 newton-src/gen.py newton-src/newton.template.html ../../_includes/pi-handbook-style.html de/newton.html

Edit the text in newton.template.html, never in de/newton.html."""
import math, re, sys

TEMPLATE, STYLE_SRC, OUT = sys.argv[1:4]

BG, MUTED, TEXT, ACCENT, OK, WARN, GRID = "#0c141d", "#a8b6c8", "#e7edf5", "#64c7ff", "#82e0aa", "#ffab5e", "#2a3b4f"
FONT = 'font-family="Segoe UI, Helvetica Neue, Arial, sans-serif"'


def text(x, y, s, size=13, fill=MUTED, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{s}</text>')


def line(x1, y1, x2, y2, stroke=MUTED, w=1.2, extra=""):
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{w}" {extra}/>')


def circle(cx, cy, r, fill="none", stroke=MUTED, w=1.2, extra=""):
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{w}" {extra}/>'


def svg(vw, vh, label, body):
    defs = (f'<defs><marker id="NLARR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>')
    return (f'<svg viewBox="0 0 {vw} {vh}" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg" {FONT}>'
            f'{defs}<rect width="{vw}" height="{vh}" rx="10" fill="{BG}"/>{"".join(body)}</svg>')


def badge(x, y, n):
    return circle(x, y, 10, WARN, BG, 1.5) + text(x, y + 4.5, str(n), 13, BG, "middle", "bold")


# ---------------------------------------------------------------------------
# Figure 1: layout of a Newtonian (f/4) with the places where errors arise
# ---------------------------------------------------------------------------
def layout_figure():
    b = []
    ax, xv, rap, f = 180.0, 585.0, 70.0, 560.0           # axis y, mirror vertex x, half aperture, focal length
    xs = 150.0                                           # secondary on the axis
    b.append(f'<rect x="60" y="100" width="545" height="160" rx="4" fill="#111c28" stroke="{GRID}" stroke-width="2"/>')
    b.append(line(60, ax, 605, ax, GRID, 1, 'stroke-dasharray="6 5"'))
    # spider (side view) and secondary
    b.append(line(xs + 6, 101, xs + 6, 259, MUTED, 1.5, 'opacity="0.6"'))
    # primary mirror with cell and clips
    pts = [(xv - (r * r) / (4 * f), ax - r) for r in [i - rap - 4 for i in range(int(2 * rap) + 9)]]
    b.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f'" stroke="{TEXT}" stroke-width="5" fill="none"/>')
    b.append(f'<rect x="{xv + 4}" y="{ax - rap - 8}" width="10" height="{2 * rap + 16}" fill="{GRID}"/>')
    for y in (ax - rap - 6, ax + rap + 2):
        b.append(f'<rect x="{xv - 7}" y="{y:.1f}" width="9" height="4" fill="{MUTED}"/>')
    # rays: sky -> primary -> secondary -> focus
    yf = ax - (f - (xv - xs))                            # focus height above the axis after the 45° flat
    hits = []
    for r in (-rap, rap):
        xm, ym = xv - r * r / (4 * f), ax + r
        dx, dy = xv - f - xm, ax - ym
        t = ((ym - ax) * dx - (xm - xs) * dy) / (dx - dy)
        hits.append((xs + t, ax + t))
    for r in (-62, -30, 30, 62):
        xm = xv - r * r / (4 * f)
        ym = ax + r
        b.append(line(20, ym, xm, ym, MUTED, 1, 'opacity="0.45"'))
        # reflected ray heads to the (unfolded) focus at (xv - f, ax)
        fx, fy = xv - f, ax
        dx, dy = fx - xm, fy - ym
        # intersect with the flat: points (xs + t, ax + t)
        t = ((ym - ax) * dx - (xm - xs) * dy) / (dx - dy)
        px, py = xs + t, ax + t
        b.append(line(xm, ym, px, py, ACCENT, 1.3))
        # after the flat the direction (dx, dy) becomes (dy, dx)
        k = (yf - py) / dx
        b.append(line(px, py, px + k * dy, yf, ACCENT, 1.3))
    b.append(line(xs - 24, ax - 24, xs + 24, ax + 24, TEXT, 5))
    # focuser, corrector, camera
    b.append(f'<rect x="{xs - 30}" y="72" width="60" height="28" fill="#1c2a3a" stroke="{MUTED}" stroke-width="1.2"/>')
    b.append(f'<rect x="{xs - 34}" y="62" width="68" height="10" rx="2" fill="#2a4560" stroke="{ACCENT}" stroke-width="1.2"/>')
    b.append(f'<rect x="{xs - 40}" y="16" width="80" height="46" rx="4" fill="#1c2a3a" stroke="{MUTED}" stroke-width="1.2"/>')
    b.append(line(xs - 22, yf, xs + 22, yf, WARN, 3))
    b.append(text(xs + 48, 30, "Kamera", 12))
    b.append(text(xs + 48, 46, "Sensor", 12, WARN))
    b.append(text(xs + 48, 71, "Korrektor", 12, ACCENT))
    b.append(text(xs + 48, 92, "Okularauszug", 12))
    b.append(text(xv - 2, 92, "Hauptspiegel", 12, TEXT, "end"))
    b.append(text(26, ax - 8, "Licht", 12))
    b.append(text(xs - 20, 278, "Spinne", 12, MUTED, "end"))
    b.append(text(xs - 34, ax + 4, "Fang-", 12, TEXT, "end"))
    b.append(text(xs - 34, ax + 18, "spiegel", 12, TEXT, "end"))
    b.append(text(380, 278, "Tubus", 12, MUTED, "middle"))
    b += offset_inset(xs, ax, hits)
    for x, y, n in ((xv - 108, 87, 1), (xs - 24, ax - 34, 2), (xs + 6, 278, 3), (xs + 138, 87, 4), (xs + 116, 66, 5), (xs + 100, 38, 6), (420, 274, 7)):
        b.append(badge(x, y, n))
    return svg(640, 432, "Aufbau eines Newton-Teleskops", b)


def offset_inset(xs, ax, hits):
    """Magnified view of the secondary: the light cone hits it off its axis point."""
    b = []
    (x1, y1), (x2, y2) = hits
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    k = 12.0
    bx, by, bw, bh = 60, 296, 320, 128
    cx, cy = bx + 130, by + bh / 2                     # the axis point A sits here
    def X(x): return cx + (x - xs) * k
    def Y(y): return cy + (y - ax) * k
    b.append(f'<rect x="{xs - 8}" y="{ax - 8}" width="16" height="16" fill="none" stroke="{WARN}" stroke-width="1.2"/>')
    b.append(line(xs + 8, ax + 8, bx + 150, by, WARN, 1, 'stroke-dasharray="3 3"'))
    b.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" fill="#0f1a26" stroke="{WARN}" stroke-width="1.2"/>')
    b.append(f'<clipPath id="NLCLIP"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6"/></clipPath>')
    g = ['<g clip-path="url(#NLCLIP)">']
    g.append(line(bx, cy, bx + bw, cy, GRID, 1.2, 'stroke-dasharray="6 5"'))
    g.append(line(cx, by, cx, by + bh, GRID, 1.2, 'stroke-dasharray="6 5"'))
    g.append(line(X(xs - 30), Y(ax - 30), X(xs + 30), Y(ax + 30), TEXT, 5))
    g.append(line(X(x1), Y(y1), X(x2), Y(y2), WARN, 5))
    g.append('</g>')
    b += g
    b.append(circle(cx, cy, 4.5, TEXT, BG, 1.5))
    b.append(circle(X(mx), Y(my), 4.5, WARN, BG, 1.5))
    b.append(line(cx, cy + 40, X(mx), cy + 40, MUTED, 1.2, 'marker-end="url(#NLARR)"'))
    b.append(line(cx - 20, cy, cx - 20, Y(my), MUTED, 1.2, 'marker-end="url(#NLARR)"'))
    b.append(text(bx + 8, by + 16, f"Fangspiegel, ca. {k:.0f}-fach vergrößert", 11, WARN))
    b.append(text(cx + 8, cy - 8, "A: Achse", 11, TEXT))
    b.append(text(X(mx) + 10, Y(my) + 4, "M: Mitte des Lichtkegels", 11, WARN))
    b.append(text(bx + bw - 6, by + bh - 8, "optische Achse", 10, MUTED, "end"))
    b.append(text(cx - 26, by + bh - 8, "Achse des Auszugs", 10, MUTED, "end"))
    # explanation beside the inset
    tx = bx + bw + 16
    b.append(text(tx, by + 26, "Fangspiegeloffset:", 12, TEXT, "start", "bold"))
    b.append(text(tx, by + 44, "Der Lichtkegel trifft den", 12))
    b.append(text(tx, by + 60, "45°-Spiegel unsymmetrisch;", 12))
    b.append(text(tx, by + 76, "M liegt vom Auszug weg und", 12))
    b.append(text(tx, by + 92, "zum Hauptspiegel hin versetzt,", 12))
    b.append(text(tx, by + 108, "je etwa m / (4 N).", 12))
    return b


# ---------------------------------------------------------------------------
# Figure 2: view through a Cheshire eyepiece
# ---------------------------------------------------------------------------
def cheshire_panel(cx, cy, title, sec=(0, 0), prim=(0, 0), mark=(0, 0)):
    b = []
    b.append(circle(cx, cy, 82, "#0f1a26", MUTED, 1.5))                                    # drawtube edge
    sx, sy = cx + sec[0], cy + sec[1]
    b.append(circle(sx, sy, 64, "#1b2836", "#5a6b80", 1.5))                                 # secondary outline
    px, py = sx + prim[0], sy + prim[1]
    b.append(circle(px, py, 52, "#22384d", ACCENT, 1.5))                                    # primary reflection
    for a in (90, 210, 330):                                                                # mirror clips
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        b.append(f'<rect x="{px + 50 * ca - 4:.1f}" y="{py - 50 * sa - 4:.1f}" width="8" height="8" fill="{MUTED}"/>')
    mx, my = px + mark[0], py + mark[1]
    b.append(circle(mx, my, 17, BG, "#5a6b80", 1))                                          # secondary seen in the primary
    for a in (0, 90, 180, 270):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        b.append(line(mx + 17 * ca, my + 17 * sa, mx + 40 * ca, my + 40 * sa, "#5a6b80", 1.2))
    b.append(circle(mx, my, 5, "none", WARN, 2.2))                                          # centre mark
    b.append(line(cx - 82, cy, cx + 82, cy, OK, 1, 'opacity="0.8"'))                        # crosshair
    b.append(line(cx, cy - 82, cx, cy + 82, OK, 1, 'opacity="0.8"'))
    b.append(text(cx, 202, title, 13, TEXT, "middle", "bold"))
    return b


def cheshire_figure():
    b = []
    b += cheshire_panel(110, 100, "justiert")
    b += cheshire_panel(320, 100, "Hauptspiegel verkippt", mark=(13, -9))
    b += cheshire_panel(530, 100, "Fangspiegel verkippt", prim=(-12, 6), mark=(-6, 3))
    legend = [(MUTED, "Auszugsrohr"), ("#5a6b80", "Fangspiegel"), (ACCENT, "Spiegelbild des Hauptspiegels"),
              (WARN, "Mittenmarkierung"), (OK, "Fadenkreuz")]
    x = 26
    for col, name in legend:
        b.append(circle(x, 228, 5, "none", col, 2))
        b.append(text(x + 10, 232, name, 12))
        x += 22 + len(name) * 6.6
    return svg(640, 246, "Blick durch ein Cheshire-Justierokular", b)


# ---------------------------------------------------------------------------
# Table: thermal focus drift of the tube against the critical focus zone
# ---------------------------------------------------------------------------
def de(x, nd=0):
    return f"{x:.{nd}f}".replace(".", ",")


def drift_table():
    lam = 0.55
    rows = []
    for f, n in ((800, 4), (1000, 5), (1200, 6)):
        cfz = 2.44 * lam * n * n
        al, st, cf = 23e-6 * f * 1000, 12e-6 * f * 1000, 1e-6 * f * 1000
        rows.append(f"<tr><td>{f} mm, f/{n}</td><td>± {de(cfz)} µm</td><td>{de(al, 1)} µm</td><td>{de(st, 1)} µm</td><td>≈ {de(cf, 1)} µm</td></tr>")
    return ('<div class="tbl"><table>\n<tr><th>Newton</th><th>kritische Fokuszone ±2,44 λ N²</th>'
            '<th>Aluminium</th><th>Stahl</th><th>Carbon</th></tr>\n<tr><td></td><td></td><td colspan="3">Längenänderung des Tubus pro °C</td></tr>\n'
            + "\n".join(rows) + "\n</table></div>")


style = re.search(r"<style>.*?</style>", open(STYLE_SRC, encoding="utf-8").read(), re.S).group(0)
page = open(TEMPLATE, encoding="utf-8").read()
for k, v in {"STYLE": style, "FIG_LAYOUT": layout_figure(), "FIG_CHESHIRE": cheshire_figure(), "TABLE_DRIFT": drift_table()}.items():
    page = page.replace("{{" + k + "}}", v)
assert "{{" not in page
open(OUT, "w", encoding="utf-8").write(page)
