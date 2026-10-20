"""The Instagram client's paging, with the HTTP call replaced by canned pages."""
from dailyverse.instagram import Instagram


def pages_of(*pages):
    """A stand-in for Instagram._call that serves `pages` in order and records each request."""
    requests = []

    def call(self, method, path, **params):
        requests.append(dict(params))
        return pages[len(requests) - 1]
    return call, requests


def page(ids, more=True):
    paging = {"cursors": {"after": f"after-{ids[-1]}"}}
    if more:
        paging["next"] = "https://graph.instagram.com/next-page"
    return {"data": [{"id": i} for i in ids], "paging": paging}


def test_recent_media_follows_pages_until_it_has_enough(monkeypatch):
    call, requests = pages_of(page(["1", "2"]), page(["3", "4"]), page(["5", "6"]))
    monkeypatch.setattr(Instagram, "_call", call)
    posts = Instagram("123", "tok").recent_media(count=3)
    assert [p["id"] for p in posts] == ["1", "2", "3"]
    assert [r.get("after") for r in requests] == [None, "after-2"]
    assert "caption" in requests[0]["fields"] and "timestamp" in requests[0]["fields"]


def test_recent_media_stops_at_the_last_page(monkeypatch):
    call, requests = pages_of(page(["1"]), page(["2"], more=False))
    monkeypatch.setattr(Instagram, "_call", call)
    assert [p["id"] for p in Instagram("123", "tok").recent_media()] == ["1", "2"]
    assert len(requests) == 2


def test_recent_media_of_a_new_account_is_empty(monkeypatch):
    call, _ = pages_of({"data": []})
    monkeypatch.setattr(Instagram, "_call", call)
    assert Instagram("123", "tok").recent_media() == []
