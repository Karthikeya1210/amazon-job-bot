# Amazon Jobs Telegram Bot — Setup Guide

## 1. Prerequisites

Install Python 3.8+ then:

```bash
pip install playwright requests
playwright install chromium
```

---

## 2. Configure the Bot

Open `amazon_jobs_bot.py` and fill in:

```python
TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."   # From @BotFather
TELEGRAM_CHAT_ID   = "987654321"           # From getUpdates API
```

### How to get your Chat ID:
1. Start a chat with your bot on Telegram
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Send any message to your bot, refresh the URL
4. Copy the `id` value inside `"chat"` → that's your Chat ID

---

## 3. Test Run

```bash
python amazon_jobs_bot.py
```

On first run it will notify you of ALL found jobs and save them to `seen_jobs.json`.
Subsequent runs only notify you of NEW jobs not seen before.

---

## 4. Schedule It (runs every 30 minutes)

### Linux/Mac — Cron:
```bash
crontab -e
```
Add this line:
```
*/30 * * * * /usr/bin/python3 /path/to/amazon_jobs_bot.py >> /path/to/bot.log 2>&1
```

### Windows — Task Scheduler:
1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, repeat every 30 minutes
3. Action: Start program → `python.exe`
4. Arguments: `C:\path\to\amazon_jobs_bot.py`

### Run 24/7 on a Free Server (Recommended):
Use **Railway**, **Render**, or **PythonAnywhere** (all free tiers available).

For Railway:
```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway new
railway up
```

Add a `Procfile`:
```
worker: python amazon_jobs_bot.py
```

And a scheduler in your `railway.toml` or use their cron feature.

---

## 5. Notes & Troubleshooting

- **No jobs found?** Amazon uses heavy JavaScript rendering. If 0 jobs are returned,
  inspect the page with `headless=False` (change in script) to see what's loading.
  You may need to update the CSS selectors in `scrape_jobs()`.

- **Blocked by Amazon?** Add longer delays or rotate user agents. Amazon may block
  automated scrapers — consider using their official Jobs API if available.

- **Reset seen jobs:** Delete `seen_jobs.json` to get notified of all current listings again.

- **Multiple chat IDs:** To notify a group or multiple people, change `TELEGRAM_CHAT_ID`
  to a list and loop over it in `send_telegram_message()`.