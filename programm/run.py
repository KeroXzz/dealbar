"""
dealbar: rendert alle Zeilen mit Status "neu" aus deals.xlsx zu Videos, dazu je ein Text zum Posten.
Wird vom Telegram-Bot (in der Cloud) und von START.bat (am PC) aufgerufen.

Deals ohne eigene Fotos bekommen ein Video im Text-Design (Bilder von Amazon sind tabu).
Das Ergebnis jedes Laufs steht zusaetzlich in videos/_ergebnis.json (liest der Bot).
"""

import json
import os
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brand as brandlib  # noqa: E402
import daten  # noqa: E402
from daten import CATEGORIES, DEALS_DIR, VIDEO_DIR, euro  # noqa: E402
from deal_video import render  # noqa: E402

RESULTS = os.path.join(VIDEO_DIR, "_ergebnis.json")
COLS = ["Status", "Ordner", "Produktname", "Alter Preis (€)", "Neuer Preis (€)", "Rabatt",
        "Preis geprüft am", "Hook", "Affiliate-Link", "Label", "Preis zeigen", "Erstellt am", "Videodatei"]


def stamp_text(v):
    dt = daten.parse_dt(v)
    if dt:
        return daten.fmt_dt(dt)
    return str(v).replace(" Uhr", "").strip() if v else daten.fmt_dt(datetime.now())


def caption(cfg, discount, stamp, nr, cat):
    """Text fuer Instagram, TikTok & Co. (mit Werbekennzeichnung und Preis-Zeitstempel)."""
    show = cfg.get("show_price", True) and cfg.get("price_new") is not None
    lines = [f"🔥 −{discount} % · {cfg['title']}" if (discount and show) else f"🔥 {cfg['title']}"]
    if cfg.get("hook"):
        lines.append(cfg["hook"])
    lines.append("")
    if show:
        p = f"💶 Jetzt {euro(cfg['price_new'])}"
        if cfg.get("price_old"):
            p += f" statt {euro(cfg['price_old'])}"
        lines.append(p + "*")
    lines.append("👉 Link in Bio" + (f" · Deal #{nr}" if nr else ""))
    lines.append("")
    if show:
        lines.append(f"*Preis vom {stamp} Uhr, kann sich jederzeit ändern.")
    lines.append(f"#Werbung | {daten.DISCLOSURE}")
    tags = ["#dealbar", "#amazondeals", "#angebote", "#schnäppchen"]
    if cat in CATEGORIES:
        tags.append("#" + CATEGORIES[cat][2])
    lines.append(" ".join(tags))
    return "\n".join(lines) + "\n"


def render_row(row, brand):
    """Eine Tabellenzeile rendern -> Ergebnis (dict) fuer Tabelle und Bot."""
    title = str(row["Produktname"]).strip()
    folder = str(row.get("Ordner") or "").strip()
    nr = row.get("Nr")
    nr = int(nr) if isinstance(nr, (int, float)) else None
    res = {"nr": nr, "title": title, "link": str(row.get("Affiliate-Link") or "").strip()}
    try:
        imgs = daten.images_in(folder)
        p_old, p_new = daten.num(row.get("Alter Preis (€)")), daten.num(row.get("Neuer Preis (€)"))
        stamp = stamp_text(row.get("Preis geprüft am"))
        show_price = str(row.get("Preis zeigen") or "ja").strip().lower() not in ("nein", "n", "no")
        discount = daten.discount(p_old, p_new)
        cat = str(row.get("Kategorie") or "").strip()
        cfg = {
            "title": title,
            "images": [os.path.join(folder, f) for f in imgs[:4]],
            "price_old": p_old if discount else None,
            "price_new": p_new,
            "price_time": stamp,
            "hook": str(row.get("Hook") or "").strip(),
            "label": str(row.get("Label") or "TOP-DEAL").strip(),
            "show_price": show_price,
            "show_discount": show_price and bool(discount),
            "category": cat,
            "nr": nr,
            "cta": f"Deal #{nr} · Link in Bio" if nr else "Zum Deal: Link in Bio",
        }
        if discount:
            cfg["discount_percent"] = discount
        name = f"{datetime.now():%Y-%m-%d}_{daten.slug(folder or title)}"
        res["duration"] = render(cfg, DEALS_DIR, os.path.join(VIDEO_DIR, name + ".mp4"), brand)
        res["caption"] = caption(cfg, discount, stamp, nr, cat)
        with open(os.path.join(VIDEO_DIR, name + "_beschreibung.txt"), "w", encoding="utf-8") as fh:
            fh.write(res["caption"])
        res.update(status="fertig", video=name + ".mp4", photos=len(imgs))
    except Exception as e:
        res.update(status="Fehler", error=str(e)[:200])
        if os.environ.get("DEALBAR_DEBUG"):
            traceback.print_exc()
    return res


def write_results(results):
    with open(RESULTS, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)


def main():
    print("dealbar Video-Generator\n")
    if not os.path.exists(daten.TABLE):
        sys.exit(f"Tabelle nicht gefunden: {daten.TABLE}")
    try:  # Pruefen, ob die Tabelle gespeichert werden kann (Excel geoeffnet?)
        with open(daten.TABLE, "r+b"):
            pass
    except PermissionError:
        sys.exit("deals.xlsx ist noch in Excel geöffnet. Bitte Excel schließen und erneut starten.")

    brand = brandlib.load_brand(os.path.join(daten.ROOT, "marke.json"))
    with daten.open_table(write=True) as t:
        missing = [c for c in COLS if c not in t.head]
        todo = [t.row(r) for r in t.rows() if t.text(r, "Status").lower() == "neu"]
    if missing:
        sys.exit("In deals.xlsx fehlen Spalten: " + ", ".join(missing))
    os.makedirs(VIDEO_DIR, exist_ok=True)
    results = []
    write_results(results)
    if not todo:
        print('Keine Zeilen mit Status "neu" gefunden. Nichts zu tun.')
        return

    for n, row in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {str(row['Produktname'])[:60]}")
        res = render_row(row, brand)
        with daten.open_table(write=True) as t:
            r = t.find(nr=res["nr"], folder=str(row.get("Ordner") or "").strip() or None, title=res["title"])
            if r:
                if t.text(r, "Status").lower() == "neu":  # inzwischen offline genommen? Dann so lassen
                    t.set(r, "Status", res["status"])
                if res["status"] == "fertig":
                    t.set(r, "Erstellt am", datetime.now().strftime("%d.%m.%Y %H:%M"))
                    t.set(r, "Videodatei", res["video"])
                else:
                    t.set(r, "Videodatei", res["error"])
        results.append(res)
        write_results(results)
        print(f"      fertig: videos/{res['video']}" if res["status"] == "fertig" else f"      FEHLER: {res['error']}")

    ok = sum(1 for r in results if r["status"] == "fertig")
    print(f"\n{ok} von {len(todo)} Videos erstellt. Sie liegen im Ordner 'videos'.")


if __name__ == "__main__":
    try:
        main()
    except PermissionError as e:
        sys.exit(str(e))
