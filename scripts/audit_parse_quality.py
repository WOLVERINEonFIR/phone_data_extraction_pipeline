
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATASET_PATH = (
    PROJECT_ROOT
    / "output"
    / "gsmarena"
    / "silver"
    / "phones.json"
)

REPORT_DIR = (
    PROJECT_ROOT
    / "output"
    / "gsmarena"
    / "silver"
    / "reports"
)

ISSUES_CSV = REPORT_DIR / "parse_quality_issues.csv"
SUMMARY_CSV = REPORT_DIR / "parse_quality_field_summary.csv"


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def get_phone_name(record):
    identity = record.get("identity") or {}

    brand = identity.get("brand") or record.get("brand") or "Unknown"
    model = (
        identity.get("model_name")
        or record.get("model_name")
        or "Unknown model"
    )

    return brand, model


def has_source_evidence(issue):
    evidence = issue.get("source_evidence")

    return evidence is not None and evidence != ""


def increment_nested(counter, key, value):
    counter[key][value] += 1


# --------------------------------------------------
# Load dataset
# --------------------------------------------------

def load_dataset():
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATASET_PATH}"
        )

    with DATASET_PATH.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError(
            "Expected the dataset to contain a JSON list."
        )

    return records


# --------------------------------------------------
# Audit
# --------------------------------------------------

def audit_dataset(records):
    total_issues = 0

    status_counts = Counter()
    priority_counts = Counter()
    classification_counts = Counter()

    evidence_counts = Counter()
    field_counts = Counter()

    field_priority_counts = defaultdict(Counter)
    field_classification_counts = defaultdict(Counter)
    field_evidence_counts = defaultdict(Counter)

    issue_rows = []

    missing_quality_count = 0
    malformed_warning_count = 0

    affected_phones = []

    for record in records:
        if not isinstance(record, dict):
            continue

        brand, model = get_phone_name(record)
        phone_name = f"{brand} {model}"

        parse_quality = record.get("parse_quality")

        if not isinstance(parse_quality, dict):
            missing_quality_count += 1
            continue

        status = parse_quality.get("status", "unknown")
        status_counts[status] += 1

        warnings = parse_quality.get("warnings") or []

        if not isinstance(warnings, list):
            malformed_warning_count += 1
            continue

        phone_issue_count = 0

        for issue in warnings:
            if not isinstance(issue, dict):
                malformed_warning_count += 1
                continue

            total_issues += 1
            phone_issue_count += 1

            field = issue.get("field") or "unknown"
            priority = issue.get("priority") or "unknown"
            classification = (
                issue.get("classification") or "unknown"
            )

            evidence_present = has_source_evidence(issue)

            priority_counts[priority] += 1
            classification_counts[classification] += 1
            field_counts[field] += 1

            field_priority_counts[field][priority] += 1
            field_classification_counts[field][classification] += 1

            if evidence_present:
                evidence_counts["present"] += 1
                field_evidence_counts[field]["present"] += 1
            else:
                evidence_counts["missing"] += 1
                field_evidence_counts[field]["missing"] += 1

            issue_rows.append({
                "brand": brand,
                "model": model,
                "field": field,
                "code": issue.get("code"),
                "priority": priority,
                "classification": classification,
                "source_evidence": issue.get("source_evidence"),
                "message": issue.get("message"),
            })

        if phone_issue_count:
            affected_phones.append({
                "phone": phone_name,
                "status": status,
                "issue_count": phone_issue_count,
            })

    return {
        "total_issues": total_issues,
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "classification_counts": classification_counts,
        "evidence_counts": evidence_counts,
        "field_counts": field_counts,
        "field_priority_counts": field_priority_counts,
        "field_classification_counts": field_classification_counts,
        "field_evidence_counts": field_evidence_counts,
        "issue_rows": issue_rows,
        "missing_quality_count": missing_quality_count,
        "malformed_warning_count": malformed_warning_count,
        "affected_phones": affected_phones,
    }


# --------------------------------------------------
# CSV reports
# --------------------------------------------------

def export_reports(results):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Detailed issue report
    issue_columns = [
        "brand",
        "model",
        "field",
        "code",
        "priority",
        "classification",
        "source_evidence",
        "message",
    ]

    with ISSUES_CSV.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=issue_columns
        )
        writer.writeheader()
        writer.writerows(results["issue_rows"])

    # Field-level summary
    summary_columns = [
        "field",
        "total_issues",
        "critical",
        "high",
        "medium",
        "low",
        "error",
        "human_review",
        "warning",
        "info",
        "evidence_present",
        "evidence_missing",
    ]

    with SUMMARY_CSV.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=summary_columns
        )
        writer.writeheader()

        for field, total in results["field_counts"].most_common():
            priority = results["field_priority_counts"][field]
            classification = results[
                "field_classification_counts"
            ][field]
            evidence = results["field_evidence_counts"][field]

            writer.writerow({
                "field": field,
                "total_issues": total,
                "critical": priority["critical"],
                "high": priority["high"],
                "medium": priority["medium"],
                "low": priority["low"],
                "error": classification["error"],
                "human_review": classification["human_review"],
                "warning": classification["warning"],
                "info": classification["info"],
                "evidence_present": evidence["present"],
                "evidence_missing": evidence["missing"],
            })


# --------------------------------------------------
# Console report
# --------------------------------------------------

def print_counter(title, counter):
    print(f"\n{title}")
    print("-" * 55)

    if not counter:
        print("No data.")
        return

    for key, count in counter.most_common():
        print(f"{key:<25} {count:>8}")


def print_report(records, results):
    print("\n" + "=" * 65)
    print("PARSE QUALITY DATASET AUDIT")
    print("=" * 65)

    print(f"\nDataset: {DATASET_PATH}")
    print(f"Total phones: {len(records)}")
    print(f"Total issues: {results['total_issues']}")

    print(
        f"Phones with missing parse_quality: "
        f"{results['missing_quality_count']}"
    )

    print(
        f"Malformed warning entries: "
        f"{results['malformed_warning_count']}"
    )

    print_counter(
        "STATUS DISTRIBUTION",
        results["status_counts"]
    )

    print_counter(
        "ISSUES BY PRIORITY",
        results["priority_counts"]
    )

    print_counter(
        "ISSUES BY CLASSIFICATION",
        results["classification_counts"]
    )

    print_counter(
        "SOURCE EVIDENCE",
        results["evidence_counts"]
    )

    print("\nTOP 20 FIELDS WITH MOST ISSUES")
    print("-" * 65)

    for field, count in results["field_counts"].most_common(20):
        evidence = results["field_evidence_counts"][field]
        classifications = results[
            "field_classification_counts"
        ][field]

        print(f"\n{field}")
        print(f"  Total issues:       {count}")
        print(f"  Evidence present:   {evidence['present']}")
        print(f"  Evidence missing:   {evidence['missing']}")
        print(
            f"  Errors:             "
            f"{classifications['error']}"
        )
        print(
            f"  Human review:       "
            f"{classifications['human_review']}"
        )
        print(
            f"  Warnings:           "
            f"{classifications['warning']}"
        )
        print(
            f"  Info:               "
            f"{classifications['info']}"
        )

    print("\nTOP 20 PHONES BY ISSUE COUNT")
    print("-" * 65)

    for phone in sorted(
        results["affected_phones"],
        key=lambda item: item["issue_count"],
        reverse=True
    )[:20]:
        print(
            f"{phone['phone']:<40} "
            f"Issues: {phone['issue_count']:<5} "
            f"Status: {phone['status']}"
        )

    print("\n" + "=" * 65)
    print("REPORT FILES")
    print("=" * 65)
    print(f"Issues:  {ISSUES_CSV}")
    print(f"Summary: {SUMMARY_CSV}")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    records = load_dataset()
    results = audit_dataset(records)

    export_reports(results)
    print_report(records, results)


if __name__ == "__main__":
    main()
