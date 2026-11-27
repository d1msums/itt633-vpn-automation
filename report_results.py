"""Create timestamped CSV and TXT verification reports."""
import csv
from datetime import datetime, timezone
from pathlib import Path


FIELDS = [
    "run_id", "timestamp_utc", "device", "test_id", "description",
    "expected", "actual", "status", "duration_seconds", "evidence_path",
]


def write_reports(results: list[dict], run_id: str, overall_status: str, output_dir: Path):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    csv_path = output_dir / "verification_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for item in results:
            row = {"run_id": run_id, "timestamp_utc": timestamp, **item}
            writer.writerow({key: row.get(key, "") for key in FIELDS})

    txt_path = output_dir / "verification_report.txt"
    lines = [
        "ITT633 SITE-TO-SITE IPsec VPN VERIFICATION",
        f"Run ID: {run_id}",
        f"Timestamp (UTC): {timestamp}",
        f"Overall status: {overall_status}",
        "",
    ]
    for item in results:
        lines.extend([
            f"{item['test_id']} | {item['device']} | {item['status']}",
            f"Test: {item['description']}",
            f"Expected: {item['expected']}",
            f"Actual: {item['actual']}",
            f"Duration: {item['duration_seconds']} seconds",
            f"Evidence: {item['evidence_path']}",
            "",
        ])
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"CSV report: {csv_path}")
    print(f"Text report: {txt_path}")
