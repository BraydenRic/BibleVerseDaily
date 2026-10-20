"""`post` end to end with Instagram, hosting and rendering replaced by fakes."""
from datetime import datetime, timedelta, timezone

import pytest

import dailyverse.hosting as hosting
import dailyverse.instagram as instagram
import dailyverse.render as render
from dailyverse import cli
from dailyverse.caption import build_caption
from dailyverse.library import load_account, load_state, upcoming

from .conftest import today_local


def live_post(caption, media_id, days_ago=0):
    """A post as Instagram lists it (UTC timestamp, in Instagram's format)."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"id": media_id, "caption": caption, "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "permalink": f"https://instagram.com/reel/{media_id}"}


class FakeInstagram:
    """Records calls; `fail_hosts` makes publishing fail like Instagram does when it can't
    download the video; `live_caption` is what reading the post back returns. `on_instagram`
    is the account's feed, newest first, shared by every run like the real account."""
    instances = []
    on_instagram = []

    def __init__(self, user_id, token):
        self.user_id, self.token = user_id, token
        self.published = []
        self.fail_hosts = set()
        self.live_caption = None
        FakeInstagram.instances.append(self)

    def publish_reel(self, url, caption, log=print):
        host = url.split(":", 1)[0]
        if host in self.fail_hosts:
            raise instagram.MediaFetchError(f"Media download has failed for {url}")
        self.published.append((url, caption))
        media_id = f"media-{len(self.published)}"
        FakeInstagram.on_instagram.insert(0, live_post(caption, media_id))
        return media_id

    def media(self, media_id, fields="caption,permalink"):
        caption = self.published[-1][1] if self.live_caption is None else self.live_caption
        return {"caption": caption, "permalink": f"https://instagram.com/reel/{media_id}"}

    def recent_media(self, count=100):
        return FakeInstagram.on_instagram[:count]


@pytest.fixture
def fakes(world, monkeypatch):
    FakeInstagram.instances = []
    FakeInstagram.on_instagram = []
    hosted = []

    def fake_render(card, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out

    def fake_host(path, account_id, method="raw"):
        hosted.append(method)
        return f"{method}://{account_id}/{path.name}"

    monkeypatch.setattr(render, "render_reel", fake_render)
    monkeypatch.setattr(hosting, "host_video", fake_host)
    monkeypatch.setattr(instagram, "Instagram", FakeInstagram)
    monkeypatch.setenv("IG_USER_ID_ACCT", "123")
    monkeypatch.setenv("IG_ACCESS_TOKEN_ACCT", "tok")
    return hosted


def ig():
    return FakeInstagram.instances[-1]


def published():
    """Everything any run put out (each run makes its own Instagram client)."""
    return [p for i in FakeInstagram.instances for p in i.published]


def captions(n):
    """Captions of the account's next n posts, exactly as they'd go out."""
    return [build_caption(s) for s in upcoming(load_account("acct"), count=n)]


def test_post_publishes_and_records_progress(world, fakes):
    assert cli.main(["post", "--force"]) == 0
    assert ig().user_id == "123" and ig().token == "tok"
    url, caption = ig().published[0]
    assert url.startswith("raw://acct/01-a-day-001")
    assert caption.startswith("Book verse 1.\nBook 1:1 (TT)")
    st = load_state("acct")
    assert (st["plan_index"], st["next_day"], st["last_posted_date"]) == (0, 2, today_local().isoformat())
    assert st["history"][-1]["media_id"] == "media-1"


def test_second_run_the_same_day_does_nothing(world, fakes):
    world.state(last_posted_date=today_local().isoformat(), next_day=2)
    assert cli.main(["post"]) == 0
    assert FakeInstagram.instances == []


def test_falls_back_to_the_release_host_when_instagram_cant_fetch(world, fakes, monkeypatch):
    original_init = FakeInstagram.__init__

    def init(self, *a):
        original_init(self, *a)
        self.fail_hosts = {"raw"}

    monkeypatch.setattr(FakeInstagram, "__init__", init)
    assert cli.main(["post", "--force"]) == 0
    assert fakes == ["raw", "release"]
    assert ig().published[0][0].startswith("release://")
    assert load_state("acct")["next_day"] == 2


def test_both_hosts_failing_fails_the_run_without_recording(world, fakes, monkeypatch):
    original_init = FakeInstagram.__init__

    def init(self, *a):
        original_init(self, *a)
        self.fail_hosts = {"raw", "release"}

    monkeypatch.setattr(FakeInstagram, "__init__", init)
    assert cli.main(["post", "--force"]) == 1
    assert load_state("acct")["last_posted_date"] is None


def test_a_dropped_caption_fails_the_run_but_keeps_the_progress(world, fakes, monkeypatch):
    original_init = FakeInstagram.__init__

    def init(self, *a):
        original_init(self, *a)
        self.live_caption = ""

    monkeypatch.setattr(FakeInstagram, "__init__", init)
    assert cli.main(["post", "--force"]) == 1          # exits non-zero so GitHub emails
    assert load_state("acct")["next_day"] == 2         # but the post is live, so it's recorded


def test_missing_secrets_fail_before_rendering(world, fakes, monkeypatch):
    monkeypatch.delenv("IG_ACCESS_TOKEN_ACCT")
    rendered = []
    monkeypatch.setattr(render, "render_reel", lambda *a: rendered.append(a))
    assert cli.main(["post", "--force"]) == 1
    assert rendered == []


def test_nothing_posts_before_the_start_date_even_when_forced(world, fakes):
    world.account(start_date=(today_local() + timedelta(1)).isoformat())
    assert cli.main(["post", "--force"]) == 0
    assert FakeInstagram.instances == []
    assert load_state("acct")["last_posted_date"] is None


def test_seasonal_post_is_never_sent_early(world, fakes):
    tomorrow = today_local() + timedelta(1)
    world.account(seasonal=[{"plan": "s-1", "start": tomorrow.isoformat()}])
    world.state(last_posted_date=today_local().isoformat(), next_day=2)
    assert cli.main(["post", "--force"]) == 0           # already posted; next one is pinned to tomorrow
    assert FakeInstagram.instances == []


class NoonToday(datetime):
    """Pins the clock to noon today so a run without --force is inside the posting window."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)


def test_a_normal_run_inside_the_window_posts(world, fakes, monkeypatch):
    monkeypatch.setattr(cli, "datetime", NoonToday)
    assert cli.main(["post"]) == 0
    assert len(ig().published) == 1


def test_a_gap_before_a_seasonal_plan_fails_loudly(world, fakes, monkeypatch, capsys):
    monkeypatch.setattr(cli, "datetime", NoonToday)
    world.state(plan_index=2, next_day=1)                # regular queue used up
    world.account(seasonal=[{"plan": "s-1", "start": (today_local() + timedelta(5)).isoformat()}])
    assert cli.main(["post"]) == 1                       # non-zero so GitHub emails
    assert "regular plans have run out" in capsys.readouterr().out
    assert published() == []


def test_due_writes_a_github_step_output(world, tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert cli.main(["due", "--force"]) == 0
    assert out.read_text() == "due=true\n"


# ---------------------------------------------------------------- never the same verse twice

def test_a_post_already_live_but_missing_from_state_is_recorded_not_repeated(world, fakes):
    # A run published day 1, then its state commit never landed (failed push, cancelled job).
    FakeInstagram.on_instagram = [live_post(captions(1)[0], "earlier")]
    assert cli.main(["post", "--force"]) == 0
    assert published() == []
    st = load_state("acct")
    assert (st["next_day"], st["last_posted_date"]) == (2, today_local().isoformat())
    assert st["history"][-1]["media_id"] == "earlier"


def test_catches_up_on_earlier_days_then_posts_the_next_verse(world, fakes):
    day1, day2, _ = captions(3)
    FakeInstagram.on_instagram = [live_post(day2, "m2", days_ago=1), live_post(day1, "m1", days_ago=2)]
    assert cli.main(["post", "--force"]) == 0
    [(_, caption)] = published()
    assert "Day 3 of 3 · Plan A" in caption
    assert [h["media_id"] for h in load_state("acct")["history"]] == ["m1", "m2", "media-1"]


def test_a_verse_already_posted_by_another_plan_is_not_posted_again(world, fakes):
    other = "Book verse 1.\nBook 1:1 (TT)\n\nCaption.\n\nDay 4 of 9 · Some other plan"
    FakeInstagram.on_instagram = [live_post(other, "old", days_ago=30)]
    assert cli.main(["post", "--force"]) == 1          # fails so GitHub emails, rather than repeat it
    assert published() == []
    assert load_state("acct")["last_posted_date"] is None


def test_a_similar_reference_is_not_mistaken_for_the_verse(world, fakes):
    # '1 Book 1:1 (TT)' contains 'Book 1:1 (TT)'; whole lines are compared, so it's another verse.
    FakeInstagram.on_instagram = [live_post("Other verse.\n1 Book 1:1 (TT)\n\nDay 1 of 3 · Plan A", "x", days_ago=3)]
    assert cli.main(["post", "--force"]) == 0
    assert len(published()) == 1


def test_a_date_pinned_post_already_out_on_another_day_is_not_repeated(world, fakes):
    # Day 1 went out yesterday, then the plan's start date was moved to today.
    world.account(seasonal=[{"plan": "s-1", "start": today_local().isoformat()}])
    FakeInstagram.on_instagram = [live_post(captions(1)[0], "pinned", days_ago=1)]
    assert cli.main(["post", "--force"]) == 1
    assert published() == []


def test_force_never_makes_a_second_post_the_same_day(world, fakes):
    assert cli.main(["post", "--force"]) == 0
    assert cli.main(["post", "--force"]) == 0
    assert len(published()) == 1
    assert load_state("acct")["next_day"] == 2


def test_nothing_posts_when_instagram_cant_say_what_is_already_live(world, fakes, monkeypatch):
    def unavailable(self, count=100):
        raise instagram.InstagramError("GET 123/media failed 3 times")

    monkeypatch.setattr(FakeInstagram, "recent_media", unavailable)
    assert cli.main(["post", "--force"]) == 1
    assert published() == []
