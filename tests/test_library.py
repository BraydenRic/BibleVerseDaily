from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dailyverse.library import (advance, balance_double_quotes, display_ref, in_window, is_due, load_account,
                                load_state, lookup, load_text, parse_ref, post_text, upcoming)

from .conftest import TZ

D0 = date(2026, 11, 2)   # an arbitrary "today" for schedule tests


def days(slots):
    return [(s.plan["id"], s.day, s.when.date()) for s in slots]


# ---------------------------------------------------------------- references

@pytest.mark.parametrize("ref, expected", [
    ("Mark 1:1", ("Mark", 1, 1, 1)),
    ("Mark 12:30-31", ("Mark", 12, 30, 31)),
    ("I Samuel 3:10", ("I Samuel", 3, 10, 10)),
    ("Song of Solomon 2:4", ("Song of Solomon", 2, 4, 4)),
    ("Tao Te Ching 8", ("Tao Te Ching", 8, None, None)),
    ("Dhammapada 1-2", ("Dhammapada", 1, None, 2)),
])
def test_parse_ref(ref, expected):
    assert parse_ref(ref) == expected


def test_lookup_joins_a_range(world):
    text = load_text("trad", "tx")
    assert lookup("Book 1:2-3", text) == "Book verse 2. Book verse 3."


def test_lookup_chapter_only_units_and_ranges():
    text = {"verses": {"Dhammapada 1": "One.", "Dhammapada 2": "Two.", "Tao Te Ching 8": "Water."}}
    assert lookup("Tao Te Ching 8", text) == "Water."
    assert lookup("Dhammapada 1-2", text) == "One. Two."


@pytest.mark.parametrize("text, expected", [
    ("He said, “Come and see.", "He said, “Come and see.”"),            # opened here, closes in a later verse
    ("It is good.” And they went.", "“It is good.” And they went."),     # opened in an earlier verse
    ("“One,” he said, “two.”", "“One,” he said, “two.”"),               # balanced: untouched
    ("“Outer “inner” still outer", "“Outer “inner” still outer”"),
    ("No quotes at all.", "No quotes at all."),
])
def test_balance_double_quotes_only_adds_marks(text, expected):
    fixed = balance_double_quotes(text)
    assert fixed == expected
    assert fixed.replace("“", "").replace("”", "") == text.replace("“", "").replace("”", "")


def test_post_text_prefers_display_and_repairs_quotes_otherwise(world):
    text = load_text("trad", "tx")
    assert post_text({"ref": "Book 2:1"}, text) == "He said, “Come and see.”"
    assert post_text({"ref": "Book 2:1", "display": "“Come and see.”"}, text) == "“Come and see.”"


def test_display_ref_uses_the_familiar_citation():
    trad = {"ref_display": {"Psalms": "Psalm", "Revelation of John": "Revelation"}}
    assert display_ref("Psalms 23:1-2", trad) == "Psalm 23:1-2"
    assert display_ref("Revelation of John 21:4", trad) == "Revelation 21:4"
    assert display_ref("Mark 1:1", trad) == "Mark 1:1"
    assert display_ref("Mark 1:1", {}) == "Mark 1:1"


# ---------------------------------------------------------------- schedule

def test_upcoming_walks_the_queue_one_post_per_day(world):
    slots = upcoming(load_account("acct"), from_date=D0)
    assert days(slots) == [("01-a", 1, D0), ("01-a", 2, D0 + timedelta(1)), ("01-a", 3, D0 + timedelta(2)),
                           ("02-b", 1, D0 + timedelta(3)), ("02-b", 2, D0 + timedelta(4))]
    assert slots[0].when == datetime(2026, 11, 2, 12, 0, tzinfo=ZoneInfo(TZ))
    assert slots[0].total == 3 and slots[3].total == 2


def test_upcoming_starts_tomorrow_if_already_posted_today(world):
    world.state(next_day=2, last_posted_date=D0.isoformat())
    slots = upcoming(load_account("acct"), count=1, from_date=D0)
    assert days(slots) == [("01-a", 2, D0 + timedelta(1))]


def test_upcoming_respects_count_and_saved_position(world):
    world.state(plan_index=1, next_day=2)
    assert days(upcoming(load_account("acct"), count=5, from_date=D0)) == [("02-b", 2, D0)]


def test_upcoming_skips_a_plan_that_was_shortened_after_it_started(world):
    world.state(plan_index=0, next_day=9)      # past the end of 01-a's 3 posts
    assert days(upcoming(load_account("acct"), count=1, from_date=D0)) == [("02-b", 1, D0)]


def test_seasonal_plan_interrupts_the_queue_and_it_resumes_after(world):
    world.account(seasonal=[{"plan": "s-1", "start": (D0 + timedelta(1)).isoformat()}])
    slots = upcoming(load_account("acct"), from_date=D0)
    assert days(slots) == [
        ("01-a", 1, D0),
        ("s-1", 1, D0 + timedelta(1)), ("s-1", 2, D0 + timedelta(2)), ("s-1", 3, D0 + timedelta(3)),
        ("01-a", 2, D0 + timedelta(4)), ("01-a", 3, D0 + timedelta(5)),
        ("02-b", 1, D0 + timedelta(6)), ("02-b", 2, D0 + timedelta(7)),
    ]
    assert [s.seasonal for s in slots[:2]] == [False, True]


def test_seasonal_days_are_placed_by_date_even_when_joined_midway(world):
    world.account(seasonal=[{"plan": "s-1", "start": (D0 - timedelta(1)).isoformat()}])
    slots = upcoming(load_account("acct"), count=3, from_date=D0)
    assert days(slots) == [("s-1", 2, D0), ("s-1", 3, D0 + timedelta(1)), ("01-a", 1, D0 + timedelta(2))]


def test_after_the_queue_runs_out_nothing_posts_until_the_next_season(world):
    world.state(plan_index=2, next_day=1)       # regular queue finished
    start = D0 + timedelta(10)
    world.account(seasonal=[{"plan": "s-1", "start": start.isoformat()}])
    assert days(upcoming(load_account("acct"), from_date=D0)) == [("s-1", n, start + timedelta(n - 1)) for n in (1, 2, 3)]


def test_seasonal_plan_in_the_past_is_ignored(world):
    world.account(seasonal=[{"plan": "s-1", "start": (D0 - timedelta(30)).isoformat()}])
    assert len(upcoming(load_account("acct"), from_date=D0)) == 5


def test_advance_moves_through_and_between_plans(world):
    acct = load_account("acct")
    slots = upcoming(acct, from_date=D0)
    st = load_state("acct")
    st = advance(acct, st, slots[0], "m1", "2026-11-02")
    assert (st["plan_index"], st["next_day"], st["last_posted_date"]) == (0, 2, "2026-11-02")
    st = advance(acct, st, slots[2], "m3", "2026-11-04")       # last post of 01-a
    assert (st["plan_index"], st["next_day"]) == (1, 1)
    assert [h["media_id"] for h in st["history"]] == ["m1", "m3"]


def test_advance_on_a_seasonal_day_leaves_the_queue_alone(world):
    world.account(seasonal=[{"plan": "s-1", "start": D0.isoformat()}])
    acct = load_account("acct")
    slot = upcoming(acct, count=1, from_date=D0)[0]
    assert slot.seasonal
    st = advance(acct, load_state("acct"), slot, "m1", D0.isoformat())
    assert (st["plan_index"], st["next_day"], st["last_posted_date"]) == (0, 1, D0.isoformat())


def test_advance_uses_the_posted_plans_real_position(world):
    world.state(plan_index=0, next_day=9)      # stale position pointing past the end of 01-a
    acct = load_account("acct")
    slot = upcoming(acct, count=1, from_date=D0)[0]
    st = advance(acct, load_state("acct"), slot, "m1", D0.isoformat())
    assert (st["plan_index"], st["next_day"]) == (1, 2)   # 02-b day 2 next, not back to 01-a


def test_history_keeps_the_last_90(world):
    acct = load_account("acct")
    slot = upcoming(acct, count=1, from_date=D0)[0]
    st = load_state("acct")
    st["history"] = [{"n": i} for i in range(95)]
    assert len(advance(acct, st, slot, "m", D0.isoformat())["history"]) == 90


# ---------------------------------------------------------------- posting window

ACCT = {"post_time": "12:00", "window_minutes_before": 30, "window_minutes_after": 120}
LA = ZoneInfo(TZ)


@pytest.mark.parametrize("when, inside", [
    (datetime(2027, 1, 15, 11, 29, tzinfo=LA), False),
    (datetime(2027, 1, 15, 11, 30, tzinfo=LA), True),     # standard time
    (datetime(2027, 1, 15, 14, 0, tzinfo=LA), True),
    (datetime(2027, 1, 15, 14, 1, tzinfo=LA), False),
    (datetime(2027, 3, 15, 11, 50, tzinfo=LA), True),     # daylight time, after the March change
    (datetime(2026, 11, 2, 11, 50, tzinfo=LA), True),     # standard time, right after the November change
    (datetime(2027, 3, 14, 12, 50, tzinfo=LA), True),     # the day clocks spring forward
    (datetime(2026, 11, 1, 12, 50, tzinfo=LA), True),     # the day clocks fall back
])
def test_in_window_holds_across_daylight_saving(when, inside):
    assert in_window(ACCT, when) is inside


def test_hourly_cron_hits_the_window_in_both_seasons():
    """The workflow runs at :50 every hour (UTC); every day of the year at least two runs
    must land inside the noon-Pacific window, so a failed post always gets a retry."""
    utc = ZoneInfo("UTC")
    d = date(2026, 10, 20)
    for _ in range(366):
        runs = [datetime(d.year, d.month, d.day, h, 50, tzinfo=utc).astimezone(LA) for h in range(24)]
        assert sum(in_window(ACCT, r) for r in runs if r.date() == d) >= 2, d
        d += timedelta(1)


def test_start_date_holds_back_the_first_post(world):
    world.account(start_date=(D0 + timedelta(3)).isoformat())
    assert days(upcoming(load_account("acct"), count=1, from_date=D0)) == [("01-a", 1, D0 + timedelta(3))]


def test_is_due_waits_for_the_start_date(world):
    world.account(start_date="2026-11-03")
    assert not is_due(load_account("acct"), datetime(2026, 11, 2, 12, 0, tzinfo=LA))
    assert is_due(load_account("acct"), datetime(2026, 11, 3, 12, 0, tzinfo=LA))


def test_is_due_only_once_per_day(world):
    acct = load_account("acct")
    noon = datetime(2026, 11, 2, 12, 0, tzinfo=LA)
    assert is_due(acct, noon)
    world.state(last_posted_date="2026-11-02")
    assert not is_due(acct, noon)
    assert not is_due(acct, datetime(2026, 11, 2, 9, 0, tzinfo=LA))
