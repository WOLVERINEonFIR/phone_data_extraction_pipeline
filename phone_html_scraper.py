import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE = Path("output/phones_urls.json")

BRONZE_DIR = Path("output/bronze/phones")
FAILED_FILE = Path("output/bronze/phones_failed.json")

# Permanent Bronze boundary agreed for GSMArena.
BRONZE_SELECTOR = "div.main.main-review.right.l-box.col"

REQUEST_TIMEOUT = 25

# Delay between successful page requests.
MIN_DELAY = 20
MAX_DELAY = 35

# Maximum attempts for one URL.
MAX_ATTEMPTS = 6

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
]

RETRY_STATUS_CODES = {
    403,
    408,
    425,
    429,
    500,
    502,
    503,
    504,
}

BLOCK_TEXT_MARKERS = [
    "too many requests",
    "you have been blocked",
    "unusual traffic",
    "access denied",
    "captcha",
    "temporarily unavailable",
]


# ============================================================
# DIRECTORY SETUP
# ============================================================

BRONZE_DIR.mkdir(parents=True, exist_ok=True)
FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# REQUEST HELPERS
# ============================================================

def get_headers():
    """Create HTTP headers for one request."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def delay():
    """Delay between normal page requests."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def backoff_delay(attempt):
    """
    Increasing delay for throttling/retry conditions.

    The multiplier makes later attempts wait longer.
    """
    wait = random.uniform(35, 60) * attempt
    print(
        f"   Sleeping {wait:.0f}s before retry "
        f"(attempt {attempt}/{MAX_ATTEMPTS})..."
    )
    time.sleep(wait)


# ============================================================
# BLOCK DETECTION
# ============================================================

def is_block_page(text):
    """
    Detect common block/throttle pages that may return HTTP 200.
    """
    if not text:
        return False

    low = text.lower()

    return any(
        marker in low
        for marker in BLOCK_TEXT_MARKERS
    )


# ============================================================
# URL / FILE HELPERS
# ============================================================

def url_to_filename(url):
    """
    Convert a GSMArena URL into a stable filesystem-safe filename.

    Example:
        https://www.gsmarena.com/apple_iphone_15_pro_max-12548.php

    becomes approximately:
        apple_iphone_15_pro_max-12548.json
    """

    parsed = urlparse(url)

    path = parsed.path.rstrip("/")

    filename = Path(path).name

    if filename.endswith(".php"):
        filename = filename[:-4]

    # Remove anything unexpected from the filename.
    filename = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        filename,
    )

    return f"{filename}.json"


def bronze_path(url):
    """Return the Bronze record path for a source URL."""
    return BRONZE_DIR / url_to_filename(url)


# ============================================================
# INPUT INVENTORY
# ============================================================

def load_phone_urls():
    """
    Load the phone URL inventory.

    Returns a list preserving the order in the JSON file.
    """
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input inventory not found: {INPUT_FILE}"
        )

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(
            f"{INPUT_FILE} must contain a JSON list."
        )

    urls = []

    for url in data:
        if isinstance(url, str) and url.strip():
            urls.append(url.strip())

    # Preserve order while removing accidental duplicates.
    return list(dict.fromkeys(urls))


# ============================================================
# FAILED URL TRACKING
# ============================================================

def load_failed_urls():
    """
    Load previous failures.

    This is informational/resumable state.
    A failed URL is NOT considered a Bronze record.
    """
    if not FAILED_FILE.exists():
        return {}

    try:
        with open(FAILED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_failed_urls(failed):
    """Persist failed URL information."""
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(
            failed,
            f,
            indent=2,
            ensure_ascii=False,
        )


# ============================================================
# HTTP FETCH
# ============================================================

def fetch_with_backoff(url):
    """
    Fetch one source URL with retry/backoff handling.

    Returns:
        requests.Response

    Raises:
        requests.RequestException
        RuntimeError
    """

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):

        try:
            print(
                f"   HTTP request "
                f"(attempt {attempt}/{MAX_ATTEMPTS})"
            )

            response = requests.get(
                url,
                headers=get_headers(),
                timeout=REQUEST_TIMEOUT,
            )

            # --------------------------------------------
            # Explicit throttle handling
            # --------------------------------------------

            if response.status_code in RETRY_STATUS_CODES:

                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()

                print(
                    f"   HTTP {response.status_code} "
                    f"for {url}"
                )

                backoff_delay(attempt)
                continue

            # --------------------------------------------
            # Some block pages return HTTP 200
            # --------------------------------------------

            if is_block_page(response.text):

                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Block page detected after "
                        f"{MAX_ATTEMPTS} attempts."
                    )

                print(
                    "   Block/throttle page detected "
                    "despite HTTP 200."
                )

                backoff_delay(attempt)
                continue

            # --------------------------------------------
            # Other HTTP failures
            # --------------------------------------------

            response.raise_for_status()

            return response

        except requests.RequestException as e:

            last_error = e

            if attempt == MAX_ATTEMPTS:
                raise

            wait = random.uniform(15, 30) * attempt

            print(
                f"   Request error: {e}"
            )

            print(
                f"   Retrying in {wait:.0f}s..."
            )

            time.sleep(wait)

    if last_error:
        raise last_error

    raise RuntimeError(
        f"Failed to fetch URL: {url}"
    )


# ============================================================
# BRONZE EXTRACTION
# ============================================================

def extract_bronze_container(html):
    """
    Extract the agreed GSMArena Bronze boundary.

    No normalization or interpretation occurs here.

    Returns:
        HTML string of the main review/specification container.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    container = soup.select_one(BRONZE_SELECTOR)

    if container is None:
        return None

    return str(container)


# ============================================================
# BRONZE RECORD CREATION
# ============================================================

def create_bronze_record(
    url,
    response,
    container_html,
):
    """
    Create one source-derived Bronze record.

    The HTML is intentionally preserved as source HTML.
    """

    scraped_at = datetime.now(
        timezone.utc
    ).isoformat()

    return {
        "source": {
            "site": "GSMArena",
            "url": url,
            "scraped_at": scraped_at,
            "http_status": response.status_code,
        },

        "content": {
            "selector": BRONZE_SELECTOR,
            "html": container_html,
        },
    }


# ============================================================
# BRONZE PERSISTENCE
# ============================================================

def save_bronze_record(url, record):
    """
    Persist one Bronze record.

    One source URL -> one Bronze JSON file.
    """

    output_path = bronze_path(url)

    temporary_path = output_path.with_suffix(
        ".json.tmp"
    )

    # Write temporary file first.
    # This prevents a partial JSON file if the process dies
    # during the write.
    with open(
        temporary_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            record,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # Replace/create final record atomically.
    temporary_path.replace(output_path)


# ============================================================
# SINGLE URL PROCESSING
# ============================================================

def process_url(url, failed):
    """
    Fetch, validate, extract and persist one URL.

    Returns:
        "success"
        "skipped"
        "failed"
    """

    output_path = bronze_path(url)

    # --------------------------------------------------------
    # Resume behavior
    # --------------------------------------------------------

    if output_path.exists():

        print("   Bronze record already exists. Skipping.")

        return "skipped"

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    try:

        response = fetch_with_backoff(url)

    except Exception as e:

        print(f"   ❌ Fetch failed: {e}")

        failed[url] = {
            "error": str(e),
            "failed_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        save_failed_urls(failed)

        return "failed"

    # --------------------------------------------------------
    # Bronze boundary extraction
    # --------------------------------------------------------

    container_html = extract_bronze_container(
        response.text
    )

    if container_html is None:

        error = (
            "Bronze selector not found: "
            f"{BRONZE_SELECTOR}"
        )

        print(f"   ❌ {error}")

        failed[url] = {
            "error": error,
            "failed_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "http_status": response.status_code,
        }

        save_failed_urls(failed)

        return "failed"

    # --------------------------------------------------------
    # Create and persist Bronze record
    # --------------------------------------------------------

    record = create_bronze_record(
        url=url,
        response=response,
        container_html=container_html,
    )

    save_bronze_record(
        url=url,
        record=record,
    )

    # If a URL previously failed but now succeeds,
    # remove its failure record.
    if url in failed:
        del failed[url]
        save_failed_urls(failed)

    print(
        f"   ✅ Bronze saved: "
        f"{output_path}"
    )

    return "success"


# ============================================================
# MAIN
# ============================================================

def main():

    print("=====================================================")
    print("GSMARENA BRONZE ACQUISITION")
    print("=====================================================")

    # --------------------------------------------------------
    # Load source inventory
    # --------------------------------------------------------

    urls = load_phone_urls()

    print(f"Phone URLs loaded : {len(urls)}")
    print(f"Bronze directory  : {BRONZE_DIR}")
    print()

    # --------------------------------------------------------
    # Load previous failures
    # --------------------------------------------------------

    failed = load_failed_urls()

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    stats = {
        "total": len(urls),
        "success": 0,
        "skipped": 0,
        "failed": 0,
    }

    # --------------------------------------------------------
    # Process sequentially
    # --------------------------------------------------------

    for index, url in enumerate(
        urls,
        start=1,
    ):

        print(
            f"\n[{index}/{len(urls)}] {url}"
        )

        result = process_url(
            url,
            failed,
        )

        stats[result] += 1

        # ----------------------------------------------------
        # Delay only after a newly fetched page.
        # Don't delay after a skipped historical Bronze record.
        # ----------------------------------------------------

        if result == "success":
            if index < len(urls):
                delay()

    # --------------------------------------------------------
    # Final summary
    # --------------------------------------------------------

    print("\n=====================================================")
    print("BRONZE ACQUISITION COMPLETED")
    print("=====================================================")

    print(f"Total URLs : {stats['total']}")
    print(f"New Bronze : {stats['success']}")
    print(f"Skipped    : {stats['skipped']}")
    print(f"Failed     : {stats['failed']}")

    print(
        f"\nBronze records: {BRONZE_DIR}"
    )

    print(
        f"Failed URLs: {FAILED_FILE}"
    )

    print("=====================================================")


if __name__ == "__main__":
    main()