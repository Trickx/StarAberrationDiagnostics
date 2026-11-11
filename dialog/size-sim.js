// Simulator of the star size map (Maps › Size) for the dialog reference, used
// by _sad_dialog/en/maps-size.md and _sad_dialog/de/maps-size.md. The controls
// are in those pages; the texts that change with the values are here, per
// language (the lang attribute of the page).
//
// Model: the stars are imaged on a curved shell whose sag grows with r² and
// with the corrector spacing error; the sensor is a tilted plane. A star's
// defocus Δz is the distance between both, its blur d = |Δz|/N adds to the
// seeing in quadrature (FWHM² = FWHM₀² + d², as in chapter 7 of the handbook).
// The map is then evaluated like evaluateFwhmGrid() in the script.
(function () {
  "use strict";
  var root = document.getElementById("size-sim");
  if (!root)
    return;
  var de = document.documentElement.lang === "de";

  var T = de ? {
    corners: ["oben links", "oben rechts", "unten links", "unten rechts"],
    seeing: "Seeing", trueCenter: "wahre FWHM in der Mitte",
    cornersHead: "Sensorecken (oben links, oben rechts, unten links, unten rechts)",
    defocus: "Defokus Δz", trueRatio: "wahres Verhältnis",
    shell: "Bildschale in der Ecke", tiltEdge: "Sensor am Ende der halben Diagonale versetzt um",
    diagA: "Diagonale oben links → unten rechts", diagB: "oben rechts → unten links",
    shellLabel: "Bildschale", sensorLabel: "Sensor", away: "↑ weiter von der Optik weg",
    n: "Sterne", deg: "°"
  } : {
    corners: ["top left", "top right", "bottom left", "bottom right"],
    seeing: "Seeing", trueCenter: "true FWHM in the center",
    cornersHead: "Sensor corners (top left, top right, bottom left, bottom right)",
    defocus: "defocus Δz", trueRatio: "true ratio",
    shell: "Image shell in the corner", tiltEdge: "Sensor offset at the end of the half diagonal by",
    diagA: "Diagonal top left → bottom right", diagB: "top right → bottom left",
    shellLabel: "Image shell", sensorLabel: "Sensor", away: "↑ farther from the optics",
    n: "stars", deg: "°"
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

  // --- The statistics of the script (trimmedMean25, robustSpread, evaluateFwhmGrid) ---

  function median(v) {
    var s = v.slice().sort(function (a, b) { return a - b; }), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
  }

  function trimmedMean25(v) {
    var s = v.slice().sort(function (a, b) { return a - b; }), k = Math.floor(s.length * 0.25);
    var t = s.slice(k, s.length - k);
    if (t.length === 0) t = s;
    return t.reduce(function (a, b) { return a + b; }, 0) / t.length;
  }

  function cellStats(v) {
    var n = v.length;
    if (n === 0) return { value: null, n: 0, se: null };
    var m = median(v);
    var spread = 1.4826 * median(v.map(function (x) { return Math.abs(x - m); }));
    return { value: trimmedMean25(v), n: n, se: n < 2 ? null : spread / Math.sqrt(n) };
  }

  var GRID = 11, MIN_STARS = 5, MAX_REL_SE = 0.10, MIN_Z = 2;
  var RATIO_LIMITS = [1.10, 1.25, 1.50], RATIO_NAMES = ["good", "slight", "clear", "strong"];
  var TILT_LIMITS = [0.05, 0.10], TILT_NAMES = ["unremarkable", "slight", "worth correcting"];
  var CORNER_NAMES = ["top left", "top right", "bottom left", "bottom right"];

  function ratioClass(r) {
    var k = 0;
    while (k < RATIO_LIMITS.length && r > RATIO_LIMITS[k]) ++k;
    return k;
  }

  // stars: [{ c, r, fwhm }] with their cell column and row.
  function evaluate(stars) {
    var buckets = [], r, c;
    for (r = 0; r < GRID; ++r) { buckets.push([]); for (c = 0; c < GRID; ++c) buckets[r].push([]); }
    stars.forEach(function (s) { buckets[s.r][s.c].push(s.fwhm); });
    function pool(r0, r1, c0, c1) {
      var out = [];
      for (var i = r0; i <= r1; ++i) for (var j = c0; j <= c1; ++j) out = out.concat(buckets[i][j]);
      return cellStats(out);
    }
    var mid = GRID >> 1, center = pool(mid - 1, mid + 1, mid - 1, mid + 1);
    if (center.value === null || center.se === null) center = null;

    var counts = { good: 0, slight: 0, clear: 0, strong: 0, notSignificant: 0, uncertain: 0 };
    var cells = buckets.map(function (row) {
      return row.map(function (b) {
        var e = cellStats(b);
        e.uncertain = e.n < MIN_STARS || e.se === null || e.se > MAX_REL_SE * e.value;
        e.ratio = null; e.cls = -1;
        if (e.value === null) return e;
        if (center !== null) e.ratio = e.value / center.value;
        if (e.uncertain) { ++counts.uncertain; return e; }
        if (center === null) return e;
        var z = (e.value - center.value) / Math.sqrt(e.se * e.se + center.se * center.se);
        var cls = ratioClass(e.ratio);
        if (cls === 0 || z >= MIN_Z) { e.cls = cls; ++counts[RATIO_NAMES[cls]]; }
        else ++counts.notSignificant;
        return e;
      });
    });

    var corners = null, tilt = null, k = Math.max(1, Math.round(GRID / 5)), last = GRID - 1;
    var cs = [pool(0, k - 1, 0, k - 1), pool(0, k - 1, last - k + 1, last),
              pool(last - k + 1, last, 0, k - 1), pool(last - k + 1, last, last - k + 1, last)];
    if (cs.every(function (s) { return s.value !== null && s.se !== null; })) {
      if (center !== null) {
        var ratios = cs.map(function (s) { return s.value / center.value; });
        corners = { stats: cs, ratios: ratios, ratio: median(ratios), cls: ratioClass(median(ratios)) };
      }
      [[0, 3], [1, 2]].forEach(function (d) {
        var a = cs[d[0]], b = cs[d[1]];
        var rel = Math.abs(a.value - b.value) / (0.5 * (a.value + b.value));
        if (tilt === null || rel > tilt.rel) {
          var soft = a.value >= b.value ? d[0] : d[1];
          tilt = { rel: rel, z: Math.abs(a.value - b.value) / Math.sqrt(a.se * a.se + b.se * b.se),
                   cls: rel < TILT_LIMITS[0] ? 0 : rel < TILT_LIMITS[1] ? 1 : 2,
                   soft: soft, sharp: soft === d[0] ? d[1] : d[0] };
        }
      });
    }

    var edge = [];
    for (r = 0; r < GRID; ++r) for (c = 0; c < GRID; ++c)
      if ((r === 0 || r === last || c === 0 || c === last) && cells[r][c].value !== null && !cells[r][c].uncertain)
        edge.push(cells[r][c].value);
    var edgeSpread = edge.length >= 4 ? (Math.max.apply(null, edge) - Math.min.apply(null, edge)) / median(edge) : null;
    return { center: center, cells: cells, corners: corners, tilt: tilt, edgeSpread: edgeSpread, counts: counts };
  }

  // The lines of the Evaluation box (English, as in the dialog).
  function evaluationLines(ev, scale, unit) {
    function fw(v) { return (v * scale).toFixed(2) + (unit === "px" ? " px" : unit); }
    var lines = [];
    if (ev.center === null) return ["Center: no stars - not judged."];
    lines.push("Center (inner 3x3 cells): x1.00 (" + fw(ev.center.value) + " ± " + fw(ev.center.se) + ", n=" + ev.center.n + ")");
    if (ev.corners !== null) {
      lines.push("Corners: x" + ev.corners.ratio.toFixed(2) + " the center (median) -> " + RATIO_NAMES[ev.corners.cls] + ".");
      ev.corners.ratios.forEach(function (r, i) {
        var s = ev.corners.stats[i];
        lines.push("   " + CORNER_NAMES[i].charAt(0).toUpperCase() + CORNER_NAMES[i].slice(1) + ": x" + r.toFixed(2) +
          " (" + fw(s.value) + " ± " + fw(s.se) + ", n=" + s.n + ")");
      });
    } else lines.push("Corners: too few stars - not judged.");
    if (ev.tilt !== null)
      lines.push("Tilt: " + CORNER_NAMES[ev.tilt.soft] + " " + (100 * ev.tilt.rel).toFixed(0) + "% softer than " +
        CORNER_NAMES[ev.tilt.sharp] + " (" + ev.tilt.z.toFixed(1) + "σ) -> " +
        (ev.tilt.z >= MIN_Z ? TILT_NAMES[ev.tilt.cls] : "not significant") + ".");
    if (ev.edgeSpread !== null)
      lines.push("Edge spread: " + (100 * ev.edgeSpread).toFixed(0) + "% between the softest and the sharpest edge cell.");
    var n = ev.counts;
    lines.push("Cells: " + n.good + " good, " + n.slight + " slight, " + n.clear + " clear, " + n.strong + " strong, " +
      n.notSignificant + " not significant, " + n.uncertain + " uncertain.");
    return lines;
  }

  // The fill of a cell (fwhmFillColor() in the script): green up to ×1.10, yellow, orange, red from ×1.50.
  var STOPS = [[1.00, 70, 215, 95], [1.10, 70, 215, 95], [1.17, 255, 228, 90], [1.25, 255, 160, 40], [1.50, 214, 38, 30]];
  function fillColor(ratio) {
    var r = Math.min(1.5, Math.max(1.0, ratio)), i = 0;
    while (i < STOPS.length - 2 && r > STOPS[i + 1][0]) ++i;
    var a = STOPS[i], b = STOPS[i + 1], u = b[0] > a[0] ? (r - a[0]) / (b[0] - a[0]) : 0;
    function mix(k) { return Math.round(a[k] + (b[k] - a[k]) * u); }
    return "rgba(" + mix(1) + "," + mix(2) + "," + mix(3) + "," + ((150 + 75 * (r - 1) / 0.5) / 255).toFixed(3) + ")";
  }

  // --- Drawing ---

  function setup(canvas, cssW, cssH) {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
    canvas.style.aspectRatio = cssW + " / " + cssH;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0c141d"; ctx.fillRect(0, 0, cssW, cssH);
    return ctx;
  }

  function haloText(ctx, s, x, y, color) {
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.strokeText(s, x, y);
    ctx.fillStyle = color; ctx.fillText(s, x, y);
  }

  function drawMap(canvas, ev, stars, show) {
    var W = 640, H = Math.round(W * show.h / show.w), cw = W / GRID, ch = H / GRID;
    var ctx = setup(canvas, W, H), r, c;
    ctx.fillStyle = "rgba(231,237,245,0.35)";
    stars.forEach(function (s) { ctx.fillRect(s.u * W - 0.75, s.v * H - 0.75, 1.5, 1.5); });
    for (r = 0; r < GRID; ++r) for (c = 0; c < GRID; ++c) {
      var e = ev.cells[r][c], x0 = c * cw, y0 = r * ch;
      if (e.value === null) continue;
      if (e.uncertain) {
        ctx.save(); ctx.beginPath(); ctx.rect(x0, y0, cw, ch); ctx.clip();
        ctx.strokeStyle = "rgba(176,176,176,0.56)"; ctx.lineWidth = 1;
        for (var t = -ch; t < cw + ch; t += 7) { ctx.beginPath(); ctx.moveTo(x0 + t, y0); ctx.lineTo(x0 + t + ch, y0 + ch); ctx.stroke(); }
        ctx.restore();
      } else if (e.cls >= 0 && e.ratio !== null) {
        ctx.fillStyle = fillColor(e.ratio); ctx.fillRect(x0, y0, cw, ch);
      }
    }
    ctx.strokeStyle = "rgba(168,182,200,0.45)"; ctx.lineWidth = 1;
    for (c = 1; c < GRID; ++c) { ctx.beginPath(); ctx.moveTo(c * cw, 0); ctx.lineTo(c * cw, H); ctx.stroke(); }
    for (r = 1; r < GRID; ++r) { ctx.beginPath(); ctx.moveTo(0, r * ch); ctx.lineTo(W, r * ch); ctx.stroke(); }
    var mid = GRID >> 1;
    ctx.strokeStyle = "#64c7ff"; ctx.lineWidth = 2;
    ctx.strokeRect((mid - 1) * cw, (mid - 1) * ch, 3 * cw, 3 * ch);

    var big = Math.max(11, Math.round(Math.min(cw, ch) * 0.24)), small = Math.max(9, Math.round(big * 0.62));
    ctx.textAlign = "center";
    for (r = 0; r < GRID; ++r) for (c = 0; c < GRID; ++c) {
      var f = ev.cells[r][c];
      if (f.value === null) continue;
      var lines = [];
      if (show.mode === "px") lines.push(f.value.toFixed(2) + " px");
      else if (show.mode === "arcsec") lines.push((f.value * show.arcsec).toFixed(2) + "\"");
      else lines.push(f.ratio !== null ? "×" + f.ratio.toFixed(2) : f.value.toFixed(2) + " px");
      if (f.uncertain) lines[0] = "(" + lines[0] + ")";
      if (show.details) {
        var sc = show.mode === "arcsec" ? show.arcsec : 1, un = show.mode === "arcsec" ? "\"" : " px";
        lines.push((f.se !== null ? "±" + (f.se * sc).toFixed(2) + un + ", " : "") + "n=" + f.n);
      }
      var cx = (c + 0.5) * cw, cy = (r + 0.5) * ch + big / 3 - (lines.length - 1) * small * 0.7;
      ctx.font = "bold " + big + "px Segoe UI, Helvetica Neue, Arial, sans-serif";
      haloText(ctx, lines[0], cx, cy, "#ffffff");
      if (lines.length > 1) {
        ctx.font = small + "px Segoe UI, Helvetica Neue, Arial, sans-serif";
        haloText(ctx, lines[1], cx, cy + small * 1.4, "#d8d8d8");
      }
    }
  }

  // Section along both diagonals: the image shell and the sensor (vertical scale exaggerated).
  function drawSection(canvas, m) {
    var W = 640, H = 220, L = 52, R = 14, top = 22, bottom = 34;
    var ctx = setup(canvas, W, H);
    var half = m.diag / 2, steps = 120, diags = [[-m.w / 2, -m.h / 2], [m.w / 2, -m.h / 2]];
    var curves = diags.map(function (d) {
      var ux = -2 * d[0] / m.diag, uy = -2 * d[1] / m.diag, shell = [], sensor = [];
      for (var i = 0; i <= steps; ++i) {
        var t = -half + 2 * half * i / steps, x = t * ux, y = t * uy;
        shell.push([t, m.shell(x, y)]); sensor.push([t, m.sensor(x, y)]);
      }
      return { shell: shell, sensor: sensor };
    });
    var zmax = 10;
    curves.forEach(function (cv) { cv.shell.concat(cv.sensor).forEach(function (p) { zmax = Math.max(zmax, Math.abs(p[1])); }); });
    zmax *= 1.15;
    function X(t) { return L + (t + half) / (2 * half) * (W - L - R); }
    function Y(z) { return top + (zmax - z) / (2 * zmax) * (H - top - bottom); }

    ctx.font = "12px Segoe UI, Helvetica Neue, Arial, sans-serif";
    ctx.strokeStyle = "#2a3b4f"; ctx.lineWidth = 1; ctx.fillStyle = "#a8b6c8";
    var step = Math.pow(10, Math.floor(Math.log10(zmax)));
    if (zmax / step < 2) step /= 5; else if (zmax / step < 5) step /= 2;
    ctx.textAlign = "right";
    for (var z = -Math.floor(zmax / step) * step; z <= zmax; z += step) {
      ctx.beginPath(); ctx.moveTo(L, Y(z)); ctx.lineTo(W - R, Y(z)); ctx.stroke();
      ctx.fillText((Math.abs(z) < 1e-9 ? "0" : sgn(z, step < 1 ? 1 : 0)) + " µm", L - 6, Y(z) + 4);
    }
    ctx.textAlign = "center";
    [-half, 0, half].forEach(function (t) { ctx.fillText(num(t, 1) + " mm", X(t), H - 12); });
    ctx.textAlign = "left"; ctx.fillText(T.away, L + 6, 14);

    function path(pts, color, dash) {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dash);
      ctx.beginPath();
      pts.forEach(function (p, i) { if (i) ctx.lineTo(X(p[0]), Y(p[1])); else ctx.moveTo(X(p[0]), Y(p[1])); });
      ctx.stroke(); ctx.setLineDash([]);
    }
    curves.forEach(function (cv, i) {
      path(cv.shell, "#64c7ff", i ? [6, 5] : []);
      path(cv.sensor, "#ffab5e", i ? [6, 5] : []);
    });
    // legend
    var lx = W - R - 300, ly = 14;
    ctx.textAlign = "left";
    [["#64c7ff", T.shellLabel], ["#ffab5e", T.sensorLabel]].forEach(function (k, i) {
      ctx.strokeStyle = k[0]; ctx.lineWidth = 2; ctx.beginPath();
      ctx.moveTo(lx + i * 110, ly - 4); ctx.lineTo(lx + i * 110 + 22, ly - 4); ctx.stroke();
      ctx.fillStyle = "#a8b6c8"; ctx.fillText(k[1], lx + i * 110 + 28, ly);
    });
    ctx.strokeStyle = "#a8b6c8"; ctx.lineWidth = 1; ctx.setLineDash([6, 5]);
    ctx.beginPath(); ctx.moveTo(lx + 205, ly - 4); ctx.lineTo(lx + 227, ly - 4); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillText("↘ / ↙", lx + 233, ly);
  }

  // --- Simulator ---

  var ids = ["ds", "sens", "tilt", "tiltdir", "focus", "N", "F", "S", "sensor", "P", "stars", "scatter", "details"];
  var el = {};
  ids.forEach(function (id) { el[id] = document.getElementById("ss-" + id); });
  var modes = document.querySelectorAll("input[name=ss-mode]");
  var seed = 1;

  function val(id) { return +el[id].value; }
  function setText(id, s) { var o = document.getElementById("ss-v" + id); if (o) o.textContent = s; }

  function draw() {
    var ds = val("ds"), sens = val("sens"), tiltDeg = val("tilt"), dir = val("tiltdir"), focus = val("focus");
    var N = val("N"), F = val("F"), seeingArc = val("S"), pitch = val("P"), nStars = val("stars"), scatter = val("scatter") / 100;
    var sz = el.sensor.value.split(",").map(Number), w = sz[0], h = sz[1];
    var mode = "ratio";
    Array.prototype.forEach.call(modes, function (m) { if (m.checked) mode = m.value; });

    setText("ds", sgn(ds, 1) + " mm");
    setText("sens", sens + " µm");
    setText("tilt", num(tiltDeg, 2) + T.deg);
    setText("tiltdir", dir + T.deg);
    setText("focus", sgn(focus, 0) + " µm");
    setText("N", "f/" + num(N, 1));
    setText("F", F + " mm");
    var f0 = seeingArc / 206265 * F * 1000;            // seeing FWHM, µm
    setText("S", num(seeingArc, 1) + "″ = " + num(f0, 1) + " µm");
    setText("P", num(pitch, 2) + " µm");
    setText("stars", nStars);
    setText("scatter", num(100 * scatter, 0) + " %");

    // Geometry in mm (x right, y down, origin in the center), depths in µm.
    var tt = Math.tan(tiltDeg * Math.PI / 180) * 1000, cd = Math.cos(dir * Math.PI / 180), sd = Math.sin(dir * Math.PI / 180);
    var kappa = sens * ds / 225;                         // sag per mm², from the sag at r = 15 mm
    function shell(x, y) { return kappa * (x * x + y * y); }
    function sensor(x, y) { return tt * (x * cd + y * sd) + focus; }
    function fwhm(x, y) { var d = (shell(x, y) - sensor(x, y)) / N; return Math.sqrt(f0 * f0 + d * d); }

    var rand = rng(seed), stars = [];
    for (var i = 0; i < nStars; ++i) {
      var u = rand(), v = rand(), x = (u - 0.5) * w, y = (v - 0.5) * h;
      var g = Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand());
      stars.push({ u: u, v: v, c: Math.min(GRID - 1, Math.floor(u * GRID)), r: Math.min(GRID - 1, Math.floor(v * GRID)),
                   fwhm: fwhm(x, y) / pitch * Math.exp(scatter * g) });
    }
    var ev = evaluate(stars), arcsec = 206.265 * pitch / F;
    drawMap(document.getElementById("ss-map"), ev, stars, { w: w, h: h, mode: mode, arcsec: arcsec, details: el.details.checked });
    var diag = Math.hypot(w, h);
    drawSection(document.getElementById("ss-section"), { w: w, h: h, diag: diag, shell: shell, sensor: sensor });

    var evalLines = mode === "arcsec" ? evaluationLines(ev, arcsec, "\"") : evaluationLines(ev, 1, "px");
    document.getElementById("ss-eval").textContent = evalLines.join("\n");

    // The true values of the model, for comparison with the measured map.
    var cx = [-w / 2, w / 2, -w / 2, w / 2], cy = [-h / 2, -h / 2, h / 2, h / 2];
    var fc = fwhm(0, 0);
    var dz = cx.map(function (x, k) { return shell(x, cy[k]) - sensor(x, cy[k]); });
    var ratio = cx.map(function (x, k) { return fwhm(x, cy[k]) / fc; });
    document.getElementById("ss-truth").innerHTML =
      T.seeing + " " + num(f0 / pitch, 2) + " px · " + T.trueCenter + " <b>" + num(fc / pitch, 2) + " px</b>" +
      "<br>" + T.cornersHead + ": " + T.defocus + " <b>" + dz.map(function (z) { return sgn(z, 0); }).join(" / ") + " µm</b>, " +
      T.trueRatio + " <b>" + ratio.map(function (r) { return "×" + num(r, 2); }).join(" / ") + "</b>" +
      "<br>" + T.shell + ": <b>" + sgn(shell(w / 2, h / 2), 0) + " µm</b> · " + T.tiltEdge + " <b>" +
      num(Math.abs(tt) * diag / 2, 0) + " µm</b>";
  }

  ids.forEach(function (id) { el[id].addEventListener("input", draw); });
  Array.prototype.forEach.call(modes, function (m) { m.addEventListener("change", draw); });
  document.getElementById("ss-reseed").addEventListener("click", function () { ++seed; draw(); });
  draw();
})();
