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

PROJECT_ROOT = Path(__file__).resolve().parents[3]

INPUT_FILE = (
    PROJECT_ROOT
    / "output"
    / "devicespecifications"
    / "url_inventory"
    / "phones.json"
)

BRONZE_DIR = (
    PROJECT_ROOT
    / "output"
    / "devicespecifications"
    / "bronze"
    / "phones"
)

FAILED_FILE = (
    PROJECT_ROOT
    / "output"
    / "devicespecifications"
    / "bronze"
    / "phones_failed.json"
)

# Permanent Bronze boundary for DeviceSpecifications.
BRONZE_SELECTOR = "div#main"

REQUEST_TIMEOUT = 25

# Conservative delay between newly fetched pages.
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
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
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

ALLOWED_HOSTS = {
    "devicespecifications.com",
    "www.devicespecifications.com",
}


# ============================================================
# PHONE INVENTORY SCOPE VALIDATION
# ============================================================

def validate_phone_inventory(devices):
    """Fail closed if inventory entries are not DeviceSpecifications phone URLs."""
    invalid = []

    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            invalid.append((index, "Inventory entry is not an object"))
            continue

        url = device.get("url")
        category = str(device.get("category", "")).strip().lower()

        if not isinstance(url, str) or not url.strip():
            invalid.append((index, "Missing URL"))
            continue

        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()

        if host not in ALLOWED_HOSTS:
            invalid.append((url, "Not a DeviceSpecifications URL"))
            continue

        # Detail pages use /en/model/<model_id>.
        path_parts = [part for part in parsed.path.split("/") if part]
        if (
            len(path_parts) != 3
            or path_parts[0] != "en"
            or path_parts[1] != "model"
            or not re.fullmatch(r"[A-Za-z0-9]+", path_parts[2])
        ):
            invalid.append((url, "Not a DeviceSpecifications model detail URL"))
            continue

        if category and category not in {"phone", "phones", "mobile phone"}:
            invalid.append((url, f"Out-of-scope category: {category}"))

    if invalid:
        print("\nERROR: Invalid entries in the DeviceSpecifications phone inventory.")
        print("No URLs will be fetched in this run.\n")

        for item, reason in invalid:
            print(f"  - {reason}: {item}")

        raise SystemExit(
            "\nCorrect phones.json or create a phone-only inventory, then rerun."
        )

    print("Phone inventory scope validation passed.")


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
    """Delay between successful page fetches."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def backoff_delay(attempt):
    """Increasing delay for throttling/retry conditions."""
    wait = random.uniform(35, 60) * attempt
    print(
        f"   Sleeping {wait:.0f}s before retry "
        f"(attempt {attempt}/{MAX_ATTEMPTS})..."
    )
    time.sleep(wait)



def is_block_page(html):
    """Detect likely access-denial pages, not incidental text."""
    if not html:
        return True

    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    main = soup.select_one("div#main")
    heading = main.select_one("header h1") if main else None

    # A valid model detail page has a main container and model heading.
    if main and heading and "specifications" in heading.get_text(" ", strip=True).lower():
        return False

    # Only flag clear block-page indicators when the expected page is absent.
    block_titles = (
        "access denied",
        "too many requests",
        "attention required",
        "security check",
        "just a moment",
    )

    if any(marker in title for marker in block_titles):
        return True

    text = soup.get_text(" ", strip=True).lower()
    strong_markers = (
        "verify you are human",
        "checking your browser before accessing",
        "your request has been blocked",
        "unusual traffic from your computer network",
    )

    return any(marker in text for marker in strong_markers)



# ============================================================
# URL / FILE HELPERS
# ============================================================

def url_to_filename(url):
    """
    Convert a DeviceSpecifications model URL into a stable filename.

    Example:
        https://www.devicespecifications.com/en/model/80a36841
    becomes:
        80a36841.json
    """
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.rstrip("/").split("/") if part]
    model_id = path_parts[-1] if path_parts else "unknown_model"
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", model_id)
    return f"{filename}.json"


def bronze_path(url):
    """Return the Bronze record path for a source URL."""
    return BRONZE_DIR / url_to_filename(url)


# ============================================================
# INPUT INVENTORY
# ============================================================

def load_phone_devices():
    """
    Load the DeviceSpecifications inventory.

    Expected format: an object containing a 'devices' list.
    Each device object must contain a URL.
    """
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input inventory not found: {INPUT_FILE}")

    with open(INPUT_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"{INPUT_FILE} must contain a JSON object with a 'devices' list."
        )

    devices = data.get("devices")
    if not isinstance(devices, list):
        raise ValueError(
            f"{INPUT_FILE} must contain a list at the top-level key 'devices'."
        )

    # Preserve order and remove duplicate URLs without changing the inventory.
    unique_devices = []
    seen_urls = set()

    for device in devices:
        if not isinstance(device, dict):
            unique_devices.append(device)
            continue

        url = device.get("url")
        if isinstance(url, str):
            url = url.strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_devices.append(device)

    return unique_devices


# ============================================================
# FAILED URL TRACKING
# ============================================================

def load_failed_urls():
    """Load prior failures; failures are not considered Bronze records."""
    if not FAILED_FILE.exists():
        return {}

    try:
        with open(FAILED_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_failed_urls(failed):
    """Persist failed URL information."""
    with open(FAILED_FILE, "w", encoding="utf-8") as file:
        json.dump(failed, file, indent=2, ensure_ascii=False)


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
            print(f"   HTTP request (attempt {attempt}/{MAX_ATTEMPTS})")

            response = requests.get(
                url,
                headers=get_headers(),
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in RETRY_STATUS_CODES:
                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()

                print(f"   HTTP {response.status_code} for {url}")
                backoff_delay(attempt)
                continue

            if is_block_page(response.text):
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Block page detected after {MAX_ATTEMPTS} attempts."
                    )

                print("   Block/throttle page detected despite HTTP 200.")
                backoff_delay(attempt)
                continue

            response.raise_for_status()
            return response

        except requests.RequestException as error:
            last_error = error

            if attempt == MAX_ATTEMPTS:
                raise

            wait = random.uniform(15, 30) * attempt
            print(f"   Request error: {error}")
            print(f"   Retrying in {wait:.0f}s...")
            time.sleep(wait)

    if last_error:
        raise last_error

    raise RuntimeError(f"Failed to fetch URL: {url}")


# ============================================================
# BRONZE EXTRACTION
# ============================================================

def extract_bronze_container(html):
    """
    Extract the agreed DeviceSpecifications Bronze boundary.

    No specification parsing, normalization, or interpretation occurs here.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(BRONZE_SELECTOR)

    if container is None:
        return None

    # Validate that the selected content resembles a model detail page.
    if container.select_one("header h1") is None:
        return None

    return str(container)


# ============================================================
# BRONZE RECORD CREATION
# ============================================================

def create_bronze_record(url, response, container_html):
    """Create one source-derived Bronze record."""
    return {
        "source": {
            "site": "DeviceSpecifications",
            "url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
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
    """Persist one Bronze record using a temporary file and atomic replacement."""
    output_path = bronze_path(url)
    temporary_path = output_path.with_suffix(".json.tmp")

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(record, file, indent=2, ensure_ascii=False)

    temporary_path.replace(output_path)


# ============================================================
# SINGLE URL PROCESSING
# ============================================================

def process_device(device, failed):
    """
    Fetch, validate, extract, and persist one device.

    Returns: 'success', 'skipped', or 'failed'.
    """
    url = device["url"].strip()
    output_path = bronze_path(url)

    if output_path.exists():
        print("   Bronze record already exists. Skipping.")
        return "skipped"

    try:
        response = fetch_with_backoff(url)
    except Exception as error:
        print(f"   Fetch failed: {error}")
        failed[url] = {
            "error": str(error),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        save_failed_urls(failed)
        return "failed"

    container_html = extract_bronze_container(response.text)

    if container_html is None:
        error = (
            "Expected model detail content not found: "
            f"{BRONZE_SELECTOR} with header h1"
        )
        print(f"   {error}")

        failed[url] = {
            "error": error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "http_status": response.status_code,
        }
        save_failed_urls(failed)
        return "failed"

    record = create_bronze_record(url, response, container_html)
    save_bronze_record(url, record)

    if url in failed:
        del failed[url]
        save_failed_urls(failed)

    print(f"   Bronze saved: {output_path}")
    return "success"


# ============================================================
# MAIN
# ============================================================

def main():
    print("=====================================================")
    print("DEVICESPECIFICATIONS BRONZE ACQUISITION")
    print("=====================================================")

    devices = load_phone_devices()
    validate_phone_inventory(devices)

    print(f"Phone records loaded : {len(devices)}")
    print(f"Bronze directory     : {BRONZE_DIR}")
    print()

    failed = load_failed_urls()

    stats = {
        "total": len(devices),
        "success": 0,
        "skipped": 0,
        "failed": 0,
    }

    for index, device in enumerate(devices, start=1):
        url = device["url"].strip()

        print(f"\n[{index}/{len(devices)}] {url}")
        result = process_device(device, failed)
        stats[result] += 1

        # Do not delay after skipping an existing Bronze record.
        if result == "success" and index < len(devices):
            delay()

    print("\n=====================================================")
    print("BRONZE ACQUISITION COMPLETED")
    print("=====================================================")
    print(f"Total URLs : {stats['total']}")
    print(f"New Bronze : {stats['success']}")
    print(f"Skipped    : {stats['skipped']}")
    print(f"Failed     : {stats['failed']}")
    print(f"\nBronze records: {BRONZE_DIR}")
    print(f"Failed URLs  : {FAILED_FILE}")
    print("=====================================================")


if __name__ == "__main__":
    main()
