"""Command line entry point.

    python -m dailyverse post    [--account ID] [--force]
    python -m dailyverse due     [--account ID] [--force]
    python -m dailyverse preview [--account ID] [--videos 3] [--stills 14]
    python -m dailyverse validate
    python -m dailyverse refresh-tokens
    python -m dailyverse new-tradition ID [--account ID]

numpy, Pillow and requests are imported inside the commands that use them, so `due`,
`validate` and `new-tradition` run on a bare Python before anything is installed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .library import (ROOT, advance, in_window, is_due, list_accounts, load_account, load_state, save_state,
                      secret, start_date, upcoming)


def log(msg: str) -> None:
    print(msg, flush=True)


def selected_accounts(args) -> list[dict]:
    return [load_account(args.account)] if args.account else list_accounts()


def cmd_due(args) -> int:
    """Say whether any account should post right now, as a GitHub step output (due=true/false).

    The workflow runs every hour and checks this first, so most runs finish in seconds
    without installing ffmpeg or rendering anything.
    """
    due = [a["id"] for a in selected_accounts(args)
           if args.force or is_due(a, datetime.now(ZoneInfo(a["timezone"])))]
    line = f"due={'true' if due else 'false'}"
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(line + "\n")
    log(f"{line}: " + (", ".join(due) if due else "no account is in its posting window without having posted today"))
    return 0


def cmd_post(args) -> int:
    failures = 0
    for acct in selected_accounts(args):
        try:
            for problem in post_account(acct, args.force):
                failures += 1
                log(f"::error::[{acct['id']}] {problem}")
        except Exception as e:          # keep going so one account can't block the others
            failures += 1
            log(f"::error::[{acct['id']}] {e}")
    return 1 if failures else 0


def post_account(acct: dict, force: bool) -> list[str]:
    """Render and publish today's post. Returns problems found after publishing (the post is live)."""
    from .caption import build_caption
    from .hosting import HOSTS, host_video
    from .instagram import Instagram, MediaFetchError
    from .render import card_for, cover_time, render_reel

    aid = acct["id"]
    now = datetime.now(ZoneInfo(acct["timezone"]))
    today = now.date()

    def posted_today() -> bool:
        return load_state(aid).get("last_posted_date") == today.isoformat()

    if (start_date(acct) or today) > today:
        # Even with --force: an early day 1 would shift the whole calendar by a day.
        log(f"[{aid}] starts {start_date(acct)}; nothing posts before then (change start_date to launch earlier)")
        return []
    if posted_today():
        # Even with --force: one post a day, so a manual run can never put out a second one.
        log(f"[{aid}] already posted today; the next post goes out tomorrow")
        return []
    if not force and not in_window(acct, now):
        log(f"[{aid}] not due: {now:%H:%M %Z} is outside the window around {acct['post_time']}")
        return []

    # Read credentials before the minute-long render so a missing secret fails fast.
    ig = Instagram(secret(acct["secrets"]["user_id"]), secret(acct["secrets"]["access_token"]))
    catch_up(ig, acct, today)
    if posted_today():
        return []          # today's post was already live; catch_up recorded it

    slots = upcoming(acct, count=1, from_date=today)
    if not slots:
        raise RuntimeError(f"No posts left. Add the next plan to accounts/{aid}.json")
    slot = slots[0]
    if slot.when.date() != today:
        # Only a date-pinned plan can come up on a later day: the regular plans ran out before it.
        if force:
            log(f"[{aid}] next post ({slot.plan['id']} day {slot.day}) is pinned to {slot.when:%Y-%m-%d}; not posting it early")
            return []
        raise RuntimeError(f"Nothing scheduled for today: the regular plans have run out before "
                           f"{slot.plan['id']} starts on {slot.when:%Y-%m-%d}. Add a plan to accounts/{aid}.json")

    card = card_for(slot)
    caption = build_caption(slot)
    out = ROOT / "out" / aid / f"{slot.plan['id']}-day-{slot.day:03d}.mp4"
    log(f"[{aid}] rendering {slot.plan['id']} day {slot.day}: {card.ref}")
    render_reel(card, out)
    log(f"---- caption ----\n{caption}\n-----------------")

    media_id, fetch_error = None, None
    for method in HOSTS:
        url = host_video(out, aid, method)
        log(f"[{aid}] hosted ({method}) at {url}")
        try:
            media_id = ig.publish_reel(url, caption, log=log, cover_ms=int(cover_time(card) * 1000))
            break
        except MediaFetchError as e:
            fetch_error = e
            log(f"::warning::[{aid}] Instagram couldn't use the {method} link, trying the next host: {e}")
    if media_id is None:
        raise fetch_error
    # The post is live: record it before anything else can fail, so it's never posted twice.
    save_state(aid, advance(acct, load_state(aid), slot, media_id, today.isoformat()))
    return check_published(ig, aid, slot, media_id, caption)


def catch_up(ig, acct: dict, today) -> None:
    """Record posts that are live on Instagram but missing from state, instead of posting them again.

    State normally knows every post, but it can fall behind Instagram: a run publishes and its
    state commit never lands (a failed push, a cancelled job), or a publish goes through but its
    reply is lost. Working from that state, the next run would post the same verse again, so
    Instagram, the record of what's actually live, is checked first. A post is recognized by its
    caption's reference line and day line (caption.ref_line, caption.day_line).
    """
    from .caption import day_line, ref_line
    aid, tz = acct["id"], ZoneInfo(acct["timezone"])
    # Whole lines are compared, so '1 John 1:1 (BSB)' is never taken for 'John 1:1 (BSB)'.
    live = [(m, {line.strip() for line in (m.get("caption") or "").splitlines()}) for m in ig.recent_media()]
    while load_state(aid).get("last_posted_date") != today.isoformat():
        slots = upcoming(acct, count=1, from_date=today)
        if not slots:
            return
        slot = slots[0]
        same_verse = [(m, lines) for m, lines in live if ref_line(slot) in lines]
        if not same_verse:
            return                      # not on Instagram yet: this is the post to make
        same_post = next((m for m, lines in same_verse if day_line(slot) in lines), None)
        if same_post is None:
            other = same_verse[0][0]
            raise RuntimeError(f"{slot.ref_label} ({slot.plan['id']} day {slot.day}) is already on Instagram from "
                               f"another post ({other.get('permalink') or other['id']}); not posting it twice. "
                               f"Swap in another verse in traditions/{acct['tradition']}/plans/{slot.plan['id']}.json")
        posted_on = datetime.fromisoformat(same_post["timestamp"]).astimezone(tz).date()
        if slot.seasonal and posted_on != today:
            # A date-pinned entry belongs to one date and the queue can't move past it, so skip
            # the day rather than repeat the verse (this only happens if its start date was changed).
            raise RuntimeError(f"Today's date-pinned post ({slot.plan['id']} day {slot.day}) already went out on "
                               f"{posted_on} ({same_post.get('permalink') or same_post['id']}); not posting it twice. "
                               f"Did its start date in accounts/{aid}.json change?")
        log(f"::warning::[{aid}] {slot.plan['id']} day {slot.day} ({slot.ref_label}) is already on Instagram from "
            f"{posted_on} but state didn't have it; recording it instead of posting it again")
        save_state(aid, advance(acct, load_state(aid), slot, same_post["id"], posted_on.isoformat()))


def check_published(ig, aid: str, slot, media_id: str, caption: str) -> list[str]:
    """Read the post back from Instagram. A Reel can publish with its caption dropped (for
    example when the caption trips an integrity check), and nothing else would show that."""
    try:
        live = ig.media(media_id)
    except Exception as e:
        return [f"published {media_id} but couldn't read it back to check the caption: {e}"]
    log(f"[{aid}] published day {slot.day} as {media_id} {live.get('permalink', '')}")
    sent, got = " ".join(caption.split()), " ".join(live.get("caption", "").split())
    if not got:
        return [f"published {live.get('permalink') or media_id} but Instagram shows no caption on it"]
    if got != sent:
        log(f"::warning::[{aid}] the caption on Instagram differs from what was sent (it may have been reformatted)")
    return []


def cmd_preview(args) -> int:
    from . import preview
    path = preview.build([args.account] if args.account else None, videos=args.videos, stills=args.stills, log=log)
    log(f"Preview written to {path}")
    return 0


def cmd_validate(args) -> int:
    from . import validate
    return validate.run(log=log)


def cmd_refresh_tokens(args) -> int:
    """Refresh every account's token and save it back with the GitHub CLI (needs GH_TOKEN with secrets:write).

    Accounts that aren't live yet are refreshed too: a token dies after 60 days without a refresh,
    and an account can easily wait that long between getting its token and launching. A disabled
    account with no token yet is skipped; a live one without a token is an error.
    """
    from .instagram import refresh_token
    failures = 0
    for acct in list_accounts(enabled_only=False):
        name = acct["secrets"]["access_token"]
        if not os.environ.get(name) and not acct.get("enabled", True):
            log(f"[{acct['id']}] no token yet; skipping")
            continue
        try:
            new, expires = refresh_token(secret(name))
            print(f"::add-mask::{new}", flush=True)
            subprocess.run(["gh", "secret", "set", name, "--body", new], check=True)
            log(f"[{acct['id']}] token refreshed, valid for {expires // 86400} days")
        except Exception as e:
            failures += 1
            log(f"::error::[{acct['id']}] {e}")
    return 1 if failures else 0


def cmd_new_tradition(args) -> int:
    from . import scaffold
    try:
        created = scaffold.create(args.id, args.account)
    except (ValueError, FileExistsError) as e:
        log(f"Error: {e}")
        return 1
    for path in created:
        log(f"created {path.relative_to(ROOT)}")
    log(scaffold.next_steps(args.id, args.account))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dailyverse")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post", help="post today's item for each account (only near post_time unless --force)")
    p.add_argument("--account")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("due", help="print due=true/false: does any account need to post right now?")
    p.add_argument("--account")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_due)

    p = sub.add_parser("preview", help="render upcoming posts into out/preview/index.html")
    p.add_argument("--account")
    p.add_argument("--videos", type=int, default=3, help="how many upcoming posts get full videos")
    p.add_argument("--stills", type=int, default=14, help="how many upcoming posts to show (0 = all)")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("validate", help="check every plan: references exist, captions fit, no gaps")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("refresh-tokens", help="refresh Instagram tokens and save them as repo secrets")
    p.set_defaults(fn=cmd_refresh_tokens)

    p = sub.add_parser("new-tradition", help="create template files for a new tradition and its account")
    p.add_argument("id", help="tradition id, e.g. islam")
    p.add_argument("--account", help="account id (defaults to the tradition id)")
    p.set_defaults(fn=cmd_new_tradition)

    args = ap.parse_args(argv)
    if getattr(args, "stills", None) == 0:
        args.stills = None
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
