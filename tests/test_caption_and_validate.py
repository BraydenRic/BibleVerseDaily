from datetime import date

from dailyverse import validate
from dailyverse.caption import MAX_CAPTION, MAX_HASHTAGS, build_caption, overlay_text
from dailyverse.library import ROOT as REAL_ROOT, load_account, load_text, upcoming

D0 = date(2026, 11, 2)


def test_caption_layout(world):
    slot = upcoming(load_account("acct"), count=1, from_date=D0)[0]
    assert build_caption(slot) == (
        "Book verse 1.\nBook 1:1 (TT)\n\n"
        "Caption for Book 1:1.\n\n"
        "Day 1 of 3 · Plan A\n\n"
        "Text: Test Text (public domain).\n\n"
        "#one #two #three #four #01a"
    )


def test_caption_and_overlay_use_the_familiar_citation(world):
    world.account(seasonal=[{"plan": "s-1", "start": D0.isoformat()}])
    slot = upcoming(load_account("acct"), count=1, from_date=D0)[0]
    assert "\nPsalm 23:1 (TT)" in build_caption(slot)
    assert overlay_text(slot) == ("Psalm verse 1.", "Psalm 23:1", "Day 1 of 3")


def test_hashtags_are_deduplicated_and_capped(world):
    slot = upcoming(load_account("acct"), count=1, from_date=D0)[0]
    slot.plan["hashtags"] = "#one #five #six #seven"
    tags = [w for w in build_caption(slot).split() if w.startswith("#")]
    assert tags == ["#one", "#two", "#three", "#four", "#five"]
    assert len(tags) == MAX_HASHTAGS


def test_check_entry(world):
    text = load_text("trad", "tx")
    assert validate.check_entry({"ref": "Book 1:1", "caption": "ok"}, text) == []
    assert validate.check_entry({"ref": "Book 9:9", "caption": "ok"}, text) == ["reference not found in the text"]
    assert validate.check_entry({"ref": "Book 1:1"}, text) == ["missing caption"]
    # display may trim to an excerpt and change quote marks, never the words
    assert validate.check_entry({"ref": "Book 2:1", "display": "“Come and see.”", "caption": "ok"}, text) == []
    assert validate.check_entry({"ref": "Book 2:1", "display": "“Come and look.”", "caption": "ok"}, text)
    long = {"ref": "Book 1:1", "display": " ".join(["Book"] * 61), "caption": "ok"}
    assert any("words on screen" in p for p in validate.check_entry(long, text))


def test_validate_flags_overlapping_seasons_and_gaps(world):
    world.account(seasonal=[{"plan": "s-1", "start": "2026-12-01"}, {"plan": "01-a", "start": "2026-12-02"}],
                  plans=["02-b"])
    problems, _ = validate.check_account(load_account("acct"))
    assert any("overlap" in p for p in problems)

    world.account(seasonal=[{"plan": "s-1", "start": "2099-01-01"}])
    problems, _ = validate.check_account(load_account("acct"))
    assert any("no post scheduled" in p for p in problems)


def test_validate_rejects_a_plan_both_pinned_and_queued(world):
    world.account(seasonal=[{"plan": "01-a", "start": "2099-01-01"}])
    problems, _ = validate.check_account(load_account("acct"))
    assert any("both date-pinned" in p for p in problems)


def test_validate_reports_the_schedule(world):
    problems, notes = validate.check_account(load_account("acct"))
    assert problems == []
    assert "5 posts scheduled" in notes[0]


def test_draft_accounts_only_warn(world, capsys):
    world.account(enabled=False, draft=True, plans=["missing-plan"])
    assert validate.run() == 0
    assert "::warning::" in capsys.readouterr().out


def test_disabled_accounts_are_still_checked_strictly(world, capsys):
    world.account(enabled=False, plans=["missing-plan"])
    assert validate.run() == 1
    assert "::error::" in capsys.readouterr().out


def test_the_real_content_is_valid():
    """Every plan every real account will post, live or not: refs exist, captions fit, no gaps."""
    import dailyverse.library as library
    assert library.ROOT == REAL_ROOT
    for acct in library.list_accounts(enabled_only=False):
        problems, notes = validate.check_account(acct)
        assert problems == [], problems
        for slot in upcoming(acct):
            assert len(build_caption(slot)) <= MAX_CAPTION


def test_every_scheduled_post_fits_on_screen():
    """No verse may overflow the card, even at the smallest type size."""
    import dailyverse.library as library
    from dailyverse.render import card_for, layout
    overflowing = []
    for acct in library.list_accounts(enabled_only=False):
        for slot in upcoming(acct):
            if not layout(card_for(slot))["fits"]:
                overflowing.append(f"{acct['id']} {slot.plan['id']} day {slot.day} ({slot.entry['ref']})")
    assert overflowing == []


def test_no_quran_post_shows_the_bismillah_heading():
    """Tanzil's text attaches the Bismillah heading to verse 1 of most surahs (in two spellings);
    posts must show the verse itself, except Al-Fatiha 1:1 where the Bismillah is the verse."""
    import dailyverse.library as library
    from dailyverse.caption import original_text
    if not (REAL_ROOT / "accounts" / "islam.json").exists():
        return
    shown_heading = [s.entry["ref"] for s in upcoming(library.load_account("islam"))
                     if original_text(s).split()[0] in ("بِسْمِ", "بِّسْمِ") and s.entry["ref"] != "Al-Fatiha 1:1"]
    assert shown_heading == []


def test_complex_scripts_render_where_a_tradition_needs_them(tmp_path):
    """Arabic and Devanagari need libraqm (fribidi) for joined, correctly ordered letters.
    Fail loudly in CI rather than post disconnected or reversed text."""
    import dailyverse.library as library
    from dailyverse.render import Card, make_overlay
    for path in sorted((REAL_ROOT / "traditions").glob("*/tradition.json")):
        trad = library.load_tradition(path.parent.name)
        orig = trad.get("style", {}).get("original")
        if not orig:
            continue
        text = library.load_text(trad["id"], orig["text"])
        sample = next(iter(text["verses"].values()))
        card = Card(text="A short line of English.", ref="Test 1:1", label="Day 1 of 1", seed=1, original=sample,
                    original_font=orig["font"], original_direction=orig.get("direction", "ltr"),
                    original_language=orig.get("language"))
        make_overlay(card, tmp_path / f"{trad['id']}.png")      # raises if shaping is unavailable
        assert (tmp_path / f"{trad['id']}.png").stat().st_size > 0
