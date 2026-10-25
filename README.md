# dealbar

Geprüfte Amazon-Deals, täglich frisch. Deals kommen per Telegram vom Handy, Videos und Website entstehen automatisch.

## So läuft es

1. Du schickst deinem privaten Telegram-Bot einen Amazon-Link, den Preis und 1–4 eigene Fotos.
2. Der Workflow „Telegram-Bot“ läuft fast durchgehend (`programm/telegram_bot.py lauschen`) und trägt jeden Deal innerhalb von Sekunden in `deals.xlsx` ein.
3. `programm/run.py` rendert das Video, der Bot schickt dir Video und Beschreibungstext zurück.
4. Danach startet der Workflow „Website“, baut die Seite (`programm/website.py`) und stellt sie bei GitHub Pages online.

Bot-Befehle: `/hilfe`, `/liste`, `/aus <Nr>`, `/abbrechen`, `/id`.

## Einstellungen bei GitHub

Settings → Secrets and variables → Actions:

| Name | Art | Inhalt |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Secret | Schlüssel von @BotFather |
| `TELEGRAM_CHAT_ID` | Variable | deine Telegram-ID (der Bot nennt sie dir) |
| `PARTNER_TAG` | Variable | deine Amazon-Tracking-ID, z. B. `dealbar-21` |

Settings → Pages → Source: **GitHub Actions**.

## Lokal

Die `.bat`-Dateien funktionieren weiterhin unter Windows (START.bat, WEBSITE.bat, BEISPIEL_WEBSITE.bat).
