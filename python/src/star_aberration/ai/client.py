"""The request to Claude (Anthropic SDK).

Structured outputs force the answer into the review schema. The request is
streamed (no HTTP timeout on long answers); thinking is always on with
claude-opus-5-5, effort medium keeps it in proportion to one data set. The
server-side fallback ("fallbacks": "default") answers with another model
should the request be declined; the answer's model says which one.

An answer cut off at the token limit can be continued: the cut-off text
goes back as Claude's own turn with a request to go on (without the schema:
it is only the rest of the JSON); the parts are joined and parsed together.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from . import AI_MODEL
from .prompt import CONTINUE_PROMPT, SYSTEM_PROMPT
from .schema import REVIEW_SCHEMA

MAX_TOKENS = 32000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Review:
    text: str
    raw_text: str
    data: dict | None
    model: str
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    truncated: bool = False
    parts: int = 1


class ReviewError(Exception):
    """A readable message for the user."""


def request_review(api_key: str, message: dict, previous: Review | None = None,
                   on_progress: Callable[[int], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> Review:
    """Sends the message (or continues previous) and returns the review.
    on_progress(characters received) while streaming. Raises ReviewError."""
    client = anthropic.Anthropic(api_key=api_key, max_retries=2)
    messages = [message]
    output_config: dict = {"effort": "medium"}
    if previous is not None:
        messages += [{"role": "assistant", "content": [{"type": "text", "text": previous.raw_text}]},
                     {"role": "user", "content": [{"type": "text", "text": CONTINUE_PROMPT}]}]
    else:
        output_config["format"] = {"type": "json_schema", "schema": REVIEW_SCHEMA}
    try:
        with client.beta.messages.stream(
            model=AI_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            received = 0
            for text in stream.text_stream:
                received += len(text)
                if on_progress:
                    on_progress(received)
                if cancelled and cancelled():
                    raise ReviewError("Cancelled.")
            final = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ReviewError("The API key was rejected (HTTP 401) - check it on the Assessment tab.") from e
    except anthropic.PermissionDeniedError as e:
        raise ReviewError(f"The API key lacks the permission for this request: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise ReviewError("Rate limit reached (HTTP 429) - try again in a minute.") from e
    except anthropic.BadRequestError as e:
        raise ReviewError(f"Claude API error (HTTP 400): {e.message}") from e
    except anthropic.APIStatusError as e:
        raise ReviewError(f"Claude API error (HTTP {e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ReviewError(f"No connection to the Claude API: {e}") from e

    if final.stop_reason == "refusal":
        raise ReviewError("Claude declined to answer this request.")
    raw_text = "\n".join(b.text for b in final.content if b.type == "text")
    usage = {"input_tokens": final.usage.input_tokens or 0, "output_tokens": final.usage.output_tokens or 0}
    if previous is not None:
        raw_text = re.sub(r"^\s*```(?:json)?\n?", "", raw_text)
        raw_text = re.sub(r"\n?```\s*$", "", raw_text)
        prev = previous.raw_text
        for k in range(min(200, len(prev), len(raw_text)), 19, -1):
            if prev[-k:] == raw_text[:k]:
                raw_text = raw_text[k:]
                break
        raw_text = prev + raw_text
        usage = {"input_tokens": previous.usage["input_tokens"] + usage["input_tokens"],
                 "output_tokens": previous.usage["output_tokens"] + usage["output_tokens"]}
    truncated = final.stop_reason == "max_tokens"
    text = raw_text.strip()
    data = None
    if not truncated:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    else:
        text += "\n\n[The answer was cut off at the token limit - click Continue to get the rest.]"
    return Review(text=text, raw_text=raw_text, data=data, model=final.model, usage=usage, truncated=truncated,
                  parts=(previous.parts + 1) if previous else 1)
