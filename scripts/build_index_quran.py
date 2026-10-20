"""Build traditions/islam/texts/{pickthall,arabic}.json.

- English: Marmaduke Pickthall, "The Meaning of the Glorious Koran" (1930). Public domain:
  published 1930 (US public domain since 2026) and Pickthall died in 1936 (life+70/80 elsewhere).
  Taken from Project Gutenberg #16955, which interleaves three translations; only the
  Pickthall ("P:") paragraphs are used. (Tanzil's own Pickthall file is licensed for
  non-commercial use only and has substitution errors, so it isn't used.) The e-text isn't
  word for word the 1930 printing; every scheduled verse was checked against archive.org scans
  of the 1930 Knopf and Allen & Unwin printings, and CORRECTIONS restores the printed wording.
- Arabic: Tanzil Project Uthmani text, verbatim. Tanzil's terms allow use with attribution and a
  link to tanzil.net but forbid changing the text, so it is stored exactly as published, including
  the opening "Bismillah" that Tanzil prefixes to verse 1 of most surahs; posts that use a verse 1
  show the verse alone through `display_original` (an excerpt, which is allowed).

Keys look like "Al-Baqarah 2:255": the surah's common English transliteration, then its number.

    python scripts/build_index_quran.py
"""
import json
import re
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "traditions" / "islam" / "texts"
GUTENBERG = "https://www.gutenberg.org/cache/epub/16955/pg16955.txt"
TANZIL = "https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt-2&agree=true"

# Where the e-text differs from Pickthall's 1930 printing, the printing wins: it's the edition
# credited, and the one that's certainly public domain. (e-text, 1930) pairs; typos first.
CORRECTIONS = {
    "Al-Mutaffifin 83:3": [("or weight for them", "or weigh for them")],
    "Al-Asr 103:2": [("man is a state of loss", "man is in a state of loss")],
    "Al-Hajj 22:37": [("their food reach not", "their blood reach not")],
    "Yusuf 12:1": [("These are verse of", "These are verses of")],
    "Al-Fatiha 1:4": [("Master of the Day", "Owner of the Day")],
    "Al-Baqarah 2:163": [("Your Allah is One Allah;", "Your God is One God;")],
    "Al-Baqarah 2:255": [("There is no deity save Him", "There is no God save Him")],
    "An-Nisa 4:149": [("Allah is ever Forgiving", "Allah is Forgiving")],
    "Al-Isra 17:85": [("They are asking thee", "They will ask thee")],
    "Al-Kahf 18:7": [("all that is on the earth", "all that is in the earth")],
    "Az-Zumar 39:54": [("Turn unto your Lord repentant", "Turn unto Him repentant")],
    "Al-Jinn 72:1": [("Lo! we have heard a marvellous", "Lo! it is a marvellous")],
    "Al-Qiyamah 75:4": [("Yea, verily. We are Able", "Yea, verily. Yea, We are Able")],
    "Al-Qadr 97:1": [("Night of Predestination", "Night of Power")],
}


def apply_corrections(verses: dict[str, str]) -> dict[str, str]:
    for key, pairs in CORRECTIONS.items():
        for old, new in pairs:
            assert old in verses[key], f"{key}: {old!r} isn't in the source any more; recheck CORRECTIONS"
            verses[key] = verses[key].replace(old, new, 1)
    return verses

SURAHS = [
    "Al-Fatiha", "Al-Baqarah", "Al-Imran", "An-Nisa", "Al-Ma'idah", "Al-An'am", "Al-A'raf", "Al-Anfal",
    "At-Tawbah", "Yunus", "Hud", "Yusuf", "Ar-Ra'd", "Ibrahim", "Al-Hijr", "An-Nahl", "Al-Isra", "Al-Kahf",
    "Maryam", "Ta-Ha", "Al-Anbiya", "Al-Hajj", "Al-Mu'minun", "An-Nur", "Al-Furqan", "Ash-Shu'ara",
    "An-Naml", "Al-Qasas", "Al-Ankabut", "Ar-Rum", "Luqman", "As-Sajdah", "Al-Ahzab", "Saba", "Fatir",
    "Ya-Sin", "As-Saffat", "Sad", "Az-Zumar", "Ghafir", "Fussilat", "Ash-Shura", "Az-Zukhruf", "Ad-Dukhan",
    "Al-Jathiyah", "Al-Ahqaf", "Muhammad", "Al-Fath", "Al-Hujurat", "Qaf", "Adh-Dhariyat", "At-Tur",
    "An-Najm", "Al-Qamar", "Ar-Rahman", "Al-Waqi'ah", "Al-Hadid", "Al-Mujadila", "Al-Hashr",
    "Al-Mumtahanah", "As-Saff", "Al-Jumu'ah", "Al-Munafiqun", "At-Taghabun", "At-Talaq", "At-Tahrim",
    "Al-Mulk", "Al-Qalam", "Al-Haqqah", "Al-Ma'arij", "Nuh", "Al-Jinn", "Al-Muzzammil", "Al-Muddaththir",
    "Al-Qiyamah", "Al-Insan", "Al-Mursalat", "An-Naba", "An-Nazi'at", "Abasa", "At-Takwir", "Al-Infitar",
    "Al-Mutaffifin", "Al-Inshiqaq", "Al-Buruj", "At-Tariq", "Al-A'la", "Al-Ghashiyah", "Al-Fajr",
    "Al-Balad", "Ash-Shams", "Al-Layl", "Ad-Duha", "Ash-Sharh", "At-Tin", "Al-Alaq", "Al-Qadr",
    "Al-Bayyinah", "Az-Zalzalah", "Al-Adiyat", "Al-Qari'ah", "At-Takathur", "Al-Asr", "Al-Humazah",
    "Al-Fil", "Quraysh", "Al-Ma'un", "Al-Kawthar", "Al-Kafirun", "An-Nasr", "Al-Masad", "Al-Ikhlas",
    "Al-Falaq", "An-Nas",
]


def arabic_verses(raw: str) -> tuple[list[tuple[int, int, str]], str]:
    """[(surah, verse, text)] in canonical order, plus Tanzil's copyright notice."""
    verses, notice = [], []
    for line in raw.splitlines():
        if line.startswith("#"):
            notice.append(line.lstrip("# ").rstrip())
        elif line.strip():
            s, v, text = line.split("|", 2)
            verses.append((int(s), int(v), text.strip()))
    return verses, "\n".join(notice).strip()


def pickthall_paragraphs(raw: str) -> list[tuple[str, str]]:
    """[(header, text)] for every verse block, in file order.

    Headers are normally "SSS.VVV", but a few are mangled in the source ("0.033" for 17:33),
    so callers pair blocks with verses by position and use headers only as a cross-check.
    """
    start = raw.index("\n001.001")
    blocks, header, para, in_p = [], None, [], False
    for line in raw[start:].splitlines():
        if re.fullmatch(r"\d+\.\d+\s*", line):
            header = line.strip()
        elif line.startswith("P: "):
            para, in_p = [line[3:].strip()], True
        elif in_p and line.strip():
            para.append(line.strip())
        elif in_p:                                   # blank line ends the Pickthall paragraph
            blocks.append((header, " ".join(para)))
            in_p = False
        if line.startswith("*** END OF"):
            break
    return blocks


def main() -> None:
    assert len(SURAHS) == 114
    arabic, notice = arabic_verses(requests.get(TANZIL, timeout=120).text)
    english = pickthall_paragraphs(requests.get(GUTENBERG, timeout=120).text)
    assert len(arabic) == 6236, len(arabic)
    assert len(english) == 6236, len(english)

    mismatched = 0
    en_verses, ar_verses, works = {}, {}, []
    for (s, v, ar), (header, en) in zip(arabic, english):
        expected = f"{s:03d}.{v:03d}"
        if header != expected:
            mismatched += 1                          # a mangled header; position decides
            assert mismatched <= 6, f"too many header mismatches near {expected} ({header})"
        key = f"{SURAHS[s - 1]} {s}:{v}"
        en_verses[key] = re.sub(r"\s+", " ", en).strip()
        ar_verses[key] = ar
        if v == 1:
            works.append({"name": SURAHS[s - 1], "number": s, "chapters": []})
        works[-1]["chapters"] = [v]

    apply_corrections(en_verses)
    OUT.mkdir(parents=True, exist_ok=True)
    common = {"works": works}
    (OUT / "pickthall.json").write_text(json.dumps(
        {"id": "pickthall", "title": "The Meaning of the Glorious Koran (Marmaduke Pickthall, 1930)", **common,
         "verses": en_verses}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "arabic.json").write_text(json.dumps(
        {"id": "arabic", "title": "The Quran, Uthmani script (Tanzil Project)", "notice": notice, **common,
         "verses": ar_verses}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(en_verses)} verses in {len(works)} surahs ({mismatched} mangled source headers handled) -> {OUT}")


if __name__ == "__main__":
    main()
