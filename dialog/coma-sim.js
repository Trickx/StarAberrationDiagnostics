// Simulator of the coma map (Maps › Coma) for the dialog reference, used by
// _sad_dialog/en/maps-coma.md and _sad_dialog/de/maps-coma.md. The controls are
// in those pages; the texts that change with the values are here, per language
// (the lang attribute of the page).
//
// Model: third-order coma as on the coma page (de/koma.html): a star at the
// field position P gets the coma vector c = q · P - D, with q the residual of
// the field coma (1 without corrector, 0 with a matching one, negative when
// over-corrected) and D the constant coma of a decollimation; its length is
// C = |c| / (16 N²). What the script measures of such a star - the third
// moment m3, the centroid offset and the elongation, relative to the core and
// inside the aperture - depends only on C / σ (σ of the seeing); it is traced
// once with rays (table()) and looked up per star. Fit, bootstrap, streamlines
// and arrows follow fitComaField() and drawComaLayers() in the script.
(function () {
  "use strict";
  var root = document.getElementById("coma-sim");
  if (!root)
    return;
  var de = document.documentElement.lang === "de";

  var T = de ? {
    corner: "Ecke oben rechts", comaLen: "Komalänge 3C", m3: "m3", kEdge: "k ohne Dejustage", trueP0: "wahrer komafreier Punkt",
    none: "keiner - überall dieselbe Koma", everywhere: "überall (keine Koma)", trueMark: "wahr",
    q: ["überkorrigiert", "passend korrigiert", "unterkorrigiert", "ohne Korrektor"],
    off: "PSF asymmetry not measured - enable \"Measure PSF asymmetry\"."
  } : {
    corner: "Top right corner", comaLen: "coma length 3C", m3: "m3", kEdge: "k without decollimation", trueP0: "true coma-free point",
    none: "none - the same coma everywhere", everywhere: "everywhere (no coma)", trueMark: "true",
    q: ["over-corrected", "matching corrector", "under-corrected", "no corrector"],
    off: "PSF asymmetry not measured - enable \"Measure PSF asymmetry\"."
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
  function median(v) {
    var s = v.slice().sort(function (a, b) { return a - b; }), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
  }

  // --- One comatic star, measured like measureStarAsymmetry() ---
  // For t = C / σ (coma along +x, lengths in units of σ): the rays binned,
  // the core center found (an iterated Gaussian-weighted centroid, standing
  // in for the PSF fit), the fitted σ from the weighted second moment, then
  // inside the aperture of 5 σ the third moment, the centroid offset and the
  // distortion of the second moments. Returns { m3, cen, chi, sf } per step
  // of t, all along +x (m3, chi dimensionless, cen and sf in units of σ).
  var T_STEP = 0.5, T_MAX = 24, RAYS = 40000;
  var TABLE = (function () {
    var out = [], rand = rng(7);
    for (var t = 0; t <= T_MAX + 1e-9; t += T_STEP) {
      var n = 128, half = Math.max(6, 1.6 * t + 7), b = 2 * half / n, grid = new Float32Array(n * n), i;
      for (i = 0; i < RAYS; ++i) {
        var rho2 = rand(), phi = 2 * Math.PI * rand();
        var g1 = Math.sqrt(-2 * Math.log(1 - rand())), g2 = 2 * Math.PI * rand();
        var x = t * (rho2 * (2 + Math.cos(2 * phi)) - 1) + g1 * Math.cos(g2);
        var y = t * rho2 * Math.sin(2 * phi) + g1 * Math.sin(g2);
        var ix = Math.floor((x + half) / b), iy = Math.floor((y + half) / b);
        if (ix >= 0 && iy >= 0 && ix < n && iy < n) grid[iy * n + ix] += 1;
      }
      var X = function (k) { return -half + (k + 0.5) * b; };
      // The PSF fit, approximated by adaptive moments: center and σ under a
      // Gaussian weight of that same σ, iterated from the peak (for a
      // Gaussian star the weighted variance is then σ²/2 per axis).
      var pk = 0;
      for (i = 1; i < n * n; ++i) if (grid[i] > grid[pk]) pk = i;
      var cx = X(pk % n), cy = X(Math.floor(pk / n)), sf = 1, it, j, k;
      for (it = 0; it < 20; ++it) {
        var sw = 0, sx = 0, sy = 0, s2 = 0;
        for (j = 0; j < n; ++j) for (k = 0; k < n; ++k) {
          var v = grid[j * n + k];
          if (!v) continue;
          var dx = X(k) - cx, dy = X(j) - cy, d2 = dx * dx + dy * dy, wg = v * Math.exp(-d2 / (2 * sf * sf));
          sw += wg; sx += wg * dx; sy += wg * dy; s2 += wg * d2 / 2;
        }
        cx += sx / sw; cy += sy / sw;
        sf = Math.sqrt(2 * s2 / sw);
      }
      var rAp = 4.5 * sf, a0 = 0, ax = 0, a3 = 0, mxx = 0, myy = 0;
      for (j = 0; j < n; ++j) for (k = 0; k < n; ++k) {
        var v3 = grid[j * n + k];
        if (!v3) continue;
        var fx = X(k) - cx, fy = X(j) - cy;
        if (fx * fx + fy * fy > rAp * rAp) continue;
        var rx = fx / sf, ry = fy / sf;
        a0 += v3; ax += v3 * fx; a3 += v3 * (rx * rx + ry * ry) * rx; mxx += v3 * fx * fx; myy += v3 * fy * fy;
      }
      out.push({ m3: a3 / a0, cen: ax / a0, chi: (mxx - myy) / (mxx + myy), sf: sf });
    }
    // The seeing alone has no asymmetry; the rays leave a little noise.
    out[0] = { m3: 0, cen: 0, chi: 0, sf: out[0].sf };
    return out;
  })();

  function lookup(t) {
    var u = Math.min(T_MAX, t) / T_STEP, i = Math.min(TABLE.length - 2, Math.floor(u)), f = u - i, a = TABLE[i], b = TABLE[i + 1];
    return { m3: a.m3 + f * (b.m3 - a.m3), cen: a.cen + f * (b.cen - a.cen), chi: a.chi + f * (b.chi - a.chi), sf: a.sf + f * (b.sf - a.sf) };
  }

  // --- fitComaField() with the bootstrap ---

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

  // Least squares of v = k · X + o (both components), 3 rounds of 3-sigma clipping.
  function fitOnce(pts) {
    var mask = null, q = null;
    for (var it = 0; it < 3; ++it) {
      var M = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], v = [0, 0, 0], count = 0, i;
      for (i = 0; i < pts.length; ++i) {
        var p = pts[i];
        if (mask && !mask[2 * i] && !mask[2 * i + 1]) continue;
        if (!mask || mask[2 * i]) { M[0][0] += p[0] * p[0]; M[0][1] += p[0]; M[1][1] += 1; v[0] += p[0] * p[2]; v[1] += p[2]; ++count; }
        if (!mask || mask[2 * i + 1]) { M[0][0] += p[1] * p[1]; M[0][2] += p[1]; M[2][2] += 1; v[0] += p[1] * p[3]; v[2] += p[3]; ++count; }
      }
      M[1][0] = M[0][1]; M[2][0] = M[0][2];
      if (count < 3) return null;
      q = solveLinearSystem(M, v);
      if (q === null) return null;
      var res = [];
      for (i = 0; i < pts.length; ++i) {
        res.push(pts[i][2] - (q[0] * pts[i][0] + q[1]));
        res.push(pts[i][3] - (q[0] * pts[i][1] + q[2]));
      }
      var sigma = 1.4826 * median(res.map(Math.abs));
      if (!(sigma > 0)) break;
      mask = res.map(function (r) { return Math.abs(r) < 3 * sigma; });
    }
    return Math.abs(q[0]) < 1e-12 ? null : { k: q[0], ox: q[1], oy: q[2] };
  }

  function fitComaField(stars, w, h, useCentroid, rand) {
    var cx = w / 2, cy = h / 2, rMax = Math.hypot(cx, cy), pts = [];
    stars.forEach(function (s) {
      pts.push([(s.x - cx) / rMax, (s.y - cy) / rMax, useCentroid ? s.asymX : s.m3X, useCentroid ? s.asymY : s.m3Y]);
    });
    if (pts.length < 30) return null;
    function fit(sample) {
      var r = fitOnce(sample);
      if (r) { r.x0 = cx - r.ox / r.k * rMax; r.y0 = cy - r.oy / r.k * rMax; }
      return r;
    }
    var main = fit(pts);
    if (main === null) return null;
    var bx = [], by = [], bk = [];
    for (var b = 0; b < 100; ++b) {
      var sample = new Array(pts.length);
      for (var i = 0; i < pts.length; ++i) sample[i] = pts[Math.floor(rand() * pts.length)];
      var r = fit(sample);
      if (r !== null) { bx.push(r.x0); by.push(r.y0); bk.push(r.k); }
    }
    function sd(a) {
      if (a.length < 2) return NaN;
      var m = a.reduce(function (p, q) { return p + q; }, 0) / a.length;
      return Math.sqrt(a.reduce(function (p, q) { return p + (q - m) * (q - m); }, 0) / (a.length - 1));
    }
    function robustSd(a) {
      if (a.length < 2) return NaN;
      var m = median(a);
      return 1.4826 * median(a.map(function (q) { return Math.abs(q - m); }));
    }
    main.n = pts.length; main.rMax = rMax;
    main.kSd = sd(bk); main.x0Sd = robustSd(bx); main.y0Sd = robustSd(by);
    main.significant = Math.abs(main.k) > 3 * main.kSd;
    return main;
  }

  // The console lines of logComaField() (English, as in the script).
  function consoleLines(fit, fitC, w, h, pitch) {
    function f(v, d) { return v.toFixed(d); }
    var lines = ["--- Coma field (PSF asymmetry) ---"];
    if (fit === null) return lines.concat(["Too few stars with a valid asymmetry measurement - coma field skipped."]);
    lines.push("Stars used: " + fit.n + ". Coma strength at the field edge k = " + f(fit.k, 3) + " ± " + f(fit.kSd, 3) +
      " (third moment, dimensionless; positive = flare points AWAY from the coma-free point, negative = toward it, i.e. over-corrected).");
    if (fitC !== null)
      lines.push("Cross-check with the centroid offset: k = " + f(fitC.k, 3) + " ± " + f(fitC.kSd, 3) + " px, coma-free point (" +
        f(fitC.x0, 0) + ", " + f(fitC.y0, 0) + ").");
    if (!fit.significant)
      return lines.concat(["-> No significant field-dependent coma (|k| < 3σ): either well corrected, or the signal is too weak " +
        "for a single frame. The coma-free point is NOT determinable."]);
    var cx = w / 2, cy = h / 2, dist = Math.hypot(fit.x0 - cx, fit.y0 - cy);
    lines.push("Coma-free point: (" + f(fit.x0, 0) + " ± " + f(fit.x0Sd, 0) + ", " + f(fit.y0, 0) + " ± " + f(fit.y0Sd, 0) +
      ") px, image center (" + f(cx, 0) + ", " + f(cy, 0) + ") -> offset " + f(dist, 0) + " px = " + f(100 * dist / fit.rMax, 0) +
      "% of the half diagonal = " + f(dist * pitch / 1000, 2) + " mm on the sensor.");
    return lines;
  }

  // --- Smoothed field and streamlines (comaFieldAt(), generateComaStreamlines()) ---

  function comaField(stars, radius) {
    var map = {}, r2 = radius * radius, sigma2 = 2 * (radius * 0.5) * (radius * 0.5);
    stars.forEach(function (s) {
      var key = Math.floor(s.x / radius) + "," + Math.floor(s.y / radius);
      (map[key] || (map[key] = [])).push(s);
    });
    return function (x, y) {
      var bi = Math.floor(x / radius), bj = Math.floor(y / radius), sx = 0, sy = 0, sw = 0, n = 0;
      for (var dj = -1; dj <= 1; ++dj) for (var di = -1; di <= 1; ++di) {
        var list = map[(bi + di) + "," + (bj + dj)];
        if (!list) continue;
        for (var i = 0; i < list.length; ++i) {
          var s = list[i], d2 = (s.x - x) * (s.x - x) + (s.y - y) * (s.y - y);
          if (d2 > r2) continue;
          var wg = Math.exp(-d2 / sigma2);
          sx += wg * s.m3X; sy += wg * s.m3Y; sw += wg; ++n;
        }
      }
      if (n < 8 || sw < 1e-6) return null;
      var vx = sx / sw, vy = sy / sw;
      return { vx: vx, vy: vy, mag: Math.hypot(vx, vy) };
    };
  }

  function comaStreamlines(fieldAt, w, h, radius, minPercent) {
    var spacing = radius * 0.6, stepLen = radius * 0.2, seeds = [];
    for (var sy = spacing / 2; sy < h; sy += spacing) for (var sx = spacing / 2; sx < w; sx += spacing) {
      var f = fieldAt(sx, sy);
      if (f !== null) seeds.push({ x: sx, y: sy, mag: f.mag });
    }
    if (seeds.length === 0) return [];
    var mags = seeds.map(function (s) { return s.mag; }).sort(function (a, b) { return a - b; });
    var minMag = mags[Math.min(mags.length - 1, Math.floor(0.9 * mags.length))] * minPercent / 100;
    function trace(x, y, dir) {
      var pts = [], pdx = 0, pdy = 0;
      for (var step = 0; step < 200; ++step) {
        var fl = fieldAt(x, y);
        if (fl === null || fl.mag < minMag) break;
        var dx = dir * fl.vx / fl.mag, dy = dir * fl.vy / fl.mag;
        if (step > 0 && dx * pdx + dy * pdy < 0) break;
        x += dx * stepLen; y += dy * stepLen;
        if (x < 0 || x > w || y < 0 || y > h) break;
        pts.push([x, y]); pdx = dx; pdy = dy;
      }
      return pts;
    }
    var lines = [];
    seeds.forEach(function (sd) {
      if (sd.mag < minMag) return;
      var full = trace(sd.x, sd.y, -1).reverse().concat([[sd.x, sd.y]]).concat(trace(sd.x, sd.y, +1));
      if (full.length > 2) lines.push(full);
    });
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

  function arrow(ctx, x0, y0, x1, y1, head, color, width) {
    var ang = Math.atan2(y1 - y0, x1 - x0);
    ctx.strokeStyle = color; ctx.lineWidth = width;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    ctx.moveTo(x1 - head * Math.cos(ang - 0.45), y1 - head * Math.sin(ang - 0.45)); ctx.lineTo(x1, y1);
    ctx.lineTo(x1 - head * Math.cos(ang + 0.45), y1 - head * Math.sin(ang + 0.45));
    ctx.stroke();
  }

  function haloText(ctx, str, x, y, color) {
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.strokeText(str, x, y);
    ctx.fillStyle = color; ctx.fillText(str, x, y);
  }

  function drawMap(canvas, d) {
    var W = 640, H = Math.round(W * d.h / d.w), s = W / d.w, o = d.show;
    var ctx = setup(canvas, W, H);
    ctx.fillStyle = "rgba(231,237,245,0.35)";
    d.stars.forEach(function (st) { ctx.fillRect(st.x * s - 0.75, st.y * s - 0.75, 1.5, 1.5); });
    ctx.font = "bold 12px Segoe UI, Helvetica Neue, Arial, sans-serif"; ctx.textAlign = "left";
    if (!o.measure) {
      haloText(ctx, T.off, 12, 22, "#ffccb2");
      return;
    }
    if (o.streamlines) {
      var spacing = 0.05 * Math.hypot(W, H), head = 6;
      [[4, "rgba(0,0,0,0.63)"], [1.6, "#ffb040"]].forEach(function (pass) {
        ctx.strokeStyle = pass[1]; ctx.lineWidth = pass[0];
        d.lines.forEach(function (pts) {
          ctx.beginPath(); ctx.moveTo(pts[0][0] * s, pts[0][1] * s);
          var length = 0, j;
          for (j = 1; j < pts.length; ++j) {
            ctx.lineTo(pts[j][0] * s, pts[j][1] * s);
            length += Math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]) * s;
          }
          var n = Math.max(1, Math.floor(length / spacing)), next = (length - (n - 1) * spacing) / 2, run = 0;
          for (j = 1; j < pts.length && n > 0; ++j) {
            var dx = (pts[j][0] - pts[j - 1][0]) * s, dy = (pts[j][1] - pts[j - 1][1]) * s, seg = Math.hypot(dx, dy);
            while (n > 0 && seg > 0 && run + seg >= next) {
              var t = (next - run) / seg, x = pts[j - 1][0] * s + t * dx, y = pts[j - 1][1] * s + t * dy, ang = Math.atan2(dy, dx);
              ctx.moveTo(x - head * Math.cos(ang - 0.45), y - head * Math.sin(ang - 0.45)); ctx.lineTo(x, y);
              ctx.lineTo(x - head * Math.cos(ang + 0.45), y - head * Math.sin(ang + 0.45));
              next += spacing; --n;
            }
            run += seg;
          }
          ctx.stroke();
        });
      });
    }
    if (o.starArrows) {
      // drawStarAsymmetryArrows(): noise from the inner field, color by alignment with the ellipse axis or by strength
      var cx = d.w / 2, cy = d.h / 2, rMax = Math.hypot(cx, cy), inner = [], mags = [];
      d.stars.forEach(function (st) {
        mags.push(Math.hypot(st.m3X, st.m3Y));
        if (Math.hypot(st.x - cx, st.y - cy) < 0.25 * rMax) inner.push(st.m3X, st.m3Y);
      });
      var noise = inner.length >= 20 ? 1.4826 * median(inner.map(Math.abs)) : 0, minMag = o.minSigma * noise;
      var sorted = mags.slice().sort(function (a, b) { return a - b; }), magMax = sorted[Math.floor(0.95 * (sorted.length - 1))] || 1;
      d.stars.forEach(function (st) {
        var mag = Math.hypot(st.m3X, st.m3Y);
        if (mag < minMag || mag <= 0) return;
        var len = Math.min(40, mag * 12 * o.scale), ux = st.m3X / mag, uy = st.m3Y / mag, col;
        if (o.byStrength) {
          var tt = Math.min(1, mag / magMax);
          col = "rgb(" + Math.round(255 * tt) + "," + Math.round(64 * (1 - Math.abs(tt - 0.5) * 2)) + "," + Math.round(255 * (1 - tt)) + ")";
        } else if (st.ell < 0.05) {
          col = "#a0a0a0";
        } else {
          var al = Math.abs(Math.cos(Math.atan2(uy, ux) - st.psi));
          col = "rgb(" + Math.round(255 * Math.min(1, 2 * (1 - al))) + "," + Math.round(255 * Math.min(1, 2 * al)) + ",48)";
        }
        arrow(ctx, st.x * s, st.y * s, st.x * s + ux * len, st.y * s + uy * len, Math.max(3, len * 0.3), col, 1.3);
      });
    }
    if (o.arrows) {
      // drawComaOverlay(): the median of each cell of a 9 x 6 grid, at least 5 stars
      var NX = 9, NY = 6, cw = W / NX, ch = H / NY, cells = [], i, j;
      for (i = 0; i < NX * NY; ++i) cells.push({ vx: [], vy: [] });
      d.stars.forEach(function (st) {
        var ci = Math.min(NX - 1, Math.floor(st.x * s / cw)), cj = Math.min(NY - 1, Math.floor(st.y * s / ch));
        cells[cj * NX + ci].vx.push(st.m3X); cells[cj * NX + ci].vy.push(st.m3Y);
      });
      var sc = Math.min(cw, ch) * 0.45 * o.scale;
      for (j = 0; j < NY; ++j) for (i = 0; i < NX; ++i) {
        var c = cells[j * NX + i];
        if (c.vx.length < 5) continue;
        var x0 = (i + 0.5) * cw, y0 = (j + 0.5) * ch, x1 = x0 + median(c.vx) * sc, y1 = y0 + median(c.vy) * sc;
        var hd = Math.max(5, Math.hypot(x1 - x0, y1 - y0) * 0.3);
        arrow(ctx, x0, y0, x1, y1, hd, "rgba(0,0,0,0.69)", 5);
        arrow(ctx, x0, y0, x1, y1, hd, "#ffa020", 2.2);
      }
    }
    // the true coma-free point of the model, small and cyan
    if (d.trueP0) {
      var tx = d.trueP0[0] * s, ty = d.trueP0[1] * s;
      if (tx > -20 && tx < W + 20 && ty > -20 && ty < H + 20) {
        ctx.strokeStyle = "#5ce1ff"; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.arc(tx, ty, 6, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
        haloText(ctx, T.trueMark, tx + 9, ty + 16, "#5ce1ff");
      }
    }
    var fit = d.fit;
    if (fit !== null && fit.significant && fit.x0 > -d.w && fit.x0 < 2 * d.w && fit.y0 > -d.h && fit.y0 < 2 * d.h) {
      var px = fit.x0 * s, py = fit.y0 * s, arm = 14;
      var rX = Math.max(4, (isFinite(fit.x0Sd) ? fit.x0Sd : 10) * s), rY = Math.max(4, (isFinite(fit.y0Sd) ? fit.y0Sd : 10) * s);
      [[5, "rgba(0,0,0,0.69)"], [2, "#ff40ff"]].forEach(function (pass) {
        ctx.strokeStyle = pass[1]; ctx.lineWidth = pass[0];
        ctx.beginPath(); ctx.ellipse(px, py, rX, rY, 0, 0, 2 * Math.PI);
        ctx.moveTo(px - arm, py); ctx.lineTo(px + arm, py); ctx.moveTo(px, py - arm); ctx.lineTo(px, py + arm);
        ctx.stroke();
      });
      var label = "Coma-free point", lw = ctx.measureText(label).width;
      haloText(ctx, label, Math.max(12, Math.min(px + arm + 6, W - 12 - lw)), Math.max(24, Math.min(py - arm, H - 12)), "#ff40ff");
    }
  }

  // --- Simulator ---

  var ids = ["corr", "dx", "dy", "N", "F", "S", "sensor", "P", "stars", "noise", "measure", "streamlines", "radius", "min",
             "arrows", "stararrows", "minsigma", "bystrength", "scale"];
  var el = {};
  ids.forEach(function (id) { el[id] = document.getElementById("co-" + id); });
  var seed = 1;

  function val(id) { return +el[id].value; }
  function setText(id, str) { var o = document.getElementById("co-v" + id); if (o) o.textContent = str; }

  function draw() {
    var q = val("corr"), Dx = val("dx"), Dy = val("dy"), N = val("N"), F = val("F"), seeingArc = val("S"), pitch = val("P");
    var nStars = val("stars"), noise = val("noise"), sz = el.sensor.value.split(",").map(Number), wmm = sz[0], hmm = sz[1];

    setText("corr", num(q, 2) + " · " + (q >= 0.995 ? T.q[3] : q > 0.02 ? T.q[2] : q < -0.02 ? T.q[0] : T.q[1]));
    setText("dx", sgn(Dx, 1) + " mm");
    setText("dy", sgn(Dy, 1) + " mm");
    setText("N", "f/" + num(N, 1));
    setText("F", F + " mm");
    var f0 = seeingArc / 206265 * F * 1000, sigma = f0 / 2.3548;
    setText("S", num(seeingArc, 1) + "″ = " + num(f0, 1) + " µm");
    setText("P", num(pitch, 2) + " µm");
    setText("stars", nStars);
    setText("noise", num(noise, 2));
    setText("radius", val("radius") + " %");
    setText("min", val("min") + " %");
    setText("minsigma", num(val("minsigma"), 1));
    setText("scale", num(val("scale"), 1));

    // A star at (x, y) mm (y down): its coma vector c = q · P - D (mm) and what the script measures of it.
    function measure(x, y) {
      var cxm = q * x - Dx, cym = q * y - Dy, h = Math.hypot(cxm, cym), C = h / (16 * N * N) * 1000;
      var m = lookup(C / sigma), ux = h > 0 ? cxm / h : 1, uy = h > 0 ? cym / h : 0;
      return { m3: m.m3, cen: m.cen * sigma / pitch, chi: m.chi, ux: ux, uy: uy, C: C };
    }

    var w = wmm * 1000 / pitch, h = hmm * 1000 / pitch, rand = rng(seed), stars = [];
    function gauss() { return Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand()); }
    for (var i = 0; i < nStars; ++i) {
      var u = rand(), v = rand(), m = measure((u - 0.5) * wmm, (v - 0.5) * hmm);
      // the elongation lies along the flare (chi > 0) or across it; noise on all of it
      var c2 = 2 * Math.atan2(m.uy, m.ux), e1 = m.chi * Math.cos(c2) + 0.03 * gauss(), e2 = m.chi * Math.sin(c2) + 0.03 * gauss();
      var chi = Math.hypot(e1, e2), ecc = Math.sqrt(2 * Math.min(0.99, chi) / (1 + Math.min(0.99, chi)));
      stars.push({ x: u * w, y: v * h, m3X: m.m3 * m.ux + noise * gauss(), m3Y: m.m3 * m.uy + noise * gauss(),
                   asymX: m.cen * m.ux + 0.3 * noise * sigma / pitch * gauss(), asymY: m.cen * m.uy + 0.3 * noise * sigma / pitch * gauss(),
                   psi: 0.5 * Math.atan2(e2, e1), ell: 1 - Math.sqrt(1 - ecc * ecc) });
    }
    var brand = rng(1000 + seed), measureOn = el.measure.checked;
    var fit = measureOn ? fitComaField(stars, w, h, false, brand) : null;
    var fitC = measureOn ? fitComaField(stars, w, h, true, brand) : null;
    var radius = Math.hypot(w, h) * val("radius") / 100;
    var lines = measureOn && el.streamlines.checked ? comaStreamlines(comaField(stars, radius), w, h, radius, val("min")) : [];
    var trueP0 = Math.abs(q) > 1e-6 ? [w / 2 + Dx / q * 1000 / pitch, h / 2 + Dy / q * 1000 / pitch] : null;
    drawMap(document.getElementById("co-map"), { w: w, h: h, stars: stars, fit: fit, lines: lines, trueP0: trueP0,
      show: { measure: measureOn, streamlines: el.streamlines.checked, arrows: el.arrows.checked, starArrows: el.stararrows.checked,
              minSigma: val("minsigma"), byStrength: el.bystrength.checked, scale: val("scale") } });
    document.getElementById("co-eval").textContent = measureOn ? consoleLines(fit, fitC, w, h, pitch).join("\n") : T.off;

    // The true values of the model, for comparison.
    var mc = measure(wmm / 2, -hmm / 2), L = 3 * mc.C;
    var edge = lookup(q === 0 ? 0 : Math.abs(q) * Math.hypot(wmm, hmm) / 2 / (16 * N * N) * 1000 / sigma).m3;
    document.getElementById("co-truth").innerHTML =
      T.corner + ": " + T.comaLen + " <b>" + num(L, 0) + " µm = " + num(L / pitch, 1) + " px = " + num(L / 1000 / F * 206265, 1) +
      "″</b>, " + T.m3 + " <b>" + num(mc.m3, 3) + "</b> · k " + (de ? "ohne Dejustage" : "without decollimation") + " <b>" +
      (q < 0 ? "−" : "") + num(edge, 3) + "</b>" +
      "<br>" + T.trueP0 + ": <b>" + (trueP0 ? "(" + num(trueP0[0], 0) + ", " + num(trueP0[1], 0) + ") px" :
        Dx || Dy ? T.none : T.everywhere) + "</b>";
  }

  ids.forEach(function (id) { el[id].addEventListener("input", draw); });
  document.getElementById("co-reseed").addEventListener("click", function () { ++seed; draw(); });
  draw();
})();
