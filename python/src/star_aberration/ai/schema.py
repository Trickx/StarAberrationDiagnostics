"""The answer's form, enforced by the API (structured outputs): per finding
the measurement, what it looks like, the derivation, the alternatives, what
to do and how to check it - so that the reasoning is always there. labels:
the field headings in the answer language, for the formatting."""

_LABEL_KEYS = ["verdict", "order", "confidence", "observation", "appearance", "reasoning", "alternatives", "action",
               "check", "disagreements", "statusOk", "statusNote", "statusAction", "high", "medium", "low"]
_FINDING_KEYS = ["topic", "status", "confidence", "confidenceText", "observation", "appearance", "reasoning",
                 "alternatives", "action", "check"]

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in _LABEL_KEYS},
            "required": _LABEL_KEYS,
            "additionalProperties": False,
        },
        "verdict": {"type": "string"},
        "order": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "status": {"type": "string", "enum": ["ok", "note", "action"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "confidenceText": {"type": "string"},
                    "observation": {"type": "string"},
                    "appearance": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "alternatives": {"type": "string"},
                    "action": {"type": "string"},
                    "check": {"type": "string"},
                },
                "required": _FINDING_KEYS,
                "additionalProperties": False,
            },
        },
        "disagreements": {"type": "string"},
    },
    "required": ["labels", "verdict", "order", "findings", "disagreements"],
    "additionalProperties": False,
}
