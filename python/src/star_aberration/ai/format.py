"""The review as HTML (the AI review tab) or plain text (the log): the
structured answer with its own headings in the answer language, or the raw
text when there is none; the model and the token usage below."""

from __future__ import annotations

import html

from .client import Review


def _text(v) -> str:
    # A continued answer is not bound to the schema: a text field can arrive
    # as a list, a dict or a number - shown as text all the same.
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n".join(t for t in (_text(x) for x in v) if t)
    if isinstance(v, dict):
        return "\n".join(f"{k}: {_text(x)}" for k, x in v.items())
    return str(v).strip()


def _lines(review: Review) -> list[tuple[str, str]]:
    """(style, text) pairs; style: "b" heading, "i" field label + text, "" plain."""
    out: list[tuple[str, str]] = []
    r = review.data
    if isinstance(r, dict) and isinstance(r.get("findings"), list):
        labels = r.get("labels") if isinstance(r.get("labels"), dict) else {}

        def label(key):
            return _text(labels.get(key)) or key
        status = {"ok": label("statusOk"), "note": label("statusNote"), "action": label("statusAction")}
        out.append(("b:", label("verdict") + "\t" + _text(r.get("verdict"))))
        order = [_text(x) for x in (r["order"] if isinstance(r.get("order"), list) else [r.get("order")])]
        order = [t for t in order if t]
        if order:
            out.append(("", ""))
            out.append(("b", label("order") + ":"))
            out += [("", f"   {i + 1}. {t}") for i, t in enumerate(order)]
        for f in r["findings"]:
            out.append(("", ""))
            if not isinstance(f, dict):
                out.append(("", _text(f)))
                continue
            s, c, ct = _text(f.get("status")), _text(f.get("confidence")), _text(f.get("confidenceText"))
            out.append(("b-", f"[{status.get(s, s)}] {_text(f.get('topic'))}\t{label('confidence')}: "
                              f"{_text(labels.get(c)) or c}" + (f" ({ct})" if ct else "")))
            for key in ("observation", "appearance", "reasoning", "alternatives", "action", "check"):
                if _text(f.get(key)):
                    out.append(("i", label(key) + "\t" + _text(f.get(key))))
        if _text(r.get("disagreements")):
            out.append(("", ""))
            out.append(("b:", label("disagreements") + "\t" + _text(r.get("disagreements"))))
    else:
        out.append(("", review.text))
    out.append(("", ""))
    out.append(("note", f"{review.model} - {review.usage.get('input_tokens', 0)} input / "
                        f"{review.usage.get('output_tokens', 0)} output tokens"))
    return out


def format_review_html(review: Review) -> str:
    e = lambda t: html.escape(t).replace("\n", "<br>")
    parts = []
    for style, text in _lines(review):
        if style == "b:":
            head, _, rest = text.partition("\t")
            parts.append(f"<b>{e(head)}:</b> {e(rest)}")
        elif style == "b-":
            head, _, rest = text.partition("\t")
            parts.append(f"<b>{e(head)}</b> - {e(rest)}")
        elif style == "b":
            parts.append(f"<b>{e(text)}</b>")
        elif style == "i":
            head, _, rest = text.partition("\t")
            parts.append(f"<i>{e(head)}:</i> {e(rest)}")
        elif style == "note":
            parts.append(f"<i>{e(text)}</i>")
        else:
            parts.append(e(text).replace("   ", "&nbsp;&nbsp;&nbsp;"))
    return "<br>".join(parts)


def format_review_text(review: Review) -> str:
    out = []
    for style, text in _lines(review):
        if style in ("b:", "i"):
            head, _, rest = text.partition("\t")
            out.append(f"{head}: {rest}")
        elif style == "b-":
            head, _, rest = text.partition("\t")
            out.append(f"{head} - {rest}")
        else:
            out.append(text)
    return "\n".join(out)
