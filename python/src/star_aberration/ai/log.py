"""Saves an AI request and its answer (or error) as a UTF-8 text file
StarAberrationAI_<date>_<time>.txt: a header with the date, the model and
the analyzed files, the request as sent (images only as placeholders) and
the answer, readable and as JSON. The API key is never written."""

from __future__ import annotations

import datetime as _dt
import json
import os

from .. import TITLE, VERSION
from . import AI_MODEL
from .client import Review
from .format import format_review_text
from .prompt import SYSTEM_PROMPT


def save_ai_log(directory: str, when: _dt.datetime, files: list[tuple[str, str]], settings: list[str],
                message: dict, review: Review | None, error: str | None) -> str:
    local = when.astimezone()
    stamp = local.strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(directory, "StarAberrationAI_" + local.strftime("%Y-%m-%d_%H-%M-%S") + ".txt")
    rule = "=" * 80
    lines = [f"{TITLE} {VERSION} - AI review",
             f"Date:     {stamp} (local), {when.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC)",
             f"Model:    {AI_MODEL}" + (f" (answered by {review.model})" if review and review.model
                                         and review.model != AI_MODEL else "")]
    lines += settings
    lines.append("Analyzed files:")
    if not files:
        lines.append("   (none)")
    lines += [f"   {label}{'  ' if label and p else ''}{p}" for label, p in files]
    lines += ["", rule, "REQUEST", rule, "--- System prompt ---", SYSTEM_PROMPT, "", "--- Message ---"]
    for block in message["content"]:
        if block["type"] == "image":
            lines.append(f"[Image: a map as PNG, {len(block['source']['data'])} characters of base64 - "
                         "not included here]")
        elif block["type"] == "text":
            lines.append(block["text"])
    lines += ["", rule, "ANSWER", rule]
    if error:
        lines.append("The request failed: " + error)
    elif review is not None:
        lines.append(format_review_text(review))
        if review.data:
            lines += ["", "--- As received (JSON) ---", json.dumps(review.data, indent=2, ensure_ascii=False)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
