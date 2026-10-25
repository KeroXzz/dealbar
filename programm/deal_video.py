#!/usr/bin/env python3
"""
Deal-Video-Generator
====================
Erstellt aus Produktbildern + ein paar Angaben ein hochkant Kurzvideo (1080x1920, MP4)
fuer TikTok, Instagram Reels und YouTube Shorts.

Benutzung:
    python deal_video.py deal.json                 # ein Video (oder mehrere, wenn die JSON eine Liste ist)
    python deal_video.py deal.json -o videos/      # Ausgabeordner waehlen

Voraussetzungen: Python 3.9+, Pillow, numpy (pip install pillow numpy) und ffmpeg im PATH.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30

# --------------------------------------------------------------------------- Schriften

FONT_CANDIDATES = {
    "black": ["fonts/Inter-Black.otf", "/usr/share/fonts/opentype/inter/Inter-Black.otf",
              "C:/Windows/Fonts/ariblk.ttf", "/Library/Fonts/Arial Black.ttf",
              "/System/Library/Fonts/Supplemental/Arial Black.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "bold": ["fonts/Inter-Bold.otf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
             "C:/Windows/Fonts/arialbd.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "medium": ["fonts/Inter-Medium.otf", "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
               "C:/Windows/Fonts/arial.ttf", "/Library/Fonts/Arial.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "light": ["fonts/Inter-Light.otf", "/usr/share/fonts/opentype/inter/Inter-Light.otf",
              "C:/Windows/Fonts/arial.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf"],
}
_font_cache = {}


def ffmpeg_exe():
    """ffmpeg aus dem Python-Paket imageio-ffmpeg, sonst das System-ffmpeg."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def font(weight, size):
    key = (weight, size)
    if key not in _font_cache:
        here = os.path.dirname(os.path.abspath(__file__))
        for p in FONT_CANDIDATES[weight]:
            path = p if os.path.isabs(p) else os.path.join(here, p)
            if os.path.exists(path):
                _font_cache[key] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default(size)
    return _font_cache[key]


# --------------------------------------------------------------------------- Hilfen

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur)


def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out_back(x, s=1.9):
    x -= 1
    return x * x * ((s + 1) * x + s) + 1


def euro(v):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def text_w(draw, txt, f):
    l, _, r, _ = draw.textbbox((0, 0), txt, font=f)
    return r - l


def wrap(draw, txt, f, max_w, max_lines=2):
    words, lines, cur = txt.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if text_w(draw, test, f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while text_w(draw, lines[-1] + " …", f) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += " …"
    return lines


def paste_alpha(base, layer, xy, alpha=1.0):
    """Ebene mit zusaetzlicher Deckkraft einfuegen."""
    if alpha <= 0:
        return
    if alpha < 1:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    base.paste(layer, (int(round(xy[0])), int(round(xy[1]))), layer)


def put_text(img, xy, text, f, color, alpha=1.0, anchor="la"):
    """Text mit Deckkraft zeichnen (ImageDraw ignoriert Alpha bei Text)."""
    if alpha <= 0 or not text:
        return
    l, t, r, b = ImageDraw.Draw(img).textbbox(xy, text, font=f, anchor=anchor)
    l, t = int(math.floor(l)) - 2, int(math.floor(t)) - 2
    m = Image.new("L", (int(r) - l + 4, int(b) - t + 4), 0)
    ImageDraw.Draw(m).text((xy[0] - l, xy[1] - t), text, font=f, fill=255, anchor=anchor)
    if alpha < 1:
        m = m.point(lambda v: int(v * alpha))
    img.paste(color, (l, t, l + m.width, t + m.height), m)


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


# --------------------------------------------------------------------------- statische Ebenen

def make_background(brand):
    """Heller, ruhiger Hintergrund in Markenfarbe mit weichem Licht hinter dem Produkt."""
    base = np.array(hex_rgb(brand["background"]), dtype=float)
    y = np.linspace(0, 1, H)[:, None, None]
    bg = base * (1.0 - 0.035 * y)  # unten minimal dunkler
    bg = np.broadcast_to(bg, (H, W, 3)).copy()
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - W / 2) ** 2 + ((yy - 730) * 0.9) ** 2)
    glow = np.exp(-(d / 560) ** 2)[..., None]
    bg = bg + glow * (255 - bg) * 0.9
    bg += np.random.default_rng(1).normal(0, 1.2, bg.shape)
    return Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))


def make_shadow(card):
    pad = 120
    sh = Image.new("RGBA", (card + pad * 2, card + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 30, pad + card, pad + card + 30), 56,
                                         fill=(40, 36, 30, 60))
    return sh.filter(ImageFilter.GaussianBlur(50)), pad


def prepare_product(path, box):
    """Bild laden, auf weisse Flaeche legen, in die Karte einpassen (mit Rand)."""
    im = Image.open(path)
    im = im.convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    inner = int(box * 0.80)
    scale = min(inner / im.width, inner / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (box, box), (255, 255, 255, 255))
    canvas.alpha_composite(im, ((box - im.width) // 2, (box - im.height) // 2))
    return canvas


def make_badge(text, accent):
    """Runder Rabatt-Sticker in Akzentfarbe (4-fach gerendert fuer glatte Kanten)."""
    r, ss = 118, 4
    size = (r * 2 + 8) * ss
    b = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    c = size // 2
    d.ellipse((c - r * ss, c - r * ss, c + r * ss, c + r * ss), fill=accent + (255,))
    fs = 76
    while fs > 30 and text_w(d, text, font("black", fs)) > (r - 30) * 2:
        fs -= 2
    f = font("black", fs * ss)
    d.text((c, c), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
    b = b.resize((size // ss, size // ss), Image.LANCZOS)
    return b.rotate(-8, resample=Image.BICUBIC, expand=False)


def make_text_card(cfg, brand, box):
    """Produktkarte ohne Foto (Amazon-Bilder sind tabu): grosser Rabatt oder Bildmarke, Kategorie, Deal-Nr."""
    import brand as brandlib

    ink, muted = hex_rgb(brand["ink"]), hex_rgb(brand["muted"])
    accent = hex_rgb(cfg.get("accent", brand["accent"]))
    tint = tuple(int(255 - (255 - c) * 0.08) for c in accent)
    im = Image.new("RGBA", (box, box), tint + (255,))
    d = ImageDraw.Draw(im)
    cat = (cfg.get("category") or "").strip()
    if cat:
        tag = brandlib.make_tagline(cat, 34, muted, font, spacing=0.16)
        im.alpha_composite(tag, ((box - tag.width) // 2, 92))
    disc = cfg.get("discount_percent") if cfg.get("show_discount", True) else None
    if disc:
        txt = f"−{disc}%"
        fs = 270
        while fs > 120 and text_w(d, txt, font("black", fs)) > box - 110:
            fs -= 6
        d.text((box / 2, box / 2 - 30), txt, font=font("black", fs), fill=accent + (255,), anchor="mm")
        lbl = brandlib.make_tagline("Rabatt", 44, ink, font, spacing=0.22)
        im.alpha_composite(lbl, ((box - lbl.width) // 2, int(box / 2 + fs * 0.42)))
    else:
        mark = brandlib.make_mark(250, ink)
        im.alpha_composite(mark, ((box - mark.width) // 2, (box - mark.height) // 2 - 10))
    if cfg.get("nr"):
        nr = brandlib.make_tagline(f"Deal #{cfg['nr']}", 30, muted, font, spacing=0.16)
        im.alpha_composite(nr, ((box - nr.width) // 2, box - 128))
    return im


def make_pill(text, f, bg, fg, padx=38, h=84):
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = text_w(tmp, text, f)
    p = Image.new("RGBA", (tw + padx * 2, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.rounded_rectangle((0, 0, p.width - 1, h - 1), h // 2, fill=bg)
    d.text((padx, h / 2 + 1), text, font=f, fill=fg, anchor="lm")
    return p


# --------------------------------------------------------------------------- Rendern

def render(cfg, base_dir, out_path, brand):
    import brand as brandlib

    ink, paper, muted = hex_rgb(brand["ink"]), hex_rgb(brand["paper"]), hex_rgb(brand["muted"])
    accent = hex_rgb(cfg.get("accent", brand["accent"]))
    dur = float(cfg.get("duration", 8))
    n_frames = int(dur * FPS)
    use_intro = cfg.get("intro", brand.get("intro", True))
    total = dur + (brandlib.INTRO_DUR if use_intro else 0)

    p_old, p_new = cfg.get("price_old"), cfg.get("price_new")
    show_price = cfg.get("show_price", True) and p_new is not None
    discount = cfg.get("discount_percent")
    if discount is None and p_old and p_new:
        discount = round((p_old - p_new) / p_old * 100)
    show_discount = cfg.get("show_discount", True) and discount

    stamp = cfg.get("price_time") or datetime.now().strftime("%d.%m.%Y, %H:%M")
    stamp_txt = f"Preis vom {stamp} Uhr · kann sich jederzeit ändern"

    # Layout (innerhalb der sicheren Zone der Plattform-Oberflaechen)
    CARD = 740
    card_x, card_y = (W - CARD) // 2, 380
    bg = make_background(brand)
    shadow, sh_pad = make_shadow(CARD)
    mask = rounded_mask((CARD, CARD), 56)

    images = [prepare_product(os.path.join(base_dir, p), CARD) for p in cfg["images"]]
    if not images:  # ohne eigene Fotos: Karte im Text-Design, der Rabatt steht dann gross darin
        images = [make_text_card(cfg, brand, CARD)]
        show_discount = False
    seg = dur * 0.82 / len(images)  # Bildwechsel im Hauptteil, letztes Bild bleibt bis Ende
    xfade = 0.45

    badge = make_badge(f"-{discount}%", accent) if show_discount else None
    pill = make_pill(cfg.get("label", "TOP-DEAL"), font("bold", 34), ink + (255,), paper + (255,),
                     padx=34, h=72)
    ad_tag = make_pill("Werbung", font("bold", 24), ink + (16,), ink + (170,), padx=20, h=44)
    mini_logo = brandlib.make_lockup(brand, 50, font)
    hook = cfg.get("hook", "")

    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_lines = wrap(tmp, cfg["title"], font("bold", 54), W - 170, 2)
    cta = make_pill(cfg.get("cta", "Zum Deal: Link in Bio"), font("bold", 42), ink + (255,),
                    paper + (255,), padx=52, h=104)
    disclosure = cfg.get("disclosure", "Als Amazon-Partner verdiene ich an qualifizierten Verkäufen.")

    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    music = cfg.get("music")
    if music:
        cmd += ["-i", os.path.join(base_dir, music), "-map", "0:v", "-map", "1:a", "-c:a", "aac",
                "-b:a", "160k", "-af", f"afade=t=in:d=0.5,afade=t=out:st={total - 0.8}:d=0.8", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    if use_intro:
        for f in brandlib.intro_frames(brand, W, H, FPS, font, bg):
            ff.stdin.write(f.tobytes())

    for i in range(n_frames):
        t = i / FPS
        fr = bg.copy()
        d = ImageDraw.Draw(fr, "RGBA")  # "RGBA" = halbtransparent zeichnen

        # --- Kopfzeile: Mini-Logo, Werbung, Label, Hook
        paste_alpha(fr, mini_logo, ((W - mini_logo.width) // 2, 112), ease_out_cubic(prog(t, 0.0, 0.5)))
        paste_alpha(fr, ad_tag, (60, 116), ease_out_cubic(prog(t, 0.0, 0.4)))
        a = ease_out_cubic(prog(t, 0.15, 0.5))
        paste_alpha(fr, pill, ((W - pill.width) // 2, 206 - 40 * (1 - a)), a)
        if hook:
            ah = ease_out_cubic(prog(t, 0.35, 0.5))
            f = font("medium", 34)
            tw = text_w(d, hook, f)
            put_text(fr, ((W - tw) / 2, 304 + 20 * (1 - ah)), hook, f, ink, 0.62 * ah)

        # --- Produktkarte mit Bildwechsel und sanftem Zoom
        ac = ease_out_cubic(prog(t, 0.2, 0.7))
        if ac > 0:
            idx = min(int(t / seg), len(images) - 1)
            card = Image.new("RGBA", (CARD, CARD), (255, 255, 255, 255))
            for k in (idx - 1, idx):
                if k < 0:
                    continue
                local = t - k * seg
                z = 1.0 + 0.07 * ease_in_out(clamp(local / (seg + xfade)))
                if k == len(images) - 1:  # letztes Bild zoomt bis zum Ende weiter
                    z = 1.0 + 0.10 * ease_in_out(clamp(local / (dur - k * seg)))
                if k < idx:  # vorheriges Bild nur waehrend der Ueberblendung
                    if t - idx * seg >= xfade:
                        continue
                    vis = 1.0
                else:
                    vis = clamp((t - idx * seg) / xfade) if idx > 0 else 1.0
                s = int(CARD * z)
                im = images[k].resize((s, s), Image.BICUBIC)
                off = (s - CARD) // 2
                im = im.crop((off, off, off + CARD, off + CARD))
                paste_alpha(card, im, (0, 0), vis)
            card.putalpha(mask)
            sc = 0.9 + 0.1 * ac
            cs = int(CARD * sc)
            cx, cy = card_x + (CARD - cs) // 2, card_y + (CARD - cs) // 2 + int(40 * (1 - ac))
            shs = shadow.resize((int(shadow.width * sc), int(shadow.height * sc)), Image.BILINEAR)
            paste_alpha(fr, shs, (cx - sh_pad * sc, cy - sh_pad * sc), ac)
            paste_alpha(fr, card.resize((cs, cs), Image.BICUBIC), (cx, cy), ac)

        # --- Rabatt-Sticker (Pop-in mit leichtem Ueberschwingen)
        if badge is not None:
            pb = prog(t, 1.1, 0.55)
            if pb > 0:
                s = ease_out_back(pb, 1.5) * (1 + 0.025 * math.sin((t - 1.65) * 4) * (t > 1.65))
                bs = max(1, int(badge.width * s))
                b = badge.resize((bs, bs), Image.BICUBIC)
                bx, by = card_x + CARD - 160, card_y - 90
                paste_alpha(fr, b, (bx - bs / 2 + badge.width / 2, by - bs / 2 + badge.width / 2), clamp(pb * 3))

        # --- Produktname
        at = ease_out_cubic(prog(t, 1.5, 0.6))
        f = font("bold", 54)
        ty = 1172
        for li, line in enumerate(title_lines):
            tw = text_w(d, line, f)
            put_text(fr, ((W - tw) / 2, ty + li * 68 + 30 * (1 - at)), line, f, ink, at)
        after_title = ty + len(title_lines) * 68 + 20

        # --- Preise
        if show_price:
            ap = ease_out_cubic(prog(t, 2.0, 0.6))
            fn, fo = font("black", 104), font("medium", 50)
            new_t = euro(p_new)
            old_t = euro(p_old) if p_old else ""
            wn = text_w(d, new_t, fn)
            wo = text_w(d, old_t, fo) if old_t else 0
            gap = 34 if old_t else 0
            x0 = (W - (wn + gap + wo)) / 2
            py = after_title + 66 + 25 * (1 - ap)
            put_text(fr, (x0, py), new_t, fn, ink, ap, "lm")
            if old_t:
                ox = x0 + wn + gap
                put_text(fr, (ox, py + 8), old_t, fo, muted, ap, "lm")
                ls = ease_out_cubic(prog(t, 2.5, 0.4))
                if ls > 0:
                    d.line((ox - 4, py + 12, ox - 4 + (wo + 8) * ls, py + 6), fill=accent + (255,), width=5)
            a2 = ease_out_cubic(prog(t, 2.8, 0.5))
            fs = font("medium", 25)
            sw = text_w(d, stamp_txt, fs)
            put_text(fr, ((W - sw) / 2, py + 70), stamp_txt, fs, muted, a2)
            after_price = py + 125
        else:
            after_price = after_title + 30

        # --- Call-to-Action (erscheint gegen Ende, pulsiert sanft)
        ta = dur - 3.0
        pc = prog(t, ta, 0.5)
        if pc > 0:
            s = ease_out_back(pc, 1.3) * (1 + 0.03 * math.sin((t - ta) * 6) * (t > ta + 0.5))
            cw, chh = max(1, int(cta.width * s)), max(1, int(cta.height * s))
            c = cta.resize((cw, chh), Image.BICUBIC)
            cy0 = max(after_price + 30, 1505)
            paste_alpha(fr, c, ((W - cw) / 2, cy0 + (cta.height - chh) / 2), clamp(pc * 2))
            ay = cy0 + cta.height + 16 + 6 * math.sin((t - ta) * 6)
            al = int(255 * clamp(pc * 2))
            d.polygon([(W / 2 - 16, ay), (W / 2 + 16, ay), (W / 2, ay + 18)], fill=ink + (al,))

        # --- Pflicht-Hinweis (dauerhaft sichtbar)
        fd = font("medium", 25)
        dw = text_w(d, disclosure, fd)
        put_text(fr, ((W - dw) / 2, 1722), disclosure, fd, muted)

        if not use_intro and t < 0.25:  # ohne Intro: weich einblenden
            fr = Image.blend(Image.new("RGB", (W, H), paper), fr, t / 0.25)
        ff.stdin.write(fr.tobytes())

    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg ist fehlgeschlagen.")
    return total


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import brand as brandlib

    ap = argparse.ArgumentParser(description="Erstellt Deal-Kurzvideos aus Produktbildern.")
    ap.add_argument("config", help="JSON-Datei mit einem Deal oder einer Liste von Deals")
    ap.add_argument("-o", "--out", default="videos", help="Ausgabeordner (Standard: videos)")
    args = ap.parse_args()

    brand = brandlib.load_brand()
    base = os.path.dirname(os.path.abspath(args.config))
    with open(args.config, encoding="utf-8") as fh:
        data = json.load(fh)
    deals = data if isinstance(data, list) else [data]
    os.makedirs(args.out, exist_ok=True)
    for n, cfg in enumerate(deals, 1):
        name = cfg.get("output") or f"deal_{n:02d}.mp4"
        path = os.path.join(args.out, name)
        print(f"[{n}/{len(deals)}] {cfg['title'][:50]} -> {path}")
        render(cfg, base, path, brand)
    print("Fertig.")


if __name__ == "__main__":
    main()
