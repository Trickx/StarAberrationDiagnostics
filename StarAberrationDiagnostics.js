#engine v8

#feature-id    StarAberrationDiagnostics : Tricx > StarAberrationDiagnostics

#feature-info  Diagnostics of the optical system from the shapes of the stars.<br/> \
   <br/> \
   Measures every star across the field - elongation, FWHM and the asymmetry \
   of its profile - and maps sensor tilt, corrector spacing, field \
   curvature, coma and collimation as well as the tracking error shared by \
   all stars. Three maps - star shape (ellipses, streamlines), star size \
   (FWHM grid, tilt axis) and coma (asymmetry field) - each on its own tab, \
   and an assessment judges the results and suggests corrections in the \
   order in which to work through them.<br/> \
   <br/> \
   Copyright &copy; 2026 Sven Kopetzki. GNU General Public License v3 or later.

#feature-icon  StarAberrationDiagnostics.svg

// The preview of the dialog uses the standard PJSR image view and splitter,
// as AperturePhotometry does.
#include <pjsr/controls/ImageView.js>
#include <pjsr/controls/Splitter.js>

/*
   StarAberrationDiagnostics.js
   ---------------------------------------------------------------------
   PixInsight PJSR script — V8 engine only (PixInsight 1.9.4 "Lockhart"+)
   Star detection + PSF fit (X, Y, FWHMx, FWHMy, Eccentricity, Rotation)
   and display as a vector map for tilt/coma diagnostics.

   Copyright (C) 2026 Sven Kopetzki

   This program is free software: you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation, either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <https://www.gnu.org/licenses/>.

   IMPORTANT: This script exclusively uses PixInsight's new V8 JavaScript
   runtime (>= 1.9.4). Its only #include directives are the PJSR image
   view and splitter controls of the dialog's preview.

   How it works:
     1. Optional: debayer (using the "SuperPixel" method) on a copy of the
        target image, if it is still an unprocessed Bayer mosaic (raw CFA)
        rather than a true RGB image. Interpolation-free 2x2 pixel
        grouping, so that the star profiles used for the tilt/coma
        measurement are not distorted by CFA pattern artifacts. The
        original is left untouched.
     2. Two-stage star detection on the (optionally debayered) target
        image, freely selectable via a ViewList: StarDetector (PixInsight's
        core star detector) supplies candidate positions; DynamicPSF
        itself does NOT search the field automatically as a process (see
        the comment at detectStarCandidates/fitStars) - it only fits a
        PSF model (Gaussian or Moffat) to a given candidate list.
     3. FWHMx, FWHMy, eccentricity and rotation angle are computed from
        sx/sy and theta.
     4. An ellipse is drawn for each star:
          - Major axis = eccentricity (scaled)
          - Minor axis = derived from the eccentricity (e = sqrt(1-(b/a)^2))
          - Angle = PSF rotation
          - Color = eccentricity (blue = round, red = strongly elongated)
        -> Radial elongation in the outer field = under-corrected coma,
           concentric = over-correction/defocus/field curvature, a pattern
           that differs between opposite sides = tilt, all stars elongated
           in the same direction = tracking. Coma itself is identified by
           the PSF asymmetry (flare direction), which the symmetric PSF
           models cannot show - see measureStarAsymmetry().
     5. Optionally, the data table can be exported as CSV.
     6. The "New Instance" icon (blue triangle, bottom left of the dialog)
        can be used to drop the current parameter set as a process icon on
        the workspace. Double-clicking this icon later reopens the dialog
        with the same settings.
        NOTE: Dragging the icon directly from the still-open dialog onto a
        target image is currently not supported by PixInsight for script
        processes (unlike native PCL processes) and results in "Attempt to
        execute a Script instance recursively".

   Note on the DynamicPSF table (empirically verified, see diagnostic
   runs): P.psf[i] actually has 18 columns, but the first 13 match exactly
   the originally assumed order:
     [ starIndex, function, circular, status, B, A, cx, cy, sx, sy,
       theta, beta, mad, ... 5 more, unused columns here ]
   IMPORTANT: status = 1 means "fit OK" (not 0, as originally assumed - all
   rows returned in the psf array had status=1).
   If your PixInsight build returns a different column order, the
   unconditional diagnostic output in fitStars() will reveal the raw row
   along with the status value distribution whenever 0 valid hits occur -
   adjust the IDX_* constants below accordingly.
   ---------------------------------------------------------------------
*/

CoreApplication.ensureMinimumVersion(1, 9, 4);

const VERSION = "0.11";
const TITLE = "Star Aberration Diagnostics";

// ---- Spaltenindizes in DynamicPSF.psf --------------------------------
const IDX_STARINDEX = 0;
const IDX_FUNCTION  = 1;
const IDX_CIRCULAR  = 2;
const IDX_STATUS    = 3;
const IDX_B         = 4;
const IDX_A         = 5;
const IDX_CX        = 6;
const IDX_CY        = 7;
const IDX_SX        = 8;
const IDX_SY        = 9;
const IDX_THETA     = 10;
const IDX_BETA      = 11;
const IDX_MAD       = 12;

// Gauss-Sigma -> FWHM Umrechnungsfaktor
const SIGMA_TO_FWHM = 2 * Math.sqrt(2 * Math.log(2)); // ~2.35482

// Moffat core width alpha -> FWHM factor for a given beta
function moffatAlphaToFwhm(beta) {
   return 2 * Math.sqrt(Math.pow(2, 1 / beta) - 1);
}

// -----------------------------------------------------------------------
// The script's settings with their factory defaults. As a PersistentObject
// (pjsr/utility/PersistentObject.js, as in AperturePhotometry) they are
// remembered between runs in PixInsight's settings store
// (StarAberrationDiagnostics/<name>: LoadSettings/SaveSettings) and stored
// on a process icon by "New Instance" (<name>: SaveParameters), from which
// a drag&drop apply or a double-clicked icon reads them (LoadParameters).
// A new option needs its default below and a line in the property list.
const SETTINGS_MODULE = "StarAberrationDiagnostics";

var ScriptParameters = class extends PersistentObject {
   constructor() {
      super(SETTINGS_MODULE, "", [
         ["threshold",                   DataType.Double],
         ["radius",                      DataType.Int32],
         ["useMoffat",                   DataType.Boolean],
         ["vectorScale",                 DataType.Double],
         ["doExport",                    DataType.Boolean],
         ["debayer",                     DataType.Boolean],
         ["bayerPattern",                DataType.UTF16String],
         ["closeIntermediateWindows",    DataType.Boolean],
         ["madOutlierFactor",            DataType.Double],
         ["maxCandidates",               DataType.Int32],
         ["sdCustom",                    DataType.Boolean],
         ["sdStructureLayers",           DataType.Int32],
         ["sdSensitivity",               DataType.Double],
         ["sdPeakResponse",              DataType.Double],
         ["sdMaxDistortion",             DataType.Double],
         ["sdAllowClustered",            DataType.Boolean],
         ["showStreamlines",             DataType.Boolean],
         ["shapeSignificantOnly",        DataType.Boolean],
         ["shapeShowCells",              DataType.Boolean],
         ["shapeShowModel",              DataType.Boolean],
         ["shapeModelSignificantOnly",   DataType.Boolean],
         ["shapeShowMatch",              DataType.Boolean],
         ["shapeShowDefects",            DataType.Boolean],
         ["streamlineRadiusPercent",     DataType.Double],
         ["pixelPitchUm",                DataType.Double],
         ["focalLengthMm",               DataType.Double],
         ["apertureMm",                  DataType.Double],
         ["show3DTiltPlot",              DataType.Boolean],
         ["showOrientationHeatmap",      DataType.Boolean],
         ["orientationHeatmapDegree",    DataType.Int32],
         ["fwhmGridDetails",             DataType.Boolean],
         ["fwhmCellValue",               DataType.Int32],
         ["opticsFromHeader",            DataType.Boolean],
         ["showStarAsymArrows",          DataType.Boolean],
         ["starAsymMinSigma",            DataType.Double],
         ["starAsymColorByStrength",     DataType.Boolean],
         ["hideEllipses",                DataType.Boolean],
         ["showTiltAxis",                DataType.Boolean],
         ["subtractTracking",            DataType.Boolean],
         ["measureAsymmetry",            DataType.Boolean],
         ["showComaStreamlines",         DataType.Boolean],
         ["comaStreamlineRadiusPercent", DataType.Double],
         ["comaStreamlineMinPercent",    DataType.Double],
         ["showComaArrows",              DataType.Boolean],
         ["opticsType",                  DataType.Int32],
         ["mountType",                   DataType.Int32],
         ["guidingType",                 DataType.Int32],
         ["seriesShowMaps",              DataType.Boolean],
         ["seriesSaveMaps",              DataType.Boolean],
         ["seriesSaveMosaic",            DataType.Boolean]
      ]);

      this.threshold = 3.0;
      this.radius = 8;
      this.useMoffat = false;
      this.vectorScale = 1.0;
      this.doExport = true;
      this.debayer = false;
      this.bayerPattern = "Auto"; // "Auto", "RGGB", "BGGR", "GBRG", "GRBG"
      this.closeIntermediateWindows = true; // close SuperPixel/side windows after processing
      this.madOutlierFactor = 3.0; // reject fits with MAD > factor x median(MAD); 0 = off
      this.maxCandidates = 1500; // max. candidates for the PSF fit (brightest first); 0 = no limit
      // StarDetector parameters (PJSR StarDetector, defaults as documented
      // there). Only applied when sdCustom is set; otherwise StarDetector
      // runs with its own defaults.
      this.sdCustom = false;
      this.sdStructureLayers = 5; // largest detectable structure ~2^n px; more = larger (defocused/aberrated) stars
      this.sdSensitivity = 0.5; // 0..1, higher = fainter stars
      this.sdPeakResponse = 0.5; // 0..1, higher = more tolerant of flat profiles
      this.sdMaxDistortion = 0.6; // 0..1, higher = more elongated/irregular stars accepted
      this.sdAllowClustered = false; // detect non-separable multiple sources as one object
      this.showStreamlines = true; // show the smoothed trend as a streamlines overlay
      this.shapeSignificantOnly = false; // streamlines only where the smoothed direction is significant (else gray there)
      this.shapeShowCells = true; // Star shape map: mean axis per cell with its uncertainty fan
      this.shapeShowModel = false; // Star shape map: streamlines of the optics model
      this.shapeModelSignificantOnly = false; // model streamlines only where the model is significant (else muted there)
      this.shapeShowMatch = false; // Star shape map: per cell, magenta frame = not explained by the model, green = matches
      this.shapeShowDefects = true; // Star shape map: singular points of the smoothed field
      this.streamlineRadiusPercent = 12; // smoothing radius, % of the image diagonal
      this.pixelPitchUm = 0; // pixel pitch (µm); 0 = not specified, tilt angle computation off
      this.focalLengthMm = 0; // telescope focal length (mm); 0 = not specified
      this.apertureMm = 0; // telescope aperture (mm); 0 = not specified
      this.show3DTiltPlot = true; // pseudo-3D comparison plot (only if the above 3 values are set)
      this.showOrientationHeatmap = false; // PSF orientation as a color-field heatmap instead of the star field in the background
      this.orientationHeatmapDegree = 2; // 2D polynomial degree for the orientation heatmap fit; 1 = plane (pure tilt only), >=2 can also show curvature (e.g. coma)
      this.fwhmGridDetails = false; // FWHM grid: a second line per cell with FWHM ± standard error and star count
      this.fwhmCellValue = 0; // FWHM grid: each cell shows its FWHM in pixels (0), in arcseconds (1, needs pixel pitch + focal length) or its ratio to the center (2)
      this.opticsFromHeader = true; // take pixel pitch/focal length/aperture from the FITS header where available
      this.showStarAsymArrows = false; // one asymmetry (coma flare) arrow per star
      this.starAsymMinSigma = 2.0; // per-star arrows only above this many sigma of the per-star asymmetry noise
      this.starAsymColorByStrength = false; // arrow color: false = alignment with the ellipse axis, true = strength
      this.hideEllipses = false; // hide the per-star ellipses (e.g. to look at the arrows alone)
      this.showTiltAxis = true; // draw the tilt axis line through the sensor center (part of the tilt overlay)
      this.subtractTracking = true; // subtract the field-wide median elongation (tracking/guiding) before drawing and analysis
      this.measureAsymmetry = true; // measure the PSF asymmetry (coma flare direction) and fit the coma-free point
      this.showComaStreamlines = true; // streamlines of the smoothed asymmetry (coma) field, orange with arrowheads
      this.comaStreamlineRadiusPercent = 24; // smoothing radius for the coma streamlines, % of the image diagonal
      this.comaStreamlineMinPercent = 20; // coma streamlines stop below this % of the field's (90th percentile) strength
      this.showComaArrows = true; // median asymmetry arrows on a coarse grid
      this.opticsType = 0; // optical system for the assessment's wording: index into OPTICS_TYPES (0 = unknown)
      this.mountType = 0; // the mount, when not read from the FITS header: index into MOUNT_TYPES (0 = unknown)
      this.guidingType = 0; // guiding: index into GUIDING_TYPES (0 = unknown; then a guide camera in the header counts)
      this.seriesShowMaps = true; // series: draw each frame's map (of the selected tab) in the preview while measuring
      this.seriesSaveMaps = true; // series: save each frame's four maps (and 3D plot) as PNG next to the frame
      this.seriesSaveMosaic = true; // series: save a 3x3 corner/edge/center mosaic (as AberrationInspector) as PNG next to the frame
   }

   // Takes the values of all settings from an object with the same
   // property names (the dialog's collectParameters()).
   assign(source) {
      for (let i = 0; i < this.properties.length; ++i) {
         let name = this.properties[i][0];
         this[name] = source[name];
      }
   }
};

// The factory defaults, replaced by the settings remembered from the last
// run (if any); a process icon's parameters replace these again in main().
let parameters = new ScriptParameters();
parameters.LoadSettings();

// -----------------------------------------------------------------------
class StarRecord {
   constructor(x, y, fwhmX, fwhmY, ecc, thetaDeg) {
      this.x = x;
      this.y = y;
      this.fwhmX = fwhmX;
      this.fwhmY = fwhmY;
      this.eccentricity = ecc;
      this.rotation = thetaDeg;
   }
}

// -----------------------------------------------------------------------
// Simple progress display for loops we control ourselves (draw loops
// etc.). Guaranteed to print real, newline-terminated console lines at
// fixed percentage steps - deliberately NO \r overwrite, since that often
// never arrives as a visible line in non-interactive/batch runs (e.g.
// "-x=auto"). Additionally drives the native GUI progress bar
// (Console.progressStart/Update/End) if available - but that's irrelevant
// for text logs, hence not the only source. Checked defensively so a
// wrongly guessed/missing API doesn't crash (see the note at the top of
// the file on the constants/API question).
function withProgress(label, total) {
   // The console shows only the process steps (Progress), not this loop's
   // percentage: no native console progress either.
   let useNative = false && typeof Console.progressStart === "function" &&
      typeof Console.progressUpdate === "function" &&
      typeof Console.progressEnd === "function";
   if (useNative) {
      try {
         Console.progressStart(label, total);
      } catch (e) {
         useNative = false;
      }
   }

   let lastBucket = -1;
   let lastPercent = -1;
   let lastTick = Date.now();
   const PERCENT_STEP = 10; // one line every 10% -> max. 11 lines in total

   return {
      update: function(value) {
         if (useNative) {
            try { Console.progressUpdate(value); } catch (e) { useNative = false; }
         }
         // The bar moves - and the dialog processes its events - with every
         // percent, and at least every 0.1 s in a slow loop, so that the
         // window keeps reacting (repaint, being sent to the background).
         let percent = total > 0 ? Math.floor((value / total) * 100) : 100;
         let now = Date.now();
         if (percent !== lastPercent || now - lastTick >= 100) {
            lastPercent = percent;
            lastTick = now;
            Progress.advance(percent / 100);
         }
         let bucket = Math.floor(percent / PERCENT_STEP) * PERCENT_STEP;
         if (bucket === lastBucket)
            return;
         lastBucket = bucket;
         Quiet.writeln(format("%s: %d%% (%d/%d)", label, percent, value, total));
      },
      end: function() {
         if (useNative) {
            try { Console.progressEnd(); } catch (e) {}
         }
         if (lastBucket < 100)
            Quiet.writeln(format("%s: 100%% (done)", label));
      }
   };
}

// -----------------------------------------------------------------------
// Progress of a whole run for the dialog's progress bar. The run is split
// into steps, each covering a part [from, to] of the bar; withProgress()
// loops move the bar within the current step. Without a listener (no
// dialog, e.g. a drag&drop apply) this does nothing.
// Every step is also the console's record of the run: when the next step
// starts (or endStep()/finish() is called), the step is written with its
// duration - the console shows only these process steps, the results are
// in the dialog (see Quiet).
const Progress = {
   listener: null, // function(text, fraction), fraction in [0,1]; text null = finished
   text: "",
   from: 0,
   to: 0,
   stepText: null,
   stepT0: 0,
   step: function(text, from, to) {
      this.endStep();
      this.stepText = text;
      this.stepT0 = Date.now();
      this.text = text;
      this.from = from;
      this.to = to;
      this.advance(0);
   },
   advance: function(fraction) {
      if (this.listener)
         this.listener(this.text, this.from + (this.to - this.from) * Math.range(fraction, 0, 1));
   },
   // Writes the running step with its duration.
   endStep: function() {
      if (this.stepText !== null)
         Console.writeln(format("  %s: %.1f s", this.stepText, (Date.now() - this.stepT0) / 1000));
      this.stepText = null;
   },
   finish: function() {
      this.endStep();
      if (this.listener)
         this.listener(null, 0);
   }
};

// The console shows only the process steps and their timing (Progress),
// the files written, warnings and errors; the results - tables, values,
// the assessment - are in the dialog. Everything that used to be written
// as a result goes here and is dropped. QUIET_CONSOLE is the same as a
// console object (con) for the report functions: results dropped,
// warnings and errors passed on.
const Quiet = { write: function() {}, writeln: function() {}, noteln: function() {} };
const QUIET_CONSOLE = {
   write: function() {}, writeln: function() {}, noteln: function() {},
   warningln: function(text) { Console.warningln(text); },
   criticalln: function(text) { Console.criticalln(text); }
};

// -----------------------------------------------------------------------
// Generic diagnostic dumper: lists all (including inherited/non-
// enumerable) properties of any PJSR object along with their values to
// the console. Used to empirically verify uncertain PCL API shapes (e.g.
// the return object of StarDetector.stars()) without being able to
// inspect PixInsight live.
function dumpObject(label, obj) {
   Quiet.writeln("--- " + label + " ---");
   if (obj === null || obj === undefined) {
      Quiet.writeln("  (null/undefined)");
      Quiet.writeln("--- end ---");
      return;
   }
   let names = Object.getOwnPropertyNames(obj);
   let proto = Object.getPrototypeOf(obj);
   if (proto) {
      let protoNames = Object.getOwnPropertyNames(proto);
      for (let i = 0; i < protoNames.length; ++i)
         if (names.indexOf(protoNames[i]) < 0)
            names.push(protoNames[i]);
   }
   for (let i = 0; i < names.length; ++i) {
      let name = names[i];
      if (name === "constructor")
         continue;
      let value;
      try { value = obj[name]; } catch (e) { value = "<error: " + e.message + ">"; }
      let desc;
      if (typeof value === "function")
         desc = "<function>";
      else if (value === null)
         desc = "null";
      else if (typeof value === "object")
         desc = "<object: " + Object.getOwnPropertyNames(value).join(", ") + ">";
      else
         desc = String(value);
      Quiet.writeln("  " + name + " = " + desc);
   }
   Quiet.writeln("--- end ---");
}

// -----------------------------------------------------------------------
// Stage 1: find candidate star positions with StarDetector (PixInsight's
// own core star detector, also used internally by StarAlignment among
// others). DynamicPSF as a process apparently only fits against a given
// P.stars table and does not itself search the field automatically (see
// diagnostic runs: P.stars.length stayed at 0 for every combination of
// autoPSF/threshold/searchRadius).
// Returns an array of DynamicPSF.stars rows ([viewIndex=0,
// channel=0, x0, y0, x1, y1]), or null on error. If candidateInfo is an
// array, it receives one {x, y, flux, size, bkg} entry per returned row
// (same index), used by reportRejections() to explain dropped candidates.
function detectStarCandidates(image, searchRadius, maxCandidates, candidateInfo, sd) {
   if (typeof StarDetector === "undefined") {
      Console.criticalln("StarDetector is not available in this PixInsight version.");
      return null;
   }

   let D = new StarDetector;
   // Optional StarDetector parameters (sdCustom): the defaults reject
   // strongly elongated and large stars, which are exactly the aberrated
   // stars at the field edge - raising maxDistortion/structureLayers
   // keeps more of them.
   if (sd && sd.sdCustom) {
      D.structureLayers = sd.sdStructureLayers;
      D.sensitivity = sd.sdSensitivity;
      D.peakResponse = sd.sdPeakResponse;
      D.maxDistortion = sd.sdMaxDistortion;
      D.allowClusteredSources = sd.sdAllowClustered;
      Quiet.writeln(format("StarDetector parameters: structureLayers=%d  sensitivity=%.2f  " +
         "peakResponse=%.2f  maxDistortion=%.2f  allowClusteredSources=%s",
         D.structureLayers, D.sensitivity, D.peakResponse, D.maxDistortion,
         D.allowClusteredSources ? "true" : "false"));
   }

   // StarDetector.stars() is a single, opaque native call with no
   // intermediate progress - on large images this can take a while with
   // no console output at all. At least show start/end/duration so it's
   // clear the script is still working.
   Quiet.writeln("StarDetector running (can take a moment on large images) ...");
   let t0 = Date.now();
   let rawStars;
   try {
      rawStars = D.stars(image);
   } catch (e) {
      Console.criticalln("StarDetector.stars() failed: " + e.message);
      return null;
   }
   Quiet.writeln(format("StarDetector finished after %.1f s.", (Date.now() - t0) / 1000));

   Quiet.noteln(rawStars.length + " candidate(s) found by StarDetector.");
   if (rawStars.length > 0)
      dumpObject("StarDetector candidate[0] (diagnostics)", rawStars[0]);

   // Limit the candidate count before the PSF fit (sorted descending by
   // brightness/flux, dropping the faintest first): the actual time sink
   // is not StarDetector but the subsequent blocking DynamicPSF fit over
   // ALL candidates - fewer, but brighter/more reliable candidates
   // directly reduce its runtime.
   if (maxCandidates > 0 && rawStars.length > maxCandidates) {
      let totalFound = rawStars.length;
      rawStars = rawStars.slice().sort(function(a, b) {
         return (b.flux || 0) - (a.flux || 0);
      }).slice(0, maxCandidates);
      Quiet.noteln(format("Limited to the %d brightest candidates (of %d found) - " +
         "reduces the runtime of the subsequent DynamicPSF fit.", maxCandidates, totalFound));
   }

   let boxes = [];
   let unrecognized = 0;
   for (let i = 0; i < rawStars.length; ++i) {
      let s = rawStars[i];
      let x, y;

      // Empirically confirmed (diagnostic dump): StarDetector candidates
      // carry x/y as direct numeric fields (not .pos.x/.pos.y).
      if (s && typeof s.x === "number" && typeof s.y === "number") {
         x = s.x; y = s.y;
      } else if (Array.isArray(s) && s.length >= 2 && typeof s[0] === "number") {
         x = s[0]; y = s[1];
      } else {
         ++unrecognized;
         continue;
      }

      let x0 = x - searchRadius, y0 = y - searchRadius;
      let x1 = x + searchRadius, y1 = y + searchRadius;

      // DynamicPSF.stars row format: according to a runtime error, exactly
      // 9 values are expected per row ("expected 9 values; got 6").
      // Reconstructed by analogy with the documented psf output table
      // (see header comment):
      // [ viewIndex, channel, status, x0, y0, x1, y1, cx, cy ].
      // Status: DynamicPSF.Star_DetectedOk, as used by the scripts shipped
      // with PixInsight (FWHMEccentricity, AperturePhotometry, 2DPlot).
      boxes.push([0, 0, dynamicPSFConstant("Star_DetectedOk", 0), x0, y0, x1, y1, x, y]);
      if (candidateInfo)
         candidateInfo.push({ x: x, y: y, flux: s.flux || 0, size: s.size || 0, bkg: s.bkg || 0 });
   }

   if (unrecognized > 0)
      Console.warningln(unrecognized + " candidate(s) with unknown format skipped " +
         "(see diagnostic dump above, adjust .pos/.rect access if needed).");

   return boxes;
}

// -----------------------------------------------------------------------
// Rejection analysis after DynamicPSF.executeGlobal(): explains why only
// part of the candidates produced a PSF row. DynamicPSF re-detects a star
// inside every search rectangle and silently drops candidates it cannot
// find there, so this compares fitted vs. dropped candidates using what is
// known about them (StarDetector size/flux, border distance, neighbors,
// peak level) and prints the raw status codes DynamicPSF reports.
//
// Status names are read at runtime from DynamicPSF's own enumeration
// constants (Star_* for P.stars, PSF_* for P.psf) instead of being guessed.

// Value of a DynamicPSF enumeration constant (static or via prototype,
// depending on the engine), or fallback if not available.
function dynamicPSFConstant(name, fallback) {
   let v = DynamicPSF[name];
   if (typeof v !== "number" && DynamicPSF.prototype)
      v = DynamicPSF.prototype[name];
   return (typeof v === "number") ? v : fallback;
}

// Map value -> name for all numeric DynamicPSF constants starting with
// prefix (e.g. "Star_" or "PSF_").
function dynamicPSFConstantNames(prefix) {
   let map = {};
   let sources = [DynamicPSF, DynamicPSF.prototype];
   for (let s = 0; s < sources.length; ++s) {
      if (!sources[s])
         continue;
      let names = Object.getOwnPropertyNames(sources[s]);
      for (let i = 0; i < names.length; ++i) {
         if (names[i].indexOf(prefix) !== 0)
            continue;
         let v;
         try { v = sources[s][names[i]]; } catch (e) { continue; }
         if (typeof v === "number" && !map.hasOwnProperty(v))
            map[v] = names[i].substring(prefix.length);
      }
   }
   return map;
}

function describeCode(map, code) {
   return code + "=" + (map.hasOwnProperty(code) ? map[code] : "?");
}

function countBy(values) {
   let counts = {};
   for (let i = 0; i < values.length; ++i)
      counts[values[i]] = (counts[values[i]] || 0) + 1;
   let parts = [];
   for (let k in counts)
      parts.push(k + ":" + counts[k]);
   return parts.join(", ");
}

function medianOf(values) {
   if (values.length === 0)
      return NaN;
   let v = values.slice().sort(function(a, b) { return a - b; });
   return v[Math.floor(v.length / 2)];
}

function reportRejections(P, image, starBoxes, candidateInfo, searchRadius) {
   const MAX_LISTED = 15;
   let n = starBoxes.length;
   let w = image.width, h = image.height;

   Quiet.writeln("--- Rejection analysis ---");

   let starNames = dynamicPSFConstantNames("Star_");
   let psfNames = dynamicPSFConstantNames("PSF_");
   let listNames = function(map) {
      let parts = [];
      for (let k in map)
         parts.push(k + "=" + map[k]);
      return parts.length > 0 ? parts.join(", ") : "(none found)";
   };
   Quiet.writeln("DynamicPSF star status codes: " + listNames(starNames));
   Quiet.writeln("DynamicPSF PSF status codes:  " + listNames(psfNames));

   // 1. What DynamicPSF wrote back into its stars table (status column).
   let starsAfter = P.stars;
   if (starsAfter && starsAfter.length > 0) {
      let codes = [];
      for (let i = 0; i < starsAfter.length; ++i)
         codes.push(describeCode(starNames, starsAfter[i][2]));
      Quiet.writeln(format("P.stars after fit: %d row(s), status column distribution (code:count): %s",
         starsAfter.length, countBy(codes)));
   } else {
      Quiet.writeln("P.stars after fit: empty (DynamicPSF did not write back its stars table).");
   }

   // 2. Status of the rows that did produce a PSF.
   let fitted = {};
   let psfCodes = [];
   for (let i = 0; i < P.psf.length; ++i) {
      let row = P.psf[i];
      fitted[row[IDX_STARINDEX]] = row[IDX_STATUS];
      let code = row[IDX_STATUS];
      psfCodes.push(describeCode(psfNames, code));
   }
   Quiet.writeln(format("P.psf: %d row(s) for %d candidate(s); status distribution: %s",
      P.psf.length, n, P.psf.length > 0 ? countBy(psfCodes) : "-"));

   if (!candidateInfo || candidateInfo.length !== n) {
      Quiet.writeln("(No StarDetector metadata available - detailed analysis skipped.)");
      Quiet.writeln("--- end ---");
      return;
   }

   // Global maximum as saturation reference (raw/debayered data often
   // does not reach 1.0, e.g. 14-bit data in a 16-bit container).
   let globalMax = image.maximum();
   let boxDiameter = 2 * searchRadius + 1;

   // Spatial grid for neighbor lookup (cell size = box size).
   let cell = Math.max(1, boxDiameter);
   let grid = {};
   for (let i = 0; i < n; ++i) {
      let key = Math.floor(candidateInfo[i].x / cell) + "," + Math.floor(candidateInfo[i].y / cell);
      (grid[key] = grid[key] || []).push(i);
   }

   let reasons = { edge: 0, tooLarge: 0, neighbor: 0, saturated: 0, none: 0 };
   // Same counts, broken down by the status DynamicPSF wrote back into
   // P.stars - shows which heuristic cause correlates with which code.
   let reasonsByStatus = {};
   let haveStarStatus = starsAfter && starsAfter.length === n;
   let dropped = [], kept = [];
   let listed = 0;

   for (let i = 0; i < n; ++i) {
      let c = candidateInfo[i];
      let isFitted = fitted.hasOwnProperty(i) && fitted[i] === dynamicPSFConstant("PSF_FittedOk", 1);
      (isFitted ? kept : dropped).push(c);
      if (isFitted)
         continue;

      let why = [];

      // Search box crosses the image border.
      if (c.x - searchRadius < 0 || c.y - searchRadius < 0 || c.x + searchRadius >= w || c.y + searchRadius >= h) {
         why.push("box crosses image border");
         ++reasons.edge;
      }

      // StarDetector size is the pixel count of the star's footprint;
      // its equivalent diameter vs. the search box.
      let diameter = 2 * Math.sqrt(c.size / Math.PI);
      if (diameter > boxDiameter) {
         why.push(format("star diameter ~%.1f px > box %d px", diameter, boxDiameter));
         ++reasons.tooLarge;
      }

      // Another candidate whose box overlaps this one.
      let gx = Math.floor(c.x / cell), gy = Math.floor(c.y / cell);
      let nearest = Infinity;
      for (let dx = -2; dx <= 2; ++dx)
         for (let dy = -2; dy <= 2; ++dy) {
            let list = grid[(gx + dx) + "," + (gy + dy)];
            if (!list)
               continue;
            for (let k = 0; k < list.length; ++k) {
               if (list[k] === i)
                  continue;
               let o = candidateInfo[list[k]];
               nearest = Math.min(nearest, Math.sqrt((o.x - c.x) * (o.x - c.x) + (o.y - c.y) * (o.y - c.y)));
            }
         }
      if (nearest < boxDiameter) {
         why.push(format("neighbor candidate at %.1f px", nearest));
         ++reasons.neighbor;
      }

      // Peak inside the search box close to the global maximum.
      let peak = NaN;
      try {
         let r = new Rect(Math.max(0, Math.floor(c.x - searchRadius)), Math.max(0, Math.floor(c.y - searchRadius)),
            Math.min(w, Math.ceil(c.x + searchRadius) + 1), Math.min(h, Math.ceil(c.y + searchRadius) + 1));
         peak = image.maximum(r);
      } catch (e) {}
      if (peak >= 0.98 * globalMax) {
         why.push(format("peak %.4f ~ saturated (image max %.4f)", peak, globalMax));
         ++reasons.saturated;
      }

      if (why.length === 0) {
         why.push("no obvious cause (likely below detection threshold / fit failed)");
         ++reasons.none;
      }

      if (haveStarStatus) {
         let st = describeCode(starNames, starsAfter[i][2]);
         let b = reasonsByStatus[st] = reasonsByStatus[st] || { n: 0, edge: 0, tooLarge: 0, neighbor: 0, saturated: 0, none: 0 };
         ++b.n;
         for (let j = 0; j < why.length; ++j) {
            if (why[j].indexOf("border") >= 0) ++b.edge;
            else if (why[j].indexOf("diameter") >= 0) ++b.tooLarge;
            else if (why[j].indexOf("neighbor") >= 0) ++b.neighbor;
            else if (why[j].indexOf("saturated") >= 0) ++b.saturated;
            else if (why[j].indexOf("no obvious") >= 0) ++b.none;
         }
         why.unshift("DynamicPSF star status " + st);
      }

      if (fitted.hasOwnProperty(i))
         why.unshift("PSF status " + describeCode(psfNames, fitted[i]));

      if (listed < MAX_LISTED) {
         Quiet.writeln(format("  dropped #%d at (%.1f, %.1f) flux=%.3g size=%d: %s",
            i, c.x, c.y, c.flux, c.size, why.join("; ")));
         ++listed;
      }
   }
   if (dropped.length > listed)
      Quiet.writeln(format("  ... %d more dropped candidate(s) not listed.", dropped.length - listed));

   Quiet.noteln(format("Dropped %d of %d candidate(s). Suspected causes (not exclusive): " +
      "border=%d, larger than search box=%d, overlapping neighbor=%d, saturated=%d, no obvious cause=%d",
      dropped.length, n, reasons.edge, reasons.tooLarge, reasons.neighbor, reasons.saturated, reasons.none));

   for (let st in reasonsByStatus) {
      let b = reasonsByStatus[st];
      Quiet.writeln(format("  star status %s: n=%d  border=%d, larger than box=%d, neighbor=%d, saturated=%d, no obvious cause=%d",
         st, b.n, b.edge, b.tooLarge, b.neighbor, b.saturated, b.none));
   }

   function stats(list, label) {
      Quiet.writeln(format("  %-8s n=%4d  median flux=%.3g  median size=%.0f px",
         label, list.length,
         medianOf(list.map(function(c) { return c.flux; })),
         medianOf(list.map(function(c) { return c.size; }))));
   }
   stats(kept, "fitted");
   stats(dropped, "dropped");
   Quiet.writeln(format("  search box = %d x %d px (radius %d)", boxDiameter, boxDiameter, searchRadius));
   Quiet.writeln("--- end ---");
}

// -----------------------------------------------------------------------
// Stage 2: compute the elliptical PSF fit (Gaussian/Moffat) for the given
// search rectangles (starBoxes).
function fitStars(view, starBoxes, threshold, searchRadius, useMoffat, madOutlierFactor, candidateInfo) {
   let P = new DynamicPSF;
   P.views = [[view.id]];

   // The format of P.stars is not known with certainty (reconstructed
   // empirically from a runtime error message) - with the wrong format,
   // the assignment fails immediately with a PCL error message. Catching
   // this here prevents a complete script crash and instead provides
   // useful diagnostics.
   try {
      P.stars = starBoxes;
   } catch (e) {
      Console.criticalln("P.stars assignment failed: " + e.message);
      if (starBoxes.length > 0)
         Quiet.writeln("First row (length " + starBoxes[0].length + "): [" + starBoxes[0].join(", ") + "]");
      return [];
   }

   P.autoPSF = false; // specify the model explicitly instead of choosing automatically
   P.circularPSF = false;
   P.gaussianPSF = !useMoffat;
   P.moffatPSF = useMoffat;
   P.autoAperture = true;
   // Re-detect and re-fit every star in P.stars instead of relying on
   // table state - set by all DynamicPSF-based scripts shipped with PixInsight.
   P.regenerate = true;
   P.searchRadius = searchRadius;
   P.threshold = threshold; // quality/aperture threshold in sigma

   // P.executeGlobal() is a single, blocking native call - while it runs,
   // no JS code (including any progress display) can run in between. At
   // least show start/end/duration.
   Quiet.writeln(format("DynamicPSF fit running (%d candidates) ...", starBoxes.length));
   let fitT0 = Date.now();
   try {
      P.executeGlobal();
   } catch (e) {
      Console.criticalln("DynamicPSF.executeGlobal() failed: " + e.message);
      return [];
   }
   Quiet.writeln(format("DynamicPSF fit finished after %.1f s.", (Date.now() - fitT0) / 1000));

   try {
      reportRejections(P, view.image, starBoxes, candidateInfo, searchRadius);
   } catch (e) {
      Console.warningln("Rejection analysis failed: " + e.message);
   }

   let stars = [];
   if (P.psf.length === 0) {
      Console.warningln("DynamicPSF: 0 of " + starBoxes.length + " candidate(s) successfully fitted.");
      if (starBoxes.length > 0)
         Quiet.writeln("Sample row P.stars[0] = [" + starBoxes[0].join(", ") + "]");
      return stars;
   }

   // First collect all rows with a successful fit (status===1).
   let accepted = [];
   for (let i = 0; i < P.psf.length; ++i) {
      let row = P.psf[i];
      if (row[IDX_STATUS] === dynamicPSFConstant("PSF_FittedOk", 1))
         accepted.push(row);
   }

   // MAD outlier filter: a row that was successfully "fitted" can still be
   // a noisy/unstable fit (hot pixel, blend, very faint candidate) - this
   // shows up as a disproportionately high MAD (mean absolute deviation
   // from the PSF model, column IDX_MAD) compared to the other stars in
   // the same image. Physically, neighboring stars should show similar
   // eccentricity/rotation (tilt/coma vary continuously across the field)
   // - an isolated, strongly deviating outlier right next to "normal"
   // stars is usually a fit artifact, not a real signal. Threshold =
   // madOutlierFactor * median(MAD), robust with respect to the actual
   // noise/signal strength of this particular image (instead of a fixed
   // absolute limit).
   let madCutoff = Infinity;
   let madMedian = 0;
   if (madOutlierFactor > 0 && accepted.length > 0) {
      let mads = accepted.map(function(row) { return row[IDX_MAD]; }).sort(function(a, b) { return a - b; });
      madMedian = mads[Math.floor(mads.length / 2)];
      if (madMedian > 0)
         madCutoff = madMedian * madOutlierFactor;
   }

   let rejectedByMad = 0;
   let badBeta = 0;
   for (let i = 0; i < accepted.length; ++i) {
      let row = accepted[i];

      if (row[IDX_MAD] > madCutoff) {
         ++rejectedByMad;
         Quiet.writeln(format("  MAD reject: star #%d at (%.1f, %.1f)  MAD=%.4g > cutoff %.4g (median %.4g)",
            row[IDX_STARINDEX], row[IDX_CX], row[IDX_CY], row[IDX_MAD], madCutoff, madMedian));
         continue;
      }

      let cx = row[IDX_CX];
      let cy = row[IDX_CY];
      let sx = row[IDX_SX];
      let sy = row[IDX_SY];
      let theta = row[IDX_THETA]; // degrees, CCW from the x-axis

      // sx/sy are the Gaussian sigma for a Gaussian fit, but the Moffat
      // core width alpha for a Moffat fit: FWHM = 2·alpha·sqrt(2^(1/beta) - 1).
      // The Gaussian factor would inflate Moffat FWHMs by 1.5x (beta = 1.5)
      // up to 2.7x (beta = 4) and more for larger beta.
      let toFwhm = SIGMA_TO_FWHM;
      if (useMoffat) {
         let beta = row[IDX_BETA];
         if (isFinite(beta) && beta > 0) {
            toFwhm = moffatAlphaToFwhm(beta);
         } else {
            ++badBeta;
         }
      }
      let fwhmX = sx * toFwhm;
      let fwhmY = sy * toFwhm;

      let major = Math.max(fwhmX, fwhmY);
      let minor = Math.min(fwhmX, fwhmY);
      let ecc = (major > 0) ? Math.sqrt(1 - (minor * minor) / (major * major)) : 0;

      stars.push(new StarRecord(cx, cy, fwhmX, fwhmY, ecc, theta));
   }

   if (badBeta > 0)
      Console.warningln(format("%d Moffat fit(s) without a valid beta (column IDX_BETA=%d) - Gaussian FWHM " +
         "factor used for those; check the column layout (IDX_* constants).", badBeta, IDX_BETA));

   if (rejectedByMad > 0)
      Quiet.noteln(rejectedByMad + " fit(s) rejected as MAD outliers (factor " +
         madOutlierFactor + " x median).");

   Quiet.noteln(format("%d / %d stars successfully fitted.", stars.length, P.psf.length));

   // Diagnostics - unconditional: if, despite P.psf.length > 0, not a
   // single star remains, distinguish between "status filter not working"
   // (column layout presumably wrong) and "MAD filter rejected everything"
   // (madOutlierFactor possibly too strict).
   if (stars.length === 0 && accepted.length === 0) {
      Console.criticalln("All " + P.psf.length + " PSF rows were rejected by the status filter " +
         "(row[" + IDX_STATUS + "] !== 1) - column layout presumably wrong. Diagnostics:");
      Quiet.writeln("Raw psf[0] (length " + P.psf[0].length + ") = [" + P.psf[0].join(", ") + "]");

      let statusCounts = {};
      for (let i = 0; i < P.psf.length; ++i) {
         let v = P.psf[i][IDX_STATUS];
         statusCounts[v] = (statusCounts[v] || 0) + 1;
      }
      let parts = [];
      for (let k in statusCounts)
         parts.push(k + ":" + statusCounts[k]);
      Quiet.writeln("Distribution row[IDX_STATUS=" + IDX_STATUS + "] -> count: " + parts.join(", "));
   } else if (stars.length === 0 && accepted.length > 0) {
      Console.criticalln(accepted.length + " successful fits present, but all rejected by the " +
         "MAD outlier filter - madOutlierFactor (" + madOutlierFactor + ") is too strict.");
   }

   return stars;
}

// -----------------------------------------------------------------------
function exportCSV(stars, filePath) {
   let f = new File;
   f.createForWriting(filePath);
   // Eccentricity/Rotation_deg are the values drawn in the map (tracking-
   // corrected if that option is on); *_raw are the uncorrected fit values.
   // Asym*/M3* are empty if the asymmetry measurement was off or failed.
   f.outTextLn("X,Y,FWHMx,FWHMy,Eccentricity,Rotation_deg,Eccentricity_raw,Rotation_raw_deg,AsymX_px,AsymY_px,M3X,M3Y");
   let num = function(v, digits) {
      return (v === null || v === undefined) ? "" : v.toFixed(digits);
   };
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      f.outTextLn(format("%.3f,%.3f,%.3f,%.3f,%.4f,%.2f,",
         s.x, s.y, s.fwhmX, s.fwhmY, s.eccentricity, s.rotation) +
         [num(s.eccentricityRaw, 4), num(s.rotationRaw, 2), num(s.asymX, 4), num(s.asymY, 4),
          num(s.m3X, 4), num(s.m3Y, 4)].join(","));
   }
   f.close();
   Console.noteln("CSV exported to: " + filePath);
}

// -----------------------------------------------------------------------
// The results of a Calculate as a file that can be loaded again (Setup >
// General: Save results / Load results), JSON:
//   format, version, script     identification
//   image                       viewId, filePath, width, height (of the
//                               measured image - half size after SuperPixel
//                               debayering), debayer, hasAsymmetry
//   measurement                 the detection settings of the run (analysisKey())
//   keywords                    the FITS keywords of the target image
//                               [[name, value], ...] - optics, mount and
//                               observation values are read from them
//   comaFit, comaFitCentroid    as measured (their bootstrap is random)
//   columns, stars              the stars as measured (without the tracking
//                               correction): one row of RESULTS_COLUMNS each
// Everything else (tracking, layers, evaluations) is computed from these.
// About 100 bytes per star. Non-ASCII characters are written as \u escapes,
// so the file is plain ASCII.
const RESULTS_FORMAT = "StarAberrationDiagnostics results";
const RESULTS_VERSION = 1;
const RESULTS_COLUMNS = ["x", "y", "fwhmX", "fwhmY", "eccentricity", "rotation", "asymX", "asymY", "m3X", "m3Y"];
const RESULTS_MEASUREMENT = ["threshold", "radius", "useMoffat", "madOutlierFactor", "maxCandidates", "debayer",
   "bayerPattern", "sdCustom", "sdStructureLayers", "sdSensitivity", "sdPeakResponse", "sdMaxDistortion",
   "sdAllowClustered"];
const RESULTS_EXTENSION = "_aberration.json";

// The FITS keywords of a view as [[name, value], ...] (values as text).
function viewKeywordList(view) {
   let out = [];
   try {
      let keywords = view.window.keywords;
      for (let i = 0; i < keywords.length; ++i)
         out.push([String(keywords[i].name).trim(), String(keywords[i].value)]);
   } catch (e) {
      // no keywords
   }
   return out;
}

// The default path of the results file: next to the original file, named
// after it, or in the temporary directory for an image never saved.
function resultsDefaultPath(header) {
   let filePath = header.filePath || "";
   return filePath.length > 0 ?
      File.extractDrive(filePath) + File.extractDirectory(filePath) + "/" + File.extractName(filePath) +
         RESULTS_EXTENSION :
      File.systemTempDirectory + "/" + header.viewId + RESULTS_EXTENSION;
}

function saveResults(analysis, p, filePath) {
   // 7 significant digits: sub-millipixel positions, far below the fit noise.
   let num = function(v) {
      return (v === null || v === undefined || !isFinite(v)) ? null : Number(v.toPrecision(7));
   };
   // The detection settings the stars were measured with: those of the
   // loaded file, or of the dialog (a Calculate result is current).
   let measurement = {};
   RESULTS_MEASUREMENT.forEach(function(k) { measurement[k] = (analysis.measurement || p)[k]; });
   let data = {
      format: RESULTS_FORMAT,
      version: RESULTS_VERSION,
      script: VERSION,
      created: (new Date()).toISOString(),
      image: { viewId: analysis.header.viewId, filePath: analysis.header.filePath, width: analysis.w,
               height: analysis.h, debayer: analysis.debayer, hasAsymmetry: analysis.hasAsymmetry },
      measurement: measurement,
      keywords: analysis.header.keywords,
      comaFit: analysis.comaFit,
      comaFitCentroid: analysis.comaFitCentroid,
      columns: RESULTS_COLUMNS,
      stars: analysis.stars.map(function(s) {
         // The analysis keeps the stars as measured: eccentricity = raw.
         return RESULTS_COLUMNS.map(function(c) { return num(s[c]); });
      })
   };
   // One star per line, so that the file stays readable and diffable.
   let stars = data.stars;
   data.stars = "@STARS@";
   let text = JSON.stringify(data, function(_key, value) {
      return (typeof value === "number" && !isFinite(value)) ? null : value;
   }, 1).replace('"@STARS@"', "[\n" + stars.map(function(r) { return "  " + JSON.stringify(r); }).join(",\n") + "\n ]");
   text = text.replace(/[\u007f-￿]/g, function(ch) {
      return "\\u" + ("000" + ch.charCodeAt(0).toString(16)).slice(-4);
   });
   let f = new File;
   f.createForWriting(filePath);
   f.outText(text + "\n");
   f.close();
   Console.noteln(format("Results saved (%d stars): %s", analysis.stars.length, filePath));
}

// A stand-in for the target image of loaded results: what resolveOptics(),
// processView() and the header readers ask of a view (its id, the file path
// and the FITS keywords of its window).
function resultsSourceView(header) {
   return {
      id: header.viewId,
      window: {
         filePath: header.filePath || "",
         keywords: header.keywords.map(function(k) { return { name: k[0], value: k[1] }; })
      }
   };
}

// The background of loaded results: the target image stretched, when it is
// open (same view id and file, and the size that was measured - twice it
// after SuperPixel debayering), else a dark field like that of a series.
function resultsBackground(header, w, h, debayer) {
   let windows = ImageWindow.windows;
   for (let i = 0; i < windows.length; ++i) {
      let win = windows[i];
      if (win.isNull || win.mainView.id !== header.viewId)
         continue;
      if ((header.filePath || "") !== "" && win.filePath !== header.filePath)
         continue;
      let iw = win.mainView.image.width, ih = win.mainView.image.height;
      let k = debayer ? 2 : 1;
      if (Math.floor(iw / k) !== w || Math.floor(ih / k) !== h)
         continue;
      let bmp = renderStretchedBitmap(win.mainView);
      return k === 1 ? bmp : bmp.scaledTo(w, h);
   }
   let background = new Bitmap(w, h);
   background.fill(0xFF101010);
   return background;
}

// Loads a results file into an analysis as analyzeView() returns it, plus
// source (the stand-in view) and file (the path). Throws an Error with a
// message for the user when the file cannot be used.
function loadResults(filePath) {
   let data;
   try {
      data = JSON.parse(File.readFile(filePath).utf8ToString());
   } catch (e) {
      throw new Error("The file is not a readable results file: " + e.message);
   }
   if (!data || data.format !== RESULTS_FORMAT)
      throw new Error("The file is not a " + TITLE + " results file.");
   if (data.version > RESULTS_VERSION)
      throw new Error(format("The file has format version %d; this script reads up to version %d - " +
         "update the script.", data.version, RESULTS_VERSION));
   let img = data.image || {};
   let w = img.width, h = img.height;
   if (!(w > 0 && h > 0) || !Array.isArray(data.stars) || !Array.isArray(data.columns))
      throw new Error("The results file is incomplete (image size or stars missing).");
   let col = {};
   data.columns.forEach(function(c, i) { col[c] = i; });
   ["x", "y", "fwhmX", "fwhmY", "eccentricity", "rotation"].forEach(function(c) {
      if (col[c] === undefined)
         throw new Error("The results file lacks the column " + c + ".");
   });
   let get = function(row, c) {
      return (col[c] === undefined || row[col[c]] === null || row[col[c]] === undefined) ? null : row[col[c]];
   };
   let stars = data.stars.map(function(row) {
      let s = new StarRecord(row[col.x], row[col.y], row[col.fwhmX], row[col.fwhmY],
         row[col.eccentricity], row[col.rotation]);
      s.eccentricityRaw = s.eccentricity;
      s.rotationRaw = s.rotation;
      if (img.hasAsymmetry) {
         s.asymX = get(row, "asymX");
         s.asymY = get(row, "asymY");
         s.m3X = get(row, "m3X");
         s.m3Y = get(row, "m3Y");
      }
      return s;
   });
   if (stars.length === 0)
      throw new Error("The results file contains no stars.");
   // The bootstrap spreads were NaN when too few runs succeeded (JSON: null).
   let coma = function(fit) {
      if (!fit)
         return null;
      ["kSd", "x0Sd", "y0Sd"].forEach(function(k) { if (fit[k] === null) fit[k] = NaN; });
      return fit;
   };
   let header = { viewId: img.viewId || File.extractName(filePath), filePath: img.filePath || "",
                  keywords: Array.isArray(data.keywords) ? data.keywords : [] };
   let hasAsymmetry = img.hasAsymmetry === true;
   return {
      key: "file:" + filePath,
      targetViewId: header.viewId,
      debayer: img.debayer === true,
      w: w,
      h: h,
      stars: stars,
      tracking: computeTrackingComponent(stars, w, h),
      hasAsymmetry: hasAsymmetry,
      comaFit: hasAsymmetry ? (data.comaFit !== undefined ? coma(data.comaFit) : fitComaField(stars, w, h, false)) : null,
      comaFitCentroid: hasAsymmetry ? (data.comaFitCentroid !== undefined ? coma(data.comaFitCentroid) :
         fitComaField(stars, w, h, true)) : null,
      background: resultsBackground(header, w, h, img.debayer === true),
      layers: {},
      header: header,
      source: resultsSourceView(header),
      file: filePath,
      measurement: data.measurement || {},
      created: data.created || ""
   };
}

// -----------------------------------------------------------------------
// Color gradient: blue (round) -> red (strongly elongated)
function eccColor(ecc, eccMax) {
   let t = Math.max(0, Math.min(1, ecc / eccMax));
   let r = Math.round(255 * t);
   let b = Math.round(255 * (1 - t));
   let g = Math.round(64 * (1 - Math.abs(t - 0.5) * 2));
   return 0xFF000000 | (r << 16) | (g << 8) | b;
}

// -----------------------------------------------------------------------
// Recreation of Siril's "Show tilt" (src/algos/ccd-inspector.c,
// compute_tilt_values() + draw_polygon(), GPL, gitlab.com/free-astro/siril).
// Unlike our vector map (one vector per star, with direction), Siril
// aggregates over 4 image quadrants and only uses the mean FWHM
// (fwhmx+fwhmy)/2 - no eccentricity/rotation. It is overlaid here on top
// of our vector map in addition to it, not as a replacement for it.

// 25%-trimmed mean (like Siril's siril_stats_trmean_from_sorted_data with
// p=0.25): discards 25% of the lowest/highest values each before
// averaging - robust against individual outlier fits per quadrant.
function trimmedMean25(values) {
   let sorted = values.slice().sort(function(a, b) { return a - b; });
   let n = sorted.length;
   let k = Math.floor(n * 0.25);
   let trimmed = sorted.slice(k, n - k);
   if (trimmed.length === 0)
      trimmed = sorted; // fallback for very few values in this quadrant
   let sum = 0;
   for (let i = 0; i < trimmed.length; ++i)
      sum += trimmed[i];
   return sum / trimmed.length;
}

// Splits the stars into 4 quadrants around the image center and computes
// the trimmed mean FWHM per quadrant (Siril: m1..m4). Also, like Siril, an
// inner circle (25% of the half-diagonal radius) vs. an outer ring (>75%)
// for the "off-axis aberration" (a field-curvature/coma-like measure,
// independent of the quadrant tilt). Returns null if a quadrant is empty
// (like Siril's compute_tilt_values()).
function computeSirilTilt(stars, w, h) {
   let cx = w / 2, cy = h / 2;
   let quadrants = [[], [], [], []]; // 0=TL, 1=TR, 2=BL, 3=BR
   let inner = [], outer = [];
   let r = Math.sqrt(cx * cx + cy * cy);
   let r1 = 0.25 * r, r2 = 0.75 * r;

   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let meanFwhm = (s.fwhmX + s.fwhmY) * 0.5;
      let left = s.x < cx, top = s.y < cy;
      let qi = top ? (left ? 0 : 1) : (left ? 2 : 3);
      quadrants[qi].push(meanFwhm);

      let dx = s.x - cx, dy = s.y - cy;
      let d2 = dx * dx + dy * dy;
      if (d2 < r1 * r1)
         inner.push(meanFwhm);
      else if (d2 > r2 * r2)
         outer.push(meanFwhm);
   }

   if (quadrants[0].length === 0 || quadrants[1].length === 0 ||
       quadrants[2].length === 0 || quadrants[3].length === 0 ||
       inner.length === 0 || outer.length === 0)
      return null;

   return {
      m1: trimmedMean25(quadrants[0]), // TL
      m2: trimmedMean25(quadrants[1]), // TR
      m3: trimmedMean25(quadrants[2]), // BL
      m4: trimmedMean25(quadrants[3]), // BR
      mInner: trimmedMean25(inner),
      mOuter: trimmedMean25(outer)
   };
}

// -----------------------------------------------------------------------
// Finer-grained alternative to computeSirilTilt()'s 4 quadrants: splits
// the image into a gridSize x gridSize grid of cells and computes the
// 25%-trimmed mean FWHM ((fwhmX+fwhmY)/2, same measure as the quadrants)
// per cell. Unlike computeSirilTilt(), an empty cell does not invalidate
// the whole result - at gridSize=11 (121 cells) some sparsely populated
// or empty cells, especially near the corners, are expected; those are
// simply left out (null) rather than failing the whole grid. Each cell
// naturally gets far fewer stars than a quadrant (~1/(gridSize/2)² as
// many on average), so individual cell values are noisier than the 4
// quadrant means - this trades statistical robustness for spatial
// resolution.

// The FWHM values ((fwhmX+fwhmY)/2, px) of the stars per cell: a
// gridSize x gridSize array of arrays (row 0 = top, column 0 = left).
function fwhmGridBuckets(stars, w, h, gridSize) {
   let cellW = w / gridSize, cellH = h / gridSize;
   let buckets = [];
   for (let r = 0; r < gridSize; ++r) {
      let row = [];
      for (let c = 0; c < gridSize; ++c)
         row.push([]);
      buckets.push(row);
   }
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let col = Math.min(gridSize - 1, Math.max(0, Math.floor(s.x / cellW)));
      let row = Math.min(gridSize - 1, Math.max(0, Math.floor(s.y / cellH)));
      buckets[row][col].push((s.fwhmX + s.fwhmY) * 0.5);
   }
   return buckets;
}

// The value of a set of FWHM values - the 25%-trimmed mean - with the
// number of values and its standard error (px; null below 2 values). The
// scatter is estimated robustly as 1.4826 x MAD (robustSpread()) - like
// the trimmed mean, it ignores the odd bad fit - so se = 1.4826 x MAD / √n.
function fwhmCellStats(values) {
   let n = values.length;
   if (n === 0)
      return { value: null, n: 0, se: null };
   return { value: trimmedMean25(values), n: n, se: n < 2 ? null : robustSpread(values) / Math.sqrt(n) };
}

// -----------------------------------------------------------------------
// Assessment of the FWHM grid. Each cell is judged by its ratio to the
// image center, R = FWHM_cell / FWHM_center - a ratio, so that seeing,
// focal length and pixel size drop out:
//    R <= 1.10 good, <= 1.25 slight, <= 1.50 clear, > 1.50 strong
// but only where the difference is significant,
//    z = (FWHM_cell - FWHM_center) / √(se_cell² + se_center²) >= 2;
// a cell that cannot be told from the center (and is not sharp anyway) is
// "not significant", an uncertain cell (fewer than FWHM_GRID_MIN_STARS
// stars or a standard error above FWHM_GRID_MAX_REL_SE of its value) is
// not judged at all. The thresholds are rules of thumb, not a standard.
// The center is the inner 3 x 3 cells pooled. For the whole image:
//  - corners: ratio of each corner (its 2 x 2 cell block pooled) to the
//    center, and their median - the rotationally symmetric part, i.e.
//    field curvature / spacing;
//  - tilt: the diagonal (TL-BR or TR-BL) whose corners differ most,
//    |a - b| / ((a + b) / 2), with its significance |a - b| / √(se_a² + se_b²)
//    - the one-sided part, i.e. sensor tilt (< 5% unremarkable, 5-10%
//    slight, > 10% worth correcting);
//  - edge spread: (max - min) / median of the judged cells of the outer
//    ring - an uneven edge points to tilt or collimation.
const FWHM_GRID_SIZE = 11;
const FWHM_GRID_MIN_STARS = 5;
const FWHM_GRID_MAX_REL_SE = 0.10;
const FWHM_GRID_MIN_Z = 2;
const FWHM_RATIO_LIMITS = [1.10, 1.25, 1.50];
const FWHM_RATIO_NAMES = ["good", "slight", "clear", "strong"];
const FWHM_TILT_LIMITS = [0.05, 0.10];
const FWHM_TILT_NAMES = ["unremarkable", "slight", "worth correcting"];
const FWHM_CORNER_NAMES = ["top left", "top right", "bottom left", "bottom right"];

// 0 good ... 3 strong.
function fwhmRatioClass(ratio) {
   let k = 0;
   while (k < FWHM_RATIO_LIMITS.length && ratio > FWHM_RATIO_LIMITS[k])
      ++k;
   return k;
}

// Returns { gridSize, center, cells, corners, tilt, edgeSpread, classCounts }:
// center { value, n, se } (null without stars in the center); cells a
// gridSize x gridSize array of { value, n, se, uncertain, ratio, z, cls }
// with cls -1 = not judged (empty, uncertain or not significant) or 0..3
// (ratio is null for empty cells and without a center, z for uncertain ones);
// corners null or { stats[4], ratios[4] (TL, TR, BL, BR), ratio (median),
// cls }; tilt null or { rel, z, significant, cls, soft, sharp (corner
// indices) }; edgeSpread null or a fraction; classCounts { good, slight,
// clear, strong, notSignificant, uncertain }.
function evaluateFwhmGrid(stars, w, h, gridSize) {
   gridSize = gridSize || FWHM_GRID_SIZE;
   let buckets = fwhmGridBuckets(stars, w, h, gridSize);
   let pool = function(r0, r1, c0, c1) {
      let out = [];
      for (let r = r0; r <= r1; ++r)
         for (let c = c0; c <= c1; ++c)
            out = out.concat(buckets[r][c]);
      return fwhmCellStats(out);
   };
   let mid = gridSize >> 1;
   let center = pool(mid - 1, mid + 1, mid - 1, mid + 1);
   if (center.value === null || center.se === null)
      center = null;

   let counts = { good: 0, slight: 0, clear: 0, strong: 0, notSignificant: 0, uncertain: 0 };
   let cells = buckets.map(function(row) {
      return row.map(function(b) {
         let e = fwhmCellStats(b);
         e.uncertain = e.n < FWHM_GRID_MIN_STARS || e.se === null || e.se > FWHM_GRID_MAX_REL_SE * e.value;
         e.ratio = null;
         e.z = null;
         e.cls = -1;
         if (e.value === null)
            return e;
         if (center !== null)
            e.ratio = e.value / center.value; // shown for uncertain cells too, in brackets
         if (e.uncertain) {
            ++counts.uncertain;
            return e;
         }
         if (center === null)
            return e;
         e.z = (e.value - center.value) / Math.sqrt(e.se * e.se + center.se * center.se);
         let cls = fwhmRatioClass(e.ratio);
         if (cls === 0 || e.z >= FWHM_GRID_MIN_Z) {
            e.cls = cls;
            ++counts[FWHM_RATIO_NAMES[cls]];
         } else {
            ++counts.notSignificant;
         }
         return e;
      });
   });

   // Corners: 2 x 2 cell blocks at 11 x 11 (about a fifth of each side).
   let corners = null, tilt = null;
   let k = Math.max(1, Math.round(gridSize / 5)), last = gridSize - 1;
   let cs = [pool(0, k - 1, 0, k - 1), pool(0, k - 1, last - k + 1, last),
             pool(last - k + 1, last, 0, k - 1), pool(last - k + 1, last, last - k + 1, last)];
   if (cs.every(function(s) { return s.value !== null && s.se !== null; })) {
      if (center !== null) {
         let ratios = cs.map(function(s) { return s.value / center.value; });
         let ratio = medianOf(ratios);
         corners = { stats: cs, ratios: ratios, ratio: ratio, cls: fwhmRatioClass(ratio) };
      }
      // Diagonals: TL (0) - BR (3), TR (1) - BL (2).
      [[0, 3], [1, 2]].forEach(function(d) {
         let a = cs[d[0]], b = cs[d[1]];
         let rel = Math.abs(a.value - b.value) / (0.5 * (a.value + b.value));
         if (tilt === null || rel > tilt.rel) {
            let z = Math.abs(a.value - b.value) / Math.sqrt(a.se * a.se + b.se * b.se);
            let soft = a.value >= b.value ? d[0] : d[1];
            let cls = rel < FWHM_TILT_LIMITS[0] ? 0 : rel < FWHM_TILT_LIMITS[1] ? 1 : 2;
            tilt = { rel: rel, z: z, significant: z >= FWHM_GRID_MIN_Z, cls: cls,
                     soft: soft, sharp: soft === d[0] ? d[1] : d[0] };
         }
      });
   }

   // Edge spread over the judged cells of the outer ring.
   let edge = [];
   for (let r = 0; r < gridSize; ++r)
      for (let c = 0; c < gridSize; ++c)
         if ((r === 0 || r === last || c === 0 || c === last) && cells[r][c].value !== null &&
             !cells[r][c].uncertain)
            edge.push(cells[r][c].value);
   let edgeSpread = edge.length >= 4 ?
      (Math.max.apply(null, edge) - Math.min.apply(null, edge)) / medianOf(edge) : null;

   return { gridSize: gridSize, center: center, cells: cells, corners: corners, tilt: tilt,
            edgeSpread: edgeSpread, classCounts: counts };
}

// One line each for the console: the center, corners, tilt and edge of
// evaluateFwhmGrid() (FWHM in px).
function formatFwhmGridEvaluation(ev, scale, unit) {
   scale = scale || 1;
   unit = unit || "px";
   let fw = function(v) { return format("%.2f", v * scale) + (unit === "px" ? " px" : unit); };
   let lines = [];
   if (ev.center === null)
      return ["Center: no stars - not judged."];
   lines.push(format("Center (inner 3x3 cells): x1.00 (%s ± %s, n=%d)", fw(ev.center.value), fw(ev.center.se),
      ev.center.n));
   if (ev.corners !== null) {
      lines.push(format("Corners: x%.2f the center (median) -> %s.", ev.corners.ratio,
         FWHM_RATIO_NAMES[ev.corners.cls]));
      // One indented line per corner.
      ev.corners.ratios.forEach(function(r, i) {
         lines.push(format("   %s: x%.2f (%s ± %s, n=%d)", FWHM_CORNER_NAMES[i].charAt(0).toUpperCase() +
            FWHM_CORNER_NAMES[i].slice(1), r, fw(ev.corners.stats[i].value), fw(ev.corners.stats[i].se),
            ev.corners.stats[i].n));
      });
   } else {
      lines.push("Corners: too few stars - not judged.");
   }
   if (ev.tilt !== null)
      lines.push(format("Tilt: %s %.0f%% softer than %s (%.1fσ) -> %s.",
         FWHM_CORNER_NAMES[ev.tilt.soft], 100 * ev.tilt.rel, FWHM_CORNER_NAMES[ev.tilt.sharp], ev.tilt.z,
         ev.tilt.significant ? FWHM_TILT_NAMES[ev.tilt.cls] : "not significant"));
   if (ev.edgeSpread !== null)
      lines.push(format("Edge spread: %.0f%% between the softest and the sharpest edge cell.",
         100 * ev.edgeSpread));
   let n = ev.classCounts;
   lines.push(format("Cells: %d good, %d slight, %d clear, %d strong, %d not significant, %d uncertain " +
      "(R = FWHM/center: <= %.2f / %.2f / %.2f / above; significant from %.0fσ).",
      n.good, n.slight, n.clear, n.strong, n.notSignificant, n.uncertain,
      FWHM_RATIO_LIMITS[0], FWHM_RATIO_LIMITS[1], FWHM_RATIO_LIMITS[2], FWHM_GRID_MIN_Z));
   return lines;
}

// The fill of a cell of the FWHM grid for its ratio to the center, along
// the four classes: green up to ×1.10 (good, held flat so that it reads as
// green), yellow around ×1.17 (slight), orange around ×1.25 (clear), red
// from FWHM_FILL_RATIO_MAX on (strong). The opacity grows with the ratio,
// so that the stars stay visible in good cells.
const FWHM_FILL_RATIO_MAX = 1.5;
const FWHM_FILL_STOPS = [[1.00, 70, 215, 95], [1.10, 70, 215, 95], [1.17, 255, 228, 90], [1.25, 255, 160, 40],
                         [1.50, 214, 38, 30]]; // ratio, r, g, b
function fwhmFillColor(ratio) {
   let r = Math.range(ratio, FWHM_FILL_STOPS[0][0], FWHM_FILL_RATIO_MAX);
   let i = 0;
   while (i < FWHM_FILL_STOPS.length - 2 && r > FWHM_FILL_STOPS[i + 1][0])
      ++i;
   let a = FWHM_FILL_STOPS[i], b = FWHM_FILL_STOPS[i + 1];
   let u = (b[0] > a[0]) ? (r - a[0]) / (b[0] - a[0]) : 0;
   let mix = function(k) { return Math.round(a[k] + (b[k] - a[k]) * u); };
   let t = (r - 1) / (FWHM_FILL_RATIO_MAX - 1);
   let alpha = Math.round(150 + 75 * t);
   return ((alpha << 24) | (mix(1) << 16) | (mix(2) << 8) | mix(3)) >>> 0;
}

const FWHM_TEXT_COLOR = 0xFFFFFFFF;
const FWHM_DETAIL_COLOR = 0xFFD8D8D8;
const FWHM_UNCERTAIN_HATCH = 0x90B0B0B0;

// Draws the grid of evaluateFwhmGrid(). Each cell shows, as selected in
// `show` (one of them), its FWHM in pixels (3.15 px), in arcseconds
// (2.07") or its ratio to the center (×1.17), large, and with details the
// standard error and the star count smaller below.
// The fill follows the ratio (fwhmFillColor()), but only where the cell
// differs significantly from the center (or is good anyway); cells that
// cannot be told from the center stay unfilled. An uncertain cell (too few
// stars or a large standard error) is hatched gray, its first value in
// brackets. show: { px, arcsecScale (arcsec/px, 0 = not shown), ratio,
// details, detailScale, detailUnit }. The legend of the fill and the
// summary (center, corners, tilt) are on the Star size page of the dialog.
function drawFwhmGrid(g, w, h, ev, show) {
   let gridSize = ev.gridSize;
   let cellW = w / gridSize, cellH = h / gridSize;
   g.antialiasing = true;

   // Fills first, the grid lines on top.
   for (let r = 0; r < gridSize; ++r) {
      for (let c = 0; c < gridSize; ++c) {
         let e = ev.cells[r][c];
         let x0 = c * cellW, y0 = r * cellH;
         if (e.value === null)
            continue;
         if (e.uncertain)
            g.fillRect(x0, y0, x0 + cellW, y0 + cellH, new Brush(FWHM_UNCERTAIN_HATCH, BrushStyle.BackwardDiagonalHatch));
         else if (e.cls >= 0 && e.ratio !== null)
            g.fillRect(x0, y0, x0 + cellW, y0 + cellH, new Brush(fwhmFillColor(e.ratio)));
      }
   }
   drawCellGrid(g, w, h, gridSize);

   let fontSize = Math.max(12, Math.round(Math.min(cellW, cellH) * 0.24));
   let font = new Font("Helvetica", fontSize);
   try { font.bold = true; } catch (e) { /* not critical */ }
   let smallSize = Math.max(9, Math.round(fontSize * 0.55));
   let smallFont = new Font("Helvetica", smallSize);

   for (let r = 0; r < gridSize; ++r) {
      for (let c = 0; c < gridSize; ++c) {
         let e = ev.cells[r][c];
         if (e.value === null)
            continue;
         let lines = [];
         if (show.px)
            lines.push(format("%.2f px", e.value));
         if (show.arcsecScale > 0)
            lines.push(format("%.2f\"", e.value * show.arcsecScale));
         if (show.ratio)
            lines.push(e.ratio !== null ? format("\u00D7%.2f", e.ratio) :
               format("%.2f px", e.value)); // no center: the FWHM instead
         if (lines.length > 0 && e.uncertain)
            lines[0] = "(" + lines[0] + ")";
         if (show.details)
            lines.push((e.se !== null ? format("\u00B1%.2f%s, ", e.se * show.detailScale,
               show.detailUnit === "px" ? " px" : show.detailUnit) : "") + "n=" + e.n);
         if (lines.length === 0)
            continue;
         // The first line large, the others smaller below; all centered together.
         let ccx = (c + 0.5) * cellW, ccy = (r + 0.5) * cellH;
         let labelY = ccy + fontSize / 3 - (lines.length - 1) * smallSize * 0.7;
         g.font = font;
         drawTextWithHalo(g, ccx - g.font.width(lines[0]) / 2, labelY, lines[0], FWHM_TEXT_COLOR, fontSize);
         g.font = smallFont;
         for (let k = 1; k < lines.length; ++k)
            drawTextWithHalo(g, ccx - g.font.width(lines[k]) / 2, labelY + smallSize * 1.4 * k,
               lines[k], FWHM_DETAIL_COLOR, smallSize);
      }
   }
   g.font = font;
}

// The lines of the gridSize x gridSize cell grid (the FWHM grid and the
// shape cells), with a frame around the inner 3x3 cells - the center the
// FWHM ratios and the shape center refer to.
function drawCellGrid(g, w, h, gridSize) {
   let cellW = w / gridSize, cellH = h / gridSize;
   let lineColor = 0xFFFFCCB2; // same salmon-orange as the Siril quadrant overlay
   g.antialiasing = true;
   g.pen = new Pen(0xB0000000, 3);
   for (let i = 0; i <= gridSize; ++i) {
      g.drawLine(i * cellW, 0, i * cellW, h);
      g.drawLine(0, i * cellH, w, i * cellH);
   }
   g.pen = new Pen(lineColor, 1);
   for (let i = 0; i <= gridSize; ++i) {
      g.drawLine(i * cellW, 0, i * cellW, h);
      g.drawLine(0, i * cellH, w, i * cellH);
   }
   let mid = gridSize >> 1;
   let fx0 = (mid - 1) * cellW, fy0 = (mid - 1) * cellH, fx1 = (mid + 2) * cellW, fy1 = (mid + 2) * cellH;
   let frameWidth = Math.max(2, Math.round(Math.min(cellW, cellH) * 0.012));
   g.pen = new Pen(0xC0000000, frameWidth + 2);
   g.drawRect(fx0, fy0, fx1, fy1);
   g.pen = new Pen(0xFFFFFFFF, frameWidth);
   g.drawRect(fx0, fy0, fx1, fy1);
}

// An ARGB color laid opaquely over the background `bg` (RGB), as it looks
// on the dark sky of the map - for the legend in the dialog.
function opaqueOver(argb, bg) {
   let a = ((argb >>> 24) & 0xFF) / 255;
   let mix = function(shift) {
      return Math.round(((argb >>> shift) & 0xFF) * a + ((bg >>> shift) & 0xFF) * (1 - a));
   };
   return (0xFF000000 | (mix(16) << 16) | (mix(8) << 8) | mix(0)) >>> 0;
}

// Draws `text` at (x,y) using the current g.font, with a dark halo behind
// it (offset copies in several directions) so it stays readable against
// any background, including the fully saturated orientation heatmap.
// `fontSize` should match the size g.font was set to - it controls how
// far the halo is offset.
function drawTextWithHalo(g, x, y, text, color, fontSize) {
   let offset = Math.max(2, Math.round(fontSize * 0.06));
   g.pen = new Pen(0xE0000000, 1);
   for (let dy = -offset; dy <= offset; dy += offset)
      for (let dx = -offset; dx <= offset; dx += offset)
         if (dx !== 0 || dy !== 0)
            g.drawText(x + dx, y + dy, text);
   g.pen = new Pen(color, 1);
   g.drawText(x, y, text);
}

// -----------------------------------------------------------------------
// Computes the tilt axis: the line through the sensor center along which
// the plane fitted to the 4 (center-relative) quadrant FWHM values
// rises/falls most steeply - same underlying plane as build3DTiltPlot(),
// just evaluated directly in image pixel space here instead of physical
// mm/µm. The axis DIRECTION needs no physical units at all: it only
// depends on the relative sign/magnitude of each quadrant's FWHM excess
// over the center (tilt.mInner), and that stays the same regardless of
// whether that excess is expressed in FWHM pixels or converted to a
// physical Δz (the conversion is a positive scalar, common to all 4
// quadrants, so it cannot change the fitted plane's direction - only its
// steepness). The axis MAGNITUDE (an actual angle in degrees) does need
// the optical parameters to convert FWHM excess into a physical Δz, via
// the same f-number-based circle-of-confusion approximation as
// computeAndLogTiltAngles()/build3DTiltPlot() - if pixelPitchUm,
// focalLengthMm or apertureMm are missing (<= 0), angleDeg is null.
// Returns { directionDeg: 0..180, angleDeg: number|null }.
function computeTiltAxis(tilt, w, h, pixelPitchUm, focalLengthMm, apertureMm) {
   function signedExcessPx(fwhm, centerFwhm) {
      let diff = fwhm * fwhm - centerFwhm * centerFwhm;
      return (diff < 0 ? -1 : 1) * Math.sqrt(Math.abs(diff));
   }
   let eTL = signedExcessPx(tilt.m1, tilt.mInner);
   let eTR = signedExcessPx(tilt.m2, tilt.mInner);
   let eBL = signedExcessPx(tilt.m3, tilt.mInner);
   let eBR = signedExcessPx(tilt.m4, tilt.mInner);

   let px = w / 4, py = h / 4;
   let gradX = (px > 0) ? ((eTR + eBR) - (eTL + eBL)) / (4 * px) : 0; // FWHM-px per image-px, x
   let gradY = (py > 0) ? ((eBL + eBR) - (eTL + eTR)) / (4 * py) : 0; // FWHM-px per image-px, y
   let gradMag = Math.hypot(gradX, gradY);

   // Direction as an undirected line (0°..180°, relative to the horizontal
   // image axis) - a line has no arrowhead/sign, see build3DTiltPlot().
   let directionDeg = (gradMag > 1e-9)
      ? (((Math.atan2(gradY, gradX) * 180 / Math.PI) % 180) + 180) % 180
      : 0;

   let angleDeg = null;
   if (pixelPitchUm > 0 && focalLengthMm > 0 && apertureMm > 0) {
      let fRatio = focalLengthMm / apertureMm;
      angleDeg = Math.atan(fRatio * gradMag) * 180 / Math.PI;
   }

   return { directionDeg: directionDeg, angleDeg: angleDeg };
}

// Draws the tilt axis (see computeTiltAxis()) as a line through the
// sensor center, extended to the image edges, labeled with the axis
// direction (always available) and, if known, the physical tilt angle.
// Uses the same light-blue axis color as build3DTiltPlot()'s tilt axis,
// for visual consistency between the 2D vector map and the separate 3D
// comparison plot.
function drawTiltAxis(g, w, h, axis) {
   let cx = w / 2, cy = h / 2;
   let rad = axis.directionDeg * Math.PI / 180;
   let dirX = Math.cos(rad), dirY = Math.sin(rad);
   let halfW = w / 2, halfH = h / 2;
   let tX = (Math.abs(dirX) > 1e-9) ? halfW / Math.abs(dirX) : Infinity;
   let tY = (Math.abs(dirY) > 1e-9) ? halfH / Math.abs(dirY) : Infinity;
   let t = Math.min(tX, tY);
   let p1 = { x: cx + t * dirX, y: cy + t * dirY };
   let p2 = { x: cx - t * dirX, y: cy - t * dirY };

   let axisColor = 0xFF66E0FF; // light blue, same as build3DTiltPlot()'s tilt axis

   g.antialiasing = true;
   g.pen = new Pen(0xB0000000, 9);
   g.drawLine(p1.x, p1.y, p2.x, p2.y);
   g.pen = new Pen(axisColor, 4);
   g.drawLine(p1.x, p1.y, p2.x, p2.y);

   let label = (axis.angleDeg !== null)
      ? format("Tilt axis: %.3f° (dir. %.0f°)", axis.angleDeg, axis.directionDeg)
      : format("Tilt axis direction: %.0f°", axis.directionDeg);

   let fontSize = Math.round(Math.max(28, Math.max(w, h) * 0.009));
   let font = new Font("Helvetica", fontSize);
   try { font.bold = true; } catch (e) { /* not critical */ }
   g.font = font;

   // Place the label near one end of the axis, clamped so it doesn't run
   // off the image edge.
   let labelX = Math.max(fontSize, Math.min(p1.x, w - fontSize - g.font.width(label)));
   let labelY = Math.max(fontSize * 2, Math.min(p1.y, h - fontSize));
   drawTextWithHalo(g, labelX, labelY, label, axisColor, fontSize);
}

// Draws Siril's tilt quadrilateral - modeled on the actual rendering code
// (src/gui-gtk4/image_display.c, draw_analysis()), but with two deliberate
// deviations from the original: the corner points sit fixed at the
// geometric center of their respective quadrant (±w/4, ±h/4 from the image
// center) instead of - as in Siril - being shifted along the diagonal
// proportionally to the FWHM deviation (this directly shows which image
// region each value comes from); additionally, the inner circle from which
// the center value (Siril's "fwhm_centre"/mInner) is computed is drawn as
// a circle outline. Otherwise as in the original: 4 outer edges + both
// diagonals (an X through the center), large bold labels per corner,
// center value.
function drawSirilTiltPolygon(g, w, h, tilt) {
   let cx = w / 2, cy = h / 2;

   // Corner points = geometric center of the respective quadrant (fixed,
   // independent of the FWHM value - shows the actual region of origin).
   let pTL = { x: cx - w / 4, y: cy - h / 4 };
   let pTR = { x: cx + w / 4, y: cy - h / 4 };
   let pBL = { x: cx - w / 4, y: cy + h / 4 };
   let pBR = { x: cx + w / 4, y: cy + h / 4 };

   // Inner-circle radius as in computeSirilTilt() (25% of the
   // half-diagonal radius) - bounds the region from which tilt.mInner was computed.
   let innerRadius = 0.25 * Math.sqrt(cx * cx + cy * cy);

   // Color as in Siril (image_display.c: RGB 1.0/0.8/0.7 -> light salmon-orange)
   let tiltColor = 0xFFFFCCB2;

   g.antialiasing = true;

   // 4 outer edges + both diagonals (an X through the center) - exact
   // Cairo path from draw_analysis(): TL-TR, BL-BR, TL-BL, BR-TR, TR-BL(diag.), BR-TL(diag.).
   // Same halo+core weight throughout (dark halo under a bright tiltColor
   // core) so the whole quadrilateral reads as consistently styled and
   // stands out against any background, including the orientation heatmap.
   g.pen = new Pen(0xB0000000, 15);
   g.drawLine(pTL.x, pTL.y, pTR.x, pTR.y);
   g.drawLine(pBL.x, pBL.y, pBR.x, pBR.y);
   g.drawLine(pTL.x, pTL.y, pBL.x, pBL.y);
   g.drawLine(pBR.x, pBR.y, pTR.x, pTR.y);
   g.drawLine(pTR.x, pTR.y, pBL.x, pBL.y);
   g.drawLine(pBR.x, pBR.y, pTL.x, pTL.y);
   g.pen = new Pen(tiltColor, 7);
   g.drawLine(pTL.x, pTL.y, pTR.x, pTR.y);
   g.drawLine(pBL.x, pBL.y, pBR.x, pBR.y);
   g.drawLine(pTL.x, pTL.y, pBL.x, pBL.y);
   g.drawLine(pBR.x, pBR.y, pTR.x, pTR.y);
   g.drawLine(pTR.x, pTR.y, pBL.x, pBL.y);
   g.drawLine(pBR.x, pBR.y, pTL.x, pTL.y);

   // Draw the inner-circle region for the center value.
   g.drawEllipse(cx - innerRadius, cy - innerRadius, cx + innerRadius, cy + innerRadius);

   // Labels - noticeably larger/bold than Siril's fixed 20px/zoom, scaled
   // proportionally to the image size, and drawn with a dark halo (offset
   // copies behind the actual color) so they stay readable against any
   // background, including the orientation heatmap.
   let fontSize = Math.round(Math.max(36, Math.max(w, h) * 0.012));
   let font = new Font("Helvetica", fontSize);
   try { font.bold = true; } catch (e) { /* not critical */ }
   g.font = font;

   drawTextWithHalo(g, pTL.x, pTL.y - fontSize, format("%.2f", tilt.m1), tiltColor, fontSize);
   drawTextWithHalo(g, pTR.x, pTR.y - fontSize, format("%.2f", tilt.m2), tiltColor, fontSize);
   drawTextWithHalo(g, pBL.x, pBL.y + fontSize, format("%.2f", tilt.m3), tiltColor, fontSize);
   drawTextWithHalo(g, pBR.x, pBR.y + fontSize, format("%.2f", tilt.m4), tiltColor, fontSize);

   // Center value as in Siril: trimmed mean of the inner circle (mInner),
   // centered below the image center.
   drawTextWithHalo(g, cx, cy + fontSize, format("%.2f", tilt.mInner), tiltColor, fontSize);

   g.pen = new Pen(tiltColor, 2);
   g.drawEllipse(cx - 3, cy - 3, cx + 3, cy + 3);
}

// -----------------------------------------------------------------------
// Converts the Siril-style quadrant FWHM differences (tilt) into an
// estimated tilt angle in degrees. Chain: excess blur (quadratic relative
// to the sensor center, since focus and defocus blur tend to add in
// quadrature rather than linearly) -> physical defocus via the circle-of-
// confusion approximation (Δz = f-number * blur) -> tilt angle via
// tan(θ) = Δz / distance between image center and quadrant center (±w/4, ±h/4).
// The reference (Δz=0) is the sensor center (tilt.mInner, trimmed FWHM
// mean within the inner circle around the image center) rather than one
// of the 4 corner quadrants - otherwise, in a tie/close call, the first
// quadrant (top-left) would always be artificially chosen as the
// reference, even though autofocus/collimation is usually optimized for
// the image center.
// Only a rough estimate: geometric optics, no diffraction/seeing
// deconvolution, the quadrant mean is assumed representative of its
// geometric center. Returns nothing, logs directly.
function computeAndLogTiltAngles(tilt, w, h, pixelPitchUm, focalLengthMm, apertureMm) {
   let fRatio = focalLengthMm / apertureMm;
   let centerFwhm = tilt.mInner;

   let quadrants = [
      { name: "Top-left",  fwhm: tilt.m1 },
      { name: "Top-right", fwhm: tilt.m2 },
      { name: "Bottom-left", fwhm: tilt.m3 },
      { name: "Bottom-right", fwhm: tilt.m4 }
   ];

   let physWidthMm = w * pixelPitchUm / 1000;
   let physHeightMm = h * pixelPitchUm / 1000;
   // Distance from image center to quadrant center is the same for all 4
   // quadrants (symmetry around the center).
   let distMm = Math.sqrt((physWidthMm / 4) * (physWidthMm / 4) + (physHeightMm / 4) * (physHeightMm / 4));

   Quiet.noteln(format("--- Tilt angle estimate (f/%.2f, %.2f µm/px, reference: sensor center, FWHM=%.2f px) ---",
      fRatio, pixelPitchUm, centerFwhm));

   for (let i = 0; i < quadrants.length; ++i) {
      let q = quadrants[i];
      if (q.fwhm <= centerFwhm) {
         Quiet.noteln(format("%s: no excess relative to sensor center (FWHM %.2f <= %.2f px).",
            q.name, q.fwhm, centerFwhm));
         continue;
      }

      let excessPx = Math.sqrt(q.fwhm * q.fwhm - centerFwhm * centerFwhm);
      let excessUm = excessPx * pixelPitchUm;
      let defocusUm = fRatio * excessUm;

      let angleDeg = (distMm > 0) ? Math.atan((defocusUm / 1000) / distMm) * 180 / Math.PI : 0;

      Quiet.noteln(format("%s: FWHM=%.2f px, excess=%.2f px (%.1f µm), defocus≈%.1f µm, " +
         "distance=%.2f mm -> tilt angle≈%.3f°", q.name, q.fwhm, excessPx, excessUm, defocusUm, distMm, angleDeg));
   }
   Quiet.noteln("--- end (rough estimate - single frame, no calibration) ---");
}

// -----------------------------------------------------------------------
// Computes a SIGNED Δz estimate in µm from a quadrant's excess blur
// relative to the sensor center (same chain as computeAndLogTiltAngles:
// excess blur -> circle-of-confusion approximation Δz = f-number * blur;
// but here not clamped to 0, instead given the sign of fwhm-centerFwhm).
// The sign itself is physically NOT reliable - nearer/farther from the
// corrector cannot be determined from a single exposure (see the note at
// build3DTiltPlot). However, for the DIRECTION of a plane fitted to the 4
// quadrants (the tilt axis), only the relative magnitude between the
// quadrants matters: a uniform sign flip would not change the computed
// axis, only swap "up"/"down".
function signedDefocusUm(fwhm, centerFwhm, fRatio, pixelPitchUm) {
   let diff = fwhm * fwhm - centerFwhm * centerFwhm;
   let sign = (diff < 0) ? -1 : 1;
   return sign * fRatio * Math.sqrt(Math.abs(diff)) * pixelPitchUm;
}

// -----------------------------------------------------------------------
// Pseudo-3D comparison plot: a perfect (flat) sensor plane vs. the tilted
// plane computed from the quadrant Δz values. PJSR has no real 3D graphics
// library (unlike e.g. matplotlib in Siril's Distortion3D.py) - this is a
// classic isometric projection (2D lines that look like 3D), not a
// rotatable true 3D view.
//
// Instead of the two geometric corner-to-corner diagonals (which have
// nothing to do with the actual tilt direction), the tilt axis is drawn:
// the plane z = gradX*x + gradY*y fitted to the 4 (signed) quadrant Δz
// values is extended along its direction of steepest ascent/descent
// through the sensor center out to the image edge - this is the line that
// actually matters when adjusting two opposing tilt screws. The
// corresponding tilt angle is labeled at both ends, as well as at each of
// the 4 corners.
//
// IMPORTANT (physical limitation): Δz comes from the circle-of-confusion
// size (Δz = f-number * excess blur) and is therefore only a MAGNITUDE.
// Whether a quadrant is CLOSER to or FARTHER from the corrector than the
// sensor center (reference) cannot be determined from a single exposure
// without a focus sweep (multiple exposures at slightly shifted focus).
// Rather than hiding this indeterminacy behind a uniform "upward" choice,
// TWO mirror-image, equally plausible tilt planes are therefore drawn (in
// 2 colors): variant 1 with the sign computed from the quadrants, variant
// 2 exactly mirrored (all heights negated). Since the fitted plane passes
// linearly through the sensor center and TR/BL as well as TL/BR are
// exactly opposite positions, planeZ(BL)=-planeZ(TR) and
// planeZ(BR)=-planeZ(TL) hold automatically - e.g. if TR is positive, BL
// is correspondingly negative. The tilt axis is independent of this
// mirroring (it passes through the same sensor center at z=0 for both
// variants) and is drawn with its actual slope, including negative values.
function build3DTiltPlot(tilt, sensorWpx, sensorHpx, pixelPitchUm, focalLengthMm, apertureMm) {
   let bmp = render3DTiltPlot(tilt, sensorWpx, sensorHpx, pixelPitchUm, focalLengthMm, apertureMm);
   let window = new ImageWindow(bmp.width, bmp.height, 3, 8, false, true, uniqueViewId("Aberration3D"));
   window.mainView.beginProcess(UndoFlag.NoSwapFile);
   window.mainView.image.blend(bmp);
   window.mainView.endProcess();
   window.show();
   return window;
}

// The 3D plot of build3DTiltPlot() as a bitmap (the series analysis saves
// it without opening a window).
function render3DTiltPlot(tilt, sensorWpx, sensorHpx, pixelPitchUm, focalLengthMm, apertureMm) {
   let fRatio = focalLengthMm / apertureMm;

   let quadrants = [
      { name: "TL", fwhm: tilt.m1, ux: -1, uy: -1 },
      { name: "TR", fwhm: tilt.m2, ux:  1, uy: -1 },
      { name: "BL", fwhm: tilt.m3, ux: -1, uy:  1 },
      { name: "BR", fwhm: tilt.m4, ux:  1, uy:  1 }
   ];

   // The reference (Δz=0) is the sensor center (tilt.mInner) rather than
   // one of the 4 corner quadrants, see the comment at computeAndLogTiltAngles().
   let centerFwhm = tilt.mInner;

   let physWidthMm = sensorWpx * pixelPitchUm / 1000;
   let physHeightMm = sensorHpx * pixelPitchUm / 1000;
   // Distance from image center to quadrant center, for the corner tilt
   // angles (as in computeAndLogTiltAngles(), called "distMm" there).
   let cornerDistMm = Math.sqrt((physWidthMm / 4) * (physWidthMm / 4) + (physHeightMm / 4) * (physHeightMm / 4));

   for (let i = 0; i < quadrants.length; ++i) {
      let q = quadrants[i];
      q.signedDzUm = signedDefocusUm(q.fwhm, centerFwhm, fRatio, pixelPitchUm);
      q.xMm = q.ux * physWidthMm / 4;
      q.yMm = q.uy * physHeightMm / 4;
   }

   // --- Tilt axis/plane: the plane z = gradX*x + gradY*y fitted to the 4
   // signed Δz values through the sensor center (z(0,0)=0). The 4 quadrant
   // centers lie symmetrically at (±px,±py) - the x and y components are
   // therefore orthogonal, and the least-squares fit reduces to the scaled
   // difference of the right/left resp. bottom/top means (derivation:
   // sum(x_i*z_i)/sum(x_i²) etc.).
   let px = physWidthMm / 4, py = physHeightMm / 4;
   let gradX = (px > 0) // TR+BR - (TL+BL), µm per mm in the x direction
      ? ((quadrants[1].signedDzUm + quadrants[3].signedDzUm) - (quadrants[0].signedDzUm + quadrants[2].signedDzUm)) / (4 * px)
      : 0;
   let gradY = (py > 0) // BL+BR - (TL+TR), µm per mm in the y direction
      ? ((quadrants[2].signedDzUm + quadrants[3].signedDzUm) - (quadrants[0].signedDzUm + quadrants[1].signedDzUm)) / (4 * py)
      : 0;
   let gradMagUmPerMm = Math.sqrt(gradX * gradX + gradY * gradY);
   let axisAngleDeg = Math.atan(gradMagUmPerMm / 1000) * 180 / Math.PI;
   // Direction of the axis as an undirected line (0°..180°, relative to
   // the horizontal image axis) - a line has no arrowhead/sign.
   let axisDirectionDeg = (gradMagUmPerMm > 1e-9)
      ? (((Math.atan2(gradY, gradX) * 180 / Math.PI) % 180) + 180) % 180
      : 0;

   // Corner heights of the fitted plane at the 4 quadrant centers - these
   // (not the raw, independently measured individual values) are drawn as
   // the "tilted rectangle", because only a plane automatically satisfies
   // the required diagonal symmetry: TR/BL and TL/BR sit at exactly
   // opposite (negated) positions, so for any linear plane through the
   // origin planeZ(BL)=-planeZ(TR) and planeZ(BR)=-planeZ(TL) always hold
   // - if TR is positive, BL is automatically negative.
   let maxDz = 0;
   for (let i = 0; i < quadrants.length; ++i) {
      let q = quadrants[i];
      q.planeZUm = gradX * q.xMm + gradY * q.yMm;
      q.angleDeg = (cornerDistMm > 0) ? Math.atan((Math.abs(q.planeZUm) / 1000) / cornerDistMm) * 180 / Math.PI : 0;
      maxDz = Math.max(maxDz, Math.abs(q.planeZUm));
   }

   // Axis through the sensor center at z=0 - by construction the fitted
   // plane always passes exactly through (0,0,0), regardless of the
   // (in any case unknown) sign variant.
   let axisP1 = { xMm: 0, yMm: 0, zUm: 0 };
   let axisP2 = { xMm: 0, yMm: 0, zUm: 0 };
   if (gradMagUmPerMm > 1e-9) {
      let dirX = gradX / gradMagUmPerMm, dirY = gradY / gradMagUmPerMm;
      let halfW = physWidthMm / 2, halfH = physHeightMm / 2;
      let tX = (Math.abs(dirX) > 1e-9) ? halfW / Math.abs(dirX) : Infinity;
      let tY = (Math.abs(dirY) > 1e-9) ? halfH / Math.abs(dirY) : Infinity;
      let t = Math.min(tX, tY);
      axisP1 = { xMm: t * dirX, yMm: t * dirY, zUm: t * gradMagUmPerMm };
      axisP2 = { xMm: -t * dirX, yMm: -t * dirY, zUm: -t * gradMagUmPerMm };
   }
   maxDz = Math.max(maxDz, Math.abs(axisP1.zUm), Math.abs(axisP2.zUm));
   if (maxDz <= 0)
      maxDz = 1; // avoid division by 0 if everything is identical

   // --- Prepare canvas & isometric projection ---
   let cw = 1000, ch = 750;
   let textMargin = 12;
   let backgroundColor = 0xFF1A1A1A; // near black: canvas background
   let bmp = new Bitmap(cw, ch);
   bmp.fill(backgroundColor);
   let g = new Graphics(bmp);
   g.antialiasing = true;

   let originX = cw / 2;
   let originY = ch * 0.58;
   let scale = (cw * 0.32) / (physWidthMm / 2); // mm -> px
   let cos30 = Math.cos(30 * Math.PI / 180);
   let sin30 = Math.sin(30 * Math.PI / 180);
   // Height exaggerated to ~22% of the smaller sensor extent on screen,
   // otherwise the µm height difference would be invisible next to the mm distances.
   let zExagPxPerUm = (Math.min(physWidthMm, physHeightMm) * scale * 0.22) / maxDz;

   function project(xMm, yMm, zUm) {
      return {
         x: originX + (xMm - yMm) * cos30 * scale,
         y: originY + (xMm + yMm) * sin30 * scale - zUm * zExagPxPerUm
      };
   }

   // Prevents labels from being cut off at the left/right canvas edge:
   // shifts the text inward as needed, instead of drawing it at a fixed
   // position (which can overshoot the edge for corners near the image
   // border).
   function drawTextClamped(x, y, text) {
      let textW = g.font.width(text);
      let clampedX = Math.max(textMargin, Math.min(x, cw - textMargin - textW));
      g.drawText(clampedX, y, text);
   }

   // Wraps text at word boundaries so each line fits within maxWidth
   // (current g.font) - complements drawTextClamped() (which only shifts,
   // but does not wrap) for legend texts whose length varies depending on
   // the numeric values/word choice and would otherwise be cut off at the edge.
   function wrapText(text, maxWidth) {
      let words = text.split(" ");
      let lines = [];
      let current = "";
      for (let i = 0; i < words.length; ++i) {
         let candidate = current.length ? (current + " " + words[i]) : words[i];
         if (current.length && g.font.width(candidate) > maxWidth) {
            lines.push(current);
            current = words[i];
         } else {
            current = candidate;
         }
      }
      if (current.length)
         lines.push(current);
      return lines;
   }

   // Draws text over multiple lines if needed (wrapped to the canvas
   // width) and returns the y position for the next line/text block.
   function drawTextBlock(x, y, text, lineHeight) {
      let lines = wrapText(text, cw - 2 * textMargin);
      for (let i = 0; i < lines.length; ++i)
         drawTextClamped(x, y + i * lineHeight, lines[i]);
      return y + lines.length * lineHeight;
   }

   let order = [0, 1, 3, 2]; // TL,TR,BR,BL - correct winding order of the rectangle

   // --- Colors (one definition per line) ---
   let flatColor = 0xFF808080;      // gray: flat reference plane
   let tiltColorA = 0xFFFFCCB2;     // salmon: variant 1 (computed sign)
   let tiltColorB = 0xFF9CFFA0;     // light green: variant 2 (mirrored)
   let axisColor = 0xFF66E0FF;      // light blue: tilt axis
   let connectorColor = 0xFF555555; // dark gray: connector lines flat -> tilted
   let legendColor = 0xFFCCCCCC;    // light gray: legend/note text

   // --- Flat reference plane (perfect, untilted sensor) ---
   let flatCorners = quadrants.map(function(q) { return project(q.xMm, q.yMm, 0); });
   g.pen = new Pen(flatColor, 1);
   for (let i = 0; i < 4; ++i) {
      let p1 = flatCorners[order[i]], p2 = flatCorners[order[(i + 1) % 4]];
      g.drawLine(p1.x, p1.y, p2.x, p2.y);
   }
   let flatCenter = project(0, 0, 0);
   g.drawEllipse(flatCenter.x - 3, flatCenter.y - 3, flatCenter.x + 3, flatCenter.y + 3);

   // --- Computed tilt plane: 2 mirror-image, equally plausible sign
   // variants (see the note at the start of the function) in 2 colors ---
   let tiltCornersA = quadrants.map(function(q) { return project(q.xMm, q.yMm, q.planeZUm); });
   let tiltCornersB = quadrants.map(function(q) { return project(q.xMm, q.yMm, -q.planeZUm); });
   g.pen = new Pen(tiltColorA, 3);
   for (let i = 0; i < 4; ++i) {
      let p1 = tiltCornersA[order[i]], p2 = tiltCornersA[order[(i + 1) % 4]];
      g.drawLine(p1.x, p1.y, p2.x, p2.y);
   }
   g.pen = new Pen(tiltColorB, 3);
   for (let i = 0; i < 4; ++i) {
      let p1 = tiltCornersB[order[i]], p2 = tiltCornersB[order[(i + 1) % 4]];
      g.drawLine(p1.x, p1.y, p2.x, p2.y);
   }

   // --- Tilt axis (instead of the two corner-to-corner diagonals) ---
   let axisPt1 = project(axisP1.xMm, axisP1.yMm, axisP1.zUm);
   let axisPt2 = project(axisP2.xMm, axisP2.yMm, axisP2.zUm);
   g.pen = new Pen(axisColor, 3);
   g.drawLine(axisPt1.x, axisPt1.y, axisPt2.x, axisPt2.y);

   // --- Connector lines (show the height difference) + corner labels ---
   let fontSize = 16;
   g.font = new Font("Helvetica", fontSize);
   for (let i = 0; i < 4; ++i) {
      let q = quadrants[i];
      let flat = flatCorners[i];
      let tpA = tiltCornersA[i];
      let tpB = tiltCornersB[i];

      g.pen = new Pen(connectorColor, 1);
      g.drawLine(flat.x, flat.y, tpA.x, tpA.y);
      g.drawLine(flat.x, flat.y, tpB.x, tpB.y);

      g.pen = new Pen(tiltColorA, 2);
      g.drawEllipse(tpA.x - 4, tpA.y - 4, tpA.x + 4, tpA.y + 4);
      g.pen = new Pen(tiltColorB, 2);
      g.drawEllipse(tpB.x - 4, tpB.y - 4, tpB.x + 4, tpB.y + 4);

      // One label per corner is enough (magnitude/angle are identical for
      // both variants, just mirrored) - placed at variant 1. "±" instead
      // of a fixed sign, because both signs are actually drawn at this
      // corner (variant 1 + variant 2), and a single "+"/"-" would falsely
      // suggest the sign is known (see the note at the start of the function).
      // Right-hand corner quadrants (ux>0) sit close to the right image
      // edge - place their label to the left instead of to the right
      // (in addition to the general clamp safeguard below).
      let label = format("%s: ±%.1f µm (%.3f°)", q.name, Math.abs(q.planeZUm), q.angleDeg);
      let labelX = (q.ux > 0) ? (tpA.x - 8 - g.font.width(label)) : (tpA.x + 8);
      drawTextClamped(labelX, tpA.y - 8, label);
   }

   // No label directly at the axis in the plot itself: depending on the
   // tilt direction, axisPt1 can end up arbitrarily close to a corner
   // label (where the text would overlap it and become unreadable) - the
   // tilt angle/direction is instead shown below as its own,
   // collision-free legend line in the axis color.

   // --- Legend / note text (multi-line, wrapped to the canvas width
   // instead of a fixed line count/length, so nothing gets cut off) ---
   let legendY = 30;
   g.pen = new Pen(legendColor, 1);
   g.font = new Font("Helvetica", 18);
   legendY = drawTextBlock(20, legendY, "Gray = flat sensor plane", 24);
   legendY = drawTextBlock(20, legendY, "Salmon/green = 2 mirror-image tilt variants", 24);
   legendY = drawTextBlock(20, legendY, "Light blue = tilt axis", 24) + 6;
   g.font = new Font("Helvetica", 15);
   legendY = drawTextBlock(20, legendY, format(
      "f/%.2f, %.2f µm/px, reference: sensor center (FWHM=%.2f px, assumed in focus)",
      fRatio, pixelPitchUm, centerFwhm), 20);
   g.pen = new Pen(axisColor, 1);
   legendY = drawTextBlock(20, legendY, format(
      "Tilt axis: %.3f° (direction %.0f° from the horizontal image axis)",
      axisAngleDeg, axisDirectionDeg), 20) + 4;
   g.pen = new Pen(legendColor, 1);
   drawTextBlock(20, legendY,
      "Note: Δz follows only from the blur (a magnitude) - whether a quadrant is closer to or farther " +
      "from the corrector is unknown without a focus sweep; both sign variants are equally plausible.",
      20);

   g.end();
   return bmp;
}

// -----------------------------------------------------------------------
// Streamlines: smoothed large-scale trend of the PSF orientation, in
// addition to the (noisy) per-star vectors.
//
// IMPORTANT: theta is only defined mod 180° (an ellipse at theta and
// theta+180° is identical) - this is a "director field", not a vector
// field with a well-defined sign. Naive angle averaging would, e.g. for
// 10° and 170° (almost identical orientation), incorrectly yield ~90°.
// Standard trick (double-angle representation, as used with structure
// tensors/directional statistics): double the angle, average in Cartesian
// coordinates, then halve again - correctly maps the 180° ambiguity onto
// a 360° quantity.

// The stars within `radius` of (x, y) and some more: those in the 3x3
// buckets (of the size of the radius) around it. The buckets are built
// once per star list and radius and kept with the list (a WeakMap), so the
// smoothing of the streamlines no longer runs over every star at every
// step - which pooled series data (tens of thousands of stars) needs.
const STAR_INDEX = new WeakMap();
function nearbyStars(stars, x, y, radius) {
   let idx = STAR_INDEX.get(stars);
   if (!idx || idx.radius !== radius) {
      idx = { radius: radius, buckets: new Map() };
      for (let i = 0; i < stars.length; ++i) {
         let key = Math.floor(stars[i].x / radius) + "," + Math.floor(stars[i].y / radius);
         let list = idx.buckets.get(key);
         if (!list)
            idx.buckets.set(key, list = []);
         list.push(stars[i]);
      }
      STAR_INDEX.set(stars, idx);
   }
   let bi = Math.floor(x / radius), bj = Math.floor(y / radius);
   let out = [];
   for (let dj = -1; dj <= 1; ++dj)
      for (let di = -1; di <= 1; ++di) {
         let list = idx.buckets.get((bi + di) + "," + (bj + dj));
         if (list)
            for (let k = 0; k < list.length; ++k)
               out.push(list[k]);
      }
   return out;
}

// Smooths the orientation field at point (x,y): weighted double-angle
// mean of all stars within the radius (Gaussian kernel, weight
// additionally scaled by eccentricity^2 - nearly round stars have a
// barely defined orientation and should barely influence the field).
// Returns null if there are no (meaningful) stars within the radius.
function orientationFieldAt(stars, x, y, radius) {
   let sumCos = 0, sumSin = 0, sumW = 0;
   let r2 = radius * radius;
   let sigma2 = 2 * (radius * 0.5) * (radius * 0.5);

   let near = nearbyStars(stars, x, y, radius);
   for (let i = 0; i < near.length; ++i) {
      let s = near[i];
      let dx = s.x - x, dy = s.y - y;
      let d2 = dx * dx + dy * dy;
      if (d2 > r2)
         continue;

      let kernel = Math.exp(-d2 / sigma2);
      let w = kernel * s.eccentricity * s.eccentricity;
      if (w <= 0)
         continue;

      let theta2 = 2 * s.rotation * Math.PI / 180;
      sumCos += w * Math.cos(theta2);
      sumSin += w * Math.sin(theta2);
      sumW += w;
   }

   if (sumW < 1e-6)
      return null;

   // DynamicPSF's theta is measured counter-clockwise on screen, while image
   // coordinates have y pointing down: the PSF's major axis therefore points
   // in the direction (cos(-theta), sin(-theta)) in image coordinates
   // (verified against second-moment measurements of the actual stars).
   return { angle: -Math.atan2(sumSin, sumCos) / 2 };
}

// Traces a single streamline starting at (x0,y0) in a direction
// (initialDx, initialDy). At each step, whichever of the two possible
// directions (theta, theta+180°) is closer to the previous step direction
// is chosen - otherwise the line could arbitrarily "flip" at every step
// due to the 180° ambiguity.
// fieldAt(x, y) returns { angle } or null; with gate(x, y) false the line
// ends there (the direction is not significant).
function traceStreamline(fieldAt, x0, y0, initialDx, initialDy, stepLen, maxSteps, w, h, gate) {
   let points = [];
   let x = x0, y = y0;
   let prevDx = initialDx, prevDy = initialDy;

   for (let step = 0; step < maxSteps; ++step) {
      if (gate && !gate(x, y))
         break;
      let field = fieldAt(x, y);
      if (field === null)
         break;

      let dx = Math.cos(field.angle), dy = Math.sin(field.angle);
      if ((dx * prevDx + dy * prevDy) < 0) {
         dx = -dx; dy = -dy;
      }

      x += dx * stepLen;
      y += dy * stepLen;
      if (x < 0 || x > w || y < 0 || y > h)
         break;

      points.push([x, y]);
      prevDx = dx; prevDy = dy;
   }

   return points;
}

// Seeds streamlines on a regular grid and traces them in both directions
// from each seed point. Returns an array of point lists (one per line,
// [[x,y], [x,y], ...]). With gate (see traceStreamline()), lines run only
// where the direction is significant.
function generateStreamlines(stars, w, h, radius, gate) {
   return traceField(function(x, y) { return orientationFieldAt(stars, x, y, radius); }, w, h, radius, gate);
}

// The streamlines of any axis field fieldAt(x, y) -> { angle } or null,
// spaced for the smoothing radius.
function traceField(fieldAt, w, h, radius, gate) {
   let spacing = radius * 0.6; // half the previous seed spacing (was radius * 1.2), for a denser grid
   let stepLen = radius * 0.25;
   let maxSteps = 200;

   let lines = [];
   for (let sy = spacing / 2; sy < h; sy += spacing) {
      for (let sx = spacing / 2; sx < w; sx += spacing) {
         if (gate && !gate(sx, sy))
            continue;
         let field0 = fieldAt(sx, sy);
         if (field0 === null)
            continue;

         let dx0 = Math.cos(field0.angle), dy0 = Math.sin(field0.angle);
         let fwd = traceStreamline(fieldAt, sx, sy, dx0, dy0, stepLen, maxSteps, w, h, gate);
         let bwd = traceStreamline(fieldAt, sx, sy, -dx0, -dy0, stepLen, maxSteps, w, h, gate);

         let full = bwd.slice().reverse().concat([[sx, sy]]).concat(fwd);
         if (full.length > 2)
            lines.push(full);
      }
   }
   return lines;
}

// Draws an ellipse centered at (cx,cy) whose major semi-axis a points in the
// direction (cos thetaDeg, sin thetaDeg) in image coordinates (y pointing
// down) - the same convention the streamlines use. The caller passes the
// image-coordinate angle (-theta of the PSF). Built from line segments with explicit math instead of
// Graphics.rotateTransformation(), whose sense of rotation/handling of
// negative angles turned out not to be reliable for this purpose.
// One drawPolyline() call per ellipse (not one drawLine() per segment:
// with thousands of stars the native calls dominated the redraw), with
// fewer segments for small ellipses.
function drawOrientedEllipse(g, cx, cy, a, b, thetaDeg) {
   const N = Math.max(12, Math.min(48, Math.round(a)));
   let t = thetaDeg * Math.PI / 180;
   let ct = Math.cos(t), st = Math.sin(t);
   let pts = [];
   for (let i = 0; i <= N; ++i) {
      let phi = 2 * Math.PI * i / N;
      let u = a * Math.cos(phi), v = b * Math.sin(phi);
      pts.push(new Point(cx + u * ct - v * st, cy + u * st + v * ct));
   }
   g.drawPolyline(pts);
}

// Draws the parts of the lines (point lists [[x, y], ...]) whose segments
// satisfy keep(pts, j) (the segment from point j-1 to j), each run of
// consecutive kept segments as one polyline - one native call per run
// instead of one per segment.
function drawLineRuns(g, lines, keep) {
   lines.forEach(function(pts) {
      let run = [];
      for (let j = 1; j < pts.length; ++j) {
         if (keep(pts, j)) {
            if (run.length === 0)
               run.push(new Point(pts[j - 1][0], pts[j - 1][1]));
            run.push(new Point(pts[j][0], pts[j][1]));
         } else if (run.length > 0) {
            g.drawPolyline(run);
            run = [];
         }
      }
      if (run.length > 1)
         g.drawPolyline(run);
   });
}

// Draws the streamlines as continuous, semi-transparent curves -
// deliberately subtle (thin, bright, partially transparent) so they show
// the rough trend in the background without obscuring the per-star
// vectors/the tilt overlay in front of them.
function drawStreamlines(g, lines, grid) {
   g.antialiasing = true;
   // Each segment colored by the significance of the smoothed field there
   // (shapeFieldGrid()): white where its direction is significant (as the
   // cell averages), gray where it is not. Without a grid, all white.
   let significant = function(x0, y0, x1, y1) {
      return !grid || shapeSignificantAt(grid, 0.5 * (x0 + x1), 0.5 * (y0 + y1));
   };
   let pass = function(pen, wanted) {
      g.pen = pen;
      drawLineRuns(g, lines, function(pts, j) {
         return significant(pts[j - 1][0], pts[j - 1][1], pts[j][0], pts[j][1]) === wanted;
      });
   };
   // Not significant: gray, as wide as the significant parts but darker,
   // below them.
   pass(new Pen(0x90000000, 4), false);
   pass(new Pen(0xFF8C8C8C, 2), false);
   // Significant: a dark halo, then a bright core on top - plain
   // semi-transparent white got lost on bright/saturated backgrounds (e.g.
   // the orientation heatmap); with the halo, the lines stay readable.
   pass(new Pen(0xA0000000, 4), true);
   pass(new Pen(0xFFFFFFFF, 2), true);
}

// -----------------------------------------------------------------------
// Statistics of the star shapes, with uncertainties. All work in the
// distortion components (c1, c2) = chi · (cos 2ψ, sin 2ψ) (ψ = image angle
// of the major axis, see starImageAngle()): they add approximately
// linearly and turn the 180° ambiguity of an axis into a plain vector, so
// means, scatter and standard errors behave as for any 2D quantity. For
// small values chi ≈ ellipticity (1 - b/a).

function shapeComponents(s) {
   let chi = distortionFromEcc(s.eccentricity);
   let psi2 = 2 * starImageAngle(s);
   return [chi * Math.cos(psi2), chi * Math.sin(psi2)];
}

// Mean, magnitude, axis angle and standard error of a set of components
// (arrays c1, c2). The standard error is that of each component (pooled):
// sqrt((var1 + var2) / 2 / n). z = magnitude / se; the axis angle has an
// uncertainty of about se / (2 · magnitude) radians.
function shapeMean(c1, c2) {
   let n = c1.length;
   if (n === 0)
      return null;
   let m1 = 0, m2 = 0;
   for (let i = 0; i < n; ++i) { m1 += c1[i]; m2 += c2[i]; }
   m1 /= n; m2 /= n;
   let v = 0;
   for (let i = 0; i < n; ++i)
      v += (c1[i] - m1) * (c1[i] - m1) + (c2[i] - m2) * (c2[i] - m2);
   let se = n > 1 ? Math.sqrt(v / (2 * (n - 1)) / n) : null;
   let chi = Math.hypot(m1, m2);
   return { m1: m1, m2: m2, chi: chi, n: n, se: se, z: se > 0 ? chi / se : null,
            psi: 0.5 * Math.atan2(m2, m1) };
}

// The field smoothed as for the streamlines (Gaussian kernel, sigma =
// radius/2, cut off at the radius), on a grid of spacing `step`, with the
// standard error of each value: the local scatter of the stars divided by
// the effective number of stars, (Σw)² / Σw². Returns { step, nx, ny,
// cells } with cells[j * nx + i] = { m1, m2, chi, se, z } or null.
function shapeFieldGrid(stars, w, h, radius, step) {
   let xs = [], ys = [], c1 = [], c2 = [];
   stars.forEach(function(s) {
      let c = shapeComponents(s);
      xs.push(s.x); ys.push(s.y); c1.push(c[0]); c2.push(c[1]);
   });
   let r2 = radius * radius, sigma2 = 2 * (radius * 0.5) * (radius * 0.5);
   // The stars sorted into buckets of the radius: a grid point only looks
   // at the 3x3 buckets around it.
   let bw = Math.max(1, Math.ceil(w / radius)), bh = Math.max(1, Math.ceil(h / radius));
   let buckets = [];
   for (let k = 0; k < bw * bh; ++k)
      buckets.push([]);
   for (let k = 0; k < xs.length; ++k) {
      let bi = Math.min(bw - 1, Math.max(0, Math.floor(xs[k] / radius)));
      let bj = Math.min(bh - 1, Math.max(0, Math.floor(ys[k] / radius)));
      buckets[bj * bw + bi].push(k);
   }
   let nx = Math.ceil(w / step) + 1, ny = Math.ceil(h / step) + 1;
   let cells = new Array(nx * ny);
   for (let j = 0; j < ny; ++j) {
      for (let i = 0; i < nx; ++i) {
         let x = i * step, y = j * step;
         let sw = 0, sw2 = 0, s1 = 0, s2 = 0, s11 = 0, s22 = 0;
         let near = [];
         let bi0 = Math.floor(x / radius), bj0 = Math.floor(y / radius);
         for (let bj = Math.max(0, bj0 - 1); bj <= Math.min(bh - 1, bj0 + 1); ++bj)
            for (let bi = Math.max(0, bi0 - 1); bi <= Math.min(bw - 1, bi0 + 1); ++bi)
               near = near.concat(buckets[bj * bw + bi]);
         for (let q = 0; q < near.length; ++q) {
            let k = near[q];
            let dx = xs[k] - x, dy = ys[k] - y;
            let d2 = dx * dx + dy * dy;
            if (d2 > r2)
               continue;
            let wk = Math.exp(-d2 / sigma2);
            sw += wk; sw2 += wk * wk;
            s1 += wk * c1[k]; s2 += wk * c2[k];
            s11 += wk * c1[k] * c1[k]; s22 += wk * c2[k] * c2[k];
         }
         if (sw < 1e-6) {
            cells[j * nx + i] = null;
            continue;
         }
         let m1 = s1 / sw, m2 = s2 / sw;
         let variance = Math.max(0, 0.5 * (s11 / sw - m1 * m1 + s22 / sw - m2 * m2));
         let nEff = sw * sw / sw2;
         let se = nEff > 1 ? Math.sqrt(variance / nEff) : null;
         let chi = Math.hypot(m1, m2);
         cells[j * nx + i] = { m1: m1, m2: m2, chi: chi, se: se, z: se > 0 ? chi / se : 0 };
      }
   }
   return { step: step, nx: nx, ny: ny, cells: cells };
}

// The smoothed field at (x, y) (nearest grid point), or null.
function shapeFieldAt(grid, x, y) {
   let i = Math.round(x / grid.step), j = Math.round(y / grid.step);
   if (i < 0 || j < 0 || i >= grid.nx || j >= grid.ny)
      return null;
   return grid.cells[j * grid.nx + i];
}

// Significance thresholds. The magnitude of a noisy 2D mean is never zero
// (about 1.25 se on average, Rayleigh-distributed): a magnitude counts as
// significant from 2.45 se, which pure noise exceeds in 5% of the cases
// (exp(-z²/2)); a single signed value (eps_rad, a model term) from 2 se.
const SHAPE_MIN_Z = 2.45;
const SHAPE_MIN_Z_1D = 2;
// Whether the direction of the smoothed field at (x, y) is significant.
function shapeSignificantAt(grid, x, y) {
   let c = shapeFieldAt(grid, x, y);
   return c !== null && c.z >= SHAPE_MIN_Z;
}

// Singular points (defects) of the smoothed axis field: around each grid
// cell, the winding of the doubled angle in units of 2π, halved - ±½ or
// ±1. Optics produce at most one +1 (radial or tangential pattern around
// the axis) or two +½ (the same split by a uniform term, or binodal
// astigmatism); more, and any -½, come from noise. Returns a list of
// { x, y, index }.
function shapeDefects(grid) {
   let wrap = function(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a <= -Math.PI) a += 2 * Math.PI; return a; };
   let defects = [];
   for (let j = 0; j + 1 < grid.ny; ++j) {
      for (let i = 0; i + 1 < grid.nx; ++i) {
         let loop = [grid.cells[j * grid.nx + i], grid.cells[j * grid.nx + i + 1],
                     grid.cells[(j + 1) * grid.nx + i + 1], grid.cells[(j + 1) * grid.nx + i]];
         if (loop.some(function(c) { return c === null; }))
            continue;
         let a = loop.map(function(c) { return Math.atan2(c.m2, c.m1); });
         let turn = 0;
         for (let k = 0; k < 4; ++k)
            turn += wrap(a[(k + 1) % 4] - a[k]);
         let n = Math.round(turn / (2 * Math.PI));
         if (n !== 0)
            defects.push({ x: (i + 0.5) * grid.step, y: (j + 0.5) * grid.step, index: n / 2 });
      }
   }
   return defects;
}

// The physical model of the shape field: what optics can produce, as low-
// order terms in u = (x - cx) / rMax, v = (y - cy) / rMax:
//   constant  (a0, b0)                 tracking residue, astigmatism on axis
//   linear    c1 = a1 u + a2 v, c2 = b1 u + b2 v
//                                      a decentered radial pattern, binodal
//                                      astigmatism of a misaligned system
//   radial    k · (u² - v², 2 u v)     field curvature / corrector spacing:
//                                      k > 0 radial, k < 0 tangential, k = the
//                                      value at the corner (r = rMax)
// fitted to all stars (least squares, 3-sigma clipping). A decentered
// radial pattern adds linear terms a1 = b2 = -2k u0, a2 = -b1 = 2k v0: the
// axis (u0, v0) is estimated from them, the rest of the linear part is the
// binodal (non-radial) astigmatism. The residuals are averaged per cell of
// the 11x11 grid: χ²/dof near 1 means the optics explain the pattern down
// to the noise; cells off by more than 3σ hold structure no optics
// produce (bent optics, dew, nebulosity, bad fits).
function fitShapeModel(stars, w, h, gridSize) {
   let cx = w / 2, cy = h / 2, rMax = Math.hypot(cx, cy);
   let rows = [], zs = [];
   let comps = stars.map(shapeComponents);
   let uv = stars.map(function(s) { return [(s.x - cx) / rMax, (s.y - cy) / rMax]; });
   for (let i = 0; i < stars.length; ++i) {
      let u = uv[i][0], v = uv[i][1];
      rows.push([1, 0, u, v, 0, 0, u * u - v * v]); zs.push(comps[i][0]);
      rows.push([0, 1, 0, 0, u, v, 2 * u * v]);     zs.push(comps[i][1]);
   }
   if (stars.length < 30)
      return null;
   let fit = solveLeastSquaresClipped(rows, zs, 3);
   if (fit === null || fit.coeffs === null)
      return null;
   let q = fit.coeffs, mask = fit.mask;
   let model = function(u, v) {
      return [q[0] + q[2] * u + q[3] * v + q[6] * (u * u - v * v),
              q[1] + q[4] * u + q[5] * v + q[6] * 2 * u * v];
   };

   // Parameter covariance: σ² (XᵀX)⁻¹ over the rows kept.
   let n = 7, M = [], ss = 0, count = 0;
   for (let r = 0; r < n; ++r) M.push(new Array(n).fill(0));
   for (let i = 0; i < rows.length; ++i) {
      if (mask && !mask[i])
         continue;
      let b = rows[i], res = zs[i];
      for (let r = 0; r < n; ++r) {
         res -= b[r] * q[r];
         for (let c = 0; c < n; ++c)
            M[r][c] += b[r] * b[c];
      }
      ss += res * res;
      ++count;
   }
   let sigma2 = count > n ? ss / (count - n) : 0;
   let se = [], cov = [];
   for (let r = 0; r < n; ++r) {
      let e = new Array(n).fill(0);
      e[r] = 1;
      let col = solveLinearSystem(M, e);
      se.push(col ? Math.sqrt(Math.max(0, col[r] * sigma2)) : null);
      cov.push(col ? col.map(function(v) { return v * sigma2; }) : null);
   }

   // Residuals and data per cell.
   let cellW = w / gridSize, cellH = h / gridSize;
   let cells = [];
   for (let k = 0; k < gridSize * gridSize; ++k)
      cells.push({ r1: [], r2: [], d1: [], d2: [], u: 0, v: 0 });
   for (let i = 0; i < stars.length; ++i) {
      let col = Math.min(gridSize - 1, Math.max(0, Math.floor(stars[i].x / cellW)));
      let row = Math.min(gridSize - 1, Math.max(0, Math.floor(stars[i].y / cellH)));
      let m = model(uv[i][0], uv[i][1]);
      let c = cells[row * gridSize + col];
      c.r1.push(comps[i][0] - m[0]); c.r2.push(comps[i][1] - m[1]);
      c.d1.push(comps[i][0]); c.d2.push(comps[i][1]);
   }
   let chi2 = 0, used = 0, ssRes = 0, ssTot = 0, bad = [], judged = [];
   for (let k = 0; k < cells.length; ++k) {
      let c = cells[k];
      if (c.r1.length < FWHM_GRID_MIN_STARS)
         continue;
      let res = shapeMean(c.r1, c.r2), dat = shapeMean(c.d1, c.d2);
      if (!(res.se > 0))
         continue;
      chi2 += (res.m1 * res.m1 + res.m2 * res.m2) / (res.se * res.se);
      ssRes += res.m1 * res.m1 + res.m2 * res.m2;
      ssTot += dat.m1 * dat.m1 + dat.m2 * dat.m2;
      ++used;
      judged.push({ row: Math.floor(k / gridSize), col: k % gridSize, z: res.z });
      if (res.z >= 3)
         bad.push({ row: Math.floor(k / gridSize), col: k % gridSize, z: res.z });
   }
   let dof = 2 * used - n;

   let k = q[6], kSe = se[6];
   let axis = null;
   // The uniform part: the constant term less what an off-center axis
   // adds to it, k (u0² - v0², 2 u0 v0) - tracking (or astigmatism on the
   // axis), the same wherever the pattern lies.
   let uniform1 = q[0], uniform2 = q[1];
   if (kSe > 0 && Math.abs(k) >= 3 * kSe) {
      let u0 = -(q[2] + q[5]) / (4 * k), v0 = (q[3] - q[4]) / (4 * k);
      axis = { x: cx + u0 * rMax, y: cy + v0 * rMax, offset: Math.hypot(u0, v0) };
      uniform1 -= k * (u0 * u0 - v0 * v0);
      uniform2 -= k * 2 * u0 * v0;
   }
   let binodal = Math.hypot(0.5 * (q[2] - q[5]), 0.5 * (q[3] + q[4]));
   let binodalSe = (se[2] > 0 && se[3] > 0) ? 0.5 * Math.hypot(se[2], se[3]) : null;
   return {
      coeffs: q, se: se, cov: cov.every(function(c) { return c !== null; }) ? cov : null,
      model: model, cx: cx, cy: cy, rMax: rMax,
      constant: Math.hypot(uniform1, uniform2), uniform1: uniform1, uniform2: uniform2,
      constantSe: (se[0] > 0) ? Math.hypot(se[0], se[1]) / Math.SQRT2 : null,
      k: k, kSe: kSe, axis: axis, binodal: binodal, binodalSe: binodalSe,
      chi2dof: dof > 0 ? chi2 / dof : null, cellsUsed: used,
      explained: ssTot > 0 ? Math.max(0, 1 - ssRes / ssTot) : null, unexplained: bad,
      judgedCells: judged // every cell with enough stars: { row, col, z } of its mean residual
   };
}

// Whether the model's field at (x, y) is significant: its magnitude at
// least SHAPE_MIN_Z times its standard error there, from the parameter
// covariance: var = bᵀ Cov b for the rows b of both components (pooled).
function shapeModelSignificantAt(fit, x, y) {
   if (!fit.cov)
      return true;
   let u = (x - fit.cx) / fit.rMax, v = (y - fit.cy) / fit.rMax;
   let rows = [[1, 0, u, v, 0, 0, u * u - v * v], [0, 1, 0, 0, u, v, 2 * u * v]];
   let variance = 0;
   rows.forEach(function(b) {
      for (let i = 0; i < 7; ++i)
         for (let j = 0; j < 7; ++j)
            variance += b[i] * fit.cov[i][j] * b[j];
   });
   let se = Math.sqrt(Math.max(0, variance / 2));
   let m = fit.model(u, v);
   return se > 0 ? Math.hypot(m[0], m[1]) >= SHAPE_MIN_Z * se : true;
}

// The model's axis angle at (x, y) for its streamlines, or null where the
// model is too weak to define one.
function shapeModelFieldAt(fit, x, y) {
   let m = fit.model((x - fit.cx) / fit.rMax, (y - fit.cy) / fit.rMax);
   let floor = Math.max(0.005, 2 * (fit.constantSe || 0));
   if (Math.hypot(m[0], m[1]) < floor)
      return null;
   return { angle: 0.5 * Math.atan2(m[1], m[0]) };
}

// Everything the Star shape map and its evaluation need, for the stars as
// drawn (tracking subtracted or not) and the streamline radius:
//   cells    the 11x11 grid: shapeMean() per cell, plus uncertain (fewer
//            than FWHM_GRID_MIN_STARS stars)
//   center   shapeMean() of the inner 3x3 cells
//   corners  per corner (2x2 cell blocks, TL, TR, BL, BR) the mean of the
//            radial component eps_rad = chi cos 2(ψ - φ) with its se: > 0
//            radial, < 0 tangential
//   edge     the same for the outer field (r > 2/3 rMax)
//   grid     shapeFieldGrid() at the streamline radius, defects on it
//   model    fitShapeModel()
function evaluateShapeField(stars, w, h, radius) {
   let gridSize = FWHM_GRID_SIZE;
   let cellW = w / gridSize, cellH = h / gridSize;
   let cx = w / 2, cy = h / 2, rMax = Math.hypot(cx, cy);
   let buckets = [];
   for (let k = 0; k < gridSize * gridSize; ++k)
      buckets.push({ c1: [], c2: [], rad: [] });
   let edge = [];
   stars.forEach(function(s) {
      let col = Math.min(gridSize - 1, Math.max(0, Math.floor(s.x / cellW)));
      let row = Math.min(gridSize - 1, Math.max(0, Math.floor(s.y / cellH)));
      let c = shapeComponents(s);
      let phi2 = 2 * Math.atan2(s.y - cy, s.x - cx);
      let rad = c[0] * Math.cos(phi2) + c[1] * Math.sin(phi2);
      let b = buckets[row * gridSize + col];
      b.c1.push(c[0]); b.c2.push(c[1]); b.rad.push(rad);
      if (Math.hypot(s.x - cx, s.y - cy) > rMax * 2 / 3)
         edge.push(rad);
   });
   let cells = buckets.map(function(b) {
      let m = shapeMean(b.c1, b.c2);
      if (m)
         m.uncertain = m.n < FWHM_GRID_MIN_STARS || m.se === null;
      return m;
   });
   let pool = function(r0, r1, c0, c1) {
      let a1 = [], a2 = [], rad = [];
      for (let r = r0; r <= r1; ++r)
         for (let c = c0; c <= c1; ++c) {
            let b = buckets[r * gridSize + c];
            a1 = a1.concat(b.c1); a2 = a2.concat(b.c2); rad = rad.concat(b.rad);
         }
      return { mean: shapeMean(a1, a2), rad: scalarMean(rad) };
   };
   let mid = gridSize >> 1, k = Math.max(1, Math.round(gridSize / 5)), last = gridSize - 1;
   let center = pool(mid - 1, mid + 1, mid - 1, mid + 1).mean;
   let corners = [pool(0, k - 1, 0, k - 1), pool(0, k - 1, last - k + 1, last),
                  pool(last - k + 1, last, 0, k - 1), pool(last - k + 1, last, last - k + 1, last)]
      .map(function(p) { return p.rad; });
   let grid = shapeFieldGrid(stars, w, h, radius, Math.max(8, radius * 0.25));
   let counts = { elongated: 0, round: 0, uncertain: 0 };
   cells.forEach(function(c) {
      if (!c) return;
      if (c.uncertain) ++counts.uncertain;
      else if (c.z >= SHAPE_MIN_Z) ++counts.elongated;
      else ++counts.round;
   });
   return { gridSize: gridSize, cells: cells, center: center, corners: corners, edge: scalarMean(edge),
            grid: grid, defects: shapeDefects(grid), model: fitShapeModel(stars, w, h, gridSize),
            counts: counts };
}

// Mean of plain values with its standard error: { mean, se, n } or null.
function scalarMean(values) {
   let n = values.length;
   if (n === 0)
      return null;
   let m = values.reduce(function(a, v) { return a + v; }, 0) / n;
   let v = values.reduce(function(a, x) { return a + (x - m) * (x - m); }, 0);
   return { mean: m, se: n > 1 ? Math.sqrt(v / (n - 1) / n) : null, n: n };
}

// The evaluation of the Star shape map as text lines ("Topic: text";
// lines starting with spaces belong to the line above).
function formatShapeEvaluation(ev, tracking, trackingSubtracted) {
   // Texts with non-ASCII characters (±, σ, °) are joined with + here, not
   // passed to format() as %s arguments: PJSR's format() runs out of memory
   // on such arguments. format() only gets plain numbers.
   let lines = [];
   let num = function(v, digits) { return format("%." + digits + "f", v); };
   let pm = function(v, se) { return num(v, 3) + (se !== null && se !== undefined ? " \u00B1 " + num(se, 3) : ""); };
   let sig = function(v, se) { return (se > 0) ? " (" + num(Math.abs(v) / se, 1) + "\u03C3)" : ""; };
   let deg = function(rad) { return (Math.round(((rad * 180 / Math.PI) % 180 + 180) % 180) % 180) + "\u00B0"; };
   if (tracking) {
      lines.push("Tracking: ellipticity " + pm(tracking.ellipticity, tracking.se) + " along " +
         num(tracking.angleDeg, 0) + "\u00B0, " + (trackingSubtracted ? "subtracted from all stars" : "not subtracted") +
         ".");
      if (tracking.method === "model")
         lines.push("   From the optics model; the plain median would be " + num(tracking.median.ellipticity, 3) +
            " along " + num(tracking.median.angleDeg, 0) + "\u00B0");
   }
   if (ev.center)
      lines.push("Center (inner 3x3 cells): elongation " + pm(ev.center.chi, ev.center.se) + " along " +
         deg(ev.center.psi) + sig(ev.center.chi, ev.center.se) + ", n=" + ev.center.n + " -> " +
         (ev.center.z >= SHAPE_MIN_Z ? "elongated" : "round") + ".");
   let corners = ev.corners;
   if (corners.every(function(c) { return c !== null; })) {
      let vals = corners.map(function(c) { return c.mean; });
      lines.push("Corners: eps_rad (+ radial, - tangential), spread " +
         num(Math.max.apply(null, vals) - Math.min.apply(null, vals), 3) + ".");
      corners.forEach(function(c, i) {
         let name = FWHM_CORNER_NAMES[i].charAt(0).toUpperCase() + FWHM_CORNER_NAMES[i].slice(1);
         lines.push("   " + name + ": " + pm(c.mean, c.se) + sig(c.mean, c.se) + ", n=" + c.n);
      });
   }
   if (ev.edge) {
      let z = ev.edge.se > 0 ? ev.edge.mean / ev.edge.se : 0;
      lines.push("Edge pattern (r > 2/3): eps_rad " + pm(ev.edge.mean, ev.edge.se) + sig(ev.edge.mean, ev.edge.se) +
         " -> " + (Math.abs(z) < SHAPE_MIN_Z_1D ? "no significant pattern" : ev.edge.mean > 0 ? "radial" : "tangential") +
         ".");
   }
   let m = ev.model;
   if (m) {
      lines.push("Optics model: explains " + (m.explained !== null ? num(100 * m.explained, 0) + "%" : "n/a") +
         " of the pattern; residual chi2/dof " + (m.chi2dof !== null ? num(m.chi2dof, 2) : "n/a") + " -> " +
         (m.chi2dof === null ? "not judged" : m.chi2dof <= 1.5 ? "physically plausible" :
            m.chi2dof <= 3 ? "some structure the optics do not explain" : "much structure the optics do not explain") +
         ".");
      lines.push("   Curvature/spacing term: " + pm(m.k, m.kSe) + " at the corner" + sig(m.k, m.kSe) + " -> " +
         (!(m.kSe > 0) || Math.abs(m.k) < SHAPE_MIN_Z_1D * m.kSe ? "none" : m.k > 0 ? "radial" : "tangential"));
      lines.push("   Uniform term: " + pm(m.constant, m.constantSe) + sig(m.constant, m.constantSe));
      lines.push("   Linear astigmatism: " + pm(m.binodal, m.binodalSe) + " at the edge" + sig(m.binodal, m.binodalSe));
      if (m.axis)
         lines.push("   Axis of the pattern: (" + num(m.axis.x, 0) + ", " + num(m.axis.y, 0) + ") px, " +
            num(100 * m.axis.offset, 0) + "% of the half diagonal off the center");
      if (m.unexplained.length > 0)
         lines.push("   Unexplained cells (> 3\u03C3 off the model): " + m.unexplained.length + " (about " +
            num(m.cellsUsed * Math.exp(-4.5), 0) + " expected by chance)");
   }
   let pos = ev.defects.filter(function(d) { return d.index > 0; }).length;
   let neg = ev.defects.length - pos;
   lines.push("Singular points: " + ev.defects.length + " (+: " + pos + ", -: " + neg + ") -> " +
      ((neg === 0 && pos <= 2) ? "as optics produce (at most two +, no -)" :
         "more than optics produce: noise in the smoothed field - raise the smoothing radius") + ".");
   lines.push("Cells: " + ev.counts.elongated + " elongated, " + ev.counts.round + " round within the noise, " +
      ev.counts.uncertain + " uncertain (significant from " + num(SHAPE_MIN_Z, 2) + "\u03C3).");
   return lines;
}

// -----------------------------------------------------------------------
function uniqueViewId(base) {
   let id = base;
   let n = 1;
   while (View.viewById(id) !== null) {
      id = base + "_" + n;
      ++n;
   }
   return id;
}

// -----------------------------------------------------------------------
// Creates an independent copy of a view (its own window, its own pixel
// data) with the same geometry/bit depth as the original.
function duplicateView(view, newId) {
   // Color images need at least 3 channels (otherwise the ImageWindow
   // constructor throws "Invalid number of channels").
   let numberOfChannels = view.image.isColor ? 3 : 1;
   let w = new ImageWindow(1, 1, numberOfChannels, view.window.bitsPerSample, view.window.isFloatSample,
      view.image.isColor, newId);
   w.mainView.beginProcess(UndoFlag.NoSwapFile);
   w.mainView.image.assign(view.image);
   w.mainView.endProcess();

   // Copy FITS keywords from the original (among others the CFA/Bayer
   // pattern metadata that Debayer("Auto") needs for pattern recognition)
   // - .assign() only copies pixel data, not window metadata.
   try {
      w.keywords = view.window.keywords;
   } catch (e) {
      Console.warningln("Could not transfer FITS keywords to the copy: " + e.message);
   }

   return w;
}

// -----------------------------------------------------------------------
// A view stretched for display, as a bitmap of its full size - the dialog
// preview of the target image and the background of the vector map, as in
// AperturePhotometry. Image.render() applies no screen stretch, so a linear
// image would render as a black rectangle: the view's display function is
// applied to a copy of the image, or the core's automatic stretch when the
// view carries none. The view itself is not changed.
function renderStretchedBitmap(view) {
   let image = new Image;
   image.assign(view.image);

   let stf = view.stf;
   let isIdentity = true;
   if (stf)
      for (let c = 0; c < stf.length && isIdentity; ++c)
         if (stf[c][0] !== 0.5 || stf[c][1] !== 0 || stf[c][2] !== 1)
            isIdentity = false;
   if (isIdentity)
      stf = image.computeAutoStretch();
   image.applyDisplayFunction(stf);

   let bmp = image.render();
   image.free();
   return bmp;
}

// -----------------------------------------------------------------------
// Builds the list of [i,j] exponent pairs for a 2D polynomial of degree
// `deg`: terms x^i*y^j for every i+j <= deg, ordered by total degree then
// by i. deg=1 gives the 3 terms of a plane ([0,0],[1,0],[0,1]); deg=2 adds
// the 3 quadratic terms (6 total); etc.
function polyExponents(deg) {
   let exps = [];
   for (let total = 0; total <= deg; ++total)
      for (let i = 0; i <= total; ++i)
         exps.push([i, total - i]);
   return exps;
}

// Solves the n x n system M*x = v via Gaussian elimination with partial
// pivoting. Returns the solution array, or null if M is (numerically) singular.
function solveLinearSystem(M, v) {
   let n = v.length;
   let A = M.map((row, i) => row.concat([v[i]]));
   for (let col = 0; col < n; ++col) {
      let maxRow = col, maxVal = Math.abs(A[col][col]);
      for (let r = col + 1; r < n; ++r) {
         if (Math.abs(A[r][col]) > maxVal) { maxVal = Math.abs(A[r][col]); maxRow = r; }
      }
      if (maxVal < 1e-12)
         return null;
      if (maxRow !== col) { let tmp = A[col]; A[col] = A[maxRow]; A[maxRow] = tmp; }
      for (let r = col + 1; r < n; ++r) {
         let factor = A[r][col] / A[col][col];
         for (let c = col; c <= n; ++c)
            A[r][c] -= factor * A[col][c];
      }
   }
   let x = new Array(n).fill(0);
   for (let r = n - 1; r >= 0; --r) {
      let sum = A[r][n];
      for (let c = r + 1; c < n; ++c)
         sum -= A[r][c] * x[c];
      x[r] = sum / A[r][r];
   }
   return x;
}

// Fits z ≈ Σ coeff_k * x^i_k * y^j_k (terms given by `exps`, see
// polyExponents()) over the (mask-active) points via least squares (normal
// equations, solved with solveLinearSystem()) - generalizes the earlier
// plane-only fit for the orientation heatmap, see
// computeOrientationHeatmapBitmap(). Returns the coefficient array in the
// same order as `exps`, or null for a singular/underdetermined system
// (e.g. too few active points for the chosen degree).
function fitPoly2DLS(xs, ys, zs, mask, exps) {
   let nTerms = exps.length;
   let M = [];
   for (let r = 0; r < nTerms; ++r)
      M.push(new Array(nTerms).fill(0));
   let v = new Array(nTerms).fill(0);

   let activeCount = 0;
   for (let p = 0; p < xs.length; ++p) {
      if (mask && !mask[p])
         continue;
      ++activeCount;
      let x = xs[p], y = ys[p], z = zs[p];
      let basis = new Array(nTerms);
      for (let k = 0; k < nTerms; ++k)
         basis[k] = Math.pow(x, exps[k][0]) * Math.pow(y, exps[k][1]);
      for (let r = 0; r < nTerms; ++r) {
         v[r] += basis[r] * z;
         for (let c = 0; c < nTerms; ++c)
            M[r][c] += basis[r] * basis[c];
      }
   }
   if (activeCount < nTerms)
      return null;

   return solveLinearSystem(M, v);
}

// Evaluates the polynomial with coefficients `coeffs` (matching `exps`,
// see polyExponents()) at (x,y).
function evalPoly2D(coeffs, exps, x, y) {
   let z = 0;
   for (let k = 0; k < exps.length; ++k)
      z += coeffs[k] * Math.pow(x, exps[k][0]) * Math.pow(y, exps[k][1]);
   return z;
}

// -----------------------------------------------------------------------
// Converts a hue value [0..1) (saturation/value = 1) to [r,g,b] with
// values in [0..1] each - the standard HSV color wheel, as used by the
// Seti Astro Suite's orientation heatmap (matplotlib colormap "hsv").
function hsvToRgb(h) {
   let i = Math.floor(h * 6) % 6;
   let f = h * 6 - Math.floor(h * 6);
   let q = 1 - f;
   switch (i) {
      case 0: return [1, f, 0];
      case 1: return [q, 1, 0];
      case 2: return [0, 1, f];
      case 3: return [0, q, 1];
      case 4: return [f, 0, 1];
      default: return [1, 0, q];
   }
}

// -----------------------------------------------------------------------
// Orientation heatmap as in the Seti Astro Suite (github.com/setiastro/
// setiastrosuite, compute_orientation_surface() in setiastrosuiteQT6.py):
// the PSF rotation is only defined mod 180°, so instead of theta itself, a
// double-angle representation (s=sin2θ, c=cos2θ) is fitted per star as a
// least-squares 2D polynomial of degree `degree` over x,y (with iterative
// 3-sigma clipping against outliers). degree=1 (a plane) only shows a
// uniform, monotonic gradient - it cannot represent the local curvature
// that a real coma pattern produces (the streamlines overlay, being a
// local fit, can show such curvature even when the heatmap looks flat);
// degree=2 or higher lets the heatmap follow that curvature too, at the
// cost of being more sensitive to noise/outliers, especially near the
// image edges where fewer stars constrain the higher-order terms.
// The fitted surface is smooth, so a pixel loop over the full image would
// just be needlessly slow - it is therefore evaluated cell-wise (~1/200
// of the long image side per cell), converted back to
// θ_fit = 0.5*atan2(s,c), encoded as the hue of an HSV color wheel
// (S=V=1) - identical to the Seti Astro Suite's "Orientation Map" - and
// the cell is filled via Graphics.fillRect().
// Returns null if there are too few stars for the chosen degree, or a
// singular system of equations prevents a meaningful fit.
function computeOrientationHeatmapBitmap(stars, w, h, degree) {
   let exps = polyExponents(degree);
   if (stars.length < 2 * exps.length)
      return null;

   let scale = Math.max(w, h) / 2;
   let cx = w / 2, cy = h / 2;
   let xs = [], ys = [], sArr = [], cArr = [];
   for (let i = 0; i < stars.length; ++i) {
      let st = stars[i];
      let theta = st.rotation * Math.PI / 180;
      xs.push((st.x - cx) / scale);
      ys.push((st.y - cy) / scale);
      sArr.push(Math.sin(2 * theta));
      cArr.push(Math.cos(2 * theta));
   }

   const SIGMA_CLIP = 3.0, MAX_ITER = 3;
   let mask = null, solS = null, solC = null;
   for (let iter = 0; iter < MAX_ITER; ++iter) {
      solS = fitPoly2DLS(xs, ys, sArr, mask, exps);
      solC = fitPoly2DLS(xs, ys, cArr, mask, exps);
      if (solS === null || solC === null)
         return null;

      let resid = [];
      for (let i = 0; i < xs.length; ++i) {
         let fitS = evalPoly2D(solS, exps, xs[i], ys[i]);
         let fitC = evalPoly2D(solC, exps, xs[i], ys[i]);
         resid.push(Math.hypot(sArr[i] - fitS, cArr[i] - fitC));
      }
      let active = mask ? resid.filter((_, i) => mask[i]) : resid;
      let meanR = active.reduce((a, b) => a + b, 0) / active.length;
      let variance = active.reduce((a, b) => a + (b - meanR) * (b - meanR), 0) / active.length;
      let std = Math.sqrt(variance);

      let newMask = resid.map(r => std === 0 || r < SIGMA_CLIP * std);
      let activeCount = newMask.filter(Boolean).length;
      let prevCount = mask ? mask.filter(Boolean).length : xs.length;
      if (activeCount === prevCount || activeCount < 2 * exps.length)
         break;
      mask = newMask;
   }

   // Draw directly in cell blocks onto a full-size bitmap (instead of
   // computing a coarse grid and upscaling it) - bitmap scaling (e.g.
   // scaledToSize) is not reliably available in PJSR, whereas
   // Graphics.fillRect()/Brush are standard PCL bindings. A cell size of
   // ~1/200 of the long image side gives an optically smooth color field
   // at image widths of several thousand pixels, for the fitted surface
   // that is smooth anyway.
   let cellSize = Math.max(4, Math.round(Math.max(w, h) / 200));
   let bmp = new Bitmap(w, h);
   let g = new Graphics(bmp);
   for (let py = 0; py < h; py += cellSize) {
      let py1 = Math.min(h, py + cellSize);
      let yn = ((py + py1) / 2 - cy) / scale;
      for (let px = 0; px < w; px += cellSize) {
         let px1 = Math.min(w, px + cellSize);
         let xn = ((px + px1) / 2 - cx) / scale;
         let fitS = evalPoly2D(solS, exps, xn, yn);
         let fitC = evalPoly2D(solC, exps, xn, yn);
         let thetaFit = 0.5 * Math.atan2(fitS, fitC);
         let hue = (thetaFit + Math.PI / 2) / Math.PI;
         hue = hue - Math.floor(hue);
         let rgb = hsvToRgb(hue);
         let color = 0xFF000000 |
            (Math.round(rgb[0] * 255) << 16) |
            (Math.round(rgb[1] * 255) << 8) |
            Math.round(rgb[2] * 255);
         g.fillRect(px, py, px1, py1, new Brush(color));
      }
   }
   g.end();

   return bmp;
}

// -----------------------------------------------------------------------
// Searches the (including non-enumerable, static as well as prototype)
// properties of a PCL process class for a numeric constant whose name
// contains one of the given search patterns (case-insensitive). More
// robust than an exact name comparison, since the exact spelling of
// the symbolic constants of process classes can differ between
// PixInsight versions/builds.
function findPclConstant(cls, patterns) {
   let sources = [cls, cls.prototype];
   for (let s = 0; s < sources.length; ++s) {
      let names = Object.getOwnPropertyNames(sources[s]);
      for (let p = 0; p < patterns.length; ++p) {
         let pat = patterns[p].toLowerCase();
         for (let i = 0; i < names.length; ++i) {
            let value;
            try { value = sources[s][names[i]]; } catch (e) { continue; }
            if (typeof value === "number" && names[i].toLowerCase().indexOf(pat) >= 0)
               return { name: names[i], value: value };
         }
      }
   }
   return null;
}

// Diagnostic helper function: lists all numeric constants of a PCL
// process class to the console (static and via the prototype).
function dumpPclConstants(cls) {
   Quiet.writeln("--- " + cls.name + " numeric constants ---");
   [cls, cls.prototype].forEach(function(src, idx) {
      let names = Object.getOwnPropertyNames(src);
      for (let i = 0; i < names.length; ++i) {
         let value;
         try { value = src[names[i]]; } catch (e) { continue; }
         if (typeof value === "number")
            Console.writeln("  [" + (idx === 0 ? "static" : "prototype") + "] " + names[i] + " = " + value);
      }
   });
   Quiet.writeln("--- end ---");
}

// -----------------------------------------------------------------------
// Applies the debayer process (method "SuperPixel") to a copy of the
// target image - the original is left unchanged. SuperPixel groups each
// 2x2 Bayer block directly into one RGB pixel without interpolation
// (halving the resolution on each axis), so unlike VNG/bilinear, no
// PSF-broadening interpolation is introduced - important because this
// script relies on unaltered star profiles (eccentricity/rotation) for
// the tilt/coma diagnostics.
// Returns the new (debayered) view, or null on error.
// bayerPattern: "Auto", "RGGB", "BGGR", "GBRG" or "GRBG". "Auto" requires
// CFA metadata in the image (see duplicateView) - if this is missing
// ("Unable to acquire CFA pattern information"), the pattern must be
// specified explicitly here.
function debayerSuperPixel(view, bayerPattern) {
   let dupId = uniqueViewId(view.id + "_debayered");
   let dupWindow = duplicateView(view, dupId);

   // Remember the set of windows before execution, to be able to detect
   // unwanted side windows afterward (e.g. a grayscale/luminance
   // intermediate window created by Debayer's noise evaluation).
   let idsBefore = ImageWindow.windows.map(function(w) { return w.mainView.id; });

   let P = new Debayer;

   // Disable noise evaluation: not used by this script, only costs time
   // and apparently creates an extra intermediate window.
   try {
      P.evaluateNoise = false;
   } catch (e) {
      Console.warningln("Could not disable P.evaluateNoise: " + e.message);
   }

   let patternName = bayerPattern || "Auto";
   let pattern = findPclConstant(Debayer, [patternName.toLowerCase()]);
   if (pattern === null) {
      Console.criticalln("Bayer pattern constant for '" + patternName + "' not found. " +
         "All available Debayer constants for diagnostics follow:");
      dumpPclConstants(Debayer);
      dupWindow.forceClose();
      return null;
   }
   Quiet.noteln(format("Bayer pattern: %s = %d", pattern.name, pattern.value));
   P.bayerPattern = pattern.value;

   let method = findPclConstant(Debayer, ["superpixel", "super_pixel", "super pixel"]);
   if (method === null) {
      Console.criticalln("Could not automatically find the 'SuperPixel' debayer method. " +
         "All available Debayer constants for diagnostics follow:");
      dumpPclConstants(Debayer);
      dupWindow.forceClose();
      return null;
   }
   Quiet.noteln(format("Debayer method detected: %s = %d", method.name, method.value));
   P.debayerMethod = method.value;

   if (!P.executeOn(dupWindow.mainView)) {
      Console.criticalln("Debayer (SuperPixel) could not be applied to " + dupWindow.mainView.id + ".");
      dupWindow.forceClose();
      return null;
   }

   // Close unwanted side windows newly created during debayer execution
   // (e.g. a grayscale/luminance intermediate window from noise
   // evaluation) - everything except the actual debayered result.
   let newWindows = ImageWindow.windows;
   for (let i = 0; i < newWindows.length; ++i) {
      let w = newWindows[i];
      if (w === dupWindow)
         continue;
      if (idsBefore.indexOf(w.mainView.id) < 0) {
         Quiet.noteln("Closing side window: " + w.mainView.id);
         w.forceClose();
      }
   }

   // Not shown here: showing a window activates PixInsight and pulls it in
   // front of other applications (on every frame of a series). The
   // analysis works on the hidden window; analyzeView() shows it at the
   // end when it is kept.
   Quiet.noteln("Debayer (SuperPixel) applied -> " + dupWindow.mainView.id +
      " (" + dupWindow.mainView.image.width + " x " + dupWindow.mainView.image.height + ")");

   return dupWindow.mainView;
}

// -----------------------------------------------------------------------
// Extended diagnostics: tracking component, PSF asymmetry (coma), radial/
// tangential decomposition and a FWHM² surface fit that separates tilt
// from field curvature.
//
// Conventions used throughout this block (same as the rest of the script):
// image coordinates with y pointing DOWN, the PSF major axis points in the
// image direction psi = -rotation (see orientationFieldAt()).
//
// Shape measures:
//   eccentricity  e   = sqrt(1 - b²/a²)          (what DynamicPSF/the map use)
//   ellipticity   eps = 1 - b/a                  (more intuitive, used in the ring table)
//   distortion    chi = (a² - b²) / (a² + b²)    (the only one of the three whose
//                                                 components add approximately
//                                                 linearly - used for averaging and
//                                                 for subtracting the tracking component)

function distortionFromEcc(e) {
   return e * e / (2 - e * e);
}

function eccFromDistortion(chi) {
   chi = Math.max(0, Math.min(0.999999, chi));
   return Math.sqrt(2 * chi / (1 + chi));
}

function ellipticityFromEcc(e) {
   return 1 - Math.sqrt(Math.max(0, 1 - e * e));
}

// Image-coordinate angle (radians) of the PSF major axis.
function starImageAngle(s) {
   return -s.rotation * Math.PI / 180;
}

// Solves an ordinary least-squares problem z ≈ rows[i] · coeffs over all
// active points (mask[i] true or mask null) via the normal equations.
function solveLeastSquares(rows, zs, mask) {
   let n = rows[0].length;
   let M = [];
   for (let r = 0; r < n; ++r)
      M.push(new Array(n).fill(0));
   let v = new Array(n).fill(0);
   let count = 0;
   for (let i = 0; i < rows.length; ++i) {
      if (mask && !mask[i])
         continue;
      ++count;
      let b = rows[i];
      for (let r = 0; r < n; ++r) {
         v[r] += b[r] * zs[i];
         for (let c = 0; c < n; ++c)
            M[r][c] += b[r] * b[c];
      }
   }
   if (count < n)
      return null;
   return solveLinearSystem(M, v);
}

// Robust least squares: repeats solveLeastSquares() with 3-sigma (MAD)
// clipping of the residuals. Returns { coeffs, mask } or null.
function solveLeastSquaresClipped(rows, zs, iterations) {
   let mask = null, coeffs = null;
   for (let it = 0; it < iterations; ++it) {
      coeffs = solveLeastSquares(rows, zs, mask);
      if (coeffs === null)
         return null;
      let resid = rows.map(function(b, i) {
         let z = 0;
         for (let k = 0; k < b.length; ++k)
            z += b[k] * coeffs[k];
         return zs[i] - z;
      });
      let sigma = 1.4826 * medianOf(resid.map(Math.abs));
      if (!(sigma > 0))
         break;
      mask = resid.map(function(r) { return Math.abs(r) < 3 * sigma; });
   }
   return { coeffs: coeffs, mask: mask };
}

// -----------------------------------------------------------------------
// (2) Tracking component: the elongation shared by all stars (tracking/
// guiding, wind, flexure). It is estimated as the uniform term of the
// optics model (fitShapeModel()), fitted together with the radial and
// linear terms. The plain median over the field is biased by the optics:
// a radial pattern averages to an elongation along the long side of a
// non-square sensor (about 0.13 x the corner value at 3:2), and a pattern
// whose axis lies off center - or off the sensor - shows mostly one side.
// The model takes both out; the median is kept for comparison and is the
// fallback when the model cannot be fitted (w, h not given, too few
// stars). On a single frame, astigmatism on the axis still looks like
// tracking; a series tells them apart (it stays, tracking changes).
// Returns the component; applyTrackingCorrection() subtracts it from every
// star.
function computeTrackingComponent(stars, w, h) {
   let c1 = [], c2 = [];
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let chi = distortionFromEcc(s.eccentricity);
      let psi = starImageAngle(s);
      c1.push(chi * Math.cos(2 * psi));
      c2.push(chi * Math.sin(2 * psi));
   }
   let component = function(t1, t2) {
      let chi = Math.hypot(t1, t2);
      let ecc = eccFromDistortion(chi);
      return { chi1: t1, chi2: t2, chi: chi, eccentricity: ecc, ellipticity: ellipticityFromEcc(ecc),
               angleDeg: ((0.5 * Math.atan2(t2, t1) * 180 / Math.PI) % 180 + 180) % 180 };
   };
   let median = component(medianOf(c1), medianOf(c2));
   let fit = (w > 0 && h > 0) ? fitShapeModel(stars, w, h, FWHM_GRID_SIZE) : null;
   if (fit === null) {
      median.method = "median";
      median.se = null;
      median.median = median;
      return median;
   }
   let result = component(fit.uniform1, fit.uniform2);
   result.method = "model";
   result.se = fit.constantSe;
   result.median = median;
   return result;
}

function applyTrackingCorrection(stars, tracking) {
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let chi = distortionFromEcc(s.eccentricity);
      let psi = starImageAngle(s);
      let c1 = chi * Math.cos(2 * psi) - tracking.chi1;
      let c2 = chi * Math.sin(2 * psi) - tracking.chi2;
      s.eccentricity = eccFromDistortion(Math.hypot(c1, c2));
      s.rotation = -(0.5 * Math.atan2(c2, c1)) * 180 / Math.PI;
      // fwhmX/fwhmY are deliberately left untouched: they feed the FWHM
      // (size) statistics, not the shape.
   }
}

function logTrackingComponent(tracking, subtracted) {
   Quiet.noteln("--- Tracking / global elongation ---");
   if (tracking.method === "model") {
      Quiet.writeln(format("Uniform term of the optics model: ellipticity %.3f ± %.3f (eccentricity %.2f), " +
         "major axis at %.0f° (image coordinates: 0° = +x/right, 90° = +y/down).",
         tracking.ellipticity, tracking.se, tracking.eccentricity, tracking.angleDeg));
      let md = tracking.median;
      Quiet.writeln(format("For comparison, the plain median over the field: ellipticity %.3f at %.0f°.",
         md.ellipticity, md.angleDeg));
      let diff = Math.hypot(md.chi1 - tracking.chi1, md.chi2 - tracking.chi2);
      if (diff > 0.01 && diff > 0.3 * md.chi)
         Quiet.writeln(format("-> the median differs by %.3f: that part of it comes from the optics (a radial " +
            "pattern on the non-square sensor, or an off-center axis), not from tracking.", diff));
   } else {
      Quiet.writeln(format("Median elongation over the whole field: ellipticity %.3f (eccentricity %.2f), " +
         "major axis at %.0f° (image coordinates: 0° = +x/right, 90° = +y/down).",
         tracking.ellipticity, tracking.eccentricity, tracking.angleDeg));
   }
   if (tracking.ellipticity < 0.03)
      Quiet.writeln("-> negligible (< 0.03): tracking/guiding looks clean.");
   else
      Quiet.writeln("-> a uniform elongation shared by all stars comes from tracking/guiding, wind or " +
         "flexure, not from the optics.");
   Quiet.writeln(subtracted
      ? "This component has been SUBTRACTED from every star before drawing and analysis."
      : "Not subtracted (option disabled) - it is contained in the vector map and all shape statistics.");
}

// -----------------------------------------------------------------------
// (1) PSF asymmetry per star. Gaussian and Moffat models are point-
// symmetric, so the fit alone cannot tell on which side of the core a
// comatic flare sits. Measured here directly on the pixels, relative to
// the PSF-fit center (cx, cy):
//   asymX/Y  = flux centroid inside the aperture minus fit center (px)
//   m3X/Y    = normalized third moment Σ w·ρ²·ρ / Σ w with ρ = d/σ
//              (dimensionless, independent of star size; weights the
//              outer profile more strongly than the centroid does)
// Both vectors point toward the flare. Stars that are saturated, too
// close to the border or without a positive signal get null values.
// Measured on channel 0, the channel DynamicPSF fits (see
// detectStarCandidates()): in a color image, lateral color shifts the
// other channels radially against the fit center, and their average would
// show a coma that the optics do not have.
function measureStarAsymmetry(image, stars) {
   let w = image.width, h = image.height;
   let measured = 0;
   let buf = null; // the pixels of one star's box, reused

   let progress = withProgress("Measuring PSF asymmetry", stars.length);
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      s.asymX = s.asymY = s.m3X = s.m3Y = null;

      let fwhm = 0.5 * (s.fwhmX + s.fwhmY);
      let sigma = fwhm / SIGMA_TO_FWHM;
      let rAp = Math.min(25, Math.max(3, 4 * sigma + 1));
      let rIn = rAp + 2, rOut = rAp + 6;
      let x0 = Math.floor(s.x - rOut), x1 = Math.ceil(s.x + rOut);
      let y0 = Math.floor(s.y - rOut), y1 = Math.ceil(s.y + rOut);
      if (x0 < 0 || y0 < 0 || x1 >= w || y1 >= h || !(sigma > 0)) {
         progress.update(i + 1);
         continue;
      }

      // The whole box in one call (getSamples(), a half-open rectangle):
      // reading pixel by pixel with sample() made this the slowest step,
      // one native call per pixel of every star.
      let nx = x1 - x0 + 1, ny = y1 - y0 + 1;
      if (!buf || buf.length < nx * ny)
         buf = new Float32Array(nx * ny);
      image.getSamples(buf, new Rect(x0, y0, x1 + 1, y1 + 1), 0);

      // First pass: the peak in the aperture and the background ring.
      let bgVals = [];
      let peak = 0;
      for (let y = y0; y <= y1; ++y) {
         let dy = y - s.y, row = (y - y0) * nx;
         for (let x = x0; x <= x1; ++x) {
            let dx = x - s.x;
            let r = Math.sqrt(dx * dx + dy * dy);
            let v = buf[row + x - x0];
            if (r <= rAp) {
               if (v > peak) peak = v;
            } else if (r >= rIn && r <= rOut) {
               bgVals.push(v);
            }
         }
      }

      if (peak >= 0.95 || bgVals.length < 10) { // saturated or no background ring
         progress.update(i + 1);
         continue;
      }

      // Second pass: the moments of the aperture above the background.
      let bg = medianOf(bgVals);
      let sw = 0, sx = 0, sy = 0, s3x = 0, s3y = 0;
      for (let y = y0; y <= y1; ++y) {
         let dy = y - s.y, row = (y - y0) * nx;
         for (let x = x0; x <= x1; ++x) {
            let dx = x - s.x;
            if (dx * dx + dy * dy > rAp * rAp)
               continue;
            let wgt = buf[row + x - x0] - bg;
            if (wgt <= 0)
               continue;
            let rx = dx / sigma, ry = dy / sigma;
            let rho2 = rx * rx + ry * ry;
            sw += wgt;
            sx += wgt * dx;
            sy += wgt * dy;
            s3x += wgt * rho2 * rx;
            s3y += wgt * rho2 * ry;
         }
      }
      if (sw > 0) {
         s.asymX = sx / sw;
         s.asymY = sy / sw;
         s.m3X = s3x / sw;
         s.m3Y = s3y / sw;
         ++measured;
      }
      progress.update(i + 1);
   }
   progress.end();
   Quiet.writeln(format("PSF asymmetry measured for %d / %d stars.", measured, stars.length));
   return measured;
}

// Fits the coma field model v(P) = k · (P - P0) / rMax to the per-star
// asymmetry vectors (third moment). Third-order coma grows linearly with
// the field distance and points radially away from the optical axis;
// miscollimation shifts that axis, i.e. adds a constant term. Solved as a
// linear problem in (k, ox, oy):
//   vx = k·X/rMax + ox,  vy = k·Y/rMax + oy   (X, Y relative to the image center)
// The coma-free point is where v = 0: P0 = center - (ox, oy)/k · rMax.
// Uncertainty via bootstrap resampling of the stars.
function fitComaField(stars, w, h, useCentroid) {
   let cx = w / 2, cy = h / 2;
   let rMax = Math.hypot(cx, cy);
   let pts = [];
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let vx = useCentroid ? s.asymX : s.m3X;
      let vy = useCentroid ? s.asymY : s.m3Y;
      if (vx === null || vx === undefined)
         continue;
      pts.push([(s.x - cx) / rMax, (s.y - cy) / rMax, vx, vy]);
   }
   if (pts.length < 30)
      return null;

   function fit(sample) {
      let rows = [], zs = [];
      for (let i = 0; i < sample.length; ++i) {
         let p = sample[i];
         rows.push([p[0], 1, 0]); zs.push(p[2]);
         rows.push([p[1], 0, 1]); zs.push(p[3]);
      }
      let res = solveLeastSquaresClipped(rows, zs, 3);
      if (res === null || Math.abs(res.coeffs[0]) < 1e-12)
         return null;
      let k = res.coeffs[0], ox = res.coeffs[1], oy = res.coeffs[2];
      return { k: k, ox: ox, oy: oy, x0: cx - ox / k * rMax, y0: cy - oy / k * rMax };
   }

   let main = fit(pts);
   if (main === null)
      return null;

   const N_BOOT = 100;
   let bx = [], by = [], bk = [];
   for (let b = 0; b < N_BOOT; ++b) {
      let sample = new Array(pts.length);
      for (let i = 0; i < pts.length; ++i)
         sample[i] = pts[Math.floor(Math.random() * pts.length)];
      let r = fit(sample);
      if (r !== null) {
         bx.push(r.x0); by.push(r.y0); bk.push(r.k);
      }
   }
   function sd(a) {
      if (a.length < 2) return NaN;
      let m = a.reduce(function(p, q) { return p + q; }, 0) / a.length;
      return Math.sqrt(a.reduce(function(p, q) { return p + (q - m) * (q - m); }, 0) / (a.length - 1));
   }
   // Robust spread for the position (a few bootstrap runs with k ≈ 0 can
   // throw P0 arbitrarily far away and would dominate a plain SD).
   function robustSd(a) {
      if (a.length < 2) return NaN;
      let m = medianOf(a);
      return 1.4826 * medianOf(a.map(function(q) { return Math.abs(q - m); }));
   }

   main.n = pts.length;
   main.rMax = rMax;
   main.kSd = sd(bk);
   main.x0Sd = robustSd(bx);
   main.y0Sd = robustSd(by);
   main.significant = Math.abs(main.k) > 3 * main.kSd;
   return main;
}

function logComaField(fitM3, fitCentroid, w, h, pixelPitchEffUm) {
   Quiet.noteln("--- Coma field (PSF asymmetry) ---");
   if (fitM3 === null) {
      Console.warningln("Too few stars with a valid asymmetry measurement - coma field skipped.");
      return;
   }
   let cx = w / 2, cy = h / 2;
   Quiet.writeln(format("Stars used: %d. Coma strength at the field edge k = %.3f ± %.3f " +
      "(third moment, dimensionless; positive = flare points AWAY from the coma-free point, " +
      "negative = toward it, i.e. over-corrected).", fitM3.n, fitM3.k, fitM3.kSd));
   if (fitCentroid !== null)
      Quiet.writeln(format("Cross-check with the centroid offset: k = %.3f ± %.3f px, coma-free point (%.0f, %.0f).",
         fitCentroid.k, fitCentroid.kSd, fitCentroid.x0, fitCentroid.y0));

   if (!fitM3.significant) {
      Quiet.writeln("-> No significant field-dependent coma (|k| < 3σ): either well corrected, or the " +
         "signal is too weak for a single frame. The coma-free point is NOT determinable.");
      return;
   }

   let dx = fitM3.x0 - cx, dy = fitM3.y0 - cy;
   let dist = Math.hypot(dx, dy);
   let line = format("Coma-free point: (%.0f ± %.0f, %.0f ± %.0f) px, image center (%.0f, %.0f) -> offset %.0f px " +
      "= %.0f%% of the half diagonal", fitM3.x0, fitM3.x0Sd, fitM3.y0, fitM3.y0Sd, cx, cy, dist,
      100 * dist / fitM3.rMax);
   if (pixelPitchEffUm > 0)
      line += format(" = %.2f mm on the sensor", dist * pixelPitchEffUm / 1000);
   Quiet.writeln(line + ".");
   Quiet.writeln("Caveats: an uneven tracking drift during the exposure also adds a constant asymmetry " +
      "and shifts this point; with a coma corrector or in a refractor k is small, which amplifies every error. " +
      "Compare several short exposures before touching the collimation screws.");
}

// Draws the coma field: median asymmetry arrows on a coarse grid (per-star
// arrows are too noisy to read; optional) plus the coma-free point with its
// bootstrap uncertainty.
function drawComaOverlay(g, w, h, stars, comaFit, vectorScale, showArrows) {
   const NX = 9, NY = 6;
   let cellW = w / NX, cellH = h / NY;
   let cells = [];
   for (let j = 0; j < NY; ++j)
      for (let i = 0; i < NX; ++i)
         cells.push({ vx: [], vy: [] });
   for (let k = 0; k < stars.length; ++k) {
      let s = stars[k];
      if (s.m3X === null || s.m3X === undefined)
         continue;
      let i = Math.min(NX - 1, Math.max(0, Math.floor(s.x / cellW)));
      let j = Math.min(NY - 1, Math.max(0, Math.floor(s.y / cellH)));
      cells[j * NX + i].vx.push(s.m3X);
      cells[j * NX + i].vy.push(s.m3Y);
   }

   let arrowColor = 0xFFFFA020; // orange
   let scale = Math.min(cellW, cellH) * 0.45 * vectorScale; // length for |m3| = 1
   g.antialiasing = true;

   function arrow(x0, y0, x1, y1, width, color) {
      let ang = Math.atan2(y1 - y0, x1 - x0);
      let head = Math.max(8, Math.hypot(x1 - x0, y1 - y0) * 0.3);
      g.pen = new Pen(color, width);
      g.drawLine(x0, y0, x1, y1);
      g.drawLine(x1, y1, x1 - head * Math.cos(ang - 0.45), y1 - head * Math.sin(ang - 0.45));
      g.drawLine(x1, y1, x1 - head * Math.cos(ang + 0.45), y1 - head * Math.sin(ang + 0.45));
   }

   for (let j = 0; j < NY && showArrows; ++j) {
      for (let i = 0; i < NX; ++i) {
         let c = cells[j * NX + i];
         if (c.vx.length < 5)
            continue;
         let vx = medianOf(c.vx), vy = medianOf(c.vy);
         let x0 = (i + 0.5) * cellW, y0 = (j + 0.5) * cellH;
         let x1 = x0 + vx * scale, y1 = y0 + vy * scale;
         arrow(x0, y0, x1, y1, 9, 0xB0000000);
         arrow(x0, y0, x1, y1, 4, arrowColor);
      }
   }

   if (comaFit !== null && comaFit.significant &&
       comaFit.x0 > -w && comaFit.x0 < 2 * w && comaFit.y0 > -h && comaFit.y0 < 2 * h) {
      let markColor = 0xFFFF40FF; // magenta
      let rX = Math.max(10, isFinite(comaFit.x0Sd) ? comaFit.x0Sd : 10);
      let rY = Math.max(10, isFinite(comaFit.y0Sd) ? comaFit.y0Sd : 10);
      let arm = Math.max(30, Math.max(w, h) * 0.015);
      for (let pass = 0; pass < 2; ++pass) {
         g.pen = pass === 0 ? new Pen(0xB0000000, 9) : new Pen(markColor, 4);
         g.drawEllipse(comaFit.x0 - rX, comaFit.y0 - rY, comaFit.x0 + rX, comaFit.y0 + rY);
         g.drawLine(comaFit.x0 - arm, comaFit.y0, comaFit.x0 + arm, comaFit.y0);
         g.drawLine(comaFit.x0, comaFit.y0 - arm, comaFit.x0, comaFit.y0 + arm);
      }
      let fontSize = Math.round(Math.max(28, Math.max(w, h) * 0.009));
      let font = new Font("Helvetica", fontSize);
      try { font.bold = true; } catch (e) { /* not critical */ }
      g.font = font;
      let label = "Coma-free point";
      let labelX = Math.max(fontSize, Math.min(comaFit.x0 + arm + 6, w - fontSize - g.font.width(label)));
      let labelY = Math.max(fontSize * 2, Math.min(comaFit.y0 - arm, h - fontSize));
      drawTextWithHalo(g, labelX, labelY, label, markColor, fontSize);
   }
}

// -----------------------------------------------------------------------
// Coma streamlines: the same idea as the elongation streamlines
// (generateStreamlines()), but traced through the smoothed asymmetry field
// (third moment m3X/m3Y) instead of the ellipse orientation. Differences:
//   - m3 is a true vector (flare direction, 360°), so it is averaged
//     directly and the lines have a real flow direction (arrowheads);
//   - weights are the plain Gaussian kernel (no eccentricity weighting -
//     the asymmetry does not depend on the elongation);
//   - lines STOP where the smoothed asymmetry falls below a threshold
//     (relative to the strongest part of the field), so weak regions such
//     as the coma-free zone do not fake a direction.
// Pure under-corrected coma gives lines diverging from the coma-free point
// (a source); over-corrected coma gives lines converging on it (a sink).
// Smoothed asymmetry vector at (x, y), or null if too few stars nearby.
// (nearbyStars(): see orientationFieldAt().)
function comaFieldAt(stars, x, y, radius) {
   let r2 = radius * radius;
   let sigma2 = 2 * (radius * 0.5) * (radius * 0.5);
   let sx = 0, sy = 0, sw = 0, n = 0;
   let near = nearbyStars(stars, x, y, radius);
   for (let i = 0; i < near.length; ++i) {
      let s = near[i];
      if (s.m3X === null || s.m3X === undefined)
         continue;
      let dx = s.x - x, dy = s.y - y;
      let d2 = dx * dx + dy * dy;
      if (d2 > r2)
         continue;
      let wgt = Math.exp(-d2 / sigma2);
      sx += wgt * s.m3X;
      sy += wgt * s.m3Y;
      sw += wgt;
      ++n;
   }
   if (n < 8 || sw < 1e-6)
      return null;
   let vx = sx / sw, vy = sy / sw;
   return { vx: vx, vy: vy, mag: Math.hypot(vx, vy) };
}

// Traces one coma streamline from (x0, y0) along (dir = +1) or against
// (dir = -1) the flow. Stops at the image edge, below minMag, or where the
// flow reverses (at a source/sink).
function traceComaStreamline(stars, x0, y0, dir, radius, stepLen, maxSteps, w, h, minMag) {
   let points = [];
   let x = x0, y = y0;
   let prevDx = 0, prevDy = 0;
   for (let step = 0; step < maxSteps; ++step) {
      let f = comaFieldAt(stars, x, y, radius);
      if (f === null || f.mag < minMag)
         break;
      let dx = dir * f.vx / f.mag, dy = dir * f.vy / f.mag;
      if (step > 0 && dx * prevDx + dy * prevDy < 0)
         break;
      x += dx * stepLen;
      y += dy * stepLen;
      if (x < 0 || x > w || y < 0 || y > h)
         break;
      points.push([x, y]);
      prevDx = dx; prevDy = dy;
   }
   return points;
}

// Seeds coma streamlines on a regular grid. The stop threshold is
// minPercent % of the 90th percentile of the field magnitude at the seed
// points. Each returned line is ordered ALONG the flow.
function generateComaStreamlines(stars, w, h, radius, minPercent) {
   let spacing = radius * 0.6;
   let stepLen = radius * 0.2;
   let maxSteps = 200;

   let seeds = [];
   for (let sy = spacing / 2; sy < h; sy += spacing)
      for (let sx = spacing / 2; sx < w; sx += spacing) {
         let f = comaFieldAt(stars, sx, sy, radius);
         if (f !== null)
            seeds.push({ x: sx, y: sy, mag: f.mag });
      }
   if (seeds.length === 0)
      return { lines: [], minMag: 0 };

   let mags = seeds.map(function(s) { return s.mag; }).sort(function(a, b) { return a - b; });
   let p90 = mags[Math.min(mags.length - 1, Math.floor(0.9 * mags.length))];
   let minMag = p90 * minPercent / 100;

   let lines = [];
   for (let i = 0; i < seeds.length; ++i) {
      let sd = seeds[i];
      if (sd.mag < minMag)
         continue;
      let fwd = traceComaStreamline(stars, sd.x, sd.y, +1, radius, stepLen, maxSteps, w, h, minMag);
      let bwd = traceComaStreamline(stars, sd.x, sd.y, -1, radius, stepLen, maxSteps, w, h, minMag);
      let full = bwd.slice().reverse().concat([[sd.x, sd.y]]).concat(fwd);
      if (full.length > 2)
         lines.push(full);
   }
   return { lines: lines, minMag: minMag };
}

// Draws the coma streamlines in orange (halo + core) with arrowheads at
// regular distances along each line, pointing along the flow (a line
// shorter than that distance gets one arrowhead at its middle).
function drawComaStreamlines(g, w, h, lines) {
   let color = 0xFFFFB040;
   let head = Math.max(12, Math.max(w, h) * 0.006);
   let spacing = Math.max(6 * head, 0.05 * Math.hypot(w, h)); // arc length between arrowheads
   let drawHead = function(x, y, ang) {
      g.drawLine(x, y, x - head * Math.cos(ang - 0.45), y - head * Math.sin(ang - 0.45));
      g.drawLine(x, y, x - head * Math.cos(ang + 0.45), y - head * Math.sin(ang + 0.45));
   };
   g.antialiasing = true;
   for (let pass = 0; pass < 2; ++pass) {
      g.pen = pass === 0 ? new Pen(0xA0000000, 5) : new Pen(color, 2);
      for (let i = 0; i < lines.length; ++i) {
         let pts = lines[i];
         let length = 0;
         for (let j = 1; j < pts.length; ++j) {
            g.drawLine(pts[j - 1][0], pts[j - 1][1], pts[j][0], pts[j][1]);
            length += Math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]);
         }

         // Arrowheads centered on the line: n of them, spacing apart.
         let n = Math.max(1, Math.floor(length / spacing));
         let next = (length - (n - 1) * spacing) / 2; // arc length of the first one
         let run = 0;
         for (let j = 1; j < pts.length && n > 0; ++j) {
            let dx = pts[j][0] - pts[j - 1][0], dy = pts[j][1] - pts[j - 1][1];
            let seg = Math.hypot(dx, dy);
            while (n > 0 && seg > 0 && run + seg >= next) {
               let t = (next - run) / seg;
               drawHead(pts[j - 1][0] + t * dx, pts[j - 1][1] + t * dy, Math.atan2(dy, dx));
               next += spacing;
               --n;
            }
            run += seg;
         }
      }
   }
}

// -----------------------------------------------------------------------
// One asymmetry arrow per star, for comparing the coma estimate (flare
// direction, m3) with the ellipse estimate star by star. Starts at the star
// center and points toward the flare; length ∝ |m3| (scaled with the vector
// scale like the ellipses). Arrows below minSigma x the per-star noise are
// skipped - the noise is estimated from the inner field (r < 25 % of the
// half diagonal), where the coma itself is close to zero.
// Color modes:
//   byStrength = false: alignment with the ellipse axis, |cos Δ| -
//       green = parallel (coma shapes the star), red = perpendicular
//       (elongation from something else), yellow in between; gray where
//       the star is nearly round and the ellipse axis is undefined
//   byStrength = true:  blue -> red by |m3| (like the ellipse colors)
function drawStarAsymmetryArrows(g, w, h, stars, vectorScale, minSigma, byStrength, quiet) {
   let cx = w / 2, cy = h / 2;
   let rMax = Math.hypot(cx, cy);
   let inner = [], mags = [];
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      if (s.m3X === null || s.m3X === undefined)
         continue;
      mags.push(Math.hypot(s.m3X, s.m3Y));
      if (Math.hypot(s.x - cx, s.y - cy) < 0.25 * rMax)
         inner.push(s.m3X, s.m3Y);
   }
   if (mags.length === 0)
      return;
   let noise = inner.length >= 20 ? 1.4826 * medianOf(inner.map(Math.abs)) : 0;
   let minMag = minSigma * noise;
   let sorted = mags.slice().sort(function(a, b) { return a - b; });
   let magMax = sorted[Math.floor(0.95 * (sorted.length - 1))] || 1;

   const LENGTH_PER_UNIT = 35; // px for |m3| = 1 at vector scale 1
   const ROUND_ELLIPTICITY = 0.05;
   let drawn = 0;
   g.antialiasing = true;
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      if (s.m3X === null || s.m3X === undefined)
         continue;
      let mag = Math.hypot(s.m3X, s.m3Y);
      if (mag < minMag || mag <= 0)
         continue;
      let len = Math.min(150, mag * LENGTH_PER_UNIT * vectorScale);
      let ux = s.m3X / mag, uy = s.m3Y / mag;
      let x1 = s.x + ux * len, y1 = s.y + uy * len;

      let col;
      if (byStrength) {
         col = eccColor(mag, magMax);
      } else if (ellipticityFromEcc(s.eccentricity) < ROUND_ELLIPTICITY) {
         col = 0xFFA0A0A0;
      } else {
         let t = Math.abs(Math.cos(Math.atan2(uy, ux) - starImageAngle(s))); // 1 parallel, 0 perpendicular
         let r = Math.round(255 * Math.min(1, 2 * (1 - t)));
         let gr = Math.round(255 * Math.min(1, 2 * t));
         col = 0xFF000000 | (r << 16) | (gr << 8) | 0x30;
      }

      let head = Math.max(4, len * 0.3);
      let ang = Math.atan2(uy, ux);
      g.pen = new Pen(col, 2);
      g.drawLine(s.x, s.y, x1, y1);
      g.drawLine(x1, y1, x1 - head * Math.cos(ang - 0.45), y1 - head * Math.sin(ang - 0.45));
      g.drawLine(x1, y1, x1 - head * Math.cos(ang + 0.45), y1 - head * Math.sin(ang + 0.45));
      ++drawn;
   }
   if (!quiet)
      Quiet.writeln(format("Per-star asymmetry arrows: %d of %d drawn (noise %.3f from the inner field, " +
         "threshold %.3f = %.1f sigma; color: %s).", drawn, mags.length, noise, minMag, minSigma,
         byStrength ? "strength" : "alignment with the ellipse axis"));
}

// Draws the tracking component (computeTrackingComponent()) as a legend in
// the lower-left corner: a double-headed bar in the direction of the
// common elongation, length proportional to the ellipticity (full length
// = ellipticity 0.3), plus a text label. Placed in the corner rather than
// the image center, where the Siril overlay's center FWHM label and the
// coma arrows already sit. Drawn whether or not the component was
// subtracted - the label says which.
function drawTrackingMarker(g, w, h, tracking, subtracted) {
   const FULL_SCALE_ELLIPTICITY = 0.3;
   let color = 0xFF60FF60; // green, distinct from the tilt (blue), Siril (yellow) and coma (orange/magenta) overlays
   let fontSize = Math.round(Math.max(28, Math.max(w, h) * 0.009));
   let font = new Font("Helvetica", fontSize);
   try { font.bold = true; } catch (e) { /* not critical */ }
   g.font = font;

   let boxR = Math.max(60, Math.min(w, h) * 0.06); // half the bar length at full scale
   let cx = fontSize + boxR;
   let cy = h - 3 * fontSize - boxR;

   // Reference circle = full scale, so the bar length can be read off.
   g.antialiasing = true;
   g.pen = new Pen(0x90000000, 5);
   g.drawEllipse(cx - boxR, cy - boxR, cx + boxR, cy + boxR);
   g.pen = new Pen(0xA0FFFFFF, 2);
   g.drawEllipse(cx - boxR, cy - boxR, cx + boxR, cy + boxR);

   let half = boxR * Math.min(1, tracking.ellipticity / FULL_SCALE_ELLIPTICITY);
   let a = tracking.angleDeg * Math.PI / 180; // image coordinates, y down
   let dx = Math.cos(a) * half, dy = Math.sin(a) * half;
   let head = Math.max(8, half * 0.25);
   function bar(width, col) {
      g.pen = new Pen(col, width);
      g.drawLine(cx - dx, cy - dy, cx + dx, cy + dy);
      if (half > 2) {
         for (let sgn = -1; sgn <= 1; sgn += 2) {
            let ex = cx + sgn * dx, ey = cy + sgn * dy;
            let ang = Math.atan2(sgn * dy, sgn * dx);
            g.drawLine(ex, ey, ex - head * Math.cos(ang - 0.45), ey - head * Math.sin(ang - 0.45));
            g.drawLine(ex, ey, ex - head * Math.cos(ang + 0.45), ey - head * Math.sin(ang + 0.45));
         }
      }
   }
   bar(11, 0xB0000000);
   bar(5, color);

   drawTextWithHalo(g, cx - boxR, cy + boxR + fontSize * 1.2,
      format("Tracking: eps %.3f @ %.0f°", tracking.ellipticity, tracking.angleDeg), color, fontSize);
   drawTextWithHalo(g, cx - boxR, cy + boxR + fontSize * 2.4,
      subtracted ? "(subtracted from the map)" : "(NOT subtracted - contained in the map)", color, fontSize);
}

// -----------------------------------------------------------------------
// (3) Radial/tangential decomposition per ring:
//   eps_rad = eps · cos 2(psi - phi)   phi = azimuth of the star around the center
//   > 0: major axis points radially (toward/away from the center)
//   < 0: major axis tangential (concentric around the center)
// plus the radial component of the asymmetry (third moment):
//   > 0: flare points outward (under-corrected coma, as in a bare Newtonian)
//   < 0: flare points inward (over-corrected coma)
// Writes the ring table and the verdict to con (the Console by default) and
// returns the outer-field verdict for assessImage().
function analyzeRadialTangential(stars, w, h, haveAsymmetry, con) {
   con = con || Console;
   let cx = w / 2, cy = h / 2;
   let rMax = Math.hypot(cx, cy);
   const NR = 6;
   let rings = [];
   for (let k = 0; k < NR; ++k)
      rings.push({ fwhm: [], eps: [], epsRad: [], m3Rad: [], alignSum: 0, alignW: 0 });

   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let dx = s.x - cx, dy = s.y - cy;
      let r = Math.hypot(dx, dy) / rMax;
      let k = Math.min(NR - 1, Math.floor(r * NR));
      let phi = Math.atan2(dy, dx);
      let eps = ellipticityFromEcc(s.eccentricity);
      rings[k].fwhm.push(0.5 * (s.fwhmX + s.fwhmY));
      rings[k].eps.push(eps);
      rings[k].epsRad.push(eps * Math.cos(2 * (starImageAngle(s) - phi)));
      if (haveAsymmetry && s.m3X !== null && s.m3X !== undefined) {
         rings[k].m3Rad.push(s.m3X * Math.cos(phi) + s.m3Y * Math.sin(phi));
         // Alignment flare direction vs. ellipse axis: cos 2Δ, weighted by
         // ellipticity and flare strength (both directions must be defined).
         let wgt = eps * Math.hypot(s.m3X, s.m3Y);
         rings[k].alignSum += wgt * Math.cos(2 * (Math.atan2(s.m3Y, s.m3X) - starImageAngle(s)));
         rings[k].alignW += wgt;
      }
   }

   // The rings as values (the console shows the same).
   let profile = [];
   for (let k = 0; k < NR; ++k) {
      let rg = rings[k];
      if (rg.eps.length < 5)
         continue;
      profile.push({ r0: k / NR, r1: (k + 1) / NR, n: rg.eps.length, fwhm: medianOf(rg.fwhm),
         ellipticity: medianOf(rg.eps), epsRad: medianOf(rg.epsRad),
         asymRad: rg.m3Rad.length >= 5 ? medianOf(rg.m3Rad) : null,
         align: rg.alignW > 0 ? rg.alignSum / rg.alignW : null });
   }

   con.noteln("--- Radial / tangential decomposition (rings around the image center) ---");
   con.writeln("r/rMax      n   FWHM[px]  ellipt.  eps_rad   asym_rad   align");
   con.writeln("            (eps_rad: + radial, - tangential/concentric; asym_rad: + flare outward, - inward;");
   con.writeln("             align: flare vs. ellipse axis, +1 parallel = coma shapes the star, -1 perpendicular)");
   for (let k = 0; k < NR; ++k) {
      let rg = rings[k];
      if (rg.eps.length < 5)
         continue;
      con.writeln(format("%.2f-%.2f %5d   %6.2f    %6.3f   %7.3f   %s   %s",
         k / NR, (k + 1) / NR, rg.eps.length, medianOf(rg.fwhm), medianOf(rg.eps), medianOf(rg.epsRad),
         rg.m3Rad.length >= 5 ? format("%7.3f", medianOf(rg.m3Rad)) : "   n/a",
         rg.alignW > 0 ? format("%6.2f", rg.alignSum / rg.alignW) : "  n/a"));
   }

   // Verdict from the outer field (r > 0.6 rMax, rings 4..6).
   let outerEps = [], outerM3 = [], innerFwhm = [], outerFwhm = [];
   for (let k = 0; k < NR; ++k) {
      if (k >= 4) {
         outerEps = outerEps.concat(rings[k].epsRad);
         outerM3 = outerM3.concat(rings[k].m3Rad);
         outerFwhm = outerFwhm.concat(rings[k].fwhm);
      } else if (k <= 1) {
         innerFwhm = innerFwhm.concat(rings[k].fwhm);
      }
   }
   if (outerEps.length < 10) {
      con.writeln("Too few stars in the outer field for a verdict.");
      return { eRad: null, outerAsym: null, defocusSuspect: false, profile: profile };
   }
   let eRad = medianOf(outerEps);
   // 5% margin: on a flat field the two medians otherwise cross by chance.
   let defocusSuspect = innerFwhm.length >= 5 && medianOf(innerFwhm) > 1.05 * medianOf(outerFwhm);

   if (eRad > 0.04)
      con.writeln(format("Outer field: stars elongated RADIALLY (eps_rad = %.3f, pattern \"B\"). " +
         "With a coma corrector/flattener this usually means under-correction - rule of thumb: " +
         "INCREASE the corrector-sensor distance.", eRad));
   else if (eRad < -0.04)
      con.writeln(format("Outer field: stars elongated TANGENTIALLY / concentric (eps_rad = %.3f, pattern \"A\"). " +
         "Rule of thumb: DECREASE the corrector-sensor distance - BUT the same pattern is produced by " +
         "defocus combined with field curvature/astigmatism.", eRad));
   else
      con.writeln(format("Outer field: no significant radial/tangential pattern (eps_rad = %.3f).", eRad));

   let outerAsym = null;
   if (outerM3.length >= 10) {
      outerAsym = medianOf(outerM3);
      con.writeln(format("Outer field asymmetry: asym_rad = %.3f -> %s", outerAsym,
         Math.abs(outerAsym) < 0.1 ? "no clear comatic flare." :
         (outerAsym > 0 ? "flare points OUTWARD (under-corrected coma)." : "flare points INWARD (over-corrected coma).")));
   }

   if (defocusSuspect)
      con.warningln("The center is SOFTER than the outer field - the frame is probably out of focus " +
         "(or focused on the field edge). Radial/tangential patterns are not reliable for judging " +
         "corrector spacing on such a frame; use a well-focused one.");

   return { eRad: eRad, outerAsym: outerAsym, defocusSuspect: defocusSuspect, profile: profile };
}

// -----------------------------------------------------------------------
// (4) FWHM² surface fit over all stars:
//   FWHM² = c0 + c1·X + c2·Y + c3·(X² + Y²)      X, Y = (x - cx, y - cy) / rMax
// c3 is the rotationally symmetric part (field curvature plus other
// off-axis blur) and c1/c2 the one-sided part (tilt). Unlike the 4-
// quadrant evaluation, the symmetric part is no longer counted as tilt.
// Physical conversion as elsewhere in this script: excess blur ->
// defocus Δz = f-number · blur. This is a rough single-frame estimate: the
// sign (towards/away from the corrector) is not determinable, and a pure
// tilt around a perfectly focused center would appear partly in c3.
// Writes to con (the Console by default) and returns the fitted values for
// assessImage(), or null when there are too few stars.
function fitFwhmSurface(stars, w, h, pixelPitchEffUm, focalLengthMm, apertureMm, con) {
   con = con || Console;
   let cx = w / 2, cy = h / 2;
   let rMax = Math.hypot(cx, cy);
   let rows = [], zs = [];
   for (let i = 0; i < stars.length; ++i) {
      let s = stars[i];
      let X = (s.x - cx) / rMax, Y = (s.y - cy) / rMax;
      let f = 0.5 * (s.fwhmX + s.fwhmY);
      rows.push([1, X, Y, X * X + Y * Y]);
      zs.push(f * f);
   }
   con.noteln("--- FWHM² surface fit (tilt vs. field curvature) ---");
   let res = (rows.length >= 20) ? solveLeastSquaresClipped(rows, zs, 4) : null;
   if (res === null) {
      con.warningln("Too few stars / singular system - surface fit skipped.");
      return null;
   }
   let c = res.coeffs;
   let physical = pixelPitchEffUm > 0 && focalLengthMm > 0 && apertureMm > 0;
   let fRatio = physical ? focalLengthMm / apertureMm : 0;
   let signedSqrt = function(v) { return (v < 0 ? -1 : 1) * Math.sqrt(Math.abs(v)); };

   let fCenter = Math.sqrt(Math.max(0, c[0]));
   let fEdge = Math.sqrt(Math.max(0, c[0] + c[3]));
   con.writeln(format("FWHM center %.2f px, FWHM at the field edge (rotationally symmetric part) %.2f px.",
      fCenter, fEdge));

   // Field curvature
   let curvBlurPx = signedSqrt(c[3]);
   let sagUm = physical ? fRatio * Math.abs(curvBlurPx) * pixelPitchEffUm : null;
   let line = format("Symmetric off-axis blur at the edge: %.2f px", curvBlurPx);
   if (physical)
      line += format(" -> field curvature sag ≈ %.0f µm between center and corner (f/%.2f; sign not determinable)",
         sagUm, fRatio);
   con.writeln(line + ".");
   if (c[3] < 0)
      con.writeln("   (negative: the edge is SHARPER than the center - frame focused off-center or defocused " +
         "with a curved field)");

   // Tilt
   let G = Math.hypot(c[1], c[2]);
   let dirDeg = ((Math.atan2(c[2], c[1]) * 180 / Math.PI) + 360) % 360;
   let dF2 = G; // FWHM² difference between center and edge along the gradient
   let fBad = Math.sqrt(Math.max(0, c[0] + dF2)), fGood = Math.sqrt(Math.max(0, c[0] - dF2));
   let dzUm = null, tiltDeg = null;
   line = format("One-sided part (tilt): FWHM along the gradient %.2f px (edge at %.0f°) vs. %.2f px (opposite edge)",
      fBad, dirDeg, fGood);
   if (physical) {
      dzUm = fRatio * Math.sqrt(G) * pixelPitchEffUm;
      tiltDeg = Math.atan((dzUm / 1000) / (rMax * pixelPitchEffUm / 1000)) * 180 / Math.PI;
      line += format(" -> Δz ≈ ±%.0f µm at the edge, tilt ≈ %.3f°", dzUm, tiltDeg);
   }
   con.writeln(line + ".");
   con.writeln("   (direction in image coordinates: 0° = right, 90° = down; the softer side is at that angle)");

   let x0 = null, y0 = null;
   if (c[3] > 0) {
      x0 = cx - c[1] / (2 * c[3]) * rMax;
      y0 = cy - c[2] / (2 * c[3]) * rMax;
      con.writeln(format("Sharpest point of the fitted surface: (%.0f, %.0f) px, image center (%.0f, %.0f).",
         x0, y0, cx, cy));
   }
   if (!physical)
      con.writeln("Enter pixel pitch, focal length and aperture to get Δz in µm and the tilt angle.");
   return { c: c, x0: x0, y0: y0, fCenter: fCenter, fEdge: fEdge, sagUm: sagUm,
            tiltDirDeg: dirDeg, fBad: fBad, fGood: fGood, dzUm: dzUm, tiltDeg: tiltDeg };
}

// -----------------------------------------------------------------------
// Analysis and drawing are separate steps, so that the dialog's preview can
// switch layers on and off without measuring the stars again:
//
//   analyzeView()      the expensive part - debayer, star detection, PSF
//                      fit, PSF asymmetry, coma field and the stretched
//                      background. Depends only on the settings in
//                      analysisKey(); the result is kept by the dialog.
//   starsFor()         the stars with or without the tracking component,
//                      cached per setting in the analysis.
//   reportAnalysis()   the console summary (tracking, coma, rings, FWHM
//                      surface, tilt) - once per Preview/Apply.
//   renderVectorMap()  draws the enabled layers onto the background. Layers
//                      that need a computation of their own (heatmap,
//                      streamlines, coma streamlines) are cached in the
//                      analysis under the settings they depend on.

// The settings the analysis depends on. When they differ from those of a
// kept analysis, the analysis has to be run again.
function analysisKey(view, p) {
   return JSON.stringify([view.id, p.threshold, p.radius, p.useMoffat, p.madOutlierFactor,
      p.maxCandidates, p.debayer, p.debayer ? p.bayerPattern : "",
      p.sdCustom ? [p.sdStructureLayers, p.sdSensitivity, p.sdPeakResponse,
                    p.sdMaxDistortion, p.sdAllowClustered] : null]);
}

// Returns the value of a layer cached in the analysis under the given
// name, computing it when it is missing or was computed for another key.
// Only the latest value per layer is kept.
function cachedLayer(analysis, name, key, compute) {
   let entry = analysis.layers[name];
   if (!entry || entry.key !== key) {
      entry = { key: key, value: compute() };
      analysis.layers[name] = entry;
   }
   return entry.value;
}

// The stars of an analysis, with the tracking component subtracted or not.
// The analysis keeps the stars as measured; the correction works on copies.
function starsFor(analysis, subtractTracking) {
   return cachedLayer(analysis, "stars", subtractTracking, function() {
      let stars = analysis.stars.map(function(s) {
         return Object.assign(Object.create(Object.getPrototypeOf(s)), s);
      });
      if (subtractTracking)
         applyTrackingCorrection(stars, analysis.tracking);
      return stars;
   });
}

// Runs the expensive part of the diagnostics on a target image (see the
// overview above). p holds the settings under the names of
// ScriptParameters. The PSF asymmetry is measured when measureAsymmetry
// is true - the dialog's preview always measures it, so that the coma
// layers can be switched on later. A debayer intermediate window is closed
// when p.closeIntermediateWindows or closeIntermediate is set. viewBitmap
// is an optional renderStretchedBitmap() of the view, reused as the
// background; with noBackground (the series analysis) none is rendered.
// Returns null when no stars could be measured.
function analyzeView(view, p, measureAsymmetry, closeIntermediate, viewBitmap, noBackground) {
   // Optional preprocessing step: debayer (SuperPixel) on a copy of the
   // target image. The original is left untouched; from here on, all
   // further steps operate on the debayered image.
   // A color image is no Bayer mosaic: it is never debayered (the dialog
   // disables the option for it; a process icon may still carry it).
   let debayer = p.debayer && !view.image.isColor;
   if (p.debayer && !debayer)
      Console.warningln("Debayer skipped: the target image is a color image, not a Bayer mosaic.");
   let targetView = view;
   if (debayer) {
      Progress.step("Debayering (SuperPixel)", 0, 0.05);
      let debayeredView = debayerSuperPixel(view, p.bayerPattern);
      if (debayeredView === null)
         return null;
      targetView = debayeredView;
   }
   // Save the ID before possibly closing the intermediate window (access
   // to .id after forceClose() would no longer be reliable).
   let targetViewId = targetView.id;
   let closeTarget = function() {
      if (debayer && (p.closeIntermediateWindows || closeIntermediate)) {
         Quiet.noteln("Closing SuperPixel intermediate window: " + targetViewId);
         targetView.window.forceClose();
      } else if (debayer) {
         targetView.window.show(); // kept: show it now (debayerSuperPixel() leaves it hidden)
      }
   };

   // Stage 1: find candidate star positions once with StarDetector -
   // does not depend on the PSF fit threshold, hence outside the retry loop.
   let candidateInfo = [];
   Progress.step("Detecting stars", 0.05, 0.12);
   let starBoxes = detectStarCandidates(targetView.image, p.radius, p.maxCandidates, candidateInfo, p);
   if (starBoxes === null) {
      closeTarget();
      return null;
   }

   if (starBoxes.length === 0) {
      Console.criticalln("StarDetector found no candidates. " +
         "Possible causes: image not yet debayered (Bayer mosaic instead of true RGB), " +
         "uncalibrated raw image, or no star field in the image area.");
      closeTarget();
      return null;
   }

   // Stage 2: elliptical PSF fit (Gaussian/Moffat). If the chosen fit
   // quality threshold results in 0 successful fits (e.g. because it was
   // chosen too strictly for a single, unstacked subframe), the script
   // automatically works its way down with lower values instead of
   // aborting immediately without a result.
   const MIN_THRESHOLD = 0.2;
   let t = p.threshold;
   let stars = [];
   for (let attempt = 1; ; ++attempt) {
      Quiet.writeln(format("Fit attempt %d: threshold=%.2f  search radius=%d  Moffat=%s",
         attempt, t, p.radius, p.useMoffat ? "true" : "false"));

      Progress.step(format("Fitting PSFs of %d stars", starBoxes.length), 0.12, 0.65);
      stars = fitStars(targetView, starBoxes, t, p.radius, p.useMoffat, p.madOutlierFactor, candidateInfo);
      if (stars.length > 0 || t <= MIN_THRESHOLD || attempt >= 6)
         break;

      let next = Math.max(MIN_THRESHOLD, t * 0.6);
      Console.warningln(format("No fits at threshold=%.2f - automatically reducing to %.2f ...", t, next));
      t = next;
   }

   if (stars.length === 0) {
      Console.criticalln("Still no successful PSF fit even after automatic threshold reduction, " +
         "although " + starBoxes.length + " candidate(s) were found by StarDetector. " +
         "Consider adjusting the search radius (see the diagnostic output above).");
      closeTarget();
      return null;
   }

   if (t !== p.threshold)
      Quiet.noteln(format("Note: threshold automatically reduced from %.2f to %.2f. " +
         "Enter this value directly in the dialog for future runs.", p.threshold, t));

   // Extended diagnostics (see the block above renderVectorMap()). Keep the
   // raw shape for the CSV export before an optional tracking correction.
   for (let i = 0; i < stars.length; ++i) {
      stars[i].eccentricityRaw = stars[i].eccentricity;
      stars[i].rotationRaw = stars[i].rotation;
   }

   let w = targetView.image.width, h = targetView.image.height;

   // The PSF asymmetry and the coma field depend only on the pixels and the
   // fitted centers, not on the tracking correction.
   let comaFit = null, comaFitCentroid = null;
   if (measureAsymmetry) {
      Progress.step("Measuring PSF asymmetry", 0.65, 0.78);
      measureStarAsymmetry(targetView.image, stars);
      comaFit = fitComaField(stars, w, h, false);
      comaFitCentroid = fitComaField(stars, w, h, true);
   }

   // The background: the target image stretched for display. Without
   // debayering, that is the bitmap the dialog already shows (viewBitmap).
   let background = null;
   if (!noBackground) {
      Progress.step("Rendering the background", 0.78, 0.85);
      background = (targetView === view && viewBitmap) ? viewBitmap : renderStretchedBitmap(targetView);
   }

   // Close the SuperPixel/debayer intermediate window by default after
   // processing (nothing needs its pixels any more at this point).
   closeTarget();

   return {
      key: analysisKey(view, p),
      targetViewId: targetViewId,
      debayer: debayer,
      w: w,
      h: h,
      stars: stars,
      tracking: computeTrackingComponent(stars, w, h),
      hasAsymmetry: measureAsymmetry,
      comaFit: comaFit,
      comaFitCentroid: comaFitCentroid,
      background: background,
      layers: {},
      // For Save results: the target image, not the debayered copy.
      header: { viewId: view.id, filePath: view.window.filePath, keywords: viewKeywordList(view) }
   };
}

// The optics used for the physical conversions: the dialog values, or
// those of the FITS header where present when p.opticsFromHeader is set.
// SuperPixel debayering halves the resolution: one output pixel spans 2x2
// sensor pixels. All physical conversions (Δz, tilt angle, 3D plot,
// coma-free point in mm, FWHM grid in arcseconds) must use this effective
// pitch - the dialog value is the physical sensor pixel pitch.
function resolveOptics(view, p, debayer, verbose) {
   // From the FITS header only, when that option is set - a value it does
   // not give stays 0 (not set); otherwise the dialog's values.
   let optics = p.opticsFromHeader ? { pixelPitchUm: 0, focalLengthMm: 0, apertureMm: 0 } :
      { pixelPitchUm: p.pixelPitchUm, focalLengthMm: p.focalLengthMm, apertureMm: p.apertureMm };

   if (p.opticsFromHeader && view) {
      let hdr = readOpticsFromHeader(view);
      let used = [];
      if (hdr.pixelPitchUm > 0) {
         optics.pixelPitchUm = hdr.pixelPitchUm;
         used.push(format("pixel pitch %.2f µm (%s)", optics.pixelPitchUm, hdr.sources.pixelPitchUm));
      }
      if (hdr.focalLengthMm > 0) {
         optics.focalLengthMm = hdr.focalLengthMm;
         used.push(format("focal length %.1f mm (%s)", optics.focalLengthMm, hdr.sources.focalLengthMm));
      }
      if (hdr.apertureMm > 0) {
         optics.apertureMm = hdr.apertureMm;
         used.push(format("aperture %.1f mm (%s)", optics.apertureMm, hdr.sources.apertureMm));
      }
      if (verbose)
         Quiet.writeln(used.length > 0
            ? "Optics from FITS header: " + used.join(", ") + (used.length < 3 ?
               ". The others are not set (uncheck \"Read the values from the FITS header\" to enter them)." : ".")
            : "No optics keywords (XPIXSZ, FOCALLEN, APTDIA/FOCRATIO) in the FITS header - not set (uncheck " +
              "\"Read the values from the FITS header\" to enter them).");
   }

   if (debayer) {
      if (verbose && optics.pixelPitchUm > 0)
         Quiet.writeln(format("SuperPixel debayer: effective pixel pitch %.2f µm (2 x %.2f µm) used for all " +
            "physical conversions.", 2 * optics.pixelPitchUm, optics.pixelPitchUm));
      optics.pixelPitchUm *= 2;
   }
   optics.physical = optics.pixelPitchUm > 0 && optics.focalLengthMm > 0 && optics.apertureMm > 0;

   // The mount: estimated from the header, or the dialog's choice; the
   // observation values (exposure, altitude ...) always from the header.
   let values = view ? viewKeywordValues(view) : {};
   let mount = p.opticsFromHeader ? estimateMount(values) :
      { type: Math.range(p.mountType || 0, 0, MOUNT_TYPES.length - 1), source: "dialog" };
   optics.mount = { type: mount.type, source: mount.source, obs: mountObservation(values) };
   optics.guiding = effectiveGuiding(p.guidingType, values);
   return optics;
}

// A console that discards everything: the dialog evaluates an analysis
// again whenever a setting changes, without repeating the report.
const SILENT_CONSOLE = {
   write: function() {}, writeln: function() {}, noteln: function() {},
   warningln: function() {}, criticalln: function() {}
};

// The console summary of an analysis for the given settings, written to con
// (the Console by default, or SILENT_CONSOLE to only compute). Returns the
// Siril-style quadrant evaluation (tilt, null when not possible) and the
// measured values that assessImage() judges (metrics).
function reportAnalysis(analysis, stars, p, optics, con) {
   con = con || Console;
   let verbose = con === Console;
   let w = analysis.w, h = analysis.h;
   let showComa = analysis.hasAsymmetry && p.measureAsymmetry;

   if (verbose) {
      logTrackingComponent(analysis.tracking, p.subtractTracking);
      if (showComa)
         logComaField(analysis.comaFit, analysis.comaFitCentroid, w, h, optics.pixelPitchUm);
   }
   let rings = analyzeRadialTangential(stars, w, h, showComa, con);
   let surface = fitFwhmSurface(stars, w, h, optics.pixelPitchUm, optics.focalLengthMm, optics.apertureMm, con);

   if (p.fwhmCellValue === FWHM_CELL_ARCSEC) {
      if (optics.pixelPitchUm > 0 && optics.focalLengthMm > 0)
         con.writeln(format("FWHM grid in arcseconds: %.3f″/px (pitch %.2f µm, focal length %.0f mm).",
            206.265 * optics.pixelPitchUm / optics.focalLengthMm, optics.pixelPitchUm, optics.focalLengthMm));
      else
         con.warningln("FWHM grid in arcseconds needs pixel pitch and focal length - showing pixels.");
   }

   // Siril-style quadrant evaluation: for the overlay, the optional tilt
   // angle computation and the assessment.
   let tilt = computeSirilTilt(stars, w, h);
   if (tilt === null) {
      con.warningln("Siril tilt evaluation: at least one quadrant/ring has no stars - skipped.");
   } else {
      let best = Math.min(tilt.m1, tilt.m2, tilt.m3, tilt.m4);
      let worst = Math.max(tilt.m1, tilt.m2, tilt.m3, tilt.m4);
      let ref = (tilt.m1 + tilt.m2 + tilt.m3 + tilt.m4) / 4;
      con.noteln(format(
         "Siril-Style Tilt: Sensor tilt[FWHM]=%.2f (%.0f%%), Off-axis aberration[FWHM]=%.2f",
         worst - best, ((worst - best) / ref) * 100, tilt.mOuter - tilt.mInner));
      if (optics.physical && verbose)
         computeAndLogTiltAngles(tilt, w, h, optics.pixelPitchUm, optics.focalLengthMm, optics.apertureMm);
   }

   // Assessment of the 11 x 11 FWHM grid (evaluateFwhmGrid()), whether the
   // grid is drawn or not.
   let grid = evaluateFwhmGrid(stars, w, h, FWHM_GRID_SIZE);
   con.noteln("--- FWHM grid assessment (11x11, ratio to the center) ---");
   formatFwhmGridEvaluation(grid).forEach(function(line) { con.writeln(line); });
   let gridSummary = {
      center: grid.center ? grid.center.value : null,
      centerSe: grid.center ? grid.center.se : null,
      cornerRatio: grid.corners ? grid.corners.ratio : null,
      cornerRatios: grid.corners ? grid.corners.ratios : null, // TL, TR, BL, BR
      tiltRel: grid.tilt ? grid.tilt.rel : null,
      tiltSigma: grid.tilt ? grid.tilt.z : null,
      tiltSoftCorner: grid.tilt ? FWHM_CORNER_NAMES[grid.tilt.soft] : null,
      edgeSpread: grid.edgeSpread,
      cells: grid.classCounts
   };

   // The star shapes with their uncertainties (evaluateShapeField()).
   let shape = shapeEvaluationFor(analysis, p);
   con.noteln("--- Star shape assessment (11x11 cells, optics model, singular points) ---");
   formatShapeEvaluation(shape, analysis.tracking, p.subtractTracking).forEach(function(line) {
      con.writeln(line);
   });
   let sm = shape.model;
   let shapeSummary = {
      centerElongation: shape.center ? shape.center.chi : null,
      centerSe: shape.center ? shape.center.se : null,
      edgeEpsRad: shape.edge ? shape.edge.mean : null,
      edgeEpsRadSe: shape.edge ? shape.edge.se : null,
      cornerEpsRad: shape.corners.map(function(c) { return c ? c.mean : null; }), // TL, TR, BL, BR
      cornerEpsRadSe: shape.corners.map(function(c) { return c ? c.se : null; }),
      modelExplained: sm ? sm.explained : null,
      modelChi2dof: sm ? sm.chi2dof : null,
      curvatureTerm: sm ? sm.k : null,
      curvatureTermSe: sm ? sm.kSe : null,
      uniformTerm: sm ? sm.constant : null,
      linearAstigmatism: sm ? sm.binodal : null,
      axisX: sm && sm.axis ? sm.axis.x : null,
      axisY: sm && sm.axis ? sm.axis.y : null,
      unexplainedCells: sm ? sm.unexplained.length : null,
      singularPlus: shape.defects.filter(function(dft) { return dft.index > 0; }).length,
      singularMinus: shape.defects.filter(function(dft) { return dft.index < 0; }).length,
      cells: shape.counts
   };

   return {
      tilt: tilt,
      metrics: {
         n: stars.length, w: w, h: h,
         tracking: analysis.tracking, trackingSubtracted: p.subtractTracking,
         rings: rings, surface: surface, siril: tilt, grid: gridSummary, shape: shapeSummary,
         guiding: optics.guiding ? { type: optics.guiding.type, name: GUIDING_TYPES[optics.guiding.type],
                                     source: optics.guiding.source } : null,
         mount: optics.mount ? {
            type: optics.mount.type, name: MOUNT_TYPES[optics.mount.type], source: optics.mount.source,
            fieldRotationCornerPx: optics.mount.type === MOUNT_ALTAZ ? fieldRotationSmearPx(optics.mount.obs, w, h) : null,
            exposureS: optics.mount.obs.exposureS
         } : null,
         fRatio: (optics.focalLengthMm > 0 && optics.apertureMm > 0) ? optics.focalLengthMm / optics.apertureMm : null,
         coma: showComa ? analysis.comaFit : undefined // undefined: not measured, null: too few stars
      }
   };
}

// -----------------------------------------------------------------------
// Assessment: judges the measured values of reportAnalysis() and suggests
// corrections, as one would read the console report. Each finding has a
// level (OK, Note, Action), a topic, what was found and what to do; the
// actions come back in the order in which they are best worked through
// (focus before tracking before tilt ...). The thresholds are starting
// points, not calibrated limits. Where the direction of a correction
// depends on the optical system, the optics type decides the wording
// (unknown: both cases are named). The indices are stored with the
// settings: new types are appended.
// A refractor differs from a Newtonian in what is normal: it has hardly
// any coma by design, its field curvature is the main off-axis error
// without a flattener, its lens is collimated at the factory, and lateral
// color (the colors imaged at slightly different scales) elongates the
// stars of a color or broadband frame radially at the field edge - which
// looks like under-corrected coma or spacing.
// Catadioptric systems focus by moving the primary mirror, so the image
// can shift or tilt with it (mirror flop); a classic SCT has coma and a
// curved field by design, an aplanatic SCT (EdgeHD), a RASA and an
// astrograph with built-in flattener are meant to be flat and coma-free -
// there both point to the back focus. Slow systems (f/8 and above) have a
// deep critical focus zone and are seeing-limited: small differences
// across the field are uncertain.
// The optical systems. The indices are stored with the settings: new types
// are appended. Per type:
//   kind       newtonian | refractor | rasa | sct | cat (classic SCT or
//              Maksutov without corrector) | astrograph | unknown
//   corrected  a corrector, flattener or reducer sets the edge of the field
//   corrector, distance  what is adjusted and how it is named
//   backFocus  where the spacing starts from (the manufacturer's value)
//   flatByDesign  the field is meant to be flat and coma-free: curvature or
//              coma point to the back focus, not to the design
//   mirrorFocus  focusing moves the primary mirror (mirror flop, image shift)
//   lateralColor  lenses image the colors at slightly different scales
const OPTICS_PROFILES = [
   { name: "Unknown", kind: "unknown" },
   { name: "Reflector without corrector (e.g. bare Newtonian)", kind: "newtonian" },
   { name: "Reflector with coma corrector / reducer", kind: "newtonian", corrected: true,
     corrector: "corrector", distance: "corrector-sensor distance" },
   { name: "Refractor without flattener", kind: "refractor", lateralColor: true },
   { name: "Refractor with flattener / reducer", kind: "refractor", corrected: true, lateralColor: true,
     corrector: "flattener", distance: "flattener-sensor distance" },
   { name: "RASA / Hyperstar (f/2)", kind: "rasa", corrected: true, flatByDesign: true, mirrorFocus: true,
     corrector: "corrector lens", distance: "camera distance to the corrector lens",
     backFocus: "the back focus Celestron or Starizona specify for your model and camera adapter" },
   { name: "SCT with reducer (e.g. f/6.3)", kind: "sct", corrected: true, mirrorFocus: true,
     corrector: "reducer", distance: "reducer-sensor distance",
     backFocus: "the reducer's specified distance (usually 105 mm)" },
   { name: "Aplanatic SCT (EdgeHD and similar)", kind: "sct", corrected: true, flatByDesign: true, mirrorFocus: true,
     corrector: "built-in corrector", distance: "back focus",
     backFocus: "the specified back focus (EdgeHD: 133.35 mm for the 8\", 146.05 mm for 9.25\"-14\", " +
        "105 mm with the 0.7x reducer)" },
   { name: "Classic SCT or Maksutov (no corrector)", kind: "cat", mirrorFocus: true },
   { name: "Astrograph with built-in flattener (Petzval, quadruplet)", kind: "astrograph", corrected: true,
     flatByDesign: true, lateralColor: true, corrector: "built-in flattener", distance: "camera back focus",
       backFocus: "the back focus the manufacturer specifies" },
   { name: "Photographic lens", kind: "camera-lens", lateralColor: true }
];
const OPTICS_TYPES = OPTICS_PROFILES.map(function(o) { return o.name; });
const OPTICS_UNKNOWN = 0, OPTICS_UNCORRECTED = 1, OPTICS_CORRECTED = 2, OPTICS_REFRACTOR = 3,
      OPTICS_REFRACTOR_FLAT = 4, OPTICS_RASA = 5, OPTICS_SCT_REDUCER = 6, OPTICS_SCT_APLANATIC = 7,
   OPTICS_CLASSIC_CAT = 8, OPTICS_ASTROGRAPH = 9, OPTICS_CAMERA_LENS = 10;
const ASSESS_OK = 0, ASSESS_NOTE = 1, ASSESS_ACTION = 2;

// Half the critical focus zone in µm at f-ratio N (CFZ = ±2.44 λ N², λ = 0.55 µm):
// a defocus within it does not enlarge the stars visibly.
function criticalFocusHalfUm(fRatio) {
   return 2.44 * 0.55 * fRatio * fRatio;
}

function assessImage(m, opticsType) {
   let P = OPTICS_PROFILES[opticsType] || OPTICS_PROFILES[OPTICS_UNKNOWN];
   let refractor = P.kind === "refractor";
   let cameraLens = P.kind === "camera-lens";
   let classicCat = P.kind === "cat";
   // No corrector, flattener or reducer: coma and curvature are by design.
   let bare = P.kind !== "unknown" && !P.corrected;
   let corrected = P.corrected === true;
   // The part whose distance to the sensor is adjusted.
   let corrector = P.corrector || "corrector or flattener";
   let distance = P.distance || "corrector-sensor distance";
   let fromBackFocus = P.backFocus ? " Start from " + P.backFocus + "." : "";
   let lateralColor = P.lateralColor ? " With lenses, lateral color produces the same radial pattern in a color " +
      "or broadband frame: compare with a narrowband frame or a single color channel." : "";
   let findings = [];
   let add = function(level, topic, text, advice) {
      findings.push({ level: level, topic: topic, text: text, advice: advice || "" });
   };
   // A direction in image coordinates (0° = right, 90° = down) in words.
   let side = function(deg) {
      const NAMES = ["right", "lower right", "bottom", "lower left", "left", "upper left", "top", "upper right"];
      return NAMES[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
   };
   let pct = function(v) { return format("%.0f%%", 100 * v); };
   let ifCorrector = corrected ? "Rule of thumb: " : "If a corrector or flattener is used: rule of thumb, ";
   // Critical focus zone (needs the f-ratio).
   let cfz = (m.fRatio > 0) ? criticalFocusHalfUm(m.fRatio) : null;

   if (m.n < 150)
      add(ASSESS_NOTE, "Data", format("Only %d stars measured - the verdicts below are uncertain.", m.n),
         "Lower the detection threshold, raise the maximum number of stars, or expose longer.");
   // Slow systems: a large depth of focus hides tilt and curvature, and the
   // seeing sets the star size.
   if (cfz !== null && m.fRatio >= 8)
      add(ASSESS_NOTE, "Data", format("At f/%.1f the critical focus zone is about ±%.0f µm: tilt and field " +
         "curvature smaller than that hardly enlarge the stars, and the seeing dominates their size - small " +
         "differences across the field are uncertain.", m.fRatio, cfz),
         "Judge tilt and curvature only when they are clear, and compare several frames.");

   // Focus first: a defocused frame falsifies the tilt and spacing verdicts.
   let s = m.surface;
   let focusBad = (m.rings && m.rings.defocusSuspect) || (s !== null && s.c[3] < 0);
   if (focusBad)
      add(ASSESS_ACTION, "Focus", "The image center is softer than the field edge: the frame is probably out of " +
         "focus, or focused on the edge.", "Refocus on the center. Tilt and spacing verdicts are unreliable until then.");
   let caveat = focusBad ? " Uncertain: the frame seems out of focus." : "";

   // The mount: field rotation of an alt-azimuth mount, no tracking.
   let mt = m.mount;
   if (mt && mt.type === MOUNT_ALTAZ) {
      let smear = mt.fieldRotationCornerPx;
      let text = "Alt-azimuth mount" + (mt.source && mt.source !== "dialog" ? " (" + mt.source + ")" : "") +
         ": without a derotator the field turns during the exposure, the stars become arcs around the rotation " +
         "center - a tangential pattern growing with the distance, like field curvature or too short a spacing.";
      if (smear !== null)
         text += format(" Here about %.1f px at the corners in %.0f s", smear, mt.exposureS) +
            (s !== null && s.fCenter > 0 ? format(" (FWHM in the center %.2f px).", s.fCenter) : ".");
      add(smear !== null && s !== null && smear > 0.5 * s.fCenter ? ASSESS_ACTION : ASSESS_NOTE, "Mount", text,
         "Judge a tangential edge pattern only with short exposures or a derotator; shorter exposures, an " +
         "altitude away from the zenith and an azimuth near east or west reduce the rotation.");
   } else if (mt && mt.type === MOUNT_NONE) {
      add(ASSESS_NOTE, "Mount", "Not tracked: all stars trail along the right ascension.",
         "The shape results show the trail, not the optics; use the FWHM results only across the trail, or " +
         "short exposures.");
   } else if (mt && mt.type !== MOUNT_UNKNOWN) {
      add(ASSESS_OK, "Mount", MOUNT_TYPES[mt.type] + (mt.source && mt.source !== "dialog" ? " (" + mt.source + ")" :
         "") + ": no field rotation.");
   }

   let t = m.tracking;
   if (t) {
      if (t.ellipticity < 0.03) {
         add(ASSESS_OK, "Tracking", format("No significant common elongation (%.3f).", t.ellipticity));
      } else {
         let gd = m.guiding;
         let advice = (gd && gd.type !== GUIDING_UNKNOWN) ? guidingCauses(gd.type) :
            "Check guiding (RMS, calibration), balance, cable drag, wind and flexure. An axis that stays " +
            "the same over several frames points to the mount, a changing one to wind.";
         if (!m.trackingSubtracted)
            advice += " Enable \"Subtract it from all stars\" so that it does not mask the optical patterns.";
         add(t.ellipticity > 0.07 ? ASSESS_ACTION : ASSESS_NOTE, "Tracking",
            format("All stars share an elongation of %.3f along %.0f° (image coordinates).", t.ellipticity, t.angleDeg),
            advice);
      }
   }

   if (s !== null) {
      let rel = (s.fBad - s.fGood) / Math.max(1e-6, 0.5 * (s.fBad + s.fGood));
      if (rel < 0.05) {
         add(ASSESS_OK, "Tilt", format("No significant one-sided blur (%s between opposite edges).", pct(rel)));
      } else {
         let text = format("The %s side is softer: FWHM %.2f px against %.2f px on the opposite side (%s).",
            side(s.tiltDirDeg), s.fBad, s.fGood, pct(rel));
         if (s.dzUm !== null) {
            text += format(" Δz ≈ ±%.0f µm at the edge, tilt ≈ %.3f°.", s.dzUm, s.tiltDeg);
            if (cfz !== null)
               text += format(" Critical focus zone ±%.0f µm.", cfz);
         }
         if (m.siril) {
            let q = [m.siril.m1, m.siril.m2, m.siril.m3, m.siril.m4];
            let mean = (q[0] + q[1] + q[2] + q[3]) / 4;
            text += format(" Quadrants differ by %s.", pct((Math.max.apply(null, q) - Math.min.apply(null, q)) / mean));
         }
         let advice = format("Adjust the tilt of the camera on the axis toward the %s (%.0f° in image coordinates). " +
            "Whether that side has to move toward or away from the optics cannot be told from one frame: change it " +
            "slightly and compare.", side(s.tiltDirDeg), s.tiltDirDeg);
         if (P.kind === "rasa")
            advice += " At f/2 even a few micrometers count: use a tilt adapter between the camera and the " +
               "corrector, and check that the camera sits square on the adapter.";
         if (P.mirrorFocus)
            advice += " Focusing moves the primary mirror: a mirror that shifted can tilt the image as well - " +
               "lock it if possible, and compare frames before adjusting.";
         add(rel > 0.10 ? ASSESS_ACTION : ASSESS_NOTE, "Tilt", text + caveat, advice);
      }
   }

   // The field edge: radial or tangential elongation (corrector spacing).
   let r = m.rings;
   if (!r || r.eRad === null) {
      add(ASSESS_NOTE, "Spacing", "Too few stars in the outer field for a radial/tangential verdict.");
   } else if (Math.abs(r.eRad) <= 0.04) {
      add(ASSESS_OK, "Spacing", format("No radial or tangential pattern at the field edge (eps_rad %.3f).", r.eRad));
   } else {
      let level = Math.abs(r.eRad) > 0.08 ? ASSESS_ACTION : ASSESS_NOTE;
      if (r.eRad > 0) {
         let text = format("Stars at the field edge are elongated radially (eps_rad %.3f).", r.eRad);
         if (cameraLens)
            add(ASSESS_NOTE, "Spacing", text + " A camera lens has no adjustable corrector spacing; this can be " +
               "a lens aberration or lateral color in a color frame." + caveat,
               "There is no back focus to adjust. Compare a single color channel, check focus and camera alignment, " +
               "and compare several frames.");
         else if (opticsType === OPTICS_UNCORRECTED)
            add(ASSESS_NOTE, "Spacing", text + " This is the normal coma of an uncorrected system.",
               "A coma corrector would remove it; there is no spacing to adjust.");
         else if (classicCat)
            add(ASSESS_NOTE, "Spacing", text + " A classic SCT has coma at the field edge by design; a Maksutov " +
               "has little - there it is rather astigmatism or a tilted camera." + caveat,
               "There is no spacing to adjust. A reducer-corrector or an aplanatic design would reduce it.");
         else if (opticsType === OPTICS_REFRACTOR)
            add(ASSESS_NOTE, "Spacing", text + " A refractor has hardly any coma: without a flattener this is " +
               "astigmatism of the curved field, or lateral color in a color or broadband frame." + caveat,
               "There is no spacing to adjust. A flattener matching the refractor would reduce it; compare with a " +
               "narrowband frame or a single color channel to tell lateral color apart.");
         else
            add(level, "Spacing", text + " With a " + corrector + " this usually means under-correction." +
               lateralColor + caveat,
               ifCorrector + "increase the " + distance + " in small steps (e.g. 0.5 mm) and measure again." +
               fromBackFocus);
      } else {
         let text = format("Stars at the field edge are elongated tangentially, concentric around the center " +
            "(eps_rad %.3f).", r.eRad);
         if (cameraLens)
            add(ASSESS_NOTE, "Spacing", text + " This can be field curvature or astigmatism of the camera lens." +
               caveat,
               "There is no spacing to adjust. Check focus, especially at the center, and compare several frames.");
         else if (opticsType === OPTICS_REFRACTOR)
            add(ASSESS_NOTE, "Spacing", text + " Without a flattener this is the field curvature or astigmatism " +
               "of the refractor." + caveat,
               "Expected; there is no spacing to adjust. Check the focus on the center; a flattener matching the " +
               "refractor would reduce it.");
         else if (classicCat)
            add(ASSESS_NOTE, "Spacing", text + " A classic SCT or Maksutov has a curved field: this is its " +
               "field curvature or astigmatism." + caveat,
               "Expected; there is no spacing to adjust. Check the focus on the center; a reducer-corrector " +
               "would flatten the field.");
         else if (opticsType === OPTICS_UNCORRECTED)
            add(level, "Spacing", text + " In an uncorrected system this is field curvature or astigmatism." + caveat,
               "Check the focus; a field flattener or corrector would reduce it.");
         else
            add(level, "Spacing", text + caveat,
               ifCorrector + "decrease the " + distance + " in small steps. Defocus combined with " +
               "field curvature looks the same, so check the focus first." + fromBackFocus);
      }
   }

   // Off-axis blur (field curvature).
   if (s !== null && s.fCenter > 0) {
      let rel = (s.fEdge - s.fCenter) / s.fCenter;
      if (rel > 0.15) {
         let text = format("Stars at the field edge are %s larger than in the center (FWHM %.2f against %.2f px).",
            pct(rel), s.fEdge, s.fCenter);
         if (s.sagUm !== null) {
            text += format(" That is about %.0f µm of focus difference", s.sagUm);
            text += cfz !== null ? format(" (critical focus zone ±%.0f µm).", cfz) : ".";
         }
         let advice = cameraLens ?
               "Camera lenses vary in their edge correction by design; check focus and compare several frames." :
            (opticsType === OPTICS_REFRACTOR) ?
               "Expected for a refractor without a flattener; a flattener matching it would remove it." :
            classicCat ?
               "Expected for a classic SCT or Maksutov; a reducer-corrector would flatten the field." :
            bare ?
               "Expected for an uncorrected system; a flattener or corrector would reduce it." :
            P.flatByDesign ?
               "The field of this system is meant to be flat: the " + distance + " is probably off (see Spacing)." +
               fromBackFocus :
            corrected ?
               "The " + corrector + " does not flatten the field fully: check its spacing (see Spacing)." +
               (fromBackFocus || " Start from the back focus the manufacturer specifies.") :
               "With a corrector or flattener, check its spacing (see Spacing); without one this is expected.";
         // Without a flattener the curvature of a refractor (or a classic
         // SCT/Maksutov) is by design.
         let level = (cameraLens || opticsType === OPTICS_REFRACTOR || classicCat) ? ASSESS_NOTE :
            rel > 0.30 ? ASSESS_ACTION : ASSESS_NOTE;
         add(level, "Field curvature", text + caveat, advice);
      } else if (rel >= 0) {
         add(ASSESS_OK, "Field curvature", format("Field edge and center are about equally sharp (%s).", pct(rel)));
      }
   }

   // Coma and collimation (PSF asymmetry).
   let c = m.coma;
   if (c === undefined) {
      add(ASSESS_NOTE, "Coma", "Not measured.", "Enable \"Measure PSF asymmetry\" on Maps > Coma.");
   } else if (c === null) {
      add(ASSESS_NOTE, "Coma", "Too few stars with a valid asymmetry measurement.");
   } else if (!c.significant) {
      add(ASSESS_OK, "Coma", format("No significant field-dependent coma (k = %.3f ± %.3f).", c.k, c.kSd));
   } else {
      if (cameraLens) {
         add(ASSESS_NOTE, "Coma", format("Field-dependent asymmetry in the camera lens (k = %.3f ± %.3f).", c.k, c.kSd),
         "Possible causes include lens aberrations, lateral color in a color or broadband frame, or a tilted or " +
         "decentered lens. Compare a single color channel and several frames; check that the camera and lens sit " +
         "straight without play.");
      } else if (opticsType === OPTICS_REFRACTOR) {
         // Not a spacing question: a bare refractor has no corrector.
         add(ASSESS_NOTE, "Coma", format("Field-dependent asymmetry, the flares pointing %s the coma-free point " +
            "(k = %.3f ± %.3f). A refractor has hardly any coma by design.", c.k > 0 ? "away from" : "toward",
            c.k, c.kSd),
            "Likely causes: lateral color in a color or broadband frame, astigmatism of the curved field, or a " +
            "tilted or decentered lens. Compare with a narrowband frame or a single color channel; if it stays, " +
            "check the focuser and the lens cell.");
      } else if (c.k > 0) {
         let text = format("Under-corrected coma: the flares point away from the coma-free point (k = %.3f ± %.3f).",
            c.k, c.kSd);
         if (opticsType === OPTICS_UNCORRECTED)
            add(ASSESS_NOTE, "Coma", text + " Normal for an uncorrected Newtonian.", "A coma corrector would remove it.");
         else if (classicCat)
            add(ASSESS_NOTE, "Coma", text + " Normal for a classic SCT; a Maksutov has little coma, there it points " +
               "to collimation.", "A reducer-corrector or an aplanatic design would remove it.");
         else
            add(ASSESS_ACTION, "Coma", text + (P.flatByDesign ? " This system is meant to be coma-free." : "") +
               lateralColor,
               ifCorrector + "increase the " + distance + " in small steps." + fromBackFocus);
      } else {
         add(ASSESS_ACTION, "Coma", format("Over-corrected coma: the flares point toward the coma-free point " +
            "(k = %.3f ± %.3f).", c.k, c.kSd),
            ifCorrector + "decrease the " + distance + " in small steps." + fromBackFocus);
      }

      let dx = c.x0 - m.w / 2, dy = c.y0 - m.h / 2;
      let dist = Math.hypot(dx, dy);
      let off = dist / c.rMax;
      let sd = Math.hypot(c.x0Sd, c.y0Sd);
      if (off > 0.15 && isFinite(sd) && dist > 2 * sd) {
         let dirDeg = ((Math.atan2(dy, dx) * 180 / Math.PI) + 360) % 360;
         // Lenses are collimated at the factory, and only some cells can be
         // adjusted: the mechanics come first.
         let lensOptics = refractor || cameraLens || P.kind === "astrograph";
         let advice = lensOptics ?
               "The lenses are collimated at the factory: check first that the focuser, " +
               (opticsType === OPTICS_REFRACTOR_FLAT ? "the flattener, " : "") + "the adapters and the camera sit " +
               "straight and without play; only then the lens cell (only some can be adjusted). With the little " +
               "coma of a lens system, the coma-free point is uncertain - compare several frames." :
            P.kind === "sct" ?
               "Collimate with the secondary mirror's screws (defocused star in the center, rings concentric). " +
               "Focusing moves the primary: approach focus from the same direction, lock the mirror if possible." :
            classicCat ?
               "A classic SCT is collimated with the secondary mirror's screws; most Maksutovs are collimated at " +
               "the factory - check the visual back, the focuser and the camera for tilt first." :
            P.kind === "rasa" ?
               "Check that the camera sits centered and square on the corrector (adapter, tilt plate); then the " +
               "collimation of the primary mirror, where the model allows it." :
            (opticsType === OPTICS_UNCORRECTED) ? "Collimate the primary and secondary mirror." :
            (opticsType === OPTICS_CORRECTED) ? "Collimate the optics, or check the corrector for tilt or decentering." :
               "Collimate the optics; with a corrector, also check it for tilt or decentering.";
         // Uneven tracking adds a constant asymmetry along the drift, which
         // shifts the coma-free point in the same way (see logComaField()).
         if (t && t.ellipticity >= 0.03) {
            let d = Math.abs(((dirDeg - t.angleDeg) % 180 + 180) % 180);
            if (Math.min(d, 180 - d) < 20)
               advice += " Caution: the offset lies along the tracking elongation and may come from uneven " +
                  "tracking - compare several frames first.";
         }
         add(lensOptics ? ASSESS_NOTE : ASSESS_ACTION, "Collimation", format("The coma-free point lies %s of the " +
            "half diagonal off the center, toward the %s (%.0f°).", pct(off), side(dirDeg), dirDeg), advice);
      } else {
         add(ASSESS_OK, "Collimation", format("The coma-free point is near the center (%s of the half diagonal).",
            pct(off)));
      }
   }

   const ORDER = ["Mount", "Focus", "Tracking", "Tilt", "Collimation", "Spacing", "Coma", "Field curvature", "Data"];
   let actions = findings.filter(function(f) { return f.level === ASSESS_ACTION; });
   actions.sort(function(a, b) { return ORDER.indexOf(a.topic) - ORDER.indexOf(b.topic); });
   return { findings: findings, actions: actions };
}

// The assessment as text with console tags, for the Console and the
// dialog's Assessment page (a TextBox interprets the same tags).
function formatAssessment(assessment) {
   const LABELS = ["OK", "Note", "Action"];
   let lines = [];
   if (assessment.actions.length === 0) {
      lines.push("<b>No corrections needed.</b>");
   } else {
      lines.push("<b>Suggested order of corrections:</b>");
      for (let i = 0; i < assessment.actions.length; ++i)
         lines.push(format("   %d. %s", i + 1, assessment.actions[i].topic));
   }
   for (let i = 0; i < assessment.findings.length; ++i) {
      let f = assessment.findings[i];
      lines.push("");
      lines.push("<b>[" + LABELS[f.level] + "] " + f.topic + ":</b> " + f.text);
      if (f.advice.length > 0)
         lines.push("   → " + f.advice);
   }
   return lines.join("<br>");
}

// -----------------------------------------------------------------------
// The key of the Anthropic API key that versions up to 0.10 stored in
// PixInsight's settings; main() removes it (the script has no network
// access any more).
const OLD_AI_KEY_SETTING = SETTINGS_MODULE + "/anthropicApiKey";

// -----------------------------------------------------------------------
// The maps of an analysis. Each shows one measured quantity on the
// stretched frame and has its own tab in the dialog; the preview shows the
// map of the selected tab, Save opens one window per map, a series saves
// one PNG per map and frame.
//   size   star size: the 11x11 FWHM grid
//   shape  star shape: one ellipse per star (elongation and orientation),
//          the streamlines of its trend, optionally the orientation heatmap
//          as background, and the tracking marker
//   coma   PSF asymmetry: the coma field (arrows, coma-free point), its
//          streamlines and optionally one arrow per star
//   tilt   Siril-style tilt: the quadrant polygon and the tilt axis
const MAP_VIEWS = [
   { key: "size", label: "Star size", windowPrefix: "AberrationSize", fileSuffix: "_Size.png" },
   { key: "shape", label: "Star shape", windowPrefix: "AberrationShape", fileSuffix: "_Shape.png" },
   { key: "coma", label: "Coma", windowPrefix: "AberrationComa", fileSuffix: "_Coma.png" },
   { key: "tilt", label: "Tilt", windowPrefix: "AberrationTilt", fileSuffix: "_Tilt.png" }
];

function mapViewByKey(key) {
   return MAP_VIEWS.filter(function(v) { return v.key === key; })[0] || MAP_VIEWS[0];
}

// Draws the map `mapView` (a key of MAP_VIEWS) of an analysis with the
// given settings and returns it as a bitmap of the size of the target
// image. With verbose, notes and progress are written to the console; the
// dialog redraws its preview without them whenever a layer is switched.
function renderVectorMap(analysis, p, optics, verbose, mapView) {
   let w = analysis.w, h = analysis.h;
   let stars = starsFor(analysis, p.subtractTracking);
   let showComa = analysis.hasAsymmetry && p.measureAsymmetry;

   let background = analysis.background;
   if (mapView === "shape" && p.showOrientationHeatmap) {
      let heatmap = cachedLayer(analysis, "heatmap", p.subtractTracking + "/" + p.orientationHeatmapDegree, function() {
         Quiet.writeln("Computing orientation heatmap ...");
         let bmp = computeOrientationHeatmapBitmap(stars, w, h, p.orientationHeatmapDegree);
         if (bmp === null)
            Console.warningln(format("Orientation heatmap: too few stars for a meaningful " +
               "degree-%d fit - using the normal background instead.", p.orientationHeatmapDegree));
         return bmp;
      });
      if (heatmap !== null)
         background = heatmap;
   }

   // The cached background stays untouched: the layers are drawn on a copy.
   let bmp = new Bitmap(w, h);
   bmp.fill(0xff000000);
   let g = new Graphics(bmp);
   g.drawBitmap(0, 0, background);
   g.antialiasing = true;

   if (mapView === "shape")
      drawShapeLayers(g, analysis, stars, p, verbose);
   else if (mapView === "size")
      drawSizeLayers(g, w, h, stars, p, optics);
   else if (mapView === "coma")
      drawComaLayers(g, analysis, stars, p, showComa, verbose);
   else if (mapView === "tilt")
      drawTiltLayers(g, w, h, stars, p, optics);

   g.end();
   return bmp;
}

// Star shape: the streamlines of the elongation trend first (so that the
// per-star ellipses on top of them stay readable), the ellipses, and the
// tracking marker.
function drawShapeLayers(g, analysis, stars, p, verbose) {
   let w = analysis.w, h = analysis.h;
   let ev = shapeEvaluationFor(analysis, p);
   // The cell grid of the evaluation first, under all layers.
   drawCellGrid(g, w, h, ev.gridSize);
   if (p.showStreamlines) {
      let lines = cachedLayer(analysis, "streamlines", p.subtractTracking + "/" + p.streamlineRadiusPercent + "/" +
            p.shapeSignificantOnly, function() {
         let radius = Math.sqrt(w * w + h * h) * (p.streamlineRadiusPercent / 100);
         Quiet.writeln("Computing streamlines ...");
         let slT0 = Date.now();
         let result = generateStreamlines(stars, w, h, radius, p.shapeSignificantOnly ?
            function(x, y) { return shapeSignificantAt(ev.grid, x, y); } : null);
         Quiet.writeln(format("Streamlines finished after %.1f s (%d lines).",
            (Date.now() - slT0) / 1000, result.length));
         return result;
      });
      drawStreamlines(g, lines, ev.grid);
   }

   if (!p.hideEllipses) {
      let eccMax = 0;
      for (let i = 0; i < stars.length; ++i)
         eccMax = Math.max(eccMax, stars[i].eccentricity);
      if (eccMax <= 0) eccMax = 1;

      let progress = verbose ? withProgress("Drawing star ellipses", stars.length) : null;
      for (let i = 0; i < stars.length; ++i) {
         let s = stars[i];
         let len = s.eccentricity * p.vectorScale * 100; // px, scaled for visibility

         // Instead of a double-headed arrow, the PSF shape is drawn as an
         // ellipse: the unambiguous direction that a single arrowhead would
         // suggest doesn't exist anyway (theta and theta+180° describe the
         // same ellipse). Major semi-axis = half the vector length, minor
         // semi-axis derived from the eccentricity (e = sqrt(1-(b/a)^2)),
         // so the shape and size of the ellipse directly show the measured
         // eccentricity.
         let a = len / 2;
         let e = s.eccentricity;
         let b = a * Math.sqrt(Math.max(0, 1 - e * e));

         g.pen = new Pen(eccColor(s.eccentricity, eccMax), 2);
         drawOrientedEllipse(g, s.x, s.y, a, b, -s.rotation); // image angle = -theta (y down)

         // small dot at the centroid
         g.pen = new Pen(0xFFFFFFFF, 1);
         g.drawEllipse(s.x - 1.5, s.y - 1.5, s.x + 1.5, s.y + 1.5);

         if (progress)
            progress.update(i + 1);
      }
      if (progress)
         progress.end();
   }

   // The optics model on top of the measured layers: its streamlines and
   // the cells it does not explain.
   if (p.shapeShowModel && ev.model) {
      let radius = Math.sqrt(w * w + h * h) * (p.streamlineRadiusPercent / 100);
      let modelLines = cachedLayer(analysis, "modelStreamlines", p.subtractTracking + "/" + p.streamlineRadiusPercent,
         function() {
            return traceField(function(x, y) { return shapeModelFieldAt(ev.model, x, y); }, w, h, radius, null);
         });
      drawModelStreamlines(g, modelLines, w, h, ev.model, p.shapeModelSignificantOnly);
   }
   if (p.shapeShowMatch && ev.model)
      drawModelMatchCells(g, w, h, ev);
   if (p.shapeShowCells)
      drawShapeCells(g, w, h, ev, p.vectorScale);
   if (p.shapeShowDefects)
      drawShapeDefects(g, w, h, ev.defects);

   if (analysis.tracking)
      drawTrackingMarker(g, w, h, analysis.tracking, p.subtractTracking);
}

// The shape evaluation (evaluateShapeField()) of an analysis for the
// current tracking subtraction and streamline radius, cached.
function shapeEvaluationFor(analysis, p) {
   let stars = starsFor(analysis, p.subtractTracking);
   return cachedLayer(analysis, "shapeEvaluation", p.subtractTracking + "/" + p.streamlineRadiusPercent, function() {
      let radius = Math.hypot(analysis.w, analysis.h) * (p.streamlineRadiusPercent / 100);
      return evaluateShapeField(stars, analysis.w, analysis.h, radius);
   });
}

// The mean shape of each cell of the 11x11 grid: a headless bar along the
// mean axis, its length the mean elongation (scaled like the ellipses),
// with a fan of ± the axis uncertainty (se / (2 · elongation), at most
// 90°). Significant cells (magnitude ≥ SHAPE_MIN_Z se) are drawn bright
// with their fan; the others thin and gray: round within the noise.
// Uncertain cells (too few stars) get a gray dot only.
const SHAPE_CELL_COLOR = 0xFFFFFFFF;
const SHAPE_CELL_FAN = 0x60FFFFFF;
const SHAPE_CELL_WEAK = 0xA0B0B0B0;
function drawShapeCells(g, w, h, ev, vectorScale) {
   let n = ev.gridSize, cellW = w / n, cellH = h / n;
   let half = Math.min(cellW, cellH) * 0.45;
   // Length: the 90th percentile of the significant cells fills the cell.
   let chis = ev.cells.filter(function(c) { return c && !c.uncertain && c.z >= SHAPE_MIN_Z; })
      .map(function(c) { return c.chi; }).sort(function(a, b) { return a - b; });
   let ref = Math.max(0.02, chis.length > 0 ? chis[Math.floor(0.9 * (chis.length - 1))] : 0.05);
   let lw = Math.max(2, Math.round(Math.min(cellW, cellH) * 0.02));
   g.antialiasing = true;
   for (let r = 0; r < n; ++r) {
      for (let c = 0; c < n; ++c) {
         let e = ev.cells[r * n + c];
         let ccx = (c + 0.5) * cellW, ccy = (r + 0.5) * cellH;
         if (!e)
            continue;
         if (e.uncertain) {
            g.fillEllipse(ccx - lw, ccy - lw, ccx + lw, ccy + lw, new Brush(SHAPE_CELL_WEAK));
            continue;
         }
         let len = Math.min(1.2, e.chi / ref) * half * vectorScale;
         let dx = Math.cos(e.psi) * len, dy = Math.sin(e.psi) * len;
         let significant = e.z >= SHAPE_MIN_Z;
         if (significant) {
            let spread = Math.min(Math.PI / 2, e.se / (2 * Math.max(1e-6, e.chi)));
            [0, Math.PI].forEach(function(side) {
               let pts = [new Point(ccx, ccy)];
               for (let k = 0; k <= 12; ++k) {
                  let a = e.psi + side - spread + 2 * spread * k / 12;
                  pts.push(new Point(ccx + Math.cos(a) * len, ccy + Math.sin(a) * len));
               }
               g.fillPolygon(pts, new Brush(SHAPE_CELL_FAN));
            });
            g.pen = new Pen(0xC0000000, lw + 2);
            g.drawLine(ccx - dx, ccy - dy, ccx + dx, ccy + dy);
            g.pen = new Pen(SHAPE_CELL_COLOR, lw);
         } else {
            g.pen = new Pen(SHAPE_CELL_WEAK, Math.max(1, lw / 2));
         }
         g.drawLine(ccx - dx, ccy - dy, ccx + dx, ccy + dy);
      }
   }
}

// The streamlines of the optics model, solid, in a light violet - the
// complement of green, so it stands out on frames with a green cast (no
// color calibration), and distinct from the white/gray measured lines, the
// orange coma and the magenta cell frames - on an opaque dark halo that
// carries it on any background. Where the model is not significant
// (shapeModelSignificantAt()) a muted gray-violet, or nothing with
// hideWeak.
const MODEL_LINE_COLOR = 0xFFD08CFF;
const MODEL_LINE_WEAK = 0xFF8A7A9E;
function drawModelStreamlines(g, lines, w, h, fit, hideWeak) {
   g.antialiasing = true;
   let core = Math.max(3, Math.round(Math.min(w, h) / 1200));
   let significant = function(pts, j) {
      return shapeModelSignificantAt(fit, 0.5 * (pts[j - 1][0] + pts[j][0]), 0.5 * (pts[j - 1][1] + pts[j][1]));
   };
   let pass = function(pen, wanted) {
      g.pen = pen;
      drawLineRuns(g, lines, function(pts, j) { return significant(pts, j) === wanted; });
   };
   if (!hideWeak) {
      pass(new Pen(0xF0000000, core + 4), false);
      pass(new Pen(MODEL_LINE_WEAK, core), false);
   }
   pass(new Pen(0xF0000000, core + 4), true);
   pass(new Pen(MODEL_LINE_COLOR, core), true);
}

// How well the optics model matches each cell: a magenta frame where the
// cell's mean residual is 3σ or more (structure the model does not
// explain), a green one where it is less (the cell agrees with the model
// within its noise); no frame for a cell with too few stars.
const MATCH_BAD_COLOR = 0xFFFF4FD8;
const MATCH_GOOD_COLOR = 0xFF4CFF6A;
function drawModelMatchCells(g, w, h, ev) {
   let n = ev.gridSize, cellW = w / n, cellH = h / n;
   let lw = Math.max(2, Math.round(Math.min(cellW, cellH) * 0.015));
   ev.model.judgedCells.forEach(function(b) {
      let x0 = b.col * cellW + lw, y0 = b.row * cellH + lw;
      g.pen = new Pen(0xC0000000, lw + 2);
      g.drawRect(x0, y0, x0 + cellW - 2 * lw, y0 + cellH - 2 * lw);
      g.pen = new Pen(b.z >= 3 ? MATCH_BAD_COLOR : MATCH_GOOD_COLOR, lw);
      g.drawRect(x0, y0, x0 + cellW - 2 * lw, y0 + cellH - 2 * lw);
   });
}

// The singular points of the smoothed field: a ring with its index, yellow
// for +, cyan for - (no optics produce a - point).
function drawShapeDefects(g, w, h, defects) {
   let size = Math.max(10, Math.round(Math.min(w, h) / 60));
   let font = new Font("Helvetica", Math.round(size * 0.9));
   try { font.bold = true; } catch (e) { /* not critical */ }
   g.font = font;
   g.antialiasing = true;
   defects.forEach(function(dft) {
      let color = dft.index > 0 ? 0xFFFFE45C : 0xFF5CE1FF;
      g.pen = new Pen(0xC0000000, 5);
      g.drawEllipse(dft.x - size, dft.y - size, dft.x + size, dft.y + size);
      g.pen = new Pen(color, 3);
      g.drawEllipse(dft.x - size, dft.y - size, dft.x + size, dft.y + size);
      let label = (dft.index > 0 ? "+" : "\u2212") + (Math.abs(dft.index) === 0.5 ? "\u00BD" : "1");
      drawTextWithHalo(g, dft.x + size * 1.2, dft.y - size * 0.6, label, color, Math.round(size * 0.9));
   });
}

// Arcseconds per pixel, or 0 without pixel pitch and focal length
// (optics.pixelPitchUm is the effective pitch, 2x after SuperPixel
// debayering, see resolveOptics()).
function fwhmArcsecScale(optics) {
   return (optics.pixelPitchUm > 0 && optics.focalLengthMm > 0) ?
      206.265 * optics.pixelPitchUm / optics.focalLengthMm : 0;
}

// What each cell of the FWHM grid shows (p.fwhmCellValue).
const FWHM_CELL_PX = 0, FWHM_CELL_ARCSEC = 1, FWHM_CELL_RATIO = 2;

// The unit of the FWHM in the cells, the evaluation and the cell details:
// { scale, unit } - arcseconds when chosen and known, else pixels.
function fwhmDisplayUnit(p, optics) {
   let arcsec = fwhmArcsecScale(optics);
   if (p.fwhmCellValue === FWHM_CELL_ARCSEC && arcsec > 0)
      return { scale: arcsec, unit: "\"" }; // plain ASCII: the ″ glyph is not guaranteed in the bitmap font
   return { scale: 1, unit: "px" };
}

// Star size: the FWHM grid.
function drawSizeLayers(g, w, h, stars, p, optics) {
   let u = fwhmDisplayUnit(p, optics);
   // Arcseconds without pixel pitch or focal length: pixels instead.
   let ratio = p.fwhmCellValue === FWHM_CELL_RATIO;
   drawFwhmGrid(g, w, h, evaluateFwhmGrid(stars, w, h, FWHM_GRID_SIZE), {
      px: !ratio && u.unit === "px",
      arcsecScale: !ratio && u.unit !== "px" ? u.scale : 0,
      ratio: ratio,
      details: p.fwhmGridDetails, detailScale: u.scale, detailUnit: u.unit });
}

// Tilt: Siril's quadrant polygon and the tilt axis. Without stars in every
// quadrant and ring, a note says so.
function drawTiltLayers(g, w, h, stars, p, optics) {
   let tilt = computeSirilTilt(stars, w, h);
   if (tilt === null) {
      let fontSize = Math.max(14, Math.round(Math.min(w, h) / 40));
      g.font = new Font("Helvetica", fontSize);
      drawTextWithHalo(g, fontSize, fontSize * 2, "Not every quadrant and ring has stars - no tilt evaluation.",
         0xFFFFCCB2, fontSize);
      return;
   }
   drawSirilTiltPolygon(g, w, h, tilt);
   if (p.showTiltAxis)
      drawTiltAxis(g, w, h, computeTiltAxis(tilt, w, h, optics.pixelPitchUm, optics.focalLengthMm, optics.apertureMm));
}

// The evaluation of the Tilt map as text lines ("Topic: text"; lines
// starting with spaces belong to the line above). FWHM in the unit of
// fwhmDisplayUnit(); texts joined with + (see formatShapeEvaluation()).
function formatTiltEvaluation(tilt, axis, scale, unit) {
   if (tilt === null)
      return ["Quadrants: not every quadrant and ring has stars - not evaluated."];
   let fw = function(v) { return format("%.2f", v * scale) + (unit === "px" ? " px" : unit); };
   let q = [tilt.m1, tilt.m2, tilt.m3, tilt.m4];
   let mean = (q[0] + q[1] + q[2] + q[3]) / 4;
   let best = Math.min.apply(null, q), worst = Math.max.apply(null, q);
   let lines = ["Quadrants (25%-trimmed mean FWHM):"];
   ["Top left", "Top right", "Bottom left", "Bottom right"].forEach(function(name, i) {
      lines.push("   " + name + ": " + fw(q[i]) + " (" + format("%+.0f", 100 * (q[i] - mean) / mean) + "% of the mean)");
   });
   lines.push("Sensor tilt (Siril): " + fw(worst - best) + " between the softest and the sharpest quadrant (" +
      format("%.0f", 100 * (worst - best) / mean) + "% of the mean).");
   lines.push("Off-axis aberration (Siril): " + fw(tilt.mOuter - tilt.mInner) + " (outer ring " + fw(tilt.mOuter) +
      " against inner circle " + fw(tilt.mInner) + ").");
   if (axis)
      lines.push("Tilt axis: direction " + format("%.0f", axis.directionDeg) + "\u00B0" +
         (axis.angleDeg !== null ? ", tilt angle " + format("%.3f", axis.angleDeg) + "\u00B0" :
            " (the tilt angle needs pixel pitch, focal length and aperture)") + ".");
   return lines;
}

// Coma: the streamlines of the asymmetry field, the per-star arrows and the
// coma field (median arrows, coma-free point). Without the asymmetry
// measurement, a note says so.
function drawComaLayers(g, analysis, stars, p, showComa, verbose) {
   let w = analysis.w, h = analysis.h;
   if (!showComa) {
      let fontSize = Math.max(14, Math.round(Math.min(w, h) / 40));
      g.font = new Font("Helvetica", fontSize);
      drawTextWithHalo(g, fontSize, fontSize * 2,
         "PSF asymmetry not measured - enable \"Measure PSF asymmetry\" on Maps > Coma.", 0xFFFFCCB2, fontSize);
      if (verbose)
         Console.warningln("Coma map: needs \"Measure PSF asymmetry\" - only the background is drawn.");
      return;
   }
   if (p.showComaStreamlines) {
      let lines = cachedLayer(analysis, "comaStreamlines",
         p.comaStreamlineRadiusPercent + "/" + p.comaStreamlineMinPercent, function() {
         let radius = Math.sqrt(w * w + h * h) * (p.comaStreamlineRadiusPercent / 100);
         Quiet.writeln("Computing coma streamlines ...");
         let cT0 = Date.now();
         let res = generateComaStreamlines(stars, w, h, radius, p.comaStreamlineMinPercent);
         Quiet.writeln(format("Coma streamlines finished after %.1f s (%d lines, stop below |m3| = %.3f).",
            (Date.now() - cT0) / 1000, res.lines.length, res.minMag));
         return res.lines;
      });
      drawComaStreamlines(g, w, h, lines);
   }
   if (p.showStarAsymArrows)
      drawStarAsymmetryArrows(g, w, h, stars, p.vectorScale, p.starAsymMinSigma, p.starAsymColorByStrength, !verbose);
   drawComaOverlay(g, w, h, stars, analysis.comaFit, p.vectorScale, p.showComaArrows);
}

// -----------------------------------------------------------------------
// Reads pixel pitch, focal length and aperture from the FITS header of the
// view's window, where available:
//   pixel pitch  XPIXSZ (µm; by convention already includes binning)
//   focal length FOCALLEN (mm)
//   aperture     APTDIA (mm), otherwise FOCALLEN / FOCRATIO
// Values outside a plausible range are ignored. Returns an object with the
// three values (0 = not found) and, per value, the keyword it came from.
function readOpticsFromHeader(view) {
   let result = { pixelPitchUm: 0, focalLengthMm: 0, apertureMm: 0, sources: {} };
   let keywords = [];
   try {
      keywords = view.window.keywords;
   } catch (e) {
      Console.warningln("Could not read FITS keywords: " + e.message);
      return result;
   }
   let values = {};
   for (let i = 0; i < keywords.length; ++i) {
      let name = String(keywords[i].name).trim().toUpperCase();
      let raw = String(keywords[i].value).trim().replace(/^'+|'+$/g, "").trim();
      let v = parseFloat(raw);
      if (isFinite(v) && !(name in values))
         values[name] = v;
   }
   function take(name, lo, hi) {
      let v = values[name];
      return (v !== undefined && v >= lo && v <= hi) ? v : 0;
   }

   let pitch = take("XPIXSZ", 0.5, 50);
   if (pitch > 0) {
      result.pixelPitchUm = pitch;
      result.sources.pixelPitchUm = "XPIXSZ";
   }
   let focal = take("FOCALLEN", 10, 20000);
   if (focal > 0) {
      result.focalLengthMm = focal;
      result.sources.focalLengthMm = "FOCALLEN";
   }
   let aperture = take("APTDIA", 10, 3000);
   if (aperture > 0) {
      result.apertureMm = aperture;
      result.sources.apertureMm = "APTDIA";
   } else {
      let fRatio = take("FOCRATIO", 1, 50);
      if (fRatio > 0 && focal > 0) {
         result.apertureMm = focal / fRatio;
         result.sources.apertureMm = "FOCALLEN/FOCRATIO";
      }
   }
   return result;
}

// The camera named in the FITS header of the view's window (INSTRUME, with
// the binning from XBINNING/YBINNING when above 1), or "" when absent.
function readCameraFromHeader(view) {
   let values = {};
   try {
      let keywords = view.window.keywords;
      for (let i = 0; i < keywords.length; ++i) {
         let name = String(keywords[i].name).trim().toUpperCase();
         if (!(name in values))
            values[name] = String(keywords[i].value).trim().replace(/^'+|'+$/g, "").trim();
      }
   } catch (e) {
      return "";
   }
   let camera = values["INSTRUME"] || "";
   if (camera.length === 0)
      return "";
   let bx = parseInt(values["XBINNING"]), by = parseInt(values["YBINNING"]);
   if (bx > 1 || by > 1)
      camera += format(" (bin %dx%d)", bx > 0 ? bx : 1, by > 0 ? by : bx);
   return camera;
}

// -----------------------------------------------------------------------
// The mount. No FITS keyword names its type, so it is estimated:
//   PIERSIDE East/West   only a German equatorial mount has a pier side
//                        (and a meridian flip)
//   TELESCOP             the mount driver's name (ASCOM/INDI call the mount
//                        "telescope"): EQMod, GS Server, iOptron CEM/GEM,
//                        AM5 ... equatorial; Alt-Az, AZ-GTi, Seestar, Dwarf,
//                        NexStar ... alt-azimuth
// otherwise unknown; with "Read the values from the FITS header" unchecked,
// the dialog's choice. An alt-azimuth mount without a derotator turns the
// field during the exposure: the stars become arcs around the rotation
// center, a tangential pattern growing with the distance - which looks
// like field curvature or too short a corrector spacing.
const MOUNT_TYPES = ["Unknown", "German equatorial (GEM)", "Equatorial fork", "Alt-azimuth, tracked", "Not tracked"];
const MOUNT_UNKNOWN = 0, MOUNT_GEM = 1, MOUNT_FORK = 2, MOUNT_ALTAZ = 3, MOUNT_NONE = 4;

// The FITS keywords of a view as { NAME: value string } (first occurrence).
function viewKeywordValues(view) {
   let values = {};
   try {
      let keywords = view.window.keywords;
      for (let i = 0; i < keywords.length; ++i) {
         let name = String(keywords[i].name).trim().toUpperCase();
         if (!(name in values))
            values[name] = String(keywords[i].value).trim().replace(/^'+|'+$/g, "").trim();
      }
   } catch (e) {
      // no keywords: nothing to estimate from
   }
   return values;
}

// { type (index of MOUNT_TYPES), source (text) } from the keywords.
function estimateMount(values) {
   let pier = (values["PIERSIDE"] || "").toUpperCase();
   if (pier.indexOf("EAST") >= 0 || pier.indexOf("WEST") >= 0)
      return { type: MOUNT_GEM, source: "PIERSIDE " + values["PIERSIDE"] };
   let tel = values["TELESCOP"] || "";
   if (/alt.?az|\baz\b|az-?gti|seestar|dwarf|vespera|stellina|nexstar|evolution|\bslt\b/i.test(tel))
      return { type: MOUNT_ALTAZ, source: "TELESCOP '" + tel + "'" };
   if (/eqmod|gsserver|gs server|green ?swamp|\beq\d|heq|neq|\bcem|\bgem|ioptron|10micron|mach ?\d|astro-?physics|losmandy|avalon|\bam[35]\b|onstep|cgem|\bcgx|\bcge\b/i.test(tel))
      return { type: MOUNT_GEM, source: "TELESCOP '" + tel + "'" };
   return { type: MOUNT_UNKNOWN, source: "" };
}

// Guiding. No FITS keyword says whether it ran, let alone how; the ASIAIR
// writes the guide camera (GUIDECAM), which shows only that one was there.
// So the user chooses it (saved, also with the header option); with
// "Unknown" and a guide camera in the header it counts as guided, method
// unknown. It decides what a common elongation (tracking component) means:
//   not guided       the mount: periodic error of the worm, polar drift
//   guide scope      differential flexure between guide scope and main
//                    optics, mirror flop - or the guiding itself, wind
//   off-axis guider  no differential flexure: the guiding, wind - or, the
//                    same in every frame, astigmatism on the axis
const GUIDING_TYPES = ["Unknown", "Not guided", "Guide scope", "Off-axis guider (OAG)", "Guided, method unknown"];
const GUIDING_UNKNOWN = 0, GUIDING_OFF = 1, GUIDING_SCOPE = 2, GUIDING_OAG = 3, GUIDING_ON = 4;

// { type, source } - the user's choice, else a guide camera in the header.
function effectiveGuiding(guidingType, values) {
   let type = Math.range(guidingType || 0, 0, GUIDING_TYPES.length - 1);
   if (type !== GUIDING_UNKNOWN)
      return { type: type, source: "dialog" };
   let cam = values["GUIDECAM"] || "";
   if (cam.length > 0)
      return { type: GUIDING_ON, source: "GUIDECAM '" + cam + "'" };
   return { type: GUIDING_UNKNOWN, source: "" };
}

// What a common elongation means with this guiding: the likely causes, for
// the assessment and the series' static/dynamic tracking.
function guidingCauses(type) {
   switch (type) {
   case GUIDING_OFF:
      return "Not guided: the mount's tracking - the periodic error of the worm (on an EQ6-R one period takes " +
         "about 479 s, so it differs from frame to frame) and the drift of the polar alignment. Guiding removes " +
         "both.";
   case GUIDING_SCOPE:
      return "Guided with a guide scope: when it is the same in every frame, differential flexure between guide " +
         "scope and main optics (often changing with altitude), mirror flop or a moving focuser - or astigmatism " +
         "on the axis; when it changes from frame to frame, the guiding (RMS, oscillation, DEC backlash), balance " +
         "and wind.";
   case GUIDING_OAG:
      return "Guided with an off-axis guider (no differential flexure): when it is the same in every frame, " +
         "rather astigmatism on the axis (optics) or a moving focuser; when it changes from frame to frame, the " +
         "guiding (RMS, oscillation, DEC backlash), balance and wind.";
   case GUIDING_ON:
      return "Guided, method unknown: with a guide scope differential flexure or mirror flop, with an off-axis " +
         "guider rather astigmatism on the axis when it is the same in every frame; changing from frame to frame, " +
         "the guiding, balance and wind. Choose the guiding method (Setup > General) for a clearer verdict.";
   default:
      return "";
   }
}

// What the field rotation needs from the header: exposure (s), altitude,
// azimuth and site latitude (°); null where missing.
function mountObservation(values) {
   let take = function(names) { let k = keywordNumber(values, names); return k ? k.value : null; };
   return { exposureS: take(["EXPTIME", "EXPOSURE"]), altDeg: take(["CENTALT", "OBJCTALT"]),
            azDeg: take(["CENTAZ", "OBJCTAZ"]), latDeg: take(["SITELAT", "LAT-OBS", "OBSGEO-B"]) };
}

// The arc a star at the image corner describes during the exposure on an
// alt-azimuth mount (px of an image w x h): field rotation rate
// ω = Ω cos φ cos A / cos h (Ω = Earth's rotation, φ latitude, A azimuth,
// h altitude) times the exposure times the half diagonal. null without the
// values or near the zenith.
function fieldRotationSmearPx(obs, w, h) {
   if (!obs || obs.exposureS === null || obs.altDeg === null || obs.azDeg === null || obs.latDeg === null)
      return null;
   let r = Math.PI / 180;
   let cosAlt = Math.cos(obs.altDeg * r);
   if (cosAlt < 0.05)
      return null;
   let rate = 7.2921e-5 * Math.cos(obs.latDeg * r) * Math.cos(obs.azDeg * r) / cosAlt; // rad/s
   return Math.abs(rate) * obs.exposureS * Math.hypot(w, h) / 2;
}

// -----------------------------------------------------------------------
// Runs the diagnostics for a given target image: the analysis (unless a
// kept one is passed in), the console summary and the maps, then - unless
// previewOnly - one window per map (MAP_VIEWS), the optional 3D plot and
// CSV export. A preview draws only the map `mapView` (default: the first).
// p holds the settings under the names of ScriptParameters (the script's
// parameters object or the dialog's current settings). Used by the
// interactive dialog (Preview and Apply) and by the direct drag&drop apply
// ("New Instance" onto a target image). viewBitmap: see analyzeView().
// Returns { analysis, bitmap (the map mapView), assessment }, or null when
// the analysis failed.
function processView(view, p, previewOnly, analysis, viewBitmap, mapView) {
   mapView = mapView || MAP_VIEWS[0].key;
   let runT0 = Date.now();
   Console.writeln((previewOnly ? "Calculate: " : "Save: ") + view.id);

   // Permanently remember all options of this run for future runs (even
   // without a process icon).
   if (p !== parameters)
      parameters.assign(p);
   parameters.SaveSettings();

   // The preview always measures the PSF asymmetry and always closes a
   // debayer intermediate window (see analyzeView()).
   // The time of the star measurement: the Series tab estimates a run from it.
   let measureSeconds = null;
   if (!analysis) {
      let measureT0 = Date.now();
      analysis = analyzeView(view, p, previewOnly || p.measureAsymmetry, previewOnly, viewBitmap);
      if (analysis === null)
         return null;
      measureSeconds = (Date.now() - measureT0) / 1000;
   } else {
      Quiet.writeln("Reusing the star measurement of the preview.");
   }

   let optics = resolveOptics(view, p, analysis.debayer, false);
   let stars = starsFor(analysis, p.subtractTracking);
   Progress.step("Evaluating", 0.85, 0.88);
   let report = reportAnalysis(analysis, stars, p, optics, QUIET_CONSOLE);
   let tilt = report.tilt;
   let assessment = assessImage(report.metrics, p.opticsType);
   Progress.step("Drawing the maps", 0.88, 1);
   let views = previewOnly ? [mapViewByKey(mapView)] : MAP_VIEWS;
   let maps = {};
   views.forEach(function(v) { maps[v.key] = renderVectorMap(analysis, p, optics, true, v.key); });

   if (!previewOnly) {
      if (p.show3DTiltPlot && !optics.physical)
         Console.warningln("3D sensor tilt plot skipped: it needs pixel pitch, focal length and aperture " +
            "(Setup > General or FITS header).");
      if (tilt !== null && optics.physical && p.show3DTiltPlot) {
         Progress.step("Drawing the 3D tilt plot", 1, 1);
         build3DTiltPlot(tilt, analysis.w, analysis.h, optics.pixelPitchUm, optics.focalLengthMm, optics.apertureMm);
      }

      Progress.step("Opening the map windows", 1, 1);
      MAP_VIEWS.forEach(function(v) {
         let window = new ImageWindow(analysis.w, analysis.h, 3, 8, false, true,
            uniqueViewId(v.windowPrefix + "_" + analysis.targetViewId));
         window.mainView.beginProcess(UndoFlag.NoSwapFile);
         window.mainView.image.blend(maps[v.key]);
         window.mainView.endProcess();
         window.show();
         window.zoomToFit();
      });

      if (p.doExport) {
         // Next to the original file, named after it; an image that was
         // never saved goes to the temporary directory under its view id.
         let filePath = view.window.filePath;
         let path = filePath.length > 0 ?
            File.extractDrive(filePath) + File.extractDirectory(filePath) + "/" +
               File.extractName(filePath) + "_aberration.csv" :
            File.systemTempDirectory + "/" + analysis.targetViewId + "_aberration.csv";
         Progress.step("Exporting the CSV table", 1, 1);
         exportCSV(stars, path);
      }
   }

   Progress.endStep();
   Console.writeln(format("Done in %.1f s.", (Date.now() - runT0) / 1000));
   return { analysis: analysis, bitmap: maps[mapView], assessment: assessment, measureSeconds: measureSeconds };
}

// -----------------------------------------------------------------------
// Series analysis: several unregistered light frames of one night, before
// and after a meridian flip. After the flip, gravity pulls on the optical
// train from the other side: a value that changes with the pier side is
// mechanical (sag, play, mirror flop, balance), one that stays the same is
// fixed in the optical train. The frames are measured one by one with the
// dialog's settings; no map is drawn.
//
// The pier side of a frame, in this order:
//   manual      set by the user in the file list
//   PIERSIDE    written by N.I.N.A. and most ASCOM/INDI capture programs
//   angle       a camera angle keyword (ROTATOR as written by the ASIAIR from
//               its plate solve, POSANGLE, ANGLE, CROTA2) turns by 180° at
//               the flip: the frames fall into two groups, named by the
//               hour angles of their members
//   hour angle  from RA, the time and the site longitude: before the
//               meridian no flip has happened yet, well after it one has;
//               close after the meridian the side stays unknown
// Sides are named as N.I.N.A. writes PIERSIDE: West before the meridian
// (telescope pointing east), East after the flip.
const SIDE_WEST = "West", SIDE_EAST = "East", SIDE_A = "Side A", SIDE_B = "Side B", SIDE_UNKNOWN = "?";
const SERIES_HA_WEST_MAX = -0.05; // h: up to here, the mount cannot have flipped yet
const SERIES_HA_EAST_MIN = 0.5;   // h: from here on, it has flipped (flips come a few minutes late)
const SERIES_ANGLE_KEYS = ["ROTATOR", "POSANGLE", "ANGLE", "CROTA2"];
const SIP_TERMS = ["A_2_0", "A_1_1", "A_0_2", "B_2_0", "B_1_1", "B_0_2"];

// The quadratic SIP distortion of a frame as the shift (px) of the four
// corners of the solved image: [dx, dy] of top left, top right, bottom left,
// bottom right (8 values), or null. Quadratic terms do not depend on the
// reference pixel (CRPIX), so they can be compared even where the rest of
// the solution is missing.
function sipCornerShifts(frame) {
   if (!frame.sip)
      return null;
   let t = frame.sip.terms, u0 = frame.sip.w / 2, v0 = frame.sip.h / 2;
   let out = [];
   [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(function(c) {
      let u = c[0] * u0, v = c[1] * v0;
      out.push(t.A_2_0 * u * u + t.A_1_1 * u * v + t.A_0_2 * v * v);
      out.push(t.B_2_0 * u * u + t.B_1_1 * u * v + t.B_0_2 * v * v);
   });
   return out;
}

// The largest corner shift (px) of sipCornerShifts().
function sipMaxShift(c) {
   let best = 0;
   for (let i = 0; i < c.length; i += 2)
      best = Math.max(best, Math.hypot(c[i], c[i + 1]));
   return best;
}

// The sensor tilt (degrees) a pure perspective would need to produce the
// perspective part of the quadratic terms (x' ≈ x - p·x² - q·xy,
// y' ≈ y - p·xy - q·y²), or null. Real sensor tilts stay below about 0.2°:
// a larger value means the terms have other causes.
function sipTiltEquivalentDeg(frame) {
   if (!frame.sip || !(frame.pitchUm > 0) || !(frame.focalMm > 0))
      return null;
   let t = frame.sip.terms;
   let p = -0.5 * (t.A_2_0 + t.B_1_1), q = -0.5 * (t.A_1_1 + t.B_0_2);
   let fPx = frame.focalMm * 1000 / frame.pitchUm;
   return Math.atan(Math.hypot(p, q) * fPx) * 180 / Math.PI;
}

// The FITS keywords of a file as { NAME: value }, read without the pixels.
// The first occurrence of a name wins; strings lose their quotes.
function readFileKeywords(path) {
   let ext = File.extractExtension(path).toLowerCase();
   let fmt = new FileFormat(ext, true, false);
   if (fmt.isNull)
      throw new Error("No installed file format can read '" + ext + "' files.");
   let f = new FileFormatInstance(fmt);
   if (f.isNull)
      throw new Error("Cannot instantiate the file format " + fmt.name + ".");
   let info = f.open(path, "verbosity 0");
   if (!info || info.length < 1)
      throw new Error("Cannot open the file.");
   let values = {};
   try {
      let keywords = f.keywords;
      for (let i = 0; i < keywords.length; ++i) {
         let name = String(keywords[i].name).trim().toUpperCase();
         if (!(name in values))
            values[name] = String(keywords[i].value).trim().replace(/^'+|'+$/g, "").trim();
      }
   } finally {
      f.close();
   }
   return values;
}

// "22 47 43", "+58:03:01" -> 22.795..., 58.050...
function parseSexagesimal(s) {
   let parts = s.trim().split(/[ :]+/);
   let v = 0;
   for (let i = parts.length - 1; i >= 0; --i)
      v = Math.abs(parseFloat(parts[i])) + v / 60;
   return parts[0].charAt(0) === "-" ? -v : v;
}

// The first of the keywords that holds a number: { value, name,
// sexagesimal }, or null.
function keywordNumber(values, names) {
   for (let i = 0; i < names.length; ++i) {
      let raw = values[names[i]];
      if (raw === undefined || raw.length === 0)
         continue;
      let sexagesimal = /^[+-]?\d+([ :]+\d+(\.\d*)?){1,2}$/.test(raw);
      let v = sexagesimal ? parseSexagesimal(raw) : parseFloat(raw);
      if (isFinite(v))
         return { value: v, name: names[i], sexagesimal: sexagesimal };
   }
   return null;
}

// A FITS date (UTC, any number of fraction digits) in ms since 1970, or null.
function parseFitsDate(s) {
   let m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)/.exec(s || "");
   if (!m)
      return null;
   return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0) + parseFloat(m[6]) * 1000;
}

// Greenwich mean sidereal time in degrees (accurate to seconds of time).
function gmstDeg(timeMs) {
   let d = (timeMs - Date.UTC(2000, 0, 1, 12, 0, 0)) / 86400000;
   return ((280.46061837 + 360.98564736629 * d) % 360 + 360) % 360;
}

// What the header of a light frame tells about when and where it was
// taken. The pier side itself is decided for the whole series in
// assignSeriesSides().
function readSeriesFrame(path) {
   let frame = {
      path: path, name: File.extractName(path) + File.extractExtension(path),
      timeMs: null, haH: null, altDeg: null, pierSide: null, angleDeg: null, angleKey: null,
      focusPos: null, tempC: null, bayer: false, sip: null, pitchUm: null, focalMm: null,
      side: SIDE_UNKNOWN, sideSource: "", manualSide: null,
      headerError: null, metrics: null, error: null
   };
   let v;
   try {
      v = readFileKeywords(path);
   } catch (x) {
      frame.headerError = x.message || String(x);
      return frame;
   }

   // The middle of the exposure.
   let t = parseFitsDate(v["DATE-AVG"]);
   if (t === null) {
      t = parseFitsDate(v["DATE-OBS"]);
      let exposure = keywordNumber(v, ["EXPTIME", "EXPOSURE"]);
      if (t !== null && exposure)
         t += 500 * exposure.value;
   }
   frame.timeMs = t;

   let ra = keywordNumber(v, ["RA", "OBJCTRA"]);
   let raDeg = ra ? (ra.sexagesimal ? 15 * ra.value : ra.value) : null;
   let dec = keywordNumber(v, ["DEC", "OBJCTDEC"]);
   let lon = keywordNumber(v, ["SITELONG", "LONG-OBS", "OBSGEO-L"]); // east positive
   let lat = keywordNumber(v, ["SITELAT", "LAT-OBS", "OBSGEO-B"]);
   if (t !== null && raDeg !== null && lon)
      frame.haH = ((((gmstDeg(t) + lon.value - raDeg) % 360) + 540) % 360 - 180) / 15;

   let alt = keywordNumber(v, ["CENTALT", "OBJCTALT"]);
   if (alt) {
      frame.altDeg = alt.value;
   } else if (frame.haH !== null && dec && lat) {
      let r = Math.PI / 180;
      frame.altDeg = Math.asin(Math.sin(lat.value * r) * Math.sin(dec.value * r) +
         Math.cos(lat.value * r) * Math.cos(dec.value * r) * Math.cos(15 * frame.haH * r)) / r;
   }

   let pier = (v["PIERSIDE"] || "").toUpperCase();
   frame.pierSide = pier.indexOf("WEST") >= 0 ? SIDE_WEST : pier.indexOf("EAST") >= 0 ? SIDE_EAST : null;
   let angle = keywordNumber(v, SERIES_ANGLE_KEYS);
   if (angle) {
      frame.angleDeg = angle.value;
      frame.angleKey = angle.name;
   }
   let focus = keywordNumber(v, ["FOCPOS", "FOCUSPOS"]);
   frame.focusPos = focus ? focus.value : null;
   let temp = keywordNumber(v, ["FOCTEMP", "FOCUSTEM", "AMBTEMP"]);
   frame.tempC = temp ? temp.value : null;
   frame.bayer = (v["BAYERPAT"] || "").length > 0;
   let pitch = keywordNumber(v, ["XPIXSZ"]), focal = keywordNumber(v, ["FOCALLEN"]);
   frame.pitchUm = pitch ? pitch.value : null;
   frame.focalMm = focal ? focal.value : null;

   // The quadratic SIP distortion terms of a plate solve (A_p_q, B_p_q with
   // p + q = 2), and the size of the image that was solved (IMAGEW/IMAGEH,
   // else the file's own size): the terms are in its pixels.
   let order = keywordNumber(v, ["A_ORDER"]);
   if (order && order.value >= 2) {
      let terms = {};
      let complete = SIP_TERMS.every(function(t) {
         let k = keywordNumber(v, [t]);
         terms[t] = k ? k.value : NaN;
         return k !== null;
      });
      let w = keywordNumber(v, ["IMAGEW", "NAXIS1"]), h = keywordNumber(v, ["IMAGEH", "NAXIS2"]);
      let nw = keywordNumber(v, ["NAXIS1"]), nh = keywordNumber(v, ["NAXIS2"]);
      if (complete && w && h)
         frame.sip = { order: order.value, terms: terms, w: w.value, h: h.value,
                       fileW: nw ? nw.value : null, fileH: nh ? nh.value : null };
   }
   return frame;
}

// Sets side and sideSource of every frame (see the overview above).
function assignSeriesSides(frames) {
   let opposite = function(s) { return s === SIDE_WEST ? SIDE_EAST : SIDE_WEST; };
   let byHourAngle = function(f) {
      if (f.haH === null)
         return SIDE_UNKNOWN;
      return f.haH <= SERIES_HA_WEST_MAX ? SIDE_WEST : f.haH >= SERIES_HA_EAST_MIN ? SIDE_EAST : SIDE_UNKNOWN;
   };

   // Camera angle groups of the frames without PIERSIDE: 0 = the angle of
   // the first such frame, 1 = turned by 180°, null = neither.
   let group = frames.map(function() { return null; });
   let ref = null;
   for (let i = 0; i < frames.length; ++i) {
      let f = frames[i];
      if (f.pierSide !== null || f.angleDeg === null)
         continue;
      if (ref === null)
         ref = f.angleDeg;
      let d = Math.abs((((f.angleDeg - ref) % 360) + 540) % 360 - 180); // 0 ... 180
      group[i] = d < 45 ? 0 : d > 135 ? 1 : null;
   }
   // Each group is named by the majority of its members' hour angles.
   let groupSide = [0, 1].map(function(g) {
      let west = 0, east = 0;
      for (let i = 0; i < frames.length; ++i) {
         if (group[i] !== g)
            continue;
         let s = byHourAngle(frames[i]);
         if (s === SIDE_WEST) ++west;
         else if (s === SIDE_EAST) ++east;
      }
      return west > east ? SIDE_WEST : east > west ? SIDE_EAST : null;
   });
   if (groupSide[0] !== null && groupSide[0] === groupSide[1])
      groupSide = [null, null];
   if (groupSide[0] === null && groupSide[1] !== null)
      groupSide[0] = opposite(groupSide[1]);
   if (groupSide[1] === null && groupSide[0] !== null)
      groupSide[1] = opposite(groupSide[0]);
   if (groupSide[0] === null)
      groupSide = [SIDE_A, SIDE_B];

   for (let i = 0; i < frames.length; ++i) {
      let f = frames[i];
      if (f.manualSide) {
         f.side = f.manualSide;
         f.sideSource = "manual";
      } else if (f.pierSide) {
         f.side = f.pierSide;
         f.sideSource = "PIERSIDE";
      } else if (group[i] !== null) {
         f.side = groupSide[group[i]];
         f.sideSource = f.angleKey + " angle";
      } else {
         f.side = byHourAngle(f);
         f.sideSource = f.side !== SIDE_UNKNOWN ? "hour angle" :
            f.haH !== null ? "near the meridian" : "no data";
      }
   }
}

// A direction in image coordinates (0° = right, 90° = down) in words.
function directionName(deg) {
   const NAMES = ["right", "lower right", "bottom", "lower left", "left", "upper left", "top", "upper right"];
   return NAMES[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
}

// The values compared between the sides, per frame from the metrics of
// reportAnalysis(); null where a frame has none. Vectors ([x, y]) are
// compared as vectors. minDiff: the smallest difference that matters in
// practice (relMinDiff: as a fraction of the value). changed/stable: what a
// change at the flip, or a clear value that stays, means.
const SERIES_METRICS = [
   {
      key: "fwhmCenter", label: "FWHM center", relMinDiff: 0.05,
      get: function(m) { return m.surface ? m.surface.fCenter : null; },
      show: function(v) { return format("%.2f px", v); },
      changed: "The focus differs between the sides: refocusing, temperature, or focuser slip under gravity."
   },
   {
      key: "fwhmEdge", label: "FWHM edge", relMinDiff: 0.05,
      get: function(m) { return m.surface ? m.surface.fEdge : null; },
      show: function(v) { return format("%.2f px", v); },
      changed: "The field edge changes with the side: play in the focuser or the corrector/flattener, or a " +
         "different focus."
   },
   {
      key: "tilt", label: "Tilt", vector: true, minDiff: 0.03,
      get: function(m) {
         let s = m.surface;
         if (s === null || s === undefined)
            return null;
         let rel = (s.fBad - s.fGood) / Math.max(1e-6, 0.5 * (s.fBad + s.fGood));
         let a = s.tiltDirDeg * Math.PI / 180;
         return [rel * Math.cos(a), rel * Math.sin(a)];
      },
      show: function(v) {
         let rel = Math.hypot(v[0], v[1]);
         return format("%.0f%% softer toward the %s", 100 * rel,
            directionName(Math.atan2(v[1], v[0]) * 180 / Math.PI));
      },
      changed: "The tilt changes with the side: the camera, the focuser or the adapter sags or has play " +
         "under gravity. Tighten the focuser lock, the camera connection and the adapter threads before " +
         "adjusting the tilt - an adjustment would only fit one side.",
      stable: "The tilt is the same on both sides: it is fixed in the optical train, so adjusting it " +
         "(tilt plate, adapter) is worthwhile.",
      stableMin: 0.05
   },
   {
      key: "gridCorners", label: "Corners / center (grid)", minDiff: 0.05,
      get: function(m) { return m.grid ? m.grid.cornerRatio : null; },
      show: function(v) { return format("x%.2f", v); },
      changed: "The corners soften differently relative to the center on the two sides: the field curvature " +
         "or the corrector/flattener spacing changes under gravity (play in the focuser or the drawtube), " +
         "or the focus differs."
   },
   {
      key: "eRad", label: "Edge elongation eps_rad", minDiff: 0.02,
      get: function(m) { return m.rings ? m.rings.eRad : null; },
      show: function(v) { return format("%+.3f", v); },
      changed: "The radial/tangential pattern at the edge changes with the side: play in the corrector/flattener or " +
         "the focuser drawtube, or a different focus. Fix that before changing the spacing.",
      stable: "The edge pattern is the same on both sides: the spacing verdict of a single frame holds.",
      stableMin: 0.04
   },
   {
      key: "tracking", label: "Tracking elongation", minDiff: 0.02,
      get: function(m) { return m.tracking ? m.tracking.ellipticity : null; },
      show: function(v) { return format("%.3f", v); },
      changed: "Tracking differs between the sides: balance (the mount is east- or west-heavy after the " +
         "flip), DEC backlash, cable drag, or wind from one side."
   },
   {
      key: "comaK", label: "Coma k", minDiff: 0,
      get: function(m) { return m.coma ? m.coma.k : null; },
      show: function(v) { return format("%+.3f", v); },
      changed: "The coma strength changes with the side: the corrector/flattener or its spacing has play."
   },
   {
      key: "comaPoint", label: "Coma-free point", vector: true, minDiff: 0.1,
      get: function(m) {
         let c = m.coma;
         return c ? [(c.x0 - m.w / 2) / c.rMax, (c.y0 - m.h / 2) / c.rMax] : null;
      },
      show: function(v) {
         return format("%.0f%% of the half diagonal toward the %s", 100 * Math.hypot(v[0], v[1]),
            directionName(Math.atan2(v[1], v[0]) * 180 / Math.PI));
      },
      changed: "The collimation shifts with the side: mirror flop (in an SCT, Maksutov or RASA the primary " +
         "mirror moved by the focuser), a loose secondary or corrector, or a sagging focuser (in a refractor: " +
         "the focuser, the flattener or the lens cell). Secure the optics before collimating.",
      stable: "The coma-free point is off center on both sides alike: the collimation is off, but stable - " +
         "collimating is worthwhile.",
      stableMin: 0.15
   },
   {
      // From the header (plate solve), not from the star shapes.
      key: "sipDistortion", label: "Plate-solve distortion (SIP)", vector: true, minDiff: 1.0,
      get: function(m, f) { return f ? sipCornerShifts(f) : null; },
      show: function(v) { return format("corners shifted by up to %.1f px", sipMaxShift(v)); },
      spread: function(v) { return format("%.1f px", v); },
      showDiff: function(a, b) {
         return format("a corner moves by up to %.1f px between the sides",
            sipMaxShift(a.map(function(x, k) { return b[k] - x; })));
      },
      changed: "The geometry of the image changes with the side: a part of the optical train shifts or tilts " +
         "under gravity - in a Newtonian, SCT, Maksutov or RASA typically the primary mirror (mirror flop), in a refractor the lens " +
         "cell, or a coma corrector, flattener or the focuser moving in the drawtube. If the coma-free point moves as well, that supports it. A " +
         "change of sign can also come from the plate solver (a solution in a frame that turns with the sky): " +
         "to be sure, solve one frame per side with PixInsight's ImageSolver and compare.",
      stable: "The plate-solve distortion is the same on both sides: nothing in the optical train shifts " +
         "measurably under gravity.",
      stableMin: 0
   }
];

function seriesMetric(key) {
   return SERIES_METRICS.filter(function(m) { return m.key === key; })[0];
}

// The length of a vector, and of the difference of two.
function vectorNorm(v) {
   return Math.sqrt(v.reduce(function(p, x) { return p + x * x; }, 0));
}
function vectorDistance(a, b) {
   return vectorNorm(a.map(function(x, k) { return x - b[k]; }));
}

// Robust spread (MAD scaled to a standard deviation).
function robustSpread(values) {
   if (values.length < 2)
      return NaN;
   let m = medianOf(values);
   return 1.4826 * medianOf(values.map(function(v) { return Math.abs(v - m); }));
}

// Median and spread of a metric's values (numbers or [x, y]).
function seriesStats(values, vector) {
   if (values.length === 0)
      return null;
   if (!vector)
      return { n: values.length, median: medianOf(values), sd: robustSpread(values) };
   // Per component; the spread is the root mean square of the components'.
   let median = [], sum2 = 0;
   for (let k = 0; k < values[0].length; ++k) {
      let c = values.map(function(v) { return v[k]; });
      median.push(medianOf(c));
      let sd = robustSpread(c);
      sum2 += sd * sd;
   }
   return { n: values.length, median: median, sd: Math.sqrt(sum2 / median.length) };
}

// Compares the measured frames of a series between its two sides and looks
// for drifts over the session. Returns { sides, counts, rows, notes }; each
// row holds a metric, the stats per side, the difference and a verdict:
// "changes" (at the flip), "stable", "unclear" (a relevant difference within
// the scatter) or "few" (fewer than 3 frames on a side).
function compareSeries(frames) {
   let measured = frames.filter(function(f) { return f.metrics !== null; });
   let counts = {};
   measured.forEach(function(f) { counts[f.side] = (counts[f.side] || 0) + 1; });
   let sides = [SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B].filter(function(s) { return counts[s] > 0; });
   if (sides.length > 2) // mixed naming (e.g. manual West/East plus angle groups A/B)
      sides = sides.slice(0, 2);
   let notes = [];

   // The SIP terms are compared only when all solutions refer to images of
   // the same size (the same solver and binning).
   let solved = measured.filter(function(f) { return f.sip; });
   let sizes = {};
   solved.forEach(function(f) { sizes[f.sip.w + "x" + f.sip.h] = true; });
   let sipComparable = Object.keys(sizes).length === 1;
   if (solved.length > 0) {
      if (!sipComparable)
         notes.push("The plate solves refer to images of different sizes (" + Object.keys(sizes).join(", ") +
            " px): their distortion terms are not compared.");
      else if (solved.length < measured.length)
         notes.push(format("Only %d of %d measured frames carry a plate solve (SIP); the distortion is compared " +
            "for those.", solved.length, measured.length));
      let f0 = solved[0];
      if (sipComparable && f0.sip.fileW !== null && (f0.sip.w !== f0.sip.fileW || f0.sip.h !== f0.sip.fileH))
         notes.push(format("The plate solves were made on a %.0f x %.0f px version of the frames (the files have " +
            "%.0f x %.0f px), e.g. inside the capture software; the distortion refers to that image.",
            f0.sip.w, f0.sip.h, f0.sip.fileW, f0.sip.fileH));
   }

   let rows = SERIES_METRICS.map(function(metric) {
      let valuesOf = function(side) {
         let out = [];
         measured.forEach(function(f) {
            if (f.side !== side)
               return;
            if (metric.key === "sipDistortion" && !sipComparable)
               return;
            let v = metric.get(f.metrics, f);
            if (v !== null && v !== undefined && (metric.vector ? v.every(isFinite) : isFinite(v)))
               out.push(v);
         });
         return out;
      };
      let row = { metric: metric, stats: sides.map(function(s) { return seriesStats(valuesOf(s), metric.vector); }),
                  diff: null, verdict: null, drift: null };

      if (sides.length === 2 && row.stats[0] && row.stats[1]) {
         let a = row.stats[0], b = row.stats[1];
         row.diff = metric.vector ? vectorDistance(b.median, a.median) : b.median - a.median;
         let size = metric.vector ? 0.5 * (vectorNorm(a.median) + vectorNorm(b.median)) :
            0.5 * (Math.abs(a.median) + Math.abs(b.median));
         let minDiff = metric.relMinDiff ? metric.relMinDiff * size : metric.minDiff;
         // Standard error of a median ≈ 1.25 σ/√n.
         let se = 1.253 * Math.sqrt(a.sd * a.sd / a.n + b.sd * b.sd / b.n);
         let significant = isFinite(se) && Math.abs(row.diff) > 3 * se;
         if (a.n < 3 || b.n < 3)
            row.verdict = "few";
         else if (minDiff > 0 && Math.abs(row.diff) < minDiff)
            row.verdict = "stable";
         else if (significant)
            row.verdict = "changes";
         else
            row.verdict = (minDiff > 0) ? "unclear" : "stable";
      }

      // Drift over the session: the values minus their side's median against
      // the time, pooled over the sides (not for vectors).
      if (!metric.vector) {
         let t = [], r = [];
         sides.forEach(function(s, k) {
            if (!row.stats[k])
               return;
            measured.forEach(function(f) {
               let v = metric.get(f.metrics, f);
               if (f.side === s && f.timeMs !== null && v !== null && v !== undefined && isFinite(v)) {
                  t.push(f.timeMs / 3600000);
                  r.push(v - row.stats[k].median);
               }
            });
         });
         if (t.length >= 6) {
            let mt = t.reduce(function(p, q) { return p + q; }, 0) / t.length;
            let mr = r.reduce(function(p, q) { return p + q; }, 0) / r.length;
            let stt = 0, srr = 0, str = 0;
            for (let i = 0; i < t.length; ++i) {
               stt += (t[i] - mt) * (t[i] - mt);
               srr += (r[i] - mr) * (r[i] - mr);
               str += (t[i] - mt) * (r[i] - mr);
            }
            if (stt > 0 && srr > 0) {
               let corr = str / Math.sqrt(stt * srr);
               let slope = str / stt; // per hour
               let span = Math.max.apply(null, t) - Math.min.apply(null, t);
               let all = row.stats.filter(function(s) { return s; });
               let size = all.reduce(function(p, s) { return p + Math.abs(s.median); }, 0) / all.length;
               let minDiff = metric.relMinDiff ? metric.relMinDiff * size : metric.minDiff;
               if (Math.abs(corr) >= 0.6 && Math.abs(slope * span) >= Math.max(minDiff, 1e-9))
                  row.drift = { perHour: slope, corr: corr, hours: span };
            }
         }
      }
      return row;
   });

   // Refocused between the sides?
   if (sides.length === 2) {
      let focusOf = function(side) {
         let v = measured.filter(function(f) { return f.side === side && f.focusPos !== null; })
            .map(function(f) { return f.focusPos; });
         return v.length > 0 ? medianOf(v) : null;
      };
      let fa = focusOf(sides[0]), fb = focusOf(sides[1]);
      if (fa !== null && fb !== null && fa !== fb)
         notes.push(format("The focuser position differs between the sides (%s %.0f, %s %.0f steps): the " +
            "frames were refocused, so differences in FWHM, edge pattern and coma may come from that " +
            "rather than from the flip. Tilt, collimation and tracking are hardly affected.",
            sides[0], fa, sides[1], fb));
   }
   let unknown = frames.filter(function(f) { return f.metrics !== null && f.side === SIDE_UNKNOWN; }).length;
   if (unknown > 0)
      notes.push(format("%d measured frame(s) have no known side (close after the meridian or without " +
         "time/position data) and are left out of the comparison. Set their side in the list if you know it.",
         unknown));
   if (sides.length < 2)
      notes.push("All measured frames are on one side: there is no flip to compare, only the drift over the " +
         "session.");
   if (frames.some(function(f) { return f.metrics !== null && f.bayerWarning; }))
      notes.push("Some frames are Bayer mosaics (BAYERPAT) measured without debayering: enable Debayer on " +
         "Setup > General, or the shapes are distorted by the color pattern.");

   return { sides: sides, counts: counts, rows: rows, notes: notes,
            measured: measured.length, total: frames.length };
}

// Measures every frame of the series with the settings p: opens the file
// in a hidden window, runs the analysis without the background and closes
// it again. Sets metrics (or error) per frame. shouldStop() is asked
// between the frames. The progress bar (Progress) covers the whole series.
// With onFrame, the background is rendered as well and onFrame(frame,
// analysis, optics, index, report) is called after each measured frame (to
// show or save its map); the analysis is not kept.
// "45 s", "12 min", "1.5 h".
function formatDuration(seconds) {
   if (seconds < 90)
      return format("%.0f s", Math.max(1, seconds));
   if (seconds < 90 * 60)
      return format("%.0f min", seconds / 60);
   return format("%.1f h", seconds / 3600);
}

// The part of the Series hints that depends on the list: the frames per
// side, what is missing for a comparison, and the estimated run time from
// timing ({ seconds per frame, source, maps } of the last Calculate or
// series run, or null).
function seriesHintsStatus(frames, timing, p) {
   let out = [];
   let counts = {};
   frames.forEach(function(f) { counts[f.side] = (counts[f.side] || 0) + 1; });
   let sides = [SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B].filter(function(s) { return counts[s] > 0; });
   let unknown = counts[SIDE_UNKNOWN] || 0;
   if (frames.length === 0) {
      out.push("<b>No frames yet</b> - add them with Add files... on the right.");
   } else {
      out.push(format("<b>%d frame%s</b>: ", frames.length, frames.length === 1 ? "" : "s") +
         sides.map(function(s) { return s + " " + counts[s]; }).concat(unknown > 0 ? ["side unknown " + unknown] : [])
            .join(", ") + ".");
      if (sides.length < 2)
         out.push("<b>Only one side</b>: add frames from the other side of the flip, else there is nothing to " +
            "compare - only the static optics and the drift over the session.");
      let few = sides.filter(function(s) { return counts[s] < 3; });
      if (sides.length >= 2 && few.length > 0)
         out.push("<b>Fewer than 3 frames</b> on " + few.join(" and ") + ": the sides are not compared until " +
            "each has at least 3.");
      if (unknown > 0)
         out.push(format("<b>%d frame%s without a known side</b> - left out of the comparison; set the side " +
            "with West / East.", unknown, unknown === 1 ? "" : "s"));
   }
   // Run time: about one star measurement per frame (plus opening the file).
   let maps = p.seriesShowMaps || p.seriesSaveMaps || p.seriesSaveMosaic;
   if (timing === null) {
      out.push("<b>Run time</b>: click Calculate on one frame of the series first - each frame takes about as " +
         "long as its star measurement.");
   } else {
      let per = format("about %s per frame", formatDuration(timing.seconds));
      let from = timing.source === "series" ? "from the last series run" : "from the last Calculate";
      let extra = "";
      if (maps && !(timing.source === "series" && timing.maps))
         extra = ", plus a few seconds per frame for the maps";
      else if (!maps && timing.source === "series" && timing.maps)
         extra = ", a little less without the maps";
      out.push("<b>Run time</b>: " + (frames.length > 0 ?
         format("about %s for %d frames (%s, %s%s).", formatDuration(timing.seconds * frames.length),
            frames.length, per, from, extra) :
         per + " (" + from + extra + ")."));
      out.push("Stop ends the run after the current frame; the frames measured so far are compared, pass 2 is " +
         "skipped.");
   }
   return out.map(function(t) { return "<p style='margin-top:0px; margin-bottom:4px;'>" + t + "</p>"; }).join("");
}

// Returns { stopped: true, after: k } when Stop ended the run after k of the
// frames, else { stopped: false }.
function measureSeries(frames, p, shouldStop, onFrame) {
   let pp = {};
   for (let name in p)
      pp[name] = p[name];
   pp.closeIntermediateWindows = true;
   let outer = Progress.listener;
   let n = frames.length;
   // Values of an earlier run belong to other settings, also for the
   // frames a stop leaves out.
   frames.forEach(function(f) {
      f.metrics = null;
      f.error = null;
      f.bayerWarning = false;
      f.mapBitmap = null;
      f.outputFiles = [];
      f.measurement = null;
   });
   for (let i = 0; i < n; ++i) {
      let f = frames[i];
      if (shouldStop && shouldStop()) {
         Console.warningln(format("Stopped after frame %d of %d: %d frame%s not measured.",
            i, n, n - i, n - i === 1 ? "" : "s"));
         return { stopped: true, after: i };
      }
      if (f.headerError) {
         f.error = f.headerError;
         Console.warningln(format("Series frame %d of %d: %s: cannot read the header (%s) - skipped.",
            i + 1, n, f.name, f.headerError));
         continue;
      }
      Console.noteln(format("Series frame %d of %d: %s", i + 1, n, f.name));
      Progress.listener = outer ? function(text, fraction) {
         outer(text === null ? null : format("%d/%d: %s", i + 1, n, text), (i + fraction) / n);
      } : null;
      let windows = null;
      try {
         Progress.step("Opening the file", 0, 0);
         windows = ImageWindow.open(f.path);
         if (!windows || windows.length < 1)
            throw new Error("Cannot open the file.");
         let view = windows[0].mainView;
         f.bayerWarning = f.bayer && !pp.debayer && !view.image.isColor;
         let analysis = analyzeView(view, pp, true, true, null, !onFrame);
         if (analysis === null)
            throw new Error("No stars could be measured.");
         let optics = resolveOptics(view, pp, analysis.debayer, false);
         Progress.step("Evaluating", 0.85, 0.88);
         let report = reportAnalysis(analysis, starsFor(analysis, pp.subtractTracking), pp, optics, SILENT_CONSOLE);
         f.metrics = report.metrics;
         // Kept for pass 2 (buildStaticSeries()): the stars as measured.
         f.measurement = { stars: analysis.stars, w: analysis.w, h: analysis.h, tracking: analysis.tracking,
                           hasAsymmetry: analysis.hasAsymmetry, debayer: analysis.debayer, optics: optics };
         if (onFrame) {
            // Only the display: a failure here does not fail the frame.
            try {
               onFrame(f, analysis, optics, i, report);
            } catch (x) {
               Console.warningln("Series frame " + f.name + ": cannot show the map: " + (x.message || x));
            }
         }
      } catch (x) {
         f.error = x.message || String(x);
         Console.warningln("Series frame " + f.name + ": " + f.error);
      } finally {
         Progress.endStep();
         if (windows)
            windows.forEach(function(w) { w.forceClose(); });
         Progress.listener = outer;
      }
   }
   return { stopped: false };
}

// -----------------------------------------------------------------------
// Pass 2 of a series: the static part of the optics, separated from the
// dynamic tracking. Pass 1 (measureSeries()) keeps the measured stars of
// every frame. The optics are fixed to the sensor, so the frames need no
// registration: their stars are pooled - all frames, and each side of the
// flip on its own - which lowers every standard error by about √n. Before
// pooling, each frame loses only its dynamic tracking, its own uniform term
// T_i less the mean over the group T̄; T̄ itself (constant tracking, or
// astigmatism on the axis - the same on a single frame) stays in the
// static result. The FWHM of each frame is scaled to the median center
// FWHM of the group, so that the changing seeing drops out and curvature
// and tilt remain. The result has the form of analyzeView()'s analysis, so
// the maps and evaluations work on it unchanged; its background is dark
// (the pooled stars belong to no single frame).
// Returns a list of { label, analysis } (empty without measured frames).
function buildStaticSeries(frames) {
   let measured = frames.filter(function(f) { return f.measurement; });
   if (measured.length === 0)
      return [];
   // Only frames of the most common size can be pooled.
   let sizes = {};
   measured.forEach(function(f) {
      let k = f.measurement.w + "x" + f.measurement.h;
      sizes[k] = (sizes[k] || 0) + 1;
   });
   let size = Object.keys(sizes).sort(function(a, b) { return sizes[b] - sizes[a]; })[0];
   let skipped = measured.filter(function(f) { return f.measurement.w + "x" + f.measurement.h !== size; });
   if (skipped.length > 0)
      Console.warningln(format("Series static: %d frame(s) of another image size left out.", skipped.length));
   measured = measured.filter(function(f) { return f.measurement.w + "x" + f.measurement.h === size; });
   let w = measured[0].measurement.w, h = measured[0].measurement.h;

   let groups = [{ label: "all", frames: measured }];
   [SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B].forEach(function(side) {
      let fs = measured.filter(function(f) { return f.side === side; });
      if (fs.length > 0 && fs.length < measured.length)
         groups.push({ label: side, frames: fs });
   });

   let background = null; // one dark bitmap for all groups
   return groups.map(function(group) {
      let fs = group.frames;
      let t1 = medianOf(fs.map(function(f) { return f.measurement.tracking.chi1; }));
      let t2 = medianOf(fs.map(function(f) { return f.measurement.tracking.chi2; }));
      let centers = fs.map(function(f) {
         let ev = evaluateFwhmGrid(f.measurement.stars, w, h, FWHM_GRID_SIZE);
         return ev.center ? ev.center.value : null;
      });
      let ref = medianOf(centers.filter(function(c) { return c !== null; }));
      let pooled = [];
      let dynamic = [];
      fs.forEach(function(f, i) {
         let m = f.measurement;
         let stars = m.stars.map(function(s) { return Object.assign(Object.create(Object.getPrototypeOf(s)), s); });
         let d1 = m.tracking.chi1 - t1, d2 = m.tracking.chi2 - t2;
         applyTrackingCorrection(stars, { chi1: d1, chi2: d2 });
         let k = (centers[i] > 0 && ref > 0) ? ref / centers[i] : 1;
         stars.forEach(function(s) { s.fwhmX *= k; s.fwhmY *= k; });
         pooled = pooled.concat(stars);
         dynamic.push({ name: f.name, side: f.side, d1: d1, d2: d2, chi: Math.hypot(d1, d2),
                        angleDeg: ((0.5 * Math.atan2(d2, d1) * 180 / Math.PI) % 180 + 180) % 180, fwhmScale: k });
      });
      let hasAsymmetry = fs.every(function(f) { return f.measurement.hasAsymmetry; });
      if (background === null) {
         background = new Bitmap(w, h);
         background.fill(0xFF101010);
      }
      let rms = Math.sqrt(dynamic.reduce(function(a, x) { return a + x.chi * x.chi; }, 0) / dynamic.length);
      let analysis = {
         key: "series:" + group.label,
         targetViewId: format("Series static, %s (%d frame%s)", group.label, fs.length, fs.length === 1 ? "" : "s"),
         debayer: fs[0].measurement.debayer,
         w: w, h: h,
         stars: pooled,
         tracking: computeTrackingComponent(pooled, w, h),
         hasAsymmetry: hasAsymmetry,
         comaFit: hasAsymmetry ? fitComaField(pooled, w, h, false) : null,
         comaFitCentroid: hasAsymmetry ? fitComaField(pooled, w, h, true) : null,
         background: background,
         layers: {},
         optics: fs[0].measurement.optics,
         series: { label: group.label, frames: fs.length, staticTracking: { chi1: t1, chi2: t2 },
                   dynamic: dynamic, dynamicRms: rms, fwhmCenter: ref }
      };
      return { label: group.label, analysis: analysis };
   });
}

// The separation of a group as text lines: the static tracking (with the
// pooled model's value), the dynamic tracking per frame and its scatter.
function formatStaticSeries(entry) {
   let a = entry.analysis, s = a.series;
   let num = function(v, digits) { return format("%." + digits + "f", v); };
   let deg = function(c1, c2) { return Math.round(((0.5 * Math.atan2(c2, c1) * 180 / Math.PI) % 180 + 180) % 180) % 180; };
   let lines = [];
   lines.push("Series static, " + entry.label + ": " + s.frames + " frame(s), " + a.stars.length +
      " stars pooled, FWHM scaled to the center " + num(s.fwhmCenter, 2) + " px.");
   lines.push("   Static tracking (median over the frames): " +
      num(Math.hypot(s.staticTracking.chi1, s.staticTracking.chi2), 3) + " along " +
      deg(s.staticTracking.chi1, s.staticTracking.chi2) + "° - constant tracking or astigmatism on the axis " +
      "(a single camera angle cannot tell them apart); pooled model: " + num(a.tracking.ellipticity, 3) +
      (a.tracking.se !== null ? " ± " + num(a.tracking.se, 3) : "") + ".");
   lines.push("   Dynamic tracking (frame to frame): rms " + num(s.dynamicRms, 3) + ", largest " +
      num(Math.max.apply(null, s.dynamic.map(function(x) { return x.chi; })), 3) + ".");
   let gd = a.optics && a.optics.guiding;
   if (gd && gd.type !== GUIDING_UNKNOWN)
      lines.push("   " + guidingCauses(gd.type));
   return lines;
}

// Saves a bitmap of a series frame, replacing a file of an earlier run, and
// lists the path in frame.outputFiles.
function saveSeriesFrameBitmap(frame, bmp, path) {
   if (File.exists(path))
      File.remove(path);
   if (bmp.save(path) === false)
      throw new Error("Cannot write " + path);
   frame.outputFiles.push(path);
   Console.writeln("Saved: " + path);
}

// The path of a frame's output file: its directory and name plus `suffix`.
function seriesFrameOutputPath(frame, suffix) {
   return File.extractDrive(frame.path) + File.extractDirectory(frame.path) + "/" +
      File.extractName(frame.path) + suffix;
}

// A 3x3 mosaic of the stretched image as AberrationInspector draws it: the
// four corners, the four edge centers and the image center, each cropped
// 1:1 to a square tile, separated by gray lines. Smaller images get
// smaller tiles so that the crops do not overlap.
const MOSAIC_TILE_SIZE = 512;      // px
const MOSAIC_SEPARATOR = 4;        // px, width of the gray lines between the tiles
const MOSAIC_SEPARATOR_COLOR = 0xFF808080;
function renderAberrationMosaic(bmp) {
   let t = Math.min(MOSAIC_TILE_SIZE, Math.floor(bmp.width / 3), Math.floor(bmp.height / 3));
   let sep = MOSAIC_SEPARATOR;
   let mosaic = new Bitmap(3 * t + 2 * sep, 3 * t + 2 * sep);
   mosaic.fill(MOSAIC_SEPARATOR_COLOR);
   let xs = [0, Math.floor((bmp.width - t) / 2), bmp.width - t];
   let ys = [0, Math.floor((bmp.height - t) / 2), bmp.height - t];
   for (let r = 0; r < 3; ++r)
      for (let c = 0; c < 3; ++c)
         mosaic.copy(c * (t + sep), r * (t + sep), bmp, xs[c], ys[r], xs[c] + t, ys[r] + t);
   return mosaic;
}

// Saves the images Apply would open for a frame of the series next to its
// file, named after it with a suffix, so that they sort right after it:
//   <name>_Size.png, <name>_Shape.png, <name>_Coma.png, <name>_Tilt.png   the maps (maps:
//                              { key: Bitmap } of MAP_VIEWS, drawn by the caller)
//   <name>_Aberration3D.png    the 3D tilt plot, when enabled and possible
// Existing files of an earlier run are replaced. The paths are listed in
// frame.outputFiles.
function saveSeriesFrameImages(frame, maps, analysis, optics, report, p) {
   MAP_VIEWS.forEach(function(v) {
      saveSeriesFrameBitmap(frame, maps[v.key], seriesFrameOutputPath(frame, v.fileSuffix));
   });
   if (p.show3DTiltPlot && optics.physical && report.tilt !== null)
      saveSeriesFrameBitmap(frame, render3DTiltPlot(report.tilt, analysis.w, analysis.h, optics.pixelPitchUm,
         optics.focalLengthMm, optics.apertureMm), seriesFrameOutputPath(frame, "_Aberration3D.png"));
}

// Saves the 3x3 mosaic of a series frame (renderAberrationMosaic(), from
// the stretched background of its analysis) next to the frame as
// <name>_Mosaic.png.
function saveSeriesFrameMosaic(frame, analysis) {
   saveSeriesFrameBitmap(frame, renderAberrationMosaic(analysis.background),
      seriesFrameOutputPath(frame, "_Mosaic.png"));
}

// The comparison as text with console tags (Console and the Series page).
function formatSeriesReport(result) {
   const VERDICTS = {
      changes: "<b>changes at the flip</b>",
      stable: "stable",
      unclear: "unclear (difference within the scatter)",
      few: "too few frames on one side for a verdict"
   };
   let sides = result.sides;
   let lines = [];
   lines.push(format("<b>%d of %d frames measured</b> - ", result.measured, result.total) +
      (sides.length > 0 ? sides.map(function(s) { return s + ": " + result.counts[s]; }).join(", ") : "no side known"));
   if (result.stop)
      lines.push(format("<b>Stopped after frame %d of %d</b>: %d frame%s not measured; pass 2 (Series static) " +
         "was skipped. Run the series again to compare all frames.", result.stop.after, result.total,
         result.total - result.stop.after, result.total - result.stop.after === 1 ? "" : "s"));
   let changes = result.rows.filter(function(r) { return r.verdict === "changes"; });
   if (sides.length === 2)
      lines.push(changes.length === 0 ?
         "<b>Nothing changes clearly at the flip</b> - the findings of a single frame hold for both sides." :
         "<b>Changes at the flip:</b> " + changes.map(function(r) { return r.metric.label; }).join(", ") + ".");

   result.rows.forEach(function(row) {
      let m = row.metric;
      let parts = [];
      sides.forEach(function(s, k) {
         let st = row.stats[k];
         if (st)
            parts.push(s + " " + m.show(st.median) + (isFinite(st.sd) ?
               " (± " + (m.spread ? m.spread(st.sd) : m.vector ? format("%.0f%%", 100 * st.sd) :
                  m.show(st.sd).replace(/^\+/, "")) + ")" : ""));
      });
      if (parts.length === 0)
         return;
      lines.push("");
      let text = "<b>" + m.label + ":</b> " + parts.join("; ");
      // For vectors, the size alone can stay while the direction turns.
      if (m.vector && row.diff !== null)
         text += "; " + (m.showDiff ? m.showDiff(row.stats[0].median, row.stats[1].median) :
            "difference " + format("%.0f%%", 100 * row.diff));
      if (row.verdict)
         text += " → " + VERDICTS[row.verdict];
      lines.push(text);
      if (row.verdict === "changes") {
         lines.push("   → " + m.changed);
      } else if (row.verdict === "stable" && m.stable) {
         let sizes = row.stats.map(function(st) {
            return m.vector ? vectorNorm(st.median) : Math.abs(st.median);
         });
         if (Math.min.apply(null, sizes) >= m.stableMin)
            lines.push("   → " + m.stable);
      }
      if (row.drift)
         lines.push(format("   Drifts over the session: %s per hour over %.1f h (r = %.2f) - temperature, " +
            "altitude or slow slip rather than the flip.", m.show(row.drift.perHour), row.drift.hours, row.drift.corr));
      if (row.drift && row.verdict === "changes")
         lines.push("   Caution: with that drift, part of the difference between the sides may be the drift - " +
            "compare frames taken shortly before and after the flip.");
   });

   if (result.notes.length > 0) {
      lines.push("");
      result.notes.forEach(function(n) { lines.push("<i>Note:</i> " + n); });
   }
   return lines.join("<br>");
}

// One line per frame with its side and measured values.
function exportSeriesCSV(frames, filePath) {
   let f = new File;
   f.createForWriting(filePath);
   f.outTextLn("File,Side,SideSource,TimeUTC,HourAngle_h,Altitude_deg,FocusPos,Temp_C,Stars," +
      "FWHMCenter_px,FWHMEdge_px,TiltRel,TiltDir_deg,GridCornerRatio,GridTiltRel,GridTiltSigma,GridTiltSoftCorner,EpsRad,TrackingEllipticity,TrackingAngle_deg," +
      "ComaK,ComaX0_px,ComaY0_px,SIPMaxShift_px,SIPTiltEquiv_deg," + SIP_TERMS.join(",") + ",Error");
   let num = function(v, digits) {
      return (v === null || v === undefined || !isFinite(v)) ? "" : v.toFixed(digits);
   };
   frames.forEach(function(fr) {
      let m = fr.metrics;
      let s = m ? m.surface : null;
      let tilt = m ? seriesMetric("tilt").get(m) : null;
      f.outTextLn([
         "\"" + fr.name.replace(/"/g, "\"\"") + "\"", fr.side, fr.sideSource,
         fr.timeMs !== null ? new Date(fr.timeMs).toISOString() : "",
         num(fr.haH, 3), num(fr.altDeg, 2), num(fr.focusPos, 0), num(fr.tempC, 2),
         m ? m.n : "",
         num(s ? s.fCenter : null, 3), num(s ? s.fEdge : null, 3),
         num(tilt ? Math.hypot(tilt[0], tilt[1]) : null, 4), num(s ? s.tiltDirDeg : null, 1),
         num(m && m.grid ? m.grid.cornerRatio : null, 3), num(m && m.grid ? m.grid.tiltRel : null, 4),
         num(m && m.grid ? m.grid.tiltSigma : null, 2), m && m.grid && m.grid.tiltSoftCorner ? m.grid.tiltSoftCorner : "",
         num(m && m.rings ? m.rings.eRad : null, 4),
         num(m && m.tracking ? m.tracking.ellipticity : null, 4), num(m && m.tracking ? m.tracking.angleDeg : null, 1),
         num(m && m.coma ? m.coma.k : null, 4), num(m && m.coma ? m.coma.x0 : null, 1),
         num(m && m.coma ? m.coma.y0 : null, 1),
         num(fr.sip ? sipMaxShift(sipCornerShifts(fr)) : null, 2), num(sipTiltEquivalentDeg(fr), 3),
         SIP_TERMS.map(function(t) { return fr.sip ? fr.sip.terms[t].toExponential(6) : ""; }).join(","),
         fr.error ? "\"" + fr.error.replace(/"/g, "\"\"") + "\"" : ""
      ].join(","));
   });
   f.close();
   Console.noteln("Series CSV exported to: " + filePath);
}

// -----------------------------------------------------------------------
// The dialog is laid out like AperturePhotometry: the parameters are
// grouped by topic into the pages of a TabBox, each page holding GroupBoxes
// with right-aligned field labels of one common width, next to a preview of
// the target image. Options that only matter when another one is checked
// are disabled otherwise (updateControls).
class StarAberrationDialog extends Dialog {
   constructor(view) {
      super();

      let d = this;
      this.view = view;


      this.windowTitle = TITLE;

      // Field labels share one width so that all edit fields line up;
      // check boxes are indented to the same column.
      const LABEL_ALIGNMENT = TextAlignment.Right | TextAlignment.VertCenter;
      this.labelWidth = Math.max(
         this.font.width("Min. strength (sigma):"),
         this.font.width("Smoothing radius (%):"),
         this.font.width("Max. stars (0 = all):")) + this.logicalPixelsToPhysical(6);

      let fieldLabel = function(text) {
         let label = new Label(d);
         label.text = text;
         label.textAlignment = LABEL_ALIGNMENT;
         label.setFixedWidth(d.labelWidth);
         return label;
      };
      // All input fields start at the same position and number fields have
      // the same width: a NumericEdit (pjsr/NumericControl.jsh) holds an
      // empty label and 4 px of spacing before its edit field, so any other
      // control (SpinBox, ComboBox, Edit) gets the same in front of it; the
      // edit field of a NumericEdit and a SpinBox get NUMBER_FIELD_CHARS.
      const NUMBER_FIELD_CHARS = "0000000000";
      let alignedField = function(control) {
         let numberWidth = d.font.width(NUMBER_FIELD_CHARS) + d.logicalPixelsToPhysical(8);
         if (control.edit instanceof Edit && control.label instanceof Label) { // a NumericEdit
            control.edit.setFixedWidth(numberWidth);
            return control;
         }
         if (control instanceof SpinBox)
            control.setFixedWidth(numberWidth + d.logicalPixelsToPhysical(16)); // + the arrows
         let field = new Control(d);
         field.sizer = new HorizontalSizer;
         field.sizer.spacing = 4;
         field.sizer.add(new Label(field));
         field.sizer.add(control);
         return field;
      };
      let fieldRow = function(label, control) {
         let row = new HorizontalSizer;
         row.spacing = 4;
         row.add(label);
         row.add(alignedField(control));
         row.addStretch();
         return row;
      };
      // A row without a field label whose controls start where the input
      // fields of fieldRow() start: a blank of the label's width, then the
      // same empty label and spacing as in front of every input field.
      let indentRow = function(controls, stretchLast) {
         let row = new HorizontalSizer;
         row.spacing = 4;
         let blank = new Label(d);
         blank.setFixedWidth(d.labelWidth);
         row.add(blank);
         row.add(new Label(d));
         controls.forEach(function(c, i) {
            row.add(c, (stretchLast && i === controls.length - 1) ? 100 : 0);
         });
         if (!stretchLast)
            row.addStretch();
         return row;
      };
      let checkRow = function(check) {
         return indentRow([check]);
      };
      let groupBox = function(title, items) {
         let group = new GroupBox(d);
         group.title = title;
         group.sizer = new VerticalSizer;
         group.sizer.margin = 6;
         group.sizer.spacing = 4;
         for (let i = 0; i < items.length; ++i)
            group.sizer.add(items[i]);
         return group;
      };
      // A tab page: the check box that enables its layer (if any) on top,
      // followed by the group boxes.
      let page = function(enableCheck, groups) {
         let control = new Control(d);
         control.sizer = new VerticalSizer;
         control.sizer.margin = 6;
         control.sizer.spacing = 6;
         if (enableCheck)
            control.sizer.add(enableCheck);
         for (let i = 0; i < groups.length; ++i)
            control.sizer.add(groups[i]);
         control.sizer.addStretch();
         return control;
      };

      // ---- Target / debayer (General page) --------------------------------

      this.label = new Label(this);
      // We leave the default alignment (left/vertically centered) as-is,
      // to stay independent of the exact name of the alignment constants.

      this.updateLabel = function() {
         if (d.view && d.view.id.length > 0)
            d.label.text = d.view.image.width + " x " + d.view.image.height + " pixels, " +
               d.view.image.numberOfChannels + " channel(s)";
         else
            d.label.text = "No target image selected.";
      };

      // Target image: the open (visible) main images, and as the last entry
      // the tips for the test frames, shown in the image area instead of an
      // image (as without any image). A ViewList cannot take an entry of
      // its own, so this is a combo box that a timer keeps in step with
      // the open images (refreshTargets()).
      const TIPS_ITEM = "Tips for the test frames";
      this.viewListLabel = fieldLabel("Target image:");
      this.targetCombo = new ComboBox(this);
      this.targetViews = [];
      this.targetSignature = null;
      this.openMainViews = function() {
         return ImageWindow.windows.filter(function(w) { return !w.isNull && w.visible; })
            .map(function(w) { return w.mainView; });
      };
      this.fillTargetCombo = function() {
         let views = d.openMainViews();
         d.targetViews = views;
         d.targetSignature = views.map(function(v) { return v.id; }).join("|");
         d.targetCombo.clear();
         views.forEach(function(v) { d.targetCombo.addItem(v.id); });
         d.targetCombo.addItem(TIPS_ITEM);
         let i = -1;
         if (d.view)
            for (let k = 0; k < views.length && i < 0; ++k)
               if (views[k].id === d.view.id)
                  i = k;
         d.targetCombo.currentItem = i >= 0 ? i : views.length;
      };
      this.selectTarget = function(v) {
         d.view = v;
         if (d.applyHeaderOptics)
            d.applyHeaderOptics();
         if (d.updateCamera)
            d.updateCamera();
         d.updateLabel();
         d.updateControls();
         d.updatePreview();
      };
      this.targetCombo.onItemSelected = function(index) {
         d.selectTarget(index < d.targetViews.length ? d.targetViews[index] : null);
      };
      // Images opened, closed or renamed: the list follows; a target that
      // was closed is dropped (the tips are shown). Not while a computation
      // runs - it opens and closes windows of its own.
      this.refreshTargets = function() {
         if (d.isBusy)
            return;
         let sig = d.openMainViews().map(function(v) { return v.id; }).join("|");
         if (sig === d.targetSignature)
            return;
         let current = d.view ? d.view.id : null;
         d.fillTargetCombo();
         if (current !== null && d.targetCombo.currentItem === d.targetViews.length)
            d.selectTarget(null);
      };
      this.targetTimer = new Timer;
      this.targetTimer.interval = 1; // s
      this.targetTimer.periodic = true;
      this.targetTimer.onTimeout = function() { d.refreshTargets(); };
      this.fillTargetCombo();

      let viewRow = new HorizontalSizer;
      viewRow.spacing = 4;
      viewRow.add(this.viewListLabel);
      viewRow.add(this.targetCombo, 100);

      let infoRow = indentRow([this.label], true);
      this.updateLabel();

      // The results of a Calculate as a file, and loaded again instead of a
      // measurement (saveResults(), loadResults()). Loaded results are the
      // kept analysis like those of a Calculate - with their own detection
      // settings and the FITS keywords of their image - until the next
      // Calculate or another target image.
      this.saveResultsButton = new PushButton(this);
      this.saveResultsButton.text = "Save results...";
      this.saveResultsButton.toolTip =
         "<p>Saves the stars measured by the last Calculate (or loaded results) as a file " +
         "<i>name</i>" + RESULTS_EXTENSION + ", by default next to the image: positions, FWHM, shape and " +
         "asymmetry of every star, the detection settings and the FITS header of the image - about 100 " +
         "bytes per star. The maps, the evaluations and the assessment are computed from it again on " +
         "loading; the current layer and optics settings are not part of it.</p>";
      this.saveResultsButton.onClick = function() {
         if (!d.analysis || !d.analysis.header) {
            (new MessageBox("Click Calculate first: there are no results to save.", TITLE, StdIcon.Information,
               StdButton.Ok)).execute();
            return;
         }
         let sfd = new SaveFileDialog;
         sfd.caption = "Save results";
         sfd.overwritePrompt = true;
         sfd.filters = [["Results files", "*.json"], ["All files", "*"]];
         sfd.initialPath = d.analysis.file || resultsDefaultPath(d.analysis.header);
         if (!sfd.execute())
            return;
         try {
            saveResults(d.analysis, d.collectParameters(), sfd.fileName);
         } catch (x) {
            (new MessageBox("Saving the results failed: " + (x.message || String(x)), TITLE, StdIcon.Error,
               StdButton.Ok)).execute();
         }
      };

      this.loadResultsButton = new PushButton(this);
      this.loadResultsButton.text = "Load results...";
      this.loadResultsButton.toolTip =
         "<p>Loads results saved with Save results and shows them as after a Calculate, without measuring " +
         "the stars again: the maps, their evaluations, the assessment and Save all work " +
         "with them. Optics, mount and observation values come from the saved FITS header (or the fields " +
         "above), the layers from the current settings.</p>" +
         "<p>The background is the image when it is open (same view and file), else dark. The detection " +
         "settings of the dialog do not apply to loaded results; the next Calculate measures the target " +
         "image again.</p>";
      this.loadResultsButton.onClick = function() {
         let ofd = new OpenFileDialog;
         ofd.caption = "Load results";
         ofd.multipleSelections = false;
         ofd.filters = [["Results files", "*" + RESULTS_EXTENSION, "*.json"], ["All files", "*"]];
         if (!ofd.execute())
            return;
         let analysis;
         try {
            analysis = loadResults(ofd.fileName);
         } catch (x) {
            (new MessageBox(x.message || String(x), TITLE, StdIcon.Error, StdButton.Ok)).execute();
            return;
         }
         Console.noteln(format("Results loaded (%d stars of %s, %d x %d px): %s", analysis.stars.length,
            analysis.targetViewId, analysis.w, analysis.h, analysis.file));
         d.analysis = analysis;
         d.previewStale = false;
         d.mapSource = 0;
         d.mapSourceCombo.currentItem = 0;
         d.updateTrackingInfo();
         d.updateResultsInfo();
         d.refreshPreview();
         d.showMapPage();
      };

      this.resultsInfoLabel = new Label(this);
      this.resultsInfoLabel.useRichText = true;
      this.resultsInfoLabel.wordWrapping = true;
      this.updateResultsInfo = function() {
         let an = d.analysis;
         d.resultsInfoLabel.text = (an && an.file) ?
            format("<i>Showing loaded results: %s (%d stars)</i>", File.extractNameAndExtension(an.file),
               an.stars.length) : "";
         d.resultsInfoLabel.visible = d.resultsInfoLabel.text.length > 0;
      };
      this.updateResultsInfo();
      let resultsRow = indentRow([this.loadResultsButton, this.saveResultsButton]);
      let resultsInfoRow = indentRow([this.resultsInfoLabel], true);

      // Debayer (SuperPixel) as a preprocessing step
      this.debayerCheck = new CheckBox(this);
      this.debayerCheck.text = "Debayer (SuperPixel) before star detection";
      this.debayerCheck.checked = parameters.debayer;
      this.debayerCheck.toolTip =
         "<p>Creates a debayered copy of the target image (SuperPixel method: direct " +
         "2x2 pixel grouping without interpolation, half resolution) and continues working " +
         "on this copy. The original image is left unchanged. Only enable this if the target " +
         "image is still an unprocessed Bayer mosaic (not true RGB).</p>" +
         "<p>It also applies to the frames of the Series tab. It is disabled for a color target image " +
         "unless the Series tab lists frames; color images are never debayered.</p>";

      // Bayer pattern (fallback, if "Auto" fails due to missing CFA metadata:
      // "Unable to acquire CFA pattern information")
      this.bayerPatternLabel = fieldLabel("Bayer pattern:");
      this.bayerPatternCombo = new ComboBox(this);
      this.bayerPatternItems = ["Auto", "RGGB", "BGGR", "GBRG", "GRBG"];
      for (let i = 0; i < this.bayerPatternItems.length; ++i)
         this.bayerPatternCombo.addItem(this.bayerPatternItems[i]);
      let bpIndex = this.bayerPatternItems.indexOf(parameters.bayerPattern);
      this.bayerPatternCombo.currentItem = (bpIndex >= 0) ? bpIndex : 0;
      this.bayerPatternCombo.toolTip =
         "<p>'Auto' reads the Bayer pattern from the image's FITS/RAW metadata. " +
         "If that fails ('Unable to acquire CFA pattern information'), manually select the " +
         "actual sensor pattern here (see the camera specification).</p>";

      // Automatically close intermediate windows (SuperPixel debayer
      // result + any noise-evaluation side windows) after processing
      this.closeWindowsCheck = new CheckBox(this);
      this.closeWindowsCheck.text = "Close intermediate windows after processing";
      this.closeWindowsCheck.checked = parameters.closeIntermediateWindows;
      this.closeWindowsCheck.toolTip =
         "<p>Closes the debayered SuperPixel intermediate window as well as any side windows " +
         "created during the debayer step (e.g. noise evaluation) once the vector map has " +
         "finished drawing. Only relevant if debayering is enabled.</p>";

      // ---- Detection -----------------------------------------------------

      // Threshold
      this.thresholdLabel = fieldLabel("Threshold (sigma):");
      this.thresholdSpin = new NumericEdit(this);
      this.thresholdSpin.setRange(0.5, 10);
      this.thresholdSpin.setValue(parameters.threshold);
      this.thresholdSpin.setPrecision(2);
      this.thresholdSpin.toolTip =
         "<p>Star detection threshold in units of the background noise. Lower = more, but " +
         "possibly less reliable stars.</p>";

      // Search radius
      this.radiusLabel = fieldLabel("Search radius (px):");
      this.radiusSpin = new SpinBox(this);
      this.radiusSpin.setRange(3, 50);
      this.radiusSpin.value = parameters.radius;
      this.radiusSpin.toolTip = "<p>Search radius of the PSF fit in pixels; adjust to the star size.</p>";

      // Limit the candidate count before the PSF fit (brightest first) -
      // the actual time sink is the blocking DynamicPSF fit over ALL
      // candidates, not StarDetector itself.
      this.maxCandLabel = fieldLabel("Max. stars (0 = all):");
      this.maxCandSpin = new SpinBox(this);
      this.maxCandSpin.setRange(0, 50000);
      this.maxCandSpin.value = parameters.maxCandidates;
      this.maxCandSpin.toolTip =
         "<p>Limits the number of candidates passed to DynamicPSF to the N " +
         "brightest (sorted by StarDetector flux). The PSF fit is a single, " +
         "blocking call with no visible intermediate progress - fewer candidates " +
         "directly shorten its runtime. 0 = fit all found candidates.</p>";

      // Optional StarDetector parameters - the defaults reject strongly
      // elongated and large stars, i.e. the aberrated stars at the field
      // edge that matter most for tilt and field curvature.
      this.sdCustomCheck = new CheckBox(this);
      this.sdCustomCheck.text = "Custom StarDetector parameters";
      this.sdCustomCheck.checked = parameters.sdCustom;
      this.sdCustomCheck.toolTip =
         "<p>Off: StarDetector runs with its own defaults (structure layers 5, sensitivity 0.5, " +
         "peak response 0.5, max. distortion 0.6, no clustered sources). These reject strongly " +
         "elongated and large stars - exactly the aberrated stars at the field edge, which then " +
         "leave the corners of the FWHM grid empty or sparsely populated.</p>" +
         "<p>On: the values below are used.</p>";

      this.sdLayersLabel = fieldLabel("Structure layers:");
      this.sdLayersSpin = new SpinBox(this);
      this.sdLayersSpin.setRange(1, 8);
      this.sdLayersSpin.value = parameters.sdStructureLayers;
      this.sdLayersSpin.toolTip =
         "<p>Number of dyadic wavelet layers: the largest detectable structure is about " +
         "2^n pixels (5 = 32 px). Increase it for large, defocused or strongly aberrated " +
         "stars. Default 5.</p>";

      this.sdSensitivityLabel = fieldLabel("Sensitivity:");
      this.sdSensitivitySpin = new NumericEdit(this);
      this.sdSensitivitySpin.setRange(0, 1);
      this.sdSensitivitySpin.setPrecision(2);
      this.sdSensitivitySpin.setValue(parameters.sdSensitivity);
      this.sdSensitivitySpin.toolTip =
         "<p>0..1, higher = fainter stars are detected. Default 0.5.</p>";

      this.sdPeakResponseLabel = fieldLabel("Peak response:");
      this.sdPeakResponseSpin = new NumericEdit(this);
      this.sdPeakResponseSpin.setRange(0, 1);
      this.sdPeakResponseSpin.setPrecision(2);
      this.sdPeakResponseSpin.setValue(parameters.sdPeakResponse);
      this.sdPeakResponseSpin.toolTip =
         "<p>0..1, higher = more tolerant of flat star profiles (bloated stars at the field " +
         "edge); lower = only stars with a prominent peak. Default 0.5.</p>";

      this.sdMaxDistortionLabel = fieldLabel("Max. distortion:");
      this.sdMaxDistortionSpin = new NumericEdit(this);
      this.sdMaxDistortionSpin.setRange(0, 1);
      this.sdMaxDistortionSpin.setPrecision(2);
      this.sdMaxDistortionSpin.setValue(parameters.sdMaxDistortion);
      this.sdMaxDistortionSpin.toolTip =
         "<p>0..1, higher = more elongated or irregular stars are accepted. Raise it (e.g. " +
         "0.8) when the stars at the field edge are strongly elongated. Too high also lets " +
         "galaxies and blends through. Default 0.6.</p>";

      this.sdClusteredCheck = new CheckBox(this);
      this.sdClusteredCheck.text = "Allow clustered sources";
      this.sdClusteredCheck.checked = parameters.sdAllowClustered;
      this.sdClusteredCheck.toolTip =
         "<p>Detect non-separable double/multiple stars as single objects. Their centroids " +
         "and shapes are unreliable, so this is off by default.</p>";

      // PSF function
      this.moffatCheck = new CheckBox(this);
      this.moffatCheck.text = "Moffat PSF instead of Gaussian";
      this.moffatCheck.checked = parameters.useMoffat;
      this.moffatCheck.toolTip =
         "<p>Moffat is the more realistic profile for stars with a diffraction disk, " +
         "Gaussian is faster.</p>";

      // MAD outlier filter: rejects fits with a disproportionately poor
      // model fit (noisy/unstable) relative to the median of all
      // successful fits in this image.
      this.madLabel = fieldLabel("MAD outlier factor:");
      this.madSpin = new NumericEdit(this);
      this.madSpin.setRange(0, 20);
      this.madSpin.setValue(parameters.madOutlierFactor);
      this.madSpin.setPrecision(1);
      this.madSpin.toolTip =
         "<p>Rejects PSF fits whose MAD (mean deviation from the model) is more than " +
         "X times the median of all successful fits in this image. Filters out " +
         "noisy/unstable fits (hot pixels, blends, very faint candidates) that " +
         "would otherwise show up in the vector map as physically implausible, isolated " +
         "outliers (strong eccentricity right next to calm neighboring stars). " +
         "0 = filter disabled.</p>";

      // ---- Vector map ----------------------------------------------------

      // Vector scale
      this.scaleLabel = fieldLabel("Vector scale:");
      this.scaleSpin = new NumericControl(this);
      this.scaleSpin.setRange(0.1, 10);
      this.scaleSpin.slider.setRange(0, 990); // steps of 0.01
      this.scaleSpin.slider.setScaledMinWidth(200);
      this.scaleSpin.setValue(parameters.vectorScale);
      this.scaleSpin.setPrecision(2);
      this.scaleSpin.toolTip = "<p>Size of the star ellipses here and of the arrows on the Coma map - " +
         "purely visual.</p>";

      // Stored as "hideEllipses"; shown as the enable switch of the layer.
      this.showEllipsesCheck = new CheckBox(this);
      this.showEllipsesCheck.text = "Show star ellipses";
      this.showEllipsesCheck.checked = !parameters.hideEllipses;
      this.showEllipsesCheck.toolTip = "<p>Draws the fitted ellipse of every star. Uncheck to look at the " +
         "streamlines or the orientation heatmap alone.</p>";

      // Tracking component (field-wide median elongation)
      // The measured tracking component of the kept analysis (see
      // computeTrackingComponent()), shown above the option.
      this.trackingInfoLabel = new Label(this);
      this.trackingInfoLabel.wordWrapping = true;
      this.trackingInfoLabel.useRichText = true;
      this.trackingInfoLabel.toolTip = "<p>The median elongation of all stars: ellipticity, " +
         "eccentricity and the direction of the major axis (image coordinates: 0° = right, " +
         "90° = down). Below an ellipticity of 0.03 it is negligible.</p>";
      this.updateTrackingInfo = function() {
         let t = d.analysis ? d.analysis.tracking : null;
         if (!t) {
            d.trackingInfoLabel.text = "<i>Measured with Calculate.</i>";
            return;
         }
         d.trackingInfoLabel.text = format("Ellipticity <b>%.3f</b> (eccentricity %.2f), axis %.0f°", t.ellipticity,
               t.eccentricity, t.angleDeg) + "<br/>" +
            ((t.ellipticity < 0.03) ? "Negligible: tracking/guiding looks clean." :
               "Shared by all stars: tracking/guiding, wind or flexure.");
      };
      let trackingInfoRow = indentRow([this.trackingInfoLabel], true);

      this.subtractTrackingCheck = new CheckBox(this);
      this.subtractTrackingCheck.text = "Subtract it from all stars";
      this.subtractTrackingCheck.checked = parameters.subtractTracking;
      this.subtractTrackingCheck.toolTip =
         "<p>Optical aberrations form patterns that are symmetric around the optical axis and " +
         "average out over the field; an elongation shared by all stars comes from tracking/" +
         "guiding, wind or flexure. Its median is always reported in the console; with this " +
         "option it is also subtracted from every star before the vector map, streamlines, " +
         "heatmap and the ring analysis are computed, so the optical pattern is not masked by " +
         "the mount. The CSV keeps the raw values in extra columns.</p>";

      // Streamlines (smoothed trend)
      this.streamlinesCheck = new CheckBox(this);
      this.streamlinesCheck.text = "Show streamlines (smoothed orientation trend)";
      this.streamlinesCheck.checked = parameters.showStreamlines;
      this.streamlinesCheck.toolTip =
         "<p>Shows the large-scale trend of the PSF orientation as continuous lines " +
         "(like field lines), smoothed over neighboring stars. Radial pattern = under-corrected " +
         "coma (corrector too close), concentric pattern = over-correction, defocus or field " +
         "curvature, a pattern that differs between the two sides = tilt; all field lines " +
         "parallel = tracking. Easier to spot than the noisy per-star vector map " +
         "alone. Correctly accounts for the fact that the PSF orientation is only " +
         "defined mod 180° (double-angle averaging).</p>" +
         "<p>White: the direction is significant there; gray: it is not (see the option below).</p>";

      this.shapeSignificantOnlyCheck = new CheckBox(this);
      this.shapeSignificantOnlyCheck.text = "Hide where the direction is not significant";
      this.shapeSignificantOnlyCheck.checked = parameters.shapeSignificantOnly;
      this.shapeSignificantOnlyCheck.toolTip =
         "<p>The streamlines show only a direction, never its strength: on pure noise they still look " +
         "orderly. They are therefore white where the smoothed elongation is at least 2.45 times its " +
         "standard error (the local scatter of the stars over their effective number; pure noise exceeds " +
         "that in 5% of the places) and gray elsewhere. With this option the gray parts are left " +
         "out.</p>";

      this.shapeShowCellsCheck = new CheckBox(this);
      this.shapeShowCellsCheck.text = "Cell averages with uncertainty";
      this.shapeShowCellsCheck.checked = parameters.shapeShowCells;
      this.shapeShowCellsCheck.toolTip =
         "<p>One bar per cell of the 11×11 grid along the mean axis of its stars (averaged in the doubled " +
         "angle, since an axis has no direction), its length the mean elongation. The white fan shows the " +
         "uncertainty of the axis (± standard error / (2 × elongation)). Bright with fan: significantly " +
         "elongated (≥ 2.45σ); thin and gray: round within the noise; gray dot: fewer than 5 stars.</p>";

      this.shapeShowModelCheck = new CheckBox(this);
      this.shapeShowModelCheck.text = "Optics model streamlines";
      this.shapeShowModelCheck.checked = parameters.shapeShowModel;
      this.shapeShowModelCheck.toolTip =
         "<p>Fits what optics can produce to all stars: a uniform term (tracking residue, astigmatism on " +
         "the axis), linear terms (a decentered pattern, binodal astigmatism of a misaligned system) and a " +
         "radial/tangential term growing with r² (field curvature, corrector spacing). Draws its streamlines " +
         "in violet (muted where it is not significant); \"Model match per cell\" below shows where the stars " +
         "agree with it. The Evaluation below gives " +
         "how much of the pattern it explains and its residual chi²/dof (about 1 = explained down to the " +
         "noise).</p>";

      this.shapeModelSignificantOnlyCheck = new CheckBox(this);
      this.shapeModelSignificantOnlyCheck.text = "Hide the model where it is not significant";
      this.shapeModelSignificantOnlyCheck.checked = parameters.shapeModelSignificantOnly;
      this.shapeModelSignificantOnlyCheck.toolTip =
         "<p>The model lines are violet where the model's field is at least 2.45 times its standard error " +
         "(from the uncertainty of the 7 fitted terms), and a muted gray-violet where it is not - near the " +
         "points where its terms cancel, and wherever the stars do not pin it down. With this option the " +
         "muted parts are left out.</p>";

      this.shapeShowMatchCheck = new CheckBox(this);
      this.shapeShowMatchCheck.text = "Model match per cell";
      this.shapeShowMatchCheck.checked = parameters.shapeShowMatch;
      this.shapeShowMatchCheck.toolTip =
         "<p>Compares each cell with the optics model: the mean difference between the stars' shapes and what " +
         "the model predicts there, in units of its standard error (scatter of the stars / √n).</p>" +
         "<p><b>Magenta</b>: 3σ or more - the cell holds structure no optics of the model produce (bent or " +
         "pinched optics, dew, vignetting by an off-axis guider or the focuser, nebulosity, halos, double " +
         "stars - or a pattern of higher order than the model). About one magenta cell per frame is chance; " +
         "the Evaluation gives the expected number.<br/><b>Green</b>: less than 3σ - the cell agrees with the " +
         "model within its noise.<br/>No frame: fewer than 5 stars.</p>";

      this.shapeShowDefectsCheck = new CheckBox(this);
      this.shapeShowDefectsCheck.text = "Singular points";
      this.shapeShowDefectsCheck.checked = parameters.shapeShowDefects;
      this.shapeShowDefectsCheck.toolTip =
         "<p>Marks the points where the smoothed axis field has no direction, with their index: optics " +
         "produce at most one +1 (radial or tangential pattern around the axis) or two +½ (the same split " +
         "by a uniform term, or binodal astigmatism). More points, and any −½, are noise in the smoothed " +
         "field - a larger smoothing radius or more stars help.</p>";

      this.streamlineRadiusLabel = fieldLabel("Smoothing radius (%):");
      this.streamlineRadiusSpin = new NumericControl(this);
      this.streamlineRadiusSpin.setRange(2, 50);
      this.streamlineRadiusSpin.slider.setRange(0, 96); // steps of 0.5
      this.streamlineRadiusSpin.slider.setScaledMinWidth(200);
      this.streamlineRadiusSpin.setValue(parameters.streamlineRadiusPercent);
      this.streamlineRadiusSpin.setPrecision(1);
      this.streamlineRadiusSpin.toolTip =
         "<p>How far neighboring stars contribute to smoothing the orientation field, in % of " +
         "the image diagonal. Smaller = follows local detail more closely (noisier), larger = " +
         "smoother, more large-scale trend.</p>";

      // Orientation heatmap (as in the Seti Astro Suite, see
      // github.com/setiastro/setiastrosuite) as the background instead of
      // the stretched star field.
      this.orientationHeatmapCheck = new CheckBox(this);
      this.orientationHeatmapCheck.text = "Show orientation heatmap (instead of the star field)";
      this.orientationHeatmapCheck.checked = parameters.showOrientationHeatmap;
      this.orientationHeatmapCheck.toolTip =
         "<p>Replaces the image background with a color-field heatmap of the PSF orientation " +
         "(like the Seti Astro Suite's \"Orientation Map\", github.com/setiastro/" +
         "setiastrosuite): θ is fitted per double-angle averaging (with 3-sigma clipping) as a " +
         "smooth 2D polynomial surface over all stars (degree set below) and colored as the " +
         "hue of an HSV color wheel - color gradients show the large-scale orientation trend " +
         "(tilt/coma) more directly than the real star field. Requires enough stars for the " +
         "chosen fit degree, otherwise it automatically falls back to the normal background.</p>";

      this.orientationHeatmapDegreeLabel = fieldLabel("Fit degree:");
      this.orientationHeatmapDegreeSpin = new SpinBox(this);
      this.orientationHeatmapDegreeSpin.setRange(1, 4);
      this.orientationHeatmapDegreeSpin.value = parameters.orientationHeatmapDegree;
      this.orientationHeatmapDegreeSpin.toolTip =
         "<p>Degree of the 2D polynomial fitted to the star orientations. 1 = a plane (pure, " +
         "uniform tilt only - matches the Seti Astro Suite's default). 2 or higher lets the " +
         "heatmap also follow local curvature (e.g. a coma pattern) that a plane cannot " +
         "represent and that may otherwise only show up in the streamlines overlay - at the " +
         "cost of being more sensitive to noise/outliers, especially near the image edges " +
         "where fewer stars constrain the higher-order terms.</p>";

      // ---- Tilt ----------------------------------------------------------

      // How the Tilt map reads (tooltip of its groups).
      this.tiltHelp =
         "<p>The yellow quadrilateral is recreated from Siril's \"Show tilt\" (ccd-inspector.c): one corner " +
         "point per image quadrant, its distance from the center proportional to the deviation of that " +
         "quadrant's mean FWHM from the average of all four. Coarser than the 11×11 grid of Star size, but " +
         "each quadrant has a quarter of all stars. The light-blue tilt axis runs through the sensor center " +
         "along the steepest rise of the plane through the four quadrants.</p>";

      this.fwhmGridDetailsCheck = new CheckBox(this);
      this.fwhmGridDetailsCheck.text = "Uncertainty and star count";
      this.fwhmGridDetailsCheck.checked = parameters.fwhmGridDetails;
      this.fwhmGridDetailsCheck.toolTip =
         "<p>Adds a smaller line to every cell: ± the 1σ uncertainty of its FWHM (its standard error, " +
         "1.4826 × MAD / √n - not the scatter of the stars; in pixels, or in arcseconds when only those are " +
         "shown) and <i>n</i> = number of stars. " +
         "Two cells differ reliably only when their difference exceeds about 2-3 × √(σ₁² + σ₂²).</p>";

      // How the grid reads (tooltip of the group).
      this.fwhmGridHelp =
         "<p>The 11×11 grid shows per cell its FWHM (25%-trimmed mean) in pixels and/or arcseconds and its " +
         "ratio to the center (inner 3×3 cells): ×1.17 = 17% larger stars than in the center. The fill " +
         "follows the ratio and its class: green ≤ 1.10 good (as sharp as the center, or sharper), yellow " +
         "slight, orange clear (around 1.25), red ≥ 1.50 strong - only where the cell differs from the center " +
         "by at least 2σ (or is good anyway); an unfilled cell cannot be told from the center.</p>" +
         "<p>Hatched, value in (brackets): uncertain - fewer than 5 stars, or a standard error above 10% of " +
         "the value.</p>" +
         "<p>The Evaluation box gives the center FWHM, the corners (median ratio of the 2×2 corner blocks: field " +
         "curvature / spacing) and the tilt (relative difference of the more unequal diagonal, with its " +
         "significance: < 5% unremarkable, 5-10% slight, > 10% worth correcting). The console lists the " +
         "details. Each cell has only a fraction of the stars: the tilt axis and the tilt angles use the " +
         "4 quadrants.</p>";

      // What each cell shows: one of the FWHM in pixels, the FWHM in
      // arcseconds and the ratio to the center (p.fwhmCellValue).
      this.fwhmPxRadio = new RadioButton(this);
      this.fwhmPxRadio.text = "FWHM in pixels";
      this.fwhmPxRadio.checked = parameters.fwhmCellValue === FWHM_CELL_PX;
      this.fwhmPxRadio.toolTip = "<p>Each cell shows its FWHM in image pixels (3.15 px).</p>";
      this.fwhmArcsecRadio = new RadioButton(this);
      this.fwhmArcsecRadio.text = "FWHM in arcseconds";
      this.fwhmArcsecRadio.checked = parameters.fwhmCellValue === FWHM_CELL_ARCSEC;
      this.fwhmArcsecRadio.toolTip =
         "<p>Each cell shows its FWHM in arcseconds (2.07\"), using 206.265 × pixel pitch [µm] / focal " +
         "length [mm]. Needs pixel pitch and focal length (Setup › General; with SuperPixel debayering, " +
         "twice the pitch is used automatically) - pixels otherwise.</p>";
      this.fwhmRatioRadio = new RadioButton(this);
      this.fwhmRatioRadio.text = "Ratio to the center";
      this.fwhmRatioRadio.checked = parameters.fwhmCellValue === FWHM_CELL_RATIO;
      this.fwhmRatioRadio.toolTip =
         "<p>Each cell shows its FWHM relative to the center (inner 3×3 cells): ×1.17 = 17% larger stars " +
         "than in the center - the value the fill and the assessment are based on.</p>";
      if (!this.fwhmArcsecRadio.checked && !this.fwhmRatioRadio.checked)
         this.fwhmPxRadio.checked = true;
      this.fwhmCellValue = function() {
         return d.fwhmArcsecRadio.checked ? FWHM_CELL_ARCSEC : d.fwhmRatioRadio.checked ? FWHM_CELL_RATIO :
            FWHM_CELL_PX;
      };

      // The legend of the fill (drawn here instead of in the map).
      this.fwhmLegend = new Control(this);
      this.fwhmLegend.setFixedHeight(Math.round(this.font.height * 6.2));
      this.fwhmLegend.onPaint = function() {
         let g = new Graphics(this);
         g.antialiasing = true;
         let fh = this.font.height, asc = this.font.ascent;
         let sky = 0x202020; // the map's dark background, under the half-transparent fill
         let x = 2, y = 0;
         let barW = Math.max(60, this.width - 4), barH = Math.round(fh * 1.6);
         g.pen = new Pen(this.foregroundColor);
         g.drawText(x, y + asc, "Fill: FWHM relative to the center (framed inner 3\u00D73 cells)");
         y += Math.round(fh * 1.3);
         let steps = 40;
         for (let i = 0; i < steps; ++i) {
            let ratio = 1 + (FWHM_FILL_RATIO_MAX - 1) * (i + 0.5) / steps;
            g.fillRect(x + barW * i / steps, y, x + barW * (i + 1) / steps + 0.5, y + barH,
               new Brush(opaqueOver(fwhmFillColor(ratio), sky)));
         }
         g.pen = new Pen(this.foregroundColor);
         g.drawRect(x, y, x + barW, y + barH);
         [1].concat(FWHM_RATIO_LIMITS).forEach(function(t) {
            let tx = x + barW * (t - 1) / (FWHM_FILL_RATIO_MAX - 1);
            g.drawLine(tx, y + barH, tx, y + barH + 3);
            let tl = format("\u00D7%.2f", t) + (t >= FWHM_FILL_RATIO_MAX ? "+" : "");
            g.drawText(Math.max(x, Math.min(tx - g.font.width(tl) / 2, x + barW - g.font.width(tl))),
               y + barH + 3 + asc, tl);
         });
         y += barH + 3 + Math.round(fh * 1.3);
         // Samples: unfilled and hatched.
         let box = barH;
         g.fillRect(x, y, x + box, y + box, new Brush(0xFF000000 | sky));
         g.drawRect(x, y, x + box, y + box);
         g.drawText(x + box + 6, y + asc - 1, "not significant");
         let x2 = x + box + 6 + g.font.width("not significant") + fh;
         g.fillRect(x2, y, x2 + box, y + box, new Brush(0xFF000000 | sky));
         g.fillRect(x2, y, x2 + box, y + box, new Brush(FWHM_UNCERTAIN_HATCH, BrushStyle.BackwardDiagonalHatch));
         g.drawRect(x2, y, x2 + box, y + box);
         g.drawText(x2 + box + 6, y + asc - 1, "uncertain");
         g.end();
      };

      this.tiltAxisCheck = new CheckBox(this);
      this.tiltAxisCheck.text = "Show tilt axis";
      this.tiltAxisCheck.checked = parameters.showTiltAxis;
      this.tiltAxisCheck.toolTip =
         "<p>Draws the light-blue tilt axis through the sensor center, derived from the " +
         "4-quadrant FWHM evaluation, with its angle/direction label.</p>";

      // ---- Optics (General page) -----------------------------------------

      // Optional inputs for the tilt angle computation (in degrees). All
      // three fields must be > 0, otherwise the computation is skipped -
      // purely optional, the rest of the script works unchanged without
      // these values.
      this.opticsFromHeaderCheck = new CheckBox(this);
      this.opticsFromHeaderCheck.text = "Read the values from the FITS header";
      this.opticsFromHeaderCheck.checked = parameters.opticsFromHeader;
      this.opticsFromHeaderCheck.toolTip =
         "<p>Uses XPIXSZ (pixel pitch, by convention including binning), FOCALLEN and APTDIA " +
         "(or FOCALLEN/FOCRATIO) from the target image's FITS header (and from each frame's header in " +
         "a series). The fields below then show these values, read-only; a value the header does not " +
         "give is not set (0) - without an image, all three. Uncheck to enter your own values instead: " +
         "they are saved and used for every image. Always the physical sensor pitch - the doubling after " +
         "SuperPixel debayering is applied automatically.</p>";

      // The camera, as the FITS header names it, in a read-only field like
      // the other optics values - empty when the header names none or
      // "Read the values from the FITS header" is unchecked.
      this.cameraLabel = fieldLabel("Camera:");
      this.cameraValue = new Edit(this);
      this.cameraValue.readOnly = true;
      this.cameraValue.toolTip = "<p>From the FITS header of the target image (INSTRUME, binning from " +
         "XBINNING/YBINNING), when \"Read the values from the FITS header\" is checked. For information only.</p>";
      this.updateCamera = function() {
         let camera = (d.opticsFromHeaderCheck.checked && d.view && d.view.id.length > 0) ?
            readCameraFromHeader(d.view) : "";
         d.cameraValue.text = camera;
      };

      // The mount: estimated from the FITS header (read-only, with its source)
      // like the optics values, or chosen and saved (manualMount).
      this.mountLabel = fieldLabel("Mount:");
      this.mountCombo = new ComboBox(this);
      MOUNT_TYPES.forEach(function(t) { d.mountCombo.addItem(t); });
      this.manualMount = Math.range(parameters.mountType, 0, MOUNT_TYPES.length - 1);
      this.mountSourceLabel = new Label(this);
      this.mountCombo.toolTip =
         "<p>The mount, for the assessment: an alt-azimuth mount without a derotator turns the field during " +
         "the exposure - the stars become arcs around the rotation center, a tangential pattern like field " +
         "curvature; the assessment then gives its size at the corners (from EXPTIME, CENTALT, CENTAZ and " +
         "SITELAT) and warns.</p>" +
         "<p>With \"Read the values from the FITS header\" it is estimated (grayed, the source beside it): " +
         "PIERSIDE East/West means a German equatorial mount; otherwise the mount driver's name in TELESCOP " +
         "(EQMod, GS Server, iOptron CEM, AM5 ... equatorial; Alt-Az, AZ-GTi, Seestar, Dwarf ... alt-azimuth); " +
         "else unknown. Uncheck it to choose the mount yourself.</p>";
      this.mountCombo.onItemSelected = function(index) {
         if (!d.opticsFromHeaderCheck.checked)
            d.manualMount = index;
         d.updateAssessment();
      };

      // Guiding: always the user's choice (the header says too little), with
      // a guide camera in the header as the hint for "Unknown".
      this.guidingLabel = fieldLabel("Guiding:");
      this.guidingCombo = new ComboBox(this);
      GUIDING_TYPES.forEach(function(t) { d.guidingCombo.addItem(t); });
      this.guidingCombo.currentItem = Math.range(parameters.guidingType, 0, GUIDING_TYPES.length - 1);
      this.guidingSourceLabel = new Label(this);
      this.guidingCombo.toolTip =
         "<p>How the exposures were guided - it decides what a common elongation of all stars means:</p>" +
         "<p><b>Not guided</b>: the mount's tracking (periodic error of the worm, polar drift).<br/>" +
         "<b>Guide scope</b>: differential flexure between guide scope and main optics or mirror flop when it " +
         "is the same in every frame, the guiding quality when it changes.<br/>" +
         "<b>Off-axis guider</b>: no differential flexure - the same in every frame points rather to " +
         "astigmatism on the axis.</p>" +
         "<p>No FITS keyword says this; it is saved, also with \"Read the values from the FITS header\". With " +
         "<b>Unknown</b>, a guide camera in the header (GUIDECAM, written by the ASIAIR) counts as \"guided, " +
         "method unknown\" - shown beside the field.</p>";
      this.updateGuidingSource = function() {
         let values = (d.view && d.view.id.length > 0) ? viewKeywordValues(d.view) : {};
         let g = effectiveGuiding(d.guidingCombo.currentItem, values);
         d.guidingSourceLabel.text = (d.guidingCombo.currentItem === GUIDING_UNKNOWN && g.type !== GUIDING_UNKNOWN) ?
            GUIDING_TYPES[g.type] + ", from " + g.source : "";
      };
      this.guidingCombo.onItemSelected = function() {
         d.updateGuidingSource();
         d.updateAssessment();
      };

      this.pixelPitchLabel = fieldLabel("Pixel pitch (µm):");
      this.pixelPitchSpin = new NumericEdit(this);
      this.pixelPitchSpin.setRange(0, 50);
      this.pixelPitchSpin.setValue(parameters.pixelPitchUm);
      this.pixelPitchSpin.setPrecision(2);
      this.pixelPitchSpin.toolTip =
         "<p>Physical pixel size of the sensor in micrometers (e.g. 4.31 for a Canon EOS 550D). " +
         "0 = not specified, the tilt angle computation is then skipped. Always enter the " +
         "physical sensor pitch - with SuperPixel debayering the script automatically uses " +
         "twice this value (one output pixel = 2x2 sensor pixels).</p>" +
         "<p>Grayed: \"Read the values from the FITS header\" is checked - the value comes from the header of " +
         "the target image (XPIXSZ), 0 when it has none or there is no image. Uncheck it to enter your own " +
         "value, which is saved.</p>";

      this.focalLengthLabel = fieldLabel("Focal length (mm):");
      this.focalLengthSpin = new NumericEdit(this);
      this.focalLengthSpin.setRange(0, 10000);
      this.focalLengthSpin.setValue(parameters.focalLengthMm);
      this.focalLengthSpin.setPrecision(0);
      this.focalLengthSpin.toolTip = "<p>Focal length of the telescope in mm (e.g. 750 for a Skywatcher 150P).</p>" +
         "<p>Grayed: from the FITS header of the target image (FOCALLEN), 0 when it has none.</p>";

      this.apertureLabel = fieldLabel("Aperture (mm):");
      this.apertureSpin = new NumericEdit(this);
      this.apertureSpin.setRange(0, 2000);
      this.apertureSpin.setValue(parameters.apertureMm);
      this.apertureSpin.setPrecision(0);
      this.apertureSpin.toolTip =
         "<p>Aperture of the telescope in mm (e.g. 150 for a Skywatcher 150P). " +
         "Together with the focal length, this gives the f-number (focal length/aperture).</p>" +
         "<p>Grayed: from the FITS header of the target image (APTDIA, or FOCALLEN/FOCRATIO), 0 when it has none.</p>";

      // The optical system decides the wording of the assessment where the
      // direction of a correction depends on it (see assessImage()).
      this.opticsTypeLabel = fieldLabel("Optics type:");
      this.opticsTypeCombo = new ComboBox(this);
      for (let i = 0; i < OPTICS_TYPES.length; ++i)
         this.opticsTypeCombo.addItem(OPTICS_TYPES[i]);
      this.opticsTypeCombo.currentItem = Math.range(parameters.opticsType, 0, OPTICS_TYPES.length - 1);
      this.opticsTypeCombo.toolTip =
         "<p>The optical system, for the suggestions of the Assessment page. A bare Newtonian shows " +
         "coma and field curvature by design; with a coma corrector, field flattener or reducer, the " +
         "same patterns point to its spacing. A refractor has hardly any coma: without a flattener, " +
         "field curvature and astigmatism at the edge are expected, and its lens is collimated at the " +
         "factory. In a color or broadband frame, a refractor's lateral color elongates the edge stars " +
         "radially like under-correction. With Unknown, the suggestions name both cases.</p>" +
         "<p>SCT, Maksutov and RASA focus by moving the primary mirror (mirror flop). A classic SCT has " +
         "coma and a curved field by design; a Maksutov or SCT at f/10-f/15 is seeing-limited with a deep " +
         "focus zone, so small differences across the field are uncertain. An aplanatic SCT (EdgeHD), a " +
         "RASA and an astrograph with built-in flattener are meant to be flat: curvature and coma point " +
         "to the back focus. A RASA at f/2 reacts to a few micrometers of tilt.</p>";

      // The optics fields have two modes, kept apart:
      //  - "Read the values from the FITS header" checked: they show the
      //    values of the target image's header, read-only (grayed); a value
      //    the header does not give - or all three without an image - is 0,
      //    not set. Nothing is saved from them.
      //  - unchecked: they take the user's own values, which are saved
      //    (manualOptics) and used for every image.
      this.manualOptics = { pixelPitchUm: parameters.pixelPitchUm, focalLengthMm: parameters.focalLengthMm,
                            apertureMm: parameters.apertureMm };
      this.opticsFromHeaderFields = { pixelPitchUm: false, focalLengthMm: false, apertureMm: false };
      this.opticsFields = [["pixelPitchUm", this.pixelPitchSpin], ["focalLengthMm", this.focalLengthSpin],
                           ["apertureMm", this.apertureSpin]];
      this.applyHeaderOptics = function() {
         let fromHeader = d.opticsFromHeaderCheck.checked;
         let haveView = d.view && d.view.id.length > 0;
         let hdr = (fromHeader && haveView) ? readOpticsFromHeader(d.view) : null;
         if (fromHeader) {
            let est = haveView ? estimateMount(viewKeywordValues(d.view)) : { type: MOUNT_UNKNOWN, source: "" };
            d.mountCombo.currentItem = est.type;
            d.mountSourceLabel.text = est.source ? "from " + est.source : (haveView ? "not in the header" : "");
         } else {
            d.mountCombo.currentItem = d.manualMount;
            d.mountSourceLabel.text = "";
         }
         d.mountCombo.enabled = !fromHeader;
         d.updateGuidingSource();
         d.opticsFields.forEach(function(f) {
            d.opticsFromHeaderFields[f[0]] = fromHeader;
            f[1].setValue(fromHeader ? (hdr !== null && hdr[f[0]] > 0 ? hdr[f[0]] : 0) : d.manualOptics[f[0]]);
            f[1].enabled = !fromHeader;
         });
      };
      // An entry of the user's (the fields are only editable then).
      this.opticsFields.forEach(function(f) {
         f[1].onValueUpdated = function(value) {
            if (!d.opticsFromHeaderFields[f[0]])
               d.manualOptics[f[0]] = value;
            d.updateControls();
            d.refreshPreview();
         };
      });
      this.opticsFromHeaderCheck.onCheck = function(checked) {
         d.applyHeaderOptics();
         d.updateCamera();
         d.updateControls();
         d.refreshPreview();
      };
      this.applyHeaderOptics();
      this.updateCamera();

      this.tiltPlot3DCheck = new CheckBox(this);
      this.tiltPlot3DCheck.text = "3D sensor tilt plot";
      this.tiltPlot3DCheck.checked = parameters.show3DTiltPlot;
      this.tiltPlot3DCheck.toolTip =
         "<p>Opens a separate window with an isometric 2D graphic (PJSR has no true 3D): " +
         "a flat reference plane next to the tilted plane computed from the Δz values. " +
         "Created by Save (and per frame by a series). It needs pixel pitch, focal length and aperture - " +
         "from the image's FITS header or the fields above: an image without them is skipped with a " +
         "console note.</p>";

      // ---- Coma ----------------------------------------------------------

      // PSF asymmetry / coma field
      this.measureAsymmetryCheck = new CheckBox(this);
      this.measureAsymmetryCheck.text = "Measure PSF asymmetry (coma direction, coma-free point)";
      this.measureAsymmetryCheck.checked = parameters.measureAsymmetry;
      this.measureAsymmetryCheck.toolTip =
         "<p>Gaussian/Moffat fits are point-symmetric and cannot show on which side of the core " +
         "a comatic flare sits. This option measures the flux centroid and the third moment of " +
         "each star directly on the pixels relative to the PSF-fit center, draws the median " +
         "flare direction on a coarse grid (orange arrows), fits the coma field " +
         "v = k·(P − P0) and marks the coma-free point P0 (magenta) with its bootstrap " +
         "uncertainty. Adds a few seconds of runtime.</p>";

      this.comaStreamlinesCheck = new CheckBox(this);
      this.comaStreamlinesCheck.text = "Show coma streamlines (orange with arrows)";
      this.comaStreamlinesCheck.checked = parameters.showComaStreamlines;
      this.comaStreamlinesCheck.toolTip =
         "<p>Streamlines through the smoothed PSF-asymmetry field (flare direction) instead of the " +
         "ellipse orientation. Unlike the elongation streamlines they have a real direction " +
         "(arrowheads) and stop where the coma becomes weak. Under-corrected coma: lines diverge " +
         "from the coma-free point (source); over-corrected: they converge on it (sink). " +
         "Deviations from straight radial lines show what a simple coma model cannot, e.g. a " +
         "tilted corrector. Requires \"Measure PSF asymmetry\".</p>";

      this.comaStreamlineRadiusLabel = fieldLabel("Smoothing radius (%):");
      this.comaStreamlineRadiusSpin = new NumericControl(this);
      this.comaStreamlineRadiusSpin.setRange(5, 60);
      this.comaStreamlineRadiusSpin.slider.setRange(0, 110); // steps of 0.5
      this.comaStreamlineRadiusSpin.slider.setScaledMinWidth(200);
      this.comaStreamlineRadiusSpin.setValue(parameters.comaStreamlineRadiusPercent);
      this.comaStreamlineRadiusSpin.setPrecision(1);
      this.comaStreamlineRadiusSpin.toolTip =
         "<p>Smoothing radius of the asymmetry field, in % of the image diagonal. The per-star " +
         "asymmetry is much noisier than the ellipse orientation, so this radius should be " +
         "larger than the one for the elongation streamlines (default: about twice).</p>";

      this.comaStreamlineMinLabel = fieldLabel("Stop below (%):");
      this.comaStreamlineMinSpin = new NumericEdit(this);
      this.comaStreamlineMinSpin.setRange(0, 90);
      this.comaStreamlineMinSpin.setValue(parameters.comaStreamlineMinPercent);
      this.comaStreamlineMinSpin.setPrecision(0);
      this.comaStreamlineMinSpin.toolTip =
         "<p>A line ends where the smoothed asymmetry drops below this percentage of the field's " +
         "strength (90th percentile over the seed points). Higher = only the clearly comatic " +
         "regions, lower = lines reach further into the coma-free zone (noisier).</p>";

      this.comaArrowsCheck = new CheckBox(this);
      this.comaArrowsCheck.text = "Show coma arrows (median per grid cell, length = strength)";
      this.comaArrowsCheck.checked = parameters.showComaArrows;
      this.comaArrowsCheck.toolTip =
         "<p>Orange arrows on a 9×6 grid with the median flare direction per cell. Unlike the " +
         "streamlines, their length shows the strength directly. Requires \"Measure PSF " +
         "asymmetry\".</p>";

      this.starAsymArrowsCheck = new CheckBox(this);
      this.starAsymArrowsCheck.text = "Show one asymmetry arrow per star";
      this.starAsymArrowsCheck.checked = parameters.showStarAsymArrows;
      this.starAsymArrowsCheck.toolTip =
         "<p>Draws the measured flare direction of every star as an arrow from the star center " +
         "(length = strength, scaled with the vector scale), for a star-by-star comparison with " +
         "the ellipses. Default color: alignment with the ellipse axis - green = parallel (coma " +
         "shapes the star), red = perpendicular (elongation from defocus/field curvature/" +
         "astigmatism), gray = star nearly round. Arrows below the threshold are hidden. " +
         "Requires \"Measure PSF asymmetry\".</p>";

      this.starAsymMinLabel = fieldLabel("Min. strength (sigma):");
      this.starAsymMinSpin = new NumericEdit(this);
      this.starAsymMinSpin.setRange(0, 10);
      this.starAsymMinSpin.setValue(parameters.starAsymMinSigma);
      this.starAsymMinSpin.setPrecision(1);
      this.starAsymMinSpin.toolTip =
         "<p>The per-star asymmetry is noisy. Its noise is estimated from the inner field, where " +
         "the coma is close to zero; only arrows stronger than this many sigma are drawn. " +
         "0 = draw all.</p>";

      this.starAsymColorCheck = new CheckBox(this);
      this.starAsymColorCheck.text = "Color by strength instead of alignment";
      this.starAsymColorCheck.checked = parameters.starAsymColorByStrength;

      // ---- Output --------------------------------------------------------

      // Export CSV
      this.exportCheck = new CheckBox(this);
      this.exportCheck.text = "Export data table as CSV";
      this.exportCheck.checked = parameters.doExport;
      this.exportCheck.toolTip =
         "<p>Save: <i>name</i>_aberration.csv next to the original file (the temporary directory for an " +
         "image that was never saved). Analyze series: StarAberrationSeries.csv in the directory of the " +
         "frames. Existing files are replaced.</p>";

      // Options that only take effect together with another one are
      // disabled while that one is off (their values are kept).
      this.updateControls = function() {
         // Only a single-channel image can be a Bayer mosaic. The frames of
         // the Series tab are files of their own, so with frames in the list
         // the option stays available whatever the target image is.
         d.debayerCheck.enabled = !d.isColorTarget() || (d.seriesFrames !== undefined && d.seriesFrames.length > 0);
         let debayer = d.debayerCheck.enabled && d.debayerCheck.checked;
         d.bayerPatternLabel.enabled = debayer;
         d.bayerPatternCombo.enabled = debayer;
         d.closeWindowsCheck.enabled = debayer;

         let sd = d.sdCustomCheck.checked;
         [d.sdLayersLabel, d.sdLayersSpin, d.sdSensitivityLabel, d.sdSensitivitySpin,
          d.sdPeakResponseLabel, d.sdPeakResponseSpin, d.sdMaxDistortionLabel,
          d.sdMaxDistortionSpin, d.sdClusteredCheck].forEach(function(c) { c.enabled = sd; });

         d.streamlineRadiusLabel.enabled = d.streamlinesCheck.checked;
         d.streamlineRadiusSpin.enabled = d.streamlinesCheck.checked;
         d.shapeSignificantOnlyCheck.enabled = d.streamlinesCheck.checked;
         d.shapeModelSignificantOnlyCheck.enabled = d.shapeShowModelCheck.checked;
         d.orientationHeatmapDegreeLabel.enabled = d.orientationHeatmapCheck.checked;
         d.orientationHeatmapDegreeSpin.enabled = d.orientationHeatmapCheck.checked;


         let asym = d.measureAsymmetryCheck.checked;
         let comaLines = asym && d.comaStreamlinesCheck.checked;
         let starArrows = asym && d.starAsymArrowsCheck.checked;
         d.comaStreamlinesCheck.enabled = asym;
         d.comaStreamlineRadiusLabel.enabled = comaLines;
         d.comaStreamlineRadiusSpin.enabled = comaLines;
         d.comaStreamlineMinLabel.enabled = comaLines;
         d.comaStreamlineMinSpin.enabled = comaLines;
         d.comaArrowsCheck.enabled = asym;
         d.starAsymArrowsCheck.enabled = asym;
         d.starAsymMinLabel.enabled = starArrows;
         d.starAsymMinSpin.enabled = starArrows;
         d.starAsymColorCheck.enabled = starArrows;

         // The 3D plot stays selectable whatever the optics fields show: with
         // "Read the values from the FITS header" the values come per image,
         // and an image without them skips the plot with a console note.
      };

      this.isColorTarget = function() {
         return d.view !== null && d.view !== undefined && d.view.id.length > 0 && d.view.image.isColor;
      };

      // The dialog's settings as an object with the names of
      // ScriptParameters, as processView() takes them.
      this.collectParameters = function() {
         return {
            threshold: d.thresholdSpin.value,
            radius: d.radiusSpin.value,
            useMoffat: d.moffatCheck.checked,
            vectorScale: d.scaleSpin.value,
            doExport: d.exportCheck.checked,
            debayer: d.debayerCheck.checked, // kept for mono images; analyzeView() skips it for color ones
            bayerPattern: d.bayerPatternItems[d.bayerPatternCombo.currentItem],
            closeIntermediateWindows: d.closeWindowsCheck.checked,
            madOutlierFactor: d.madSpin.value,
            maxCandidates: d.maxCandSpin.value,
            sdCustom: d.sdCustomCheck.checked,
            sdStructureLayers: d.sdLayersSpin.value,
            sdSensitivity: d.sdSensitivitySpin.value,
            sdPeakResponse: d.sdPeakResponseSpin.value,
            sdMaxDistortion: d.sdMaxDistortionSpin.value,
            sdAllowClustered: d.sdClusteredCheck.checked,
            showStreamlines: d.streamlinesCheck.checked,
            shapeSignificantOnly: d.shapeSignificantOnlyCheck.checked,
            shapeShowCells: d.shapeShowCellsCheck.checked,
            shapeShowModel: d.shapeShowModelCheck.checked,
            shapeModelSignificantOnly: d.shapeModelSignificantOnlyCheck.checked,
            shapeShowMatch: d.shapeShowMatchCheck.checked,
            shapeShowDefects: d.shapeShowDefectsCheck.checked,
            streamlineRadiusPercent: d.streamlineRadiusSpin.value,
            // The user's own values (header values are read again per image,
            // see resolveOptics()).
            pixelPitchUm: d.manualOptics.pixelPitchUm,
            focalLengthMm: d.manualOptics.focalLengthMm,
            apertureMm: d.manualOptics.apertureMm,
            show3DTiltPlot: d.tiltPlot3DCheck.checked,
            showOrientationHeatmap: d.orientationHeatmapCheck.checked,
            orientationHeatmapDegree: d.orientationHeatmapDegreeSpin.value,
            fwhmGridDetails: d.fwhmGridDetailsCheck.checked,
            fwhmCellValue: d.fwhmCellValue(),
            opticsFromHeader: d.opticsFromHeaderCheck.checked,
            showStarAsymArrows: d.starAsymArrowsCheck.checked,
            starAsymMinSigma: d.starAsymMinSpin.value,
            starAsymColorByStrength: d.starAsymColorCheck.checked,
            hideEllipses: !d.showEllipsesCheck.checked,
            showTiltAxis: d.tiltAxisCheck.checked,
            subtractTracking: d.subtractTrackingCheck.checked,
            measureAsymmetry: d.measureAsymmetryCheck.checked,
            showComaStreamlines: d.comaStreamlinesCheck.checked,
            comaStreamlineRadiusPercent: d.comaStreamlineRadiusSpin.value,
            comaStreamlineMinPercent: d.comaStreamlineMinSpin.value,
            showComaArrows: d.comaArrowsCheck.checked,
            opticsType: d.opticsTypeCombo.currentItem,
            mountType: d.manualMount,
            guidingType: d.guidingCombo.currentItem,
            seriesShowMaps: d.seriesShowMapsCheck.checked,
            seriesSaveMaps: d.seriesSaveMapsCheck.checked,
            seriesSaveMosaic: d.seriesSaveMosaicCheck.checked
         };
      };

      // Every change of a setting updates the dependent controls and
      // redraws the preview when it shows a vector map (refreshPreview).
      let settingChanged = function() {
         d.updateControls();
         d.refreshPreview();
      };
      [this.debayerCheck, this.closeWindowsCheck, this.moffatCheck, this.showEllipsesCheck,
       this.subtractTrackingCheck, this.streamlinesCheck, this.orientationHeatmapCheck,
       this.fwhmGridDetailsCheck, this.tiltAxisCheck,
       this.tiltPlot3DCheck, this.measureAsymmetryCheck, this.comaStreamlinesCheck,
       this.comaArrowsCheck, this.starAsymArrowsCheck, this.starAsymColorCheck,
       this.exportCheck, this.sdCustomCheck, this.shapeSignificantOnlyCheck, this.shapeShowCellsCheck,
       this.shapeShowModelCheck, this.shapeModelSignificantOnlyCheck, this.shapeShowMatchCheck,
       this.shapeShowDefectsCheck,
       this.sdClusteredCheck].forEach(function(c) { c.onCheck = settingChanged; });
      // A radio button reports both the one switched off and the one switched
      // on: redraw once, for the new one.
      [this.fwhmPxRadio, this.fwhmArcsecRadio, this.fwhmRatioRadio].forEach(function(c) {
         c.onCheck = function(checked) { if (checked) settingChanged(); };
      });
      [this.thresholdSpin, this.radiusSpin, this.maxCandSpin, this.madSpin,
       this.sdLayersSpin, this.sdSensitivitySpin, this.sdPeakResponseSpin, this.sdMaxDistortionSpin,
       this.orientationHeatmapDegreeSpin,
       this.comaStreamlineMinSpin, this.starAsymMinSpin].forEach(function(c) { c.onValueUpdated = settingChanged; });
      // A slider reports every step while it is dragged: redraw once it has
      // rested for a moment, not for every step (the streamlines are
      // computed anew for every radius).
      this.sliderTimer = new Timer;
      this.sliderTimer.interval = 0.3; // s
      this.sliderTimer.singleShot = true;
      this.sliderTimer.onTimeout = function() { settingChanged(); };
      [this.scaleSpin, this.streamlineRadiusSpin, this.comaStreamlineRadiusSpin].forEach(function(c) {
         c.onValueUpdated = function() {
            d.sliderTimer.stop();
            d.sliderTimer.start();
         };
      });
      this.bayerPatternCombo.onItemSelected = settingChanged;
      this.opticsTypeCombo.onItemSelected = settingChanged;

      // New Instance (blue drag icon): drops a process icon with the
      // current settings on the workspace. Dragging directly onto a
      // target image is currently not supported by PixInsight for script
      // processes (results in "execute a Script instance recursively") -
      // instead: drop the icon on the workspace, close the dialog, later
      // reopen it with the same settings by double-clicking the icon.
      this.newInstanceButton = new ToolButton(this);
      this.newInstanceButton.icon = this.scaledResource(":/process-interface/new-instance.png");
      this.newInstanceButton.setScaledFixedSize(20, 20);
      this.newInstanceButton.toolTip =
         "<p>Drop the current settings as a process icon on the workspace. " +
         "Double-clicking the dropped icon later reopens the dialog with " +
         "the same settings.</p>" +
         "<p><b>Note:</b> Dragging directly onto an image is not supported for script " +
         "processes in current PixInsight versions.</p>";
      this.newInstanceButton.onMousePress = () => {
         this.hasFocus = true;

         parameters.assign(d.collectParameters());
         parameters.SaveParameters();

         this.pushed = false;
         this.dialog.newInstance();
      };

      // Browse Documentation: the PIDoc reference installed with the script
      // in doc/scripts/StarAberrationDiagnostics/ (built by pidoc-src/).
      this.documentationButton = new ToolButton(this);
      this.documentationButton.icon = this.scaledResource(":/process-interface/browse-documentation.png");
      this.documentationButton.setScaledFixedSize(20, 20);
      this.documentationButton.toolTip =
         "<p>Opens the reference documentation of the dialog in the PixInsight documentation browser.</p>";
      this.documentationButton.onClick = () => {
         if (!Dialog.browseScriptDocumentation("StarAberrationDiagnostics"))
            (new MessageBox("<p>The documentation has not been installed.</p>" +
               "<p>It is also on the website: tricx.de/pi-scripts/StarAberrationDiagnostics/dialog/</p>",
               TITLE, StdIcon.Information, StdButton.Ok)).execute();
      };

      // Buttons
      // The analysis of the last Preview (see analyzeView()) is kept while
      // the dialog is open. Switching layers or changing their settings
      // then only redraws the map; Preview and Apply reuse it as long as
      // the detection settings and the target image are unchanged.
      this.analysis = null;
      this.previewStale = false; // the detection settings differ from those of the kept analysis

      this.keptAnalysis = function(p) {
         return (d.analysis && d.analysis.key === analysisKey(d.view, p)) ? d.analysis : null;
      };

      // The map shown in the preview: that of the selected map page, or the
      // last one while another page is selected.
      this.mapView = "size";

      // The maps and their evaluations show the analysis of the chosen
      // source (Maps tab): the target image (Calculate, index 0), or the
      // static result of a series (pass 2, buildStaticSeries()).
      this.seriesStatic = [];
      this.mapSource = 0;
      this.currentAnalysis = function() {
         return d.mapSource === 0 ? d.analysis : (d.seriesStatic[d.mapSource - 1] || {}).analysis || null;
      };
      // A series analysis carries the optics of its frames; the target
      // image's come from its header or the dialog, those of loaded results
      // from their saved header or the dialog.
      this.opticsFor = function(analysis, p) {
         return analysis.optics ? analysis.optics :
            resolveOptics(analysis.source || d.view, p, analysis.debayer, false);
      };

      this.showMap = function(bmp) {
         d.previewStale = false;
         d.setPreviewBitmap(bmp, "Preview of <i>" + d.currentAnalysis().targetViewId + "</i> - " +
            mapViewByKey(d.mapView).label);
      };

      // Redraws the vector map in the preview from the kept analysis, or
      // marks it as outdated when the detection settings have changed.
      this.refreshPreview = function() {
         let an = d.currentAnalysis();
         if (!an)
            return;
         let p = d.collectParameters();
         // A series result was measured with the settings of its run, loaded
         // results with those of their file; only the target image's
         // analysis goes stale.
         if (an === d.analysis && !an.source && d.keptAnalysis(p) === null) {
            if (!d.previewStale) {
               d.previewStale = true;
               d.preview.setStatusMessage("<b>Detection settings changed</b> - click Calculate to update the map");
            }
            return;
         }
         d.showMap(renderVectorMap(an, p, d.opticsFor(an, p), false, d.mapView));
         d.updateAssessment();
         d.updateGridEvaluation();
         d.updateShapeEvaluation();
         d.updateTiltEvaluation();
      };

      // The Assessment page: the assessment of the kept analysis with the
      // current settings (tracking subtraction, optics, optics type), computed
      // again without console output whenever a setting changes.
      this.assessmentBox = new TextBox(this);
      this.assessmentBox.readOnly = true;
      this.assessmentBox.setScaledMinSize(300, 200);
      this.updateAssessment = function() {
         if (!d.analysis) {
            d.assessmentBox.text = "<i>Click Calculate to assess the image.</i>";
            return;
         }
         let p = d.collectParameters();
         let optics = d.opticsFor(d.analysis, p);
         let report = reportAnalysis(d.analysis, starsFor(d.analysis, p.subtractTracking), p, optics, SILENT_CONSOLE);
         d.assessmentBox.text = formatAssessment(assessImage(report.metrics, p.opticsType));
      };

      // ---- Series (Series page) ------------------------------------------

      // Light frames before and after a meridian flip (see measureSeries()).
      // The list shows each frame's side as soon as it is added (from the
      // header); Analyze series measures them with the current settings and
      // fills in the values. A side set by hand only changes the comparison,
      // the frames are not measured again.
      this.seriesFrames = [];
      this.seriesResult = null;

      const SERIES_COLUMNS = ["File", "Side", "From", "HA (h)", "Alt", "Focus", "Stars", "FWHM", "Tilt",
                              "eps_rad", "Track", "Coma k", "SIP"];
      this.seriesTree = new TreeBox(this);
      this.seriesTree.numberOfColumns = SERIES_COLUMNS.length;
      this.seriesTree.headerVisible = true;
      this.seriesTree.rootDecoration = false;
      this.seriesTree.multipleSelection = true;
      this.seriesTree.alternateRowColor = true;
      for (let i = 0; i < SERIES_COLUMNS.length; ++i)
         this.seriesTree.setHeaderText(i, SERIES_COLUMNS[i]);
      this.seriesTree.setScaledMinSize(300, 160);
      this.seriesTree.toolTip =
         "<p>The light frames of the series. <b>From</b> tells where the side comes from: the PIERSIDE keyword " +
         "(N.I.N.A., ASCOM), a camera angle that turns by 180° at the flip (ROTATOR of the ASIAIR), the hour " +
         "angle, or manual. Frames close after the meridian may have no known side.</p>" +
         "<p>Use unregistered, uncropped frames: the image center must be the optical axis.</p>";

      this.fillSeriesTree = function() {
         // The selection survives the rebuild (a side button acts on it).
         let selected = d.selectedSeriesFrames();
         d.seriesTree.clear();
         d.seriesFrames.forEach(function(f) {
            let node = new TreeBoxNode(d.seriesTree);
            node.selected = selected.indexOf(f) >= 0;
            let m = f.metrics;
            let tilt = m ? seriesMetric("tilt").get(m) : null;
            let cells = [
               f.name, f.side, f.sideSource,
               f.haH !== null ? format("%+.2f", f.haH) : "",
               f.altDeg !== null ? format("%.0f°", f.altDeg) : "",
               f.focusPos !== null ? format("%.0f", f.focusPos) : "",
               m ? format("%d", m.n) : (f.error || f.headerError ? "failed" : ""),
               m && m.surface ? format("%.2f", m.surface.fCenter) : "",
               tilt ? format("%.0f%% %s", 100 * Math.hypot(tilt[0], tilt[1]),
                  directionName(Math.atan2(tilt[1], tilt[0]) * 180 / Math.PI)) : "",
               m && m.rings && m.rings.eRad !== null ? format("%+.3f", m.rings.eRad) : "",
               m && m.tracking ? format("%.3f", m.tracking.ellipticity) : "",
               m && m.coma ? format("%+.3f", m.coma.k) : "",
               f.sip ? format("%.1f px", sipMaxShift(sipCornerShifts(f))) : ""
            ];
            for (let i = 0; i < cells.length; ++i)
               node.setText(i, cells[i]);
            node.setToolTip(0, f.path);
            if (f.error || f.headerError)
               node.setToolTip(6, f.error || f.headerError);
         });
         if (typeof d.seriesTree.adjustColumnWidthToContents === "function")
            for (let i = 0; i < SERIES_COLUMNS.length; ++i)
               d.seriesTree.adjustColumnWidthToContents(i);
      };

      // A click on a measured frame shows its map (the reduced copy kept by
      // Analyze series); the Preview's analysis is not
      // affected, a Preview or a layer change draws the target's map again.
      this.seriesTree.onNodeClicked = function(node) {
         // The same node, or else (should PJSR hand out a new wrapper per
         // call) the first frame with the node's file name.
         let f = null;
         for (let i = 0; i < d.seriesTree.numberOfChildren && f === null; ++i)
            if (d.seriesTree.child(i) === node)
               f = d.seriesFrames[i];
         if (f === null && node)
            f = d.seriesFrames.filter(function(fr) { return fr.name === node.text(0); })[0] || null;
         if (f && f.mapBitmap && d.seriesShown)
            d.showSeriesTab(SERIES_TAB_MAP);
         if (f && f.mapBitmap)
            d.setPreviewBitmap(f.mapBitmap, "Series frame <i>" + f.name + "</i> - " +
               mapViewByKey(f.mapView).label + " (reduced)");
      };

      this.seriesShowMapsCheck = new CheckBox(this);
      this.seriesShowMapsCheck.text = "Show each frame's map";
      this.seriesShowMapsCheck.checked = parameters.seriesShowMaps;
      this.seriesShowMapsCheck.toolTip =
         "<p>Draws the map of the selected tab (Star shape, Star size or Coma) of each frame in the preview as " +
         "soon as it is measured, and keeps a reduced copy: clicking a frame in the list shows it again. This " +
         "takes a few seconds more per frame.</p>" +
         "<p>Only the display changes: the analysis of Calculate and the Assessment tab stay as " +
         "they are.</p>";

      this.seriesSaveMapsCheck = new CheckBox(this);
      this.seriesSaveMapsCheck.text = "Save maps next to the frames";
      this.seriesSaveMapsCheck.checked = parameters.seriesSaveMaps;
      this.seriesSaveMapsCheck.toolTip =
         "<p>Saves for each frame the images Save opens, as PNG in the frame's directory, named after " +
         "the frame so that they sort right after it:</p>" +
         "<p><i>name</i>_Size.png, <i>name</i>_Shape.png, <i>name</i>_Coma.png, <i>name</i>_Tilt.png - the " +
         "four maps with the layers of their tabs<br/>" +
         "<i>name</i>_Aberration3D.png - the 3D sensor tilt plot, when it is enabled (Setup > General) and pixel " +
         "pitch, focal length and aperture are known</p>" +
         "<p>After pass 2 also SeriesStatic_<i>group</i>_Size.png ... _Tilt.png in the directory of the first " +
         "frame - the static maps of all frames and of each side.</p>" +
         "<p>Files of an earlier run are replaced.</p>";

      this.seriesSaveMosaicCheck = new CheckBox(this);
      this.seriesSaveMosaicCheck.text = "Save a 3x3 mosaic next to the frames";
      this.seriesSaveMosaicCheck.checked = parameters.seriesSaveMosaic;
      this.seriesSaveMosaicCheck.toolTip =
         "<p>Saves for each frame a mosaic as AberrationInspector draws it: the four corners, the four edge " +
         "centers and the image center, each cropped 1:1 to 512 x 512 px of the stretched image, separated by " +
         "gray lines.</p>" +
         "<p><i>name</i>_Mosaic.png - PNG, in the frame's directory</p>" +
         "<p>With Debayer enabled, the tiles come from the half-size SuperPixel image. Files of an earlier run " +
         "are replaced.</p>";

      // The maps change the estimated run time in the hints.
      [this.seriesShowMapsCheck, this.seriesSaveMapsCheck, this.seriesSaveMosaicCheck].forEach(function(c) {
         c.onCheck = function() {
            if (d.seriesHintsShown)
               d.updateSeriesHints();
         };
      });

      this.seriesBox = new TextBox(this);
      this.seriesBox.readOnly = true;
      this.seriesBox.setScaledMinSize(300, 150);
      this.clearSeriesResult = function() {
         d.seriesResult = null;
         d.seriesStop = null;
         d.setSeriesStatic([]);
         d.seriesBox.text = d.seriesFrames.length === 0 ?
            "<i>Add unregistered light frames from before and after a meridian flip.</i>" :
            "<i>Click Analyze series to measure the frames and compare the sides.</i>";
         if (d.syncLeftPane)
            d.syncLeftPane();
      };
      this.seriesStop = null; // { after } when Stop ended the last run (formatSeriesReport())
      this.showSeriesResult = function() {
         d.seriesResult = compareSeries(d.seriesFrames);
         d.seriesResult.stop = d.seriesStop;
         d.seriesBox.text = formatSeriesReport(d.seriesResult);
         if (d.syncLeftPane)
            d.syncLeftPane();
      };
      // The sides depend on the whole list (camera angle groups), so they
      // are assigned again whenever it changes.
      this.seriesChanged = function(keepResult) {
         assignSeriesSides(d.seriesFrames);
         if (d.seriesHintsShown)
            d.updateSeriesHints();
         d.updateControls();
         d.fillSeriesTree();
         d.updateSideButtons();
         if (keepResult && d.seriesResult) {
            d.showSeriesResult();
            // The sides changed: pool them anew.
            if (d.seriesStatic.length > 0)
               d.setSeriesStatic(buildStaticSeries(d.seriesFrames));
         } else {
            d.clearSeriesResult();
         }
      };
      // Takes the static results of pass 2 into the source list of the maps.
      this.setSeriesStatic = function(list) {
         d.seriesStatic = list;
         if (!d.mapSourceCombo)
            return;
         let wasSeries = d.mapSource > 0;
         d.fillMapSources();
         if (wasSeries)
            d.selectMapSource(d.mapSource);
      };
      this.clearSeriesResult();

      this.seriesAddButton = new PushButton(this);
      this.seriesAddButton.text = "Add files...";
      this.seriesAddButton.onClick = function() {
         let ofd = new OpenFileDialog;
         ofd.caption = "Select light frames";
         ofd.multipleSelections = true;
         ofd.filters = [["FITS and XISF files", "*.fit", "*.fits", "*.fts", "*.xisf"], ["All files", "*"]];
         if (!ofd.execute())
            return;
         let known = {};
         d.seriesFrames.forEach(function(f) { known[f.path] = true; });
         ofd.filePaths.forEach(function(path) {
            if (!known[path])
               d.seriesFrames.push(readSeriesFrame(path));
         });
         d.seriesFrames.sort(function(a, b) {
            if (a.timeMs === null || b.timeMs === null)
               return (a.timeMs === null) - (b.timeMs === null) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
            return a.timeMs - b.timeMs;
         });
         d.seriesChanged(false);
      };

      this.selectedSeriesFrames = function() {
         let out = [];
         for (let i = 0; i < d.seriesTree.numberOfChildren; ++i)
            if (d.seriesTree.child(i).selected)
               out.push(d.seriesFrames[i]);
         return out;
      };

      this.seriesRemoveButton = new PushButton(this);
      this.seriesRemoveButton.text = "Remove";
      this.seriesRemoveButton.toolTip = "<p>Removes the selected frames from the list.</p>";
      this.seriesRemoveButton.onClick = function() {
         let selected = d.selectedSeriesFrames();
         d.seriesFrames = d.seriesFrames.filter(function(f) { return selected.indexOf(f) < 0; });
         d.seriesChanged(false);
      };

      this.seriesClearButton = new PushButton(this);
      this.seriesClearButton.text = "Clear";
      this.seriesClearButton.onClick = function() {
         d.seriesFrames = [];
         d.seriesChanged(false);
      };

      // Sets the side of the selected frames by hand (null = automatic).
      // Checkable tool buttons, so that the side of the selection shows:
      // see updateSideButtons().
      let sideButton = function(text, side, tip) {
         let button = new ToolButton(d);
         button.text = text;
         button.toolTip = tip;
         button.checkable = true;
         button.onClick = function() {
            d.selectedSeriesFrames().forEach(function(f) { f.manualSide = side; });
            d.seriesChanged(true);
         };
         return button;
      };
      // Checks the button of the side the selected frames share: Auto for
      // frames without a manual side - the default of every new frame, and
      // what Auto shows with nothing selected; none for a mixed selection.
      this.updateSideButtons = function() {
         let selected = d.selectedSeriesFrames();
         let shared = selected.length === 0 ? null : selected[0].manualSide;
         let mixed = selected.some(function(f) { return f.manualSide !== shared; });
         d.seriesWestButton.checked = !mixed && shared === SIDE_WEST;
         d.seriesEastButton.checked = !mixed && shared === SIDE_EAST;
         d.seriesAutoButton.checked = !mixed && shared === null;
      };
      this.seriesSideLabel = new Label(this);
      this.seriesSideLabel.text = "Side:";
      this.seriesSideLabel.toolTip = "<p>Sets the side of the selected frames.</p>";
      this.seriesSideLabel.textAlignment = LABEL_ALIGNMENT;
      this.seriesWestButton = sideButton("West", SIDE_WEST,
         "<p>Before the flip: telescope west of the pier, pointing east (as N.I.N.A. writes PIERSIDE).</p>");
      this.seriesEastButton = sideButton("East", SIDE_EAST,
         "<p>After the flip: telescope east of the pier, pointing west.</p>");
      this.seriesAutoButton = sideButton("Auto", null,
         "<p>Takes the side from the header again (PIERSIDE, camera angle or hour angle).</p>");

      this.seriesTree.onNodeSelectionUpdated = function() { d.updateSideButtons(); };
      this.updateSideButtons();

      this.seriesAnalyzeButton = new PushButton(this);
      this.seriesAnalyzeButton.text = "Analyze series";
      this.seriesAnalyzeButton.toolTip =
         "<p>Measures every frame with the current settings (Setup > Star detection, debayer, tracking subtraction, " +
         "optics) and compares the sides: tilt, FWHM, edge pattern, tracking, coma strength and coma-free " +
         "point, each judged as stable or changing at the flip, plus drifts over the session.</p>" +
         "<p>Each frame takes about as long as a Calculate. <b>Stop</b> ends the run after the current frame; " +
         "the frames measured so far are compared, pass 2 is skipped. The CSV export (Setup > General) writes " +
         "one line per frame.</p>" +
         "<p><b>Pass 2</b> then pools the stars of all frames, and of each side, after taking out each frame's " +
         "dynamic tracking (its own uniform term less the mean) and scaling its FWHM to the median center " +
         "FWHM: the static optics with about √n smaller errors. The maps show it at once (Maps - Show: Series " +
         "static); the report gives the static and the dynamic tracking. Calculate stays a single pass on the " +
         "target image - to check the settings before a series.</p>";
      this.seriesAnalyzeButton.onClick = function() {
         if (d.seriesFrames.length === 0) {
            (new MessageBox("Add some light frames first.", TITLE, StdIcon.Information, StdButton.Ok)).execute();
            return;
         }
         let p = d.collectParameters();
         parameters.assign(p);
         parameters.SaveSettings();
         const SERIES_MAP_KEEP_SIDE = 1600; // longer side of the kept copy of each frame's map, px
         let onFrame = (p.seriesShowMaps || p.seriesSaveMaps || p.seriesSaveMosaic) ?
               function(f, analysis, optics, i, report) {
            if (p.seriesSaveMosaic) {
               try {
                  saveSeriesFrameMosaic(f, analysis);
               } catch (x) {
                  Console.warningln("Series frame " + f.name + ": " + (x.message || x));
               }
            }
            if (!p.seriesShowMaps && !p.seriesSaveMaps)
               return;
            // Saving needs all maps, showing only the one of the selected tab.
            Progress.step("Drawing the maps", 0.85, 1);
            let maps = {};
            (p.seriesSaveMaps ? MAP_VIEWS : [mapViewByKey(d.mapView)]).forEach(function(v) {
               maps[v.key] = renderVectorMap(analysis, p, optics, false, v.key);
            });
            let bmp = maps[d.mapView];
            if (p.seriesSaveMaps) {
               try {
                  saveSeriesFrameImages(f, maps, analysis, optics, report, p);
               } catch (x) {
                  Console.warningln("Series frame " + f.name + ": " + (x.message || x));
               }
            }
            if (p.seriesShowMaps) {
               let k = Math.min(1, SERIES_MAP_KEEP_SIDE / Math.max(bmp.width, bmp.height));
               f.mapBitmap = (k < 1) ?
                  bmp.scaledTo(Math.max(1, Math.round(bmp.width * k)), Math.max(1, Math.round(bmp.height * k))) : bmp;
               f.mapView = d.mapView;
               d.setPreviewBitmap(bmp, format("Series frame %d of %d: <i>%s</i> - %s", i + 1, d.seriesFrames.length,
                  f.name, mapViewByKey(d.mapView).label));
            }
            d.fillSeriesTree();
            CoreApplication.processEvents();
         } : null;
         // Pass 1: measure every frame. The hints give way to the preview,
         // which shows each frame's map.
         let seriesT0 = Date.now();
         Console.writeln(format("Analyze series: %d frame(s)", d.seriesFrames.length));
         d.seriesRunning = true;
         d.syncLeftPane();
         d.showSeriesTab(SERIES_TAB_MAP);
         let run = d.runBusy(function() {
            return measureSeries(d.seriesFrames, p, function() { return d.stopRequested; }, onFrame);
         }, true);
         d.seriesRunning = false;
         // The time per frame of this run, for the estimate of the next one.
         let measuredFrames = d.seriesFrames.filter(function(f) { return f.metrics !== null || f.error; }).length;
         if (measuredFrames > 0)
            d.frameTiming = { seconds: (Date.now() - seriesT0) / 1000 / measuredFrames, source: "series",
                              maps: onFrame !== null };
         d.seriesChanged(false);
         d.seriesStop = run && run.stopped ? { after: run.after } : null;
         d.showSeriesResult();
         d.showSeriesTab(SERIES_TAB_REPORT);

         // Pass 2: pool the frames - the static optics, the dynamic tracking.
         // Skipped after a Stop: the pooled maps would leave frames out.
         let staticList = d.seriesStop ? null : d.runBusy(function() {
            Progress.step("Pass 2: pooling the frames", 0, 1);
            let list = buildStaticSeries(d.seriesFrames);
            if (list.length > 0 && p.seriesSaveMaps) {
               let dir = File.extractDrive(d.seriesFrames[0].path) + File.extractDirectory(d.seriesFrames[0].path);
               list.forEach(function(e) {
                  MAP_VIEWS.forEach(function(v) {
                     let path = dir + "/SeriesStatic_" + e.label.replace(/[^A-Za-z0-9]+/g, "") + v.fileSuffix;
                     try {
                        let bmp = renderVectorMap(e.analysis, p, e.analysis.optics, false, v.key);
                        if (File.exists(path))
                           File.remove(path);
                        if (bmp.save(path) === false)
                           throw new Error("Cannot write " + path);
                        Console.writeln("Saved: " + path);
                     } catch (x) {
                        Console.warningln("Series static: " + (x.message || x));
                     }
                  });
               });
            }
            return list;
         });
         if (staticList && staticList.length > 0) {
            d.setSeriesStatic(staticList);
            d.selectMapSource(1); // the static result of all frames: the Map tab shows it
            d.seriesBox.text += "<br><br><b>Series static</b> (pass 2): see Maps - Show: Series static. " +
               staticList.map(function(e) { return formatStaticSeries(e).join("<br>"); }).join("<br>");
         }
         Console.writeln(format("Series done in %.1f s.", (Date.now() - seriesT0) / 1000));
         if (p.doExport) // in the directory of the (first) frame
            exportSeriesCSV(d.seriesFrames, File.extractDrive(d.seriesFrames[0].path) +
               File.extractDirectory(d.seriesFrames[0].path) + "/StarAberrationSeries.csv");
      };

      this.checkTarget = function() {
         if (d.view && d.view.id.length > 0)
            return true;
         (new MessageBox("Please select a target image first.", TITLE, StdIcon.Error, StdButton.Ok)).execute();
         return false;
      };

      this.previewButton = new PushButton(this);
      this.previewButton.text = "Calculate";
      this.previewButton.toolTip =
         "<p>Runs the analysis with the current settings and shows a map in the preview - that of the " +
         "selected Maps tab (Size, Shape, Coma, Tilt) - without opening a window, exporting the CSV file " +
         "or drawing the 3D plot. Switching between these tabs switches the map at once. " +
         "The PSF asymmetry is always measured, so that the coma layers can be switched on " +
         "later.</p>" +
         "<p>After that, the layers and their settings can be changed without measuring the " +
         "stars again: the preview is redrawn at once. Only a change of the detection " +
         "settings or the target image needs a new Calculate.</p>";
      this.previewButton.onClick = function() {
         if (!d.checkTarget())
            return;
         let p = d.collectParameters();
         let result = d.runBusy(function() {
            return processView(d.view, p, true, d.keptAnalysis(p), d.viewBitmap, d.mapView);
         });
         if (result) {
            d.analysis = result.analysis;
            if (result.measureSeconds !== null)
               d.frameTiming = { seconds: result.measureSeconds, source: "calculate" };
            d.mapSource = 0;
            d.mapSourceCombo.currentItem = 0;
            d.updateTrackingInfo();
            d.updateResultsInfo();
            d.showMap(result.bitmap);
            d.assessmentBox.text = formatAssessment(result.assessment);
            d.updateGridEvaluation();
            d.updateShapeEvaluation();
            d.updateTiltEvaluation();
               d.showMapPage();
         }
      };

      this.applyButton = new PushButton(this);
      this.applyButton.text = "Save";
      this.applyButton.toolTip = "<p>Opens the four maps in new windows: Star size, Star shape, Coma and Tilt " +
         "(AberrationSize_…, AberrationShape_…, AberrationComa_…, AberrationTilt_…), with the 3D plot and the " +
         "CSV export when " +
         "enabled, and closes the dialog. The star measurement of Calculate is reused when its detection " +
         "settings are still current.</p>";
      this.applyButton.onClick = function() {
         let p = d.collectParameters();
         // Loaded results: their maps, without a target image.
         if (d.analysis && d.analysis.source) {
            let loaded = d.analysis;
            d.done(d.runBusy(function() { return processView(loaded.source, p, false, loaded, null); }) ? 1 : 0);
            return;
         }
         if (!d.checkTarget())
            return;
         let result = d.runBusy(function() { return processView(d.view, p, false, d.keptAnalysis(p), d.viewBitmap); });
         d.done(result ? 1 : 0);
      };

      this.cancelButton = new PushButton(this);
      this.cancelButton.text = "Cancel";
      this.cancelButton.onClick = function() {
         // While a series runs, the button reads Stop (see runBusy()).
         if (d.busyStoppable) {
            d.stopRequested = true;
            d.cancelButton.enabled = false;
            return;
         }
         d.done(0);
      };

      // Progress bar of a Preview/Apply run, right of the New Instance
      // button; hidden while nothing is computed. PJSR has no progress bar
      // control, so it is drawn here.
      this.progressText = null;
      this.progressFraction = 0;
      this.progressBar = new Control(this);
      this.progressBar.setScaledFixedSize(340, 20);
      this.progressBar.visible = false;
      this.progressBar.onPaint = function(x0, y0, x1, y1) {
         if (d.progressText === null)
            return;
         let g = new Graphics(this);
         let w = this.width, h = this.height;
         g.fillRect(0, 0, w, h, new Brush(0xffd8d8d8));
         g.fillRect(0, 0, Math.round(w * d.progressFraction), h, new Brush(0xff6fa8dc));
         g.pen = new Pen(0xff808080);
         g.drawRect(0, 0, w - 1, h - 1);
         g.pen = new Pen(0xff000000);
         let text = format("%s ... %d%%", d.progressText, Math.round(100 * d.progressFraction));
         g.drawText((w - this.font.width(text)) / 2, (h + this.font.ascent - this.font.descent) / 2, text);
         g.end();
      };

      // Progress listener (see Progress): repaints the bar and lets the
      // dialog process its events, so that it stays responsive.
      this.progressListener = function(text, fraction) {
         d.progressText = text;
         d.progressFraction = fraction;
         d.progressBar.visible = text !== null;
         d.progressBar.repaint();
         CoreApplication.processEvents();
      };

      // Runs a computation with the progress bar shown. The parameters and
      // buttons are disabled meanwhile: processing events would otherwise
      // let a second Preview start in the middle of the first. With
      // stoppable, the Cancel button stays enabled as Stop and sets
      // stopRequested, which the task has to check itself.
      this.busyStoppable = false;
      this.isBusy = false;
      this.stopRequested = false;
      this.runBusy = function(task, stoppable) {
         let controls = [d.parameters_TabBox, d.previewButton, d.applyButton, d.cancelButton,
                         d.newInstanceButton, d.preview];
         if (stoppable)
            controls.splice(controls.indexOf(d.cancelButton), 1);
         controls.forEach(function(c) { c.enabled = false; });
         d.busyStoppable = !!stoppable;
         d.isBusy = true;
         d.stopRequested = false;
         if (stoppable)
            d.cancelButton.text = "Stop";
         Progress.listener = d.progressListener;
         try {
            return task();
         } finally {
            Progress.finish();
            Progress.listener = null;
            d.busyStoppable = false;
            d.isBusy = false;
            d.cancelButton.text = "Cancel";
            d.cancelButton.enabled = true;
            controls.forEach(function(c) { c.enabled = true; });
         }
      };

      let buttonRow = new HorizontalSizer;
      buttonRow.spacing = 6;
      buttonRow.add(this.newInstanceButton);
      buttonRow.add(this.documentationButton);
      buttonRow.add(this.progressBar);
      buttonRow.addStretch();
      buttonRow.add(this.previewButton);
      buttonRow.add(this.applyButton);
      buttonRow.add(this.cancelButton);


      // ---- Preview -------------------------------------------------------

      // The target image or the vector map, stretched for display, in the
      // standard PJSR image view: zoom buttons, mouse wheel zoom, scroll
      // bars and dragging, as in AperturePhotometry.
      this.preview = new ImageView(this);
      // The free room around an image smaller than the view takes the
      // dialog's background color instead of the default dark gray.
      if ((this.backgroundColor & 0x00ffffff) !== 0)
         this.preview.viewportCanvasColor = this.backgroundColor;

      // ImageView zooms in 1:n steps only, so its own zoom to fit leaves the
      // image well smaller than the view. Here, fitting shows the bitmap
      // scaled to exactly fill the view (at ImageView's 1:1) and follows the
      // size of the view; the zoom buttons and the mouse wheel leave the
      // fitted view for the next 1:n step of the full bitmap.
      const PREVIEW_FIT_SOURCE_SIZE = 3000; // longer side of the copy fitted views are scaled from
      this.previewFull = null;      // the bitmap shown, at full size
      this.previewFitSource = null; // a reduced copy of it, for fast refitting
      this.previewFitting = true;   // the fitted view is shown
      this.previewFitScale = 1;     // fitted view: scale of previewFull on the screen
      let viewport = this.preview.scrollbox.viewport;

      this.fitPreview = function() {
         let full = d.previewFull;
         if (!full || viewport.width <= 0 || viewport.height <= 0)
            return;
         d.previewFitting = true;
         let k = Math.min(viewport.width / full.width, viewport.height / full.height);
         if (k >= 1) {
            d.previewFitScale = 1;
            d.preview.setImage(full);
            return;
         }
         let fw = Math.max(1, Math.floor(full.width * k)), fh = Math.max(1, Math.floor(full.height * k));
         d.previewFitScale = fw / full.width;
         d.preview.setImage(d.previewFitSource.scaledTo(fw, fh));
         d.preview.zoomVal_Label.text = format("1:%.2f", 1 / d.previewFitScale);
      };

      // Leaves the fitted view for the full bitmap at the 1:n (or n:1) zoom
      // next to the fitted scale: direction +1 larger, -1 smaller, 0 1:1.
      this.leaveFit = function(direction) {
         let s = d.previewFitScale;
         let zoom = 1;
         if (direction > 0)
            zoom = (s >= 1) ? 2 : ((Math.ceil(1 / s) - 1 > 1) ? -(Math.ceil(1 / s) - 1) : 1);
         else if (direction < 0)
            zoom = (s >= 1) ? -2 : -(Math.floor(1 / s) + 1);
         d.previewFitting = false;
         d.preview.setImage(d.previewFull);
         if (zoom !== 1)
            d.preview.regenerate(null, zoom);
      };

      this.preview.zoomIn_Button.onMousePress = function() {
         if (d.previewFitting)
            d.leaveFit(+1);
         else
            d.preview.zoomIn();
      };
      this.preview.zoomOut_Button.onMousePress = function() {
         if (d.previewFitting)
            d.leaveFit(-1);
         else
            d.preview.zoomOut();
      };
      this.preview.zoom11_Button.onMousePress = function() {
         if (d.previewFitting)
            d.leaveFit(0);
         else
            d.preview.zoom1_1();
      };
      this.preview.zoomFit_Button.onMousePress = function() {
         d.fitPreview();
      };

      // ImageView's own viewport handlers are kept and extended.
      let imageViewWheel = viewport.onMouseWheel;
      viewport.onMouseWheel = function(x, y, delta, buttonState, modifiers) {
         if (d.previewFitting)
            d.leaveFit((delta > 0) ? -1 : +1);
         else if (typeof imageViewWheel === "function")
            imageViewWheel.call(this, x, y, delta, buttonState, modifiers);
      };
      let imageViewResize = viewport.onResize;
      viewport.onResize = function(newWidth, newHeight) {
         if (typeof imageViewResize === "function")
            imageViewResize.call(this, newWidth, newHeight);
         if (d.previewFitting)
            d.fitPreview();
      };
      // The coordinates readout of a fitted view in pixels of the full bitmap.
      let imageViewMove = viewport.onMouseMove;
      viewport.onMouseMove = function(x, y, buttonState, modifiers) {
         if (typeof imageViewMove === "function")
            imageViewMove.call(this, x, y, buttonState, modifiers);
         if (d.previewFitting && d.previewFitScale < 1 && d.preview.xValue_Label.text !== "---") {
            let pt = d.preview.viewportToImage(x, y);
            d.preview.xValue_Label.text = format("%8.2f", pt.x / d.previewFitScale);
            d.preview.yValue_Label.text = format("%8.2f", pt.y / d.previewFitScale);
         }
      };

      // Shows a bitmap in the preview. A bitmap of the same size replaces
      // the one shown without changing zoom and position (switching layers,
      // the map of the image shown); another size is fitted into the view.
      // Without a bitmap, the tips for the test frames take its place.
      this.setPreviewBitmap = function(bmp, status) {
         if (bmp === null) {
            d.previewFull = d.previewFitSource = null;
            d.preview.clear();
            d.showTips(true);
         } else {
            let wasTips = d.tipsShown;
            d.showTips(false);
            if (wasTips)
               d.previewFull = null; // the view had no room meanwhile: fit anew
            let sameSize = d.previewFull !== null &&
               d.previewFull.width === bmp.width && d.previewFull.height === bmp.height;
            d.previewFull = bmp;
            let k = Math.min(1, PREVIEW_FIT_SOURCE_SIZE / Math.max(bmp.width, bmp.height));
            d.previewFitSource = (k < 1) ?
               bmp.scaledTo(Math.max(1, Math.round(bmp.width * k)), Math.max(1, Math.round(bmp.height * k))) : bmp;
            if (!sameSize || d.previewFitting)
               d.fitPreview();
            else
               d.preview.regenerate(bmp);
         }
         d.preview.setStatusMessage(status || "");
      };

      // Shows the target image; a kept analysis belongs to the previous one.
      this.updatePreview = function() {
         d.analysis = null;
         d.updateResultsInfo();
         // A new target image: the maps show it again (not a series result).
         d.mapSource = 0;
         if (d.mapSourceCombo)
            d.mapSourceCombo.currentItem = 0;
         d.updateTrackingInfo();
         d.updateAssessment();
         d.updateGridEvaluation();
         d.updateShapeEvaluation();
         d.updateTiltEvaluation();
         d.previewStale = false;
         let bmp = null;
         d.viewBitmap = null;
         if (d.view && d.view.id.length > 0) {
            try {
               bmp = d.viewBitmap = renderStretchedBitmap(d.view);
            } catch (x) {
               Console.warningln("Preview: " + x);
            }
         }
         d.setPreviewBitmap(bmp, bmp ? "<i>" + d.view.id + "</i>" : "No target image selected");
      };

      // ---- Layout --------------------------------------------------------

      // How to take a frame that the analysis can judge well. The analysis
      // takes the image center for the optical axis, needs stars in every
      // quadrant and ring, skips saturated stars and fits linear data.
      this.tipsLabel = new Label(this);
      this.tipsLabel.wordWrapping = true;
      this.tipsLabel.useRichText = true;
      // One heading per topic with short bullet points.
      let tipsSection = function(title, items) {
         return "<p style='margin-top:10px; margin-bottom:2px;'><b>" + title + "</b></p>" +
            "<ul style='margin-top:0px; margin-left:-20px;'>" +
            items.map(function(t) { return "<li>" + t + "</li>"; }).join("") + "</ul>";
      };
      this.tipsLabel.text =
         "<h3>Tips for the test frames</h3>" +
         tipsSection("Exposure", [
            "Single subframes of about 10-60 s",
            "Long enough for many stars with good signal",
            "Short enough that seeing and tracking errors do not dominate",
            "No saturated stars - they are skipped"]) +
         tipsSection("Focus", [
            "Focus carefully on the image center (Bahtinov mask or FWHM)",
            "Let the optics reach thermal equilibrium first"]) +
         tipsSection("Star field", [
            "Rich and even up to the corners",
            "Above 45° altitude",
            "Avoid bright nebulae and the Milky Way's dust lanes"]) +
         tipsSection("Unprocessed", [
            "Linear data",
            "Not cropped, rotated, registered, drizzled or deconvolved - the image center must stay " +
            "the optical axis",
            "Raw CFA frames: enable Debayer"]) +
         tipsSection("Filter", [
            "Luminance, or a mono camera's broadband filter",
            "Color camera: the combined channels"]) +
         tipsSection("Compare", [
            "Several frames, and after every adjustment",
            "A stack blurs tracking effects, but also mixes frames with different guiding"]);

      // The tips take the place of the image view while no image is shown
      // (no target image, or the tips entry of the target list): see
      // showTips().
      this.tipsPane = new Control(this);
      this.tipsPane.sizer = new VerticalSizer;
      this.tipsPane.sizer.margin = 16;
      this.tipsPane.sizer.add(this.tipsLabel);
      this.tipsPane.sizer.addStretch();
      this.tipsPane.hide();

      // On the Series tab before a run, the left side shows how to use the
      // series analysis, with the frames listed so far and an estimate of
      // the run time (updateSeriesHints()).
      this.frameTiming = null; // { seconds per frame, source: "calculate" | "series", maps }
      this.seriesRunning = false;
      this.seriesHintsLabel = new Label(this);
      this.seriesHintsLabel.useRichText = true;
      this.seriesHintsLabel.wordWrapping = true;
      // The files a run writes with the current settings - the frames
      // themselves are never changed.
      let seriesFilesWritten = function(frames, p) {
         let dir = frames.length > 0 ? "<i>" + File.extractDrive(frames[0].path) +
            File.extractDirectory(frames[0].path) + "</i>" : "the directory of the first frame";
         let items = [];
         items.push(p.doExport ?
            "<b>StarAberrationSeries.csv</b> - one line per frame, in " + dir :
            "No CSV table - <i>Export data table as CSV</i> is off (Setup › General)");
         if (p.seriesSaveMaps) {
            items.push("Next to each frame: <b><i>name</i>_Size.png, _Shape.png, _Coma.png, _Tilt.png</b>" +
               (p.show3DTiltPlot ? " and <b>_Aberration3D.png</b> (with pixel pitch, focal length and aperture)" :
                  ""));
            items.push("After pass 2, in " + dir + ": <b>SeriesStatic_all_Size.png ... _Tilt.png</b>, and the " +
               "same per side (e.g. SeriesStatic_West_...)");
         } else {
            items.push("No map images - <i>Save maps next to the frames</i> is off");
         }
         items.push(p.seriesSaveMosaic ? "Next to each frame: <b><i>name</i>_Mosaic.png</b>" :
            "No mosaics - <i>Save a 3x3 mosaic next to the frames</i> is off");
         items.push("Files of an earlier run are replaced; the frames themselves are never changed");
         return tipsSection("Files written", items);
      };
      this.updateSeriesHints = function() {
         let p = d.collectParameters();
         d.seriesHintsLabel.text = "<h3>Series analysis: before you start</h3>" +
            seriesHintsStatus(d.seriesFrames, d.frameTiming, p) +
            seriesFilesWritten(d.seriesFrames, p) +
            tipsSection("How many frames", [
               "At least <b>3 per side</b> of the flip - with fewer, the sides are not compared",
               "Better <b>5-10 per side</b>: the uncertainty shrinks with √n (4 frames: half, 9: a third, " +
               "16: a quarter of a single frame's)",
               "Beyond about 15 per side, more frames gain little"]) +
            tipsSection("Before and after the meridian flip", [
               "Frames from <b>both sides</b>: after the flip gravity pulls on the optical train from the " +
               "other side",
               "What changes with the side is mechanical - sag, play, mirror flop, focuser, a flexing guide " +
               "scope; what stays is fixed in the optics - sensor tilt, spacing, collimation",
               "Best the last frames before and the first after the flip: similar altitude and temperature, " +
               "so the flip is the only change",
               "Without a flip, the series still shows the static optics and the drift over the session"]) +
            tipsSection("The frames", [
               "Unregistered, uncropped light frames of one night and one optical train - the image center " +
               "must be the optical axis",
               "Same filter and exposure; leave out frames with clouds, wind gusts or bad guiding",
               "Check the settings first with Calculate on one frame: are there enough stars up to the corners?",
               "The side comes from PIERSIDE (N.I.N.A., ASCOM), the camera angle (ASIAIR) or the hour angle: " +
               "check the Side column, and set it with West / East where it is ?"]);
      };
      this.seriesHintsPane = new Control(this);
      this.seriesHintsPane.sizer = new VerticalSizer;
      this.seriesHintsPane.sizer.margin = 16;
      this.seriesHintsPane.sizer.add(this.seriesHintsLabel);
      this.seriesHintsPane.sizer.addStretch();
      this.seriesHintsPane.hide();

      // On the Assessment tab, the left side shows the assessment of the
      // script (assessImage()) instead of a map.
      let textPage = function(box) {
         let page = new Control(d);
         page.sizer = new VerticalSizer;
         page.sizer.margin = 4;
         page.sizer.add(box, 100);
         return page;
      };
      this.assessmentPane = textPage(this.assessmentBox);
      this.assessmentPane.hide();

      // The map: the preview, or the tips in its place without an image
      // (showTips()). On the Series tab after a run it moves into the Map
      // tab of seriesPane, else it fills the left side (placeMapHost()).
      this.mapHost = new Control(this);
      this.mapHost.sizer = new VerticalSizer;
      this.mapHost.sizer.add(this.preview, 100);
      this.mapHost.sizer.add(this.tipsPane, 100);

      // On the Series tab once a run has started: the map of the series
      // (each frame's, or the static result) and the report of the script as
      // tabs.
      this.seriesMapPage = new Control(this);
      this.seriesMapPage.sizer = new VerticalSizer;
      this.seriesMapPage.sizer.margin = 4;
      this.seriesPane = new TabBox(this);
      this.seriesPane.addPage(this.seriesMapPage, "Map");
      this.seriesPane.addPage(textPage(this.seriesBox), "Series report");
      this.seriesPane.hide();
      this.seriesPane.onPageSelected = function(index) {
         if (index === 0 && d.previewFull && d.previewFitting)
            d.fitPreview();
      };
      const SERIES_TAB_MAP = 0, SERIES_TAB_REPORT = 1;
      this.showSeriesTab = function(index) { d.seriesPane.currentPageIndex = index; };

      this.previewPane = new Control(this);
      this.previewPane.sizer = new VerticalSizer;
      this.previewPane.sizer.add(this.mapHost, 100);
      this.previewPane.sizer.add(this.assessmentPane, 100);
      this.previewPane.sizer.add(this.seriesHintsPane, 100);
      this.previewPane.sizer.add(this.seriesPane, 100);

      // Moves the map between the left side ("pane") and the Map tab of the
      // Series tab ("series"): out of one sizer, into the other.
      this.mapHostIn = "pane";
      this.placeMapHost = function(where) {
         if (where === d.mapHostIn)
            return;
         let from = d.mapHostIn === "series" ? d.seriesMapPage : d.previewPane;
         let to = where === "series" ? d.seriesMapPage : d.previewPane;
         from.sizer.remove(d.mapHost);
         d.mapHost.parent = to;
         to.sizer.add(d.mapHost, 100);
         d.mapHostIn = where;
      };

      // What the left side shows: the assessments (Assessment tab), the
      // series hints (Series tab before a run), the series tabs (Series tab
      // from a run on), else the map. Own flags: `visible` is false for
      // every child before the dialog is shown.
      this.tipsShown = false;
      this.assessmentShown = false;
      this.seriesHintsShown = false;
      this.seriesShown = false;
      this.leftPane = "map";
      this.updateLeftPane = function() {
         let pane = d.assessmentShown ? "assessment" : d.seriesHintsShown ? "seriesHints" :
            d.seriesShown ? "series" : "map";
         if (pane === d.leftPane)
            return;
         d.leftPane = pane;
         d.placeMapHost(pane === "series" ? "series" : "pane");
         [[d.mapHost, "map"], [d.assessmentPane, "assessment"], [d.seriesHintsPane, "seriesHints"],
          [d.seriesPane, "series"]].forEach(function(c) {
            if (c[1] === pane || (c[0] === d.mapHost && pane === "series"))
               c[0].show();
            else
               c[0].hide();
         });
         // A map set while the preview was hidden is fitted to the view now.
         if ((pane === "map" || pane === "series") && d.previewFull && d.previewFitting)
            d.fitPreview();
      };
      // The tips take the place of the preview inside the map.
      this.showTips = function(tips) {
         if (tips === d.tipsShown)
            return;
         d.tipsShown = tips;
         if (tips) {
            d.preview.hide();
            d.tipsPane.show();
         } else {
            d.tipsPane.hide();
            d.preview.show();
         }
      };

      // Four tabs with sub-tabs: Setup (General: target, debayer, optics,
      // output; Detection: the star measurement), Maps (one sub-tab per map
      // of MAP_VIEWS), Assessment and Series. Each layer starts with the
      // check box that enables it, the first row of its group box, which in
      // turn enables its settings (updateControls). Check boxes and texts
      // without a label start where the input fields start (indentRow()).
      // The camera name can be long: its field takes the width of the row.
      // It sits behind an empty label with the same spacing as the edit
      // field inside a NumericEdit (pjsr/NumericControl.jsh), so that it
      // starts where the number fields above start.
      let cameraField = new Control(this);
      cameraField.sizer = new HorizontalSizer;
      cameraField.sizer.spacing = 4;
      cameraField.sizer.add(new Label(cameraField));
      cameraField.sizer.add(this.cameraValue, 100);
      // The mount, aligned like the other fields, its source beside it.
      let mountRow = new HorizontalSizer;
      mountRow.spacing = 4;
      mountRow.add(this.mountLabel);
      mountRow.add(alignedField(this.mountCombo));
      mountRow.addSpacing(6);
      mountRow.add(this.mountSourceLabel);
      mountRow.addStretch();
      let guidingRow = new HorizontalSizer;
      guidingRow.spacing = 4;
      guidingRow.add(this.guidingLabel);
      guidingRow.add(alignedField(this.guidingCombo));
      guidingRow.addSpacing(6);
      guidingRow.add(this.guidingSourceLabel);
      guidingRow.addStretch();
      let cameraRow = new HorizontalSizer;
      cameraRow.spacing = 4;
      cameraRow.add(this.cameraLabel);
      cameraRow.add(cameraField, 100);

      this.general_Control = page(null, [
         groupBox("Target", [viewRow, infoRow, resultsRow, resultsInfoRow]),
         groupBox("Debayer", [
            checkRow(this.debayerCheck),
            fieldRow(this.bayerPatternLabel, this.bayerPatternCombo),
            checkRow(this.closeWindowsCheck)]),
         groupBox("Optics, camera and mount", [
            fieldRow(this.opticsTypeLabel, this.opticsTypeCombo),
            checkRow(this.opticsFromHeaderCheck),
            fieldRow(this.pixelPitchLabel, this.pixelPitchSpin),
            fieldRow(this.focalLengthLabel, this.focalLengthSpin),
            fieldRow(this.apertureLabel, this.apertureSpin),
            cameraRow,
            mountRow,
            guidingRow]),
         groupBox("Output", [
            checkRow(this.tiltPlot3DCheck),
            checkRow(this.exportCheck)])
      ]);

      this.detection_Control = page(null, [
         groupBox("Star detection", [
            fieldRow(this.thresholdLabel, this.thresholdSpin),
            fieldRow(this.maxCandLabel, this.maxCandSpin),
            checkRow(this.sdCustomCheck),
            fieldRow(this.sdLayersLabel, this.sdLayersSpin),
            fieldRow(this.sdSensitivityLabel, this.sdSensitivitySpin),
            fieldRow(this.sdPeakResponseLabel, this.sdPeakResponseSpin),
            fieldRow(this.sdMaxDistortionLabel, this.sdMaxDistortionSpin),
            checkRow(this.sdClusteredCheck)]),
         groupBox("PSF fit", [
            checkRow(this.moffatCheck),
            fieldRow(this.radiusLabel, this.radiusSpin),
            fieldRow(this.madLabel, this.madSpin)]),
         groupBox("Common tracking error", [
            trackingInfoRow,
            checkRow(this.subtractTrackingCheck)])
      ]);

      // One page per map (MAP_VIEWS): selecting it shows its map in the
      // preview (parameters_TabBox.onPageSelected).
      // Rich text of evaluation lines ("Topic: text"; lines starting with
      // spaces indented under the line above), as on both evaluation boxes.
      let evaluationHtml = function(lines) {
         return lines.map(function(line) {
            let sub = /^\s/.test(line);
            line = line.trim();
            let k = line.indexOf(":");
            return "<p style='margin-top:0px; margin-bottom:" + (sub ? "1px; margin-left:16px;" : "4px;") + "'>" +
               (k > 0 ? "<b>" + line.slice(0, k + 1) + "</b>" + line.slice(k + 1) : line) + "</p>";
         }).join("");
      };
      this.evaluationHtml = evaluationHtml;

      // The evaluation of the star shapes, for the kept analysis with the
      // current settings: updateShapeEvaluation().
      this.shapeEvalLabel = new Label(this);
      this.shapeEvalLabel.wordWrapping = true;
      this.shapeEvalLabel.useRichText = true;
      this.updateShapeEvaluation = function() {
         let an = d.currentAnalysis();
         if (!an) {
            d.shapeEvalLabel.text = "<i>Click Calculate to evaluate the image.</i>";
            return;
         }
         let p = d.collectParameters();
         let lines = an.series ? formatStaticSeries(d.seriesStatic[d.mapSource - 1]) : [];
         d.shapeEvalLabel.text = evaluationHtml(lines.concat(formatShapeEvaluation(shapeEvaluationFor(an, p),
            an.tracking, p.subtractTracking)));
      };
      this.updateShapeEvaluation();

      this.shape_Control = page(null, [
         groupBox("Star ellipses", [
            checkRow(this.showEllipsesCheck),
            fieldRow(this.scaleLabel, this.scaleSpin),
            checkRow(this.shapeShowCellsCheck)]),
         groupBox("Streamlines", [
            checkRow(this.streamlinesCheck),
            fieldRow(this.streamlineRadiusLabel, this.streamlineRadiusSpin),
            checkRow(this.shapeSignificantOnlyCheck)]),
         groupBox("Optics model", [
            checkRow(this.shapeShowModelCheck),
            checkRow(this.shapeModelSignificantOnlyCheck),
            checkRow(this.shapeShowMatchCheck),
            checkRow(this.shapeShowDefectsCheck)]),
         groupBox("Orientation heatmap", [
            checkRow(this.orientationHeatmapCheck),
            fieldRow(this.orientationHeatmapDegreeLabel, this.orientationHeatmapDegreeSpin)]),
         groupBox("Evaluation", [this.shapeEvalLabel])
      ]);

      // The evaluation of the FWHM grid (formerly the legend in the map),
      // for the kept analysis with the current settings: updateGridEvaluation().
      this.gridEvalLabel = new Label(this);
      this.gridEvalLabel.wordWrapping = true;
      this.gridEvalLabel.useRichText = true;
      this.updateGridEvaluation = function() {
         let an = d.currentAnalysis();
         if (!an) {
            d.gridEvalLabel.text = "<i>Click Calculate to evaluate the image.</i>";
            return;
         }
         let p = d.collectParameters();
         let optics = d.opticsFor(an, p);
         let ev = evaluateFwhmGrid(starsFor(an, p.subtractTracking), an.w, an.h, FWHM_GRID_SIZE);
         let u = fwhmDisplayUnit(p, optics);
         d.gridEvalLabel.text = d.evaluationHtml(formatFwhmGridEvaluation(ev, u.scale, u.unit));
      };
      this.updateGridEvaluation();
      let evalGroup = groupBox("Evaluation", [this.gridEvalLabel]);
      evalGroup.toolTip = this.fwhmGridHelp;

      let fwhmGroup = groupBox("FWHM grid: each cell shows", [
         checkRow(this.fwhmPxRadio),
         checkRow(this.fwhmArcsecRadio),
         checkRow(this.fwhmRatioRadio),
         checkRow(this.fwhmGridDetailsCheck)]);
      fwhmGroup.toolTip = this.fwhmGridHelp;
      let legendGroup = groupBox("Legend", [this.fwhmLegend]);
      legendGroup.toolTip = this.fwhmGridHelp;
      this.size_Control = page(null, [
         legendGroup,
         fwhmGroup,
         evalGroup
      ]);

      // The Tilt page: the tilt axis and the evaluation of the quadrants.
      this.tiltEvalLabel = new Label(this);
      this.tiltEvalLabel.wordWrapping = true;
      this.tiltEvalLabel.useRichText = true;
      this.updateTiltEvaluation = function() {
         let an = d.currentAnalysis();
         if (!an) {
            d.tiltEvalLabel.text = "<i>Click Calculate to evaluate the image.</i>";
            return;
         }
         let p = d.collectParameters();
         let optics = d.opticsFor(an, p);
         let tilt = computeSirilTilt(starsFor(an, p.subtractTracking), an.w, an.h);
         let axis = tilt !== null ? computeTiltAxis(tilt, an.w, an.h, optics.pixelPitchUm,
            optics.focalLengthMm, optics.apertureMm) : null;
         let u = fwhmDisplayUnit(p, optics);
         d.tiltEvalLabel.text = d.evaluationHtml(formatTiltEvaluation(tilt, axis, u.scale, u.unit));
      };
      this.updateTiltEvaluation();
      let tiltAxisGroup = groupBox("Tilt axis", [checkRow(this.tiltAxisCheck)]);
      tiltAxisGroup.toolTip = this.tiltHelp;
      let tiltEvalGroup = groupBox("Evaluation", [this.tiltEvalLabel]);
      tiltEvalGroup.toolTip = this.tiltHelp;
      this.tilt_Control = page(null, [tiltAxisGroup, tiltEvalGroup]);

      this.coma_Control = page(null, [
         groupBox("Coma field", [
            checkRow(this.measureAsymmetryCheck),
            checkRow(this.comaStreamlinesCheck),
            fieldRow(this.comaStreamlineRadiusLabel, this.comaStreamlineRadiusSpin),
            fieldRow(this.comaStreamlineMinLabel, this.comaStreamlineMinSpin),
            checkRow(this.comaArrowsCheck)]),
         groupBox("Per-star arrows", [
            checkRow(this.starAsymArrowsCheck),
            fieldRow(this.starAsymMinLabel, this.starAsymMinSpin),
            checkRow(this.starAsymColorCheck)])
      ]);

      // The assessment itself is shown on the left (assessmentPane); the
      // tab on the right only says so.
      this.assessmentInfoLabel = new Label(this);
      this.assessmentInfoLabel.useRichText = true;
      this.assessmentInfoLabel.wordWrapping = true;
      this.assessmentInfoLabel.text =
         "<p>The left side shows the assessment of the last Calculate (or Load results): the suggested " +
         "order of corrections, then one entry per topic with its verdict and, where something is to be " +
         "done, a suggestion.</p>" +
         "<p>The settings of Setup › General - optics type, mount and guiding - shape the suggestions.</p>";
      this.assessment_Control = new Control(this);
      this.assessment_Control.sizer = new VerticalSizer;
      this.assessment_Control.sizer.margin = 6;
      this.assessment_Control.sizer.spacing = 6;
      this.assessment_Control.sizer.add(this.assessmentInfoLabel);
      this.assessment_Control.sizer.addStretch();

      // A page holding a tab box of sub-pages.
      let subTabs = function(tabBox, pages) {
         pages.forEach(function(pg) { tabBox.addPage(pg[0], pg[1]); });
         let control = new Control(d);
         control.sizer = new VerticalSizer;
         control.sizer.margin = 4;
         control.sizer.add(tabBox, 100);
         return control;
      };
      this.setup_TabBox = new TabBox(this);
      this.setup_Control = subTabs(this.setup_TabBox, [
         [this.general_Control, "General"], [this.detection_Control, "Star detection"]]);
      // The map sub-tabs in the order of MAP_VIEWS.
      this.maps_TabBox = new TabBox(this);
      [[this.size_Control, "Size"], [this.shape_Control, "Shape"], [this.coma_Control, "Coma"],
       [this.tilt_Control, "Tilt"]].forEach(function(pg) { d.maps_TabBox.addPage(pg[0], pg[1]); });

      // What the maps show: the target image (Calculate - to check the
      // settings before a batch) or the static result of the last series
      // run (pass 2), for all frames or one side of the flip.
      this.mapSourceLabel = fieldLabel("Show:");
      this.mapSourceCombo = new ComboBox(this);
      this.mapSourceCombo.toolTip =
         "<p><b>Target image (Calculate)</b>: the image measured by Calculate - to check the settings on one " +
         "frame before running a series.</p>" +
         "<p><b>Series static</b>: after Analyze series, the stars of all frames (or of one side of the flip) " +
         "pooled. Each frame loses its dynamic tracking (its own uniform term less the mean of the group) and " +
         "its FWHM is scaled to the median center FWHM, so the seeing drops out: what remains is fixed to the " +
         "sensor - the optics, plus any constant tracking, which a single camera angle cannot tell from " +
         "astigmatism on the axis. With n frames the standard errors are about √n smaller. The background " +
         "is dark: the pooled stars belong to no single frame.</p>";
      this.fillMapSources = function() {
         d.mapSourceCombo.clear();
         d.mapSourceCombo.addItem("Target image (Calculate)");
         d.seriesStatic.forEach(function(e) {
            d.mapSourceCombo.addItem("Series static: " + e.label + " (" + e.analysis.series.frames + " frames)");
         });
         d.mapSource = Math.min(d.mapSource, d.seriesStatic.length);
         d.mapSourceCombo.currentItem = d.mapSource;
      };
      this.selectMapSource = function(index) {
         d.mapSource = index;
         d.mapSourceCombo.currentItem = index;
         if (d.currentAnalysis())
            d.refreshPreview();
         else
            d.setPreviewBitmap(d.viewBitmap, d.view && d.view.id.length > 0 ? "<i>" + d.view.id + "</i>" :
               "No target image selected");
         d.updateGridEvaluation();
         d.updateShapeEvaluation();
         d.updateTiltEvaluation();
      };
      this.mapSourceCombo.onItemSelected = function(index) { d.selectMapSource(index); };
      this.fillMapSources();

      this.maps_Control = new Control(this);
      this.maps_Control.sizer = new VerticalSizer;
      this.maps_Control.sizer.margin = 4;
      this.maps_Control.sizer.spacing = 4;
      let mapSourceRow = new HorizontalSizer;
      mapSourceRow.spacing = 4;
      mapSourceRow.add(this.mapSourceLabel);
      mapSourceRow.add(this.mapSourceCombo, 100);
      this.maps_Control.sizer.add(mapSourceRow);
      this.maps_Control.sizer.add(this.maps_TabBox, 100);

      this.parameters_TabBox = new TabBox(this);
      this.parameters_TabBox.addPage(this.setup_Control, "Setup");
      this.parameters_TabBox.addPage(this.maps_Control, "Maps");
      // The series: the file list, its buttons and the comparison.
      let seriesFileRow = new HorizontalSizer;
      seriesFileRow.spacing = 4;
      seriesFileRow.add(this.seriesAddButton);
      seriesFileRow.add(this.seriesRemoveButton);
      seriesFileRow.add(this.seriesClearButton);
      seriesFileRow.addStretch();
      let seriesSideRow = new HorizontalSizer;
      seriesSideRow.spacing = 4;
      seriesSideRow.add(this.seriesSideLabel);
      seriesSideRow.add(this.seriesWestButton);
      seriesSideRow.add(this.seriesEastButton);
      seriesSideRow.add(this.seriesAutoButton);
      seriesSideRow.addStretch();
      let seriesRunRow = new HorizontalSizer;
      seriesRunRow.spacing = 4;
      seriesRunRow.add(this.seriesAnalyzeButton);
      seriesRunRow.addStretch();
      let seriesMapRow = new VerticalSizer;
      seriesMapRow.spacing = 4;
      seriesMapRow.add(this.seriesShowMapsCheck);
      seriesMapRow.add(this.seriesSaveMapsCheck);
      seriesMapRow.add(this.seriesSaveMosaicCheck);
      this.series_Control = new Control(this);
      this.series_Control.sizer = new VerticalSizer;
      this.series_Control.sizer.margin = 6;
      this.series_Control.sizer.spacing = 6;
      this.series_Control.sizer.add(this.seriesTree, 100);
      this.series_Control.sizer.add(seriesFileRow);
      this.series_Control.sizer.add(seriesSideRow);
      this.series_Control.sizer.add(seriesMapRow);
      this.series_Control.sizer.add(seriesRunRow);

      this.parameters_TabBox.addPage(this.assessment_Control, "Assessment");
      this.parameters_TabBox.addPage(this.series_Control, "Series");

      // Selecting a map sub-tab (or the Maps tab) shows its map in the
      // preview (redrawn from the kept analysis; nothing to do before the
      // first Preview).
      this.selectMapPage = function() {
         let view = MAP_VIEWS[d.maps_TabBox.currentPageIndex];
         if (!view || view.key === d.mapView)
            return;
         d.mapView = view.key;
         d.refreshPreview();
      };
      this.maps_TabBox.currentPageIndex = MAP_VIEWS.indexOf(mapViewByKey(this.mapView));
      // After a computation: the parameters jump to the page of the map the
      // preview shows (Maps, its sub-tab), with its settings and evaluation.
      this.showMapPage = function() {
         let mapsIndex = -1;
         for (let i = 0; i < d.parameters_TabBox.numberOfPages; ++i)
            if (d.parameters_TabBox.pageControlByIndex(i) === d.maps_Control)
               mapsIndex = i;
         if (mapsIndex >= 0)
            d.parameters_TabBox.currentPageIndex = mapsIndex;
         d.syncLeftPane();
         d.maps_TabBox.currentPageIndex = MAP_VIEWS.indexOf(mapViewByKey(d.mapView));
      };
      this.maps_TabBox.onPageSelected = function() { d.selectMapPage(); };
      // The left side follows the tab: the assessments on the Assessment tab,
      // the map (or the tips) on all others. Also called after the tab is
      // changed by the script, in case that does not raise the event.
      this.syncLeftPane = function() {
         let page = d.parameters_TabBox.pageControlByIndex(d.parameters_TabBox.currentPageIndex);
         d.assessmentShown = page === d.assessment_Control;
         let series = page === d.series_Control;
         d.seriesHintsShown = series && d.seriesResult === null && !d.seriesRunning;
         d.seriesShown = series && !d.seriesHintsShown;
         if (d.seriesHintsShown)
            d.updateSeriesHints();
         d.updateLeftPane();
      };
      this.parameters_TabBox.onPageSelected = function(index) {
         if (d.parameters_TabBox.pageControlByIndex(index) === d.maps_Control)
            d.selectMapPage();
         d.syncLeftPane();
      };

      // The signature of the script, as in AperturePhotometry.
      this.information_Label = new Label(this);
      this.information_Label.cssId = "SCPInfoLabel";
      this.information_Label.minWidth = 45 * this.font.width('M');
      this.information_Label.wordWrapping = true;
      this.information_Label.useRichText = true;
      this.information_Label.text = "<p><b>" + TITLE + " version " + VERSION + "</b><br/>" +
         "Measures the shape of every star across the field - elongation, FWHM and coma " +
         "asymmetry - and maps sensor tilt, corrector spacing, collimation and tracking errors, " +
         "with an assessment and suggested corrections.</p>";

      // Preview and parameters side by side, with a splitter between them
      // whose position is remembered.
      this.content_Sizer = new HorizontalSizer;
      this.content_Sizer.add(this.previewPane, 100);
      this.panel_Splitter = new HorizontalSplitter(this, this.content_Sizer, this.previewPane, this.parameters_TabBox);
      this.panel_Splitter.settingsKey = SETTINGS_MODULE + "/panelFraction";
      this.content_Sizer.add(this.panel_Splitter);
      this.content_Sizer.add(this.parameters_TabBox);

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add(this.information_Label);
      this.sizer.add(this.content_Sizer, 100);
      this.sizer.addSpacing(2);
      this.sizer.add(buttonRow);

      this.userResizable = true;

      // The minimum size of the dialog is that of its contents with the
      // preview at 400x300 logical pixels (as in AperturePhotometry); it
      // opens larger, so the preview gets most of the room.
      this.preview.setScaledFixedSize(400, 300);
      this.ensureLayoutUpdated();
      this.adjustToContents();
      this.setMinSize();
      this.panel_Splitter.minFirstSize = this.previewPane.width;
      this.panel_Splitter.minSecondSize = this.parameters_TabBox.width;
      this.preview.setVariableSize();
      this.resize(this.logicalPixelsToPhysical(1150), this.logicalPixelsToPhysical(720));
      this.panel_Splitter.restoreState();
      this.onResize = function() { d.panel_Splitter.checkMinimumSize(d); };

      this.updateControls();
      this.updatePreview();

      // The view has its final size only once the dialog is shown.
      this.onShow = function() {
         d.fitPreview();
         d.targetTimer.start();
      };
      this.onHide = function() {
         d.targetTimer.stop();
      };
   }
}

// -----------------------------------------------------------------------
function main() {
   // An API key stored by an earlier version (0.10 and before) is removed.
   try {
      Settings.read(OLD_AI_KEY_SETTING, DataType.UTF16String);
      if (Settings.lastReadOK)
         Settings.remove(OLD_AI_KEY_SETTING);
   } catch (x) {
   }
   Console.show();
   Console.writeln("=== " + TITLE + " v" + VERSION + " ===");
   Console.writeln("Script file: " + #__FILE__); // which copy PixInsight loaded (installed vs. repository)

   // Adopt parameters previously saved via "New Instance" or recorded on
   // a process icon (if any).
   parameters.LoadParameters();

   if (Parameters.isViewTarget) {
      // The script instance was applied directly to a target image via
      // drag&drop ("blue triangle") - run without a dialog.
      processView(Parameters.targetView, parameters, false);
      return;
   }

   // The target image can be freely chosen in the dialog via the
   // ViewList. Without an open image, the dialog opens without a target:
   // the Series tab works on files, Preview and Apply ask for a target.
   let windows = ImageWindow.windows;
   let activeWindow = ImageWindow.activeWindow;
   let initialView = (activeWindow !== null && !activeWindow.isNull) ? activeWindow.currentView :
      (windows.length > 0) ? windows[0].mainView : null;

   let dialog = new StarAberrationDialog(initialView);
   dialog.execute();
   // Frees the bitmaps of the image view (see pjsr/controls/ImageView.js).
   dialog.preview.reset();
}

main();
