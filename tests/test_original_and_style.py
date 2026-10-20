"""Traditions that show the original-language text, and per-tradition look and sound."""
import json
import wave
from datetime import date

import pytest

from dailyverse import validate
from dailyverse.caption import build_caption, original_text
from dailyverse.library import ROOT as REAL_ROOT, load_account, load_text, upcoming
from dailyverse.render import AUDIO_STYLES, card_for, make_audio

from .conftest import write

D0 = date(2026, 11, 2)
ORIGINAL = {"text": "orig", "font": "Lora-Variable.ttf", "direction": "ltr", "on_screen": True, "in_caption": True}


@pytest.fixture
def with_original(world):
    """Give the test tradition an original-language text alongside its translation."""
    t = world.root / "traditions" / "trad"
    trad = json.loads((t / "tradition.json").read_text())
    trad["texts"]["orig"] = {"title": "Original", "short": "OR", "file": "texts/orig.json", "license": "PD",
                             "source": "here", "attribution": "x"}
    trad["style"] = {"original": dict(ORIGINAL), "audio": "nature",
                     "palettes": [["#000000", "#111111", ["#222222"]]]}
    write(t / "tradition.json", trad)
    write(t / "texts" / "orig.json", {"id": "orig", "title": "Original", "works": [],
                                      "verses": {f"Book 1:{v}": f"Alpha beta {v}" for v in range(1, 11)}})
    return world


def first_slot():
    return upcoming(load_account("acct"), count=1, from_date=D0)[0]


def test_original_text_leads_the_caption(with_original):
    slot = first_slot()
    assert original_text(slot) == "Alpha beta 1"
    assert build_caption(slot).startswith("Alpha beta 1\n\nBook verse 1.\nBook 1:1 (TT)")


def test_card_carries_the_traditions_style(with_original):
    card = card_for(first_slot())
    assert card.original == "Alpha beta 1"
    assert card.audio == "nature"
    assert card.palettes == [["#000000", "#111111", ["#222222"]]]


def test_display_original_overrides_the_original(with_original):
    slot = first_slot()
    slot.entry["display_original"] = "beta 1"
    assert original_text(slot) == "beta 1"


def test_excerpts_must_match_in_both_languages(with_original):
    text, orig = load_text("trad", "tx"), load_text("trad", "orig")
    ok = {"ref": "Book 1:1", "caption": "c"}
    assert validate.check_entry(ok, text, orig, True) == []
    excerpt_only_english = {"ref": "Book 1:1", "display": "verse 1.", "caption": "c"}
    assert any("display_original" in p for p in validate.check_entry(excerpt_only_english, text, orig, True))
    both = {"ref": "Book 1:1", "display": "verse 1.", "display_original": "beta 1", "caption": "c"}
    assert validate.check_entry(both, text, orig, True) == []
    changed = {"ref": "Book 1:1", "display": "verse 1.", "display_original": "gamma 1", "caption": "c"}
    assert any("changes the original" in p for p in validate.check_entry(changed, text, orig, True))


def test_style_problems_are_reported(with_original):
    t = with_original.root / "traditions" / "trad" / "tradition.json"
    trad = json.loads(t.read_text())
    trad["style"]["audio"] = "orchestra"
    trad["style"]["original"]["font"] = "Missing.ttf"
    write(t, trad)
    problems, _ = validate.check_account(load_account("acct"))
    assert any("style.audio" in p for p in problems)
    assert any("Missing.ttf" in p for p in problems)


@pytest.mark.parametrize("style", AUDIO_STYLES)
def test_every_audio_style_writes_a_quiet_stereo_track(tmp_path, style):
    import numpy as np
    path = tmp_path / f"{style}.wav"
    make_audio(7, 3.0, path, style)
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 44100)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768
    rms_db = 20 * np.log10(np.sqrt(np.mean(pcm ** 2)) + 1e-12)
    if style == "silent":
        assert np.all(pcm == 0)
    else:
        assert -32 < rms_db < -14          # audible but well under full scale
        assert np.max(np.abs(pcm)) <= 0.9


def test_audio_is_deterministic(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    make_audio(42, 2.0, a, "nature")
    make_audio(42, 2.0, b, "nature")
    assert a.read_bytes() == b.read_bytes()


def test_validate_knows_every_render_option():
    from dailyverse import render
    assert validate.AUDIO_STYLES == render.AUDIO_STYLES
    assert validate.SCENES == render.SCENES


def test_an_unknown_scene_is_reported(with_original):
    t = with_original.root / "traditions" / "trad" / "tradition.json"
    trad = json.loads(t.read_text())
    trad["style"]["scene"] = "fireworks"
    write(t, trad)
    problems, _ = validate.check_account(load_account("acct"))
    assert any("style.scene" in p for p in problems)


@pytest.mark.parametrize("scene", ["glow", "sunrise", "night", "dusk"])
def test_scenes_draw_deterministic_frames(with_original, scene):
    import numpy as np
    from dataclasses import replace
    from dailyverse.render import H, W, BG_SCALE, scene_for
    card = replace(card_for(first_slot()), scene=scene, palettes=None)
    a, b = scene_for(card).frame(3.2), scene_for(card).frame(3.2)
    assert a.shape == (H // BG_SCALE, W // BG_SCALE, 3) and a.dtype == np.uint8
    assert np.array_equal(a, b)                                   # previews match what's posted
    assert not np.array_equal(a, scene_for(card).frame(9.0))      # and it moves


@pytest.mark.parametrize("scene", ["glow", "sunrise", "night", "dusk"])
def test_the_verse_is_revealed_in_order_and_complete_at_the_cover(with_original, scene):
    from dataclasses import replace
    from dailyverse.render import BOX_BOTTOM, FADE_IN, MAX_SECONDS, MIN_SECONDS, compose
    card = replace(card_for(first_slot()), scene=scene)
    pieces, tl = compose(card)
    entries = [p.t_in for p in pieces]
    assert entries == sorted(entries)                             # header, original, English, emblem, reference
    assert len(pieces) > len(card.text.split())                   # the verse arrives a word at a time
    assert all(p.t_in + FADE_IN <= tl.cover for p in pieces)       # the grid cover shows everything
    assert MIN_SECONDS <= tl.duration <= MAX_SECONDS and tl.cover < tl.duration
    assert all(p.y + p.image.height <= BOX_BOTTOM + 60 for p in pieces)   # clear of Instagram's caption area


def test_the_header_names_the_plan_and_day(with_original):
    card = card_for(first_slot())
    assert card.header == "Plan A · Day 1 of 3"


@pytest.mark.parametrize("style", ["ambient", "drone", "nature"])
def test_music_is_calm_whatever_the_volume(tmp_path, style):
    """Loudness is set the way meters measure it (LUFS), well under music mastered for Instagram (~-14)."""
    import numpy as np
    from dailyverse.render import LOUDNESS, loudness
    path = tmp_path / f"{style}.wav"
    make_audio(11, 8.0, path, style)
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2) / 32768
    assert abs(loudness(pcm) - LOUDNESS[style]) < 1.0
    assert LOUDNESS[style] <= -24 and np.max(np.abs(pcm)) <= 0.71


def test_arabic_words_appear_right_to_left():
    """The first word of an Arabic line sits at its right end, and each next word to its left."""
    import dailyverse.library as library
    from dailyverse.render import timeline
    if not (REAL_ROOT / "accounts" / "islam.json").exists():
        return
    card = card_for(upcoming(library.load_account("islam"), count=1)[0])
    first_line_y = None
    xs = []
    for line in timeline(card).lines:
        if line.shaping.get("direction") == "rtl":
            first_line_y = first_line_y or line.y
            if line.y == first_line_y:
                xs.append(line.x)
    assert len(xs) > 1 and xs == sorted(xs, reverse=True)


def test_punctuation_brings_a_natural_pause(with_original):
    from dataclasses import replace
    from dailyverse.render import PAUSE_COMMA, PAUSE_STOP, READ_PACE, timeline
    card = replace(card_for(first_slot()), original=None,
                   text="In the beginning, the Word was there—and it was good. So it was.")
    words = [ln for ln in timeline(card).lines if ln.shaping == {} and ln.x is not None]
    gap = {w.text: nxt.t_in - w.t_in for w, nxt in zip(words, words[1:])}
    assert gap["In"] == pytest.approx(READ_PACE)                                 # an ordinary word
    assert gap["beginning,"] == pytest.approx(READ_PACE + PAUSE_COMMA)  # a beat after a comma
    assert gap["there—"] == pytest.approx(READ_PACE + PAUSE_COMMA)      # words joined by a dash split at it
    assert gap["good."] == pytest.approx(READ_PACE + PAUSE_STOP)        # a longer one after a sentence
    assert [w.text for w in words][6:8] == ["there—", "and"]
