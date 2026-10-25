"""
dealbar: Telegram-Briefkasten fuer neue Deals. Laeuft in der Cloud (GitHub Actions), siehe .github/workflows.

    python telegram_bot.py lauschen  # Dauerbetrieb in GitHub Actions (bis MAX_MINUTES)
    python telegram_bot.py eingang   # einmal neue Nachrichten abholen, Deals in deals.xlsx eintragen
    python telegram_bot.py ausgang   # fertige Videos + Beschreibung an dich zurueckschicken

Umgebungsvariablen:
    TELEGRAM_BOT_TOKEN  (Secret, von @BotFather)
    TELEGRAM_CHAT_ID    (deine Telegram-ID; nur von dieser ID werden Deals angenommen)
    PARTNER_TAG         (deine Amazon-Tracking-ID, z. B. dealbar-21)

Eine Deal-Nachricht (Reihenfolge der Zeilen egal, Fotos als Anhang oder direkt danach):
    https://www.amazon.de/...           <- Pflicht
    19,99 statt 34,99                    <- neuer und alter Preis
    Haelt 24 h kalt und 12 h heiss       <- Hook
    Kueche                               <- Kategorie
"""

import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from openpyxl import load_workbook  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

TABLE = os.path.join(ROOT, "deals.xlsx")
DEALS_DIR = os.path.join(ROOT, "deals")
VIDEO_DIR = os.path.join(ROOT, "videos")
STATE = os.path.join(HERE, "telegram_status.json")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TAG = os.environ.get("PARTNER_TAG", "").strip()

CATEGORIES = ["Technik", "Haushalt", "Küche", "Mode", "Sport & Freizeit", "Beauty & Pflege", "Spielzeug",
              "Garten & Baumarkt", "Sonstiges"]
ALIASES = {"kleidung": "Mode", "fashion": "Mode", "schuhe": "Mode", "elektronik": "Technik", "tech": "Technik",
           "garten": "Garten & Baumarkt", "werkzeug": "Garten & Baumarkt", "sport": "Sport & Freizeit",
           "beauty": "Beauty & Pflege", "drogerie": "Beauty & Pflege", "spiele": "Spielzeug"}
LINK_RE = re.compile(r"https?://[^\s<>]*(?:amazon\.de|amzn\.to|amzn\.eu|a\.co)[^\s<>]*", re.I)
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|d)/([A-Z0-9]{10})(?:[/?&#]|$)", re.I)
HIDDEN = ("abgelaufen", "aus", "offline")
PHOTO_WAIT = 120        # Sekunden Ruhe, bevor ein Deal mit Fotos gespeichert wird
PENDING_MAX = 30 * 60   # so lange wartet ein Deal ohne Fotos auf Bilder

HELP = """So schickst du mir einen Deal:

1. Amazon-Link (aus der App: Teilen → Link kopieren)
2. Preis, z. B. "19,99 statt 34,99"
3. Optional: kurzer Vorteil (Hook), eine Kategorie und ein eigener Titel ("Titel: ...")
4. 1–4 eigene Fotos anhängen oder direkt danach schicken

Kategorien: """ + ", ".join(CATEGORIES) + """

Befehle:
/liste – die letzten aktiven Deals
/aus 3 – Deal Nr. 3 aus der Liste von der Website nehmen
/abbrechen – angefangenen Deal verwerfen
/id – deine Telegram-ID

Video und Website werden automatisch erstellt, das dauert ein paar Minuten."""


# --------------------------------------------------------------------------- Telegram

def api(method, params=None, files=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if files:
        boundary = "dealbar" + str(int(time.time() * 1000))
        body = io.BytesIO()
        for k, v in (params or {}).items():
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (name, data) in files.items():
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                       f"Content-Type: application/octet-stream\r\n\r\n".encode())
            body.write(data)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, data=body.getvalue(),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method}: {res}")
    return res["result"]


def send(chat, text):
    try:
        api("sendMessage", {"chat_id": chat, "text": text, "disable_web_page_preview": True})
    except Exception as e:  # Antworten sind nett, aber nicht kritisch
        print("Senden fehlgeschlagen:", e)


def download(file_id):
    path = api("getFile", {"file_id": file_id})["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=120) as r:
        return r.read()


# --------------------------------------------------------------------------- Zustand

def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- Nachricht auswerten

def num(s):
    s = s.replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def resolve(link):
    """Kurzlinks (amzn.to, amzn.eu, a.co) aufloesen, um die Produktnummer (ASIN) zu finden."""
    if ASIN_RE.search(link):
        return link
    try:
        req = urllib.request.Request(link, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:  # Amazon antwortet auf HEAD manchmal mit Fehler, die URL stimmt trotzdem
        return e.url or link
    except Exception:
        return link


def shorten(text, limit=70):
    text = text.strip()
    if len(text) > limit and len(text.split(",")[0]) >= 15:
        text = text.split(",")[0]
    out = ""
    for w in text.split():
        if len(out) + len(w) + 1 > limit:
            break
        out = (out + " " + w).strip()
    return out or text[:limit]


def title_from_url(url):
    path = urllib.parse.urlparse(url).path
    m = re.search(r"/([^/]+)/(?:dp|gp/product)/", path)
    if not m or len(m.group(1)) <= 2:
        return ""
    return shorten(urllib.parse.unquote(m.group(1)).replace("-", " "), 60)


def match_category(line):
    low = line.lower().strip(" .:#")
    if low in ALIASES:
        return ALIASES[low]
    for c in CATEGORIES:
        if low == c.lower() or low in [p.strip() for p in c.lower().split("&")]:
            return c
    return None


def is_price_line(line):
    """'19,99 statt 34,99', '19.99€ / 34.99€', '19,99' ..."""
    rest = re.sub(r"statt|vorher|uvp|jetzt|nur|von|eur(o)?|€|[-/→>–:]", " ", line.lower())
    return bool(re.search(r"\d", rest)) and re.fullmatch(r"[\d.,\s]+", rest) is not None


def parse(text):
    """Text einer Deal-Nachricht -> Felder. Fehlende Felder bleiben leer."""
    d = {}
    rest = []
    lines = []
    for line in (text or "").splitlines():
        # Alles in einer Zeile mit dem Link: "Link, 19,99 statt 34,99, Küche, Hook"
        lines += re.split(r"[,;]\s+|,$", line) if LINK_RE.search(line) else [line]
    for line in lines:
        line = line.strip(" ,;")
        if not line:
            continue
        m = LINK_RE.search(line)
        if m:
            d["link"] = m.group(0).rstrip(").,")
            line = (line[:m.start()] + line[m.end():]).strip(" -:|")
            if not line:
                continue
            if not is_price_line(line):
                # Geteilter Text aus der Amazon-App: "Sieh dir ... an: <Produktname> <Link>"
                share = re.sub(r"^.{0,60}?\b(an|out|this|bei amazon)\s*:\s*", "", line, flags=re.I)
                d["share_title"] = shorten(share)
                continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        if val and k in ("titel", "hook", "kategorie", "label"):
            d[{"titel": "title"}.get(k, k if k != "kategorie" else "cat")] = val.strip()
            continue
        cat = match_category(line)
        if cat:
            d["cat"] = cat
            continue
        if is_price_line(line):
            prices = [num(x) for x in re.findall(r"\d[\d.,]*", line)]
            if "statt" in line.lower() or "vorher" in line.lower():
                d["new"], d["old"] = prices[0], (prices[1] if len(prices) > 1 else None)
            else:
                d["new"], d["old"] = min(prices), (max(prices) if len(prices) > 1 else None)
            if not d["old"] or d["old"] <= d["new"]:
                d.pop("old")
            continue
        rest.append(line)
    if rest:
        if "title" not in d and "share_title" not in d and len(rest) >= 2:
            d["title"] = rest.pop(0)
        d.setdefault("hook", " ".join(rest))
    if "share_title" in d:
        d.setdefault("title", d.pop("share_title"))
    return d


def affiliate_link(link):
    """Partnerlink bauen. Gibt (link, asin, hinweis) zurueck."""
    full = resolve(link)
    m = ASIN_RE.search(full)
    asin = m.group(1).upper() if m else None
    if asin and TAG:
        return f"https://www.amazon.de/dp/{asin}?tag={TAG}", asin, full, None
    if "tag=" in full or "amzn.to" in link:
        return link, asin, full, None
    return link, asin, full, "Achtung: keine Partner-ID im Link, dafür gibt es keine Provision (PARTNER_TAG fehlt)."


def slug(s):
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "deal"


# --------------------------------------------------------------------------- Tabelle

def open_table():
    wb = load_workbook(TABLE)
    ws = wb["Deals"]
    head = {str(c.value).strip(): c.column for c in ws[1] if c.value}
    return wb, ws, head


def active_rows(ws, head):
    rows = []
    for r in range(2, ws.max_row + 1):
        name = ws.cell(r, head["Produktname"]).value
        status = str(ws.cell(r, head["Status"]).value or "").strip().lower()
        if name and status not in HIDDEN:
            rows.append(r)
    return list(reversed(rows))[:10]


def save_deal(p):
    """Fertigen Deal speichern: Fotos in deals/<ordner>/, Zeile in deals.xlsx. Gibt Bestaetigung zurueck."""
    link, asin, full, warn = affiliate_link(p["link"])
    title = shorten(p.get("title", "")) or title_from_url(full) or (f"Amazon-Produkt {asin}" if asin else "Neuer Deal")
    folder = slug(title)
    n = 2
    while os.path.exists(os.path.join(DEALS_DIR, folder)):
        folder = f"{slug(title)[:37]}-{n}"
        n += 1
    os.makedirs(os.path.join(DEALS_DIR, folder))
    saved = 0
    for i, fid in enumerate(p.get("photos", [])[:4], 1):
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(download(fid)))).convert("RGB")
            im.thumbnail((1600, 1600), Image.LANCZOS)
            im.save(os.path.join(DEALS_DIR, folder, f"{i}.jpg"), "JPEG", quality=88)
            saved += 1
        except Exception as e:
            print("Foto fehlgeschlagen:", e)

    wb, ws, head = open_table()
    r = next((r for r in range(2, ws.max_row + 2) if not ws.cell(r, head["Produktname"]).value), ws.max_row + 1)
    stamp = datetime.fromtimestamp(p["time"]).strftime("%d.%m.%Y, %H:%M")
    old, new = p.get("old"), p.get("new")
    values = {
        "Status": "neu" if saved else "ohne Video",
        "Ordner": folder,
        "Produktname": title,
        "Alter Preis (€)": old,
        "Neuer Preis (€)": new,
        "Preis geprüft am": stamp,
        "Hook": p.get("hook", ""),
        "Affiliate-Link": link,
        "Label": p.get("label", "TOP-DEAL"),
        "Preis zeigen": "ja" if new else "nein",
        "Kategorie": p.get("cat", ""),
    }
    for k, v in values.items():
        if k in head:
            ws.cell(r, head[k]).value = v
    if "Rabatt" in head and not ws.cell(r, head["Rabatt"]).value:
        o, nn = ws.cell(r, head["Alter Preis (€)"]).coordinate, ws.cell(r, head["Neuer Preis (€)"]).coordinate
        ws.cell(r, head["Rabatt"]).value = f'=IF(AND(ISNUMBER({o}),ISNUMBER({nn}),{o}>{nn}),1-{nn}/{o},"")'
    wb.save(TABLE)

    disc = f" (−{round((old - new) / old * 100)} %)" if old and new and old > new else ""
    lines = [f"✓ Deal gespeichert: {title}"]
    if new:
        lines.append(f"Preis: {new:.2f} €".replace(".", ",") + (f" statt {old:.2f} €".replace(".", ",") if old else "") + disc)
    else:
        lines.append("Kein Preis erkannt, der Deal erscheint ohne Rabatt.")
    lines.append(f"Kategorie: {p.get('cat') or '–'} · Fotos: {saved}")
    if not p.get("title") and not title_from_url(full):
        lines.append('Tipp: Titel fehlte, schick beim nächsten Mal eine Zeile "Titel: ..." mit.')
    if warn:
        lines.append(warn)
    lines.append("Video und Website folgen in ein paar Minuten." if saved else
                 "Ohne Fotos gibt es kein Video, der Deal kommt nur auf die Website.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- Ablauf

def handle_command(cmd, arg, chat, st):
    if cmd in ("/start", "/hilfe", "/help"):
        send(chat, HELP)
    elif cmd == "/id":
        send(chat, f"Deine Telegram-ID: {chat}")
    elif cmd == "/abbrechen":
        st.pop("pending", None)
        st["loose"] = []
        send(chat, "Angefangener Deal verworfen.")
    elif cmd == "/liste":
        wb, ws, head = open_table()
        rows = active_rows(ws, head)
        if not rows:
            send(chat, "Keine aktiven Deals.")
            return
        send(chat, "\n".join(f"{i}. {ws.cell(r, head['Produktname']).value} ({ws.cell(r, head['Status']).value})"
                             for i, r in enumerate(rows, 1)) + "\n\nMit /aus <Nr> nimmst du einen Deal von der Website.")
    elif cmd == "/aus":
        wb, ws, head = open_table()
        rows = active_rows(ws, head)
        try:
            r = rows[int(arg) - 1]
        except (ValueError, IndexError):
            send(chat, "Bitte eine Nummer aus /liste angeben, z. B. /aus 2")
            return
        ws.cell(r, head["Status"]).value = "abgelaufen"
        wb.save(TABLE)
        send(chat, f"Von der Website genommen: {ws.cell(r, head['Produktname']).value}")
        return True
    else:
        send(chat, "Unbekannter Befehl. /hilfe zeigt alle Befehle.")


def finalize(st, chat, now, force=False):
    """Angefangenen Deal speichern, sobald er vollstaendig ist. True, wenn gespeichert."""
    p = st.get("pending")
    if not p:
        return False
    quiet = now - p["last"]
    if force or (p["photos"] and (quiet >= PHOTO_WAIT or len(p["photos"]) >= 4)) or quiet >= PENDING_MAX:
        st.pop("pending")
        try:
            send(chat, save_deal(p))
        except Exception as e:
            send(chat, f"Fehler beim Speichern des Deals: {e}")
            raise
        return True
    return False


def process(updates, st):
    """Nachrichten verarbeiten. True, wenn sich Deals geaendert haben."""
    changed = False
    chat = CHAT_ID
    for u in updates:
        st["offset"] = u["update_id"] + 1
        msg = u.get("message")
        if not msg:
            continue
        sender = str(msg["chat"]["id"])
        if not CHAT_ID:
            send(sender, f"Hallo! Deine Telegram-ID ist {sender}.\nTrage sie bei GitHub als Variable "
                         "TELEGRAM_CHAT_ID ein, dann nehme ich Deals von dir an.")
            continue
        if sender != CHAT_ID:
            continue
        text = msg.get("text") or msg.get("caption") or ""
        photo = msg["photo"][-1]["file_id"] if msg.get("photo") else None
        if not photo and msg.get("document", {}).get("mime_type", "").startswith("image/"):
            photo = msg["document"]["file_id"]
        t = msg["date"]

        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            changed |= bool(handle_command(cmd.split("@")[0].lower(), arg.strip(), chat, st))
            continue
        fields = parse(text)
        if "link" in fields:
            changed |= finalize(st, chat, time.time(), force=True)
            loose = [x["id"] for x in st.get("loose", []) if t - x["time"] < PENDING_MAX]
            st["pending"] = dict(fields, photos=loose, time=t, last=t)
            st["loose"] = []
        elif st.get("pending"):
            p = st["pending"]
            for k, v in fields.items():
                p[k] = v
            p["last"] = t
        elif photo:
            st.setdefault("loose", []).append({"id": photo, "time": t})
        elif text:
            send(chat, "Ich habe keinen Amazon-Link gefunden. /hilfe zeigt, wie ein Deal aussieht.")
        if photo and st.get("pending"):
            st["pending"]["photos"].append(photo)
            st["pending"]["last"] = t
    return changed


def eingang():
    """Einmal abholen (fuer Tests und lokale Nutzung)."""
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN fehlt.")
    st = load_state()
    updates = api("getUpdates", {"offset": st.get("offset", 0), "timeout": 0, "allowed_updates": ["message"]})
    process(updates, st)
    finalize(st, CHAT_ID, time.time())
    save_state(st)
    print(f"{len(updates)} Nachrichten verarbeitet.")


def sh(*cmd):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


def git_save(message):
    sh("git", "add", "-A", "deals", "deals.xlsx", "programm")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return False
    sh("git", "commit", "-q", "-m", message)
    sh("git", "pull", "-q", "--rebase", "--autostash")
    return sh("git", "push", "-q") == 0


def publish_website():
    """Website-Workflow anstossen (Pushes mit dem Actions-Token loesen selbst keinen Workflow aus)."""
    repo, gh_token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and gh_token):
        return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/website.yml/dispatches",
        data=json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main")}).encode(), method="POST",
        headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print("Website-Workflow nicht gestartet:", e)


def lauschen():
    """Dauerbetrieb: wartet per Long-Polling auf Nachrichten und verarbeitet neue Deals sofort."""
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN fehlt.")
    end = time.time() + float(os.environ.get("MAX_MINUTES", "340")) * 60
    st = load_state()
    wb, ws, head = open_table()
    if any(str(ws.cell(r, head["Status"]).value or "").strip().lower() == "neu" for r in range(2, ws.max_row + 1)):
        # Zeilen, die direkt in deals.xlsx auf "neu" gesetzt wurden, gleich rendern
        sh(sys.executable, os.path.join(HERE, "run.py"))
        ausgang()
        git_save("Videos neu erstellt")
        publish_website()
    while time.time() < end:
        try:
            updates = api("getUpdates", {"offset": st.get("offset", 0), "allowed_updates": ["message"],
                                         "timeout": int(max(1, min(50, end - time.time())))})
        except Exception as e:
            print("Telegram nicht erreichbar:", e)
            time.sleep(10)
            continue
        changed = process(updates, st)
        changed |= finalize(st, CHAT_ID, time.time())
        save_state(st)
        if changed:
            sh(sys.executable, os.path.join(HERE, "run.py"))
            ausgang()
            git_save("Neue Deals aus Telegram")
            publish_website()
    git_save("Telegram-Status")
    print("Lauf beendet, der nächste übernimmt.")


def ausgang():
    """Neue Videos samt Beschreibung an dich schicken, damit du sie direkt vom Handy posten kannst."""
    if not (TOKEN and CHAT_ID) or not os.path.isdir(VIDEO_DIR):
        return
    st = load_state()
    sent = set(st.get("sent", []))
    for f in sorted(os.listdir(VIDEO_DIR)):
        key = f"{f}@{int(os.path.getmtime(os.path.join(VIDEO_DIR, f)))}"  # neu gerenderte Videos erneut senden
        if not f.endswith(".mp4") or key in sent:
            continue
        base = f[:-4]
        cap_path = os.path.join(VIDEO_DIR, base + "_beschreibung.txt")
        caption = open(cap_path, encoding="utf-8").read() if os.path.exists(cap_path) else ""
        with open(os.path.join(VIDEO_DIR, f), "rb") as fh:
            api("sendVideo", {"chat_id": CHAT_ID, "supports_streaming": "true"}, files={"video": (f, fh.read())})
        if caption:
            send(CHAT_ID, caption)
        sent.add(key)
        print("Gesendet:", f)
    st["sent"] = sorted(sent)[-200:]
    save_state(st)


if __name__ == "__main__":
    {"eingang": eingang, "ausgang": ausgang, "lauschen": lauschen}[sys.argv[1] if len(sys.argv) > 1 else "eingang"]()
