"""Build a phone-friendly preview page of upcoming posts: exactly the video and caption that will go out."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from .caption import build_caption
from .library import ROOT, Slot, list_accounts, load_account, upcoming
from .render import card_for, render_reel, render_still

OUT = ROOT / "out" / "preview"


def render_slot(slot: Slot, folder: Path, video: bool) -> dict:
    card = card_for(slot)
    stem = f"{slot.plan['id']}-day-{slot.day:03d}"
    still = render_still(card, folder / f"{stem}.jpg")
    mp4 = render_reel(card, folder / f"{stem}.mp4") if video else None
    return {
        "when": slot.when.isoformat(),
        "when_label": slot.when.strftime("%a %b %-d, %-I:%M %p %Z"),
        "ref": card.ref,
        "day": slot.day,
        "total": slot.total,
        "plan": slot.plan["title"],
        "still": still.name,
        "video": mp4.name if mp4 else None,
        "caption": build_caption(slot),
    }


def build(account_ids: list[str] | None, videos: int, stills: int | None, log=print) -> Path:
    # Disabled accounts too: previewing a tradition before it goes live is the main reason to have one.
    accounts = [load_account(a) for a in account_ids] if account_ids else list_accounts(enabled_only=False)
    OUT.mkdir(parents=True, exist_ok=True)
    data = []
    for acct in accounts:
        folder = OUT / acct["id"]
        folder.mkdir(parents=True, exist_ok=True)
        slots = upcoming(acct, count=stills)
        items = []
        for i, slot in enumerate(slots):
            log(f"[{acct['id']}] {slot.when:%Y-%m-%d} day {slot.day}: {slot.entry['ref']}"
                + (" (video)" if i < videos else ""))
            items.append(render_slot(slot, folder, video=i < videos))
        data.append({"id": acct["id"], "handle": acct.get("instagram_handle", acct["id"]),
                     "live": acct.get("enabled", True), "items": items})
    (OUT / "index.html").write_text(page(data), encoding="utf-8")
    (OUT / "preview.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return OUT / "index.html"


def page(accounts: list[dict]) -> str:
    cards = []
    for acct in accounts:
        status = "" if acct.get("live", True) else ' <span class="off">not live yet</span>'
        cards.append(f'<h2 class="acct">{html.escape(acct["handle"])}{status}</h2>')
        if not acct["items"]:
            cards.append('<p class="empty">No upcoming posts. Add a plan to this account.</p>')
        for i, it in enumerate(acct["items"]):
            src = f'{acct["id"]}/{it["video"] or it["still"]}'
            poster = f'{acct["id"]}/{it["still"]}'
            media = (f'<video src="{src}" poster="{poster}" controls playsinline loop preload="none"></video>'
                     if it["video"] else f'<img src="{poster}" alt="{html.escape(it["ref"])}" loading="lazy">')
            first, _, rest = it["caption"].partition("\n\n")
            badge = '<span class="badge">Next up</span>' if i == 0 else ""
            cards.append(f"""
<article class="post">
  <header><time datetime="{it['when']}">{html.escape(it['when_label'])}</time>{badge}</header>
  <div class="media">{media}</div>
  <div class="meta"><strong>{html.escape(acct['handle'])}</strong> <span class="ref">{html.escape(it['plan'])}, day {it['day']} of {it['total']}</span></div>
  <details><summary>{html.escape(first)}</summary><p class="cap">{html.escape(rest)}</p></details>
</article>""")
    built = datetime.now().strftime("%b %-d, %Y %-I:%M %p")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upcoming posts</title>
<style>
:root {{ --bg:#f6f4f0; --card:#ffffff; --ink:#1d1b19; --muted:#6d6862; --line:#e6e1da; --accent:#5b6fa8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#121212; --card:#1c1c1e; --ink:#f2efe9; --muted:#9a958e; --line:#2c2c2e; --accent:#9fb1e6; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width:440px; margin:0 auto; padding:20px 16px 48px; }}
h1 {{ font-size:20px; margin:4px 0 2px; }}
.sub {{ color:var(--muted); font-size:13px; margin:0 0 20px; }}
h2.acct {{ font-size:15px; margin:28px 0 10px; color:var(--muted); font-weight:600; }}
.post {{ background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; margin-bottom:18px; }}
.post header {{ display:flex; justify-content:space-between; align-items:center; padding:10px 14px; font-size:13px; color:var(--muted); }}
.badge {{ background:var(--accent); color:#fff; border-radius:999px; padding:2px 10px; font-size:12px; font-weight:600; }}
.media {{ aspect-ratio:9/16; background:#000; }}
.media video, .media img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.meta {{ padding:12px 14px 0; font-size:14px; }}
.ref {{ color:var(--muted); }}
details {{ padding:6px 14px 14px; }}
summary {{ cursor:pointer; white-space:pre-line; list-style:none; }}
summary::-webkit-details-marker {{ display:none; }}
summary::after {{ content:" more"; color:var(--muted); }}
details[open] summary::after {{ content:""; }}
.cap {{ white-space:pre-line; margin:10px 0 0; }}
.empty {{ color:var(--muted); }}
.off {{ font-size:12px; font-weight:600; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:1px 8px; margin-left:6px; }}
</style></head>
<body><main>
<h1>Upcoming posts</h1>
<p class="sub">Exactly what will be posted, in order. Built {built}. Nothing here has been sent.</p>
{''.join(cards)}
</main></body></html>"""
