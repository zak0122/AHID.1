# Changelog

## 2026-11-13 — global discovery and reproducible outcomes
- Expanded topic-seed discovery from configured-journal-only sampling to a bounded PubMed-wide primary-study sample covering the preceding 365 days, with a configured-journal supplement.
- Kept the journal watch, RCT track, observational track and existing-review searches distinct.
- Added immutable daily outcome snapshots under `outcomes/YYYY/MM/DD/`, run provenance metadata and a yearly index.
- Added archive tests; no full-text eligibility or publication-potential claims are inferred from PubMed hit counts.
- Added candidate-level RCT, observational and review citation CSV exports with PMID/DOI/title deduplication and conservative human-screening triage labels.
- Added tests for citation deduplication, review classification, conservative uncertainty handling and CSV output.
