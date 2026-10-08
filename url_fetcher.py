import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pathlib import Path
import random
import time
import json

# =====================================================
# CONFIGURATION
# =====================================================

BASE_URL = "https://www.gsmarena.com/"

OUTPUT_PHONES = "output/phones_urls.json"
OUTPUT_TABLETS = "output/tablets_urls.json"
OUTPUT_WATCHES = "output/watches_urls.json"

# These are intentionally FIRST-PAGE URLs only.
# We do not follow pagination because the existing inventory
# already contains older products.
TARGET_URLS = [
    "https://www.gsmarena.com/samsung-phones-9.php",
    "https://www.gsmarena.com/apple-phones-48.php",
    "https://www.gsmarena.com/vivo-phones-98.php",
    "https://www.gsmarena.com/oneplus-phones-95.php",
    "https://www.gsmarena.com/oppo-phones-82.php",
    "https://www.gsmarena.com/realme-phones-118.php",
    "https://www.gsmarena.com/xiaomi-phones-80.php",
    "https://www.gsmarena.com/infinix-phones-119.php",
    "https://www.gsmarena.com/motorola-phones-4.php",
    "https://www.gsmarena.com/nothing-phones-128.php",
    "https://www.gsmarena.com/google-phones-107.php",
    "https://www.gsmarena.com/lava-phones-94.php",
    "https://www.gsmarena.com/lenovo-phones-73.php",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36",

    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0",
]

REQUEST_TIMEOUT = 15

# Delay between brand-page requests.
# This is deliberately conservative because GSMArena
# may return HTTP 429 when requests are too frequent.
MIN_DELAY = 8
MAX_DELAY = 12

Path("output").mkdir(exist_ok=True)


# =====================================================
# HTTP
# =====================================================

def get_headers():
    """Return a request header set with a randomly selected User-Agent."""
    return {
        "User-Agent": random.choice(USER_AGENTS)
    }


def delay():
    """Pause between requests."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# =====================================================
# INVENTORY
# =====================================================

def load_existing_urls(filepath):
    """
    Load an existing URL inventory.

    Returns:
        set[str]
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(data)

        print(f"⚠️ {filepath} does not contain a JSON list.")
        return set()

    except FileNotFoundError:
        return set()

    except json.JSONDecodeError:
        print(f"⚠️ Could not decode {filepath}. Starting with empty inventory.")
        return set()


def save_urls(filepath, urls):
    """
    Save a URL set to disk.

    Existing URLs are never removed by this function.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, indent=2, ensure_ascii=False)


# =====================================================
# CATEGORY CLASSIFICATION
# =====================================================

WATCH_KEYWORDS = [
    "watch",
    "band",
    "gear",
    "fit",
    "wear",
]

TABLET_KEYWORDS = [
    "tablet",
    "tab",
    "pad",
]


def get_device_category(name):
    """
    Classify a discovered GSMArena product using its displayed name.

    Returns:
        "phone"
        "tablet"
        "watch"
    """
    name_lower = name.lower()

    # Check watch-related names first.
    if any(keyword in name_lower for keyword in WATCH_KEYWORDS):
        return "watch"

    # Then tablet-related names.
    if any(keyword in name_lower for keyword in TABLET_KEYWORDS):
        return "tablet"

    # Everything else is treated as a phone.
    return "phone"


# =====================================================
# DISCOVERY
# =====================================================

def discover_products(page_url):
    """
    Fetch one current GSMArena brand page and extract
    product URLs from the .makers list.

    No pagination is followed.

    Returns:
        list of dictionaries:
        [
            {
                "name": "...",
                "url": "...",
                "category": "phone"
            },
            ...
        ]
    """

    print(f"\n🔍 Fetching: {page_url}")

    try:
        response = requests.get(
            page_url,
            headers=get_headers(),
            timeout=REQUEST_TIMEOUT,
        )

    except requests.RequestException as e:
        print(f"❌ Request failed: {e}")
        return []

    # Handle rate limiting explicitly.
    if response.status_code == 429:
        print("⛔ HTTP 429: GSMArena rate-limited this request.")
        print("   Existing inventories will not be modified.")
        return []

    if response.status_code != 200:
        print(f"❌ HTTP {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    devices = soup.select(".makers ul li a")

    if not devices:
        print("⚠️ No products found using '.makers ul li a'.")
        return []

    products = []

    for device in devices:
        name = device.get_text(strip=True)
        href = device.get("href")

        if not href:
            continue

        full_url = urljoin(BASE_URL, href)

        category = get_device_category(name)

        products.append({
            "name": name,
            "url": full_url,
            "category": category,
        })

    return products


# =====================================================
# MAIN
# =====================================================

def main():

    # -------------------------------------------------
    # Load historical inventory
    # -------------------------------------------------

    phone_urls = load_existing_urls(OUTPUT_PHONES)
    tablet_urls = load_existing_urls(OUTPUT_TABLETS)
    watch_urls = load_existing_urls(OUTPUT_WATCHES)

    print("=====================================================")
    print("HISTORICAL INVENTORY")
    print("=====================================================")
    print(f"Phones  : {len(phone_urls)}")
    print(f"Tablets : {len(tablet_urls)}")
    print(f"Watches : {len(watch_urls)}")
    print("=====================================================")

    total_new = {
        "phone": 0,
        "tablet": 0,
        "watch": 0,
    }

    # -------------------------------------------------
    # Crawl each current first-page URL
    # -------------------------------------------------

    for page_url in TARGET_URLS:

        products = discover_products(page_url)

        if not products:
            # Do not modify inventory if this page failed.
            continue

        extracted = len(products)

        new_phone = 0
        new_tablet = 0
        new_watch = 0

        # -------------------------------------------------
        # Merge discovered URLs into historical inventory
        # -------------------------------------------------

        for product in products:

            url = product["url"]
            category = product["category"]

            if category == "phone":

                if url not in phone_urls:
                    phone_urls.add(url)
                    new_phone += 1
                    total_new["phone"] += 1

            elif category == "tablet":

                if url not in tablet_urls:
                    tablet_urls.add(url)
                    new_tablet += 1
                    total_new["tablet"] += 1

            elif category == "watch":

                if url not in watch_urls:
                    watch_urls.add(url)
                    new_watch += 1
                    total_new["watch"] += 1

        # -------------------------------------------------
        # Checkpoint save
        # -------------------------------------------------

        save_urls(OUTPUT_PHONES, phone_urls)
        save_urls(OUTPUT_TABLETS, tablet_urls)
        save_urls(OUTPUT_WATCHES, watch_urls)

        print(f"✅ Extracted products : {extracted}")
        print(f"   New phones         : {new_phone}")
        print(f"   New tablets        : {new_tablet}")
        print(f"   New watches        : {new_watch}")

        # Wait before the next brand request.
        delay()

    # -------------------------------------------------
    # Final summary
    # -------------------------------------------------

    print("\n=====================================================")
    print("FETCHER COMPLETED")
    print("=====================================================")

    print(f"New phones discovered  : {total_new['phone']}")
    print(f"New tablets discovered : {total_new['tablet']}")
    print(f"New watches discovered : {total_new['watch']}")

    print("\nFinal inventory:")
    print(f"Phones  : {len(phone_urls)}")
    print(f"Tablets : {len(tablet_urls)}")
    print(f"Watches : {len(watch_urls)}")

    print("=====================================================")


if __name__ == "__main__":
    main()