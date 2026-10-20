# Daily Verse

Posts one scripture Reel a day to Instagram at noon: a calm moving background, the verse, and a short caption explaining the context. Verses follow reading plans in order, with date-pinned plans for major holy days. Each tradition gets its own account and a full year of content.

| Account | Tradition | On screen and in caption | Audio | Noon in | Status |
|---|---|---|---|---|---|
| `christianity` | Bible (Berean Standard Bible) | English | ambient music | Los Angeles | live from Oct 20, 2026 |
| `islam` | Quran (Arabic + Pickthall's English, "translation of the meaning") | Arabic, then English | none | London | live from Oct 20, 2026 |
| `hinduism` | Bhagavad Gita (Sanskrit + Besant's English) | Devanagari, then English | ambient music | Kolkata | live from Oct 20, 2026 |

Each has 365 days queued. Advent ends on Christmas, John ends on Easter, Ramadan reads through the Quran in 30 days, and the Gita ends a reading on Gita Jayanti. Full details and the design: **[SPEC.md](SPEC.md)**. Licenses: **[CREDITS.md](CREDITS.md)**.

## Quick start

```bash
pip install -r requirements-dev.txt     # also needs ffmpeg (and libfribidi0 for Arabic/Devanagari)
python -m pytest -q                     # tests
python -m dailyverse validate           # check every plan; prints how far ahead posts are scheduled
python -m dailyverse preview            # render upcoming posts to out/preview/index.html
```

## Launching an account

Nothing posts until an account is switched on. For each one:

1. Create the Instagram account, switch it to a professional (Creator) account, and keep it public.
2. In the Meta developer app, add it as an **Instagram Tester**, accept the invite from the account, and generate its token (SPEC.md section 6).
3. Add its two repo secrets: `bash scripts/set_ig_secrets.sh <account>` asks for the token, shows which Instagram account it belongs to, and saves both (the names are in its account file: `IG_USER_ID_<ACCOUNT>`, `IG_ACCESS_TOKEN_<ACCOUNT>`). The repo also needs `GH_PAT` once, for token refreshes. The three current accounts are already wired into the workflows.
4. In `accounts/<account>.json`, set `"start_date"` to the first day it should post and `"enabled": true`, then push. It posts at the next noon on or after `start_date`.

Date-pinned plans keep their dates whenever you launch; the regular reading starts on `start_date`. Islamic and Hindu dates follow the lunar calendar and can differ by a day by community, which the captions allow for. Have someone who practices each tradition read its preview before launch.

## Where things live

```
traditions/<tradition>/          content: one folder per tradition
  tradition.json                 translations, licenses, citation names, look and sound (style), base hashtags
  texts/<text>.json              full texts, indexed by reference
  plans/NN-<name>.json           reading plans: one verse + caption per day
accounts/<account>.json          one Instagram account: plan queue, date-pinned plans, time zone, start date
state/<account>.json             where the account is in its queue (committed by the bot)
scripts/build_index_*.py         rebuild each text index from its public source
dailyverse/                      the code
tests/                           pytest
```

A tradition is content; an account posts one tradition's plans; state tracks the account's progress. Adding a tradition means adding files, not changing code.

## Day to day

Nothing. The workflow runs hourly and each live account posts once inside its window around `post_time`. Instagram tokens refresh weekly. If a run fails, GitHub emails you; no email means it's running. The preview page (GitHub Pages) refreshes after every post.

Things that do need you, eventually:

- **Content.** `validate` prints how far ahead posts are scheduled. Add plans to the account's queue before then.
- **Date-pinned plans** (`seasonal`) are tied to a year. Add next year's dates when the time comes.
- **`GH_PAT`**, if you gave it an expiry date.

## Adding a tradition

```bash
python -m dailyverse new-tradition taoism      # or: new-tradition buddhism --account dhammapada
```

This creates the tradition folder, a template plan, and a disabled **draft** account with its own secret names (`validate` only warns about drafts), and prints the remaining steps. The only workflow change is passing the new account's two secrets by name (the command prints the exact lines, and a test fails if they're missing). Time zones and post times need no workflow changes. See SPEC.md section 7.
