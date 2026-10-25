# dealbar

Geprüfte Amazon-Deals, täglich frisch. Deals kommen per Telegram vom Handy, Website, Videos und Kanal-Posts entstehen automatisch.

Website: https://keroxzz.github.io/dealbar/

## So kommt ein Deal rein (alles vom Handy)

1. In der Amazon-App beim Produkt auf **Teilen → Telegram → dein Bot** tippen (oder den Link einfügen).
2. Den Preis dazuschreiben, z. B. `19,99 statt 34,99`. Optional: Kategorie, kurzer Vorteil und 1–4 **eigene** Fotos.
3. Der Bot zeigt einen Entwurf mit Knöpfen (Titel, Preis, Vorteil, Kategorie). **✅ Speichern**, fertig.

Alles in einer Nachricht geht auch: `Link, 19,99 statt 34,99, Mode`. Mit Fotos speichert der Bot den Entwurf 2 Minuten nach dem letzten Foto von selbst.

Danach passiert automatisch:

- neue Zeile in `deals.xlsx` mit Deal-Nummer (#12) und ASIN, Link mit deiner Partner-ID
- nach ca. 1 Minute auf der Website, mit eigener Deal-Seite (`…/dealbar/12/`) zum Teilen
- nach 2–3 Minuten kommt das Video (9:16) mit fertigem Text zum Posten in den Chat; ohne eigene Fotos im Text-Design
- falls verbunden: Post im Telegram-Kanal

## Bot-Befehle

| Befehl | Wofür |
|---|---|
| `/liste` | Deals verwalten: Preis bestätigen (Rabatt wieder 24 h sichtbar), neuer Preis, Fotos ändern, Video neu, offline nehmen, im Kanal posten |
| `/status` | Einrichtung prüfen (Partner-ID, Impressum, Kanal) |
| `/partnerid deinname-21` | Amazon-Partner-ID eintragen, gilt auch für alle bisherigen Links |
| `/impressum` | Impressum eintragen (Name, Straße, PLZ Ort, E-Mail, je eine Zeile) |
| `/kanal @kanalname` | Telegram-Kanal verbinden (Bot muss dort Administrator sein) |
| `/abbrechen` | Entwurf verwerfen |
| `/hilfe` | Kurzanleitung |

## Amazon-Regeln, die eingebaut sind

- Keine Bilder oder Screenshots von Amazon: nur eigene Fotos (Dateien „Screenshot…“ werden ignoriert), sonst Text-Design.
- Werbekennzeichnung auf jeder Seite, in jedem Video-Text und Kanal-Post; Partnerlinks mit * und `rel="sponsored"`.
- Rabatt nur bis 24 Stunden nach „Preis geprüft am“ und immer mit Datum und Uhrzeit, danach „Aktuellen Preis bei Amazon ansehen“. Euro-Preise auf der Website erst mit Creators API (`website.json`: `preise_in_euro_zeigen`).
- Fremde Partner-IDs (z. B. aus weitergeleiteten Deals) werden aus Links entfernt.

## Technik

| Datei | Aufgabe |
|---|---|
| `.github/workflows/bot.yml` | Bot läuft fast durchgehend (je Lauf ca. 5,7 h, startet den nächsten selbst; stündlicher Zeitplan als Reserve). Holt alle 3 Minuten Änderungen und startet bei neuer Programmversion neu. |
| `.github/workflows/website.yml` | baut die Website bei jeder Änderung und täglich früh, GitHub Pages |
| `.github/workflows/tests.yml` | automatische Tests bei jeder Änderung (`python programm/tests.py --video`) |
| `programm/telegram_bot.py` | Telegram-Bot (Entwürfe, Knöpfe, Befehle, Git, Kanal) |
| `programm/run.py`, `deal_video.py` | Videos und Texte zum Posten |
| `programm/website.py`, `web/` | Website (mobil zuerst, ohne Cookies) |
| `programm/daten.py` | gemeinsame Bausteine: Tabelle, Amazon-Links, Einstellungen, Tabellen-Konflikte |

Ändert jemand `deals.xlsx` bei GitHub, während der Bot läuft, führt der Bot beide Stände zeilenweise zusammen.

## Einstellungen

GitHub → Settings → Secrets and variables → Actions:

| Name | Art | Inhalt |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Secret | Schlüssel von @BotFather |
| `TELEGRAM_CHAT_ID` | Variable | deine Telegram-ID (der Bot nennt sie dir) |
| `PARTNER_TAG` | Variable, optional | einfacher per `/partnerid` im Bot |

GitHub → Settings → Pages → Source: **GitHub Actions**.

`website.json`: Titel, Beschreibung, eigene Domain, wie lange Rabatte sichtbar sind, ab wann Deals unter „Ältere Deals“ stehen, Social-Media-Links (`social`), Impressum. Partner-ID, Impressum und Kanal setzt der Bot per Befehl.

## Am PC (optional)

`START.bat` (Videos + Website), `WEBSITE.bat`, `BEISPIEL_WEBSITE.bat`, `LOGO_EXPORTIEREN.bat`. Vorher die neueste Version von GitHub holen, denn der Bot ändert `deals.xlsx` in der Cloud.

Der Bot nutzt GitHub Actions als Dauerläufer. Sollte GitHub das einschränken, läuft er unverändert auf jedem kleinen Server: `DEALBAR_GIT=1 python programm/telegram_bot.py lauschen`.
