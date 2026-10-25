"""
dealbar: baut aus deals.xlsx die Website (Ordner "website"): mobil zuerst, schnell, ohne Cookies.
Wird vom Workflow "Website" bei GitHub gebaut (und am PC von WEBSITE.bat). Einstellungen: website.json.

Was entsteht:
- index.html mit allen Deals (neuester gross oben, Suche nach Produkt oder Deal-Nr., Kategorien, Sortierung)
- je Deal eine eigene Seite unter /<Nr>/ zum Teilen (WhatsApp & Co. zeigen dann Bild und Titel)
- Impressum, Datenschutz, Affiliate-Hinweis, 404-Seite, sitemap.xml, Icons

Amazon-Regeln, die hier eingebaut sind:
- Euro-Preise nur, wenn "preise_in_euro_zeigen" an ist (erst mit Creators API sinnvoll).
- Rabatt (und Preis) erscheinen nur, solange "Preis geprüft am" juenger als "rabatt_zeigen_stunden" ist,
  immer mit Datum und Uhrzeit. Das prueft der Browser beim Aufruf; ohne JavaScript bleibt beides weg.
- Bilder: eigene Fotos aus dem Deal-Ordner oder SiteStripe-Bildcode (sitestripe.txt), nie Screenshots.
  Ohne Bild zeigt die Seite eine Kachel im Text-Design.
- Jeder Partnerlink hat rel="sponsored" und ein Sternchen; der Hinweis steht oben und unten auf jeder Seite.
"""

import html
import json
import os
import re
import shutil
import sys
import urllib.parse
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw, ImageOps  # noqa: E402

import brand as brandlib  # noqa: E402
import daten  # noqa: E402
from daten import CATEGORIES, DEALS_DIR, DISCLOSURE  # noqa: E402

ROOT = daten.ROOT
OUT = os.path.join(ROOT, "website")
WEB = os.path.join(HERE, "web")
HUES = {"Technik": 215, "Gaming": 265, "Haushalt": 32, "Küche": 14, "Mode": 330, "Beauty & Pflege": 300,
        "Sport & Freizeit": 145, "Spielzeug": 45, "Baby & Kind": 190, "Garten & Baumarkt": 95, "Tierbedarf": 24,
        "Sonstiges": 210}
LINK_ATTR = 'target="_blank" rel="sponsored nofollow noopener"'

ICON = {
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
              'aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "share": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><path d="M12 3v13"/><path d="m7 8 5-5 5 5"/>'
             '<path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg>',
    "arrow": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
    "telegram": '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21.9 4.3 18.7 19.4c-.2 '
                '1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.4-5 9-8.2c.4-.3-.1-.5-.6-.2L6.3 13 1.5 11.5c-1-.3'
                '-1-1 .2-1.5L20.6 2.8c.9-.3 1.6.2 1.3 1.5z"/></svg>',
}
SOCIAL = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube", "pinterest": "Pinterest",
          "facebook": "Facebook"}


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# --------------------------------------------------------------------------- Daten

def read_deals(settings):
    """Alle Deals mit gueltigem Link (auch offline genommene, fuer deren Deal-Seite). Neueste zuerst."""
    tag = daten.partner_tag(settings)
    euro_on = bool(settings.get("preise_in_euro_zeigen", False))
    deals, warnings = [], []
    with daten.open_table() as t:
        for r in t.rows():
            title, status = t.text(r, "Produktname"), t.text(r, "Status").lower()
            link = t.text(r, "Affiliate-Link")
            if not daten.valid_link(link):
                if status not in daten.HIDDEN:
                    warnings.append(f"Zeile {r} ({title[:40]}): kein gültiger Affiliate-Link, nicht auf der Website.")
                continue
            p_old, p_new = daten.num(t.get(r, "Alter Preis (€)")), daten.num(t.get(r, "Neuer Preis (€)"))
            show_price = t.text(r, "Preis zeigen").lower() not in ("nein", "n", "no")
            disc = daten.discount(p_old, p_new) if show_price else None
            checked = daten.parse_dt(t.get(r, "Preis geprüft am"))
            folder = t.text(r, "Ordner")
            deals.append({
                "nr": t.nr(r), "id": daten.slug(folder or title), "title": title, "hook": t.text(r, "Hook"),
                "label": t.text(r, "Label"), "cat": t.text(r, "Kategorie"), "link": daten.with_tag(link, tag),
                "folder": folder, "discount": disc,
                "old": p_old if (euro_on and disc) else None, "new": p_new if (euro_on and show_price) else None,
                "checked": checked, "date": checked or daten.parse_dt(t.get(r, "Erstellt am")),
                "online": status not in daten.HIDDEN, "row": r,
            })
    deals.sort(key=lambda d: (d["date"] or datetime.min, d.get("row", 0)), reverse=True)
    return deals, warnings


def deal_path(d):
    return f"{d['nr']}/" if d.get("nr") else f"d/{d['id']}/"


def sitestripe_image(folder):
    """Bild-URL aus dem SiteStripe-Code (Amazon: Text > Bild) in deals/<ordner>/sitestripe.txt."""
    p = os.path.join(DEALS_DIR, folder, "sitestripe.txt")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8", errors="ignore") as fh:
        code = fh.read()
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", code) or re.search(r"(https?://\S+)", code)
    if not m:
        return None
    src = html.unescape(m.group(1))
    return "https:" + src if src.startswith("//") else src


def flat(path):
    im = ImageOps.exif_transpose(Image.open(path))
    im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA", "PA") else im.convert("RGB")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        return bg
    return im


def og_image(brand, photo, d, path):
    """Vorschaubild 1200x630 fuer WhatsApp, Telegram & Co.: Foto (oder Text-Kachel), Logo, Titel."""
    from deal_video import font, make_text_card, wrap
    W, H = 1200, 630
    bg = Image.new("RGB", (W, H), brandlib.hex_rgb(brand["background"]))
    card = Image.new("RGB", (540, 540), (255, 255, 255))
    if photo is not None:
        p = photo.copy()
        p.thumbnail((470, 470), Image.LANCZOS)
        card.paste(p, ((540 - p.width) // 2, (540 - p.height) // 2))
    else:
        card = make_text_card({"category": d.get("cat"), "nr": d.get("nr"), "show_discount": False}, brand, 540)
        card = card.convert("RGB")
    mask = Image.new("L", (540, 540), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 539, 539), 40, fill=255)
    bg.paste(card, (45, 45), mask)
    ink, muted = brandlib.hex_rgb(brand["ink"]), brandlib.hex_rgb(brand["muted"])
    logo = brandlib.make_lockup(brand, 46, font)
    bg.paste(logo, (640, 70), logo)
    dr = ImageDraw.Draw(bg)
    y = 190
    if d.get("cat"):
        dr.text((640, y), d["cat"].upper(), font=font("bold", 26), fill=muted)
        y += 48
    for line in wrap(dr, d["title"], font("black", 50), 500, 4):
        dr.text((640, y), line, font=font("black", 50), fill=ink)
        y += 62
    if d.get("nr"):
        dr.text((640, 545), f"Deal #{d['nr']} · Jetzt ansehen", font=font("bold", 28), fill=ink)
    bg.save(path, "JPEG", quality=84, optimize=True)


def image_files(d, brand, photo, remote=None):
    """Foto (PIL-Bild oder None) -> webp in 2 Groessen + Vorschaubild fuer WhatsApp & Co."""
    name = f"{d['nr']}-{d['id']}" if d.get("nr") else d["id"]
    og = f"bilder/{name}-og.jpg"
    og_image(brand, photo, d, os.path.join(OUT, og))
    if remote:
        return {"src": remote, "remote": True, "og": og}
    if photo is None:
        return {"og": og}
    out = {}
    for w in (360, 720):
        im = photo.copy()
        im.thumbnail((w, w), Image.LANCZOS)
        rel = f"bilder/{name}-{w}.webp"
        im.save(os.path.join(OUT, rel), "WEBP", quality=80, method=6)
        out[w] = (rel, im.width, im.height)
    return {"src": out[360][0], "srcset": f"{out[360][0]} 360w, {out[720][0]} 720w", "w": out[720][1],
            "h": out[720][2], "og": og}


def deal_image(d, brand, warnings):
    """Eigenes Foto aus dem Deal-Ordner, sonst SiteStripe-Bild (laedt direkt von Amazon), sonst Text-Kachel."""
    folder = d.get("folder") or ""
    remote = sitestripe_image(folder) if folder else None
    imgs = daten.images_in(folder)
    photo = None
    if imgs and not remote:
        try:
            photo = flat(os.path.join(DEALS_DIR, folder, imgs[0]))
        except Exception as ex:
            warnings.append(f"#{d.get('nr')} {d['title'][:40]}: Bild nicht lesbar ({ex}).")
    elif not remote:
        path = os.path.join(DEALS_DIR, folder)
        if folder and os.path.isdir(path) and any(f.lower().startswith(daten.SCREENSHOT) for f in os.listdir(path)):
            warnings.append(f"#{d.get('nr')} {d['title'][:40]}: nur Screenshots im Ordner (nicht erlaubt), "
                            "die Website zeigt eine Text-Kachel.")
    return image_files(d, brand, photo, remote)


# --------------------------------------------------------------------------- Bausteine

SIZES_CARD = "(min-width:1180px) 270px, (min-width:960px) 30vw, (min-width:640px) 45vw, 112px"
SIZES_LEAD = "(min-width:1180px) 560px, (min-width:640px) 48vw, 92vw"


def picture(d, base, lead=False, eager=False, alt=""):
    img = d.get("img") or {}
    if img.get("src"):
        load = 'fetchpriority="high"' if eager else 'loading="lazy"'
        if img.get("remote"):
            return f'<img src="{e(img["src"])}" alt="{e(alt)}" {load} decoding="async" referrerpolicy="no-referrer">'
        srcset = ", ".join(base + part for part in img["srcset"].split(", "))
        return (f'<img src="{e(base + img["src"])}" srcset="{e(srcset)}" sizes="{SIZES_LEAD if lead else SIZES_CARD}" '
                f'data-sizes="{SIZES_CARD}" data-lead-sizes="{SIZES_LEAD}" width="{img["w"]}" height="{img["h"]}" '
                f'alt="{e(alt)}" {load} decoding="async">')
    emoji = CATEGORIES.get(d.get("cat"), ("🛍️",))[0]
    return (f'<span class="tile" style="--h:{HUES.get(d.get("cat"), 12)}" aria-hidden="true"><b>{emoji}</b>'
            f'<small>{e(d.get("cat") or "Deal")}</small></span>')


def deal_attrs(d):
    q = " ".join(x for x in (d["title"], d.get("hook"), d.get("cat"), d.get("label"),
                             f"#{d['nr']}" if d.get("nr") else "") if x).lower()
    return (f'data-deal id="{e(d["id"])}" data-nr="{d.get("nr") or ""}" data-cat="{e(d.get("cat"))}" '
            f'data-checked="{daten.iso(d.get("checked"))}" data-date="{daten.iso(d.get("date"))}" '
            f'data-off="{d.get("discount") or ""}" data-q="{e(q)}"')


def badges(d):
    out = f'<span class="off" hidden>−{d["discount"]} %</span>' if d.get("discount") else ""
    return out + '<span class="new" hidden>NEU</span>'


def price_html(d):
    if not d.get("new"):
        return ""
    old = f"<s>{e(daten.euro(d['old']))}</s>" if d.get("old") else ""
    return f'<p class="price" hidden><b>{e(daten.euro(d["new"]))}*</b>{old}</p>'


def meta_html(d):
    cat = d.get("cat")
    parts = []
    if d.get("label") and d["label"].upper() not in ("TIPP",):
        parts.append(f'<span class="lbl">{e(d["label"].upper())}</span>')
    if cat:
        emoji, short = CATEGORIES.get(cat, ("🏷️", cat))[:2]
        parts.append(f'<span class="cat">{emoji} {e(short)}</span>')
    parts.append("<span data-ago></span>")
    if d.get("nr"):
        parts.append(f'<span class="nr">#{d["nr"]}</span>')
    return '<p class="meta">' + "".join(parts) + "</p>"


def card(d, base, lead=False, eager=False, heading="h2"):
    link = e(d["link"])
    return f"""<article class="card{' lead' if lead else ''}" {deal_attrs(d)}>
<a class="pic" href="{link}" {LINK_ATTR} tabindex="-1" aria-hidden="true">{picture(d, base, lead, eager)}{badges(d)}</a>
<div class="info">{meta_html(d)}
<{heading} class="title"><a href="{link}" {LINK_ATTR}>{e(d["title"])}</a></{heading}>
{f'<p class="hook">{e(d["hook"])}</p>' if d.get("hook") else ''}{price_html(d)}
<p class="stamp" data-stamp>Aktuellen Preis bei Amazon ansehen.</p>
<div class="actions"><a class="cta" href="{link}" {LINK_ATTR}><span>Zum Deal<span class="cta-long"> bei Amazon</span>*</span>\
{ICON["arrow"]}</a>\
<button class="share" type="button" data-share="{e(base + deal_path(d))}" data-title="{e(d["title"])}" \
aria-label="Deal teilen">{ICON["share"]}</button></div>
</div>
</article>"""


def brand_css(brand):
    def lum(rgb):
        c = [x / 255 for x in rgb]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    cta = brandlib.hex_rgb(brand["accent"])
    while (1.05 / (lum(cta) + 0.05)) < 4.5:  # weisse Schrift auf dem Knopf gut lesbar (WCAG AA)
        cta = tuple(max(0, int(x * 0.96)) for x in cta)
    hexa = "#%02X%02X%02X" % cta
    return (f":root{{--brand-ink:{brand['ink']};--brand-paper:{brand['paper']};--brand-bg:{brand['background']};"
            f"--brand-accent:{brand['accent']};--brand-cta:{hexa}}}")


def css(brand):
    with open(os.path.join(WEB, "style.css"), encoding="utf-8") as fh:
        style = fh.read()
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    style = re.sub(r"\s*\n\s*", "", style)
    return brand_css(brand) + style


def js(settings):
    with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
        code = fh.read()
    conf = {"maxh": float(settings.get("rabatt_zeigen_stunden", 24))}
    return f"window.DEALBAR={json.dumps(conf)};\n" + code


def page(ctx, title, main, base="", desc=None, og=None, url=None, robots="", extra_head=""):
    settings, brand, site = ctx["settings"], ctx["brand"], ctx["site"]
    desc = desc or settings.get("beschreibung", "")
    name = brand["name_bold"] + brand["name_light"]
    og_img = og or "og.png"
    abs_img = f"{site}/{og_img}" if site else base + og_img
    canon = f'<link rel="canonical" href="{e(url)}">\n<meta property="og:url" content="{e(url)}">' if url else ""
    tg = daten.channel_link(settings)
    top_links = (f'<a class="pill" href="{e(tg)}" target="_blank" rel="noopener">{ICON["telegram"]}'
                 f'<span>Deals aufs Handy</span></a>') if tg else ""
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
{robots}<meta name="theme-color" content="{e(brand['background'])}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e0e0f" media="(prefers-color-scheme: dark)">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{e(name)}">
<meta property="og:locale" content="de_DE">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="{e(abs_img)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
{canon}
<link rel="icon" href="{base}favicon.png" type="image/png">
<link rel="apple-touch-icon" href="{base}apple-touch-icon.png">
<link rel="manifest" href="{base}manifest.webmanifest">
<link rel="preload" href="{base}fonts/inter-900.woff2" as="font" type="font/woff2" crossorigin>
<style>{ctx["css"].replace("url(fonts/", f"url({base}fonts/")}</style>
{extra_head}</head>
<body>
<a class="skip" href="#main">Zum Inhalt</a>
<div class="ad">Anzeige: Links mit * sind Amazon-Partnerlinks. {DISCLOSURE}</div>
<header class="top"><div class="wrap">
<a class="brand" href="{base or './'}" aria-label="{e(name)} Startseite"><picture>\
<source srcset="{base}logo-hell.png" media="(prefers-color-scheme: dark)">\
<img src="{base}logo.png" alt="{e(name)}" width="{ctx['logo_w']}" height="{ctx['logo_h']}"></picture></a>
<nav class="top-links" aria-label="Folgen">{top_links}</nav>
</div></header>
<main id="main">
{main}
</main>
<footer><div class="wrap">
<nav aria-label="Rechtliches"><a href="{base or './'}">Alle Deals</a><a href="{base}affiliate-hinweis.html">Affiliate-Hinweis</a>\
<a href="{base}impressum.html">Impressum</a><a href="{base}datenschutz.html">Datenschutz</a></nav>
<p>* Amazon-Partnerlink (Werbung). {DISCLOSURE} Für dich ändert sich der Preis dadurch nicht.</p>
<p>Rabatte gelten zum angegebenen Zeitpunkt und können sich jederzeit ändern. Maßgeblich ist der Preis auf Amazon \
zum Zeitpunkt des Kaufs. Amazon und das Amazon-Logo sind Warenzeichen von Amazon.com, Inc. oder eines seiner \
verbundenen Unternehmen.</p>
</div></footer>
<script>{ctx["js"]}</script>
</body>
</html>
"""


def follow_box(settings):
    tg = daten.channel_link(settings)
    social = [(SOCIAL[k], v) for k, v in (settings.get("social") or {}).items() if v and k in SOCIAL]
    if not (tg or social):
        return ""
    btn = (f'<a class="btn" href="{e(tg)}" target="_blank" rel="noopener">{ICON["telegram"]} Telegram-Kanal folgen</a>'
           if tg else "")
    links = "".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(n)} ↗</a>' for n, u in social)
    text = "Neue Deals sofort aufs Handy, kostenlos im Telegram-Kanal." if tg else "Folge dealbar für neue Deals."
    return f"""<section class="follow wrap" aria-label="Folgen"><div class="box">
<div><h2>Keinen Deal mehr verpassen</h2><p>{text}</p>{f'<div class="social">{links}</div>' if links else ''}</div>{btn}
</div></section>"""


# --------------------------------------------------------------------------- Seiten

def index_main(ctx, deals):
    settings = ctx["settings"]
    online = [d for d in deals if d["online"]]
    cutoff = datetime.now() - timedelta(days=float(settings.get("aeltere_deals_ab_tagen", 21)))
    fresh = [d for d in online if (d.get("date") or datetime.now()) >= cutoff]
    older = [d for d in online if d not in fresh]
    if not fresh and older:  # nie eine leere Startseite
        fresh, older = older[:8], older[8:]
    cats = [c for c in CATEGORIES if any(d.get("cat") == c for d in online)]
    cats += sorted({d["cat"] for d in online if d.get("cat") and d["cat"] not in CATEGORIES})
    chips = ""
    if len(cats) > 1:
        chips = '<button class="chip" type="button" data-cat="" aria-pressed="true">Alle</button>' + "".join(
            f'<button class="chip" type="button" data-cat="{e(c)}" aria-pressed="false">'
            f'{CATEGORIES.get(c, ("🏷️",))[0]} {e(CATEGORIES.get(c, (None, c))[1])}</button>' for c in cats)
    cards = "\n".join(card(d, "", lead=(i == 0), eager=(i < 2)) for i, d in enumerate(fresh))
    older_html = ""
    if older:
        older_html = (f'<details class="older" id="older"><summary>Ältere Deals ansehen ({len(older)})</summary>'
                      f'<div class="grid" data-list="older">' + "\n".join(card(d, "") for d in older) +
                      "</div></details>")
    empty_txt = ("Gerade sind keine Deals online. Schau bald wieder vorbei!" if not online else
                 "Keine Deals für diese Auswahl.")
    n = len(online)
    sort = ('<label class="sort"><span class="sr">Sortieren nach</span><select id="sort"><option value="new">Neueste'
            '</option><option value="off">Rabatt</option></select></label>') if n >= 3 else ""
    bar = (f'<div class="bar"><div class="wrap"><div class="chips" id="chips" role="group" aria-label="Kategorien">'
           f'{chips}</div>{sort}</div></div>\n') if (chips or sort) else ""
    return f"""<section class="hero wrap">
<h1>Die besten <span class="nw">Amazon-Deals.</span><br><em>Von Hand geprüft.</em></h1>
<p class="lead">Täglich neue Angebote mit echtem Rabatt. Ohne Abo, ohne Anmeldung, ohne Cookies.</p>
<form class="search" role="search" onsubmit="return false">{ICON["search"]}
<label class="sr" for="q">Deals durchsuchen</label>
<input id="q" type="search" placeholder="Produkt oder Deal-Nr. suchen" autocomplete="off" enterkeyhint="search">
</form>
<p class="live"><span class="dot" aria-hidden="true"></span><span id="live">{n} Deal{'s' if n != 1 else ''} online</span></p>
</section>
{bar}<section class="deals wrap" aria-label="Deals">
<div class="grid" data-list="main">
{cards}
</div>
{older_html}
<div class="empty" id="empty"{'' if not online else ' hidden'}><p>{empty_txt}</p>\
{'<button class="btn" id="reset" type="button">Alle Deals zeigen</button>' if online else ''}</div>
</section>
{follow_box(settings)}"""


def deal_main(ctx, d, others):
    base = "../"
    link = e(d["link"])
    gone = "" if d["online"] else (
        '<p class="gone"><b>Dieser Deal ist leider abgelaufen.</b> Das Produkt findest du trotzdem bei Amazon, '
        f'aktuelle Deals <a href="{base}">gibt es hier</a>.</p>')
    more = "\n".join(card(x, base, heading="h3") for x in others)
    more_html = f'<section class="more" aria-label="Weitere Deals"><h2>Weitere Deals</h2><div class="grid">{more}</div></section>' if more else ""
    cta = "Zum Deal bei Amazon" if d["online"] else "Produkt bei Amazon ansehen"
    return f"""<div class="wrap">
<a class="back" href="{base}">← Alle Deals</a>
{gone}<article class="dp" {deal_attrs(d)}>
<a class="pic" href="{link}" {LINK_ATTR} tabindex="-1" aria-hidden="true">{picture(d, base, lead=True, eager=True, alt=d["title"])}\
{badges(d) if d["online"] else ''}</a>
<div class="info">{meta_html(d)}
<h1>{e(d["title"])}</h1>
{f'<p class="hook">{e(d["hook"])}</p>' if d.get("hook") else ''}{price_html(d) if d["online"] else ''}
<p class="stamp" data-stamp>Aktuellen Preis bei Amazon ansehen.</p>
<div class="actions"><a class="cta" href="{link}" {LINK_ATTR}><span>{cta}*</span>{ICON["arrow"]}</a>
<button class="share" type="button" data-share="./" data-title="{e(d["title"])}">{ICON["share"]} Deal teilen</button></div>
<p class="fine">* Amazon-Partnerlink. Preis und Verfügbarkeit können sich jederzeit ändern; maßgeblich ist der Preis \
bei Amazon zum Zeitpunkt des Kaufs.</p>
</div>
</article>
{more_html}
</div>
{follow_box(ctx["settings"])}"""


def legal_main(title, body):
    return f'<div class="wrap legal"><h1>{e(title)}</h1>{body}</div>'


def impressum_body(settings):
    i = {k: e(v) for k, v in settings.get("impressum", {}).items()}
    tel = f"<br>Telefon: {i['telefon']}" if i.get("telefon") else ""
    warn = "" if daten.impressum_ok(settings) else (
        '<p class="warn">Noch nicht ausgefüllt: Schick dem dealbar-Bot <b>/impressum</b> oder trage die Angaben '
        'in website.json ein.</p>')
    return f"""{warn}<div class="box">
<h2>Angaben gemäß § 5 DDG</h2>
<p>{i.get('name', '')}<br>{i.get('strasse', '')}<br>{i.get('plz_ort', '')}</p>
<h2>Kontakt</h2>
<p>E-Mail: <a href="mailto:{i.get('email', '')}">{i.get('email', '')}</a>{tel}</p>
<h2>Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV</h2>
<p>{i.get('name', '')}, Anschrift wie oben</p>
<h2>Hinweis zu Partnerlinks</h2>
<p>Diese Website enthält Amazon-Partnerlinks. {DISCLOSURE} Mehr dazu im <a href="affiliate-hinweis.html">Affiliate-Hinweis</a>.</p>
<h2>Haftung für Links</h2>
<p>Diese Website enthält Links zu externen Websites, auf deren Inhalte ich keinen Einfluss habe. Für diese Inhalte ist der jeweilige Anbieter verantwortlich. Bei Bekanntwerden von Rechtsverletzungen entferne ich solche Links umgehend.</p>
<h2>Verbraucherstreitbeilegung</h2>
<p>Ich bin nicht bereit und nicht verpflichtet, an Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen.</p>
</div>"""


def affiliate_body():
    return f"""<div class="box">
<p><strong>{DISCLOSURE}</strong></p>
<p>dealbar ist Teilnehmer des Partnerprogramms von Amazon EU, das zur Bereitstellung eines Mediums für Websites konzipiert wurde, mittels dessen durch die Platzierung von Werbeanzeigen und Links zu Amazon.de Werbekostenerstattung verdient werden kann.</p>
<p>Alle Links zu Amazon auf dieser Seite sind Partnerlinks und mit einem Sternchen (*) gekennzeichnet. Kaufst du über einen solchen Link, erhalte ich eine kleine Provision. Für dich ändert sich der Preis dadurch nicht.</p>
<h2>Preise und Rabatte</h2>
<p>Rabatte werden mit Datum und Uhrzeit der Prüfung angezeigt und nur für kurze Zeit. Preise und Verfügbarkeit können sich jederzeit ändern. Maßgeblich ist allein der Preis, der zum Zeitpunkt des Kaufs auf Amazon.de angezeigt wird.</p>
<h2>Auswahl der Deals</h2>
<p>Ich wähle die Angebote selbst aus. Die Provision hat keinen Einfluss darauf, welche Produkte ich zeige.</p>
</div>"""


def datenschutz_body(settings, uses_amazon_images):
    i = {k: e(v) for k, v in settings.get("impressum", {}).items()}
    img_txt = ""
    if uses_amazon_images:
        img_txt = """<h2>Produktbilder von Amazon</h2>
<p>Einige Produktbilder werden direkt von Servern von Amazon geladen (Amazon-Partnerprogramm, SiteStripe). Dabei wird deine IP-Adresse an Amazon übertragen. Rechtsgrundlage ist mein berechtigtes Interesse an einer korrekten Darstellung der Angebote (Art. 6 Abs. 1 lit. f DSGVO). Anbieter: Amazon Europe Core S.à r.l., 38 Avenue John F. Kennedy, L-1855 Luxemburg.</p>"""
    tg = ""
    if daten.channel_link(settings):
        tg = """<h2>Telegram</h2>
<p>Auf meinen Telegram-Kanal wird nur verlinkt. Daten werden erst übertragen, wenn du den Link anklickst; dann gilt die Datenschutzerklärung von Telegram.</p>"""
    return f"""<div class="box">
<h2>Verantwortlicher</h2>
<p>{i.get('name', '')}, {i.get('strasse', '')}, {i.get('plz_ort', '')}<br>E-Mail: {i.get('email', '')}</p>
<h2>Keine Cookies, kein Tracking</h2>
<p>Diese Website setzt keine Cookies und verwendet keine Analyse- oder Tracking-Werkzeuge. Schriftarten werden von dieser Website selbst ausgeliefert, nicht von Google.</p>
<h2>Hosting und Server-Logfiles</h2>
<p>Diese Website wird bei GitHub Pages gehostet (GitHub Inc., 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, USA). Beim Aufruf verarbeitet GitHub technisch notwendige Daten, z. B. IP-Adresse, Datum und Uhrzeit, aufgerufene Seite und Browser, um die Seite sicher auszuliefern (Art. 6 Abs. 1 lit. f DSGVO). Dabei können Daten in die USA übertragen werden; Grundlage dafür sind das EU-US Data Privacy Framework bzw. die EU-Standardvertragsklauseln. Mehr dazu in der <a href="https://docs.github.com/de/site-policy/privacy-policies/github-general-privacy-statement" target="_blank" rel="noopener">Datenschutzerklärung von GitHub</a>.</p>
{img_txt}
<h2>Amazon-Partnerlinks</h2>
<p>Klickst du auf einen Partnerlink, wirst du zu Amazon weitergeleitet. Amazon kann dort Cookies setzen, um nachzuvollziehen, dass der Kauf über diese Seite kam. Dafür gilt die Datenschutzerklärung von Amazon. Ich erhalte von Amazon keine personenbezogenen Daten über dich, nur anonyme Umsatzzahlen.</p>
{tg}
<h2>Deine Rechte</h2>
<p>Du hast das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit und Widerspruch (Art. 15 bis 21 DSGVO) sowie das Recht, dich bei einer Datenschutz-Aufsichtsbehörde zu beschweren.</p>
</div>"""


def notfound_main():
    return """<div class="wrap legal" style="text-align:center;padding-top:40px">
<h1>Hier ist leider nichts (mehr).</h1>
<p>Vielleicht ist der Deal abgelaufen. Die aktuellen Angebote findest du auf der Startseite.</p>
<p><a class="btn" href="./">Zu den aktuellen Deals</a></p></div>"""


# --------------------------------------------------------------------------- Bauen

def assets(brand):
    """Logos, Icons und Vorschaubild aus der Marke erzeugen. Gibt die Logo-Groesse zurueck."""
    from deal_video import font
    ink, paper = brandlib.hex_rgb(brand["ink"]), brandlib.hex_rgb(brand["paper"])
    bgc = brandlib.hex_rgb(brand["background"])
    logo = brandlib.make_lockup(brand, 96, font)
    logo.save(os.path.join(OUT, "logo.png"), optimize=True)
    brandlib.make_lockup(brand, 96, font, ink=(243, 242, 238)).save(os.path.join(OUT, "logo-hell.png"), optimize=True)
    mark = brandlib.make_mark(96, ink)
    fav = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    fav.alpha_composite(mark, ((128 - mark.width) // 2, (128 - mark.height) // 2))
    fav.save(os.path.join(OUT, "favicon.png"))
    for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        ic = Image.new("RGBA", (size, size), paper + (255,))
        m = brandlib.make_mark(int(size * 0.5), ink)
        ic.alpha_composite(m, ((size - m.width) // 2 + size // 40, (size - m.height) // 2))
        ic.convert("RGB").save(os.path.join(OUT, name))
    og = Image.new("RGB", (1200, 630), bgc)
    lk = brandlib.make_lockup(brand, 130, font)
    og.paste(lk, ((1200 - lk.width) // 2, 200), lk)
    tg = brandlib.make_tagline(brand.get("tagline", ""), 34, brandlib.hex_rgb(brand["muted"]), font)
    og.paste(tg, ((1200 - tg.width) // 2, 400), tg)
    og.save(os.path.join(OUT, "og.png"), optimize=True)
    return logo.width * 30 // logo.height, 30


def write(rel, content):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def build():
    print("dealbar Website-Generator\n")
    if not os.path.exists(daten.TABLE):
        sys.exit(f"Tabelle nicht gefunden: {daten.TABLE}")
    settings = daten.load_settings()
    brand = brandlib.load_brand(os.path.join(ROOT, "marke.json"))
    site = daten.site_url(settings)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "bilder"))
    os.makedirs(os.path.join(OUT, "fonts"))
    for f in os.listdir(os.path.join(HERE, "fonts", "web")):
        shutil.copy(os.path.join(HERE, "fonts", "web", f), os.path.join(OUT, "fonts", f))
    shutil.copy(os.path.join(HERE, "fonts", "LICENSE.txt"), os.path.join(OUT, "fonts", "LICENSE.txt"))
    logo_w, logo_h = assets(brand)

    deals, warnings = read_deals(settings)
    for d in deals:
        if "img" not in d:
            d["img"] = deal_image(d, brand, warnings)
    ctx = {"settings": settings, "brand": brand, "site": site, "css": css(brand), "js": js(settings),
           "logo_w": logo_w, "logo_h": logo_h}
    title = settings.get("titel", "dealbar")
    name = brand["name_bold"] + brand["name_light"]
    write("index.html", page(ctx, title, index_main(ctx, deals), url=f"{site}/" if site else None))
    online = [d for d in deals if d["online"]]
    for d in deals:
        others = [x for x in online if x is not d][:4]
        desc = d.get("hook") or f"Von Hand geprüfter Amazon-Deal bei {name}. Jetzt ansehen, bevor er weg ist."
        img = (d.get("img") or {}).get("og")
        write(deal_path(d) + "index.html", page(
            ctx, f"{d['title']} – Deal bei {name}", deal_main(ctx, d, others), base="../" * deal_path(d).count("/"),
            desc=desc, og=img, url=f"{site}/{deal_path(d)}" if site else None,
            robots="" if d["online"] else '<meta name="robots" content="noindex">\n'))
    uses_remote = any((d.get("img") or {}).get("remote") for d in deals)
    for fname, t, body in (
            ("impressum.html", "Impressum", impressum_body(settings)),
            ("datenschutz.html", "Datenschutzerklärung", datenschutz_body(settings, uses_remote)),
            ("affiliate-hinweis.html", "Affiliate-Hinweis", affiliate_body())):
        write(fname, page(ctx, f"{t} – {name}", legal_main(t, body), url=f"{site}/{fname}" if site else None))
    base404 = urllib.parse.urlparse(site).path.rstrip("/") + "/" if site else ""
    write("404.html", page(ctx, f"Nicht gefunden – {name}", notfound_main(), robots='<meta name="robots" content="noindex">\n',
                           extra_head=f'<base href="{e(base404)}">\n' if base404 else ""))
    write("manifest.webmanifest", json.dumps({
        "name": name, "short_name": name, "start_url": "./", "display": "minimal-ui", "lang": "de",
        "background_color": brand["background"], "theme_color": brand["background"],
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}, ensure_ascii=False))
    if site:
        urls = [f"{site}/"] + [f"{site}/{deal_path(d)}" for d in online] + \
               [f"{site}/{f}" for f in ("affiliate-hinweis.html", "impressum.html", "datenschutz.html")]
        write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
              "".join(f"<url><loc>{e(u)}</loc></url>\n" for u in urls) + "</urlset>\n")
        write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site}/sitemap.xml\n")

    print(f"{len(online)} Deals online, {len(deals) - len(online)} offline (Deal-Seite bleibt mit Hinweis).")
    if not daten.impressum_ok(settings):
        warnings.insert(0, "Impressum: noch nicht ausgefüllt. Im Telegram-Bot /impressum schicken!")
    if not daten.partner_tag(settings):
        warnings.insert(0, "Partner-ID fehlt: Links bringen noch keine Provision. Im Telegram-Bot /partnerid schicken.")
    if warnings:
        print("\nHinweise:")
        for w in warnings:
            print("  - " + w)
    print(f"\nFertig: {OUT}")


if __name__ == "__main__":
    build()
