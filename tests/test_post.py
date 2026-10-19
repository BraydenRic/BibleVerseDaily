"""`post` end to end with Instagram, hosting and rendering replaced by fakes."""
from datetime import datetime, timedelta

import pytest

import dailyverse.hosting as hosting
import dailyverse.instagram as instagram
import dailyverse.render as render
from dailyverse import cli
from dailyverse.library import load_account, load_state

from .conftest import today_local


class FakeInstagram:
    """Records calls; `fail_hosts` makes publishing fail like Instagram does when it can't
    download the video; `live_caption` is what reading the post back returns."""
    instances = []

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
        return f"media-{len(self.published)}"

    def media(self, media_id, fields="caption,permalink"):
        caption = self.published[-1][1] if self.live_caption is None else self.live_caption
        return {"caption": caption, "permalink": f"https://instagram.com/reel/{media_id}"}


@pytest.fixture
def fakes(world, monkeypatch):
    FakeInstagram.instances = []
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
    assert FakeInstagram.instances == []


def test_due_writes_a_github_step_output(world, tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert cli.main(["due", "--force"]) == 0
    assert out.read_text() == "due=true\n"
