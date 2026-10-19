"""Give Instagram a public URL for the video.

Instagram's API pulls the video from a URL (uploading the bytes directly is only offered with
Facebook Login), so for a minute or two each day the file has to be reachable on the public
internet. Two hosts, tried in this order; both need a public repo:

- "raw": force-push the file alone to a branch `media-<account>` and hand over the
  raw.githubusercontent.com URL pinned to that commit. The branch is rewritten every day,
  so old videos don't pile up in the repo.
- "release": the only asset of a pre-release tagged `reel-<account>`, replaced every day.
  The fallback for when Instagram can't fetch the raw URL (GitHub rate-limits
  raw.githubusercontent.com for anonymous clients, and Meta's fetcher is anonymous).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HOSTS = ("raw", "release")
BOT = ["-c", "user.name=daily-verse-bot", "-c", "user.email=bot@users.noreply.github.com"]


def host_video(path: Path, account_id: str, method: str = "raw") -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is not set (owner/repo). Hosting runs in GitHub Actions; "
                           "set it yourself to post from a laptop.")
    if method == "raw":
        url = _push_to_branch(path, repo, account_id)
    elif method == "release":
        url = _upload_release_asset(path, repo, account_id)
    else:
        raise ValueError(f"unknown hosting method {method!r}")
    _wait_until_reachable(url)
    return url


def _push_to_branch(path: Path, repo: str, account_id: str) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set; it's needed to push the video branch.")
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    branch = f"media-{account_id}"
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(path, Path(tmp) / "reel.mp4")

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()

        git("init", "-q", "-b", branch)
        git(*BOT, "add", "reel.mp4")
        git(*BOT, "commit", "-q", "-m", f"reel for {account_id}")
        sha = git("rev-parse", "HEAD")
        git("push", "-q", "-f", remote, branch)
    return f"https://raw.githubusercontent.com/{repo}/{sha}/reel.mp4"


def _upload_release_asset(path: Path, repo: str, account_id: str) -> str:
    """Uses the GitHub CLI (preinstalled on runners; reads GH_TOKEN)."""
    tag = f"reel-{account_id}"
    # A new file name each time, so neither GitHub's CDN nor Instagram can serve yesterday's video.
    name = f"reel-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.mp4"

    def gh(*args: str) -> str:
        return subprocess.run(["gh", *args, "-R", repo], check=True, capture_output=True, text=True).stdout

    try:
        assets = json.loads(gh("release", "view", tag, "--json", "assets"))["assets"]
    except subprocess.CalledProcessError:
        gh("release", "create", tag, "--prerelease", "--title", f"Reel hosting ({account_id})",
           "--notes", "Managed by the daily post workflow: holds today's video so Instagram can download it.")
        assets = []
    for asset in assets:                       # only ever keep today's video
        gh("release", "delete-asset", tag, asset["name"], "--yes")
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / name
        shutil.copy(path, staged)
        gh("release", "upload", tag, str(staged))
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _wait_until_reachable(url: str) -> None:
    # GET rather than HEAD: release downloads redirect to signed storage URLs that only accept GET.
    for _ in range(20):
        try:
            with requests.get(url, stream=True, timeout=20, allow_redirects=True) as r:
                if r.status_code == 200:
                    return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError(f"Hosted video never became reachable: {url}")
