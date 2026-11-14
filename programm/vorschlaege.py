"""
dealbar: Deal-Vorschlaege fuer den Telegram-Bot.

Quelle ist der oeffentliche RSS-Feed von mydealz, nur als Hinweisgeber: Wir uebernehmen Produktname,
Kategorie und den genannten Preis als Startwert, aber keine Texte und keine Bilder von dort. Amazon-Seiten
werden nie abgerufen. Die Produktnummer (ASIN) kommt aus der Weiterleitung von mydealz zu Amazon, die wir
nur lesen und nicht verfolgen. Den Preis prueft Johannes vor dem Posten selbst in der Amazon-App.

    python programm/vorschlaege.py      # zum Ausprobieren: aktuelle Vorschlaege anzeigen
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import daten

FEED_URL = "https://www.mydealz.de/rss/{}"
VISIT_URL = "https://www.mydealz.de/visit/thread/{}"
NS = {"pepper": "http://www.pepper.com/rss"}

DEFAULTS = {
    "aktiv": True,
    "zeiten": ["08:00", "18:00"],   # Uhrzeiten (Berlin), zu denen der Bot Vorschlaege schickt
    "anzahl": 6,                    # hoechstens so viele pro Liste: lieber wenige gute als viele
    "feeds": ["trending", "alles"],  # mydealz: heiss gerade / alle neuen
    "min_grad": 50,                 # mindestens so "heiss" auf mydealz
    "nische": [],                   # dealbar-Kategorien, leer = alle
    "nur_marken": True,             # nur Produkte bekannter Qualitaetsmarken (MARKEN + marken_plus)
    "marken_plus": [],              # eigene Marken dazu (per /marken + Name)
    "marken_minus": [],             # Marken nie vorschlagen (per /marken - Name)
}

# Bekannte Qualitaetsmarken: Produkte, denen man ohne Erklaerung vertraut. No-Name-Ware faellt raus.
MARKEN = """
WMF, Fissler, Zwilling, Le Creuset, Rösle, Silit, Tefal, Emile Henry, Wüsthof, Victorinox, Opinel,
KitchenAid, Kenwood, Bosch, Siemens, Miele, AEG, Neff, Gaggenau, Liebherr, Braun, Philips, De'Longhi, Krups,
Jura, Melitta, Nespresso, Sage, Smeg, Russell Hobbs, Severin, Rommelsbacher, Graef, Ninja, Cosori, Tefal,
Vorwerk, Kärcher, Dyson, Vileda, Leifheit, Rowenta, iRobot, Roborock, Ecovacs, Dreame, Shark, Vitamix,
Brita, SodaStream, Emsa, Rotpunkt, Villeroy & Boch, Rosenthal, Hutschenreuther, Leonardo, Riedel,
Schott Zwiesel, Spiegelau, Kahla, Asa Selection, Blomus, Stelton, Eva Solo, Alessi, Joseph Joseph,
OXO, Lodge, Petromax, Weber, Napoleon, Outdoorchef, Gardena, Fiskars, Stihl, Einhell, Makita, Metabo,
Festool, Fein, Milwaukee, DeWalt, Black+Decker, Wera, Wiha, Knipex, Gedore, Hazet, Stabila, Leatherman,
Sony, Bose, Sennheiser, Beyerdynamic, JBL, Teufel, Sonos, Bang & Olufsen, Marshall, Apple, Samsung, LG,
Panasonic, Canon, Nikon, Fujifilm, GoPro, DJI, Garmin, Polar, Suunto, Withings, Logitech, Anker, Ugreen,
Belkin, AVM, Fritz, Netgear, TP-Link, Eufy, Google Nest, Google, Amazon Echo, Kindle, Fire TV, Tado, Netatmo,
Philips Hue, Nanoleaf, Ledvance, Osram, Varta, Duracell, Eneloop, Microsoft, Nintendo, PlayStation, Xbox,
Razer, SteelSeries, Corsair, Elgato, Western Digital, SanDisk, Crucial, Kingston, Lenovo, Asus, Acer, HP,
Dell, Brother, Epson, Oral-B, Philips Sonicare, Panasonic, Remington, Wahl, Babyliss, ghd, Dyson,
Nivea, Eucerin, La Roche-Posay, Vichy, Weleda, Clinique, Lancôme, L'Oréal, Kiehl's, Paula's Choice,
The Ordinary, Rituals, Tom Ford, Hugo Boss, Calvin Klein, Ralph Lauren, Tommy Hilfiger, Levi's, Lacoste,
Adidas, Nike, Puma, Asics, New Balance, Saucony, Brooks, Hoka, On Running, Salomon, Lowa, Meindl, Hanwag,
Birkenstock, Ecco, Timberland, Dr. Martens, Converse, Vans, Helly Hansen, The North Face, Patagonia,
Jack Wolfskin, Mammut, Arc'teryx, Vaude, Fjällräven, Columbia, Schöffel, Bergans, Deuter, Osprey, Ortlieb,
Thule, Samsonite, Rimowa, Eastpak, Herschel, Casio, Seiko, Citizen, Tissot, Fossil, Lego, Playmobil, Ravensburger,
Haba, Schleich, Hape, Brio, Tonies, Tiptoi, Kosmos, Carrera, Hot Wheels, Mattel, Hasbro, Märklin, Steiff,
Cybex, Maxi-Cosi, Britax, Römer, Stokke, Joie, Hauck, Avent, MAM, NUK, Pampers, Royal Canin, Trixie, Kong,
Tchibo, Kitchenaid, Hario, Bialetti, Moccamaster, Rancilio, Gaggia, Lavazza, Illy, Dallmayr, Tassimo,
Concept2, Kettler, Technogym, Garmin, Wahoo, Shimano, SRAM, Abus, Bosch eBike, Cube, Canyon, Schwalbe,
Continental, Michelin, Thermos, Stanley, Yeti, Hydro Flask, Klean Kanteen, Sigg, Chilly's, Dopper,
Coleman, Campingaz, Primus, MSR, Tatonka, Gerber, Morakniv, Ledlenser, Fenix, Bodum, Le Parfait, Weck,
Tupperware, Lock & Lock, Curver, Wenko, Zeller, Brabantia, Simplehuman, Hailo, Kesper, Continenta,
Ikea, Muji, Hay, Muuto, Vitra, Tempur, Emma, Dormando, Kneer, Estella, Möve, Vossen,
Frottana, Cawö, Bassetti, Erwin Müller, Irisette, Lattoflex, Swiss Sense
"""


def _norm(x):
    x = str(x or "").lower().replace("’", "'")
    x = x.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e", "ô": "o"}))
    return re.sub(r"[^a-z0-9+&]+", " ", x.replace("'", "")).strip()


def brand_list(cfg):
    minus = {_norm(b) for b in cfg.get("marken_minus") or []}
    names = [b.strip() for b in MARKEN.replace("\n", " ").split(",")] + list(cfg.get("marken_plus") or [])
    out = {}
    for b in names:
        n = _norm(b)
        if n and n not in minus:
            out.setdefault(n, b.strip())
    return out


def find_brand(title, brands):
    """Erste bekannte Marke im Produktnamen (oder None). Laengere Namen gewinnen ('Philips Hue' vor 'Philips')."""
    t = f" {_norm(title)} "
    best = None
    for n, name in brands.items():
        i = t.find(f" {n} ")
        if i >= 0 and (best is None or (i, -len(n)) < best[0]):
            best = ((i, -len(n)), name)
    return best[1] if best else None

# Woerter im mydealz-Titel, die nichts mit dem Produkt zu tun haben
TITLE_JUNK = [
    r"^\s*-?\d+\s*°\s*-\s*",                                          # "116° - "
    r"\[[^\]]*\]", r"\((?:[^()]*(?:prime|spar-?abo|amazon|gutschein|coupon|personalisiert|bestpreis|"
    r"tiefstpreis|eur|€|%)[^()]*)\)",
    r"\b(?:amazon(?:\.de)?|prime|spar-?abo|bestpreis|tiefstpreis|bestpreis|neuer\s+bestpreis|gutschein|"
    r"coupon|deal|angebot|knaller|preisfehler)\b[!:]?",
    r"\b(?:für|fuer|nur|ab|statt|jetzt|zum)\s+(?=\d)",
]
HINTS = (("spar-abo", "nur im Spar-Abo"), ("spar abo", "nur im Spar-Abo"), ("gutschein", "mit Gutschein"),
         ("coupon", "mit Coupon"), ("personalisiert", "personalisierter Preis"), ("prime exklusiv", "nur für Prime"),
         ("prime-exklusiv", "nur für Prime"),
         ("marketplace", "Marketplace-Händler"), ("warehouse", "Amazon Warehouse (gebraucht)"))

# mydealz-Kategorien, die match_category nicht kennt
EXTRA_CATS = {"home": "Haushalt", "living": "Haushalt", "wohnen": "Haushalt", "haushalt": "Haushalt",
              "küche": "Küche", "kueche": "Küche", "lebensmittel": "Küche", "getränke": "Küche",
              "elektronik": "Technik", "computer": "Technik", "telefon": "Technik", "handy": "Technik",
              "audio": "Technik", "tv": "Technik", "gaming": "Gaming", "spiele": "Gaming",
              "mode": "Mode", "fashion": "Mode", "accessoires": "Mode", "beauty": "Beauty & Pflege",
              "drogerie": "Beauty & Pflege", "gesundheit": "Beauty & Pflege", "sport": "Sport & Freizeit",
              "freizeit": "Sport & Freizeit", "fahrrad": "Sport & Freizeit", "spielzeug": "Spielzeug",
              "familie": "Baby & Kind", "kinder": "Baby & Kind", "baby": "Baby & Kind",
              "garten": "Garten & Baumarkt", "baumarkt": "Garten & Baumarkt", "heimwerk": "Garten & Baumarkt",
              "auto": "Garten & Baumarkt", "tier": "Tierbedarf"}


def settings(s=None):
    out = dict(DEFAULTS)
    out.update((s or daten.load_settings()).get("vorschlaege") or {})
    return out


def map_category(text):
    low = str(text or "").lower()
    cat = daten.match_category(low)
    if cat:
        return cat
    for part in re.split(r"\s*(?:&|,|und)\s*", low):
        cat = daten.match_category(part)
        if cat:
            return cat
    for k, v in EXTRA_CATS.items():
        if k in low:
            return v
    return None


def product_title(raw, limit=70):
    """mydealz-Titel -> nur der Produktname (ohne Grad, Preise, Shop, Werbewoerter)."""
    import telegram_bot as tb  # shorten() und PRICE_FRAG wohnen im Bot
    t = str(raw or "")
    t = re.sub(TITLE_JUNK[0], "", t)
    t = tb.PRICE_FRAG.sub(" ", t)
    for pat in TITLE_JUNK[1:]:
        t = re.sub(pat, " ", t, flags=re.I)
    t = re.sub(r"\(\s*\)|\[\s*\]|[►▶★☆✓✔→]", " ", t)
    t = re.sub(r"\s+", " ", t)
    parts = [p.strip(" -–|:,;/+") for p in re.split(r"\s+[|–]\s+|\s+-\s+|\s*\|\s*", t)]
    parts = [p for p in parts if re.search(r"[A-Za-zÄÖÜäöü]{2}", p)]
    if not parts:
        return ""
    t = parts[0]
    if len(t.split()) <= 2 and len(parts) > 1:  # "Marke - Produkt" zusammenhalten
        t = f"{t} {parts[1]}"
    return tb.shorten(t, limit)


def hints_for(raw):
    low = str(raw or "").lower()
    out = []
    for k, v in HINTS:
        if k in low and v not in out:
            out.append(v)
    return out


def _text(el, tag):
    x = el.find(tag, NS)
    return (x.text or "").strip() if x is not None else ""


def parse_feed(xml_text):
    """RSS -> Liste von Vorschlaegen (nur Amazon). Keine Beschreibungstexte, keine Bilder."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print("mydealz-Feed nicht lesbar:", e)
        return out
    for it in root.iter("item"):
        merchant = it.find("pepper:merchant", NS)
        name = (merchant.get("name") if merchant is not None else "") or ""
        if not name.lower().startswith("amazon"):
            continue
        link = _text(it, "link")
        m = re.search(r"-(\d{5,})/?$", link)
        if not m:
            continue
        raw = _text(it, "title")
        g = re.match(r"\s*(-?\d+)\s*°", raw)
        try:
            pub = datetime.strptime(_text(it, "pubDate"), "%a, %d %b %Y %H:%M:%S %z").timestamp()
        except ValueError:
            pub = time.time()
        out.append({
            "id": m.group(1), "raw": raw, "title": product_title(raw), "grad": int(g.group(1)) if g else 0,
            "price": daten.num(merchant.get("price")) if merchant is not None else None,
            "cat": map_category(_text(it, "category")), "hints": hints_for(raw), "pub": pub, "quelle": link,
        })
    return out


def fetch_feed(name, timeout=20):
    req = urllib.request.Request(FEED_URL.format(name), headers=daten.UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def amazon_url(thread_id, timeout=15):
    """Ziel-Link eines mydealz-Deals lesen, ohne der Weiterleitung zu folgen (keine Amazon-Abrufe)."""
    opener = urllib.request.build_opener(daten._NoRedirect)
    url = VISIT_URL.format(thread_id)
    for _ in range(3):
        loc = None
        try:
            with opener.open(urllib.request.Request(url, headers=daten.UA), timeout=timeout) as r:
                loc = r.headers.get("Location")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
        except Exception as e:
            print("mydealz-Weiterleitung nicht lesbar:", e)
            return None
        if not loc:
            return None
        loc = urllib.parse.urljoin(url, loc)
        if daten.is_amazon(loc):
            return loc
        q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        inner = (q.get("url") or [""])[0]
        if daten.is_amazon(inner):
            return inner
        if "mydealz" not in daten.host_of(loc):
            return None  # anderer Shop
        url = loc
    return None


def collect(s=None, seen=(), known_asins=(), fetch=None, resolve=None, now=None):
    """Neue Vorschlaege holen, filtern und sortieren (heisseste zuerst). Gibt (liste, fehler) zurueck."""
    cfg = settings(s)
    fetch, resolve = fetch or fetch_feed, resolve or amazon_url
    now = now or time.time()
    items, errors, ids = [], [], set()
    for feed in cfg["feeds"]:
        try:
            got = parse_feed(fetch(feed))
        except Exception as e:
            errors.append(f"{feed}: {e}")
            continue
        for it in got:
            if it["id"] in ids or it["id"] in seen:
                continue
            ids.add(it["id"])
            items.append(it)
    nische = set(cfg.get("nische") or [])
    brands = brand_list(cfg)
    minus = {_norm(b) for b in cfg.get("marken_minus") or []}
    for it in items:
        it["marke"] = find_brand(it["title"], brands) or find_brand(it["raw"], brands)
    items = [it for it in items if it["grad"] >= cfg["min_grad"] and now - it["pub"] < 36 * 3600
             and it["title"] and (not nische or it["cat"] in nische)
             and (it["marke"] or not cfg.get("nur_marken"))
             and not any(f" {m} " in f" {_norm(it['raw'])} " for m in minus)]
    items.sort(key=lambda it: it["grad"], reverse=True)
    out = []
    for it in items:
        if len(out) >= int(cfg["anzahl"]):
            break
        url = resolve(it["id"])
        asin = daten.asin_of(url) if url else None
        if not asin or asin in known_asins or daten.amazon_domain(url) != "www.amazon.de":
            continue  # nur amazon.de, dort gilt die Partner-ID
        it.update(asin=asin, link=f"https://{daten.amazon_domain(url)}/dp/{asin}", t=now)
        out.append(it)
    return out, errors


def due_slot(cfg, last, now=None, catch_up=timedelta(hours=3)):
    """Welche geplante Uhrzeit ist gerade dran? -> 'JJJJ-MM-TT HH:MM' oder None."""
    now = now or datetime.now()
    best = None
    for z in cfg.get("zeiten") or []:
        try:
            h, m = (int(x) for x in str(z).split(":"))
        except ValueError:
            continue
        for day in (now.date(), now.date() - timedelta(days=1)):
            at = datetime(day.year, day.month, day.day, h, m)
            if at <= now < at + catch_up:
                key = at.strftime("%Y-%m-%d %H:%M")
                if key != last and (not last or key > last) and (best is None or key > best):
                    best = key
    return best


if __name__ == "__main__":
    found, errs = collect()
    for e in errs:
        print("Fehler:", e)
    for v in found:
        print(f"{v['grad']:>4}°  {daten.euro(v['price']) if v['price'] else '?':>9}  {v['asin']}  "
              f"{v['cat'] or '-':<18} {v['title']}  {', '.join(v['hints'])}")
        print(f"{'':>17}Marke: {v.get('marke') or '-'} · aus: {v['raw']}")
