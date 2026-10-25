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

import brand as brandlib  # noqa: E402
import daten  # noqa: E402
import website  # noqa: E402
from deal_video import font  # noqa: E402

website.OUT = os.path.join(website.ROOT, "website_beispiel")

# Titel, Hook, Kategorie, Label, alter Preis, neuer Preis, vor wie vielen Stunden geprueft,
# Farbe des Beispielbilds (None = Deal ohne eigenes Foto, die Website zeigt dann eine Text-Kachel)
DEALS = [
    ("Noise-Cancelling Kopfhörer, kabellos", "40 h Akku und starke Geräuschunterdrückung", "Technik", "TOP-DEAL", 249.99, 149.99, 1, "#DDE7F5"),
    ("Smartwatch mit GPS und Pulsmessung", "Bis zu 14 Tage Laufzeit", "Technik", "DEAL", 199.00, 129.00, 3, "#E4E0F5"),
    ("Powerbank 20.000 mAh, USB-C", "Lädt zwei Handys gleichzeitig", "Technik", "", 39.99, 24.99, 6, None),
    ("Akku-Staubsauger, beutellos", "Leicht, leise und 45 Minuten Laufzeit", "Haushalt", "TOP-DEAL", 299.00, 169.00, 2, "#F2E6DA"),
    ("Saugroboter mit Wischfunktion", "App-Steuerung und Kartierung", "Haushalt", "BESTPREIS", 399.00, 229.00, 8, "#E6EDE3"),
    ("LED-Schreibtischlampe, dimmbar", "5 Lichtfarben, USB-Ladeport", "Haushalt", "", 45.99, 32.99, 12, None),
    ("Edelstahl-Trinkflasche 750 ml", "Hält 24 h kalt und 12 h heiß", "Küche", "TOP-DEAL", 34.99, 19.99, 0.3, "#DCEFF2"),
    ("Heißluftfritteuse 5,5 l", "Knusprig mit bis zu 80 % weniger Fett", "Küche", "", 129.99, 79.99, 4, "#F5E1DC"),
    ("Kaffeevollautomat mit Milchsystem", "Preis vor mehr als 24 h geprüft: Rabatt wird ausgeblendet", "Küche", "BESTPREIS", 599.00, 399.00, 30, "#EDE5DC"),
    ("Yogamatte, rutschfest, 6 mm", "Inklusive Tragegurt", "Sport & Freizeit", "", 29.99, 17.99, 5, "#E3F0E4"),
    ("Elektrische Zahnbürste mit Drucksensor", "3 Putzmodi und Reiseetui", "Beauty & Pflege", "TOP-DEAL", 89.99, 44.99, 2, None),
    ("Klemmbaustein-Set Weltraumstation", "Über 900 Teile, ab 9 Jahren", "Spielzeug", "", 79.99, 59.99, 10, "#F5ECD6"),
    ("Regenjacke für Damen, wasserdicht", "Älterer Deal, steht unter „Ältere Deals“", "Mode", "", 119.99, 49.79, 24 * 25, "#E8E3F0"),
    ("Bluetooth-Lautsprecher, wasserfest", "Offline genommen: Deal-Seite zeigt einen Hinweis", "Technik", "", 59.99, 34.99, 24 * 3, None),
]


def placeholder(title, color):
    """Neutrales Platzhalterbild: farbige Flaeche mit Anfangsbuchstaben."""
    W = 720
    im = Image.new("RGB", (W, W), "#FFFFFF")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((80, 80, W - 80, W - 80), radius=70, fill=color)
    initials = "".join(w[0] for w in title.replace(",", "").replace("-", " ").split() if w[0].isalpha())[:2].upper()
    d.text((W / 2, W / 2 - 24), initials, font=font("black", 170), fill="#111111", anchor="mm")
    d.text((W / 2, W / 2 + 120), "BEISPIELBILD", font=font("medium", 32), fill="#8A8A86", anchor="mm")
    return im


def demo_deals(settings):
    brand = brandlib.load_brand(os.path.join(website.ROOT, "marke.json"))
    euro_on = bool(settings.get("preise_in_euro_zeigen", False))
    now = datetime.now()
    deals = []
    # Nummern wie im echten Betrieb: der aelteste Deal hat die kleinste Nummer
    for nr, (title, hook, cat, label, old, new, hours, color) in enumerate(sorted(DEALS, key=lambda x: -x[6]), 1):
        checked = now - timedelta(hours=hours)
        d = {"nr": nr, "id": daten.slug(title), "title": title, "hook": hook, "label": label, "cat": cat,
             "link": "https://www.amazon.de/", "folder": "", "discount": daten.discount(old, new),
             "old": old if euro_on else None, "new": new if euro_on else None,
             "checked": checked, "date": checked, "online": not hook.startswith("Offline"), "row": nr}
        d["img"] = website.image_files(d, brand, placeholder(title, color) if color else None)
        deals.append(d)
    deals.sort(key=lambda d: d["date"], reverse=True)
    return deals, []


NOTE = ('<div class="wrap"><p class="warn demo">Beispielseite: alle Deals sind erfunden, die Links führen nur zur '
        'Amazon-Startseite.</p></div>\n')

if __name__ == "__main__":
    website.read_deals = demo_deals
    website.build()
    for folder, _, files in os.walk(website.OUT):
        for f in files:
            if f.endswith(".html"):
                path = os.path.join(folder, f)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text.replace('<main id="main">\n', '<main id="main">\n' + NOTE, 1))
