"""
Marke: Logo (Preisschild mit Haken + Schriftzug) und 3-Sekunden-Intro.
Einstellungen kommen aus marke.json (Name, Tagline, Farben).

Einzeln ausfuehren, um die Logo-Dateien zu exportieren:
    python brand.py            # erzeugt logo_*.png und intro.mp4 im Ordner "marke"
"""

import json
import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 4  # Supersampling fuer glatte Kanten


def load_brand(path=None):
    path = path or os.path.join(HERE, "marke.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur)


def ease_out_expo(x):
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out_back(x, s=1.3):
    x -= 1
    return x * x * ((s + 1) * x + s) + 1


# --------------------------------------------------------------------------- Bildmarke

_tag_cache = {}


def _tag_mask(h):
    """Preisschild-Silhouette (zeigt nach links) mit Loch, Ecken weich gerundet."""
    if h in _tag_cache:
        return _tag_cache[h].copy()
    u = h * SS
    w = int(u * 1.3)
    pad = int(u * 0.1)
    m = Image.new("L", (w + pad * 2, u + pad * 2), 0)
    d = ImageDraw.Draw(m)
    pts = [(0.00, 0.50), (0.36, 0.00), (1.30, 0.00), (1.30, 1.00), (0.36, 1.00)]
    d.polygon([(pad + x * u, pad + y * u) for x, y in pts], fill=255)
    # Ecken runden: weichzeichnen und wieder hart schneiden
    m = m.filter(ImageFilter.GaussianBlur(u * 0.07)).point(lambda v: 255 if v > 128 else 0)
    # Loch
    r = u * 0.075
    cx, cy = pad + 0.30 * u, pad + 0.50 * u
    d = ImageDraw.Draw(m)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=0)
    _tag_cache[h] = m
    return m.copy()


def _check_points(h):
    u = h * SS
    pad = int(u * 0.1)
    pts = [(0.58, 0.52), (0.76, 0.69), (1.08, 0.33)]
    return [(pad + x * u, pad + y * u) for x, y in pts], u * 0.115


def _draw_partial_polyline(d, pts, width, p, fill):
    """Linienzug bis zum Anteil p (0..1) zeichnen, mit runden Enden."""
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    lens = [math.dist(a, b) for a, b in segs]
    remaining = sum(lens) * p
    r = width / 2
    d.ellipse((pts[0][0] - r, pts[0][1] - r, pts[0][0] + r, pts[0][1] + r), fill=fill)
    for (a, b), L in zip(segs, lens):
        if remaining <= 0:
            break
        f = min(1.0, remaining / L)
        e = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        d.line((a, e), fill=fill, width=int(width))
        d.ellipse((e[0] - r, e[1] - r, e[0] + r, e[1] + r), fill=fill)
        remaining -= L


def make_mark(h, ink, check_p=1.0):
    """Bildmarke als RGBA (Hoehe h in Pixeln). Haken und Loch sind ausgestanzt (transparent)."""
    m = _tag_mask(h)
    if check_p > 0:
        pts, width = _check_points(h)
        _draw_partial_polyline(ImageDraw.Draw(m), pts, width, check_p, 0)
    m = m.resize((m.width // SS, m.height // SS), Image.LANCZOS)
    out = Image.new("RGBA", m.size, ink + (0,))
    out.putalpha(m)
    return out


# --------------------------------------------------------------------------- Schriftzug

def make_wordmark(brand, size, ink, font_fn):
    fb, fl = font_fn("black", size), font_fn("light", size)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    b1 = tmp.textbbox((0, 0), brand["name_bold"], font=fb, anchor="ls")
    b2 = tmp.textbbox((0, 0), brand["name_light"], font=fl, anchor="ls")
    w1 = b1[2] - b1[0]
    gap = int(size * 0.02)
    w = w1 + gap + (b2[2] - b2[0]) + 8
    top = min(b1[1], b2[1])
    hgt = max(b1[3], b2[3]) - top + 8
    im = Image.new("RGBA", (w, hgt), ink + (0,))
    d = ImageDraw.Draw(im)
    d.text((-b1[0], -top), brand["name_bold"], font=fb, fill=ink + (255,), anchor="ls")
    d.text((w1 + gap - b2[0], -top), brand["name_light"], font=fl, fill=ink + (255,), anchor="ls")
    return im


def make_tagline(text, size, color, font_fn, spacing=0.14):
    f = font_fn("medium", size)
    text = text.upper()
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    sp = size * spacing
    widths = [tmp.textlength(c, font=f) for c in text]
    w = int(sum(widths) + sp * (len(text) - 1)) + 4
    im = Image.new("RGBA", (w, int(size * 1.4)), color + (0,))
    d = ImageDraw.Draw(im)
    x = 0
    for c, cw in zip(text, widths):
        d.text((x, size * 1.1), c, font=f, fill=color + (255,), anchor="ls")
        x += cw + sp
    return im


def make_lockup(brand, mark_h, font_fn, ink=None):
    """Bildmarke + Schriftzug nebeneinander (fuer Mini-Logo im Video)."""
    ink = ink or hex_rgb(brand["ink"])
    mark = make_mark(mark_h, ink)
    word = make_wordmark(brand, int(mark_h * 0.78), ink, font_fn)
    gap = int(mark_h * 0.28)
    w = mark.width + gap + word.width
    hh = max(mark.height, word.height)
    im = Image.new("RGBA", (w, hh), ink + (0,))
    im.alpha_composite(mark, (0, (hh - mark.height) // 2))
    im.alpha_composite(word, (mark.width + gap, (hh - word.height) // 2 + int(mark_h * 0.03)))
    return im


# --------------------------------------------------------------------------- Intro

INTRO_DUR = 3.0


def intro_frames(brand, W, H, fps, font_fn, end_bg):
    """Erzeugt die Intro-Bilder (RGB). end_bg = erstes Hintergrundbild des Deals (fuer den Uebergang)."""
    ink, paper, muted = hex_rgb(brand["ink"]), hex_rgb(brand["paper"]), hex_rgb(brand["muted"])
    MH = 200
    word = make_wordmark(brand, 150, ink, font_fn)
    tag = make_tagline(brand["tagline"], 30, muted, font_fn)
    mark_full = make_mark(MH, ink)
    gap = 48
    total_w = mark_full.width + gap + word.width
    lock_x = (W - total_w) // 2
    cy = int(H * 0.46)
    white = Image.new("RGB", (W, H), paper)

    n = int(INTRO_DUR * fps)
    for i in range(n):
        t = i / fps
        fr = white.copy()

        # 1) Preisschild erscheint (Skalierung + leichte Drehung), zuerst mittig
        pm = ease_out_back(prog(t, 0.15, 0.7))
        a_m = ease_out_cubic(prog(t, 0.15, 0.4))
        # 2) Haken zeichnet sich
        ck = ease_in_out(prog(t, 0.65, 0.45))
        # 3) Marke gleitet nach links, Schriftzug wird enthuellt
        sl = ease_out_expo(prog(t, 1.05, 0.9))
        # 4) Tagline
        tg = ease_out_cubic(prog(t, 1.55, 0.6))
        # 5) Ausblenden in den Deal-Hintergrund
        out = ease_in_out(prog(t, 2.45, 0.55))

        layer = Image.new("RGBA", (W, H), ink + (0,))
        start_x = (W - mark_full.width) / 2
        if pm > 0:
            mark = make_mark(MH, ink, ck)
            s = 0.55 + 0.45 * pm
            rot = -14 * (1 - ease_out_cubic(prog(t, 0.15, 0.8)))
            mw, mh = max(1, int(mark.width * s)), max(1, int(mark.height * s))
            mk = mark.resize((mw, mh), Image.LANCZOS).rotate(rot, Image.BICUBIC, expand=True)
            mx = start_x + (lock_x - start_x) * sl
            px = mx + (mark_full.width - mk.width) / 2
            py = cy - mk.height / 2
            if a_m < 1:
                mk.putalpha(mk.getchannel("A").point(lambda v: int(v * a_m)))
            layer.alpha_composite(mk, (int(px), int(py)))

        if sl > 0:
            wx = start_x + (lock_x - start_x) * sl + mark_full.width + gap
            reveal = int(word.width * sl)
            if reveal > 0:
                wpart = word.crop((0, 0, reveal, word.height))
                off = int(40 * (1 - sl))
                layer.alpha_composite(wpart, (int(wx - off), int(cy - word.height / 2 + 6)))

        if tg > 0:
            tl = tag.copy()
            tl.putalpha(tl.getchannel("A").point(lambda v: int(v * tg)))
            layer.alpha_composite(tl, ((W - tag.width) // 2, int(cy + 150 + 18 * (1 - tg))))

        if out > 0:
            # minimales Heranzoomen beim Ausblenden
            s = 1 + 0.035 * out
            lw, lh = int(W * s), int(H * s)
            layer = layer.resize((lw, lh), Image.BICUBIC).crop(
                ((lw - W) // 2, (lh - H) // 2, (lw - W) // 2 + W, (lh - H) // 2 + H))
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * (1 - out))))
            fr = Image.blend(white, end_bg, out)

        fr.paste(layer, (0, 0), layer)
        yield fr


# --------------------------------------------------------------------------- Export

if __name__ == "__main__":
    import subprocess
    import sys
    sys.path.insert(0, HERE)
    from deal_video import font, make_background, ffmpeg_exe, W, H, FPS

    root = os.path.dirname(HERE)
    bfile = os.path.join(root, "marke.json")
    brand = load_brand(bfile if os.path.exists(bfile) else None)
    out = os.path.join(root, "marke")
    os.makedirs(out, exist_ok=True)
    ink, paper = hex_rgb(brand["ink"]), hex_rgb(brand["paper"])

    # Logo quer, transparent
    lk = make_lockup(brand, 300, font)
    lk.save(os.path.join(out, "logo_transparent.png"))
    # Profilbild (quadratisch, nur Bildmarke)
    pb = Image.new("RGB", (1080, 1080), paper)
    mk = make_mark(420, ink)
    pb.paste(mk, ((1080 - mk.width) // 2 + 20, (1080 - mk.height) // 2), mk)
    pb.save(os.path.join(out, "profilbild.png"))
    # Logo auf Weiss
    lw = Image.new("RGB", (lk.width + 240, lk.height + 240), paper)
    lw.paste(lk, (120, 120), lk)
    lw.save(os.path.join(out, "logo_weiss.png"))

    # Intro einzeln als Video
    bg = make_background(brand)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", os.path.join(out, "intro.mp4")]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in intro_frames(brand, W, H, FPS, font, bg):
        ff.stdin.write(f.tobytes())
    ff.stdin.close()
    ff.wait()
    print("Exportiert nach", out)
