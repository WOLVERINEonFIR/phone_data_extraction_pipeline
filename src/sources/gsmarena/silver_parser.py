"""

GSMArena device-spec HTML parser for GadgetGuru's existing phone-document shape.

Principles:

- Extract only information explicitly present in the supplied GSMArena HTML.

- Never infer missing specs from chipset, brand, marketing tier, or other fields.

- Missing scalar specs are None; absent repeated specs are empty lists.

- Preserve raw source text where parsing to a stable primitive is not justified.

- Supports both a full GSMArena page and the saved JSON wrapper:

  {"source": {...}, "content": {"html": "..."}}

"""

from __future__ import annotations

import json

import re

from pathlib import Path

from typing import Any, Iterable

from bs4 import BeautifulSoup

def _clean(value: Any) -> str | None:

    if value is None:

        return None

    text = re.sub(r"\s+", " ", str(value)).strip()

    return text or None

def _first_int(text: str | None) -> int | None:

    if not text:

        return None

    match = re.search(r"(?<!\d)(\d[\d,]*)", text)

    return int(match.group(1).replace(",", "")) if match else None

def _first_float(text: str | None) -> float | None:

    if not text:

        return None

    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)", text)

    return float(match.group(1)) if match else None

def _spec(soup: BeautifulSoup, key: str) -> str | None:

    node = soup.select_one(f'[data-spec="{key}"]')

    return _clean(node.get_text(" ", strip=True)) if node else None

def _spec_multiline(soup: BeautifulSoup, key: str) -> str | None:

    """Keep explicit line boundaries for repeated camera-module entries."""

    node = soup.select_one(f'[data-spec="{key}"]')

    if not node:

        return None

    for br in node.find_all("br"):

        br.replace_with("###LINE###")

    raw = node.get_text(" ", strip=True)

    lines = [_clean(line) for line in raw.split("###LINE###")]

    return "\n".join(line for line in lines if line) or None

def _section_rows(soup: BeautifulSoup) -> dict[str, dict[str, str]]:

    """Return {section: {label: value}} from the GSMArena spec tables."""

    result: dict[str, dict[str, str]] = {}

    for table in soup.select("#specs-list table"):

        current_section = None

        for row in table.select("tr"):

            heading = row.find("th", scope="row")

            if heading:

                current_section = _clean(heading.get_text(" ", strip=True))

                result.setdefault(current_section or "", {})

            cells = row.find_all("td", recursive=False)

            if current_section and len(cells) >= 2:

                label = _clean(cells[0].get_text(" ", strip=True))

                value = _clean(cells[-1].get_text(" ", strip=True))

                if label and value:

                    # Repeated blank labels are deliberately not merged into invented keys.

                    result[current_section][label] = value

    return result

def _parse_dimensions(text: str | None) -> dict[str, Any]:
    """Parse dimensions while retaining thickness ranges and discrete alternatives."""
    if not text:
        return {
            "length_mm": None,
            "width_mm": None,
            "thickness_mm": None,
            "thickness_range_mm": None,
            "thickness_options_mm": [],
            "unfolded": None,
            "folded": None,
            "raw": None,
        }

    def parse_triplet(value: str | None) -> dict[str, Any]:
        value = value or ""
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*"
            r"(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*mm",
            value,
            re.I,
        )
        thickness_range = None
        thickness = None
        thickness_options: list[float] = []

        if match:
            if match.group(4) is not None:
                thickness_range = [float(match.group(3)), float(match.group(4))]
            else:
                thickness = float(match.group(3))

        # Discrete alternatives are different from a continuous range.
        alternative_match = re.search(
            r"(\d+(?:\.\d+)?)\s*mm\s+or\s+"
            r"(\d+(?:\.\d+)?)\s*mm",
            value,
            re.I,
        )
        if alternative_match:
            thickness_options = list(dict.fromkeys([
                float(alternative_match.group(1)),
                float(alternative_match.group(2)),
            ]))
            thickness = None
            thickness_range = None

        return {
            "length_mm": float(match.group(1)) if match else None,
            "width_mm": float(match.group(2)) if match else None,
            "thickness_mm": thickness,
            "thickness_range_mm": thickness_range,
            "thickness_options_mm": thickness_options,
        }

    if re.search(r"\b(?:unfolded|folded):", text, re.I):
        unfolded_match = re.search(
            r"(?<![A-Za-z])Unfolded:\s*(.*?)(?=(?<![A-Za-z])Folded:|$)", text, re.I
        )
        folded_match = re.search(
            r"(?<![A-Za-z])Folded:\s*(.*?)(?=(?<![A-Za-z])Unfolded:|$)", text, re.I
        )
        return {
            "length_mm": None,
            "width_mm": None,
            "thickness_mm": None,
            "thickness_range_mm": None,
            "thickness_options_mm": [],
            "unfolded": parse_triplet(unfolded_match.group(1) if unfolded_match else None),
            "folded": parse_triplet(folded_match.group(1) if folded_match else None),
            "raw": text,
        }

    return {**parse_triplet(text), "unfolded": None, "folded": None, "raw": text}


def _parse_device_type(model_name: str | None) -> str:
    """Classify obvious non-phone products; this parser's default corpus is phones."""
    name = (model_name or "").strip().lower()
    if not name:
        return "unknown"
    if re.search(r"\bwatch(?:\d|\b)|\bblack shark gs3(?: ultra)?\b", name):
        return "smartwatch"
    if re.search(r"\btablet\b|\bipad\b|\bpixel c\b|\bgalaxy tab\b|\bredmi pad\b|\boneplus pad\b", name):
        return "tablet"
    return "phone"


def _parse_weight_options(text: str | None) -> list[float]:
    """Return distinct gram values, including slash/or alternatives with one trailing unit."""
    if not text:
        return []
    values: list[float] = []
    # Capture alternatives such as "222.5/227.9/229.8 g" where the unit appears once.
    for match in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?(?:\s*(?:/|\bor\b)\s*\d+(?:\.\d+)?)+)\s*g\b",
        text,
        re.I,
    ):
        values.extend(float(value) for value in re.findall(r"\d+(?:\.\d+)?", match.group(1)))
    # Also capture individually unit-labelled values such as "222 g, 227 g".
    values.extend(
        float(value)
        for value in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*g\b", text, re.I)
    )
    return list(dict.fromkeys(values))

def _parse_variants(text: str | None) -> list[dict[str, int | None]]:

    """Parse only explicitly listed RAM/storage pairs; normalize TB to GB."""

    if not text:

        return []

    pattern = re.compile(

        r"(?P<storage>\d+(?:\.\d+)?)\s*(?P<storage_unit>TB|GB)\s+"

        r"(?P<ram>\d+(?:\.\d+)?)\s*GB\s*RAM",

        re.I,

    )

    variants: list[dict[str, int | None]] = []

    seen: set[tuple[int, int]] = set()

    for match in pattern.finditer(text):

        storage = float(match.group("storage"))

        if match.group("storage_unit").upper() == "TB":

            storage *= 1024

        pair = (int(float(match.group("ram"))), int(storage))

        if pair in seen:

            continue

        seen.add(pair)

        variants.append({

            "ram_gb": pair[0],

            "storage_gb": pair[1],

        })

    return variants

def _extract_base_price_inr(soup: BeautifulSoup) -> int | None:
    """Extract an explicitly listed Indian rupee base price."""
    node = soup.select_one('[data-spec="price"]')
    if not node:
        return None

    text = node.get_text(" ", strip=True)

    # Accept only explicit INR symbols, not USD or other currencies.
    match = re.search(
        r"₹\s*([\d,]+)(?![\d,.])",
        text,
    )
    if not match:
        return None

    return int(match.group(1).replace(",", ""))

def _extract_indian_variants(
    soup: BeautifulSoup,
) -> list[dict[str, int | str | None]]:
    """
    Extract configuration, Indian price, and Amazon India URL
    from GSMArena's pricing widget.

    Only the first offer cell in each pricing row is considered
    the Indian offer, as specified for this source layout.
    """
    from urllib.parse import urlparse

    table = soup.select_one("table.pricing.inline.widget")
    if not table:
        return []

    variants: list[dict[str, int | str | None]] = []
    seen: set[tuple[int | None, int | None, int | None, str | None]] = set()

    for row in table.select("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue

        # Read the configuration label without combining offer details.
        config_text = cells[0].get_text(" ", strip=True)
        if not config_text:
            continue

        # Storage and RAM are parsed independently.
        storage_match = re.search(
            r"(?<!\d)(\d+(?:\.\d+)?)\s*(TB|GB)\b",
            config_text,
            re.I,
        )
        ram_match = re.search(
            r"(?<!\d)(\d+)\s*GB\s*RAM\b",
            config_text,
            re.I,
        )

        storage_gb = None
        if storage_match:
            storage_value = float(storage_match.group(1))
            if storage_match.group(2).upper() == "TB":
                storage_value *= 1024
            storage_gb = int(storage_value)

        ram_gb = int(ram_match.group(1)) if ram_match else None

        # The first offer cell is the Indian offer in this HTML layout.
        offer_cell = cells[1]
        offer_text = offer_cell.get_text(" ", strip=True)

        price_match = re.search(
            r"₹\s*([\d,]+)(?![\d,.])",
            offer_text,
        )
        price_inr = (
            int(price_match.group(1).replace(",", ""))
            if price_match
            else None
        )

        amazon_url = None

        for link in offer_cell.select("a[href]"):
            href = link.get("href")
            if not isinstance(href, str) or not href.strip():
                continue

            parsed = urlparse(href.strip())
            hostname = (parsed.hostname or "").lower()

            if (
                parsed.scheme in {"http", "https"}
                and hostname in {"amazon.in", "www.amazon.in"}
            ):
                # Keep the original URL and its query parameters.
                amazon_url = href.strip()
                break

        # Ignore rows with neither a usable Indian price nor an Amazon URL.
        if price_inr is None and amazon_url is None:
            continue

        # Avoid duplicates without collapsing different prices or URLs.
        signature = (storage_gb, ram_gb, price_inr, amazon_url)
        if signature in seen:
            continue
        seen.add(signature)

        variants.append({
            "storage_gb": storage_gb,
            "ram_gb": ram_gb,
            "price_inr": price_inr,
            "amazon_url": amazon_url,
        })

    return variants

def _extract_youtube_links(
    soup: BeautifulSoup,
) -> list[dict[str, str]]:
    """Extract unique YouTube review videos and normalize their URLs."""
    from urllib.parse import parse_qs, urlparse

    results: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    video_section = soup.select(
        ".module.module-vid-review iframe[src]"
    )

    for iframe in video_section:
        src = iframe.get("src")
        if not isinstance(src, str) or not src.strip():
            continue

        parsed = urlparse(src.strip())
        host = (parsed.hostname or "").lower()
        video_id = None

        # Standard embed URLs.
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path.startswith("/embed/"):
                video_id = parsed.path.split("/embed/", 1)[1].split("/")[0]
            elif parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [None])[0]

        # Shortened YouTube URLs.
        elif host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0] or None

        # Validate the extracted identifier.
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue

        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        results.append({
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return results

def _parse_camera_lenses(text: str | None) -> list[dict[str, Any]]:

    """Extract lens entries and explicitly stated attributes from the camera module text."""

    if not text:

        return []

    lenses: list[dict[str, Any]] = []

    # GSMArena separates lenses with line breaks in the nfo cell. Parsing each line avoids

    # merging specifications from different lenses.

    for raw_line in re.split(r"\\\n|[\n\r]+", text):

        line = _clean(raw_line)

        if not line:

            continue

        mp = re.search(r"(\d+(?:\.\d+)?)\s*MP", line, re.I)

        unknown_mp = re.search(r"\bXX\s*MP\b", line, re.I)

        if not mp and not unknown_mp:

            continue

        aperture = re.search(r"f\s*/\s*(\d+(?:\.\d+)?)", line, re.I)

        sensor = re.search(r"(\d+/\d+(?:\.\d+)?)\s*[\"″]", line)

        focal = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", line, re.I)

        pixel_size = re.search(r"(\d+(?:\.\d+)?)\s*µm", line, re.I)

        angle = re.search(r"(\d{2,3})\s*[˚°]", line)

        lens_type = None

        for candidate in ("under display", "ultrawide", "ultra-wide", "periscope", "telephoto", "macro", "wide"):

            if candidate in line.lower():

                lens_type = "under_display" if candidate == "under display" else (

                    "ultrawide" if candidate == "ultra-wide" else candidate

                )

                break

        lenses.append({

            "mp": (float(mp.group(1)) if "." in mp.group(1) else int(mp.group(1))) if mp else None,

            "type": lens_type,

            "aperture_f": float(aperture.group(1)) if aperture else None,

            "sensor_size": sensor.group(1) if sensor else None,

            "focal_length_mm": float(focal.group(1)) if focal else None,

            "pixel_size_um": float(pixel_size.group(1)) if pixel_size else None,

            "field_of_view_degrees": int(angle.group(1)) if angle else None,

            "raw": line,

        })

    return lenses

def _parse_ip_rating(text: str | None) -> str | None:

    if not text:

        return None

    match = re.search(r"\bIP\d{2}(?:/IP\d{2,3})?\b", text, re.I)

    return match.group(0).upper() if match else None

def _parse_resolution(text: str | None) -> dict[str, int | None]:

    match = re.search(r"(\d{3,4})\s*x\s*(\d{3,4})", text or "", re.I)

    return {

        "width_px": int(match.group(1)) if match else None,

        "height_px": int(match.group(2)) if match else None,

    }

def _parse_release_year(text: str | None) -> int | None:

    match = re.search(r"\b(20\d{2})\b", text or "")

    return int(match.group(1)) if match else None

def _parse_major_updates(text: str | None) -> int | None:

    if not text:

        return None

    match = re.search(r"up to\s+(\d+)\s+major\s+OS updates?", text, re.I)

    return int(match.group(1)) if match else None

def _parse_bands(text: str | None) -> list[str]:

    """Extract unique band numbers, excluding trailing model identifiers and mode labels."""

    if not text:

        return []

    head = re.split(r"\s+-\s+(?=[A-Z]{1,4}\d)", text, maxsplit=1)[0]

    head = re.split(

        r"\b(?:SA/NSA|SA|NSA|Sub6|mmWave|International|India|China|USA|ROW)\b",

        head,

        maxsplit=1,

        flags=re.I,

    )[0]

    values = re.findall(r"(?<![A-Za-z0-9])\d{1,3}(?![A-Za-z0-9])", head)

    return list(dict.fromkeys(values))

def _parse_model_numbers(text: str | None) -> list[str]:
    """Extract explicit device identifiers, including Google and Apple-style IDs."""
    if not text:
        return []
    # Apple identifiers contain an internal comma (for example, iPhone18,4).
    pattern = re.compile(
        r"(?i)\biPhone\d+,\d+\b|"
        r"\b(?=[A-Z0-9/-]*[A-Z])(?=[A-Z0-9/-]*\d)"
        r"[A-Z0-9]+(?:-[A-Z0-9]+)?(?:/[A-Z0-9]+)?\b"
    )
    result = []
    for match in pattern.finditer(text):
        token = match.group(0)
        # The generic alternative must contain both letters and digits.
        if not re.search(r"[A-Za-z]", token) or not re.search(r"\d", token):
            continue
        if token not in result:
            result.append(token)
    return result

def _network_configurations(soup: BeautifulSoup, key: str) -> list[dict[str, Any]]:

    """Extract the primary and optional regional rows without merging configurations."""

    primary = soup.select_one(f'[data-spec="{key}"]')

    if not primary:

        return []

    rows = [primary.parent]

    current = primary.parent

    while True:

        current = current.find_next_sibling("tr")

        if current is None:

            break

        # Optional regional rows have blank labels; stop at the next named spec row.

        heading = current.select_one(".ttl")

        if heading and _clean(heading.get_text(" ", strip=True)):

            break

        if "data-spec-optional" not in current.attrs:

            break

        value_node = current.select_one(".nfo")

        if value_node and _clean(value_node.get_text(" ", strip=True)):

            rows.append(current)

    configurations = []

    for row in rows:

        value_node = row.select_one(".nfo")

        raw = _clean(value_node.get_text(" ", strip=True)) if value_node else None

        if not raw:

            continue

        suffix_match = re.search(r"\s+-\s+(.+)$", raw)
        suffix = suffix_match.group(1).strip() if suffix_match else None
        region_match = re.fullmatch(r"(International|USA|India|China|ROW|Europe|Global)", suffix or "", re.I)
        suffix_models = _parse_model_numbers(suffix)
        # Only classify a suffix as model IDs if the entire suffix is a comma-separated
        # list of identifiers; otherwise retain it as raw text and do not guess.
        model_match = bool(
            suffix and not region_match
            and re.fullmatch(
                r"(?i)(?:iPhone\d+,\d+|[A-Z0-9]+(?:-[A-Z0-9]+)?(?:/[A-Z0-9]+)?)"
                r"(?:\s*,\s*(?:iPhone\d+,\d+|[A-Z0-9]+(?:-[A-Z0-9]+)?(?:/[A-Z0-9]+)?))*",
                suffix,
            )
            and suffix_models
        )
        qualifier = suffix if model_match else (region_match.group(1) if region_match else None)

        configurations.append({

            "raw": raw,

            "bands": _parse_bands(raw),

            "models": suffix_models if model_match else [],

            "region": qualifier if region_match else None,

        })

    return configurations

def _all_spec_rows(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Extract every labeled GSMArena spec row, preserving section and label text."""
    out: dict[str, dict[str, str]] = {}
    for table in soup.select("#specs-list table"):
        section = None
        for row in table.select("tr"):
            heading = row.find("th", scope="row")
            if heading:
                section = _clean(heading.get_text(" ", strip=True))
                if section:
                    out.setdefault(section, {})
            if not section:
                continue
            label_node = row.select_one(".ttl")
            value_node = row.select_one(".nfo")
            label = _clean(label_node.get_text(" ", strip=True)) if label_node else None
            if not label or not value_node:
                continue
            # Preserve multi-line values, particularly camera/variant rows.
            for br in value_node.find_all("br"):
                br.replace_with(" | ")
            value = _clean(value_node.get_text(" ", strip=True))
            if value:
                # Duplicate labels can occur; retain all values rather than overwrite.
                if label not in out[section]:
                    out[section][label] = value
                else:
                    old = out[section][label]
                    if isinstance(old, list):
                        old.append(value)
                    else:
                        out[section][label] = [old, value]
    return out


def _tri_state_yes_no(text: str | None) -> bool | None:
    if not text:
        return None
    t = text.strip().lower()
    if re.match(r"^(?:yes|true|supported|available)\b", t):
        return True
    if re.match(r"^(?:no|false|not supported|unavailable|none)\b", t):
        return False
    return None


def _split_list(text: str | None) -> list[str]:
    if not text:
        return []
    # Keep semantic phrases intact; split only on common GSMArena delimiters.
    return list(dict.fromkeys(x.strip(" .;,") for x in re.split(r"[,;|]\s*", text) if x.strip(" .;,")))


def _parse_positioning(text: str | None) -> dict[str, Any]:
    if not text:
        return {"raw": None, "systems": [], "signals": [], "bands": []}
    systems_map = {
        "GPS": r"\bGPS\b", "GLONASS": r"\bGLONASS\b", "Galileo": r"\bGalileo\b",
        "BeiDou": r"\bBeiDou\b|\bBDS\b", "QZSS": r"\bQZSS\b", "NavIC": r"\bNavIC\b|\bIRNSS\b",
        "A-GPS": r"\bA-?GPS\b", "LTEPP": r"\bLTEPP\b", "SUPL": r"\bSUPL\b",
    }
    systems = [name for name, pat in systems_map.items() if re.search(pat, text, re.I)]
    signals = list(dict.fromkeys(x.upper() for x in re.findall(r"\b(L1|L5|E1|E5a|E5b|E6|B1|B2a|B2b|B3|L2|L6)\b", text, re.I)))
    bands = list(dict.fromkeys(re.findall(r"\b(?:band\s*)?(L\d|E\d[a-z]?|B\d[a-z]?)\b", text, re.I)))
    return {"raw": text, "systems": systems, "signals": signals, "bands": bands}


def _parse_sensors(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    # Keep named sensor entries as source-faithful items; do not over-normalize aliases.
    aliases = [
        ("fingerprint", r"fingerprint(?:\s*\([^)]*\))?"), ("accelerometer", r"accelerometer"),
        ("gyroscope", r"gyroscope|\bgyro\b"), ("proximity", r"proximity"), ("compass", r"(?:compass|magnetometer)"),
        ("ambient_light", r"(?:ambient light|light sensor)"), ("barometer", r"barometer"),
        ("color_spectrum", r"color spectrum"), ("hall", r"hall sensor"), ("temperature", r"temperature sensor"),
        ("ultrasound", r"ultrasound"), ("face_id", r"face\s*id|facial recognition"),
    ]
    found = [{"type": key, "source_label": label.group(0), "raw": label.group(0)} for key, pat in aliases if (label := re.search(pat, text, re.I))]
    # Preserve every listed sensor even when the alias map does not recognize it.
    known_spans = [(m.start(), m.end()) for _, pat in aliases for m in re.finditer(pat, text, re.I)]
    if not found:
        return [{"type": "other", "source_label": item, "raw": item} for item in _split_list(text)]
    found.append({"type": "source_list", "source_label": text, "raw": text})
    return found


def _parse_benchmarks(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw_entries: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for section, fields in rows.items():
        for label, value in fields.items():
            vals = value if isinstance(value, list) else [value]
            for raw_value in vals:
                raw = str(raw_value)
                is_perf = (section.lower() == "performance" or
                           (section.lower() == "our tests" and re.search(r"performance|antutu|geekbench|3dmark|gfxbench|pcmark|wild ?life", str(label), re.I)) or
                           re.search(r"antutu|geekbench|3dmark|gfxbench|pcmark|wild ?life", str(label), re.I))
                if not is_perf:
                    continue
                raw_entries.append({"section": section, "test": str(label), "raw": raw})
                # GSMArena Our Tests combines multiple benchmark families in one row.
                for part in re.split(r"\s*\|\s*", raw):
                    match = re.match(r"\s*([^:]+):\s*(.*?)\s*$", part)
                    if not match:
                        continue
                    test_name, scores_text = match.group(1).strip(), match.group(2)
                    score_matches = list(re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s*\((v\d+(?:\.\d+)?)\))?", scores_text))
                    if score_matches:
                        for sm in score_matches:
                            score_raw = sm.group(1)
                            results.append({
                                "test": test_name,
                                "version": sm.group(2),
                                "score": float(score_raw.replace(',', '')) if '.' in score_raw else int(score_raw.replace(',', '')),
                                "raw": part.strip(),
                            })
                    else:
                        results.append({"test": test_name, "version": None, "score": None, "raw": part.strip()})
    return {"raw": raw_entries, "results": results}


def _parse_colors(text: str | None) -> list[str]:
    if not text:
        return []
    # Slash and comma-separated colors are common; retain raw separately.
    return _split_list(text)


def _parse_usb(text: str | None) -> dict[str, Any]:
    if not text:
        return {"raw": None, "connector": None, "usb_standard": None, "otg": None, "video_output": None, "charging_protocols": []}
    connector = None
    for name, pat in [("USB Type-C", r"USB\s*(?:Type-?C|C)\b|USB-C"), ("Lightning", r"Lightning"), ("Micro-USB", r"Micro-USB|MicroUSB"), ("USB Type-B", r"USB\s*Type-?B")]:
        if re.search(pat, text, re.I):
            connector = name
            break
    standard_match = re.search(r"\bUSB(?:\s+Type-?C)?\s*(\d+(?:\.\d+)?(?:\s*Gen\s*\d(?:\.\d)?)?)\b", text, re.I)
    return {
        "raw": text, "connector": connector,
        "usb_standard": _clean(standard_match.group(0)) if standard_match else None,
        "otg": True if re.search(r"\bOTG\b", text, re.I) else None,
        "video_output": True if re.search(r"DisplayPort|video output|display output", text, re.I) else None,
        "charging_protocols": [x for x in ["USB Power Delivery" if re.search(r"\bPD\b|Power Delivery", text, re.I) else None, "PPS" if re.search(r"\bPPS\b", text, re.I) else None] if x],
    }


def _parse_sim_configurations(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    chunks = [x.strip(" ·;|") for x in re.split(r"\s*[·|]\s*", text) if x.strip(" ·;|")]
    if len(chunks) == 1:
        chunks = [x.strip() for x in re.split(r"\s*;\s*", text) if x.strip()]
    configs = []
    for chunk in chunks:
        physical_slots = len(re.findall(r"Nano-SIM|Micro-SIM|Mini-SIM|SIM slot", chunk, re.I))
        esim_mentions = len(re.findall(r"eSIM", chunk, re.I))
        max_active = re.search(r"max\s*(\d+)\s*at a time", chunk, re.I)
        region = re.search(r"\b(International|USA|China|India|Europe|Global|Japan|Korea)\b", chunk, re.I)
        configs.append({"raw": chunk, "physical_sim_mentions": physical_slots, "esim_mentions": esim_mentions,
                        "max_active_sims": int(max_active.group(1)) if max_active else None,
                        "region_label": region.group(1) if region else None})
    return configs


def _parse_build_materials(text: str | None) -> dict[str, Any]:
    result = {"raw": text, "front_material": None, "frame_material": None, "back_material": None, "alternatives_present": False}
    if not text:
        return result
    result["alternatives_present"] = bool(re.search(r"\bor\b|/", text, re.I))
    patterns = [("front_material", r"([^,;]+?)\s+front"), ("back_material", r"([^,;]+?)\s+back"), ("frame_material", r"([^,;]+?)\s+frame")]
    for key, pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            result[key] = m.group(1).strip()
    return result


def _parse_camera_detail(raw: str | None) -> dict[str, Any]:
    text = raw or ""
    return {
        "raw": raw,
        "autofocus": list(dict.fromkeys(x.upper() for x in re.findall(r"\b(PDAF|Dual Pixel PDAF|Laser AF|AF)\b", text, re.I))),
        "stabilization": list(dict.fromkeys(x for x in ["OIS" if re.search(r"\bOIS\b|optical image stabilization", text, re.I) else None,
                                                        "EIS" if re.search(r"\bEIS\b|electronic image stabilization", text, re.I) else None,
                                                        "sensor-shift OIS" if re.search(r"sensor[- ]shift OIS", text, re.I) else None] if x)),
        "zoom_claims": list(dict.fromkeys(re.findall(r"\b(?:\d+(?:\.\d+)?x\s*)?(?:optical|hybrid|digital) zoom\b|\b\d+(?:\.\d+)?x\b", text, re.I))),
        "flash_claims": list(dict.fromkeys(re.findall(r"(?:Dual-LED|dual-tone|LED|ring LED|quad-LED) flash", text, re.I))),
        "hdr_standards": list(dict.fromkeys(re.findall(r"\b(?:HDR10\+?|Dolby Vision|HLG)\b", text, re.I))),
    }


def _extract_extra_fields(soup: BeautifulSoup) -> dict[str, Any]:
    rows = _all_spec_rows(soup)
    # Source rows excluded by user request: pricing and SAR. Preserve all other rows verbatim.
    preserved_rows: dict[str, dict[str, Any]] = {}
    for section, fields in rows.items():
        if section.lower() in {"price", "sar", "sar eu"}:
            continue
        keep = {label: value for label, value in fields.items() if not re.search(r"\bSAR(?:\s+EU)?\b", label, re.I) and label.lower() != "price"}
        if keep:
            preserved_rows[section] = keep
    positioning = rows.get("Comms", {}).get("Positioning")
    sensors = rows.get("Features", {}).get("Sensors") or rows.get("Comms", {}).get("Sensors")
    loudspeaker = rows.get("Sound", {}).get("Loudspeaker") or rows.get("Comms", {}).get("Loudspeaker")
    radio = rows.get("Comms", {}).get("Radio") or rows.get("Network", {}).get("Radio")
    colors = rows.get("Misc", {}).get("Colors") or rows.get("Misc", {}).get("Colour")
    fm_radio = _tri_state_yes_no(radio)
    if radio and re.search(r"FM radio", radio, re.I):
        fm_radio = True
    elif radio and re.search(r"No radio|No FM radio", radio, re.I):
        fm_radio = False
    wifi_raw = rows.get("Comms", {}).get("WLAN")
    bluetooth_raw = rows.get("Comms", {}).get("Bluetooth")
    usb_raw = rows.get("Comms", {}).get("USB")
    # Standard label variants for EU labels, plus exact raw values.
    durability_rows = {}
    for section, fields in rows.items():
        for label, value in fields.items():
            if re.search(r"energy class|free fall|repairability|protection|dust|water resistant|waterproof|MIL-STD|drop", label, re.I):
                durability_rows[f"{section}.{label}"] = value
    energy_class = next((v for sec, fs in rows.items() for lab, v in fs.items() if re.fullmatch(r"Energy(?: Class)?", lab, re.I) and sec.lower() == "eu label"), None)
    free_fall = next((v for sec, fs in rows.items() for lab, v in fs.items() if re.search(r"Free fall", lab, re.I)), None)
    repairability = next((v for sec, fs in rows.items() for lab, v in fs.items() if re.search(r"Repairability", lab, re.I)), None)
    display_row = rows.get("Our Tests", {}).get("Display") or rows.get("Display", {}).get("Display")
    display_type = rows.get("Display", {}).get("Type")
    return {
        "source_specifications": preserved_rows,
        "audio": {
            "loudspeaker_raw": loudspeaker,
            "stereo_speakers": True if loudspeaker and re.search(r"stereo speakers|stereo loudspeakers", loudspeaker, re.I) else (False if loudspeaker and re.search(r"mono speaker|single speaker", loudspeaker, re.I) else None),
            "speaker_count": int(m.group(1)) if loudspeaker and (m := re.search(r"(\d+)\s*speakers?", loudspeaker, re.I)) else None,
            "radio_raw": radio,
            "fm_radio": fm_radio,
            "jack_raw": rows.get("Sound", {}).get("3.5mm jack") or rows.get("Comms", {}).get("3.5mm jack"),
        },
        "sensors": {"raw": sensors, "items": _parse_sensors(sensors)},
        "colors": {"raw": colors, "listed": _parse_colors(colors)},
        "connectivity_extra": {
            "positioning": _parse_positioning(positioning),
            "wifi_features": [x for x in ["dual-band" if wifi_raw and re.search(r"dual[- ]band", wifi_raw, re.I) else None,
                                            "tri-band" if wifi_raw and re.search(r"tri[- ]band", wifi_raw, re.I) else None,
                                            "Wi-Fi Direct" if wifi_raw and re.search(r"Wi-?Fi Direct", wifi_raw, re.I) else None,
                                            "hotspot" if wifi_raw and re.search(r"hotspot", wifi_raw, re.I) else None,
                                            "Wi-Fi 6E" if wifi_raw and re.search(r"Wi-?Fi\s*6E|802\.11ax.*6\s*GHz", wifi_raw, re.I) else None] if x],
            "wifi_bands_ghz": [float(x) for x in re.findall(r"(?<!\d)(2\.4|5|6)\s*GHz", wifi_raw or "", re.I)],
            "bluetooth_profiles": [x for x in ["A2DP", "LE", "aptX", "aptX HD", "aptX Adaptive", "LDAC", "LHDC", "AAC", "SBC", "LC3", "Auracast"] if bluetooth_raw and re.search(r"\b" + re.escape(x) + r"\b", bluetooth_raw, re.I)],
            "usb": _parse_usb(usb_raw),
            "radio_raw": radio,
        },
        "sim_configurations": _parse_sim_configurations(rows.get("Body", {}).get("SIM") or rows.get("Network", {}).get("SIM") or rows.get("Misc", {}).get("SIM")),
        "build_materials": _parse_build_materials(rows.get("Body", {}).get("Build")),
        "camera_details": {
            "rear": _parse_camera_detail(" ".join(str(v) for k, v in rows.get("Main Camera", {}).items() if re.search(r"camera|video|features|dual|triple|quad", k, re.I))),
            "front": _parse_camera_detail(" ".join(str(v) for k, v in rows.get("Selfie camera", {}).items() if re.search(r"camera|video|features|dual|triple|quad", k, re.I))),
        },
        "display_details": {
            "type_raw": display_type,
            "measured_brightness_raw": display_row if display_row and re.search(r"nits", str(display_row), re.I) else None,
            "color_depth_bits": int(m.group(1)) if display_type and (m := re.search(r"(\d+)\s*bits?", display_type, re.I)) else None,
            "hdr_standards": list(dict.fromkeys(re.findall(r"\b(?:HDR10\+?|Dolby Vision|HDR Vivid|HLG)\b", display_type or "", re.I))),
            "pwm_dimming_hz": int(m.group(1)) if display_type and (m := re.search(r"(\d+)\s*Hz\s*PWM", display_type, re.I)) else None,
            "touch_sampling_hz": int(m.group(1)) if display_type and (m := re.search(r"(\d+)\s*Hz\s*touch", display_type, re.I)) else None,
            "always_on_display": True if display_type and re.search(r"always[- ]on display", display_type, re.I) else None,
        },
        "benchmarks": _parse_benchmarks(rows),
        "durability": {
            "eu_energy_class_raw": energy_class,
            "eu_free_fall_raw": free_fall,
            "repairability_raw": repairability,
            "raw_claims": durability_rows,
        },
    }


def parse_gsmarena_html(html: str, source: dict[str, Any] | None = None) -> dict[str, Any]:

    """Parse one GSMArena device HTML fragment into a recommendation-oriented phone document."""

    soup = BeautifulSoup(html, "html.parser")

    source = source or {}

    model_name = _spec(soup, "modelname")

    if not model_name:

        title = soup.select_one("h1.specs-phone-name-title")

        model_name = _clean(title.get_text(" ", strip=True)) if title else None

    # GSMArena may expose the product image in either the main image or CSS background.

    image_node = soup.select_one(".specs-photo-main img")

    image_url = image_node.get("src") if image_node else None

    page_url = source.get("url")

    announced = _spec(soup, "year")

    status = _spec(soup, "status")

    release_text = _spec(soup, "released-hl") or ""

    release_date_match = re.search(r"\bReleased\s+(.+)", release_text, re.I)

    expected_release_match = re.search(r"\bExp\.?\s*release\s+(.+)", release_text, re.I)

    release_date = _clean(release_date_match.group(1)) if release_date_match else None

    expected_release_date = _clean(expected_release_match.group(1)) if expected_release_match else None

    base_price_inr = _extract_base_price_inr(soup)
    
    india_variants = _extract_indian_variants(soup)

    youtube_links = _extract_youtube_links(soup)

    display_type = _spec(soup, "displaytype")

    display_size_text = _spec(soup, "displaysize")

    display_size = _first_float(display_size_text)

    display_resolution = _parse_resolution(_spec(soup, "displayresolution"))

    ppi_match = re.search(r"(\d+)\s*ppi", _spec(soup, "displayresolution") or "", re.I)

    if not ppi_match:

        ppi_match = re.search(r"(\d+)\s*ppi", display_size_text or "", re.I)

    refresh_match = re.search(r"(\d{2,3})\s*Hz", display_type or "", re.I)

    peak_match = re.search(r"(\d[\d,]*)\s*nits\s*\(peak\)", display_type or "", re.I)

    hbm_match = re.search(r"(\d[\d,]*)\s*nits\s*\(HBM\)", display_type or "", re.I)

    typical_match = re.search(r"(\d[\d,]*)\s*nits\s*\(typ\)", display_type or "", re.I)

    chipset_text = _spec(soup, "chipset")

    process_match = re.search(r"\((\d+)\s*nm\)", chipset_text or "", re.I)

    cpu_text = _spec(soup, "cpu")

    gpu_text = _spec(soup, "gpu")

    memory_text = _spec(soup, "internalmemory")

    main_camera_text = _spec_multiline(soup, "cam1modules")

    selfie_camera_text = _spec_multiline(soup, "cam2modules")

    battery_text = _spec(soup, "battype-hl")

    battery_capacity_texts = [
        value for value in (_spec(soup, "batsize-hl"), _spec(soup, "batsize")) if value
    ]
    battery_capacity_values: list[int] = []
    for capacity_text in battery_capacity_texts:
        battery_capacity_values.extend(
            int(value.replace(",", ""))
            for value in re.findall(r"(?<!\d)(\d[\d,]*)\s*mAh\b", capacity_text, re.I)
        )
    battery_capacity_values = list(dict.fromkeys(battery_capacity_values))

    # Keep the legacy scalar for compatibility, while exposing every explicit capacity.
    battery_capacity = battery_capacity_values[0] if battery_capacity_values else None
    if battery_capacity is None:
        # Some captures expose a numeric capacity without the mAh suffix.
        fallback_capacity_text = next(iter(battery_capacity_texts), None)
        battery_capacity = _first_int(fallback_capacity_text)
        if battery_capacity is not None:
            battery_capacity_values = [battery_capacity]

    # Preserve the compact spotlight text, but normalize charging only from the full

    # Battery > Charging row. A wattage adjacent to "wireless" is never treated as wired.

    charging_row = next(

        (

            row for row in soup.select("#specs-list tr")

            if row.select_one(".ttl")

            and (_clean(row.select_one(".ttl").get_text(" ", strip=True)) or "").lower() == "charging"

        ),

        None,

    )

    charging_detail = (

        _clean(charging_row.select_one(".nfo").get_text(" ", strip=True))

        if charging_row and charging_row.select_one(".nfo")

        else None

    )

    charging_raw = charging_detail or battery_text

    # Bypass charging is an explicit capability used to reduce battery heat during
    # sustained workloads (for example gaming or navigation while plugged in).
    # Preserve a tri-state value: absence of a phrase is unknown, not unsupported.
    bypass_charging = None
    if charging_raw:
        if re.search(r"\b(?:no|not support(?:ed)?|does not support|without)\s+(?:the\s+)?bypass charging\b", charging_raw, re.I):
            bypass_charging = False
        elif re.search(r"\bbypass charging\b", charging_raw, re.I):
            bypass_charging = True

    charging = {

        "wired_watt": None,

        "wireless_watt": None,

        "reverse_wired_watt": None,

        "reverse_wireless_watt": None,

        "bypass_charging": bypass_charging,

        "raw": charging_raw,

    }

    if charging_detail:

        # Remove reverse and wireless phrases before looking for explicitly wired wattage.

        reverse_wired_matches = re.findall(

            r"(\d+(?:\.\d+)?)\s*W\s*reverse[- ]wired|reverse[- ]wired[^,;.\n]*?(\d+(?:\.\d+)?)\s*W",

            charging_detail,

            re.I,

        )

        reverse_wireless_matches = re.findall(

            r"(\d+(?:\.\d+)?)\s*W\s*reverse[- ]wireless|reverse[- ]wireless[^,;.\n]*?(\d+(?:\.\d+)?)\s*W",

            charging_detail,

            re.I,

        )

        reverse_wired_values = [float(v) for pair in reverse_wired_matches for v in pair if v]

        reverse_wireless_values = [float(v) for pair in reverse_wireless_matches for v in pair if v]

        if reverse_wired_values:

            value = max(reverse_wired_values)

            charging["reverse_wired_watt"] = int(value) if value.is_integer() else value

        if reverse_wireless_values:

            value = max(reverse_wireless_values)

            charging["reverse_wireless_watt"] = int(value) if value.is_integer() else value

        wireless_values = [

            float(value)

            for value in re.findall(r"(\d+(?:\.\d+)?)\s*W?\s*wireless\b", charging_detail, re.I)

        ]

        if wireless_values:

            value = max(wireless_values)

            charging["wireless_watt"] = int(value) if value.is_integer() else value

        # Exclude wireless/reverse clauses to prevent cross-classification.

        wired_only_text = re.sub(

            r"\d+(?:\.\d+)?\s*W\s*(?:reverse[- ]wired|reverse[- ]wireless|wireless)\b[^,;.\n]*",

            "",

            charging_detail,

            flags=re.I,

        )

        wired_only_text = re.sub(

            r"(?:reverse[- ]wired|reverse[- ]wireless|wireless)[^,;.\n]*",

            "",

            wired_only_text,

            flags=re.I,

        )

        wired_match = re.search(
            r"(?<![\d.])(\d+(?:\.\d+)?)\s*W?\s*wired\b"
            r"|wired\b[^,;.\n]*?(?<![\d.])(\d+(?:\.\d+)?)\s*W?\b",
            wired_only_text,
            re.I,
        )

        if wired_match:

            value = float(wired_match.group(1) or wired_match.group(2))

            charging["wired_watt"] = int(value) if value.is_integer() else value

    # Explicit identity fields.

    brand_match = re.match(r"^([A-Za-z][A-Za-z0-9.+&'-]*)\b", model_name or "")

    brand = brand_match.group(1).strip() if brand_match else None

    models_raw = _spec(soup, "models")

    model_numbers = _parse_model_numbers(models_raw)

    wlan_text = _spec(soup, "wlan")

    wifi_candidates: list[str] = []

    if wlan_text:

        # Prefer explicit consumer generations; avoid mistaking 802.11 for Wi-Fi 802.

        wifi_candidates.extend(

            match.upper() for match in re.findall(r"\bWi-?Fi\s*(\d+(?:E)?)\b(?!\.)", wlan_text, re.I)

        )

        wifi_candidates.extend(match.upper() for match in re.findall(r"/\s*([4-7]E?)\b", wlan_text, re.I))

        amendments = re.findall(r"(?<![A-Za-z0-9])(be|ax|ac|ad|n)(?![A-Za-z0-9])", wlan_text, re.I)

        mapping = {"n": "4", "ac": "5", "ax": "6", "be": "7", "ad": "60"}

        wifi_candidates.extend(mapping[item.lower()] for item in amendments if item.lower() in mapping)

    # Keep every explicit/normalized generation in order of increasing generation; expose the

    # highest one as the compatibility field without discarding the source text.

    def wifi_sort_key(value: str) -> tuple[int, int]:

        return (int(re.sub(r"\D", "", value)), 1 if value.endswith("E") else 0)

    wifi_generations = sorted(set(wifi_candidates), key=wifi_sort_key)

    wifi_generation = max(wifi_generations, key=wifi_sort_key) if wifi_generations else None

    bluetooth_text = _spec(soup, "bluetooth")
    bluetooth_versions = list(dict.fromkeys(
        re.findall(r"(?<!\d)(\d+\.\d+)(?!\d)", bluetooth_text or "")
    ))
    # A scalar version is only unambiguous when the source lists exactly one version.
    bluetooth_version = bluetooth_versions[0] if len(bluetooth_versions) == 1 else None

    nfc_text = _spec(soup, "nfc")

    jack_text = _spec(soup, "audio-jack")

    if not jack_text:

        jack_row = next(

            (row for row in soup.select("#specs-list tr")

             if row.select_one(".ttl") and

             (_clean(row.select_one(".ttl").get_text(" ", strip=True)) or "").lower() == "3.5mm jack"),

            None,

        )

        jack_text = _clean(jack_row.select_one(".nfo").get_text(" ", strip=True)) if jack_row and jack_row.select_one(".nfo") else None

    body_other = _spec(soup, "bodyother")
    weight_text = _spec(soup, "weight")
    weight_options = _parse_weight_options(weight_text)
    # Avoid presenting one weight as definitive when the source lists alternatives.
    weight_g = weight_options[0] if len(weight_options) == 1 else None

    battery_description = _spec(soup, "batdescription1")

    energy_match = re.search(r"(\d+(?:\.\d+)?)\s*Wh\b", battery_description or "", re.I)

    endurance_text = _spec(soup, "batlife")

    endurance_match = re.search(r"Endurance rating\s+(\d+)\s*h", endurance_text or "", re.I)

    measured_brightness_text = None

    for row in soup.select("#specs-list tr"):

        label = _clean(row.select_one(".ttl").get_text(" ", strip=True)) if row.select_one(".ttl") else ""

        value = _clean(row.select_one(".nfo").get_text(" ", strip=True)) if row.select_one(".nfo") else ""

        if (label or "").lower() == "display" and value and "nits" in value.lower():

            measured_brightness_text = value

            break

    measured_brightness_match = re.search(

        r"(\d[\d,]*)\s*nits\s*max brightness\s*\(measured\)",

        measured_brightness_text or "",

        re.I,

    )

    # Camera fields are parsed independently so specifications never bleed across lenses.

    camera_video = _spec(soup, "cam1video")

    selfie_video = _spec(soup, "cam2video")

    rear_lenses = _parse_camera_lenses(main_camera_text)

    selfie_lenses = _parse_camera_lenses(selfie_camera_text)

    rear_mp = max(

        (lens["mp"] for lens in rear_lenses if isinstance(lens.get("mp"), (int, float))),

        default=None,

    )

    rear_features = _spec(soup, "cam1features") or ""

    # GSMArena normally lists OIS beside individual rear camera modules, not in
    # the generic camera Features row. Inspect lens-level raw text and video text.
    ois_evidence = "\n".join(
        [main_camera_text or "", camera_video or "", rear_features]
        + [str(lens.get("raw") or "") for lens in rear_lenses]
    )
    ois_value = True if re.search(r"\bOIS\b|optical image stabilization", ois_evidence, re.I) else None

    comms_rows = _section_rows(soup).get("Comms", {})
    ir_text = comms_rows.get("Infrared port")
    has_ir = (
        True if ir_text and re.match(r"^yes\b", ir_text.strip(), re.I) else
        False if ir_text and re.match(r"^no\b", ir_text.strip(), re.I) else
        None
    )

    net4g_text = _spec(soup, "net4g")

    net5g_text = _spec(soup, "net5g")

    bands = {

        "technology": _spec(soup, "nettech"),

        "2g_raw": _spec(soup, "net2g"),

        "3g_raw": _spec(soup, "net3g"),

        "4g_raw": net4g_text,

        "5g_raw": net5g_text,

        "4g_bands": _parse_bands(net4g_text),

        "5g_bands": _parse_bands(net5g_text),

        "4g_configurations": _network_configurations(soup, "net4g"),

        "5g_configurations": _network_configurations(soup, "net5g"),

    }

    result = {

        "model_name": model_name,

        "brand": brand,

        "smartphone_brand": brand,

        "device_type": _parse_device_type(model_name),

        "status": ("coming_soon" if status and "coming soon" in status.lower() else
                   "discontinued" if status and "discontinued" in status.lower() else
                   "available" if status and "available" in status.lower() else None),

        "image_url": image_url,

        "image": image_url,

        "source": {

            "site": source.get("site", "GSMArena"),

            "url": page_url,

            "scraped_at": source.get("scraped_at"),

            "http_status": source.get("http_status"),

        },

        "release_date": release_date,

        "expected_release_date": expected_release_date,

        "release_year": _parse_release_year(release_date),

        "announced": announced,

        "variants": _parse_variants(memory_text),

        "pricing": {

            "india": {

                "base_price_inr": base_price_inr,

                "variants": india_variants,

            },
            
            "youtube_links": youtube_links,
        },

        "performance": {

            "chipset": chipset_text,

            "chipset_tier": None,

            "processor_name": cpu_text,

            "cpu": cpu_text,

            "gpu": gpu_text,

            "process_node_nm": int(process_match.group(1)) if process_match else None,

        },

        "display": {

            "panel_type": display_type,

            "size_inches": display_size,

            "refresh_rate_hz": int(refresh_match.group(1)) if refresh_match else None,

            "peak_nits": int(peak_match.group(1).replace(",", "")) if peak_match else None,

            "hbm_nits": int(hbm_match.group(1).replace(",", "")) if hbm_match else None,

            "typical_nits": int(typical_match.group(1).replace(",", "")) if typical_match else None,

            "measured_nits": int(measured_brightness_match.group(1).replace(",", "")) if measured_brightness_match else None,

            "resolution_width": display_resolution["width_px"],

            "resolution_height": display_resolution["height_px"],

            "display_ppi": int(ppi_match.group(1)) if ppi_match else None,

            "protection": _spec(soup, "displayprotection"),

            "other": _spec(soup, "displayother"),

        },

        "battery": {

            "capacity_mah": battery_capacity,

            "capacity_options_mah": battery_capacity_values,

            "energy_wh": float(energy_match.group(1)) if energy_match else None,

            "charging": charging,

            "charging_watt": charging["wired_watt"],

            "wireless_charging_watt": charging["wireless_watt"],

            "bypass_charging": charging["bypass_charging"],

            "active_use_hours": None,

            "active_use_raw": _spec(soup, "batlife"),

            "active_use_score_minutes": (int(m.group(1)) * 60 + int(m.group(2)) if (m := re.search(r"Active use score\s+(\d+):(\d+)h", _spec(soup, "batlife") or "", re.I)) else None),

            "endurance_hours": int(endurance_match.group(1)) if endurance_match else None,

        },

        "camera": {

            "rear": {

                "lenses": rear_lenses,

                "max_video_resolution": camera_video,

                "features": _spec(soup, "cam1features"),

                "raw_modules": main_camera_text,

            },

            "front": {

                "lenses": selfie_lenses,

                "max_video_resolution": selfie_video,

                "features": _spec(soup, "cam2features"),

                "raw_modules": selfie_camera_text,

            },

            "ois": ois_value,

        },

        "software": {

            "os": _spec(soup, "os"),

            "major_android_updates_years": _parse_major_updates(_spec(soup, "os")),

        },

        "memory": {

            "internal_raw": memory_text,

            "storage_type": _spec(soup, "memoryother"),

            "card_slot": _spec(soup, "memoryslot"),

        },

        "connectivity": {

            **bands,

            "wifi": {"generation": wifi_generation, "generations": wifi_generations, "raw": wlan_text},

            "bluetooth": {
                "version": bluetooth_version,
                "versions": bluetooth_versions,
                "raw": bluetooth_text,
            },

            "has_nfc": (True if nfc_text and re.match(r"^yes\b", nfc_text.strip(), re.I) else
                        False if nfc_text and re.match(r"^no\b", nfc_text.strip(), re.I) else None),
            "nfc_availability": (
                "region_dependent"
                if nfc_text and re.search(r"market/region dependent|region dependent", nfc_text, re.I)
                else "available"
                if nfc_text and re.match(r"^yes\b", nfc_text.strip(), re.I)
                else "unavailable"
                if nfc_text and re.match(r"^no\b", nfc_text.strip(), re.I)
                else "unknown"
            ),
            "nfc_raw": nfc_text,

            "has_ir": has_ir,
            "ir_raw": ir_text,

            "has_5g": True if "5g" in (bands["technology"] or "").lower() else (

                False if bands["technology"] else None

            ),

            "usb": _spec(soup, "usb"),

        },

        "physical": {

            "dimensions": _parse_dimensions(_spec(soup, "dimensions")),

            "weight_g": weight_g if weight_options else _first_float(weight_text),

            "weight_options_g": weight_options,

            "weight_raw": weight_text,

            "build": _spec(soup, "build"),

            "sim": _spec(soup, "sim"),

            "ip_rating": _parse_ip_rating(body_other),

            "water_resistance_raw": body_other,

            "model_numbers": model_numbers,

            "form_factor": "foldable" if "folded:" in (_spec(soup, "dimensions") or "").lower() else None,

            "headphone_jack": (False if jack_text and jack_text.strip().lower() == "no" else

                               True if jack_text and jack_text.strip().lower() == "yes" else None),

        },

        "filter": {

            "release_year": _parse_release_year(release_date),

            "ram_options_gb": sorted({v["ram_gb"] for v in _parse_variants(memory_text)}),

            "storage_options_gb": sorted({v["storage_gb"] for v in _parse_variants(memory_text)}),

            "display_ppi": int(ppi_match.group(1)) if ppi_match else None,

            "form_factor": "foldable" if "folded:" in (_spec(soup, "dimensions") or "").lower() else None,

        },

        "scores": {"gaming": None, "camera": None, "display": None, "battery": None},

        "rear_camera_mp": rear_mp,

        "rear_camera_count": len(rear_lenses),

    }

    # Complete extraction layer: retain all non-price/non-SAR source rows and add
    # normalized, source-grounded fields. Never convert missing rows into False.
    extras = _extract_extra_fields(soup)
    result.update({k: v for k, v in extras.items() if k not in {"connectivity_extra"}})
    result["connectivity"].update(extras["connectivity_extra"])
    result["connectivity"]["positioning_raw"] = extras["connectivity_extra"]["positioning"]["raw"]
    result["connectivity"]["positioning_systems"] = extras["connectivity_extra"]["positioning"]["systems"]
    result["connectivity"]["positioning_signals"] = extras["connectivity_extra"]["positioning"]["signals"]
    result["connectivity"]["fm_radio"] = extras["audio"]["fm_radio"]
    result["connectivity"]["wifi"]["features"] = extras["connectivity_extra"]["wifi_features"]
    result["connectivity"]["wifi"]["bands_ghz"] = extras["connectivity_extra"]["wifi_bands_ghz"]
    result["connectivity"]["bluetooth"]["profiles_and_codecs"] = extras["connectivity_extra"]["bluetooth_profiles"]
    result["physical"]["sim_configurations"] = extras["sim_configurations"]
    result["physical"]["build_materials"] = extras["build_materials"]
    result["camera"]["rear"]["details"] = extras["camera_details"]["rear"]
    result["camera"]["front"]["details"] = extras["camera_details"]["front"]
    result["display"]["details"] = extras["display_details"]
    battery_test_raw = extras["source_specifications"].get("Our Tests", {}).get("Battery")
    if battery_test_raw:
        result["battery"]["active_use_raw"] = battery_test_raw
        active_match = re.search(r"Active use score\s+(\d+):(\d+)h", battery_test_raw, re.I)
        result["battery"]["active_use_score_minutes"] = (int(active_match.group(1)) * 60 + int(active_match.group(2))) if active_match else None
    return result

def parse_saved_json(path: str | Path) -> dict[str, Any]:

    """Parse a saved JSON wrapper containing source metadata and content.html."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    html = payload.get("content", {}).get("html")

    if not isinstance(html, str):

        raise ValueError(f"{path}: expected string at content.html")

    return parse_gsmarena_html(html, payload.get("source", {}))

def parse_many(paths: Iterable[str | Path]) -> list[dict[str, Any]]:

    """Parse multiple saved device JSON files; errors are raised, not silently skipped."""

    return [parse_saved_json(path) for path in paths]

if __name__ == "__main__":

    import argparse

    project_root = Path.cwd()

    default_input = project_root / "output" / "gsmarena" / "bronze" / "phones"

    default_output = project_root / "output" / "gsmarena" / "silver" / "phones.json"

    parser = argparse.ArgumentParser(description="Parse GSMArena Bronze captures into one Silver phones.json")

    parser.add_argument("files", nargs="*", help="Optional input JSON files; defaults to all JSON files in the Bronze directory")

    parser.add_argument("--output", type=Path, default=default_output, help="Output path for the combined phones.json array")

    args = parser.parse_args()

    input_files = [Path(f) for f in args.files] if args.files else sorted(default_input.glob("*.json"))

    if not input_files:

        parser.error(f"No JSON files found in {default_input}")

    results = parse_many(input_files)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Parsed {len(results)} phone record(s).")

    print(f"Output written to: {args.output}")
