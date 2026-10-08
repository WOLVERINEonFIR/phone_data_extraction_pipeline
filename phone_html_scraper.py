import requests
import random
import time
from bs4 import BeautifulSoup

INPUT_FILE="output/phones_urls.json"
OUTPUT_FILE="output/phones_html.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

def delay():
    time.sleep(random.uniform(20, 35))

RETRY_STATUS_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
BLOCK_TEXT_MARKERS = [
    "too many requests",
    "you have been blocked",
    "unusual traffic",
    "access denied",
    "captcha",
    "temporarily unavailable",
]

def _is_block_page(text):
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in BLOCK_TEXT_MARKERS)

def fetch_with_backoff(url, max_attempts=6):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(url, headers=headers(), timeout=25)
            soup=BeautifulSoup(r.text, 'html.parser')
            html_content = soup.select_one('div.main.main-review.right.l-box.col') # Get the raw HTML string

            # 2. Write the scraped content into a file
            with open("scraped_page.html", "w", encoding="utf-8") as file:
                file.write(html_content)

            print("HTML file saved successfully!")

            # Block pages can still return 200; treat as retryable throttle event.
            if r.status_code in RETRY_STATUS_CODES or _is_block_page(r.text):
                wait = random.uniform(35, 60) * attempt
                print(f"Throttle/block detected for {url} (attempt {attempt}/{max_attempts}). Sleeping {wait:.0f}s...")
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r

        except requests.HTTPError as e:
            last_error = e
            code = e.response.status_code if e.response is not None else None
            if code not in RETRY_STATUS_CODES or attempt == max_attempts:
                raise
            wait = random.uniform(25, 45) * attempt
            print(f"HTTP {code} for {url} (attempt {attempt}/{max_attempts}). Retrying in {wait:.0f}s...")
            time.sleep(wait)

        except requests.RequestException as e:
            last_error = e
            if attempt == max_attempts:
                raise
            wait = random.uniform(15, 30) * attempt
            print(f"Request error for {url} (attempt {attempt}/{max_attempts}): {e}. Retrying in {wait:.0f}s...")
            time.sleep(wait)

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch URL: {url}")