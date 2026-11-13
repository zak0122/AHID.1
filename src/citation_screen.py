"""Conservative citation deduplication and screening triage.

Without a finalized topic-specific protocol and full texts, the tool must not
label records as finally eligible. It only removes duplicate citations and
flags clearly non-results publication types; all other citations go to human
title/abstract screening as 'unclear'.
"""
import csv
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def deduplicate(records: Iterable[Dict]) -> Tuple[List[Dict], int]:
    """Deduplicate by PMID, DOI, then normalized title; keep first occurrence."""
    kept = []
    seen = set()
    duplicates = 0
    for original in records:
        record = dict(original)
        keys = []
        if record.get("pmid"):
            keys.append(("pmid", _norm(str(record["pmid"]))))
        if record.get("doi"):
            keys.append(("doi", _norm(str(record["doi"]).replace("https://doi.org/", ""))))
        if record.get("title"):
            keys.append(("title", _norm(record["title"])))
        if any(key in seen for key in keys):
            duplicates += 1
            continue
        kept.append(record)
        seen.update(keys)
    return kept, duplicates


def triage_record(record: Dict) -> Tuple[str, str]:
    """Return conservative triage label and reason; never infer final eligibility."""
    title = (record.get("title") or "").lower()
    design = (record.get("design") or record.get("study_design") or "").lower()
    publication_types = " ".join(record.get("publication_types", [])).lower()
    text = " ".join([title, design, publication_types])

    if "protocol" in text and ("protocol" in title or "protocol" in design):
        return "exclude_from_results_pool", "Protocol record; not a results publication"
    if "systematic review" in text or "meta-analysis" in text or "meta analysis" in text:
        return "exclude_from_primary_pool", "Secondary review; retain for overlap/background review"
    if any(term in text for term in ("editorial", "letter to the editor", "comment")):
        return "exclude_from_results_pool", "Editorial/letter/comment publication type"
    if "case report" in text or "case series" in text:
        return "manual_check", "Case report/series; eligibility depends on protocol"
    return "unclear_human_screen", (
        "Potential primary-study citation; apply topic-specific PICO/PECO "
        "criteria during human title/abstract screening"
    )


def prepare_screening_rows(records: Iterable[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    unique, duplicates = deduplicate(records)
    rows = []
    counts = {
        "retrieved": len(unique) + duplicates,
        "duplicates_removed": duplicates,
        "unique_records": len(unique),
        "unclear_human_screen": 0,
        "manual_check": 0,
        "exclude_from_primary_pool": 0,
        "exclude_from_results_pool": 0,
    }
    for record in unique:
        label, reason = triage_record(record)
        rows.append({
            "pmid": record.get("pmid", ""),
            "doi": record.get("doi", ""),
            "title": record.get("title", ""),
            "design": record.get("design", record.get("study_design", "")),
            "sample_size_signal": record.get("sample_size", ""),
            "link": record.get("link", ""),
            "abstract": record.get("abstract", ""),
            "triage_label": label,
            "triage_reason": reason,
        })
        counts[label] = counts.get(label, 0) + 1
    return rows, counts


def write_screening_csv(rows: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pmid", "doi", "title", "design", "sample_size_signal", "link",
        "abstract", "triage_label", "triage_reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
