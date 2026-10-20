"""`python -m dailyverse validate`: check every account's plans before anything is posted.

Every account is checked strictly, live or not, so finished content waiting to launch can't
rot unnoticed. Only accounts marked `"draft": true` (what `new-tradition` creates, before
its texts and plans exist) are reported as warnings.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .caption import MAX_CAPTION, MAX_HASHTAGS, build_caption
from .library import (list_accounts, load_plan, load_state, load_text, load_tradition, lookup, parse_ref, post_text,
                      seasons, upcoming, verse_keys)

MAX_SCREEN_WORDS = 60   # beyond this the overlay hits its smallest font size and text can overflow
MAX_WORDS_WITH_ORIGINAL = 45        # translation + original share the screen, so each gets less
AUDIO_STYLES = ("ambient", "nature", "silent")   # mirrors render.AUDIO_STYLES (render needs numpy)


def _words(s: str) -> list[str]:
    # Quote marks hug words ("‘Love", "strength.’"), so strip them from token edges but keep
    # in-word apostrophes ("God’s"), which are part of the text.
    tokens = re.sub(r"[^\w\s'-]", " ", s.replace("’", "'").replace("‘", "'")).lower().split()
    # a lone dash ("ease -") is punctuation, not a word
    return [t.strip("'-") for t in tokens if t.strip("'-")]


def _is_excerpt(shown: str, source: str) -> bool:
    """True if `shown` is a contiguous run of `source`'s words (quote marks and punctuation aside)."""
    s, src = _words(shown), _words(source)
    return any(src[i:i + len(s)] == s for i in range(len(src) - len(s) + 1))


def check_entry(entry: dict, text: dict, original: dict | None = None, original_on_screen: bool = False) -> list[str]:
    """`original` is the original-language text index for traditions that show it."""
    try:
        raw = lookup(entry["ref"], text)
    except Exception:
        return ["reference not found in the text"]
    problems = []
    if not entry.get("caption"):
        problems.append("missing caption")
    if entry.get("display") and not _is_excerpt(entry["display"], raw):
        problems.append("display changes the text's words (only an excerpt and quote marks may differ)")
    limit = MAX_WORDS_WITH_ORIGINAL if original_on_screen else MAX_SCREEN_WORDS
    words = len(post_text(entry, text).split())
    if words > limit:
        problems.append(f"{words} words on screen (max {limit})")
    if original is not None:
        try:
            raw_original = lookup(entry["ref"], original)
        except Exception:
            return problems + ["reference not found in the original-language text"]
        shown_original = entry.get("display_original") or raw_original
        if entry.get("display_original") and not _is_excerpt(entry["display_original"], raw_original):
            problems.append("display_original changes the original's words (only an excerpt may differ)")
        trimmed = entry.get("display") and _words(entry["display"]) != _words(raw)
        if trimmed and not entry.get("display_original"):
            problems.append("display shows an excerpt, so display_original must show the same excerpt")
        if original_on_screen and len(shown_original.split()) > MAX_WORDS_WITH_ORIGINAL:
            problems.append(f"{len(shown_original.split())} original-language words on screen "
                            f"(max {MAX_WORDS_WITH_ORIGINAL}); use display/display_original excerpts")
    return problems


def check_style(tradition: dict) -> list[str]:
    from .library import ROOT
    style, problems = tradition.get("style", {}), []
    if style.get("audio", "ambient") not in AUDIO_STYLES:
        problems.append(f"style.audio must be one of {AUDIO_STYLES}")
    for palette in style.get("palettes", []):
        if len(palette) != 3 or len(palette[2]) < 1:
            problems.append("each style.palettes entry is [top color, bottom color, [glow colors]]")
    original = style.get("original")
    if original:
        for key in ("text", "caption_text"):
            if key in original and original[key] not in tradition.get("texts", {}):
                problems.append(f"style.original.{key} {original[key]!r} isn't one of the tradition's texts")
        font = ROOT / "assets" / "fonts" / original.get("font", "Lora-Variable.ttf")
        if not font.exists():
            problems.append(f"font {font.name} is missing from assets/fonts")
    return problems


def check_repeats(aid: str, slots: list, history: list[dict]) -> list[str]:
    """The same verse twice on one account: scheduled twice, or scheduled again after it went out
    (state's history holds the last 90 posts). Overlapping ranges count: 'Mark 12:30-31' repeats 'Mark 12:31'."""
    first: dict[str, str] = {}      # verse key -> where it first appears
    for post in history:
        for key in verse_keys(post["ref"]):
            first.setdefault(key, f"posted {post['date']} ({post['plan']} day {post['day']})")
    problems = []
    for slot in slots:
        keys = verse_keys(slot.entry["ref"])
        clash = next((first[k] for k in keys if k in first), None)
        if clash:
            problems.append(f"{aid} {slot.plan['id']} day {slot.day} ({slot.entry['ref']}): repeats a verse already {clash}")
        for key in keys:
            first.setdefault(key, f"scheduled {slot.when:%Y-%m-%d} ({slot.plan['id']} day {slot.day})")
    return problems


def check_account(acct: dict) -> tuple[list[str], list[str]]:
    """(problems, informational notes) for one account."""
    aid = acct["id"]
    try:
        tradition = load_tradition(acct["tradition"])
    except FileNotFoundError:
        return [f"{aid}: tradition {acct['tradition']!r} not found"], []
    problems: list[str] = []
    if not tradition.get("texts"):
        problems.append(f"{aid}: tradition {tradition['id']} has no texts configured")
    problems += [f"{aid}: {p}" for p in check_style(tradition)]
    if problems:
        return problems, []
    original_cfg = tradition.get("style", {}).get("original")
    try:
        original = load_text(acct["tradition"], original_cfg["text"]) if original_cfg else None
    except FileNotFoundError as e:
        return [f"{aid}: original-language text file not found ({e.filename})"], []
    on_screen = bool(original_cfg and original_cfg.get("on_screen", True))

    pinned_ids = [s["plan"] for s in acct.get("seasonal", [])]
    for pid in pinned_ids:
        if pid in acct["plans"]:
            problems.append(f"{aid}: {pid} is both date-pinned (seasonal) and in the regular queue")
    for pid in acct["plans"] + pinned_ids:
        try:
            plan = load_plan(acct["tradition"], pid)
            text = load_text(acct["tradition"], plan["text"])
        except FileNotFoundError as e:
            problems.append(f"{aid}/{pid}: file not found ({e.filename})")
            continue
        except KeyError:
            problems.append(f"{aid}/{pid}: its text isn't listed in tradition.json")
            continue
        for i, entry in enumerate(plan["posts"], 1):
            problems += [f"{aid}/{pid} day {i} ({entry.get('ref')}): {p}"
                         for p in check_entry(entry, text, original, on_screen)]
    if problems:
        return problems, []          # the schedule checks below need valid plans

    try:
        pinned = seasons(acct)
    except ValueError as e:
        return [f"{aid}: bad seasonal start date ({e})"], []
    for a, b in zip(pinned, pinned[1:]):
        if b.start <= a.end:
            problems.append(f"{aid}: seasonal plans {a.plan['id']} and {b.plan['id']} overlap")

    work_of_tag = {tag: work for work, tag in tradition.get("work_hashtags", {}).items()}
    slots = upcoming(acct)
    for slot in slots:
        caption = build_caption(slot)
        where = f"{aid} {slot.plan['id']} day {slot.day}"
        if len(caption) > MAX_CAPTION:
            problems.append(f"{where}: caption is {len(caption)} chars (max {MAX_CAPTION})")
        tags = [w for w in caption.split() if w.startswith("#")]
        if len(tags) > MAX_HASHTAGS:
            problems.append(f"{where}: {len(tags)} hashtags (max {MAX_HASHTAGS})")
        # A tag naming a book or surah goes only on verses from it (a plan tag can't name one surah
        # when the plan reads through several).
        work = parse_ref(slot.entry["ref"])[0]
        for tag in tags:
            if work_of_tag.get(tag, work) != work:
                problems.append(f"{where}: {tag} is the tag for {work_of_tag[tag]}, but {slot.entry['ref']} is from {work}")
    problems += check_repeats(aid, slots, load_state(aid).get("history", []))
    for a, b in zip(slots, slots[1:]):
        if (b.when.date() - a.when.date()).days > 1:
            first, last = a.when.date() + timedelta(days=1), b.when.date() - timedelta(days=1)
            problems.append(f"{aid}: no post scheduled {first} to {last} (the regular plans run out "
                            f"before {b.plan['id']} starts); add a plan to the queue")
    if not slots:
        problems.append(f"{aid}: nothing left to post; add a plan to accounts/{aid}.json")
        return problems, []
    notes = [f"{aid}: {len(slots)} posts scheduled, {slots[0].when:%a %b %-d, %Y} through {slots[-1].when:%a %b %-d, %Y}"]
    return problems, notes


def run(log=print) -> int:
    failures = 0
    for acct in list_accounts(enabled_only=False):
        problems, notes = check_account(acct)
        level = "warning" if acct.get("draft") else "error"
        for p in problems:
            log(f"::{level}::{p}")
        if level == "error":
            failures += len(problems)
        for n in notes:
            log(n)
    log("OK" if not failures else f"{failures} problem(s)")
    return 1 if failures else 0
