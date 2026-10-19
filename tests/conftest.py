"""A tiny, self-contained tradition/account/state tree in a temp folder, so tests never touch
the real content and can set up any schedule they need."""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import dailyverse.cli as cli
import dailyverse.library as library

TZ = "America/Los_Angeles"


def today_local() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def plan(plan_id: str, refs: list[str], title: str | None = None) -> dict:
    return {"id": plan_id, "title": title or plan_id, "text": "tx", "intro": "x", "hashtags": f"#{plan_id.replace('-', '')}",
            "posts": [{"ref": r, "caption": f"Caption for {r}."} for r in refs]}


class World:
    """Helpers to rewrite the account and state between assertions."""

    def __init__(self, root):
        self.root = root

    def account(self, **changes) -> dict:
        acct = {"id": "acct", "enabled": True, "tradition": "trad", "plans": ["01-a", "02-b"], "seasonal": [],
                "timezone": TZ, "post_time": "12:00", "window_minutes_before": 30, "window_minutes_after": 120,
                "secrets": {"user_id": "IG_USER_ID_ACCT", "access_token": "IG_ACCESS_TOKEN_ACCT"}}
        acct.update(changes)
        write(self.root / "accounts" / "acct.json", acct)
        return acct

    def state(self, **changes) -> dict:
        st = {"plan_index": 0, "next_day": 1, "last_posted_date": None, "history": []}
        st.update(changes)
        write(self.root / "state" / "acct.json", st)
        return st


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    t = tmp_path / "traditions" / "trad"
    write(t / "tradition.json", {
        "id": "trad", "name": "Trad", "unit_name": "verse",
        "texts": {"tx": {"title": "Test Text", "short": "TT", "file": "texts/tx.json", "license": "PD",
                         "source": "here", "attribution": "Text: Test Text (public domain)."}},
        "ref_display": {"Psalms": "Psalm"},
        "hashtags": "#one #two #three #four",
    })
    verses = {f"Book 1:{v}": f"Book verse {v}." for v in range(1, 11)}
    verses.update({f"Psalms 23:{v}": f"Psalm verse {v}." for v in range(1, 7)})
    verses["Book 2:1"] = "He said, “Come and see."      # opens a quotation that closes later
    verses["Book 2:2"] = "It is good.” And they went."  # closes one opened earlier
    write(t / "texts" / "tx.json", {"id": "tx", "title": "Test Text", "works": [], "verses": verses})
    write(t / "plans" / "01-a.json", plan("01-a", ["Book 1:1", "Book 1:2", "Book 1:3"], "Plan A"))
    write(t / "plans" / "02-b.json", plan("02-b", ["Book 1:4", "Book 1:5"], "Plan B"))
    write(t / "plans" / "s-1.json", plan("s-1", ["Psalms 23:1", "Psalms 23:2", "Psalms 23:3"], "Season"))

    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    library.load_text.cache_clear()
    w = World(tmp_path)
    w.account()
    w.state()
    yield w
    library.load_text.cache_clear()
