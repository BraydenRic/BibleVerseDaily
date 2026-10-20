"""Traditions that show the original-language text, and per-tradition look and sound."""
import json
import wave
from datetime import date

import pytest

from dailyverse import validate
from dailyverse.caption import build_caption, original_text
from dailyverse.library import load_account, load_text, upcoming
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
    assert all(p.t_in + FADE_IN <= tl.cover for p in pieces)       # the grid cover shows everything
    assert MIN_SECONDS <= tl.duration <= MAX_SECONDS and tl.cover < tl.fade_out
    assert all(p.y + p.image.height <= BOX_BOTTOM + 60 for p in pieces)   # clear of Instagram's caption area


def test_the_header_names_the_plan_and_day(with_original):
    card = card_for(first_slot())
    assert card.header == "Plan A · Day 1 of 3"
