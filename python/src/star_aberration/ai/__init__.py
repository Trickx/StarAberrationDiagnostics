"""The AI review: sends the measured values, the rule-based assessment and
(optionally) the maps of the single frame as images to Claude (Anthropic
API) and returns its review. The rules stay the reference; the AI weighs
the findings against each other, points out contradictions and explains
the suggested order.

    prompt    the system prompt, the data as JSON, the user message
    schema    the form of the answer (structured outputs)
    client    the request (Anthropic SDK, streaming)
    format    the answer as HTML or text
    log       the request and the answer as a text file
"""

AI_MODEL = "claude-opus-5-5"
AI_DETAIL_LEVELS = ["Short (for experienced users)", "Step by step (for beginners)"]
AI_MAP_MAX_SIDE = 1092  # longer side of each map sent as an image, px (about 1100 tokens each)
