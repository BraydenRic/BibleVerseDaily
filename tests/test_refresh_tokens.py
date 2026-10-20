"""`refresh-tokens` with Instagram and the GitHub CLI replaced by fakes."""
import subprocess

import pytest

import dailyverse.instagram as instagram
from dailyverse import cli

TOKEN = "IG_ACCESS_TOKEN_ACCT"


@pytest.fixture
def saved(world, monkeypatch):
    """Secrets written with `gh secret set`, as (name, value)."""
    writes = []

    def fake_run(cmd, check=False):
        assert cmd[:3] == ["gh", "secret", "set"]
        writes.append((cmd[3], cmd[cmd.index("--body") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(instagram, "refresh_token", lambda token: (f"new-{token}", 60 * 86400))
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.delenv(TOKEN, raising=False)
    return writes


def test_refreshes_a_live_accounts_token(world, saved, monkeypatch):
    monkeypatch.setenv(TOKEN, "tok")
    assert cli.main(["refresh-tokens"]) == 0
    assert saved == [(TOKEN, "new-tok")]


def test_refreshes_an_account_that_is_not_live_yet(world, saved, monkeypatch):
    # Its token was made before launch; left alone it would expire before the account goes live.
    world.account(enabled=False)
    monkeypatch.setenv(TOKEN, "tok")
    assert cli.main(["refresh-tokens"]) == 0
    assert saved == [(TOKEN, "new-tok")]


def test_skips_an_account_not_live_yet_without_a_token(world, saved):
    world.account(enabled=False)
    assert cli.main(["refresh-tokens"]) == 0
    assert saved == []


def test_a_live_account_without_a_token_fails(world, saved):
    assert cli.main(["refresh-tokens"]) == 1
    assert saved == []
