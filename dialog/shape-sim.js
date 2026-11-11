// Simulator of the star shape map (Maps › Shape) for the dialog reference, used
// by _sad_dialog/en/maps-shape.md and _sad_dialog/de/maps-shape.md. The controls
// are in those pages; the texts that change with the values are here, per
// language (the lang attribute of the page).
//
// Model: an astigmatic image with a sagittal and a tangential focal surface,
// z_S,T = κ r² ∓ α r² around the axis. The corrector spacing error bends the
// medial surface (κ, as in the size simulator); α is the residual astigmatism
// of the corrector, which leaves the stars round where the sensor meets the
// medial surface. The sensor is a tilted plane. Each blur adds its second
// moments: the seeing, the defocus ellipse (|z_T - z|/N radially, |z_S - z|/N
// tangentially, variance d²/16 of a uniform disk) and the tracking smear (L²/12
// along its direction). The stars' shapes are evaluated like
// evaluateShapeField() in the script.
(function () {
  "use strict";
  var root = document.getElementById("shape-sim");
  if (!root)
    return;
  var de = document.documentElement.lang === "de";

  var T = de ? {
    cornersHead: "Sensorecken (oben links, oben rechts, unten links, unten rechts)",
    epsRad: "eps_rad", ell: "Elliptizität", tracking: "Nachführung allein: Elliptizität",
    along: "entlang", axis: "Achse der Optik", center: "Bildmitte"
  } : {
    cornersHead: "Sensor corners (top left, top right, bottom left, bottom right)",
    epsRad: "eps_rad", ell: "ellipticity", tracking: "Tracking alone: ellipticity",
    along: "along", axis: "Optical axis", center: "image center"
  };

  function num(x, d) { var s = x.toFixed(d); return de ? s.replace(".", ",") : s; }
  function sgn(x, d) { return (x > 0 ? "+" : x < 0 ? "−" : "") + num(Math.abs(x), d); }

  function rng(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  // --- The statistics of the script (shapeMean, shapeFieldGrid, fitShapeModel, evaluateShapeField) ---
  // A star is { x, y (px), c1, c2 }: its distortion components chi · (cos 2ψ, sin 2ψ).

  var GRID = 11, MIN_STARS = 5, SHAPE_MIN_Z = 2.45, SHAPE_MIN_Z_1D = 2;
  var CORNER_NAMES = ["top left", "top right", "bottom left", "bottom right"];

  function median(v) {
    var s = v.slice().sort(function (a, b) { return a - b; }), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
  }
  function eccFromDistortion(chi) { chi = Math.max(0, Math.min(0.999999, chi)); return Math.sqrt(2 * chi / (1 + chi)); }
  function ellipticityFromEcc(e) { return 1 - Math.sqrt(Math.max(0, 1 - e * e)); }

  function shapeMean(c1, c2) {
    var n = c1.length, m1 = 0, m2 = 0, v = 0, i;
    if (n === 0) return null;
    for (i = 0; i < n; ++i) { m1 += c1[i]; m2 += c2[i]; }
    m1 /= n; m2 /= n;
    for (i = 0; i < n; ++i) v += (c1[i] - m1) * (c1[i] - m1) + (c2[i] - m2) * (c2[i] - m2);
    var se = n > 1 ? Math.sqrt(v / (2 * (n - 1)) / n) : null, chi = Math.hypot(m1, m2);
    return { m1: m1, m2: m2, chi: chi, n: n, se: se, z: se > 0 ? chi / se : null, psi: 0.5 * Math.atan2(m2, m1) };
  }

  function scalarMean(values) {
    var n = values.length;
    if (n === 0) return null;
    var m = values.reduce(function (a, v) { return a + v; }, 0) / n;
    var v = values.reduce(function (a, x) { return a + (x - m) * (x - m); }, 0);
    return { mean: m, se: n > 1 ? Math.sqrt(v / (n - 1) / n) : null, n: n };
  }

  // The stars in buckets of the radius, for the smoothed fields.
  function bucketIndex(stars, radius) {
    var map = {};
    stars.forEach(function (s) {
      var key = Math.floor(s.x / radius) + "," + Math.floor(s.y / radius);
      (map[key] || (map[key] = [])).push(s);
    });
    return function (x, y) {
      var bi = Math.floor(x / radius), bj = Math.floor(y / radius), out = [];
      for (var dj = -1; dj <= 1; ++dj) for (var di = -1; di <= 1; ++di) {
        var list = map[(bi + di) + "," + (bj + dj)];
        if (list) out = out.concat(list);
      }
      return out;
    };
  }

  // orientationFieldAt(): the double-angle mean, Gaussian kernel times e².
  function orientationField(stars, radius) {
    var near = bucketIndex(stars, radius), r2 = radius * radius, sigma2 = 2 * (radius * 0.5) * (radius * 0.5);
    return function (x, y) {
      var sc = 0, ss = 0, sw = 0;
      near(x, y).forEach(function (s) {
        var d2 = (s.x - x) * (s.x - x) + (s.y - y) * (s.y - y);
        if (d2 > r2) return;
        var w = Math.exp(-d2 / sigma2) * s.e * s.e;
        sc += w * Math.cos(2 * s.psi); ss += w * Math.sin(2 * s.psi); sw += w;
      });
      return sw < 1e-6 ? null : { angle: 0.5 * Math.atan2(ss, sc) };
    };
  }

  function traceStreamline(fieldAt, x0, y0, dx0, dy0, stepLen, w, h, gate) {
    var pts = [], x = x0, y = y0, pdx = dx0, pdy = dy0;
    for (var step = 0; step < 200; ++step) {
      if (gate && !gate(x, y)) break;
      var f = fieldAt(x, y);
      if (f === null) break;
      var dx = Math.cos(f.angle), dy = Math.sin(f.angle);
      if (dx * pdx + dy * pdy < 0) { dx = -dx; dy = -dy; }
      x += dx * stepLen; y += dy * stepLen;
      if (x < 0 || x > w || y < 0 || y > h) break;
      pts.push([x, y]); pdx = dx; pdy = dy;
    }
    return pts;
  }

  function traceField(fieldAt, w, h, radius, gate) {
    var spacing = radius * 0.6, stepLen = radius * 0.25, lines = [];
    for (var sy = spacing / 2; sy < h; sy += spacing) for (var sx = spacing / 2; sx < w; sx += spacing) {
      if (gate && !gate(sx, sy)) continue;
      var f0 = fieldAt(sx, sy);
      if (f0 === null) continue;
      var dx0 = Math.cos(f0.angle), dy0 = Math.sin(f0.angle);
      var fwd = traceStreamline(fieldAt, sx, sy, dx0, dy0, stepLen, w, h, gate);
      var bwd = traceStreamline(fieldAt, sx, sy, -dx0, -dy0, stepLen, w, h, gate);
      var full = bwd.reverse().concat([[sx, sy]]).concat(fwd);
      if (full.length > 2) lines.push(full);
    }
    return lines;
  }

  function shapeFieldGrid(stars, w, h, radius, step) {
    var near = bucketIndex(stars, radius), r2 = radius * radius, sigma2 = 2 * (radius * 0.5) * (radius * 0.5);
    var nx = Math.ceil(w / step) + 1, ny = Math.ceil(h / step) + 1, cells = new Array(nx * ny);
    for (var j = 0; j < ny; ++j) for (var i = 0; i < nx; ++i) {
      var x = i * step, y = j * step, sw = 0, sw2 = 0, s1 = 0, s2 = 0, s11 = 0, s22 = 0;
      near(x, y).forEach(function (s) {
        var d2 = (s.x - x) * (s.x - x) + (s.y - y) * (s.y - y);
        if (d2 > r2) return;
        var wk = Math.exp(-d2 / sigma2);
        sw += wk; sw2 += wk * wk; s1 += wk * s.c1; s2 += wk * s.c2; s11 += wk * s.c1 * s.c1; s22 += wk * s.c2 * s.c2;
      });
      if (sw < 1e-6) { cells[j * nx + i] = null; continue; }
      var m1 = s1 / sw, m2 = s2 / sw;
      var variance = Math.max(0, 0.5 * (s11 / sw - m1 * m1 + s22 / sw - m2 * m2)), nEff = sw * sw / sw2;
      var se = nEff > 1 ? Math.sqrt(variance / nEff) : null, chi = Math.hypot(m1, m2);
      cells[j * nx + i] = { m1: m1, m2: m2, chi: chi, se: se, z: se > 0 ? chi / se : 0 };
    }
    return { step: step, nx: nx, ny: ny, cells: cells };
  }

  function shapeFieldAt(grid, x, y) {
    var i = Math.round(x / grid.step), j = Math.round(y / grid.step);
    return i < 0 || j < 0 || i >= grid.nx || j >= grid.ny ? null : grid.cells[j * grid.nx + i];
  }
  function shapeSignificantAt(grid, x, y) { var c = shapeFieldAt(grid, x, y); return c !== null && c.z >= SHAPE_MIN_Z; }

  function shapeDefects(grid) {
    function wrap(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a <= -Math.PI) a += 2 * Math.PI; return a; }
    var defects = [];
    for (var j = 0; j + 1 < grid.ny; ++j) for (var i = 0; i + 1 < grid.nx; ++i) {
      var loop = [grid.cells[j * grid.nx + i], grid.cells[j * grid.nx + i + 1],
                  grid.cells[(j + 1) * grid.nx + i + 1], grid.cells[(j + 1) * grid.nx + i]];
      if (loop.some(function (c) { return c === null; })) continue;
      var a = loop.map(function (c) { return Math.atan2(c.m2, c.m1); }), turn = 0;
      for (var k = 0; k < 4; ++k) turn += wrap(a[(k + 1) % 4] - a[k]);
      var n = Math.round(turn / (2 * Math.PI));
      if (n !== 0) defects.push({ x: (i + 0.5) * grid.step, y: (j + 0.5) * grid.step, index: n / 2 });
    }
    return defects;
  }

  function solveLinearSystem(M, v) {
    var n = v.length, A = M.map(function (row, i) { return row.concat([v[i]]); }), r, c;
    for (var col = 0; col < n; ++col) {
      var maxRow = col, maxVal = Math.abs(A[col][col]);
      for (r = col + 1; r < n; ++r) if (Math.abs(A[r][col]) > maxVal) { maxVal = Math.abs(A[r][col]); maxRow = r; }
      if (maxVal < 1e-12) return null;
      if (maxRow !== col) { var tmp = A[col]; A[col] = A[maxRow]; A[maxRow] = tmp; }
      for (r = col + 1; r < n; ++r) {
        var factor = A[r][col] / A[col][col];
        for (c = col; c <= n; ++c) A[r][c] -= factor * A[col][c];
      }
    }
    var x = new Array(n).fill(0);
    for (r = n - 1; r >= 0; --r) {
      var sum = A[r][n];
      for (c = r + 1; c < n; ++c) sum -= A[r][c] * x[c];
      x[r] = sum / A[r][r];
    }
    return x;
  }

  function solveLeastSquares(rows, zs, mask) {
    var n = rows[0].length, M = [], v = new Array(n).fill(0), count = 0;
    for (var r = 0; r < n; ++r) M.push(new Array(n).fill(0));
    for (var i = 0; i < rows.length; ++i) {
      if (mask && !mask[i]) continue;
      ++count;
      var b = rows[i];
      for (r = 0; r < n; ++r) { v[r] += b[r] * zs[i]; for (var c = 0; c < n; ++c) M[r][c] += b[r] * b[c]; }
    }
    return count < n ? null : solveLinearSystem(M, v);
  }

  function solveLeastSquaresClipped(rows, zs, iterations) {
    var mask = null, coeffs = null;
    for (var it = 0; it < iterations; ++it) {
      coeffs = solveLeastSquares(rows, zs, mask);
      if (coeffs === null) return null;
      var resid = rows.map(function (b, i) {
        var z = 0;
        for (var k = 0; k < b.length; ++k) z += b[k] * coeffs[k];
        return zs[i] - z;
      });
      var sigma = 1.4826 * median(resid.map(Math.abs));
      if (!(sigma > 0)) break;
      mask = resid.map(function (r) { return Math.abs(r) < 3 * sigma; });
    }
    return { coeffs: coeffs, mask: mask };
  }

  function fitShapeModel(stars, w, h) {
    var cx = w / 2, cy = h / 2, rMax = Math.hypot(cx, cy), rows = [], zs = [], i, r, c;
    if (stars.length < 30) return null;
    var uv = stars.map(function (s) { return [(s.x - cx) / rMax, (s.y - cy) / rMax]; });
    for (i = 0; i < stars.length; ++i) {
      var u = uv[i][0], v = uv[i][1];
      rows.push([1, 0, u, v, 0, 0, u * u - v * v]); zs.push(stars[i].c1);
      rows.push([0, 1, 0, 0, u, v, 2 * u * v]); zs.push(stars[i].c2);
    }
    var fit = solveLeastSquaresClipped(rows, zs, 3);
    if (fit === null || fit.coeffs === null) return null;
    var q = fit.coeffs, mask = fit.mask;
    function model(u, v) {
      return [q[0] + q[2] * u + q[3] * v + q[6] * (u * u - v * v), q[1] + q[4] * u + q[5] * v + q[6] * 2 * u * v];
    }
    var n = 7, M = [], ss = 0, count = 0;
    for (r = 0; r < n; ++r) M.push(new Array(n).fill(0));
    for (i = 0; i < rows.length; ++i) {
      if (mask && !mask[i]) continue;
      var b = rows[i], res = zs[i];
      for (r = 0; r < n; ++r) { res -= b[r] * q[r]; for (c = 0; c < n; ++c) M[r][c] += b[r] * b[c]; }
      ss += res * res; ++count;
    }
    var sigma2 = count > n ? ss / (count - n) : 0, se = [], cov = [];
    for (r = 0; r < n; ++r) {
      var e = new Array(n).fill(0);
      e[r] = 1;
      var col = solveLinearSystem(M, e);
      se.push(col ? Math.sqrt(Math.max(0, col[r] * sigma2)) : null);
      cov.push(col ? col.map(function (x) { return x * sigma2; }) : null);
    }
    var cellW = w / GRID, cellH = h / GRID, cells = [];
    for (i = 0; i < GRID * GRID; ++i) cells.push({ r1: [], r2: [], d1: [], d2: [] });
    for (i = 0; i < stars.length; ++i) {
      var cc = Math.min(GRID - 1, Math.max(0, Math.floor(stars[i].x / cellW)));
      var rr = Math.min(GRID - 1, Math.max(0, Math.floor(stars[i].y / cellH)));
      var m = model(uv[i][0], uv[i][1]), cell = cells[rr * GRID + cc];
      cell.r1.push(stars[i].c1 - m[0]); cell.r2.push(stars[i].c2 - m[1]); cell.d1.push(stars[i].c1); cell.d2.push(stars[i].c2);
    }
    var chi2 = 0, used = 0, ssRes = 0, ssTot = 0, bad = [], judged = [];
    cells.forEach(function (cl, k) {
      if (cl.r1.length < MIN_STARS) return;
      var rs = shapeMean(cl.r1, cl.r2), dat = shapeMean(cl.d1, cl.d2);
      if (!(rs.se > 0)) return;
      chi2 += (rs.m1 * rs.m1 + rs.m2 * rs.m2) / (rs.se * rs.se);
      ssRes += rs.m1 * rs.m1 + rs.m2 * rs.m2; ssTot += dat.m1 * dat.m1 + dat.m2 * dat.m2;
      ++used;
      judged.push({ row: Math.floor(k / GRID), col: k % GRID, z: rs.z });
      if (rs.z >= 3) bad.push(k);
    });
    var dof = 2 * used - n, k6 = q[6], kSe = se[6], axis = null, uniform1 = q[0], uniform2 = q[1];
    if (kSe > 0 && Math.abs(k6) >= 3 * kSe) {
      var u0 = -(q[2] + q[5]) / (4 * k6), v0 = (q[3] - q[4]) / (4 * k6);
      axis = { x: cx + u0 * rMax, y: cy + v0 * rMax, offset: Math.hypot(u0, v0) };
      uniform1 -= k6 * (u0 * u0 - v0 * v0); uniform2 -= k6 * 2 * u0 * v0;
    }
    return {
      cov: cov.every(function (x) { return x !== null; }) ? cov : null, model: model, cx: cx, cy: cy, rMax: rMax,
      constant: Math.hypot(uniform1, uniform2), uniform1: uniform1, uniform2: uniform2,
      constantSe: se[0] > 0 ? Math.hypot(se[0], se[1]) / Math.SQRT2 : null,
      k: k6, kSe: kSe, axis: axis, binodal: Math.hypot(0.5 * (q[2] - q[5]), 0.5 * (q[3] + q[4])),
      binodalSe: se[2] > 0 && se[3] > 0 ? 0.5 * Math.hypot(se[2], se[3]) : null,
      chi2dof: dof > 0 ? chi2 / dof : null, cellsUsed: used,
      explained: ssTot > 0 ? Math.max(0, 1 - ssRes / ssTot) : null, unexplained: bad, judgedCells: judged
    };
  }

  function shapeModelSignificantAt(fit, x, y) {
    if (!fit.cov) return true;
    var u = (x - fit.cx) / fit.rMax, v = (y - fit.cy) / fit.rMax, variance = 0;
    [[1, 0, u, v, 0, 0, u * u - v * v], [0, 1, 0, 0, u, v, 2 * u * v]].forEach(function (b) {
      for (var i = 0; i < 7; ++i) for (var j = 0; j < 7; ++j) variance += b[i] * fit.cov[i][j] * b[j];
    });
    var se = Math.sqrt(Math.max(0, variance / 2)), m = fit.model(u, v);
    return se > 0 ? Math.hypot(m[0], m[1]) >= SHAPE_MIN_Z * se : true;
  }

  function shapeModelFieldAt(fit, x, y) {
    var m = fit.model((x - fit.cx) / fit.rMax, (y - fit.cy) / fit.rMax);
    return Math.hypot(m[0], m[1]) < Math.max(0.005, 2 * (fit.constantSe || 0)) ? null : { angle: 0.5 * Math.atan2(m[1], m[0]) };
  }

  // computeTrackingComponent() from the uniform term of the model (the median without a model).
  function trackingComponent(stars, w, h) {
    var fit = fitShapeModel(stars, w, h);
    var t1 = fit ? fit.uniform1 : median(stars.map(function (s) { return s.c1; }));
    var t2 = fit ? fit.uniform2 : median(stars.map(function (s) { return s.c2; }));
    var ecc = eccFromDistortion(Math.hypot(t1, t2));
    return { chi1: t1, chi2: t2, ellipticity: ellipticityFromEcc(ecc), se: fit ? fit.constantSe : null,
             angleDeg: ((0.5 * Math.atan2(t2, t1) * 180 / Math.PI) % 180 + 180) % 180 };
  }

  function evaluateShapeField(stars, w, h, radius) {
    var cellW = w / GRID, cellH = h / GRID, cx = w / 2, cy = h / 2, rMax = Math.hypot(cx, cy), buckets = [], edge = [];
    for (var k = 0; k < GRID * GRID; ++k) buckets.push({ c1: [], c2: [], rad: [] });
    stars.forEach(function (s) {
      var col = Math.min(GRID - 1, Math.max(0, Math.floor(s.x / cellW)));
      var row = Math.min(GRID - 1, Math.max(0, Math.floor(s.y / cellH)));
      var phi2 = 2 * Math.atan2(s.y - cy, s.x - cx), rad = s.c1 * Math.cos(phi2) + s.c2 * Math.sin(phi2);
      var b = buckets[row * GRID + col];
      b.c1.push(s.c1); b.c2.push(s.c2); b.rad.push(rad);
      if (Math.hypot(s.x - cx, s.y - cy) > rMax * 2 / 3) edge.push(rad);
    });
    var cells = buckets.map(function (b) {
      var m = shapeMean(b.c1, b.c2);
      if (m) m.uncertain = m.n < MIN_STARS || m.se === null;
      return m;
    });
    function pool(r0, r1, c0, c1) {
      var a1 = [], a2 = [], rad = [];
      for (var r = r0; r <= r1; ++r) for (var c = c0; c <= c1; ++c) {
        var b = buckets[r * GRID + c];
        a1 = a1.concat(b.c1); a2 = a2.concat(b.c2); rad = rad.concat(b.rad);
      }
      return { mean: shapeMean(a1, a2), rad: scalarMean(rad) };
    }
    var mid = GRID >> 1, kk = Math.max(1, Math.round(GRID / 5)), last = GRID - 1;
    var corners = [pool(0, kk - 1, 0, kk - 1), pool(0, kk - 1, last - kk + 1, last),
                   pool(last - kk + 1, last, 0, kk - 1), pool(last - kk + 1, last, last - kk + 1, last)]
      .map(function (p) { return p.rad; });
    var grid = shapeFieldGrid(stars, w, h, radius, Math.max(8, radius * 0.25));
    var counts = { elongated: 0, round: 0, uncertain: 0 };
    cells.forEach(function (c) {
      if (!c) return;
      if (c.uncertain) ++counts.uncertain; else if (c.z >= SHAPE_MIN_Z) ++counts.elongated; else ++counts.round;
    });
    return { cells: cells, center: pool(mid - 1, mid + 1, mid - 1, mid + 1).mean, corners: corners, edge: scalarMean(edge),
             grid: grid, defects: shapeDefects(grid), model: fitShapeModel(stars, w, h), counts: counts };
  }

  // The lines of the Evaluation box (English, as in the dialog; formatShapeEvaluation()).
  function evaluationLines(ev, tracking, subtracted) {
    function f(v, d) { return v.toFixed(d); }
    function pm(v, se) { return f(v, 3) + (se !== null && se !== undefined ? " ± " + f(se, 3) : ""); }
    function sig(v, se) { return se > 0 ? " (" + f(Math.abs(v) / se, 1) + "σ)" : ""; }
    function deg(rad) { return (Math.round(((rad * 180 / Math.PI) % 180 + 180) % 180) % 180) + "°"; }
    var lines = [];
    lines.push("Tracking: ellipticity " + pm(tracking.ellipticity, tracking.se) + " along " + f(tracking.angleDeg, 0) + "°, " +
      (subtracted ? "subtracted from all stars" : "not subtracted") + ".");
    if (ev.center)
      lines.push("Center (inner 3x3 cells): elongation " + pm(ev.center.chi, ev.center.se) + " along " + deg(ev.center.psi) +
        sig(ev.center.chi, ev.center.se) + ", n=" + ev.center.n + " -> " + (ev.center.z >= SHAPE_MIN_Z ? "elongated" : "round") + ".");
    if (ev.corners.every(function (c) { return c !== null; })) {
      var vals = ev.corners.map(function (c) { return c.mean; });
      lines.push("Corners: eps_rad (+ radial, - tangential), spread " + f(Math.max.apply(null, vals) - Math.min.apply(null, vals), 3) + ".");
      ev.corners.forEach(function (c, i) {
        lines.push("   " + CORNER_NAMES[i].charAt(0).toUpperCase() + CORNER_NAMES[i].slice(1) + ": " + pm(c.mean, c.se) +
          sig(c.mean, c.se) + ", n=" + c.n);
      });
    }
    if (ev.edge) {
      var z = ev.edge.se > 0 ? ev.edge.mean / ev.edge.se : 0;
      lines.push("Edge pattern (r > 2/3): eps_rad " + pm(ev.edge.mean, ev.edge.se) + sig(ev.edge.mean, ev.edge.se) + " -> " +
        (Math.abs(z) < SHAPE_MIN_Z_1D ? "no significant pattern" : ev.edge.mean > 0 ? "radial" : "tangential") + ".");
    }
    var m = ev.model;
    if (m) {
      lines.push("Optics model: explains " + (m.explained !== null ? f(100 * m.explained, 0) + "%" : "n/a") +
        " of the pattern; residual chi2/dof " + (m.chi2dof !== null ? f(m.chi2dof, 2) : "n/a") + " -> " +
        (m.chi2dof === null ? "not judged" : m.chi2dof <= 1.5 ? "physically plausible" :
          m.chi2dof <= 3 ? "some structure the optics do not explain" : "much structure the optics do not explain") + ".");
      lines.push("   Curvature/spacing term: " + pm(m.k, m.kSe) + " at the corner" + sig(m.k, m.kSe) + " -> " +
        (!(m.kSe > 0) || Math.abs(m.k) < SHAPE_MIN_Z_1D * m.kSe ? "none" : m.k > 0 ? "radial" : "tangential"));
      lines.push("   Uniform term: " + pm(m.constant, m.constantSe) + sig(m.constant, m.constantSe));
      lines.push("   Linear astigmatism: " + pm(m.binodal, m.binodalSe) + " at the edge" + sig(m.binodal, m.binodalSe));
      if (m.axis)
        lines.push("   Axis of the pattern: (" + f(m.axis.x, 0) + ", " + f(m.axis.y, 0) + ") px, " + f(100 * m.axis.offset, 0) +
          "% of the half diagonal off the center");
      if (m.unexplained.length > 0)
        lines.push("   Unexplained cells (> 3σ off the model): " + m.unexplained.length + " (about " +
          f(m.cellsUsed * Math.exp(-4.5), 0) + " expected by chance)");
    }
    var pos = ev.defects.filter(function (d) { return d.index > 0; }).length, neg = ev.defects.length - pos;
    lines.push("Singular points: " + ev.defects.length + " (+: " + pos + ", -: " + neg + ") -> " +
      (neg === 0 && pos <= 2 ? "as optics produce (at most two +, no -)" :
        "more than optics produce: noise in the smoothed field - raise the smoothing radius") + ".");
    lines.push("Cells: " + ev.counts.elongated + " elongated, " + ev.counts.round + " round within the noise, " +
      ev.counts.uncertain + " uncertain (significant from " + f(SHAPE_MIN_Z, 2) + "σ).");
    return lines;
  }

  // --- Drawing (canvas pixels; s = canvas px per image px) ---

  function setup(canvas, cssW, cssH) {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
    canvas.style.aspectRatio = cssW + " / " + cssH;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0c141d"; ctx.fillRect(0, 0, cssW, cssH);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    return ctx;
  }

  function eccColor(e, eMax) {
    var t = Math.max(0, Math.min(1, e / eMax));
    return "rgb(" + Math.round(255 * t) + "," + Math.round(64 * (1 - Math.abs(t - 0.5) * 2)) + "," + Math.round(255 * (1 - t)) + ")";
  }

  // The runs of segments for which keep(pts, j) is true, each as one path.
  function lineRuns(ctx, lines, s, keep, color, width) {
    ctx.strokeStyle = color; ctx.lineWidth = width;
    lines.forEach(function (pts) {
      var open = false;
      ctx.beginPath();
      for (var j = 1; j < pts.length; ++j) {
        if (keep(pts, j)) {
          if (!open) { ctx.moveTo(pts[j - 1][0] * s, pts[j - 1][1] * s); open = true; }
          ctx.lineTo(pts[j][0] * s, pts[j][1] * s);
        } else open = false;
      }
      ctx.stroke();
    });
  }

  function haloText(ctx, str, x, y, color) {
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.strokeText(str, x, y);
    ctx.fillStyle = color; ctx.fillText(str, x, y);
  }

  function drawMap(canvas, d) {
    var W = 640, H = Math.round(W * d.h / d.w), s = W / d.w, cw = W / GRID, ch = H / GRID, ev = d.ev, o = d.show;
    var ctx = setup(canvas, W, H), r, c;
    ctx.strokeStyle = "rgba(168,182,200,0.30)"; ctx.lineWidth = 1;
    for (c = 1; c < GRID; ++c) { ctx.beginPath(); ctx.moveTo(c * cw, 0); ctx.lineTo(c * cw, H); ctx.stroke(); }
    for (r = 1; r < GRID; ++r) { ctx.beginPath(); ctx.moveTo(0, r * ch); ctx.lineTo(W, r * ch); ctx.stroke(); }
    var mid = GRID >> 1;
    ctx.strokeStyle = "rgba(100,199,255,0.6)"; ctx.lineWidth = 1.5;
    ctx.strokeRect((mid - 1) * cw, (mid - 1) * ch, 3 * cw, 3 * ch);

    if (o.streamlines) {
      var sig = function (pts, j) {
        return shapeSignificantAt(ev.grid, 0.5 * (pts[j - 1][0] + pts[j][0]), 0.5 * (pts[j - 1][1] + pts[j][1]));
      };
      var weak = function (pts, j) { return !sig(pts, j); };
      lineRuns(ctx, d.lines, s, weak, "rgba(0,0,0,0.56)", 3.5);
      lineRuns(ctx, d.lines, s, weak, "#8c8c8c", 1.5);
      lineRuns(ctx, d.lines, s, sig, "rgba(0,0,0,0.63)", 3.5);
      lineRuns(ctx, d.lines, s, sig, "#ffffff", 1.5);
    }
    if (o.ellipses) {
      var eMax = 0;
      d.stars.forEach(function (st) { eMax = Math.max(eMax, st.e); });
      if (eMax <= 0) eMax = 1;
      ctx.lineWidth = 1.3;
      d.stars.forEach(function (st) {
        var a = st.e * o.scale * 0.03 * W, b = a * Math.sqrt(Math.max(0, 1 - st.e * st.e));
        ctx.strokeStyle = eccColor(st.e, eMax);
        ctx.beginPath(); ctx.ellipse(st.x * s, st.y * s, Math.max(0.5, a), Math.max(0.5, b), st.psi, 0, 2 * Math.PI); ctx.stroke();
        ctx.fillStyle = "#ffffff"; ctx.fillRect(st.x * s - 0.6, st.y * s - 0.6, 1.2, 1.2);
      });
    }
    if (o.model && ev.model) {
      var msig = function (pts, j) {
        return shapeModelSignificantAt(ev.model, 0.5 * (pts[j - 1][0] + pts[j][0]), 0.5 * (pts[j - 1][1] + pts[j][1]));
      };
      var mweak = function (pts, j) { return !msig(pts, j); };
      if (!o.modelSignificantOnly) {
        lineRuns(ctx, d.modelLines, s, mweak, "rgba(0,0,0,0.94)", 5);
        lineRuns(ctx, d.modelLines, s, mweak, "#8a7a9e", 2.5);
      }
      lineRuns(ctx, d.modelLines, s, msig, "rgba(0,0,0,0.94)", 5);
      lineRuns(ctx, d.modelLines, s, msig, "#d08cff", 2.5);
    }
    if (o.match && ev.model) {
      ev.model.judgedCells.forEach(function (b) {
        var x0 = b.col * cw + 2, y0 = b.row * ch + 2;
        ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.lineWidth = 3.5; ctx.strokeRect(x0, y0, cw - 4, ch - 4);
        ctx.strokeStyle = b.z >= 3 ? "#ff4fd8" : "#4cff6a"; ctx.lineWidth = 1.5; ctx.strokeRect(x0, y0, cw - 4, ch - 4);
      });
    }
    if (o.cells) {
      var half = Math.min(cw, ch) * 0.45;
      var chis = ev.cells.filter(function (e) { return e && !e.uncertain && e.z >= SHAPE_MIN_Z; })
        .map(function (e) { return e.chi; }).sort(function (a, b) { return a - b; });
      var ref = Math.max(0.02, chis.length > 0 ? chis[Math.floor(0.9 * (chis.length - 1))] : 0.05);
      for (r = 0; r < GRID; ++r) for (c = 0; c < GRID; ++c) {
        var e = ev.cells[r * GRID + c], ccx = (c + 0.5) * cw, ccy = (r + 0.5) * ch;
        if (!e) continue;
        if (e.uncertain) {
          ctx.fillStyle = "rgba(176,176,176,0.63)"; ctx.beginPath(); ctx.arc(ccx, ccy, 2, 0, 2 * Math.PI); ctx.fill();
          continue;
        }
        var len = Math.min(1.2, e.chi / ref) * half * o.scale, dx = Math.cos(e.psi) * len, dy = Math.sin(e.psi) * len;
        if (e.z >= SHAPE_MIN_Z) {
          var spread = Math.min(Math.PI / 2, e.se / (2 * Math.max(1e-6, e.chi)));
          ctx.fillStyle = "rgba(255,255,255,0.38)";
          [0, Math.PI].forEach(function (side) {
            ctx.beginPath(); ctx.moveTo(ccx, ccy);
            ctx.arc(ccx, ccy, len, e.psi + side - spread, e.psi + side + spread); ctx.closePath(); ctx.fill();
          });
          ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.lineWidth = 4.5;
          ctx.beginPath(); ctx.moveTo(ccx - dx, ccy - dy); ctx.lineTo(ccx + dx, ccy + dy); ctx.stroke();
          ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2.5;
        } else {
          ctx.strokeStyle = "rgba(176,176,176,0.63)"; ctx.lineWidth = 1.2;
        }
        ctx.beginPath(); ctx.moveTo(ccx - dx, ccy - dy); ctx.lineTo(ccx + dx, ccy + dy); ctx.stroke();
      }
    }
    if (o.defects) {
      ctx.font = "bold 12px Segoe UI, Helvetica Neue, Arial, sans-serif"; ctx.textAlign = "left";
      ev.defects.forEach(function (df) {
        var color = df.index > 0 ? "#ffe45c" : "#5ce1ff", x = df.x * s, y = df.y * s;
        ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.stroke();
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.stroke();
        haloText(ctx, (df.index > 0 ? "+" : "−") + (Math.abs(df.index) === 0.5 ? "½" : "1"), x + 10, y - 5, color);
      });
    }
    // The tracking marker (drawTrackingMarker()): full scale = ellipticity 0.3.
    var tr = d.tracking, R = 26, mx = 14 + R, my = H - 34 - R;
    ctx.strokeStyle = "rgba(0,0,0,0.56)"; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(mx, my, R, 0, 2 * Math.PI); ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,0.63)"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(mx, my, R, 0, 2 * Math.PI); ctx.stroke();
    var hl = R * Math.min(1, tr.ellipticity / 0.3), ta = tr.angleDeg * Math.PI / 180, tx = Math.cos(ta) * hl, ty = Math.sin(ta) * hl;
    [[6, "rgba(0,0,0,0.69)"], [3, "#60ff60"]].forEach(function (p) {
      ctx.strokeStyle = p[1]; ctx.lineWidth = p[0];
      ctx.beginPath(); ctx.moveTo(mx - tx, my - ty); ctx.lineTo(mx + tx, my + ty); ctx.stroke();
    });
    ctx.font = "bold 11px Segoe UI, Helvetica Neue, Arial, sans-serif"; ctx.textAlign = "left";
    haloText(ctx, "Tracking: eps " + tr.ellipticity.toFixed(3) + " @ " + tr.angleDeg.toFixed(0) + "°", mx - R, my + R + 14, "#60ff60");
    haloText(ctx, d.subtracted ? "(subtracted from the map)" : "(NOT subtracted - contained in the map)", mx - R, my + R + 27, "#60ff60");
  }

  // --- Simulator ---

  var ids = ["ds", "sens", "ast", "tilt", "tiltdir", "focus", "trk", "trkdir", "ax", "ay", "N", "F", "S", "sensor", "P",
             "stars", "noise", "subtract", "ellipses", "scale", "cells", "streamlines", "radius", "sigonly", "model",
             "modelsig", "match", "defects"];
  var el = {};
  ids.forEach(function (id) { el[id] = document.getElementById("sh-" + id); });
  var seed = 1;

  function val(id) { return +el[id].value; }
  function setText(id, str) { var o = document.getElementById("sh-v" + id); if (o) o.textContent = str; }

  function draw() {
    var ds = val("ds"), sens = val("sens"), ast = val("ast"), tiltDeg = val("tilt"), dir = val("tiltdir"), focus = val("focus");
    var trk = val("trk"), trkDir = val("trkdir"), ax = val("ax"), ay = val("ay");
    var N = val("N"), F = val("F"), seeingArc = val("S"), pitch = val("P"), nStars = val("stars"), noise = val("noise");
    var sz = el.sensor.value.split(",").map(Number), wmm = sz[0], hmm = sz[1];

    setText("ds", sgn(ds, 1) + " mm");
    setText("sens", sens + " µm");
    setText("ast", ast + " µm");
    setText("tilt", num(tiltDeg, 2) + "°");
    setText("tiltdir", dir + "°");
    setText("focus", sgn(focus, 0) + " µm");
    var trkUm = trk / 206265 * F * 1000;
    setText("trk", num(trk, 1) + "″ = " + num(trkUm, 1) + " µm");
    setText("trkdir", trkDir + "°");
    setText("ax", sgn(ax, 1) + " mm");
    setText("ay", sgn(ay, 1) + " mm");
    setText("N", "f/" + num(N, 1));
    setText("F", F + " mm");
    var f0 = seeingArc / 206265 * F * 1000;
    setText("S", num(seeingArc, 1) + "″ = " + num(f0, 1) + " µm");
    setText("P", num(pitch, 2) + " µm");
    setText("stars", nStars);
    setText("noise", num(noise, 3));
    setText("scale", num(val("scale"), 1));
    setText("radius", val("radius") + " %");

    // Geometry in mm (x right, y down, origin in the center), depths in µm.
    var tt = Math.tan(tiltDeg * Math.PI / 180) * 1000, cd = Math.cos(dir * Math.PI / 180), sd = Math.sin(dir * Math.PI / 180);
    var kappa = sens * ds / 225, alpha = -ast / 225;     // per mm², from the values at r = 15 mm
    var s0 = f0 / 2.3548, tc = Math.cos(trkDir * Math.PI / 180), ts = Math.sin(trkDir * Math.PI / 180), tv = trkUm * trkUm / 12;

    // The second moments of a star at (x, y) mm: [xx, yy, xy] in µm².
    function moments(x, y) {
      var rx = x - ax, ry = y - ay, r2 = rx * rx + ry * ry, r = Math.sqrt(r2);
      var ux = r > 0 ? rx / r : 1, uy = r > 0 ? ry / r : 0;
      var zs = tt * (x * cd + y * sd) + focus;
      var dT = ((kappa + alpha) * r2 - zs) / N, dS = ((kappa - alpha) * r2 - zs) / N;
      var vT = dT * dT / 16, vS = dS * dS / 16;
      return [s0 * s0 + vT * ux * ux + vS * uy * uy + tv * tc * tc,
              s0 * s0 + vT * uy * uy + vS * ux * ux + tv * ts * ts,
              (vT - vS) * ux * uy + tv * tc * ts];
    }
    function components(m) { var t = m[0] + m[1]; return [(m[0] - m[1]) / t, 2 * m[2] / t]; }

    var w = wmm * 1000 / pitch, h = hmm * 1000 / pitch;
    var rand = rng(seed), raw = [];
    function gauss() { return Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand()); }
    for (var i = 0; i < nStars; ++i) {
      var u = rand(), v = rand(), c = components(moments((u - 0.5) * wmm, (v - 0.5) * hmm));
      raw.push({ x: u * w, y: v * h, c1: c[0] + noise * gauss(), c2: c[1] + noise * gauss() });
    }
    var tracking = trackingComponent(raw, w, h), subtracted = el.subtract.checked;
    var stars = raw.map(function (st) {
      var c1 = st.c1 - (subtracted ? tracking.chi1 : 0), c2 = st.c2 - (subtracted ? tracking.chi2 : 0);
      return { x: st.x, y: st.y, c1: c1, c2: c2, e: eccFromDistortion(Math.hypot(c1, c2)), psi: 0.5 * Math.atan2(c2, c1) };
    });

    var radius = Math.hypot(w, h) * val("radius") / 100, ev = evaluateShapeField(stars, w, h, radius);
    var show = { ellipses: el.ellipses.checked, scale: val("scale"), cells: el.cells.checked, streamlines: el.streamlines.checked,
                 model: el.model.checked, modelSignificantOnly: el.modelsig.checked, match: el.match.checked, defects: el.defects.checked };
    var lines = show.streamlines ? traceField(orientationField(stars, radius), w, h, radius,
      el.sigonly.checked ? function (x, y) { return shapeSignificantAt(ev.grid, x, y); } : null) : [];
    var modelLines = show.model && ev.model ?
      traceField(function (x, y) { return shapeModelFieldAt(ev.model, x, y); }, w, h, radius, null) : [];
    drawMap(document.getElementById("sh-map"), { w: w, h: h, ev: ev, stars: stars, lines: lines, modelLines: modelLines,
      show: show, tracking: tracking, subtracted: subtracted });
    document.getElementById("sh-eval").textContent = evaluationLines(ev, tracking, subtracted).join("\n");

    // The true values of the model, without noise, for comparison.
    var cx = [-wmm / 2, wmm / 2, -wmm / 2, wmm / 2], cy = [-hmm / 2, -hmm / 2, hmm / 2, hmm / 2];
    var truth = cx.map(function (x, k) {
      var m = moments(x, cy[k]), c = components(m), phi2 = 2 * Math.atan2(cy[k], x);
      var l1 = 0.5 * (m[0] + m[1]) + Math.hypot(0.5 * (m[0] - m[1]), m[2]), l2 = m[0] + m[1] - l1;
      return { rad: c[0] * Math.cos(phi2) + c[1] * Math.sin(phi2), ell: 1 - Math.sqrt(l2 / l1) };
    });
    var trEll = 1 - Math.sqrt(s0 * s0 / (s0 * s0 + tv));
    document.getElementById("sh-truth").innerHTML =
      T.cornersHead + ": " + T.epsRad + " <b>" + truth.map(function (t) { return sgn(t.rad, 3); }).join(" / ") + "</b>, " +
      T.ell + " <b>" + truth.map(function (t) { return num(t.ell, 3); }).join(" / ") + "</b>" +
      "<br>" + T.tracking + " <b>" + num(trEll, 3) + "</b> " + T.along + " <b>" + trkDir + "°</b> · " + T.axis + ": <b>" +
      (ax || ay ? "(" + num(w / 2 + ax * 1000 / pitch, 0) + ", " + num(h / 2 + ay * 1000 / pitch, 0) + ") px" : T.center) + "</b>";
  }

  ids.forEach(function (id) { el[id].addEventListener("input", draw); });
  document.getElementById("sh-reseed").addEventListener("click", function () { ++seed; draw(); });
  draw();
})();
