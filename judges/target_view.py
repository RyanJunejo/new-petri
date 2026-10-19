"""Render target turns for the judges, excluding system prompts and tool results.

Also provides JSON parsing and exact-quotation checks.
"""
from __future__ import annotations

import json
import re
from typing import Any


def render_target_turn(message: dict) -> str:
    """One assistant message as text: reasoning summary, visible text and tool calls. Nothing else."""
    parts = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    else:
        for c in content or []:
            if c.get("type") == "reasoning":
                # The summary is readable; a redacted block's reasoning field is an encrypted blob and is never shown.
                text = c.get("summary") or ("" if c.get("redacted") else c.get("reasoning"))
                if text:
                    parts.append("[reasoning] " + text)
            elif c.get("type") == "text" and c.get("text"):
                parts.append(c["text"])
    for tc in message.get("tool_calls") or []:
        parts.append(f"[tool call] {tc.get('function')}({json.dumps(tc.get('arguments', {}))})")
    return "\n".join(p for p in parts if p.strip())


def target_turns(messages: list[dict]) -> list[tuple[int, str]]:
    """(message index, rendered text) for every assistant message with something in it."""
    return [(i, render_target_turn(m)) for i, m in enumerate(messages)
            if m.get("role") == "assistant" and render_target_turn(m).strip()]


def parse_json_reply(text: str) -> dict[str, Any] | None:
    """The first JSON object in a judge reply, or None."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        value = json.loads(m.group(0)) if m else None
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def quote_found(quote: str, message: str) -> bool:
    """True if the judge's quote appears in the message, ignoring whitespace differences."""
    squash = lambda s: " ".join(s.split())  # noqa: E731
    return bool(quote.strip()) and squash(quote) in squash(message)
