"""
DeviceSpecifications URL inventory fetcher.

Outputs:
  output/devicespecifications/url_inventory/phones.json
  output/devicespecifications/url_inventory/tablets.json

Install: py -m pip install requests beautifulsoup4
Run:     py src/sources/devicespecifications/url_fetcher.py
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output" / "devicespecifications" / "url_inventory"
OUTPUT_PHONES = OUTPUT_DIR / "phones.json"
OUTPUT_TABLETS = OUTPUT_DIR / "tablets.json"

YEAR_SELECTORS = {
    2026: "#main > div:nth-child(3)",
    2025: "#main > div:nth-child(5)",
    2024: "#main > div:nth-child(7)",
}
BRAND_PAGES = [
    {"brand": "Samsung", "url": "https://www.devicespecifications.com/en/brand/d1cd1"},
    {"brand": "Apple", "url": "https://www.devicespecifications.com/en/brand/cefa26"},
    {"brand": "Vivo", "url": "https://www.devicespecifications.com/en/brand/b9e76e"},
    {"brand": "OnePlus", "url": "https://www.devicespecifications.com/en/brand/e7f78a"},
    {"brand": "Oppo", "url": "https://www.devicespecifications.com/en/brand/46076a"},
    {"brand": "Realme", "url": "https://www.devicespecifications.com/en/brand/83401ab"},
    {"brand": "Poco", "url": "https://www.devicespecifications.com/en/brand/bd241cc"},
    {"brand": "Xiaomi", "url": "https://www.devicespecifications.com/en/brand/1e7667"},
    {"brand": "Infinix", "url": "https://www.devicespecifications.com/en/brand/0a9b116"},
    {"brand": "Motorola", "url": "https://www.devicespecifications.com/en/brand/7a815"},
    {"brand": "Nothing", "url": "https://www.devicespecifications.com/en/brand/c6f91d4"},
    {"brand": "Google", "url": "https://www.devicespecifications.com/en/brand/e7aa12e"},
    {"brand": "Lava", "url": "https://www.devicespecifications.com/en/brand/c9bc55"},
]
DELAY_SECONDS = 2
TIMEOUT = (10, 30)
TABLET_NAME_RE = re.compile(
    r"\bipad\b|\bgalaxy\s+tab\b|\bredmi\s+pad\b|\bxiaomi\s+pad\b|"
    r"\bpoco\s+pad\b|\boneplus\s+pad\b|\boppo\s+pad\b|\bvivo\s+pad\b|"
    r"\binfinix\s+xpad\b|\bmatepad\b|\btablet\b|\btab(?:\s|[-\d])|"
    r"\bpad(?:\s|[-\d])",
    re.IGNORECASE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOG = logging.getLogger("devicespecifications_url_fetcher")


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.devicespecifications.com/",
    })
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_html(session: requests.Session, url: str) -> str | None:
    try:
        response = session.get(url, timeout=TIMEOUT)
        if response.status_code != 200:
            LOG.error("HTTP %s for %s", response.status_code, url)
            return None
        return response.text
    except requests.RequestException as exc:
        LOG.error("Request failed for %s: %s", url, exc)
        return None


def canonical_model_url(href: str, page_url: str) -> str | None:
    if not href:
        return None
    parsed = urlparse(urljoin(page_url, href.strip()))
    if parsed.netloc.lower() not in {"www.devicespecifications.com", "devicespecifications.com"}:
        return None
    if not re.fullmatch(r"/en/model/[A-Za-z0-9]+", parsed.path.rstrip("/")):
        return None
    return "https://www.devicespecifications.com" + parsed.path.rstrip("/")


def classify(name: str, image_alt: str = "") -> str:
    # Heuristic based on model naming; review new/ambiguous product naming.
    return "tablet" if TABLET_NAME_RE.search(f"{name} {image_alt}") else "phone"


def extract_cards(container: Tag, brand: str, year: int, page_url: str) -> list[dict]:
    records = []
    for card in container.select('div[id^="model_"]'):
        link = card.select_one("h3 a[href]") or card.select_one('a[href*="/en/model/"]')
        if not link:
            continue
        name = link.get_text(" ", strip=True)
        url = canonical_model_url(str(link.get("href", "")), page_url)
        if not name or not url:
            continue
        image = card.select_one("img[alt]")
        image_alt = str(image.get("alt", "")).strip() if image else ""
        match = re.match(r"^model_(.+)$", str(card.get("id", "")))
        model_id = match.group(1) if match else url.rsplit("/", 1)[-1]
        records.append({
            "brand": brand,
            "name": name,
            "url": url,
            "model_id": model_id,
            "category": classify(name, image_alt),
            "release_year": year,
            "source_page": page_url,
        })
    return records


def deduplicate(records: list[dict]) -> list[dict]:
    unique = {}
    for record in records:
        if record["url"] not in unique:
            unique[record["url"]] = record
        elif unique[record["url"]]["release_year"] != record["release_year"]:
            LOG.warning("URL found in multiple year sections: %s", record["url"])
    return list(unique.values())


def save_json(path: Path, records: list[dict]) -> None:
    payload = {
        "source": "DeviceSpecifications",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_years": [2024, 2025, 2026],
        "count": len(records),
        "devices": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    LOG.info("Saved %d records: %s", len(records), path)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    phones, tablets, failed = [], [], []
    session = build_session()
    try:
        for i, item in enumerate(BRAND_PAGES, start=1):
            brand, page_url = item["brand"], item["url"]
            LOG.info("(%d/%d) Fetching %s: %s", i, len(BRAND_PAGES), brand, page_url)
            html = fetch_html(session, page_url)
            if html is None:
                failed.append({"brand": brand, "url": page_url})
                time.sleep(DELAY_SECONDS)
                continue

            soup = BeautifulSoup(html, "html.parser")
            brand_records = []
            for year, selector in YEAR_SELECTORS.items():
                container = soup.select_one(selector)
                if container is None:
                    LOG.warning("%s: missing %s container (%s)", brand, year, selector)
                    continue
                year_records = extract_cards(container, brand, year, page_url)
                if not year_records:
                    LOG.warning("%s: %s container found but no model cards extracted", brand, year)
                else:
                    LOG.info("%s %s: found %d model cards", brand, year, len(year_records))
                brand_records.extend(year_records)

            for record in brand_records:
                (tablets if record["category"] == "tablet" else phones).append(record)
            time.sleep(DELAY_SECONDS)
    finally:
        session.close()

    for records in (phones, tablets):
        records[:] = deduplicate(records)
        records.sort(key=lambda r: (r["brand"].casefold(), r["release_year"], r["name"].casefold()))

    save_json(OUTPUT_PHONES, phones)
    save_json(OUTPUT_TABLETS, tablets)

    if failed:
        failed_path = OUTPUT_DIR / "failed_brand_pages.json"
        failed_path.write_text(json.dumps({
            "source": "DeviceSpecifications",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(failed),
            "failed_pages": failed,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        LOG.warning("%d page(s) failed; see %s", len(failed), failed_path)

    LOG.info("Complete: %d phones, %d tablets, %d failed brand pages", len(phones), len(tablets), len(failed))


if __name__ == "__main__":
    main()
