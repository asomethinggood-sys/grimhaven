# 🚀 Hosting Guide — Grimhaven (نبرد جاودانگان و تسخیر آسمان)

The bot uses **long-polling by default** → it needs no public URL, no domain,
no SSL certificate. Any always-on machine (free tier included) works.

> ⚠️ **Security first:** the bot token was shared in chat history. Open
> [@BotFather](https://t.me/BotFather) → `/mybots` → @Grimheaven_bot →
> *API Token → Revoke current token*, then paste the NEW token into `.env`.

---

## Option A — Render.com (easiest, free, no server admin)

1. Sign in at [render.com](https://render.com) (you can use your Gmail account).
2. **New → Blueprint** → select the `grimhaven` GitHub repo.
3. Render reads `deploy/render.yaml` and creates two services:
   - `grimhaven-bot` (worker — long polling)
   - `grimhaven-demo` (web — playable demo console)
4. When prompted, set the env var `TELEGRAM_BOT_TOKEN` (from @BotFather) and
   `ADMIN_IDS` (your Telegram numeric id — ask [@userinfobot](https://t.me/userinfobot)).
5. **Deploy**. Open `https://t.me/Grimheaven_bot` and press Start.

## Option B — PythonAnywhere (free tier, Gmail signup)

1. Sign up at [pythonanywhere.com](https://www.pythonanywhere.com) with your Gmail.
2. *Files* → upload the repo (or `git clone` from a Bash console).
3. In a Bash console:
   ```bash
   cd grimhaven
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env && nano .env    # paste your bot token
   ```
4. *Tasks* (paid) or a always-on console: `python run_bot.py`.
   On the free tier use a **Scheduled Task** every day, or the "Always-on task"
   feature on a paid plan.

## Option C — Any VPS with Docker

```bash
git clone https://github.com/asomethinggood-sys/grimhaven.git
cd grimhaven
cp .env.example .env && nano .env       # TELEGRAM_BOT_TOKEN, ADMIN_IDS
docker compose -f deploy/docker-compose.yml up -d --build
```
- Bot: runs headless via long-polling.
- Demo console: http://your-server:8000

## Option D — Bare VPS (systemd)

```bash
sudo mkdir -p /opt/grimhaven && sudo git clone https://github.com/asomethinggood-sys/grimhaven.git /opt/grimhaven
cd /opt/grimhaven
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
sudo cp deploy/grimhaven.service /etc/systemd/system/
sudo systemctl enable --now grimhaven && sudo journalctl -fu grimhaven
```

## Option E — Webhook mode (only if you already have HTTPS hosting)

```bash
WEBHOOK_URL=https://bot.yourdomain.com LISTEN_PORT=8080 python run_bot.py
```

---

## Environment variables

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token of @Grimheaven_bot from @BotFather |
| `ADMIN_IDS` | Comma-separated Telegram ids with access to `/admin` (Hall of Heavenly Will) |
| `DATABASE_PATH` | SQLite file storing the JSON documents (default `data/grimhaven.db`) |
| `WEBHOOK_URL` | Optional — switches the bot from polling to webhook |
| `DEMO_PORT` | Port of the browser demo console (default 8000) |
