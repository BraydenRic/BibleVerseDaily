import json

import pytest

from dailyverse import scaffold, validate
from dailyverse.library import load_account


def test_new_tradition_creates_a_disabled_account_and_templates(world):
    created = scaffold.create("taoism", root=world.root)
    names = {p.relative_to(world.root).as_posix() for p in created}
    assert names == {
        "traditions/taoism/tradition.json",
        "traditions/taoism/plans/01-first-plan.json",
        "traditions/taoism/texts/README.md",
        "accounts/taoism.json",
        "state/taoism.json",
    }
    acct = load_account("taoism")
    assert acct["enabled"] is False and acct["draft"] is True
    assert acct["secrets"] == {"user_id": "IG_USER_ID_TAOISM", "access_token": "IG_ACCESS_TOKEN_TAOISM"}
    plan = json.loads((world.root / "traditions/taoism/plans/01-first-plan.json").read_text())
    assert plan["id"] == "01-first-plan"


def test_new_tradition_with_a_separate_account_id(world):
    scaffold.create("islam", account_id="quran-english", root=world.root)
    assert load_account("quran-english")["secrets"]["user_id"] == "IG_USER_ID_QURAN_ENGLISH"


def test_new_tradition_never_overwrites(world):
    scaffold.create("taoism", root=world.root)
    with pytest.raises(FileExistsError):
        scaffold.create("taoism", root=world.root)


def test_new_tradition_rejects_odd_ids(world):
    with pytest.raises(ValueError):
        scaffold.create("Tao Ism", root=world.root)


def test_a_scaffolded_tradition_only_warns_until_enabled(world, capsys):
    scaffold.create("taoism", root=world.root)
    assert validate.run() == 0                     # the existing, enabled account is still fine
    assert "::warning::" in capsys.readouterr().out
