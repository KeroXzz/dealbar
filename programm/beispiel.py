"""
dealbar: baut eine Beispiel-Website mit erfundenen Deals in den Ordner "website_beispiel".
Deine echten Daten (deals.xlsx, Ordner "website") werden nicht angefasst.
Wird von BEISPIEL_WEBSITE.bat aufgerufen.
"""

import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw  # noqa: E402

import website  # noqa: E402
from deal_video import font  # noqa: E402

website.OUT = os.path.join(website.ROOT, "website_beispiel")

# Titel, Hook, Kategorie, Label, alter Preis, neuer Preis, vor wie vielen Stunden geprueft, Farbe
DEALS = [
    ("Noise-Cancelling Kopfhörer, kabellos", "40 h Akku und starke Geräuschunterdrückung", "Technik", "TOP-DEAL", 249.99, 149.99, 1, "#DDE7F5"),
    ("Smartwatch mit GPS und Pulsmessung", "Bis zu 14 Tage Laufzeit", "Technik", "TECH-DEAL", 199.00, 129.00, 3, "#E4E0F5"),
    ("Powerbank 20.000 mAh, USB-C", "Lädt zwei Handys gleichzeitig", "Technik", "", 39.99, 24.99, 6, "#E0EEF0"),
    ("Akku-Staubsauger, beutellos", "Leicht, leise und 45 Minuten Laufzeit", "Haushalt", "TOP-DEAL", 299.00, 169.00, 2, "#F2E6DA"),
    ("Saugroboter mit Wischfunktion", "App-Steuerung und Kartierung", "Haushalt", "BESTPREIS", 399.00, 229.00, 8, "#E6EDE3"),
    ("LED-Schreibtischlampe, dimmbar", "5 Lichtfarben, USB-Ladeport", "Haushalt", "", 45.99, 32.99, 12, "#F5F0DC"),
    ("Edelstahl-Trinkflasche 750 ml", "Hält 24 h kalt und 12 h heiß", "Küche", "TOP-DEAL", 34.99, 19.99, 1, "#DCEFF2"),
    ("Heißluftfritteuse 5,5 l", "Knusprig mit bis zu 80 % weniger Fett", "Küche", "", 129.99, 79.99, 4, "#F5E1DC"),
    ("Kaffeevollautomat mit Milchsystem", "One-Touch für Cappuccino und Latte", "Küche", "BESTPREIS", 599.00, 399.00, 30, "#EDE5DC"),
    ("Yogamatte, rutschfest, 6 mm", "Inklusive Tragegurt", "Sport & Freizeit", "", 29.99, 17.99, 5, "#E3F0E4"),
    ("Elektrische Zahnbürste mit Drucksensor", "3 Putzmodi und Reiseetui", "Beauty & Pflege", "TOP-DEAL", 89.99, 44.99, 2, "#F2E3EE"),
    ("Klemmbaustein-Set Weltraumstation", "Über 900 Teile, ab 9 Jahren", "Spielzeug", "", 79.99, 59.99, 10, "#F5ECD6"),
]


def placeholder(title, color, path):
    """Neutrales Platzhalterbild: farbige Flaeche mit Produktname."""
    W = 640
    im = Image.new("RGB", (W, W), "#FFFFFF")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((70, 70, W - 70, W - 70), radius=60, fill=color)
    f_big, f_small = font("black", 150), font("medium", 30)
    initials = "".join(w[0] for w in title.replace(",", "").replace("-", " ").split() if w[0].isalpha())[:2].upper()
    d.text((W / 2, W / 2 - 20), initials, font=f_big, fill="#111111", anchor="mm")
    d.text((W / 2, W / 2 + 110), "BEISPIELBILD", font=f_small, fill="#8A8A86", anchor="mm")
    im.save(path, "WEBP", quality=85)


def demo_deals(settings):
    now = datetime.now()
    deals = []
    for i, (title, hook, cat, label, old, new, hours, color) in enumerate(DEALS):
        name = website.slug(title)
        rel = f"bilder/{name}.webp"
        placeholder(title, color, os.path.join(website.OUT, rel))
        euro = settings.get("preise_in_euro_zeigen", False)
        deals.append({
            "id": name, "title": title, "hook": hook, "label": label, "cat": cat,
            "link": "https://www.amazon.de/", "img": rel,
            "discount": round((old - new) / old * 100),
            "old": old if euro else None, "new": new if euro else None,
            "checked": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M"),
            "added": "",
        })
    deals.sort(key=lambda d: d["checked"], reverse=True)
    return deals, []


if __name__ == "__main__":
    website.read_deals = demo_deals
    website.build()
    idx = os.path.join(website.OUT, "index.html")
    with open(idx, encoding="utf-8") as fh:
        page = fh.read()
    note = ('<div class="wrap" style="padding-top:12px"><p class="warn">Beispielseite: alle Deals sind erfunden, '
            'die Links führen nur zur Amazon-Startseite.</p></div>\n<main>')
    with open(idx, "w", encoding="utf-8") as fh:
        fh.write(page.replace("<main>", note, 1))
