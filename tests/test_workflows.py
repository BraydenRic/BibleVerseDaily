"""The workflows pass each account's secrets by name (never the whole secrets map, which GitHub
flags as possibly malicious and refuses to run). These tests keep them in step with accounts/."""
import re

from dailyverse.library import ROOT, list_accounts

WORKFLOWS = ROOT / ".github" / "workflows"


def passed_secrets(workflow: str) -> set[str]:
    """Env names set from the secret of the same name (`IG_X: ${{ secrets.IG_X }}`)."""
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    pairs = re.findall(r"^\s+(\w+): \$\{\{ secrets\.(\w+) \}\}", text, re.M)
    return {name for name, secret in pairs if name == secret}


def test_no_workflow_hands_over_every_secret():
    for path in WORKFLOWS.glob("*.yml"):
        assert "toJSON(secrets)" not in path.read_text(encoding="utf-8"), path.name


def test_daily_post_receives_every_accounts_secrets():
    passed = passed_secrets("daily-post.yml")
    for acct in list_accounts(enabled_only=False):
        if acct.get("draft"):
            continue
        for name in acct["secrets"].values():
            assert name in passed, f"add `{name}: ${{{{ secrets.{name} }}}}` to daily-post.yml"


def test_token_refresh_receives_every_accounts_token():
    passed = passed_secrets("refresh-token.yml")
    for acct in list_accounts(enabled_only=False):
        if acct.get("draft"):
            continue
        name = acct["secrets"]["access_token"]
        assert name in passed, f"add `{name}: ${{{{ secrets.{name} }}}}` to refresh-token.yml"
