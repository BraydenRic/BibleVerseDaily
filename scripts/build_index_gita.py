"""Build traditions/hinduism/texts/{besant,sanskrit,iast}.json for the Bhagavad Gita.

- English and Sanskrit (Devanagari): Annie Besant's edition (first published 1895; Wikisource
  has the 4th edition, 1922, which prints the Sanskrit with each verse). Public domain:
  published before 1931 (US) and Besant died in 1933 (life+70 elsewhere). Parsed from
  Wikisource's rendered chapter pages: each Sanskrit verse ends with a Devanagari "॥ N ॥"
  marker and Besant's English for it follows, ending "(N)".
- IAST transliteration (for captions): github.com/gita/gita (Unlicense), whose word-split
  romanization reads easily. Its translation files are modern and copyrighted, so only
  verse.json is used. Where its line doesn't match the verse (a handful are merged or shifted
  relative to Besant's numbering), the line is generated from the Devanagari instead with
  indic-transliteration (MIT; `pip install indic-transliteration`, only needed for this script).

Both sources number chapter 13 with Arjuna's opening question as verse 1 (701 verses in all);
many Indian editions start chapter 13 one verse later.

The Wikisource transcription of the Devanagari has some errors (hyphens left from printed line
breaks, slipped spaces, misspellings). Those are fixed to the standard (Gita Press) reading: the
hyphens by rule, the rest in CORRECTIONS.

Keys look like "Bhagavad Gita 2:47".

    python scripts/build_index_gita.py
"""
import html
import json
import re
import time
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "traditions" / "hinduism" / "texts"
VERSES = "https://raw.githubusercontent.com/gita/gita/main/data/verse.json"

# Transcription errors in the Devanagari, as (transcribed, standard reading) pairs, checked
# against the Gita Press text.
CORRECTIONS = {
    "2:3": [("क्लैव्यं", "क्लैब्यं")],
    "2:6": [("जिजीविषाम:", "जिजीविषामः")],                    # a Latin colon for the visarga
    "2:67": [("मनोऽनु विधीयते", "मनोऽनुविधीयते")],
    "4:14": [("बद्ध्यते", "बध्यते")],
    "4:22": [("निबद्ध्यते", "निबध्यते")],
    "7:11": [("बलं बलवतामस्मि", "बलं बलवतां चाहं")],
    "11:28": [("नरलोकवीराः विशन्ति", "नरलोकवीरा विशन्ति")],
    "12:14": [("यो मे भक्तः", "यो मद्भक्तः")],
    "13:18": [("धिष्ठितम्", "विष्ठितम्")],
    "15:5": [("जितसङ्गदोषाः अध्यात्मनित्या", "जितसङ्गदोषा अध्यात्मनित्या")],
    "15:20": [("एतद्‌बुध्वा", "एतद्बुद्ध्वा")],
    "17:11": [("विधिदृष्टोय इज्यते", "विधिदृष्टो य इज्यते")],
}


def correct_sanskrit(verses: dict[str, str]) -> dict[str, str]:
    for key, text in verses.items():
        # a hyphen and space left where the printed line broke inside a word
        verses[key] = re.sub(r"(?<=[ऀ-ॿ])- (?=[ऀ-ॿ])", "", text)
    for ref, pairs in CORRECTIONS.items():
        key = f"{WORK} {ref}"
        for old, new in pairs:
            assert old in verses[key], f"{key}: {old!r} isn't in the source any more; recheck CORRECTIONS"
            verses[key] = verses[key].replace(old, new, 1)
    return verses
WIKISOURCE = ("https://en.wikisource.org/w/api.php?action=parse&format=json&formatversion=2&prop=text"
              "&page=Bhagavad-Gita_(Besant_4th)/Discourse_{n}")
WORK = "Bhagavad Gita"
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def page_lines(chapter: int) -> list[str]:
    # Wikimedia asks API clients to identify themselves with a contact URL and to go slowly;
    # anonymous-looking clients get HTTP 429.
    for attempt in range(5):
        r = requests.get(WIKISOURCE.format(n=chapter), timeout=60,
                         headers={"User-Agent": "daily-verse-index-builder/1.0 (https://github.com/BraydenRic/BibleVerseDaily)"})
        if r.status_code != 429:
            break
        time.sleep(10 * (attempt + 1))
    r.raise_for_status()
    time.sleep(1)
    raw = r.json()["parse"]["text"]
    raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.S)      # inline CSS would leak in as text
    # Wikisource reproduces the 1922 printing's misprints faithfully but marks them, e.g.
    # <span title="[sic] 'vilest qualities.'">vilest</span>. Use the editors' marked reading.
    raw = re.sub(r"<span[^>]*title=\"\[sic\] '([^']*)'\"[^>]*>[^<]*</span>",
                 lambda m: html.unescape(m.group(1)), html.unescape(raw).replace("&#91;", "[").replace("&#93;", "]"))
    raw = re.sub(r"<sup[^>]*>.*?</sup>", "", raw, flags=re.S)          # footnote markers
    # Page-number markers sit inside words that cross a printed page ("investiga<marker>tion"):
    # drop them whole, and break lines only at block boundaries, never at inline tags like the
    # small-caps <span> around "Self", or the words split apart.
    raw = re.sub(r'<span><span class="pagenum[^"]*"[^>]*>(?:<span[^>]*>[^<]*</span>)?</span></span>', "", raw)
    raw = re.sub(r"<br\s*/?>|</p>|</div>|</dd>|</dl>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("​", "").replace("\xa0", " ")
    text = re.sub(r"\[\s*\d+\s*\]", "", text)                          # page numbers like [ 17 ]
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def besant_chapter(chapter: int) -> tuple[dict[int, str], dict[int, str]]:
    """({verse: Sanskrit}, {verse: English}) from one chapter page of Besant's edition.

    Devanagari lines accumulate until one ends "॥ N ॥", which closes verse N's Sanskrit (a
    speaker line such as "अर्जुन उवाच ।" stays with the verse it introduces). The Latin lines
    after it, up to the next Devanagari line, are Besant's English. When Besant renders several
    verses as one paragraph, each of those verses gets that paragraph. Parsing stops at the
    chapter's closing colophon ("इति श्रीमद्भगवद्गीतासु..."), which ends "॥ <chapter> ॥" and
    would otherwise overwrite that verse.
    """
    sanskrit: dict[int, str] = {}
    english: dict[int, str] = {}
    pending: list[int] = []          # verses whose Sanskrit has closed but whose English hasn't come yet
    deva: list[str] = []
    buffer: list[str] = []

    def flush():
        if pending and buffer:
            para = re.sub(r"\s+", " ", " ".join(buffer)).strip()
            para = re.sub(r"\s*\(?(\d+)(?:\s*[-–,]\s*\d+)*\)?\s*$", "", para)  # "(47)", "(5-6)", "(23", "32)"
            para = re.sub(r"\s+([,.;:!?])", r"\1", para)                        # "Eternal ." -> "Eternal."
            for v in pending:
                english.setdefault(v, para)
            pending.clear()
        buffer.clear()

    for line in page_lines(chapter):
        if DEVANAGARI.search(line):
            if "इति श्रीमद्भगवद्गीतासु" in line or "श्रीमद्भगवद्गीतासूपनिषत्सु" in line:
                break
            if buffer:
                flush()
            deva.append(line)
            marker = re.search(r"॥\s*([०-९0-9]+)\s*॥", line)
            if marker:
                v = int(marker.group(1).translate(DEV_DIGITS))
                verse = re.sub(r"॥\s*[०-९0-9]+\s*॥", "", " ".join(deva))
                sanskrit.setdefault(v, re.sub(r"\s+", " ", verse).strip())
                deva = []
                pending.append(v)
        elif pending:
            buffer.append(line)
    flush()
    return sanskrit, english


def _skeleton(s: str) -> str:
    """Letters only, with gita/gita's spelling conventions mapped to standard IAST, so two
    romanizations can be compared for 'same verse?' rather than for style."""
    s = s.lower().replace("’", "").replace("'", "")
    for a, b in (("ṣh", "ṣ"), ("śh", "ś"), ("chh", "c"), ("ch", "c"), ("ṛi", "ṛ"), ("ṁ", "ṃ"), ("oṃ", "")):
        s = s.replace(a, b)
    return re.sub(r"[^a-zāīūṛṝḷḹēōṃḥṅñṭḍṇśṣ]", "", s)


def matching_iast(iast: dict[str, str], sanskrit: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """gita/gita's readable line where it is the same verse as Besant's Sanskrit, otherwise a
    line generated from that Sanskrit. Returns (lines, keys that were regenerated)."""
    import difflib

    from indic_transliteration import sanscript
    out, regenerated = {}, []
    for key, deva in sanskrit.items():
        generated = sanscript.transliterate(deva, sanscript.DEVANAGARI, sanscript.IAST)
        same = difflib.SequenceMatcher(None, _skeleton(generated), _skeleton(iast.get(key, ""))).ratio() >= 0.9
        out[key] = iast[key] if same else generated
        if not same:
            regenerated.append(key.rsplit(" ", 1)[1])
    return out, regenerated


def main() -> None:
    # gita/gita supplies the romanized (IAST) line and the verse counts; its Devanagari has some
    # encoding damage (fused speaker tags, misplaced vowel signs, truncated verses), so the
    # Devanagari shown on screen comes from Besant's own edition on Wikisource instead.
    data = requests.get(VERSES, timeout=60).json()
    counts: dict[int, int] = {}
    sanskrit, iast, english = {}, {}, {}
    for rec in data:
        c, v = rec["chapter_number"], rec["verse_number"]
        counts[c] = max(counts.get(c, 0), v)
        iast[f"{WORK} {c}:{v}"] = re.sub(r"\s+", " ", rec["transliteration"]).strip()
    assert len(iast) == 701, len(iast)

    missing = []
    for c in range(1, 19):
        chapter_sa, chapter_en = besant_chapter(c)
        for v in range(1, counts[c] + 1):
            key = f"{WORK} {c}:{v}"
            if v in chapter_en and v in chapter_sa:
                english[key], sanskrit[key] = chapter_en[v], chapter_sa[v]
            else:
                missing.append(f"{c}:{v}")
    assert not any("Upanishads of the Bhagavad-Gita" in e for e in english.values()), "a colophon leaked into a verse"
    correct_sanskrit(sanskrit)
    iast, regenerated = matching_iast(iast, sanskrit)
    print(f"IAST regenerated from the Devanagari for {len(regenerated)} verses: {', '.join(regenerated)}")
    works = [{"name": WORK, "chapters": [counts[c] for c in range(1, 19)]}]

    OUT.mkdir(parents=True, exist_ok=True)
    for name, title, verses in (
        ("besant", "The Bhagavad-Gita, translated by Annie Besant (4th edition, 1922)", english),
        ("sanskrit", "Bhagavad Gita, Sanskrit (Devanagari)", sanskrit),
        ("iast", "Bhagavad Gita, Sanskrit transliterated (IAST)", iast),
    ):
        (OUT / f"{name}.json").write_text(json.dumps(
            {"id": name, "title": title, "works": works, "verses": verses},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(english)} of 701 verses have Besant's Sanskrit and English; missing: {missing or 'none'} -> {OUT}")


if __name__ == "__main__":
    main()
