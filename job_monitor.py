"""
Amazon Jobs Telegram Bot — Railway Final
 
Railway Setup:
  1. Add environment variables in Railway dashboard:
       TELEGRAM_BOT_TOKEN   → your bot token from @BotFather
       TELEGRAM_CHAT_ID     → your chat/channel ID
       EXPIRY_DAYS          → (optional) days before a job can re-notify, default 2
  2. Add a Volume mounted at /data (so seen_jobs.json persists across redeploys)
  3. Set Build Command:
       pip install -r requirements.txt && playwright install chromium --with-deps
  4. Set Start Command:
       python amazon_jobs_bot.py
 
requirements.txt:
  playwright
  requests
"""
 
import json
import os
import re
import sys
import time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
 
# ── CONFIG ────────────────────────────────────────────────────────────────────
 
def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"[FATAL] Missing required environment variable: {name}", flush=True)
        sys.exit(1)
    return value
 
TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _require_env("TELEGRAM_CHAT_ID")
 
# Re-notify if the same job is still listed after this many days
EXPIRY_DAYS = int(os.environ.get("EXPIRY_DAYS", "2"))
 
# /data is a Railway mounted volume — falls back to current dir for local testing
_DATA_DIR      = "/data" if os.path.isdir("/data") else "."
SEEN_JOBS_FILE = os.path.join(_DATA_DIR, "seen_jobs.json")
 
URLS = [
    {
        "url": "https://www.jobsatamazon.co.uk/app#/jobSearch?query=Sortation%20Operative&locale=en-GB",
        "label": "Sortation Operative"
    },
    {
        "url": "https://www.jobsatamazon.co.uk/app#/jobSearch?query=Warehouse%20Operative&locale=en-GB",
        "label": "Warehouse Operative"
    },
]
 
# ── SEEN JOBS (timestamp-based) ───────────────────────────────────────────────
 
def load_seen_jobs() -> dict:
    """Returns {job_id: unix_timestamp} dict."""
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                data = json.load(f)
            # Migrate old list format (no timestamps) to dict
            if isinstance(data, list):
                print("  ⚠️  Migrating old seen_jobs format to timestamp dict", flush=True)
                return {job_id: 0 for job_id in data}
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Warning] Could not read seen_jobs file: {e}. Starting fresh.", flush=True)
    return {}
 
 
def save_seen_jobs(seen: dict):
    try:
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(seen, f, indent=2)
    except OSError as e:
        print(f"[Warning] Could not save seen_jobs file: {e}", flush=True)
        print("  Tip: Mount a Railway Volume at /data to persist seen jobs.", flush=True)
 
 
def purge_expired(seen: dict) -> dict:
    """Remove jobs older than EXPIRY_DAYS so they can trigger a re-notification."""
    cutoff  = time.time() - (EXPIRY_DAYS * 86400)
    purged  = {job_id: ts for job_id, ts in seen.items() if ts > cutoff}
    removed = len(seen) - len(purged)
    if removed:
        print(f"  🗑 Purged {removed} expired job(s) from seen list", flush=True)
    return purged
 
# ── JOB ID ────────────────────────────────────────────────────────────────────
 
def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for stable ID generation."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text
 
 
def make_job_id(title: str, location: str) -> str:
    """Stable ID from title + location only. Pay intentionally excluded — changes too often."""
    return f"{normalize(title)}|{normalize(location)}"
 
# ── TELEGRAM ──────────────────────────────────────────────────────────────────
 
def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f"  [Telegram Error] {resp.status_code}: {resp.text}", flush=True)
        else:
            print("  ✅ Telegram message sent", flush=True)
    except requests.RequestException as e:
        print(f"  [Telegram Error] Request failed: {e}", flush=True)
 
# ── SCRAPING ──────────────────────────────────────────────────────────────────
 
def dismiss_popups(page):
    """Dismiss cookie banner and profile modal if they appear."""
    try:
        page.locator("text=Continue without accepting").wait_for(timeout=8000)
        page.locator("text=Continue without accepting").click()
        page.wait_for_timeout(1500)
        print("  🍪 Cookie banner dismissed", flush=True)
    except PlaywrightTimeoutError:
        try:
            page.locator("text=Accept all").click(timeout=3000)
            page.wait_for_timeout(1500)
            print("  🍪 Cookie banner accepted", flush=True)
        except PlaywrightTimeoutError:
            pass
 
    for sel in ["button[aria-label='Close']", "[role='dialog'] button", "[class*='modal'] button"]:
        try:
            page.locator(sel).first.click(timeout=4000)
            page.wait_for_timeout(1000)
            print("  👤 Profile modal dismissed", flush=True)
            break
        except Exception:
            pass
 
 
def parse_card(card, label: str, page_url: str) -> dict | None:
    try:
        detail_divs = card.query_selector_all(".jobDetailText")
        if not detail_divs:
            return None
 
        title_el = detail_divs[0].query_selector("strong")
        title    = title_el.inner_text().strip() if title_el else None
        if not title:
            return None
 
        pay_el   = card.query_selector("[data-test-id='jobCardPayRateText']")
        pay_text = pay_el.inner_text().strip() if pay_el else ""
        pay      = pay_text.replace("Pay rate:", "").strip()
 
        duration_el   = card.query_selector("[data-test-id='jobCardDurationText']")
        duration_text = duration_el.inner_text().strip() if duration_el else ""
        duration      = duration_text.replace("Duration:", "").strip()
 
        job_type = ""
        if len(detail_divs) >= 2:
            type_text = detail_divs[1].inner_text().strip()
            job_type  = type_text.replace("Type:", "").strip()
 
        all_strongs = card.query_selector_all("strong")
        location    = all_strongs[-1].inner_text().strip() if all_strongs else "Unknown"
 
        return {
            "id":       make_job_id(title, location),
            "title":    title,
            "type":     job_type,
            "duration": duration,
            "pay":      pay,
            "location": location,
            "url":      page_url,
            "category": label,
        }
 
    except Exception as e:
        print(f"  [Warning] Error parsing card: {e}", flush=True)
        return None
 
 
def scrape_jobs(url: str, label: str) -> list:
    jobs = []
 
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",           # required in Railway/Docker containers
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
        )
        page = context.new_page()
 
        print(f"\n[Scraping] {label}", flush=True)
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except PlaywrightTimeoutError:
            print("  [Warning] Page load timed out — attempting to continue anyway", flush=True)
 
        page.wait_for_timeout(3000)
        dismiss_popups(page)
 
        try:
            page.wait_for_selector("[class*='jobCard']", timeout=20000)
        except PlaywrightTimeoutError:
            print("  ❌ No job cards appeared within 20s", flush=True)
            browser.close()
            return []
 
        page.wait_for_timeout(1000)
        cards = page.query_selector_all("[class*='jobCard']")
        print(f"  📋 {len(cards)} card slot(s) found", flush=True)
 
        for card in cards:
            job = parse_card(card, label, url)
            if job:
                jobs.append(job)
                print(f"  ✅ {job['title']} | {job['type']} | {job['pay']} | {job['location']}", flush=True)
 
        print(f"  → {len(jobs)} valid job(s) extracted", flush=True)
        browser.close()
 
    return jobs
 
# ── FORMATTING ────────────────────────────────────────────────────────────────
 
def format_message(job: dict) -> str:
    lines = [
        "🆕 <b>New Amazon Job!</b>",
        "",
        f"📋 <b>{job['title']}</b>",
        f"🏷 Category: {job['category']}",
    ]
    if job.get("type"):
        lines.append(f"⏰ Type: {job['type']}")
    if job.get("duration"):
        lines.append(f"📆 Duration: {job['duration']}")
    if job.get("pay"):
        lines.append(f"💷 Pay: {job['pay']}")
    lines.append(f"📍 Location: {job['location']}")
    lines.append(f"🔗 <a href='{job['url']}'>View Job →</a>")
    return "\n".join(lines)
 
# ── MAIN ──────────────────────────────────────────────────────────────────────
 
def run():
    print("=" * 50, flush=True)
    print("Amazon Jobs Bot — Starting run", flush=True)
    print(f"Seen jobs file : {SEEN_JOBS_FILE}", flush=True)
    print(f"Expiry         : {EXPIRY_DAYS} day(s)", flush=True)
    print("=" * 50, flush=True)
 
    seen_jobs = load_seen_jobs()
    seen_jobs = purge_expired(seen_jobs)
    new_count = 0
 
    for entry in URLS:
        jobs = scrape_jobs(entry["url"], entry["label"])
        for job in jobs:
            if job["id"] not in seen_jobs:
                print(f"\n  🔔 NEW JOB: {job['title']} @ {job['location']}", flush=True)
                send_telegram_message(format_message(job))
                seen_jobs[job["id"]] = time.time()  # store timestamp, not just presence
                new_count += 1
                time.sleep(1)  # avoid Telegram rate limits
            else:
                print(f"  ⏭ Already seen: {job['title']} @ {job['location']}", flush=True)
 
    save_seen_jobs(seen_jobs)
    print(f"\n{'=' * 50}", flush=True)
    print(f"Run complete. {new_count} new job(s) notified.", flush=True)
    print("=" * 50, flush=True)
 
 
if __name__ == "__main__":
    run()
 
