"""Human-completion worksheet for outcome compatibility; never infers poolability."""
import csv
from pathlib import Path

FIELDS = [
    "pmid", "doi", "title", "study_design", "abstract", "pubmed_url",
    "outcome_name", "outcome_definition", "measure_type",
    "timepoint_or_followup", "numerator_events", "denominator_n",
    "effect_estimate", "effect_measure", "adjusted_or_unadjusted",
    "adjustment_covariates", "cohort_overlap_risk",
    "compatibility_decision", "pooling_notes", "reviewer_verification",
]


def compatibility_rows(records):
    """Copy citation identifiers; leave clinical extraction/compatibility blank."""
    rows = []
    for record in records or []:
        rows.append({
            "pmid": record.get("pmid", ""),
            "doi": record.get("doi", ""),
            "title": record.get("title", ""),
            "study_design": record.get("design", record.get("study_design", "")),
            "abstract": record.get("abstract", ""),
            "pubmed_url": record.get("link", ""),
            **{field: "" for field in FIELDS[6:]},
        })
    return rows


def write_compatibility_csv(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = compatibility_rows(records)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path
