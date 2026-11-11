"""Builds de/koma.html from koma.template.html: exact ray traces on a parabola,
the comatic circles, the Seidel spot diagrams and the table of coma lengths.
The page style is taken from the handbook (_includes/pi-handbook-style.html).

Usage, from pi-scripts/StarAberrationDiagnostics:
   python3 koma-src/gen.py koma-src/koma.template.html ../../_includes/pi-handbook-style.html de/koma.html

Edit the text in koma.template.html, never in de/koma.html."""
import math, re, sys

TEMPLATE, STYLE_SRC, OUT = sys.argv[1:4]

BG, MUTED, TEXT, ACCENT, OK, WARN, GRID = "#0c141d", "#a8b6c8", "#e7edf5", "#64c7ff", "#82e0aa", "#ffab5e", "#2a3b4f"
ZONE = ["#64c7ff", "#8fd6c0", "#d9c27a", "#ffab5e"]          # rho = 0.25, 0.5, 0.75, 1
FONT = 'font-family="Segoe UI, Helvetica Neue, Arial, sans-serif"'


def text(x, y, s, size=13, fill=MUTED, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{s}</text>')


def line(x1, y1, x2, y2, stroke=MUTED, w=1.2, extra=""):
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{w}" {extra}/>')


def svg(vw, vh, label, body, prefix):
    defs = f'''<defs><marker id="{prefix}ARR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>'''
    return (f'<svg viewBox="0 0 {vw} {vh}" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg" {FONT}>'
            f'{defs}<rect width="{vw}" height="{vh}" rx="10" fill="{BG}"/>{"".join(body)}</svg>')


# ---------------------------------------------------------------------------
# Ray trace on a parabolic mirror z = r^2 / (4 f); the sky lies toward +z.
# ---------------------------------------------------------------------------
F = 3.0            # focal length; aperture r = -1 ... 1, i.e. f/1.5 (exaggerated)


def reflect(theta, r1):
    """Ray of field angle theta that crosses the vertex plane z = 0 at r1."""
    dr, dz = -math.sin(theta), -math.cos(theta)
    a, b, c = dr * dr, 2 * r1 * dr - 4 * F * dz, r1 * r1
    if abs(a) < 1e-12:
        t = -c / b
    else:
        disc = math.sqrt(b * b - 4 * a * c)
        t = min(((-b + disc) / (2 * a), (-b - disc) / (2 * a)), key=abs)
    rh, zh = r1 + t * dr, t * dz
    nr, nz = -rh / (2 * F), 1.0
    n = math.hypot(nr, nz); nr, nz = nr / n, nz / n
    dot = dr * nr + dz * nz
    return (rh, zh), (dr, dz), (dr - 2 * dot * nr, dz - 2 * dot * nz)


def at_z(p, d, z):
    t = (z - p[1]) / d[1]
    return p[0] + t * d[0]


def cross(p1, d1, p2, d2):
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    t = ((p2[0] - p1[0]) * (-d2[1]) - (p2[1] - p1[1]) * (-d2[0])) / det
    return p1[0] + t * d1[0], p1[1] + t * d1[1]


S, XV, Y0 = 110.0, 600.0, 145.0          # px per unit, vertex x, axis y


def X(z): return XV - z * S
def Y(r): return Y0 - r * S


def mirror_path():
    pts = [(X(r * r / (4 * F)), Y(r)) for r in [i / 40 - 1 for i in range(81)]]
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def ray_figure(theta, prefix, label, inset):
    b = []
    b.append(line(20, Y0, 620, Y0, GRID, 1, 'stroke-dasharray="6 5"'))
    b.append(text(24, Y0 - 6, "optische Achse", 12))
    b.append(f'<path d="{mirror_path()}" stroke="{TEXT}" stroke-width="5" fill="none"/>')
    b.append(text(626, Y(1) - 12, "Parabolspiegel", 13, TEXT, "end"))
    zstart = F + 2.9
    zend = F - 0.35
    hs = [-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1]
    for h in hs:
        hit, d, d2 = reflect(theta, h)
        col = TEXT if h == 0 else ZONE[int(round(abs(h) * 4)) - 1]
        rs = hit[0] + (zstart - hit[1]) / d[1] * d[0]
        b.append(line(X(zstart), Y(rs), X(hit[1]), Y(hit[0]), MUTED, 0.8, 'opacity="0.45"'))
        re_ = at_z(hit, d2, zend)
        b.append(line(X(hit[1]), Y(hit[0]), X(zend), Y(re_), col, 1.3, 'opacity="0.9"'))
    # focal plane
    b.append(line(X(F), Y(1.05), X(F), Y(-1.05), MUTED, 1, 'stroke-dasharray="3 3"'))
    b.append(text(X(F), Y(-1.05) + 16, "Brennebene", 12, MUTED, "middle"))
    if inset:
        b += inset_zoom(theta, prefix)
    else:
        b.append(f'<circle cx="{X(F):.1f}" cy="{Y0:.1f}" r="4" fill="{OK}"/>')
        b.append(text(X(F) - 12, Y0 + 50, "alle Zonen treffen", 13, OK, "end"))
        b.append(text(X(F) - 12, Y0 + 66, "einen Punkt", 13, OK, "end"))
    b.append(text(30, 24, label, 14, TEXT, "start", "bold"))
    return svg(640, 300, label, b, prefix)


def inset_zoom(theta, prefix):
    b = []
    chief_hit, _, chief_d = reflect(theta, 0)
    rc = at_z(chief_hit, chief_d, F)
    pts = []
    for i, h in enumerate([0.25, 0.5, 0.75, 1.0]):
        p1, _, d1 = reflect(theta, h)
        p2, _, d2 = reflect(theta, -h)
        pts.append((i, cross(p1, d1, p2, d2), (p1, d1), (p2, d2)))
    # window around the meeting points
    rs = [p[1][0] for p in pts] + [rc]
    zs = [p[1][1] for p in pts] + [F]
    rmid, zmid = (max(rs) + min(rs)) / 2, (max(zs) + min(zs)) / 2
    span = max(max(rs) - min(rs), max(zs) - min(zs)) * 1.5
    bx, by, bw, bh = 322, 176, 252, 112
    k = min(bw, bh) / span
    def ix(z): return bx + bw / 2 - (z - zmid) * k
    def iy(r): return by + bh / 2 - (r - rmid) * k
    # marker on the main drawing and connector
    mx, my = X(zmid), Y(rmid)
    b.append(f'<rect x="{mx-7:.1f}" y="{my-7:.1f}" width="14" height="14" fill="none" stroke="{WARN}" stroke-width="1.2"/>')
    b.append(line(mx + 7, my + 7, bx, by, WARN, 1, 'stroke-dasharray="3 3"'))
    b.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" fill="#0f1a26" stroke="{WARN}" stroke-width="1.2"/>')
    b.append(f'<clipPath id="{prefix}CLIP"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6"/></clipPath>')
    g = [f'<g clip-path="url(#{prefix}CLIP)">']
    zl, zr = zmid + span, zmid - span
    for i, (cx_, cz_), (p1, d1), (p2, d2) in pts:
        for p, d in ((p1, d1), (p2, d2)):
            g.append(line(ix(zl), iy(at_z(p, d, zl)), ix(zr), iy(at_z(p, d, zr)), ZONE[i], 1.3))
    g.append(line(ix(zl), iy(at_z(chief_hit, chief_d, zl)), ix(zr), iy(at_z(chief_hit, chief_d, zr)), TEXT, 1.6))
    g.append(line(ix(F), by, ix(F), by + bh, MUTED, 1, 'stroke-dasharray="3 3"'))
    for i, (cx_, cz_), *_ in pts:
        g.append(f'<circle cx="{ix(cz_):.1f}" cy="{iy(cx_):.1f}" r="3.5" fill="{ZONE[i]}" stroke="{BG}" stroke-width="1"/>')
    g.append('</g>')
    b += g
    b.append(text(bx + 8, by + 16, f"Ausschnitt, ca. {k / S:.0f}-fach vergrößert", 11, WARN))
    return b


fig_axis = ray_figure(0.0, "KAX", "Stern auf der Achse", False)
fig_off = ray_figure(math.radians(5), "KOFF", "Stern 5° neben der Achse", True)

# ---------------------------------------------------------------------------
# Pupil zones and comatic circles
# ---------------------------------------------------------------------------
def zones_figure():
    b = []
    pcx, pcy, pr = 150, 175, 105
    b.append(text(pcx, 26, "Öffnung (Spiegel), von vorn", 13, TEXT, "middle", "bold"))
    for i in range(3, -1, -1):
        b.append(f'<circle cx="{pcx}" cy="{pcy}" r="{pr*(i+1)/4:.1f}" fill="{ZONE[i]}" fill-opacity="0.13" stroke="{ZONE[i]}" stroke-width="2"/>')
    b.append(f'<circle cx="{pcx}" cy="{pcy}" r="3" fill="{TEXT}"/>')
    for kk in range(8):
        phi = math.radians(45 * kk)
        x, y = pcx + pr * math.sin(phi), pcy - pr * math.cos(phi)
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{ZONE[3]}" stroke="{BG}" stroke-width="1.5"/>')
        lx, ly = pcx + (pr + 16) * math.sin(phi), pcy - (pr + 16) * math.cos(phi) + 4.5
        b.append(text(lx, ly, str(kk + 1), 13, TEXT, "middle", "bold"))
    b.append(text(pcx, pcy + pr + 42, "Zonen ρ = ¼, ½, ¾, 1 · Punkte 1–8 auf der äußeren Zone", 12, MUTED, "middle"))
    # arrow
    b.append(line(282, 175, 336, 175, MUTED, 2, 'marker-end="url(#KZARR)"'))
    b.append(text(309, 163, "Abbildung", 12, MUTED, "middle"))
    # comatic circles
    ox, oy, c = 470, 282, 62
    b.append(text(ox, 26, "Bild in der Brennebene", 13, TEXT, "middle", "bold"))
    top = oy - 3 * c
    dx = math.tan(math.radians(30)) * 3 * c
    b.append(line(ox, oy, ox - dx, top, GRID, 1.2, 'stroke-dasharray="4 4"'))
    b.append(line(ox, oy, ox + dx, top, GRID, 1.2, 'stroke-dasharray="4 4"'))
    b.append(text(ox - dx - 4, top + 4, "60°-Keil", 12, MUTED, "end"))
    for i in range(4):
        rho2 = ((i + 1) / 4) ** 2
        b.append(f'<circle cx="{ox}" cy="{oy - 2*c*rho2:.1f}" r="{c*rho2:.1f}" fill="none" stroke="{ZONE[i]}" stroke-width="2"/>')
    seen = {}
    for kk in range(8):
        phi = math.radians(45 * kk)
        dr, dt = c * (2 + math.cos(2 * phi)), c * math.sin(2 * phi)
        key = (round(dr), round(dt))
        seen.setdefault(key, []).append(kk + 1)
    for (dr, dt), ks in seen.items():
        x, y = ox + dt, oy - dr
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{ZONE[3]}" stroke="{BG}" stroke-width="1.5"/>')
        ccy = oy - 2 * c
        vx, vy = x - ox, y - ccy
        n = math.hypot(vx, vy) or 1
        lx, ly = x + vx / n * 20, y + vy / n * 20 + 4.5
        if vy > 0.9 * n:                       # bottom point: label beside it
            lx, ly = x - 24, y + 4.5
        b.append(text(lx, ly, "·".join(map(str, ks)), 13, TEXT, "middle", "bold"))
    b.append(f'<circle cx="{ox}" cy="{oy}" r="4" fill="{TEXT}"/>')
    b.append(text(ox + 12, oy + 4, "Kern: Mitte der Öffnung", 12, TEXT))
    b.append(line(612, 270, 612, 90, MUTED, 1.2, 'marker-end="url(#KZARR)"'))
    b.append(text(626, 76, "von der Bildmitte weg", 11, MUTED, "end"))
    return svg(640, 330, "Komakreise", b, "KZ")


fig_zones = zones_figure()

# ---------------------------------------------------------------------------
# The five Seidel aberrations as spot diagrams
# ---------------------------------------------------------------------------
def seidel_figure():
    b = []
    W, cy = 128, 82
    rings = [i / 6 for i in range(1, 7)]
    def spot(cx, f):
        out = []
        for rho in rings:
            m = max(6, int(28 * rho))
            for j in range(m):
                phi = 2 * math.pi * j / m
                dx, dy = f(rho, phi)
                out.append(f'<circle cx="{cx + dx:.1f}" cy="{cy - dy:.1f}" r="1.3" fill="#fff1d6" opacity="0.8"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="none" stroke="{ACCENT}" stroke-width="1"/>')
        return out
    panels = [
        ("sphärische Aberr.", "ρ³", lambda r, p: (34 * r**3 * math.sin(p), 34 * r**3 * math.cos(p))),
        ("Koma", "h·ρ²", lambda r, p: (21 * r * r * math.sin(2 * p), 21 * r * r * (2 + math.cos(2 * p)) - 31)),
        ("Astigmatismus", "h²·ρ", lambda r, p: (12 * r * math.sin(p), 36 * r * math.cos(p))),
        ("Bildfeldwölbung", "h²·ρ", lambda r, p: (30 * r * math.sin(p), 30 * r * math.cos(p))),
    ]
    for i, (name, term, f) in enumerate(panels):
        cx = 16 + W / 2 + i * W
        b += spot(cx, f)
        b.append(text(cx, 150, name, 13, TEXT, "middle", "bold"))
        b.append(text(cx, 168, term, 13, WARN, "middle"))
    # distortion: a sharp point, but the grid is bent
    cx = 16 + W / 2 + 4 * W
    for k in range(-2, 3):
        for horiz in (True, False):
            pts = []
            for t in [j / 10 - 1 for j in range(21)]:
                x, y = (t, k / 2) if horiz else (k / 2, t)
                r2 = x * x + y * y
                x, y = x * (1 + 0.12 * r2), y * (1 + 0.12 * r2)
                pts.append(f"{cx + 34 * x:.1f},{cy - 34 * y:.1f}")
            b.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{MUTED}" stroke-width="1" opacity="0.7"/>')
    b.append(text(cx, 150, "Verzeichnung", 13, TEXT, "middle", "bold"))
    b.append(text(cx, 168, "h³", 13, WARN, "middle"))
    return svg(672, 184, "Die fünf Seidel'schen Fehler", b, "KS")


fig_seidel = seidel_figure()

# ---------------------------------------------------------------------------
# Table: tangential coma length 3h/(16 N^2)
# ---------------------------------------------------------------------------
def de(x, nd=0):
    return f"{x:.{nd}f}".replace(".", ",")


rows = [(5, "5 mm"), (10, "10 mm"), (14.1, "14,1 mm (Ecke APS-C)"), (21.6, "21,6 mm (Ecke Vollformat)")]
Ns = [4, 5, 8]
tbl = ['<div class="tbl"><table>', "<tr><th>Abstand h von der Achse</th>" +
       "".join(f"<th>f/{n}</th>" for n in Ns) + "</tr>"]
for h, lab in rows:
    cells = []
    for n in Ns:
        um = 3 * h / (16 * n * n) * 1000
        cells.append(f"<td>{de(um)} µm · {de(um / 3.76)} px</td>")
    tbl.append(f"<tr><td>{lab}</td>{''.join(cells)}</tr>")
tbl.append("</table></div>")
table = "\n".join(tbl)

style = re.search(r"<style>.*?</style>", open(STYLE_SRC, encoding="utf-8").read(), re.S).group(0)
page = open(TEMPLATE, encoding="utf-8").read()
for k, v in {"STYLE": style, "FIG_AXIS": fig_axis, "FIG_OFF": fig_off, "FIG_ZONES": fig_zones, "FIG_SEIDEL": fig_seidel, "TABLE": table}.items():
    page = page.replace("{{" + k + "}}", v)
assert "{{" not in page
open(OUT, "w", encoding="utf-8").write(page)
