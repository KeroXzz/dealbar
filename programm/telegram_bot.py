"""
dealbar: Telegram-Bot als Briefkasten und Fernbedienung fuer Deals. Laeuft in der Cloud (GitHub Actions, bot.yml).

    python telegram_bot.py lauschen   # Dauerbetrieb, so laeuft er bei GitHub (bis MAX_MINUTES)
    python telegram_bot.py eingang    # einmal Nachrichten abholen und verarbeiten (zum Testen)

So kommt ein Deal rein (alles vom Handy):
    1. In der Amazon-App "Teilen" -> Telegram -> dein Bot (oder den Link einfuegen)
    2. Preis dazuschreiben, z. B. "19,99 statt 34,99" (optional: Kategorie, kurzer Vorteil, eigene Fotos)
    3. Der Bot zeigt einen Entwurf mit Knoepfen: "✅ Speichern" -> fertig
Danach entstehen automatisch: Zeile in deals.xlsx, Video + Text zum Posten, Website-Eintrag und
(falls verbunden) ein Post im Telegram-Kanal. Mit /liste verwaltest du alle Deals.

Umgebungsvariablen (GitHub -> Settings -> Secrets and variables -> Actions):
    TELEGRAM_BOT_TOKEN  Secret, von @BotFather
    TELEGRAM_CHAT_ID    deine Telegram-ID; nur von ihr nimmt der Bot Deals an
    PARTNER_TAG         optional; einfacher per /partnerid im Bot (steht dann in website.json)
"""

import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image, ImageOps  # noqa: E402

import daten  # noqa: E402
from daten import CATEGORIES, DEALS_DIR, ROOT, VIDEO_DIR, euro  # noqa: E402

STATE = os.path.join(HERE, "telegram_status.json")
RESULTS = os.path.join(VIDEO_DIR, "_ergebnis.json")
RUN_PY = os.path.join(HERE, "run.py")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

PHOTO_WAIT = 120        # Sekunden Ruhe nach dem letzten Foto, dann speichert der Bot den Entwurf selbst
DRAFT_MAX = 30 * 60     # so lange wartet ein Entwurf ohne Fotos, bevor er automatisch gespeichert wird
AWAIT_MAX = 30 * 60     # so lange gilt eine offene Frage ("Schick mir den Titel ...")
MAX_PHOTOS = 4
LIST_PAGE = 8
SYNC_EVERY = 180        # Sekunden: neue Programmversion / Tabellenaenderungen von GitHub holen

HELP = """<b>So schickst du einen Deal</b>

1️⃣ In der Amazon-App beim Produkt auf <b>Teilen</b> tippen → Telegram → diesen Bot. Oder den Link hier einfügen.
2️⃣ Den Preis dazuschreiben, z. B. <code>19,99 statt 34,99</code>
3️⃣ Optional: Kategorie, kurzer Vorteil und 1–4 <b>eigene</b> Fotos. Keine Screenshots oder Bilder von Amazon, das verbietet Amazon. Ohne Fotos gibt es ein Video im Text-Design.
4️⃣ Im Entwurf auf <b>✅ Speichern</b> tippen.

Den Rest mache ich: Website-Eintrag, Video mit fertigem Text zum Posten und, falls verbunden, den Post im Telegram-Kanal.

Alles in einer Nachricht geht auch:
<code>Link, 19,99 statt 34,99, Mode</code>

<b>Befehle</b>
/liste – Deals verwalten (Preis bestätigen, offline nehmen, Fotos, Video)
/status – Einrichtung prüfen
/impressum – Impressum eintragen
/partnerid – Amazon-Partner-ID eintragen
/kanal – Telegram-Kanal verbinden
/abbrechen – Entwurf verwerfen"""

COMMANDS = [("liste", "Deals verwalten"), ("status", "Einrichtung und Status prüfen"),
            ("hilfe", "So schickst du einen Deal"), ("abbrechen", "Entwurf verwerfen"),
            ("impressum", "Impressum eintragen"), ("partnerid", "Amazon-Partner-ID eintragen"),
            ("kanal", "Telegram-Kanal verbinden"), ("id", "Deine Telegram-ID")]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)


def eur(v):
    return euro(v).replace(" ", " ")


# --------------------------------------------------------------------------- Telegram

class TelegramError(Exception):
    pass


def _request(url, params, files):
    if not files:
        return urllib.request.Request(url, data=json.dumps(params or {}).encode(),
                                      headers={"Content-Type": "application/json"})
    boundary = "dealbar" + str(int(time.time() * 1000))
    body = io.BytesIO()
    for k, v in (params or {}).items():
        v = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data) in files.items():
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                   f"Content-Type: application/octet-stream\r\n\r\n".encode())
        body.write(data)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return urllib.request.Request(url, data=body.getvalue(),
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def api(method, params=None, files=None, timeout=60):
    """Telegram-Bot-API aufrufen. Wiederholt bei Netzwerkfehlern und 'Too Many Requests'."""
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(_request(url, params, files), timeout=timeout) as r:
                res = json.load(r)
            if not res.get("ok"):
                raise TelegramError(f"{method}: {res.get('description')}")
            return res["result"]
        except urllib.error.HTTPError as e:
            try:
                res = json.load(e)
            except Exception:
                res = {"description": str(e)}
            if e.code == 429:
                time.sleep(int(res.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise TelegramError(f"{method}: {res.get('description', e)}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise TelegramError(f"{method}: keine Antwort")


def keyboard(rows):
    out = []
    for row in rows:
        out.append([{"text": label, "url": action[4:]} if action.startswith("url:")
                    else {"text": label, "callback_data": action} for label, action in row if action])
    return {"inline_keyboard": [r for r in out if r]}


def send(text, buttons=None, chat=None, force_reply=None):
    params = {"chat_id": chat or CHAT_ID, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}}
    if buttons:
        params["reply_markup"] = keyboard(buttons)
    elif force_reply:
        params["reply_markup"] = {"force_reply": True, "input_field_placeholder": force_reply[:64]}
    try:
        return api("sendMessage", params)["message_id"]
    except Exception as e:  # Antworten sind wichtig, aber kein Grund abzustuerzen
        print("Senden fehlgeschlagen:", e)
        return None


def edit(mid, text, buttons=None):
    if not mid:
        return False
    params = {"chat_id": CHAT_ID, "message_id": mid, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True},
              "reply_markup": keyboard(buttons) if buttons else {"inline_keyboard": []}}
    try:
        api("editMessageText", params)
        return True
    except TelegramError as e:
        if "not modified" in str(e):
            return True
        print("Bearbeiten fehlgeschlagen:", e)
        return False
    except Exception as e:
        print("Bearbeiten fehlgeschlagen:", e)
        return False


def delete(mid):
    if mid:
        try:
            api("deleteMessage", {"chat_id": CHAT_ID, "message_id": mid})
        except Exception:
            pass


def answer(qid, text=""):
    try:
        api("answerCallbackQuery", {"callback_query_id": qid, "text": text[:190]})
    except Exception:
        pass


def download(file_id):
    path = api("getFile", {"file_id": file_id})["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=120) as r:
        return r.read()


# --------------------------------------------------------------------------- Nachricht auswerten

SHARE_JUNK = [
    r"^(schau|sieh|guck)\s+(dir\s+)?(mal\s+)?(dies(es|en|e)?|das|den|die)?\s*(produkt|artikel|deal|angebot)?\s*"
    r"(bei|auf)\s+amazon(\.de)?\s+an\b\s*[:!.\-–]*",
    r"^(check\s+out|look\s+at)\s*(this\s+(deal|product|item))?\s*((on|at)\s+amazon(\.\w+)*)?\s*[:!.\-–]*",
    r"^(ich\s+habe|i\s+found|found)\b.*?\b(bei|auf|on)\s+amazon(\.\w+)*(\s+(gefunden|entdeckt))?\s*[:!.\-–]*",
    r"^amazon\.\w+(\.\w+)?\s*:\s*",
    r"\s*[|–-]\s*amazon\.\w+.*$",
]


def shorten(text, limit=70):
    """Lange Amazon-Titel auf das Wesentliche kuerzen (bis zum ersten Komma, max. limit Zeichen)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip(" ,;:-–|")
    first = re.split(r",|\s[|–-]\s|\(", text)[0].strip()
    if len(first) >= 12:
        text = first
    out = ""
    for w in text.split():
        if len(out) + len(w) + 1 > limit:
            break
        out = (out + " " + w).strip()
    return out or text[:limit]


def clean_share(text):
    """Geteilten Text aus der Amazon-App/Browser -> (Titel, Kategorie-Hinweis)."""
    t = re.sub(r"\s+", " ", text).strip(" -:|–")
    for pat in SHARE_JUNK:
        t = re.sub(pat, "", t, flags=re.I).strip(" -:|–\"'")
    cat = None
    parts = [p.strip() for p in re.split(r"\s+:\s+", t) if p.strip()]
    if len(parts) > 1:
        cat = daten.match_category(parts[-1])
        t = parts[0]
    if len(re.sub(r"[^A-Za-zÄÖÜäöüß]", "", t)) < 4:
        return "", cat
    return shorten(t), cat


def title_from_url(url):
    path = urllib.parse.urlparse(url or "").path
    m = re.search(r"/([^/]+)/(?:dp|gp/product)/", path)
    if not m or len(m.group(1)) <= 2 or m.group(1) in ("-", "gp"):
        return ""
    return shorten(urllib.parse.unquote(m.group(1)).replace("-", " "), 60)


PRICE_RE = re.compile(r"\d+(?:[.,]\d+)*")


def parse_prices(line):
    """'19,99 statt 34,99' | '19.99€ / 34.99€' | 'UVP 34,99 → 19,99' | '19,99' -> (neu, alt) oder None."""
    low = re.sub(r"[-−–]?\s*\d+(?:[.,]\d+)?\s*%", " ", line.lower())  # Prozentangaben ignorieren
    rest = re.sub(r"statt|vorher|uvp|jetzt|nur|von|auf|ab|eur(?:o)?|preis|reduziert|neu|alt|€|\*|"
                  r"[-−–—/→>:=()\[\]!]", " ", low)
    if not re.search(r"\d", rest) or not re.fullmatch(r"[\d.,\s]+", rest):
        return None
    prices = [p for p in (daten.num(x) for x in PRICE_RE.findall(low)) if p and p > 0]
    if not prices or len(prices) > 3:
        return None
    new, old = min(prices), max(prices)
    return new, (old if old > new else None)


def parse(text):
    """Text einer Nachricht -> Felder (link, share_title, title, hook, cat, new, old, label, rest).

    Text vor dem Link (geteilt aus der Amazon-App) gilt als Produkttitel. Nach dem Link geht auch
    alles in einer Zeile: "Link, 19,99 statt 34,99, Mode, Vorteil".
    """
    d, rest, pre = {}, [], []
    raw = (text or "").splitlines()
    link_i = next((i for i, ln in enumerate(raw) if daten.find_link(ln)), None)
    lines = []
    for i, line in enumerate(raw):
        if i == link_i:
            url, a, b = daten.find_link(line)
            d["link"] = url
            before, after = line[:a].strip(" -:|,;"), line[b:].strip(" -:|,;")
            if before:
                lines.append((before, True))
            lines += [(x, False) for x in re.split(r"[,;]\s+|,$", after) if x.strip()]
        else:
            lines.append((line, link_i is not None and i < link_i))
    for line, before in lines:
        line = line.strip(" ,;")
        if not line:
            continue
        key, sep, val = line.partition(":")
        k = key.strip().lower()
        if sep and val.strip() and k in ("titel", "title", "name", "hook", "vorteil", "text", "kategorie",
                                         "label", "preis"):
            val = val.strip()
            if k in ("titel", "title", "name"):
                d["title"] = shorten(val, 80)
            elif k in ("hook", "vorteil", "text"):
                d["hook"] = val[:90]
            elif k == "kategorie":
                d["cat"] = daten.match_category(val) or val[:30]
            elif k == "label":
                d["label"] = val.upper()[:20]
            elif parse_prices(val):
                d["new"], d["old"] = parse_prices(val)
            continue
        cat = daten.match_category(line)
        if cat:
            d["cat"] = cat
            continue
        prices = parse_prices(line)
        if prices:
            d["new"], d["old"] = prices
            continue
        (pre if before else rest).append(line)
    if pre:
        title, cat = clean_share(" ".join(pre))
        if title:
            d["share_title"] = title
        if cat:
            d.setdefault("cat", cat)
    if rest:
        d["rest"] = rest
    return d


def auto_label(old, new):
    off = daten.discount(old, new) or 0
    return "TOP-DEAL" if off >= 50 else "DEAL" if off >= 25 else "TIPP"


def folder_for(title):
    base = daten.slug(title)
    folder, n = base, 2
    while os.path.exists(os.path.join(DEALS_DIR, folder)):
        folder = f"{base[:36]}-{n}"
        n += 1
    return folder


def save_photos(file_ids, folder):
    """Fotos von Telegram holen und als 1.jpg, 2.jpg ... speichern. Gibt die Anzahl zurueck."""
    os.makedirs(os.path.join(DEALS_DIR, folder), exist_ok=True)
    saved = 0
    for fid in file_ids[:MAX_PHOTOS]:
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(download(fid)))).convert("RGB")
            im.thumbnail((1600, 1600), Image.LANCZOS)
            saved += 1
            im.save(os.path.join(DEALS_DIR, folder, f"{saved}.jpg"), "JPEG", quality=88)
        except Exception as e:
            print("Foto fehlgeschlagen:", e)
    return saved


# --------------------------------------------------------------------------- Git

def git(*args, raw=False, quiet=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=not raw)
    if r.returncode and not (raw or quiet):
        print(f"$ git {' '.join(args)}\n{r.stderr.strip()}")
    return r


class Repo:
    """Speichert Aenderungen bei GitHub. Bei Konflikten in deals.xlsx werden die Zeilen zusammengefuehrt."""
    PATHS = ("deals", "deals.xlsx", "website.json", "programm/telegram_status.json")

    def __init__(self):
        self.enabled = os.path.isdir(os.path.join(ROOT, ".git")) and bool(
            os.environ.get("GITHUB_ACTIONS") or os.environ.get("DEALBAR_GIT"))
        self.unpushed = False
        self.failures = 0
        self.next_try = 0

    def commit(self, message):
        if not self.enabled:
            return False
        with daten.locked():
            git("add", "-A", "--", *[p for p in self.PATHS if os.path.exists(os.path.join(ROOT, p))])
            if git("diff", "--cached", "--quiet", quiet=True).returncode == 0:
                return False
            git("commit", "-q", "-m", message)
        self.unpushed = True
        return True

    def _rebasing(self):
        g = os.path.join(ROOT, ".git")
        return os.path.isdir(os.path.join(g, "rebase-merge")) or os.path.isdir(os.path.join(g, "rebase-apply"))

    def _resolve(self):
        """Laufenden Rebase mit Konflikten loesen: deals.xlsx zeilenweise, sonst gewinnt der Bot."""
        for _ in range(30):
            if not self._rebasing():
                return True
            files = [f for f in git("diff", "--name-only", "--diff-filter=U").stdout.split("\n") if f]
            for f in files:
                if f == "deals.xlsx":
                    parts = [git("show", f":{i}:deals.xlsx", raw=True) for i in (1, 2, 3)]
                    base, ours, theirs = [p.stdout if p.returncode == 0 else None for p in parts]
                    merged = daten.merge_tables(base, ours, theirs or ours)
                    with open(daten.TABLE, "wb") as fh:
                        fh.write(merged)
                    print("deals.xlsx: Konflikt zeilenweise zusammengefuehrt")
                elif git("checkout", "--theirs", "--", f).returncode != 0:
                    git("rm", "-q", "--", f)
                    continue
                git("add", "--", f)
            r = git("-c", "core.editor=true", "rebase", "--continue")
            if r.returncode and not files and self._rebasing():
                break
        git("rebase", "--abort")
        return False

    def pull(self):
        with daten.locked():
            if git("pull", "-q", "--rebase", "--autostash").returncode == 0:
                return True
            return self._resolve() if self._rebasing() else False

    def push(self):
        """True, wenn alles bei GitHub ist."""
        if not self.enabled or not self.unpushed:
            return True
        if time.time() < self.next_try:
            return False
        if self.pull() and git("push", "-q").returncode == 0:
            self.unpushed, self.failures = False, 0
            return True
        self.failures += 1
        self.next_try = time.time() + min(300, 15 * self.failures)
        return False

    def code_hash(self):
        if not self.enabled:
            return ""
        out = git("ls-tree", "-r", "HEAD", "--", "programm", ".github/workflows/bot.yml").stdout
        keep = [ln for ln in out.splitlines() if ln.endswith((".py", "requirements.txt", "bot.yml"))]
        return hashlib.sha1("\n".join(keep).encode()).hexdigest()


def dispatch(workflow):
    """Workflow starten (Pushes mit dem Actions-Token loesen selbst keinen Workflow aus)."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        return False
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        data=json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main")}).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        print(f"Workflow {workflow} nicht gestartet:", e)
        return False


# --------------------------------------------------------------------------- Zustand

def load_state():
    st = {}
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as fh:
                st = json.load(fh)
        except ValueError:
            st = {}
    if "pending" in st:  # Format der ersten Bot-Version
        p = st.pop("pending")
        if p and p.get("link"):
            p.setdefault("photos", [])
            st["draft"] = p
    st.setdefault("loose", [])
    st.setdefault("posted", [])
    st.pop("sent", None)
    return st


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


# --------------------------------------------------------------------------- Bot

STATUS_ICON = {"fertig": "🟢", "ohne video": "🟢", "neu": "⏳", "fehler": "⚠️"}


def status_icon(status):
    s = (status or "").strip().lower()
    return "⏸" if s in daten.HIDDEN else STATUS_ICON.get(s, "🟢")


class Bot:
    def __init__(self):
        self.st = load_state()
        self.repo = Repo()
        self.settings = daten.load_settings()
        self.proc = None            # laufender Video-Generator
        self.render_wanted = False
        self.site_dirty = False     # Website neu bauen, sobald alles bei GitHub ist
        self.site_retry = 0
        self.show_draft = False     # Entwurfskarte unten neu zeigen
        self.show_await = False     # Foto-Abfrage unten neu zeigen
        self.loose_hint = False
        self.restart = None
        self.end = time.time() + float(os.environ.get("MAX_MINUTES", "340")) * 60
        self.last_sync = time.time()
        self.last_action = 0
        self.code = self.repo.code_hash()

    # ------------------------------------------------------------ Hilfen

    def tag(self):
        return daten.partner_tag(self.settings)

    def site(self):
        return daten.site_url(self.settings)

    def deal_url(self, nr):
        s = self.site()
        return f"{s}/{nr}/" if s and nr else ""

    def commit(self, message, site=True):
        save_state(self.st)
        self.repo.commit(message)
        self.site_dirty |= site

    # ------------------------------------------------------------ Ablauf

    def handle_batch(self, updates):
        for u in updates:
            self.st["offset"] = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    self.on_button(u["callback_query"])
                elif "message" in u:
                    self.on_message(u["message"])
            except Exception as e:
                traceback.print_exc()
                send(f"⚠️ Da ist etwas schiefgelaufen: <code>{esc(e)}</code>\nBitte nochmal versuchen.")
        self.flush()
        save_state(self.st)

    def flush(self):
        """Entwurf bzw. Foto-Abfrage nach neuen Nachrichten unten im Chat neu zeigen (Knoepfe griffbereit)."""
        if self.show_draft and self.st.get("draft"):
            d = self.st["draft"]
            delete(d.get("msg"))
            d["msg"] = send(self.draft_text(d), self.draft_buttons(d))
        if self.show_await and self.st.get("await", {}).get("what") == "photos":
            aw = self.st["await"]
            delete(aw.get("msg"))
            aw["msg"] = send(self.photo_prompt(aw), self.photo_buttons(aw))
        if self.loose_hint and not self.st.get("draft"):
            send("📷 Foto erhalten. Zu welchem Deal gehört es? Schick mir jetzt den Amazon-Link.")
        self.show_draft = self.show_await = self.loose_hint = False

    def tick(self, now=None):
        now = now or time.time()
        d = self.st.get("draft")
        if d and not self.st.get("await"):
            quiet = now - d.get("last", now)
            if (d.get("photos") and quiet >= PHOTO_WAIT) or quiet >= DRAFT_MAX:
                self.save_draft(auto=True)
        aw = self.st.get("await")
        if aw and now - aw.get("t", now) > AWAIT_MAX:
            self.st.pop("await")
            if aw.get("what") == "photos":
                edit(aw.get("msg"), "⌛ Keine neuen Fotos bekommen, alles bleibt wie es war.")
        self.st["loose"] = [x for x in self.st.get("loose", []) if now - x["time"] < DRAFT_MAX]
        self.pipeline()
        if self.repo.push() and self.site_dirty and now >= self.site_retry:
            if dispatch("website.yml") or not os.environ.get("GITHUB_ACTIONS"):
                self.site_dirty = False
            else:
                self.site_retry = now + 60
        save_state(self.st)

    def pipeline(self):
        """Video-Generator im Hintergrund: der Bot bleibt waehrend des Renderns ansprechbar."""
        if self.proc:
            if self.proc.poll() is None:
                if time.time() - self.last_action > 4.5:
                    try:
                        api("sendChatAction", {"chat_id": CHAT_ID, "action": "record_video"})
                    except Exception:
                        pass
                    self.last_action = time.time()
                return
            rc, self.proc = self.proc.returncode, None
            self.after_render(rc)
        if self.render_wanted:
            self.render_wanted = False
            if os.path.exists(RESULTS):
                os.remove(RESULTS)
            self.proc = subprocess.Popen([sys.executable, RUN_PY], cwd=ROOT)

    def after_render(self, rc):
        results = []
        if os.path.exists(RESULTS):
            with open(RESULTS, encoding="utf-8") as fh:
                results = json.load(fh)
        for res in results:
            try:
                if res.get("status") == "fertig":
                    self.send_video(res)
                else:
                    send(f"⚠️ Video für <b>#{res.get('nr')} {esc(res.get('title'))}</b> fehlgeschlagen:\n"
                         f"<code>{esc(res.get('error'))}</code>\nDer Deal ist trotzdem auf der Website. "
                         "In /liste kannst du es mit 🎬 nochmal versuchen.")
                nr = res.get("nr")
                if nr and nr not in self.st["posted"]:
                    self.post_channel(nr, res.get("video"))
            except Exception as e:
                traceback.print_exc()
                send(f"⚠️ Video konnte nicht gesendet werden: <code>{esc(e)}</code>")
        if rc and not results:
            send(f"⚠️ Der Video-Generator ist abgebrochen (Code {rc}). Die Deals sind gespeichert; "
                 "mit 🎬 in /liste kannst du es erneut versuchen.")
        self.commit("Videos erstellt", site=False)

    # ------------------------------------------------------------ Nachrichten

    def on_message(self, msg):
        sender = str(msg["chat"]["id"])
        if not CHAT_ID:
            send(f"Hallo! Deine Telegram-ID ist <code>{sender}</code>.\nTrage sie bei GitHub als Variable "
                 "<b>TELEGRAM_CHAT_ID</b> ein, dann nehme ich Deals von dir an.", chat=sender)
            return
        if sender != CHAT_ID:
            return
        text = msg.get("text") or msg.get("caption") or ""
        photo = msg["photo"][-1]["file_id"] if msg.get("photo") else None
        doc = msg.get("document") or {}
        if not photo and str(doc.get("mime_type", "")).startswith("image/"):
            photo = doc["file_id"]
        t = msg.get("date", int(time.time()))

        if text.startswith("/"):
            m = re.match(r"(\S+)\s*(.*)", text, re.S)
            self.on_command(m.group(1).split("@")[0].lower(), m.group(2).strip())
            return
        fields = parse(text) if text else {}
        if "link" in fields:
            self.new_draft(fields, photo, t)
            return
        aw = self.st.get("await")
        if aw:
            if aw["what"] == "photos" and photo:
                aw["photos"] = (aw.get("photos") or []) + [photo]
                aw["t"] = t
                self.show_await = True
                return
            if text and aw["what"] in ("title", "hook", "price"):
                self.answer_await(aw, text)
                return
        d = self.st.get("draft")
        if d:
            if photo:
                if len(d["photos"]) < MAX_PHOTOS:
                    d["photos"].append(photo)
                else:
                    d["too_many"] = True
            self.merge_fields(d, fields)
            d["last"] = t
            self.show_draft = True
            return
        if photo:
            self.st["loose"].append({"id": photo, "time": t})
            self.loose_hint = True
        elif msg.get("video") or msg.get("video_note"):
            send("Videos kann ich noch nicht verwenden. Schick mir einen Amazon-Link und 1–4 eigene Fotos.")
        elif text:
            send("Ich habe keinen Amazon-Link gefunden. Teile das Produkt aus der Amazon-App mit mir "
                 "oder füge den Link ein. /hilfe zeigt, wie es geht.")

    def merge_fields(self, d, f):
        for k in ("title", "hook", "cat", "label"):
            if f.get(k):
                d[k] = f[k]
                if k == "title":
                    d["title_src"] = "text"
        if f.get("new"):
            d["new"], d["old"] = f["new"], f.get("old")
        rest = f.get("rest") or []
        if rest and d.get("title_src") == "auto":
            d["title"], d["title_src"] = shorten(rest.pop(0), 80), "text"
        if rest:
            d["hook"] = " ".join(rest)[:90]

    def new_draft(self, f, photo, t):
        if self.st.get("draft"):
            self.save_draft(auto=True)
        self.st.pop("await", None)
        full = daten.resolve_short(f["link"])
        url, asin, note = daten.clean_link(full, self.tag(), resolve=False)
        rest = f.get("rest") or []
        title, src = f.get("title"), "text"
        if not title and f.get("share_title"):
            title, src = f["share_title"], "share"
        if not title and len(rest) >= 2:
            title, src = shorten(rest.pop(0), 80), "text"
        if not title:
            title, src = title_from_url(full), "url"
        if not title and len(rest) == 1:
            title, src = shorten(rest.pop(0), 80), "text"
        if not title:
            title, src = (f"Amazon-Produkt {asin}" if asin else "Neuer Deal"), "auto"
        photos = [x["id"] for x in self.st.get("loose", []) if t - x["time"] < DRAFT_MAX]
        if photo:
            photos.append(photo)
        self.st["draft"] = {
            "link": f["link"], "url": url, "asin": asin, "note": note, "title": title, "title_src": src,
            "hook": f.get("hook") or " ".join(rest)[:90], "cat": f.get("cat"), "new": f.get("new"),
            "old": f.get("old"), "label": f.get("label"), "photos": photos[:MAX_PHOTOS], "time": t, "last": t,
        }
        self.st["loose"] = []
        self.show_draft = True

    # ------------------------------------------------------------ Entwurf

    def draft_text(self, d):
        L = ["📝 <b>Neuer Deal</b> · Entwurf", ""]
        L.append(f"<b>{esc(d['title'])}</b>")
        if d.get("title_src") in ("auto", "url"):
            L.append("<i>✏️ Titel automatisch erkannt, bei Bedarf ändern.</i>")
        if d.get("new"):
            off = daten.discount(d.get("old"), d["new"])
            L.append(f"💶 {eur(d['new'])}" + (f" statt {eur(d['old'])}" if d.get("old") else "") +
                     (f" <b>(−{off} %)</b>" if off else ""))
        else:
            L.append("💶 <i>Preis fehlt</i> – schick z. B. <code>19,99 statt 34,99</code>")
        cat = d.get("cat")
        L.append(f"{CATEGORIES.get(cat, ('🏷',))[0]} {esc(cat)}" if cat else "🏷 <i>Kategorie wählen</i> ↓")
        n = len(d.get("photos", []))
        if n:
            L.append(f"📷 {n} Foto{'s' if n > 1 else ''}" + (" (mehr als 4 gehen nicht)" if d.get("too_many") else ""))
        else:
            L.append("📷 Keine Fotos – Video kommt im Text-Design. Eigene Fotos? Einfach schicken.")
        if d.get("hook"):
            L.append(f"💬 {esc(d['hook'])}")
        notes = []
        if d.get("note"):
            notes.append("⚠️ " + esc(d["note"]))
        if not self.tag() and "tag=" not in (d.get("url") or ""):
            notes.append("⚠️ Partner-ID fehlt, ohne sie gibt es keine Provision → /partnerid")
        if notes:
            L += [""] + notes
        L += ["", "Passt alles? Dann <b>✅ Speichern</b>."]
        if n:
            L.append(f"<i>Sonst speichere ich {PHOTO_WAIT // 60} Min. nach dem letzten Foto automatisch.</i>")
        return "\n".join(L)

    def draft_buttons(self, d):
        rows = []
        if not d.get("cat") or d.get("pick"):
            names = list(CATEGORIES)
            for i in range(0, len(names), 3):
                rows.append([(f"{CATEGORIES[c][0]} {CATEGORIES[c][1]}", f"dr:c:{j}")
                             for j, c in enumerate(names[i:i + 3], i)])
        rows.append([("✏️ Titel", "dr:titel"), ("💶 Preis", "dr:preis")])
        rows.append([("💬 Vorteil", "dr:hook")] + ([("🏷 Kategorie", "dr:kat")] if d.get("cat") else []))
        rows.append([("🗑 Verwerfen", "dr:weg"), ("✅ Speichern", "dr:ok")])
        return rows

    def save_draft(self, auto=False):
        d = self.st.pop("draft", None)
        if not d:
            return None
        title = d.get("title") or "Neuer Deal"
        folder = folder_for(title)
        saved = save_photos(d.get("photos", []), folder)
        if not saved:
            os.makedirs(os.path.join(DEALS_DIR, folder), exist_ok=True)
        old, new = d.get("old"), d.get("new")
        stamp = daten.fmt_dt(datetime.fromtimestamp(d.get("time") or time.time()))
        with daten.open_table(write=True) as t:
            r, nr = t.next_row(), t.next_nr()
            values = {
                "Status": "neu", "Ordner": folder, "Produktname": title, "Alter Preis (€)": old,
                "Neuer Preis (€)": new, "Preis geprüft am": stamp, "Hook": d.get("hook") or "",
                "Affiliate-Link": d.get("url") or d.get("link"), "Label": d.get("label") or auto_label(old, new),
                "Preis zeigen": "ja" if new else "nein", "Kategorie": d.get("cat") or "", "Nr": nr,
                "ASIN": d.get("asin") or "", "Erstellt am": None, "Videodatei": None,
            }
            for k, v in values.items():
                t.set(r, k, v)
            t.ensure_formula(r)
        self.render_wanted = True
        self.commit(f"Neuer Deal #{nr}: {title}")
        off = daten.discount(old, new)
        L = [f"✅ <b>Gespeichert als Deal #{nr}</b>" + (" (automatisch)" if auto else ""), esc(title)]
        if off:
            L.append(f"💶 {eur(new)} statt {eur(old)} (−{off} %)")
        L.append(f"📷 {saved} Foto{'s' if saved != 1 else ''} · {esc(d.get('cat') or 'ohne Kategorie')}")
        L.append("")
        L.append("🌐 In ca. 1 Minute auf der Website, das Video folgt in 2–3 Minuten.")
        url = self.deal_url(nr)
        buttons = [[("🌐 Deal-Seite", "url:" + url)]] if url else None
        if not edit(d.get("msg"), "\n".join(L), buttons):
            send("\n".join(L), buttons)
        return nr

    def on_draft_button(self, action, arg, d, mid):
        if not d or (d.get("msg") and d.get("msg") != mid):
            edit(mid, "Dieser Entwurf ist schon erledigt.")
            return "Schon erledigt"
        d["last"] = time.time()
        if action == "ok":
            self.save_draft()
            return "Gespeichert"
        if action == "weg":
            self.st.pop("draft")
            self.st["loose"] = []
            edit(mid, f"🗑 Entwurf verworfen: {esc(d.get('title'))}")
            return "Verworfen"
        if action == "c":
            d["cat"] = list(CATEGORIES)[int(arg)]
            d["pick"] = False
        elif action == "kat":
            d["pick"] = True
        elif action in ("titel", "preis", "hook"):
            prompts = {
                "titel": ("✏️ Schick mir den Titel, kurz und klar, z. B. <i>Helly Hansen Damen Regenjacke</i>",
                          "Titel"),
                "preis": ("💶 Schick mir den Preis, z. B. <code>19,99 statt 34,99</code>", "19,99 statt 34,99"),
                "hook": ("💬 Schick mir einen kurzen Vorteil fürs Video (bis 60 Zeichen), "
                         "z. B. <i>Wasserdicht und atmungsaktiv</i>", "Vorteil"),
            }
            text, hint = prompts[action]
            self.st["await"] = {"what": {"titel": "title"}.get(action, action), "nr": None, "t": time.time(),
                                "msg": send(text, force_reply=hint)}
            return ""
        edit(mid, self.draft_text(d), self.draft_buttons(d))
        return ""

    def answer_await(self, aw, text):
        nr, what = aw.get("nr"), aw["what"]
        if what == "price":
            prices = parse_prices(text) or (parse_prices(text.split(":", 1)[-1]) if ":" in text else None)
            if not prices:
                send("Den Preis habe ich nicht verstanden. Bitte so: <code>19,99 statt 34,99</code>",
                     force_reply="19,99 statt 34,99")
                return
        self.st.pop("await", None)
        if nr is None:
            d = self.st.get("draft")
            if not d:
                return
            if what == "title":
                d["title"], d["title_src"] = shorten(text, 80), "text"
            elif what == "hook":
                d["hook"] = text.strip()[:90]
            else:
                d["new"], d["old"] = prices
            d["last"] = time.time()
            self.show_draft = True
            return
        if what == "price":
            new, old = prices
            with daten.open_table(write=True) as t:
                r = t.find(nr=nr)
                if not r:
                    send(f"Deal #{nr} nicht gefunden.")
                    return
                t.set(r, "Neuer Preis (€)", new)
                t.set(r, "Alter Preis (€)", old)
                t.set(r, "Preis geprüft am", daten.fmt_dt(datetime.now()))
                t.set(r, "Preis zeigen", "ja")
                title = t.text(r, "Produktname")
            self.commit(f"Deal #{nr}: neuer Preis")
            off = daten.discount(old, new)
            send(f"✅ Neuer Preis für <b>#{nr} {esc(title)}</b>: {eur(new)}" +
                 (f" statt {eur(old)} (−{off} %)" if off else "") +
                 "\nDie Website zeigt ihn in ca. 1 Minute. Das Video zeigt noch den alten Preis.",
                 [[("🎬 Neues Video erstellen", f"dx:{nr}:video")], [("◀ Zum Deal", f"dv:{nr}")]])

    # ------------------------------------------------------------ Knoepfe

    def on_button(self, q):
        msg = q.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != CHAT_ID:
            answer(q["id"])
            return
        data, mid = q.get("data", ""), msg.get("message_id")
        kind, _, rest = data.partition(":")
        toast = ""
        if kind == "dr":
            action, _, arg = rest.partition(":")
            toast = self.on_draft_button(action, arg, self.st.get("draft"), mid)
        elif kind == "ls":
            text, buttons = self.list_view(int(rest or 0))
            edit(mid, text, buttons)
        elif kind == "dv":
            text, buttons = self.deal_view(int(rest))
            edit(mid, text, buttons)
        elif kind == "dx":
            nr, _, action = rest.partition(":")
            toast = self.deal_action(int(nr), action, mid)
        elif kind == "aw":
            toast = self.on_await_button(rest, mid)
        answer(q["id"], toast or "")

    # ------------------------------------------------------------ Deals verwalten

    def list_view(self, page=0):
        with daten.open_table() as t:
            rows = [r for r in t.rows() if daten.valid_link(t.text(r, "Affiliate-Link"))]
            rows.sort(key=lambda r: (t.nr(r) or 0, r), reverse=True)
            items = [(t.nr(r), t.text(r, "Produktname"), t.text(r, "Status")) for r in rows]
        if not items:
            return "Noch keine Deals. Schick mir einfach einen Amazon-Link.", None
        pages = (len(items) + LIST_PAGE - 1) // LIST_PAGE
        page = max(0, min(page, pages - 1))
        chunk = items[page * LIST_PAGE:(page + 1) * LIST_PAGE]
        text = (f"📋 <b>Deine Deals</b> ({len(items)}, neueste zuerst)\nTippe einen an, um ihn zu bearbeiten.\n"
                "<i>🟢 online · ⏳ Video in Arbeit · ⚠️ Video-Fehler · ⏸ offline</i>")
        rows = [[(f"{status_icon(s)} #{nr} {title[:30]}{'…' if len(title) > 30 else ''}", f"dv:{nr}")]
                for nr, title, s in chunk]
        nav = []
        if page > 0:
            nav.append(("◀ Neuere", f"ls:{page - 1}"))
        if page < pages - 1:
            nav.append(("Ältere ▶", f"ls:{page + 1}"))
        if nav:
            rows.append(nav)
        return text, rows

    def deal_view(self, nr, note=""):
        hours = float(self.settings.get("rabatt_zeigen_stunden", 24))
        with daten.open_table() as t:
            r = t.find(nr=nr)
            if not r:
                return f"Deal #{nr} nicht gefunden.", [[("◀ Zur Liste", "ls:0")]]
            title, status = t.text(r, "Produktname"), t.text(r, "Status")
            old, new = daten.num(t.get(r, "Alter Preis (€)")), daten.num(t.get(r, "Neuer Preis (€)"))
            checked = daten.parse_dt(t.get(r, "Preis geprüft am"))
            cat, hook, link = t.text(r, "Kategorie"), t.text(r, "Hook"), t.text(r, "Affiliate-Link")
            folder, video = t.text(r, "Ordner"), t.text(r, "Videodatei")
        hidden = status.lower() in daten.HIDDEN
        L = []
        if note:
            L += [note, ""]
        L.append(f"<b>#{nr} · {esc(title)}</b>")
        L.append(("⏸ Offline" if hidden else "🟢 Online") +
                 (f" · {CATEGORIES.get(cat, ('🏷',))[0]} {esc(cat)}" if cat else ""))
        off = daten.discount(old, new)
        if new:
            L.append(f"💶 {eur(new)}" + (f" statt {eur(old)} (−{off} %)" if off else ""))
        if checked:
            until = checked + timedelta(hours=hours)
            if datetime.now() < until:
                L.append(f"🕒 Preis vom {daten.fmt_dt(checked)}, Rabatt auf der Website sichtbar bis "
                         f"{until.strftime('%d.%m., %H:%M')} Uhr")
            else:
                L.append(f"🕒 Preis vom {daten.fmt_dt(checked)}, Rabatt auf der Website ausgeblendet "
                         "(älter als 24 h). Noch gültig? Dann 🔄")
        if hook:
            L.append(f"💬 {esc(hook)}")
        n = len(daten.images_in(folder))
        vid = {"fertig": "fertig", "neu": "in Arbeit", "fehler": "Fehler"}.get(status.lower(), "–" if not video else "fertig")
        L.append(f"📷 {n} Foto{'s' if n != 1 else ''} · 🎬 Video: {vid}")
        rows = [[("🔄 Preis noch aktuell", f"dx:{nr}:frisch"), ("💶 Neuer Preis", f"dx:{nr}:preis")],
                [("🎬 Video neu", f"dx:{nr}:video"), ("🖼 Fotos ändern", f"dx:{nr}:fotos")],
                [("▶️ Wieder online", f"dx:{nr}:an") if hidden else ("⏸ Offline nehmen", f"dx:{nr}:aus")]]
        if self.settings.get("telegram_kanal") and not hidden:
            rows.append([("📣 Im Kanal posten", f"dx:{nr}:post")])
        links = []
        if self.deal_url(nr) and not hidden:
            links.append(("🌐 Deal-Seite", "url:" + self.deal_url(nr)))
        if daten.valid_link(link):
            links.append(("🔍 Bei Amazon", "url:" + daten.plain_link(link)))
        if links:
            rows.append(links)
        rows.append([("◀ Zur Liste", "ls:0")])
        return "\n".join(L), rows

    def deal_action(self, nr, action, mid):
        if action in ("preis", "fotos"):
            if action == "preis":
                self.st["await"] = {"what": "price", "nr": nr, "t": time.time(),
                                    "msg": send(f"💶 Neuer Preis für <b>#{nr}</b>? z. B. <code>17,99 statt 34,99</code>",
                                                force_reply="17,99 statt 34,99")}
            else:
                aw = {"what": "photos", "nr": nr, "t": time.time(), "photos": []}
                aw["msg"] = send(self.photo_prompt(aw), self.photo_buttons(aw))
                self.st["await"] = aw
            return ""
        if action == "post":
            ok = self.post_channel(nr, None, again=True)
            note = "📣 Im Kanal gepostet." if ok else "⚠️ Posten im Kanal hat nicht geklappt, siehe /status."
            text, buttons = self.deal_view(nr, note)
            edit(mid, text, buttons)
            return "Gepostet" if ok else "Fehler"
        with daten.open_table(write=True) as t:
            r = t.find(nr=nr)
            if not r:
                edit(mid, f"Deal #{nr} nicht gefunden.", [[("◀ Zur Liste", "ls:0")]])
                return ""
            if action == "frisch":
                t.set(r, "Preis geprüft am", daten.fmt_dt(datetime.now()))
                note, msg = "✅ Preis bestätigt: Der Rabatt ist wieder 24 Stunden auf der Website.", "Preis bestätigt"
            elif action == "aus":
                t.set(r, "Status", "abgelaufen")
                note, msg = "⏸ Offline genommen, in ca. 1 Minute ist er von der Website verschwunden.", "Offline"
            elif action == "an":
                t.set(r, "Status", "fertig" if t.text(r, "Videodatei") else "neu")
                note, msg = "▶️ Wieder online.", "Online"
            elif action == "video":
                t.set(r, "Status", "neu")
                note, msg = "🎬 Neues Video wird erstellt, es kommt in 2–3 Minuten.", "Video kommt"
            else:
                return ""
        self.commit(f"Deal #{nr}: {msg}", site=action != "video")
        if action == "video" or t.text(r, "Status") == "neu":
            self.render_wanted = True
        text, buttons = self.deal_view(nr, note)
        edit(mid, text, buttons)
        return msg

    def photo_prompt(self, aw):
        n = len(aw.get("photos") or [])
        return (f"🖼 <b>Fotos für Deal #{aw['nr']} ändern</b>\n"
                f"Schick mir 1–4 neue <b>eigene</b> Fotos, sie ersetzen die alten. Keine Screenshots von Amazon.\n\n"
                f"Bisher erhalten: {n} Foto{'s' if n != 1 else ''}.\n"
                "Oder entferne alle Fotos, dann gibt es ein Video im Text-Design.")

    def photo_buttons(self, aw):
        rows = []
        if aw.get("photos"):
            rows.append([("✅ Diese Fotos nehmen", "aw:ok")])
        rows.append([("🚫 Ohne Fotos", "aw:ohne"), ("✖ Abbrechen", "aw:x")])
        return rows

    def on_await_button(self, action, mid):
        aw = self.st.get("await")
        if not aw or aw.get("what") != "photos":
            edit(mid, "Diese Abfrage ist schon erledigt.")
            return ""
        self.st.pop("await")
        nr = aw["nr"]
        if action == "x":
            edit(mid, f"✖ Fotos von #{nr} bleiben, wie sie waren.")
            return "Abgebrochen"
        with daten.open_table(write=True) as t:
            r = t.find(nr=nr)
            if not r:
                edit(mid, f"Deal #{nr} nicht gefunden.")
                return ""
            folder = t.text(r, "Ordner") or folder_for(t.text(r, "Produktname"))
            t.set(r, "Ordner", folder)
            path = os.path.join(DEALS_DIR, folder)
            os.makedirs(path, exist_ok=True)
            for f in os.listdir(path):
                if f.lower().endswith(daten.IMG_EXT):
                    os.remove(os.path.join(path, f))
            saved = save_photos(aw.get("photos", []), folder) if action == "ok" else 0
            t.set(r, "Status", "neu")
        self.render_wanted = True
        self.commit(f"Deal #{nr}: Fotos geändert")
        edit(mid, f"✅ Deal #{nr}: " + (f"{saved} neue Fotos gespeichert." if saved else "alle Fotos entfernt.") +
             " Website und Video werden aktualisiert.", [[("◀ Zum Deal", f"dv:{nr}")]])
        return "Gespeichert"

    # ------------------------------------------------------------ Videos und Kanal

    def send_video(self, res):
        nr, title = res.get("nr"), res.get("title", "")
        path = os.path.join(VIDEO_DIR, res["video"])
        buttons = []
        if self.deal_url(nr):
            buttons.append(("🌐 Deal-Seite", "url:" + self.deal_url(nr)))
        if daten.valid_link(res.get("link")):
            buttons.append(("🔍 Bei Amazon", "url:" + daten.plain_link(res["link"])))
        with open(path, "rb") as fh:
            data = fh.read()
        params = {"chat_id": CHAT_ID, "caption": f"🎬 <b>Deal #{nr}</b> · {esc(title)}", "parse_mode": "HTML",
                  "supports_streaming": "true", "width": 1080, "height": 1920,
                  "duration": int(res.get("duration") or 0)}
        if buttons:
            params["reply_markup"] = keyboard([buttons])
        api("sendVideo", params, files={"video": (res["video"], data)}, timeout=180)
        if res.get("caption"):
            send("📋 <b>Text zum Posten</b> (antippen zum Kopieren):\n<pre>" + esc(res["caption"].strip()) + "</pre>")

    def channel_text(self, row):
        title, hook = str(row.get("Produktname") or ""), str(row.get("Hook") or "")
        old, new = daten.num(row.get("Alter Preis (€)")), daten.num(row.get("Neuer Preis (€)"))
        show = str(row.get("Preis zeigen") or "ja").strip().lower() not in ("nein", "n", "no")
        off = daten.discount(old, new) if show else None
        link = daten.with_tag(str(row.get("Affiliate-Link") or ""), self.tag())
        L = [f"🔥 <b>{f'−{off} % · ' if off else ''}{esc(title)}</b>"]
        if hook:
            L.append(esc(hook))
        L.append("")
        if show and new:
            L.append(f"💶 Jetzt <b>{eur(new)}</b>" + (f" statt <s>{eur(old)}</s>" if off else "") + "*")
        L.append(f'🛒 <a href="{html.escape(link)}">Zum Angebot bei Amazon</a>*')
        L.append("")
        foot = []
        checked = daten.parse_dt(row.get("Preis geprüft am"))
        if show and new and checked:
            foot.append(f"*Preis vom {daten.fmt_dt(checked)} Uhr, kann sich jederzeit ändern.")
        foot.append(f"Anzeige · {daten.DISCLOSURE}")
        L.append("<i>" + "\n".join(foot) + "</i>")
        return "\n".join(L), link

    def post_channel(self, nr, video=None, again=False):
        """Deal im Telegram-Kanal posten (falls mit /kanal verbunden). True bei Erfolg."""
        kanal = (self.settings.get("telegram_kanal") or "").strip()
        if not kanal or (nr in self.st["posted"] and not again):
            return False
        with daten.open_table() as t:
            r = t.find(nr=nr)
            if not r or t.text(r, "Status").lower() in daten.HIDDEN:
                return False
            row = t.row(r)
        text, link = self.channel_text(row)
        buttons = [[("🛒 Zum Angebot*", "url:" + link)]] if daten.valid_link(link) else []
        if self.site():
            buttons.append([("🌐 Alle Deals", "url:" + self.site() + "/")])
        params = {"chat_id": kanal, "parse_mode": "HTML", "reply_markup": keyboard(buttons)}
        imgs = daten.images_in(str(row.get("Ordner") or ""))
        video = video or str(row.get("Videodatei") or "")
        vpath = os.path.join(VIDEO_DIR, video) if video.endswith(".mp4") else ""
        try:
            if vpath and os.path.exists(vpath):
                with open(vpath, "rb") as fh:
                    api("sendVideo", dict(params, caption=text, supports_streaming="true", width=1080, height=1920),
                        files={"video": (video, fh.read())}, timeout=180)
            elif imgs:
                with open(os.path.join(DEALS_DIR, str(row["Ordner"]), imgs[0]), "rb") as fh:
                    api("sendPhoto", dict(params, caption=text), files={"photo": (imgs[0], fh.read())})
            else:
                api("sendMessage", dict(params, text=text, link_preview_options={"is_disabled": True}))
        except Exception as e:
            send(f"⚠️ Post im Kanal {esc(kanal)} fehlgeschlagen: <code>{esc(e)}</code>\n"
                 "Ist der Bot dort Administrator mit Recht zum Posten? Prüfen mit /kanal")
            return False
        if nr not in self.st["posted"]:
            self.st["posted"] = (self.st["posted"] + [nr])[-500:]
        return True

    # ------------------------------------------------------------ Befehle

    def on_command(self, cmd, arg):
        if cmd in ("/start", "/hilfe", "/help"):
            send(HELP)
        elif cmd == "/id":
            send(f"Deine Telegram-ID: <code>{CHAT_ID}</code>")
        elif cmd == "/abbrechen":
            d = self.st.pop("draft", None)
            self.st.pop("await", None)
            self.st["loose"] = []
            if d:
                edit(d.get("msg"), f"🗑 Entwurf verworfen: {esc(d.get('title'))}")
            send("Alles zurückgesetzt. Schick mir den nächsten Amazon-Link, wenn du so weit bist.")
        elif cmd == "/liste":
            text, buttons = self.list_view(0)
            send(text, buttons)
        elif cmd == "/aus":
            try:
                nr = int(arg.strip(" #"))
            except ValueError:
                send("Bitte die Deal-Nummer angeben, z. B. <code>/aus 12</code>. Einfacher geht es über /liste.")
                return
            text, buttons = self.deal_view(nr)
            mid = send(text, buttons)
            self.deal_action(nr, "aus", mid)
        elif cmd == "/status":
            send(self.status_text())
        elif cmd == "/partnerid":
            self.cmd_partnerid(arg)
        elif cmd == "/impressum":
            self.cmd_impressum(arg)
        elif cmd == "/kanal":
            self.cmd_kanal(arg)
        else:
            send("Unbekannter Befehl. /hilfe zeigt alle Befehle.")

    def status_text(self):
        s = self.settings
        with daten.open_table() as t:
            rows = [r for r in t.rows() if daten.valid_link(t.text(r, "Affiliate-Link"))]
            online = sum(1 for r in rows if t.text(r, "Status").lower() not in daten.HIDDEN)
        L = ["📊 <b>dealbar-Status</b>", ""]
        L.append(f"✅ Bot läuft, nächster automatischer Neustart ca. "
                 f"{datetime.fromtimestamp(self.end).strftime('%H:%M')} Uhr (dauert 1 Minute)")
        site = self.site()
        L.append(f"🌐 {online} Deal{'s' if online != 1 else ''} online" + (f": {site}/" if site else ""))
        tag = self.tag()
        L.append(f"✅ Partner-ID: <code>{esc(tag)}</code>" if tag else
                 "⚠️ <b>Partner-ID fehlt</b>, ohne sie gibt es keine Provision → /partnerid")
        L.append("✅ Impressum ausgefüllt" if daten.impressum_ok(s) else
                 "⚠️ <b>Impressum fehlt</b> (Pflicht für die Website) → /impressum")
        k = s.get("telegram_kanal")
        L.append(f"✅ Telegram-Kanal: {esc(k)}" if k else "➖ Telegram-Kanal nicht verbunden (optional) → /kanal")
        L += ["", "📷 Erinnerung: nur eigene Fotos, keine Screenshots oder Bilder von Amazon."]
        return "\n".join(L)

    def cmd_partnerid(self, arg):
        tag = arg.strip().split()[0] if arg.strip() else ""
        if not tag:
            cur = self.tag()
            send(("Aktuelle Partner-ID: <code>" + esc(cur) + "</code>\n\n" if cur else "") +
                 "Deine Partner-ID (Tracking-ID) findest du in Amazon PartnerNet oben rechts, "
                 "sie endet auf <b>-21</b>. Schick sie mir so:\n<code>/partnerid deinname-21</code>")
            return
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,60}-2\d", tag):
            send("Das sieht nicht nach einer Partner-ID aus. Sie endet auf <b>-21</b>, "
                 "z. B. <code>/partnerid dealbar-21</code>")
            return
        daten.save_settings({"partner_tag": tag})
        self.settings = daten.load_settings()
        env = os.environ.get("PARTNER_TAG", "").strip()
        self.commit(f"Partner-ID eingetragen: {tag}")
        send(f"✅ Partner-ID <code>{esc(tag)}</code> gespeichert. Alle Links auf der Website bekommen sie "
             "in ca. 1 Minute, auch die der bisherigen Deals." +
             (f"\n⚠️ Bei GitHub ist zusätzlich PARTNER_TAG=<code>{esc(env)}</code> eingetragen, die hat Vorrang."
              if env and env != tag else ""))

    def cmd_impressum(self, arg):
        lines = [x.strip() for x in re.split(r"\n|\s{2,}", arg) if x.strip()]
        email = next((x for x in lines if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", x)), "")
        plz = next((x for x in lines if re.fullmatch(r"(D-)?\d{5}\s+\S.*", x)), "")
        phone = next((x for x in lines if x not in (email, plz) and re.fullmatch(r"[+\d][\d\s/()-]{6,}", x)), "")
        others = [x for x in lines if x not in (email, plz, phone)]
        if not (email and plz and len(others) >= 2):
            send("Schick mir dein Impressum so, in einer Nachricht:\n\n"
                 "<code>/impressum\nVorname Nachname\nStraße Hausnummer\n12345 Ort\ndeine@email.de</code>\n\n"
                 "Das ist gesetzlich Pflicht und steht danach öffentlich auf der Website (Impressum).")
            return
        imp = {"name": others[0], "strasse": others[1], "plz_ort": plz, "email": email, "telefon": phone}
        daten.save_settings({"impressum": imp})
        self.settings = daten.load_settings()
        self.commit("Impressum eingetragen")
        send("✅ Impressum gespeichert, die Website wird in ca. 1 Minute aktualisiert:\n\n" +
             esc(f"{imp['name']}\n{imp['strasse']}\n{imp['plz_ort']}\n{imp['email']}" +
                 (f"\n{phone}" if phone else "")))

    def cmd_kanal(self, arg):
        arg = arg.strip()
        if arg.lower() in ("aus", "off", "trennen"):
            daten.save_settings({"telegram_kanal": ""})
            self.settings = daten.load_settings()
            self.commit("Telegram-Kanal getrennt")
            send("Telegram-Kanal getrennt. Neue Deals werden nicht mehr automatisch gepostet.")
            return
        if not arg:
            cur = self.settings.get("telegram_kanal")
            send((f"Verbunden: {esc(cur)}\n\n" if cur else "") +
                 "<b>So verbindest du deinen Telegram-Kanal</b>\n"
                 "1. In Telegram einen öffentlichen Kanal anlegen (z. B. <i>dealbar Deals</i>, Link <i>@dealbar_deals</i>).\n"
                 "2. Kanal-Info → Administratoren → Administrator hinzufügen → diesen Bot wählen "
                 "(Recht „Nachrichten senden“ reicht).\n"
                 "3. Mir schicken: <code>/kanal @dealbar_deals</code>\n\n"
                 "Danach poste ich jeden neuen Deal automatisch dort, mit Video, Link und Werbehinweis. "
                 "Trage den Kanal auch in PartnerNet unter „Websites und mobile Apps“ ein.")
            return
        kanal = arg.split()[0]
        if kanal.startswith("https://t.me/"):
            kanal = "@" + kanal.rsplit("/", 1)[-1]
        if not kanal.startswith("@") and not re.fullmatch(r"-?\d+", kanal):
            kanal = "@" + kanal
        try:
            chat = api("getChat", {"chat_id": kanal})
            me = api("getMe")
            member = api("getChatMember", {"chat_id": chat["id"], "user_id": me["id"]})
        except Exception as e:
            send(f"⚠️ Ich komme nicht an den Kanal {esc(kanal)}: <code>{esc(e)}</code>\n"
                 "Stimmt der Name, und bin ich dort Administrator?")
            return
        if member.get("status") != "administrator" or member.get("can_post_messages") is False:
            send(f"⚠️ Ich bin in {esc(chat.get('title') or kanal)} noch kein Administrator mit Recht zum Posten. "
                 "Kanal-Info → Administratoren → Administrator hinzufügen → mich auswählen.")
            return
        daten.save_settings({"telegram_kanal": kanal})
        self.settings = daten.load_settings()
        with daten.open_table() as t:  # bisherige Deals nicht nachtraeglich automatisch posten
            self.st["posted"] = sorted(set(self.st["posted"]) | {t.nr(r) for r in t.rows() if t.nr(r)})
        self.commit(f"Telegram-Kanal verbunden: {kanal}")
        send(f"✅ Kanal <b>{esc(chat.get('title') or kanal)}</b> verbunden. Ab jetzt poste ich jeden neuen Deal "
             "dort automatisch. Ältere Deals kannst du in /liste mit 📣 posten.")

    # ------------------------------------------------------------ Start und Ende

    def startup(self):
        try:
            api("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
        except Exception as e:
            print("Befehlsliste nicht gesetzt:", e)
        if daten.migrate_table():
            self.commit("Tabelle: Spalten Nr und ASIN ergänzt")
        with daten.open_table() as t:
            if any(t.text(r, "Status").lower() == "neu" for r in t.rows()):
                self.render_wanted = True  # Zeilen, die direkt in deals.xlsx auf "neu" gesetzt wurden

    def sync(self):
        """Alle paar Minuten: Aenderungen von GitHub holen. Neue Programmversion -> sauber neu starten."""
        if not self.repo.enabled or time.time() - self.last_sync < SYNC_EVERY or self.proc:
            return
        self.last_sync = time.time()
        if self.repo.unpushed and not self.repo.push():
            return
        if self.repo.pull():
            self.settings = daten.load_settings()
            if self.code and self.repo.code_hash() != self.code:
                self.restart = "neue Programmversion"

    def shutdown(self):
        deadline = time.time() + 10 * 60
        while (self.proc or self.render_wanted) and time.time() < deadline:
            self.pipeline()
            time.sleep(2)
        save_state(self.st)
        self.repo.commit("Telegram-Status")
        for _ in range(5):
            if self.repo.push():
                break
            time.sleep(10)
            self.repo.next_try = 0
        if self.site_dirty:
            dispatch("website.yml")
        dispatch("bot.yml")  # naechsten Lauf selbst anstossen, der Zeitplan ist nur die Reserve
        print(f"Lauf beendet ({self.restart or 'Zeit um'}), der nächste übernimmt.")


def lauschen():
    """Dauerbetrieb: wartet per Long-Polling auf Nachrichten und verarbeitet sie sofort."""
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN fehlt.")
    bot = Bot()
    bot.startup()
    while time.time() < bot.end and not bot.restart:
        wait = 3 if (bot.proc or bot.render_wanted) else 25
        wait = int(max(1, min(wait, bot.end - time.time())))
        try:
            updates = api("getUpdates", {"offset": bot.st.get("offset", 0), "timeout": wait,
                                         "allowed_updates": ["message", "callback_query"]}, timeout=wait + 30)
        except Exception as e:
            print("Telegram nicht erreichbar:", e)
            time.sleep(10)
            updates = []
        bot.handle_batch(updates)
        bot.tick()
        bot.sync()
    bot.shutdown()


def eingang():
    """Einmal abholen und alles abarbeiten (zum Testen und fuer lokale Nutzung)."""
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN fehlt.")
    bot = Bot()
    bot.startup()
    updates = api("getUpdates", {"offset": bot.st.get("offset", 0), "timeout": 0,
                                 "allowed_updates": ["message", "callback_query"]})
    bot.handle_batch(updates)
    bot.tick()
    while bot.proc or bot.render_wanted:
        bot.pipeline()
        time.sleep(1)
    save_state(bot.st)
    print(f"{len(updates)} Nachrichten verarbeitet.")


if __name__ == "__main__":
    {"eingang": eingang, "lauschen": lauschen}[sys.argv[1] if len(sys.argv) > 1 else "eingang"]()
