"""Star Aberration Diagnostics - diagnostics of the optical system from the
shapes of the stars: sensor tilt, corrector spacing, field curvature, coma,
collimation and tracking errors.

Python port of the PixInsight script StarAberrationDiagnostics 0.10.

Packages:
    model       the measurement and its evaluation (no GUI dependency)
    render      the maps, drawn with QPainter into QImages
    ai          the AI review (Claude, Anthropic API)
    view        the Qt widgets
    controller  connects view and model, runs the work in threads
"""

VERSION = "0.10"
TITLE = "Star Aberration Diagnostics"
