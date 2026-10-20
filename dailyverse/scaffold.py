"""`python -m dailyverse new-tradition <id>`: create the files for a new tradition and its account.

Everything is a template to fill in. The account starts disabled (nothing posts) and as a
draft (`validate` only warns about it). Remove "draft" once the texts and plans exist.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .library import ROOT, write_json

TEXTS_README = """# Texts for this tradition

Each file here is a full index of one translation, built by a script in `scripts/`
(copy `scripts/build_index_bsb.py` as a starting point):

```json
{
  "id": "<text id>",
  "title": "<translation title>",
  "works": [{"name": "<work>", "chapters": [<units per chapter>, ...]}],
  "verses": {"<Work> <Chapter>:<Verse>": "text", "...": "..."}
}
```

For texts divided only by chapter (the Tao Te Ching, for example) use `"<Work> <Chapter>"` keys.
Use a translation whose license allows reposting, preferably public domain, and record the
license and source in `tradition.json`.
"""


def create(tradition_id: str, account_id: str | None = None, root: Path = ROOT) -> list[Path]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tradition_id):
        raise ValueError("Use lowercase letters, digits and hyphens for the id, e.g. 'islam' or 'taoism'.")
    account_id = account_id or tradition_id
    tdir = root / "traditions" / tradition_id
    account_file = root / "accounts" / f"{account_id}.json"
    state_file = root / "state" / f"{account_id}.json"
    for path in (tdir, account_file, state_file):
        if path.exists():
            raise FileExistsError(f"{path.relative_to(root)} already exists; not overwriting it.")

    secret_stem = account_id.upper().replace("-", "_")
    files = {
        tdir / "tradition.json": {
            "id": tradition_id,
            "name": tradition_id.replace("-", " ").title(),
            "unit_name": "verse",
            "texts": {
                "example": {
                    "title": "Full title of the translation",
                    "short": "ABBR",
                    "file": "texts/example.json",
                    "license": "Public domain (say why: year, translator, terms page)",
                    "source": "Where the text was downloaded from",
                    "attribution": "Text from <translation> (public domain).",
                }
            },
            "ref_display": {},
            "hashtags": "#dailyverse",
        },
        tdir / "plans" / "01-first-plan.json": {
            "id": "01-first-plan",
            "title": "First plan",
            "text": "example",
            "intro": "One sentence describing this plan.",
            "hashtags": "#example",
            "posts": [{"ref": "Work 1:1", "caption": "2 to 4 plain-English sentences of context for a newcomer."}],
        },
        account_file: {
            "id": account_id,
            "enabled": False,
            "draft": True,
            "tradition": tradition_id,
            "plans": ["01-first-plan"],
            "seasonal": [],
            "timezone": "America/Los_Angeles",
            "post_time": "12:00",
            "window_minutes_before": 30,
            "window_minutes_after": 719,
            "secrets": {"user_id": f"IG_USER_ID_{secret_stem}", "access_token": f"IG_ACCESS_TOKEN_{secret_stem}"},
        },
        state_file: {"plan_index": 0, "next_day": 1, "last_posted_date": None, "history": []},
    }
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
    (tdir / "texts").mkdir(exist_ok=True)
    (tdir / "texts" / "README.md").write_text(TEXTS_README, encoding="utf-8")
    return sorted([*files, tdir / "texts" / "README.md"])


def next_steps(tradition_id: str, account_id: str | None = None) -> str:
    account_id = account_id or tradition_id
    stem = account_id.upper().replace("-", "_")
    return f"""Next steps:
  1. Pick a translation you may repost and fill in traditions/{tradition_id}/tradition.json.
  2. Write scripts/build_index_<text>.py (copy build_index_bsb.py) and build traditions/{tradition_id}/texts/<text>.json.
  3. Write the first plan in traditions/{tradition_id}/plans/ and list it in accounts/{account_id}.json.
  4. Create the Instagram account, add it as an Instagram Tester on the Meta app, generate its token,
     and add the repo secrets IG_USER_ID_{stem} and IG_ACCESS_TOKEN_{stem}. Then pass them to the
     workflows (each secret is passed by name; the tests check this):
       .github/workflows/daily-post.yml, "Render and post" env:
         IG_USER_ID_{stem}: ${{{{ secrets.IG_USER_ID_{stem} }}}}
         IG_ACCESS_TOKEN_{stem}: ${{{{ secrets.IG_ACCESS_TOKEN_{stem} }}}}
       .github/workflows/refresh-token.yml, "Refresh and save" env:
         IG_ACCESS_TOKEN_{stem}: ${{{{ secrets.IG_ACCESS_TOKEN_{stem} }}}}
  5. Remove "draft": true, run `python -m dailyverse validate` and the Preview workflow,
     then set "start_date" and "enabled": true to launch."""
