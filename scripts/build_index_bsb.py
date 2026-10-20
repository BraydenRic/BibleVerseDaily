"""Rebuild traditions/christianity/texts/bsb.json from the public domain Berean Standard Bible.

Use this as the template for adding any new text: download a public domain source,
flatten it into {"Work Chapter:Verse": "text"} (or {"Work Chapter": "text"} for
chapter-only texts), and save it under traditions/<id>/texts/.

    python scripts/build_index_bsb.py
"""
import json
import re
from pathlib import Path

import requests

SRC = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/json/BSB.json"
OUT = Path(__file__).resolve().parent.parent / "traditions" / "christianity" / "texts" / "bsb.json"


def clean(text: str) -> str:
    """The BSB prints poetry line by line and the source joins the lines with spaces, which leaves
    "confidence— but" where a line ended on a dash. The BSB's dashes are closed ("confidence—but");
    only spacing changes, never words."""
    return re.sub(r"\s*—\s*", "—", text.strip())


def main() -> None:
    data = requests.get(SRC, timeout=120).json()
    works, verses = [], {}
    for book in data["books"]:
        works.append({"name": book["name"], "chapters": [len(c["verses"]) for c in book["chapters"]]})
        for ch in book["chapters"]:
            for v in ch["verses"]:
                verses[f"{book['name']} {ch['chapter']}:{v['verse']}"] = clean(v["text"])
    OUT.write_text(json.dumps({"id": "bsb", "title": "Berean Standard Bible", "works": works, "verses": verses},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(verses)} verses in {len(works)} books -> {OUT}")


if __name__ == "__main__":
    main()
