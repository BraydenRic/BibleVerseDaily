"""Loading traditions, texts, plans, accounts and state, and resolving references to text."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- loaders

def list_accounts(enabled_only: bool = True) -> list[dict]:
    accts = [read_json(p) for p in sorted((ROOT / "accounts").glob("*.json"))]
    return [a for a in accts if a.get("enabled", True) or not enabled_only]


def load_account(account_id: str) -> dict:
    return read_json(ROOT / "accounts" / f"{account_id}.json")


def load_tradition(tradition_id: str) -> dict:
    return read_json(ROOT / "traditions" / tradition_id / "tradition.json")


@lru_cache(maxsize=8)
def load_text(tradition_id: str, text_id: str) -> dict:
    meta = load_tradition(tradition_id)["texts"][text_id]
    return read_json(ROOT / "traditions" / tradition_id / meta["file"])


def load_plan(tradition_id: str, plan_id: str) -> dict:
    return read_json(ROOT / "traditions" / tradition_id / "plans" / f"{plan_id}.json")


def load_state(account_id: str) -> dict:
    path = ROOT / "state" / f"{account_id}.json"
    if not path.exists():
        return {"plan_index": 0, "next_day": 1, "last_posted_date": None, "history": []}
    return read_json(path)


def save_state(account_id: str, state: dict) -> None:
    write_json(ROOT / "state" / f"{account_id}.json", state)


def secret(name: str) -> str:
    """Read a secret from its own env var, or from SECRETS_JSON (the whole Actions secrets map)."""
    if os.environ.get(name):
        return os.environ[name]
    blob = os.environ.get("SECRETS_JSON")
    if blob:
        val = json.loads(blob).get(name)
        if val:
            return val
    raise KeyError(f"Missing secret {name}")


# ---------------------------------------------------------------- references

def parse_ref(ref: str) -> tuple[str, int, int | None, int | None]:
    """'Mark 12:30-31' -> ('Mark', 12, 30, 31); 'Tao Te Ching 8' -> ('Tao Te Ching', 8, None, None).

    Texts numbered by a single unit (the Dhammapada's 423 verses) use the chapter-only form,
    which may also be a range: 'Dhammapada 1-2' -> ('Dhammapada', 1, None, 2).
    Work names may contain spaces ('1 Samuel', 'Revelation of John').
    """
    work, loc = ref.rsplit(" ", 1)
    if ":" not in loc:
        first, _, last = loc.partition("-")
        return work, int(first), None, int(last) if last else None
    chapter, verses = loc.split(":")
    a, _, b = verses.partition("-")
    return work, int(chapter), int(a), int(b or a)


def balance_double_quotes(text: str) -> str:
    """A single verse often opens or closes a quotation that spans other verses.
    Add only the missing quote mark; never change any words."""
    depth, missing_open = 0, 0
    for ch in text:
        if ch == "“":
            depth += 1
        elif ch == "”":
            if depth == 0:
                missing_open += 1
            else:
                depth -= 1
    return "“" * missing_open + text + "”" * depth


def lookup(ref: str, text: dict) -> str:
    work, ch, a, b = parse_ref(ref)
    units = text["verses"]
    if a is None:
        return " ".join(units[f"{work} {n}"] for n in range(ch, (b or ch) + 1))
    return " ".join(units[f"{work} {ch}:{v}"] for v in range(a, b + 1))


def post_text(entry: dict, text: dict) -> str:
    if entry.get("display"):
        return entry["display"]
    return balance_double_quotes(lookup(entry["ref"], text))


def display_ref(ref: str, tradition: dict) -> str:
    """How a reference is shown to readers.

    Index keys use the source text's own work names ('Psalms 23:1', 'I Samuel 3:10'), which
    aren't always how people cite them. `ref_display` in tradition.json maps them to the
    familiar form ('Psalm 23:1', '1 Samuel 3:10') without touching the index or the plans.
    """
    work, loc = ref.rsplit(" ", 1)
    return f"{tradition.get('ref_display', {}).get(work, work)} {loc}"


# ---------------------------------------------------------------- schedule

@dataclass
class Slot:
    """One upcoming post for an account."""
    account: dict
    tradition: dict
    plan: dict
    entry: dict
    day: int          # 1-based day within the plan
    total: int        # posts in the plan
    when: datetime    # scheduled local time
    seasonal: bool = False   # from a date-pinned plan (doesn't move the regular queue)

    @property
    def text_meta(self) -> dict:
        return self.tradition["texts"][self.plan["text"]]

    @property
    def ref_label(self) -> str:
        return display_ref(self.entry["ref"], self.tradition)

    @property
    def seed(self) -> int:
        return sum(ord(c) for c in self.account["id"] + self.plan["id"]) * 1000 + self.day


@dataclass
class Season:
    """A plan pinned to the calendar (Advent ending on Christmas, say): day N always posts on
    start + N - 1, so a late start or a missed day can't push it off its dates."""
    plan: dict
    start: date

    @property
    def end(self) -> date:
        return self.start + timedelta(days=len(self.plan["posts"]) - 1)

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end


def start_date(account: dict) -> date | None:
    """Optional `start_date`: nothing posts before it, so the code can be pushed early
    (or a launch delayed) without the hourly run posting day 1 too soon."""
    return date.fromisoformat(account["start_date"]) if account.get("start_date") else None


def seasons(account: dict) -> list[Season]:
    """The account's date-pinned plans (`seasonal` in the account file), earliest first."""
    pinned = [Season(load_plan(account["tradition"], s["plan"]), date.fromisoformat(s["start"]))
              for s in account.get("seasonal", [])]
    return sorted(pinned, key=lambda s: s.start)


def upcoming(account: dict, count: int | None = None, from_date: date | None = None) -> list[Slot]:
    """Posts that will go out next, one per day, in order, with their scheduled dates.

    On a day covered by a seasonal plan, that plan's entry for the date goes out and the
    regular queue pauses; on every other day the regular queue continues where it left off.
    If the regular queue runs out, nothing is scheduled until the next seasonal plan starts.
    """
    state = load_state(account["id"])
    tradition = load_tradition(account["tradition"])
    tz = ZoneInfo(account["timezone"])
    hh, mm = map(int, account["post_time"].split(":"))
    today = from_date or datetime.now(tz).date()
    d = today + timedelta(days=1) if state.get("last_posted_date") == today.isoformat() else today
    d = max(d, start_date(account) or d)

    pinned = seasons(account)
    queue = account["plans"]
    loaded: dict[str, dict] = {}

    def plan_at(i: int) -> dict:
        if queue[i] not in loaded:
            loaded[queue[i]] = load_plan(account["tradition"], queue[i])
        return loaded[queue[i]]

    def at(day_: date) -> datetime:
        return datetime(day_.year, day_.month, day_.day, hh, mm, tzinfo=tz)

    slots: list[Slot] = []
    plan_index, day = state["plan_index"], state["next_day"]
    while count is None or len(slots) < count:
        season = next((s for s in pinned if s.covers(d)), None)
        if season:
            n = (d - season.start).days + 1
            posts = season.plan["posts"]
            slots.append(Slot(account, tradition, season.plan, posts[n - 1], n, len(posts), at(d), seasonal=True))
        else:
            # skip finished plans (also covers a plan that was shortened after it started)
            while plan_index < len(queue) and day > len(plan_at(plan_index)["posts"]):
                plan_index, day = plan_index + 1, 1
            if plan_index >= len(queue):
                later = [s.start for s in pinned if s.start > d]
                if not later:
                    break
                d = later[0]
                continue
            plan = plan_at(plan_index)
            slots.append(Slot(account, tradition, plan, plan["posts"][day - 1], day, len(plan["posts"]), at(d)))
            day += 1
        d += timedelta(days=1)
    return slots


def in_window(account: dict, now: datetime) -> bool:
    hh, mm = map(int, account["post_time"].split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (target - timedelta(minutes=account["window_minutes_before"])
            <= now <= target + timedelta(minutes=account["window_minutes_after"]))


def is_due(account: dict, now: datetime) -> bool:
    """True when the account has started, hasn't posted today (its local date), and `now` is in its window."""
    if (start_date(account) or now.date()) > now.date():
        return False
    posted_today = load_state(account["id"]).get("last_posted_date") == now.date().isoformat()
    return not posted_today and in_window(account, now)


def advance(account: dict, state: dict, slot: Slot, media_id: str, posted_on: str) -> dict:
    state["history"].append({"plan": slot.plan["id"], "day": slot.day, "ref": slot.entry["ref"],
                             "date": posted_on, "media_id": media_id})
    state["history"] = state["history"][-90:]
    state["last_posted_date"] = posted_on
    if slot.seasonal:
        return state     # seasonal days are placed by date; the regular queue just waits
    # Position comes from the plan actually posted, not the saved index, so a queue that
    # skipped a finished plan can't send the next run back to an old one.
    index = account["plans"].index(slot.plan["id"])
    if slot.day >= slot.total:
        state["plan_index"], state["next_day"] = index + 1, 1
    else:
        state["plan_index"], state["next_day"] = index, slot.day + 1
    return state
