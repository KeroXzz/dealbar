"""
dealbar: automatische Tests. Laufen bei GitHub nach jeder Aenderung (Workflow "Tests").

    python programm/tests.py           # alle Tests, in einer Kopie des Projekts (deine Daten bleiben unberuehrt)
    python programm/tests.py --video   # zusaetzlich ein Video rendern (dauert ca. 30 Sekunden)

Getestet wird u. a.: Nachrichten auswerten, Amazon-Links, der ganze Ablauf im Telegram-Bot mit simulierten
Nachrichten und Knoepfen, Tabellen-Konflikte bei Git, Website bauen.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def copy_project():
    tmp = tempfile.mkdtemp(prefix="dealbar-test-")
    shutil.copytree(HERE, os.path.join(tmp, "programm"),
                    ignore=shutil.ignore_patterns("__pycache__", "telegram_status.json*"))
    for f in ("deals.xlsx", "website.json", "marke.json"):
        shutil.copy(os.path.join(ROOT, f), tmp)
    if os.path.isdir(os.path.join(ROOT, "deals")):
        shutil.copytree(os.path.join(ROOT, "deals"), os.path.join(tmp, "deals"))
    return tmp


def main():
    tmp = copy_project()
    env = {k: v for k, v in os.environ.items() if k not in (
        "GITHUB_ACTIONS", "DEALBAR_GIT", "PARTNER_TAG", "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "GITHUB_REF_NAME")}
    env.update(TZ="Europe/Berlin", TELEGRAM_CHAT_ID="4242", GITHUB_REPOSITORY="KeroXzz/dealbar",
               PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([sys.executable, os.path.join(tmp, "programm", "tests.py"), "--hier", *sys.argv[1:]],
                           env=env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(r.returncode)


# =========================================================================== Tests (laufen in der Kopie)

def photo_bytes(color=(40, 120, 200)):
    from PIL import Image
    im = Image.new("RGB", (900, 700), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def test_parse():
    import telegram_bot as tb
    d = tb.parse("Amazon.de: Helly Hansen Womens Verglas Jacket : Fashion\nhttps://amzn.eu/d/aB3dE5f")
    assert d["link"] == "https://amzn.eu/d/aB3dE5f", d
    assert d.get("share_title") == "Helly Hansen Womens Verglas Jacket", d
    assert d.get("cat") == "Mode", d
    d = tb.parse("https://www.amazon.de/dp/B09Q83R59N 49,79 statt 119,99, Mode, Wasserdicht und leicht")
    assert (d["new"], d["old"], d["cat"]) == (49.79, 119.99, "Mode"), d
    assert d["rest"] == ["Wasserdicht und leicht"], d
    d = tb.parse("Titel: Meine Jacke\nPreis: 19,99 statt 39,99\nKategorie: Küche\nVorteil: Super leicht\n"
                 "amazon.de/dp/B000000001")
    assert d["link"] == "https://amazon.de/dp/B000000001", d
    assert (d["title"], d["new"], d["old"], d["cat"], d["hook"]) == (
        "Meine Jacke", 19.99, 39.99, "Küche", "Super leicht"), d
    d = tb.parse("Schau dir dieses Produkt bei Amazon an: Bosch Akkuschrauber 18V, 2 Akkus https://amzn.eu/d/x1")
    assert d.get("share_title") == "Bosch Akkuschrauber 18V", d
    d = tb.parse("🔥 Bosch Akkuschrauber 18V für 49,99 statt 89,99 (-44%)\nhttps://www.amazon.de/dp/B0TEST0005")
    assert (d.get("share_title"), d.get("new"), d.get("old")) == ("Bosch Akkuschrauber 18V", 49.99, 89.99), d
    d = tb.parse("Amazon.de: Herren T-Shirt 100% Baumwolle : Fashion https://amzn.eu/d/x2")
    assert d.get("share_title") == "Herren T-Shirt 100% Baumwolle" and "new" not in d, d
    for text, want in (("19,99 statt 34,99", (19.99, 34.99)), ("UVP 34,99 → 19,99", (19.99, 34.99)),
                       ("-43% 19.99€", (19.99, None)), ("1.299,00 statt 1.599,00", (1299.0, 1599.0)),
                       ("jetzt nur 9,99 €", (9.99, None)), ("Mode", None), ("3 Stück", None)):
        assert tb.parse_prices(text) == want, (text, tb.parse_prices(text))


def test_links():
    import daten
    url = "https://www.amazon.de/-/en/Helly-Hansen-Womens-Verglas-Jacket/dp/B09Q83R59N/ref=sr_1_6?sr=8-6"
    assert daten.clean_link(url, "test-21") == ("https://www.amazon.de/dp/B09Q83R59N?tag=test-21", "B09Q83R59N", None)
    assert daten.clean_link("https://www.amazon.de/dp/B09Q83R59N?tag=fremd-21", "")[0] == \
        "https://www.amazon.de/dp/B09Q83R59N"  # fremde Partner-ID fliegt raus
    assert daten.clean_link("https://www.amazon.com/dp/B09Q83R59N", "x-21")[2]  # Hinweis: nicht amazon.de
    assert daten.with_tag("https://www.amazon.de/dp/B09Q83R59N", "x-21") == "https://www.amazon.de/dp/B09Q83R59N?tag=x-21"
    assert daten.plain_link("https://www.amazon.de/dp/B09Q83R59N?tag=x-21") == "https://www.amazon.de/dp/B09Q83R59N"
    assert daten.find_link("Link: amazon.de/dp/B09Q83R59N, danke")[0] == "https://amazon.de/dp/B09Q83R59N"
    assert daten.find_link("https://example.com/dp/B09Q83R59N") is None
    assert not daten.valid_link("https://amzn.to/DEIN-LINK") and not daten.valid_link("link")


def test_dates():
    import daten
    from datetime import datetime
    assert daten.parse_dt("25.10.2026, 08:59") == datetime(2026, 10, 25, 8, 59)
    assert daten.parse_dt("Preis vom 25.10.2026, 08:59 Uhr") == datetime(2026, 10, 25, 8, 59)
    assert daten.parse_dt("2026-10-25T08:59") == datetime(2026, 10, 25, 8, 59)
    assert daten.parse_dt("25.10.2026") == datetime(2026, 10, 25)
    assert daten.iso(datetime(2026, 10, 25, 10, 0)).endswith("+01:00")
    assert daten.iso(datetime(2026, 7, 1, 10, 0)).endswith("+02:00")
    assert daten.num("1.299,00") == 1299.0 and daten.num("19,99 €") == 19.99 and daten.num("0.999") == 0.999


def test_table():
    import daten
    daten.migrate_table()
    with daten.open_table() as t:
        nrs = [t.nr(r) for r in t.rows() if daten.valid_link(t.text(r, "Affiliate-Link"))]
        assert all(nrs) and len(nrs) == len(set(nrs)), nrs
        for r in t.rows():
            if daten.asin_of(t.text(r, "Affiliate-Link")):
                assert t.text(r, "ASIN"), r


class FakeTelegram:
    """Ersetzt die Telegram-API: merkt sich alle Aufrufe und antwortet wie Telegram."""

    def __init__(self):
        self.calls, self.mid, self.uid = [], 500, 0

    def api(self, method, params=None, files=None, timeout=60):
        self.calls.append((method, params or {}, files))
        if method in ("sendMessage", "sendVideo", "sendPhoto"):
            self.mid += 1
            return {"message_id": self.mid}
        if method == "getFile":
            return {"file_path": "photos/x.jpg"}
        if method == "getChat":
            return {"id": -100123, "title": "dealbar Deals"}
        if method == "getMe":
            return {"id": 999}
        if method == "getChatMember":
            return {"status": "administrator", "can_post_messages": True}
        return True

    def texts(self, n=1):
        """Texte der letzten n gesendeten oder bearbeiteten Nachrichten."""
        out = [p.get("text") or p.get("caption") or "" for m, p, _ in self.calls
               if m in ("sendMessage", "editMessageText", "sendVideo", "sendPhoto")]
        return "\n---\n".join(out[-n:])

    def msg(self, text=None, photo=None, chat=4242, entities=None):
        self.uid += 1
        m = {"message_id": 1000 + self.uid, "chat": {"id": chat}, "date": int(time.time())}
        if entities:
            m["caption_entities" if photo else "entities"] = entities
        if photo:
            m["photo"] = [{"file_id": photo + "-klein"}, {"file_id": photo}]
            if text:
                m["caption"] = text
        elif text is not None:
            m["text"] = text
        return {"update_id": self.uid, "message": m}

    def button(self, data, message_id, chat=4242):
        self.uid += 1
        return {"update_id": self.uid, "callback_query": {
            "id": f"q{self.uid}", "data": data, "message": {"message_id": message_id, "chat": {"id": chat}}}}


def make_bot():
    import daten
    import telegram_bot as tb
    fake = FakeTelegram()
    tb.api = fake.api
    tb.download = lambda fid: photo_bytes((hash(fid) % 200, 90, 160))
    daten.resolve_short = lambda url, hops=4: (
        "https://www.amazon.de/Helly-Hansen-Damen-Jacke/dp/B09Q83R59N/ref=xy?tag=fremd-21" if "amzn" in url else url)
    bot = tb.Bot()
    bot.pipeline = lambda: None  # keine Videos im Hintergrund waehrend der Tests
    bot.startup()
    return bot, fake


def row_of(nr):
    import daten
    with daten.open_table() as t:
        r = t.find(nr=nr)
        return t.row(r) if r else None


def test_bot():
    import daten
    import telegram_bot as tb
    bot, fake = make_bot()
    assert any(m == "setMyCommands" for m, _, _ in fake.calls)
    with daten.open_table() as t:
        n0 = t.next_nr()

    # 1. Aus der Amazon-App geteilt, ohne Preis -> Entwurf mit Titel, Kategorie, sauberem Link
    bot.handle_batch([fake.msg("Amazon.de: Helly Hansen Damen Regenjacke : Fashion https://amzn.eu/d/aB3dE5f")])
    d = bot.st["draft"]
    assert (d["title"], d["cat"], d["url"], d["asin"]) == (
        "Helly Hansen Damen Regenjacke", "Mode", "https://www.amazon.de/dp/B09Q83R59N", "B09Q83R59N"), d
    assert "Preis fehlt" in fake.texts() and "Partner-ID fehlt" in fake.texts()

    # 2. Preis, Foto und Vorteil nachreichen
    bot.handle_batch([fake.msg("49,79 statt 119,99")])
    bot.handle_batch([fake.msg(photo="foto-1")])
    assert (d["new"], d["old"], len(d["photos"])) == (49.79, 119.99, 1), d
    assert "−59 %" in fake.texts()
    bot.handle_batch([fake.button("dr:hook", d["msg"])])
    assert bot.st["await"]["what"] == "hook"
    bot.handle_batch([fake.msg("Wasserdicht und atmungsaktiv")])
    assert d["hook"] == "Wasserdicht und atmungsaktiv" and "await" not in bot.st

    # 3. Alter Knopf eines laengst ersetzten Entwurfs
    bot.handle_batch([fake.button("dr:ok", 1)])
    assert "draft" in bot.st and "schon erledigt" in fake.texts()

    # 4. Speichern -> Zeile in der Tabelle, Foto im Ordner
    bot.handle_batch([fake.button("dr:ok", bot.st["draft"]["msg"])])
    assert "draft" not in bot.st and bot.render_wanted
    assert f"Gespeichert als Deal #{n0}" in fake.texts() and f"keroxzz.github.io/dealbar/{n0}/" in json.dumps(fake.calls[-3:])
    row = row_of(n0)
    assert row["Status"] == "neu" and row["Label"] == "TOP-DEAL" and row["Kategorie"] == "Mode", row
    assert row["Affiliate-Link"] == "https://www.amazon.de/dp/B09Q83R59N" and row["ASIN"] == "B09Q83R59N", row
    assert row["Hook"] == "Wasserdicht und atmungsaktiv" and row["Preis zeigen"] == "ja", row
    assert daten.images_in(row["Ordner"]) == ["1.jpg"], row

    # 5. /liste und Deal-Ansicht
    bot.handle_batch([fake.msg("/liste")])
    assert f"#{n0} Helly Hansen" in json.dumps(fake.calls[-1][1], ensure_ascii=False)
    bot.handle_batch([fake.button(f"dv:{n0}", 77)])
    assert f"#{n0} · Helly Hansen" in fake.texts()

    # 6. Offline, wieder online, Preis bestaetigen, neuer Preis
    bot.render_wanted = False
    bot.handle_batch([fake.button(f"dx:{n0}:aus", 77)])
    assert row_of(n0)["Status"] == "abgelaufen" and "Offline genommen" in fake.texts()
    bot.handle_batch([fake.button(f"dx:{n0}:an", 77)])
    assert row_of(n0)["Status"] == "neu" and bot.render_wanted
    bot.handle_batch([fake.button(f"dx:{n0}:frisch", 77)])
    assert "Preis bestätigt" in fake.texts()
    bot.handle_batch([fake.button(f"dx:{n0}:preis", 77)])
    bot.handle_batch([fake.msg("das weiss ich nicht")])
    assert "nicht verstanden" in fake.texts() and bot.st["await"]["what"] == "price"
    bot.handle_batch([fake.msg("39,99 statt 119,99")])
    assert daten.num(row_of(n0)["Neuer Preis (€)"]) == 39.99 and "await" not in bot.st

    # 7. Fotos ersetzen und entfernen
    bot.handle_batch([fake.button(f"dx:{n0}:fotos", 77)])
    bot.handle_batch([fake.msg(photo="foto-2"), fake.msg(photo="foto-3")])
    assert len(bot.st["await"]["photos"]) == 2
    bot.handle_batch([fake.button("aw:ok", bot.st["await"]["msg"])])
    assert daten.images_in(row_of(n0)["Ordner"]) == ["1.jpg", "2.jpg"]
    bot.handle_batch([fake.button(f"dx:{n0}:fotos", 77)])
    bot.handle_batch([fake.button("aw:ohne", bot.st["await"]["msg"])])
    assert daten.images_in(row_of(n0)["Ordner"]) == [] and "alle Fotos entfernt" in fake.texts()

    # 8. Einstellungen per Befehl
    bot.handle_batch([fake.msg("/partnerid falsch")])
    assert "nicht nach einer Partner-ID" in fake.texts()
    bot.handle_batch([fake.msg("/partnerid dealbar-21")])
    assert daten.load_settings()["partner_tag"] == "dealbar-21"
    bot.handle_batch([fake.msg("/impressum\nErika Muster\nMusterweg 1\n12345 Musterstadt\nerika@example.org")])
    s = daten.load_settings()
    assert daten.impressum_ok(s) and s["impressum"]["plz_ort"] == "12345 Musterstadt", s["impressum"]
    bot.handle_batch([fake.msg("/kanal @dealbar_test")])
    assert daten.load_settings()["telegram_kanal"] == "@dealbar_test" and n0 in bot.st["posted"]
    bot.handle_batch([fake.msg("/status")])
    assert "Partner-ID: <code>dealbar-21</code>" in fake.texts() and "Impressum ausgefüllt" in fake.texts()

    # 9. Alles in einer Nachricht, mit Foto -> nach 2 Minuten Ruhe automatisch gespeichert
    bot.handle_batch([fake.msg("https://www.amazon.de/dp/B0TEST0001, 19,99 statt 34,99, Küche", photo="foto-4")])
    d = bot.st["draft"]
    assert d["url"] == "https://www.amazon.de/dp/B0TEST0001?tag=dealbar-21" and d["cat"] == "Küche", d
    bot.tick(now=time.time() + tb.PHOTO_WAIT + 5)
    assert "draft" not in bot.st and "automatisch" in fake.texts(2)
    assert row_of(n0 + 1)["Label"] == "DEAL"  # 43 % Rabatt

    # 10. Erst Foto, dann Link -> Foto gehoert zum Deal; Verwerfen; Text ohne Link; fremder Chat
    bot.handle_batch([fake.msg(photo="foto-5")])
    assert "Zu welchem Deal" in fake.texts()
    bot.handle_batch([fake.msg("https://www.amazon.de/dp/B0TEST0002")])
    assert len(bot.st["draft"]["photos"]) == 1 and bot.st["draft"]["title"] == "Amazon-Produkt B0TEST0002"
    bot.handle_batch([fake.button("dr:weg", bot.st["draft"]["msg"])])
    assert "draft" not in bot.st and "verworfen" in fake.texts()
    bot.handle_batch([fake.msg("Hallo Bot")])
    assert "keinen Amazon-Link" in fake.texts()
    n = len(fake.calls)
    bot.handle_batch([fake.msg("https://www.amazon.de/dp/B0TEST0003", chat=666)])
    assert "draft" not in bot.st and len(fake.calls) == n

    # 10b. Weitergeleiteter Deal-Post: Link versteckt hinter "Zum Deal", fremde Partner-ID; dann /abbrechen
    bot.handle_batch([fake.msg("Bosch Akkuschrauber 18V für 49,99 statt 89,99 Zum Deal", entities=[
        {"type": "bold", "offset": 0, "length": 5},
        {"type": "text_link", "offset": 46, "length": 8, "url": "https://www.amazon.de/dp/B0TEST0006?tag=fremd-21"}])])
    d = bot.st["draft"]
    assert d["url"] == "https://www.amazon.de/dp/B0TEST0006?tag=dealbar-21" and (d["new"], d["old"]) == (49.99, 89.99), d
    bot.handle_batch([fake.msg("/abbrechen")])
    assert "draft" not in bot.st and "verworfen" in fake.texts(2)

    # 11. Kanal-Post: mit Werbehinweis und Partnerlink
    assert bot.post_channel(n0 + 1)
    method, params, _ = fake.calls[-1]
    assert method == "sendPhoto" and params["chat_id"] == "@dealbar_test", (method, params)
    assert daten.DISCLOSURE in params["caption"] and "tag=dealbar-21" in json.dumps(params)

    # 12. Zustand ueberlebt einen Neustart (inkl. altem Format der ersten Bot-Version)
    tb.save_state({"offset": 7, "pending": {"link": "https://www.amazon.de/dp/B0TEST0004", "title": "Alt"},
                   "sent": [1, 2]})
    st = tb.load_state()
    assert st["draft"]["title"] == "Alt" and st["draft"]["photos"] == [] and "sent" not in st, st


def test_website():
    import daten
    import website
    website.build()
    out = website.OUT
    with daten.open_table() as t:
        nrs = [t.nr(r) for r in t.rows() if daten.valid_link(t.text(r, "Affiliate-Link"))]
    for nr in nrs:
        assert os.path.exists(os.path.join(out, str(nr), "index.html")), nr
    with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert "tag=dealbar-21" in page and 'rel="sponsored' in page and daten.DISCLOSURE in page
    assert "B0TEST0001" in page
    with open(os.path.join(out, "impressum.html"), encoding="utf-8") as fh:
        assert "Erika Muster" in fh.read()
    with open(os.path.join(out, "sitemap.xml"), encoding="utf-8") as fh:
        assert "https://keroxzz.github.io/dealbar/" in fh.read()
    with open(os.path.join(out, "404.html"), encoding="utf-8") as fh:
        assert '<base href="/dealbar/">' in fh.read()
    for f in ("logo.png", "favicon.png", "og.png", "manifest.webmanifest", "fonts/inter-900.woff2"):
        assert os.path.exists(os.path.join(out, f)), f
    # Beispiel-Website
    r = subprocess.run([sys.executable, os.path.join(HERE, "beispiel.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(daten.ROOT, "website_beispiel", "index.html"))


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout


def test_git_conflict():
    """Bot und GitHub aendern deals.xlsx gleichzeitig -> zeilenweise zusammengefuehrt, nichts geht verloren."""
    import daten
    root = daten.ROOT
    remote = os.path.join(root, "..", os.path.basename(root) + "-remote.git")
    other = os.path.join(root, "..", os.path.basename(root) + "-other")
    ident = ["-c", "user.name=test", "-c", "user.email=test@example.org"]
    try:
        git("init", "-q", "--bare", remote, cwd=root)
        git("init", "-q", cwd=root)
        git("checkout", "-q", "-b", "main", cwd=root)
        git("add", "-A", cwd=root)
        git(*ident, "commit", "-q", "-m", "Start", cwd=root)
        git("remote", "add", "origin", remote, cwd=root)
        git("push", "-q", "-u", "origin", "main", cwd=root)
        git("clone", "-q", "-b", "main", remote, other, cwd=root)
        with daten.open_table() as t:
            first = t.nr(t.rows()[-1])

        # Auf GitHub (anderer Klon): Hook eines Deals geaendert und eine Zeile von Hand ergaenzt
        from openpyxl import load_workbook
        path = os.path.join(other, "deals.xlsx")
        tab = daten.Table(load_workbook(path))
        tab.set(tab.find(nr=first), "Hook", "Von GitHub geaendert")
        r = tab.next_row()
        nr_github = tab.next_nr()
        for k, v in {"Status": "fertig", "Produktname": "Von Hand ergaenzt", "Nr": nr_github,
                     "Affiliate-Link": "https://www.amazon.de/dp/B0GITHUB01"}.items():
            tab.set(r, k, v)
        tab.wb.save(path)
        git("add", "-A", cwd=other)
        git(*ident, "commit", "-q", "-m", "Von Hand", cwd=other)
        git("push", "-q", cwd=other)

        # Gleichzeitig im Bot: neuer Deal (bekommt dieselbe Nummer) und Preis eines Deals geaendert
        os.environ["DEALBAR_GIT"] = "1"
        git("config", "user.name", "dealbar-bot", cwd=root)
        git("config", "user.email", "bot@example.org", cwd=root)
        bot, fake = make_bot()
        assert bot.repo.enabled
        bot.handle_batch([fake.msg("https://www.amazon.de/dp/B0TEST0009 9,99 statt 19,99, Technik")])
        bot.handle_batch([fake.button("dr:ok", bot.st["draft"]["msg"])])
        with daten.open_table(write=True) as t:
            t.set(t.find(nr=first), "Neuer Preis (€)", 1.23)
        bot.commit("Preis")
        assert bot.repo.push(), "Push nach Konflikt fehlgeschlagen"

        with daten.open_table() as t:
            by_title = {t.text(r, "Produktname"): r for r in t.rows()}
            assert "Von Hand ergaenzt" in by_title and "Amazon-Produkt B0TEST0009" in by_title, list(by_title)
            nrs = [t.nr(r) for r in t.rows() if t.nr(r)]
            assert len(nrs) == len(set(nrs)), nrs
            r = t.find(nr=first)
            assert t.text(r, "Hook") == "Von GitHub geaendert" and daten.num(t.get(r, "Neuer Preis (€)")) == 1.23
        assert git("status", "--porcelain", cwd=root).strip() == "" and "rebase" not in os.listdir(
            os.path.join(root, ".git"))
        git("pull", "-q", cwd=other)
        assert git("rev-parse", "HEAD", cwd=other) == git("rev-parse", "HEAD", cwd=root)
    finally:
        os.environ.pop("DEALBAR_GIT", None)
        shutil.rmtree(remote, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)


def test_video():
    import brand as brandlib
    import daten
    import run
    brand = brandlib.load_brand(os.path.join(daten.ROOT, "marke.json"))
    os.makedirs(daten.VIDEO_DIR, exist_ok=True)
    for title in ("Amazon-Produkt B0TEST0001", "Helly Hansen Damen Regenjacke"):  # mit Foto / Text-Design
        with daten.open_table() as t:
            row = t.row(t.find(title=title))
        res = run.render_row(row, brand)
        assert res["status"] == "fertig", res
        assert os.path.getsize(os.path.join(daten.VIDEO_DIR, res["video"])) > 100_000
        assert daten.DISCLOSURE in res["caption"] and "#Werbung" in res["caption"]


def run_tests():
    sys.path.insert(0, HERE)
    tests = [test_parse, test_links, test_dates, test_table, test_bot, test_website, test_git_conflict]
    if "--video" in sys.argv:
        tests.append(test_video)
    failed = 0
    for fn in tests:
        start = time.time()
        try:
            fn()
            print(f"OK      {fn.__name__} ({time.time() - start:.1f} s)")
        except Exception:
            failed += 1
            print(f"FEHLER  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} von {len(tests)} Tests bestanden.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--hier" in sys.argv:
        run_tests()
    else:
        main()
