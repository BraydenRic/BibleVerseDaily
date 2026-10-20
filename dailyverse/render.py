"""Render a post as a calm vertical Reel: drifting background, text overlay, original audio.

Everything here is generated from code, so there are no stock-footage or music licenses to track.
A tradition can choose its own palettes and audio style, and show the original-language text
(Arabic, Devanagari, ...) above the translation; see `style` in tradition.json.
"""
from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"

W, H = 1080, 1920          # 9:16 Reel
BG_SCALE = 5               # background is drawn at 1/5 size and upscaled (smooth gradients upscale cleanly)
FPS = 30
SR = 44100
CREAM = (255, 251, 244)

# Calm palettes: (top color, bottom color, glow colors)
PALETTES = [
    ("#141c33", "#a8746a", ["#f3c98b", "#e79a8c", "#7d6aa6"]),   # dawn
    ("#0c2233", "#2f6d78", ["#8fd3d1", "#5aa1c2", "#c9e6e4"]),   # ocean
    ("#18241f", "#5e7f63", ["#e9e2c3", "#9cc39a", "#c7d7a8"]),   # sage
    ("#1f1834", "#6f6194", ["#e6b3cc", "#b4a4e0", "#f1d6e3"]),   # lavender
    ("#1b1a2c", "#8c5a6e", ["#f2b38f", "#d27f8f", "#6c7fb8"]),   # dusk
]
AUDIO_STYLES = ("ambient", "nature", "silent")


@dataclass
class Card:
    """Everything that ends up on screen and in the soundtrack for one post."""
    text: str                       # the translation shown on screen
    ref: str                        # reader-facing reference, e.g. "Psalm 23:1"
    label: str                      # small top label, e.g. "Day 3 of 40"
    seed: int                       # makes background and audio deterministic per post
    original: str | None = None     # original-language text shown above the translation
    original_font: str = "Lora-Variable.ttf"
    original_direction: str = "ltr"         # "rtl" for Arabic and Hebrew
    original_language: str | None = None    # e.g. "ar", "sa": guides shaping
    original_line_height: float = 1.85      # Arabic needs room for vowel marks; Devanagari less
    palettes: list | None = None    # overrides PALETTES
    audio: str = "ambient"          # one of AUDIO_STYLES


def card_for(slot) -> Card:
    """The Card for a scheduled post, with the tradition's look and sound applied."""
    from .caption import original_text, overlay_text
    body, ref, label = overlay_text(slot)
    style = slot.tradition.get("style", {})
    orig = style.get("original") or {}
    return Card(
        text=body, ref=ref, label=label, seed=slot.seed,
        original=original_text(slot) if orig.get("on_screen", True) else None,
        original_font=orig.get("font", "Lora-Variable.ttf"),
        original_direction=orig.get("direction", "ltr"),
        original_language=orig.get("language"),
        original_line_height=orig.get("line_height", 1.85),
        palettes=style.get("palettes"),
        audio=style.get("audio", "ambient"),
    )


# ---------------------------------------------------------------- timing

def duration_for(text: str) -> float:
    words = len(text.split())
    return float(min(30.0, max(12.0, 8.0 + words * 0.33)))


# ---------------------------------------------------------------- text overlay

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


def layout(card: Card) -> dict:
    """Pick the largest type size at which the text block fits its box.

    With an original text, both blocks scale together: the original a little larger (Arabic and
    Devanagari read small at the same point size) with taller lines for vowel marks. `fits` is
    False if even the smallest size overflows; tests check every scheduled post against it.
    """
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_w = 860
    max_h = 1100 if card.original else 980
    shaping = _shaping(card) if card.original else {}
    for size in range(78, 28, -2):
        L = {"size": size, "font": _font("Lora-Variable.ttf", size, "Regular"), "shaping": shaping}
        L["lines"] = _wrap(draw, card.text, L["font"], max_w)
        L["line_h"] = int(size * 1.42)
        L["block_h"] = len(L["lines"]) * L["line_h"]
        if card.original:
            o_size = int(size * 1.12)
            L["o_font"] = _font(card.original_font, o_size)
            L["o_lines"] = _wrap(draw, card.original, L["o_font"], max_w, **shaping)
            L["o_line_h"] = int(o_size * card.original_line_height)
            L["o_gap"] = int(size * 1.4)
            L["block_h"] += len(L["o_lines"]) * L["o_line_h"] + L["o_gap"]
        L["fits"] = L["block_h"] <= max_h
        if L["fits"]:
            break
    return L


def make_overlay(card: Card, path: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    L = layout(card)
    font, lines, line_h, block_h, shaping = L["font"], L["lines"], L["line_h"], L["block_h"], L["shaping"]
    if card.original:
        o_font, o_lines, o_line_h, o_gap = L["o_font"], L["o_lines"], L["o_line_h"], L["o_gap"]

    ref_font = _font("Poppins-Medium.ttf", 34)
    label_font = _font("Poppins-Regular.ttf", 28)
    gap = 70
    total_h = block_h + gap + 40
    y = (H - total_h) // 2 - 30

    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    if card.original:
        for ln in o_lines:
            lw = td.textlength(ln, font=o_font, **shaping)
            td.text(((W - lw) / 2, y), ln, font=o_font, fill=CREAM + (255,), **shaping)
            y += o_line_h
        y += o_gap
    for ln in lines:
        lw = td.textlength(ln, font=font)
        # the translation steps back slightly when it sits under the original
        td.text(((W - lw) / 2, y), ln, font=font, fill=CREAM + (230 if card.original else 255,))
        y += line_h

    # thin divider, then the reference
    y += gap // 2
    td.line([(W / 2 - 36, y), (W / 2 + 36, y)], fill=CREAM + (150,), width=2)
    y += gap // 2 - 6
    ref_up = " ".join(card.ref.upper())  # letter-spaced
    rw = td.textlength(ref_up, font=ref_font)
    td.text(((W - rw) / 2, y), ref_up, font=ref_font, fill=CREAM + (215,))

    # small label near the top
    lab = " ".join(card.label.upper())
    lw = td.textlength(lab, font=label_font)
    td.text(((W - lw) / 2, 250), lab, font=label_font, fill=CREAM + (150,))

    # soft shadow so text stays readable on any palette
    shadow = text_layer.split()[3].filter(ImageFilter.GaussianBlur(14))
    shadow = shadow.point(lambda a: int(a * 0.55))
    dark = Image.new("RGBA", (W, H), (10, 8, 20, 0))
    dark.putalpha(shadow)
    img = Image.alpha_composite(img, dark)
    img = Image.alpha_composite(img, text_layer)
    img.save(path)


# ---------------------------------------------------------------- background

def _hex(c: str) -> np.ndarray:
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


class Background:
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
        # vignette, gentle
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
    """Soft pad chords and sparse bell notes, dry (reverb is added in _finish)."""
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
            target_db = -18
        else:
            mix = _nature(dur, rng)
            target_db = -26          # background texture, well under speech level
        # Gentle low-pass in the frequency domain to keep it warm
        freqs = np.fft.rfftfreq(n, 1 / SR)
        gain = 1 / np.sqrt(1 + (freqs / 3500) ** 4)
        for ch in range(2):
            mix[:, ch] = np.fft.irfft(np.fft.rfft(mix[:, ch]) * gain, n)
        # Fades and loudness (fades shrink to fit very short clips)
        fade_in, fade_out = min(int(1.5 * SR), n // 2), min(int(2.5 * SR), n // 2)
        mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
        mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
        rms = np.sqrt(np.mean(mix ** 2)) + 1e-9
        mix *= 10 ** (target_db / 20) / rms
        peak = np.max(np.abs(mix))
        if peak > 0.89:
            mix *= 0.89 / peak

    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- video

def _seconds(card: Card) -> float:
    # Reading time follows the translation; an original text above it adds a little.
    extra = 0.15 * len(card.original.split()) if card.original else 0.0
    return min(30.0, duration_for(card.text) + extra)


def cover_time(card: Card) -> float:
    """The moment used as the Reel's cover in the profile grid, and for preview stills: midway,
    with the text fully in. (Instagram's default is the first frame, which is a fade from black.)"""
    return _seconds(card) / 2


def render_still(card: Card, out_jpg: Path) -> Path:
    """Fast preview: one background frame plus the text overlay, no video or audio."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    overlay = out_jpg.with_suffix(".overlay.png")
    make_overlay(card, overlay)
    bg = Background(card.seed, card.palettes)
    frame = Image.fromarray(bg.frame(cover_time(card))).resize((W, H), Image.BICUBIC).convert("RGBA")
    Image.alpha_composite(frame, Image.open(overlay)).convert("RGB").save(out_jpg, quality=88)
    overlay.unlink()
    return out_jpg


def render_reel(card: Card, out_path: Path) -> Path:
    """Full Reel: 1080x1920 H.264 + AAC, 12 to 30 seconds depending on text length."""
    dur = _seconds(card)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overlay = out_path.with_suffix(".overlay.png")
    audio = out_path.with_suffix(".wav")

    make_overlay(card, overlay)
    make_audio(card.seed, dur, audio, card.audio)
    bg = Background(card.seed, card.palettes)

    fade_out_start = max(0.0, dur - 1.4)
    filt = (
        f"[0:v]scale={W}:{H}:flags=bicubic[bg];"
        f"[1:v]format=rgba,fade=t=in:st=0.7:d=1.6:alpha=1,"
        f"fade=t=out:st={fade_out_start:.2f}:d=1.2:alpha=1[txt];"
        f"[bg][txt]overlay=0:0:shortest=1,noise=alls=3:allf=t,"
        f"fade=t=in:st=0:d=0.8,format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{bg.w}x{bg.h}", "-r", str(FPS), "-i", "-",
        "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
        "-i", str(audio),
        "-filter_complex", filt, "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-profile:v", "high",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),   # 128k: Meta's Reels audio spec
        "-movflags", "+faststart", "-t", f"{dur:.2f}", "-shortest", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(int(math.ceil(dur * FPS))):
            proc.stdin.write(bg.frame(i / FPS).tobytes())
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    overlay.unlink()
    audio.unlink()
    return out_path
