"""
generate_motivation.py
======================
Generates a daily JEE motivation image:
  1. Groq writes a high-quality English quote (no Hindi, no attribution, no branding)
  2. Pillow renders it on a beautiful gradient with Montserrat font
  3. Saves to motivation_images/ folder

Usage:
  python generate_motivation.py              # today's image
  python generate_motivation.py --preview 6  # 6 samples
  python generate_motivation.py --category comeback_after_failure
"""

import os, sys, json, random, math, argparse
from datetime import date
from pathlib import Path
from groq import Groq
from PIL import Image, ImageDraw, ImageFont

# ─── CONFIG ────────────────────────────────────────────────────────────────────

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
FONT_DIR     = Path(__file__).parent

FONT_BOLD    = str(FONT_DIR / "Montserrat-Bold.ttf")
FONT_REG     = str(FONT_DIR / "Montserrat-Regular.ttf")
FONT_ITALIC  = str(FONT_DIR / "Montserrat-Italic.ttf")

OUTPUT_DIR = Path(__file__).parent / "motivation_images"
OUTPUT_DIR.mkdir(exist_ok=True)

IMG_W, IMG_H = 1080, 1080

# ─── PALETTES ─────────────────────────────────────────────────────────────────
# (top_color, bottom_color, accent_color, text_color)

PALETTES = [
    # Deep navy blue
    ((8, 15, 50),    (15, 50, 130),   (80, 160, 255),  (255, 255, 255)),
    # Midnight purple
    ((25, 5,  55),   (60, 15, 120),   (180, 100, 255), (255, 255, 255)),
    # Dark teal
    ((5,  40, 50),   (10, 90, 110),   (0,  210, 200),  (255, 255, 255)),
    # Charcoal with orange
    ((20, 20, 22),   (45, 42, 38),    (255, 140, 0),   (255, 255, 255)),
    # Deep green
    ((5,  35, 25),   (10, 80,  55),   (60, 220, 130),  (255, 255, 255)),
    # Slate black with cyan
    ((12, 15, 22),   (30, 35, 50),    (0,  200, 255),  (255, 255, 255)),
    # Dark maroon
    ((45, 5,  5),    (100, 18, 18),   (255, 100, 80),  (255, 255, 255)),
    # Navy + gold
    ((8,  12, 45),   (18, 28, 75),    (255, 195, 40),  (255, 255, 255)),
    # Dark indigo
    ((20, 20, 70),   (40, 40, 120),   (150, 180, 255), (255, 255, 255)),
    # Forest dark
    ((10, 28, 20),   (20, 65, 45),    (100, 255, 160), (255, 255, 255)),
    # Warm charcoal
    ((30, 22, 15),   (60, 45, 30),    (255, 180, 80),  (255, 255, 255)),
    # Cold steel
    ((18, 22, 30),   (38, 48, 65),    (140, 200, 255), (255, 255, 255)),
    # Dark violet
    ((30, 10, 60),   (70, 25, 130),   (220, 150, 255), (255, 255, 255)),
    # Near black + lime
    ((12, 18, 12),   (25, 45, 25),    (120, 255, 100), (255, 255, 255)),
    # Obsidian + rose
    ((20, 15, 20),   (45, 30, 45),    (255, 130, 180), (255, 255, 255)),
]

# ─── QUOTE CATEGORIES ─────────────────────────────────────────────────────────

CATEGORIES = [
    "discipline_and_consistency",
    "exam_pressure_and_fear",
    "parents_sacrifice",
    "comeback_after_failure",
    "late_night_study_grind",
    "mock_test_mindset",
    "iit_dream_and_visualization",
    "competition_mindset",
    "time_as_currency",
    "subject_mastery_physics",
    "subject_mastery_maths",
    "mental_toughness",
]

# ─── GROQ SYSTEM PROMPT ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You write deeply authentic motivational quotes for JEE/IIT aspirants in ENGLISH ONLY.

Your quotes must feel RAW and REAL — like something a JEE topper, a beloved mentor, or a desperate-but-hungry student actually thinks at 2 AM, NOT a LinkedIn post.

STRICT RULES:
✅ English only — no Hindi, no transliteration, no Devanagari
✅ Specific to JEE reality: mock ranks, rank drops, 3 AM studying, Kota pressure, PCM formulas, parents' sacrifices
✅ Short to medium length: 1–4 lines max. Punchy. No essays.
✅ Emotion first — the student must FEEL seen, not lectured
✅ Vary the style: some fierce, some poetic, some raw and honest, some darkly funny

BANNED PHRASES (NEVER use these):
❌ "Never give up" / "Keep going" / "Believe in yourself"
❌ "Success comes to those who work hard"
❌ "You can do it" / "Stay focused" / "Dream big"
❌ Any generic motivational cliché

GREAT EXAMPLES (study these closely):
- "Your rank dropped 3000. Your parents said nothing. That silence is the heaviest weight you'll ever carry into an exam room."
- "The integration you couldn't solve at midnight — that's the one on JEE paper. Sit back down."
- "You didn't come this far to scroll reels at 11 PM. You came this far to make 11 PM count."
- "Every topper in that rank list had a night they wanted to quit. You're in that night right now. Stay."
- "Kota didn't break you. The idea of going home empty-handed will."
- "Not sleeping isn't dedication. Understanding the derivation is. Know the difference."
- "The student who scores 99 percentile doesn't work harder than you. They waste less."
- "Your mock test is a mirror. You don't break the mirror because you don't like what you see."

RETURN: Only a raw JSON object. No markdown. No explanation.
{
  "quote": "the quote text, use \\n for line breaks",
  "style": "fierce" | "raw" | "poetic" | "darkly_honest" | "sharp"
}"""


def get_quote(category: str = None, day_seed: int = None) -> dict:
    if not GROQ_API_KEY:
        # Demo fallback (no API key)
        demos = [
            {"quote": "Your rank dropped 3000.\nYour parents said nothing.\nThat silence is the heaviest weight\nyou'll carry into an exam room.", "style": "raw"},
            {"quote": "The integration you couldn't solve at midnight —\nthat's the one on JEE paper.\nSit back down.", "style": "fierce"},
            {"quote": "Every topper in that rank list\nhad a night they wanted to quit.\nYou're in that night right now.\nStay.", "style": "poetic"},
            {"quote": "The student who scores 99 percentile\ndoesn't work harder than you.\nThey waste less.", "style": "sharp"},
        ]
        return random.choice(demos)

    client = Groq(api_key=GROQ_API_KEY)
    cat = category or random.choice(CATEGORIES)

    user_msg = f"""Write ONE quote for category: {cat}
Variety seed: {day_seed or date.today().toordinal()}

Remember: English only. Specific to JEE. No clichés. Make it hit."""

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.88,
        max_tokens=200,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content.strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"quote": raw, "style": "fierce"}


# ─── GRADIENT HELPERS ─────────────────────────────────────────────────────────

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    px  = img.load()
    for y in range(h):
        for x in range(w):
            # Diagonal gradient — more dynamic than flat vertical
            t = (x / w) * 0.35 + (y / h) * 0.65
            px[x, y] = lerp(top, bottom, t)
    return img


def add_noise(img, intensity=7):
    import random as rnd
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for _ in range(w * h // 50):
        x = rnd.randint(0, w - 1)
        y = rnd.randint(0, h - 1)
        v = rnd.randint(-intensity, intensity)
        px = img.getpixel((x, y))
        draw.point((x, y), fill=tuple(max(0, min(255, px[i] + v)) for i in range(3)))
    return img


def draw_accents(draw, w, h, accent, style):
    a   = (*accent, 35)
    a2  = (*accent, 20)

    if style == "circles":
        draw.ellipse([(w - 280, -130), (w + 80,  220)], outline=(*accent, 40), width=2)
        draw.ellipse([(w - 180, -30),  (w - 10,  140)], outline=(*accent, 25), width=1)
        draw.ellipse([(-100, h - 220), (160, h + 100)], outline=(*accent, 35), width=2)

    elif style == "lines":
        for i in range(0, 220, 32):
            draw.line([(w - 240 + i, 0), (w, 240 - i)], fill=(*accent, 35), width=1)
        for i in range(0, 160, 28):
            draw.line([(0, h - 160 + i), (160 - i, h)], fill=(*accent, 28), width=1)

    elif style == "dots":
        for gx in range(w - 200, w - 30, 24):
            for gy in range(h - 200, h - 30, 24):
                draw.ellipse([(gx, gy), (gx + 5, gy + 5)], fill=(*accent, 65))
        for gx in range(30, 200, 24):
            for gy in range(30, 200, 24):
                draw.ellipse([(gx, gy), (gx + 5, gy + 5)], fill=(*accent, 50))

    elif style == "brackets":
        bw = 55
        lw = 3
        # top-left bracket
        draw.line([(85, 85), (85, 230)],       fill=(*accent, 90), width=lw)
        draw.line([(85, 85), (85 + bw, 85)],   fill=(*accent, 90), width=lw)
        draw.line([(85, 230), (85 + bw, 230)], fill=(*accent, 90), width=lw)
        # bottom-right bracket
        draw.line([(w-85, h-85),  (w-85, h-230)],        fill=(*accent, 90), width=lw)
        draw.line([(w-85, h-85),  (w-85-bw, h-85)],      fill=(*accent, 90), width=lw)
        draw.line([(w-85, h-230), (w-85-bw, h-230)],     fill=(*accent, 90), width=lw)

    elif style == "corner_fills":
        # Subtle filled triangles at corners
        draw.polygon([(0,0),(0,120),(120,0)],       fill=(*accent, 18))
        draw.polygon([(w,h),(w,h-120),(w-120,h)],   fill=(*accent, 18))

    # Always: thin top accent bar
    draw.rectangle([(0, 0), (w, 6)], fill=(*accent, 220))


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def wrap_text(text, font, draw, max_w):
    """Wrap text to fit max_w pixels."""
    result = []
    for para in text.split('\n'):
        words  = para.split()
        if not words:
            result.append("")
            continue
        line = ""
        for word in words:
            test = (line + " " + word).strip()
            if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
                line = test
            else:
                if line:
                    result.append(line)
                line = word
        if line:
            result.append(line)
    return result


# ─── MAIN RENDER ──────────────────────────────────────────────────────────────

def render(quote_data: dict, palette_idx: int = None) -> Image.Image:
    w, h = IMG_W, IMG_H

    if palette_idx is None:
        palette_idx = random.randint(0, len(PALETTES) - 1)
    top, bottom, accent, text_col = PALETTES[palette_idx % len(PALETTES)]

    # Background
    img = make_gradient(w, h, top, bottom)
    img = add_noise(img, 6)

    draw  = ImageDraw.Draw(img, "RGBA")
    geom  = random.choice(["circles", "lines", "dots", "brackets", "corner_fills"])
    draw_accents(draw, w, h, accent, geom)

    # ── OPENING QUOTE MARK ────────────────────────────────────────────────
    qfont = load_font(FONT_BOLD, 170)
    draw.text((85, 38), "\u201C", font=qfont, fill=(*accent, 95))

    # ── QUOTE TEXT ────────────────────────────────────────────────────────
    quote  = quote_data.get("quote", "")
    PADD   = 105
    MAX_W  = w - PADD * 2

    # Auto-size font to fit
    for fs in [80, 72, 64, 56, 50, 44, 38]:
        font_q = load_font(FONT_BOLD, fs)
        lines  = wrap_text(quote, font_q, draw, MAX_W)
        lh     = fs + 18
        if len(lines) * lh < h * 0.60 and len(lines) <= 9:
            break

    lh        = fs + 18
    total_h   = len(lines) * lh
    start_y   = (h - total_h) // 2 - 30   # slightly above center

    for i, line in enumerate(lines):
        y    = start_y + i * lh
        bw   = draw.textbbox((0, 0), line, font=font_q)[2]
        x    = (w - bw) // 2
        # Shadow
        draw.text((x + 3, y + 3), line, font=font_q, fill=(0, 0, 0, 85))
        # Text
        draw.text((x, y), line, font=font_q, fill=(*text_col, 255))

    # ── ACCENT DIVIDER ────────────────────────────────────────────────────
    div_y = start_y + total_h + 35
    draw.rectangle([(w//2 - 50, div_y), (w//2 + 50, div_y + 3)], fill=(*accent, 200))

    return img


# ─── ENTRY POINTS ─────────────────────────────────────────────────────────────

def generate_today(category=None, save=True):
    today    = date.today()
    day_seed = today.toordinal()
    pal_idx  = (day_seed * 7 + 3) % len(PALETTES)

    print(f"[1/2] Generating quote (category: {category or 'auto'})...")
    q = get_quote(category=category, day_seed=day_seed)
    print(f"      → {q['quote'][:70]}...")

    print(f"[2/2] Rendering...")
    img = render(q, palette_idx=pal_idx)

    if save:
        path = OUTPUT_DIR / f"motivation_{today.strftime('%Y-%m-%d')}.jpg"
        img.save(str(path), "JPEG", quality=96)
        print(f"      Saved → {path}")
        return path
    return img


def generate_previews(n=6):
    print(f"Generating {n} previews...\n")
    for i in range(n):
        cat = CATEGORIES[i % len(CATEGORIES)]
        q   = get_quote(category=cat, day_seed=i * 1337)
        img = render(q, palette_idx=i)
        path = OUTPUT_DIR / f"preview_{i+1:02d}.jpg"
        img.save(str(path), "JPEG", quality=94)
        print(f"[{i+1}] {path}  — \"{q['quote'][:55]}...\"")
    print("\nDone.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--preview",  type=int,  metavar="N")
    p.add_argument("--category", type=str,  default=None)
    p.add_argument("--no-save",  action="store_true")
    args = p.parse_args()

    if args.preview:
        generate_previews(args.preview)
    else:
        generate_today(category=args.category, save=not args.no_save)
