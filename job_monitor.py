"""
Amazon Jobs Telegram Bot — Fixed Version
 
Changes from original:
  - Job ID now uses title + location only (pay removed — too volatile)
  - Fields normalized before ID generation (lowercase, no punctuation)
  - seen_jobs stores timestamps instead of plain set
  - Entries expire after EXPIRY_DAYS (default 2) so re-listed jobs notify again
 
Requirements:
    pip install playwright requests
    playwright install chromium
 
Setup:
    1. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables
    2. Run: python amazon_jobs_bot.py
    3. Schedule with cron or GitHub Actions (see bottom of file)
"""
 
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
 
# ── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
 
EXPIRY_DAYS = 2  # re-notify if the same job is still listed after this many days
 
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
 
SEEN_JOBS_FILE = "seen_jobs.json"
# ─────────────────────────────────────────────────────────────────────────────
 
 
def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for stable ID generation."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)  # remove punctuation
    text = re.sub(r'\s+', ' ', text)     # collapse multiple spaces
    return text
 
 
def make_job_id(title: str, location: str) -> str:
    """Build a stable job ID from title and location only.
    Pay is intentionally excluded — it changes too often and causes false duplicates.
    """
    return f"{normalize(title)}|{normalize(location)}"
 
 
def load_seen_jobs() -> dict:
    """Returns {job_id: unix_timestamp} dict."""
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE, "r") as f:
            return json.load(f)
    return {}
 
 
def save_seen_jobs(seen: dict):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(seen, f, indent=2)
 
 
def purge_expired(seen: dict) -> dict:
    """Remove jobs older than EXPIRY_DAYS so they can be re-notified."""
    cutoff = time.time() - (EXPIRY_DAYS * 86400)
    purged = {job_id: ts for job_id, ts in seen.items() if ts > cutoff}
    removed = len(seen) - len(purged)
    if removed:
        print(f"  🗑 Purged {removed} expired job(s) from seen list")
    return purged
 
 
def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        print(f"  [Telegram Error] {resp.status_code}: {resp.text}")
    else:
        print("  ✅ Telegram message sent")
 
 
def dismiss_popups(page):
    """Dismiss cookie banner and profile modal."""
    # 1. Cookie banner
    try:
        page.locator("text=Continue without accepting").wait_for(timeout=8000)
        page.locator("text=Continue without accepting").click()
        page.wait_for_timeout(1500)
        print("  🍪 Cookie banner dismissed")
    except PlaywrightTimeoutError:
        try:
            page.locator("text=Accept all").click(timeout=3000)
            page.wait_for_timeout(1500)
            print("  🍪 Cookie banner accepted")
        except PlaywrightTimeoutError:
            pass
 
    # 2. "Tell us about yourself" modal
    for sel in ["button[aria-label='Close']", "[role='dialog'] button", "[class*='modal'] button"]:
        try:
            page.locator(sel).first.click(timeout=4000)
            page.wait_for_timeout(1000)
            print(f"  👤 Profile modal dismissed")
            break
        except Exception:
            pass
 
 
def parse_card(card, label: str, page_url: str) -> dict | None:
    """
    Extract job details from a card using the confirmed class names:
      - Title:    first <strong> inside .jobDetailText
      - Type:     second .jobDetailText div (contains "Type:")
      - Duration: [data-test-id='jobCardDurationText']
      - Pay:      [data-test-id='jobCardPayRateText']
      - Location: last <strong> in the card
    """
    try:
        detail_divs = card.query_selector_all(".jobDetailText")
 
        if not detail_divs:
            return None  # Empty padding card — skip
 
        title_el = detail_divs[0].query_selector("strong")
        title = title_el.inner_text().strip() if title_el else None
 
        if not title:
            return None  # Skip empty cards
 
        pay_el = card.query_selector("[data-test-id='jobCardPayRateText']")
        pay_text = pay_el.inner_text().strip() if pay_el else ""
        pay = pay_text.replace("Pay rate:", "").strip()
 
        duration_el = card.query_selector("[data-test-id='jobCardDurationText']")
        duration_text = duration_el.inner_text().strip() if duration_el else ""
        duration = duration_text.replace("Duration:", "").strip()
 
        job_type = ""
        if len(detail_divs) >= 2:
            type_text = detail_divs[1].inner_text().strip()
            job_type = type_text.replace("Type:", "").strip()
 
        all_strongs = card.query_selector_all("strong")
        location = all_strongs[-1].inner_text().strip() if all_strongs else "Unknown"
 
        # Stable ID: title + location only, both normalized
        job_id = make_job_id(title, location)
 
        return {
            "id":       job_id,
            "title":    title,
            "type":     job_type,
            "duration": duration,
            "pay":      pay,
            "location": location,
            "url":      page_url,
            "category": label,
        }
 
    except Exception as e:
        print(f"  [Warning] Error parsing card: {e}")
        return None
 
 
def scrape_jobs(url: str, label: str) -> list:
    jobs = []
 
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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
 
        print(f"\n[Scraping] {label}")
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
 
        dismiss_popups(page)
 
        try:
            page.wait_for_selector("[class*='jobCard']", timeout=20000)
        except PlaywrightTimeoutError:
            print("  ❌ No job cards appeared within 20s")
            browser.close()
            return []
 
        page.wait_for_timeout(1000)
        cards = page.query_selector_all("[class*='jobCard']")
        print(f"  📋 {len(cards)} card slots found")
 
        for card in cards:
            job = parse_card(card, label, url)
            if job:
                jobs.append(job)
                print(f"  ✅ {job['title']} | {job['type']} | {job['pay']} | {job['location']}")
 
        print(f"  → {len(jobs)} valid job(s) extracted")
        browser.close()
 
    return jobs
 
 
def format_message(job: dict) -> str:
    lines = [
        f"🆕 <b>New Amazon Job!</b>",
        f"",
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
 
 
def run():
    print("=" * 50)
    print("Amazon Jobs Bot — Starting run")
    print("=" * 50)
 
    seen_jobs = load_seen_jobs()
    seen_jobs = purge_expired(seen_jobs)  # clean up expired entries first
    new_count = 0
 
    for entry in URLS:
        jobs = scrape_jobs(entry["url"], entry["label"])
        for job in jobs:
            if job["id"] not in seen_jobs:
                print(f"\n  🔔 NEW JOB: {job['title']} @ {job['location']}")
                send_telegram_message(format_message(job))
                seen_jobs[job["id"]] = time.time()  # store timestamp
                new_count += 1
                time.sleep(1)  # avoid Telegram rate limits
            else:
                print(f"  ⏭ Already seen: {job['title']} @ {job['location']}")
 
    save_seen_jobs(seen_jobs)
    print(f"\n{'='*50}")
    print(f"Run complete. {new_count} new job(s) notified.")
    print("=" * 50)
 
 
if __name__ == "__main__":
    run()
 
