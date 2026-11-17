import csv
import json
import re
from pathlib import Path

DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "output" / "gsmarena" / "silver" / "phones.json"
)

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "output" / "gsmarena" / "silver" / "reports"
    / "wireless_charging_audit.csv"
)

with DATASET_PATH.open("r", encoding="utf-8") as file:
    phones = json.load(file)

rows = []

for phone in phones:
    battery = phone.get("battery") or {}
    wireless = battery.get("wireless_charging") or {}

    if wireless.get("max_power_w") is not None:
        continue

    identity = phone.get("identity") or {}
    provenance = phone.get("provenance") or {}
    raw_specs = provenance.get("raw_specifications") or {}
    raw_battery = raw_specs.get("Battery") or {}

    raw_charging = (
        str(raw_battery.get("Charging") or "")
        if isinstance(raw_battery, dict)
        else ""
    )

    clauses = raw_charging.split("|")

    ordinary_clauses = [
        clause for clause in clauses
        if not re.search(r"\breverse[- ]wireless\b", clause, re.I)
    ]

    ordinary_wireless = any(
        re.search(r"\bwireless\b", clause, re.I)
        for clause in ordinary_clauses
    )

    explicit_wattage = any(
        re.search(
            r"\d+(?:\.\d+)?\s*W\s*wireless\b"
            r"|\bwireless\b[^,;]*?\d+(?:\.\d+)?\s*W",
            clause,
            re.I,
        )
        for clause in ordinary_clauses
    )

    reverse_wireless = bool(
        re.search(r"\breverse[- ]wireless\b", raw_charging, re.I)
    )

    if explicit_wattage:
        classification = "explicit_wireless_wattage"
    elif ordinary_wireless:
        classification = "wireless_mentioned_no_wattage"
    elif reverse_wireless:
        classification = "reverse_wireless_only"
    elif raw_charging:
        classification = "no_wireless_mention"
    else:
        classification = "charging_section_missing"

    rows.append({
        "brand": identity.get("brand"),
        "model": identity.get("model_name"),
        "normalized_supported": wireless.get("supported"),
        "normalized_max_power_w": wireless.get("max_power_w"),
        "normalized_standards": ", ".join(
            wireless.get("standards") or []
        ),
        "raw_charging": raw_charging,
        "classification": classification,
    })

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

with OUTPUT_PATH.open("w", newline="", encoding="utf-8-sig") as file:
    writer = csv.DictWriter(file, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"Total phones: {len(phones)}")
print(f"Missing wireless wattage: {len(rows)}")
print("\nCLASSIFICATION SUMMARY")

for category in sorted({row["classification"] for row in rows}):
    count = sum(
        row["classification"] == category for row in rows
    )
    print(f"{category}: {count}")

print(f"\nReport saved to: {OUTPUT_PATH}")