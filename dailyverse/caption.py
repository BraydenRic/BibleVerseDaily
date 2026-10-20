"""Instagram caption for a post, and the text that goes on screen."""
from __future__ import annotations

from .library import Slot, load_text, lookup, post_text

MAX_CAPTION = 2200
MAX_HASHTAGS = 5   # Instagram's in-app limit since Dec 2025; the API docs still say 30, so stay at 5


def original_text(slot: Slot, for_caption: bool = False) -> str | None:
    """The verse in its original language, for traditions that show it (`style.original`).

    `caption_text` can name a different text for the caption: the Gita shows Devanagari on
    screen but a romanized (IAST) line in the caption, which more followers can read.
    """
    cfg = slot.tradition.get("style", {}).get("original")
    if not cfg:
        return None
    if for_caption and cfg.get("caption_text"):
        return lookup(slot.entry["ref"], load_text(slot.tradition["id"], cfg["caption_text"]))
    if slot.entry.get("display_original"):
        return slot.entry["display_original"]
    return lookup(slot.entry["ref"], load_text(slot.tradition["id"], cfg["text"]))


def ref_line(slot: Slot) -> str:
    """The caption line naming the verse, e.g. 'Psalm 23:1 (BSB)'. With day_line, it's also how a
    post that's already on Instagram is recognized (cli.catch_up), so keep each on its own line."""
    return f"{slot.ref_label} ({slot.text_meta['short']})"


def day_line(slot: Slot) -> str:
    """The caption line placing the post in its plan, e.g. 'Day 3 of 40 · Psalms'."""
    return f"Day {slot.day} of {slot.total} · {slot.plan['title']}"


def build_caption(slot: Slot) -> str:
    text = post_text(slot.entry, load_text(slot.tradition["id"], slot.plan["text"]))
    cfg = slot.tradition.get("style", {}).get("original") or {}
    original = original_text(slot, for_caption=True) if cfg.get("in_caption", True) else None
    tags = " ".join(filter(None, [slot.tradition.get("hashtags", ""), slot.plan.get("hashtags", "")])).split()
    tags = list(dict.fromkeys(tags))[:MAX_HASHTAGS]
    parts = [
        original,
        f"{text}\n{ref_line(slot)}",
        slot.entry["caption"],
        day_line(slot),
        slot.text_meta["attribution"],
        " ".join(tags),
    ]
    return "\n\n".join(p for p in parts if p)


def overlay_text(slot: Slot) -> tuple[str, str, str]:
    """(body, reference, top label) for the video overlay."""
    text = post_text(slot.entry, load_text(slot.tradition["id"], slot.plan["text"]))
    return text, slot.ref_label, f"Day {slot.day} of {slot.total}"
