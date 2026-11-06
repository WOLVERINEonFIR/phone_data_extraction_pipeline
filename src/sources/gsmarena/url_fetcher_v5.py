
import json
import random
import re
import time

from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =====================================================
# CONFIGURATION
# =====================================================

BASE_URL = "https://www.gsmarena.com/"

PROJECT_ROOT = Path(__file__).resolve().parents[3]

OUTPUT_DIR = PROJECT_ROOT / "output" / "gsmarena" / "url_inventory"

OUTPUT_PHONES = OUTPUT_DIR / "phones.json"
OUTPUT_TABLETS = OUTPUT_DIR / "tablets.json"
OUTPUT_WATCHES = OUTPUT_DIR / "watches.json"
OUTPUT_UNKNOWN = OUTPUT_DIR / "unknown.json"

CATEGORY_FILES = {
    "phone": OUTPUT_PHONES,
    "tablet": OUTPUT_TABLETS,
    "watch": OUTPUT_WATCHES,
    "unknown": OUTPUT_UNKNOWN,
}


# First-page URLs only. No pagination.
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
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
]

REQUEST_TIMEOUT = 15

MIN_DELAY = 8
MAX_DELAY = 12

UNKNOWN_CATEGORY = "unknown"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =====================================================
# HTTP
# =====================================================

def get_headers():
    """Return headers with a randomly selected User-Agent."""

    return {
        "User-Agent": random.choice(USER_AGENTS)
    }


def delay():
    """Pause between brand-page requests."""

    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# =====================================================
# CATEGORY CLASSIFICATION
# =====================================================

def get_device_category(description):
    """
    Classify a GSMArena product using its image title.

    Examples:
        'Android smartphone' -> phone
        'Android tablet'     -> tablet
        'Android watch'      -> watch

    Returns:
        phone, tablet, watch, or unknown

    No name-based guesses or hardcoded URL overrides.
    """

    description_lower = description.lower()

    detected_categories = set()

    if re.search(r"\bsmartphone\b", description_lower):
        detected_categories.add("phone")

    if re.search(r"\btablet\b", description_lower):
        detected_categories.add("tablet")

    if re.search(r"\bwatch\b", description_lower):
        detected_categories.add("watch")

    # Accept only one unambiguous category.
    if len(detected_categories) == 1:
        return detected_categories.pop()

    # Missing or conflicting descriptions.
    return UNKNOWN_CATEGORY


# =====================================================
# INVENTORY
# =====================================================

def load_existing_urls(filepath):
    """
    Load an existing URL inventory.

    Returns:
        set[str]

    Raises an error for malformed inventory files
    instead of silently replacing their contents.
    """

    if not filepath.exists():
        return set()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Invalid JSON in {filepath}. "
            "Repair this inventory before running the fetcher."
        ) from e

    if not isinstance(data, list):
        raise RuntimeError(
            f"{filepath} must contain a JSON list."
        )

    if not all(isinstance(url, str) for url in data):
        raise RuntimeError(
            f"{filepath} contains non-string entries."
        )

    return set(data)


def save_urls(filepath, urls):
    """
    Save URLs atomically.

    Existing inventory files are not partially overwritten
    if the save operation fails.
    """

    temp_filepath = filepath.with_suffix(".tmp")

    with open(temp_filepath, "w", encoding="utf-8") as f:
        json.dump(
            sorted(urls),
            f,
            indent=2,
            ensure_ascii=False,
        )

    temp_filepath.replace(filepath)


def save_all_inventories(inventories):
    """Save all four category inventories."""

    for category, filepath in CATEGORY_FILES.items():
        save_urls(filepath, inventories[category])


# =====================================================
# DISCOVERY
# =====================================================

def discover_products(page_url):
    """
    Fetch one GSMArena brand page and extract products
    from the .makers list.

    Device type is determined using img[title].

    No pagination is followed.

    Returns a list of product dictionaries.
    """

    print(f"\nFetching: {page_url}")

    try:
        response = requests.get(
            page_url,
            headers=get_headers(),
            timeout=REQUEST_TIMEOUT,
        )

    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return []

    if response.status_code == 429:
        print("HTTP 429: GSMArena rate-limited this request.")
        print("Existing inventories will not be modified.")
        return []

    if response.status_code != 200:
        print(f"HTTP {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    devices = soup.select(".makers ul li a")

    if not devices:
        print("No products found using '.makers ul li a'.")
        return []

    products = []

    for device in devices:

        name = device.get_text(strip=True)
        href = device.get("href")

        if not href:
            continue

        full_url = urljoin(BASE_URL, href)

        # ---------------------------------------------
        # Extract image title
        # ---------------------------------------------

        image = device.find("img")

        description = ""

        if image:
            description = image.get("title", "").strip()

        # ---------------------------------------------
        # Classify using description only
        # ---------------------------------------------

        category = get_device_category(description)

        products.append({
            "name": name,
            "url": full_url,
            "description": description,
            "category": category,
        })

    return products


# =====================================================
# MAIN
# =====================================================

def main():

    # -------------------------------------------------
    # Load historical inventories
    # -------------------------------------------------

    inventories = {
        category: load_existing_urls(filepath)
        for category, filepath in CATEGORY_FILES.items()
    }

    print("=" * 55)
    print("HISTORICAL INVENTORY")
    print("=" * 55)

    for category, urls in inventories.items():
        print(f"{category.capitalize():10}: {len(urls)}")

    print("=" * 55)

    total_new = {
        category: 0
        for category in CATEGORY_FILES
    }

    total_moved = 0

    # -------------------------------------------------
    # Crawl target brand pages
    # -------------------------------------------------

    for page_url in TARGET_URLS:

        products = discover_products(page_url)

        if not products:
            print("Skipping inventory update for this page.")
            continue

        extracted = len(products)

        new_counts = {
            category: 0
            for category in CATEGORY_FILES
        }

        moved_count = 0

        # ---------------------------------------------
        # Merge products into historical inventories
        # ---------------------------------------------

        for product in products:

            url = product["url"]
            category = product["category"]

            # Remove this URL from any incorrect
            # previous category when rediscovered.
            for existing_category, urls in inventories.items():

                if existing_category != category and url in urls:
                    urls.remove(url)
                    moved_count += 1
                    total_moved += 1

            # Add to the newly determined category.
            if url not in inventories[category]:

                inventories[category].add(url)

                new_counts[category] += 1
                total_new[category] += 1

        # ---------------------------------------------
        # Checkpoint save
        # ---------------------------------------------

        save_all_inventories(inventories)

        print(f"\nExtracted products: {extracted}")

        for category in CATEGORY_FILES:
            print(
                f"New {category:8}: "
                f"{new_counts[category]}"
            )

        print(f"Reclassified URLs: {moved_count}")

        # Wait before the next request.
        delay()

    # -------------------------------------------------
    # Final summary
    # -------------------------------------------------

    print("\n" + "=" * 55)
    print("FETCHER COMPLETED")
    print("=" * 55)

    print("\nNew URLs discovered:")

    for category, count in total_new.items():
        print(f"{category.capitalize():10}: {count}")

    print(f"\nURLs moved between categories: {total_moved}")

    print("\nFinal inventory:")

    for category, urls in inventories.items():
        print(f"{category.capitalize():10}: {len(urls)}")

    print("=" * 55)


if __name__ == "__main__":
    main()
