"""Render a post as a calm vertical Reel: a moving scene, the verse revealed line by line, original audio.

Everything here is generated from code, so there are no stock-footage or music licenses to track.

Each tradition picks a scene (`style.scene` in tradition.json) that matches its profile picture: an
emblem near the top (a sun, a crescent moon, a lotus) and a slowly moving background around it (light
rays with drifting dust, twinkling stars, rising embers). Over it go a header naming the plan and day,
the verse (the original language first, for traditions that show it) appearing a word at a time at
reading pace, a small copy of the emblem, and the reference. Text stays in the band Instagram leaves
clear: below the Reels header, above the caption and buttons, and inside the 4:5 feed crop and the
3:4 profile-grid crop.
"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"

W, H = 1080, 1920          # 9:16 Reel
BG_SCALE = 2               # backgrounds are drawn at half size (soft light upscales cleanly) and scaled up
FPS = 30
SR = 44100
CREAM = (255, 251, 244)

# The original look, kept as the "glow" scene for traditions that don't pick one:
# (top color, bottom color, glow colors)
PALETTES = [
    ("#141c33", "#a8746a", ["#f3c98b", "#e79a8c", "#7d6aa6"]),   # dawn
    ("#0c2233", "#2f6d78", ["#8fd3d1", "#5aa1c2", "#c9e6e4"]),   # ocean
    ("#18241f", "#5e7f63", ["#e9e2c3", "#9cc39a", "#c7d7a8"]),   # sage
    ("#1f1834", "#6f6194", ["#e6b3cc", "#b4a4e0", "#f1d6e3"]),   # lavender
    ("#1b1a2c", "#8c5a6e", ["#f2b38f", "#d27f8f", "#6c7fb8"]),   # dusk
]
# Per scene: (sky top, sky bottom, [main light, particles, highlight]); one per day in rotation.
SCENE_PALETTES = {
    "sunrise": [("#141634", "#9a5a4a", ["#ffcf85", "#ffe9c4", "#fff0cc"]),
                ("#1a1838", "#a8684c", ["#ffd690", "#ffeccc", "#fff3d6"]),
                ("#102037", "#80606e", ["#ffdca0", "#fff0d6", "#fff6e2"])],
    "night": [("#050b1c", "#123046", ["#f3d98f", "#dfe9f7", "#eef3fa"]),
              ("#04120f", "#154a40", ["#efd893", "#e2f3ec", "#eef8f3"]),
              ("#0a0a1d", "#2a2d5a", ["#f0d48e", "#e6e4f7", "#f1effa"])],
    "dusk": [("#170c1c", "#8a3d27", ["#ffb35c", "#ffb066", "#ffe0a8"]),
             ("#1b0d16", "#9c4f2c", ["#ffbf6a", "#ffb870", "#ffe6b8"]),
             ("#120e1e", "#6e3550", ["#ffae66", "#ffa86a", "#ffd9b0"])],
}
SCENES = ("glow", *SCENE_PALETTES)
AUDIO_STYLES = ("ambient", "drone", "nature", "silent")
# Integrated loudness targets (LUFS). Music on Instagram is mastered around -14; broadcast TV is -23.
# With no voice over it, background music this calm should sit well under both.
LOUDNESS = {"ambient": -24.0, "drone": -26.0, "nature": -28.0}

# Layout. Full-screen Reels cover about the top 220px and bottom 400px; the feed shows a 4:5 crop
# (y 285 to 1635) and the profile grid a 3:4 crop (y 240 to 1680).
EMBLEM_Y, EMBLEM_R = 345, 56          # centre and radius of the scene's emblem
HEADER_Y = 428                        # top of the header
BOX_TOP, BOX_BOTTOM = 530, 1530       # the verse, the small emblem and the reference are centred here
TEXT_W = 860
DIVIDER_H = 96                        # from the last verse line to the reference
REF_H = 40

# Timing (seconds)
T_HEADER, T_FIRST = 0.3, 1.0
FADE_IN, RISE, BLUR_IN = 0.9, 14, 4.0  # each word fades in over this long, rising this many pixels into
                                       # place and coming into focus from this blur; fades overlap so
                                       # three or four words are always in motion
ORIGINAL_STEP = 0.14                  # the original appears word by word at an easy, steady pace
READ_PACE = 0.27                      # per English word: unhurried reading speed
# Natural pauses, as a reader would take them: a beat at , ; : and dashes, a longer one at the end
# of a sentence, and a beat at the Sanskrit danda (।, the end of a half-verse).
PAUSE_COMMA, PAUSE_STOP, PAUSE_DANDA = 0.7, 1.2, 0.6
MIN_SECONDS, MAX_SECONDS = 12.0, 30.0


@dataclass
class Card:
    """Everything that ends up on screen and in the soundtrack for one post."""
    text: str                       # the translation shown on screen
    ref: str                        # reader-facing reference, e.g. "Psalm 23:1"
    label: str                      # "Day 3 of 40"; the header falls back to it
    seed: int                       # makes background and audio deterministic per post
    original: str | None = None     # original-language text shown above the translation
    original_font: str = "Lora-Variable.ttf"
    original_direction: str = "ltr"         # "rtl" for Arabic and Hebrew
    original_language: str | None = None    # e.g. "ar", "sa": guides shaping
    original_line_height: float = 1.85      # Arabic needs room for vowel marks; Devanagari less
    palettes: list | None = None    # overrides the scene's palettes
    audio: str = "ambient"          # one of AUDIO_STYLES
    header: str = ""                # "Gospel of Mark · Day 3 of 40"
    scene: str = "glow"             # one of SCENES


def card_for(slot) -> Card:
    """The Card for a scheduled post, with the tradition's look and sound applied."""
    from .caption import original_text, overlay_text
    body, ref, label = overlay_text(slot)
    style = slot.tradition.get("style", {})
    orig = style.get("original") or {}
    title = slot.plan["title"].split(":")[0].strip()     # "Ramadan: Through the Quran..." -> "Ramadan"
    return Card(
        text=body, ref=ref, label=label, seed=slot.seed,
        original=original_text(slot) if orig.get("on_screen", True) else None,
        original_font=orig.get("font", "Lora-Variable.ttf"),
        original_direction=orig.get("direction", "ltr"),
        original_language=orig.get("language"),
        original_line_height=orig.get("line_height", 1.85),
        palettes=style.get("palettes"),
        audio=style.get("audio", "ambient"),
        header=f"{title} · Day {slot.day} of {slot.total}",
        scene=style.get("scene", "glow"),
    )


def duration_for(text: str) -> float:
    """Rough reading time; kept for callers that only have the text. Reels use `timeline`."""
    words = len(text.split())
    return float(min(MAX_SECONDS, max(MIN_SECONDS, 8.0 + words * 0.33)))


# ---------------------------------------------------------------- text

def _font(name: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    return f


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, **shaping) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font, **shaping) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _shaping(card: Card) -> dict:
    """Arguments that make Pillow shape the original script properly (joined Arabic letters,
    right-to-left order, Devanagari conjuncts). That needs libraqm; without it the text would
    render as disconnected, reversed letters, so refuse rather than post that."""
    if card.original_direction == "ltr" and not card.original_language:
        return {}
    if not features.check("raqm"):
        raise RuntimeError("This tradition shows complex-script text, which needs Pillow with libraqm "
                           "(install libfribidi0; Pillow's wheels bundle the rest).")
    args = {"direction": card.original_direction}
    if card.original_language:
        args["language"] = card.original_language
    return args


def _spaced(text: str) -> str:
    return " ".join(text.upper())       # letter-spaced capitals for labels


def layout(card: Card) -> dict:
    """Pick the largest type size at which the verse fits its band.

    With an original text, both blocks scale together: the original a little larger (Arabic and
    Devanagari read small at the same point size) with taller lines for vowel marks. `fits` is
    False if even the smallest size overflows; tests check every scheduled post against it.
    """
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    room = BOX_BOTTOM - BOX_TOP - DIVIDER_H - REF_H
    shaping = _shaping(card) if card.original else {}
    for size in range(78, 26, -2):
        L = {"size": size, "font": _font("Lora-Variable.ttf", size, "Medium"), "shaping": shaping}
        L["lines"] = _wrap(draw, card.text, L["font"], TEXT_W)
        L["line_h"] = int(size * 1.42)
        L["block_h"] = len(L["lines"]) * L["line_h"]
        if card.original:
            o_size = int(size * 1.12)
            L["o_font"] = _font(card.original_font, o_size)
            L["o_lines"] = _wrap(draw, card.original, L["o_font"], TEXT_W, **shaping)
            L["o_line_h"] = int(o_size * card.original_line_height)
            L["o_gap"] = int(size * 1.2)
            L["block_h"] += len(L["o_lines"]) * L["o_line_h"] + L["o_gap"]
        L["fits"] = L["block_h"] <= room
        if L["fits"]:
            break
    return L


def _header(card: Card, draw: ImageDraw.ImageDraw) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """One letter-spaced line if it fits ("GOSPEL OF MARK · DAY 3 OF 40"), else plan over day."""
    text = card.header or card.label
    for size in (30, 28):
        font = _font("Poppins-Medium.ttf", size)
        if draw.textlength(_spaced(text), font=font) <= TEXT_W:
            return font, [_spaced(text)]
    font = _font("Poppins-Medium.ttf", 27)
    return font, [_spaced(part) for part in text.split(" · ", 1)]


@dataclass
class Line:
    """One piece of text on screen (a word, or the whole header or reference) and when it appears."""
    text: str
    font: ImageFont.FreeTypeFont
    y: int
    t_in: float
    fill: tuple
    shaping: dict
    x: float | None = None     # where the text starts; None centres it


@dataclass
class Timeline:
    lines: list[Line]          # header, then the verse word by word, then the reference
    ornament_y: int            # centre of the small emblem between the verse and the reference
    ornament_t: float
    duration: float
    cover: float               # a moment with everything on screen (grid cover, preview still)


def _units(text: str, split_dashes: bool) -> list[tuple[int, int]]:
    """Character spans of the pieces that appear one at a time: each word, and in English each half
    of words joined by a dash ("confidence—but"), so the pause can fall at the dash."""
    spans = []
    for m in re.finditer(r"\S+", text):
        start, end = m.span()
        if split_dashes:
            for d in re.finditer("—", text[start:end]):
                cut = start + d.end()
                if cut < end:
                    spans.append((start, cut))
                    start = cut
        spans.append((start, end))
    return spans


def _unit_x(draw, text: str, span: tuple[int, int], font, origin_x: float, advance: float, shaping: dict) -> float:
    """Where a piece of a centred line starts. Arabic runs right to left, so its first word sits at
    the right end of the line."""
    if shaping.get("direction") == "rtl":
        return origin_x + advance - draw.textlength(text[:span[1]], font=font, **shaping)
    return origin_x + (draw.textlength(text[:span[0]], font=font, **shaping) if span[0] else 0)


def _english_step(piece: str) -> float:
    """Time until the next piece: reading pace, plus a pause at punctuation (closing quotes aside)."""
    end = piece.rstrip("”’\"')]")
    return READ_PACE + (PAUSE_STOP if end.endswith((".", "!", "?")) else
                        PAUSE_COMMA if end.endswith((",", ";", ":", "—")) else 0.0)


def _original_step(piece: str) -> float:
    return ORIGINAL_STEP + (PAUSE_DANDA if piece.endswith(("।", "॥")) else 0.0)


def timeline(card: Card) -> Timeline:
    """Where every word goes and when it appears, and how long the Reel runs. Words stay once
    they're in; the Reel ends on the whole verse."""
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    L = layout(card)
    lines: list[Line] = []
    h_font, h_lines = _header(card, draw)
    for i, text in enumerate(h_lines):
        lines.append(Line(text, h_font, HEADER_Y + i * 38, T_HEADER, CREAM + (205,), {}))

    total = L["block_h"] + DIVIDER_H + REF_H
    y = BOX_TOP + max(0, (BOX_BOTTOM - BOX_TOP - total) // 2)
    t = T_FIRST

    def words_of(text: str, font, fill: tuple, shaping: dict, step, split_dashes: bool = False) -> None:
        nonlocal t
        advance = draw.textlength(text, font=font, **shaping)
        for span in _units(text, split_dashes):
            piece = text[span[0]:span[1]]
            x = _unit_x(draw, text, span, font, (W - advance) / 2, advance, shaping)
            lines.append(Line(piece, font, y, t, fill, shaping, x))
            t += step(piece)

    for text in L.get("o_lines", []):
        words_of(text, L["o_font"], CREAM + (255,), L["shaping"], _original_step)
        y += L["o_line_h"]
    if card.original:
        y += L["o_gap"]
        t += 0.4
    for text in L["lines"]:
        # the translation steps back slightly when it sits under the original
        words_of(text, L["font"], CREAM + (232 if card.original else 255,), {}, _english_step, split_dashes=True)
        y += L["line_h"]
    ornament_y, ornament_t = y + DIVIDER_H // 2 - 8, t + 0.1
    lines.append(Line(_spaced(card.ref), _font("Poppins-Medium.ttf", 36), y + DIVIDER_H - 8, t + 0.45,
                      CREAM + (220,), {}))

    # Long passages reveal faster so the whole thing fits in 30 seconds with time to finish reading.
    t_ref = lines[-1].t_in
    latest_ref = MAX_SECONDS - 4.0 - FADE_IN
    if t_ref > latest_ref:
        k = (latest_ref - T_FIRST) / (t_ref - T_FIRST)
        for ln in lines:
            if ln.t_in >= T_FIRST:
                ln.t_in = T_FIRST + (ln.t_in - T_FIRST) * k
        ornament_t = T_FIRST + (ornament_t - T_FIRST) * k
        t_ref = lines[-1].t_in
    revealed = t_ref + FADE_IN
    hold = min(7.0, max(4.0, 3.0 + 0.06 * len(card.text.split())))
    duration = min(MAX_SECONDS, max(MIN_SECONDS, revealed + hold))
    return Timeline(lines, ornament_y, ornament_t, duration, min(revealed + 0.5, duration - 0.5))


def _seconds(card: Card) -> float:
    return timeline(card).duration


def cover_time(card: Card) -> float:
    """The moment used as the Reel's cover in the profile grid, and for preview stills: once the
    whole verse is on screen. (Instagram's default is the first frame, before any text appears.)"""
    return timeline(card).cover


# ---------------------------------------------------------------- pieces drawn on top of the scene

PAD = 44                    # room around each piece for its shadow


def _with_shadow(img: Image.Image, blur: float = 9, strength: float = 0.5) -> Image.Image:
    """A soft dark halo under light text and shapes, so they read on any part of the scene."""
    alpha = img.split()[3].filter(ImageFilter.GaussianBlur(blur)).point(lambda a: int(a * strength))
    shadow = Image.new("RGBA", img.size, (10, 8, 20, 0))
    shadow.putalpha(alpha)
    return Image.alpha_composite(shadow, img)


@dataclass
class Piece:
    image: Image.Image      # RGBA, cropped to the piece and its shadow
    x: int
    y: int
    t_in: float


def _text_piece(line: Line) -> Piece:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    advance = probe.textlength(line.text, font=line.font, **line.shaping)
    left, top, right, bottom = probe.textbbox((0, 0), line.text, font=line.font, **line.shaping)
    img = Image.new("RGBA", (int(right - left) + 2 * PAD, int(bottom - top) + 2 * PAD), CREAM + (0,))
    ImageDraw.Draw(img).text((PAD - left, PAD - top), line.text, font=line.font, fill=line.fill, **line.shaping)
    origin_x = line.x if line.x is not None else (W - advance) / 2     # centred lines: by advance width
    return Piece(_with_shadow(img), round(origin_x + left - PAD), round(line.y + top - PAD), line.t_in)


def _petal(cx: float, cy: float, angle: float, length: float, half_w: float) -> list[tuple[float, float]]:
    """An almond-shaped lotus petal from its base, rotated `angle` degrees from upright."""
    a = math.radians(angle)
    left, right = [], []
    for i in range(41):
        t = i / 40
        w = half_w * math.sin(math.pi * t ** 0.8) ** 0.85
        for side, out in ((-1, left), (1, right)):
            x, y = side * w, length * t
            out.append((cx + x * math.cos(a) + y * math.sin(a), cy + x * math.sin(a) - y * math.cos(a)))
    return left + right[::-1]


def _star_points(cx: float, cy: float, r: float, points: int = 8) -> list[tuple[float, float]]:
    """The Islamic eight-pointed star (two overlapping squares) as one outline."""
    inner = r * math.cos(math.radians(45)) / math.cos(math.radians(22.5))
    return [(cx + (r if i % 2 == 0 else inner) * math.cos(math.radians(-90 + i * 180 / points)),
             cy + (r if i % 2 == 0 else inner) * math.sin(math.radians(-90 + i * 180 / points)))
            for i in range(points * 2)]


def _emblem(scene: str, r: float) -> Image.Image | None:
    """The scene's symbol at radius `r` px, matching the account's profile picture."""
    if scene not in SCENE_PALETTES:
        return None
    ss = 4                                   # drawn at 4x and scaled down, for clean edges
    R, size = r * ss, int(2.6 * r * ss)
    c = size / 2
    img = Image.new("RGBA", (size, size), CREAM + (0,))
    d = ImageDraw.Draw(img)
    fill = CREAM + (255,)
    if scene == "sunrise":
        d.ellipse([c - 0.4 * R, c - 0.4 * R, c + 0.4 * R, c + 0.4 * R], fill=fill)
        for i in range(16):
            a = 2 * math.pi * i / 16
            r0, r1, half = 0.56 * R, (1.0 if i % 2 == 0 else 0.8) * R, 0.065 * R
            px, py = -math.sin(a), math.cos(a)
            d.polygon([(c + r0 * math.cos(a) + half * px, c + r0 * math.sin(a) + half * py),
                       (c + r1 * math.cos(a), c + r1 * math.sin(a)),
                       (c + r0 * math.cos(a) - half * px, c + r0 * math.sin(a) - half * py)], fill=fill)
    elif scene == "night":
        d.ellipse([c - 0.18 * R - 0.78 * R, c - 0.78 * R, c - 0.18 * R + 0.78 * R, c + 0.78 * R], fill=fill)
        cx, cy, cr = c + 0.12 * R, c - 0.17 * R, 0.68 * R
        d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=CREAM + (0,))
        d.polygon(_star_points(c + 0.62 * R, c - 0.42 * R, 0.24 * R), fill=fill)
    elif scene == "dusk":
        edge = (122, 63, 52, 150)
        base_y = c + 0.62 * R
        for layer in ([(-66, 0.7, 0.2), (66, 0.7, 0.2)], [(-38, 0.95, 0.22), (38, 0.95, 0.22)],
                      [(-16, 1.08, 0.22), (16, 1.08, 0.22)], [(0, 1.18, 0.24)]):
            for ang, ln, hw in layer:
                pts = _petal(c, base_y, ang, ln * R, hw * R)
                d.polygon(pts, fill=fill)
                d.line(pts + [pts[0]], fill=edge, width=max(2, int(0.035 * R)), joint="curve")
    return img.resize((size // ss, size // ss), Image.LANCZOS)


def _ornament(card: Card, y: int, t_in: float) -> Piece:
    """The small emblem between the verse and the reference (a short rule for the plain scene)."""
    small = _emblem(card.scene, 23)
    if small is None:
        small = Image.new("RGBA", (72, 4), CREAM + (150,))
    img = Image.new("RGBA", (small.width + 2 * PAD, small.height + 2 * PAD), CREAM + (0,))
    img.alpha_composite(small, (PAD, PAD))
    img = _with_shadow(img, blur=8, strength=0.5)
    return Piece(img, (W - img.width) // 2, y - img.height // 2, t_in)


def compose(card: Card) -> tuple[list[Piece], Timeline]:
    """Every piece of text (and the small emblem) as an image with its place and entry time."""
    tl = timeline(card)
    pieces = [_text_piece(line) for line in tl.lines]
    pieces.insert(len(pieces) - 1, _ornament(card, tl.ornament_y, tl.ornament_t))
    return pieces, tl


def _lattice() -> Image.Image:
    """A faint field of eight-pointed stars, the classic Islamic geometric tiling, strongest at the
    edges and fading out behind the text."""
    layer = Image.new("RGBA", (W, H), CREAM + (0,))
    d = ImageDraw.Draw(layer)
    step, r = 132, 52
    for row in range(-1, H // step + 2):
        for col in range(-1, W // step + 2):
            cx, cy = col * step + (step / 2 if row % 2 else 0), row * step
            pts = _star_points(cx, cy, r)
            d.line(pts + [pts[0]], fill=CREAM + (255,), width=2)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H * 0.52) / H * 1.1) ** 2)
    fade = np.clip((dist - 0.34) / 0.22, 0, 1) * 0.07
    alpha = (np.asarray(layer.split()[3], np.float32) * fade).astype(np.uint8)
    layer.putalpha(Image.fromarray(alpha))
    return layer


def motif(card: Card) -> Image.Image:
    """The still part of the scene: its emblem, plus the Quran scene's star tiling and the Gita
    scene's rings around the lotus. Drawn at full resolution so it stays crisp."""
    layer = Image.new("RGBA", (W, H), CREAM + (0,))
    if card.scene == "night":
        layer = Image.alpha_composite(layer, _lattice())
    if card.scene == "dusk":
        d = ImageDraw.Draw(layer)
        for rr, alpha in ((EMBLEM_R * 1.3, 60), (EMBLEM_R * 1.45, 30)):
            d.ellipse([W / 2 - rr, EMBLEM_Y - rr, W / 2 + rr, EMBLEM_Y + rr], outline=CREAM + (alpha,), width=2)
    emblem = _emblem(card.scene, EMBLEM_R)
    if emblem is not None:
        holder = Image.new("RGBA", (emblem.width + 2 * PAD, emblem.height + 2 * PAD), CREAM + (0,))
        holder.alpha_composite(emblem, (PAD, PAD))
        holder = _with_shadow(holder, blur=10, strength=0.45)
        layer.alpha_composite(holder, (W // 2 - holder.width // 2, EMBLEM_Y - holder.height // 2))
    return layer


def make_overlay(card: Card, path: Path) -> None:
    """Everything on top of the scene, fully revealed, as one transparent PNG."""
    img = motif(card)
    pieces, _ = compose(card)
    for p in pieces:
        img.alpha_composite(p.image, (p.x, p.y))
    img.save(path)


# ---------------------------------------------------------------- scenes (moving backgrounds)

def _hex(c: str) -> np.ndarray:
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


class Background:
    """The plain "glow" scene: a shifting two-color gradient with soft glowing blobs that drift
    and breathe."""

    def __init__(self, seed: int, palettes: list | None = None):
        rng = np.random.default_rng(seed)
        palettes = palettes or PALETTES
        top, bottom, glows = palettes[seed % len(palettes)]
        self.top, self.bottom = _hex(top), _hex(bottom)
        self.w, self.h = W // BG_SCALE, H // BG_SCALE
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        self.xx, self.yy = xx / self.w, yy / self.h
        self.blobs = []
        for i in range(6):
            self.blobs.append(dict(
                color=_hex(glows[i % len(glows)]),
                cx=rng.uniform(0.15, 0.85), cy=rng.uniform(0.1, 0.9),
                ax=rng.uniform(0.08, 0.2), ay=rng.uniform(0.05, 0.15),
                fx=rng.uniform(0.02, 0.05), fy=rng.uniform(0.02, 0.05),
                ph=rng.uniform(0, 2 * math.pi),
                r=rng.uniform(0.18, 0.32), k=rng.uniform(0.18, 0.32),
            ))
        d = np.sqrt((self.xx - 0.5) ** 2 + ((self.yy - 0.5) * 1.2) ** 2)
        self.vignette = (1.0 - 0.55 * np.clip(d / 0.75, 0, 1) ** 2)[..., None]

    def frame(self, t: float) -> np.ndarray:
        shift = 0.08 * math.sin(t * 0.12)
        g = np.clip(self.yy + shift, 0, 1)[..., None]
        img = self.top * (1 - g) + self.bottom * g
        for b in self.blobs:
            cx = b["cx"] + b["ax"] * math.sin(2 * math.pi * b["fx"] * t + b["ph"])
            cy = b["cy"] + b["ay"] * math.cos(2 * math.pi * b["fy"] * t + b["ph"] * 0.7)
            breathe = 1.0 + 0.12 * math.sin(t * 0.35 + b["ph"])
            d2 = (self.xx - cx) ** 2 + ((self.yy - cy) * (H / W)) ** 2
            glow = np.exp(-d2 / (2 * (b["r"] * breathe) ** 2))[..., None] * b["k"]
            img = 1 - (1 - img) * (1 - glow * b["color"])   # screen blend
        img = img * self.vignette * 0.86   # keep it dim enough for white text
        return (np.clip(img, 0, 1) * 255).astype(np.uint8)


class Scene:
    """A tradition's moving background, drawn small (it's all soft light) and scaled up by ffmpeg.

    sunrise: warm dawn sky, the sun's glow at the emblem, slow rays turning, dust drifting in them.
    night:   a dark sky with moon-glow at the emblem and stars twinkling at their own pace.
    dusk:    plum to saffron, warm lamp-light from below and embers rising and fading.
    """

    def __init__(self, card: Card):
        self.kind = card.scene
        palettes = card.palettes or SCENE_PALETTES[card.scene]
        top, bottom, glows = palettes[card.seed % len(palettes)]
        self.light, self.particle, self.highlight = (_hex(glows[i % len(glows)]) for i in range(3))
        self.w, self.h = W // BG_SCALE, H // BG_SCALE
        rng = np.random.default_rng(card.seed)
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        self.xx, self.yy = xx / self.w, yy / self.h
        aspect = H / W
        ex, ey = 0.5, EMBLEM_Y / H                         # the emblem, where the main light sits
        self.dx, self.dy = self.xx - ex, (self.yy - ey) * aspect
        self.dist = np.sqrt(self.dx ** 2 + self.dy ** 2)
        g = np.clip(self.yy, 0, 1)[..., None]
        sky = _hex(top) * (1 - g) + _hex(bottom) * g

        def glow(cx, cy, r, k, color):
            d2 = (self.xx - cx) ** 2 + ((self.yy - cy) * aspect) ** 2
            return np.exp(-d2 / (2 * r * r))[..., None] * k * color

        if self.kind == "sunrise":
            lights = [glow(ex, ey, 0.24, 0.42, self.light), glow(0.5, 1.08, 0.5, 0.45, self.light)]
            self.theta = np.arctan2(self.dy, self.dx)
            self.count, self.drift = 30, (0.004, 0.012)
        elif self.kind == "night":
            lights = [glow(ex, ey, 0.22, 0.42, self.light), glow(0.5, 1.1, 0.5, 0.12, self.highlight)]
            self.count, self.drift = 120, (0.0, 0.0)
        else:  # dusk
            lights = [glow(0.5, 1.08, 0.48, 0.62, self.light), glow(ex, ey, 0.16, 0.30, self.highlight)]
            self.count, self.drift = 46, (0.025, 0.06)
        for light in lights:
            sky = 1 - (1 - sky) * (1 - light)                # screen blend
        vignette = 1.0 - 0.5 * np.clip(np.sqrt((self.xx - 0.5) ** 2 + ((self.yy - 0.5) * 1.2) ** 2) / 0.78, 0, 1) ** 2
        self.base = sky * vignette[..., None] * 0.9

        # particles: dust motes, stars or embers
        n = self.count
        self.px, self.py = rng.uniform(0.04, 0.96, n), rng.uniform(0.02, 0.98, n)
        if self.kind == "night":                              # keep the moon's patch of sky clear
            keep = np.sqrt((self.px - ex) ** 2 + ((self.py - ey) * aspect) ** 2) > 0.16
            self.px, self.py = self.px[keep], self.py[keep]
        n = len(self.px)
        # sizes in full-resolution pixels: stars small and sharp, dust and embers a little softer
        full = {"night": (1.3, 2.6), "sunrise": (2.0, 4.0), "dusk": (2.2, 4.4)}[self.kind]
        self.size = rng.uniform(*full, n) / BG_SCALE
        self.bright = rng.uniform(0.35, 1.0, n)
        self.rate = rng.uniform(0.15, 0.55, n)                # twinkle / flicker speed (Hz)
        self.phase = rng.uniform(0, 2 * math.pi, n)
        self.speed = rng.uniform(*self.drift, n) if self.drift[1] else np.zeros(n)
        if self.kind == "night":
            in_text = (np.abs(self.px - 0.5) < 0.42) & (self.py > HEADER_Y / H - 0.02) & (self.py < BOX_BOTTOM / H)
            self.bright = np.where(in_text, self.bright * 0.25, self.bright)
        self.sway = rng.uniform(0.004, 0.02, n)

    def _splat(self, img: np.ndarray, x: float, y: float, sigma: float, amount: np.ndarray) -> None:
        """Add a small soft dot (gaussian) at (x, y) in frame fractions."""
        px, py = x * self.w, y * self.h
        r = int(math.ceil(sigma * 3))
        x0, x1 = max(0, int(px) - r), min(self.w, int(px) + r + 2)
        y0, y1 = max(0, int(py) - r), min(self.h, int(py) + r + 2)
        if x0 >= x1 or y0 >= y1:
            return
        gx = np.exp(-((np.arange(x0, x1) - px) ** 2) / (2 * sigma ** 2))
        gy = np.exp(-((np.arange(y0, y1) - py) ** 2) / (2 * sigma ** 2))
        patch = (gy[:, None] * gx[None, :])[..., None] * amount
        img[y0:y1, x0:x1] = 1 - (1 - img[y0:y1, x0:x1]) * (1 - patch)

    def frame(self, t: float) -> np.ndarray:
        img = self.base.copy()
        if self.kind == "sunrise":
            rays = (0.5 + 0.5 * np.cos(12 * (self.theta - 0.025 * t))) ** 4
            reach = np.clip(1 - self.dist / 1.15, 0, 1) ** 1.6 * np.clip((self.dist - 0.06) / 0.12, 0, 1)
            light = (rays * reach * (0.065 + 0.012 * math.sin(t * 0.4)))[..., None] * self.light
            img = 1 - (1 - img) * (1 - light)
        for i in range(len(self.px)):
            if self.kind == "night":
                x, y = self.px[i], self.py[i]
                level = self.bright[i] * (0.55 + 0.45 * math.sin(2 * math.pi * self.rate[i] * t + self.phase[i]))
                color = self.particle
            elif self.kind == "sunrise":                      # dust drifting slowly up and across
                y = (self.py[i] - self.speed[i] * t) % 1.0
                x = self.px[i] + self.sway[i] * math.sin(0.3 * t + self.phase[i])
                level = 0.32 * self.bright[i] * (0.6 + 0.4 * math.sin(2 * math.pi * self.rate[i] * t + self.phase[i]))
                color = self.particle
            else:                                             # embers rising, flickering, fading out high up
                y = 1.05 - ((1.05 - self.py[i]) + self.speed[i] * t) % 1.1
                x = self.px[i] + self.sway[i] * math.sin(2 * math.pi * 0.12 * t + self.phase[i])
                fade = np.clip((y - 0.3) / 0.35, 0, 1) * (0.4 if BOX_TOP / H < y < BOX_BOTTOM / H and abs(x - 0.5) < 0.42 else 1.0)
                level = 0.75 * self.bright[i] * fade * (0.6 + 0.4 * math.sin(2 * math.pi * 1.5 * self.rate[i] * t + self.phase[i]))
                color = self.particle
            if level > 0.01:
                self._splat(img, x, y, self.size[i], level * color)
        return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def scene_for(card: Card):
    return Scene(card) if card.scene in SCENE_PALETTES else Background(card.seed, card.palettes)


# ---------------------------------------------------------------- audio

NOTE = {"C": 0, "D": 2, "Eb": 3, "E": 4, "F": 5, "G": 7, "A": 9, "Bb": 10}
PROGRESSIONS = [
    [0, 7, 9, 5],     # I  V  vi IV
    [0, 9, 5, 7],     # I  vi IV V
    [9, 5, 0, 7],     # vi IV I  V
    [0, 5, 9, 5],     # I  IV vi IV
]


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _chord(degree: int, key_root: int) -> list[int]:
    """Diatonic triad plus 7th/9th color in a major key, voiced around middle C."""
    scale = [0, 2, 4, 5, 7, 9, 11]
    idx = scale.index(degree % 12)
    tones = [scale[(idx + s) % 7] + 12 * ((idx + s) // 7) for s in (0, 2, 4, 6, 8)]
    base = 48 + key_root
    return [base + t for t in tones]


def _ambient(seed: int, dur: float, rng: np.random.Generator) -> np.ndarray:
    """Soft pad chords and sparse bell notes, dry (reverb is added in make_audio)."""
    key = list(NOTE.values())[seed % len(NOTE)]
    prog = PROGRESSIONS[seed % len(PROGRESSIONS)]
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2), dtype=np.float64)
    chord_len = 4.0

    # Pad: soft, slightly detuned sines, slow swells, overlapping chords
    num_chords = int(math.ceil(dur / chord_len)) + 1
    for c in range(num_chords):
        start = c * chord_len - 1.0
        notes = _chord(prog[c % len(prog)], key)
        voices = [notes[0] - 12, notes[0], notes[1], notes[2], notes[3]]
        s0, s1 = max(0, int(start * SR)), min(n, int((start + chord_len + 2.5) * SR))
        if s1 <= s0:
            continue
        tt = t[s0:s1] - start
        env = np.clip(tt / 1.6, 0, 1) * np.clip((chord_len + 2.5 - tt) / 2.0, 0, 1)
        env = env ** 1.5
        for vi, m in enumerate(voices):
            amp = 0.05 if vi == 0 else 0.035
            pan = 0.5 + 0.3 * math.sin(vi * 1.7)
            for cents in (-7, 0, 7):
                f = _freq(m + cents / 100)
                ph = rng.uniform(0, 2 * math.pi)
                wave_ = np.sin(2 * math.pi * f * tt + ph) + 0.18 * np.sin(4 * math.pi * f * tt + ph)
                sig = amp * env * wave_ / 3
                out[s0:s1, 0] += sig * (1 - pan)
                out[s0:s1, 1] += sig * pan

    # Sparse bell / soft piano notes from the major pentatonic
    penta = [0, 2, 4, 7, 9]
    tnow = 1.5
    while tnow < dur - 2.5:
        m = 72 + key + penta[rng.integers(0, 5)] + (12 if rng.random() < 0.2 else 0)
        f = _freq(m)
        s0 = int(tnow * SR)
        s1 = min(n, s0 + int(4.0 * SR))
        tt = t[s0:s1] - tnow
        env = np.exp(-tt * 1.3) * np.clip(tt / 0.01, 0, 1)
        tone = np.sin(2 * math.pi * f * tt) + 0.25 * np.sin(2 * math.pi * 2 * f * tt) * np.exp(-tt * 2) \
            + 0.08 * np.sin(2 * math.pi * 2.76 * f * tt) * np.exp(-tt * 3)
        vel = rng.uniform(0.04, 0.07)
        pan = rng.uniform(0.25, 0.75)
        out[s0:s1, 0] += vel * env * tone * (1 - pan)
        out[s0:s1, 1] += vel * env * tone * pan
        tnow += rng.choice([1.0, 1.5, 2.0, 2.0, 3.0])
    return out


def _drone(seed: int, dur: float, rng: np.random.Generator) -> np.ndarray:
    """A tanpura, the drone under Indian classical music: four strings (Pa, Sa, Sa, low Sa) plucked
    in a slow cycle, each a bright, buzzing tone whose brightness sweeps down as it rings (the
    jawari bridge), so the sound never stops. Now and then, softly, a temple bell."""
    n = int(dur * SR)
    out = np.zeros((n, 2))
    sa = 130.81 * 2 ** ((seed % 3) / 12)              # C, C# or D below middle C: usual tanpura pitches
    strings = [(sa * 3 / 4, 0.38), (sa, 0.56), (sa, 0.62), (sa / 2, 0.47)]   # (frequency, pan)
    ring = 6.5
    m = int(ring * SR)
    tt = np.arange(m) / SR
    centre = 2.5 + 4 * np.exp(-tt / 1.5)              # the jawari's bright band, kept low and gentle
    notes = []
    for f, pan in strings:
        tone = np.zeros(m)
        for h in range(1, 25):
            if h * f > 4000:
                break
            amp = h ** -1.15 * (0.25 + 0.6 * np.exp(-((h - centre) / 2.0) ** 2))
            tone += amp * np.sin(2 * math.pi * h * f * (1 + 0.0004 * h) * tt + rng.uniform(0, 2 * math.pi))
        env = np.exp(-tt / 4.0) * np.clip(tt / 0.025, 0, 1)      # a soft, fingertip pluck
        notes.append((tone * env, pan))
    t0, k = 0.15, 0
    while t0 < dur:
        note, pan = notes[k % 4]
        s0 = int(t0 * SR)
        s1 = min(n, s0 + m)
        gain = rng.uniform(0.85, 1.0)
        out[s0:s1, 0] += note[:s1 - s0] * (1 - pan) * gain
        out[s0:s1, 1] += note[:s1 - s0] * pan * gain
        t0 += 1.6 * (1.5 if k % 4 == 3 else 1.0)      # unhurried, with a breath after the low string
        k += 1
    t = np.arange(n) / SR
    for f, level in ((sa, 0.05), (sa / 2, 0.06)):     # a soft, steady hum under the plucks
        hum = level * np.sin(2 * math.pi * f * t) * (0.85 + 0.15 * np.sin(2 * math.pi * 0.07 * t))
        out[:, 0] += hum
        out[:, 1] += hum
    bell_at = rng.uniform(8.0, 12.0)
    while bell_at < dur - 2.0:
        fb = sa * 3                               # a low, warm bell, rarely
        s0 = int(bell_at * SR)
        bt = t[s0:] - bell_at
        bell = sum(a * np.exp(-bt / dk) * np.sin(2 * math.pi * fb * ratio * bt)
                   for ratio, a, dk in ((1.0, 1.0, 4.0), (2.0, 0.5, 2.5), (2.76, 0.35, 1.8), (5.4, 0.15, 0.9)))
        bell *= np.clip(bt / 0.004, 0, 1) * 0.12     # an accent, well under the drone
        pan = rng.uniform(0.3, 0.7)
        out[s0:, 0] += bell * (1 - pan)
        out[s0:, 1] += bell * pan
        bell_at += rng.uniform(18.0, 26.0)
    return out


def _nature(dur: float, rng: np.random.Generator) -> np.ndarray:
    """Soft wind with a faint rain hiss: no pitch, rhythm, or instruments, for traditions where
    many people avoid music. Shaped noise: a deep, slowly gusting band plus a quiet high band."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    freqs = np.fft.rfftfreq(n, 1 / SR)
    wind_shape = (freqs > 30) / np.maximum(freqs, 30) / np.sqrt(1 + (freqs / 700) ** 4)
    rain_shape = ((freqs > 2500) & (freqs < 8000)).astype(float)
    out = np.zeros((n, 2))
    for ch in range(2):
        wind = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * wind_shape, n)
        rain = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * rain_shape, n)
        wind /= np.std(wind) + 1e-9
        rain /= np.std(rain) + 1e-9
        gusts = 0.6 + 0.4 * np.sin(2 * math.pi * rng.uniform(0.03, 0.06) * t + rng.uniform(0, 2 * math.pi)) \
            * np.sin(2 * math.pi * rng.uniform(0.008, 0.015) * t + rng.uniform(0, 2 * math.pi))
        out[:, ch] = wind * gusts + 0.06 * rain
    return out


def _reverb(out: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Convolve with decaying noise (different per channel for width)."""
    n = len(out)
    ir_len = int(3.2 * SR)
    decay = np.exp(-np.arange(ir_len) / SR * 2.2)
    wet = np.zeros_like(out)
    size = 1 << int(math.ceil(math.log2(n + ir_len)))
    for ch in range(2):
        ir = rng.standard_normal(ir_len) * decay
        ir /= np.sqrt(np.sum(ir ** 2))
        wet[:, ch] = np.fft.irfft(np.fft.rfft(out[:, ch], size) * np.fft.rfft(ir, size), size)[:n]
    return out * 0.65 + wet * 0.6


def _k_weighting(n: int) -> np.ndarray:
    """|H| of the ITU-R BS.1770 K-weighting filter (the ear-like curve loudness meters use) at each
    rfft bin of an n-sample signal. The standard gives the coefficients for 48 kHz."""
    z1 = np.exp(-2j * np.pi * np.fft.rfftfreq(n, 1 / SR) / 48000)          # z^-1 at each frequency

    def biquad(b, a):
        return np.abs((b[0] + b[1] * z1 + b[2] * z1 ** 2) / (a[0] + a[1] * z1 + a[2] * z1 ** 2))
    shelf = biquad((1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585))
    high_pass = biquad((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621))
    return shelf * high_pass


def loudness(mix: np.ndarray) -> float:
    """Integrated loudness in LUFS (BS.1770 K-weighting, summed over both channels). It skips the
    standard's gating, which only matters for audio with long silences."""
    n, k = len(mix), _k_weighting(len(mix))
    power = sum(np.mean(np.fft.irfft(np.fft.rfft(mix[:, ch]) * k, n) ** 2) for ch in range(2))
    return -0.691 + 10 * math.log10(power + 1e-20)


def make_audio(seed: int, dur: float, path: Path, style: str = "ambient") -> None:
    if style not in AUDIO_STYLES:
        raise ValueError(f"audio style must be one of {AUDIO_STYLES}, not {style!r}")
    rng = np.random.default_rng(seed + 1000)
    n = int(dur * SR)
    if style == "silent":
        mix = np.zeros((n, 2))
    else:
        if style == "ambient":
            mix = _reverb(_ambient(seed, dur, rng), rng)
        elif style == "drone":
            mix = _reverb(_drone(seed, dur, rng), rng)
        else:
            mix = _nature(dur, rng)
        # Gentle low-pass in the frequency domain to keep it warm
        freqs = np.fft.rfftfreq(n, 1 / SR)
        gain = 1 / np.sqrt(1 + (freqs / (2400 if style == "drone" else 3500)) ** 4)
        for ch in range(2):
            mix[:, ch] = np.fft.irfft(np.fft.rfft(mix[:, ch]) * gain, n)
        # Fades and loudness (fades shrink to fit very short clips)
        fade_in, fade_out = min(int(1.5 * SR), n // 2), min(int(2.5 * SR), n // 2)
        mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
        mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
        mix *= 10 ** ((LOUDNESS[style] - loudness(mix)) / 20)
        peak = np.max(np.abs(mix))
        if peak > 0.7:                     # about -3 dBFS of headroom
            mix *= 0.7 / peak

    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- output

def render_still(card: Card, out_jpg: Path) -> Path:
    """Fast preview: the scene at the cover moment with everything revealed, no video or audio."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    overlay = out_jpg.with_suffix(".overlay.png")
    make_overlay(card, overlay)
    bg = scene_for(card)
    frame = Image.fromarray(bg.frame(cover_time(card))).resize((W, H), Image.BICUBIC).convert("RGBA")
    Image.alpha_composite(frame, Image.open(overlay)).convert("RGB").save(out_jpg, quality=88)
    overlay.unlink()
    return out_jpg


def _ease(a: float) -> float:
    a = min(1.0, max(0.0, a))
    return a * a * (3 - 2 * a)            # smoothstep: starts and lands softly


def _ease_out(a: float) -> float:
    a = min(1.0, max(0.0, a))
    return 1 - (1 - a) ** 3               # quick to start moving, settling gently into place


def _arriving(p: Piece, t: float) -> tuple[Image.Image, int, int]:
    """A piece partway through its entrance: partly faded in, a little low and a little soft. The
    rise is drawn to a fraction of a pixel, so the motion is smooth rather than stepping."""
    u = (t - p.t_in) / FADE_IN
    alpha, settle = _ease(u), _ease_out(u)
    img = p.image
    blur = BLUR_IN * (1 - settle)
    if blur > 0.25:                       # blurred premultiplied, so edges don't pick up stray color
        img = img.convert("RGBa").filter(ImageFilter.GaussianBlur(blur)).convert("RGBA")
    else:
        img = img.copy()
    img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
    dy = RISE * (1 - settle)
    whole = math.floor(dy)
    if dy - whole > 0.01:
        img = img.transform(img.size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, -(dy - whole)), Image.BICUBIC)
    return img, p.x, p.y + whole


def render_reel(card: Card, out_path: Path) -> Path:
    """Full Reel: 1080x1920 H.264 + AAC, 12 to 30 seconds depending on text length.

    Each frame is built here: the scene, then everything already in place (the emblem and every
    word that has landed), then the words still fading in, each rising a few pixels as it does.
    ffmpeg adds a little film grain (it prevents banding in the gradients) and encodes.
    """
    pieces, tl = compose(card)
    pieces.sort(key=lambda p: p.t_in)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg = scene_for(card)
    landed = motif(card)                      # grows as words finish fading in
    # A fixed, very fine noise breaks up banding in the gradients. Moving grain did the same but cost
    # bitrate every frame and smeared when Instagram re-compressed it; a still pattern costs nothing.
    dither = np.random.default_rng(card.seed).integers(-1, 2, (H, W, 1), dtype=np.int16)
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.wav"
        make_audio(card.seed, tl.duration, audio, card.audio)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-i", str(audio), "-map", "0:v", "-map", "1:a",
            # HD (BT.709) color, converted and tagged, so Instagram doesn't shift the colors
            "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            # high quality, with extra bits for dark, smooth gradients (aq-mode 3), where banding shows first
            "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-level", "4.2",
            "-x264-params", "aq-mode=3",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),   # 128k: Meta's Reels audio spec
            "-movflags", "+faststart", "-t", f"{tl.duration:.2f}", "-shortest", str(out_path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        waiting, fading = list(pieces), []
        try:
            for i in range(int(math.ceil(tl.duration * FPS))):
                t = i / FPS
                while waiting and waiting[0].t_in <= t:
                    fading.append(waiting.pop(0))
                for p in [p for p in fading if t >= p.t_in + FADE_IN]:
                    landed.alpha_composite(p.image, (p.x, p.y))
                fading = [p for p in fading if t < p.t_in + FADE_IN]
                frame = Image.fromarray(bg.frame(t)).resize((W, H), Image.BICUBIC).convert("RGBA")
                frame.alpha_composite(landed)
                for p in fading:
                    img, x, y = _arriving(p, t)
                    frame.alpha_composite(img, (x, y))
                rgb = np.asarray(frame.convert("RGB"), dtype=np.int16) + dither
                proc.stdin.write(np.clip(rgb, 0, 255).astype(np.uint8).tobytes())
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")
    return out_path
