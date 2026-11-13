"""Immutable, date-partitioned daily outcome archive for AHID runs."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def save_daily_outcome(report: str, root: Optional[Path] = None,
                       run_date: Optional[dt.date] = None) -> Path:
    """Save report + provenance metadata under outcomes/YYYY/MM/DD.

    Existing dated reports are never silently overwritten. Repeated workflow
    runs on the same date receive a run-number suffix so the audit trail stays
    intact. The yearly index is updated idempotently.
    """
    base = Path(root) if root is not None else ROOT
    day = run_date or dt.datetime.now(dt.timezone.utc).date()
    day_dir = base / "outcomes" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    day_dir.mkdir(parents=True, exist_ok=True)

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    suffix = f"_run-{run_number}" if run_number else ""
    report_path = day_dir / f"report{suffix}.md"
    metadata_path = day_dir / f"metadata{suffix}.json"

    # A local/manual rerun without a workflow run number must not destroy
    # an earlier result for the same date.
    if report_path.exists():
        counter = 2
        while (day_dir / f"report{suffix}_{counter}.md").exists():
            counter += 1
        suffix = f"{suffix}_{counter}"
        report_path = day_dir / f"report{suffix}.md"
        metadata_path = day_dir / f"metadata{suffix}.json"

    report_path.write_text(report, encoding="utf-8")
    metadata = {
        "run_date": day.isoformat(),
        "saved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_sha": os.environ.get("GITHUB_SHA", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "workflow_run_id": run_id,
        "workflow_run_number": run_number,
        "report_file": report_path.name,
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "archive_schema_version": 1,
        "note": "PubMed retrieval and heuristic triage are not full-text eligibility decisions.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    year_dir = base / "outcomes" / f"{day:%Y}"
    year_dir.mkdir(parents=True, exist_ok=True)
    index_path = year_dir / "README.md"
    heading = f"# AHID outcomes — {day:%Y}\n\nDaily immutable run archive. Reports are grouped by month/day.\n\n"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else heading
    rel = report_path.relative_to(year_dir).as_posix()
    entry = f"- {day.isoformat()}: [{report_path.name}]({rel}) · [metadata]({metadata_path.relative_to(year_dir).as_posix()})"
    if entry not in existing:
        index_path.write_text(existing.rstrip() + "\n" + entry + "\n", encoding="utf-8")
    return report_path
